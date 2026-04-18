"""PipeWire microphone capture + speaker playback.

`sounddevice` is a thin cffi binding around PortAudio; on NixOS it's
routed through PipeWire's Pulse shim. We keep the callback path
*allocation-free* — grab the frame, hand it to the consumer, return.
"""

from __future__ import annotations

import contextlib
import queue
import threading
from collections.abc import Callable

import numpy as np
import sounddevice as sd


class MicStream:
    def __init__(self, sample_rate: int = 16_000, frame_length: int = 512) -> None:
        self.sample_rate = sample_rate
        self.frame_length = frame_length
        self._stream: sd.InputStream | None = None

    async def start(self, on_frame: Callable[[np.ndarray], None]) -> None:
        def _cb(indata, frames, time_info, status) -> None:  # noqa: ANN001
            if status:
                # Under-runs are survivable; log at driver level instead of raising.
                return
            on_frame(indata[:, 0].astype(np.int16))

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.frame_length,
            callback=_cb,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class SpeakerSink:
    """Feed arbitrary PCM s16le chunks out to default output."""

    def __init__(self, sample_rate: int = 24_000) -> None:
        self.sample_rate = sample_rate
        self._q: queue.Queue[bytes | None] = queue.Queue(maxsize=64)
        self._stream: sd.OutputStream | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
        )
        self._stream.start()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def write(self, pcm: bytes) -> None:
        # Drop rather than block the WS ingress if the speaker can't keep up.
        with contextlib.suppress(queue.Full):
            self._q.put_nowait(pcm)

    def stop(self) -> None:
        self._q.put(None)
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()

    def _pump(self) -> None:
        assert self._stream is not None
        while True:
            item = self._q.get()
            if item is None:
                return
            arr = np.frombuffer(item, dtype=np.int16)
            self._stream.write(arr)
