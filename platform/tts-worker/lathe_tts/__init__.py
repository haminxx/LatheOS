"""LatheOS MisoTTS worker package.

Opt-in, GPU-only "premium voice" for the assistant. Piper remains the
lightweight default everywhere; this serves NVIDIA-class emotive speech on
machines that actually have the VRAM for an 8B TTS model. See
modules/tts.nix and docs/VOICE_TTS.md.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
