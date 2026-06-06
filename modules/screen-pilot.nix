################################################################################
# LatheOS Screen Pilot — local, on-device on-screen guidance (OPT-IN).
#
# The "Clicky for LatheOS". When the user is lost ("how do I find X / where do
# I click / walk me through doing Y"), the pilot:
#   1. screenshots the Sway session with grim (tmpfile, never leaves the box),
#   2. asks the LOCAL Ollama vision model (LATHEOS_VLM_MODEL) for an ordered
#      step plan + a grounding phrase for the current step's target,
#   3. resolves that phrase to a pixel coord with the LOCAL, opt-in
#      LocateAnything-3B grounding service (modules/vision-grounding.nix),
#   4. moves the cursor there with ydotool (opt-in; uinput-based — Wayland
#      forbids ordinary synthetic input, hence the kernel route),
#   5. shows a small floating step card near the target via eww (wlr-layer-shell)
#      and narrates the step with the local TTS (Piper / MisoTTS).
#
# UX reference (NOT code): Clicky — https://github.com/farzaa/clicky — which is
# macOS + cloud. LatheOS is the OPPOSITE: Wayland/Sway and 100% LOCAL/PRIVATE.
# Everything is loopback-only; there is no cloud path and no telemetry.
#
# House style mirrored from modules/vision-grounding.nix and modules/tts.nix:
#   * latheos.screenPilot.enable, DEFAULT FALSE (opt-in).
#   * The runtime contract (/etc/latheos/pilot.env) is ALWAYS written so the
#     CLI / daemon / keybind know the endpoints + flags even when disabled.
#   * The heavier bits (the ydotoold input service, the tool closure, the
#     packaged worker) only land when the option is enabled.
#   * Graceful degradation everywhere: no GPU / vision off / no uinput / no eww
#     -> the pilot describes the steps (text + optional speech) and never moves
#     the cursor or crashes the session/boot.
#
# References:
#   ydotool (uinput input automation) : https://github.com/ReimuNotMoe/ydotool
#   wlr-layer-shell protocol          : https://wayland.app/protocols/wlr-layer-shell-unstable-v1
#   eww (wacky widgets)               : https://github.com/elkowar/eww
#   LocateAnything-3B                 : https://huggingface.co/nvidia/LocateAnything-3B
#
# Enable with:   latheos.screenPilot.enable = true;
# (and pull a vision model: `ollama pull llama3.2-vision`; for cursor targeting
#  also enable latheos.vision.enable and bake the grounding weights.)
################################################################################

{ config, pkgs, lib, ... }:

let
  cfg = config.latheos.screenPilot;

  py = pkgs.python3Packages;

  # The pilot has ZERO third-party Python deps (it talks to loopback services
  # over stdlib urllib and shells out to the tools below). We just wrap the
  # console script so grim / ydotool / eww / piper / a WAV player are on its
  # PATH regardless of what the rest of the system profile exposes.
  pilotRuntimeTools = [
    pkgs.grim            # wlroots screenshotter (capture)
    pkgs.ydotool         # uinput cursor move + click (primary input backend)
    pkgs.wl-clipboard    # occasionally handy for copy-driven flows
    pkgs.piper-tts       # local TTS (narration; same voice as the daemon)
    pkgs.alsa-utils      # aplay fallback player
    pkgs.ffmpeg          # ffplay fallback player
  ]
  # eww is the layer-shell overlay; gate on availability so aarch64 / minimal
  # channels still evaluate even if the attr is missing.
  ++ lib.optionals (pkgs ? eww)    [ pkgs.eww ]
  ++ lib.optionals (pkgs ? wlrctl) [ pkgs.wlrctl ]   # click fallback
  ++ lib.optionals (pkgs ? wtype)  [ pkgs.wtype ];   # keyboard fallback

  pilotWorker = py.buildPythonApplication {
    pname = "lathe-pilot";
    version = "0.1.0";
    src = ../platform/screen-pilot;
    pyproject = true;

    nativeBuildInputs = [ py.setuptools pkgs.makeWrapper ];
    # No propagated Python deps on purpose (see pyproject.toml).
    propagatedBuildInputs = [ ];

    pythonImportsCheck = [ "lathe_pilot" ];
    doCheck = false;

    # Put the external local tools on the worker's PATH so it finds them even
    # when launched by systemd / a Sway keybind with a minimal environment.
    postFixup = ''
      wrapProgram $out/bin/lathe-pilot \
        --prefix PATH : ${lib.makeBinPath pilotRuntimeTools}
    '';

    meta = {
      description = "LatheOS Screen Pilot — local on-screen guidance (capture/plan/ground/guide)";
      license = lib.licenses.mit;   # our wrapper is MIT; models are fetched by the user
      mainProgram = "lathe-pilot";
      platforms = lib.platforms.linux;
    };
  };

  enableStr      = if cfg.enable     then "1" else "0";
  allowMoveStr   = if cfg.allowMove  then "1" else "0";
  allowClickStr  = if cfg.allowClick then "1" else "0";
  speakStr       = if cfg.speak      then "1" else "0";

  ydotoolSocket = "/run/ydotoold/socket";
