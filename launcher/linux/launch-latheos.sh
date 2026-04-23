#!/usr/bin/env bash
################################################################################
# launch-latheos.sh — Linux host launcher (Mode B)
#
# Boots the LatheOS USB inside a QEMU/KVM window on a Linux host, without
# reformatting or dual-booting. The VM reads and writes the same USB bytes
# as Mode A, so work is continuous across modes.
#
# Requirements
#   * qemu-system-x86_64 (apt/dnf/pacman/nix: `qemu`)
#   * /dev/kvm accessible (the user must be in the `kvm` group)
#   * The LatheOS USB plugged in — we auto-detect it by GPT partition label.
################################################################################

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing dependency: $1" >&2
    echo "install qemu-system-x86_64 for your distro, then retry." >&2
    exit 1
  }
}
need qemu-system-x86_64
need lsblk

# --- Detect the LatheOS USB ------------------------------------------------
# We look for a whole-disk device whose GPT hosts a partition with the
# `LATHE_ASSETS` label (our exFAT side). This survives device renaming
# across boots.
usb_device() {
  lsblk -lpno NAME,LABEL | awk '$2=="LATHE_ASSETS"{print $1}' | head -n1 \
    | sed -E 's/p?[0-9]+$//'
}

USB="$(usb_device || true)"
if [[ -z "${USB:-}" ]]; then
  echo "Could not find a LatheOS USB (no partition labelled LATHE_ASSETS)."
  echo "Plug the stick in, wait a few seconds, and try again."
  exit 1
fi

# The user must own or have group access to the raw device for QEMU to open
# it. If not, fail clearly rather than silently running in read-only mode.
if ! [ -r "$USB" ] || ! [ -w "$USB" ]; then
  echo "No rw access to $USB — either run with sudo or add yourself to the"
  echo "'disk' group and re-login. (sudo is the simplest one-off fix.)"
  exit 1
fi

# --- VM resources ----------------------------------------------------------
# Keep the host responsive: half of the host's RAM, min 4G, max 16G.
host_kib="$(awk '/MemTotal/{print $2}' /proc/meminfo)"
ram_mb=$(( host_kib / 2 / 1024 ))
(( ram_mb < 4096 )) && ram_mb=4096
(( ram_mb > 16384 )) && ram_mb=16384

cpus="$(nproc)"
(( cpus > 6 )) && cpus=6

accel="tcg"
[ -r /dev/kvm ] && [ -w /dev/kvm ] && accel="kvm"

echo "Booting LatheOS from $USB  (RAM: ${ram_mb} MiB, CPUs: ${cpus}, accel: ${accel})"

exec qemu-system-x86_64 \
  -name "LatheOS" \
  -machine q35,accel=${accel} \
  -cpu host \
  -smp "${cpus}" \
  -m "${ram_mb}" \
  -drive file="${USB}",format=raw,if=virtio,cache=none \
  -bios /usr/share/OVMF/OVMF_CODE.fd \
  -device virtio-net-pci,netdev=n0 -netdev user,id=n0 \
  -device intel-hda -device hda-duplex \
  -display gtk,gl=on \
  -usb -device usb-tablet
