#!/usr/bin/env bash
################################################################################
# install-latheos.sh — Linux pre-boot installer (flasher + first-run profile).
#
# Mirror of the Windows PowerShell installer. Run BEFORE you have LatheOS.
#
#   sudo ./install-latheos.sh                       # English defaults
#   sudo ./install-latheos.sh --language ko         # Korean preset
#   sudo ./install-latheos.sh --picovoice-key ABC   # stage wake-word key
################################################################################

set -euo pipefail

IMAGE_URL="${IMAGE_URL:-https://github.com/haminxx/LatheOS/releases/latest/download/latheos-usb.zip}"
CACHE_DIR="${CACHE_DIR:-$HOME/.cache/latheos}"
LANGUAGE="en"
PV_KEY=""
WAKE_BACKEND="oww"         # oww | porcupine | none
FORCE=false

usage() { sed -n '2,12p' "$0"; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --language)        LANGUAGE="$2";     shift 2 ;;
    --wake-backend)    WAKE_BACKEND="$2"; shift 2 ;;
    --picovoice-key)   PV_KEY="$2";       shift 2 ;;
    --force)           FORCE=true;        shift ;;
    -h|--help)         usage 0 ;;
    *) echo "unknown flag: $1" >&2; usage 1 ;;
  esac
done

case "$WAKE_BACKEND" in oww|porcupine|none) ;; *) echo "unknown --wake-backend: $WAKE_BACKEND" >&2; exit 1 ;; esac

[[ $EUID -eq 0 ]] || { echo "re-run with sudo" >&2; exit 1; }
for t in curl unzip lsblk wipefs dd sha256sum partprobe; do
  command -v "$t" >/dev/null || { echo "missing: $t" >&2; exit 1; }
done

mkdir -p "$CACHE_DIR"
ZIP="$CACHE_DIR/latheos-usb.zip"
IMG="$CACHE_DIR/latheos-usb.img"

if [[ ! -f "$ZIP" ]]; then
  echo "downloading $IMAGE_URL ..."
  curl -L --fail --progress-bar -o "$ZIP" "$IMAGE_URL"
fi
if [[ ! -f "$IMG" ]]; then
  echo "extracting image..."
  unzip -j -o "$ZIP" latheos-usb.img -d "$CACHE_DIR" >/dev/null
fi
[[ -f "$IMG" ]] || { echo "image missing after extract: $IMG" >&2; exit 1; }

echo ""
echo "Removable disks:"
lsblk -d -o NAME,SIZE,MODEL,TRAN,RM,MOUNTPOINT | awk 'NR==1 || $5==1 || $4=="usb"'
echo ""

read -rp "Target device (e.g. /dev/sdb): " DEV
[[ -b "$DEV" ]] || { echo "not a block device: $DEV" >&2; exit 1; }
if [[ "$DEV" == /dev/sda || "$DEV" == /dev/nvme0n1 ]] && [[ "$FORCE" != true ]]; then
  echo "refusing to touch $DEV (likely system disk). Use --force if you're SURE." >&2
  exit 1
fi
read -rp "Type ERASE to confirm flashing $DEV: " OK
[[ "$OK" == "ERASE" ]] || { echo "cancelled."; exit 1; }

# Make sure nothing on the disk is mounted.
for p in $(lsblk -nr -o NAME "$DEV" | tail -n +2); do
  umount "/dev/$p" 2>/dev/null || true
done
wipefs -af "$DEV"

echo "flashing..."
dd if="$IMG" of="$DEV" bs=4M status=progress conv=fsync
sync
partprobe "$DEV"

# Find the exFAT partition by label so we can drop firstrun.json onto it.
echo "locating LATHE_ASSETS partition..."
for _ in 1 2 3 4 5; do
  ASSETS=$(lsblk -lpno NAME,LABEL "$DEV" | awk '$2=="LATHE_ASSETS"{print $1}' | head -n1)
  [[ -n "${ASSETS:-}" ]] && break
  sleep 1
done

if [[ -n "${ASSETS:-}" ]]; then
  MNT=$(mktemp -d)
  mount "$ASSETS" "$MNT"
  mkdir -p "$MNT/latheos" "$MNT/latheos/secrets"
  cat > "$MNT/latheos/firstrun.json" <<JSON
{
  "language": "$LANGUAGE",
  "timezone": "$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)",
  "keyboard": "",
  "wake_backend": "$WAKE_BACKEND",
  "created_on": "$(date -Is)",
  "created_by_host": "$(hostname)"
}
JSON
  if [[ "$WAKE_BACKEND" == "porcupine" && -n "$PV_KEY" ]]; then
    cat > "$MNT/latheos/secrets/cam.env" <<EOF
LATHEOS_WAKE_BACKEND=porcupine
PICOVOICE_ACCESS_KEY=$PV_KEY
CAM_KEYWORD_PATH=/persist/secrets/hey-cam.ppn
EOF
    chmod 600 "$MNT/latheos/secrets/cam.env"
  elif [[ "$WAKE_BACKEND" != "oww" ]]; then
    echo "LATHEOS_WAKE_BACKEND=$WAKE_BACKEND" > "$MNT/latheos/secrets/cam.env"
    chmod 600 "$MNT/latheos/secrets/cam.env"
  fi
  sync
  umount "$MNT"
  rmdir "$MNT"
  echo "first-run profile staged (lang=$LANGUAGE, wake=$WAKE_BACKEND)."
else
  echo "warning: LATHE_ASSETS partition not found; first-run profile NOT staged."
fi

echo ""
echo "done. Reboot and pick the USB in your firmware boot menu,"
echo "or open launcher/linux/launch-latheos.sh on a host to run it in a window."
