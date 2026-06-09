"""Engine B — the cloud frontier model, OpenAI-compatible and confirm-gated.

LatheOS is local-first. This module is the ONLY place a user prompt can leave
the device, and it never fires on its own: Hermes calls it solely after the
router picks "cloud" AND the user confirms that specific task (see
``hermes.Hermes`` / the voice + ``lathe-cloud`` confirm gates).

Provider-agnostic: we speak the OpenAI ``/chat/completions`` shape, which both
OpenRouter and NVIDIA NIM expose, so switching providers is just a URL + model +
key-name change in ``/etc/latheos/llm.env``.

API key resolution (most → least trusted, first hit wins):
    1. ``LATHEOS_CLOUD_API_KEY`` env (the daemon gets this from
       ``/run/latheos/cloud.env``, written by the root ``latheos-cloud-key``
       service that decrypts the vault — the daemon user can't read the age key
       directly).
    2. ``/run/latheos/cloud.env`` read directly (for the ``lathe-cloud`` CLI).
    3. ``vault get NAME`` / ``sudo vault get NAME`` (interactive CLI fallback;
       the dev user has passwordless sudo).

Privacy: outbound payloads are redacted of obvious secrets before send.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

import structlog

from cam_daemon.redact import redact

try:
    import httpx
except ImportError:                       # keep import-safe on bare CI
    httpx = None                          # type: ignore[assignment]

log = structlog.get_logger("cam-daemon.cloud")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(slots=True)
class CloudConfig:
    enabled: bool = field(default_factory=lambda: _env("LATHEOS_CLOUD_ENABLE", "0") == "1")
    base_url: str = field(
        default_factory=lambda: _env("LATHEOS_CLOUD_URL", "https://openrouter.ai/api/v1")
    )
    model: str = field(
        default_factory=lambda: _env("LATHEOS_CLOUD_MODEL", "nvidia/nemotron-3-ultra")
    )
    key_name: str = field(
        default_factory=lambda: _env("LATHEOS_CLOUD_API_KEY_NAME", "OPENROUTER_API_KEY")
    )
    key_env_file: str = field(
        default_factory=lambda: _env("LATHEOS_CLOUD_KEY_FILE", "/run/latheos/cloud.env")
    )
    timeout_s: float = field(
        default_factory=lambda: float(_env("LATHEOS_CLOUD_TIMEOUT", "120") or "120")
    )
    max_tokens: int = field(
        default_factory=lambda: int(_env("LATHEOS_CLOUD_MAX_TOKENS", "1024") or "1024")
    )


def _read_key_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("LATHEOS_CLOUD_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _vault_get(name: str) -> str:
    for cmd in (["vault", "get", name], ["sudo", "-n", "vault", "get", name]):
        try:
            out = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10, check=False
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
    return ""


def resolve_api_key(cfg: CloudConfig) -> str:
    return (
        _env("LATHEOS_CLOUD_API_KEY")
        or _read_key_file(cfg.key_env_file)
        or _vault_get(cfg.key_name)
    )


class CloudEngine:
    def __init__(self, cfg: CloudConfig | None = None) -> None:
        self.cfg = cfg or CloudConfig()

    def available(self) -> tuple[bool, str]:
        """Is the cloud usable right now? Returns (ok, reason-if-not)."""
        if not self.cfg.enabled:
            return False, "cloud disabled (LATHEOS_CLOUD_ENABLE=0)"
        if httpx is None:
            return False, "httpx not installed"
        if not self.cfg.model:
            return False, "no cloud model configured (LATHEOS_CLOUD_MODEL)"
        if not resolve_api_key(self.cfg):
            return False, f"no API key (store '{self.cfg.key_name}' in the vault)"
        return True, ""

    async def generate(self, system: str, user: str) -> str:
        """One cloud completion. Redacts the payload first. Raises on failure
        so Hermes can fall back to the local engine."""
        if httpx is None:
            raise RuntimeError("httpx is not installed")
        key = resolve_api_key(self.cfg)
        if not key:
            raise RuntimeError(f"no API key for {self.cfg.key_name}")

        body = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": redact(system)},
                {"role": "user", "content": redact(user)},
            ],
            "max_tokens": self.cfg.max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            # OpenRouter likes attribution headers; harmless elsewhere.
            "HTTP-Referer": "https://latheos.local",
            "X-Title": "LatheOS Hermes",
        }
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(timeout=self.cfg.timeout_s) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("cloud returned no choices")
        return (choices[0].get("message") or {}).get("content", "").strip()
