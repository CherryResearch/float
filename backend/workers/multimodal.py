"""Definitions for multimodal worker roles.

VisionCaptioner now prefers a real image captioning model when available.
Defaults to Google's PaliGemma2 small model and falls back to a
deterministic placeholder when dependencies or weights are not present.

Environment variables:
 - VISION_CAPTION_MODEL: HF repo id or local path (default: google/paligemma2-3b-pt-224)
 - FLOAT_MODELS_DIR: optional models root for local snapshots

Note: In tests and minimal environments, the placeholder path is used to
avoid heavyweight downloads. Real inference is used only when the
transformers/PIL/torch stack is detected and the model can be loaded.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
import io
import logging
import os
from typing import Any, Dict, Set

# simple in-memory caches standing in for Redis/S3 backends
VISION_CACHE: Dict[str, Any] = {}
logger = logging.getLogger(__name__)
ASR_CACHE: Dict[str, Any] = {}

PLACEHOLDER_PREFIX = "[placeholder]"


def placeholder_caption(ref: str) -> str:
    """Return a deterministic, human-readable placeholder caption."""

    tag = (ref or "").strip() or "unknown"
    return (
        f"{PLACEHOLDER_PREFIX} Unable to generate caption (vision model offline)."
        f" ref {tag}"
    )


def is_placeholder_caption(text: str) -> bool:
    """Check whether `text` is one of our placeholder captions."""

    return isinstance(text, str) and text.startswith(PLACEHOLDER_PREFIX)


@dataclass
class Worker:
    """Base worker definition.

    Each worker advertises the capabilities it provides and the type of
    input it consumes. The actual processing logic is intentionally
    minimal; in the real system these classes would wrap concrete model
    checkpoints or services.
    """

    model: str
    provides: Set[str] = field(default_factory=set)
    consumes: Set[str] = field(default_factory=set)
    location: str = "central"

    def run(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process ``data`` and return artifacts for each provided capability.

        The default implementation merely returns placeholder strings for the
        capabilities this worker advertises. Real implementations would call
        model inference or external services.
        """

        return {cap: f"{self.model}:{cap}" for cap in self.provides}


class LLM(Worker):
    def __init__(self, model: str = "gpt-4o-mini") -> None:
        super().__init__(
            model,
            {"text", "reasoning", "tool_call", "image", "audio"},
            {"text", "image", "audio", "image_caption", "asr"},
        )


