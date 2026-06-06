################################################################################
# Cursor programmatic agent CLI — @cursor/sdk packaged for LatheOS.
#
# Installs `latheos-cursor-agent` on PATH (models / once / run subcommands).
# Requires CURSOR_API_KEY at runtime; see docs/LATHEOS_VIBE_PLATFORM.md.
################################################################################

{ config, pkgs, lib, ... }:

let
  latheosCursorAgent = pkgs.callPackage ../pkgs/latheos-cursor-agent.nix { };
in
{
  environment.systemPackages = [ latheosCursorAgent ];

  # Defaults aligned with modules/embedded-shell.nix (project root on exFAT).
  environment.etc."latheos/cursor-programmatic.env".text = ''
    LATHEOS_PROJECT_ROOT=/assets/projects
  '';
}
