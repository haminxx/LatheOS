# lathe-pilot — LatheOS Screen Pilot

Local, on-device on-screen guidance for the Sway/Wayland LatheOS desktop. When
you're lost ("how do I find X", "where do I click", "walk me through doing Y"),
the pilot screenshots the screen, asks the **local** Ollama vision model for an
ordered step plan, resolves each step's target to a pixel coordinate with the
**local** LocateAnything-3B grounding service, moves the cursor there (opt-in),
and shows a small floating card — one step at a time — while narrating with the
local TTS.

UX reference: [Clicky](https://github.com/farzaa/clicky). **Critical
difference:** Clicky is macOS + cloud; LatheOS Screen Pilot is Wayland/Sway and
**100% local / private** — nothing ever leaves the machine (loopback only, no
telemetry). Screenshots are 0600 tmpfiles handed straight to the local model
and deleted immediately.

This package has **zero third-party Python deps**: it talks to the loopback
services over stdlib `urllib` and shells out to `grim`, `ydotool`, `eww`, and
`piper` (all placed on PATH by `modules/screen-pilot.nix`). It is import-safe
on any box and degrades gracefully when a capability is missing.

## CLI

```bash
lathe-pilot probe                 # capability scan
lathe-pilot guide "open the Wi-Fi settings"
lathe-pilot guide "..." --auto    # non-interactive (daemon/keybind) dwell mode
lathe-pilot capture               # debug: screenshot -> path
lathe-pilot point "the settings gear"   # debug: phrase -> pixel + [POINT] tag
lathe-pilot move 800 450          # debug: warp cursor (gated)
lathe-pilot card "Step 1 of 3" --x 800 --y 450 --hold 3
```

See `../../docs/SCREEN_PILOT.md` for the full design, env vars, services, the
Sway keybind, the voice intent, and the on-device verification checklist.
