################################################################################
# LatheOS — top-level system composition.
#
# Keep this file as a *manifest* only: every concrete block lives in ./modules.
# The goal is that a one-line diff here is enough to reshape the entire OS
# (e.g. swap Sway for Hyprland, or disable CAM for an offline build).
################################################################################

{ config, pkgs, lib, ... }:

{
  imports = [
    ./modules/sway.nix
    ./modules/audio.nix
    ./modules/cam-daemon.nix
    ./modules/storage.nix
  ];

  # ---- identity ---------------------------------------------------------------

  networking.hostName = "latheos";
  time.timeZone = lib.mkDefault "UTC";
  i18n.defaultLocale = "en_US.UTF-8";

  # ---- kernel / boot ----------------------------------------------------------
  # `latest` kernel gives us the freshest PipeWire / NVMe bits, both of which
  # directly affect wake-to-WS latency.
  boot.kernelPackages = pkgs.linuxPackages_latest;
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  # NVMe + virtio drivers are always available in the initrd so the same image
  # boots bare-metal *and* inside a Type-2 hypervisor (QEMU, Parallels, UTM).
  boot.initrd.availableKernelModules = [
    "nvme" "xhci_pci" "ahci" "usb_storage" "sd_mod"
    "virtio_pci" "virtio_blk" "virtio_net" "virtio_scsi"
  ];

  # ---- users ------------------------------------------------------------------

  users.mutableUsers = false;
  users.users.dev = {
    isNormalUser = true;
    description = "LatheOS developer";
    extraGroups = [ "wheel" "video" "audio" "input" "networkmanager" ];
    shell = pkgs.zsh;
    # Password is set on first boot via systemd-firstboot; no secrets in Nix.
    hashedPasswordFile = "/persist/secrets/dev.hash";
  };
  security.sudo.wheelNeedsPassword = false;

  # ---- baseline tooling -------------------------------------------------------
  # Deliberately sparse. Heavy dev toolchains live in per-project flakes so the
  # system image stays small and CPU/RAM stay reserved for the user's code.
  environment.systemPackages = with pkgs; [
    git curl wget jq ripgrep fd bat eza htop
    zsh tmux neovim
    foot            # GPU-free terminal, aligns with monochrome aesthetic
    wl-clipboard grim slurp swappy
    pipewire wireplumber
    networkmanagerapplet
  ];

  programs.zsh.enable = true;
  programs.command-not-found.enable = false;

  # ---- networking -------------------------------------------------------------

  networking.networkmanager.enable = true;
  networking.firewall.enable = true;
  # Outbound WebSocket to CAM Cloud Proxy — no inbound ports opened.
  networking.firewall.allowedTCPPorts = [ ];

  # ---- services ---------------------------------------------------------------

  services.openssh.enable = false;            # developer-facing box; no remote
  services.dbus.enable = true;
  services.seatd.enable = true;               # Wayland session without logind
  xdg.portal = {
    enable = true;
    wlr.enable = true;
    extraPortals = [ pkgs.xdg-desktop-portal-gtk ];
  };

  # ---- persistence boundary ---------------------------------------------------
  # `/persist` lives on the EXT4 partition; `/assets` is the cross-platform
  # exFAT partition mounted read-write for the dev user. See modules/storage.nix.

  environment.etc."latheos/release".text = ''
    LatheOS ${config.system.nixos.release} — CAM-ready build
  '';

  system.stateVersion = "24.11";
}
