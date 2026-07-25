from __future__ import annotations

import os
from typing import List, Optional
from urllib.parse import urlsplit


def configured_cors_origins(value: Optional[str] = None) -> List[str]:
    """Return explicit browser origins allowed to call the backend directly."""

    raw = os.getenv("FLOAT_CORS_ORIGINS", "") if value is None else value
    origins: List[str] = []
    for candidate in str(raw or "").split(","):
        origin = candidate.strip().rstrip("/")
        if not origin:
            continue
        if origin == "*":
            raise ValueError(
                "FLOAT_CORS_ORIGINS must list explicit origins; wildcard CORS is disabled"
            )
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"Invalid CORS origin: {origin}")
        normalized = f"{parsed.scheme}://{parsed.netloc}"
        if normalized not in origins:
            origins.append(normalized)
    return origins


__all__ = ["configured_cors_origins"]
