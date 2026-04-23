# LatheOS repository structure

```
LatheOS_Core_System/
├── docs/
│   ├── LATHEOS_VIBE_PLATFORM.md   # Portable USB + local-first AI architecture
│   └── REPO_STRUCTURE.md          # This file
├── flake.nix
├── configuration.nix              # Top-level imports only
├── modules/
│   ├── sway.nix                   # Monochrome Wayland desktop
│   ├── audio.nix                  # PipeWire low-latency capture
│   ├── cam-daemon.nix             # Local daemon (cloud proxy now opt-in)
│   ├── storage.nix                # ESP + ext4 + exFAT partitions
│   ├── home.nix                   # Home-Manager polish
│   ├── iso.nix                    # Installer ISO (legacy path)
│   ├── local-llm.nix              # NEW — Ollama + whisper + piper offline
│   ├── embedded-shell.nix         # NEW — in-OS Monaco+chat scaffold
│   ├── greeter.nix                # NEW — CAM login briefing (Jarvis-style)
│   └── vault.nix                  # NEW — age-encrypted secret vault
├── daemon/                        # cam_daemon, camctl
├── platform/                      # Future embedded editor GUI source
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
| Wake word + WS + local commands | `daemon/cam_daemon/`, `modules/cam-daemon.nix` |
| Local LLM / STT / TTS | `modules/local-llm.nix` |
| Embedded editor shell (scaffold) | `modules/embedded-shell.nix`, `platform/embedded-shell/` |
| VM mode on host OS | `launcher/{windows,linux,macos}/` |
| Pre-boot USB setup (Windows) | `installer/windows/Install-LatheOS.ps1` |
| Multi-agent orchestrator | `daemon/cam_daemon/agents.py` |

The **CAM Cloud Proxy** (AWS) lives in a separate repository and becomes an **optional** upgrade in this architecture; see root [`SETUP.md`](../SETUP.md).
