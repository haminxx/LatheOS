"""`lathe-vision` entrypoint.

Subcommands
  serve         start the loopback HTTP API (used by latheos-vision.service)
  probe         pre-flight check (GPU + weights + ML stack), print + exit
  health        GET /health against a running server
  detect        POST /detect   (comma-separated categories)
  ground        POST /ground   (phrase grounding, multi by default)
  point         POST /point    (pointing — e.g. a GUI element)

The query subcommands use stdlib urllib so this package needs no HTTP client
dependency; they exist so a user (or the embedded shell) has a minimal CLI
without standing up Python. Config defaults come from the LATHEOS_VISION_*
env vars written by modules/vision-grounding.nix into /etc/latheos/vision.env.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

from .worker import DEFAULT_MAX_NEW_TOKENS, DEFAULT_MODE, WorkerConfig, probe


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _cfg_from_env(args: argparse.Namespace) -> WorkerConfig:
    return WorkerConfig(
        model_path=args.model or _env("LATHEOS_VISION_MODEL", "/assets/models/locateanything"),
        device=args.device or _env("LATHEOS_VISION_DEVICE", "cuda"),
        generation_mode=args.mode or _env("LATHEOS_VISION_MODE", DEFAULT_MODE),
        max_new_tokens=int(_env("LATHEOS_VISION_MAX_NEW_TOKENS", str(DEFAULT_MAX_NEW_TOKENS))),
    )


def _base_url(args: argparse.Namespace) -> str:
    return (args.url or _env("LATHEOS_VISION_URL", "http://127.0.0.1:11435")).rstrip("/")


def _image_spec(path: str) -> dict:
    """Read a local image file into a base64 spec the server understands."""
    with open(path, "rb") as fh:
        return {"b64": base64.b64encode(fh.read()).decode("ascii")}


def _post(url: str, body: dict, timeout: float = 600.0) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"content-type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — loopback
        return json.loads(resp.read().decode("utf-8"))


def _get(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — loopback
        return json.loads(resp.read().decode("utf-8"))


def _print(obj: dict) -> int:
    json.dump(obj, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if obj.get("ok", True) else 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    # Shared flags live on a parent parser added to every subcommand, so they
    # are written AFTER the subcommand (e.g. `lathe-vision probe --model X`).
    # They are intentionally not on the top-level parser: argparse subparser
    # defaults would otherwise clobber a value given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", help="Vision server base URL (default $LATHEOS_VISION_URL).")
    common.add_argument("--model", help="Local model path (default $LATHEOS_VISION_MODEL).")
    common.add_argument("--device", help="torch device (default $LATHEOS_VISION_DEVICE/cuda).")
    common.add_argument("--mode", help="generation mode: fast|slow|hybrid (default hybrid).")

    parser = argparse.ArgumentParser(
        prog="lathe-vision",
        description="LatheOS visual grounding (NVIDIA LocateAnything-3B). "
        "Opt-in, GPU-only, non-commercial license — see docs/VISION_GROUNDING.md.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", parents=[common], help="Start the loopback HTTP API.")
    p_serve.add_argument("--host", default=_env("LATHEOS_VISION_HOST", "127.0.0.1"))
    p_serve.add_argument("--port", type=int, default=int(_env("LATHEOS_VISION_PORT", "11435")))
    p_serve.add_argument(
        "--no-preload",
        action="store_true",
        help="Do not load the model at startup (load on first request).",
    )

    sub.add_parser("probe", parents=[common], help="Check GPU + weights + ML stack, then exit.")
    sub.add_parser("health", parents=[common], help="GET /health against a running server.")

    p_detect = sub.add_parser(
        "detect", parents=[common], help="Object detection from comma-separated categories."
    )
    p_detect.add_argument("image")
    p_detect.add_argument("categories", help="e.g. 'person,car,bicycle'")

    p_ground = sub.add_parser("ground", parents=[common], help="Phrase grounding (boxes).")
    p_ground.add_argument("image")
    p_ground.add_argument("query")
    p_ground.add_argument("--single", action="store_true", help="Expect a single instance.")

    p_point = sub.add_parser("point", parents=[common], help="Pointing — e.g. a GUI element.")
    p_point.add_argument("image")
    p_point.add_argument("query")

    args = parser.parse_args(argv)

    if args.cmd == "serve":
        from .server import serve
        from .worker import VisionUnavailable

        cfg = _cfg_from_env(args)
        try:
            serve(cfg, host=args.host, port=args.port, preload=not args.no_preload)
            return 0
        except VisionUnavailable as exc:
            # Graceful no-op: no GPU / no weights. systemd treats 0 as success
            # (SuccessExitStatus) so the box does not thrash trying to restart.
            print(f"lathe-vision: not starting — {exc}", file=sys.stderr)
            return 0

    if args.cmd == "probe":
        cfg = _cfg_from_env(args)
        ok, reason = probe(cfg)
        print(json.dumps({"ok": ok, "reason": reason, "model": cfg.model_path}, indent=2))
        return 0 if ok else 1

    base = _base_url(args)
    try:
        if args.cmd == "health":
            return _print(_get(f"{base}/health"))
        if args.cmd == "detect":
            return _print(_post(f"{base}/detect", {
                "image": _image_spec(args.image),
                "categories": args.categories,
            }))
        if args.cmd == "ground":
            return _print(_post(f"{base}/ground", {
                "image": _image_spec(args.image),
                "query": args.query,
                "single": args.single,
            }))
        if args.cmd == "point":
            return _print(_post(f"{base}/point", {
                "image": _image_spec(args.image),
                "query": args.query,
            }))
    except urllib.error.URLError as exc:
        print(f"lathe-vision: cannot reach {base} — {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"lathe-vision: {exc}", file=sys.stderr)
        return 2

    parser.error(f"unhandled command {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
