from __future__ import annotations

import inspect
import io
import logging
import os
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from app import config as app_config
from app.model_registry import resolve_model_alias

logger = logging.getLogger(__name__)

_PIPELINE_CACHE: Dict[str, Any] = {}
_KITTEN_CACHE: Dict[str, Any] = {}
_KOKORO_CACHE: Dict[str, Any] = {}


@dataclass
class TtsResult:
    audio: bytes
    content_type: str
    provider: str
    model: str
    sample_rate: Optional[int] = None
    voice: Optional[str] = None


def _resolve_local_model_dir(model_name: str, search_dirs: list[Path]) -> Optional[Path]:
    for root in search_dirs:
        candidate = root / model_name
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def _encode_wav(audio: Any, sample_rate: int) -> bytes:
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("numpy is required to encode local TTS audio") from exc

    arr = np.asarray(audio)
    if arr.ndim > 1:
        arr = arr.mean(axis=0)
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype(np.int16)
    with io.BytesIO() as buf:
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(int(sample_rate))
            wav.writeframes(pcm.tobytes())
        return buf.getvalue()


def _is_kitten_model(model_name: str, model_id: Optional[str]) -> bool:
    needle = "kitten"
    return needle in model_name.lower() or (model_id and needle in model_id.lower())


def _is_kokoro_model(model_name: str, model_id: Optional[str]) -> bool:
    needle = "kokoro"
    return needle in model_name.lower() or (model_id and needle in model_id.lower())


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
        raise RuntimeError("transformers and torch are required for local TTS") from exc

    device = 0 if torch.cuda.is_available() else -1
    kwargs: Dict[str, Any] = {
        "model": model_id,
        "device": device,
        "local_files_only": local_files_only,
    }
    if trust_remote_code:
        kwargs["trust_remote_code"] = True
    if token:
        kwargs["token"] = token

    try:
        pipe = pipeline("text-to-speech", **kwargs)
    except TypeError:
        if "token" in kwargs:
            kwargs["use_auth_token"] = kwargs.pop("token")
        pipe = pipeline("text-to-speech", **kwargs)
    _PIPELINE_CACHE[model_id] = pipe
    return pipe


