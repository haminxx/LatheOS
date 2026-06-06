# LatheOS repository structure

```
LatheOS_Core_System/
├── docs/
│   ├── LATHEOS_VIBE_PLATFORM.md   # Portable USB + fully-local AI architecture
│   ├── VOICE_TTS.md               # Tiered TTS — Piper default, MisoTTS opt-in
│   ├── VISION_GROUNDING.md        # Opt-in LocateAnything-3B grounding service
│   └── REPO_STRUCTURE.md          # This file
├── flake.nix
├── configuration.nix              # Top-level imports only
├── pkgs/
│   └── latheos-cursor-agent.nix   # buildNpmPackage → latheos-cursor-agent CLI
├── modules/
│   ├── sway.nix                   # Monochrome Wayland desktop
│   ├── audio.nix                  # PipeWire low-latency capture
│   ├── cam-daemon.nix             # Fully-local voice loop daemon (no cloud)
│   ├── storage.nix                # ESP + ext4 + exFAT partitions
│   ├── home.nix                   # Home-Manager polish
│   ├── iso.nix                    # Installer ISO (legacy path)
│   ├── local-llm.nix              # Ollama + whisper + RAM-aware model autoselect
│   ├── tts.nix                    # NEW — tiered TTS (Piper / MisoTTS autoselect)
│   ├── camera.nix                 # NEW — v4l2/ffmpeg capture for local vision
│   ├── vision-grounding.nix       # Opt-in LocateAnything-3B loopback service
│   ├── embedded-shell.nix         # NEW — in-OS Monaco+chat scaffold
│   ├── greeter.nix                # NEW — CAM login briefing (Jarvis-style)
│   ├── vault.nix                  # NEW — age-encrypted secret vault
│   └── cursor-sdk-bridge.nix      # Cursor @cursor/sdk CLI on PATH
├── daemon/
│   ├── cam_daemon/                # Local voice loop:
│   │   ├── wake.py                #   wake (openWakeWord / Porcupine / clap / PTT)
│   │   ├── stt.py                 #   NEW — energy-VAD capture + whisper.cpp STT
│   │   ├── agents.py              #   local Ollama multi-agent pool
│   │   ├── tts.py                 #   NEW — TTS router (Piper / MisoTTS)
│   │   ├── camera.py              #   NEW — local camera capture (ffmpeg/v4l2)
│   │   ├── vision.py              #   NEW — vision routing (Ollama VLM / grounding)
│   │   ├── bus.py                 #   NEW — voice-turn event bus (events.jsonl)
│   │   ├── executor.py            #   optional allowlisted command runner
│   │   └── control_socket.py      #   /run/cam-daemon/control.sock (camctl + F5)
│   └── camctl/                    # Local CLI for the daemon's control socket
├── platform/
│   ├── embedded-shell/            # Python TUI (lathe); lathe_shell/models.py
│   │                              #   (model CLI) + voicebus.py (voice mirror/PTT)
│   ├── tts-worker/                # NEW — MisoTTS loopback worker (127.0.0.1:11436)
│   └── cursor-programmatic/       # TypeScript CLI (latheos-cursor-agent)
├── scripts/
│   ├── build-latheos-iso.sh
│   ├── fetch-nixos-base.sh
│   ├── flash-usb.sh
│   └── build-usb-image.sh         # NEW — USB raw image + launcher bundle
├── launcher/                      # Mode B (VM-on-host) launchers
│   ├── README.md
│   ├── windows/Launch-LatheOS.bat
│   ├── linux/launch-latheos.sh
│   └── macos/launch-latheos.command
├── installer/                     # Pre-boot setup apps for each host
│   ├── windows/
│   │   ├── Install-LatheOS.ps1
│   │   └── README.md
│   ├── linux/install-latheos.sh
│   └── macos/install-latheos.command
├── .github/workflows/release.yml  # NEW — tag push → builds + publishes latheos-usb.zip
└── RELEASE_README.md              # NEW — user-facing README bundled in the release zip
```

### Concern mapping

| Concern | Primary location |
|---------|------------------|
| Bootable OS image | `flake.nix`, `modules/iso.nix` |
| USB raw disk image | `scripts/build-usb-image.sh` (planned) |
| Monochrome desktop | `modules/sway.nix` |
| Cross-platform disk partition | `modules/storage.nix` (`/assets` exFAT) |
| Local voice loop (wake → STT → LLM → TTS) | `daemon/cam_daemon/`, `modules/cam-daemon.nix` |
| Local LLM runtime | `modules/local-llm.nix` |
| Speech-to-text (energy-VAD + whisper.cpp) | `daemon/cam_daemon/stt.py` |
| Tiered TTS (Piper / MisoTTS) | `daemon/cam_daemon/tts.py`, `modules/tts.nix`, `platform/tts-worker/` |
| Camera vision + grounding | `daemon/cam_daemon/{camera,vision}.py`, `modules/camera.nix`, `modules/vision-grounding.nix` |
| User-swappable models | `lathe models` → `platform/embedded-shell/lathe_shell/models.py` |
| Embedded editor shell (scaffold) | `modules/embedded-shell.nix`, `platform/embedded-shell/` |
| Cursor SDK CLI (programmatic agents) | `modules/cursor-sdk-bridge.nix`, `platform/cursor-programmatic/`, `pkgs/latheos-cursor-agent.nix` |
| VM mode on host OS | `launcher/{windows,linux,macos}/` |
| Pre-boot USB setup (Windows) | `installer/windows/Install-LatheOS.ps1` |
| Multi-agent orchestrator | `daemon/cam_daemon/agents.py` |

There is **no cloud component**. The former `CAM_Cloud_Proxy/` project and the
daemon's WebSocket client (`daemon/cam_daemon/ws_client.py` and its
`tests/test_ws_client.py`) have been removed — the assistant runs entirely
on-device. See root [`SETUP.md`](../SETUP.md) for the fully-local runbook.
