"""Daemon entrypoint. Wired by systemd via `ExecStart=cam-daemon`.

LatheOS is privacy-first and fully local. The whole assistant loop runs on
the USB — no microphone audio, transcript, or prompt ever leaves the
machine. There is no cloud, no account, no hardware token.

Lifecycle:
    boot -> idle-listen for a wake event (wake word / clap / control socket)
         -> capture one utterance from the mic
         -> whisper.cpp speech-to-text   (cam_daemon.stt)
         -> local Ollama multi-agent     (cam_daemon.agents.run_agents)
         -> Piper / MisoTTS speech-out    (cam_daemon.tts)
         -> optionally run an allowlisted command (cam_daemon.executor)
         -> publish the turn to the lathe shell (cam_daemon.bus)
         -> back to idle-listen.

A single mic stream feeds two queues: wake events and raw PCM. After a wake
fires we drain the PCM queue into one utterance, transcribe it, think, and
speak. Everything is best-effort: a missing model or a silent room logs and
returns to idle rather than taking the daemon down.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import signal

import structlog

from cam_daemon.agents import AgentConfig, run_agents
from cam_daemon.audio_io import play_pcm
from cam_daemon.bus import EventBus
from cam_daemon.camera import Camera
from cam_daemon.control_socket import ControlSocket
from cam_daemon.executor import dispatch
from cam_daemon.stt import SpeechToText, record_utterance
from cam_daemon.tts import TextToSpeech
from cam_daemon.vision import VisionRouter
from cam_daemon.wake import Activator

log = structlog.get_logger("cam-daemon")

# "Look through the camera" intents. We keep this dead simple (keyword match)
# rather than burning an LLM round-trip just to route — the worker LLM still
# does the real thinking once we have a description.
_LOCATE_RE = re.compile(
    r"\b(?:where(?:'s| is| are)|find(?: the| a| me)?|locate|point to|point at)\b\s+(?P<q>.+)",
    re.IGNORECASE,
)
_DESCRIBE_HINTS = (
    "what do you see", "what can you see", "what's in front", "what is in front",
    "describe what", "describe the", "look at this", "take a look", "look around",
    "what am i looking at", "what is this", "can you see",
)


def classify_vision(transcript: str) -> tuple[str | None, str]:
    """Return ('describe'|'locate'|None, query) for a transcript."""
    text = transcript.strip()
    low = text.lower()
    m = _LOCATE_RE.search(text)
    if m:
        return "locate", m.group("q").strip(" ?.!")
    if any(h in low for h in _DESCRIBE_HINTS):
        return "describe", text
    return None, ""


# "Walk me through / guide me on screen" intents → the local Screen Pilot
# (modules/screen-pilot.nix). These are explicit ON-SCREEN guidance requests,
# distinct from the camera "where is X" path above. We match a few natural
# phrasings and treat the rest of the utterance as the goal. The pilot itself
# screenshots the Sway session, plans, grounds, moves the cursor (opt-in), and
# narrates — so here we just hand it the goal and let it drive.
_PILOT_RE = re.compile(
    r"\b("
    r"walk me through|guide me(?: through| to)?|show me how to|"
    r"how do i|how do you|how can i|"
    r"where do i (?:click|go|find)|help me (?:find|get to|navigate)|"
    r"take me to"
    r")\b\s*(?P<goal>.*)",
    re.IGNORECASE,
)


def _pilot_enabled() -> bool:
    return os.environ.get("LATHEOS_PILOT_ENABLE", "0").strip() == "1"


def classify_pilot(transcript: str) -> str | None:
    """Return the on-screen guidance goal, or None if this isn't a pilot ask.

    Only fires when the Screen Pilot feature is enabled (env flag from
    /etc/latheos/pilot.env); otherwise we fall through to the normal agents.
    """
    if not _pilot_enabled():
        return None
    m = _PILOT_RE.search(transcript.strip())
    if not m:
        return None
    goal = m.group("goal").strip(" ?.!")
    # Fall back to the whole utterance if the phrasing had no trailing goal.
    return goal or transcript.strip()


# A {"action": ..., "command": ...} JSON object the local LLM may emit when it
# wants the OS to *do* something (open an app, run a vetted command). Voice
# execution is OFF by default — set LATHEOS_VOICE_EXEC=1 to let spoken requests
# trigger the (already allowlisted) executor.
_CMD_RE = re.compile(r"\{[^{}]*\"action\"\s*:\s*\"[^\"]+\"[^{}]*\}", re.DOTALL)


def _voice_exec_enabled() -> bool:
    return os.environ.get("LATHEOS_VOICE_EXEC", "0").strip() == "1"


async def _maybe_execute(results) -> None:
    """Scan worker outputs for a single JSON command and dispatch it.

    Conservative on purpose: only fires when LATHEOS_VOICE_EXEC=1, and the
    executor itself still enforces the bash allowlist. Voice should never be
    able to run arbitrary shell by default.
    """
    if not _voice_exec_enabled():
        return
    for r in results:
        match = _CMD_RE.search(r.output or "")
        if not match:
            continue
        try:
            command = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(command, dict) and command.get("action"):
            log.info("voice.dispatch", action=command.get("action"))
            await dispatch(command)
            return


async def _run_screen_pilot(goal: str, *, tts: TextToSpeech, bus: EventBus) -> None:
    """Hand an on-screen guidance goal to the local `lathe-pilot` CLI.

    Runs non-interactively (--auto): the pilot dwells on each step rather than
    waiting for an Enter key (there is no TTY here). It screenshots, plans,
    grounds, moves the cursor (opt-in), shows the floating card, and narrates
    itself — so the daemon just launches it and waits. Fully best-effort: a
    missing binary or a sandbox without Wayland access degrades to the pilot's
    own spoken/text fallback, and we never take the daemon down.

    Note: cam-daemon is a system service; grim/ydotool/eww need the Sway
    session env (WAYLAND_DISPLAY/XDG_RUNTIME_DIR). When those are absent the
    pilot says it can't see the screen — the $mod+g Sway keybind path always
    has them. See docs/SCREEN_PILOT.md.
    """
    await _speak(tts, "Let me walk you through it.")
    bus.publish({"type": "pilot", "phase": "voice_trigger", "goal": goal})
    try:
        proc = await asyncio.create_subprocess_exec(
            "lathe-pilot", "guide", goal, "--auto",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode not in (0, None):
            log.warning(
                "pilot.nonzero",
                rc=proc.returncode,
                stderr=(stderr.decode(errors="replace")[-200:] if stderr else ""),
            )
    except FileNotFoundError:
        log.warning("pilot.not_installed")
        await _speak(tts, "On-screen guidance isn't installed on this drive.")
    except Exception as exc:                # noqa: BLE001 — never fatal
        log.warning("pilot.failed", error=str(exc))


async def _speak(tts: TextToSpeech, text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    synth = await tts.synthesize(text)
    if synth is None:
        log.warning("tts.no_audio", text=text[:80])
        return
    pcm, rate = synth
    await asyncio.to_thread(play_pcm, pcm, rate)


async def _handle_vision(
    intent: str,
    query: str,
    transcript: str,
    *,
    camera: Camera,
    vision: VisionRouter,
    tts: TextToSpeech,
    bus: EventBus,
) -> None:
    """Camera path: grab a frame and describe it (Ollama VLM) or locate (LocateAnything)."""
    frame = await camera.capture()
    if frame is None:
        await _speak(tts, "I couldn't access the camera.")
        return
    try:
        if intent == "locate":
            result = await vision.locate(frame, query)
            boxes = (result or {}).get("boxes") or []
            if result and result.get("ok") and boxes:
                reply = f"I found {len(boxes)} match{'es' if len(boxes) != 1 else ''} for {query}."
            elif result and result.get("ok"):
                reply = f"I couldn't spot {query} in view."
            else:
                reply = "Object locating isn't available — enable the vision model to use it."
        else:
            described = await vision.describe(frame, transcript)
            reply = described or (
                "I can't describe images yet. Pull a vision model with "
                "'ollama pull llama3.2-vision' to enable it."
            )
    finally:
        with contextlib.suppress(OSError):
            os.unlink(frame)

    bus.publish({"type": "cam", "text": reply, "detail": [{"role": "vision", "task": intent}]})
    await _speak(tts, reply)


async def handle_activation(
    activation,
    *,
    stt: SpeechToText,
    tts: TextToSpeech,
    agent_cfg: AgentConfig,
    audio_q: "asyncio.Queue[bytes]",
    bus: EventBus,
    camera: Camera,
    vision: VisionRouter,
) -> None:
    """One full local turn: listen -> transcribe -> (see | think) -> speak."""
    pcm = await record_utterance(audio_q, sample_rate=stt.sample_rate)
    if not pcm:
        log.info("session.no_speech")
        return

    transcript = await stt.transcribe(pcm)
    if not transcript:
        log.info("session.empty_transcript")
        await _speak(tts, "Sorry, I didn't catch that.")
        return

    log.info("user.said", text=transcript)
    bus.publish({"type": "user", "text": transcript})

    # On-screen guidance ("walk me through / how do I") takes priority over the
    # generic agent path when the Screen Pilot feature is enabled.
    pilot_goal = classify_pilot(transcript)
    if pilot_goal is not None:
        log.info("pilot.intent", goal=pilot_goal)
        await _run_screen_pilot(pilot_goal, tts=tts, bus=bus)
        return

    intent, query = classify_vision(transcript)
    if intent is not None:
        log.info("vision.intent", intent=intent, query=query)
        await _handle_vision(
            intent, query, transcript,
            camera=camera, vision=vision, tts=tts, bus=bus,
        )
        return

    try:
        results = await run_agents(transcript, agent_cfg)
    except Exception as exc:                # noqa: BLE001 — never fatal
        log.exception("agents.failed", error=str(exc))
        await _speak(tts, "Something went wrong thinking that through.")
        return

    spoken = results[-1].output.strip() if results else ""
    detail = [
        {"role": r.role, "task": r.task, "output": r.output, "error": r.error}
        for r in results[:-1]
    ]
    bus.publish({"type": "cam", "text": spoken, "detail": detail})

    with contextlib.suppress(Exception):
        await _maybe_execute(results[:-1])

    await _speak(tts, spoken or "Done.")


async def main_loop() -> None:
    activator = Activator(
        access_key=os.environ.get("PICOVOICE_ACCESS_KEY"),
        keyword_path=os.environ.get("CAM_KEYWORD_PATH"),
        backend=os.environ.get("LATHEOS_WAKE_BACKEND"),
    )
    stt = SpeechToText()
    tts = TextToSpeech()
    agent_cfg = AgentConfig()
    bus = EventBus()
    camera = Camera()
    vision = VisionRouter()

    events, audio_q = await activator.listen()

    state = {"phase": "idle", "sessions": 0}
    control = ControlSocket(events, status_fn=lambda: dict(state))
    await control.start()

    log.info(
        "daemon.idle",
        mode="local",
        waiting_for="wake_word|clap|control_socket",
        stt_model=stt.model,
    )

    try:
        while True:
            activation = await events.get()
            state["phase"] = "active"
            state["sessions"] += 1
            log.info("wake.fired", kind=activation.kind, conf=activation.confidence)
            try:
                await handle_activation(
                    activation,
                    stt=stt,
                    tts=tts,
                    agent_cfg=agent_cfg,
                    audio_q=audio_q,
                    bus=bus,
                    camera=camera,
                    vision=vision,
                )
            except Exception as exc:        # noqa: BLE001 — keep listening
                log.exception("session.failed", error=str(exc))
            finally:
                state["phase"] = "idle"
                log.info("daemon.idle", waiting_for="wake_word|clap|control_socket")
    finally:
        await control.stop()
        activator.stop()


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
