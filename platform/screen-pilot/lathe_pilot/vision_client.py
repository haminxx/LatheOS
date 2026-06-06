"""Client for the local LocateAnything-3B grounding service (loopback).

This is the LatheOS twist on Clicky: instead of trusting an LLM's pixel
guesses, we resolve "the search button" -> (x, y) with the opt-in, GPU-only
LocateAnything-3B service that already runs on 127.0.0.1:11435
(modules/vision-grounding.nix, platform/vision-worker). We mirror the method
surface of lathe_shell/vision.py (gui / point / ground / health) but with the
stdlib HTTP helper so this package stays dependency-free.

Crash-proof: a disabled service / no GPU / no weights returns ok=False and the
engine falls back to a description-only walkthrough (no cursor movement).

Endpoints (see platform/vision-worker/lathe_vision/server.py):
  GET  /health                        -> {"ok": bool, ...}
  POST /gui   {image, query, output_type:"point"} -> {"points":[{x,y}], ...}
  POST /point {image, query}                       -> {"points":[{x,y}], ...}
"""

from __future__ import annotations

import base64

from .httpjson import get_json, post_json


def _encode_image(path: str) -> dict | None:
    try:
        with open(path, "rb") as fh:
            return {"b64": base64.b64encode(fh.read()).decode("ascii")}
    except OSError:
        return None


class VisionClient:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")

    def health(self) -> bool:
        """True only when the grounding server is up AND reports a usable GPU."""
        data = get_json(f"{self.url}/health", timeout=2.0)
        return bool(data and data.get("ok"))

    def point_at(self, image_path: str, phrase: str, *, prefer_gui: bool = True) -> dict:
        """Resolve a grounding phrase to a pixel point on the screenshot.

        Returns {"ok": bool, "point": {"x","y"} | None, "image_size": {...},
                 "error": str?}. Prefers the GUI-tuned /gui endpoint (the model
        was trained with GUI prompts) and falls back to generic /point.
        """
        spec = _encode_image(image_path)
        if spec is None:
            return {"ok": False, "error": f"cannot read image {image_path}", "point": None}

        route = "/gui" if prefer_gui else "/point"
        body = {"image": spec, "query": phrase}
        if prefer_gui:
            body["output_type"] = "point"
        data = post_json(f"{self.url}{route}", body)

        if not data.get("ok"):
            # Try the other endpoint once before giving up.
            alt = "/point" if prefer_gui else "/gui"
            alt_body = {"image": spec, "query": phrase}
            if alt == "/gui":
                alt_body["output_type"] = "point"
            data = post_json(f"{self.url}{alt}", alt_body)

        if not data.get("ok"):
            return {"ok": False, "error": data.get("error", "grounding failed"), "point": None}

        points = data.get("points") or []
        point = points[0] if points else None
        return {
            "ok": point is not None,
            "point": point,
            "image_size": data.get("image_size"),
            "answer": data.get("answer", ""),
            "error": None if point else "no point returned for phrase",
        }
