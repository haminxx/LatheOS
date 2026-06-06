"""`lathe-pilot` entrypoint — LatheOS Screen Pilot CLI.

Subcommands
  guide "<goal>"   run the full pilot loop (capture -> plan -> ground -> guide)
  probe            print capability scan (grim / vlm / grounding / cursor / eww)
  capture [out]    grab a screenshot (debug); prints the path
  point "<phrase>" capture + ground a phrase, print the pixel point + [POINT] tag
  move X Y         warp the cursor to absolute (X, Y) (debug; opt-in gated)
  card "<text>"    show a step card near a point (debug)
  health           one-line summary of local service availability

Config comes from the LATHEOS_PILOT_* / LATHEOS_* env written by
modules/screen-pilot.nix into /etc/latheos/pilot.env. Everything is local and
loopback-only; see docs/SCREEN_PILOT.md.
"""

from __future__ import annotations

import argparse
import sys

from .capture import capture_screen, grim_available
from .config import PilotConfig
from .cursor import CursorController
from .overlay import make_overlay
from .pilot import Pilot
from .tags import render_tag
from .vision_client import VisionClient


def _cmd_guide(args: argparse.Namespace) -> int:
    cfg = PilotConfig.from_env()
    # The flag lets a brave user opt a single run into clicking; the engine
    # still requires LATHEOS_PILOT_ALLOW_CLICK=1 AND a per-step confirmation.
    pilot = Pilot(cfg=cfg, interactive=not args.auto, want_click=args.allow_click)
    return pilot.guide(args.goal)


def _cmd_probe(args: argparse.Namespace) -> int:
    cfg = PilotConfig.from_env()
    caps = Pilot(cfg=cfg).probe()
    print("LatheOS Screen Pilot — capability scan")
    print(f"  enabled         : {cfg.enable}")
    print(f"  grim (capture)  : {caps.grim}")
    print(f"  vlm model       : {cfg.vlm_model or '(unset — pull e.g. llama3.2-vision)'}")
    print(f"  grounding (GPU) : {caps.vision}  ({cfg.vision_url})")
    print(f"  cursor backend  : {caps.cursor}  (ydotoold socket: {caps.cursor_socket})")
    print(f"  overlay (eww)   : {caps.overlay}")
    print(f"  allow_move      : {cfg.allow_move}")
    print(f"  allow_click     : {cfg.allow_click}")
    print(f"  summary         : {caps.summary()}")
    return 0


def _cmd_capture(args: argparse.Namespace) -> int:
    if not grim_available():
        print("lathe-pilot: grim not found on PATH", file=sys.stderr)
        return 1
    shot = capture_screen(args.output)
    if shot is None:
        print("lathe-pilot: capture failed (no Wayland session?)", file=sys.stderr)
        return 1
    print(shot)
    return 0


def _cmd_point(args: argparse.Namespace) -> int:
    cfg = PilotConfig.from_env()
    shot = args.image or capture_screen()
    if shot is None:
        print("lathe-pilot: could not obtain a screenshot", file=sys.stderr)
        return 1
    res = VisionClient(cfg.vision_url).point_at(shot, args.phrase)
    if not res.get("ok"):
        print(f"lathe-pilot: grounding failed — {res.get('error')}", file=sys.stderr)
        return 1
    p = res["point"]
    print(f"point: x={p['x']:.0f} y={p['y']:.0f}")
    print(render_tag(p["x"], p["y"], args.phrase))
    return 0


def _cmd_move(args: argparse.Namespace) -> int:
    cfg = PilotConfig.from_env()
    if not cfg.allow_move:
        print("lathe-pilot: movement disabled (LATHEOS_PILOT_ALLOW_MOVE=0)", file=sys.stderr)
        return 1
    r = CursorController(cfg.ydotool_socket).move_absolute(args.x, args.y)
    print(f"move: ok={r.ok} backend={r.backend} {r.detail}".rstrip())
    return 0 if r.ok else 1


def _cmd_card(args: argparse.Namespace) -> int:
    cfg = PilotConfig.from_env()
    overlay = make_overlay(cfg.overlay)
    if not overlay.available():
        print("lathe-pilot: eww overlay not available", file=sys.stderr)
        return 1
    ok = overlay.show(index_text="Preview", step_text=args.text, x=args.x, y=args.y, hint="")
    print(f"card: shown={ok}")
    if args.hold:
        import time

        time.sleep(args.hold)
        overlay.shutdown()
    return 0 if ok else 1


def _cmd_health(args: argparse.Namespace) -> int:
    cfg = PilotConfig.from_env()
    caps = Pilot(cfg=cfg).probe()
    print(caps.summary())
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lathe-pilot",
        description="LatheOS Screen Pilot — local on-screen guidance "
        "(capture -> plan -> ground -> guide). Fully local; see docs/SCREEN_PILOT.md.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("guide", help="Run the full pilot loop for a goal.")
    g.add_argument("goal", help="What the user wants to do, e.g. 'open the Wi-Fi settings'.")
    g.add_argument(
        "--auto",
        action="store_true",
        help="Non-interactive: dwell on each step instead of waiting for Enter.",
    )
    g.add_argument(
        "--allow-click",
        action="store_true",
        help="Permit confirmed clicks this run (still needs LATHEOS_PILOT_ALLOW_CLICK=1).",
    )
    g.set_defaults(func=_cmd_guide)

    pr = sub.add_parser("probe", help="Print a capability scan.")
    pr.set_defaults(func=_cmd_probe)

    c = sub.add_parser("capture", help="Grab a screenshot (debug).")
    c.add_argument("output", nargs="?", default=None, help="Restrict to a named output.")
    c.set_defaults(func=_cmd_capture)

    pt = sub.add_parser("point", help="Ground a phrase to a pixel point (debug).")
    pt.add_argument("phrase", help="What to point at, e.g. 'the settings gear'.")
    pt.add_argument("--image", default=None, help="Use this screenshot instead of capturing.")
    pt.set_defaults(func=_cmd_point)

    mv = sub.add_parser("move", help="Warp the cursor to absolute X Y (debug, gated).")
    mv.add_argument("x", type=int)
    mv.add_argument("y", type=int)
    mv.set_defaults(func=_cmd_move)

    cd = sub.add_parser("card", help="Show a step card near a point (debug).")
    cd.add_argument("text")
    cd.add_argument("--x", type=int, default=80)
    cd.add_argument("--y", type=int, default=80)
    cd.add_argument("--hold", type=float, default=0.0, help="Seconds to keep it on screen.")
    cd.set_defaults(func=_cmd_card)

    h = sub.add_parser("health", help="One-line local service availability summary.")
    h.set_defaults(func=_cmd_health)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
