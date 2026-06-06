"""Narration via the local tiered TTS (Piper default, MisoTTS opt-in).

Mirrors the contract in daemon/cam_daemon/tts.py but standalone and
synchronous: prefer the MisoTTS loopback server when the backend is "miso",
otherwise shell out to piper. Audio is played with whatever simple player is
on PATH (paplay / aplay / ffplay). All best-effort — a missing voice or player
just means the step card + cursor still work, only the speech is silent.

PRIVACY: piper runs locally; the MisoTTS server is loopback (127.0.0.1:11436).
No audio or text ever leaves the machine.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from shutil import which

from .httpjson import post_bytes


class Narrator:
    def __init__(
        self,
        *,
        backend: str = "piper",
        piper_voice: str = "/assets/models/piper/en_US-amy-medium.onnx",
        tts_url: str = "http://127.0.0.1:11436",
        enabled: bool = True,
    ) -> None:
        self.backend = (backend or "piper").strip().lower()
        self.piper_voice = piper_voice
        self.tts_url = tts_url.rstrip("/")
        self.enabled = enabled

    def speak(self, text: str) -> bool:
        """Synthesize + play `text`. Returns True if audio was produced."""
        text = (text or "").strip()
        if not text or not self.enabled:
            return False

        wav: str | None = None
        try:
            if self.backend == "miso":
                wav = self._miso(text)
            if wav is None:
                wav = self._piper(text)
            if wav is None:
                return False
            return self._play(wav)
        finally:
            if wav and os.path.exists(wav):
                try:
                    os.unlink(wav)
                except OSError:
                    pass

    # ---- backends -----------------------------------------------------------

    def _piper(self, text: str) -> str | None:
        if which("piper") is None or not os.path.exists(self.piper_voice):
            return None
        fd, out = tempfile.mkstemp(suffix=".wav", prefix="pilot-tts-")
        os.close(fd)
        try:
            proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                ["piper", "--model", self.piper_voice, "--output_file", out],
                input=text.encode(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            _safe_unlink(out)
            return None
        if proc.returncode == 0 and os.path.getsize(out) > 0:
            return out
        _safe_unlink(out)
        return None

    def _miso(self, text: str) -> str | None:
        # MisoTTS answers /synthesize with raw WAV bytes (not JSON). Fetch them
        # and stage to a tmpfile; on any failure return None so speak() falls
        # back to piper. A valid WAV starts with the "RIFF" magic.
        audio = post_bytes(f"{self.tts_url}/synthesize", {"text": text}, timeout=60.0)
        if not audio or audio[:4] != b"RIFF":
            return None
        fd, out = tempfile.mkstemp(suffix=".wav", prefix="pilot-tts-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(audio)
        except OSError:
            _safe_unlink(out)
            return None
        return out

    def _play(self, wav_path: str) -> bool:
        for player, argv in (
            ("paplay", ["paplay", wav_path]),
            ("aplay", ["aplay", "-q", wav_path]),
            ("ffplay", ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet", wav_path]),
        ):
            if which(player) is None:
                continue
            try:
                subprocess.run(  # noqa: S603 — fixed argv, no shell
                    argv,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=60,
                    check=False,
                )
                return True
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                continue
        return False


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
