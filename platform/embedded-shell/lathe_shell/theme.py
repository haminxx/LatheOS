"""Palette + bar glyphs.

Every widget asks `theme()` for a `Theme` instance instead of hard-coding
colours — that way `--color`/`--ascii` is a single boolean we toggle once at
startup and every pane follows.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Theme:
    # Foreground / background shades. These match modules/sway.nix so the
    # embedded shell feels like part of the same OS, not a bolted-on app.
    bg: str = "#0a0a0a"
    surface: str = "#141414"
    border: str = "#1f1f1f"
    text: str = "#e8e8e8"
    text_dim: str = "#8a8a8a"
    accent: str = "#f2f2f2"

    # Status indicators — stay monochrome in ASCII mode, pick up colour
    # only when the user opts in via `--color`.
    ok: str = "#e8e8e8"
    warn: str = "#8a8a8a"
    bad: str = "#5c5c5c"

    # Bar glyphs used by the HUD widget. Two presets: a plain ASCII one
    # (works on the linux console before any fonts are loaded) and a
    # Unicode-block variant (default inside Sway + foot).
    bar_full: str = "▓"
    bar_half: str = "▒"
    bar_empty: str = "░"


_MONOCHROME = Theme()

_COLOR = Theme(
    ok="#7bd88f",
    warn="#e6b450",
    bad="#e06c75",
    accent="#82aaff",
)

_ASCII_ONLY = Theme(
    bar_full="#",
    bar_half="=",
    bar_empty=".",
)


def theme(color: bool = False, ascii_only: bool = False) -> Theme:
    """Return the active palette for the current session."""
    if ascii_only:
        # ASCII-only always wins — monochrome + safe glyphs.
        return _ASCII_ONLY
    if color:
        return _COLOR
    return _MONOCHROME


def condition_style(pct: float, t: Theme) -> str:
    """Map a utilisation/health percentage to a palette colour."""
    if pct >= 85:
        return t.bad
    if pct >= 60:
        return t.warn
    return t.ok
