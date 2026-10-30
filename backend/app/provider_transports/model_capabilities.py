"""Small shared capability rules for the provider families Float supports."""

import re
from typing import Optional


def is_gpt_reasoning_family(model: Optional[str]) -> bool:
    name = str(model or "").strip().lower()
    return bool(re.match(r"^gpt-(?:5(?:\.\d+)?|6(?:\.1)?)(?:-|$)", name))


def supports_openai_reasoning_controls(model: Optional[str]) -> bool:
    name = str(model or "").strip().lower()
    if re.search(r"-chat(?:-|$)", name):
        return False
    return is_gpt_reasoning_family(name) or bool(
        re.match(r"^(?:o1|o3|o4|gpt-realtime-2(?:\.\d+)?)(?:-|$)", name)
    )


def supports_openai_reasoning_summary(model: Optional[str]) -> bool:
    # Do not send OpenAI-specific summary options to unknown compatible models.
    return is_gpt_reasoning_family(model) and supports_openai_reasoning_controls(model)
