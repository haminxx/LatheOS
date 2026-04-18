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

  # Optional wake-word packages — pulled in only if the overlay provides them.
  optionalWakePkgs =
       lib.optionals (py ? pvporcupine) [ py.pvporcupine ]
    ++ lib.optionals (py ? aubio)       [ py.aubio ];

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
  environment.etc."latheos/cam.env".text = ''
    CAM_PROXY_URL=wss://cam.example.com/ws/cam
    CAM_SAMPLE_RATE=16000
    # Overridden by /persist/secrets/cam.env on production drives:
    #   PICOVOICE_ACCESS_KEY=<Picovoice console key>
    #   CAM_HARDWARE_TOKEN=<32-char HW fingerprint>
    #   CAM_KEYWORD_PATH=/etc/latheos/hey-cam.ppn
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
        "-/persist/secrets/cam.env"   # optional, overrides the baked values
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
