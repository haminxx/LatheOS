"""Lower "components" pane — the Jarvis-style ASCII schematic.

For each detected piece of hardware we render its ASCII plate + a health
indicator row. The `--detail` flag swaps in the larger plates.

This is a static widget (no reactive state beyond the initial scan) because
brand/model don't change between pollings. Only the HUD needs live numbers.
"""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import Static

from . import ascii_art
from .hardware import HardwareInventory
from .theme import Theme


class ComponentsPane(VerticalScroll):
    """Scrollable column of ASCII component plates."""

    DEFAULT_CSS = """
    ComponentsPane {
        border: round $panel;
        background: $panel-darken-1;
        padding: 0 1;
    }
    ComponentsPane > Static {
        padding: 1 0;
    }
    """

    def __init__(self, inv: HardwareInventory, theme: Theme, *, detail: bool) -> None:
        super().__init__(id="components")
        self._inv = inv
        self._theme = theme
        self._detail = detail

    def on_mount(self) -> None:
        self.border_title = "components · detected"
        for c in self._inv.components:
            plate = ascii_art.render(c, detail=self._detail)
            health_style = {"ok": self._theme.ok, "warn": self._theme.warn, "bad": self._theme.bad}[
                c.health
            ]
            indicator = f"[{health_style}]● {c.health.upper()}[/]"
            self.mount(Static(f"{plate}\n{indicator}\n"))
