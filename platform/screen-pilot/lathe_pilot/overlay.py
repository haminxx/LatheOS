"""Floating step card via eww (ElKowars wacky widgets) on wlr-layer-shell.

Sway is wlroots-based, so a layer-shell surface is the correct way to float a
card ABOVE the tiled windows without becoming a managed window. eww is in
nixpkgs and speaks wlr-layer-shell (window `:stacking "overlay"`), so we drive
it rather than hand-rolling a gtk-layer-shell surface — fewer moving parts, all
local, easy to restyle to the LatheOS monochrome aesthetic.

We generate a tiny, self-contained eww config in a tmp dir at runtime (so we
never depend on the user's ~/.config/eww), start an isolated eww daemon for it,
and per step: push the text via `eww update`, position the window near the
target, and open it. The card shows ONE step at a time:

    Step 2 of 5
    Click the Settings gear, top-right

Everything degrades gracefully: if eww isn't installed or fails to start, the
overlay quietly no-ops and the pilot still moves the cursor + narrates. The
step text is ALSO published to the cam-daemon event bus (see pilot.py) so the
embedded shell HUD can show it even when no overlay renders.

eww reference: https://github.com/elkowar/eww
wlr-layer-shell: https://wayland.app/protocols/wlr-layer-shell-unstable-v1
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from shutil import which

# Monochrome to match modules/sway.nix's palette (near-black surface, near-white
# text, 1px border, zero translucency).
_SCSS = """
* { all: unset; font-family: "JetBrains Mono", monospace; }
.pilot-card {
  background-color: #141414;
  border: 1px solid #f2f2f2;
  padding: 12px 14px;
  margin: 6px;
}
.pilot-index {
  color: #8a8a8a;
  font-size: 11px;
  margin-bottom: 4px;
}
.pilot-step {
  color: #e8e8e8;
  font-size: 15px;
}
.pilot-hint {
  color: #8a8a8a;
  font-size: 11px;
  margin-top: 6px;
}
"""

_YUCK = """
(defvar step_text "")
(defvar step_index "")
(defvar step_hint "")
(defvar card_x "60px")
(defvar card_y "60px")

(defwidget card []
  (box :class "pilot-card" :orientation "v" :space-evenly false
    (label :class "pilot-index" :halign "start" :text step_index)
    (label :class "pilot-step"  :halign "start" :wrap true :limit-width 42 :text step_text)
    (label :class "pilot-hint"  :halign "start" :text step_hint)))

(defwindow pilot-card
  :monitor 0
  :stacking "overlay"
  :focusable false
  :geometry (geometry :x card_x :y card_y :width "360px" :height "0px" :anchor "top left")
  (card))
"""


class EwwOverlay:
    """An isolated eww instance that renders the step card. Best-effort."""

    def __init__(self) -> None:
        self._dir: str | None = None
        self._started = False

    @staticmethod
    def available() -> bool:
        return which("eww") is not None

    def _ensure_config(self) -> str:
        if self._dir and os.path.isdir(self._dir):
            return self._dir
        d = tempfile.mkdtemp(prefix="pilot-eww-")
        with open(os.path.join(d, "eww.yuck"), "w", encoding="utf-8") as fh:
            fh.write(_YUCK)
        with open(os.path.join(d, "eww.scss"), "w", encoding="utf-8") as fh:
            fh.write(_SCSS)
        self._dir = d
        return d

    def _eww(self, *args: str, timeout: float = 8.0) -> int:
        d = self._ensure_config()
        try:
            proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                ["eww", "--config", d, *args],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
            return proc.returncode
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return 1

    def start(self) -> bool:
        if not self.available():
            return False
        if self._started:
            return True
        # An explicit daemon start makes failures obvious; opening a window
        # would auto-start one anyway.
        self._eww("daemon")
        self._started = True
        return True

    def show(
        self,
        *,
        index_text: str,
        step_text: str,
        x: int,
        y: int,
        hint: str = "",
    ) -> bool:
        """Position + populate the card and (re)open it. Returns False on no-op."""
        if not self.start():
            return False
        # Offset a little down-right of the target so the card doesn't cover it.
        cx = max(0, int(x) + 24)
        cy = max(0, int(y) + 24)
        self._eww(
            "update",
            f"step_index={index_text}",
            f"step_text={step_text}",
            f"step_hint={hint}",
            f"card_x={cx}px",
            f"card_y={cy}px",
        )
        # Close then open so the geometry change is re-evaluated (eww does not
        # always reposition an already-open window on a var change).
        self._eww("close", "pilot-card")
        return self._eww("open", "pilot-card") == 0

    def close(self) -> None:
        if self._started:
            self._eww("close", "pilot-card")

    def shutdown(self) -> None:
        if self._started:
            self._eww("close", "pilot-card")
            self._eww("kill")
            self._started = False
        if self._dir:
            for name in ("eww.yuck", "eww.scss"):
                _safe_unlink(os.path.join(self._dir, name))
            try:
                os.rmdir(self._dir)
            except OSError:
                pass
            self._dir = None


class NullOverlay:
    """No-op overlay used when eww is disabled/unavailable. Always succeeds quietly."""

    @staticmethod
    def available() -> bool:
        return False

    def start(self) -> bool:
        return False

    def show(self, **_kw) -> bool:
        return False

    def close(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


def make_overlay(kind: str):
    """Factory: 'eww' -> EwwOverlay (if installed), anything else -> NullOverlay."""
    if (kind or "").strip().lower() == "eww" and EwwOverlay.available():
        return EwwOverlay()
    return NullOverlay()


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
