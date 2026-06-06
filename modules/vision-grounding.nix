################################################################################
# LatheOS Vision Grounding — NVIDIA LocateAnything-3B (OPT-IN, GPU-only).
#
# Adds a *visual grounding* brain alongside the Ollama text stack: given an
# image + a natural-language query it returns bounding boxes / points —
# "where is the search button", "locate every resistor", "point to the mug".
# This powers GUI grounding for the agentic editor and on-device perception.
#
# This is deliberately SEPARATE from modules/local-llm.nix because the
# constraints are different and much heavier:
#
#   * Hardware : needs an NVIDIA GPU (Ampere/Ada/Hopper/Blackwell), Linux.
#                CPU works but is far too slow to be useful.
#   * Runtime  : HF Transformers (transformers==4.57.1) with
#                `trust_remote_code=True` — NOT Ollama. Runs as a small local
#                HTTP server on 127.0.0.1:11435 (loopback only).
#   * Weights  : ~8 GB BF16. Live on the exFAT partition at
#                /assets/models/locateanything so they survive nixos-rebuild
#                and can be managed from Windows/macOS (same policy as Ollama).
#   * LICENSE  : NVIDIA NON-COMMERCIAL (research / non-profit ONLY). The vision
#                encoder MoonViT is MIT; the LM Qwen2.5-3B is under the Qwen
#                Research License. Commercial use is NOT permitted. THIS is the
#                main reason the feature is disabled by default.
#
# Sources:
#   https://huggingface.co/nvidia/LocateAnything-3B
#   https://github.com/NVlabs/Eagle/tree/main/Embodied
#   https://research.nvidia.com/labs/lpr/locate-anything/
#
# Enable it with, in your config:
#     latheos.vision.enable = true;
# …and bake the weights with `WITH_VISION=1 scripts/prefetch-models.sh`.
# Because the torch+CUDA closure is large, the build only pulls it in when the
# option is true. At runtime you can still flip it off without a rebuild by
# setting LATHEOS_VISION_ENABLE=0 in /persist/secrets/vision.env.
################################################################################

{ config, pkgs, lib, ... }:

let
  cfg = config.latheos.vision;

  # ---- shared constants (keep in sync with platform/vision-worker + docs) ----
  modelDir = "/assets/models/locateanything";
  visionHost = "127.0.0.1";
  visionPort = 11435;
  visionUrl = "http://${visionHost}:${toString visionPort}";

  py = pkgs.python3Packages;

  # The heavy ML stack. nixpkgs may not carry the exact pins the model card
  # wants (transformers==4.57.1) and the custom remote code expects them, so we
  # take whatever nixpkgs exposes (best-effort, version-gated below) and let
  # users override with a venv on /assets if they need the exact pins. Each dep
  # is optional: a missing one just means the service probe fails gracefully
  # and the unit exits 0 instead of crashing the boot.
  optionalMlPkgs =
       lib.optionals (py ? torch)         [ py.torch ]
    ++ lib.optionals (py ? torchvision)   [ py.torchvision ]
    ++ lib.optionals (py ? transformers)  [ py.transformers ]
    ++ lib.optionals (py ? accelerate)    [ py.accelerate ]
    ++ lib.optionals (py ? safetensors)   [ py.safetensors ]
    ++ lib.optionals (py ? sentencepiece) [ py.sentencepiece ]
    ++ lib.optionals (py ? peft)          [ py.peft ]
    ++ lib.optionals (py ? einops)        [ py.einops ]
    ++ lib.optionals (py ? opencv4)       [ py.opencv4 ];

  visionWorker = py.buildPythonApplication {
    pname = "lathe-vision";
    version = "0.1.0";
    src = ../platform/vision-worker;
    pyproject = true;

    nativeBuildInputs = [ py.setuptools pkgs.makeWrapper ];
    # Light deps are always present; the ML stack is best-effort (see above).
    propagatedBuildInputs = [
      py.pillow
      py.numpy
    ] ++ optionalMlPkgs;

    # The light deps are all we declare as hard requirements in pyproject; the
    # [gpu] extra is intentionally not resolved at build time.
    pythonImportsCheck = [ "lathe_vision" ];
    doCheck = false;

    meta = {
      description = "LatheOS visual grounding worker (NVIDIA LocateAnything-3B)";
      # The wrapper code is MIT (ours); the MODEL WEIGHTS are NVIDIA
      # non-commercial and are NOT redistributed by this package — the user
      # fetches them. See docs/VISION_GROUNDING.md.
      license = lib.licenses.mit;
      mainProgram = "lathe-vision";
      platforms = lib.platforms.linux;
    };
  };

  enableStr = if cfg.enable then "1" else "0";
