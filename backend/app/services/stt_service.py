from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from app import config as app_config
from app.model_registry import resolve_model_alias

_PIPELINE_CACHE: Dict[str, Any] = {}

API_STT_MODELS = {
    "whisper-1",
    "gpt-4o-transcribe",
    "gpt-4o-transcribe-latest",
    "gpt-4o-transcribe-diarize",
    "gpt-4o-mini-transcribe",
    "gpt-4o-mini-transcribe-2025-12-15",
    "gpt-realtime-whisper",
}

REALTIME_ONLY_STT_UPLOAD_FALLBACKS = {
    "gpt-realtime-whisper": "gpt-4o-mini-transcribe",
}


@dataclass
class SttResult:
    text: str
    provider: str
    model: str


def is_api_stt_model(model_name: str | None) -> bool:
    raw = str(model_name or "").strip()
    normalized = raw.lower()
    if not normalized:
        return False
    if normalized.startswith("api:"):
        return True
    if normalized in API_STT_MODELS:
        return True
    return normalized.startswith("gpt-") and (
        "transcribe" in normalized or normalized == "gpt-realtime-whisper"
    )


def _strip_model_prefix(model_name: str, prefix: str) -> str:
    if model_name.lower().startswith(prefix):
        return model_name[len(prefix) :].strip()
    return model_name


def _audio_transcriptions_model(model_name: str) -> str:
    normalized = str(model_name or "").strip().lower()
    return REALTIME_ONLY_STT_UPLOAD_FALLBACKS.get(normalized, model_name)


def _resolve_local_model_dir(
    model_name: str, search_dirs: list[Path]
) -> Optional[Path]:
    candidates = [model_name]
    if "/" in model_name:
        candidates.append(model_name.split("/")[-1])
    for root in search_dirs:
        for candidate_name in candidates:
            candidate = root / candidate_name
            if candidate.exists() and candidate.is_dir():
                return candidate
    return None


def _load_pipeline(
    model_id: str,
    *,
    token: Optional[str],
    local_files_only: bool,
    trust_remote_code: bool,
) -> Any:
    if model_id in _PIPELINE_CACHE:
        return _PIPELINE_CACHE[model_id]
    try:
        import torch
        from transformers import pipeline
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("transformers and torch are required for local STT") from exc

    kwargs: Dict[str, Any] = {
        "model": model_id,
        "device": 0 if torch.cuda.is_available() else -1,
        "local_files_only": local_files_only,
    }
    if trust_remote_code:
        kwargs["trust_remote_code"] = True
    if token:
        kwargs["token"] = token

    try:
        pipe = pipeline("automatic-speech-recognition", **kwargs)
    except TypeError:
        if "token" in kwargs:
            kwargs["use_auth_token"] = kwargs.pop("token")
        pipe = pipeline("automatic-speech-recognition", **kwargs)
    _PIPELINE_CACHE[model_id] = pipe
    return pipe


def _guess_suffix(filename: str, content_type: str) -> str:
    suffix = Path(filename or "").suffix
    if suffix:
        return suffix
    normalized_type = str(content_type or "").split(";")[0].strip().lower()
    if normalized_type == "audio/wav":
        return ".wav"
    if normalized_type == "audio/mpeg":
        return ".mp3"
    if normalized_type == "audio/ogg":
        return ".ogg"
    return ".webm"


def _extract_text(result: Any) -> str:
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        return str(result.get("text") or "").strip()
    if isinstance(result, list):
        parts = []
        for item in result:
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                if text:
                    parts.append(text)
        return " ".join(parts).strip()
    return ""


class STTService:
    """Transcribe speech using OpenAI STT models or local Whisper checkpoints."""

    def transcribe(
        self,
        audio: bytes,
        cfg: Optional[Dict[str, Any]] = None,
        *,
        model: Optional[str] = None,
        filename: str = "recording.webm",
        content_type: str = "audio/webm",
    ) -> SttResult:
        if not audio:
            raise ValueError("audio is required for STT")
        cfg = cfg or app_config.load_config()
        raw_model = str(model or cfg.get("stt_model") or "whisper-1").strip()
        stt_model = raw_model or "whisper-1"
        if is_api_stt_model(stt_model):
            return self._transcribe_openai(
                audio,
                cfg,
                model=_strip_model_prefix(stt_model, "api:"),
                filename=filename,
                content_type=content_type,
            )
        return self._transcribe_local(
            audio,
            cfg,
            model=_strip_model_prefix(stt_model, "local:"),
            filename=filename,
            content_type=content_type,
        )

    def _transcribe_openai(
        self,
        audio: bytes,
        cfg: Dict[str, Any],
        *,
        model: str,
        filename: str,
        content_type: str,
    ) -> SttResult:
        api_key = str(cfg.get("api_key") or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for API STT")
        transcription_model = _audio_transcriptions_model(model)
        response = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (filename, audio, content_type)},
            data={"model": transcription_model},
            timeout=60,
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get("error", {}).get("message")
            except Exception:
                detail = response.text
            raise RuntimeError(
                str(detail or "Speech-to-text provider rejected the audio.")
            )
        payload = response.json()
        text = str(payload.get("text") or "").strip()
        return SttResult(text=text, provider="openai", model=transcription_model)

    def _transcribe_local(
        self,
        audio: bytes,
        cfg: Dict[str, Any],
        *,
        model: str,
        filename: str,
        content_type: str,
    ) -> SttResult:
        model_id = resolve_model_alias(model) or model
        search_dirs = app_config.model_search_dirs(cfg.get("models_folder"))
        resolved_dir = _resolve_local_model_dir(model, search_dirs)
        if resolved_dir is None and model_id:
            resolved_dir = _resolve_local_model_dir(str(model_id), search_dirs)
        load_target = str(resolved_dir) if resolved_dir is not None else str(model_id)
        token = (
            str(cfg.get("hf_token") or "").strip()
            or os.getenv("HUGGINGFACE_HUB_TOKEN")
            or os.getenv("HF_TOKEN")
        )
        if token:
            os.environ.setdefault("HUGGINGFACE_HUB_TOKEN", token)
            os.environ.setdefault("HF_TOKEN", token)
        try:
            pipe = _load_pipeline(
                load_target,
                token=token or None,
                local_files_only=True,
                trust_remote_code=bool(cfg.get("allow_remote_code", True)),
            )
        except Exception as exc:
            searched = ", ".join(str(path) for path in search_dirs)
            raise RuntimeError(
                f"Failed to load local STT model '{model}' from '{load_target}'. "
                f"Ensure the model is downloaded (searched: {searched}) and the required "
                "STT dependencies are installed."
            ) from exc

        temp_path = ""
        suffix = _guess_suffix(filename, content_type)
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
                handle.write(audio)
                temp_path = handle.name
            text = _extract_text(pipe(temp_path))
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
        return SttResult(text=text, provider="local", model=model)
