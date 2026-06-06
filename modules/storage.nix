################################################################################
# Multi-partition NVMe/USB — ENCRYPTED EXT4 for the OS + private docs, plus an
# unencrypted exFAT area for cross-platform file sharing.
#
# Layout (created by scripts/build-usb-image.sh; not managed declaratively
# because the kernel already owns block devices):
#
#   p1  ESP    vfat        unencrypted bootloader (/boot)        LABEL=ESP
#   p2  root   LUKS2+ext4  ENCRYPTED OS + /persist + documents   PARTLABEL=cryptroot
#                          (the decrypted ext4 carries LABEL=latheos)
#   p3  shared exfat        UNENCRYPTED cross-OS file drop        LABEL=LATHE_ASSETS
#
# Privacy model (the "balanced" choice):
#   * The OS, /persist/secrets, the vault private key, and the user's private
#     documents (/persist/documents) all live on the ENCRYPTED root, unlocked
#     by a passphrase typed at boot. A lost stick is unreadable.
#   * /assets stays a small UNENCRYPTED exFAT area so the same drive can be
#     plugged into Windows/macOS to drag files in and out. NEVER put secrets
#     here — it is readable by any OS by design.
#
# The image ships with a default passphrase ("latheos"); the first-run wizard
# (modules/firstrun-wizard.nix) forces the user to change it on first boot.
################################################################################

{ config, pkgs, lib, ... }:

{
  # Unlock the encrypted root in the initrd, before the real root is mounted.
  # The physical LUKS container is found by its GPT partition name (set by the
  # build script: `parted ... mkpart cryptroot ...`). Once opened it appears as
  # /dev/mapper/latheos_crypt, whose ext4 carries LABEL=latheos (below).
  boot.initrd.luks.devices."latheos_crypt" = {
    device = "/dev/disk/by-partlabel/cryptroot";
    allowDiscards = true;          # TRIM passthrough for SSD health/longevity
  };

  fileSystems."/" = {
    device = "/dev/disk/by-label/latheos";   # the decrypted /dev/mapper device
    fsType = "ext4";
    options = [ "noatime" "discard=async" ];
  };

  fileSystems."/boot" = {
    device = "/dev/disk/by-label/ESP";
    fsType = "vfat";
    options = [ "umask=0077" ];
  };

  fileSystems."/assets" = {
    device = "/dev/disk/by-label/LATHE_ASSETS";
    fsType = "exfat";
    options = [
      "uid=1000" "gid=100" "umask=007"
      "nofail" "x-systemd.device-timeout=5s"
    ];
  };

  # `/persist` lives on the ENCRYPTED root and survives nixos-rebuild. Secrets
  # (user password hash, vault private key, per-drive overrides) and the user's
  # private documents live here, so they are protected by the boot passphrase.
  # `/assets/shared` is the explicit cross-OS drop folder (unencrypted).
  systemd.tmpfiles.rules = [
    "d /persist              0755 root  root  - -"
    "d /persist/secrets      0700 root  root  - -"
    "d /persist/home/dev     0755 dev   users - -"
    "d /persist/documents    0700 dev   users - -"   # private, encrypted at rest
    "d /assets/shared        0755 dev   users - -"    # cross-OS file drop (NOT private)
  ];

  # exFAT tooling needed by mount + fsck.
  environment.systemPackages = with pkgs; [ exfatprogs ];
}
