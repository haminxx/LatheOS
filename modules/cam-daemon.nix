################################################################################
# CAM Local Daemon — packaged as a Nix derivation, run as a systemd service.
#
# The daemon is built from ../daemon so a single `nixos-rebuild switch`
# rebuilds both the OS *and* the daemon image together. No pip, no venvs,
# no drift.
#
# Design trade-off: Porcupine (wake-word) and aubio (clap onset) are NOT
# carried in nixpkgs reliably, so we keep them as *optional* extras that an
# overlay can inject. The daemon itself starts cleanly without them — in
# degraded mode it will simply wait for a control-socket nudge from `camctl`
# (a future CLI) rather than mic activation. This keeps first-boot stable
# on fresh channels while letting advanced users opt-in via overlay.
################################################################################

{ config, pkgs, lib, ... }:

let
  py = pkgs.python3Packages;

  # Optional wake-word backends. We enable whichever nixpkgs exposes; the
  # daemon itself chooses which one runs at boot via LATHEOS_WAKE_BACKEND
  # (see daemon/cam_daemon/wake.py). Defaults to openWakeWord when present.
  #
  # Priority in our own overlays:
  #   openwakeword  -> Apache-2.0, ONNX, no vendor key (DEFAULT)
  #   pvporcupine   -> proprietary, needs PICOVOICE_ACCESS_KEY
  #   aubio         -> always-on clap onset detector
  optionalWakePkgs =
       lib.optionals (py ? openwakeword) [ py.openwakeword py.onnxruntime ]
    ++ lib.optionals (py ? pvporcupine)  [ py.pvporcupine ]
    ++ lib.optionals (py ? aubio)        [ py.aubio ];

  camDaemon = py.buildPythonApplication {
    pname = "cam-daemon";
    version = "0.1.0";
    src = ../daemon;
    pyproject = true;
    nativeBuildInputs = [ py.setuptools pkgs.makeWrapper ];
    propagatedBuildInputs = [
      py.websockets
      py.sounddevice
      py.numpy
      py.orjson
      py.structlog
      py.httpx
    ] ++ optionalWakePkgs;
    doCheck = false;
    # sounddevice dlopens libportaudio at runtime; point LD_LIBRARY_PATH at it.
    postFixup = ''
      wrapProgram $out/bin/cam-daemon \
        --prefix LD_LIBRARY_PATH : ${lib.makeLibraryPath [ pkgs.portaudio ]}
    '';
    meta = {
      description = "LatheOS local bridge to the CAM Cloud Proxy";
      license = lib.licenses.mit;
      platforms = lib.platforms.linux;
    };
  };

in {
  environment.systemPackages = [ camDaemon ];

  # Runtime configuration — overridden at flash time via an env-file on the
  # persistent partition. Never commit this file to the store.
  #
  # LatheOS is now LOCAL-FIRST: by default the daemon routes prompts to the
  # on-disk Ollama instance documented in modules/local-llm.nix. The cloud
  # proxy becomes an OPT-IN "bigger brain" — set CAM_PROXY_URL in
  # /persist/secrets/cam.env to enable it. Leaving it blank keeps the whole
  # OS offline-capable, which is required for the self-repair story (if the
  # network is what broke, the agent must still be able to fix it).
  environment.etc."latheos/cam.env".text = ''
    CAM_SAMPLE_RATE=16000

    # --- Local AI (always on; see modules/local-llm.nix) -----------------
    CAM_LOCAL_LLM_URL=http://127.0.0.1:11434
    # The daemon reads LATHEOS_VOICE_MODEL / LATHEOS_HEAVY_MODEL from
    # /etc/latheos/llm.env; no need to duplicate the model names here.

    # --- Wake word -------------------------------------------------------
    # Default backend is openWakeWord (Apache-2.0, ONNX, no vendor key).
    # Swap to "porcupine" on a drive that has a valid PICOVOICE_ACCESS_KEY,
    # or "none" to disable and rely purely on clap + $mod+space PTT.
    LATHEOS_WAKE_BACKEND=oww

    # --- Cloud proxy (OPT-IN) --------------------------------------------
    # Leave blank to stay fully offline. Set in /persist/secrets/cam.env to
    # route to the AWS-hosted CAM Cloud Proxy for larger models / streaming
    # STT + TTS.
    CAM_PROXY_URL=

    # Overridden by /persist/secrets/cam.env on production drives:
    #   PICOVOICE_ACCESS_KEY=<Picovoice console key>     (only for porcupine backend)
    #   CAM_HARDWARE_TOKEN=<32-char HW fingerprint>      (only if cloud enabled)
    #   CAM_KEYWORD_PATH=/etc/latheos/hey-cam.ppn        (only for porcupine backend)
    #   LATHEOS_WAKE_BACKEND=oww|porcupine|none
  '';

  systemd.services.cam-daemon = {
    description = "CAM Local Daemon (wake-word bridge to CAM Cloud Proxy)";
    wantedBy = [ "graphical-session.target" ];
    after    = [ "graphical-session.target" "pipewire.service" "network-online.target" ];
    wants    = [ "network-online.target" ];
    partOf   = [ "graphical-session.target" ];

    serviceConfig = {
      Type = "simple";
      ExecStart = "${camDaemon}/bin/cam-daemon";
      Restart = "on-failure";
      RestartSec = "2s";
      StartLimitIntervalSec = "30s";
      StartLimitBurst = 5;
      User = "dev";
      Group = "audio";
      SupplementaryGroups = [ "video" "input" ];
      EnvironmentFile = [
        "/etc/latheos/cam.env"
        "/etc/latheos/llm.env"        # local model names + local endpoint
        "-/persist/secrets/cam.env"   # optional, overrides the baked values
        "-/persist/secrets/llm.env"   # optional, per-drive model overrides
      ];

      # ---- hardening ----
      # The daemon only needs audio + network. Everything else is denied.
      NoNewPrivileges = true;
      ProtectSystem = "strict";
      ProtectHome = "read-only";
      PrivateTmp = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      ProtectClock = true;
      RestrictNamespaces = true;
      RestrictRealtime = false;         # we *do* want RT audio scheduling
      LockPersonality = true;
      MemoryDenyWriteExecute = false;   # sounddevice/portaudio JIT paths
      SystemCallArchitectures = "native";
      SystemCallFilter = [ "@system-service" "~@privileged" "~@resources" ];
      ReadWritePaths = [ "/run/user" ];
    };
  };
}
