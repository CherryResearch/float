from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict
import base64
import hmac
import hashlib

try:
    import jwt as _pyjwt  # PyJWT
    _HAVE_PYJWT = hasattr(_pyjwt, "encode") and hasattr(_pyjwt, "decode")
except Exception:  # pragma: no cover - allow tests to stub jwt
    _pyjwt = None
    _HAVE_PYJWT = False
try:
    import jwt as _jwt_mod  # for InvalidTokenError symbol if available
except Exception:  # pragma: no cover
    _jwt_mod = None

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "sync_destinations.json"
)


@dataclass
class SyncDestination:
    """Configuration for a manual sync destination."""

    name: str
    url: str


class SyncService:
    """Handle synchronization over various transports.

    The service currently provides stub implementations for Wi-Fi,
    SSH, and a cloud relay.  All operations require a valid JWT token
    and use TLS contexts to ensure encrypted channels.  The actual
    network transfer is left as a TODO for future work.
    """

    def __init__(
        self,
        secret_key: str,
        config_path: Path | None = None,
    ) -> None:
        self.secret_key = secret_key
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self.destinations: Dict[str, str] = {}
        self._load_destinations()

    # ------------------------------------------------------------------
    # Destination management
    # ------------------------------------------------------------------
    def _load_destinations(self) -> None:
        if self.config_path.exists():
            try:
                self.destinations = json.loads(self.config_path.read_text())
            except Exception:
                self.destinations = {}
        else:
            self.destinations = {}

    def _save_destinations(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(self.destinations, indent=2))

    def add_destination(self, name: str, url: str) -> None:
        self.destinations[name] = url
        self._save_destinations()

    def remove_destination(self, name: str) -> None:
        if name in self.destinations:
            self.destinations.pop(name)
            self._save_destinations()

    def list_destinations(self) -> Dict[str, str]:
        return dict(self.destinations)

    # ------------------------------------------------------------------
    # Authentication helpers
    # ------------------------------------------------------------------
    def generate_token(self, payload: Dict[str, Any]) -> str:
        if _HAVE_PYJWT:
            return _pyjwt.encode(payload, self.secret_key, algorithm="HS256")  # type: ignore[attr-defined]
        # Minimal JWT HS256 implementation (for test environments without PyJWT)
        header = {"alg": "HS256", "typ": "JWT"}
        def b64url(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")
        header_b64 = b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        payload_b64 = b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        sig = hmac.new(self.secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
        return f"{header_b64}.{payload_b64}.{b64url(sig)}"

    def verify_token(self, token: str) -> Dict[str, Any]:
        if _HAVE_PYJWT:
            return _pyjwt.decode(token, self.secret_key, algorithms=["HS256"])  # type: ignore[attr-defined]
        try:
            header_b64, payload_b64, sig_b64 = token.split(".")
        except ValueError:
            err = getattr(_jwt_mod, "InvalidTokenError", ValueError)
            raise err("Invalid token format")
        def b64url_decode(s: str) -> bytes:
            pad = "=" * (-len(s) % 4)
            return base64.urlsafe_b64decode((s + pad).encode("ascii"))
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        expected = hmac.new(self.secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
        actual = b64url_decode(sig_b64)
        if not hmac.compare_digest(expected, actual):
            err = getattr(_jwt_mod, "InvalidTokenError", ValueError)
            raise err("Invalid signature")
        payload_json = b64url_decode(payload_b64)
        return json.loads(payload_json.decode("utf-8"))

    # ------------------------------------------------------------------
    # Sync operations (stubs)
    # ------------------------------------------------------------------
    def _tls_context(self) -> ssl.SSLContext:
        """Return a permissive TLS context for encrypted channels."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def sync_via_wifi(self, token: str, data: bytes) -> dict:
        self.verify_token(token)
        _ = self._tls_context()
        # TODO: implement real Wi-Fi transfer using TLS context
        return {
            "transport": "wifi",
            "encrypted": True,
            "bytes": len(data),
        }

    async def sync_via_ssh(self, token: str, data: bytes, host: str) -> dict:
        self.verify_token(token)
        _ = self._tls_context()
        # TODO: implement real SSH transfer to ``host`` using TLS context
        return {
            "transport": "ssh",
            "host": host,
            "encrypted": True,
            "bytes": len(data),
        }

    async def sync_via_relay(
        self,
        token: str,
        data: bytes,
        relay_url: str,
    ) -> dict:
        self.verify_token(token)
        _ = self._tls_context()
        # TODO: implement relay transfer to ``relay_url`` using TLS context
        return {
            "transport": "relay",
            "url": relay_url,
            "encrypted": True,
            "bytes": len(data),
        }
