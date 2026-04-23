"""Chat strip — streaming against the local voice model.

UI is a read-only RichLog + an Input box. Every user line is appended to
the log, then we stream model tokens back into the same log without
re-rendering existing history (Textual handles the diff).
"""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, RichLog

from .hardware import HardwareInventory
from .llm import LocalLLM


_SYSTEM_PROMPT = """You are CAM, the on-device assistant for LatheOS.
Guidelines:
- Short, precise answers. Max three sentences by default.
- Refuse destructive commands unless the user confirms with 'yes, do it'.
- You can propose shell commands; the user runs them in the terminal pane.
- Never fabricate hardware specs — only quote what is in HARDWARE below.
- No emojis, no hashtags.
"""


class ChatPane(Vertical):

    DEFAULT_CSS = """
    ChatPane {
        border: round $panel;
        background: $panel-darken-1;
    }
    ChatPane > RichLog {
        padding: 0 1;
    }
    ChatPane > Input {
        margin: 0 1 1 1;
    }
    """

    def __init__(self, llm: LocalLLM, inv: HardwareInventory) -> None:
        super().__init__(id="chat")
        self._llm = llm
        self._inv = inv

    def compose(self) -> ComposeResult:
        yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True)
        yield Input(placeholder="ask CAM — e.g. why is my fan loud?", id="chat-input")

    def on_mount(self) -> None:
        self.border_title = "CAM · voice model"
        log = self.query_one("#chat-log", RichLog)
        log.write("[dim]CAM is ready. Type a question and press Enter.[/]")

    def _hardware_brief(self) -> str:
        lines = ["HARDWARE:"]
        for c in self._inv.components:
            lines.append(f"- {c.kind}: {c.brand} {c.model} ({c.detail or '—'}; {c.health})")
        return "\n".join(lines)

    @on(Input.Submitted, "#chat-input")
    def _on_submit(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        if not text:
            return
        inp = self.query_one("#chat-input", Input)
        inp.value = ""
        log = self.query_one("#chat-log", RichLog)
        log.write(f"[b]you>[/b] {text}")
        self._ask(text)

    @work(exclusive=True, group="chat")
    async def _ask(self, prompt: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        if not await self._llm.health():
            log.write("[dim]CAM: local LLM is not ready. Check `systemctl status ollama`.[/]")
            return
        system = _SYSTEM_PROMPT + "\n" + self._hardware_brief()
        log.write("[b]CAM>[/b]")
        buffer: list[str] = []
        try:
            async for token in self._llm.chat(prompt, system=system):
                buffer.append(token)
                # Re-render the active line without scrolling more than needed.
                log.write("".join(buffer))
        except Exception as exc:  # noqa: BLE001 - user-facing, must never propagate
            log.write(f"[red]CAM error:[/] {exc}")
