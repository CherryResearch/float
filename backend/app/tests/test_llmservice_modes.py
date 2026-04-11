import json
import os
from types import SimpleNamespace

import pytest
from app.base_services import LLMService, ModelContext


class DummyTokenizer:
    def __call__(
        self,
        text,
        return_tensors=None,
        truncation=None,
        max_length=None,
    ):
        # Mimic tokenizer output without relying on torch
        return {"input_ids": [[0]]}

    def decode(self, ids, skip_special_tokens=True):
        return "local response"


class DummyModel:
    def generate(self, **kwargs):
        return [[0, 1]]


class DummyMultimodalProcessor:
    def __init__(self):
        self.tokenizer = self
        self.last_messages = None
        self.last_images = None

    def apply_chat_template(self, messages, add_generation_prompt=True, tokenize=False):
        self.last_messages = messages
        return "multimodal prompt"

    def __call__(self, text=None, images=None, return_tensors=None):
        self.last_images = images
        return {"input_ids": [[10, 20, 30]], "pixel_values": [1]}

    def decode(self, ids, skip_special_tokens=True):
        return "gemma multimodal response"


class DummyMultimodalModel:
    def generate(self, **kwargs):
        return [[10, 20, 30, 99]]


class DummyModelCacheConflict:
    def __init__(self):
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise ValueError(
                "Passing both `cache_implementation` and `past_key_values` is not supported."
            )
        assert "past_key_values" not in kwargs
        return [[0, 1]]


class DummyResponse:
    status_code = 200

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=True):
        yield "part1"
        yield "part2"


class DummyProcess:
    def __init__(self):
        self.terminated = False
        self.waited = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True


class DummyApiResponse:
    def __init__(self, payload):
        self.status_code = 200
        self.headers = {}
        self._payload = payload
        self.text = json.dumps(payload)

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class DummyStreamingApiResponse:
    def __init__(self, lines):
        self.status_code = 200
        self.headers = {}
        self.encoding = "utf-8"
        self._lines = list(lines)

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=False):
        for raw in self._lines:
            if decode_unicode and isinstance(raw, (bytes, bytearray)):
                yield raw.decode(self.encoding)
            else:
                yield raw


def test_verify_local_model_accepts_repo_style_model_name(tmp_path):
    model_dir = tmp_path / "gemma-3-270m"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "config.json").write_text("{}", encoding="utf-8")

    svc = LLMService(
        mode="local",
        config={
            "local_model": "google/gemma-3-270m",
            "models_folder": str(tmp_path),
        },
    )

    summary = svc.verify_local_model("google/gemma-3-270m")

    assert summary["found"] is True
    assert str(summary["path"]).endswith("gemma-3-270m")


def _build_sse_lines(*chunks):
    lines = []
    for chunk in chunks:
        lines.append(f"data: {json.dumps(chunk)}\n".encode("utf-8"))
    lines.append(b"data: [DONE]\n")
    return lines


def test_generate_api_responses_persists_response_ids_and_writes_capture(
    monkeypatch, tmp_path
):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return DummyApiResponse(
            {
                "id": "resp_test_123",
                "previous_response_id": "resp_test_prev",
                "model": "gpt-5.4",
                "output": [
                    {
                        "id": "out_a",
                        "content": [{"type": "output_text", "text": "hi"}],
                    },
                    {
                        "id": "out_b",
                        "content": [],
                    },
                ],
            }
        )

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    monkeypatch.setattr(
        "app.base_services.write_oai_api_capture",
        lambda **kwargs: os.fspath(tmp_path / "resp_test_123.json"),
    )

    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/responses",
            "api_key": "test-key",
            "api_model": "gpt-5.4",
        },
    )
    result = svc.generate(
        "hello",
        session_id="sess",
        stream_message_id="m1",
        metadata={"session_name": "sess", "message_id": "m1"},
        capture_raw_api=True,
    )

    assert captured["payload"]["metadata"] == {
        "session_name": "sess",
        "message_id": "m1",
    }
    assert result["text"] == "hi"
    assert result["metadata"]["response_id"] == "resp_test_123"
    assert result["metadata"]["previous_response_id"] == "resp_test_prev"
    assert result["metadata"]["output_ids"] == ["out_a", "out_b"]
    assert result["metadata"]["oai_api_log_path"] == os.fspath(
        tmp_path / "resp_test_123.json"
    )


