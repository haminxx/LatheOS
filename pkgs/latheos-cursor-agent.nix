{
  lib,
  buildNpmPackage,
  nodejs_22,
  python3,
  sqlite,
}:

# npmDepsHash: refresh when package-lock.json changes:
#   nix run nixpkgs/nixos-24.11#prefetch-npm-deps -- platform/cursor-programmatic/package-lock.json
(buildNpmPackage.override { nodejs = nodejs_22; }) rec {
  pname = "latheos-cursor-agent";
  version = "0.1.0";

  src = ../platform/cursor-programmatic;

  npmDepsHash = "sha256-3g4AqcWkGpngSsQRi7fUkgGBONtNixCjh1KbFCER3cA=";

  npmInstallFlags = [ "--include=dev" ];

  nativeBuildInputs = [ python3 ];

  buildInputs = [ sqlite ];

  strictDeps = false;

  doCheck = false;

  meta = {
    description = "LatheOS CLI bridge to Cursor @cursor/sdk (local and cloud agents)";
    license = with lib.licenses; [ mit unfree ];
    mainProgram = "latheos-cursor-agent";
    platforms = lib.platforms.linux;
  };
}
