#!/usr/bin/env bash
# Flash a LatheOS ISO to a USB drive with progress output.
# Usage:  ./scripts/flash-usb.sh <iso-path> <block-device>
# Example: ./scripts/flash-usb.sh result-latheos-iso/iso/latheos-*.iso /dev/sdb

set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <iso-path> <block-device>" >&2
  exit 1
fi

ISO="$1"
DEV="$2"

if [[ ! -f "${ISO}" ]]; then
  echo "error: ISO not found at ${ISO}" >&2
  exit 2
fi
if [[ ! -b "${DEV}" ]]; then
  echo "error: ${DEV} is not a block device" >&2
  exit 3
fi

echo "About to OVERWRITE ${DEV} with ${ISO}."
lsblk "${DEV}" || true
read -rp "Type YES to continue: " CONFIRM
[[ "${CONFIRM}" == "YES" ]] || { echo "aborted"; exit 4; }

if command -v pv >/dev/null; then
  SIZE="$(stat -c%s "${ISO}")"
  pv -s "${SIZE}" "${ISO}" | sudo dd of="${DEV}" bs=4M conv=fsync
else
  sudo dd if="${ISO}" of="${DEV}" bs=4M status=progress conv=fsync
fi

sync
echo "Done. You may remove the USB."
