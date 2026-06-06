# Screen Pilot — local, on-device on-screen guidance (opt-in)

LatheOS Screen Pilot is the **local "Clicky"**: when you're lost — *"how do I
find X"*, *"where do I click"*, *"walk me through doing Y"* — the assistant
**sees the screen**, **plans the step-by-step path**, **moves the mouse cursor**
to the right UI element, and shows a **small floating card near the cursor**
that tells you, one step at a time, what to click / where to go next. It then
narrates the step with the local voice.

UX reference: **Clicky** — <https://github.com/farzaa/clicky> (push-to-talk →
screenshot + transcript → model → `[POINT:x,y:label:screenN]` tags → a cursor
overlay flies to each element → TTS narration).

> **Critical difference.** Clicky is **macOS + cloud** (a Cloudflare Worker
> proxying Anthropic / AssemblyAI / ElevenLabs). LatheOS Screen Pilot is the
> **opposite**: **Wayland/Sway** and **100% local / private**. Nothing ever
> touches the network beyond loopback, there is no account, and there is no
> telemetry. Screenshots are `0600` tmpfiles handed straight to the local model
> and deleted immediately. We reuse Clicky only as a **UX reference**.

## How it works

```
user goal ("connect to wifi")
   │
   ├─ grim ............... screenshot the Sway/wlroots session (tmpfile, 0600)
   ├─ Ollama VLM ......... LATHEOS_VLM_MODEL builds an ordered step plan; each
   │                       step = imperative instruction + a grounding PHRASE
   │                       for its target element (NOT a pixel guess)
   │   for the current step:
   ├─ LocateAnything-3B .. resolve the phrase → pixel (x,y) on a fresh capture
   │                       (loopback 127.0.0.1:11435; modules/vision-grounding.nix)
   ├─ [POINT:x,y:label] .. Clicky-style tag, baked from the ground-truth pixel
   ├─ ydotool ............ (opt-in) warp the cursor to (x,y) via uinput
   ├─ eww ................ floating step card near the target (wlr-layer-shell)
   ├─ Piper / MisoTTS .... narrate the step
   └─ advance ............ on user confirmation (interactive) or a dwell timer
```

The LatheOS twist on Clicky: **we never trust the LLM's pixel guesses.** The
planning VLM proposes *what* to point at (a phrase/label); the *pixels* are
resolved locally by LocateAnything-3B against the real screenshot, then baked
into the `[POINT:...]` tag.

### Components reused

| Piece | Source in this repo |
|-------|---------------------|
| Grounding service `/gui` `/point` `/ground` `/health` (loopback `:11435`) | `modules/vision-grounding.nix`, `platform/vision-worker/` |
| VLM scene/plan via Ollama (`LATHEOS_VLM_MODEL`) | `daemon/cam_daemon/vision.py`, `modules/camera.nix` |
| Voice loop + allowlisted executor + intent routing | `daemon/cam_daemon/` |
| Tiered TTS (Piper default / MisoTTS opt-in) | `modules/tts.nix`, `daemon/cam_daemon/tts.py` |
| Event bus (HUD step strip) | `daemon/cam_daemon/bus.py` |

## Why uinput / ydotool (the hard part)

