#!/usr/bin/env bash
################################################################################
# prefetch-models.sh — bake AI weights into the LatheOS USB image.
#
# Pulls every model the OS needs on first boot and stages them under
# `dist/prefetch/` so build-usb-image.sh can copy them onto the exFAT
# partition before it seals the image. This is what makes the claim
# "works offline on first boot" actually true.
#
# What we download:
#   * Ollama voice model (llama3.2:3b)          ~2 GB q4
#   * Ollama heavy models (llama3.1:8b, maybe gemma3:12b)    ~5 / ~8 GB
#   * Ollama embeddings (nomic-embed-text) for Hermes memory ~270 MB
#   * Piper voices (en_US-amy-medium, ko_KR-kss-medium)      ~60 MB each
#   * Whisper.cpp ggml-base.en                               ~150 MB
#   * openWakeWord pretrained bundle (hey_jarvis + alexa)     ~3 MB
#
# OPT-IN (off by default; large + GPU-only):
#   * NVIDIA LocateAnything-3B visual grounding model         ~8 GB BF16
#     -> staged to dist/prefetch/locateanything, then onto
#        /assets/models/locateanything by build-usb-image.sh.
#     -> NON-COMMERCIAL license (research/non-profit only). See
#        docs/VISION_GROUNDING.md and modules/vision-grounding.nix.
#
# Usage
#   ./scripts/prefetch-models.sh                              # voice+small heavy (~7 GB)
#   HEAVY=big ./scripts/prefetch-models.sh                    # + codestral 22B (~22 GB)
#   SKIP_OLLAMA=1 ./scripts/prefetch-models.sh                # just Piper+whisper+OWW
#   WITH_VISION=1 ./scripts/prefetch-models.sh               # + LocateAnything-3B (~8 GB, GPU-only)
#   WITH_MISO=1 ./scripts/prefetch-models.sh                 # + MisoTTS premium voice (~30-40 GB, GPU-only)
#
# Requires: curl, ollama, python3 (for openWakeWord). The vision / MisoTTS
# downloads additionally need `huggingface-cli`/`hf` OR python `huggingface_hub`
# (and `git` for the MisoTTS source). Runs on any host with network access.
################################################################################

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/dist/prefetch"

HEAVY="${HEAVY:-small}"                  # small | big
SKIP_OLLAMA="${SKIP_OLLAMA:-0}"
SKIP_PIPER="${SKIP_PIPER:-0}"
SKIP_WHISPER="${SKIP_WHISPER:-0}"
SKIP_OWW="${SKIP_OWW:-0}"
WITH_VISION="${WITH_VISION:-0}"          # 1 = also fetch LocateAnything-3B (~8 GB)
WITH_MISO="${WITH_MISO:-0}"              # 1 = also fetch MisoTTS premium voice (~30-40 GB)

VOICE_MODEL="llama3.2:3b"
HEAVY_SMALL="llama3.1:8b"
HEAVY_BIG="gemma3:12b"                   # Engine A headline model (the architecture's "Gemma 12B")
EMBED_MODEL="nomic-embed-text"          # Hermes "General" memory embeddings
SKIP_EMBED="${SKIP_EMBED:-0}"

# NVIDIA LocateAnything-3B — visual grounding VLM. OPT-IN, GPU-only,
# NON-COMMERCIAL license (https://huggingface.co/nvidia/LocateAnything-3B).
VISION_REPO="nvidia/LocateAnything-3B"

# MisoTTS — 8B emotive TTS. OPT-IN, GPU-only, English-only, watermarked.
# Source repo provides generator.py/models.py; weights live on HF.
MISO_HF_REPO="MisoLabs/MisoTTS"
MISO_GIT="https://github.com/MisoLabsAI/MisoTTS.git"

# Piper voice CDN — the repo release mirror.
PIPER_CDN="https://huggingface.co/rhasspy/piper-voices/resolve/main"

# Whisper.cpp GGML models mirror.
WHISPER_CDN="https://huggingface.co/ggerganov/whisper.cpp/resolve/main"

