"""Runtime configuration for the Screen Pilot, resolved from the environment.

All knobs come from /etc/latheos/pilot.env (written by modules/screen-pilot.nix)
with optional per-drive overrides in /persist/secrets/pilot.env. The defaults
here mirror those in the Nix module so the CLI behaves the same whether it is
launched by the daemon (env-file loaded) or by hand in a shell.

PRIVACY-FIRST: every URL below is loopback. There is no cloud endpoint and no
telemetry knob — by design.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _flag(name: str, default: bool) -> bool:
    """Parse a 0/1-style env flag, tolerant of '', 'true', 'yes'."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _str(name: str, default: str) -> str:
    val = os.environ.get(name)
    return val.strip() if val and val.strip() else default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class PilotConfig:
    """Everything the pilot needs to know, all from env (loopback only)."""

    # Master switch. When false the CLI still works in "describe-only" mode but
    # the daemon intent / keybind path is expected to short-circuit earlier.
    enable: bool = field(default_factory=lambda: _flag("LATHEOS_PILOT_ENABLE", False))

    # --- local model endpoints (loopback) ---------------------------------
    # Ollama for the step-planning VLM (scene understanding + plan).
    llm_url: str = field(
        default_factory=lambda: _str("LATHEOS_LLM_URL", "http://127.0.0.1:11434").rstrip("/")
    )
    # Vision-capable Ollama model the user pulls themselves (e.g. llama3.2-vision).
    vlm_model: str = field(default_factory=lambda: _str("LATHEOS_VLM_MODEL", ""))
    # LocateAnything-3B grounding service (opt-in, GPU-only) for pixel coords.
    vision_url: str = field(
        default_factory=lambda: _str("LATHEOS_VISION_URL", "http://127.0.0.1:11435").rstrip("/")
    )

    # --- input gating (conservative on purpose) ---------------------------
    # Move the cursor to the target? Safe (no state change) so on by default
    # once the feature is enabled. Still no-ops cleanly with no uinput/ydotool.
    allow_move: bool = field(default_factory=lambda: _flag("LATHEOS_PILOT_ALLOW_MOVE", True))
    # Synthetic CLICK is OFF by default and additionally requires an explicit
    # per-step confirmation. Mirrors the executor's allowlisted philosophy:
    # never auto-click destructive things.
    allow_click: bool = field(default_factory=lambda: _flag("LATHEOS_PILOT_ALLOW_CLICK", False))

    # --- presentation ------------------------------------------------------
    overlay: str = field(default_factory=lambda: _str("LATHEOS_PILOT_OVERLAY", "eww"))
    speak: bool = field(default_factory=lambda: _flag("LATHEOS_PILOT_TTS", True))
    # Seconds to dwell on each step in non-interactive (auto) mode.
    step_pause_s: float = field(default_factory=lambda: _float("LATHEOS_PILOT_STEP_PAUSE", 6.0))
    max_steps: int = field(default_factory=lambda: _int("LATHEOS_PILOT_MAX_STEPS", 8))

    # --- input plumbing ----------------------------------------------------
    ydotool_socket: str = field(
        default_factory=lambda: _str("YDOTOOL_SOCKET", "/run/ydotoold/socket")
    )

    # --- TTS (reuse the daemon's tiered Piper/MisoTTS contract) ------------
    piper_voice: str = field(
        default_factory=lambda: _str(
            "LATHEOS_PIPER_VOICE", "/assets/models/piper/en_US-amy-medium.onnx"
        )
    )
    tts_url: str = field(
        default_factory=lambda: _str("LATHEOS_TTS_URL", "http://127.0.0.1:11436").rstrip("/")
    )
    tts_backend: str = field(default_factory=lambda: _str("LATHEOS_TTS_BACKEND", "piper"))

    # --- event bus (share steps with the embedded shell HUD) --------------
    event_log: str = field(
        default_factory=lambda: _str("CAM_EVENT_LOG", "/run/cam-daemon/events.jsonl")
    )

    @classmethod
    def from_env(cls) -> PilotConfig:
        return cls()
