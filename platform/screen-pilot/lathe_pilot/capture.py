"""Wayland/wlroots screenshot capture via grim.

Sway is wlroots-based, so grim (the wlroots screenshot tool) is the right,
boring way to grab the screen. We write to a 0600 tmpfile that NEVER leaves the
machine — it is handed straight to the local grounding model and unlinked by
the caller (mirrors daemon/cam_daemon/camera.py).

Degrades gracefully: no grim on PATH, no Wayland socket, or a failed grab just
returns None and the pilot falls back to a description-only walkthrough.

grim references:
  https://sr.ht/~emersion/grim/  (wlr-screencopy based screenshotter)
"""

from __future__ import annotations

import os
import subprocess
import tempfile

# A region as (x, y, w, h) in layout pixels, passed to grim -g "x,y wxh".
Region = tuple[int, int, int, int]


def capture_screen(output: str | None = None, region: Region | None = None) -> str | None:
    """Grab the screen (or a region) to a PNG tmpfile. Returns the path or None.

    `output`  : restrict to a named wlroots output (e.g. "eDP-1"); None = all.
    `region`  : (x, y, w, h) to capture just a slice; None = full screen.
    """
    fd, out = tempfile.mkstemp(suffix=".png", prefix="pilot-shot-")
    os.close(fd)
    # 0600 — the screenshot may contain anything on screen; keep it private.
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass

    argv = ["grim"]
    if output:
        argv += ["-o", output]
    if region:
        x, y, w, h = region
        argv += ["-g", f"{x},{y} {w}x{h}"]
    argv.append(out)

    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
    except FileNotFoundError:
        _cleanup(out)
        return None
    except (subprocess.TimeoutExpired, OSError):
        _cleanup(out)
        return None

    if proc.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    _cleanup(out)
    return None


def _cleanup(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def grim_available() -> bool:
    """Cheap check that grim is on PATH."""
    from shutil import which

    return which("grim") is not None
