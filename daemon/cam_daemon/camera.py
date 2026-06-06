"""On-demand webcam capture for the assistant's "eyes".

Grabs a single frame from the local camera with ffmpeg (v4l2) when the user
asks the assistant to look at something. The frame is written to a tmpfile
and never leaves the machine — it's handed straight to a local vision model
(see cam_daemon.vision). Everything degrades gracefully: no camera, no
ffmpeg, or a disabled feature just returns None and the daemon says so.

Provisioned by modules/camera.nix (ffmpeg on PATH, the `video` group, and
LATHEOS_CAMERA_* env).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile

import structlog

log = structlog.get_logger("cam-daemon.camera")

DEFAULT_DEVICE = "/dev/video0"


class Camera:
    def __init__(self) -> None:
        self.device = os.environ.get("LATHEOS_CAMERA_DEVICE", DEFAULT_DEVICE)
        self.enabled = os.environ.get("LATHEOS_CAMERA_ENABLE", "1").strip() == "1"

    async def capture(self) -> str | None:
        """Grab one frame; return the JPEG path or None if unavailable."""
        if not self.enabled:
            log.info("camera.disabled")
            return None
        if not os.path.exists(self.device):
            log.warning("camera.device_missing", device=self.device)
            return None

        fd, out = tempfile.mkstemp(suffix=".jpg", prefix="cam-frame-")
        os.close(fd)
        argv = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "v4l2", "-i", self.device,
            "-frames:v", "1", out,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
        except FileNotFoundError:
            log.warning("camera.ffmpeg_missing")
            with contextlib.suppress(OSError):
                os.unlink(out)
            return None

        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out

        log.warning("camera.capture_failed",
                    rc=proc.returncode,
                    stderr=stderr.decode(errors="replace")[-200:])
        with contextlib.suppress(OSError):
            os.unlink(out)
        return None
