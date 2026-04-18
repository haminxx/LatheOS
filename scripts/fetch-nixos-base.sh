#!/usr/bin/env bash
# Fetch the upstream NixOS 24.11 minimal installer ISO.
#
# Use this when you want to install STOCK NixOS first on a bare machine,
# then layer LatheOS on top via `nixos-rebuild switch --flake` (the
# "dual-install" path).
#
# If you just want a LatheOS-branded installer with everything pre-baked,
# use `scripts/build-latheos-iso.sh` instead.
#
# The ISO goes to a cache directory outside the project tree so it never
# ends up in git, OneDrive, or the Nix store by accident.

set -euo pipefail

CHANNEL="${CHANNEL:-nixos-24.11}"
ARCH="${ARCH:-x86_64-linux}"
CACHE_DIR="${CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/latheos}"

# As of nixos-24.11 the latest minimal ISO lives at a fixed channels redirect:
URL_BASE="https://channels.nixos.org/${CHANNEL}/latest-nixos-minimal-${ARCH}"
ISO_URL="${URL_BASE}.iso"
SHA_URL="${URL_BASE}.iso.sha256"

mkdir -p "${CACHE_DIR}"
cd "${CACHE_DIR}"

echo "[fetch] channel=${CHANNEL} arch=${ARCH}"
echo "[fetch] destination=${CACHE_DIR}"

# The SHA file holds `<sha256>  <filename>`; we pull that first so we can
# (a) know the exact filename and (b) verify the ISO after download.
curl -fL "${SHA_URL}" -o nixos-minimal.sha256
read -r EXPECTED FILENAME < nixos-minimal.sha256

if [[ -f "${FILENAME}" ]]; then
  ACTUAL="$(sha256sum "${FILENAME}" | awk '{print $1}')"
  if [[ "${ACTUAL}" == "${EXPECTED}" ]]; then
    echo "[fetch] already have verified ${FILENAME}"
    ln -sfn "${FILENAME}" nixos-minimal.iso
    exit 0
  fi
  echo "[fetch] existing copy is stale; re-downloading"
  rm -f "${FILENAME}"
fi

echo "[fetch] downloading ${FILENAME}..."
curl -fL --progress-bar "${ISO_URL}" -o "${FILENAME}"

ACTUAL="$(sha256sum "${FILENAME}" | awk '{print $1}')"
if [[ "${ACTUAL}" != "${EXPECTED}" ]]; then
  echo "[fetch] SHA256 mismatch!" >&2
  echo "  expected ${EXPECTED}" >&2
  echo "  actual   ${ACTUAL}"   >&2
  exit 1
fi

ln -sfn "${FILENAME}" nixos-minimal.iso
echo "[fetch] OK — ${CACHE_DIR}/nixos-minimal.iso -> ${FILENAME}"
