"""Bridge between the lathe shell and the cam-daemon voice loop.

Two tiny, crash-proof pieces:

  * `VoiceBusReader` tails the daemon's append-only event log
    (`CAM_EVENT_LOG`, default /run/cam-daemon/events.jsonl) so spoken turns
    — what you said and what CAM replied — show up live in the chat strip.
  * `push_to_talk()` injects a wake activation over the daemon's control
    socket (`CAM_CONTROL_SOCKET`), so a keybind in the shell reuses the exact
    same local listen -> whisper -> Ollama -> TTS loop a wake word would.

Both degrade silently when cam-daemon isn't running: the reader returns no
events, PTT returns False. The shell never crashes because the daemon is down.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket

EVENT_LOG = os.environ.get("CAM_EVENT_LOG", "/run/cam-daemon/events.jsonl")
CONTROL_SOCK = os.environ.get("CAM_CONTROL_SOCKET", "/run/cam-daemon/control.sock")


class VoiceBusReader:
    """Incrementally read new JSON lines from the daemon event log."""

    def __init__(self, path: str = EVENT_LOG) -> None:
        self.path = path
        self._pos = 0
        self._inode: int | None = None
        self._primed = False

    def poll(self) -> list[dict]:
        events: list[dict] = []
        try:
            st = os.stat(self.path)
        except OSError:
            return events

        # Handle rotation / truncation (daemon trims the file periodically).
        if self._inode is not None and st.st_ino != self._inode:
            self._pos = 0
        self._inode = st.st_ino
        if st.st_size < self._pos:
            self._pos = 0

        try:
            with open(self.path, encoding="utf-8") as fh:
                # First successful poll: skip existing history, only show what
                # happens from now on.
                if not self._primed:
                    fh.seek(0, os.SEEK_END)
                    self._pos = fh.tell()
                    self._primed = True
                    return events
                fh.seek(self._pos)
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    with contextlib.suppress(ValueError):
                        events.append(json.loads(line))
                self._pos = fh.tell()
        except OSError:
            pass
        return events


def push_to_talk() -> bool:
    """Inject a wake activation into cam-daemon. True if it was accepted."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(CONTROL_SOCK)
            s.sendall(b'{"cmd": "activate", "kind": "wake_word"}\n')
            with contextlib.suppress(OSError):
                s.recv(256)
        return True
    except OSError:
        return False
