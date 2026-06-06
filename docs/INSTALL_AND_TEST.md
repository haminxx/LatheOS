# LatheOS — Install & Test (SSD edition)

This is the practical guide to building the encrypted LatheOS image, putting it
on an SSD (e.g. a Samsung T7), and testing it **safely**.

> **Read this first — the safe way to test.** You do **not** need to risk your
> PC. The recommended first test runs LatheOS **in a window** (a virtual
> machine) on your normal Windows/macOS desktop. It cannot touch or change your
> computer. Only move to "real boot" once you've seen it work in the window.

---

## What's on the drive (v1 spec)

- **Encrypted OS + private documents** behind a password you type at startup
  (LUKS on the root partition). A lost stick is unreadable.
- **A small unencrypted shared folder** (`/assets`, exFAT) so you can drag files
  to/from Windows/macOS. Never put secrets there.
- **AI models baked in** → works offline the moment it starts (tuned for a
  16 GB laptop: 3B voice + 8B heavy, auto-upgrades on bigger machines).
- **Local-first AI**, with an opt-in cloud booster: `lathe-ai` (Cursor by
  default; Claude / opencode / custom if you add them).
- **A beginner setup wizard**, `lathe-setup`, that runs you through securing the
  drive on first boot.

---

## 0. The one hard requirement: you build the image on Linux

Creating an encrypted ext4 + exFAT disk image needs a Linux machine with root
(it uses `cryptsetup`, `losetup`, `mkfs`, `nixos-install`, and `nix`). Windows
and macOS **cannot** build it locally.

Easiest options:
- A spare Linux PC, **or**
- A Linux virtual machine on your Windows PC (e.g. Ubuntu in VirtualBox/Hyper-V),
  with [Nix](https://nixos.org/download) installed and flakes enabled.

(You still *flash* and *test* from Windows — only the build step needs Linux.)

---

## 1. Build the image (on the Linux machine)

```bash
# in the repo (LatheOS_Core_System/)
# (a) bake the AI models in so first boot is fully offline:
./scripts/prefetch-models.sh                 # ~7.5 GB (voice + 8B heavy + voices + whisper)

# (b) build the encrypted USB image, sized for your stick.
#     The T7 is 2 TB; build a generous image (NixOS does not auto-shrink).
sudo ./scripts/build-usb-image.sh --size 64G
# Output: dist/latheos-usb.img  (+ .sha256)
```

Notes:
- The image ships with a **factory disk password: `latheos`**. You change it on
  first boot with `lathe-setup` (step 5). To set a different factory password:
  `INITIAL_LUKS_PASS='something' sudo ./scripts/build-usb-image.sh --size 64G`.
- `--size 64G` keeps the build/flash fast for testing. You can build larger to
  use more of the 2 TB; the encrypted root is fixed at build size.

---

## 2. Put the image on the SSD

Copy `dist/latheos-usb.img` to your Windows PC, then flash it to the T7 with
**balenaEtcher** (easiest) or **Rufus** (DD-image mode):

1. Plug the T7 into a **USB 3 / USB-C** port (not a hub).
2. Open Etcher → *Flash from file* → pick `latheos-usb.img`.
3. Select the T7 as the target. **This erases the T7 — back it up first.**
4. Flash.

---

## 3. ✅ Safe test #1 — run it in a window (no risk to your PC)

This runs LatheOS in a virtual machine against the real SSD. Your Windows
install is untouched.

1. After flashing, the T7's small shared partition shows up in Windows.
2. Open it → `launcher/windows/` → double-click **`Launch-LatheOS.bat`**.
3. A LatheOS window opens and boots from the SSD.
4. It will ask for the **disk password** — type `latheos` (the factory one).
5. Log in, then run **`lathe-setup`** to change the password and finish setup.

What to check: it boots, you can log in, the shell opens, you can type to the
local AI, and (if the VM passes audio) voice works. Window mode is slower and
has no real GPU — that's expected; it's a functional test, not a speed test.

---

## 4. Real boot (optional, once the window test passes)

For full speed, boot a PC directly from the T7:

1. Plug in the T7, restart, and open the **boot menu** (`F12` / `F2` / `Esc` —
   varies by brand).
2. In firmware: **Secure Boot OFF**, **UEFI mode ON**.
3. Pick the T7 as the boot device.
4. Enter your disk password.
5. When the T7 isn't selected, the PC boots its normal OS as usual — your
   internal drive is never written to.

---

## 5. First-run setup (`lathe-setup`)

On first login you'll see a one-line nudge. Run:

```bash
lathe-setup
```

It walks you through:
1. **Change the disk password** away from the public `latheos`.
2. **Set your account password** (login + sudo).
3. **Language** (English default; Korean available).
4. **Wi-Fi** (hands off to the network tool).
5. **Optional:** store a cloud API key in the encrypted vault for `lathe-ai`.

---

## About testing on a Raspberry Pi

A **Pi 4 is not a drop-in test target.** A PC boots via UEFI from the SSD; a Pi
boots through its own firmware and needs a different (aarch64 SD-card) image —
the build script here targets PC-style UEFI boot. So for *testing*, the
**window-mode VM on your Windows PC (step 3) is both safer and easier** than a
Pi. If you later want a real Pi/Jetson build, that's a separate aarch64 image
target we can add.

---

## Quick reference — new commands

| Command | What it does |
|---|---|
| `lathe-setup` | First-run wizard: disk password, account password, language, Wi-Fi, cloud key |
| `lathe-ai "..."` | Opt-in cloud coding (Cursor default; `--provider claude\|opencode`) |
| `vault set NAME` | Store a secret/API key in the encrypted vault |
| `lathe models` | List / switch / pull local Ollama models |
