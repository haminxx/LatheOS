"""Persistent WebSocket client to CAM Cloud Proxy.

`connect()` performs the hello handshake with exponential backoff — short
Wi-Fi drops, a restarting proxy instance, or a brief ALB failover no longer
drop the wake-to-cloud flow on the floor. Once connected, a mid-session
disconnect is reported by the async iterator ending; the outer daemon loop
returns to idle-listen and the next wake event opens a fresh session.

The client exposes three operations:

    - `connect()`     : open the WS, send the hello frame, retry transient failures
    - `send_audio()`  : forward a PCM chunk; silently no-op if disconnected
    - `events()`      : async iterator over decoded JSON frames and raw bytes
    - `close()`       : best-effort teardown (safe to call more than once)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass

import structlog
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import InvalidStatus, WebSocketException

log = structlog.get_logger("cam-daemon.ws")

_CONNECT_BASE_DELAY_S = 0.5
_CONNECT_MAX_DELAY_S = 15.0
_CONNECT_MAX_ATTEMPTS = 6


@dataclass(slots=True)
class CloudConfig:
    url: str
    hardware_token: str
    sample_rate: int = 16_000

    @classmethod
    def from_env(cls) -> CloudConfig:
        return cls(
            url=os.environ.get("CAM_PROXY_URL", "wss://cam.example.com/ws/cam"),
            hardware_token=os.environ.get("CAM_HARDWARE_TOKEN", ""),
            sample_rate=int(os.environ.get("CAM_SAMPLE_RATE", "16000")),
        )


class CloudClient:
    def __init__(self, cfg: CloudConfig) -> None:
        self.cfg = cfg
        self._ws: ClientConnection | None = None

    async def connect(self) -> None:
        """Open the WS and send the hello frame, retrying transient failures.

        Auth rejections (4xx close codes) are raised immediately — a stale
        hardware token will never succeed on a retry and should surface to
        the operator, not loop forever.
        """
        last_exc: Exception | None = None
        for attempt in range(1, _CONNECT_MAX_ATTEMPTS + 1):
            try:
                self._ws = await ws_connect(
                    self.cfg.url,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2 * 1024 * 1024,
                )
                await self._ws.send(
                    json.dumps(
                        {
                            "type": "hello",
                            "hardware_token": self.cfg.hardware_token,
                            "sample_rate": self.cfg.sample_rate,
                            "encoding": "linear16",
                            "client_version": "0.1.0",
                        }
                    )
                )
                return
            except InvalidStatus as exc:
                # 401/403 etc — permanent; don't burn battery retrying.
                log.error("cloud.handshake_rejected", status=exc.response.status_code)
                raise
            except (OSError, TimeoutError, WebSocketException) as exc:
                last_exc = exc
                delay = min(
                    _CONNECT_MAX_DELAY_S,
                    _CONNECT_BASE_DELAY_S * 2 ** (attempt - 1),
                )
                # Full jitter; don't hammer the ALB in lockstep with every
                # other lathe-01, lathe-02, ... coming back online.
                delay *= 0.5 + random.random()
                log.warning(
                    "cloud.connect.retry",
                    attempt=attempt,
                    delay_s=round(delay, 2),
                    error=str(exc),
                )
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    async def send_audio(self, pcm: bytes) -> None:
        if self._ws is not None:
            await self._ws.send(pcm)

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    async def events(self) -> AsyncIterator[dict | bytes]:
        assert self._ws is not None, "call connect() before events()"
        async for frame in self._ws:
            if isinstance(frame, bytes):
                yield frame
            else:
                try:
                    yield json.loads(frame)
                except json.JSONDecodeError:
                    continue
