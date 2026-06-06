"""Vision router for the daemon — describe (Ollama VLM) + locate (LocateAnything).

Two fully-local paths, picked by intent in __main__:

  * describe(): general scene understanding via a vision-capable OLLAMA model
    the user pulls themselves (LATHEOS_VLM_MODEL, e.g. llama3.2-vision). This
    is the privacy-friendly default for "what do you see".
  * locate(): pixel grounding via the opt-in LocateAnything service
    (LATHEOS_VISION_URL; modules/vision-grounding.nix) for "where is X".

Crash-proof like the rest of the local stack: a missing model, a 404 from
Ollama (model not pulled), or a disabled vision service returns None / a
clean error rather than raising.
"""

from __future__ import annotations

import base64
import os

import structlog

try:
    import httpx
except ImportError:                       # import-safe on bare CI
    httpx = None                          # type: ignore[assignment]

log = structlog.get_logger("cam-daemon.vision")


def _b64(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            return base64.b64encode(fh.read()).decode("ascii")
    except OSError as exc:
        log.warning("vision.read_failed", path=path, error=str(exc))
        return None


class VisionRouter:
    def __init__(self) -> None:
        self.llm_url = os.environ.get("LATHEOS_LLM_URL", "http://127.0.0.1:11434").rstrip("/")
        self.vlm_model = os.environ.get("LATHEOS_VLM_MODEL", "").strip()
        self.locate_url = os.environ.get("LATHEOS_VISION_URL", "http://127.0.0.1:11435").rstrip("/")

    async def describe(self, image_path: str, prompt: str | None = None) -> str | None:
        """Scene description via a vision-capable Ollama model."""
        if httpx is None or not self.vlm_model:
            return None
        b64 = _b64(image_path)
        if b64 is None:
            return None
        prompt = (prompt or "").strip() or "Describe what you see in one or two short sentences."
        payload = {
            "model": self.vlm_model,
            "prompt": prompt,
            "images": [b64],
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 200},
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(f"{self.llm_url}/api/generate", json=payload)
                if resp.status_code == 404:
                    log.warning("vision.vlm_not_pulled", model=self.vlm_model)
                    return None
                resp.raise_for_status()
                return (resp.json().get("response") or "").strip() or None
        except Exception as exc:           # noqa: BLE001 — never fatal
            log.warning("vision.describe_failed", error=str(exc))
            return None

    async def locate(self, image_path: str, query: str) -> dict | None:
        """Pixel grounding via the opt-in LocateAnything service."""
        if httpx is None:
            return None
        b64 = _b64(image_path)
        if b64 is None:
            return None
        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                resp = await client.post(
                    f"{self.locate_url}/ground",
                    json={"image": {"b64": b64}, "query": query},
                )
                data = resp.json()
                if resp.status_code != 200:
                    return {"ok": False, "error": data.get("error", f"HTTP {resp.status_code}")}
                return data
        except Exception as exc:           # noqa: BLE001 — vision is optional
            log.warning("vision.locate_failed", error=str(exc))
            return {"ok": False, "error": str(exc)}