class TTSService:
    """Synthesize speech from text using API or local models."""

    def synthesize(
        self,
        text: str,
        cfg: Optional[Dict[str, Any]] = None,
        *,
        model: Optional[str] = None,
        voice: Optional[str] = None,
        audio_format: str = "wav",
    ) -> TtsResult:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text is required for TTS")
        cfg = cfg or app_config.load_config()
        tts_model = (model or cfg.get("tts_model") or "").strip()
        if not tts_model:
            raise RuntimeError("No TTS model configured")
        voice_model = (voice or cfg.get("voice_model") or "").strip() or None

        if tts_model.lower().startswith("tts-"):
            return self._synthesize_openai(
                text,
                cfg,
                model=tts_model,
                voice=voice_model,
                audio_format=audio_format,
            )

        return self._synthesize_local(
            text,
            cfg,
            model=tts_model,
            voice=voice_model,
            audio_format=audio_format,
        )

    def _synthesize_openai(
        self,
        text: str,
        cfg: Dict[str, Any],
        *,
        model: str,
        voice: Optional[str],
        audio_format: str,
    ) -> TtsResult:
        api_key = cfg.get("api_key")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for API TTS")
        headers = {"Authorization": f"Bearer {api_key}"}
        allowed_voices = {"alloy", "nova", "shimmer", "echo", "fable", "onyx"}
        selected_voice = (voice or "").strip().lower()
        if selected_voice and selected_voice not in allowed_voices:
            selected_voice = "alloy"
        payload: Dict[str, Any] = {"model": model, "input": text}
        if selected_voice:
            payload["voice"] = selected_voice
        if audio_format:
            payload["response_format"] = audio_format
        resp = requests.post(
            "https://api.openai.com/v1/audio/speech",
            headers=headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type") or f"audio/{audio_format}"
        return TtsResult(
            audio=resp.content,
            content_type=content_type,
            provider="openai",
            model=model,
            sample_rate=None,
            voice=selected_voice or None,
        )

    def _synthesize_local(
        self,
        text: str,
        cfg: Dict[str, Any],
        *,
        model: str,
        voice: Optional[str],
        audio_format: str,
    ) -> TtsResult:
        model_id = resolve_model_alias(model) or model
        search_dirs = app_config.model_search_dirs(cfg.get("models_folder"))
        resolved_dir = _resolve_local_model_dir(model, search_dirs)
        if resolved_dir is None and model_id:
            resolved_dir = _resolve_local_model_dir(os.path.basename(str(model_id)), search_dirs)
        load_target = str(resolved_dir) if resolved_dir is not None else str(model_id)
        token = (cfg.get("hf_token") or "").strip() or os.getenv("HUGGINGFACE_HUB_TOKEN") or os.getenv("HF_TOKEN")
        if token:
            os.environ.setdefault("HUGGINGFACE_HUB_TOKEN", token)
            os.environ.setdefault("HF_TOKEN", token)

        if _is_kitten_model(model, model_id):
            return self._synthesize_kitten(
                text,
                load_target,
                model_id,
                voice,
                audio_format,
            )
        if _is_kokoro_model(model, model_id):
            return self._synthesize_kokoro(
                text,
                load_target,
                model_id,
                voice,
                audio_format,
            )

        local_files_only = True
        allow_remote_code = bool(cfg.get("allow_remote_code", True))

        try:
            pipe = _load_pipeline(
                load_target,
                token=token or None,
                local_files_only=local_files_only,
                trust_remote_code=allow_remote_code,
            )
        except Exception as exc:
            searched = ", ".join(str(p) for p in search_dirs)
            raise RuntimeError(
                f"Failed to load local TTS model '{model}' from '{load_target}'. "
                f"Ensure the model is downloaded (searched: {searched}) and the required "
                "TTS dependencies are installed."
            ) from exc
        result = pipe(text)
        audio = None
        sample_rate = None
        if isinstance(result, dict):
            audio = result.get("audio")
            sample_rate = result.get("sampling_rate") or result.get("sample_rate")
        if audio is None:
            raise RuntimeError("Local TTS did not return audio")
        if not sample_rate:
            sample_rate = 22050
        if audio_format.lower() not in {"wav", "wave"}:
            raise RuntimeError("Only wav output is supported for local TTS")
        wav_bytes = _encode_wav(audio, int(sample_rate))
        return TtsResult(
            audio=wav_bytes,
            content_type="audio/wav",
            provider="local",
            model=model,
            sample_rate=int(sample_rate),
            voice=voice,
        )

    def _synthesize_kitten(
        self,
        text: str,
        model_path: str,
        model_id: Optional[str],
        voice: Optional[str],
        audio_format: str,
    ) -> TtsResult:
        if audio_format.lower() not in {"wav", "wave"}:
            raise RuntimeError("Only wav output is supported for local TTS")
        try:
            from kittentts import KittenTTS
        except Exception as exc:
            raise RuntimeError(
                "Kitten TTS requires the 'kittentts' package. "
                "Install it via Poetry (kittentts) before retrying."
            ) from exc
        engine = _KITTEN_CACHE.get(model_path)
        if engine is None:
            engine = None
            last_exc = None
            candidates = [model_path]
            if model_id and model_id not in candidates:
                candidates.append(model_id)
            for candidate in candidates:
                try:
                    engine = KittenTTS(candidate)
                    _KITTEN_CACHE[model_path] = engine
                    break
                except Exception as exc:  # pragma: no cover - best effort
                    last_exc = exc
                    continue
            if engine is None:
                detail = "Failed to initialize Kitten TTS."
                if last_exc:
                    detail = f"{detail} {last_exc}"
                raise RuntimeError(detail) from last_exc
        voice_id = voice or "expr-voice-2-f"
        if not voice_id.startswith("expr-voice-"):
            voice_id = "expr-voice-2-f"
        try:
            audio = engine.generate(text, voice=voice_id)
        except TypeError:
            audio = engine.generate(text)
        sample_rate = getattr(engine, "sample_rate", None) or 24000
        wav_bytes = _encode_wav(audio, int(sample_rate))
        return TtsResult(
            audio=wav_bytes,
            content_type="audio/wav",
            provider="local",
            model="kitten",
            sample_rate=int(sample_rate),
            voice=voice_id,
        )

    def _synthesize_kokoro(
        self,
        text: str,
        model_path: str,
        model_id: Optional[str],
        voice: Optional[str],
        audio_format: str,
    ) -> TtsResult:
        if audio_format.lower() not in {"wav", "wave"}:
            raise RuntimeError("Only wav output is supported for local TTS")
        try:
            from kokoro import KPipeline
        except Exception as exc:
            raise RuntimeError(
                "Kokoro TTS requires the 'kokoro' package. "
                "Install it via Poetry (kokoro) before retrying."
            ) from exc
        pipeline = _KOKORO_CACHE.get(model_path)
        if pipeline is None:
            kwargs_template: Dict[str, Any] = {}
            try:
                params = inspect.signature(KPipeline).parameters
            except (TypeError, ValueError):
                params = {}
            if "lang_code" in params:
                kwargs_template["lang_code"] = "a"

            def _build_pipeline(candidate: str):
                kwargs = dict(kwargs_template)
                if "repo_id" in params:
                    kwargs["repo_id"] = candidate
                elif "model" in params:
                    kwargs["model"] = candidate
                elif "model_id" in params:
                    kwargs["model_id"] = candidate
                return KPipeline(**kwargs)

            pipeline = None
            last_exc = None
            candidates = [model_path]
            if model_id and model_id not in candidates:
                candidates.append(model_id)
            for candidate in candidates:
                try:
                    pipeline = _build_pipeline(candidate)
                    _KOKORO_CACHE[model_path] = pipeline
                    break
                except Exception as exc:  # pragma: no cover - best effort
                    last_exc = exc
                    continue
            if pipeline is None:
                raise RuntimeError("Failed to initialize Kokoro pipeline.") from last_exc
        voice_id = voice or "af_heart"
        if not voice_id.startswith(("af_", "am_", "bf_", "bm_", "jf_", "jm_", "zf_", "zm_", "ef_", "em_", "ff_", "hf_", "hm_", "if_", "im_", "pf_", "pm_")):
            voice_id = "af_heart"
        try:
            generator = pipeline(text, voice=voice_id)
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Kokoro needs espeak-ng available on the host. "
                "Install espeak-ng and retry."
            ) from exc
        try:
            import numpy as np
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("numpy is required to encode local TTS audio") from exc
        chunks = []
        for item in generator:
            audio = None
            if isinstance(item, (tuple, list)) and len(item) >= 3:
                audio = item[2]
            elif item is not None:
                audio = item
            if audio is None:
                continue
            chunks.append(np.asarray(audio))
        if not chunks:
            raise RuntimeError("Kokoro did not return audio")
        merged = np.concatenate(chunks)
        sample_rate = getattr(pipeline, "sample_rate", None) or 24000
        wav_bytes = _encode_wav(merged, int(sample_rate))
        return TtsResult(
            audio=wav_bytes,
            content_type="audio/wav",
            provider="local",
            model="kokoro",
            sample_rate=int(sample_rate),
            voice=voice_id,
        )
