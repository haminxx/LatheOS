################################################################################
# Multi-partition NVMe — EXT4 for the OS, exFAT for cross-platform assets.
#
# Layout (created once during install; not managed declaratively because the
# kernel already owns block devices):
#
#   /dev/nvme0n1p1   256M  vfat    ESP (/boot)
#   /dev/nvme0n1p2   rest  ext4    NixOS root + /persist  (LABEL=latheos)
#   /dev/nvme0n1p3   user  exfat   Portable asset vault   (LABEL=LATHE_ASSETS)
#
# The exFAT partition is deliberately user-visible so the same drive can be
# plugged into macOS/Windows and read directly.
################################################################################

{ config, pkgs, lib, ... }:

{
  fileSystems."/" = {
    device = "/dev/disk/by-label/latheos";
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

  # `/persist` is a subvolume of the root FS that survives nixos-rebuild.
  # Secrets (hardware token, wake-word model, user password hash) live here.
  systemd.tmpfiles.rules = [
    "d /persist              0755 root  root  - -"
    "d /persist/secrets      0700 root  root  - -"
    "d /persist/home/dev     0755 dev   users - -"
  ];

  # exFAT tooling needed by mount + fsck.
  environment.systemPackages = with pkgs; [ exfatprogs ];
}
