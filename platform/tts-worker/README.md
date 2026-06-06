# lathe-tts — MisoTTS premium voice worker

The **opt-in, GPU-only** tier of LatheOS's tiered text-to-speech. Piper stays
the lightweight default on every machine; this serves
[MisoTTS 8B](https://github.com/MisoLabsAI/MisoTTS) emotive speech when a
capable GPU is present.

It exposes a tiny loopback HTTP API (stdlib `http.server`, no web framework):

```
GET  /health                 -> {"ok", "loaded", "reason", "sample_rate", ...}
POST /synthesize {"text": …}  -> audio/wav bytes (mono s16le)
```

The daemon's TTS router (`daemon/cam_daemon/tts.py`) calls `/synthesize` and
plays the WAV through PipeWire. When this service is down or unavailable it
falls back to Piper automatically.

## Constraints

- **~24 GB VRAM** for interactive bf16 use; CPU is far too slow for a voice loop.
- **~30-40 GB** first download (model + Mimi codec + SilentCipher + tokenizer).
- **English only.** Output is **watermarked** (SilentCipher) by default.

## Enabling

See [`docs/VOICE_TTS.md`](../../docs/VOICE_TTS.md). In short:

1. Provision weights + code + a venv on the exFAT partition (`/assets/models/miso`).
2. `latheos.tts.miso.enable = true;` and rebuild.
3. The GPU autoselect promotes `LATHEOS_TTS_BACKEND=miso` only on a machine
   with enough VRAM; otherwise the assistant keeps using Piper.

The Python here is import-safe with no GPU: `probe` / the service degrade
gracefully and exit cleanly rather than crashing the boot.
