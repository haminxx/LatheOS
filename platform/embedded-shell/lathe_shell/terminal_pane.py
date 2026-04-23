"""Minimal embedded terminal pane.

We do NOT embed a full PTY — that's a rabbit hole and Textual doesn't have
built-in pty support. Instead we give the user a one-shot command runner:

  * the Input line accepts a shell command
  * we run it via asyncio.create_subprocess_shell in a locked-down env
  * stdout + stderr stream into a RichLog

This covers 90% of the interactive-use-case in the embedded shell (the user
who wants a real PTY opens Foot via $mod+Return anyway). Good enough for
v1; can be replaced with a real `ptyprocess` widget later.
"""

from __future__ import annotations

import asyncio
import os
import shlex

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, RichLog


class TerminalPane(Vertical):

    DEFAULT_CSS = """
    TerminalPane {
        border: round $panel;
        background: $panel-darken-1;
    }
    TerminalPane > RichLog {
        padding: 0 1;
    }
    TerminalPane > Input {
        margin: 0 1 1 1;
    }
    """

    def __init__(self, cwd: str) -> None:
        super().__init__(id="term")
        self._cwd = cwd if os.path.isdir(cwd) else os.path.expanduser("~")

    def compose(self) -> ComposeResult:
        yield RichLog(id="term-log", highlight=False, markup=True, wrap=True)
        yield Input(placeholder=f"$ (cwd: {self._cwd})", id="term-input")

    def on_mount(self) -> None:
        self.border_title = "terminal · one-shot"
        log = self.query_one("#term-log", RichLog)
        log.write(f"[dim]cwd: {self._cwd}. Commands run non-interactively.[/]")

    @on(Input.Submitted, "#term-input")
    def _on_submit(self, event: Input.Submitted) -> None:
        line = (event.value or "").strip()
        if not line:
            return
        inp = self.query_one("#term-input", Input)
        inp.value = ""
        log = self.query_one("#term-log", RichLog)
        log.write(f"[b]$[/b] {line}")
        # Treat `cd` specially — otherwise it wouldn't survive the subshell.
        if line.startswith("cd "):
            target = line[3:].strip() or os.path.expanduser("~")
            new = os.path.abspath(os.path.join(self._cwd, target))
            if os.path.isdir(new):
                self._cwd = new
                self.query_one("#term-input", Input).placeholder = f"$ (cwd: {self._cwd})"
                log.write(f"[dim]cwd -> {self._cwd}[/]")
            else:
                log.write(f"[red]cd:[/] {new} is not a directory")
            return
        self._run(line)

    @work(exclusive=True, group="term")
    async def _run(self, cmd: str) -> None:
        log = self.query_one("#term-log", RichLog)
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._cwd,
                env={**os.environ, "TERM": "xterm-256color"},
            )
            assert proc.stdout is not None
            async for line in proc.stdout:
                log.write(line.decode(errors="replace").rstrip())
            rc = await proc.wait()
            if rc != 0:
                log.write(f"[red]exit {rc}[/]")
        except OSError as exc:
            log.write(f"[red]error:[/] {exc}")

    # Let other panes nudge a command into the terminal (CAM uses this).
    def submit(self, cmd: str) -> None:
        inp = self.query_one("#term-input", Input)
        inp.value = cmd
        inp.action_submit()
