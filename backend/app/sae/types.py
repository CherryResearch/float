from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _copy_2d(values: list[list[float]]) -> list[list[float]]:
    return [[float(item) for item in row] for row in values]


@dataclass
class ActivationRecord:
    """Activation capture snapshot for a single prompt at one layer."""

    model_name: str
    layer_name: str
    token_ids: list[int]
    activations: list[list[float]]
    tokens: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.token_ids = [int(token_id) for token_id in self.token_ids]
        self.activations = _copy_2d(self.activations)
        self.tokens = [str(token) for token in self.tokens]
        if self.tokens and len(self.tokens) != len(self.token_ids):
            raise ValueError("tokens and token_ids must have identical lengths.")
        if len(self.activations) != len(self.token_ids):
            raise ValueError("activations must have one row per token id.")

    @property
    def token_count(self) -> int:
        return len(self.token_ids)

    @property
    def d_model(self) -> int:
        if not self.activations:
            return 0
        return len(self.activations[0])

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "activation_record.v1",
            "model_name": self.model_name,
            "layer_name": self.layer_name,
            "token_ids": self.token_ids,
            "tokens": self.tokens,
            "activations": self.activations,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ActivationRecord":
        tokens = payload.get("tokens") or []
        return cls(
            model_name=str(payload["model_name"]),
            layer_name=str(payload["layer_name"]),
            token_ids=list(payload["token_ids"]),
            tokens=list(tokens),
            activations=list(payload["activations"]),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass
class SAEWeights:
    """Simple SAE weight container.

    `encoder` and `decoder` are shaped `[n_features][d_model]`.
    """

    encoder: list[list[float]]
    decoder: list[list[float]]
    encoder_bias: list[float] = field(default_factory=list)
    feature_labels: dict[int, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.encoder = _copy_2d(self.encoder)
        self.decoder = _copy_2d(self.decoder)
        self.encoder_bias = [float(value) for value in self.encoder_bias]
        self.feature_labels = {int(k): str(v) for k, v in self.feature_labels.items()}

        if len(self.encoder) != len(self.decoder):
            raise ValueError("encoder and decoder must have the same number of features.")
        if self.encoder:
            d_model = len(self.encoder[0])
            if any(len(row) != d_model for row in self.encoder):
                raise ValueError("encoder rows must share one d_model width.")
            if any(len(row) != d_model for row in self.decoder):
                raise ValueError("decoder rows must share one d_model width.")
        if not self.encoder_bias:
            self.encoder_bias = [0.0] * len(self.encoder)
        if len(self.encoder_bias) != len(self.encoder):
            raise ValueError("encoder_bias length must match number of features.")

    @property
    def n_features(self) -> int:
        return len(self.encoder)

    @property
    def d_model(self) -> int:
        if not self.encoder:
            return 0
        return len(self.encoder[0])

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "sae_weights.v1",
            "encoder": self.encoder,
            "decoder": self.decoder,
            "encoder_bias": self.encoder_bias,
            "feature_labels": {str(key): value for key, value in self.feature_labels.items()},
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SAEWeights":
        labels_payload = payload.get("feature_labels") or {}
        labels = {int(k): str(v) for k, v in labels_payload.items()}
        return cls(
            encoder=list(payload["encoder"]),
            decoder=list(payload["decoder"]),
            encoder_bias=list(payload.get("encoder_bias") or []),
            feature_labels=labels,
            metadata=dict(payload.get("metadata") or {}),
        )

    def label_for(self, feature_id: int) -> str | None:
        return self.feature_labels.get(int(feature_id))


@dataclass
class SteeringConfig:
    """Inference-time feature intervention settings."""

    features: dict[int, float]
    layer: int | None = None
    token_positions: str = "all"
    dry_run: bool = False

    def __post_init__(self) -> None:
        self.features = {int(feature_id): float(alpha) for feature_id, alpha in self.features.items()}
        self.token_positions = str(self.token_positions).strip() or "all"
