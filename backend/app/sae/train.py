from __future__ import annotations

import argparse
import random

from .io import load_activation_record, save_sae_weights
from .model import normalize
from .types import SAEWeights


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tiny SAE trainer scaffold.")
    parser.add_argument(
        "--activations", required=True, help="Activation record input path."
    )
    parser.add_argument("--out", required=True, help="Output SAE weights JSON path.")
    parser.add_argument(
        "--features", type=int, default=256, help="Number of SAE features."
    )
    parser.add_argument(
        "--seed", type=int, default=0, help="Random seed for deterministic init."
    )
    parser.add_argument(
        "--strategy",
        default="exemplar",
        choices=("exemplar",),
        help="Training strategy. 'exemplar' is lightweight and dependency-free.",
    )
    return parser.parse_args()


def _build_exemplar_dictionary(
    activations: list[list[float]],
    n_features: int,
    seed: int,
) -> list[list[float]]:
    if not activations:
        raise ValueError("No activations provided.")
    rng = random.Random(seed)
    total = len(activations)
    decoder: list[list[float]] = []
    for feature_index in range(n_features):
        # Lightweight placeholder strategy: choose exemplar vectors and normalize.
        jitter = rng.randint(0, max(0, total - 1))
        index = (feature_index * max(1, total // n_features) + jitter) % total
        decoder.append(normalize([float(value) for value in activations[index]]))
    return decoder


def main() -> int:
    args = _parse_args()
    record = load_activation_record(args.activations)

    decoder = _build_exemplar_dictionary(
        activations=record.activations,
        n_features=args.features,
        seed=args.seed,
    )
    encoder = [row[:] for row in decoder]
    bias = [0.0] * len(decoder)
    weights = SAEWeights(
        encoder=encoder,
        decoder=decoder,
        encoder_bias=bias,
        metadata={
            "trainer": "app.sae.train",
            "strategy": args.strategy,
            "note": (
                "Placeholder dependency-free trainer. "
                "Swap with full torch SAE training when large-model activations are available."
            ),
            "source_model": record.model_name,
            "source_layer": record.layer_name,
        },
    )
    save_sae_weights(weights, args.out)

    print("sae train complete")
    print(f"  activations={args.activations}")
    print(f"  out={args.out}")
    print(f"  features={args.features}")
    print("  strategy=exemplar (placeholder)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
