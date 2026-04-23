# Windows host launcher (Mode B fallback)

Double-click **`Launch-LatheOS.bat`** to boot the USB inside a window on Windows 10/11 (x64).

## One-time setup (only if `qemu\` is not already bundled)

The launcher expects a portable QEMU build at:

```
launcher/windows/qemu/qemu-system-x86_64.exe
```

If it is missing:

1. Download QEMU for Windows from <https://www.qemu.org/download/#windows> (GPL-licensed, free).
2. Extract the archive so that `qemu-system-x86_64.exe` sits exactly where the launcher expects.
3. Run `Launch-LatheOS.bat` again.

## Finding the USB disk number

PowerShell → run:

```powershell
Get-Disk | Format-Table Number,FriendlyName,Size,BusType
```

Pick the row whose size and name match your LatheOS stick and type that `Number` into the launcher prompt.

> **Warning:** Entering the wrong disk number can confuse the VM. The launcher only **reads/writes** the selected disk; it does not format it. Double-check before confirming.

## Troubleshooting

- **Launcher closes instantly** — open `cmd.exe`, `cd` into this folder, and run `Launch-LatheOS.bat` from there to see the error message.
- **WHPX not available** — Windows 10/11 Home needs *Windows Hypervisor Platform* enabled in *Turn Windows features on or off*. Without it, the VM will fall back to software emulation (very slow).
- **Audio drops** — expected in Mode B; the real voice experience lives in Mode A (boot the USB).
