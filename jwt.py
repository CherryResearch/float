"""Minimal JWT HS256 implementation for test environments.

This module provides ``encode`` and ``decode`` compatible with a subset of the
PyJWT interface and defines ``InvalidTokenError`` so tests can import it.

It is NOT a full JWT implementation and should not be used for production
security-sensitive code outside this project.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any, Dict, Iterable


class InvalidTokenError(Exception):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def encode(payload: Dict[str, Any], key: str, algorithm: str = "HS256") -> str:
    if algorithm != "HS256":
        raise ValueError("Only HS256 supported in test jwt")
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url(sig)}"


def decode(token: str, key: str, algorithms: Iterable[str] | None = None) -> Dict[str, Any]:
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError as e:
        raise InvalidTokenError("Invalid token format") from e
    if algorithms and "HS256" not in algorithms:
        raise InvalidTokenError("Unsupported algorithm")
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    actual = _b64url_decode(sig_b64)
    if not hmac.compare_digest(expected, actual):
        raise InvalidTokenError("Invalid signature")
    payload_json = _b64url_decode(payload_b64)
    return json.loads(payload_json.decode("utf-8"))

