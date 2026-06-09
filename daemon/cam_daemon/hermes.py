"""Hermes — the single brain that orchestrates every assistant turn.

Hermes is the one entrypoint the voice loop and the ``lathe-cloud`` CLI both
call. It owns the whole decision, in this order:

    1. Assemble 3-tier memory (Core + General + Trend) — identical for every
       engine, so an answer feels continuous whether it ran local or cloud.
    2. Ask the router whether this looks like a cloud-eligible task.
    3. If cloud-eligible AND a confirm callback approves it, send to Engine B
       (cloud). Otherwise (declined, offline, unconfigured, or it errors) run
       Engine A (local Ollama). Local is always the safe default.
    4. Remember the turn into General memory (best-effort).

The legacy 5-role fan-out in ``agents.py`` is no longer the main path. It's kept
as an optional "deep local" tool behind ``LATHEOS_DEEP_LOCAL=1`` for users who
want heavier offline reasoning at the cost of latency.

Confirm gate
------------
``respond(..., confirm=cb)`` takes an async callback ``cb(reason) -> bool``. The
voice loop speaks the ask and listens; the CLI prompts ``[y/N]``. Nothing leaves
the device unless that callback returns True. If it's None, cloud is treated as
declined (safe default).
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog

try:
    import httpx
except ImportError:                       # keep import-safe on bare CI
    httpx = None                          # type: ignore[assignment]

from cam_daemon.cloud import CloudEngine
from cam_daemon.memory import ContextBundle, MemoryEngine
from cam_daemon.router import Decision, RouterConfig, route

log = structlog.get_logger("cam-daemon.hermes")

ConfirmCallback = Callable[[str], Awaitable[bool]]

_BASE_SYSTEM = (
    "You are Hermes, the orchestrator intelligence of LatheOS — a privacy-first, "
    "AI-native operating system. You are calm, precise, and helpful. Prefer short, "
    "direct answers unless the task clearly needs depth. Never invent system facts."
)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


@dataclass(slots=True)
class HermesConfig:
    base_url: str = field(default_factory=lambda: _env("LATHEOS_LLM_URL", "http://127.0.0.1:11434"))
    voice_model: str = field(default_factory=lambda: _env("LATHEOS_VOICE_MODEL", "llama3.2:3b"))
    heavy_model: str = field(default_factory=lambda: _env("LATHEOS_HEAVY_MODEL", "llama3.1:8b"))
    deep_local: bool = field(default_factory=lambda: _env("LATHEOS_DEEP_LOCAL", "0") == "1")
    cloud_confirm: bool = field(default_factory=lambda: _env("LATHEOS_CLOUD_CONFIRM", "1") == "1")
    local_timeout_s: float = 90.0

    def local_model_for(self, intent: str) -> str:
        # Quick intents get the snappy voice model; everything else thinks
        # with the heavy (RAM-autoselected) model.
        return self.voice_model if intent in {"os_action", "simple"} else self.heavy_model


@dataclass(slots=True)
class HermesReply:
    text: str
    engine: str               # "local" | "cloud"
    intent: str
    reason: str
    online: bool
    detail: list[dict] = field(default_factory=list)


class Hermes:
    def __init__(
        self,
        cfg: HermesConfig | None = None,
        *,
        memory: MemoryEngine | None = None,
        cloud: CloudEngine | None = None,
        router_cfg: RouterConfig | None = None,
    ) -> None:
        self.cfg = cfg or HermesConfig()
        self.memory = memory or MemoryEngine()
        self.cloud = cloud or CloudEngine()
        self.router_cfg = router_cfg or RouterConfig()

    # -- prompt assembly ----------------------------------------------------

    def _build_system(self, ctx: ContextBundle) -> str:
        suffix = ctx.system_suffix()
        return f"{_BASE_SYSTEM}\n\n{suffix}" if suffix else _BASE_SYSTEM

    # -- local engine -------------------------------------------------------

    async def _local_generate(self, system: str, user: str, model: str) -> str:
        if httpx is None:
            raise RuntimeError("httpx is not installed")
        payload = {
            "model": model,
            "system": system,
            "prompt": user,
            "stream": False,
            "options": {"temperature": 0.4, "num_predict": 768},
        }
        async with httpx.AsyncClient(timeout=self.cfg.local_timeout_s) as client:
            resp = await client.post(f"{self.cfg.base_url}/api/generate", json=payload)
            resp.raise_for_status()
            return (resp.json().get("response") or "").strip()

    async def _run_local(
        self, user_input: str, system: str, decision: Decision
    ) -> tuple[str, list[dict]]:
        # Optional heavyweight offline path: the legacy multi-agent fan-out.
        if self.cfg.deep_local and decision.intent not in {"os_action", "simple"}:
            try:
                from cam_daemon.agents import run_agents

                results = await run_agents(user_input)
                detail = [
                    {"role": r.role, "task": r.task, "output": r.output, "error": r.error}
                    for r in results[:-1]
                ]
                spoken = results[-1].output.strip() if results else ""
                if spoken:
                    return spoken, detail
            except Exception as exc:          # noqa: BLE001 — fall through to single call
                log.warning("hermes.deep_local_failed", error=str(exc))

        model = self.cfg.local_model_for(decision.intent)
        text = await self._local_generate(system, user_input, model)
        return text, [{"role": "local", "model": model}]

    # -- main entrypoint ----------------------------------------------------

    async def respond(
        self,
        user_input: str,
        *,
        confirm: ConfirmCallback | None = None,
    ) -> HermesReply:
        """Run one full turn. Never raises — errors degrade to a spoken-safe
        message and the local engine."""
        ctx = await self.memory.assemble(user_input)
        system = self._build_system(ctx)

        try:
            decision = await route(user_input, self.router_cfg)
        except Exception as exc:              # noqa: BLE001 — default local
            log.warning("hermes.route_failed", error=str(exc))
            decision = Decision("local", "simple", "router error -> local", False)

        # --- cloud path (confirm-gated) ------------------------------------
        if decision.engine == "cloud":
            ok, why = self.cloud.available()
            if not ok:
                log.info("hermes.cloud_unavailable", reason=why)
                decision = Decision("local", decision.intent, f"cloud unavailable: {why}", decision.online)
            else:
                approved = True
                if self.cfg.cloud_confirm:
                    approved = bool(confirm and await confirm(decision.reason))
                if approved:
                    try:
                        text = await self.cloud.generate(system, user_input)
                        await self._remember(user_input, text)
                        return HermesReply(
                            text=text or "The cloud model returned an empty answer.",
                            engine="cloud",
                            intent=decision.intent,
                            reason=decision.reason,
                            online=decision.online,
                            detail=[{"role": "cloud", "model": self.cloud.cfg.model}],
                        )
                    except Exception as exc:  # noqa: BLE001 — fall back to local
                        log.warning("hermes.cloud_failed", error=str(exc))
                        decision = Decision("local", decision.intent, f"cloud error -> local: {exc}", decision.online)
                else:
                    decision = Decision("local", decision.intent, "cloud declined -> local", decision.online)

        # --- local path (default) ------------------------------------------
        try:
            text, detail = await self._run_local(user_input, system, decision)
        except Exception as exc:              # noqa: BLE001 — never fatal
            log.exception("hermes.local_failed", error=str(exc))
            return HermesReply(
                text="Something went wrong thinking that through.",
                engine="local",
                intent=decision.intent,
                reason=f"local error: {exc}",
                online=decision.online,
                detail=[],
            )
        await self._remember(user_input, text)
        return HermesReply(
            text=text or "Done.",
            engine="local",
            intent=decision.intent,
            reason=decision.reason,
            online=decision.online,
            detail=detail,
        )

    async def _remember(self, user_input: str, reply: str) -> None:
        user_input, reply = user_input.strip(), reply.strip()
        if not reply:
            return
        # Don't churn the SSD or pollute memory with trivial exchanges
        # ("ok", "yes", "thanks"). Only persist turns with real substance.
        if len(user_input) + len(reply) < 40:
            return
        snippet = f"User asked: {user_input}\nAssistant: {reply}"
        try:
            await self.memory.remember(snippet[:2000], kind="turn")
        except Exception as exc:              # noqa: BLE001 — best-effort
            log.warning("hermes.remember_failed", error=str(exc))