def test_generate_api_streaming_responses_persists_response_ids_and_writes_capture(
    monkeypatch, tmp_path
):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None, stream=False):
        captured["payload"] = json
        captured["stream"] = stream
        return DummyStreamingApiResponse(
            _build_sse_lines(
                {"type": "response.output_text.delta", "delta": "hi"},
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_stream_123",
                        "previous_response_id": "resp_stream_prev",
                        "model": "gpt-5.4",
                        "output": [
                            {
                                "id": "out_stream_a",
                                "content": [{"type": "output_text", "text": "hi"}],
                            }
                        ],
                    },
                },
            )
        )

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    monkeypatch.setattr(
        "app.base_services.write_oai_api_capture",
        lambda **kwargs: os.fspath(tmp_path / "resp_stream_123.json"),
    )

    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/responses",
            "api_key": "test-key",
            "api_model": "gpt-5.4",
        },
    )
    events = []
    result = svc.generate(
        "hello",
        session_id="sess-stream",
        stream_message_id="m-stream",
        metadata={"session_name": "sess-stream", "message_id": "m-stream"},
        stream_consumer=events.append,
        capture_raw_api=True,
    )

    assert captured["stream"] is True
    assert captured["payload"]["metadata"] == {
        "session_name": "sess-stream",
        "message_id": "m-stream",
    }
    assert result["text"] == "hi"
    assert result["metadata"]["response_id"] == "resp_stream_123"
    assert result["metadata"]["previous_response_id"] == "resp_stream_prev"
    assert result["metadata"]["output_ids"] == ["out_stream_a"]
    assert result["metadata"]["oai_api_log_path"] == os.fspath(
        tmp_path / "resp_stream_123.json"
    )
    assert any(
        event.get("type") == "content" and event.get("content") == "hi"
        for event in events
    )


def _mark_local_preflight_ready(monkeypatch):
    def fake_preflight(self, model_name=None):
        target = model_name or self.config.get("local_model") or ""
        supports_images = str(target).startswith("gemma-4-")
        return {
            "ready": True,
            "model": target,
            "reason": None,
            "loader": "image_text_to_text" if supports_images else "causal_lm",
            "supports_images": supports_images,
            "python_executable": "test-python",
            "missing_packages": [],
            "missing_runtime_components": [],
            "recommended_packages": [],
            "checkpoint_metadata": {},
            "hint": None,
        }

    monkeypatch.setattr(LLMService, "local_runtime_preflight", fake_preflight)


def test_generate_local(monkeypatch):
    _mark_local_preflight_ready(monkeypatch)
    tokenizer = DummyTokenizer()
    model = DummyModel()
    monkeypatch.setattr(
        "app.base_services.AutoTokenizer",
        SimpleNamespace(from_pretrained=lambda name, **_: tokenizer),
    )
    monkeypatch.setattr(
        "app.base_services.AutoModelForCausalLM",
        SimpleNamespace(from_pretrained=lambda name, **_: model),
    )
    svc = LLMService(mode="local", config={"local_model": "dummy"})
    res = svc.generate("hello")
    assert res["text"] == "local response"


def test_generate_local_retries_on_cache_conflict(monkeypatch):
    _mark_local_preflight_ready(monkeypatch)
    tokenizer = DummyTokenizer()
    model = DummyModelCacheConflict()
    monkeypatch.setattr(
        "app.base_services.AutoTokenizer",
        SimpleNamespace(from_pretrained=lambda name, **_: tokenizer),
    )
    monkeypatch.setattr(
        "app.base_services.AutoModelForCausalLM",
        SimpleNamespace(from_pretrained=lambda name, **_: model),
    )
    svc = LLMService(mode="local", config={"local_model": "dummy"})
    svc._kv_cache["default"] = object()
    res = svc.generate("hello", session_id="default")
    assert res["text"] == "local response"
    assert "default" not in svc._kv_cache
    assert model.calls == 2