log()  { printf '[prefetch] %s\n' "$*" >&2; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1" >&2; exit 1; }; }

mkdir -p \
  "${OUT}/ollama" \
  "${OUT}/piper" \
  "${OUT}/whisper" \
  "${OUT}/openwakeword"
[[ "$WITH_VISION" == 1 ]] && mkdir -p "${OUT}/locateanything"
[[ "$WITH_MISO" == 1 ]] && mkdir -p "${OUT}/miso"

# ---------------------------------------------------------------------------
# 1. Ollama models — use `ollama pull` with OLLAMA_MODELS pointed at our dir
#    so the binary blobs land where build-usb-image.sh expects them.
# ---------------------------------------------------------------------------
if [[ "$SKIP_OLLAMA" != 1 ]]; then
  need ollama
  export OLLAMA_MODELS="${OUT}/ollama"
  log "ollama models will land in ${OLLAMA_MODELS}"

  # Ollama needs its server running during `pull`. Start a private one on
  # a non-default port so we don't fight a user's existing install.
  : "${OLLAMA_HOST:=127.0.0.1:11555}"
  export OLLAMA_HOST
  ollama serve >"${OUT}/ollama-serve.log" 2>&1 &
  OLLAMA_PID=$!
  trap 'kill "$OLLAMA_PID" 2>/dev/null || true' EXIT

  # Wait for the API to come up.
  for _ in $(seq 1 20); do
    if curl -fsS "http://${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then break; fi
    sleep 1
  done

  log "pulling voice: ${VOICE_MODEL}"
  ollama pull "${VOICE_MODEL}"

  if [[ "$HEAVY" == "big" ]]; then
    log "pulling heavy (big): ${HEAVY_BIG}"
    ollama pull "${HEAVY_BIG}"
  else
    log "pulling heavy (small): ${HEAVY_SMALL}"
    ollama pull "${HEAVY_SMALL}"
  fi

  # Embeddings for Hermes "General" memory (vector RAG). Tiny; bake it in so
  # memory works offline on first boot. Skip with SKIP_EMBED=1.
  if [[ "$SKIP_EMBED" != 1 ]]; then
    log "pulling embeddings: ${EMBED_MODEL}"
    ollama pull "${EMBED_MODEL}"
  fi

  kill "$OLLAMA_PID" 2>/dev/null || true
  trap - EXIT
fi

# ---------------------------------------------------------------------------
# 2. Piper voices (en + ko).
# ---------------------------------------------------------------------------
if [[ "$SKIP_PIPER" != 1 ]]; then
  need curl
  declare -a VOICES=(
    "en/en_US/amy/medium/en_US-amy-medium.onnx"
    "en/en_US/amy/medium/en_US-amy-medium.onnx.json"
    "ko/ko_KR/kss/medium/ko_KR-kss-medium.onnx"
    "ko/ko_KR/kss/medium/ko_KR-kss-medium.onnx.json"
  )
  for v in "${VOICES[@]}"; do
    dst="${OUT}/piper/$(basename "$v")"
    if [[ -f "$dst" ]]; then
      log "piper hit cache: $(basename "$v")"
      continue
    fi
    log "fetch piper: $v"
    curl -L --fail --progress-bar -o "$dst" "${PIPER_CDN}/${v}"
  done
fi

# ---------------------------------------------------------------------------
# 3. Whisper.cpp base.en (the daemon reads /assets/models/whisper/ggml-base.en.bin).
# ---------------------------------------------------------------------------
if [[ "$SKIP_WHISPER" != 1 ]]; then
  need curl
  dst="${OUT}/whisper/ggml-base.en.bin"
  if [[ -f "$dst" ]]; then
    log "whisper hit cache: $(basename "$dst")"
  else
    log "fetch whisper: ggml-base.en.bin"
    curl -L --fail --progress-bar \
      -o "$dst" "${WHISPER_CDN}/ggml-base.en.bin"
  fi
fi

# ---------------------------------------------------------------------------
# 4. openWakeWord pretrained ONNX models. Uses the library's own bootstrap.
#    We download the bundle once here and mirror it into /assets so the
#    daemon never phones home on first boot.
# ---------------------------------------------------------------------------
if [[ "$SKIP_OWW" != 1 ]]; then
  need python3
  python3 - <<'PY' "${OUT}/openwakeword"
import os, sys, shutil
dst = sys.argv[1]
os.makedirs(dst, exist_ok=True)
try:
    # Works in both CI and dev so long as `pip install openwakeword` ran first.
    import openwakeword  # type: ignore
    from openwakeword.utils import download_models  # type: ignore
    download_models(target_directory=dst)
    print(f"openwakeword downloaded to {dst}", file=sys.stderr)
except Exception as e:  # noqa: BLE001
    print(f"openwakeword prefetch skipped: {e}", file=sys.stderr)
PY
fi

# ---------------------------------------------------------------------------
# 5. (OPT-IN) NVIDIA LocateAnything-3B visual grounding model.
#    Off by default — it's ~8 GB and only useful on an NVIDIA GPU. The OS
#    feature is itself opt-in (latheos.vision.enable). Snapshot the full HF
#    repo (weights + the custom remote code the model needs) into
#    dist/prefetch/locateanything; build-usb-image.sh stages it onto
#    /assets/models/locateanything.
#
#    LICENSE: NVIDIA non-commercial (research/non-profit only). You are
#    downloading this under those terms — see docs/VISION_GROUNDING.md.
# ---------------------------------------------------------------------------
if [[ "$WITH_VISION" == 1 ]]; then
  dst="${OUT}/locateanything"
  log "fetching vision model: ${VISION_REPO} -> ${dst} (~8 GB, NON-COMMERCIAL)"

  if command -v hf >/dev/null 2>&1; then
    hf download "${VISION_REPO}" --local-dir "${dst}"
  elif command -v huggingface-cli >/dev/null 2>&1; then
    huggingface-cli download "${VISION_REPO}" --local-dir "${dst}"
  elif command -v python3 >/dev/null 2>&1; then
    log "hf CLI not found — falling back to python huggingface_hub.snapshot_download"
    python3 - "$VISION_REPO" "$dst" <<'PY'
import sys
repo, dst = sys.argv[1], sys.argv[2]
try:
    from huggingface_hub import snapshot_download
except Exception as e:  # noqa: BLE001
    print(f"vision prefetch skipped: install huggingface_hub ({e})", file=sys.stderr)
    raise SystemExit(1)
snapshot_download(repo_id=repo, local_dir=dst, local_dir_use_symlinks=False)
print(f"vision model downloaded to {dst}", file=sys.stderr)
PY
  else
    log "vision prefetch skipped: need `hf`, `huggingface-cli`, or python3+huggingface_hub"
  fi
fi

# ---------------------------------------------------------------------------
# 6. (OPT-IN) MisoTTS premium voice.
#    Off by default — ~30-40 GB and only useful on a ~24 GB-VRAM GPU. The OS
#    keeps using Piper until latheos.tts.miso.enable AND a capable GPU. We
#    stage BOTH the repo source (generator.py/models.py) and the HF weights
#    into dist/prefetch/miso; build-usb-image.sh copies it to
#    /assets/models/miso.
#
#    NOTE: the Mimi codec + SilentCipher watermarker are pulled by MisoTTS at
#    first run; full offline use needs them cached too (see docs/VOICE_TTS.md).
# ---------------------------------------------------------------------------
if [[ "$WITH_MISO" == 1 ]]; then
  dst="${OUT}/miso"
  log "fetching premium voice: MisoTTS (~30-40 GB, GPU-only, English-only, watermarked)"

  # 1) Repo source so the worker can import `generator` offline.
  if [ ! -f "${dst}/generator.py" ]; then
    if command -v git >/dev/null 2>&1; then
      git clone --depth 1 "$MISO_GIT" "${dst}" || log "miso git clone failed (continuing)"
    else
      log "git not found — cannot fetch MisoTTS source (generator.py)"
    fi
  fi

  # 2) Model weights snapshot into the same dir.
  if command -v hf >/dev/null 2>&1; then
    hf download "${MISO_HF_REPO}" --local-dir "${dst}"
  elif command -v huggingface-cli >/dev/null 2>&1; then
    huggingface-cli download "${MISO_HF_REPO}" --local-dir "${dst}"
  elif command -v python3 >/dev/null 2>&1; then
    log "hf CLI not found — falling back to python huggingface_hub.snapshot_download"
    python3 - "$MISO_HF_REPO" "$dst" <<'PY'
import sys
repo, dst = sys.argv[1], sys.argv[2]
try:
    from huggingface_hub import snapshot_download
except Exception as e:  # noqa: BLE001
    print(f"miso prefetch skipped: install huggingface_hub ({e})", file=sys.stderr)
    raise SystemExit(1)
snapshot_download(repo_id=repo, local_dir=dst, local_dir_use_symlinks=False)
print(f"miso weights downloaded to {dst}", file=sys.stderr)
PY
  else
    log "miso prefetch skipped: need `hf`, `huggingface-cli`, or python3+huggingface_hub"
  fi
  log "MisoTTS note: Mimi codec + SilentCipher pull on first run — see docs/VOICE_TTS.md"
fi

log "done. staged bundle:"
du -sh "${OUT}"/* 2>/dev/null || true
