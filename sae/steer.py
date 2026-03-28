from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any

from .hooks import RuntimeUnavailableError, run_transformers_with_steering
from .io import load_activation_record, load_sae_weights, save_activation_record
from .model import decode_sparse, l2_norm, make_identity_sae
from .types import ActivationRecord, SteeringConfig


def parse_feature_overrides(raw: str) -> dict[int, float]:
    """Parse `feature:alpha,feature:alpha` into a dict."""

    features: dict[int, float] = {}
    text = raw.strip()
    if not text:
        return features
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(
                f"Invalid feature override '{chunk}'. Expected 'feature_id:alpha'."
            )
        feature_text, alpha_text = chunk.split(":", 1)
        features[int(feature_text.strip())] = float(alpha_text.strip())
    return features


def resolve_token_positions(token_positions: str, seq_len: int) -> list[int]:
    mode = token_positions.strip().lower()
    if mode == "all":
        return list(range(seq_len))
    if mode == "last":
        return [seq_len - 1] if seq_len > 0 else []

    positions: list[int] = []
    for chunk in mode.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        index = int(chunk)
        if index < 0:
            index = seq_len + index
        if 0 <= index < seq_len:
            positions.append(index)
    return sorted(set(positions))


def steering_delta(decoder: list[list[float]], features: dict[int, float]) -> list[float]:
    sparse = [(int(feature_id), float(alpha)) for feature_id, alpha in features.items()]
    return decode_sparse(sparse_features=sparse, decoder=decoder)


