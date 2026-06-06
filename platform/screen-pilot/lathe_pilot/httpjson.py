"""Tiny stdlib JSON-over-HTTP helpers (loopback only).

We avoid httpx/requests so this package has ZERO third-party deps and builds
straight from nixpkgs. All calls target 127.0.0.1 services (Ollama,
LocateAnything, MisoTTS), so urllib is plenty and the noqa: S310 is benign.

Every helper is crash-proof: on any network / decode error it returns a clean
{"ok": False, "error": ...} dict (or None for GET) instead of raising. Vision /
TTS are optional — a failure must degrade, never take the session down.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


def post_json(url: str, body: dict, *, timeout: float = 600.0) -> dict:
    """POST a JSON body, return the decoded dict or a clean error dict."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — loopback
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw) if raw else {}
        if isinstance(parsed, dict):
            parsed.setdefault("ok", True)
            return parsed
        return {"ok": True, "data": parsed}
    except urllib.error.HTTPError as exc:
        # Try to surface the server's JSON error body if there is one.
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("ok", False)
                return payload
        except (ValueError, OSError):
            pass
        return {"ok": False, "error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, ValueError, OSError) as exc:
        return {"ok": False, "error": str(exc)}


def get_json(url: str, *, timeout: float = 5.0) -> dict | None:
    """GET a JSON document, return the decoded dict or None on any failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — loopback
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw) if raw else None
        return parsed if isinstance(parsed, dict) else None
    except (urllib.error.URLError, ValueError, OSError):
        return None


def post_bytes(url: str, body: dict, *, timeout: float = 60.0) -> bytes | None:
    """POST a JSON body, return the raw response bytes (e.g. a WAV) or None.

    Used for endpoints that answer with binary (the MisoTTS /synthesize route
    returns audio/wav, not JSON). Crash-proof like the rest: any failure returns
    None so the caller can fall back.
    """
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — loopback
            return resp.read()
    except (urllib.error.URLError, OSError):
        return None
