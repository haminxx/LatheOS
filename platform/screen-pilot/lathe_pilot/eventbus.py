"""Append-only event publisher, compatible with the cam-daemon event bus.

The pilot writes one JSON object per line to CAM_EVENT_LOG (default
/run/cam-daemon/events.jsonl) so the embedded shell HUD can show the current
step even when no graphical overlay renders. Mirrors daemon/cam_daemon/bus.py:
a dumb, crash-proof, append-only file — any write failure is swallowed because
surfacing a step is a nicety, never something that should take the pilot down.
"""

from __future__ import annotations

import contextlib
import json
import os
import time


class EventBus:
    def __init__(self, path: str) -> None:
        self.path = path

    def publish(self, event: dict) -> None:
        record = {"ts": round(time.time(), 3), **event}
        with contextlib.suppress(OSError):
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
