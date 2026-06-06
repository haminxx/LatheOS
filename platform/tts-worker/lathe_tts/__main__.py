"""`lathe-tts` entrypoint.

Subcommands
  serve     start the loopback HTTP API (used by latheos-tts.service)
  probe     pre-flight check (GPU + weights + code), print + exit
  health    GET /health against a running server
  say       POST /synthesize and write the returned WAV to a file

Config defaults come from the LATHEOS_MISO_* / LATHEOS_TTS_* env vars written
by modules/tts.nix into /etc/latheos/tts.env.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from .worker import DEFAULT_MAX_MS, DEFAULT_SPEAKER, MisoConfig, probe


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _cfg_from_env(args: argparse.Namespace) -> MisoConfig:
    return MisoConfig(
        model_path=args.model or _env("LATHEOS_MISO_MODEL", "/assets/models/miso"),
        code_dir=args.code_dir or _env("LATHEOS_MISO_CODE_DIR", _env("LATHEOS_MISO_MODEL", "/assets/models/miso")),
        device=args.device or _env("LATHEOS_MISO_DEVICE", "cuda"),
        speaker=int(_env("LATHEOS_MISO_SPEAKER", str(DEFAULT_SPEAKER))),
        max_audio_ms=int(_env("LATHEOS_MISO_MAX_MS", str(DEFAULT_MAX_MS))),
    )


def _base_url(args: argparse.Namespace) -> str:
    return (args.url or _env("LATHEOS_TTS_URL", "http://127.0.0.1:11436")).rstrip("/")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", help="TTS server base URL (default $LATHEOS_TTS_URL).")
    common.add_argument("--model", help="MisoTTS weights path (default $LATHEOS_MISO_MODEL).")
    common.add_argument("--code-dir", dest="code_dir", help="MisoTTS source dir (generator.py).")
    common.add_argument("--device", help="torch device (default $LATHEOS_MISO_DEVICE/cuda).")

    parser = argparse.ArgumentParser(
        prog="lathe-tts",
        description="LatheOS MisoTTS premium voice (opt-in, GPU-only). "
        "Piper remains the default — see docs/VOICE_TTS.md.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", parents=[common], help="Start the loopback HTTP API.")
    p_serve.add_argument("--host", default=_env("LATHEOS_TTS_HOST", "127.0.0.1"))
    p_serve.add_argument("--port", type=int, default=int(_env("LATHEOS_TTS_PORT", "11436")))
    p_serve.add_argument(
        "--no-preload",
        action="store_true",
        help="Do not load the model at startup (load on first request).",
    )

    sub.add_parser("probe", parents=[common], help="Check GPU + weights + code, then exit.")
    sub.add_parser("health", parents=[common], help="GET /health against a running server.")

    p_say = sub.add_parser("say", parents=[common], help="Synthesize text to a WAV file.")
    p_say.add_argument("text")
    p_say.add_argument("-o", "--output", default="miso.wav", help="Output WAV path.")

    args = parser.parse_args(argv)

    if args.cmd == "serve":
        from .server import serve
        from .worker import TTSUnavailable

        cfg = _cfg_from_env(args)
        try:
            serve(cfg, host=args.host, port=args.port, preload=not args.no_preload)
            return 0
        except TTSUnavailable as exc:
            print(f"lathe-tts: not starting — {exc}", file=sys.stderr)
            return 0

    if args.cmd == "probe":
        cfg = _cfg_from_env(args)
        ok, reason = probe(cfg)
        print(json.dumps({"ok": ok, "reason": reason, "model": cfg.model_path}, indent=2))
        return 0 if ok else 1

    base = _base_url(args)
    try:
        if args.cmd == "health":
            with urllib.request.urlopen(f"{base}/health", timeout=5.0) as resp:  # noqa: S310
                payload = json.loads(resp.read().decode("utf-8"))
            json.dump(payload, sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 0 if payload.get("ok") else 1
        if args.cmd == "say":
            data = json.dumps({"text": args.text}).encode("utf-8")
            req = urllib.request.Request(
                f"{base}/synthesize", data=data,
                headers={"content-type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=600.0) as resp:  # noqa: S310 — loopback
                wav = resp.read()
            with open(args.output, "wb") as fh:
                fh.write(wav)
            print(f"wrote {len(wav)} bytes -> {args.output}")
            return 0
    except urllib.error.URLError as exc:
        print(f"lathe-tts: cannot reach {base} — {exc}", file=sys.stderr)
        return 2

    parser.error(f"unhandled command {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
