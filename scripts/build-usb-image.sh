#!/usr/bin/env bash
################################################################################
# build-usb-image.sh — produce a flashable LatheOS USB image.
#
# Output
#   dist/latheos-usb.img            raw disk image, 3 partitions
#   dist/latheos-usb.img.sha256     checksum
#   dist/latheos-usb.img.zip        zipped + installers + launchers (release)
#
# Requires (run on Linux, or WSL2 on Windows):
#   * nix (with flakes)
#   * root (sudo) — we mkfs + mount loopback devices
#   * parted, dosfstools, e2fsprogs, exfatprogs, util-linux (losetup), zip
#
# Usage
#   sudo ./scripts/build-usb-image.sh [--size 16G] [--arch x86_64]
#
# The produced .img is "universal": it boots as Mode A on bare metal AND is
# the disk a Mode-B launcher (QEMU / UTM) opens. Same bytes, same state.
################################################################################

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"
IMG="${DIST_DIR}/latheos-usb.img"
ZIP="${DIST_DIR}/latheos-usb.zip"

ARCH="x86_64"
SIZE="16G"     # total image size. User's real stick can be bigger — NixOS will resize /assets on first boot.

# The encrypted root ships with this passphrase; modules/firstrun-wizard.nix
# forces the user to change it on first boot. Override at build time with
# INITIAL_LUKS_PASS=... if you want a different factory passphrase.
INITIAL_LUKS_PASS="${INITIAL_LUKS_PASS:-latheos}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch) ARCH="$2"; shift 2 ;;
    --size) SIZE="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 1 ;;
  esac
done

