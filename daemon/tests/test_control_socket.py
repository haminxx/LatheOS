"""ControlSocket dispatch tests.

Exercising the router directly keeps the tests portable — Windows dev boxes
can't bind Unix domain sockets, but the decision logic is pure Python.
"""

from __future__ import annotations

import asyncio

import pytest

from cam_daemon.control_socket import ControlSocket
from cam_daemon.wake import Activation


@pytest.fixture
def cs() -> tuple[ControlSocket, asyncio.Queue[Activation]]:
    q: asyncio.Queue[Activation] = asyncio.Queue()
    return ControlSocket(q, status_fn=lambda: {"phase": "idle", "sessions": 0}), q


@pytest.mark.asyncio
async def test_ping(cs: tuple[ControlSocket, asyncio.Queue[Activation]]) -> None:
    socket, _ = cs
    assert await socket._dispatch({"cmd": "ping"}) == {"ok": True, "pong": True}


@pytest.mark.asyncio
async def test_status_returns_fn_output(
    cs: tuple[ControlSocket, asyncio.Queue[Activation]],
) -> None:
    socket, _ = cs
    resp = await socket._dispatch({"cmd": "status"})
    assert resp["ok"] is True
    assert resp["status"] == {"phase": "idle", "sessions": 0}


@pytest.mark.asyncio
async def test_activate_wake_word_enqueues(
    cs: tuple[ControlSocket, asyncio.Queue[Activation]],
) -> None:
    socket, q = cs
    resp = await socket._dispatch({"cmd": "activate", "kind": "wake_word"})
    assert resp["ok"] is True
    activation = q.get_nowait()
    assert activation.kind == "wake_word"
    assert activation.confidence == 1.0


@pytest.mark.asyncio
async def test_activate_clap_carries_confidence(
    cs: tuple[ControlSocket, asyncio.Queue[Activation]],
) -> None:
    socket, q = cs
    resp = await socket._dispatch({"cmd": "activate", "kind": "clap", "confidence": 0.73})
    assert resp["ok"] is True
    activation = q.get_nowait()
    assert activation.kind == "clap"
    assert activation.confidence == pytest.approx(0.73)


@pytest.mark.asyncio
async def test_unknown_kind_rejected(
    cs: tuple[ControlSocket, asyncio.Queue[Activation]],
) -> None:
    socket, q = cs
    resp = await socket._dispatch({"cmd": "activate", "kind": "voodoo"})
    assert resp["ok"] is False
    assert "unknown_kind" in resp["error"]
    assert q.empty()


@pytest.mark.asyncio
async def test_unknown_cmd_rejected(
    cs: tuple[ControlSocket, asyncio.Queue[Activation]],
) -> None:
    socket, _ = cs
    resp = await socket._dispatch({"cmd": "self_destruct"})
    assert resp["ok"] is False
    assert "unknown_cmd" in resp["error"]
