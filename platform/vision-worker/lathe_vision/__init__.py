"""LatheOS vision-grounding worker (NVIDIA LocateAnything-3B).

Opt-in, GPU-gated, non-commercial. See modules/vision-grounding.nix and
docs/VISION_GROUNDING.md. Heavy ML deps are imported lazily so this package
is import-safe on a box with no GPU and no torch.
"""

from .worker import (
    DEFAULT_MAX_NEW_TOKENS,
    DEFAULT_MODE,
    LocateAnythingWorker,
    VisionUnavailable,
    WorkerConfig,
)

__all__ = [
    "DEFAULT_MAX_NEW_TOKENS",
    "DEFAULT_MODE",
    "LocateAnythingWorker",
    "VisionUnavailable",
    "WorkerConfig",
]

__version__ = "0.1.0"
