"""Textual App — lays out the four panes.

 +---------------------- LatheOS · lathe ---------------------+
 |  HUD (live bars)            |        CAM (chat + streaming) |
 |                             |                               |
 +-----------------------------+-------------------------------+
 |  components (ASCII plates)  |        terminal (one-shot)    |
 |                             |                               |
 +-----------------------------+-------------------------------+
 |  F1 help · F2 color · F3 detail ·   F10 quit                |
 +-------------------------------------------------------------+
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid
from textual.widgets import Footer, Header, Static

from . import ascii_art
from .chat import ChatPane
from .components_pane import ComponentsPane
from .hardware import HardwareInventory
from .hud import HUDBar
from .llm import LLMConfig, LocalLLM
from .terminal_pane import TerminalPane
from .theme import theme as make_theme


class LatheShellApp(App[None]):
    TITLE = "LatheOS · lathe"
    CSS = """
    Screen {
        layout: vertical;
    }
    #grid {
        layout: grid;
        grid-size: 2 2;
        grid-columns: 1fr 2fr;
        grid-rows: 1fr 2fr;
        padding: 0 1;
    }
    #banner {
        height: auto;
        color: $accent;
        padding: 0 2;
    }
    """

    BINDINGS = [
        Binding("f1", "help",           "help"),
        Binding("f2", "toggle_color",   "color"),
        Binding("f3", "toggle_detail",  "detail"),
        Binding("f10", "quit",          "quit"),
        Binding("ctrl+c", "quit",       "quit", show=False),
    ]

    def __init__(
        self,
        *,
        color: bool = False,
        detail: bool = False,
        project_root: str = "/assets/projects",
        llm_url: str = "http://127.0.0.1:11434",
        voice_model: str = "llama3.2:3b",
        offline: bool = False,
    ) -> None:
        super().__init__()
        self._color = color
        self._detail = detail
        self._project = project_root
        self._offline = offline
        self._inv = HardwareInventory.scan()
        self._llm = LocalLLM(LLMConfig(url=llm_url, model=voice_model))
        self._theme_obj = make_theme(color=color)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(ascii_art.render_banner(self._inv.host), id="banner")
        with Grid(id="grid"):
            yield HUDBar(self._inv, self._theme_obj)
            yield ChatPane(self._llm, self._inv)
            yield ComponentsPane(self._inv, self._theme_obj, detail=self._detail)
            yield TerminalPane(self._project)
        yield Footer()

    async def on_unmount(self) -> None:
        await self._llm.close()

    # ------------------------------------------------------------------ actions
    def action_toggle_color(self) -> None:
        self._color = not self._color
        self.notify(f"color: {'on' if self._color else 'off'}")
        self._theme_obj = make_theme(color=self._color)
        # Force full recompose so the new theme propagates. Cheap — this is
        # a tiny widget tree.
        self.refresh(recompose=True)

    def action_toggle_detail(self) -> None:
        self._detail = not self._detail
        self.notify(f"detail: {'on' if self._detail else 'off'}")
        self.refresh(recompose=True)

    def action_help(self) -> None:
        self.notify(
            "LatheOS shell\n"
            "  F2  toggle color palette\n"
            "  F3  toggle detailed component plates\n"
            "  F10 quit\n"
            "Ask CAM in the right pane. Terminal runs one command at a time.",
            severity="information",
            timeout=8,
        )
