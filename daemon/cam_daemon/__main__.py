"""Daemon entrypoint. Wired by systemd via `ExecStart=cam-daemon`.

Lifecycle:
    boot → idle-listen for wake event → open WS to CAM → duplex stream
         → on hang-up or long silence, go back to idle-listen.

A single mic stream feeds two queues: wake events and raw PCM. During an
active session we run two tasks in parallel — one pumps mic PCM up to
the cloud, the other consumes server frames (transcripts, TTS audio,
JSON commands). Either task ending tears the session down cleanly.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys

import structlog

from cam_daemon.audio_io import SpeakerSink
from cam_daemon.control_socket import ControlSocket
from cam_daemon.executor import dispatch
from cam_daemon.wake import Activator
from cam_daemon.ws_client import CloudClient, CloudConfig

log = structlog.get_logger("cam-daemon")

# Session auto-ends after this much contiguous silence of server events
# to keep DynamoDB quota tight. The server will also hang up on its own
# timeout; whichever fires first wins.
_SESSION_MAX_IDLE_S = 20.0


async def _pump_mic(cloud: CloudClient, audio_q: asyncio.Queue[bytes]) -> None:
    """Forward every PCM frame from the mic to the cloud until cancelled."""
    while True:
        frame = await audio_q.get()
        await cloud.send_audio(frame)


async def _pump_events(cloud: CloudClient, speaker: SpeakerSink) -> None:
    """Consume server frames; play TTS, log transcripts, run commands."""
    async for event in cloud.events():
        if isinstance(event, bytes):
            speaker.write(event)
            continue
        etype = event.get("type")
        if etype == "transcript" and event.get("is_final"):
            log.info("transcript.final", text=event.get("text"))
        elif etype == "command":
            await dispatch(event)
        elif etype == "error":
            log.error("cloud.error", **event)
            return


async def active_session(
    cloud: CloudClient,
    speaker: SpeakerSink,
    audio_q: asyncio.Queue[bytes],
) -> None:
    await cloud.connect()
    log.info("cloud.connected")

    # Both tasks share the WS; whichever finishes first tears the session
    # down so a dead server can't leave us pumping mic into the void.
    send_task = asyncio.create_task(_pump_mic(cloud, audio_q), name="pump-mic")
    recv_task = asyncio.create_task(_pump_events(cloud, speaker), name="pump-events")
    done, pending = await asyncio.wait(
        {send_task, recv_task},
        return_when=asyncio.FIRST_COMPLETED,
        timeout=_SESSION_MAX_IDLE_S,
    )
    for t in pending:
        t.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await asyncio.gather(*pending, return_exceptions=True)
    for t in done:
        exc = t.exception()
        if exc is not None:
            log.warning("session.task_failed", task=t.get_name(), error=str(exc))


async def main_loop() -> None:
    cfg = CloudConfig.from_env()
    if not cfg.hardware_token:
        log.error("boot.missing_hardware_token")
        sys.exit(2)

    activator = Activator(
        access_key=os.environ.get("PICOVOICE_ACCESS_KEY"),
        keyword_path=os.environ.get("CAM_KEYWORD_PATH"),
        backend=os.environ.get("LATHEOS_WAKE_BACKEND"),
    )
    speaker = SpeakerSink(sample_rate=24_000)
    speaker.start()

    events, audio_q = await activator.listen()

    state = {"phase": "idle", "sessions": 0}
    control = ControlSocket(events, status_fn=lambda: dict(state))
    await control.start()

    log.info("daemon.idle", waiting_for="wake_word|clap|control_socket")

    try:
        while True:
            activation = await events.get()
            state["phase"] = "active"
            state["sessions"] += 1
            log.info("wake.fired", kind=activation.kind, conf=activation.confidence)
            cloud = CloudClient(cfg)
            try:
                await active_session(cloud, speaker, audio_q)
            except Exception as exc:
                log.exception("session.failed", error=str(exc))
            finally:
                await cloud.close()
                state["phase"] = "idle"
                log.info("daemon.idle", waiting_for="wake_word|clap|control_socket")
    finally:
        await control.stop()
        activator.stop()
        speaker.stop()


def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    task = loop.create_task(main_loop())
    try:
        loop.run_until_complete(asyncio.wait({task}, timeout=None))
    finally:
        task.cancel()
        loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
        loop.close()


if __name__ == "__main__":
    main()