log()  { printf '[build] %s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1" >&2; exit 1; }; }

[[ "$(uname -s)" == "Linux" ]] || {
  echo "This script must run on Linux (or WSL2). macOS/Windows cannot create an ext4 + exFAT image locally." >&2
  exit 1
}
[[ $EUID -eq 0 ]] || { echo "Re-run with sudo." >&2; exit 1; }

for t in nix parted mkfs.fat mkfs.ext4 mkfs.exfat losetup sgdisk zip sha256sum cryptsetup; do need "$t"; done

mkdir -p "$DIST_DIR"
rm -f "$IMG" "$ZIP"

# ---------------------------------------------------------------------------
# 1. Build the NixOS system closure for this arch.
# ---------------------------------------------------------------------------
log "building LatheOS closure for ${ARCH}..."
nix build --print-out-paths --out-link "${DIST_DIR}/result-system" \
  "${REPO_ROOT}#nixosConfigurations.latheos-${ARCH}.config.system.build.toplevel"
SYSTEM_PATH="$(readlink -f "${DIST_DIR}/result-system")"
log "system closure: ${SYSTEM_PATH}"

# ---------------------------------------------------------------------------
# 2. Allocate the raw image and partition it.
#    Layout:
#       p1  ESP        FAT32       1 GiB                 (LABEL=ESP, unencrypted)
#       p2  cryptroot  LUKS2+ext4  remainder - 2 GiB     (PARTLABEL=cryptroot;
#                                  decrypted ext4 LABEL=latheos)
#       p3  data       exfat       2 GiB (placeholder)   (LABEL=LATHEASSETS)
#    The user's real stick will be bigger than 16 GiB; a first-boot
#    growpart-style service will expand /assets to fill the stick.
#
#    p2 is LUKS-encrypted: the OS, /persist (secrets, vault key, documents) sit
#    behind a boot passphrase. p1 (ESP) and p3 (shared exFAT) stay unencrypted.
# ---------------------------------------------------------------------------
log "allocating sparse image (${SIZE})..."
truncate -s "${SIZE}" "$IMG"

log "partitioning..."
# NB: the `--` is REQUIRED. The cryptroot/data partitions use negative,
# from-the-end offsets ("-2049MiB" = 2049 MiB before the disk end). Without
# `--`, parted parses that leading dash as option flags (-2 -0 -4 ...) and
# aborts with "invalid option". `--` stops option parsing so the values are
# read as partition positions.
parted -s "$IMG" -- \
  mklabel gpt \
  mkpart ESP fat32 1MiB 1025MiB \
  set 1 esp on \
  mkpart cryptroot 1025MiB -2049MiB \
  mkpart data      -2049MiB 100%

# ---------------------------------------------------------------------------
# 3. Map as loopback device, LUKS-encrypt the root, and format each partition.
# ---------------------------------------------------------------------------
LOOP=$(losetup --show -fP "$IMG")
log "loop device: ${LOOP}"
CRYPT_NAME="latheos_crypt_build"
trap 'cryptsetup close "${CRYPT_NAME}" 2>/dev/null || true; losetup -d "${LOOP}" 2>/dev/null || true' EXIT

log "LUKS-formatting the root partition (factory passphrase; changed on first boot)..."
printf '%s' "${INITIAL_LUKS_PASS}" | cryptsetup luksFormat --type luks2 --batch-mode "${LOOP}p2" -
printf '%s' "${INITIAL_LUKS_PASS}" | cryptsetup open "${LOOP}p2" "${CRYPT_NAME}" -

log "formatting partitions..."
mkfs.fat  -F 32 -n ESP          "${LOOP}p1"
mkfs.ext4 -F    -L latheos      "/dev/mapper/${CRYPT_NAME}"
mkfs.exfat      -L LATHEASSETS "${LOOP}p3"

# ---------------------------------------------------------------------------
# 4. Mount and install NixOS into the decrypted ext4 partition.
# ---------------------------------------------------------------------------
MNT=$(mktemp -d)
trap 'umount -R "${MNT}" 2>/dev/null || true; cryptsetup close "${CRYPT_NAME}" 2>/dev/null || true; losetup -d "${LOOP}" 2>/dev/null || true; rmdir "${MNT}" 2>/dev/null || true' EXIT

log "mounting root..."
mount "/dev/mapper/${CRYPT_NAME}" "${MNT}"
mkdir -p "${MNT}/boot" "${MNT}/assets" "${MNT}/persist/secrets" "${MNT}/persist/state" "${MNT}/persist/documents"
mount "${LOOP}p1" "${MNT}/boot"
mount "${LOOP}p3" "${MNT}/assets"

log "nixos-install (this populates /nix/store on the image)..."
# nixos-install ships in the `nixos-install-tools` package. On a NixOS host it
# is already on PATH; on a plain Linux + Nix build box (the usual case) it is
# NOT, so fetch it from the SAME pinned nixpkgs this flake uses — reproducible,
# and it reuses the already-downloaded 24.11 nixpkgs (no extra channel). Falls
# back to the flake registry's nixpkgs if that attribute path is unavailable.
if command -v nixos-install >/dev/null 2>&1; then
  NIXOS_INSTALL=nixos-install
else
  log "nixos-install not on PATH (non-NixOS host); fetching nixos-install-tools..."
  TOOLS_PATH="$(nix build --no-link --print-out-paths \
      "${REPO_ROOT}#nixosConfigurations.latheos-${ARCH}.pkgs.nixos-install-tools" 2>/dev/null \
      || nix build --no-link --print-out-paths "nixpkgs#nixos-install-tools")"
  NIXOS_INSTALL="${TOOLS_PATH}/bin/nixos-install"
fi

"${NIXOS_INSTALL}" \
  --root "${MNT}" \
  --system "${SYSTEM_PATH}" \
  --no-root-password \
  --no-channel-copy

# ---------------------------------------------------------------------------
# 5. Seed the exFAT partition with launchers and a friendly README so that,
#    the moment the user plugs the stick into Windows/macOS/Linux, they see
#    the host-side tools there.
# ---------------------------------------------------------------------------
log "seeding exFAT partition with launchers + installers..."
cp -r "${REPO_ROOT}/launcher/."  "${MNT}/assets/launcher/"
cp -r "${REPO_ROOT}/installer/." "${MNT}/assets/installer/"
[ -r "${REPO_ROOT}/RELEASE_README.md" ] && \
  install -m 0644 "${REPO_ROOT}/RELEASE_README.md" "${MNT}/assets/README.md"

