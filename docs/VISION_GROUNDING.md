# Visual grounding — NVIDIA LocateAnything-3B (opt-in)

LatheOS can run a **visual grounding** brain alongside the offline text stack
(Ollama + whisper + piper). Given an image and a natural-language query it
returns **bounding boxes** and **points** — not chat. That powers:

- **GUI grounding** for the agentic editor — "where is the search button" →
  a pixel point you can click.
- On-device perception — "locate every resistor", "point to the red mug",
  scene text detection, document layout, open-set object detection.

The model is **NVIDIA LocateAnything-3B** (MoonViT vision encoder + Qwen2.5-3B
decoder, ~4B params BF16) with Parallel Box Decoding.

- Model card: <https://huggingface.co/nvidia/LocateAnything-3B>
- Code: <https://github.com/NVlabs/Eagle/tree/main/Embodied>
- Project page: <https://research.nvidia.com/labs/lpr/locate-anything/>

## Constraints — read before enabling

| Constraint | Detail |
|------------|--------|
| **License** | **NVIDIA non-commercial** — academic / non-profit research **only**. Commercial use is **not** permitted (except by NVIDIA). MoonViT is MIT; Qwen2.5-3B is under the Qwen Research License. Redistribution must keep the license + attribution. |
| **Hardware** | An **NVIDIA GPU** (Ampere / Ada / Hopper / Blackwell). CPU technically works but is far too slow. |
| **OS** | **Linux** only. |
| **Runtime** | HF **Transformers** (`transformers==4.57.1`) with `trust_remote_code=True` — **not** Ollama. Runs as a small local HTTP server on `127.0.0.1:11435`. |
| **Size** | ~8 GB BF16 weights, stored on exFAT at `/assets/models/locateanything`. |

Because of the license + GPU constraints, this feature is **disabled by
default**. Enabling it never changes the existing Ollama/voice stack or the
boot path — if there's no GPU or no weights, the service logs and exits
cleanly.

## Enable it

1. **Bake the weights** (off by default; ~8 GB, accepts the non-commercial
   terms by downloading):

   ```bash
   WITH_VISION=1 ./scripts/prefetch-models.sh
   # -> dist/prefetch/locateanything/  (staged onto /assets/models/locateanything
   #    by scripts/build-usb-image.sh)
   ```

2. **Turn on the OS feature** (pulls the torch+CUDA closure into the build):

   ```nix
   # configuration.nix already imports modules/vision-grounding.nix
   latheos.vision.enable = true;          # default false
   # optional:
   latheos.vision.mode = "hybrid";        # fast | slow | hybrid (default)
   latheos.vision.maxNewTokens = 8192;    # model-card default
   ```

   On an already-built image you can also flip it at runtime (no rebuild) by
   editing `/persist/secrets/vision.env`:

   ```ini
   LATHEOS_VISION_ENABLE=1
   ```

   then `systemctl restart latheos-vision`.

3. **Check it:**

   ```bash
   lathe-vision probe        # GPU + weights + ML stack pre-flight
   lathe-vision health       # {"ok": true, "loaded": ...} once warm
   ```

### nixpkgs pin caveat (exact versions)

The model card pins `transformers==4.57.1` (plus `opencv-python-headless==
4.11.0.86`, `decord==0.6.0`, `lmdb==1.7.5`, `peft`, `torchvision`) and the
custom remote code expects them. nixpkgs `nixos-24.11` may not carry those
exact versions, so the Nix build provides a best-effort ML stack and the
service exits cleanly if an import fails. For an exact, model-card-faithful
runtime, provision a venv on the exFAT partition and the service will prefer
it automatically:

```bash
python -m venv /assets/models/locateanything/.venv
. /assets/models/locateanything/.venv/bin/activate
pip install torch torchvision transformers==4.57.1 \
    opencv-python-headless==4.11.0.86 numpy==1.25.0 Pillow==11.1.0 \
    peft decord==0.6.0 lmdb==1.7.5
pip install /path/to/LatheOS/platform/vision-worker   # the lathe-vision package
```

The interpreter path is `LATHEOS_VISION_VENV` in `/etc/latheos/vision.env`.

## Runtime contract

| Name | Default | Meaning |
|------|---------|---------|
| `LATHEOS_VISION_ENABLE` | `0` | Runtime master switch (the service no-ops unless `1`). |
| `LATHEOS_VISION_URL` | `http://127.0.0.1:11435` | Loopback HTTP endpoint. |
| `LATHEOS_VISION_HOST` / `LATHEOS_VISION_PORT` | `127.0.0.1` / `11435` | Bind address. |
| `LATHEOS_VISION_MODEL` | `/assets/models/locateanything` | Local weights path. |
| `LATHEOS_VISION_MODE` | `hybrid` | `fast` (MTP) / `slow` (AR) / `hybrid`. |
| `LATHEOS_VISION_MAX_NEW_TOKENS` | `8192` | Generation budget. |
| `LATHEOS_VISION_DEVICE` | `cuda` | torch device. |
| `LATHEOS_VISION_VENV` | `…/locateanything/.venv` | Optional interpreter override. |

These live in `/etc/latheos/vision.env` (written by
`modules/vision-grounding.nix`), overridable per-drive via
`/persist/secrets/vision.env`.

## HTTP API (loopback only)

`image` is `{"b64": "<base64>"}` or `{"path": "/abs/path.jpg"}`. Coordinates
come back in **pixels** with the raw model `answer` and `image_size`.

| Method | Route | Body | Returns |
|--------|-------|------|---------|
| GET | `/health` | — | status |
| POST | `/detect` | `{image, categories:[...]}` | boxes |
| POST | `/ground` | `{image, query, single?}` | boxes |
| POST | `/ground_text` | `{image, query}` | boxes |
| POST | `/detect_text` | `{image}` | boxes |
| POST | `/point` | `{image, query}` | points |
| POST | `/gui` | `{image, query, output_type}` | box/point |

### Prompt templates

The worker uses NVIDIA's exact trained prompt strings (do not paraphrase):

| Task | Template |
|------|----------|
| Object detection | `Locate all the instances that matches the following description: [CATEGORIES].` |
| Phrase grounding (single) | `Locate a single instance that matches the following description: [PHRASE].` |
| Phrase grounding (multi) | `Locate all the instances that match the following description: [PHRASE].` |
| Text grounding | `Please locate the text referred as [PHRASE].` |
| Scene text detection | `Detect all the text in box format.` |
| GUI grounding (box) | `Locate the region that matches the following description: [PHRASE].` |
| GUI / pointing | `Point to: [PHRASE].` |

## Example usage

CLI (stdlib only — works without an HTTP client installed):

```bash
lathe-vision detect frame.png "person,car,bicycle"
lathe-vision ground frame.png "the red mug"
lathe-vision point  screen.png "the search button"
```

curl:

```bash
B64=$(base64 -w0 screen.png)
curl -s http://127.0.0.1:11435/point \
  -H 'content-type: application/json' \
  -d "{\"image\":{\"b64\":\"$B64\"},\"query\":\"the search button\"}"
```

Programmatic (from the embedded shell / agent — crash-proof, see
`platform/embedded-shell/lathe_shell/vision.py`):

```python
from lathe_shell.vision import LocalVision

vis = LocalVision()
if await vis.health():                       # False when disabled / no GPU
    res = await vis.point("screen.png", "the search button")
    if res.get("ok"):
        for p in res["points"]:
            click(p["x"], p["y"])            # pixel coordinates
await vis.close()
```

When vision is off (the default), `health()` returns `False` and callers fall
back to text-only behaviour — nothing breaks.
