################################################################################
# LatheOS flake — dual-arch reproducible system image.
#
# Build:
#   nixos-rebuild switch --flake .#latheos-x86_64
#   nixos-rebuild switch --flake .#latheos-aarch64
#
# Both targets share `configuration.nix` verbatim; only the nixpkgs system
# string differs. This is what guarantees "Mac ARM and PC x86" portability.
#
# Home-Manager is an optional input. If pinned, `modules/home.nix` is imported
# automatically via `specialArgs.inputs`. To build the OS without HM just
# remove the home-manager line from inputs and the nixosConfiguration below.
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
      mkSystem = system:
        nixpkgs.lib.nixosSystem {
          inherit system;
          specialArgs = { inherit inputs; };
          modules = [
            ./configuration.nix
            home-manager.nixosModules.home-manager
            ./modules/home.nix
          ];
        };
    in {
      nixosConfigurations = {
        "latheos-x86_64"  = mkSystem "x86_64-linux";
        "latheos-aarch64" = mkSystem "aarch64-linux";
      };
    };
}
