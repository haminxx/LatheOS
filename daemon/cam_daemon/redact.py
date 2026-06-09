"""Secret redaction — shared by every path that exports or stores text.

Centralised so the cloud sender (before a prompt leaves the device) and the
memory store (before a snippet is persisted to disk) strip the exact same secret
shapes. One source of truth = one place to harden.
"""

from __future__ import annotations

import re

# A whole assignment line whose name looks secret (FOO_API_KEY=..., TOKEN: ...).
_SECRET_LINE = re.compile(
    r"(?im)^\s*(?:export\s+)?[A-Z0-9_]*"
    r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|API)[A-Z0-9_]*\s*[=:].*$"
)
# Common provider token shapes, even when they appear mid-sentence.
_TOKEN_SHAPE = re.compile(
    r"\b(?:sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{12,}|AIza[0-9A-Za-z_\-]{20,})\b"
)
# The on-device secrets directory should never be echoed in full.
_SECRET_PATH = re.compile(r"/persist/secrets/\S+")


def redact(text: str) -> str:
    """Strip obvious secrets from text. Conservative: over-redact, never leak."""
    if not text:
        return text
    text = _SECRET_LINE.sub("[REDACTED-SECRET]", text)
    text = _TOKEN_SHAPE.sub("[REDACTED-TOKEN]", text)
    text = _SECRET_PATH.sub("/persist/secrets/[REDACTED]", text)
    return text
