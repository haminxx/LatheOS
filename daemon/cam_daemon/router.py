"""Inference router — decide local (Engine A) vs cloud (Engine B) per request.

Cost ladder (cheapest stage that's confident wins):
    1. Keyword heuristic — free, instant. Catches the obvious "open the
       terminal" (local) and "refactor the whole repo" (cloud candidate) cases
       without touching a model. Mirrors the keyword routing already used for
       vision/pilot intents in ``__main__.py``.
    2. Network check — a cloud route is pointless offline, so anything that
       isn't clearly local is forced local when there's no connectivity.
    3. Tiny-model classify — only when 1+2 are inconclusive. Uses the small
       voice/classifier model (never the 12B) for a ~1-2s single-word label.

The router only ever marks a request as *cloud-eligible*. Hermes still applies
the confirm gate before anything leaves the device — routing never sends data.
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
from dataclasses import dataclass, field

import structlog

try:
    import httpx
except ImportError:                       # keep import-safe on bare CI
    httpx = None                          # type: ignore[assignment]

log = structlog.get_logger("cam-daemon.router")

# Intents that should always stay local (fast OS actions / chit-chat).
_LOCAL_RE = re.compile(
    r"\b(?:open|launch|start|close|quit|kill|volume|brightness|mute|"
    r"play|pause|screenshot|what(?:'s| is) the time|what time|set (?:a )?(?:timer|alarm)|"
    r"remind me|battery|wifi|wi-fi|bluetooth|connect to|disconnect|"
    r"list (?:files|the)|show me the|cd |ls |open file|read file)\b",
    re.IGNORECASE,
)

# Intents worth escalating to the frontier model (deep, long-horizon work).
_CLOUD_RE = re.compile(
    r"\b(?:refactor|debug|implement|write (?:a |the )?(?:code|function|program|app|script|test)|"
    r"architect|architecture|design (?:a |the )?(?:system|api|schema|database)|"
    r"across the (?:repo|codebase|project)|repository[- ]wide|whole (?:repo|codebase)|"
    r"deep research|research (?:the|how|whether)|investigate|"
    r"plan (?:out )?(?:a |the )?(?:migration|rollout|project)|optimi[sz]e the|"
    r"build (?:me )?(?:a|an|the) (?:app|service|feature|pipeline))\b",
    re.IGNORECASE,
)

_CLOUD_INTENTS = {"coding", "research", "architecture"}
_VALID_INTENTS = {"os_action", "simple", "coding", "research", "architecture"}


@dataclass(slots=True)
class RouterConfig:
    enabled: bool = field(default_factory=lambda: os.environ.get("LATHEOS_ROUTER_ENABLE", "1").strip() == "1")
    base_url: str = field(
        default_factory=lambda: os.environ.get("LATHEOS_LLM_URL", "http://127.0.0.1:11434").strip()
    )
    classifier_model: str = field(
        default_factory=lambda: (
            os.environ.get("LATHEOS_CLASSIFIER_MODEL")
            or os.environ.get("LATHEOS_VOICE_MODEL")
            or "llama3.2:3b"
        ).strip()
    )
    net_host: str = field(
        default_factory=lambda: os.environ.get("LATHEOS_NET_PROBE_HOST", "1.1.1.1").strip()
    )
    net_port: int = field(
        default_factory=lambda: int(os.environ.get("LATHEOS_NET_PROBE_PORT", "53") or "53")
    )
    classify_timeout_s: float = 8.0


@dataclass(slots=True)
class Decision:
    engine: str          # "local" | "cloud"
    intent: str          # os_action | simple | coding | research | architecture
    reason: str
    online: bool


_CLASSIFY_SYSTEM = (
    "You label a user request with exactly one word from this set: "
    "os_action, simple, coding, research, architecture. "
    "os_action = control the computer/app. simple = quick factual or chit-chat. "
    "coding = write/refactor/debug software. research = deep multi-source lookup. "
    "architecture = system/design planning. "
    "Reply with ONLY the single label word, nothing else."
)


def network_up(cfg: RouterConfig) -> bool:
    try:
        with socket.create_connection((cfg.net_host, cfg.net_port), timeout=1.5):
            return True
    except OSError:
        return False


async def _classify(prompt: str, cfg: RouterConfig) -> str | None:
    if httpx is None:
        return None
    payload = {
        "model": cfg.classifier_model,
        "system": _CLASSIFY_SYSTEM,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 8},
    }
    try:
        async with httpx.AsyncClient(timeout=cfg.classify_timeout_s) as client:
            resp = await client.post(f"{cfg.base_url}/api/generate", json=payload)
            resp.raise_for_status()
            raw = (resp.json().get("response") or "").strip().lower()
    except Exception as exc:                # noqa: BLE001 — degrade to local
        log.warning("router.classify_failed", error=str(exc))
        return None
    for label in _VALID_INTENTS:
        if label in raw:
            return label
    return None


async def route(prompt: str, cfg: RouterConfig | None = None) -> Decision:
    """Decide where a request should run. Never raises; defaults to local."""
    cfg = cfg or RouterConfig()
    text = (prompt or "").strip()

    if not cfg.enabled or not text:
        return Decision("local", "simple", "router disabled", False)

    # Stage 1 — keyword heuristic (free).
    if _LOCAL_RE.search(text):
        return Decision("local", "os_action", "keyword: local OS/simple", False)
    cloud_keyword = bool(_CLOUD_RE.search(text))

    # Stage 2 — network. Cloud is impossible offline.
    online = await asyncio.to_thread(network_up, cfg)
    if not online:
        intent = "coding" if cloud_keyword else "simple"
        return Decision("local", intent, "offline -> forced local", False)

    # Stage 3 — tiny-model classify (only model call the router makes).
    intent = await _classify(text, cfg)
    if intent is None:
        # Couldn't classify: trust the keyword signal, else stay local.
        if cloud_keyword:
            return Decision("cloud", "coding", "keyword: cloud (classify unavailable)", True)
        return Decision("local", "simple", "classify unavailable -> local", True)

    if intent in _CLOUD_INTENTS or cloud_keyword:
        chosen = intent if intent in _CLOUD_INTENTS else "coding"
        return Decision("cloud", chosen, f"classified: {chosen}", True)
    return Decision("local", intent, f"classified: {intent}", True)
