"""Offline speech-to-text via whisper.cpp.

Everything here runs on the USB — mic PCM never leaves the machine. We
capture one utterance after a wake event using a dead-simple energy VAD
(no extra model), dump it to a temp 16 kHz mono WAV, and shell out to the
`whisper-cpp` binary that `modules/local-llm.nix` already puts on PATH.

The whisper model lives on the exFAT partition
(`LATHEOS_WHISPER_MODEL`, default `/assets/models/whisper/ggml-base.en.bin`)
so it survives `nixos-rebuild` and can be swapped from another OS.

Both functions are defensive: a missing model, a missing binary, or a
silent room return an empty string rather than raising, so the daemon can
fall back to "I didn't catch that" instead of crashing.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
import wave

import numpy as np
import structlog

log = structlog.get_logger("cam-daemon.stt")

DEFAULT_WHISPER_MODEL = "/assets/models/whisper/ggml-base.en.bin"
DEFAULT_WHISPER_BIN = "whisper-cpp"


async def record_utterance(
    audio_q: "asyncio.Queue[bytes]",
    sample_rate: int = 16_000,
    *,
    max_wait_s: float = 4.0,
    max_utterance_s: float = 15.0,
    silence_tail_s: float = 0.8,
    threshold: float = 500.0,
) -> bytes:
    """Pull mic frames off the shared queue until the speaker goes quiet.

    Returns the raw s16le PCM of the captured utterance (possibly empty if
    the user never started talking within ``max_wait_s``).
    """
    # Drop anything buffered before the wake fired so we don't transcribe the
    # tail of whatever was happening in the room a second ago.
    while not audio_q.empty():
        with contextlib.suppress(asyncio.QueueEmpty):
            audio_q.get_nowait()

    frames: list[bytes] = []
    started = False
    waited = 0.0
    spoken = 0.0
    silent_run = 0.0

    while True:
        try:
            frame = await asyncio.wait_for(audio_q.get(), timeout=2.0)
        except (TimeoutError, asyncio.TimeoutError):
            break

        arr = np.frombuffer(frame, dtype=np.int16)
        if arr.size == 0:
            continue
        dur = arr.size / sample_rate
        rms = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))

        if not started:
            waited += dur
            if rms >= threshold:
                started = True
                frames.append(frame)
            elif waited >= max_wait_s:
                break
            continue

        frames.append(frame)
        spoken += dur
        if rms < threshold:
            silent_run += dur
            if silent_run >= silence_tail_s:
                break
        else:
            silent_run = 0.0
        if spoken >= max_utterance_s:
            break

    return b"".join(frames)


class SpeechToText:
    def __init__(self) -> None:
        self.model = os.environ.get("LATHEOS_WHISPER_MODEL", DEFAULT_WHISPER_MODEL)
        self.binary = os.environ.get("LATHEOS_WHISPER_BIN", DEFAULT_WHISPER_BIN)
        self.language = os.environ.get("LATHEOS_WHISPER_LANG", "auto")
        self.sample_rate = int(os.environ.get("CAM_SAMPLE_RATE", "16000"))

    async def transcribe(self, pcm: bytes) -> str:
        if not pcm:
            return ""
        if not os.path.exists(self.model):
            log.warning("stt.model_missing", path=self.model)
            return ""

        wav_path = self._write_wav(pcm)
        try:
            return await self._run_whisper(wav_path)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(wav_path)

    def _write_wav(self, pcm: bytes) -> str:
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="cam-stt-")
        os.close(fd)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            w.writeframes(pcm)
        return path

    async def _run_whisper(self, wav_path: str) -> str:
        out_base = wav_path + ".out"
        argv = [
            self.binary,
            "-m", self.model,
            "-f", wav_path,
            "-nt",            # no timestamps
            "-otxt",          # also write <out_base>.txt
            "-of", out_base,
        ]
        if self.language and self.language != "auto":
            argv += ["-l", self.language]

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except FileNotFoundError:
            log.warning("stt.binary_missing", binary=self.binary)
            return ""

        # Prefer the .txt artefact; fall back to parsing stdout.
        txt_path = out_base + ".txt"
        text = ""
        if os.path.exists(txt_path):
            with contextlib.suppress(OSError):
                with open(txt_path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            with contextlib.suppress(OSError):
                os.unlink(txt_path)
        if not text.strip():
            text = stdout.decode(errors="replace")

        if proc.returncode not in (0, None) and not text.strip():
            log.warning("stt.failed", rc=proc.returncode,
                        stderr=stderr.decode(errors="replace")[-300:])
        return _clean(text)


def _clean(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines()]
    return " ".join(ln for ln in lines if ln).strip()
