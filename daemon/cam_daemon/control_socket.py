"""Local control socket.

Exposes a Unix domain socket (default `/run/cam-daemon/control.sock`) so
operators and tests can nudge the daemon without a microphone. Each command
is a single newline-terminated JSON object:

    {"cmd": "activate", "kind": "wake_word"}
    {"cmd": "activate", "kind": "clap", "confidence": 0.9}
    {"cmd": "status"}

On `activate` we enqueue an `Activation` into the same asyncio.Queue the
wake detector feeds, so the rest of the pipeline cannot tell the difference
between a real wake and a synthetic one.

The socket is chmod 0660 on creation and the group is inherited from the
daemon's supplementary groups — pair it with a Nix module that puts the
operator user in the `cam` group to grant access.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from pathlib import Path

import structlog

from cam_daemon.wake import Activation

log = structlog.get_logger("cam-daemon.control")

DEFAULT_SOCK = "/run/cam-daemon/control.sock"


class ControlSocket:
    def __init__(
        self,
        queue: asyncio.Queue[Activation],
        path: str | None = None,
        status_fn: Callable[[], dict] | None = None,
    ) -> None:
        self.queue = queue
        self.path = path or os.environ.get("CAM_CONTROL_SOCKET", DEFAULT_SOCK)
        self._status_fn = status_fn or (lambda: {"state": "unknown"})
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        sock_path = Path(self.path)
        sock_path.parent.mkdir(parents=True, exist_ok=True)
        if sock_path.exists():
            sock_path.unlink()

        self._server = await asyncio.start_unix_server(
            self._handle, path=str(sock_path)
        )
        os.chmod(sock_path, 0o660)
        log.info("control.listening", path=str(sock_path))

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        try:
            Path(self.path).unlink()
        except FileNotFoundError:
            pass

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            raw = await reader.readline()
            if not raw:
                return
            try:
                msg = json.loads(raw.decode().strip())
            except json.JSONDecodeError as exc:
                await self._reply(writer, {"ok": False, "error": f"bad_json: {exc}"})
                return

            reply = await self._dispatch(msg)
            await self._reply(writer, reply)
        finally:
            writer.close()
            with _suppress():
                await writer.wait_closed()

    async def _dispatch(self, msg: dict) -> dict:
        cmd = msg.get("cmd")
        if cmd == "activate":
            kind = msg.get("kind", "wake_word")
            if kind not in ("wake_word", "clap"):
                return {"ok": False, "error": f"unknown_kind: {kind}"}
            confidence = float(msg.get("confidence", 1.0))
            await self.queue.put(Activation(kind=kind, confidence=confidence))
            log.info("control.activate.injected", kind=kind, confidence=confidence)
            return {"ok": True, "enqueued": {"kind": kind, "confidence": confidence}}

        if cmd == "status":
            return {"ok": True, "status": self._status_fn()}

        if cmd == "ping":
            return {"ok": True, "pong": True}

        return {"ok": False, "error": f"unknown_cmd: {cmd}"}

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, payload: dict) -> None:
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()


class _suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return True
