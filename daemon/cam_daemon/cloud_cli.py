"""``lathe-cloud`` — terminal entrypoint to Hermes with a [y/N] cloud gate.

LatheOS is local-first; this command lets you run a single request through the
*same* Hermes brain the voice loop uses, from a normal shell. The router decides
whether the task is cloud-worthy, and — exactly like the voice path — nothing
leaves the device unless you confirm at the ``[y/N]`` prompt.

Usage:
    lathe-cloud "refactor this module to remove the global state"
    lathe-cloud status      # show cloud config + whether a key is present
    lathe-cloud test        # tiny round-trip to verify the endpoint/key
    lathe-cloud --yes "..." # auto-confirm the cloud send (skip the prompt)
"""

from __future__ import annotations

import asyncio
import sys

from cam_daemon.cloud import CloudConfig, CloudEngine, resolve_api_key
from cam_daemon.hermes import Hermes


def _terminal_confirm(auto_yes: bool):
    async def _confirm(reason: str) -> bool:
        if auto_yes:
            print(f"[hermes] cloud route ({reason}) — auto-confirmed.", file=sys.stderr)
            return True
        try:
            ans = input(f"[hermes] This looks like deep work ({reason}).\n"
                        f"         Send to the cloud frontier model? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return False
        return ans in ("y", "yes")

    return _confirm


def _cmd_status() -> int:
    cfg = CloudConfig()
    has_key = bool(resolve_api_key(cfg))
    print("LatheOS cloud (Engine B) status")
    print(f"  enabled : {cfg.enabled}")
    print(f"  url     : {cfg.base_url}")
    print(f"  model   : {cfg.model}")
    print(f"  key name: {cfg.key_name}")
    print(f"  api key : {'present' if has_key else 'MISSING (store it in the vault)'}")
    ok, why = CloudEngine(cfg).available()
    print(f"  ready   : {ok}" + ("" if ok else f" ({why})"))
    return 0 if ok else 1


def _cmd_test() -> int:
    engine = CloudEngine()
    ok, why = engine.available()
    if not ok:
        print(f"cloud not ready: {why}", file=sys.stderr)
        return 1

    async def _run() -> str:
        return await engine.generate(
            "You are a connectivity probe. Reply with exactly: OK",
            "Say OK.",
        )

    try:
        out = asyncio.run(_run())
    except Exception as exc:                 # noqa: BLE001 — surface to user
        print(f"cloud test failed: {exc}", file=sys.stderr)
        return 1
    print(f"cloud responded: {out[:200]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if args[0] == "status":
        return _cmd_status()
    if args[0] == "test":
        return _cmd_test()

    auto_yes = False
    if args and args[0] in ("-y", "--yes"):
        auto_yes = True
        args = args[1:]

    prompt = " ".join(args).strip()
    if not prompt:
        print("usage: lathe-cloud [--yes] \"your request\" | status | test", file=sys.stderr)
        return 2

    hermes = Hermes()

    async def _run() -> None:
        reply = await hermes.respond(prompt, confirm=_terminal_confirm(auto_yes))
        tag = "CLOUD" if reply.engine == "cloud" else "local"
        print(f"\n[{tag}] {reply.text}\n")
        print(f"[hermes] engine={reply.engine} intent={reply.intent} — {reply.reason}",
              file=sys.stderr)

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
