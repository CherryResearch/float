import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import requests

import jwt


DEFAULT_REALTIME_SESSION_URL = "https://api.openai.com/v1/realtime/client_secrets"
DEFAULT_REALTIME_CONNECT_URL = "https://api.openai.com/v1/realtime/calls"
DEFAULT_REALTIME_MODEL = "gpt-realtime"
DEFAULT_REALTIME_VOICE = "alloy"
DEFAULT_REALTIME_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
REALTIME_VOICE_OPTIONS = {
    "alloy",
    "ash",
    "ballad",
    "cedar",
    "coral",
    "echo",
    "marin",
    "sage",
    "shimmer",
    "verse",
}


class LiveKitService:
    """Live streaming helper that supports LiveKit or OpenAI Realtime."""

    def __init__(self, config: dict):
        self.mode = (config.get("stream_backend") or "livekit").lower()
        self.rooms: set[str] = set()
        self.config = config

        # LiveKit specific configuration
        self.api_key = config.get("livekit_api_key", "")
        self.secret = config.get("livekit_secret", "")
        self.url = config.get("livekit_url", "ws://localhost:7880")

        # OpenAI Realtime specific configuration
        self.openai_api_key = config.get("api_key") or os.getenv("OPENAI_API_KEY", "")
        self.realtime_model = config.get("realtime_model", DEFAULT_REALTIME_MODEL)
        self.realtime_voice = config.get(
            "realtime_voice",
            config.get("voice_model", DEFAULT_REALTIME_VOICE),
        )
        self.realtime_base_url = config.get(
            "realtime_base_url", DEFAULT_REALTIME_SESSION_URL
        )
        self.realtime_connect_url = config.get(
            "realtime_connect_url", DEFAULT_REALTIME_CONNECT_URL
        )
        self.realtime_transcription_model = (
            str(
                config.get("realtime_transcription_model")
                or os.getenv("OPENAI_REALTIME_TRANSCRIPTION_MODEL", "")
            ).strip()
            or DEFAULT_REALTIME_TRANSCRIPTION_MODEL
        )
        self.realtime_timeout = int(os.getenv("OPENAI_REALTIME_TIMEOUT", "10"))
        self.realtime_ttl_seconds = int(os.getenv("OPENAI_REALTIME_TTL_SECONDS", "600"))
        self.realtime_turn_detection = (
            os.getenv("OPENAI_REALTIME_TURN_DETECTION", "server_vad").strip()
            or "server_vad"
        )

    # ------------------------------------------------------------------
    # LiveKit helpers
    def create_room(self, room: str) -> None:
        """Register a room locally (LiveKit mode only)."""
        if self.mode == "livekit":
            self.rooms.add(room)

    def generate_token(self, identity: str, room: str) -> str:
        """Return a JWT token for connecting to LiveKit."""
        if self.mode != "livekit":  # pragma: no cover - defensive guard
            raise RuntimeError("LiveKit token requested while not in LiveKit mode")
        now = datetime.now(tz=timezone.utc)
        payload = {
            "iss": self.api_key,
            "sub": identity,
            "aud": "livekit",
            "iat": now,
            "exp": now + timedelta(hours=1),
            "nbf": now,
            "jti": str(uuid.uuid4()),
            "video": {
                "room": room,
                "roomJoin": True,
                "canPublish": True,
                "canSubscribe": True,
            },
        }
        return jwt.encode(payload, self.secret, algorithm="HS256")

    # ------------------------------------------------------------------
    # OpenAI Realtime helpers
    def _normalize_realtime_voice(self, voice: str | None) -> str:
        normalized = str(voice or "").strip().lower()
        if normalized in REALTIME_VOICE_OPTIONS:
            return normalized
        return DEFAULT_REALTIME_VOICE

    def _create_realtime_session(self, identity: str, room: str) -> Dict[str, Any]:
        if not self.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for realtime streaming")

        voice = self._normalize_realtime_voice(self.realtime_voice)
        session_payload: Dict[str, Any] = {
            "type": "realtime",
            "model": self.realtime_model,
            "audio": {
                "input": {
                    "turn_detection": {
                        "type": self.realtime_turn_detection,
                        "create_response": False,
                        "interrupt_response": True,
                    },
                    "transcription": {
                        "model": self.realtime_transcription_model,
                    },
                },
                "output": {
                    "voice": voice,
                },
            },
        }
        instructions = str(self.config.get("system_prompt") or "").strip()
        if instructions:
            session_payload["instructions"] = instructions
        payload = {
            "session": session_payload,
        }
        if self.realtime_ttl_seconds > 0:
            payload["expires_after"] = {
                "anchor": "created_at",
                "seconds": self.realtime_ttl_seconds,
            }
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.post(
                self.realtime_base_url,
                headers=headers,
                json=payload,
                timeout=self.realtime_timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            detail = ""
            if exc.response is not None:
                try:
                    detail = exc.response.text
                except Exception:
                    detail = ""
            message = "OpenAI Realtime session creation failed"
            if detail:
                message = f"{message}: {detail}"
            raise RuntimeError(message) from exc

        data = response.json()
        session_data = data.get("session") if isinstance(data.get("session"), dict) else data
        client_secret = None
        if isinstance(data.get("value"), str):
            client_secret = data["value"]
        elif isinstance(data.get("client_secret"), dict):
            client_secret = data["client_secret"].get("value")
        elif isinstance(data.get("client_secret"), str):
            client_secret = data["client_secret"]
        if not client_secret:
            raise RuntimeError("OpenAI Realtime session response did not include a client secret")
        return {
            "provider": "openai-realtime",
            "url": self.realtime_connect_url,
            "client_secret": client_secret,
            "expires_at": data.get("expires_at")
            or (
                session_data.get("expires_at")
                if isinstance(session_data, dict)
                else None
            ),
            "model": (
                session_data.get("model")
                if isinstance(session_data, dict)
                else self.realtime_model
            ),
            "session": session_data,
            "session_id": (
                session_data.get("id") if isinstance(session_data, dict) else None
            ),
            "voice": voice,
        }

    # ------------------------------------------------------------------
    def connect(self, identity: str, room: str) -> Dict[str, Any]:
        """Return connection details for the configured streaming backend."""
        if self.mode == "api":
            return self._create_realtime_session(identity, room)

        # Default to LiveKit for backwards compatibility
        self.create_room(room)
        token = self.generate_token(identity, room)
        return {"provider": "livekit", "url": self.url, "token": token}

    @property
    def is_api_mode(self) -> bool:
        return self.mode == "api"
