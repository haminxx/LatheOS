"""Synthetic cursor movement + clicks on Wayland — the hard part.

Wayland (unlike X11) deliberately FORBIDS one client from warping the pointer
or injecting clicks into another client. There is no XTEST equivalent. The
standard escape hatch is to inject at the kernel level via /dev/uinput, which
is exactly what ydotool does (a ydotoold daemon owns the uinput device and a
client talks to it over a socket). modules/screen-pilot.nix provisions the
ydotoold systemd service + the uinput group/udev rule.

  Primary  : ydotool   — uinput virtual mouse, supports ABSOLUTE moves + clicks.
              `ydotool mousemove --absolute -x X -y Y`
              `ydotool click 0xC0`   (0xC0 = left press+release)
  Fallback : wlrctl    — `wlrctl pointer move` is RELATIVE only on most setups,
              so we can only use it for clicks, not absolute targeting.
  (wtype is keyboard-only and is used elsewhere; it cannot move the pointer.)

SAFETY: clicks are gated twice — the caller must pass allow_click=True (env
LATHEOS_PILOT_ALLOW_CLICK=1) AND the engine only ever calls click() after an
explicit per-step user confirmation. Movement is non-destructive and allowed by
default, but still no-ops cleanly if ydotoold/uinput are unavailable.

ydotool reference: https://github.com/ReimuNotMoe/ydotool
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from shutil import which

# ydotool button code for a left press + release in one shot.
YDOTOOL_LEFT_CLICK = "0xC0"


@dataclass(slots=True)
class CursorResult:
    ok: bool
    backend: str
    detail: str = ""


class CursorController:
    """Move / click the pointer via the best available local backend."""

    def __init__(self, ydotool_socket: str | None = None) -> None:
        # ydotool finds the daemon socket via $YDOTOOL_SOCKET.
        self.socket = ydotool_socket or os.environ.get("YDOTOOL_SOCKET", "/run/ydotoold/socket")
        self._env = dict(os.environ)
        if self.socket:
            self._env["YDOTOOL_SOCKET"] = self.socket

    # ---- capability probes --------------------------------------------------

    def have_ydotool(self) -> bool:
        return which("ydotool") is not None

    def have_wlrctl(self) -> bool:
        return which("wlrctl") is not None

    def available(self) -> bool:
        """True if ANY synthetic-input backend is usable."""
        return self.have_ydotool() or self.have_wlrctl()

    def socket_ready(self) -> bool:
        """ydotoold up? (the socket exists). Not fatal if missing — we degrade."""
        try:
            return bool(self.socket) and os.path.exists(self.socket)
        except OSError:
            return False

    # ---- actions ------------------------------------------------------------

    def move_absolute(self, x: int, y: int) -> CursorResult:
        """Warp the pointer to absolute (x, y). No-ops gracefully if unable."""
        if self.have_ydotool():
            rc, err = self._run(
                ["ydotool", "mousemove", "--absolute", "-x", str(int(x)), "-y", str(int(y))]
            )
            if rc == 0:
                return CursorResult(True, "ydotool")
            return CursorResult(False, "ydotool", err)
        # wlrctl pointer move is relative-only; we cannot reliably do an
        # absolute warp with it, so we honestly report "no absolute backend".
        return CursorResult(False, "none", "no absolute-move backend (ydotool missing)")

    def click(self) -> CursorResult:
        """Left-click at the current pointer position. Caller must have gated this."""
        if self.have_ydotool():
            rc, err = self._run(["ydotool", "click", YDOTOOL_LEFT_CLICK])
            if rc == 0:
                return CursorResult(True, "ydotool")
            return CursorResult(False, "ydotool", err)
        if self.have_wlrctl():
            rc, err = self._run(["wlrctl", "pointer", "click", "left"])
            if rc == 0:
                return CursorResult(True, "wlrctl")
            return CursorResult(False, "wlrctl", err)
        return CursorResult(False, "none", "no click backend (ydotool/wlrctl missing)")

    # ---- internals ----------------------------------------------------------

    def _run(self, argv: list[str]) -> tuple[int, str]:
        try:
            proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                argv,
                env=self._env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
        except FileNotFoundError:
            return 127, f"{argv[0]} not found"
        except (subprocess.TimeoutExpired, OSError) as exc:
            return 1, str(exc)
        err = proc.stderr.decode(errors="replace")[-200:] if proc.stderr else ""
        return proc.returncode, err
