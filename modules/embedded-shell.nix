################################################################################
# LatheOS Embedded Shell — Jarvis-style ASCII TUI.
#
# Builds `platform/embedded-shell/` as a real Python application. Exposes a
# `lathe` command on PATH, wires a Sway keybind hook point (actual bind is
# declared in modules/sway.nix), and installs a desktop entry so wofi can
# launch it.
#
# Packaging choices
# -----------------
#   * Uses nixpkgs python3Packages.* for every runtime dep so the build is
#     hermetic — no pip, no PyPI fetch during nixos-rebuild.
#   * lshw / lspci / pciutils / dmidecode are added to PATH via makeWrapper
#     so the hardware scanner finds them deterministically (not via the
#     user's $PATH).
#   * psutil wants sysstat's /proc helpers; all in nixpkgs.
#
# Runtime contract
# ----------------
#   * LATHEOS_LLM_URL         where to find local Ollama (default 127.0.0.1:11434)
#   * LATHEOS_VOICE_MODEL     Ollama tag of the voice model (default llama3.2:3b)
#   * LATHEOS_PROJECT_ROOT    cwd the terminal pane opens into (default /assets/projects)
################################################################################

{ config, pkgs, lib, ... }:

let
  py = pkgs.python3Packages;

  latheShell = py.buildPythonApplication {
    pname = "lathe-shell";
    version = "0.1.0";
    src = ../platform/embedded-shell;
    pyproject = true;

    nativeBuildInputs = [ py.setuptools pkgs.makeWrapper ];
    propagatedBuildInputs = [
      py.textual
      py.rich
      py.httpx
      py.orjson
      py.psutil
      py.structlog
    ];

    doCheck = false;

    # Hardware detection calls out to these binaries. Baking the paths onto
    # the wrapper means the scanner works the same under systemd, Sway, or
    # a TTY, regardless of the user's environment.
    postFixup = ''
      wrapProgram $out/bin/lathe \
        --prefix PATH : ${lib.makeBinPath [
          pkgs.pciutils       # lspci
          pkgs.usbutils       # lsusb
          pkgs.util-linux     # lsblk
          pkgs.dmidecode      # SMBIOS
          pkgs.lshw           # optional richer detail
          pkgs.coreutils
        ]}
    '';

    meta = {
      description = "LatheOS embedded shell — ASCII Jarvis TUI";
      license = lib.licenses.mit;
      mainProgram = "lathe";
      platforms = lib.platforms.linux;
    };
  };

in
{
  environment.systemPackages = [
    latheShell

    # Keep these here too so a curious user running `lathe` from a shell
    # that didn't inherit the wrapped PATH still gets sane output.
    pkgs.pciutils pkgs.usbutils pkgs.util-linux pkgs.dmidecode pkgs.lshw

    # LSPs the future Monaco-surface will need when we grow past the TUI.
    pkgs.nil
    pkgs.nodejs_22
  ];

  # Desktop entry so wofi lists "LatheOS Shell" alongside anything else.
  environment.etc."xdg/applications/latheos-shell.desktop".text = ''
    [Desktop Entry]
    Type=Application
    Name=LatheOS Shell
    Comment=Embedded editor, HUD, chat (local AI)
    Exec=foot -T "LatheOS Shell" lathe --color
    Icon=utilities-terminal
    Categories=Development;IDE;
    Terminal=false
  '';

  # Put the runtime URLs somewhere both this app and cam-daemon can source.
  # Duplicated with local-llm.nix on purpose so embedded-shell boots even
  # if local-llm.nix is disabled for a minimal build.
  environment.etc."latheos/embedded-shell.env".text = ''
    LATHEOS_LLM_URL=http://127.0.0.1:11434
    LATHEOS_VOICE_MODEL=llama3.2:3b
    LATHEOS_PROJECT_ROOT=/assets/projects
  '';
}
