"""Multi-modal activation: Porcupine wake-word OR acoustic clap.

Both detectors consume the same 16 kHz mono int16 stream, so we fan out
once from the microphone and run them cheaply in parallel. The first to
fire wins, emitting an activation event on the returned asyncio.Queue.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Literal

import numpy as np

try:
    import pvporcupine  # type: ignore
except ImportError:  # pragma: no cover - optional at build time
    pvporcupine = None  # type: ignore

try:
    import aubio  # type: ignore
except ImportError:  # pragma: no cover
    aubio = None  # type: ignore

from cam_daemon.audio_io import MicStream

ActivationKind = Literal["wake_word", "clap"]


@dataclass(slots=True)
class Activation:
    kind: ActivationKind
    confidence: float


class Activator:
    def __init__(
        self,
        access_key: str | None = None,
        keyword_path: str | None = None,
        sample_rate: int = 16_000,
    ) -> None:
        self.sample_rate = sample_rate
        self._porcupine = self._build_porcupine(access_key, keyword_path)
        self._onset = self._build_onset(sample_rate) if aubio else None

    @staticmethod
    def _build_porcupine(access_key: str | None, keyword_path: str | None):
        if pvporcupine is None:
            return None
        key = access_key or os.environ.get("PICOVOICE_ACCESS_KEY", "")
        if not key:
            return None
        if keyword_path and os.path.exists(keyword_path):
            return pvporcupine.create(access_key=key, keyword_paths=[keyword_path])
        return pvporcupine.create(access_key=key, keywords=["computer"])

    @staticmethod
    def _build_onset(sample_rate: int):
        # "hfc" is the sharpest onset method for transients like claps.
        onset = aubio.onset("hfc", 1024, 512, sample_rate)
        onset.set_threshold(0.6)
        onset.set_silence(-30.0)
        return onset

    @property
    def frame_length(self) -> int:
        return self._porcupine.frame_length if self._porcupine else 512

    def feed(self, frame: np.ndarray) -> Activation | None:
        if self._porcupine is not None:
            idx = self._porcupine.process(frame)
            if idx >= 0:
                return Activation("wake_word", 1.0)
        if self._onset is not None:
            f32 = (frame.astype(np.float32) / 32768.0)[: self._onset.hop_size]
            if self._onset(f32):
                peak = float(np.max(np.abs(f32)))
                if peak > 0.5:
                    return Activation("clap", peak)
        return None

    async def listen(self) -> asyncio.Queue[Activation]:
        """Start the mic stream and return a queue of activation events."""
        queue: asyncio.Queue[Activation] = asyncio.Queue()
        mic = MicStream(sample_rate=self.sample_rate, frame_length=self.frame_length)
        loop = asyncio.get_running_loop()

        def on_frame(frame: np.ndarray) -> None:
            event = self.feed(frame)
            if event is not None:
                loop.call_soon_threadsafe(queue.put_nowait, event)

        await mic.start(on_frame)
        return queue
