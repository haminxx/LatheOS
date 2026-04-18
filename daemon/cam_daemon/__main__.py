"""Daemon entrypoint. Wired by systemd via `ExecStart=cam-daemon`.

Lifecycle:
    boot → idle-listen for wake event → open WS to CAM → duplex stream
         → on hang-up or long silence, go back to idle-listen.
"""

from __future__ import annotations

import asyncio
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


async def active_session(cloud: CloudClient, speaker: SpeakerSink) -> None:
    await cloud.connect()
    log.info("cloud.connected")

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


async def main_loop() -> None:
    cfg = CloudConfig.from_env()
    if not cfg.hardware_token:
        log.error("boot.missing_hardware_token")
        sys.exit(2)

    activator = Activator(
        access_key=os.environ.get("PICOVOICE_ACCESS_KEY"),
        keyword_path=os.environ.get("CAM_KEYWORD_PATH"),
    )
    speaker = SpeakerSink(sample_rate=24_000)
    speaker.start()

    events = await activator.listen()

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
                await active_session(cloud, speaker)
            except Exception as exc:
                log.exception("session.failed", error=str(exc))
            finally:
                await cloud.close()
                state["phase"] = "idle"
                log.info("daemon.idle", waiting_for="wake_word|clap|control_socket")
    finally:
        await control.stop()


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
