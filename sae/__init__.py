"""Sparse autoencoder inspection and steering scaffolds.

This package is intentionally lightweight:
- Works now with offline activation files (JSON).
- Supports NPZ/Parquet when optional deps are installed.
- Includes forward-hook scaffolding for Hugging Face transformers runtimes.
"""

from .types import ActivationRecord, SAEWeights, SteeringConfig

__all__ = ["ActivationRecord", "SAEWeights", "SteeringConfig"]
