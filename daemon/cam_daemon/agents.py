"""CAM multi-agent orchestrator — local, parallel, role-specialised.

Design
------
The voice loop should feel like one assistant but be doing many things
concurrently (the same pattern coding agents use): a fast *Dispatcher*
reads the user's intent and spawns 2..N *Workers* that call different
specialised prompts on the local LLM pool at the same time, then a
*Merger* condenses their outputs into one reply.

Why this pattern
    * Latency: 4 small calls in parallel usually beat 1 huge sequential call.
    * Quality: each worker is given ONE job (code, plan, safety, speech),
      which keeps prompts short and reduces model confusion.
    * Composability: new skills = add a worker role in AGENT_ROLES. No
      other code changes required.

What runs where
    * Dispatcher + Speaker  → voice model (LATHEOS_VOICE_MODEL, ~3B).
    * Coder, Planner, Critic → heavy model (LATHEOS_HEAVY_MODEL, 8B–22B).
    * All calls hit the local Ollama instance on 127.0.0.1:11434. No data
      leaves the USB unless the cloud proxy is explicitly turned on.

This module is intentionally small and self-contained so it can be unit
tested without the audio stack.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

import structlog

try:
    import httpx
except ImportError:                       # keep import-safe on bare CI
    httpx = None                           # type: ignore[assignment]

log = structlog.get_logger("cam-daemon.agents")


# ---------------------------------------------------------------------------
# Role catalogue
# ---------------------------------------------------------------------------

AgentTier = Literal["voice", "heavy"]


@dataclass(frozen=True, slots=True)
class AgentRole:
    """One specialist profile. `tier` picks which local model serves it."""

    name: str
    tier: AgentTier
    system: str

    def model_env_var(self) -> str:
        return (
            "LATHEOS_VOICE_MODEL" if self.tier == "voice" else "LATHEOS_HEAVY_MODEL"
        )


AGENT_ROLES: dict[str, AgentRole] = {
    "dispatcher": AgentRole(
        name="dispatcher",
        tier="voice",
        system=(
            "You are CAM's Dispatcher. Read the user's request and return a "
            "JSON array of at most 4 sub-tasks, each with keys "
            "'role' (one of: planner, coder, critic, speaker) and 'task' "
            "(short imperative string). Never include explanations."
        ),
    ),
    "planner": AgentRole(
        name="planner",
        tier="heavy",
        system=(
            "You are CAM's Planner. Decompose the task into 3-6 concrete, "
            "ordered steps. Keep each step under 12 words. Output plain text."
        ),
    ),
    "coder": AgentRole(
        name="coder",
        tier="heavy",
        system=(
            "You are CAM's Coder. Produce minimal code or a unified diff "
            "that satisfies the task. Assume NixOS + LatheOS context. "
            "Return only code fenced in a single block."
        ),
    ),
    "critic": AgentRole(
        name="critic",
        tier="heavy",
        system=(
            "You are CAM's Critic. Scan the other workers' outputs for "
            "security, data-loss, or reversibility risks on a NixOS system. "
            "Reply with a short bullet list; be terse."
        ),
    ),
    "speaker": AgentRole(
        name="speaker",
        tier="voice",
        system=(
            "You are CAM's Speaker. Summarise the assembled results for the "
            "user in at most 3 sentences. Calm, factual, no emojis."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Config + pool
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AgentConfig:
    """Resolved endpoints and model names for one run."""

    base_url: str = field(
        default_factory=lambda: os.environ.get(
            "LATHEOS_LLM_URL", "http://127.0.0.1:11434"
        )
    )
    voice_model: str = field(
        default_factory=lambda: os.environ.get(
            "LATHEOS_VOICE_MODEL", "llama3.2:3b"
        )
    )
    heavy_model: str = field(
        default_factory=lambda: os.environ.get(
            "LATHEOS_HEAVY_MODEL", "llama3.1:8b"
        )
    )
    max_parallel: int = field(
        default_factory=lambda: int(os.environ.get("LATHEOS_MAX_AGENTS", "4"))
    )
    request_timeout_s: float = 60.0

    def model_for(self, role: AgentRole) -> str:
        return self.voice_model if role.tier == "voice" else self.heavy_model


@dataclass(slots=True)
class AgentResult:
    role: str
    task: str
    output: str
    error: str | None = None


# ---------------------------------------------------------------------------
# HTTP call
# ---------------------------------------------------------------------------


async def _call_ollama(
    client: "httpx.AsyncClient",
    cfg: AgentConfig,
    role: AgentRole,
    task: str,
) -> AgentResult:
    """Single LLM turn for one role. Never raises — errors are captured."""
    payload = {
        "model": cfg.model_for(role),
        "stream": False,
        "system": role.system,
        "prompt": task,
        "options": {"temperature": 0.3, "num_predict": 512},
    }
    try:
        resp = await client.post(
            f"{cfg.base_url}/api/generate",
            json=payload,
            timeout=cfg.request_timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        return AgentResult(role=role.name, task=task, output=data.get("response", ""))
    except Exception as exc:                # noqa: BLE001 — surfaced in result
        log.warning("agent.call_failed", role=role.name, error=str(exc))
        return AgentResult(role=role.name, task=task, output="", error=str(exc))


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def _dispatch_plan(
    client: "httpx.AsyncClient", cfg: AgentConfig, user_request: str
) -> list[dict[str, str]]:
    """Ask the Dispatcher for the sub-task list. Falls back to a sane default."""
    role = AGENT_ROLES["dispatcher"]
    out = await _call_ollama(client, cfg, role, user_request)
    if out.error or not out.output.strip():
        return [
            {"role": "planner", "task": user_request},
            {"role": "coder", "task": user_request},
            {"role": "critic", "task": user_request},
        ]
    try:
        start = out.output.find("[")
        end = out.output.rfind("]")
        plan = json.loads(out.output[start : end + 1])
        if not isinstance(plan, list):
            raise ValueError("dispatcher did not return a list")
    except Exception:                       # noqa: BLE001
        return [
            {"role": "planner", "task": user_request},
            {"role": "coder", "task": user_request},
        ]
    return [
        p for p in plan
        if isinstance(p, dict)
        and p.get("role") in AGENT_ROLES
        and isinstance(p.get("task"), str)
    ][: cfg.max_parallel]


async def run_agents(
    user_request: str,
    cfg: AgentConfig | None = None,
) -> list[AgentResult]:
    """Execute the full Dispatcher → parallel Workers → Speaker flow.

    Returns every AgentResult (including the final Speaker turn). The caller
    decides what to show on screen vs. read aloud — typically:

        results[-1]  → the Speaker summary to feed Piper TTS
        results[:-1] → detail the embedded shell can render
    """
    if httpx is None:
        raise RuntimeError(
            "httpx is not installed — install it to enable the agent pool"
        )
    cfg = cfg or AgentConfig()

    async with httpx.AsyncClient() as client:
        plan = await _dispatch_plan(client, cfg, user_request)
        log.info("agents.plan", count=len(plan), plan=plan)

        worker_calls = [
            _call_ollama(client, cfg, AGENT_ROLES[step["role"]], step["task"])
            for step in plan
        ]
        worker_results = await asyncio.gather(*worker_calls)

        merged = "\n\n".join(
            f"[{r.role}] {r.output.strip()}" for r in worker_results if r.output
        )
        speaker = await _call_ollama(
            client,
            cfg,
            AGENT_ROLES["speaker"],
            f"User asked: {user_request}\n\nWorker findings:\n{merged}",
        )

    return [*worker_results, speaker]


def select_roles(names: Iterable[str]) -> list[AgentRole]:
    """Resolve role names; unknown names are silently dropped."""
    return [AGENT_ROLES[n] for n in names if n in AGENT_ROLES]