class VisionCaptioner(Worker):
    def __init__(self, model: str | None = None) -> None:
        # Accept either raw image bytes ("image") or a keyframe reference ("keyframe")
        default = os.getenv("VISION_CAPTION_MODEL", "google/paligemma2-3b-pt-224")
        super().__init__(model or default, {"image_caption"}, {"image", "keyframe"})
        # Lazy-loaded inference stack
        self._loaded = False
        self._proc = None
        self._net = None
        self._device = None

    # --- Optional heavy path -------------------------------------------------

    def _load_if_possible(self) -> None:
        if self._loaded:
            return
        self._loaded = True  # ensure we only try once
        try:
            import importlib
            from transformers import AutoProcessor

            # Prefer the specific PaliGemma class if available; otherwise abort
            try:
                PaligemmaCls = importlib.import_module(
                    "transformers.models.paligemma2.modeling_paligemma2"
                ).PaliGemmaForConditionalGeneration
            except Exception:
                from transformers import PaliGemmaForConditionalGeneration as PaligemmaCls  # type: ignore

            # Try local-only load first to avoid accidental network calls
            local_only = True
            model_id = self.model

            # If a local folder exists under FLOAT_MODELS_DIR/<name>, use that
            # without setting local_files_only=False.
            try:
                from app import config as app_config  # type: ignore

                name = os.path.basename(str(model_id))
                for root in app_config.model_search_dirs(os.getenv("FLOAT_MODELS_DIR")):
                    candidate = root / name
                    if candidate.exists() and candidate.is_dir():
                        model_id = str(candidate)
                        break
            except Exception:
                pass  # best-effort; fall back to HF cache resolution

            # Resolve optional auth token
            token = os.getenv("HUGGINGFACE_HUB_TOKEN") or os.getenv("HF_TOKEN")

            # Lazy import torch and PIL only when available
            import torch  # type: ignore
            from PIL import Image  # type: ignore

            dtype = torch.bfloat16 if hasattr(torch, "bfloat16") else torch.float16
            device = "cuda" if torch.cuda.is_available() else ("mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu")

            proc = AutoProcessor.from_pretrained(model_id, local_files_only=local_only, token=token)
            net = PaligemmaCls.from_pretrained(
                model_id,
                local_files_only=local_only,
                token=token,
                torch_dtype=dtype,
            )
            if device != "cpu":
                net = net.to(device)
            self._proc = proc
            self._net = net
            self._device = device
            logger.info("VisionCaptioner loaded model: %s on %s", model_id, device)
        except Exception as e:
            # Keep placeholder behaviour if anything fails (missing deps, weights, etc.)
            logger.debug("VisionCaptioner heavy path unavailable: %s", e)

    def _caption_with_model(self, image_bytes: bytes) -> str | None:
        if not self._net or not self._proc:
            return None
        try:
            from PIL import Image  # type: ignore
            import torch  # type: ignore

            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            # PaliGemma expects an explicit image token prefix in text prompts.
            inputs = self._proc(
                text="<image> caption",
                images=img,
                return_tensors="pt",
            )
            if self._device and self._device != "cpu":
                inputs = {k: v.to(self._device) if hasattr(v, "to") else v for k, v in inputs.items()}
            with torch.no_grad():
                out = self._net.generate(**inputs, max_new_tokens=48)
            text = self._proc.decode(out[0], skip_special_tokens=True)
            return text.strip()
        except Exception as e:
            logger.debug("VisionCaptioner inference failed: %s", e)
            return None

    # --- Public API ----------------------------------------------------------

    def run(self, data: Any) -> Any:
        # Backwards compatibility: accept raw bytes as in older tests
        if isinstance(data, (bytes, bytearray)):
            key = hashlib.sha256(bytes(data)).hexdigest()
            if key in VISION_CACHE:
                return VISION_CACHE[key]
            # Try heavy path
            self._load_if_possible()
            best = self._caption_with_model(bytes(data))
            if best:
                VISION_CACHE[key] = best
                return best
            # Placeholder fallback
            caption = placeholder_caption(key[:8])
            VISION_CACHE[key] = caption
            return caption

        # New path: dict input from pipeline with either image bytes or keyframe ref
        # Prefer raw image bytes when available
        if "image" in data and isinstance(data["image"], (bytes, bytearray)):
            raw = data["image"]
            key = hashlib.sha256(raw).hexdigest()
            # Heavy path attempt
            self._load_if_possible()
            best = self._caption_with_model(raw)
            if best:
                return {"image_caption": best, "placeholder": False}
        else:
            # Fall back to a keyframe identifier/path string
            keyframe_ref = str(data.get("keyframe", ""))
            key = hashlib.sha256(keyframe_ref.encode("utf-8")).hexdigest()
        # Do not populate the global cache for non-bytes inputs to avoid pollution
        caption = VISION_CACHE.get(key)
        if caption is None:
            caption = placeholder_caption(key[:8])
        return {"image_caption": caption, "placeholder": is_placeholder_caption(caption)}


class ImageEmbedder(Worker):
    def __init__(self, model: str = "clip-vit-L/14") -> None:
        super().__init__(model, {"image_embed"}, {"image"})


class ASR(Worker):
    def __init__(self, model: str = "whisper-large-v3") -> None:
        # For unit tests, accept raw audio bytes; in pipelines we consume speech_turn
        super().__init__(model, {"asr"}, {"audio", "speech_turn"}, location="edge")

    def run(self, data: Any) -> Any:
        # Backwards compatibility: raw audio bytes
        if isinstance(data, (bytes, bytearray)):
            key = hashlib.sha256(bytes(data)).hexdigest()
            if key in ASR_CACHE:
                return ASR_CACHE[key]
            transcript = f"transcript:{key[:8]}"
            ASR_CACHE[key] = transcript
            return transcript

        # New path: dict payload with a detected speech turn token
        turn = str(data.get("speech_turn", ""))
        key = hashlib.sha256(turn.encode("utf-8")).hexdigest()
        transcript = ASR_CACHE.get(key)
        if transcript is None:
            transcript = f"transcript:{key[:8]}"
            # Do not mutate global cache for dict-path to keep cache size stable in tests
        return {"asr": transcript}


class TTS(Worker):
    def __init__(self, model: str = "xtts-v2") -> None:
        super().__init__(model, {"tts"}, {"text"})


class VAD(Worker):
    def __init__(self, model: str = "silero-vad") -> None:
        super().__init__(model, {"speech_turn"}, {"audio"}, location="edge")


class KeyframeDetector(Worker):
    def __init__(self, model: str = "scenedetect-r11") -> None:
        super().__init__(model, {"keyframe"}, {"video"})


ROLE_REGISTRY: Dict[str, Worker] = {
    "LLM": LLM(),
    "VisionCaptioner": VisionCaptioner(),
    "ImageEmbedder": ImageEmbedder(),
    "ASR": ASR(),
    "TTS": TTS(),
    "VAD": VAD(),
    "KeyframeDetector": KeyframeDetector(),
}