def test_generate_local_decodes_only_new_tokens(monkeypatch):
    _mark_local_preflight_ready(monkeypatch)

    class EchoTokenizer:
        def __call__(
            self,
            text,
            return_tensors=None,
            truncation=None,
            max_length=None,
        ):
            return {"input_ids": [[10, 20, 30]], "attention_mask": [[1, 1, 1]]}

        def decode(self, ids, skip_special_tokens=True):
            return ",".join(str(i) for i in ids)

    class EchoModel:
        def generate(self, **kwargs):
            return [[10, 20, 30, 99, 100]]

    monkeypatch.setattr(
        "app.base_services.AutoTokenizer",
        SimpleNamespace(from_pretrained=lambda name, **_: EchoTokenizer()),
    )
    monkeypatch.setattr(
        "app.base_services.AutoModelForCausalLM",
        SimpleNamespace(from_pretrained=lambda name, **_: EchoModel()),
    )
    svc = LLMService(mode="local", config={"local_model": "dummy"})
    res = svc.generate("hello")
    assert res["text"] == "99,100"


def test_generate_dynamic(monkeypatch):
    def fake_post(url, json, stream, timeout):
        assert stream
        return DummyResponse()

    def fake_start(self):
        self.dynamic_process = DummyProcess()

    monkeypatch.setattr("app.base_services.requests.post", fake_post)
    monkeypatch.setattr(LLMService, "start_dynamic_server", fake_start)

    svc = LLMService(
        mode="dynamic",
        config={"dynamic_url": "http://localhost"},
    )
    res = svc.generate("hello")
    assert res["text"] == "part1part2"


def test_normalize_server_url_adds_http_scheme_for_bare_host():
    svc = LLMService(mode="server")
    assert (
        svc._normalize_server_url("127.0.0.1:11434")
        == "http://127.0.0.1:11434/v1/chat/completions"
    )


def test_dynamic_server_start_stop(monkeypatch):
    proc = DummyProcess()

    def fake_popen(args):
        return proc

    monkeypatch.setattr("app.base_services.Popen", fake_popen)
    svc = LLMService(mode="dynamic")
    svc.start_dynamic_server()
    assert svc.dynamic_process is proc
    svc.stop_dynamic_server()
    assert proc.terminated and proc.waited


def test_local_runtime_status_reports_transformers_backend(monkeypatch):
    _mark_local_preflight_ready(monkeypatch)
    tokenizer = DummyTokenizer()
    model = DummyModel()
    monkeypatch.setattr(
        "app.base_services.AutoTokenizer",
        SimpleNamespace(from_pretrained=lambda name, **_: tokenizer),
    )
    monkeypatch.setattr(
        "app.base_services.AutoModelForCausalLM",
        SimpleNamespace(from_pretrained=lambda name, **_: model),
    )
    svc = LLMService(mode="local", config={"local_model": "dummy"})
    svc.generate("hello")
    status = svc.local_runtime_status()
    assert status["active_backend"] == "transformers"


