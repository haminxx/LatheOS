"""Tiny loopback HTTP API in front of MisoWorker.

Stdlib http.server (no FastAPI/aiohttp) for the same reasons as the vision
worker: keep the Nix build hermetic and avoid an async web stack in front of
a blocking GPU call. One global worker + a lock; the GPU is the bottleneck so
serialising synthesis is the honest model.

Contract (loopback only, 127.0.0.1:11436 by default)
  GET  /health                 -> {"ok", "loaded", "reason", "sample_rate", ...}
  POST /synthesize {text, ...}  -> audio/wav bytes (mono s16le)

`/synthesize` returns raw WAV so the daemon's TTS router (cam_daemon/tts.py)
can decode it with the stdlib `wave` module and play it through PipeWire.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .worker import MisoConfig, MisoWorker, TTSUnavailable

_WORKER: MisoWorker | None = None
_LOCK = threading.Lock()


def _get_worker(cfg: MisoConfig) -> MisoWorker:
    global _WORKER
    if _WORKER is None:
        _WORKER = MisoWorker(cfg)
    return _WORKER


class _Handler(BaseHTTPRequestHandler):
    server_version = "lathe-tts/0.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 — silence access log
        return

    @property
    def _cfg(self) -> MisoConfig:
        return self.server.cfg  # type: ignore[attr-defined]

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_wav(self, data: bytes) -> None:
        self.send_response(200)
        self.send_header("content-type", "audio/wav")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("content-length", 0) or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def do_GET(self) -> None:  # noqa: N802 — stdlib naming
        if self.path.rstrip("/") in ("/health", ""):
            from .worker import probe

            ok, reason = probe(self._cfg)
            worker = _WORKER
            self._send_json(200, {
                "ok": ok,
                "loaded": bool(worker and worker.loaded),
                "reason": reason,
                "model": self._cfg.model_path,
                "device": self._cfg.device,
                "sample_rate": worker.sample_rate if worker else None,
            })
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 — stdlib naming
        if self.path.rstrip("/") != "/synthesize":
            self._send_json(404, {"ok": False, "error": "unknown route"})
            return
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"ok": False, "error": f"bad JSON: {exc}"})
            return

        text = (body.get("text") or "").strip()
        if not text:
            self._send_json(400, {"ok": False, "error": "'text' is required"})
            return

        try:
            worker = _get_worker(self._cfg)
            with _LOCK:
                wav = worker.synthesize(
                    text,
                    speaker=body.get("speaker"),
                    max_audio_ms=body.get("max_audio_ms"),
                )
        except TTSUnavailable as exc:
            self._send_json(503, {"ok": False, "error": str(exc), "unavailable": True})
            return
        except Exception as exc:  # noqa: BLE001 — never leak a stack to the wire
            self._send_json(500, {"ok": False, "error": f"synthesis failed: {exc}"})
            return

        self._send_wav(wav)


def serve(
    cfg: MisoConfig,
    host: str = "127.0.0.1",
    port: int = 11436,
    *,
    preload: bool = True,
) -> None:
    """Start the loopback HTTP server. Blocks until interrupted.

    With `preload`, load the model up front; a TTSUnavailable here is the
    'no GPU / no weights' path the systemd unit treats as a clean exit.
    """
    if preload:
        _get_worker(cfg).load()  # may raise TTSUnavailable -> caller exits 0

    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.cfg = cfg  # type: ignore[attr-defined]
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
