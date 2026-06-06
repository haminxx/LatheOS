# LatheOS + CAM — end-to-end setup guide

This is the *zero-to-working* runbook for the full stack. LatheOS is
**privacy-first and fully local**: the entire CAM assistant runs 100%
on-device on a portable USB OS. No microphone audio, transcript, or prompt
ever leaves the machine — there is **no cloud component** to set up, no
accounts to create, and no token to provision.

If you follow every step top-to-bottom you will end with:

- a LatheOS installer ISO flashed to a USB,
- a machine that boots straight into a monochrome Sway desktop, and
- a fully-local voice loop: say the wake word, it transcribes on-device with
  whisper.cpp, answers with a local Ollama model, and speaks back through
  Piper — all offline, even with networking unplugged.

Everything lives in a single repository:

| Repository | Purpose | Runs on |
|---|---|---|
| [`haminxx/LatheOS`](https://github.com/haminxx/LatheOS) | Declarative OS, Sway UI, local voice daemon, installer | Your USB / NVMe |

There is nothing to wire to an external service. The assistant is self-contained.

---

## 0. What you need before you touch any code

You do **not** need any cloud accounts, API keys, or a domain. The default
stack is entirely offline.

Optional accounts (only if you want the matching optional feature):

| Service | Why | When you'd want it |
|---|---|---|
| **Picovoice** | Alternative "Porcupine" wake backend | Only if you prefer Porcupine over the default openWakeWord. <https://console.picovoice.ai/> (free for personal) |
| **Cursor** | `latheos-cursor-agent` programmatic coding | Only if you use the optional Cursor SDK CLI. <https://cursor.com/dashboard/integrations> |

Hardware / media you actually need:

- A USB stick — **≥ 32 GB** to boot, **≥ 64 GB** recommended once voice +
  heavy models are baked in, **≥ 128 GB** if you want a large coder model
  plus headroom for projects.
- A target machine with a microphone and speaker (and, optionally, a webcam
  for the camera-vision features).
- A GPU with ~24 GB VRAM is **only** required for the opt-in MisoTTS premium
  voice and the opt-in LocateAnything-3B vision grounding; everything else
  runs on CPU.

Tools on your laptop (install once):

```
git
# Plus, only if you build the ISO/USB image yourself:
nix >= 2.18   # on Linux or WSL2 (Windows cannot format ext4 + exFAT loopbacks natively)
```

If you just download a prebuilt ISO from CI, you need nothing but a USB
flasher (Rufus / balenaEtcher / `dd`).

---

## 1. Get the LatheOS installer ISO — 2 min (or 20 if you build locally)

**The easy path — download the CI-built ISO.** Every green push to `main`
on `haminxx/LatheOS` publishes the ISO as a workflow artifact. Tag pushes
(`v*`) publish it as a permanent GitHub Release. To grab the most recent:

1. Open <https://github.com/haminxx/LatheOS/actions/workflows/nix.yml>
2. Click the top green run.
3. Scroll to *Artifacts* → `latheos-installer-<sha>` → download.
4. Unzip to get `latheos-*.iso`.

**The local path** (Linux / WSL / macOS + nix):

```bash
git clone https://github.com/haminxx/LatheOS.git
cd LatheOS
./scripts/build-latheos-iso.sh
# -> result-latheos-iso/iso/latheos-*.iso
```

---

## 2. (Optional) Bake the offline models before first boot

The USB works with no network on first boot **if** the model weights are
already on the exFAT `/assets` partition. The prefetch script downloads them
on a machine that has internet, and `scripts/build-usb-image.sh` seeds them:

```bash
# Default bake: Ollama voice + heavy + Piper + Whisper + openWakeWord weights
./scripts/prefetch-models.sh

# Add the opt-in premium voice (MisoTTS 8B, GPU-only, ~30-40 GB download):
WITH_MISO=1 ./scripts/prefetch-models.sh

# Add the opt-in vision grounding (LocateAnything-3B, GPU-only, ~8 GB):
WITH_VISION=1 ./scripts/prefetch-models.sh
```

You can also skip this entirely and pull models after first boot with
`ollama pull` (see §6). Weights live on exFAT `/assets`, so they survive
`nixos-rebuild` and can be managed from Windows/macOS too.

---

## 3. Flash the ISO to a USB — 2 min

- **Linux**: `./scripts/flash-usb.sh path/to/latheos-*.iso /dev/sdX`
  (the script refuses to write to a mounted disk and uses `pv` for progress)
- **Windows**: [Rufus](https://rufus.ie) → *DD image mode* → pick the ISO,
  pick the USB, write.
- **macOS**: `diskutil list` → `sudo dd if=latheos.iso of=/dev/rdiskN bs=4m`.

Verify by booting once: the USB should drop you at a TTY with the message
`Welcome to the LatheOS live installer. Run: sudo /etc/latheos/install.sh`.

---

## 4. Install LatheOS onto the target machine — 10 min

Boot the target from the USB, log in as `nixos` (no password), and run:

```bash
sudo /etc/latheos/install.sh
```

It asks **two** questions:

1. **Target disk** — `/dev/nvme0n1` on most modern laptops. *This wipes it.*
2. **Hostname** — anything; `lathe-01` is fine.

There is no hardware token step. The installer then:

- Partitions the NVMe: 513 MiB ESP, ~90% ext4 (LABEL `latheos`), remainder
  exFAT (LABEL `LATHE_ASSETS` — this partition is cross-platform so you can
  plug the drive into macOS/Windows to move big files and models).
- Clones the LatheOS flake into `/etc/nixos/latheos`.
- Runs `nixos-install --flake .#latheos-x86_64`.

Reboot, remove the USB, and LatheOS comes up on tty1 with Sway.

> Prefer to keep everything on the stick? You can also run LatheOS directly
> off the USB (Mode A) or in a VM window on your host (Mode B) without
> touching an internal disk — see
> [`docs/LATHEOS_VIBE_PLATFORM.md`](docs/LATHEOS_VIBE_PLATFORM.md).

---

## 5. First-boot verification — 3 min

Everything below runs offline. You can literally unplug the network cable
and the full voice loop still works.

```bash
# 1. Daemon is up and idle:
systemctl status cam-daemon
# -> Active: active (running); journal: {"event":"daemon.idle", ...}

# 2. The local LLM is serving — Ollama on loopback with your models pulled:
ollama list
# -> you should see your voice + heavy models (e.g. llama3.2:3b, a coder model)

# 3. Control socket responds:
camctl ping
# -> {"ok": true, "pong": true}

# 4. Fully-local round trip. Say the wake word (openWakeWord default), OR
#    press F5 in the `lathe` shell for push-to-talk, then speak a short
#    request. Tail the log while you do it:
journalctl -fu cam-daemon
# expect, all on-device: wake.fired → stt.final ("...your words...") →
#         agents respond via local Ollama → tts spoken back through Piper.
#         Each turn is also appended to /run/cam-daemon/events.jsonl and
#         mirrored into the `lathe` chat strip.
```

If nothing transcribes, confirm the mic is the default PipeWire source and
that whisper + the wake model were baked or pulled. No network is involved at
any step — there is nothing remote to misconfigure.

### Pulling and switching models (no rebuild)

Models are user-swappable at runtime. Pull anything you like with Ollama,
then point a role at it:

```bash
# See what's pulled and which model each role currently uses:
lathe models list

# Pull a new model and assign it to a role (voice | heavy | vision):
ollama pull qwen2.5-coder:14b
lathe models set heavy qwen2.5-coder:14b

# Inspect a single role:
lathe models get voice

# Camera vision model:
ollama pull llama3.2-vision
lathe models set vision llama3.2-vision
```

`lathe models set` persists to `/persist/secrets/llm.env` (env keys
`LATHEOS_VOICE_MODEL`, `LATHEOS_HEAVY_MODEL`, `LATHEOS_VLM_MODEL`) with **no
rebuild**. Run `systemctl restart cam-daemon` to apply a new voice/heavy
model to the live voice loop.

### Optional: `latheos-cursor-agent` (Cursor TypeScript SDK)

The installed system includes **`latheos-cursor-agent`**, a CLI over Cursor's
`@cursor/sdk`. It does **not** replace the offline Ollama/CAM stack; it adds
programmatic coding against a repo when you have a
[Cursor API key](https://cursor.com/dashboard/integrations). Example:

```bash
export CURSOR_API_KEY="…"   # store via the age vault in production; see docs/LATHEOS_VIBE_PLATFORM.md §4.3.1
latheos-cursor-agent models
latheos-cursor-agent once "List top-level items in this flake" --cwd /etc/nixos/latheos
```

---

## 6. Optional features

### 6.1 Premium voice — MisoTTS (GPU-only)

Piper is the CPU default everywhere. If you have a ~24 GB-VRAM GPU and want a
richer, emotive English voice, enable **MisoTTS 8B**
([source](https://github.com/MisoLabsAI/MisoTTS)). It serves on loopback
`127.0.0.1:11436`, is English-only, and watermarks its output.

```nix
# configuration.nix
latheos.tts.miso.enable = true;
```

```bash
WITH_MISO=1 ./scripts/prefetch-models.sh   # bake the weights onto /assets
```

A boot-time `cam-tts-autoselect` promotes `miso` only when it's enabled, the
weights are present, and a ≥~24 GB GPU is detected — otherwise it stays on
Piper. Full setup and constraints are in
[`docs/VOICE_TTS.md`](docs/VOICE_TTS.md).

### 6.2 Camera vision

The daemon can see through a local webcam (capture via ffmpeg/v4l2 to a
tmpfile that never leaves the machine):

- **"What do you see / describe this"** routes to a vision-capable Ollama
  model you pull yourself: `ollama pull llama3.2-vision`, selectable via
  `lathe models set vision …` (`LATHEOS_VLM_MODEL`).
- **"Where is X / find the button"** routes to the opt-in
  **LocateAnything-3B** grounding service (NVIDIA non-commercial license,
  GPU-only). See [`docs/VISION_GROUNDING.md`](docs/VISION_GROUNDING.md).

### 6.3 Alternative wake backend — Porcupine

openWakeWord is the default and needs no key. If you'd rather use Picovoice
Porcupine, pass `--wake-backend porcupine` to the installer and provide a
Picovoice key; otherwise you never need one.

---

## Pipeline at a glance (all on-device)

```
     Microphone (PipeWire, 16 kHz mono)
            │
            ▼
┌─────────────────────────────────────────────┐
│  LatheOS cam-daemon   [systemd hardened unit] │
│                                               │
│  Activator  ── openWakeWord / clap / F5 PTT   │
│      │ activation                             │
│      ▼                                        │
│  stt.py     ── energy-VAD capture             │
│      │                                        │
│      ▼                                        │
│  stt.py     ── whisper.cpp speech-to-text     │
│      │ transcript                             │
│      ▼                                        │
│  agents.py  ── local Ollama multi-agent pool  │
│      │        (Dispatcher → Planner / Coder / │
│      │         Critic → Speaker)              │
│      ├──► tts.py ── Piper (or MisoTTS) ──► Speaker
│      ├──► executor.py ── optional allowlisted command
│      │                   (OFF unless LATHEOS_VOICE_EXEC=1)
│      └──► bus.py ── event bus (/run/cam-daemon/events.jsonl)
└─────────────────────────────────────────────┘
```

Nothing in this diagram reaches the network. The model weights live on the
exFAT `/assets` partition; the binaries are built by Nix and pinned with the
flake.

## Day-two ops

| Task | Command |
|---|---|
| List / switch models | `lathe models list` · `lathe models set <role> <model>` |
| Pull a new model | `ollama pull <model>` then `lathe models set <role> <model>` |
| Apply a new voice/heavy model to the live loop | `systemctl restart cam-daemon` |
| Update the OS on a drive | `sudo nixos-rebuild switch --flake github:haminxx/LatheOS#latheos-x86_64` |
| Tail daemon logs | `journalctl -fu cam-daemon` |
| Watch voice turns | `tail -f /run/cam-daemon/events.jsonl` |

## Troubleshooting

- **`wake.fired` never appears** → with the default openWakeWord backend,
  confirm the ONNX wake model is on `/assets` and the mic is the default
  PipeWire source. (If you opted into Porcupine, a missing Picovoice key
  drops the daemon to control-socket-only mode — `camctl activate` and F5
  push-to-talk still work.)
- **Nothing transcribes** → check the whisper model was baked/pulled and the
  capture device is correct; everything is local, so there's no remote
  endpoint to blame.
- **No spoken reply** → Piper voice file missing on `/assets`, or (if you
  enabled it) MisoTTS lacks a big-enough GPU and the autoselect correctly fell
  back to Piper. See [`docs/VOICE_TTS.md`](docs/VOICE_TTS.md).
- **CI red on LatheOS** → the `flake / per-config eval` job isolates each
  config; the failing step name tells you exactly which one broke. Stderr
  spills into the run's public Summary tab via `scripts/ci-eval.sh`.