def test_generate_local_gemma4_multimodal_path(monkeypatch):
    _mark_local_preflight_ready(monkeypatch)
    processor = DummyMultimodalProcessor()
    model = DummyMultimodalModel()
    monkeypatch.setattr(
        "app.base_services.AutoProcessor",
        SimpleNamespace(from_pretrained=lambda name, **_: processor),
    )
    monkeypatch.setattr(
        "app.base_services.AutoModelForImageTextToText",
        SimpleNamespace(from_pretrained=lambda name, **_: model),
    )
    monkeypatch.setattr(
        "app.base_services.load_blob", lambda _content_hash: b"raw-image"
    )
    monkeypatch.setattr(
        "app.base_services._open_local_image",
        lambda raw: {"opened": raw == b"raw-image"},
    )

    svc = LLMService(mode="local", config={"local_model": "gemma-4-E2B-it"})
    result = svc.generate(
        "describe the image",
        attachments=[
            {
                "name": "sample.png",
                "type": "image/png",
                "content_hash": "hash-image",
            }
        ],
    )

    assert result["text"] == "gemma multimodal response"
    assert processor.last_images == [{"opened": True}]
    assert processor.last_messages[-1]["content"][0]["type"] == "image"
    assert result["metadata"]["local_loader"] == "image_text_to_text"
    assert result["metadata"]["supports_images"] is True
    status = svc.local_runtime_status()
    assert status["local_loader"] == "image_text_to_text"
    assert status["supports_images"] is True


def test_generate_local_gemma4_prefers_multimodal_lm_loader(monkeypatch):
    _mark_local_preflight_ready(monkeypatch)
    processor = DummyMultimodalProcessor()
    model = DummyMultimodalModel()
    multimodal_calls = []
    legacy_calls = []
    monkeypatch.setattr(
        "app.base_services.AutoProcessor",
        SimpleNamespace(from_pretrained=lambda name, **_: processor),
    )
    monkeypatch.setattr(
        "app.base_services.AutoModelForMultimodalLM",
        SimpleNamespace(
            from_pretrained=lambda name, **_: multimodal_calls.append(name) or model
        ),
        raising=False,
    )
    monkeypatch.setattr(
        "app.base_services.AutoModelForImageTextToText",
        SimpleNamespace(
            from_pretrained=lambda name, **_: legacy_calls.append(name) or model
        ),
    )

    svc = LLMService(mode="local", config={"local_model": "gemma-4-E2B-it"})
    result = svc.generate("describe the image")

    assert result["text"] == "gemma multimodal response"
    assert len(multimodal_calls) == 1
    assert multimodal_calls[0].endswith("gemma-4-E2B-it")
    assert legacy_calls == []


