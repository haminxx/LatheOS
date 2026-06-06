################################################################################
# LatheOS Tiered Text-to-Speech.
#
# Two voices, one router:
#
#   * Piper   — tiny ONNX voice, CPU-only. THE DEFAULT on every machine the USB
#               lands on. Provisioned by modules/local-llm.nix (piper-tts +
#               LATHEOS_PIPER_VOICE). Nothing here is required for Piper to work.
#   * MisoTTS — MisoLabs' 8B emotive TTS. OPT-IN, GPU-only (~24 GB VRAM). Served
#               by latheos-tts.service over loopback HTTP on 127.0.0.1:11436 and
#               wrapped by platform/tts-worker (lathe-tts).
#
# How the daemon picks a voice (cam_daemon/tts.py):
#   LATHEOS_TTS_BACKEND = auto | piper | miso
#     auto  -> Piper, UNLESS cam-tts-autoselect promoted us to miso because a
#              capable GPU + the weights were found (it writes
#              /run/latheos/tts-backend.env, loaded after this file).
#     piper -> force Piper.   miso -> try MisoTTS, fall back to Piper on error.
#
# This mirrors modules/vision-grounding.nix: the heavy torch closure and the
# service only land when `latheos.tts.miso.enable = true`, and the runtime
# contract (env file) is always written so the daemon/greeter know the URL.
#
# Source:  https://github.com/MisoLabsAI/MisoTTS
# LICENSE: see MisoTTS upstream. English-only; output is watermarked.
#
# Enable with:  latheos.tts.miso.enable = true;
# and bake/provision weights with:  WITH_MISO=1 scripts/prefetch-models.sh
################################################################################

{ config, pkgs, lib, ... }:

let
  cfg = config.latheos.tts;

  # ---- shared constants (keep in sync with platform/tts-worker + docs) ----
  modelDir = "/assets/models/miso";
  ttsHost = "127.0.0.1";
  ttsPort = 11436;
  ttsUrl = "http://${ttsHost}:${toString ttsPort}";
  # A capable GPU for MisoTTS bf16 needs ~24 GB; require a little under that so
  # 24 GB cards (which report ~24576 MiB) comfortably qualify.
  minVramMiB = 23000;

  py = pkgs.python3Packages;

  # Light deps only at build time; the MisoTTS runtime is provisioned via a
  # venv on /assets (LATHEOS_MISO_VENV). Mirrors the vision worker.
  ttsWorker = py.buildPythonApplication {
    pname = "lathe-tts";
    version = "0.1.0";
    src = ../platform/tts-worker;
    pyproject = true;

    nativeBuildInputs = [ py.setuptools pkgs.makeWrapper ];
    propagatedBuildInputs = [ py.numpy ];

    pythonImportsCheck = [ "lathe_tts" ];
    doCheck = false;

    meta = {
      description = "LatheOS premium voice worker (MisoTTS 8B)";
      # Our wrapper is MIT; the MODEL WEIGHTS are MisoLabs' and are NOT
      # redistributed by this package — the user fetches them. See docs/VOICE_TTS.md.
      license = lib.licenses.mit;
      mainProgram = "lathe-tts";
      platforms = lib.platforms.linux;
    };
  };

  enableStr = if cfg.miso.enable then "1" else "0";
