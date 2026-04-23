#!/usr/bin/env bash
################################################################################
# launch-latheos.command — macOS host launcher (Mode B)
#
# Boots the LatheOS USB inside a QEMU window on macOS. Works on both Intel
# and Apple Silicon Macs, but the target architecture differs:
#
#   * Intel Mac         → x86_64 LatheOS (same image as Windows/Linux hosts)
#   * Apple Silicon Mac → aarch64 LatheOS (from `nixosConfigurations.latheos-
#                         iso-aarch64`) because Apple does NOT provide a way
#                         to run native x86 VMs at reasonable speed.
#
# Apple-specific notes
#   * We use Apple's Hypervisor.framework via QEMU's `hvf` accelerator.
#   * No kernel extensions are required — Apple no longer allows them.
#   * macOS does not give userland raw access to whole USB disks without the
#     stick being unmounted first. We auto-unmount the filesystem partitions
#     (not wipe them) so QEMU can open the raw node.
#
# Requirements (install once)
#   * QEMU:  `brew install qemu`   -- OR --   UTM from the Mac App Store.
#   * Disk Arbitration permission granted to Terminal/iTerm when prompted.
################################################################################

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

command -v qemu-system-x86_64 >/dev/null 2>&1 || {
  echo "QEMU is not installed."
  echo "Easiest install:  brew install qemu"
  echo "(Or install UTM from the Mac App Store and import the disk manually.)"
  exit 1
}

# --- Detect the USB --------------------------------------------------------
# `diskutil list` is the canonical way on macOS. We match by LatheOS labels.
USB_ID="$(
  diskutil list \
    | awk '/LATHE_ASSETS|latheos|ESP/{print $NF}' \
    | grep -E '^disk[0-9]+s[0-9]+$' \
    | head -n1 \
    | sed -E 's/s[0-9]+$//'
)"

if [[ -z "${USB_ID}" ]]; then
  echo "No LatheOS USB detected."
  echo "Plug it in, wait for Finder to mount it, then run this launcher again."
  exit 1
fi

RAW="/dev/r${USB_ID}"
echo "Using raw disk: ${RAW}"

# Unmount partitions so QEMU can open the raw node exclusively.
diskutil unmountDisk "/dev/${USB_ID}" >/dev/null || {
  echo "Could not unmount ${USB_ID}. Close any Finder windows showing it."
  exit 1
}

# --- Architecture + accelerator --------------------------------------------
ARCH="$(uname -m)"
if [[ "${ARCH}" == "arm64" ]]; then
  QEMU_BIN="qemu-system-aarch64"
  MACHINE="virt,accel=hvf,highmem=on"
  CPU="host"
  FIRMWARE="/opt/homebrew/share/qemu/edk2-aarch64-code.fd"
else
  QEMU_BIN="qemu-system-x86_64"
  MACHINE="q35,accel=hvf"
  CPU="host,-pdpe1gb"                # Apple hvf dislikes PDPE1GB
  FIRMWARE="/opt/homebrew/share/qemu/edk2-x86_64-code.fd"
  [[ -r "${FIRMWARE}" ]] || FIRMWARE="/usr/local/share/qemu/edk2-x86_64-code.fd"
fi

# --- VM resources ----------------------------------------------------------
ram_mb=$(( $(sysctl -n hw.memsize) / 2 / 1024 / 1024 ))
(( ram_mb < 4096 )) && ram_mb=4096
(( ram_mb > 16384 )) && ram_mb=16384
cpus="$(sysctl -n hw.ncpu)"
(( cpus > 6 )) && cpus=6

echo "Booting LatheOS (${ARCH}) — RAM: ${ram_mb} MiB, CPUs: ${cpus}"

exec "${QEMU_BIN}" \
  -name "LatheOS" \
  -machine "${MACHINE}" \
  -cpu "${CPU}" \
  -smp "${cpus}" \
  -m "${ram_mb}" \
  -drive "file=${RAW},format=raw,if=virtio,cache=none" \
  -bios "${FIRMWARE}" \
  -device virtio-net-pci,netdev=n0 -netdev user,id=n0 \
  -device intel-hda -device hda-duplex \
  -display cocoa \
  -usb -device usb-tablet
