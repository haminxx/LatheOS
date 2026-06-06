"""Tiny append-only event bus shared between the daemon and the lathe shell.

The daemon publishes one JSON object per line to a small log file
(`CAM_EVENT_LOG`, default `/run/cam-daemon/events.jsonl`). The embedded
shell tails that file so a spoken voice turn shows up in the on-screen chat
strip. This is intentionally a dumb, crash-proof, append-only file — no
socket server to babysit, no shared state to corrupt. If the write fails
(read-only /run, disk full, whatever) we swallow it: surfacing a transcript
is a nicety, never something that should take the assistant down.
"""

from __future__ import annotations

import contextlib
import json
import os
import time

DEFAULT_EVENT_LOG = "/run/cam-daemon/events.jsonl"

# Keep the file from growing without bound on a long-lived session. We rewrite
# it to the last N lines whenever it crosses the cap.
_MAX_LINES = 500


class EventBus:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.environ.get("CAM_EVENT_LOG", DEFAULT_EVENT_LOG)

    def publish(self, event: dict) -> None:
        record = {"ts": round(time.time(), 3), **event}
        with contextlib.suppress(OSError):
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._maybe_trim()

    def _maybe_trim(self) -> None:
        with contextlib.suppress(OSError):
            with open(self.path, encoding="utf-8") as fh:
                lines = fh.readlines()
            if len(lines) > _MAX_LINES:
                with open(self.path, "w", encoding="utf-8") as fh:
                    fh.writelines(lines[-_MAX_LINES:])
