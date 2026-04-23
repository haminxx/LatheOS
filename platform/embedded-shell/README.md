# `lathe` — LatheOS embedded vibe-coding shell

The in-OS window the user sees when they log in to LatheOS: one Textual TUI
with five panes.

```
┌──────────────────────────── LatheOS · lathe 0.1 ────────────────────────────┐
│ ┌─ HUD ────────────────┐  ┌─ CAM ────────────────────────────────────────┐ │
│ │ CPU  ▓▓▓▓▓▓░░░  62 % │  │ CAM> Welcome back. Systems online; disk      │ │
│ │ RAM  ▓▓▓▓░░░░░  41 % │  │       78G free; battery 92%. 2 open todos.   │ │
│ │ GPU  ▓▓░░░░░░░  18 % │  │ you> open the nixos flake and check it       │ │
│ │ NVMe ░░░░░░░░░   6 % │  │ CAM> running `nix flake check --no-build` …  │ │
│ │ BAT  ▓▓▓▓▓▓▓▓░  92 % │  │                                              │ │
│ └──────────────────────┘  └──────────────────────────────────────────────┘ │
│ ┌─ components ─────────┐  ┌─ terminal ───────────────────────────────────┐ │
│ │  ┌─────┐             │  │ dev@latheos:/assets/projects$ _               │ │
│ │  │ CPU │  AMD Ryzen  │  │                                              │ │
│ │  │     │  7 7840HS   │  │                                              │ │
│ │  └─────┘  ok 64 °C   │  │                                              │ │
│ │  …                   │  │                                              │ │
│ └──────────────────────┘  └──────────────────────────────────────────────┘ │
└──────────────────────────── F1 help · F2 color ── F10 quit ────────────────┘
```

## Entrypoint

`lathe` — Textual app. Flags:

| Flag | Default | Effect |
|---|---|---|
| `--color` | off (monochrome) | Enable the full LatheOS accent palette |
| `--detail` | off | Render the detailed ASCII-3D component plates (heavier) |
| `--project PATH` | `/assets/projects` | Starting folder for the tree + terminal |
| `--llm URL` | `$LATHEOS_LLM_URL` | Override the local Ollama endpoint |

## Subcomponents

| Module | What it does |
|---|---|
| `lathe_shell.app` | Textual `App` subclass — wires the panes |
| `lathe_shell.hud` | Live CPU/RAM/GPU/NVMe/battery bars |
| `lathe_shell.hardware` | Brand/model/condition detection (lshw, lspci, dmidecode, `/sys`) |
| `lathe_shell.ascii_art` | ASCII art library for common component brands |
| `lathe_shell.chat` | Streaming chat against the voice model via the local Ollama |
| `lathe_shell.terminal_pane` | Embedded shell pane (pty via `textual.widgets.Log` + `asyncio.subprocess`) |
| `lathe_shell.llm` | Lightweight HTTP client for Ollama + the cam-daemon control socket |
| `lathe_shell.theme` | Monochrome vs. color palette |

## Running locally

```bash
cd platform/embedded-shell
python -m venv .venv && . .venv/bin/activate
pip install -e .
lathe --color --detail     # nice for dev
lathe                      # production monochrome
```

On LatheOS, the Nix module (`modules/embedded-shell.nix`) builds this into a
real binary; the Sway keybind `$mod+o` opens it.
