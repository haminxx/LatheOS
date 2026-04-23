"""`lathe` entrypoint. Parses flags, builds the Textual app, runs it.

Kept deliberately short so `modules/embedded-shell.nix` can wrap it with
`makeWrapper` and pin env vars without re-reading argv.
"""

from __future__ import annotations

import argparse
import os
import sys

from lathe_shell.app import LatheShellApp


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lathe",
        description="LatheOS embedded vibe-coding shell (Jarvis HUD + chat + terminal).",
    )
    parser.add_argument(
        "--color",
        action="store_true",
        help="Enable the accent colour palette (default is monochrome ASCII).",
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        help="Render the detailed ASCII-3D component plates (heavier, same accuracy).",
    )
    parser.add_argument(
        "--project",
        default=os.environ.get("LATHEOS_PROJECT_ROOT", "/assets/projects"),
        help="Project root the file tree + terminal open into.",
    )
    parser.add_argument(
        "--llm",
        default=os.environ.get("LATHEOS_LLM_URL", "http://127.0.0.1:11434"),
        help="Local Ollama URL. Leave default unless you're tunnelling somewhere.",
    )
    parser.add_argument(
        "--voice-model",
        default=os.environ.get("LATHEOS_VOICE_MODEL", "llama3.2:3b"),
        help="Ollama tag for the conversational model used by the chat strip.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Disable any network fallbacks (vendor lookup, etc.). Default on LatheOS.",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = _parse_argv(sys.argv[1:])
    app = LatheShellApp(
        color=args.color,
        detail=args.detail,
        project_root=args.project,
        llm_url=args.llm,
        voice_model=args.voice_model,
        offline=args.offline,
    )
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
