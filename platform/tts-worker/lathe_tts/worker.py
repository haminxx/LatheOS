"""MisoWorker — local emotive text-to-speech for LatheOS (opt-in, GPU-only).

Wraps MisoLabs' MisoTTS 8B model. This is the *premium* tier of LatheOS's
tiered TTS: Piper (CPU, tiny) is the default on every machine; MisoTTS only
runs when a capable GPU is detected and the feature is enabled.

Source:
  * Code / model : https://github.com/MisoLabsAI/MisoTTS  (MisoLabs/MisoTTS on HF)

Constraints (why this is opt-in and disabled by default)
  * Hardware : ~8.2B params; needs a ~24 GB-VRAM GPU for bf16 interactive use.
               CPU "works" but is far too slow for a voice loop.
  * Download : ~30-40 GB on first fetch (model + Mimi codec + SilentCipher
               watermarker + Llama 3.2 tokenizer).
  * Language : English only.
  * Watermark: output is watermarked by default (SilentCipher). If you deploy
               this elsewhere, use your own private key.

Design (mirrors platform/vision-worker)
  * Heavy deps (torch, the MisoTTS `generator` module) are imported LAZILY in
    `load()` so this module is import-safe on a box with no GPU / no weights.
  * Weights + the MisoTTS source live on the exFAT partition
    (LATHEOS_MISO_MODEL / LATHEOS_MISO_CODE_DIR, default /assets/models/miso)
    so they survive nixos-rebuild and can be managed from another OS.
  * `synthesize()` returns a ready-to-play WAV (s16le mono) built with the
    stdlib `wave` module — no torchaudio dependency on the hot path.
"""

from __future__ import annotations

import io
import os
import sys
import wave
from dataclasses import dataclass, field

DEFAULT_SAMPLE_RATE = 24_000          # Mimi codec runs at 24 kHz
DEFAULT_SPEAKER = 0
DEFAULT_MAX_MS = 20_000


class TTSUnavailable(RuntimeError):
    """Raised when the ML stack, a CUDA GPU, or the MisoTTS weights are missing.

    The HTTP server turns this into a clean 503 and the systemd unit treats a
    startup probe failure as a graceful exit, so a GPU-less boot never thrashes.
    """


@dataclass(slots=True)
class MisoConfig:
    model_path: str = "/assets/models/miso"
    # Where generator.py / models.py from the MisoTTS repo live. Defaults to
    # the weights dir (clone the repo there and let it cache the weights).
    code_dir: str = "/assets/models/miso"
    device: str = "cuda"
    speaker: int = DEFAULT_SPEAKER
    max_audio_ms: int = DEFAULT_MAX_MS
    notes: list[str] = field(default_factory=list)


def probe(cfg: MisoConfig) -> tuple[bool, str]:
    """Cheap pre-flight: weights/code on disk + torch + CUDA. Never raises."""
    if not os.path.isdir(cfg.model_path) or not os.listdir(cfg.model_path):
        return False, f"MisoTTS weights not found at {cfg.model_path}"
    if not os.path.isfile(os.path.join(cfg.code_dir, "generator.py")):
        return False, f"MisoTTS code (generator.py) not found in {cfg.code_dir}"

    try:
        import torch  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"torch not importable: {exc}"

    if cfg.device.startswith("cuda"):
        try:
            import torch

            if not torch.cuda.is_available():
                return False, "no CUDA GPU visible (MisoTTS needs a ~24GB GPU)"
        except Exception as exc:  # noqa: BLE001
            return False, f"CUDA check failed: {exc}"

    return True, "ok"


class MisoWorker:
    def __init__(self, cfg: MisoConfig | None = None) -> None:
        self.cfg = cfg or MisoConfig()
        self._gen = None
        self._torch = None
        self._loaded = False
        self.sample_rate = DEFAULT_SAMPLE_RATE

    @property
    def loaded(self) -> bool:
        return self._loaded

    def load(self) -> None:
        """Load the MisoTTS generator from the local path. Idempotent."""
        if self._loaded:
            return

        ok, reason = probe(self.cfg)
        if not ok:
            raise TTSUnavailable(reason)

        if self.cfg.code_dir and self.cfg.code_dir not in sys.path:
            sys.path.insert(0, self.cfg.code_dir)

        try:
            import torch
            from generator import load_miso_8b  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise TTSUnavailable(
                f"MisoTTS code import failed (need generator.py in {self.cfg.code_dir}): {exc}"
            ) from exc

        try:
            self._gen = load_miso_8b(
                device=self.cfg.device,
                model_path_or_repo_id=self.cfg.model_path,
            )
        except Exception as exc:  # noqa: BLE001 — surface as a clean 503
            raise TTSUnavailable(f"MisoTTS model load failed: {exc}") from exc

        self._torch = torch
        self.sample_rate = int(getattr(self._gen, "sample_rate", DEFAULT_SAMPLE_RATE))
        self._loaded = True

    def synthesize(
        self,
        text: str,
        *,
        speaker: int | None = None,
        max_audio_ms: int | None = None,
    ) -> bytes:
        """Generate speech for `text`; return a mono s16le WAV byte string."""
        if not self._loaded:
            self.load()

        torch = self._torch
        spk = self.cfg.speaker if speaker is None else int(speaker)
        max_ms = self.cfg.max_audio_ms if max_audio_ms is None else int(max_audio_ms)

        with torch.no_grad():
            audio = self._gen.generate(
                text=text,
                speaker=spk,
                context=[],
                max_audio_length_ms=max_ms,
            )
        return _tensor_to_wav(audio, self.sample_rate)


def _tensor_to_wav(audio, sample_rate: int) -> bytes:
    """Convert a float [-1, 1] torch tensor to a mono 16-bit PCM WAV."""
    import numpy as np

    arr = audio.detach().to("cpu").float().numpy().reshape(-1)
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype("<i2")

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