in
{
  ##############################################################################
  # 0. Options
  ##############################################################################
  options.latheos.screenPilot = {
    enable = lib.mkEnableOption ''
      LatheOS Screen Pilot: local, on-device on-screen guidance that moves the
      cursor and shows a floating step card. Pulls grim/ydotool/eww and starts
      a ydotoold (uinput) input service. Cursor TARGETING additionally needs a
      vision model (Ollama VLM) and, for pixel grounding, latheos.vision.enable.
      100% local — no cloud, loopback only
    '';

    allowMove = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Allow the pilot to MOVE the cursor to the resolved target (a
        non-destructive action). Disable to keep the pilot to "describe +
        floating card" only. No-ops cleanly if ydotoold/uinput are unavailable.
      '';
    };

    allowClick = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Allow CONFIRMED synthetic clicks. OFF by default and, even when on, the
        engine only clicks after an explicit per-step user confirmation — it
        NEVER auto-clicks. Mirrors the daemon executor's conservative,
        allowlisted philosophy.
      '';
    };

    overlay = lib.mkOption {
      type = lib.types.enum [ "eww" "none" ];
      default = "eww";
      description = "Floating step-card backend. 'eww' uses wlr-layer-shell; 'none' disables it.";
    };

    speak = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Narrate each step with the local TTS (Piper/MisoTTS). Falls back silently.";
    };

    vlmModel = lib.mkOption {
      type = lib.types.str;
      default = "llama3.2-vision";
      description = ''
        Vision-capable Ollama model used to build the step plan (pull it
        yourself, e.g. `ollama pull llama3.2-vision`). Matches the default in
        modules/camera.nix; override per-drive in /persist/secrets/pilot.env.
      '';
    };

    maxSteps = lib.mkOption {
      type = lib.types.int;
      default = 8;
      description = "Maximum number of steps in a single walkthrough.";
    };
  };

  ##############################################################################
  # 1. Runtime contract — always written so the CLI/daemon/keybind have config.
  #    Heavy build + the input service only land when the option is enabled.
  ##############################################################################
  config = lib.mkMerge [
    {
      # Drop-in env consumed by `lathe-pilot`, the cam-daemon intent path, and
      # the Sway keybind. Always present (even when disabled) so callers know
      # the endpoints + flags. Flip flags per-drive without a rebuild via
      # /persist/secrets/pilot.env (loaded after this file by the daemon).
      environment.etc."latheos/pilot.env".text = ''
        # --- LatheOS Screen Pilot (local on-screen guidance) ----------------
        # OPT-IN. 100% local / loopback only. See docs/SCREEN_PILOT.md.
        # Flip flags without a rebuild by overriding them in
        # /persist/secrets/pilot.env.
        LATHEOS_PILOT_ENABLE=${enableStr}

        # Local model endpoints (loopback only).
        LATHEOS_LLM_URL=http://127.0.0.1:11434
        LATHEOS_VISION_URL=http://127.0.0.1:11435
        # Step-planning vision model (pull with `ollama pull`). Shared with
        # modules/camera.nix; kept here too so the keybind/CLI path has it.
        LATHEOS_VLM_MODEL=${cfg.vlmModel}

        # Input gating. Movement is non-destructive (default on); clicking is
        # OFF by default and always needs an explicit per-step confirmation.
        LATHEOS_PILOT_ALLOW_MOVE=${allowMoveStr}
        LATHEOS_PILOT_ALLOW_CLICK=${allowClickStr}

        # Presentation.
        LATHEOS_PILOT_OVERLAY=${cfg.overlay}
        LATHEOS_PILOT_TTS=${speakStr}
        LATHEOS_PILOT_MAX_STEPS=${toString cfg.maxSteps}
        LATHEOS_PILOT_STEP_PAUSE=6.0

        # uinput input daemon socket (ydotoold; see the service below).
        YDOTOOL_SOCKET=${ydotoolSocket}
      '';

      # Make the socket path visible to the whole Sway session so the keybind /
      # interactive `lathe-pilot` finds ydotoold without sourcing the env file.
      environment.sessionVariables.YDOTOOL_SOCKET = ydotoolSocket;
    }

    (lib.mkIf cfg.enable {
      environment.systemPackages = [ pilotWorker ] ++ pilotRuntimeTools;

      # The cam-daemon launches `lathe-pilot` by name on the voice-guidance
      # intent; put it (wrapped, with its own tool closure) on the daemon unit's
      # PATH. `path` lists merge additively across modules, so this composes
      # with modules/cam-daemon.nix without editing it.
      systemd.services.cam-daemon.path = [ pilotWorker ];

      ##########################################################################
      # 2. uinput plumbing for synthetic input (the hard part on Wayland).
      ##########################################################################
      # Wayland clients cannot warp the pointer or inject clicks into other
      # clients (no XTEST). The supported route is the kernel uinput device,
      # driven by ydotoold. We load the module, expose /dev/uinput to the
      # `input` group, and run ydotoold AS the `dev` user (already in `input`)
      # so the socket it creates is naturally owned by the session user.
      boot.kernelModules = [ "uinput" ];

      # Static node + group/mode so /dev/uinput exists and the `input` group can
      # open it. `dev` is in `input` (see configuration.nix).
      services.udev.extraRules = ''
        KERNEL=="uinput", SUBSYSTEM=="misc", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"
      '';

      # ydotoold — the uinput input daemon. Loopback in spirit: it only owns a
      # local Unix socket and a virtual input device; it touches no network.
      systemd.services.ydotoold = {
        description = "LatheOS — ydotoold uinput input daemon (Screen Pilot cursor control)";
        wantedBy = [ "multi-user.target" ];
        after = [ "systemd-udev-settle.service" ];

        serviceConfig = {
          # Run as the session user so the socket is owned by `dev`. The user is
          # in `input`, and the udev rule grants `input` access to /dev/uinput.
          User = "dev";
          Group = "input";
          # RuntimeDirectory gives us a writable /run/ydotoold owned by dev.
          RuntimeDirectory = "ydotoold";
          RuntimeDirectoryMode = "0750";
          ExecStart =
            "${pkgs.ydotool}/bin/ydotoold "
            + "--socket-path=${ydotoolSocket} --socket-perm=0600";
          Restart = "on-failure";
          RestartSec = "3s";
          # If uinput is missing (locked-down kernel), fail soft: don't thrash.
          StartLimitIntervalSec = "30s";
          StartLimitBurst = 3;
        };
      };

      ##########################################################################
      # 3. tmpfiles — nothing persistent needed; ensure the runtime dir exists
      #    early (RuntimeDirectory also handles it, this is belt-and-braces).
      ##########################################################################
      systemd.tmpfiles.rules = [
        "d /run/ydotoold 0750 dev input - -"
      ];
    })
  ];
}