Wayland — unlike X11 — **deliberately forbids** one client from warping the
pointer or injecting clicks into another client (there is no XTEST). The
supported escape hatch is the **kernel `uinput` device**, driven by
[`ydotool`](https://github.com/ReimuNotMoe/ydotool) via its `ydotoold` daemon.
`modules/screen-pilot.nix` therefore:

- loads the `uinput` kernel module and adds a udev rule giving `/dev/uinput` to
  the `input` group (the `dev` user is already in `input`);
- runs **`ydotoold`** as `dev:input` with a socket at `/run/ydotoold/socket`
  (`YDOTOOL_SOCKET`, also exported into the Sway session).

Fallbacks: `wlrctl` (clicks only; its pointer move is relative-only so it can't
do absolute targeting) and `wtype` (keyboard only). The floating card uses
[`eww`](https://github.com/elkowar/eww) on the
[wlr-layer-shell](https://wayland.app/protocols/wlr-layer-shell-unstable-v1)
protocol.

## Safety model (conservative by design)

Mirrors the daemon executor's allowlisted philosophy:

- **Movement** (`LATHEOS_PILOT_ALLOW_MOVE`, default **1**) is non-destructive,
  so it's allowed once the feature is on. It still no-ops cleanly with no
  ydotoold/uinput.
- **Clicking** (`LATHEOS_PILOT_ALLOW_CLICK`, default **0**) is OFF, and even
  when enabled the engine **only clicks after an explicit per-step
  confirmation** in the interactive CLI. It **never auto-clicks** anything, and
  the voice/keybind (non-interactive) path **never clicks at all** — it moves +
  describes only.

## Enable it

1. **Pull a vision model** (for the step plan):

   ```bash
   ollama pull llama3.2-vision      # matches the modules/camera.nix default
   ```

2. **(For cursor targeting) enable grounding** — see
   `docs/VISION_GROUNDING.md` (GPU-only, opt-in, non-commercial license):

   ```nix
   latheos.vision.enable = true;
   ```

   Without it, the pilot still plans + narrates + shows cards, but can't
   resolve pixels, so it won't move the cursor (it tells you to find the
   element yourself).

3. **Turn on the pilot** (`configuration.nix` already imports the module):

   ```nix
   latheos.screenPilot.enable = true;     # default false
   # optional:
   latheos.screenPilot.allowMove  = true; # default true (non-destructive)
   latheos.screenPilot.allowClick = false;# default false (confirmed clicks only)
   latheos.screenPilot.overlay    = "eww";# eww | none
   latheos.screenPilot.speak      = true; # narrate steps
   latheos.screenPilot.vlmModel   = "llama3.2-vision";
   latheos.screenPilot.maxSteps   = 8;
   ```

   On an already-built image you can flip flags at runtime (no rebuild) via
   `/persist/secrets/pilot.env`:

   ```ini
   LATHEOS_PILOT_ENABLE=1
   LATHEOS_PILOT_ALLOW_CLICK=0
   ```

## Trigger it

- **Keybind:** `Mod+g` (Super+g) — pops a `wofi` prompt for your goal, then
  runs the interactive walkthrough in a terminal. Cancel the prompt and nothing
  runs.
- **Voice:** say *"walk me through…"*, *"guide me to…"*, *"how do I…"*,
  *"where do I click…"*, *"help me find…"*, *"show me how to…"*, *"take me
  to…"*. The cam-daemon routes these to the pilot (only when
  `LATHEOS_PILOT_ENABLE=1`) and runs it non-interactively (move + describe, no
  clicks).
- **CLI:**

  ```bash
  lathe-pilot probe                          # capability scan
  lathe-pilot guide "open the Wi-Fi settings"   # interactive (Enter = next)
  lathe-pilot guide "..." --auto             # non-interactive dwell mode
  lathe-pilot guide "..." --allow-click      # permit confirmed clicks this run
  lathe-pilot capture                        # debug: screenshot → path
  lathe-pilot point "the settings gear"      # debug: phrase → pixel + [POINT] tag
  lathe-pilot move 800 450                   # debug: warp cursor (gated)
  lathe-pilot card "Step 1 of 3" --x 800 --y 450 --hold 3
  ```

## Configuration reference

`/etc/latheos/pilot.env` (always written; per-drive overrides in
`/persist/secrets/pilot.env`):

| Var | Default | Meaning |
|-----|---------|---------|
| `LATHEOS_PILOT_ENABLE` | `0` | Master switch (set by the Nix option). |
| `LATHEOS_LLM_URL` | `http://127.0.0.1:11434` | Local Ollama (step planning). |
| `LATHEOS_VISION_URL` | `http://127.0.0.1:11435` | LocateAnything-3B grounding. |
| `LATHEOS_VLM_MODEL` | `llama3.2-vision` | Vision-capable Ollama model. |
| `LATHEOS_PILOT_ALLOW_MOVE` | `1` | Allow cursor movement. |
| `LATHEOS_PILOT_ALLOW_CLICK` | `0` | Allow confirmed clicks. |
| `LATHEOS_PILOT_OVERLAY` | `eww` | Step-card backend (`eww`/`none`). |
| `LATHEOS_PILOT_TTS` | `1` | Narrate steps. |
| `LATHEOS_PILOT_MAX_STEPS` | `8` | Plan length cap. |
| `LATHEOS_PILOT_STEP_PAUSE` | `6.0` | Dwell seconds per step in `--auto`. |
| `YDOTOOL_SOCKET` | `/run/ydotoold/socket` | uinput daemon socket. |

Ports: none opened. Everything is loopback (`:11434` Ollama, `:11435`
grounding, `:11436` MisoTTS) plus the local `ydotoold` Unix socket.

## Files

| File | Purpose |
|------|---------|
| `platform/screen-pilot/` | The `lathe-pilot` worker (zero third-party deps). |
| `modules/screen-pilot.nix` | Opt-in module: option, env file, `ydotoold` + uinput plumbing, eww/grim/ydotool closure, packaging. |
| `docs/SCREEN_PILOT.md` | This document. |

## Graceful degradation

Every stage fails soft — it never crashes the session or boot:

| Missing | Behaviour |
|---------|-----------|
| `grim` / no Wayland | Says it can't see the screen; no walkthrough. |
| VLM not pulled / Ollama down | Single describe-only step with the goal. |
| Grounding off / no GPU | Plans + narrates + cards, but no cursor target. |
| `ydotoold` / uinput unavailable | No cursor movement; describe + card only. |
| `eww` unavailable | No floating card; cursor + narration + HUD strip only. |
| TTS unavailable | Silent; card + cursor still work. |

## Needs on-device verification (Linux/Wayland + GPU)

The Windows host **cannot** build the OS or test Wayland input/overlay at
runtime. The following must be validated on real hardware in a Sway session:

1. **uinput synthetic input** — that `ydotoold` starts (udev rule + `uinput`
   module + `input` group), the socket is owned by `dev`, and
   `ydotool mousemove --absolute` actually warps the cursor under Sway.
2. **layer-shell overlay rendering** — that the generated `eww` config opens a
   `:stacking "overlay"` window, repositions per step via the `card_x/card_y`
   geometry vars (eww may need the close+open we already do), and is styled
   correctly. Confirm a `gtk-layer-shell` fallback isn't needed.
3. **LocateAnything grounding accuracy** — that real screenshots + the GUI
   prompts return sensible points for common targets (toolbar buttons, gears,
   menu items), and tune the `/gui` vs `/point` preference if needed.
4. **Daemon (voice) path Wayland access** — `cam-daemon` is a *system* service;
   confirm it inherits `WAYLAND_DISPLAY`/`XDG_RUNTIME_DIR` (or wire them) so the
   voice-triggered pilot can screenshot. The `Mod+g` keybind path always has
   the session env.
5. **Audio coexistence** — that the pilot's narration and the daemon's voice
   don't fight over the audio device when triggered by voice.
