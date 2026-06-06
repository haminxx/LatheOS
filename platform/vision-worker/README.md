# lathe-vision — LatheOS visual grounding worker

A thin loopback HTTP server in front of NVIDIA **LocateAnything-3B**, a
visual-grounding / detection / pointing VLM (MoonViT vision encoder +
Qwen2.5-3B decoder, ~4B params BF16). It turns natural-language queries into
bounding boxes and points on an image — "where is the search button",
"locate every resistor", "point to the traffic light".

This is **not** a chat model and does **not** run in Ollama. It runs through HF
Transformers with `trust_remote_code=True` on an **NVIDIA GPU (Linux)**.

> **License — non-commercial.** LocateAnything-3B is under the NVIDIA
> non-commercial license (academic / non-profit research only). Vision encoder
> MoonViT is MIT; the LM Qwen2.5-3B is under the Qwen Research License. This is
> why the LatheOS vision feature is **opt-in and disabled by default**.
> Sources: <https://huggingface.co/nvidia/LocateAnything-3B>,
> <https://github.com/NVlabs/Eagle/tree/main/Embodied>,
> <https://research.nvidia.com/labs/lpr/locate-anything/>.

## Layout

```
lathe_vision/
  worker.py     # LocateAnythingWorker (adapted from NVIDIA's reference)
  server.py     # stdlib ThreadingHTTPServer, loopback only, model lock
  __main__.py   # `lathe-vision` CLI: serve | probe | health | detect | ground | point
```

## Packaging

Light deps (pillow, numpy) are hermetic via nixpkgs; the heavy ML stack
(`torch`+CUDA, `transformers==4.57.1`, `opencv`) is the `[gpu]` extra and is
imported lazily so the package is import-safe (and the service exits cleanly)
on a machine with no GPU. In LatheOS this is built + served by
[`modules/vision-grounding.nix`](../../modules/vision-grounding.nix); see
[`docs/VISION_GROUNDING.md`](../../docs/VISION_GROUNDING.md).

## HTTP API (default `http://127.0.0.1:11435`)

| Method | Route          | Body                                   | Returns |
|--------|----------------|----------------------------------------|---------|
| GET    | `/health`      | —                                      | status  |
| POST   | `/detect`      | `{image, categories:[...]}`            | boxes   |
| POST   | `/ground`      | `{image, query, single?}`             | boxes   |
| POST   | `/ground_text` | `{image, query}`                      | boxes   |
| POST   | `/detect_text` | `{image}`                              | boxes   |
| POST   | `/point`       | `{image, query}`                      | points  |
| POST   | `/gui`         | `{image, query, output_type}`         | box/pt  |

`image` is `{"b64": "<base64>"}` or `{"path": "/abs/path.jpg"}`. Coordinates
come back in **pixels**, alongside the raw model `answer` and `image_size`.

## CLI

```bash
lathe-vision probe                          # GPU + weights + stack check
lathe-vision serve --port 11435             # start the server
lathe-vision health
lathe-vision detect frame.png "person,car,bicycle"
lathe-vision ground frame.png "the red mug"
lathe-vision point  screen.png "the search button"
```

The query subcommands use only the Python stdlib, so they work without any
HTTP client installed.
