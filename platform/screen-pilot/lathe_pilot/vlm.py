"""Step-planning via the local Ollama vision model (loopback).

Given the user's goal + a screenshot, we ask a vision-capable Ollama model
(LATHEOS_VLM_MODEL, e.g. llama3.2-vision — the same one daemon/cam_daemon/
vision.py uses) to produce an ORDERED plan. For each step the model returns:

  * instruction : a short imperative ("Click the Settings gear, top-right").
  * target      : a grounding PHRASE for the element to point at ("settings
                  gear icon in the top-right toolbar"). This is what we feed to
                  LocateAnything — NOT a pixel guess.

We force JSON output and parse defensively. If the model isn't pulled, returns
junk, or the service is down, we fall back to a single describe-only step so
the pilot still says SOMETHING useful instead of crashing.

PRIVACY: this hits 127.0.0.1:11434 only. The screenshot is sent as base64 to
the local Ollama; nothing leaves the machine.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from .httpjson import post_json


@dataclass(slots=True)
class Step:
    instruction: str
    target: str           # grounding phrase for LocateAnything ("" = no target)


# Asking for strict JSON keeps parsing simple and model-agnostic. We keep the
# step count small so the plan stays followable one card at a time.
_SYSTEM = (
    "You are LatheOS Screen Pilot, a calm on-screen guide. You see a screenshot "
    "of the user's Linux (Sway/Wayland) desktop and a GOAL. Produce a SHORT, "
    "ordered click-path that walks the user to the goal, ONE UI action per step. "
    "For each step give a plain imperative 'instruction' (<= 14 words) and a "
    "'target' which is a concise visual description of the single UI element to "
    "point at (e.g. 'the blue Search button in the top toolbar'). The target is "
    "used by a separate grounding model to find pixels, so describe what it LOOKS "
    "like and WHERE it is, do not guess coordinates. If a step needs no on-screen "
    "target (e.g. 'type your query'), use an empty target. "
    "Reply with ONLY JSON: {\"steps\":[{\"instruction\":\"...\",\"target\":\"...\"}]}."
)


def _b64(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            return base64.b64encode(fh.read()).decode("ascii")
    except OSError:
        return None


def plan_steps(
    llm_url: str,
    model: str,
    goal: str,
    image_path: str,
    *,
    max_steps: int = 8,
    timeout: float = 120.0,
) -> tuple[list[Step], str | None]:
    """Return (steps, error). On any failure, steps is a single fallback step."""
    if not model:
        return [_fallback_step(goal)], "no LATHEOS_VLM_MODEL set (pull e.g. llama3.2-vision)"

    b64 = _b64(image_path)
    if b64 is None:
        return [_fallback_step(goal)], f"cannot read screenshot {image_path}"

    prompt = (
        f"GOAL: {goal}\n\n"
        "Look at the screenshot and produce the JSON click-path now."
    )
    payload = {
        "model": model,
        "system": _SYSTEM,
        "prompt": prompt,
        "images": [b64],
        "stream": False,
        "format": "json",                       # Ollama: constrain to JSON.
        "options": {"temperature": 0.2, "num_predict": 700},
    }
    data = post_json(f"{llm_url}/api/generate", payload, timeout=timeout)
    if not data.get("ok"):
        err = data.get("error", "vlm request failed")
        # 404 from Ollama usually means the model isn't pulled.
        return [_fallback_step(goal)], err

    raw = (data.get("response") or "").strip()
    steps = _parse_steps(raw)
    if not steps:
        return [_fallback_step(goal)], "vlm returned no usable steps"
    return steps[:max_steps], None


def _parse_steps(raw: str) -> list[Step]:
    """Tolerantly pull a steps list out of the model's JSON-ish reply."""
    obj = _loads_loose(raw)
    if obj is None:
        return []
    items = obj.get("steps") if isinstance(obj, dict) else obj
    if not isinstance(items, list):
        return []
    steps: list[Step] = []
    for it in items:
        if isinstance(it, dict):
            instr = str(it.get("instruction") or it.get("step") or "").strip()
            target = str(it.get("target") or it.get("element") or "").strip()
        elif isinstance(it, str):
            instr, target = it.strip(), ""
        else:
            continue
        if instr:
            steps.append(Step(instruction=instr, target=target))
    return steps


def _loads_loose(raw: str):
    """json.loads, but salvage the first {...} or [...] block if there's noise."""
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start = raw.find(open_c)
        end = raw.rfind(close_c)
        if 0 <= start < end:
            try:
                return json.loads(raw[start : end + 1])
            except (ValueError, TypeError):
                continue
    return None


def _fallback_step(goal: str) -> Step:
    """Used when the VLM is unavailable: describe-only, no grounding target."""
    return Step(
        instruction=f"I can't plan visually right now. Goal: {goal}",
        target="",
    )
