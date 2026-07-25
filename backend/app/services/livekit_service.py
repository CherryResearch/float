import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Protocol

import jwt
import requests
from app.model_registry import model_supports_images

DEFAULT_REALTIME_SESSION_URL = "https://api.openai.com/v1/realtime/client_secrets"
DEFAULT_REALTIME_CONNECT_URL = "https://api.openai.com/v1/realtime/calls"
DEFAULT_REALTIME_MODEL = "gpt-realtime-2.1"
DEFAULT_REALTIME_VOICE = "alloy"
DEFAULT_REALTIME_TRANSCRIPTION_MODEL = "gpt-realtime-whisper"
DEFAULT_LIVE_AGENT_MODE = "local"
REALTIME_TRANSCRIPTION_MODELS = {
    "gpt-realtime-whisper",
    "gpt-4o-transcribe",
    "gpt-4o-mini-transcribe",
}
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


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalize_stream_backend(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"api", "livekit", "local"}:
        return raw
    return "api"


def _normalize_live_agent_mode(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"api", "local", "server"}:
        return raw
    return DEFAULT_LIVE_AGENT_MODE


def _realtime_transcription_model_from_stt(value: Any) -> str:
    raw = str(value or "").strip()
    normalized = raw.lower()
    if not normalized:
        return ""
    if normalized.startswith("api:"):
        raw = raw[4:].strip()
        normalized = raw.lower()
    if normalized in REALTIME_TRANSCRIPTION_MODELS:
        return raw
    if normalized.startswith("gpt-4o") and "transcribe" in normalized:
        return raw
    return ""


def _normalize_realtime_reasoning_effort(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"low", "medium", "high", "xhigh"}:
        return raw
    return ""


