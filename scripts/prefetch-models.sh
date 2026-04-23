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
#   * Ollama heavy models (llama3.1:8b, maybe codestral:22b) ~5 / ~13 GB
#   * Piper voices (en_US-amy-medium, ko_KR-kss-medium)      ~60 MB each
#   * Whisper.cpp ggml-base.en                               ~150 MB
#   * openWakeWord pretrained bundle (hey_jarvis + alexa)     ~3 MB
#
# Usage
#   ./scripts/prefetch-models.sh                              # voice+small heavy (~7 GB)
#   HEAVY=big ./scripts/prefetch-models.sh                    # + codestral 22B (~22 GB)
#   SKIP_OLLAMA=1 ./scripts/prefetch-models.sh                # just Piper+whisper+OWW
#
# Requires: curl, ollama, python3 (for openWakeWord). Runs on any host with
# network access — macOS, Linux, or WSL.
################################################################################

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/dist/prefetch"

HEAVY="${HEAVY:-small}"                  # small | big
SKIP_OLLAMA="${SKIP_OLLAMA:-0}"
SKIP_PIPER="${SKIP_PIPER:-0}"
SKIP_WHISPER="${SKIP_WHISPER:-0}"
SKIP_OWW="${SKIP_OWW:-0}"

VOICE_MODEL="llama3.2:3b"
HEAVY_SMALL="llama3.1:8b"
HEAVY_BIG="codestral:22b"

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

log "done. staged bundle:"
du -sh "${OUT}"/* 2>/dev/null || true
