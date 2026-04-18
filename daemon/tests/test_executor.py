"""Executor allowlist & dispatch tests.

`_run` is monkey-patched so no real subprocesses spawn — we just verify the
routing decisions (allow / reject / unknown-action) that the daemon makes
before shelling out.
"""

from __future__ import annotations

import pytest

from cam_daemon import executor


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    async def fake_run(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    monkeypatch.setattr(executor, "_run", fake_run)
    return calls


@pytest.mark.asyncio
async def test_allowlisted_bash_runs(seen: list[list[str]]) -> None:
    await executor.dispatch({"action": "execute_bash", "command": "docker compose up -d"})
    assert seen == [["bash", "-lc", "docker compose up -d"]]


@pytest.mark.asyncio
async def test_unlisted_bash_rejected(seen: list[list[str]]) -> None:
    # `rm -rf /` is the canonical example of why we have an allowlist.
    await executor.dispatch({"action": "execute_bash", "command": "rm -rf /"})
    assert seen == []


@pytest.mark.asyncio
async def test_noop_does_nothing(seen: list[list[str]]) -> None:
    await executor.dispatch({"action": "noop", "command": "ignored"})
    await executor.dispatch({"action": "execute_bash", "command": ""})
    assert seen == []


@pytest.mark.asyncio
async def test_sway_msg_splits_arguments(seen: list[list[str]]) -> None:
    await executor.dispatch({"action": "sway_msg", "command": "exec cursor ."})
    assert seen == [["swaymsg", "--", "exec", "cursor", "."]]


@pytest.mark.asyncio
async def test_open_app_splits_arguments(seen: list[list[str]]) -> None:
    await executor.dispatch({"action": "open_app", "command": "firefox https://example.com"})
    assert seen == [["firefox", "https://example.com"]]


@pytest.mark.asyncio
async def test_unknown_action_is_dropped(seen: list[list[str]]) -> None:
    await executor.dispatch({"action": "eval", "command": "__import__('os').system('pwn')"})
    assert seen == []
