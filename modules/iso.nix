################################################################################
# LatheOS installer ISO.
#
# Produces a bootable, hybrid BIOS/UEFI installer ISO that is *fully NixOS*
# at the base, with LatheOS carried along as a baked-in flake. Flash to USB,
# boot, run `sudo /etc/latheos/install.sh`, reboot.
#
# Deliberately TTY-only — the ISO's only job is to land the full LatheOS
# system onto disk. The rich Sway UX lives on the installed drive, not the
# installer. This keeps the ISO small (<700 MB) and avoids the greetd/getty
# conflict that arises when we try to layer the installer CD *and* a display
# manager.
#
# Build:
#   nix build .#latheos-iso
# Output:
#   result/iso/latheos-*.iso
################################################################################

{ config, pkgs, lib, modulesPath, ... }:

{
  imports = [
    # Upstream NixOS minimal installer CD. This is the "download NixOS"
    # foundation — we are layering, not forking.
    "${modulesPath}/installer/cd-dvd/installation-cd-minimal.nix"
  ];

  # ---- ISO metadata -----------------------------------------------------------

  isoImage.isoBaseName = lib.mkForce "latheos";
  isoImage.volumeID = lib.mkForce "LATHEOS_LIVE";
  isoImage.squashfsCompression = "zstd -Xcompression-level 19";
  isoImage.makeEfiBootable = true;
  isoImage.makeUsbBootable = true;

  # ---- Live-session additions -------------------------------------------------
  # The installer-cd already provides the `nixos` autologin user. We just add
  # the groups LatheOS-installed users will need so the live session behaves
  # the same way as the installed one.
  users.users.nixos.extraGroups = [ "audio" "input" ];

  # Tools the installer script actually calls. Everything here is also useful
  # for interactive recovery from a broken install.
  environment.systemPackages = with pkgs; [
    parted gptfdisk exfatprogs dosfstools e2fsprogs
    git curl neovim
    ddrescue pv
    tmux htop
  ];

  # A friendly one-shot installer. `sudo /etc/latheos/install.sh` answers
  # three prompts (disk, hostname, hardware token), partitions the NVMe,
  # and hands off to `nixos-install --flake`.
  environment.etc."latheos/install.sh" = {
    mode = "0755";
    text = ''
      #!${pkgs.runtimeShell}
      set -euo pipefail

      echo ""
      echo "==============================================="
      echo "  LatheOS installer (NixOS-based, flake-driven)"
      echo "==============================================="
      echo ""

      read -rp "Target disk (e.g. /dev/nvme0n1) : " DISK
      read -rp "Hostname                        : " HOST
      read -rp "Hardware token                  : " TOKEN

      : "''${DISK:?disk required}"
      : "''${HOST:?hostname required}"
      : "''${TOKEN:?hardware token required}"

      echo ""
      echo "Partitioning ''${DISK}..."
      ${pkgs.parted}/bin/parted -s "''${DISK}" -- mklabel gpt
      ${pkgs.parted}/bin/parted -s "''${DISK}" -- mkpart ESP fat32 1MiB 513MiB
      ${pkgs.parted}/bin/parted -s "''${DISK}" -- set 1 esp on
      ${pkgs.parted}/bin/parted -s "''${DISK}" -- mkpart root ext4 513MiB 90%
      ${pkgs.parted}/bin/parted -s "''${DISK}" -- mkpart assets 90% 100%

      # NVMe/MMC use pN suffix; SATA/virtio don't.
      if [[ "''${DISK}" =~ nvme|mmcblk ]]; then SFX="p"; else SFX=""; fi
      ${pkgs.dosfstools}/bin/mkfs.fat  -F 32 -n ESP          "''${DISK}''${SFX}1"
      ${pkgs.e2fsprogs}/bin/mkfs.ext4  -F    -L latheos      "''${DISK}''${SFX}2"
      ${pkgs.exfatprogs}/bin/mkfs.exfat -L LATHE_ASSETS      "''${DISK}''${SFX}3"

      mount "''${DISK}''${SFX}2" /mnt
      mkdir -p /mnt/boot /mnt/assets /mnt/persist/secrets
      mount "''${DISK}''${SFX}1" /mnt/boot

      echo "CAM_HARDWARE_TOKEN=''${TOKEN}" > /mnt/persist/secrets/cam.env
      chmod 600 /mnt/persist/secrets/cam.env

      FLAKE_DIR="/etc/nixos/latheos"
      if [[ ! -d "''${FLAKE_DIR}" ]]; then
        echo ""
        echo "Cloning LatheOS flake into ''${FLAKE_DIR}..."
        mkdir -p "''${FLAKE_DIR}"
        ${pkgs.git}/bin/git clone https://github.com/haminxx/LatheOS.git "''${FLAKE_DIR}"
      fi

      nixos-install --flake "''${FLAKE_DIR}#latheos-x86_64" --no-root-password

      echo ""
      echo "Install complete. Reboot and remove the USB."
    '';
  };

  # Login MOTD so the next step is obvious.
  environment.etc."issue".text = ''

      Welcome to the LatheOS live installer.
      Run:  sudo /etc/latheos/install.sh

  '';

  # Trim locale noise — saves ~100 MB on the ISO.
  i18n.supportedLocales = lib.mkForce [ "en_US.UTF-8/UTF-8" ];
  documentation.man.enable = lib.mkForce false;
  documentation.info.enable = lib.mkForce false;
}
