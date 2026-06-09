"""Lightweight memory injection for the chat strip.

The embedded shell is a deliberately thin, crash-proof Ollama client (see
``llm.py``) that must keep working even when cam-daemon is down. So rather than
import the daemon's full Hermes/memory stack (a separate Nix package), we read
the two *cheap* memory tiers straight off disk:

    Core  — ``/persist/state/core.yml`` (identity + directives), injected
            verbatim. Same file Hermes uses.
    Trend — the tail of the daemon event bus + the greeter session snapshot,
            for recent context continuity.

The third tier (General / vector RAG) needs embedding round-trips and lives in
the daemon's Hermes; the shell intentionally skips it to stay snappy. For deep,
memory-rich answers — local or cloud — use ``lathe-cloud "..."`` from the
terminal, which runs the full Hermes brain.

Everything here is best-effort: a missing file yields an empty string, never an
exception.
"""

from __future__ import annotations

import contextlib
import json
import os

CORE_PATH = os.environ.get("LATHEOS_CORE_MEMORY", "/persist/state/core.yml")
EVENT_LOG = os.environ.get("LATHEOS_TREND_EVENTS") or os.environ.get(
    "CAM_EVENT_LOG", "/run/cam-daemon/events.jsonl"
)
SESSION_FILE = os.environ.get("LATHEOS_SESSION_FILE", "/persist/state/session.json")
_TREND_TURNS = int(os.environ.get("LATHEOS_TREND_TURNS", "6") or "6")


def _load_core() -> str:
    with contextlib.suppress(OSError):
        with open(CORE_PATH, encoding="utf-8") as fh:
            return fh.read().strip()
    return ""


def _load_trend() -> str:
    lines: list[str] = []

    with contextlib.suppress(OSError, json.JSONDecodeError):
        with open(SESSION_FILE, encoding="utf-8") as fh:
            sess = json.load(fh)
        if isinstance(sess, dict):
            last = sess.get("last_task") or sess.get("task")
            if last:
                lines.append(f"last task: {last}")

    with contextlib.suppress(OSError):
        with open(EVENT_LOG, encoding="utf-8") as fh:
            tail = fh.readlines()[-(_TREND_TURNS * 2):]
        for raw in tail:
            with contextlib.suppress(json.JSONDecodeError):
                ev = json.loads(raw)
                text = (ev.get("text") or "").strip()
                if not text:
                    continue
                if ev.get("type") == "user":
                    lines.append(f"user: {text}")
                elif ev.get("type") == "cam":
                    lines.append(f"assistant: {text}")
    return "\n".join(lines[-(_TREND_TURNS * 2):]).strip()


def memory_brief() -> str:
    """Assemble the Core + Trend memory block for the chat system prompt."""
    parts: list[str] = []
    core = _load_core()
    if core:
        parts.append("# CORE MEMORY (authoritative)\n" + core)
    trend = _load_trend()
    if trend:
        parts.append("# RECENT SESSION (most recent last)\n" + trend)
    return "\n\n".join(parts).strip()
