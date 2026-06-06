"""Local text-to-speech router: Piper (default) or MisoTTS (opt-in GPU).

Tiered by design (see docs):

  * Piper  — tiny ONNX voice, CPU-only, works on every machine the USB lands
             on. This is the default everywhere.
  * MisoTTS — 8B emotive TTS, needs a ~24 GB-VRAM GPU. Served by the opt-in
             `latheos-tts.service` over loopback HTTP. We only use it when
             the autoselect step set LATHEOS_TTS_BACKEND=miso (i.e. a capable
             GPU was detected) and we silently fall back to Piper if the
             service is down or fails.

`LATHEOS_TTS_BACKEND` (auto|piper|miso):
  * auto  → Piper, unless /run/latheos/tts-backend.env overrode it to miso.
  * piper → force Piper.
  * miso  → try MisoTTS, fall back to Piper.

`synthesize()` returns an (int16 mono numpy array, sample_rate) tuple ready
for `cam_daemon.audio_io.play_pcm`, or None when nothing could be produced.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import tempfile
import wave

import numpy as np
import structlog

try:
    import httpx
except ImportError:                       # keep import-safe on bare CI
    httpx = None                          # type: ignore[assignment]

log = structlog.get_logger("cam-daemon.tts")

DEFAULT_PIPER_VOICE = "/assets/models/piper/en_US-amy-medium.onnx"
DEFAULT_TTS_URL = "http://127.0.0.1:11436"


def _decode_wav(data: bytes) -> tuple[np.ndarray, int] | None:
    try:
        with contextlib.closing(wave.open(io.BytesIO(data), "rb")) as w:
            rate = w.getframerate()
            channels = w.getnchannels()
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError, OSError):
        return None
    arr = np.frombuffer(frames, dtype=np.int16)
    if channels > 1 and arr.size:
        arr = arr.reshape(-1, channels)[:, 0].copy()
    return arr, rate


class TextToSpeech:
    def __init__(self) -> None:
        self.piper_voice = os.environ.get("LATHEOS_PIPER_VOICE", DEFAULT_PIPER_VOICE)
        self.tts_url = os.environ.get("LATHEOS_TTS_URL", DEFAULT_TTS_URL).rstrip("/")

    def _backend(self) -> str:
        choice = (os.environ.get("LATHEOS_TTS_BACKEND", "auto") or "auto").strip().lower()
        # "auto" resolves to Piper unless the autoselect drop-in promoted us to
        # miso on a GPU box (that file is loaded into the env by systemd).
        return "piper" if choice in ("", "auto") else choice

    async def synthesize(self, text: str) -> tuple[np.ndarray, int] | None:
        text = (text or "").strip()
        if not text:
            return None

        if self._backend() == "miso":
            wav = await self._miso(text)
            if wav is not None:
                decoded = _decode_wav(wav)
                if decoded is not None:
                    return decoded
            log.warning("tts.miso_unavailable_fallback_piper")

        return await self._piper(text)

    async def _piper(self, text: str) -> tuple[np.ndarray, int] | None:
        if not os.path.exists(self.piper_voice):
            log.warning("tts.piper_voice_missing", path=self.piper_voice)
            return None

        fd, out = tempfile.mkstemp(suffix=".wav", prefix="cam-tts-")
        os.close(fd)
        try:
            proc = await asyncio.create_subprocess_exec(
                "piper", "--model", self.piper_voice, "--output_file", out,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate(text.encode())
            with contextlib.suppress(OSError):
                with open(out, "rb") as fh:
                    data = fh.read()
                if data:
                    return _decode_wav(data)
            return None
        except FileNotFoundError:
            log.warning("tts.piper_not_installed")
            return None
        finally:
            with contextlib.suppress(OSError):
                os.unlink(out)

    async def _miso(self, text: str) -> bytes | None:
        if httpx is None:
            return None
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self.tts_url}/synthesize", json={"text": text}
                )
                resp.raise_for_status()
                return resp.content
        except Exception as exc:           # noqa: BLE001 — never fatal
            log.warning("tts.miso_error", error=str(exc))
            return None
