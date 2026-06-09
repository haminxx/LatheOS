################################################################################
# CAM Local Daemon — packaged as a Nix derivation, run as a systemd service.
#
# The daemon is built from ../daemon so a single `nixos-rebuild switch`
# rebuilds both the OS *and* the daemon image together. No pip, no venvs,
# no drift.
#
# PRIVACY-FIRST / FULLY LOCAL: the entire voice loop runs on the USB —
#   wake word  -> openWakeWord / clap / push-to-talk   (cam_daemon.wake)
#   listen     -> energy VAD utterance capture          (cam_daemon.stt)
#   transcribe -> whisper.cpp                            (cam_daemon.stt)
#   think      -> local Ollama multi-agent              (cam_daemon.agents)
#   speak      -> Piper (default) / MisoTTS (opt-in GPU) (cam_daemon.tts)
# No cloud, no account, no hardware token. Nothing leaves the machine.
#
# Design trade-off: Porcupine (wake-word) and aubio (clap onset) are NOT
# carried in nixpkgs reliably, so we keep them as *optional* extras that an
# overlay can inject. The daemon itself starts cleanly without them — in
# degraded mode it will simply wait for a control-socket nudge from `camctl`
# rather than mic activation. This keeps first-boot stable on fresh channels
# while letting advanced users opt-in via overlay.
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
      py.sounddevice
      py.numpy
      py.orjson
      py.structlog
      py.httpx
    ] ++ optionalWakePkgs;
    doCheck = false;
    # sounddevice dlopens libportaudio at runtime; point LD_LIBRARY_PATH at it.
    # The local voice loop shells out to whisper.cpp (STT) and piper (TTS), so
    # put those on the daemon's PATH explicitly — independent of whatever else
    # the system profile happens to expose.
    postFixup = ''
      wrapProgram $out/bin/cam-daemon \
        --prefix LD_LIBRARY_PATH : ${lib.makeLibraryPath [ pkgs.portaudio ]} \
        --prefix PATH : ${lib.makeBinPath [ pkgs.openai-whisper-cpp pkgs.piper-tts pkgs.alsa-utils pkgs.ffmpeg ]}
    '';
    meta = {
      description = "LatheOS on-device voice assistant (wake -> STT -> Ollama -> TTS)";
      license = lib.licenses.mit;
      platforms = lib.platforms.linux;
    };
  };

in {
  environment.systemPackages = [ camDaemon ];

  # Runtime configuration — overridden at flash time via an env-file on the
  # persistent partition. Never commit this file to the store.
  #
  # LatheOS is PRIVACY-FIRST / LOCAL-DEFAULT: the daemon transcribes with
  # whisper.cpp, reasons with the on-disk Ollama instance (modules/
  # local-llm.nix), and speaks with Piper/MisoTTS. Hermes can ALSO route a task
  # to a cloud frontier model (Engine B), but only when the user opts in
  # (LATHEOS_CLOUD_ENABLE=1) AND confirms that specific task by voice — nothing
  # leaves the device otherwise. The model names + endpoints come from
  # /etc/latheos/llm.env; this file only carries the daemon's own knobs.
  environment.etc."latheos/cam.env".text = ''
    CAM_SAMPLE_RATE=16000

    # --- Speech to text (whisper.cpp; see modules/local-llm.nix) ---------
    # The model path itself (LATHEOS_WHISPER_MODEL) is set in llm.env. Pin the
    # binary + language here if you need to.
    LATHEOS_WHISPER_BIN=whisper-cpp
    # LATHEOS_WHISPER_LANG=en   # "auto" (default) lets whisper detect it.

    # --- Wake word -------------------------------------------------------
    # Default backend is openWakeWord (Apache-2.0, ONNX, no vendor key).
    # Swap to "porcupine" on a drive that has a valid PICOVOICE_ACCESS_KEY,
    # or "none" to disable and rely purely on clap + $mod+space PTT.
    LATHEOS_WAKE_BACKEND=oww

    # --- Voice-triggered command execution (OFF by default) --------------
    # When 1, a spoken request whose worker output contains a JSON
    # {"action":...,"command":...} object is dispatched through the
    # allowlisted executor (cam_daemon/executor.py). Keep it 0 unless you
    # explicitly want voice to be able to run vetted commands.
    LATHEOS_VOICE_EXEC=0

    # Overridden by /persist/secrets/cam.env on drives that opt into Porcupine:
    #   PICOVOICE_ACCESS_KEY=<Picovoice console key>     (only for porcupine backend)
    #   CAM_KEYWORD_PATH=/etc/latheos/hey-cam.ppn        (only for porcupine backend)
    #   LATHEOS_WAKE_BACKEND=oww|porcupine|none
  '';

  systemd.services.cam-daemon = {
    description = "CAM on-device voice assistant (wake -> whisper -> Ollama -> Piper/MisoTTS)";
    wantedBy = [ "graphical-session.target" ];
    after    = [ "graphical-session.target" "pipewire.service" ];
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
      # Writable runtime dir for the control socket + the lathe event bus
      # (events.jsonl). RuntimeDirectory is auto-created writable even under
      # ProtectSystem=strict.
      RuntimeDirectory = "cam-daemon";
      RuntimeDirectoryMode = "0750";
      EnvironmentFile = [
        "/etc/latheos/cam.env"
        "/etc/latheos/llm.env"            # local model names + endpoint + voice/whisper paths
        "/etc/latheos/tts.env"            # TTS router defaults (backend + URL); see tts.nix
        "/etc/latheos/camera.env"         # webcam device + scene-description VLM; see camera.nix
        "-/etc/latheos/vision.env"        # LocateAnything grounding URL/enable; see vision-grounding.nix
        "-/etc/latheos/pilot.env"         # Screen Pilot enable/flags + endpoints; see screen-pilot.nix
        "-/run/latheos/heavy-model.env"   # RAM-based heavy-model autoselect (local-llm.nix)
        "-/run/latheos/tts-backend.env"   # GPU-based TTS autoselect (tts.nix)
        "-/run/latheos/cloud.env"         # cloud API key decrypted from the vault (local-llm.nix)
        "-/persist/secrets/cam.env"       # optional per-drive overrides (e.g. Picovoice key)
        "-/persist/secrets/llm.env"       # optional per-drive model overrides
        "-/persist/secrets/camera.env"    # optional per-drive camera override
        "-/persist/secrets/pilot.env"     # optional per-drive Screen Pilot override
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
      # Hermes memory needs to persist beyond /run: the General vector DB
      # (/persist/cache/llm) and Core/Trend reads (/persist/state). ProtectHome
      # is read-only, so these explicit RW paths are how the daemon writes them.
      ReadWritePaths = [ "/run/user" "/persist/cache/llm" "/persist/state" ];
    };
  };
}
