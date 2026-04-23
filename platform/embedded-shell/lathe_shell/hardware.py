"""Hardware enumeration.

Strategy
--------
We do NOT call out to heavy tools at UI refresh time. Instead:

  1. On app start (`HardwareInventory.scan()`) we ask the kernel for
     everything once: `/proc/cpuinfo`, `/sys/class/*`, `lspci -mm`, `lsusb`,
     `lsblk -J -O`, and `dmidecode` if available. That gives us a static
     inventory of brand/model strings.
  2. At runtime we only poll cheap counters — `psutil.cpu_percent`,
     `psutil.virtual_memory`, `/sys/class/power_supply/BAT*/capacity`,
     `/sys/class/thermal/...` — to refresh the HUD at ~2 Hz.

This split keeps the refresh path allocation-free and the inventory path
well-tested and offline. Nothing here reaches the network. Vendor lookup
(pretty names from PCI IDs) is served from the local `/usr/share/hwdata/`
database that every Linux ships.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Component:
    """A single piece of hardware we can render in the HUD."""

    kind: str                   # "cpu" | "ram" | "gpu" | "nvme" | "battery" | "mb" | "net"
    brand: str = "unknown"
    model: str = "unknown"
    detail: str = ""            # extra freeform line (size, speed, freq, …)
    health: str = "ok"          # "ok" | "warn" | "bad"
    metrics: dict[str, float] = field(default_factory=dict)   # runtime gauges


@dataclass(slots=True)
class HardwareInventory:
    components: list[Component] = field(default_factory=list)
    host: dict[str, str] = field(default_factory=dict)

    @classmethod
    def scan(cls) -> "HardwareInventory":
        inv = cls()
        inv.host = _host_info()
        inv.components.extend(_scan_cpu())
        inv.components.extend(_scan_memory())
        inv.components.extend(_scan_gpu())
        inv.components.extend(_scan_storage())
        inv.components.extend(_scan_battery())
        inv.components.extend(_scan_motherboard())
        inv.components.extend(_scan_network())
        return inv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read(path: str | Path, default: str = "") -> str:
    try:
        return Path(path).read_text(errors="replace").strip()
    except OSError:
        return default


def _run(argv: list[str], timeout: float = 2.0) -> str:
    """Capture the stdout of a helper command; return "" on any failure.

    We wrap every exec in a tight timeout because this code runs on every
    boot — a hung `lshw` should never block the login shell.
    """
    if not shutil.which(argv[0]):
        return ""
    try:
        out = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return out.stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _host_info() -> dict[str, str]:
    # `os.uname` is POSIX-only; `platform` is the portable fallback so this
    # module also imports on a Windows dev box (tests, lint, the occasional
    # sanity check during development).
    return {
        "hostname": _read("/etc/hostname") or platform.node(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "uptime_s": _read("/proc/uptime").split(" ", 1)[0] or "0",
    }


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------


def _scan_cpu() -> list[Component]:
    """Parse `/proc/cpuinfo` once to get vendor + model name."""
    cpuinfo = _read("/proc/cpuinfo")
    model = "unknown CPU"
    vendor = "unknown"
    cores = 0
    for line in cpuinfo.splitlines():
        if line.startswith("model name"):
            model = line.split(":", 1)[1].strip()
        elif line.startswith("vendor_id"):
            vendor = line.split(":", 1)[1].strip()
        elif line.startswith("processor"):
            cores += 1

    brand = _pretty_cpu_vendor(vendor, model)
    return [
        Component(
            kind="cpu",
            brand=brand,
            model=model,
            detail=f"{cores} threads · {_cpu_freq_mhz()} MHz",
        )
    ]


def _pretty_cpu_vendor(vendor: str, model: str) -> str:
    m = model.lower()
    if "intel" in m or vendor == "GenuineIntel":
        return "Intel"
    if "amd" in m or vendor == "AuthenticAMD":
        return "AMD"
    if "apple" in m or vendor == "Apple":
        return "Apple"
    if "qualcomm" in m:
        return "Qualcomm"
    return vendor or "CPU"


def _cpu_freq_mhz() -> int:
    # /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq is in kHz.
    raw = _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq", "0")
    try:
        return int(raw) // 1000
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Memory (SMBIOS via dmidecode for accurate brand/model, psutil for totals)
# ---------------------------------------------------------------------------


def _scan_memory() -> list[Component]:
    total_gb = 0.0
    try:
        meminfo = _read("/proc/meminfo")
        for line in meminfo.splitlines():
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                total_gb = round(kb / (1024 * 1024), 1)
                break
    except (ValueError, IndexError):
        pass

    # dmidecode gives us module-level detail; only root can read it. When we
    # can't, we still surface the total from psutil so the HUD is never empty.
    dmi = _run(["dmidecode", "--type", "memory"])
    modules = _parse_dmidecode_memory(dmi)

    if not modules:
        return [
            Component(kind="ram", brand="RAM", model=f"{total_gb} GB total", detail="")
        ]
    comps: list[Component] = []
    for mfr, part, size_mb, speed in modules:
        comps.append(
            Component(
                kind="ram",
                brand=mfr or "RAM",
                model=part or f"{size_mb} MB",
                detail=f"{size_mb // 1024} GB · {speed} MT/s" if speed else f"{size_mb} MB",
            )
        )
    return comps


_DMI_MEM_RE = {
    "Manufacturer": re.compile(r"^\s*Manufacturer:\s*(.+)$", re.MULTILINE),
    "Part Number":  re.compile(r"^\s*Part Number:\s*(.+)$",  re.MULTILINE),
    "Size":         re.compile(r"^\s*Size:\s*(\d+)\s*([GM])B", re.MULTILINE),
    "Speed":        re.compile(r"^\s*Configured Memory Speed:\s*(\d+)", re.MULTILINE),
}


def _parse_dmidecode_memory(text: str) -> list[tuple[str, str, int, int]]:
    if not text:
        return []
    blocks = re.split(r"\n(?=Handle )", text)
    out: list[tuple[str, str, int, int]] = []
    for b in blocks:
        if "Memory Device" not in b or "Size: No Module" in b:
            continue
        mfr  = _first(_DMI_MEM_RE["Manufacturer"],  b)
        part = _first(_DMI_MEM_RE["Part Number"],   b)
        size_match = _DMI_MEM_RE["Size"].search(b)
        speed_match = _DMI_MEM_RE["Speed"].search(b)
        size_mb = 0
        if size_match:
            n, unit = int(size_match.group(1)), size_match.group(2)
            size_mb = n * 1024 if unit == "G" else n
        speed = int(speed_match.group(1)) if speed_match else 0
        if size_mb:
            out.append((mfr or "", part or "", size_mb, speed))
    return out


def _first(rx: re.Pattern[str], s: str) -> str:
    m = rx.search(s)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# GPU (lspci)
# ---------------------------------------------------------------------------


_GPU_CLASS = {"0300", "0302", "0380"}   # VGA, 3D, Display


def _scan_gpu() -> list[Component]:
    raw = _run(["lspci", "-mm", "-nn"])
    gpus: list[Component] = []
    for line in raw.splitlines():
        parts = _split_lspci(line)
        if len(parts) < 3:
            continue
        # class [xxxx], vendor, device
        cls_match = re.search(r"\[([0-9a-f]{4})\]", parts[1])
        if not cls_match or cls_match.group(1) not in _GPU_CLASS:
            continue
        vendor = _strip_brackets(parts[2])
        device = _strip_brackets(parts[3]) if len(parts) > 3 else "GPU"
        gpus.append(
            Component(
                kind="gpu",
                brand=_pretty_gpu_vendor(vendor),
                model=device,
                detail=vendor,
            )
        )
    if not gpus:
        # Fallback: some headless servers / VMs only expose a framebuffer via
        # /sys/class/drm — still better to show "virtio-gpu" than blank.
        for drm in sorted(Path("/sys/class/drm").glob("card[0-9]")):
            name = _read(drm / "device/uevent")
            if "DRIVER=" in name:
                drv = next(
                    (l.split("=", 1)[1] for l in name.splitlines() if l.startswith("DRIVER=")),
                    "drm",
                )
                gpus.append(Component(kind="gpu", brand="GPU", model=drv))
                break
    return gpus


def _pretty_gpu_vendor(vendor: str) -> str:
    v = vendor.lower()
    if "nvidia" in v:
        return "NVIDIA"
    if "amd" in v or "advanced micro" in v or "ati" in v:
        return "AMD"
    if "intel" in v:
        return "Intel"
    if "apple" in v:
        return "Apple"
    return vendor.split()[0] if vendor else "GPU"


def _split_lspci(line: str) -> list[str]:
    # lspci -mm quotes fields with spaces. shlex is overkill; simple split works.
    out, buf, in_q = [], [], False
    for c in line:
        if c == '"':
            in_q = not in_q
            if not in_q:
                out.append("".join(buf))
                buf = []
        elif c == " " and not in_q:
            if buf:
                out.append("".join(buf))
                buf = []
        else:
            buf.append(c)
    if buf:
        out.append("".join(buf))
    return out


def _strip_brackets(s: str) -> str:
    return re.sub(r"\s*\[[0-9a-f:]+\]\s*$", "", s).strip()


# ---------------------------------------------------------------------------
# Storage — prefer NVMe model strings from /sys.
# ---------------------------------------------------------------------------


def _scan_storage() -> list[Component]:
    out: list[Component] = []
    for block in sorted(Path("/sys/block").glob("nvme*n*")):
        model = _read(block / "device/model") or "NVMe SSD"
        brand = model.split()[0] if model != "NVMe SSD" else "NVMe"
        size_sectors = _read(block / "size", "0")
        try:
            size_gb = int(size_sectors) * 512 // (1024**3)
        except ValueError:
            size_gb = 0
        out.append(
            Component(
                kind="nvme",
                brand=brand,
                model=model,
                detail=f"{size_gb} GB" if size_gb else "",
            )
        )
    if not out:
        # Fall back to the first SATA/USB block device we can see.
        for block in sorted(Path("/sys/block").glob("sd[a-z]")):
            model = _read(block / "device/model") or "disk"
            out.append(Component(kind="nvme", brand="disk", model=model))
            break
    return out


# ---------------------------------------------------------------------------
# Battery — reads /sys/class/power_supply synchronously; refreshed at 2 Hz.
# ---------------------------------------------------------------------------


def _scan_battery() -> list[Component]:
    base = Path("/sys/class/power_supply")
    if not base.exists():
        return []
    out: list[Component] = []
    for bat in sorted(base.glob("BAT*")):
        mfr = _read(bat / "manufacturer") or "battery"
        model = _read(bat / "model_name") or bat.name
        tech = _read(bat / "technology") or ""
        capacity = _read(bat / "capacity") or "0"
        design = _read(bat / "charge_full_design") or _read(bat / "energy_full_design")
        full   = _read(bat / "charge_full")        or _read(bat / "energy_full")
        health = "ok"
        if design and full:
            try:
                ratio = int(full) / int(design)
                if ratio < 0.7:
                    health = "bad"
                elif ratio < 0.85:
                    health = "warn"
            except (ValueError, ZeroDivisionError):
                pass
        out.append(
            Component(
                kind="battery",
                brand=mfr,
                model=model,
                detail=f"{tech} · {capacity}%",
                health=health,
                metrics={"capacity_pct": float(capacity or 0.0)},
            )
        )
    return out


# ---------------------------------------------------------------------------
# Motherboard (SMBIOS)
# ---------------------------------------------------------------------------


def _scan_motherboard() -> list[Component]:
    dmi = _run(["dmidecode", "--type", "baseboard"])
    mfr = _first(re.compile(r"^\s*Manufacturer:\s*(.+)$", re.MULTILINE), dmi)
    prod = _first(re.compile(r"^\s*Product Name:\s*(.+)$", re.MULTILINE), dmi)
    if not (mfr or prod):
        # Fallback to /sys (no root required).
        mfr = _read("/sys/class/dmi/id/board_vendor")
        prod = _read("/sys/class/dmi/id/board_name")
    if not (mfr or prod):
        return []
    return [Component(kind="mb", brand=mfr or "mainboard", model=prod or "")]


# ---------------------------------------------------------------------------
# Network (first active link only — we render it as a status line, not HUD)
# ---------------------------------------------------------------------------


def _scan_network() -> list[Component]:
    base = Path("/sys/class/net")
    if not base.exists():
        return []
    for iface in sorted(base.iterdir()):
        if iface.name == "lo":
            continue
        operstate = _read(iface / "operstate")
        if operstate != "up":
            continue
        speed = _read(iface / "speed") or ""
        return [
            Component(
                kind="net",
                brand="network",
                model=iface.name,
                detail=f"{speed} Mb/s" if speed.isdigit() else operstate,
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Pretty print helpers used by the JSON --status dump (useful for cam-daemon).
# ---------------------------------------------------------------------------


def inventory_as_json(inv: HardwareInventory) -> str:
    return json.dumps(
        {
            "host": inv.host,
            "components": [c.__dict__ for c in inv.components],
        },
        indent=2,
    )
