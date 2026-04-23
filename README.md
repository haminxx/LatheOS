# LatheOS Core System

Declarative, dual-arch NixOS image for the **vibe coding OS**. Boots from a
partitioned NVMe drive, renders a stark monochrome Sway desktop, and launches
the **CAM Local Daemon** that bridges "Hey CAM" / handclap activations to the
cloud proxy over a persistent WebSocket.

> **First time here?** Read [`SETUP.md`](./SETUP.md) for the zero-to-working
> runbook covering both this repo and the [CAM Cloud Proxy](https://github.com/haminxx/CAM-LatheOS-Agent-)
> side (AWS setup, vendor keys, ISO flash, hardware token provisioning,
> first-boot verification). The sections below are reference material for
> after you're up and running.

**LatheOS is NixOS.** The flake at `flake.nix` pins
[`nixpkgs/nixos-24.11`](https://github.com/NixOS/nixpkgs/tree/nixos-24.11) as its
only base — everything else is a layer on top of stock NixOS. The installer ISO
we produce imports the upstream `installation-cd-minimal.nix` module verbatim.
No fork, no custom kernel, no drift.

## Repo layout

```
flake.nix              # x86_64 + aarch64 targets share one config
configuration.nix      # System composition (imports only)
modules/
  sway.nix             # Wayland tiling + monochrome theme
  audio.nix            # PipeWire @ 64–1024 quantum for <10ms capture
  cam-daemon.nix       # Systemd unit + Nix-built daemon derivation
  storage.nix          # EXT4 root + exFAT /assets
  home.nix             # Home-Manager polish (zsh, foot, neovim)
  iso.nix              # Bootable installer ISO (layers on NixOS minimal CD)
daemon/
  cam_daemon/          # Wake-word + WS bridge + executor
  camctl/              # Local CLI for the daemon's control socket
scripts/
  fetch-nixos-base.sh  # Pull upstream NixOS ISO for dual-install workflow
  build-latheos-iso.sh # Build our custom installer ISO
  flash-usb.sh         # Flash an ISO to a USB block device safely
home/sway/config       # Per-user Sway overlay (optional)
```

## Install paths

### Path A — Custom LatheOS installer ISO (recommended)

You have two ways to get an ISO:

**A1. Download from CI (no Nix required).**
Every push to `main` builds the ISO on a Linux runner and uploads it as a
workflow artifact. Grab the latest green run from
[Actions → nix](https://github.com/haminxx/LatheOS/actions/workflows/nix.yml),
expand the `iso / build + upload` job, and download the
`latheos-installer-<sha>` artifact. Tagged releases (`v*`) additionally publish
the ISO as a permanent GitHub Release asset.

**A2. Build locally.**

```bash
# On any Linux box with Nix ≥ 2.18 (or WSL, or macOS + nix-darwin):
./scripts/build-latheos-iso.sh
# -> result-latheos-iso/iso/latheos-*.iso
```

Either way, flash the resulting ISO and boot:

```bash
# Flash to a USB (Linux):
./scripts/flash-usb.sh path/to/latheos-*.iso /dev/sdX

# Boot the target machine from USB, then inside the live session:
sudo /etc/latheos/install.sh
```

The live installer prompts for disk, hostname, and hardware token, partitions
the NVMe (ESP + ext4 `latheos` + exFAT `LATHE_ASSETS`), and hands off to
`nixos-install --flake .#latheos-x86_64`. Reboot when it finishes — you're on
LatheOS.

### Path B — Dual-install (stock NixOS first)

For bringup on unfamiliar hardware where you want to confirm NixOS boots
before layering anything custom.

```bash
./scripts/fetch-nixos-base.sh          # pulls nixos-minimal-*.iso to $XDG_CACHE_HOME/latheos/
./scripts/flash-usb.sh ~/.cache/latheos/nixos-minimal.iso /dev/sdX

# Boot stock NixOS, install as normal, then:
sudo nixos-generate-config --root /mnt
cd /mnt/etc/nixos
git clone https://github.com/haminxx/LatheOS.git latheos
sudo nixos-rebuild switch --flake /mnt/etc/nixos/latheos#latheos-x86_64
```

### Windows users (this repo was seeded from Windows)

Run the fetch script under WSL2 (`ubuntu` / `nixos`), or just `Invoke-WebRequest`
directly against the channel URL. An ISO is already cached at
`%LOCALAPPDATA%\latheos-cache\nixos-minimal-*.iso` on this machine — flash it
with [Rufus](https://rufus.ie) or [balenaEtcher](https://etcher.balena.io).

## Partition layout on the target NVMe

```
/dev/nvme0n1p1  vfat   513 MiB   LABEL=ESP              → /boot
/dev/nvme0n1p2  ext4   ~80 %     LABEL=latheos          → /
/dev/nvme0n1p3  exfat  remainder LABEL=LATHE_ASSETS     → /assets    (cross-OS)
```

The installer script writes this layout automatically. `/persist/secrets/`
survives `nixos-rebuild` and is where the hardware token + Picovoice key live.

## First-boot checklist

1. `systemctl status cam-daemon` → should reach `daemon.idle, waiting_for=wake_word|clap|control_socket`.
2. `camctl ping` → `{"ok": true, "pong": true}`.
3. `camctl activate --kind wake_word` → forces a synthetic activation (skips the mic, exercises the WS roundtrip).
4. Real mic test: say "Hey CAM". Expect the daemon log to emit `wake.fired`.

## CAM daemon sequence

```
boot → sway session → pipewire ready →
  cam-daemon.service starts →
    Activator.listen()  (Porcupine + aubio fan out from one mic stream)
    ControlSocket.start() (opens /run/cam-daemon/control.sock for camctl)
      on fire → CloudClient.connect() →
        duplex:  mic → WS (binary)        /  WS (JSON)  → Executor
                                          \  WS (bytes) → SpeakerSink
```

## Local dev commands

```bash
make help             # list all targets
make check            # nix flake check (evaluates, doesn't build)
make build-x86        # build the x86_64 system closure
make switch           # DESTRUCTIVE: switch current host to LatheOS
make lint-daemon      # ruff on daemon/ + camctl/
```

## Pushing to the LatheOS remote

Already configured:

```bash
git remote -v
# origin  https://github.com/haminxx/LatheOS.git (fetch)
# origin  https://github.com/haminxx/LatheOS.git (push)
```

`git push origin main` lands in the `haminxx/LatheOS` repo.

## Extended platform (portable drive, self-repair, embedded shell)

For the roadmap that covers **exFAT portability across OSes**, **Nix-backed self-repair**, and a **menu/voice-first embedded editor** (vs. launching full Cursor/VS Code first), see [`docs/LATHEOS_VIBE_PLATFORM.md`](docs/LATHEOS_VIBE_PLATFORM.md). Repository layout is in [`docs/REPO_STRUCTURE.md`](docs/REPO_STRUCTURE.md).

## Where we are right now

The one-pager status, Mermaid architecture, and blocker list are in
[`docs/PLAN.md`](docs/PLAN.md). Short version:

- `lathe` (the Jarvis-style ASCII embedded shell) is **built**, not a stub.
- Wake-word engine has **three backends** — openWakeWord (default, free),
  Porcupine (opt-in, needs Picovoice key), clap + `$mod+space` push-to-talk
  (always on).
- Offline model bake is **wired end-to-end**: `scripts/prefetch-models.sh`
  downloads Ollama + Piper + Whisper + openWakeWord weights, and
  `scripts/build-usb-image.sh` seeds them onto the exFAT partition so the
  USB works with no network on first boot.
- Installers for Windows / macOS / Linux all accept `--wake-backend` and
  only prompt for a Picovoice key if you pick Porcupine explicitly.
- The actual ISO/USB build must still run on a **Linux host** (WSL2 works)
  or the `release` GitHub workflow — Windows cannot format ext4 + exFAT
  loopbacks natively.

Local dev loop for the embedded shell:

```bash
make shell-dev          # runs `lathe --color` in a venv
```

Full offline USB bundle (requires Linux + root + ~10 GB disk):

```bash
make release            # prefetch + image + zip
```
