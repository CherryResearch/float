from __future__ import annotations

import math
from typing import Iterable

from .types import SAEWeights


def dot(left: Iterable[float], right: Iterable[float]) -> float:
    return sum(float(a) * float(b) for a, b in zip(left, right))


def l2_norm(vector: Iterable[float]) -> float:
    return math.sqrt(sum(float(value) * float(value) for value in vector))


def normalize(vector: list[float]) -> list[float]:
    norm = l2_norm(vector)
    if norm <= 0.0:
        return [0.0 for _ in vector]
    return [float(value) / norm for value in vector]


def encode_topk(
    vector: list[float],
    weights: SAEWeights,
    topk: int = 20,
    l0_threshold: float = 0.0,
) -> list[tuple[int, float]]:
    """Encode one hidden vector into sparse SAE feature activations."""

    if len(vector) != weights.d_model:
        raise ValueError(
            f"Vector width ({len(vector)}) must equal SAE d_model ({weights.d_model})."
        )
    if topk <= 0:
        return []

    activations: list[tuple[int, float]] = []
    for feature_id, row in enumerate(weights.encoder):
        activation = dot(row, vector) + weights.encoder_bias[feature_id]
        if activation <= l0_threshold:
            continue
        activations.append((feature_id, activation))

    activations.sort(key=lambda item: item[1], reverse=True)
    return activations[:topk]


def decode_sparse(
    sparse_features: Iterable[tuple[int, float]],
    decoder: list[list[float]],
    d_model: int | None = None,
) -> list[float]:
    """Decode sparse features into hidden-state delta."""

    if d_model is None:
        d_model = len(decoder[0]) if decoder else 0
    result = [0.0] * d_model
    for feature_id, value in sparse_features:
        if feature_id < 0 or feature_id >= len(decoder):
            continue
        row = decoder[feature_id]
        for index, basis in enumerate(row):
            result[index] += float(value) * float(basis)
    return result


def make_identity_sae(d_model: int, n_features: int | None = None) -> SAEWeights:
    """Create a deterministic SAE fallback (basis features) for scaffolding."""

    if d_model <= 0:
        raise ValueError("d_model must be positive.")
    feature_count = d_model if n_features is None else int(n_features)
    if feature_count <= 0:
        raise ValueError("n_features must be positive.")

    encoder: list[list[float]] = []
    decoder: list[list[float]] = []
    for feature_id in range(feature_count):
        row = [0.0] * d_model
        row[feature_id % d_model] = 1.0
        encoder.append(row[:])
        decoder.append(row[:])

    return SAEWeights(
        encoder=encoder,
        decoder=decoder,
        encoder_bias=[0.0] * feature_count,
        metadata={"kind": "identity_fallback"},
    )