in
{
  ##############################################################################
  # 0. Option
  ##############################################################################
  options.latheos.vision = {
    enable = lib.mkEnableOption ''
      NVIDIA LocateAnything-3B visual grounding (GPU-only, non-commercial
      license). Pulls a large torch+CUDA closure; keep off unless you have an
      NVIDIA GPU and accept the research-only license terms
    '';

    mode = lib.mkOption {
      type = lib.types.enum [ "fast" "slow" "hybrid" ];
      default = "hybrid";
      description = "Generation mode. 'hybrid' (model-card default) balances speed/accuracy.";
    };

    maxNewTokens = lib.mkOption {
      type = lib.types.int;
      default = 8192;
      description = "Max newly generated tokens (model-card recommends 8192).";
    };
  };

  ##############################################################################
  # 1. Runtime contract — always written so client tools know the endpoint.
  #    Heavy build + service only land when the option is enabled.
  ##############################################################################
  config = lib.mkMerge [
    {
      # Drop-in env file consumed by latheos-vision.service and the client
      # helpers (lathe_shell/vision.py, daemon). Separate from llm.env so the
      # vision feature is fully self-contained and easy to flip per-drive via
      # /persist/secrets/vision.env without a rebuild.
      environment.etc."latheos/vision.env".text = ''
        # --- LatheOS visual grounding (NVIDIA LocateAnything-3B) -------------
        # OPT-IN, GPU-only, NON-COMMERCIAL license. See docs/VISION_GROUNDING.md.
        # Flip LATHEOS_VISION_ENABLE without a rebuild by overriding it in
        # /persist/secrets/vision.env (loaded after this file by the service).
        LATHEOS_VISION_ENABLE=${enableStr}
        LATHEOS_VISION_URL=${visionUrl}
        LATHEOS_VISION_HOST=${visionHost}
        LATHEOS_VISION_PORT=${toString visionPort}
        LATHEOS_VISION_MODEL=${modelDir}
        LATHEOS_VISION_MODE=${cfg.mode}
        LATHEOS_VISION_MAX_NEW_TOKENS=${toString cfg.maxNewTokens}
        LATHEOS_VISION_DEVICE=cuda
        # Optional escape hatch for the exact model-card pins (transformers==
        # 4.57.1 etc.). If this interpreter exists the service uses it instead
        # of the Nix python. Create it on the exFAT partition and install BOTH
        # the heavy stack AND our worker package into it, e.g.:
        #   python -m venv /assets/models/locateanything/.venv
        #   .venv/bin/pip install torch torchvision transformers==4.57.1 \
        #       opencv-python-headless==4.11.0.86 numpy==1.25.0 Pillow==11.1.0 \
        #       peft decord==0.6.0 lmdb==1.7.5
        #   .venv/bin/pip install /path/to/LatheOS/platform/vision-worker
        LATHEOS_VISION_VENV=${modelDir}/.venv
      '';

      # Weights dir on exFAT — created even on a fresh stick so prefetch /
      # first-run staging have somewhere to land.
      systemd.tmpfiles.rules = [
        "d /assets/models/locateanything 0755 dev users - -"
      ];
    }

    (lib.mkIf cfg.enable {
      environment.systemPackages = [ visionWorker ];

      ##########################################################################
      # 2. The vision server — loopback-only, GPU-gated, crash-proof.
      ##########################################################################
      # Runs as a small local HTTP server on 127.0.0.1:11435. Degrades
      # gracefully: if LATHEOS_VISION_ENABLE!=1, the weights are missing, or no
      # CUDA GPU is visible, it logs and exits 0 (SuccessExitStatus) so a
      # GPU-less boot never thrashes the unit. Mirrors the non-fatal pattern of
      # latheos-llm-bootstrap in modules/local-llm.nix.
      systemd.services.latheos-vision = {
        description = "LatheOS — visual grounding server (NVIDIA LocateAnything-3B)";
        # Needs the exFAT partition (weights live there) and is best-effort.
        after    = [ "assets.mount" "network-online.target" ];
        wants    = [ "network-online.target" ];
        requires = [ "assets.mount" ];
        wantedBy = [ "multi-user.target" ];

        serviceConfig = {
          Type = "simple";
          # 0 = clean "no GPU / disabled / no weights" exit; do not treat as
          # failure so Restart=on-failure won't fight a machine without a GPU.
          SuccessExitStatus = "0";
          Restart = "on-failure";
          RestartSec = "5s";
          User = "dev";
          Group = "users";
          EnvironmentFile = [
            "/etc/latheos/vision.env"
            "-/persist/secrets/vision.env"   # optional per-drive override
          ];
          # Let the model see the GPU; everything else stays sandboxed.
          NoNewPrivileges = true;
          ProtectSystem = "strict";
          ProtectHome = "read-only";
          PrivateTmp = true;
          # The weights + an optional venv live under /assets; cache under
          # /persist. Grant write only where the runtime actually needs it.
          ReadWritePaths = [ "/assets/models/locateanything" "/persist/cache" ];
        };

        # A thin launcher: bail early if disabled / no weights, pick the venv
        # python if the user provisioned one for the exact pins, else the Nix
        # interpreter. Keeps heavy imports out of the path until we know a GPU
        # run is even wanted.
        script = ''
          set -u
          if [ "''${LATHEOS_VISION_ENABLE:-0}" != "1" ]; then
            echo "latheos-vision: disabled (LATHEOS_VISION_ENABLE!=1) — exiting cleanly."
            exit 0
          fi
          if [ ! -d "''${LATHEOS_VISION_MODEL:-${modelDir}}" ] || \
             [ -z "$(ls -A "''${LATHEOS_VISION_MODEL:-${modelDir}}" 2>/dev/null)" ]; then
            echo "latheos-vision: no weights at ''${LATHEOS_VISION_MODEL:-${modelDir}} — exiting cleanly."
            echo "  Bake them with: WITH_VISION=1 scripts/prefetch-models.sh"
            exit 0
          fi

          VENV="''${LATHEOS_VISION_VENV:-}"
          if [ -n "$VENV" ] && [ -x "$VENV/bin/python" ]; then
            echo "latheos-vision: using venv interpreter $VENV/bin/python"
            exec "$VENV/bin/python" -m lathe_vision serve
          fi

          echo "latheos-vision: using Nix interpreter (lathe-vision)"
          exec ${visionWorker}/bin/lathe-vision serve
        '';
      };
    })
  ];
}
