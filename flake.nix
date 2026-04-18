################################################################################
# LatheOS flake — dual-arch reproducible system + bootable installer ISO.
#
# Installed system (from inside a running NixOS host):
#   sudo nixos-rebuild switch --flake .#latheos-x86_64
#   sudo nixos-rebuild switch --flake .#latheos-aarch64
#
# Installer ISO (runs anywhere Nix is installed):
#   nix build .#latheos-iso              # x86_64 host
#   nix build .#latheos-iso-aarch64      # aarch64 host (or x86_64 with binfmt)
#   ls -lh result/iso/
#
# Both the installed system and the installer ISO derive from the same pinned
# `nixos-24.11` nixpkgs input — the ISO *is* NixOS, with LatheOS layered on.
################################################################################

{
  description = "LatheOS — declarative vibe-coding OS with CAM orchestration";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";

    home-manager = {
      url = "github:nix-community/home-manager/release-24.11";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = inputs@{ self, nixpkgs, home-manager, ... }:
    let
      mkInstalledSystem = system:
        nixpkgs.lib.nixosSystem {
          inherit system;
          specialArgs = { inherit inputs; };
          modules = [
            ./configuration.nix
            home-manager.nixosModules.home-manager
            ./modules/home.nix
          ];
        };

      # The installer ISO is its own nixosConfiguration because the upstream
      # installer CD module owns bootloader + fileSystems, which collides
      # with our storage.nix. Keeping it separate is the canonical pattern.
      mkInstallerIso = system:
        nixpkgs.lib.nixosSystem {
          inherit system;
          specialArgs = { inherit inputs; };
          modules = [ ./modules/iso.nix ];
        };
    in {
      nixosConfigurations = {
        "latheos-x86_64"        = mkInstalledSystem "x86_64-linux";
        "latheos-aarch64"       = mkInstalledSystem "aarch64-linux";
        "latheos-iso"           = mkInstallerIso   "x86_64-linux";
        "latheos-iso-aarch64"   = mkInstallerIso   "aarch64-linux";
      };

      # `nix build .#latheos-iso` — direct alias for the ISO artefact.
      # Only x86_64-linux is exposed as a package (cross-building the ISO
      # without QEMU binfmt silently fails on most laptops); aarch64 users
      # call the long `nixosConfigurations.latheos-iso-aarch64.*` path.
      packages.x86_64-linux = {
        latheos-iso = self.nixosConfigurations.latheos-iso.config.system.build.isoImage;
        default     = self.nixosConfigurations.latheos-iso.config.system.build.isoImage;
      };
    };
}
