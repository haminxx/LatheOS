# LatheOS — plug-and-play vibe coding OS

Welcome. This archive turns any decent USB stick into a **portable vibe coding workstation**:

- Boots on its own (PC, Intel Mac, most laptops).
- Runs in a **window** on Windows / Linux / macOS when you can't reboot.
- Ships a **local AI** that fixes its own config if things break.
- Holds an **encrypted secret vault** for your API keys and dev secrets — visible on every OS, but only *decryptable* from LatheOS.
- Is entirely **offline-capable**. No cloud accounts required.

## Pick the installer for your host OS

> You need a USB stick ≥ **32 GB** (1 TB+ recommended). Everything on it will be erased.

| Your host OS | Run this | Result |
|---|---|---|
| **Windows 10 / 11** | Right-click `installer/windows/Install-LatheOS.ps1` → **Run with PowerShell** *as Administrator* | USB is flashed; launchers + first-run profile staged. |
| **Linux** | `sudo installer/linux/install-latheos.sh` | Same as above, via `dd`. |
| **macOS** | `sudo installer/macos/install-latheos.command` | Same, via `diskutil` + `dd`. |

All three installers support:

- `-Language en` / `--language en` (default) or `ko`
- `-PicovoiceKey …` / `--picovoice-key …` (optional; enables wake-word on first boot)

## What happens after the installer finishes

You get a USB with three partitions:

| Partition | Visible from host OS? | What's there |
|---|---|---|
| `ESP` (FAT32) | Yes | Bootloader. |
| `latheos` (ext4) | **Linux only** | NixOS + `/persist/secrets` (vault key, Picovoice key). |
| `LATHE_ASSETS` (exFAT) | **Yes, all OSes** | Your projects + models + encrypted vault + host launchers. |

Two ways to use it:

### Mode A — Boot the USB (primary)

Plug the USB into a computer, power on, pick the stick in the firmware boot menu. LatheOS starts on the host's native CPU/RAM/GPU. CAM (the voice agent) greets you with a status briefing.

### Mode B — Run it in a window on top of your current OS

You don't have to reboot. Open the stick in your file manager and launch the one for your host:

| Host | Launcher |
|---|---|
| Windows | `launcher/windows/Launch-LatheOS.bat` (portable QEMU bundled) |
| Linux | `launcher/linux/launch-latheos.sh` (uses system QEMU/KVM) |
| macOS | `launcher/macos/launch-latheos.command` (requires `brew install qemu` or UTM) |

Mode A and Mode B read/write the **same bytes** on the USB, so whatever you do in one mode is waiting for you in the other.

## First boot experience

1. LatheOS applies the language + timezone you picked in the installer.
2. It auto-generates an **age keypair**: private key stays on the Linux-only `latheos` partition; public key sits on `LATHE_ASSETS/vault/PUBLIC_KEY.txt` for you to share if you want.
3. CAM speaks a short **status briefing** in your language.
4. Models download in the background (≈ 2 GB voice + 5–15 GB coder depending on your machine's RAM). Until they arrive, CAM uses a templated fallback.

## The secret vault — why this USB is safer than a `.env`

```sh
vault set OPENAI_API_KEY          # prompts securely
vault set GITHUB_TOKEN ghp_xxxxx
vault mark-auto OPENAI_API_KEY    # auto-inject as env var in new shells
vault export                      # emit bash `export` lines (e.g. for CI)
```

- Values are AES-encrypted through [age](https://age-encryption.org/).
- The **private key never leaves the Linux partition**, so the host OS physically cannot read it when the stick is plugged in.
- You can move the stick between computers without exposing secrets.

## Self-repair with CAM

When something breaks, you can just **ask**:

> "Hey CAM, the Wi-Fi stopped working after the last update. Fix it."

CAM splits the request across a local multi-agent pool (planner / coder / critic / speaker, all running on the stick), proposes a NixOS diff, shows it to you, and only runs `nixos-rebuild` after you confirm. If a rebuild ever fails, NixOS keeps the previous generation in the boot menu — you can always boot back to "yesterday".

## Portable office convention

Drop your projects under:

```
/assets/projects/<your-project>/
```

That path is reachable from Windows / macOS / Linux as a normal exFAT folder, **and** from LatheOS as a regular Linux directory. Each project can have its own `.envrc` — combined with `vault unlock-env`, your secrets show up as environment variables automatically when you `cd` in.

## Troubleshooting quick links

- The voice never triggers → Picovoice key is missing. Paste into `/persist/secrets/cam.env`.
- Mode B is slow → use Mode A instead, or check that hardware virtualization is on in your BIOS.
- Model didn't pull → plug in, connect to Wi-Fi, then `systemctl start latheos-llm-bootstrap`.
- Forgot the vault password / lost the stick's ext4 key → secrets are unrecoverable by design. That's the point.

## Version info

The raw image is packed alongside this file as `latheos-usb.img`; its sha256 is in `latheos-usb.img.sha256`.
