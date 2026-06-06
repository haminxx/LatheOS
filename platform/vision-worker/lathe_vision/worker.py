"""LocateAnythingWorker — local visual-grounding inference for LatheOS.

This is a faithful adaptation of NVIDIA's reference worker for
`nvidia/LocateAnything-3B`, wired for LatheOS's offline-first, exFAT model
layout. The model is a visual GROUNDING / detection / pointing VLM
(MoonViT vision encoder + Qwen2.5-3B decoder, ~4B params BF16). It is NOT a
general chat model and does NOT run in Ollama — it runs via HF Transformers
with `trust_remote_code=True` on an NVIDIA GPU (Linux).

Sources (cite when redistributing):
  * Model card : https://huggingface.co/nvidia/LocateAnything-3B
  * Code       : https://github.com/NVlabs/Eagle/tree/main/Embodied
  * Project    : https://research.nvidia.com/labs/lpr/locate-anything/

LICENSE — IMPORTANT
  LocateAnything-3B is released under the **NVIDIA non-commercial license**:
  academic / non-profit research use only. Commercial use is NOT permitted
  (except by NVIDIA). The vision encoder MoonViT is MIT; the language model
  Qwen2.5-3B is under the Qwen Research License. Redistribution must retain
  the license + attribution. This is why the LatheOS vision feature is
  OPT-IN and disabled by default — see modules/vision-grounding.nix.

Design notes for LatheOS
  * Heavy ML deps (torch, transformers==4.57.1, opencv) are imported LAZILY
    inside `load()`, so this module stays import-safe on a box with no GPU
    and no ML stack (mirrors daemon/cam_daemon/agents.py's `try: import`).
  * Model weights load from a LOCAL path (default /assets/models/
    locateanything) so first inference never phones home. The custom remote
    code still needs `trust_remote_code=True`; it ships inside the snapshot.
  * Coordinates in model output are normalised integers in [0, 1000]; the
    parse helpers convert them back to pixel coordinates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Default mode / token budget follow the model card guidance:
#   "use max_new_tokens=8192 and generation_mode='hybrid' to avoid truncated
#    response and balance speed with accuracy."
DEFAULT_MODE = "hybrid"            # "fast" (MTP) | "slow" (NTP/AR) | "hybrid"
DEFAULT_MAX_NEW_TOKENS = 8192


class VisionUnavailable(RuntimeError):
    """Raised when the ML stack, a CUDA GPU, or the weights are missing.

    The HTTP server turns this into a clean 503 instead of crashing, and the
    systemd unit treats a startup probe failure as a graceful no-op exit so a
    GPU-less boot never thrashes.
    """


@dataclass(slots=True)
class WorkerConfig:
    model_path: str = "/assets/models/locateanything"
    device: str = "cuda"
    generation_mode: str = DEFAULT_MODE
    max_new_tokens: int = DEFAULT_MAX_NEW_TOKENS
    temperature: float = 0.7
    # Free-form list mostly for diagnostics in /health.
    notes: list[str] = field(default_factory=list)


def probe(cfg: WorkerConfig) -> tuple[bool, str]:
    """Cheap pre-flight check: ML stack importable + CUDA present + weights on disk.

    Returns (ok, reason). Never raises — the caller decides what to do. Used by
    the systemd unit to exit cleanly on a machine with no GPU / no model rather
    than letting a torch import blow up the service.
    """
    import os

    if not os.path.isdir(cfg.model_path) or not os.listdir(cfg.model_path):
        return False, f"model weights not found at {cfg.model_path}"

    try:
        import torch  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — torch may be absent on minimal builds
        return False, f"torch not importable: {exc}"

    try:
        import transformers  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"transformers not importable: {exc}"

    if cfg.device.startswith("cuda"):
        try:
            import torch

            if not torch.cuda.is_available():
                return False, "no CUDA GPU visible (LocateAnything needs an NVIDIA GPU)"
        except Exception as exc:  # noqa: BLE001
            return False, f"CUDA check failed: {exc}"

    return True, "ok"


class LocateAnythingWorker:
    """Stateful worker that loads the model once and serves perception queries.

    Adapted from NVIDIA's reference `locateanything_worker.py`. The class is
    constructed cheaply; the expensive model load happens in `load()` so the
    server can report a clean error if the GPU / weights are missing.
    """

    def __init__(self, cfg: WorkerConfig | None = None) -> None:
        self.cfg = cfg or WorkerConfig()
        self._model = None
        self._tokenizer = None
        self._processor = None
        self._torch = None
        self._loaded = False

    # ---- lifecycle ----------------------------------------------------------

    @property
    def loaded(self) -> bool:
        return self._loaded

    def load(self) -> None:
        """Load tokenizer / processor / model from the local path. Idempotent.

        Raises VisionUnavailable with a clear message if the stack or GPU or
        weights are missing, so the HTTP layer can answer 503 cleanly.
        """
        if self._loaded:
            return

        ok, reason = probe(self.cfg)
        if not ok:
            raise VisionUnavailable(reason)

        try:
            import torch
            from transformers import AutoModel, AutoProcessor, AutoTokenizer
        except Exception as exc:  # noqa: BLE001
            raise VisionUnavailable(f"ML stack import failed: {exc}") from exc

        dtype = torch.bfloat16
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.cfg.model_path, trust_remote_code=True
            )
            self._processor = AutoProcessor.from_pretrained(
                self.cfg.model_path, trust_remote_code=True
            )
            self._model = (
                AutoModel.from_pretrained(
                    self.cfg.model_path,
                    torch_dtype=dtype,
                    trust_remote_code=True,
                )
                .to(self.cfg.device)
                .eval()
            )
        except Exception as exc:  # noqa: BLE001 — surface as a clean 503
            raise VisionUnavailable(f"model load failed: {exc}") from exc

        self._torch = torch
        self._dtype = dtype
        self._loaded = True

    # ---- core inference -----------------------------------------------------

    def predict(
        self,
        image,
        question: str,
        *,
        generation_mode: str | None = None,
        max_new_tokens: int | None = None,
        temperature: float | None = None,
        verbose: bool = False,
    ) -> dict:
        """Run one grounding query against a PIL.Image. Returns {"answer": str, ...}."""
        if not self._loaded:
            self.load()

        torch = self._torch
        gen_mode = generation_mode or self.cfg.generation_mode
        max_tok = max_new_tokens or self.cfg.max_new_tokens
        temp = self.cfg.temperature if temperature is None else temperature

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image},
                    {"type": "text", "text": question},
                ],
            }
        ]

        with torch.no_grad():
            text = self._processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            images, videos = self._processor.process_vision_info(messages)
            inputs = self._processor(
                text=[text], images=images, videos=videos, return_tensors="pt"
            ).to(self.cfg.device)

            pixel_values = inputs["pixel_values"].to(self._dtype)
            input_ids = inputs["input_ids"]
            image_grid_hws = inputs.get("image_grid_hws", None)

            response = self._model.generate(
                pixel_values=pixel_values,
                input_ids=input_ids,
                attention_mask=inputs["attention_mask"],
                image_grid_hws=image_grid_hws,
                tokenizer=self._tokenizer,
                max_new_tokens=max_tok,
                use_cache=True,
                generation_mode=gen_mode,
                temperature=temp,
                do_sample=True,
                top_p=0.9,
                repetition_penalty=1.1,
                verbose=verbose,
            )

        result = {"answer": response[0] if isinstance(response, tuple) else response}
        if isinstance(response, tuple) and len(response) >= 3:
            result["history"] = response[1]
            result["stats"] = response[2]
        return result

    # ---- convenience methods (one per supported task) -----------------------
    # Prompt templates are copied verbatim from the NVIDIA model card; do not
    # paraphrase them — the model was trained on these exact strings.

    def detect(self, image, categories: list[str], **kw) -> dict:
        """Object detection / document layout analysis. Output: boxes."""
        cats = "</c>".join(categories)
        prompt = f"Locate all the instances that matches the following description: {cats}."
        return self.predict(image, prompt, **kw)

    def ground_single(self, image, phrase: str, **kw) -> dict:
        """Phrase grounding — single instance. Output: one box."""
        prompt = f"Locate a single instance that matches the following description: {phrase}."
        return self.predict(image, prompt, **kw)

    def ground_multi(self, image, phrase: str, **kw) -> dict:
        """Phrase grounding — multiple instances. Output: boxes."""
        prompt = f"Locate all the instances that match the following description: {phrase}."
        return self.predict(image, prompt, **kw)

    def ground_text(self, image, phrase: str, **kw) -> dict:
        """Text grounding. Output: box."""
        prompt = f"Please locate the text referred as {phrase}."
        return self.predict(image, prompt, **kw)

    def detect_text(self, image, **kw) -> dict:
        """Scene text detection. Output: boxes."""
        prompt = "Detect all the text in box format."
        return self.predict(image, prompt, **kw)

    def ground_gui(self, image, phrase: str, output_type: str = "box", **kw) -> dict:
        """GUI grounding (box or point) — e.g. 'the search button'."""
        if output_type == "point":
            prompt = f"Point to: {phrase}."
        else:
            prompt = f"Locate the region that matches the following description: {phrase}."
        return self.predict(image, prompt, **kw)

    def point(self, image, phrase: str, **kw) -> dict:
        """Pointing. Output: point."""
        prompt = f"Point to: {phrase}."
        return self.predict(image, prompt, **kw)

    # ---- output parsing -----------------------------------------------------

    @staticmethod
    def parse_boxes(answer: str, image_width: int, image_height: int) -> list[dict]:
        """Parse model output into pixel-coordinate bounding boxes.

        Coordinates in model output are normalised integers in [0, 1000].
        """
        boxes: list[dict] = []
        for m in re.finditer(r"<box><(\d+)><(\d+)><(\d+)><(\d+)></box>", answer):
            x1, y1, x2, y2 = (int(g) for g in m.groups())
            boxes.append(
                {
                    "x1": x1 / 1000 * image_width,
                    "y1": y1 / 1000 * image_height,
                    "x2": x2 / 1000 * image_width,
                    "y2": y2 / 1000 * image_height,
                }
            )
        return boxes

    @staticmethod
    def parse_points(answer: str, image_width: int, image_height: int) -> list[dict]:
        """Parse model output into pixel-coordinate points."""
        points: list[dict] = []
        for m in re.finditer(r"<box><(\d+)><(\d+)></box>", answer):
            x, y = int(m.group(1)), int(m.group(2))
            points.append(
                {
                    "x": x / 1000 * image_width,
                    "y": y / 1000 * image_height,
                }
            )
        return points
