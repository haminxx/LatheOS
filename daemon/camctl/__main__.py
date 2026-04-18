"""camctl entrypoint.

Usage:
    camctl activate [--kind wake_word|clap] [--confidence 1.0]
    camctl status
    camctl ping

Talks to the cam-daemon's Unix domain socket (default /run/cam-daemon/control.sock,
overridable with --sock or CAM_CONTROL_SOCKET). Zero runtime deps beyond the
stdlib — keeps the binary trivial to ship onto a constrained device.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from typing import Any

DEFAULT_SOCK = "/run/cam-daemon/control.sock"


def _send(
    sock_path: str, payload: dict[str, Any], timeout: float = 2.0
) -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(sock_path)
        s.sendall((json.dumps(payload) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    line = buf.split(b"\n", 1)[0].decode().strip()
    if not line:
        return {"ok": False, "error": "empty_response"}
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"bad_reply: {exc}", "raw": line}


def _resolve_sock(cli: str | None) -> str:
    return cli or os.environ.get("CAM_CONTROL_SOCKET", DEFAULT_SOCK)


def cmd_activate(args: argparse.Namespace) -> int:
    reply = _send(
        _resolve_sock(args.sock),
        {"cmd": "activate", "kind": args.kind, "confidence": args.confidence},
    )
    print(json.dumps(reply, indent=2))
    return 0 if reply.get("ok") else 1


def cmd_status(args: argparse.Namespace) -> int:
    reply = _send(_resolve_sock(args.sock), {"cmd": "status"})
    print(json.dumps(reply, indent=2))
    return 0 if reply.get("ok") else 1


def cmd_ping(args: argparse.Namespace) -> int:
    reply = _send(_resolve_sock(args.sock), {"cmd": "ping"})
    print(json.dumps(reply, indent=2))
    return 0 if reply.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="camctl", description="Poke the local cam-daemon.")
    p.add_argument(
        "--sock", default=None, help=f"Unix socket path (default: {DEFAULT_SOCK})"
    )
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("activate", help="Inject a synthetic activation event.")
    a.add_argument("--kind", choices=["wake_word", "clap"], default="wake_word")
    a.add_argument("--confidence", type=float, default=1.0)
    a.set_defaults(func=cmd_activate)

    s = sub.add_parser("status", help="Fetch the daemon's current phase.")
    s.set_defaults(func=cmd_status)

    pg = sub.add_parser("ping", help="Verify the socket is alive.")
    pg.set_defaults(func=cmd_ping)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError:
        sock = _resolve_sock(args.sock)
        print(
            f"camctl: socket not found at {sock} — is cam-daemon running?",
            file=sys.stderr,
        )
        return 2
    except (ConnectionRefusedError, PermissionError) as exc:
        print(f"camctl: {exc}", file=sys.stderr)
        return 2
    except TimeoutError:
        print("camctl: timed out waiting for daemon reply", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
