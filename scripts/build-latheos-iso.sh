#!/usr/bin/env bash
# Build the LatheOS installer ISO.
#
# Wraps `nix build` so the output path is printed explicitly and the common
# flags (experimental features, link-result) don't have to be remembered.
#
# Run from anywhere with Nix ≥ 2.18 installed (Linux host, macOS Nix, WSL).

set -euo pipefail

cd "$(dirname "$0")/.."

ARCH="${ARCH:-x86_64}"
TARGET=".#latheos-iso"
if [[ "${ARCH}" == "aarch64" ]]; then
  TARGET=".#nixosConfigurations.latheos-iso-aarch64.config.system.build.isoImage"
fi

echo "[build] target=${TARGET}"
nix build --extra-experimental-features 'nix-command flakes' \
  --print-build-logs \
  --out-link result-latheos-iso \
  "${TARGET}"

ISO="$(ls -1 result-latheos-iso/iso/*.iso 2>/dev/null | head -1 || true)"
if [[ -z "${ISO}" ]]; then
  echo "[build] build succeeded but no ISO found under result-latheos-iso/iso/" >&2
  exit 1
fi

echo ""
echo "[build] ISO ready:"
ls -lh "${ISO}"
echo ""
echo "Flash to USB with:"
echo "  sudo dd if=${ISO} of=/dev/sdX bs=4M status=progress conv=fsync"
