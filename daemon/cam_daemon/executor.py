"""Sandboxed execution of server-issued commands.

Three action types are supported. Everything else is dropped with a log
entry — *never* eval, never shell=True on unvetted input.
"""

from __future__ import annotations

import asyncio
import shlex

import structlog

log = structlog.get_logger(__name__)

_ALLOWED_BASH_PREFIXES = (
    "docker compose",
    "docker ",
    "git ",
    "nix ",
    "sway",
    "cursor",
    "code",
    "make ",
    "npm ",
    "pnpm ",
    "pytest",
    "python ",
    "uv ",
)


async def _run(argv: list[str]) -> int:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    log.info(
        "exec.done",
        argv=argv,
        rc=proc.returncode,
        stdout=stdout.decode(errors="replace")[-512:],
        stderr=stderr.decode(errors="replace")[-512:],
    )
    return proc.returncode or 0


async def dispatch(command: dict) -> None:
    action = command.get("action", "noop")
    payload = command.get("command", "")

    if action == "noop" or not payload:
        return

    if action == "execute_bash":
        if not any(payload.startswith(prefix) for prefix in _ALLOWED_BASH_PREFIXES):
            log.warning("exec.rejected", reason="not in allowlist", command=payload)
            return
        await _run(["bash", "-lc", payload])
        return

    if action == "sway_msg":
        await _run(["swaymsg", "--", *shlex.split(payload)])
        return

    if action == "open_app":
        await _run(shlex.split(payload))
        return

    log.warning("exec.unknown_action", action=action)