def apply_steering_to_hidden(
    hidden_states: list[list[float]],
    decoder: list[list[float]],
    config: SteeringConfig,
    layer: int | None = None,
) -> tuple[list[list[float]], dict[str, Any]]:
    """Apply `h <- h + Σ alpha_i * decoder[i]` on selected token positions."""

    if config.layer is not None and layer is not None and config.layer != layer:
        return copy.deepcopy(hidden_states), {
            "applied": False,
            "reason": f"layer mismatch (target={config.layer}, current={layer})",
            "positions": [],
            "delta_l2": 0.0,
        }

    seq_len = len(hidden_states)
    positions = resolve_token_positions(config.token_positions, seq_len=seq_len)
    delta = steering_delta(decoder=decoder, features=config.features)
    delta_norm = l2_norm(delta)

    if config.dry_run:
        return copy.deepcopy(hidden_states), {
            "applied": False,
            "reason": "dry_run",
            "positions": positions,
            "delta_l2": delta_norm,
        }

    if not positions:
        return copy.deepcopy(hidden_states), {
            "applied": False,
            "reason": "no token positions resolved",
            "positions": [],
            "delta_l2": delta_norm,
        }

    result = copy.deepcopy(hidden_states)
    for position in positions:
        if position < 0 or position >= len(result):
            continue
        row = result[position]
        if len(row) != len(delta):
            raise ValueError(
                f"Hidden width ({len(row)}) must match decoder width ({len(delta)})."
            )
        for idx, value in enumerate(delta):
            row[idx] += value

    before = l2_norm([value for row in hidden_states for value in row])
    after = l2_norm([value for row in result for value in row])
    return result, {
        "applied": True,
        "positions": positions,
        "delta_l2": delta_norm,
        "global_norm_before": before,
        "global_norm_after": after,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SAE feature steering scaffold.")
    parser.add_argument(
        "--features",
        required=True,
        help="Comma-separated overrides, e.g. 123:+0.8,91:-0.4",
    )
    parser.add_argument("--layer", type=int, default=None, help="Layer index to target.")
    parser.add_argument(
        "--token-positions",
        default="all",
        help="Token selection: all | last | comma-list (e.g., 0,3,-1).",
    )
    parser.add_argument("--sae-weights", help="Path to SAE weights JSON (decoder source).")
    parser.add_argument(
        "--activations",
        help="Offline activation record path (.json/.npz/.parquet).",
    )
    parser.add_argument(
        "--out",
        help="Optional output path for steered activation record when using --activations.",
    )
    parser.add_argument("--model", help="Live model id (future-ready path).")
    parser.add_argument("--prompt", default="", help="Prompt text for live model path.")
    parser.add_argument("--module-target", default="resid_post", help="Hook target module.")
    parser.add_argument("--device", default="cpu", help="Torch device for live model path.")
    parser.add_argument("--max-new-tokens", type=int, default=48, help="Generation length.")
    parser.add_argument("--dry-run", action="store_true", help="Print interventions only.")
    return parser.parse_args()


def _build_config(args: argparse.Namespace) -> SteeringConfig:
    features = parse_feature_overrides(args.features)
    return SteeringConfig(
        features=features,
        layer=args.layer,
        token_positions=args.token_positions,
        dry_run=args.dry_run,
    )


def _load_decoder_for_record(args: argparse.Namespace, record: ActivationRecord) -> list[list[float]]:
    if args.sae_weights:
        return load_sae_weights(args.sae_weights).decoder
    return make_identity_sae(d_model=record.d_model).decoder


def _offline_path(args: argparse.Namespace, config: SteeringConfig) -> int:
    record = load_activation_record(args.activations)
    decoder = _load_decoder_for_record(args, record)

    if config.dry_run:
        delta = steering_delta(decoder, config.features)
        print("steering dry-run (offline)")
        print(f"  layer={config.layer}")
        print(f"  token_positions={config.token_positions}")
        print(f"  features={config.features}")
        print(f"  delta_l2={l2_norm(delta):.6f}")
        return 0

    steered, report = apply_steering_to_hidden(
        hidden_states=record.activations,
        decoder=decoder,
        config=config,
        layer=config.layer,
    )
    print("steering applied (offline)")
    print(f"  positions={report.get('positions')}")
    print(f"  delta_l2={report.get('delta_l2', 0.0):.6f}")
    print(
        f"  global_norm={report.get('global_norm_before', 0.0):.6f}"
        f" -> {report.get('global_norm_after', 0.0):.6f}"
    )

    if args.out:
        steered_record = ActivationRecord(
            model_name=record.model_name,
            layer_name=record.layer_name,
            token_ids=record.token_ids,
            tokens=record.tokens,
            activations=steered,
            metadata={
                **record.metadata,
                "steering_features": config.features,
                "steering_token_positions": config.token_positions,
            },
        )
        save_activation_record(steered_record, args.out)
        print(f"  wrote={Path(args.out)}")
    return 0


def _live_path(args: argparse.Namespace, config: SteeringConfig) -> int:
    if not args.model:
        raise ValueError("Live steering requires --model.")
    if not args.prompt:
        raise ValueError("Live steering requires --prompt.")
    if not args.sae_weights:
        raise ValueError("Live steering requires --sae-weights for decoder directions.")

    decoder = load_sae_weights(args.sae_weights).decoder
    result = run_transformers_with_steering(
        model_name=args.model,
        prompt=args.prompt,
        layer=config.layer if config.layer is not None else 0,
        module_target=args.module_target,
        config=config,
        decoder=decoder,
        device=args.device,
        max_new_tokens=args.max_new_tokens,
    )
    if result.get("dry_run"):
        print("steering dry-run (live)")
        print(f"  model={result['model_name']}")
        print(f"  layer={result['layer_name']}")
        print(f"  features={result['features']}")
        print(f"  token_positions={result['token_positions']}")
        return 0

    print("steering applied (live)")
    print(f"  model={result['model_name']}")
    print(f"  layer={result['layer_name']}")
    print(f"  positions={result.get('applied_positions')}")
    print("  output:")
    print(result["text"])
    return 0


def main() -> int:
    args = _parse_args()
    config = _build_config(args)
    try:
        if args.activations:
            return _offline_path(args, config)
        return _live_path(args, config)
    except RuntimeUnavailableError as exc:
        print(f"runtime unavailable: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"steer failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
