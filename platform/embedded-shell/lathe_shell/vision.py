"""Thin async client for the local vision-grounding server.

Talks to the loopback HTTP API served by latheos-vision.service
(NVIDIA LocateAnything-3B; see modules/vision-grounding.nix and
platform/vision-worker/). Gives the embedded shell / agent a way to ask
"where is X in this image" and get back pixel boxes / points — e.g. GUI
grounding for the agentic editor ("where is the search button").

Same defensive contract as lathe_shell/llm.py: this client NEVER raises on a
network / server problem. Every method returns a graceful empty/failure value
so the shell stays crash-proof when vision is disabled, has no GPU, or the
weights aren't baked yet (the common case — it's OPT-IN).

Vision is OPT-IN and runs a NON-COMMERCIAL, GPU-only model. By default
LATHEOS_VISION_ENABLE=0 and `health()` returns False; callers should treat
that as "feature off" and fall back to text-only behaviour.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

import httpx
import orjson


@dataclass(slots=True)
class VisionConfig:
    url: str = "http://127.0.0.1:11435"
    timeout: float = 600.0          # BF16 generate() can be slow; be patient.

    @classmethod
    def from_env(cls) -> VisionConfig:
        return cls(url=os.environ.get("LATHEOS_VISION_URL", "http://127.0.0.1:11435"))


def _encode_image(path: str) -> dict:
    """Read a local image into the {"b64": ...} spec the server understands."""
    with open(path, "rb") as fh:
        return {"b64": base64.b64encode(fh.read()).decode("ascii")}


class LocalVision:
    """Crash-proof async client. Box methods return {"boxes": [...]} | error."""

    def __init__(self, cfg: VisionConfig | None = None) -> None:
        self.cfg = cfg or VisionConfig.from_env()
        self._client = httpx.AsyncClient(
            base_url=self.cfg.url,
            timeout=self.cfg.timeout,
            headers={"content-type": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> bool:
        """True only when the server is up AND reports a usable GPU + weights."""
        try:
            r = await self._client.get("/health", timeout=2.0)
            if r.status_code != 200:
                return False
            return bool(orjson.loads(r.content).get("ok"))
        except (httpx.HTTPError, orjson.JSONDecodeError):
            return False

    async def _post(self, route: str, body: dict) -> dict:
        try:
            r = await self._client.post(route, content=orjson.dumps(body))
            data = orjson.loads(r.content)
            if r.status_code != 200:
                return {"ok": False, "error": data.get("error", f"HTTP {r.status_code}")}
            return data
        except (httpx.HTTPError, orjson.JSONDecodeError) as exc:
            # Vision is optional — surface a clean error, never propagate.
            return {"ok": False, "error": str(exc)}

    # ---- task helpers (image given as a local path) -------------------------

    async def detect(self, image_path: str, categories: list[str]) -> dict:
        """Object detection. Returns {"ok", "boxes":[{x1,y1,x2,y2}], "answer"}."""
        try:
            spec = _encode_image(image_path)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return await self._post("/detect", {"image": spec, "categories": categories})

    async def ground(self, image_path: str, query: str, *, single: bool = False) -> dict:
        """Phrase grounding (boxes) for a free-form description."""
        try:
            spec = _encode_image(image_path)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return await self._post("/ground", {"image": spec, "query": query, "single": single})

    async def point(self, image_path: str, query: str) -> dict:
        """Pointing — returns {"ok", "points":[{x,y}], "answer"}.

        Handy for GUI grounding: point(screenshot, "the search button").
        """
        try:
            spec = _encode_image(image_path)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return await self._post("/point", {"image": spec, "query": query})

    async def gui(self, image_path: str, query: str, *, output_type: str = "point") -> dict:
        """GUI element grounding (point by default, or box)."""
        try:
            spec = _encode_image(image_path)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return await self._post(
            "/gui", {"image": spec, "query": query, "output_type": output_type}
        )
