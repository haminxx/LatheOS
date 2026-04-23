# platform/ — embedded vibe coding shell (planned)

This directory is reserved for the **in-OS Monaco + terminal + chat window** described in
[`docs/LATHEOS_VIBE_PLATFORM.md`](../docs/LATHEOS_VIBE_PLATFORM.md) §6.

**Intended contents**

- `embedded-shell/` — WebKit or Tauri host loading Monaco, xterm.js, and a chat strip wired to the **local** LLM daemon.
- Diff-review component used by the **self-repair loop** (§5 of the platform doc).

**Not here:**

- The CAM daemon stays in `daemon/`.
- The local LLM / STT / TTS services live in `modules/local-llm.nix` (planned).
- Host-side VM launchers (for Mode B) live in `launcher/` at the repo root (planned).
