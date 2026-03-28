"""Compatibility shim for Harmony message and encoding types.

This module attempts to import symbols from the ``openai_harmony`` package.
When the package is unavailable (or tests stub it out), we provide minimal
fallback implementations that mimic the small surface area used by the codebase:

- Message.from_role_and_content(role, content)
- Message.to_dict()
- Message.with_channel(channel)
- Role with USER/ASSISTANT/SYSTEM constants
- load_harmony_encoding / HarmonyEncodingName used by the tokenizer
"""

from __future__ import annotations

from typing import Any, List

try:  # Prefer the real implementation when available
    from openai_harmony import (  # type: ignore
        Message as _HarmonyMessage,
        Role as _HarmonyRole,
        HarmonyEncodingName as _HarmonyEncodingName,
        load_harmony_encoding as _load_harmony_encoding,
    )

    # Some test setups may stub these as None; guard against that.
    if _HarmonyMessage is not None and _HarmonyRole is not None:
        Message = _HarmonyMessage
        Role = _HarmonyRole
        HarmonyEncodingName = _HarmonyEncodingName
        load_harmony_encoding = _load_harmony_encoding
    else:
        raise ImportError("harmony symbols are None (test stub)")
except Exception:  # Fallback minimal shims

    class Role:  # type: ignore
        USER = "user"
        ASSISTANT = "assistant"
        SYSTEM = "system"

    class _Part:
        def __init__(self, text: str):
            self.type = "text"
            self.text = text

        def to_dict(self) -> dict:
            return {"type": self.type, "text": self.text}

    class _Author:
        def __init__(self, role: str):
            self.role = role

    class Message:  # type: ignore
        def __init__(self, role: str, content: List[_Part]):
            self.author = _Author(role)
            self.content = content
            self.channel = None

        @staticmethod
        def from_role_and_content(role: str, content: Any) -> "Message":
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(_Part(item.get("text", "")))
                    elif isinstance(item, str):
                        parts.append(_Part(item))
                return Message(role, parts)
            return Message(role, [_Part(str(content))])

        def with_channel(self, ch: str) -> "Message":
            self.channel = ch
            return self

        def to_dict(self) -> dict:
            # Minimal mapping used by Chat Completions compatibility
            # Prefer text parts flattened
            text = "".join(p.text for p in self.content)
            return {"role": self.author.role, "content": text}

    # Tokenizer fallbacks: provide no-op encoding/decoding
    class HarmonyEncodingName:  # type: ignore
        HARMONY_GPT_OSS = "o200k_harmony"

    def load_harmony_encoding(name: str):  # type: ignore
        class _Tok:
            def encode(self, s: str):
                return [ord(c) for c in s]

            def decode(self, ids: List[int]):
                return "".join(chr(i) for i in ids)

        return _Tok()

