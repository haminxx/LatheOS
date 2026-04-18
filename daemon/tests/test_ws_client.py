"""Backoff math for the WS client.

We don't need a live server to verify the retry loop does what the docstring
claims — the work that matters is the delay schedule. Monkey-patch
`ws_connect` + `asyncio.sleep` and assert the sleeps fan out as expected.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from cam_daemon import ws_client
from cam_daemon.ws_client import CloudClient, CloudConfig


@pytest.mark.asyncio
async def test_connect_retries_transient_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    class _FakeWS:
        async def send(self, _: str) -> None:
            return None

        async def close(self) -> None:
            return None

    async def flaky_connect(*_args: object, **_kwargs: object) -> _FakeWS:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError("transient")
        return _FakeWS()

    slept: list[float] = []

    async def record_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(ws_client, "ws_connect", flaky_connect)
    monkeypatch.setattr(ws_client.asyncio, "sleep", record_sleep)

    client = CloudClient(CloudConfig(url="wss://x", hardware_token="t"))
    await client.connect()

    assert attempts == 3
    # Two sleeps preceding the successful third attempt.
    assert len(slept) == 2
    # Schedule: base=0.5, doubled per attempt, times a [0.5, 1.5) jitter factor.
    # Conservative bounds — attempt 1 ∈ [0.25, 0.75], attempt 2 ∈ [0.5, 1.5].
    assert 0.25 <= slept[0] <= 0.75
    assert 0.5 <= slept[1] <= 1.5


@pytest.mark.asyncio
async def test_connect_gives_up_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def always_fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("never ready")

    async def skip_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(ws_client, "ws_connect", always_fail)
    monkeypatch.setattr(ws_client.asyncio, "sleep", skip_sleep)

    client = CloudClient(CloudConfig(url="wss://x", hardware_token="t"))
    with pytest.raises(OSError):
        await client.connect()


def test_cloud_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAM_PROXY_URL", "wss://example.test/ws/cam")
    monkeypatch.setenv("CAM_HARDWARE_TOKEN", "deadbeef")
    monkeypatch.setenv("CAM_SAMPLE_RATE", "24000")
    cfg = CloudConfig.from_env()
    assert cfg.url == "wss://example.test/ws/cam"
    assert cfg.hardware_token == "deadbeef"
    assert cfg.sample_rate == 24000


# Type hint exists solely to document the fixture contract used above;
# pytest doesn't need it but keeping the file self-explanatory is cheap.
_ = Callable[[], Awaitable[None]]