def test_generate_local_gemma4_reports_transformers_compatibility_error(
    monkeypatch, tmp_path
):
    _mark_local_preflight_ready(monkeypatch)
    model_dir = tmp_path / "gemma-4-E2B-it"
    model_dir.mkdir()
    (model_dir / "processor_config.json").write_text(
        json.dumps({"processor_class": "Gemma4Processor"}),
        encoding="utf-8",
    )
    (model_dir / "config.json").write_text(
        json.dumps(
            {
                "model_type": "gemma4",
                "architectures": ["Gemma4ForConditionalGeneration"],
                "transformers_version": "5.5.0.dev0",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.base_services.AutoProcessor",
        SimpleNamespace(
            from_pretrained=lambda name, **_: (_ for _ in ()).throw(
                ValueError(
                    f"Unrecognized processing class in {name}. "
                    "Can't instantiate a processor, a tokenizer, an image processor or a feature extractor for this model."
                )
            )
        ),
    )
    monkeypatch.setattr(
        "app.base_services.AutoModelForImageTextToText",
        SimpleNamespace(from_pretrained=lambda name, **_: DummyMultimodalModel()),
    )

    svc = LLMService(
        mode="local",
        config={
            "local_model": "gemma-4-E2B-it",
            "models_folder": str(tmp_path),
        },
    )

    with pytest.raises(RuntimeError) as exc_info:
        svc.generate("hello")

    message = str(exc_info.value)
    assert "Local Gemma 4 files were found" in message
    assert "Gemma4Processor" in message
    assert "5.5.0.dev0" in message
    assert "provider/server lane" in message


def test_local_runtime_preflight_surfaces_backend_python_for_missing_packages(
    monkeypatch, tmp_path
):
    model_dir = tmp_path / "gemma-4-E2B-it"
    model_dir.mkdir()
    monkeypatch.setattr(
        "app.base_services.app_config.model_search_dirs",
        lambda _models_folder=None: [tmp_path],
    )
    monkeypatch.setattr(
        "app.base_services._resolve_local_model_dir",
        lambda _search_dirs, _model_name: model_dir,
    )
    monkeypatch.setattr(
        "app.base_services._local_checkpoint_metadata",
        lambda _resolved_dir: {
            "family": "gemma4",
            "declared_transformers_version": "5.5.0.dev0",
        },
    )
    monkeypatch.setattr(
        "app.base_services._safe_package_version",
        lambda name: None if name in {"torch", "transformers"} else "1.0.0",
    )
    monkeypatch.setattr(
        "app.base_services._get_transformers_components",
        lambda: (None, None, None, None, None),
    )

    svc = LLMService(
        mode="local",
        config={
            "local_model": "gemma-4-E2B-it",
            "models_folder": str(tmp_path),
        },
    )

    preflight = svc.local_runtime_preflight()
    assert preflight["ready"] is False
    assert preflight["missing_packages"] == ["torch", "transformers"]
    assert preflight["missing_runtime_components"] == [
        "AutoProcessor",
        "AutoModelForMultimodalLM or AutoModelForImageTextToText",
    ]
    assert preflight["python_executable"] in (preflight["hint"] or "")
    assert "poetry install" in (preflight["hint"] or "")
    with pytest.raises(RuntimeError) as exc_info:
        svc.generate("hello")

    message = str(exc_info.value)
    assert preflight["python_executable"] in message
    assert "torch, transformers" in message


def test_local_runtime_preflight_blocks_gemma4_without_torchvision(
    monkeypatch, tmp_path
):
    model_dir = tmp_path / "gemma-4-E2B-it"
    model_dir.mkdir()
    monkeypatch.setattr(
        "app.base_services.app_config.model_search_dirs",
        lambda _models_folder=None: [tmp_path],
    )
    monkeypatch.setattr(
        "app.base_services._resolve_local_model_dir",
        lambda _search_dirs, _model_name: model_dir,
    )
    monkeypatch.setattr(
        "app.base_services._local_checkpoint_metadata",
        lambda _resolved_dir: {
            "family": "gemma4",
            "declared_transformers_version": "5.5.0.dev0",
        },
    )
    monkeypatch.setattr(
        "app.base_services._safe_package_version",
        lambda name: (
            None
            if name == "torchvision"
            else "5.5.0"
            if name == "transformers"
            else "2.7.1"
            if name == "torch"
            else "1.0.0"
        ),
    )
    monkeypatch.setattr(
        "app.base_services._get_transformers_components",
        lambda: ("causal", "image-text", "multimodal", "processor", "tokenizer"),
    )

    svc = LLMService(
        mode="local",
        config={
            "local_model": "gemma-4-E2B-it",
            "models_folder": str(tmp_path),
        },
    )

    preflight = svc.local_runtime_preflight()

    assert preflight["ready"] is False
    assert preflight["missing_packages"] == ["torchvision"]
    assert "README.md" in (preflight["hint"] or "")
    assert "docs/environment setup.md" in (preflight["hint"] or "")
    assert "torchvision==0.22.1+cpu" in (preflight["hint"] or "")


def test_local_runtime_preflight_reloads_transformers_components_when_stale(
    monkeypatch, tmp_path
):
    model_dir = tmp_path / "gemma-4-E2B-it"
    model_dir.mkdir()
    monkeypatch.setattr(
        "app.base_services.app_config.model_search_dirs",
        lambda _models_folder=None: [tmp_path],
    )
    monkeypatch.setattr(
        "app.base_services._resolve_local_model_dir",
        lambda _search_dirs, _model_name: model_dir,
    )
    monkeypatch.setattr(
        "app.base_services._local_checkpoint_metadata",
        lambda _resolved_dir: {
            "family": "gemma4",
            "declared_transformers_version": "5.5.0.dev0",
        },
    )
    monkeypatch.setattr(
        "app.base_services._safe_package_version",
        lambda name: "5.5.0" if name == "transformers" else "1.0.0",
    )
    monkeypatch.setattr(
        "app.base_services._get_transformers_components",
        lambda: (None, None, None, None, None),
    )
    monkeypatch.setattr(
        "app.base_services._reload_transformers_components",
        lambda: ("causal", "image-text", "multimodal", "processor", "tokenizer"),
    )

    svc = LLMService(
        mode="local",
        config={
            "local_model": "gemma-4-E2B-it",
            "models_folder": str(tmp_path),
        },
    )

    preflight = svc.local_runtime_preflight()

    assert preflight["ready"] is True
    assert preflight["missing_runtime_components"] == []


def test_local_runtime_preflight_surfaces_restart_hint_after_failed_reload(
    monkeypatch, tmp_path
):
    model_dir = tmp_path / "gemma-4-E2B-it"
    model_dir.mkdir()
    monkeypatch.setattr(
        "app.base_services.app_config.model_search_dirs",
        lambda _models_folder=None: [tmp_path],
    )
    monkeypatch.setattr(
        "app.base_services._resolve_local_model_dir",
        lambda _search_dirs, _model_name: model_dir,
    )
    monkeypatch.setattr(
        "app.base_services._local_checkpoint_metadata",
        lambda _resolved_dir: {
            "family": "gemma4",
            "declared_transformers_version": "5.5.0.dev0",
        },
    )
    monkeypatch.setattr(
        "app.base_services._safe_package_version",
        lambda name: "5.5.0" if name == "transformers" else "1.0.0",
    )
    monkeypatch.setattr(
        "app.base_services._get_transformers_components",
        lambda: (None, None, None, None, None),
    )
    monkeypatch.setattr(
        "app.base_services._reload_transformers_components",
        lambda: (None, None, None, None, None),
    )

    svc = LLMService(
        mode="local",
        config={
            "local_model": "gemma-4-E2B-it",
            "models_folder": str(tmp_path),
        },
    )

    preflight = svc.local_runtime_preflight()

    assert preflight["ready"] is False
    assert "restart Float and retry" in (preflight["hint"] or "")


def test_generate_local_gemma4_reports_torchvision_install_guidance(
    monkeypatch, tmp_path
):
    _mark_local_preflight_ready(monkeypatch)
    model_dir = tmp_path / "gemma-4-E2B-it"
    model_dir.mkdir()
    (model_dir / "processor_config.json").write_text(
        json.dumps({"processor_class": "Gemma4Processor"}),
        encoding="utf-8",
    )
    (model_dir / "config.json").write_text(
        json.dumps(
            {
                "model_type": "gemma4",
                "architectures": ["Gemma4ForConditionalGeneration"],
                "transformers_version": "5.5.0.dev0",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.base_services.AutoProcessor",
        SimpleNamespace(
            from_pretrained=lambda name, **_: (_ for _ in ()).throw(
                RuntimeError(
                    "Gemma4VideoProcessor requires the Torchvision library but it was not found in your environment."
                )
            )
        ),
    )
    monkeypatch.setattr(
        "app.base_services.AutoModelForImageTextToText",
        SimpleNamespace(from_pretrained=lambda name, **_: DummyMultimodalModel()),
    )
    monkeypatch.setattr(
        "app.base_services._safe_package_version",
        lambda name: (
            None
            if name == "torchvision"
            else "5.5.0"
            if name == "transformers"
            else "2.7.1"
            if name == "torch"
            else "1.0.0"
        ),
    )

    svc = LLMService(
        mode="local",
        config={
            "local_model": "gemma-4-E2B-it",
            "models_folder": str(tmp_path),
        },
    )

    with pytest.raises(RuntimeError) as exc_info:
        svc.generate("hello")

    message = str(exc_info.value)
    assert "requires torchvision" in message.lower()
    assert "README.md" in message
    assert "docs/environment setup.md" in message
    assert "poetry run uv pip install" in message
    assert "Restart Float after installation" in message


def test_local_runtime_preflight_accepts_stable_transformers_for_dev_declared_gemma4(
    monkeypatch, tmp_path
):
    model_dir = tmp_path / "gemma-4-E2B-it"
    model_dir.mkdir()
    monkeypatch.setattr(
        "app.base_services.app_config.model_search_dirs",
        lambda _models_folder=None: [tmp_path],
    )
    monkeypatch.setattr(
        "app.base_services._resolve_local_model_dir",
        lambda _search_dirs, _model_name: model_dir,
    )
    monkeypatch.setattr(
        "app.base_services._local_checkpoint_metadata",
        lambda _resolved_dir: {
            "family": "gemma4",
            "declared_transformers_version": "5.5.0.dev0",
        },
    )
    monkeypatch.setattr(
        "app.base_services._safe_package_version",
        lambda name: "5.5.0" if name == "transformers" else "1.0.0",
    )
    monkeypatch.setattr(
        "app.base_services._get_transformers_components",
        lambda: ("causal", "image-text", "multimodal", "processor", "tokenizer"),
    )

    svc = LLMService(
        mode="local",
        config={
            "local_model": "gemma-4-E2B-it",
            "models_folder": str(tmp_path),
        },
    )

    preflight = svc.local_runtime_preflight()
    assert preflight["ready"] is True
    assert "declares transformers" not in (preflight.get("hint") or "")


def test_generate_api_inlines_native_image_parts_for_supported_models(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return DummyApiResponse(
            {
                "model": "gpt-4.1-mini",
                "choices": [{"message": {"content": "vision ok"}}],
            }
        )

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    monkeypatch.setattr(
        "app.base_services.load_blob", lambda _content_hash: b"img-bytes"
    )

    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/chat/completions",
            "api_key": "test-key",
            "api_model": "gpt-4.1-mini",
        },
    )
    result = svc.generate(
        "describe the image",
        attachments=[
            {
                "name": "sample.png",
                "type": "image/png",
                "url": "/api/attachments/hash-native/sample.png",
                "content_hash": "hash-native",
            }
        ],
        vision_workflow="caption",
    )

    content = captured["payload"]["messages"][-1]["content"]
    assert any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in content
    )
    assert result["metadata"]["vision"]["workflow"] == "caption"
    assert result["metadata"]["vision"]["native_image_input"] is True
    assert result["metadata"]["vision"]["fallback_used"] is False


def test_generate_api_uses_local_caption_fallback_for_non_vision_models(monkeypatch):
    captured = {}

    class DummyCaptioner:
        def __init__(self, model):
            self.model = model

        def run(self, raw):
            assert raw == b"fallback-image"
            return {"image_caption": "Local fallback caption", "placeholder": False}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return DummyApiResponse(
            {
                "model": "text-only-model",
                "choices": [{"message": {"content": "fallback ok"}}],
            }
        )

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    monkeypatch.setattr(
        "app.base_services.load_blob", lambda _content_hash: b"fallback-image"
    )
    monkeypatch.setattr("app.base_services.VisionCaptioner", DummyCaptioner)

    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/chat/completions",
            "api_key": "test-key",
            "api_model": "text-only-model",
            "vision_model": "local-caption-model",
        },
    )
    result = svc.generate(
        "describe the image",
        attachments=[
            {
                "name": "fallback.png",
                "type": "image/png",
                "url": "/api/attachments/hash-fallback/fallback.png",
                "content_hash": "hash-fallback",
            }
        ],
        vision_workflow="caption",
    )

    content = captured["payload"]["messages"][-1]["content"]
    assert not any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in content
    )
    assert any(
        isinstance(part, dict)
        and "Local vision fallback caption" in str(part.get("text", ""))
        for part in content
    )
    vision_meta = result["metadata"]["vision"]
    assert vision_meta["workflow"] == "caption"
    assert vision_meta["native_image_input"] is False
    assert vision_meta["fallback_used"] is True
    assert vision_meta["fallback_images"] == 1
    assert vision_meta["fallback_attachments"][0]["caption"] == "Local fallback caption"
    assert vision_meta["fallback_attachments"][0]["caption_model"] == (
        "local-caption-model"
    )


