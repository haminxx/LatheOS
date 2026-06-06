"""Clicky-style structured target tags: [POINT:x,y:label] (and :screenN).

Clicky (https://github.com/farzaa/clicky) has its model emit inline
`[POINT:x,y:label:screenN]` tags that a cursor overlay then flies to. We reuse
the SHAPE of that tag as our wire format between the pilot engine, the overlay,
and the daemon — but with one critical LatheOS twist:

  We do NOT trust an LLM's pixel guesses. The planning VLM proposes WHAT to
  point at (a grounding phrase / label); the pixel coordinates are resolved
  LOCALLY by LocateAnything-3B against the real screenshot, and only THEN
  baked into a [POINT:...] tag. So a tag in LatheOS always carries
  ground-truth-ish pixels from the grounding model, not a hallucinated guess.

Format
    [POINT:<x>,<y>:<label>]
    [POINT:<x>,<y>:<label>:screen<N>]   # optional output index, Clicky-style

`x`/`y` are integer pixel coordinates in the captured image's own coordinate
space. `label` is a short human string (no ']' allowed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# label is non-greedy and forbids ']' so the trailing bracket can't be eaten.
# The optional :screenN suffix mirrors Clicky's multi-display tags.
_TAG_RE = re.compile(
    r"\[POINT:\s*(-?\d+)\s*,\s*(-?\d+)\s*:\s*(?P<label>[^\]:]+?)"
    r"(?::\s*screen\s*(?P<screen>\d+)\s*)?\]",
    re.IGNORECASE,
)


@dataclass(slots=True)
class PointTag:
    x: int
    y: int
    label: str
    screen: int = 0

    def render(self) -> str:
        if self.screen:
            return f"[POINT:{self.x},{self.y}:{self.label}:screen{self.screen}]"
        return f"[POINT:{self.x},{self.y}:{self.label}]"


def render_tag(x: float, y: float, label: str, screen: int = 0) -> str:
    """Build a [POINT:...] tag from (possibly float) pixel coords."""
    label = (label or "target").replace("]", "").replace(":", " ").strip() or "target"
    return PointTag(int(round(x)), int(round(y)), label, screen).render()


def parse_tags(text: str) -> list[PointTag]:
    """Pull every [POINT:...] tag out of a blob of text (e.g. model output)."""
    out: list[PointTag] = []
    for m in _TAG_RE.finditer(text or ""):
        out.append(
            PointTag(
                x=int(m.group(1)),
                y=int(m.group(2)),
                label=m.group("label").strip(),
                screen=int(m.group("screen")) if m.group("screen") else 0,
            )
        )
    return out
