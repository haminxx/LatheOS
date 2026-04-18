# LatheOS Core System

Declarative, dual-arch NixOS image for the **vibe coding OS**. Boots from a
partitioned NVMe drive, renders a stark monochrome Sway desktop, and launches
the **CAM Local Daemon** that bridges "Hey CAM" / handclap activations to the
cloud proxy over a persistent WebSocket.

## Layout

```
flake.nix              # x86_64 + aarch64 targets share one config
configuration.nix      # System composition (imports only)
modules/
  sway.nix             # Wayland tiling + monochrome theme
  audio.nix            # PipeWire @ 64–1024 quantum for <10ms capture
  cam-daemon.nix       # Systemd unit + Nix-built daemon derivation
  storage.nix          # EXT4 root + exFAT /assets
daemon/                # Python source for the local daemon (built by Nix)
home/sway/config       # User-space overlay (optional)
```

## Build & install

```bash
# From inside an existing NixOS host (or a Nix-installed macOS/Linux):
nix flake update
sudo nixos-rebuild switch --flake .#latheos-x86_64      # PC
sudo nixos-rebuild switch --flake .#latheos-aarch64     # Mac ARM via UTM
```

Partition the NVMe before first install:

```
/dev/nvme0n1p1  vfat   256M   LABEL=ESP              → /boot
/dev/nvme0n1p2  ext4   rest   LABEL=latheos          → /
/dev/nvme0n1p3  exfat  user   LABEL=LATHE_ASSETS     → /assets
```

## First boot checklist

1. Write `/persist/secrets/dev.hash` (from `mkpasswd -m sha-512`).
2. Drop the hardware token into `/persist/secrets/cam.env`:
   ```
   CAM_HARDWARE_TOKEN=<32-char HW fingerprint>
   PICOVOICE_ACCESS_KEY=<Picovoice console key>
   CAM_PROXY_URL=wss://your-alb.example.com/ws/cam
   ```
3. `systemctl status cam-daemon` → should reach **idle, waiting_for=wake_word|clap**.

## CAM daemon sequence

```
boot → sway session → pipewire ready →
  cam-daemon.service starts →
    Activator.listen()  (Porcupine + aubio fanning from one mic stream) →
      on fire → CloudClient.connect() →
        duplex:  mic → WS (binary)        /   WS (JSON)  → Executor
                                          \   WS (bytes) → SpeakerSink
```
