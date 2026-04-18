"""Persistent WebSocket client to CAM Cloud Proxy.

Handles reconnection with exponential backoff. The client emits three
async iterables to the rest of the daemon:

    - `transcripts()` : strings as they finalise (for on-screen overlay)
    - `commands()`    : dicts from the server's command frames
    - `audio()`       : raw PCM bytes to hand to the speaker sink
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import websockets


@dataclass(slots=True)
class CloudConfig:
    url: str
    hardware_token: str
    sample_rate: int = 16_000

    @classmethod
    def from_env(cls) -> "CloudConfig":
        return cls(
            url=os.environ.get("CAM_PROXY_URL", "wss://cam.example.com/ws/cam"),
            hardware_token=os.environ.get("CAM_HARDWARE_TOKEN", ""),
            sample_rate=int(os.environ.get("CAM_SAMPLE_RATE", "16000")),
        )


class CloudClient:
    def __init__(self, cfg: CloudConfig) -> None:
        self.cfg = cfg
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._out_q: asyncio.Queue[dict | bytes] = asyncio.Queue(maxsize=256)

    async def connect(self) -> None:
        self._ws = await websockets.connect(
            self.cfg.url,
            ping_interval=20,
            ping_timeout=20,
            max_size=2 * 1024 * 1024,
        )
        hello = {
            "type": "hello",
            "hardware_token": self.cfg.hardware_token,
            "sample_rate": self.cfg.sample_rate,
            "encoding": "linear16",
            "client_version": "0.1.0",
        }
        await self._ws.send(json.dumps(hello))

    async def send_audio(self, pcm: bytes) -> None:
        if self._ws is not None:
            await self._ws.send(pcm)

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def events(self) -> AsyncIterator[dict | bytes]:
        assert self._ws is not None
        async for frame in self._ws:
            if isinstance(frame, bytes):
                yield frame
            else:
                try:
                    yield json.loads(frame)
                except json.JSONDecodeError:
                    continue
