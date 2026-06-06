"""Tiny loopback HTTP API in front of LocateAnythingWorker.

Why the stdlib http.server (and not FastAPI / aiohttp)?
  * The repo only carries an async HTTP *client* (httpx) — no server framework
    is a runtime dependency, and we want this build to stay hermetic through
    nixpkgs (no PyPI fetch at nixos-rebuild).
  * The hot path is a blocking BF16 torch.generate() on the GPU. A threaded
    server with a single model lock is the honest model here: concurrency
    buys nothing while the GPU is busy, and we avoid pulling an async web
    stack just to await a synchronous CUDA call.

Contract (loopback only, 127.0.0.1:11435 by default)
  GET  /health        -> {"ok": bool, "loaded": bool, "reason": str, ...}
  POST /detect        {image, categories:[...]}        -> boxes
  POST /ground        {image, query, multi?, single?}  -> boxes
  POST /ground_text   {image, query}                   -> boxes
  POST /detect_text   {image}                           -> boxes
  POST /point         {image, query}                   -> points
  POST /gui           {image, query, output_type}      -> boxes|points

`image` is either {"b64": "<base64 png/jpeg>"} or {"path": "/abs/path.jpg"}.
Responses always include the raw model "answer" plus parsed pixel coords and
the source image size, so callers can re-scale however they like.
"""

from __future__ import annotations

import base64
import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .worker import LocateAnythingWorker, VisionUnavailable, WorkerConfig

# Single global worker + lock. The model is large; we load it once and
# serialise inference so two requests never trample one CUDA stream.
_WORKER: LocateAnythingWorker | None = None
_LOCK = threading.Lock()


def _get_worker(cfg: WorkerConfig) -> LocateAnythingWorker:
    global _WORKER
    if _WORKER is None:
        _WORKER = LocateAnythingWorker(cfg)
    return _WORKER


def _load_image(spec: dict):
    """Turn an {"b64": ...} or {"path": ...} spec into an RGB PIL.Image.

    PIL is imported lazily so importing this module never requires Pillow on a
    box that only wants the client side.
    """
    from PIL import Image

    if not isinstance(spec, dict):
        raise ValueError("'image' must be an object with 'b64' or 'path'")
    if spec.get("b64"):
        raw = base64.b64decode(spec["b64"])
        return Image.open(io.BytesIO(raw)).convert("RGB")
    if spec.get("path"):
        path = spec["path"]
        if not os.path.isfile(path):
            raise FileNotFoundError(f"image path not found: {path}")
        return Image.open(path).convert("RGB")
    raise ValueError("'image' needs either 'b64' or 'path'")


class _Handler(BaseHTTPRequestHandler):
    # ThreadingHTTPServer sets .cfg on the server instance (see serve()).
    server_version = "lathe-vision/0.1"

    # Silence the default noisy stderr access log; rely on systemd journal.
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    @property
    def _cfg(self) -> WorkerConfig:
        return self.server.cfg  # type: ignore[attr-defined]

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("content-length", 0) or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    # ---- routing ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 — stdlib naming
        if self.path.rstrip("/") in ("/health", ""):
            self._health()
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 — stdlib naming
        route = self.path.rstrip("/")
        handlers = {
            "/detect": self._detect,
            "/ground": self._ground,
            "/ground_text": self._ground_text,
            "/detect_text": self._detect_text,
            "/point": self._point,
            "/gui": self._gui,
        }
        fn = handlers.get(route)
        if fn is None:
            self._send(404, {"ok": False, "error": f"unknown route {route}"})
            return
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"ok": False, "error": f"bad JSON: {exc}"})
            return
        try:
            fn(body)
        except VisionUnavailable as exc:
            # GPU / weights / stack missing — degrade gracefully, do not 500.
            self._send(503, {"ok": False, "error": str(exc), "unavailable": True})
        except (ValueError, FileNotFoundError) as exc:
            self._send(400, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — never leak a stack to the wire
            self._send(500, {"ok": False, "error": f"inference failed: {exc}"})

    # ---- endpoints ----------------------------------------------------------

    def _health(self) -> None:
        from .worker import probe

        ok, reason = probe(self._cfg)
        worker = _WORKER
        self._send(
            200,
            {
                "ok": ok,
                "loaded": bool(worker and worker.loaded),
                "reason": reason,
                "model": self._cfg.model_path,
                "mode": self._cfg.generation_mode,
                "device": self._cfg.device,
            },
        )

    def _run(self, body: dict, method: str, *args, parse: str = "boxes", **kw) -> None:
        img = _load_image(body.get("image", {}))
        w, h = img.size
        worker = _get_worker(self._cfg)
        with _LOCK:
            result = getattr(worker, method)(img, *args, **kw)
        answer = result.get("answer", "")
        out = {
            "ok": True,
            "answer": answer,
            "image_size": {"width": w, "height": h},
        }
        if parse == "points":
            out["points"] = LocateAnythingWorker.parse_points(answer, w, h)
        else:
            out["boxes"] = LocateAnythingWorker.parse_boxes(answer, w, h)
        self._send(200, out)

    def _detect(self, body: dict) -> None:
        cats = body.get("categories") or body.get("query")
        if isinstance(cats, str):
            cats = [c.strip() for c in cats.split(",") if c.strip()]
        if not cats:
            raise ValueError("'categories' (list or comma string) is required")
        self._run(body, "detect", cats)

    def _ground(self, body: dict) -> None:
        query = body.get("query")
        if not query:
            raise ValueError("'query' is required")
        method = "ground_single" if body.get("single") else "ground_multi"
        self._run(body, method, query)

    def _ground_text(self, body: dict) -> None:
        query = body.get("query")
        if not query:
            raise ValueError("'query' is required")
        self._run(body, "ground_text", query)

    def _detect_text(self, body: dict) -> None:
        self._run(body, "detect_text")

    def _point(self, body: dict) -> None:
        query = body.get("query")
        if not query:
            raise ValueError("'query' is required")
        self._run(body, "point", query, parse="points")

    def _gui(self, body: dict) -> None:
        query = body.get("query")
        if not query:
            raise ValueError("'query' is required")
        output_type = body.get("output_type", "box")
        parse = "points" if output_type == "point" else "boxes"
        self._run(body, "ground_gui", query, parse=parse, output_type=output_type)


def serve(
    cfg: WorkerConfig,
    host: str = "127.0.0.1",
    port: int = 11435,
    *,
    preload: bool = True,
) -> None:
    """Start the loopback HTTP server. Blocks until interrupted.

    If `preload` is set we attempt to load the model up front so the first
    request is fast; a VisionUnavailable here is fatal (the systemd unit
    treats that as the 'no GPU, exit cleanly' path).
    """
    if preload:
        _get_worker(cfg).load()  # may raise VisionUnavailable -> caller exits 0

    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.cfg = cfg  # type: ignore[attr-defined]
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
