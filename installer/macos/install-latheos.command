#!/usr/bin/env bash
################################################################################
# install-latheos.command — macOS pre-boot installer (flasher).
#
# Double-click in Finder, or run:
#   sudo ./install-latheos.command \
#       [--language en|ko] \
#       [--wake-backend oww|porcupine|none] \
#       [--picovoice-key KEY]
################################################################################

set -euo pipefail

IMAGE_URL="${IMAGE_URL:-https://github.com/haminxx/LatheOS/releases/latest/download/latheos-usb.zip}"
CACHE_DIR="${CACHE_DIR:-$HOME/Library/Caches/LatheOS}"
LANGUAGE="en"
WAKE_BACKEND="oww"
PV_KEY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --language)       LANGUAGE="$2";     shift 2 ;;
    --wake-backend)   WAKE_BACKEND="$2"; shift 2 ;;
    --picovoice-key)  PV_KEY="$2";       shift 2 ;;
    -h|--help)        sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 1 ;;
  esac
done

case "$WAKE_BACKEND" in oww|porcupine|none) ;; *) echo "unknown --wake-backend: $WAKE_BACKEND" >&2; exit 1 ;; esac

[[ $EUID -eq 0 ]] || { echo "re-run with sudo"; exit 1; }
for t in curl unzip diskutil dd; do command -v "$t" >/dev/null || { echo "missing: $t" >&2; exit 1; }; done

mkdir -p "$CACHE_DIR"
ZIP="$CACHE_DIR/latheos-usb.zip"
IMG="$CACHE_DIR/latheos-usb.img"

[[ -f "$ZIP" ]] || { echo "downloading..."; curl -L --fail --progress-bar -o "$ZIP" "$IMAGE_URL"; }
[[ -f "$IMG" ]] || { echo "extracting..."; unzip -j -o "$ZIP" latheos-usb.img -d "$CACHE_DIR" >/dev/null; }

echo ""
echo "Removable disks (external only):"
diskutil list external physical
echo ""
read -rp "Target disk (e.g. disk4): " D
[[ "$D" =~ ^disk[0-9]+$ ]] || { echo "expected a name like 'disk4'"; exit 1; }
read -rp "Type ERASE to confirm flashing /dev/$D: " OK
[[ "$OK" == "ERASE" ]] || { echo "cancelled."; exit 1; }

diskutil unmountDisk "/dev/$D"

echo "flashing (this takes a while; use the raw node /dev/r$D for speed)..."
dd if="$IMG" of="/dev/r$D" bs=4m status=progress
sync

# Re-probe and mount the exFAT to drop firstrun.json.
diskutil list "/dev/$D" >/dev/null || true
ASSETS_VOL=""
for _ in 1 2 3 4 5; do
  ASSETS_VOL=$(diskutil info /Volumes/LATHE_ASSETS 2>/dev/null | awk -F': *' '/Mount Point/{print $2}')
  [[ -n "$ASSETS_VOL" ]] && break
  diskutil mountDisk "/dev/$D" >/dev/null || true
  sleep 1
done

if [[ -n "${ASSETS_VOL:-}" ]]; then
  mkdir -p "$ASSETS_VOL/latheos" "$ASSETS_VOL/latheos/secrets"
  cat > "$ASSETS_VOL/latheos/firstrun.json" <<JSON
{
  "language": "$LANGUAGE",
  "timezone": "$(systemsetup -gettimezone 2>/dev/null | awk -F': ' '{print $2}' || echo UTC)",
  "keyboard": "",
  "wake_backend": "$WAKE_BACKEND",
  "created_on": "$(date -Is 2>/dev/null || date -u +%FT%TZ)",
  "created_by_host": "$(hostname -s)"
}
JSON
  if [[ "$WAKE_BACKEND" == "porcupine" && -n "$PV_KEY" ]]; then
    cat > "$ASSETS_VOL/latheos/secrets/cam.env" <<EOF
LATHEOS_WAKE_BACKEND=porcupine
PICOVOICE_ACCESS_KEY=$PV_KEY
CAM_KEYWORD_PATH=/persist/secrets/hey-cam.ppn
EOF
    chmod 600 "$ASSETS_VOL/latheos/secrets/cam.env"
  elif [[ "$WAKE_BACKEND" != "oww" ]]; then
    echo "LATHEOS_WAKE_BACKEND=$WAKE_BACKEND" > "$ASSETS_VOL/latheos/secrets/cam.env"
    chmod 600 "$ASSETS_VOL/latheos/secrets/cam.env"
  fi
  echo "first-run profile staged (lang=$LANGUAGE, wake=$WAKE_BACKEND)."
else
  echo "warning: LATHE_ASSETS not mounted; first-run profile NOT staged."
fi

echo ""
echo "done. On Apple Silicon, use launcher/macos/launch-latheos.command to run LatheOS in a window (aarch64)."
