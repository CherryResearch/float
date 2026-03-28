"""Utility tokenizer based on Harmony encodings.

This module provides a thin wrapper around the ``openai_harmony``
``load_harmony_encoding`` helper so that other components can encode and
decode text using the ``o200k_harmony`` scheme.  The previous implementation
relied on custom special tokens (e.g. ``<COT>`` markers); with the shift to
Harmony channels those tokens are no longer required.
"""

from __future__ import annotations

from app.utils.harmony import HarmonyEncodingName, load_harmony_encoding


class CustomTokenizer:
    """Wrapper around ``openai_harmony`` encodings."""

    _tokenizer = None

    def __init__(self) -> None:  # pragma: no cover - trivial
        if CustomTokenizer._tokenizer is None:
            try:
                CustomTokenizer._tokenizer = load_harmony_encoding(
                    "o200k_harmony",
                )
            except Exception:  # pragma: no cover - fallback for older libs
                CustomTokenizer._tokenizer = load_harmony_encoding(
                    HarmonyEncodingName.HARMONY_GPT_OSS
                )
        self.tokenizer = CustomTokenizer._tokenizer

    def encode(self, text: str) -> list[int]:
        """Convert *text* to a list of token IDs."""

        return list(self.tokenizer.encode(text))

    def decode(self, tokens: list[int]) -> str:
        """Convert token IDs back to text."""

        return self.tokenizer.decode(tokens)


# Harmony-style channel and turn markers (retained for compatibility)
USER_START = "<|user|>"
ASSISTANT_START = "<|assistant|>"
SYSTEM_START = "<|system|>"
TURN_END = "<|eot|>"
