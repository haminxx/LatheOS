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
    ./modules/local-llm.nix       # Local Ollama + whisper + piper (offline-first AI).
    ./modules/tts.nix             # Tiered TTS: Piper (default) + MisoTTS (opt-in GPU).
    ./modules/camera.nix          # Local webcam capture + scene-description routing.
    ./modules/vision-grounding.nix # OPT-IN NVIDIA LocateAnything-3B visual grounding (GPU-only).
    ./modules/screen-pilot.nix    # OPT-IN local on-screen guidance ("Clicky", fully local).
    ./modules/embedded-shell.nix  # Scaffold for the in-OS Monaco + chat window.
    ./modules/greeter.nix         # CAM "Jarvis" login briefing (status + voice).
    ./modules/vault.nix           # Age-encrypted, cross-platform-visible secret vault.
    # NOTE: cursor-sdk-bridge.nix (which bakes the @cursor/sdk npm agent into the
    # image) is intentionally NOT imported. That npm package is the optional CLOUD
    # booster, not part of the local-first stack, and baking it bloats the image
    # and pins a fragile npm dependency. `lathe-ai` (below) still routes to the
    # Cursor agent if the user installs it later — see modules/ai-providers.nix.
    # ./modules/cursor-sdk-bridge.nix
    ./modules/ai-providers.nix    # `lathe-ai` opt-in cloud router (cursor/claude/opencode/...).
    ./modules/firstrun-wizard.nix # `lathe-setup` beginner first-boot wizard.
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
  # This is a PORTABLE, removable drive meant to boot on many different machines.
  # It must NOT rewrite each host's EFI/NVRAM boot entries, so we don't touch EFI
  # variables. systemd-boot still installs the removable fallback loader at
  # EFI/BOOT/BOOTX64.EFI, which UEFI firmware boots automatically — and this also
  # lets nixos-install lay down the bootloader from a non-NixOS / BIOS-booted
  # build host (e.g. the VM you build the image in).
  boot.loader.efi.canTouchEfiVariables = false;

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
  # Fully local AI — all model servers (Ollama, vision, TTS) bind to loopback
  # only. No inbound ports are opened and the assistant needs no outbound
  # connection to function.
  networking.firewall.allowedTCPPorts = [ ];

  # ---- services ---------------------------------------------------------------

  services.openssh.enable = false;            # developer-facing box; no remote
  services.dbus.enable = true;

  # logrotate ships enabled-by-default and validates its config at build time.
  # Inside the Nix build sandbox /var/log lacks real-system permissions, so the
  # check spuriously fails on the default btmp/wtmp rules ("insecure
  # permissions"). Skip the *build-time* check only; logrotate still runs and
  # rotates logs normally at runtime. This is the upstream-recommended fix.
  services.logrotate.checkConfig = false;
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
