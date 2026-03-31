from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .hooks import RuntimeUnavailableError, capture_transformers_activations
from .io import load_activation_record, load_sae_weights, save_activation_record
from .model import encode_topk, make_identity_sae
from .types import ActivationRecord, SAEWeights


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect sparse SAE features from layer activations."
    )
    parser.add_argument("--model", default="gpt-oss-20b", help="Model ID (or metadata label).")
    parser.add_argument("--layer", required=True, help="Layer index or name.")
    parser.add_argument("--prompt", default="", help="Prompt text for live capture.")
    parser.add_argument("--topk", type=int, default=20, help="Top active features to print.")
    parser.add_argument(
        "--l0-threshold",
        type=float,
        default=0.0,
        help="Drop features at or below this activation value.",
    )
    parser.add_argument(
        "--activations",
        help="Path to offline activation record (.json/.npz/.parquet).",
    )
    parser.add_argument("--record-out", help="Write captured live activations to this path.")
    parser.add_argument("--sae-weights", help="Path to SAE weights JSON.")
    parser.add_argument(
        "--feature-labels",
        help="Optional JSON mapping feature_id -> label; overrides labels in weights.",
    )
    parser.add_argument(
        "--module-target",
        default="resid_post",
        help="Live capture hook target (resid_post or mlp where supported).",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        help="Torch device for live capture (future path for larger models).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned action without running live hooks.",
    )
    return parser.parse_args()


def _load_feature_labels(path: str | None) -> dict[int, str]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {int(key): str(value) for key, value in payload.items()}


def _resolve_record(args: argparse.Namespace) -> ActivationRecord:
    if args.activations:
        return load_activation_record(args.activations)

    if args.dry_run:
        print("inspect dry-run")
        print(f"  model={args.model}")
        print(f"  layer={args.layer}")
        print(f"  module_target={args.module_target}")
        print("  action=capture_transformers_activations")
        raise SystemExit(0)

    try:
        layer_index = int(args.layer)
    except ValueError as exc:
        raise ValueError(
            "Live capture requires numeric --layer. Use --activations for named layers."
        ) from exc

    record = capture_transformers_activations(
        model_name=args.model,
        prompt=args.prompt,
        layer=layer_index,
        module_target=args.module_target,
        device=args.device,
    )
    if args.record_out:
        save_activation_record(record, args.record_out)
        print(f"saved activations: {args.record_out}")
    return record


def _resolve_weights(record: ActivationRecord, args: argparse.Namespace) -> SAEWeights:
    if args.sae_weights:
        weights = load_sae_weights(args.sae_weights)
    else:
        # Placeholder fallback keeps the pipeline usable before real SAE weights exist.
        weights = make_identity_sae(d_model=record.d_model)

    labels = _load_feature_labels(args.feature_labels)
    if labels:
        weights.feature_labels.update(labels)
    return weights


def _token_text(record: ActivationRecord, idx: int) -> str:
    if idx < len(record.tokens) and record.tokens[idx]:
        return record.tokens[idx]
    return f"<id:{record.token_ids[idx]}>"


def _print_trace(record: ActivationRecord, weights: SAEWeights, topk: int, threshold: float) -> None:
    print("=== SAE inspection trace ===")
    print(f"model: {record.model_name}")
    print(f"layer: {record.layer_name}")
    print(f"tokens: {record.token_count}")
    print(f"sae_features: {weights.n_features}")
    print(f"d_model: {record.d_model}")
    print("")

    for token_index, hidden in enumerate(record.activations):
        token = _token_text(record, token_index).replace("\n", "\\n")
        token_id = record.token_ids[token_index]
        active = encode_topk(
            vector=hidden,
            weights=weights,
            topk=topk,
            l0_threshold=threshold,
        )
        print(f"[{token_index:03d}] token={token!r} id={token_id}")
        if not active:
            print("  (no active features)")
            continue
        for rank, (feature_id, activation) in enumerate(active, start=1):
            label = weights.label_for(feature_id)
            label_suffix = f" | {label}" if label else ""
            print(f"  {rank:02d}. f{feature_id:<6d} {activation:+.6f}{label_suffix}")
        print("")


def main() -> int:
    args = _parse_args()
    try:
        record = _resolve_record(args)
        weights = _resolve_weights(record, args)
        _print_trace(record, weights, topk=args.topk, threshold=args.l0_threshold)
        return 0
    except RuntimeUnavailableError as exc:
        print(f"runtime unavailable: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"inspect failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
