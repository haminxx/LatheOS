"""Thin async client for the local Ollama runtime.

The embedded shell only needs three things from Ollama:

  * `health()`     — is the daemon up?
  * `list()`       — which models are already pulled?
  * `chat(msg)`    — stream tokens back for the chat strip.

We deliberately do not reuse `cam_daemon.agents`, because that module has
the full multi-agent machinery (dispatcher/planner/coder/critic) and we
want the embedded shell to stay lightweight + crash-proof even if cam-daemon
is down.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import orjson


@dataclass(slots=True)
class LLMConfig:
    url: str = "http://127.0.0.1:11434"
    model: str = "llama3.2:3b"
    timeout: float = 60.0


class LocalLLM:
    def __init__(self, cfg: LLMConfig) -> None:
        self.cfg = cfg
        self._client = httpx.AsyncClient(
            base_url=cfg.url,
            timeout=cfg.timeout,
            headers={"content-type": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> bool:
        try:
            r = await self._client.get("/api/tags", timeout=1.5)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def models(self) -> list[str]:
        try:
            r = await self._client.get("/api/tags", timeout=3.0)
            r.raise_for_status()
            data = orjson.loads(r.content)
            return [m["name"] for m in data.get("models", [])]
        except (httpx.HTTPError, orjson.JSONDecodeError, KeyError):
            return []

    async def chat(self, prompt: str, *, system: str | None = None) -> AsyncIterator[str]:
        """Yield model tokens as they arrive."""
        body = {
            "model": self.cfg.model,
            "prompt": prompt if system is None else f"{system}\n\nUser: {prompt}",
            "stream": True,
            "options": {"temperature": 0.5, "num_predict": 320},
        }
        async with self._client.stream(
            "POST", "/api/generate", content=orjson.dumps(body)
        ) as resp:
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    payload = orjson.loads(line)
                except orjson.JSONDecodeError:
                    continue
                chunk = payload.get("response", "")
                if chunk:
                    yield chunk
                if payload.get("done"):
                    break
