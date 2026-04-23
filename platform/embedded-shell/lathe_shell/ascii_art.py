"""ASCII-art plates for hardware components.

Each plate is a multi-line string rendered in the HUD. Two sizes per kind:

  * `compact` — 3-5 lines, fits in the top HUD strip.
  * `detailed` — 8-12 lines, fits the lower "components" pane when the
    user passes `--detail`.

Design rules
------------
  * **Pure ASCII** — never depends on a particular font or locale. This is
    a deliberate choice so the Linux console (which renders before any font
    is loaded) can show the same HUD the foot-terminal does.
  * **No brand-specific trademarks** — we use generic silhouettes and let
    the brand/model text do the naming, so redistribution is safe.
  * **Brand-neutral base + accent line** — the first + last rows are a
    small accent the theme can colour. Middle rows are the structural
    silhouette.
  * Every plate has a stable bounding box (width, height) so panes do not
    reflow when components change.
"""

from __future__ import annotations

from dataclasses import dataclass

from .hardware import Component


@dataclass(frozen=True, slots=True)
class Plate:
    kind: str
    width: int
    height: int
    art: str


# ---------------------------------------------------------------------------
# Compact plates (HUD strip)
# ---------------------------------------------------------------------------


_COMPACT: dict[str, Plate] = {
    "cpu": Plate("cpu", 15, 3, r"""
 +-----------+
 | [::::::]  |
 +-----------+
""".strip("\n")),

    "ram": Plate("ram", 15, 3, r"""
 +-----------+
 | |||||||||||
 +-----------+
""".strip("\n")),

    "gpu": Plate("gpu", 15, 3, r"""
 +---+-------+
 |fan| [::::]|
 +---+-------+
""".strip("\n")),

    "nvme": Plate("nvme", 15, 3, r"""
 +-----------+
 | NVMe M.2  |
 +--o--------+
""".strip("\n")),

    "battery": Plate("battery", 15, 3, r"""
 +----------+-+
 | cells []  |#
 +----------+-+
""".strip("\n")),

    "mb": Plate("mb", 15, 3, r"""
 +----+-+----+
 |::: | |::::|
 +----+-+----+
""".strip("\n")),

    "net": Plate("net", 15, 3, r"""
 +-----------+
 | ))) wifi  |
 +-----------+
""".strip("\n")),
}


# ---------------------------------------------------------------------------
# Detailed plates (ASCII-3D-ish). Multi-line. Meant to be read — more like
# exploded schematics than render frames.
# ---------------------------------------------------------------------------


_DETAILED: dict[str, Plate] = {
    "cpu": Plate("cpu", 22, 8, r"""
   .----------------.
  /                 /|
 +------------------+ |
 |  +------------+  | |
 |  |  DIE  [::] |  | |
 |  +------------+  | /
 |  . . . . . . .   |/
 +------------------+
""".strip("\n")),

    "ram": Plate("ram", 22, 8, r"""
   ___________________
  |||||||||||||||||||||
  |===================|
  | DDR SO-DIMM       |
  |===================|
  |  . . . . . . . .  |
  |___________________|
   ~~~~~~~~~~~~~~~~~~~
""".strip("\n")),

    "gpu": Plate("gpu", 22, 8, r"""
   _____________________
  |  ####    ##########|
  |  ####    # DIE    #|
  |  ####    ##########|
  |  ####    |||| |||| |
  |  fan     HDMI DP   |
  |_____________________|
         --|PCIe|--
""".strip("\n")),

    "nvme": Plate("nvme", 22, 8, r"""
   ___________________
  |  NAND  NAND  ctrl |
  |  [::]  [::]  [##] |
  | === === === === =o|
  |___________________|
     M.2  2280  PCIe
         <-keying->
""".strip("\n")),

    "battery": Plate("battery", 22, 8, r"""
    _________________+-+
   |                 | |
   |  [=][=][=][=]   | |
   |  cell cell cell | |
   |  [=][=][=][=]   | |
   |_________________| |
                     +-+
""".strip("\n")),

    "mb": Plate("mb", 22, 8, r"""
    _____________________
   | +---+  +----------+ |
   | |CPU|  |   RAM    | |
   | +---+  +----------+ |
   |  []  []  [PCIe]    |
   |  [NVMe]   [chipset]|
   |_____________________|
""".strip("\n")),

    "net": Plate("net", 22, 8, r"""
      .-.    .-.    .-.
     ( ( )  ( ( )  ( ( )
      '-'    '-'    '-'
    +-----------------+
    |    wifi/eth     |
    +-----------------+
""".strip("\n")),
}


# ---------------------------------------------------------------------------
# Brand accents — a small tag line that runs above the plate. Keeps the
# plate itself generic while still giving the user the "my parts" feeling.
# ---------------------------------------------------------------------------


def _brand_tag(comp: Component) -> str:
    brand = (comp.brand or "").lower()
    # Collapse very long OEM names into tight tags.
    for needle, tag in (
        ("nvidia", "NVIDIA"),
        ("amd",    "AMD   "),
        ("intel",  "INTEL "),
        ("apple",  "APPLE "),
        ("samsung","SAMSNG"),
        ("hynix",  "HYNIX "),
        ("micron", "MICRON"),
        ("kingston","KNGSTN"),
        ("corsair","CORSAR"),
        ("crucial","CRUCIL"),
        ("western","WDC   "),
        ("seagate","SEAGT "),
        ("sk hynix","SKHX  "),
    ):
        if needle in brand:
            return tag
    return (comp.brand[:6].upper()).ljust(6)


def render(comp: Component, *, detail: bool = False) -> str:
    """Return a multi-line ASCII block for `comp`.

    The text above the plate is brand + model truncated to a consistent
    width so HUD rows line up regardless of which part is inside.
    """
    registry = _DETAILED if detail else _COMPACT
    plate = registry.get(comp.kind)
    if plate is None:
        # Unknown kind (e.g. new thermometer widget). Fall back to a box.
        body = " +---+ \n | ? | \n +---+ "
    else:
        body = plate.art

    tag = _brand_tag(comp)
    title = f"[{tag}] {comp.model}"
    max_w = max(plate.width if plate else 9, len(title))
    title = title[:max_w]
    lines = [title, *body.splitlines()]
    if comp.detail:
        lines.append(comp.detail[:max_w])
    return "\n".join(lines)


def render_banner(host: dict[str, str]) -> str:
    """A welcome banner drawn once at app start."""
    hostname = host.get("hostname", "latheos")
    kernel = host.get("kernel", "?")
    return rf"""
  _           _   _          ___  ____
 | |    __ _| |_| |__   ___/ _ \/ ___|
 | |   / _` | __| '_ \ / _ \ | | \___ \
 | |__| (_| | |_| | | |  __/ |_| |___) |
 |_____\__,_|\__|_| |_|\___|\___/|____/

  host : {hostname}
  kern : {kernel}
  mode : offline-first · cloud optional
""".rstrip()
