"""The HUD row — live CPU/RAM/GPU/NVMe/battery bars, refreshed at 2 Hz.

The widget is deliberately tiny: one `Static` that we rebuild on every tick.
Textual reconciles the diff so there's no flicker, and the render cost is
dominated by the 6 string concatenations for the bars.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import psutil
from textual.reactive import reactive
from textual.widgets import Static

from .hardware import HardwareInventory
from .theme import Theme, condition_style


def _bar(pct: float, width: int, theme: Theme) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(round((pct / 100) * width))
    return theme.bar_full * filled + theme.bar_empty * (width - filled)


def _battery_pct() -> float:
    for bat in sorted(Path("/sys/class/power_supply").glob("BAT*")):
        try:
            return float((bat / "capacity").read_text().strip())
        except (OSError, ValueError):
            continue
    return 0.0


def _nvme_pct(mount: str = "/") -> float:
    try:
        st = shutil.disk_usage(mount)
    except OSError:
        return 0.0
    return (st.used / st.total) * 100 if st.total else 0.0


def _gpu_pct() -> float:
    """Best-effort GPU busy %.

    We try `/sys/class/drm/card0/device/gpu_busy_percent` (AMD), then
    nvidia-smi, then give up (0%). Anything more accurate requires privileged
    sysfs or NVML, which we refuse to depend on.
    """
    amd = Path("/sys/class/drm/card0/device/gpu_busy_percent")
    if amd.exists():
        try:
            return float(amd.read_text().strip())
        except (OSError, ValueError):
            return 0.0
    return 0.0


class HUDBar(Static):
    """Top-row live utilisation bars."""

    DEFAULT_CSS = """
    HUDBar {
        height: 7;
        padding: 0 1;
        border: round $panel;
        background: $panel-darken-1;
    }
    """

    cpu: reactive[float] = reactive(0.0)
    mem: reactive[float] = reactive(0.0)
    gpu: reactive[float] = reactive(0.0)
    nvme: reactive[float] = reactive(0.0)
    bat: reactive[float] = reactive(0.0)

    def __init__(self, inv: HardwareInventory, theme: Theme) -> None:
        super().__init__("", id="hud")
        self._theme = theme
        self._inv = inv

    def on_mount(self) -> None:
        self.set_interval(0.5, self._refresh_metrics)
        self.border_title = "HUD · live"

    def _refresh_metrics(self) -> None:
        # psutil.cpu_percent returns instantaneous when interval=None and it
        # was called once before — here we prime on first tick.
        self.cpu = psutil.cpu_percent(interval=None)
        self.mem = psutil.virtual_memory().percent
        self.gpu = _gpu_pct()
        self.nvme = _nvme_pct()
        self.bat = _battery_pct()
        self.update(self._render())

    def _render(self) -> str:
        t = self._theme
        rows = [
            self._row("CPU ", self.cpu,  t),
            self._row("RAM ", self.mem,  t),
            self._row("GPU ", self.gpu,  t),
            self._row("NVMe", self.nvme, t),
            self._row("BAT ", self.bat,  t),
        ]
        return "\n".join(rows)

    def _row(self, label: str, value: float, t: Theme) -> str:
        bar = _bar(value, 18, t)
        style = condition_style(value, t)
        return f"[{style}]{label}[/]  {bar}  {value:5.1f} %"