def test_generate_api_uses_placeholder_caption_without_hashlib_crash(monkeypatch):
    captured = {}

    class EmptyCaptioner:
        def __init__(self, model):
            self.model = model

        def run(self, raw):
            assert raw == b"fallback-image"
            return {"image_caption": "", "placeholder": False}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return DummyApiResponse(
            {
                "model": "text-only-model",
                "choices": [{"message": {"content": "fallback ok"}}],
            }
        )

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    monkeypatch.setattr(
        "app.base_services.load_blob", lambda _content_hash: b"fallback-image"
    )
    monkeypatch.setattr("app.base_services.VisionCaptioner", EmptyCaptioner)

    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/chat/completions",
            "api_key": "test-key",
            "api_model": "text-only-model",
            "vision_model": "local-caption-model",
        },
    )

    result = svc.generate(
        "describe the image",
        attachments=[
            {
                "name": "fallback-no-caption.png",
                "type": "image/png",
                "url": "/api/attachments/hash-fallback/fallback-no-caption.png",
                "content_hash": "",
            }
        ],
        vision_workflow="caption",
    )

    content = captured["payload"]["messages"][-1]["content"]
    fallback_text = next(
        (
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict)
            and "Local vision fallback caption" in str(part.get("text", ""))
        ),
        "",
    )
    assert "Unable to generate caption" in fallback_text
    assert result["metadata"]["vision"]["fallback_used"] is True