def _realtime_model_supports_reasoning(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized.startswith("gpt-realtime-2")


def _normalize_realtime_tracing(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"auto", "true", "1", "yes", "on"}:
        return "auto"
    return ""


class LiveSessionTransportAdapter(Protocol):
    def connect(
        self, identity: str, room: str, *, reasoning_effort: str | None = None
    ) -> Dict[str, Any]:
        """Return transport-specific connection details."""


class OpenAIRealtimeTransportAdapter:
    def __init__(self, service: "LiveKitService") -> None:
        self.service = service

    def connect(
        self, identity: str, room: str, *, reasoning_effort: str | None = None
    ) -> Dict[str, Any]:
        return self.service._create_realtime_session(
            identity, room, reasoning_effort=reasoning_effort
        )


class LiveKitTransportAdapter:
    def __init__(self, service: "LiveKitService") -> None:
        self.service = service

    def connect(
        self, identity: str, room: str, *, reasoning_effort: str | None = None
    ) -> Dict[str, Any]:
        self.service.create_room(room)
        token = self.service.generate_token(identity, room)
        return {
            "provider": "livekit",
            "url": self.service.url,
            "token": token,
            "transport": "livekit",
            "source": "live",
        }


class LocalBridgeTransportAdapter:
    def __init__(self, service: "LiveKitService") -> None:
        self.service = service

    def connect(
        self, identity: str, room: str, *, reasoning_effort: str | None = None
    ) -> Dict[str, Any]:
        return {
            "provider": "float-local-live",
            "transport": "local-bridge",
            "source": "live",
            "session_id": f"local-live-{uuid.uuid4()}",
            "identity": identity,
            "room": room,
            "status": "planned",
            "detail": (
                "Local live bridge is selected, but browser duplex audio is not wired yet."
            ),
            "mode": self.service.live_agent_mode,
            "response_model": self.service._resolve_live_response_model(
                "float-local-live"
            ),
        }


class LiveKitService:
    """Live streaming helper that supports LiveKit or OpenAI Realtime."""

    def __init__(self, config: dict):
        self.mode = _normalize_stream_backend(config.get("stream_backend"))
        self.rooms: set[str] = set()
        self.config = config

        # LiveKit specific configuration
        self.api_key = config.get("livekit_api_key", "")
        self.secret = config.get("livekit_secret", "")
        self.url = config.get("livekit_url", "ws://localhost:7880")

        # OpenAI Realtime specific configuration
        self.openai_api_key = config.get("api_key") or os.getenv("OPENAI_API_KEY", "")
        self.realtime_model = _first_non_empty(
            config.get("realtime_model"), DEFAULT_REALTIME_MODEL
        )
        self.realtime_voice = config.get(
            "realtime_voice",
            config.get("voice_model", DEFAULT_REALTIME_VOICE),
        )
        self.live_agent_mode = _normalize_live_agent_mode(config.get("live_agent_mode"))
        self.live_agent_model = _first_non_empty(config.get("live_agent_model"))
        self.live_multimodal_model = _first_non_empty(
            config.get("live_multimodal_model")
        )
        self.caption_model = _first_non_empty(config.get("vision_model"))
        self.provider_preferred_model = _first_non_empty(
            config.get("local_provider_preferred_model")
        )
        self.realtime_base_url = config.get(
            "realtime_base_url", DEFAULT_REALTIME_SESSION_URL
        )
        self.realtime_connect_url = config.get(
            "realtime_connect_url", DEFAULT_REALTIME_CONNECT_URL
        )
        explicit_realtime_stt = str(
            config.get("realtime_transcription_model")
            or os.getenv("OPENAI_REALTIME_TRANSCRIPTION_MODEL", "")
        ).strip()
        inherited_realtime_stt = _realtime_transcription_model_from_stt(
            config.get("stt_model")
        )
        self.realtime_transcription_model = (
            explicit_realtime_stt
            or inherited_realtime_stt
            or DEFAULT_REALTIME_TRANSCRIPTION_MODEL
        )
        self.realtime_reasoning_effort = _normalize_realtime_reasoning_effort(
            config.get("realtime_reasoning_effort")
            or os.getenv("OPENAI_REALTIME_REASONING_EFFORT", "")
        )
        self.realtime_tracing = _normalize_realtime_tracing(
            config.get("realtime_tracing") or os.getenv("OPENAI_REALTIME_TRACING", "")
        )
        self.realtime_transcription_logprobs = bool(
            config.get("realtime_transcription_logprobs")
            or str(os.getenv("OPENAI_REALTIME_TRANSCRIPTION_LOGPROBS", ""))
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
        )
        self.realtime_timeout = int(os.getenv("OPENAI_REALTIME_TIMEOUT", "10"))
        self.realtime_ttl_seconds = int(os.getenv("OPENAI_REALTIME_TTL_SECONDS", "600"))
        self.realtime_turn_detection = (
            os.getenv("OPENAI_REALTIME_TURN_DETECTION", "server_vad").strip()
            or "server_vad"
        )
        self._adapters: dict[str, LiveSessionTransportAdapter] = {
            "api": OpenAIRealtimeTransportAdapter(self),
            "local": LocalBridgeTransportAdapter(self),
            "livekit": LiveKitTransportAdapter(self),
        }

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
    # Runtime profile helpers
    def _resolve_live_response_model(self, transport_provider: str) -> str:
        explicit = self.live_agent_model
        if explicit:
            return explicit
        if transport_provider == "openai-realtime":
            return _first_non_empty(self.realtime_model, DEFAULT_REALTIME_MODEL)
        return self.provider_preferred_model

    def _resolve_live_multimodal_model(self, response_model: str) -> str:
        if self.live_multimodal_model:
            return self.live_multimodal_model
        if response_model and model_supports_images(response_model):
            return response_model
        if (
            self.provider_preferred_model
            and self.provider_preferred_model != response_model
            and model_supports_images(self.provider_preferred_model)
        ):
            return self.provider_preferred_model
        return ""

    def _build_live_runtime_profile(
        self,
        *,
        transport_backend: str,
        transport_provider: str,
        response_mode: str | None = None,
        response_model: str | None = None,
        voice: str | None = None,
    ) -> Dict[str, Any]:
        effective_mode = _normalize_live_agent_mode(
            response_mode
            or (
                "api"
                if transport_provider == "openai-realtime"
                else self.live_agent_mode
            )
        )
        effective_model = _first_non_empty(
            response_model, self._resolve_live_response_model(transport_provider)
        )
        multimodal_model = self._resolve_live_multimodal_model(effective_model)
        runtime: Dict[str, Any] = {
            "source": "live",
            "transport_backend": _normalize_stream_backend(transport_backend),
            "provider": transport_provider,
            "mode": effective_mode,
            "response_model": effective_model,
            "multimodal_model": multimodal_model,
            "caption_model": self.caption_model,
            "stt_model": _first_non_empty(self.config.get("stt_model")),
            "realtime_transcription_model": self.realtime_transcription_model,
            "tts_model": _first_non_empty(self.config.get("tts_model")),
            "voice_model": _first_non_empty(voice, self.config.get("voice_model")),
            "supports_visual_input": bool(multimodal_model or self.caption_model),
        }
        if self.realtime_reasoning_effort:
            runtime["realtime_reasoning_effort"] = self.realtime_reasoning_effort
        return runtime

    def _attach_live_runtime(
        self,
        payload: Dict[str, Any],
        *,
        transport_provider: str,
        transport_backend: str | None = None,
        response_mode: str | None = None,
        response_model: str | None = None,
        voice: str | None = None,
    ) -> Dict[str, Any]:
        enriched = dict(payload)
        runtime = self._build_live_runtime_profile(
            transport_backend=transport_backend or self.mode,
            transport_provider=transport_provider,
            response_mode=response_mode,
            response_model=response_model,
            voice=voice,
        )
        enriched.setdefault("source", runtime["source"])
        enriched.setdefault(
            "transport",
            "webrtc" if transport_provider == "openai-realtime" else transport_provider,
        )
        enriched["runtime"] = runtime
        enriched["mode"] = runtime["mode"]
        enriched["model"] = runtime["response_model"]
        enriched["response_model"] = runtime["response_model"]
        enriched["multimodal_model"] = runtime["multimodal_model"]
        enriched["caption_model"] = runtime["caption_model"]
        return enriched

    # ------------------------------------------------------------------
    # OpenAI Realtime helpers
    def _normalize_realtime_voice(self, voice: str | None) -> str:
        normalized = str(voice or "").strip().lower()
        if normalized in REALTIME_VOICE_OPTIONS:
            return normalized
        return DEFAULT_REALTIME_VOICE

    def _create_realtime_session(
        self, identity: str, room: str, *, reasoning_effort: str | None = None
    ) -> Dict[str, Any]:
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
        effective_reasoning_effort = (
            _normalize_realtime_reasoning_effort(reasoning_effort)
            or self.realtime_reasoning_effort
        )
        if effective_reasoning_effort and _realtime_model_supports_reasoning(
            self.realtime_model
        ):
            session_payload["reasoning"] = {"effort": effective_reasoning_effort}
        if self.realtime_tracing:
            session_payload["tracing"] = self.realtime_tracing
        if self.realtime_transcription_logprobs:
            session_payload["include"] = ["item.input_audio_transcription.logprobs"]
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
        session_data = (
            data.get("session") if isinstance(data.get("session"), dict) else data
        )
        client_secret = None
        if isinstance(data.get("value"), str):
            client_secret = data["value"]
        elif isinstance(data.get("client_secret"), dict):
            client_secret = data["client_secret"].get("value")
        elif isinstance(data.get("client_secret"), str):
            client_secret = data["client_secret"]
        elif isinstance(session_data, dict) and isinstance(
            session_data.get("client_secret"), dict
        ):
            client_secret = session_data["client_secret"].get("value")
        elif isinstance(session_data, dict) and isinstance(
            session_data.get("client_secret"), str
        ):
            client_secret = session_data["client_secret"]
        if not client_secret:
            raise RuntimeError(
                "OpenAI Realtime session response did not include a client secret"
            )
        client_secret_expires_at = None
        if isinstance(session_data, dict) and isinstance(
            session_data.get("client_secret"), dict
        ):
            client_secret_expires_at = session_data["client_secret"].get("expires_at")
        result = self._attach_live_runtime(
            {
                "provider": "openai-realtime",
                "url": self.realtime_connect_url,
                "client_secret": client_secret,
                "expires_at": data.get("expires_at")
                or client_secret_expires_at
                or (
                    session_data.get("expires_at")
                    if isinstance(session_data, dict)
                    else None
                ),
                "session": session_data,
                "session_id": (
                    session_data.get("id") if isinstance(session_data, dict) else None
                ),
                "voice": voice,
            },
            transport_provider="openai-realtime",
            response_mode="api",
            response_model=(
                session_data.get("model")
                if isinstance(session_data, dict)
                else self.realtime_model
            ),
            voice=voice,
        )
        runtime = result.get("runtime")
        if (
            isinstance(runtime, dict)
            and effective_reasoning_effort
            and _realtime_model_supports_reasoning(self.realtime_model)
        ):
            runtime["realtime_reasoning_effort"] = effective_reasoning_effort
        return result

    # ------------------------------------------------------------------
    def connect(
        self, identity: str, room: str, *, reasoning_effort: str | None = None
    ) -> Dict[str, Any]:
        """Return connection details for the configured streaming backend."""
        adapter = self._adapters.get(self.mode)
        if adapter is None:
            raise RuntimeError(f"Unsupported live streaming backend: {self.mode}")
        payload = adapter.connect(identity, room, reasoning_effort=reasoning_effort)
        provider = _first_non_empty(payload.get("provider"))
        response_model = _first_non_empty(
            payload.get("response_model"), payload.get("model")
        )
        response_mode = _first_non_empty(payload.get("mode"))
        voice = _first_non_empty(payload.get("voice"))
        if not isinstance(payload.get("runtime"), dict):
            payload = self._attach_live_runtime(
                payload,
                transport_provider=provider or self.mode,
                response_mode=response_mode or None,
                response_model=response_model or None,
                voice=voice or None,
            )
        return payload

    @property
    def is_api_mode(self) -> bool:
        return self.mode == "api"
