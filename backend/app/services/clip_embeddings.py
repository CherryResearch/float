"""CLIP embedding helpers (optional dependency).

These helpers are intentionally *lazy* imports so Float can start without
`open_clip` installed. Callers should treat failures as "multimodal embeddings
unavailable" and fall back to text-only storage/retrieval.

Schema/architecture note:
- CLIP text and image embeddings live in the *same* vector space, but they
  generally have a different dimensionality from our text embedder
  (sentence-transformers or the hash fallback). Because Chroma/Weaviate expect
  consistent dimensions per collection/class, CLIP vectors should be stored in
  a dedicated index/collection.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

CLIP_DEFAULT_MODEL = "ViT-B-32"
CLIP_DEFAULT_PRETRAINED = "openai"


@dataclass(frozen=True, slots=True)
class _ClipBundle:
    model: Any
    preprocess: Any
    tokenizer: Any
    device: str


@lru_cache(maxsize=2)
def _load_clip(
    model_name: str,
    pretrained: str,
    device: str,
    cache_dir: str | None,
) -> _ClipBundle:
    try:
        import open_clip
        import torch
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "open_clip + torch are required for CLIP embeddings"
        ) from exc

    model, _, preprocess = open_clip.create_model_and_transforms(
        model_name,
        pretrained=pretrained,
        cache_dir=cache_dir,
    )
    model = model.to(device)
    model.eval()
    tokenizer = open_clip.get_tokenizer(model_name)
    # Ensure torch is imported so the returned bundle is usable by callers.
    _ = torch  # noqa: F841
    return _ClipBundle(
        model=model, preprocess=preprocess, tokenizer=tokenizer, device=device
    )


def _normalize_vec(vec) -> list[float]:
    try:
        import torch
    except Exception:  # pragma: no cover - torch is already required by _load_clip
        return [float(v) for v in (vec.tolist() if hasattr(vec, "tolist") else vec)]

    t = vec.detach().float().cpu()
    denom = torch.linalg.norm(t)
    if float(denom) > 0:
        t = t / denom
    return t.tolist()


def embed_clip_text(
    text: str,
    *,
    model_name: str = CLIP_DEFAULT_MODEL,
    pretrained: str = CLIP_DEFAULT_PRETRAINED,
    cache_dir: Path | None = Path("models/embeddings/clip"),
    device: str = "cpu",
) -> list[float]:
    """Embed text into CLIP space (for querying an image-vector index)."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text is required for CLIP text embedding")
    try:
        import torch
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("torch is required for CLIP embeddings") from exc

    bundle = _load_clip(
        model_name=model_name,
        pretrained=pretrained,
        device=device,
        cache_dir=str(cache_dir) if cache_dir else None,
    )
    tokens = bundle.tokenizer([text]).to(bundle.device)
    with torch.no_grad():
        vec = bundle.model.encode_text(tokens)[0]
    return _normalize_vec(vec)


def embed_clip_image_bytes(
    data: bytes,
    *,
    model_name: str = CLIP_DEFAULT_MODEL,
    pretrained: str = CLIP_DEFAULT_PRETRAINED,
    cache_dir: Path | None = Path("models/embeddings/clip"),
    device: str = "cpu",
) -> list[float]:
    """Embed image bytes into CLIP space."""
    if not data:
        raise ValueError("image bytes are required for CLIP image embedding")
    try:
        import torch
        from PIL import Image
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "torch + Pillow are required for CLIP image embeddings"
        ) from exc

    bundle = _load_clip(
        model_name=model_name,
        pretrained=pretrained,
        device=device,
        cache_dir=str(cache_dir) if cache_dir else None,
    )
    image = Image.open(io.BytesIO(data)).convert("RGB")
    tensor = bundle.preprocess(image).unsqueeze(0).to(bundle.device)
    with torch.no_grad():
        vec = bundle.model.encode_image(tensor)[0]
    return _normalize_vec(vec)


class ClipTextEmbedder:
    """Adapter so RAGService can treat CLIP like a sentence-transformer.

    Exposes `encode(text)` and returns a float list for a single string input.
    """

    def __init__(
        self,
        model_name: str = CLIP_DEFAULT_MODEL,
        *,
        pretrained: str = CLIP_DEFAULT_PRETRAINED,
        cache_dir: Path | None = Path("models/embeddings/clip"),
        device: str = "cpu",
    ) -> None:
        self.model_name = model_name
        self.pretrained = pretrained
        self.cache_dir = cache_dir
        self.device = device

    def encode(self, text: str) -> list[float]:
        return embed_clip_text(
            text,
            model_name=self.model_name,
            pretrained=self.pretrained,
            cache_dir=self.cache_dir,
            device=self.device,
        )
