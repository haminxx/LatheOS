"""Multi-modal activation for LatheOS.

Four backends feed the same activation queue. Whichever fires first wins:

  1. **openWakeWord** (Apache-2.0, ONNX) — default when `LATHEOS_WAKE_BACKEND=oww`.
     Ships its own "hey_jarvis" + "alexa" pretrained models; no vendor key.
  2. **Picovoice Porcupine** — default when `LATHEOS_WAKE_BACKEND=porcupine`
     and `PICOVOICE_ACCESS_KEY` is set. Requires the user's free-tier key.
  3. **aubio onset** — always-on clap detector. Zero dependencies at
     runtime beyond aubio itself. Useful when the wake model is missing.
  4. **Control socket** — `camctl activate` and the Sway `$mod+space`
     keybind both produce a synthetic activation via `cam_daemon.control_socket`.

Why layered
-----------
The user explicitly asked to not depend on Picovoice while approval is
pending. Making every backend optional means: (a) shipping today with
openWakeWord + clap + PTT as defaults, (b) toggling Picovoice on later
with a single env var on the already-flashed USB — no rebuild required.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

import numpy as np

if TYPE_CHECKING:
    from cam_daemon.audio_io import MicStream

# ---------------------------------------------------------------------------
# Optional vendor imports. Keep each one wrapped so a missing package never
# prevents cam-daemon from starting — we want the control socket to work on
# a bone-stock Python install in CI.
# ---------------------------------------------------------------------------

try:
    import pvporcupine  # type: ignore
except ImportError:  # pragma: no cover
    pvporcupine = None  # type: ignore

try:
    import openwakeword  # type: ignore
    from openwakeword.model import Model as _OWWModel  # type: ignore
except ImportError:  # pragma: no cover
    openwakeword = None  # type: ignore
    _OWWModel = None  # type: ignore

try:
    import aubio  # type: ignore
except ImportError:  # pragma: no cover
    aubio = None  # type: ignore


ActivationKind = Literal["wake_word", "clap", "control_socket"]


@dataclass(slots=True)
class Activation:
    kind: ActivationKind
    confidence: float


class _WakeBackend(Protocol):
    """Common interface so `feed()` can polymorphically call whichever backend."""

    frame_length: int

    def process(self, frame: np.ndarray) -> float | None: ...


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class _PorcupineBackend:
    frame_length: int

    def __init__(self, access_key: str, keyword_path: str | None) -> None:
        if keyword_path and os.path.exists(keyword_path):
            self._pv = pvporcupine.create(access_key=access_key, keyword_paths=[keyword_path])
        else:
            self._pv = pvporcupine.create(access_key=access_key, keywords=["computer"])
        self.frame_length = self._pv.frame_length

    def process(self, frame: np.ndarray) -> float | None:
        idx = self._pv.process(frame)
        return 1.0 if idx >= 0 else None


class _OpenWakeWordBackend:
    """Apache-2.0 ONNX wake-word detector.

    openWakeWord feeds 1280-sample blocks (80 ms @ 16 kHz). We keep the
    frame length consistent with Porcupine's 512 to avoid re-designing the
    mic pump; openWakeWord internally buffers.
    """

    frame_length: int = 1280

    def __init__(self, threshold: float = 0.5, keyword: str = "hey_jarvis") -> None:
        # Passing inference_framework="onnx" keeps us off of TensorFlow.
        self._model = _OWWModel(inference_framework="onnx", wakeword_models=None)
        self._threshold = threshold
        self._keyword = keyword

    def process(self, frame: np.ndarray) -> float | None:
        scores = self._model.predict(frame)
        best = max(scores.values()) if scores else 0.0
        return best if best >= self._threshold else None


# ---------------------------------------------------------------------------
# Activator
# ---------------------------------------------------------------------------


_AUDIO_QUEUE_MAX = 16


class Activator:
    def __init__(
        self,
        access_key: str | None = None,
        keyword_path: str | None = None,
        sample_rate: int = 16_000,
        backend: str | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self._backend = self._build_backend(backend, access_key, keyword_path)
        self._onset = self._build_onset(sample_rate) if aubio else None
        self._mic: "MicStream | None" = None

    # ------------------------------------------------------------------
    @staticmethod
    def _build_backend(
        backend: str | None,
        access_key: str | None,
        keyword_path: str | None,
    ) -> _WakeBackend | None:
        """Pick a backend based on env + availability.

        Priority:
          1. Explicit env (`LATHEOS_WAKE_BACKEND=oww|porcupine|none`).
          2. openWakeWord if importable (free, default).
          3. Porcupine if a Picovoice key is present.
          4. None — clap + control-socket only.
        """
        choice = (backend or os.environ.get("LATHEOS_WAKE_BACKEND", "")).strip().lower()

        if choice == "none":
            return None

        if choice in ("", "oww", "openwakeword") and _OWWModel is not None:
            try:
                return _OpenWakeWordBackend()
            except Exception:  # noqa: BLE001 - never fatal; fall through
                pass

        if choice in ("", "porcupine") and pvporcupine is not None:
            key = access_key or os.environ.get("PICOVOICE_ACCESS_KEY", "")
            if key:
                try:
                    return _PorcupineBackend(key, keyword_path)
                except Exception:  # noqa: BLE001
                    return None

        return None

    @staticmethod
    def _build_onset(sample_rate: int):
        onset = aubio.onset("hfc", 1024, 512, sample_rate)
        onset.set_threshold(0.6)
        onset.set_silence(-30.0)
        return onset

    # ------------------------------------------------------------------
    @property
    def frame_length(self) -> int:
        if self._backend is not None:
            return self._backend.frame_length
        # No wake model — stick to 512, matches Porcupine's pre-2023 default.
        return 512

    def feed(self, frame: np.ndarray) -> Activation | None:
        if self._backend is not None:
            score = self._backend.process(frame)
            if score is not None:
                return Activation("wake_word", float(score))
        if self._onset is not None:
            f32 = (frame.astype(np.float32) / 32768.0)[: self._onset.hop_size]
            if self._onset(f32):
                peak = float(np.max(np.abs(f32)))
                if peak > 0.5:
                    return Activation("clap", peak)
        return None

    # ------------------------------------------------------------------
    async def listen(self) -> tuple[asyncio.Queue[Activation], asyncio.Queue[bytes]]:
        """Start the mic stream. Same contract as before — two queues."""
        from cam_daemon.audio_io import MicStream  # lazy — see note at top

        activations: asyncio.Queue[Activation] = asyncio.Queue()
        audio: asyncio.Queue[bytes] = asyncio.Queue(maxsize=_AUDIO_QUEUE_MAX)
        mic = MicStream(sample_rate=self.sample_rate, frame_length=self.frame_length)
        self._mic = mic
        loop = asyncio.get_running_loop()

        def on_frame(frame: np.ndarray) -> None:
            event = self.feed(frame)
            if event is not None:
                loop.call_soon_threadsafe(activations.put_nowait, event)

            pcm = frame.tobytes()

            def _enqueue() -> None:
                if audio.full():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        audio.get_nowait()
                audio.put_nowait(pcm)

            loop.call_soon_threadsafe(_enqueue)

        await mic.start(on_frame)
        return activations, audio

    def stop(self) -> None:
        if self._mic is not None:
            self._mic.stop()
            self._mic = None
