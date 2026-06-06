"""LatheOS Screen Pilot — local, on-device on-screen guidance.

The "Clicky for LatheOS": when the user is lost ("how do I find X", "where do
I click", "walk me through doing Y"), the pilot (1) screenshots the Wayland
session with grim, (2) asks the LOCAL Ollama vision model for an ordered step
plan + a grounding phrase for the current step, (3) resolves that phrase to a
pixel coordinate with the LOCAL LocateAnything-3B service, (4) moves the mouse
cursor there with ydotool (opt-in), and (5) shows a small floating step card
near the target — one step at a time — while narrating via the local TTS.

Reference UX: Clicky (https://github.com/farzaa/clicky). CRITICAL DIFFERENCE:
Clicky is macOS + cloud (Cloudflare Worker proxying Anthropic / AssemblyAI /
ElevenLabs). LatheOS is the OPPOSITE — Wayland/Sway and 100% LOCAL/PRIVATE.
Nothing here ever touches the network beyond loopback; screenshots are
tmpfiles that never leave the machine.

Everything is OPT-IN and degrades gracefully: no GPU / vision disabled / no
uinput / no overlay → the pilot falls back to describing the steps (text +
optional speech) without ever moving the cursor or crashing.

Heavy work is delegated to external local services; this package itself has
NO third-party deps and is import-safe everywhere.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
