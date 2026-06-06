# LatheOS Voice (Tiered Text-to-Speech)

LatheOS speaks **fully offline**. There are two voices and one router so the
same USB stick sounds good on a tiny laptop and great on a workstation GPU.

| Tier | Engine | Where it runs | Default? |
|------|--------|---------------|----------|
| Default | [Piper](https://github.com/rhasspy/piper) | CPU, every machine | ✅ |
| Premium | [MisoTTS 8B](https://github.com/MisoLabsAI/MisoTTS) | NVIDIA GPU (~24 GB VRAM) | opt-in |

## How a voice is chosen

The daemon's TTS router (`daemon/cam_daemon/tts.py`) reads
`LATHEOS_TTS_BACKEND` (`auto` \| `piper` \| `miso`):

- `auto` → **Piper**, unless the boot-time GPU autoselect promoted us to MisoTTS.
- `piper` → always Piper.
- `miso` → try MisoTTS, automatically fall back to Piper if the service is
  down or errors.

`cam-tts-autoselect.service` runs on every boot (before `cam-daemon`) and
writes `/run/latheos/tts-backend.env`. It only picks `miso` when **all** of:

1. `latheos.tts.miso.enable = true` (you turned it on), and
2. the weights are present at `/assets/models/miso`, and
3. `nvidia-smi` reports a GPU with ≥ ~24 GB VRAM.

Move the stick to a machine without a big GPU and it silently drops back to
Piper — no rebuild, no edits.

## Piper (default — nothing to do)

Provisioned by `modules/local-llm.nix`. The voice file is
`LATHEOS_PIPER_VOICE` (default `/assets/models/piper/en_US-amy-medium.onnx`),
baked by `scripts/prefetch-models.sh`. To change voices, drop another Piper
`.onnx`/`.json` pair on `/assets/models/piper` and point `LATHEOS_PIPER_VOICE`
at it in `/persist/secrets/llm.env`.

## MisoTTS (premium — opt-in)

> ~8.2B params, **~24 GB VRAM**, ~30-40 GB download, **English-only**, output is
> **watermarked** (SilentCipher). That's why it is disabled by default.

### 1. Provision weights + code on the exFAT partition

From any machine (or pre-bake into the image):

```bash
# Bake into the USB image at build time:
WITH_MISO=1 ./scripts/prefetch-models.sh        # clones the repo + pulls weights
# build-usb-image.sh then stages dist/prefetch/miso -> /assets/models/miso
```

Or directly on the running OS / stick:

```bash
git clone https://github.com/MisoLabsAI/MisoTTS /assets/models/miso
cd /assets/models/miso
uv sync --python 3.10           # MisoTTS pins Python 3.10 upstream
# install our worker into that venv so `python -m lathe_tts serve` works:
.venv/bin/pip install /etc/nixos/latheos/platform/tts-worker
```

The service prefers `LATHEOS_MISO_VENV` (`/assets/models/miso/.venv`) when it
exists, so you get MisoTTS's exact pinned deps instead of the Nix interpreter.

> The Mimi codec (`kyutai/mimi`) and SilentCipher watermarker download on first
> run. For a *fully* offline box, run one synthesis online first so they cache,
> or pre-stage those HF repos under the venv's HF cache on `/assets`.

### 2. Enable the feature and rebuild

```nix
# configuration.nix (or a per-host module)
latheos.tts.miso.enable = true;
# optional:
latheos.tts.miso.speaker    = 0;      # MisoTTS speaker id
latheos.tts.miso.maxAudioMs = 20000;  # max audio per turn
```

```bash
sudo nixos-rebuild switch --flake /etc/nixos/latheos#latheos-x86_64
```

### 3. Verify

```bash
systemctl status latheos-tts          # should be active on a GPU box
lathe-tts health                      # {"ok": true, "loaded": true, ...}
lathe-tts say "Hello from Miso." -o /tmp/miso.wav && aplay /tmp/miso.wav
cat /run/latheos/tts-backend.env      # LATHEOS_TTS_BACKEND=miso when promoted
```

If anything is missing (no GPU, no weights), `latheos-tts.service` exits
cleanly and the assistant keeps talking through Piper.

## Endpoints

`latheos-tts.service` is loopback-only on `127.0.0.1:11436`:

```
GET  /health                 -> {"ok", "loaded", "reason", "sample_rate", ...}
POST /synthesize {"text": …}  -> audio/wav bytes (mono s16le)
```

No inbound firewall ports are opened; nothing leaves the machine.