def test_generate_api_merges_attachments_when_prompt_is_sequence(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return DummyApiResponse(
            {
                "model": "gpt-4.1-mini",
                "choices": [{"message": {"content": "vision ok"}}],
            }
        )

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    monkeypatch.setattr(
        "app.base_services.load_blob", lambda _content_hash: b"img-bytes"
    )

    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/chat/completions",
            "api_key": "test-key",
            "api_model": "gpt-4.1-mini",
        },
    )

    svc.generate(
        [],
        attachments=[
            {
                "name": "recalled.png",
                "type": "image/png",
                "url": "/api/attachments/hash-recalled/recalled.png",
                "content_hash": "hash-recalled",
            }
        ],
        vision_workflow="image_qa",
    )

    messages = captured["payload"]["messages"]
    trailing = messages[-1]["content"]
    assert any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in trailing
    )


def test_generate_api_dedupes_recalled_context_attachments_against_prompt_attachments(
    monkeypatch,
):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return DummyApiResponse(
            {
                "id": "resp_dedupe",
                "model": "gpt-5.4",
                "output_text": "vision ok",
            }
        )

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    monkeypatch.setattr(
        "app.base_services.load_blob", lambda _content_hash: b"img-bytes"
    )

    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/responses",
            "api_key": "test-key",
            "api_model": "gpt-5.4",
        },
    )
    attachment = {
        "name": "recalled.png",
        "type": "image/png",
        "url": "/api/attachments/hash-recalled/recalled.png",
        "content_hash": "hash-recalled",
    }
    ctx = ModelContext(system_prompt="")
    ctx.add_message(
        "user",
        "Earlier image context",
        metadata={"attachments": [dict(attachment)]},
    )

    svc.generate(
        "Follow up on the same image",
        attachments=[dict(attachment)],
        context=ctx,
        vision_workflow="caption",
    )

    input_items = captured["payload"]["input"]
    image_parts = [
        part
        for item in input_items
        if isinstance(item, dict)
        for part in (item.get("content") or [])
        if isinstance(part, dict) and part.get("type") == "input_image"
    ]
    assert len(image_parts) == 1
