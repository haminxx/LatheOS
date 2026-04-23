# LatheOS — Windows installer (first-time setup app)

This is the small tool a Windows user runs **before they have LatheOS**. It:

1. Downloads (or uses a cached) LatheOS USB image.
2. Asks which USB stick to use (with safety rails — refuses to touch the system disk, refuses disks outside 32 GB–2 TB).
3. Flashes the image.
4. Copies the host-side launchers onto the stick.
5. Writes a first-run profile (language, timezone, optional Picovoice key).

After it finishes, the user can either **reboot to the stick** (Mode A) or **double-click the launcher** inside the stick to run it in a window (Mode B).

## Run it

Open PowerShell **as Administrator** in this folder, then:

```powershell
# English defaults
.\Install-LatheOS.ps1

# Korean UI + voice
.\Install-LatheOS.ps1 -Language ko

# If your Picovoice approval is through, include the key up-front so the
# wake word works on first boot:
.\Install-LatheOS.ps1 -Language ko -PicovoiceKey 'YOUR_32_CHAR_KEY'
```

## What gets written where

| Location | What |
|----------|------|
| `%LOCALAPPDATA%\LatheOS\cache\` | Downloaded `latheos-usb.img.zip` + extracted `.img`. Delete to force a fresh download. |
| USB partition 1 (FAT32 / ESP) | Bootloader — do not touch. |
| USB partition 2 (ext4, LatheOS root) | Written by the flasher; LatheOS expands it on first boot. |
| USB partition 3 (exFAT `LATHE_ASSETS`) | `latheos\firstrun.json`, optional `latheos\secrets\cam.env`, and the `launcher\` folder. Visible from Windows/Mac/Linux. |

## Safety notes

- The script **requires admin**.
- It **refuses** to target a disk marked `IsBoot` or `IsSystem`.
- It **refuses** disks smaller than 32 GB or larger than 2 TB unless you pass `-Force` (default guardrail against flashing the wrong NAS / external SSD).
- You must type the word `ERASE` to confirm the chosen disk.

## Planned follow-ups

- WPF / WinUI wrapper so non-PowerShell users get a normal-looking window.
- Built-in model downloader (pre-populate `/assets/models/ollama` while the stick is still on Windows, so first boot is fully offline-ready).
- Linux (`.deb` / `.rpm`) and macOS (`.pkg`) equivalents of this installer.