in
{
  ##############################################################################
  # 0. Options
  ##############################################################################
  options.latheos.tts = {
    miso.enable = lib.mkEnableOption ''
      MisoTTS 8B premium voice (opt-in, GPU-only, ~24 GB VRAM, English-only,
      watermarked output). Piper remains the default; this only runs on a
      machine with a capable GPU. Pulls a large torch closure when enabled
    '';

    miso.speaker = lib.mkOption {
      type = lib.types.int;
      default = 0;
      description = "MisoTTS speaker id used for the assistant voice.";
    };

    miso.maxAudioMs = lib.mkOption {
      type = lib.types.int;
      default = 20000;
      description = "Max synthesized audio length per turn, in milliseconds.";
    };
  };

  ##############################################################################
  # 1. Runtime contract (always written) + GPU autoselect.
  #    Heavy build + MisoTTS service only land when miso.enable is true.
  ##############################################################################
  config = lib.mkMerge [
    {
      # Drop-in env consumed by the daemon TTS router, the greeter, and (when
      # enabled) latheos-tts.service. Always present so callers know the URL +
      # default backend even on a Piper-only machine.
      environment.etc."latheos/tts.env".text = ''
        # --- LatheOS tiered TTS ---------------------------------------------
        # Backend selection: auto|piper|miso. "auto" => Piper unless the GPU
        # autoselect promotes us to miso (see /run/latheos/tts-backend.env).
        LATHEOS_TTS_BACKEND=auto
        LATHEOS_TTS_URL=${ttsUrl}
        LATHEOS_TTS_HOST=${ttsHost}
        LATHEOS_TTS_PORT=${toString ttsPort}

        # --- MisoTTS (premium voice; opt-in, GPU-only) ----------------------
        LATHEOS_MISO_ENABLE=${enableStr}
        LATHEOS_MISO_MODEL=${modelDir}
        LATHEOS_MISO_CODE_DIR=${modelDir}
        LATHEOS_MISO_SPEAKER=${toString cfg.miso.speaker}
        LATHEOS_MISO_MAX_MS=${toString cfg.miso.maxAudioMs}
        LATHEOS_MISO_DEVICE=cuda
        LATHEOS_TTS_MIN_VRAM_MIB=${toString minVramMiB}
        # The MisoTTS runtime is pinned to Python 3.10 upstream; provision it in
        # a venv on the exFAT partition and the service prefers that interpreter:
        #   git clone https://github.com/MisoLabsAI/MisoTTS ${modelDir}
        #   cd ${modelDir} && uv sync --python 3.10
        #   .venv/bin/pip install /path/to/LatheOS/platform/tts-worker
        LATHEOS_MISO_VENV=${modelDir}/.venv
      '';

      # Weights dir on exFAT — created even on a fresh stick so prefetch /
      # provisioning have somewhere to land.
      systemd.tmpfiles.rules = [
        "d /assets/models/miso 0755 dev users - -"
      ];

      # GPU-based voice autoselect. Runs every boot (before cam-daemon) so
      # moving the stick between a laptop and a workstation upgrades / downgrades
      # the voice for free. Writes LATHEOS_TTS_BACKEND into a drop-in the daemon
      # and greeter load. Only ever picks "miso" when the feature is enabled,
      # the weights are present, AND a >= ~24 GB GPU is visible.
      systemd.services.cam-tts-autoselect = {
        description = "LatheOS — pick TTS voice (Piper vs MisoTTS) based on GPU";
        wantedBy = [ "multi-user.target" ];
        before   = [ "cam-daemon.service" ];
        after    = [ "assets.mount" ];
        serviceConfig.Type = "oneshot";
        serviceConfig.RemainAfterExit = true;
        # coreutils + gawk are all we need; nvidia-smi (when the host has the
        # NVIDIA driver) is picked up from the system profile by the PATH probe
        # below. No GPU / no nvidia-smi simply means VRAM=0 -> Piper.
        path = [ pkgs.coreutils pkgs.gawk ];
        script = ''
          set -u
          export PATH="$PATH:/run/current-system/sw/bin:/run/wrappers/bin"
          mkdir -p /run/latheos
          PICK=piper
          ENABLE=${enableStr}
          WEIGHTS_OK=0
          if [ -d "${modelDir}" ] && [ -n "$(ls -A "${modelDir}" 2>/dev/null)" ]; then
            WEIGHTS_OK=1
          fi

          VRAM=0
          if command -v nvidia-smi >/dev/null 2>&1; then
            VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null \
              | sort -n | tail -1 || echo 0)
            [ -z "$VRAM" ] && VRAM=0
          fi

          if [ "$ENABLE" = "1" ] && [ "$WEIGHTS_OK" = "1" ] && [ "$VRAM" -ge ${toString minVramMiB} ]; then
            PICK=miso
          fi

          printf 'LATHEOS_TTS_BACKEND=%s\n' "$PICK" > /run/latheos/tts-backend.env
          echo "cam-tts-autoselect: enable=$ENABLE weights=$WEIGHTS_OK vram=$VRAM MiB -> backend=$PICK"
        '';
      };
    }

    (lib.mkIf cfg.miso.enable {
      environment.systemPackages = [ ttsWorker ];

      ##########################################################################
      # 2. MisoTTS server — loopback-only, GPU-gated, crash-proof.
      ##########################################################################
      # Degrades gracefully: if disabled, weights missing, or no CUDA GPU, it
      # logs and exits 0 so a GPU-less boot never thrashes the unit. Mirrors
      # latheos-vision.service.
      systemd.services.latheos-tts = {
        description = "LatheOS — premium voice server (MisoTTS 8B)";
        after    = [ "assets.mount" ];
        requires = [ "assets.mount" ];
        wantedBy = [ "multi-user.target" ];

        serviceConfig = {
          Type = "simple";
          SuccessExitStatus = "0";
          Restart = "on-failure";
          RestartSec = "5s";
          User = "dev";
          Group = "users";
          EnvironmentFile = [
            "/etc/latheos/tts.env"
            "-/persist/secrets/tts.env"   # optional per-drive override
          ];
          NoNewPrivileges = true;
          ProtectSystem = "strict";
          ProtectHome = "read-only";
          PrivateTmp = true;
          ReadWritePaths = [ "/assets/models/miso" "/persist/cache" ];
        };

        # Bail early if disabled / no weights; prefer the venv interpreter (the
        # MisoTTS runtime is pinned to Python 3.10 upstream) when present.
        script = ''
          set -u
          if [ "''${LATHEOS_MISO_ENABLE:-0}" != "1" ]; then
            echo "latheos-tts: disabled (LATHEOS_MISO_ENABLE!=1) — exiting cleanly."
            exit 0
          fi
          if [ ! -d "''${LATHEOS_MISO_MODEL:-${modelDir}}" ] || \
             [ -z "$(ls -A "''${LATHEOS_MISO_MODEL:-${modelDir}}" 2>/dev/null)" ]; then
            echo "latheos-tts: no weights at ''${LATHEOS_MISO_MODEL:-${modelDir}} — exiting cleanly."
            echo "  Provision them with: WITH_MISO=1 scripts/prefetch-models.sh (see docs/VOICE_TTS.md)"
            exit 0
          fi

          VENV="''${LATHEOS_MISO_VENV:-}"
          if [ -n "$VENV" ] && [ -x "$VENV/bin/python" ]; then
            echo "latheos-tts: using venv interpreter $VENV/bin/python"
            exec "$VENV/bin/python" -m lathe_tts serve
          fi

          echo "latheos-tts: using Nix interpreter (lathe-tts)"
          exec ${ttsWorker}/bin/lathe-tts serve
        '';
      };
    })
  ];
}
