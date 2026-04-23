# LatheOS host-side launchers (Mode B — run in a window)

These launchers let someone open LatheOS **inside** their current OS, without rebooting. The USB is still the disk; the host just runs a virtual machine pointing at it.

> **Mode A (boot from USB) is still the recommended primary mode.** Use these launchers when you can't reboot, or as a fallback if your host can't boot from USB (e.g. work laptops with locked firmware, Apple Silicon Macs).

## What each folder will contain

```
launcher/
├── windows/   — Double-click Launch-LatheOS.bat (bundled portable QEMU).
├── linux/     — ./launch-latheos.sh (uses system QEMU/KVM).
└── macos/     — launch-latheos.command (requires UTM or QEMU installed).
```

## Requirements by host

| Host OS | Hypervisor | Shipped with USB? |
|---------|------------|--------------------|
| Windows 10/11 (x64) | QEMU for Windows | **Yes**, bundled as portable binaries under `windows/qemu/`. |
| Linux (x86_64) | System QEMU/KVM | No — install `qemu-system-x86_64` via your package manager. |
| macOS (Intel) | QEMU | No — `brew install qemu`. |
| macOS (Apple Silicon M1/M2/M3/M4) | **UTM** (free, Mac App Store) | No — UTM must be installed; the launcher hands UTM a pre-made config. |

Apple Silicon cannot legally run an x86-64 LatheOS image at native speed. The Apple Silicon launcher will target the **aarch64** LatheOS build instead.

## Usage (once the bundle is on the exFAT partition)

1. Plug the USB into the host.
2. Open the USB in the host's file manager.
3. Open `launcher/<your-os>/` and double-click the launcher (or run the script).
4. A LatheOS window opens. Your files on the exFAT partition are available inside LatheOS as `/assets` as usual.

## Security note

The VM is given **raw access to the USB device** so your edits persist. Unplug the USB safely (host-side "eject") before removing it.
