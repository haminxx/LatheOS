"""The Screen Pilot engine: capture -> plan -> ground -> guide.

This is the local, Wayland-native answer to Clicky's loop. For a user goal:

    1. grim screenshots the Sway session (tmpfile, never leaves the machine).
    2. The local Ollama VLM produces an ordered step plan; each step has an
       imperative instruction + a grounding PHRASE for its target element.
    3. Per step we RE-CAPTURE (the screen changes as you go), ask
       LocateAnything-3B to resolve the phrase to a pixel point, and bake a
       Clicky-style [POINT:x,y:label] tag from that ground-truth pixel.
    4. (opt-in) ydotool warps the cursor to the point.
    5. An eww layer-shell card shows the single current step near the target.
    6. The local TTS narrates the instruction.
    7. We advance on user confirmation (interactive) or a dwell timer (auto).
       Clicking is gated twice (allow_click + explicit confirm) and never
       happens automatically.

Degrades gracefully at EVERY stage: no grim -> describe-only; vision down ->
no cursor target (still narrate + card with a centred fallback); no ydotool ->
no movement; no eww -> HUD/text only. It must never crash the session.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from dataclasses import dataclass, field

from .capture import capture_screen, grim_available
from .config import PilotConfig
from .cursor import CursorController
from .eventbus import EventBus
from .overlay import make_overlay
from .tags import render_tag
from .tts_client import Narrator
from .vision_client import VisionClient
from .vlm import Step, plan_steps


@dataclass(slots=True)
class Capabilities:
    grim: bool = False
    vision: bool = False
    cursor: bool = False
    cursor_socket: bool = False
    overlay: bool = False
    vlm: bool = False

    def summary(self) -> str:
        def mark(b: bool) -> str:
            return "yes" if b else "no"

        return (
            f"grim={mark(self.grim)} vlm={mark(self.vlm)} "
            f"grounding={mark(self.vision)} cursor={mark(self.cursor)}"
            f"(socket={mark(self.cursor_socket)}) overlay={mark(self.overlay)}"
        )


@dataclass(slots=True)
class Pilot:
    cfg: PilotConfig
    interactive: bool = True
    # When True, attempt synthetic clicks after confirmation (still requires
    # cfg.allow_click). Default False — move + describe only.
    want_click: bool = False

    _vision: VisionClient = field(init=False)
    _cursor: CursorController = field(init=False)
    _narrator: Narrator = field(init=False)
    _bus: EventBus = field(init=False)

    def __post_init__(self) -> None:
        self._vision = VisionClient(self.cfg.vision_url)
        self._cursor = CursorController(self.cfg.ydotool_socket)
        self._narrator = Narrator(
            backend=self.cfg.tts_backend,
            piper_voice=self.cfg.piper_voice,
            tts_url=self.cfg.tts_url,
            enabled=self.cfg.speak,
        )
        self._bus = EventBus(self.cfg.event_log)

    # ---- capabilities -------------------------------------------------------

    def probe(self) -> Capabilities:
        """Best-effort capability scan. Never raises."""
        caps = Capabilities()
        caps.grim = grim_available()
        caps.cursor = self._cursor.available()
        caps.cursor_socket = self._cursor.socket_ready()
        caps.overlay = make_overlay(self.cfg.overlay).available()
        caps.vlm = bool(self.cfg.vlm_model)
        with contextlib.suppress(Exception):
            caps.vision = self._vision.health()
        return caps

    # ---- main loop ----------------------------------------------------------

    def guide(self, goal: str) -> int:
        """Run the full pilot for `goal`. Returns a process exit code."""
        goal = (goal or "").strip()
        if not goal:
            self._emit({"type": "pilot", "phase": "error", "text": "No goal given."})
            print("lathe-pilot: no goal given", file=sys.stderr)
            return 2

        caps = self.probe()
        self._emit({"type": "pilot", "phase": "start", "goal": goal, "caps": caps.summary()})

        if not caps.grim:
            # Without a screenshot we can't plan visually at all. Say so.
            msg = (
                "Screen Pilot can't capture the screen (grim missing), so I "
                "can't see where things are. Make sure you're in the Sway session."
            )
            self._say_and_show(msg, "Screen Pilot", x=80, y=80, hint="")
            return 1

        shot = capture_screen()
        if shot is None:
            self._say_and_show(
                "I couldn't grab the screen just now.", "Screen Pilot", x=80, y=80
            )
            return 1

        try:
            steps, err = plan_steps(
                self.cfg.llm_url,
                self.cfg.vlm_model,
                goal,
                shot,
                max_steps=self.cfg.max_steps,
            )
        finally:
            _safe_unlink(shot)

        if err:
            self._emit({"type": "pilot", "phase": "plan_degraded", "error": err})

        total = len(steps)
        overlay = make_overlay(self.cfg.overlay)
        try:
            self._say(f"Okay. {total} step{'s' if total != 1 else ''} to {goal}.")
            for i, step in enumerate(steps):
                action = self._run_step(i, total, step, caps, overlay)
                if action == "quit":
                    self._say("Stopping the walkthrough.")
                    break
        finally:
            overlay.shutdown()

        self._emit({"type": "pilot", "phase": "done", "goal": goal})
        return 0

    # ---- one step -----------------------------------------------------------

    def _run_step(self, i: int, total: int, step: Step, caps: Capabilities, overlay) -> str:
        index_text = f"Step {i + 1} of {total}"
        point = None
        tag = ""

        if step.target and caps.vision:
            shot = capture_screen()
            if shot is not None:
                try:
                    res = self._vision.point_at(shot, step.target)
                finally:
                    _safe_unlink(shot)
                if res.get("ok") and res.get("point"):
                    point = res["point"]
                    tag = render_tag(point["x"], point["y"], step.target)

        self._emit(
            {
                "type": "pilot",
                "phase": "step",
                "index": i + 1,
                "total": total,
                "instruction": step.instruction,
                "target": step.target,
                "tag": tag,
            }
        )

        # Move the cursor to the resolved point (non-destructive, opt-in).
        moved = False
        if point and self.cfg.allow_move:
            r = self._cursor.move_absolute(int(point["x"]), int(point["y"]))
            moved = r.ok
            if not r.ok and r.detail:
                self._emit({"type": "pilot", "phase": "move_failed", "detail": r.detail})

        # Card position: near the target if we have one, else a sane corner.
        cx, cy = (int(point["x"]), int(point["y"])) if point else (80, 80)
        hint = self._hint(caps, has_target=point is not None)
        self._show(overlay, index_text, step.instruction, cx, cy, hint)

        # Narrate the instruction (and a soft note if we couldn't locate it).
        spoken = step.instruction
        if step.target and point is None:
            spoken += ". I couldn't pinpoint it on screen, so look for it yourself."
        elif moved:
            spoken += ". I've moved the pointer there."
        self._say(spoken)

        return self._advance(point)

    # ---- advance / confirm --------------------------------------------------

    def _advance(self, point: dict | None) -> str:
        """Return 'next' | 'quit'. May perform a confirmed, gated click."""
        if not self.interactive or not sys.stdin or not sys.stdin.isatty():
            # Non-interactive (daemon / keybind): dwell, never click.
            time.sleep(max(0.5, self.cfg.step_pause_s))
            return "next"

        clickable = point is not None and self.want_click and self.cfg.allow_click
        prompt = "[Enter] next  ·  [q] quit"
        if clickable:
            prompt = "[Enter] next  ·  [c] click here  ·  [q] quit"
        try:
            choice = input(f"  {prompt} > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "quit"

        if choice == "q":
            return "quit"
        if choice == "c" and clickable:
            r = self._cursor.click()
            self._emit({"type": "pilot", "phase": "click", "ok": r.ok, "backend": r.backend})
            if not r.ok:
                self._say("I couldn't click for you.")
        return "next"

    # ---- helpers ------------------------------------------------------------

    def _hint(self, caps: Capabilities, *, has_target: bool) -> str:
        if not self.interactive or not sys.stdin or not sys.stdin.isatty():
            return ""
        if has_target and self.want_click and self.cfg.allow_click:
            return "Enter: next  ·  c: click  ·  q: quit"
        return "Enter: next  ·  q: quit"

    def _show(self, overlay, index_text: str, text: str, x: int, y: int, hint: str) -> None:
        with contextlib.suppress(Exception):
            overlay.show(index_text=index_text, step_text=text, x=x, y=y, hint=hint)

    def _say(self, text: str) -> None:
        with contextlib.suppress(Exception):
            self._narrator.speak(text)

    def _say_and_show(self, text: str, index_text: str, *, x: int, y: int, hint: str = "") -> None:
        overlay = make_overlay(self.cfg.overlay)
        try:
            self._show(overlay, index_text, text, x, y, hint)
            self._say(text)
        finally:
            overlay.shutdown()

    def _emit(self, event: dict) -> None:
        with contextlib.suppress(Exception):
            self._bus.publish(event)


def _safe_unlink(path: str | None) -> None:
    if not path:
        return
    with contextlib.suppress(OSError):
        os.unlink(path)