# ---------------------------------------------------------------------------
# 5b. Bake pre-fetched AI models onto /assets so first boot works OFFLINE.
#     The user can run `scripts/prefetch-models.sh` ahead of this step to
#     stage Ollama / Piper / whisper / openWakeWord weights. If they didn't,
#     we keep building — the image still boots, just pulls on first network.
# ---------------------------------------------------------------------------
PREFETCH="${DIST_DIR}/prefetch"
if [ -d "$PREFETCH" ]; then
  log "copying pre-fetched models from ${PREFETCH} into /assets/models"
  mkdir -p \
    "${MNT}/assets/models/ollama" \
    "${MNT}/assets/models/piper" \
    "${MNT}/assets/models/whisper" \
    "${MNT}/assets/models/openwakeword"

  [ -d "${PREFETCH}/ollama" ]        && cp -r "${PREFETCH}/ollama/."        "${MNT}/assets/models/ollama/"
  [ -d "${PREFETCH}/piper" ]         && cp -r "${PREFETCH}/piper/."         "${MNT}/assets/models/piper/"
  [ -d "${PREFETCH}/whisper" ]       && cp -r "${PREFETCH}/whisper/."       "${MNT}/assets/models/whisper/"
  [ -d "${PREFETCH}/openwakeword" ]  && cp -r "${PREFETCH}/openwakeword/."  "${MNT}/assets/models/openwakeword/"

  # OPT-IN visual grounding model (NVIDIA LocateAnything-3B, ~8 GB, GPU-only,
  # NON-COMMERCIAL). Only present if the user ran the prefetch with
  # WITH_VISION=1. The OS feature stays off until latheos.vision.enable = true.
  if [ -d "${PREFETCH}/locateanything" ]; then
    log "staging vision model -> /assets/models/locateanything (non-commercial)"
    mkdir -p "${MNT}/assets/models/locateanything"
    cp -r "${PREFETCH}/locateanything/." "${MNT}/assets/models/locateanything/"
  fi

  # OPT-IN premium voice (MisoTTS 8B, ~30-40 GB, GPU-only, English-only,
  # watermarked). Only present if the user ran the prefetch with WITH_MISO=1.
  # The OS keeps using Piper until a capable GPU is detected AND
  # latheos.tts.miso.enable = true.
  if [ -d "${PREFETCH}/miso" ]; then
    log "staging premium voice -> /assets/models/miso (MisoTTS, GPU-only)"
    mkdir -p "${MNT}/assets/models/miso"
    cp -r "${PREFETCH}/miso/." "${MNT}/assets/models/miso/"
  fi

  # Marker file — modules/local-llm.nix sees this and skips the first-boot
  # pull entirely, which means offline users get full Jarvis out of the box.
  date -u +%FT%TZ > "${MNT}/assets/models/.prefetched"
  log "pre-fetched bundle baked in (marker: /assets/models/.prefetched)"
else
  log "no dist/prefetch/ — models will pull on first network."
fi

# ---------------------------------------------------------------------------
# 6. Unmount, detach, compute checksum, pack release zip.
# ---------------------------------------------------------------------------
log "unmounting..."
sync
umount -R "${MNT}"
cryptsetup close "${CRYPT_NAME}"
losetup -d "${LOOP}"
trap - EXIT

log "checksumming..."
sha256sum "$IMG" > "${IMG}.sha256"

log "packing release zip..."
STAGE=$(mktemp -d)
cp "$IMG" "${STAGE}/latheos-usb.img"
cp "${IMG}.sha256" "${STAGE}/latheos-usb.img.sha256"
cp -r "${REPO_ROOT}/launcher"  "${STAGE}/launcher"
cp -r "${REPO_ROOT}/installer" "${STAGE}/installer"
[ -r "${REPO_ROOT}/RELEASE_README.md" ] && cp "${REPO_ROOT}/RELEASE_README.md" "${STAGE}/README.md"
( cd "$STAGE" && zip -rq "${ZIP}" . )
rm -rf "${STAGE}"

log "done."
ls -lh "$IMG" "$ZIP"
