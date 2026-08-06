import base64
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from app.base_services import LLMService, ModelContext, _model_supports_native_images
from PIL import Image


def _content_hash(label: object) -> str:
    return hashlib.sha256(str(label).encode("utf-8")).hexdigest()


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


def test_gemma4_server_models_are_native_image_capable():
    assert _model_supports_native_images("google/gemma-4-12b")
    assert _model_supports_native_images("google/gemma4-31b-qat")


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


class DummyErrorApiResponse(DummyApiResponse):
    def __init__(self, payload, status_code=400):
        super().__init__(payload)
        self.status_code = status_code

    def raise_for_status(self):
        raise requests.exceptions.HTTPError(
            f"{self.status_code} provider error",
            response=self,
        )


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


def test_function_tool_responses_non_streaming_uses_extended_timeout_with_retry(
    monkeypatch,
):
    for key in ("LLM_REQUEST_TIMEOUT", "LLM_TIMEOUT", "FLOAT_REQUEST_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)

    captured_timeouts = []

    def fake_post(url, headers=None, json=None, timeout=None):
        captured_timeouts.append(timeout)
        if len(captured_timeouts) == 1:
            raise requests.exceptions.ReadTimeout("provider is still reasoning")
        return DummyApiResponse(
            {
                "id": "resp_native_timeout_retry",
                "model": "gpt-5.6-sol",
                "output_text": "Recovered after retry.",
            }
        )

    monkeypatch.setenv("LLM_API_RETRIES", "1")
    monkeypatch.setenv("LLM_API_RETRY_DELAY", "0")
    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    service = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/responses",
            "api_key": "test-key",
            "api_model": "gpt-5.6-sol",
            "request_timeout": 30,
            "stream_idle_timeout": 45,
            "timeout_backoff": [30, 90],
        },
    )

    result = service.generate(
        [],
        native_tool_definitions=[
            {
                "type": "function",
                "name": "recall",
                "description": "Recall saved context.",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
    )

    assert result["text"] == "Recovered after retry."
    assert captured_timeouts == [(30.0, 45.0), (30.0, 60.0)]


def test_structured_prompt_timeout_returns_truthful_failure(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        raise requests.exceptions.ReadTimeout("provider read timed out")

    monkeypatch.setenv("LLM_API_RETRIES", "1")
    monkeypatch.setenv("LLM_API_RETRY_DELAY", "0")
    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    service = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/responses",
            "api_key": "test-key",
            "api_model": "gpt-5.6-sol",
            "request_timeout": 30,
            "stream_idle_timeout": 180,
        },
    )

    result = service.generate([])

    assert result["text"] == (
        "The model provider timed out before returning a response."
    )
    assert "You said" not in result["text"]
    assert result["metadata"]["category"] == "timeout"
    assert "stream idle timeout" in result["metadata"]["hint"]


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


def test_generate_api_streaming_responses_parses_gpt56_function_call(monkeypatch):
    captured = {"calls": 0}
    function_call = {
        "type": "function_call",
        "id": "fc_read_1",
        "call_id": "call_read_1",
        "name": "read_file",
        "arguments": '{"path":"workspace/note.txt"}',
    }

    def fake_post(url, headers=None, json=None, timeout=None, stream=False):
        captured["calls"] += 1
        captured["payload"] = json
        captured["stream"] = stream
        return DummyStreamingApiResponse(
            _build_sse_lines(
                {
                    "type": "response.reasoning_summary_text.delta",
                    "delta": "I should read the requested file.",
                },
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**function_call, "arguments": ""},
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "output_index": 0,
                    "item_id": "fc_read_1",
                    "delta": '{"path":"workspace/',
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "output_index": 0,
                    "item_id": "fc_read_1",
                    "delta": 'note.txt"}',
                },
                {
                    "type": "response.function_call_arguments.done",
                    "output_index": 0,
                    "item_id": "fc_read_1",
                    "arguments": function_call["arguments"],
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": function_call,
                },
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_gpt56_tool",
                        "model": "gpt-5.6-sol",
                        "status": "completed",
                        "output": [function_call],
                    },
                },
            )
        )

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    service = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/responses",
            "api_key": "test-key",
            "api_model": "gpt-5.6-sol",
        },
    )
    context = ModelContext(
        tools=[
            {
                "name": "read_file",
                "description": "Read a text file from the workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            }
        ]
    )
    events = []

    result = service.generate(
        "Read workspace/note.txt and quote its first line.",
        context=context,
        session_id="sess-gpt56",
        stream_message_id="m-gpt56",
        stream_consumer=events.append,
    )

    assert captured["calls"] == 1
    assert captured["stream"] is True
    assert captured["payload"]["model"] == "gpt-5.6-sol"
    assert captured["payload"]["tools"][0]["name"] == "read_file"
    assert result["text"] == "[[tool_call:0]]"
    assert result["thought"] == "I should read the requested file."
    assert result["tools_used"] == [
        {
            "name": "read_file",
            "args": {"path": "workspace/note.txt"},
            "call_id": "call_read_1",
            "response_item": function_call,
        }
    ]
    tool_deltas = [event for event in events if event.get("type") == "tool_call_delta"]
    assert [event.get("fragment") for event in tool_deltas] == [
        '{"path":"workspace/',
        'note.txt"}',
    ]
    assert tool_deltas[-1]["arguments"] == function_call["arguments"]
    visible_content = "".join(
        str(event.get("content") or "")
        for event in events
        if event.get("type") == "content"
    )
    assert "workspace/note.txt" not in visible_content
    assert function_call["arguments"] not in visible_content
    assert result["metadata"]["response_id"] == "resp_gpt56_tool"


def test_generate_api_streaming_responses_drops_incomplete_function_call(
    monkeypatch,
):
    function_call = {
        "type": "function_call",
        "id": "fc_partial_1",
        "call_id": "call_partial_1",
        "name": "read_file",
        "arguments": "",
    }

    def fake_post(url, headers=None, json=None, timeout=None, stream=False):
        if not stream:
            return DummyApiResponse({"output_text": "Recovered without a tool call."})
        return DummyStreamingApiResponse(
            _build_sse_lines(
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": function_call,
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "output_index": 0,
                    "item_id": "fc_partial_1",
                    "delta": '{"path":"workspace/',
                },
            )
        )

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    service = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/responses",
            "api_key": "test-key",
            "api_model": "gpt-5.6-sol",
        },
    )
    events = []

    result = service.generate(
        "Read the note.",
        context=ModelContext(
            tools=[
                {
                    "name": "read_file",
                    "description": "Read a file.",
                    "parameters": {"type": "object", "properties": {}},
                }
            ]
        ),
        stream_consumer=events.append,
    )

    assert result["tools_used"] == []


def test_generate_api_streaming_responses_drops_malformed_completed_call(
    monkeypatch,
):
    function_call = {
        "type": "function_call",
        "id": "fc_malformed_1",
        "call_id": "call_malformed_1",
        "name": "read_file",
        "arguments": '{"path":',
    }

    def fake_post(url, headers=None, json=None, timeout=None, stream=False):
        if not stream:
            return DummyApiResponse({"output_text": "Recovered without a tool call."})
        return DummyStreamingApiResponse(
            _build_sse_lines(
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**function_call, "arguments": ""},
                },
                {
                    "type": "response.function_call_arguments.done",
                    "output_index": 0,
                    "item_id": "fc_malformed_1",
                    "arguments": function_call["arguments"],
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": function_call,
                },
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_malformed_tool",
                        "model": "gpt-5.6-sol",
                        "status": "completed",
                        "output": [function_call],
                    },
                },
            )
        )

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    service = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/responses",
            "api_key": "test-key",
            "api_model": "gpt-5.6-sol",
        },
    )

    result = service.generate(
        "Read the note.",
        context=ModelContext(
            tools=[
                {
                    "name": "read_file",
                    "description": "Read a file.",
                    "parameters": {"type": "object", "properties": {}},
                }
            ]
        ),
        stream_consumer=lambda event: None,
    )

    assert result["tools_used"] == []


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


def test_tinker_server_uses_tinker_key_and_reasoning_content(monkeypatch):
    from app.server_presets import TINKER_OPENAI_BASE_URL

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json
        return DummyApiResponse(
            {
                "model": "tinker://run:train:0/sampler_weights/inkling-custom",
                "choices": [
                    {
                        "message": {
                            "content": "Inkling answer",
                            "reasoning_content": "Inkling thought",
                        }
                    }
                ],
            }
        )

    monkeypatch.setenv("TINKER_API_KEY", "tinker-secret")
    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    service = LLMService(
        mode="server",
        config={
            "server_url": TINKER_OPENAI_BASE_URL,
            "server_preset_id": "tinker",
            "server_presets": [],
            "api_key": "openai-secret",
        },
    )
    model = "tinker://run:train:0/sampler_weights/inkling-custom"

    result = service.generate(
        "hello",
        model=model,
        reasoning={"effort": "high"},
        output_token_limit=65536,
        native_tool_definitions=[
            {
                "name": "tool_info",
                "description": "Look up one available tool.",
                "parameters": {
                    "type": "object",
                    "properties": {"tool_name": {"type": "string"}},
                    "required": ["tool_name"],
                },
            }
        ],
    )

    assert captured["url"].endswith("/oai/api/v1/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer tinker-secret"
    assert captured["payload"]["model"] == model
    assert captured["payload"]["reasoning_effort"] == "high"
    assert captured["payload"]["max_tokens"] == 65536
    assert captured["payload"]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "tool_info",
                "parameters": {
                    "type": "object",
                    "properties": {"tool_name": {"type": "string"}},
                    "required": ["tool_name"],
                },
                "description": "Look up one available tool.",
            },
        }
    ]
    assert result["text"] == "Inkling answer"
    assert result["thought"] == "Inkling thought"


def test_tinker_models_receive_chat_reasoning_controls():
    from app.routes import (
        _apply_reasoning_response_metadata,
        _reasoning_generation_kwargs,
        _reasoning_payload_for_model,
        _runtime_model_identity_note,
    )

    high = _reasoning_payload_for_model(
        "high",
        model="tinker://run:train:0/sampler_weights/inkling-custom",
    )
    assert high["effort"] == "high"
    assert "output_token_budget" not in high

    continuous = _reasoning_payload_for_model("0.83", model="tml/Inkling")
    assert continuous["effort"] == 0.83
    assert continuous["preset"] == "high"
    assert continuous["rounded"] is False

    rounded = _reasoning_payload_for_model(0.83, model="gpt-5.4")
    assert rounded["effort"] == "high"
    assert rounded["preset"] == "high"
    assert rounded["rounded"] is True

    low = _reasoning_payload_for_model("low", model="tml/Inkling")
    assert low["effort"] == "low"
    assert "output_token_budget" not in low

    automatic = _reasoning_generation_kwargs(
        "high",
        model="tml/Inkling",
    )
    assert "output_token_limit" not in automatic
    explicit = _reasoning_generation_kwargs(
        "high",
        model="tml/Inkling",
        max_output_tokens=65536,
    )
    assert explicit["output_token_limit"] == 65536

    response = {"metadata": {"finish_reason": "length"}}
    _apply_reasoning_response_metadata(response, continuous, 65536)
    assert response["metadata"]["output_truncated"] is True
    assert response["metadata"]["termination_category"] == "output_token_limit"
    assert response["metadata"]["reasoning"]["effective_effort"] == 0.83
    assert response["metadata"]["generation"] == {
        "max_output_tokens": 65536,
        "output_limit_source": "user",
    }

    provider_default = {"metadata": {}}
    _apply_reasoning_response_metadata(provider_default, high)
    assert provider_default["metadata"]["generation"] == {
        "max_output_tokens": None,
        "output_limit_source": "provider_default",
    }

    identity_note = _runtime_model_identity_note(
        model="thinkingmachines/Inkling",
        mode="server",
        provider="tinker",
    )
    assert "You are Float" in identity_note
    assert "thinkingmachines/Inkling" in identity_note


@pytest.mark.parametrize(
    ("provider_message", "expected_category"),
    [
        (
            "reasoning_effort is not supported by this model",
            "reasoning_control_unsupported",
        ),
        (
            "max_tokens exceeds the output token limit",
            "output_token_limit",
        ),
    ],
)
def test_server_provider_errors_classify_reasoning_and_output_limits(
    monkeypatch,
    provider_message,
    expected_category,
):
    def fake_post(*args, **kwargs):
        return DummyErrorApiResponse({"error": {"message": provider_message}})

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    service = LLMService(
        mode="server",
        config={
            "server_url": "https://example.test/v1",
            "server_presets": [],
        },
    )

    result = service.generate(
        "hello",
        model="thinkingmachines/Inkling",
        reasoning={"effort": 0.83},
        output_token_limit=8192,
        retries=0,
    )

    assert result["metadata"]["category"] == expected_category
    assert provider_message in result["metadata"]["provider_message"]
    assert "hint" in result["metadata"]


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
                "content_hash": _content_hash("local-gemma-image"),
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
            else "2.10.0"
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
    assert "torchvision==0.25.0" in (preflight["hint"] or "")


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
            else "2.10.0"
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
    content_hash = _content_hash("native-image")
    result = svc.generate(
        "describe the image",
        attachments=[
            {
                "name": "sample.png",
                "type": "image/png",
                "url": f"/api/attachments/{content_hash}/sample.png",
                "content_hash": content_hash,
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


def test_generate_api_does_not_read_unmanaged_context_image_path(
    monkeypatch,
):
    captured = {}
    unmanaged_file = Path(__file__).resolve().parents[3] / "pyproject.toml"
    assert unmanaged_file.is_file()

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return DummyApiResponse(
            {
                "model": "gpt-4.1-mini",
                "choices": [{"message": {"content": "safe"}}],
            }
        )

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/chat/completions",
            "api_key": "test-key",
            "api_model": "gpt-4.1-mini",
        },
    )

    svc.generate(
        "hello",
        context=ModelContext(metadata={"images": [{"path": str(unmanaged_file)}]}),
    )

    serialized = json.dumps(captured["payload"])
    assert "data:image" not in serialized
    assert "Consider these images" not in serialized


def test_generate_api_inlines_four_images_with_distinct_provenance_references(
    monkeypatch,
):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return DummyApiResponse(
            {
                "id": "resp_four_images",
                "model": "gpt-5.4",
                "output_text": "vision ok",
            }
        )

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    monkeypatch.setattr("app.base_services.load_blob", lambda _content_hash: b"img")

    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/responses",
            "api_key": "test-key",
            "api_model": "gpt-5.4",
        },
    )
    attachments = []
    for index in range(1, 5):
        content_hash = _content_hash(f"provenance-{index}")
        attachments.append(
            {
                "name": f"image-{index}.png",
                "type": "image/png",
                "url": f"/api/attachments/{content_hash}/image-{index}.png",
                "content_hash": content_hash,
                "relative_path": f"uploads/{content_hash}/image-{index}.png",
                "_canonical_attachment_resolved": True,
                **(
                    {
                        "display_name": "Ravine owl",
                        "source_url": "https://example.test/gallery/original",
                        "source_url_recorded_at": "2026-07-29T12:00:00Z",
                    }
                    if index == 1
                    else {}
                ),
            }
        )

    result = svc.generate("compare these", attachments=attachments)

    user_content = next(
        item["content"]
        for item in captured["payload"]["input"]
        if isinstance(item, dict)
        and item.get("role") == "user"
        and any(
            isinstance(part, dict) and part.get("type") == "input_image"
            for part in (item.get("content") or [])
        )
    )
    image_indexes = [
        index
        for index, part in enumerate(user_content)
        if isinstance(part, dict) and part.get("type") == "input_image"
    ]
    assert len(image_indexes) == 4
    for ordinal, image_index in enumerate(image_indexes, start=1):
        content_hash = _content_hash(f"provenance-{ordinal}")
        reference = user_content[image_index - 1]
        assert reference["type"] == "input_text"
        expected_label = "Ravine owl" if ordinal == 1 else f"image-{ordinal}.png"
        assert f"Image {ordinal} ({expected_label})" in reference["text"]
        assert (
            f"content_hash={content_hash} (durable attachment/sync id)"
            in reference["text"]
        )
        assert (
            "relative_path="
            f"uploads/{content_hash}/image-{ordinal}.png "
            "(current managed deployment-relative storage path)" in reference["text"]
        )
        assert (
            f"url=/api/attachments/{content_hash}/image-{ordinal}.png "
            "(reconstructable API retrieval route)" in reference["text"]
        )
        if ordinal == 1:
            assert (
                "source_url=https://example.test/gallery/original "
                "(recorded external provenance, recorded 2026-07-29T12:00:00Z; "
                "not the durable copy)" in reference["text"]
            )
    assert result["metadata"]["vision"]["native_image_input"] is True
    assert result["metadata"]["vision"]["fallback_used"] is False


def test_native_image_references_omit_unresolved_and_credentialed_provenance(
    monkeypatch,
):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return DummyApiResponse(
            {"id": "resp_safe_refs", "model": "gpt-5.4", "output_text": "ok"}
        )

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    monkeypatch.setattr("app.base_services.load_blob", lambda _hash: b"image")
    unresolved_hash = _content_hash("unresolved-client-reference")
    canonical_hash = _content_hash("canonical-signed-reference")
    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/responses",
            "api_key": "test-key",
            "api_model": "gpt-5.4",
        },
    )

    svc.generate(
        "compare",
        attachments=[
            {
                "name": "client.png",
                "type": "image/png",
                "url": f"/api/attachments/{unresolved_hash}/client.png",
                "content_hash": unresolved_hash,
                "relative_path": "workspace/private/client.png",
                "source_url": "https://example.test/client-supplied",
            },
            {
                "name": "canonical.png",
                "type": "image/png",
                "url": f"/api/attachments/{canonical_hash}/canonical.png",
                "content_hash": canonical_hash,
                "relative_path": f"uploads/{canonical_hash}/canonical.png",
                "source_url": "https://user:password@example.test/source.png",
                "_canonical_attachment_resolved": True,
            },
        ],
    )

    user_content = next(
        item["content"]
        for item in captured["payload"]["input"]
        if isinstance(item, dict)
        and item.get("role") == "user"
        and any(
            isinstance(part, dict) and part.get("type") == "input_image"
            for part in (item.get("content") or [])
        )
    )
    references = [
        str(user_content[index - 1].get("text") or "")
        for index, part in enumerate(user_content)
        if isinstance(part, dict) and part.get("type") == "input_image"
    ]
    assert references[0] == "Image 1 (client.png)"
    assert unresolved_hash not in references[0]
    assert "workspace/private" not in references[0]
    assert "client-supplied" not in references[0]
    assert f"content_hash={canonical_hash}" in references[1]
    assert f"url=/api/attachments/{canonical_hash}/canonical.png" in references[1]
    assert "user:password" not in references[1]
    assert "source_url=" not in references[1]


def test_generate_api_fallback_uses_actual_attachment_ordinal(monkeypatch):
    captured = {}

    def empty_captioner(raw, *, model=None, **_kwargs):
        return SimpleNamespace(model=model), {
            "image_caption": "",
            "placeholder": False,
        }

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return DummyApiResponse(
            {
                "id": "resp_fifth_fallback",
                "model": "gpt-5.4",
                "output_text": "vision ok",
            }
        )

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    monkeypatch.setattr("app.base_services.load_blob", lambda _content_hash: b"img")
    monkeypatch.setattr(
        "app.base_services.run_shared_vision_captioner", empty_captioner
    )

    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/responses",
            "api_key": "test-key",
            "api_model": "gpt-5.4",
        },
    )
    attachments = []
    for index in range(1, 5):
        content_hash = _content_hash(f"ordinal-{index}")
        attachments.append(
            {
                "name": f"image-{index}.png",
                "type": "image/png",
                "url": f"/api/attachments/{content_hash}/image-{index}.png",
                "content_hash": content_hash,
                "_canonical_attachment_resolved": True,
            }
        )
    attachments.append(dict(attachments[0]))
    fifth_hash = _content_hash("ordinal-5")
    attachments.append(
        {
            "name": "image-5.png",
            "type": "image/png",
            "url": f"/api/attachments/{fifth_hash}/image-5.png",
            "content_hash": fifth_hash,
            "relative_path": f"uploads/{fifth_hash}/image-5.png",
            "source_url": "https://example.test/images/fifth",
            "_canonical_attachment_resolved": True,
        }
    )

    result = svc.generate("compare these", attachments=attachments)

    content_parts = [
        part
        for item in captured["payload"]["input"]
        if isinstance(item, dict)
        for part in (item.get("content") or [])
        if isinstance(part, dict)
    ]
    assert sum(part.get("type") == "input_image" for part in content_parts) == 4
    fallback_text = next(
        str(part.get("text") or "")
        for part in content_parts
        if "Image delivery notice" in str(part.get("text") or "")
    )
    assert "Image 5 (image-5.png)" in fallback_text
    assert f"/api/attachments/{fifth_hash}/image-5.png" in fallback_text
    fallback_reference = next(
        str(part.get("text") or "")
        for part in content_parts
        if f"content_hash={fifth_hash} (durable attachment/sync id)"
        in str(part.get("text") or "")
    )
    assert f"relative_path=uploads/{fifth_hash}/image-5.png" in fallback_reference
    assert f"url=/api/attachments/{fifth_hash}/image-5.png" in fallback_reference
    assert "source_url=https://example.test/images/fifth" in fallback_reference
    vision_meta = result["metadata"]["vision"]
    assert vision_meta["native_image_input"] is True
    assert vision_meta["fallback_used"] is True
    assert vision_meta["fallback_images"] == 1


def test_generate_api_inlines_transient_camera_capture_for_responses(
    monkeypatch, tmp_path
):
    from app.services.capture_service import CaptureService

    captured = {}
    service = CaptureService(data_dir=tmp_path)
    service.metadata_root = (tmp_path / "capture-meta").resolve()
    service.metadata_root.mkdir(parents=True, exist_ok=True)
    capture = service.create_capture_from_bytes(
        b"transient-camera-image",
        filename="camera.png",
        source="camera",
        content_type="image/png",
        capture_source="chat_camera",
    )

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return DummyApiResponse(
            {
                "id": "resp_camera",
                "model": "gpt-5.4",
                "output_text": "vision ok",
            }
        )

    def missing_blob(_content_hash):
        raise FileNotFoundError("not in durable blob store")

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    monkeypatch.setattr("app.base_services.load_blob", missing_blob)
    monkeypatch.setattr("app.base_services.get_capture_service", lambda: service)

    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/responses",
            "api_key": "test-key",
            "api_model": "gpt-5.4",
        },
    )
    result = svc.generate(
        "describe the camera image",
        attachments=[
            {
                "name": "camera.png",
                "type": "image/png",
                "url": capture["url"],
                "content_hash": capture["content_hash"],
                "capture_id": capture["capture_id"],
                "origin": "captured",
                "transient": True,
            }
        ],
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
    assert image_parts[0]["image_url"].startswith("data:image/png;base64,")
    assert result["metadata"]["vision"]["native_image_input"] is True
    assert result["metadata"]["vision"]["fallback_used"] is False


def test_generate_api_caption_fallback_resolves_transient_camera_capture(
    monkeypatch, tmp_path
):
    from app.services.capture_service import CaptureService

    captured = {}
    service = CaptureService(data_dir=tmp_path)
    service.metadata_root = (tmp_path / "capture-meta").resolve()
    service.metadata_root.mkdir(parents=True, exist_ok=True)
    capture = service.create_capture_from_bytes(
        b"fallback-camera-image",
        filename="camera.png",
        source="camera",
        content_type="image/png",
        capture_source="chat_camera",
    )

    def dummy_captioner(raw, *, model=None, **_kwargs):
        assert raw == b"fallback-camera-image"
        return SimpleNamespace(model=model), {
            "image_caption": "Transient camera caption",
            "placeholder": False,
        }

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return DummyApiResponse(
            {
                "model": "text-only-model",
                "choices": [{"message": {"content": "fallback ok"}}],
            }
        )

    def missing_blob(_content_hash):
        raise FileNotFoundError("not in durable blob store")

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    monkeypatch.setattr("app.base_services.load_blob", missing_blob)
    monkeypatch.setattr("app.base_services.get_capture_service", lambda: service)
    monkeypatch.setattr(
        "app.base_services.run_shared_vision_captioner", dummy_captioner
    )

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
        "describe the camera image",
        attachments=[
            {
                "name": "camera.png",
                "type": "image/png",
                "url": capture["url"],
                "content_hash": capture["content_hash"],
                "capture_id": capture["capture_id"],
                "origin": "captured",
                "transient": True,
            }
        ],
        vision_workflow="caption",
    )

    content = captured["payload"]["messages"][-1]["content"]
    assert any(
        isinstance(part, dict)
        and "Transient camera caption" in str(part.get("text", ""))
        for part in content
    )
    vision_meta = result["metadata"]["vision"]
    assert vision_meta["fallback_used"] is True
    assert vision_meta["fallback_attachments"][0]["capture_id"] == capture["capture_id"]
    assert vision_meta["fallback_attachments"][0]["placeholder"] is False


def test_generate_api_blocks_supported_model_when_image_bytes_missing(monkeypatch):
    called = {"post": False}

    def fake_post(url, headers=None, json=None, timeout=None):
        called["post"] = True
        return DummyApiResponse({"id": "resp_unexpected", "output_text": "unexpected"})

    def missing_blob(_content_hash):
        raise FileNotFoundError("not in durable blob store")

    service = SimpleNamespace(
        capture_path=lambda _capture_id: None,
        capture_path_for_content_hash=lambda _content_hash: None,
    )
    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    monkeypatch.setattr("app.base_services.load_blob", missing_blob)
    monkeypatch.setattr("app.base_services.get_capture_service", lambda: service)

    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/responses",
            "api_key": "test-key",
            "api_model": "gpt-5.4",
        },
    )
    result = svc.generate(
        "describe the missing camera image",
        attachments=[
            {
                "name": "camera.png",
                "type": "image/png",
                "url": "/api/captures/missing-capture/content",
                "content_hash": "missing-hash",
                "capture_id": "missing-capture",
                "origin": "captured",
                "transient": True,
            }
        ],
        vision_workflow="caption",
    )

    assert called["post"] is False
    assert result["tools_used"] == []
    assert result["metadata"]["category"] == "vision_attachment_resolution_error"
    assert result["metadata"]["vision"]["native_image_input"] is False
    assert (
        result["metadata"]["vision"]["fallback_attachments"][0]["placeholder"] is True
    )


def test_generate_api_uses_local_caption_fallback_for_non_vision_models(monkeypatch):
    captured = {}
    logo_path = (
        Path(__file__).resolve().parents[3] / "docs" / "resources" / "floatlogo.png"
    )
    image_bytes = logo_path.read_bytes()
    with Image.open(io.BytesIO(image_bytes)) as decoded:
        assert decoded.width > 32
        assert decoded.height > 32
        assert decoded.getbbox() is not None

    def dummy_captioner(raw, *, model=None, **_kwargs):
        assert raw == image_bytes
        return SimpleNamespace(model=model), {
            "image_caption": "Local fallback caption",
            "placeholder": False,
        }

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
        "app.base_services.load_blob", lambda _content_hash: image_bytes
    )
    monkeypatch.setattr(
        "app.base_services.run_shared_vision_captioner", dummy_captioner
    )

    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/chat/completions",
            "api_key": "test-key",
            "api_model": "text-only-model",
            "vision_model": "local-caption-model",
            "image_caption_engine": "local",
        },
    )
    content_hash = hashlib.sha256(image_bytes).hexdigest()
    result = svc.generate(
        "describe the image",
        attachments=[
            {
                "name": "fallback.png",
                "type": "image/png",
                "url": f"/api/attachments/{content_hash}/fallback.png",
                "content_hash": content_hash,
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


def test_generate_api_reuses_canonical_stored_caption_before_loading_vision_model(
    monkeypatch,
):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return DummyApiResponse(
            {
                "model": "text-only-model",
                "choices": [{"message": {"content": "fallback ok"}}],
            }
        )

    def unexpected_caption(*_args, **_kwargs):
        raise AssertionError("stored caption should prevent local model loading")

    def unexpected_blob(_content_hash):
        raise AssertionError("stored caption should prevent an unnecessary blob read")

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    monkeypatch.setattr("app.base_services.load_blob", unexpected_blob)
    monkeypatch.setattr(
        "app.base_services.run_shared_vision_captioner", unexpected_caption
    )
    content_hash = _content_hash("stored-caption")
    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/chat/completions",
            "api_key": "test-key",
            "api_model": "text-only-model",
            "vision_model": "local-caption-model",
            "image_caption_engine": "local",
        },
    )

    result = svc.generate(
        "what is in the image?",
        attachments=[
            {
                "name": "meal.png",
                "type": "image/png",
                "url": f"/api/attachments/{content_hash}/meal.png",
                "content_hash": content_hash,
                "relative_path": f"uploads/{content_hash}/meal.png",
                "_canonical_attachment_resolved": True,
                "caption": "A bowl of noodles with greens.",
                "caption_status": "manual",
                "caption_model": "manual-caption",
            }
        ],
        vision_workflow="image_qa",
    )

    content = captured["payload"]["messages"][-1]["content"]
    assert any(
        "Saved attachment caption: Image 1 (meal.png): "
        "A bowl of noodles with greens." in str(part.get("text", ""))
        for part in content
        if isinstance(part, dict)
    )
    detail = result["metadata"]["vision"]["fallback_attachments"][0]
    assert detail["caption"] == "A bowl of noodles with greens."
    assert detail["caption_model"] == "manual-caption"


@pytest.mark.parametrize("caption_engine", ["cloud", "off"])
def test_chat_fallback_does_not_implicitly_invoke_cloud_or_disabled_caption_engine(
    monkeypatch,
    caption_engine,
):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return DummyApiResponse(
            {
                "model": "text-only-model",
                "choices": [{"message": {"content": "fallback ok"}}],
            }
        )

    def unexpected_caption(*_args, **_kwargs):
        raise AssertionError("chat fallback must not invoke this caption engine")

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    monkeypatch.setattr("app.base_services.load_blob", lambda _hash: b"image")
    monkeypatch.setattr(
        "app.base_services.run_shared_vision_captioner", unexpected_caption
    )
    content_hash = _content_hash(f"{caption_engine}-fallback")
    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/chat/completions",
            "api_key": "test-key",
            "api_model": "text-only-model",
            "image_caption_engine": caption_engine,
        },
    )

    result = svc.generate(
        "describe it",
        attachments=[
            {
                "name": "image.png",
                "type": "image/png",
                "content_hash": content_hash,
            }
        ],
        vision_workflow="caption",
    )

    content = captured["payload"]["messages"][-1]["content"]
    notice = next(
        str(part.get("text") or "")
        for part in content
        if isinstance(part, dict) and "Image delivery notice" in str(part.get("text"))
    )
    if caption_engine == "cloud":
        assert "did not send the image to a separate cloud captioning service" in notice
    else:
        assert "Image caption generation is disabled" in notice
    detail = result["metadata"]["vision"]["fallback_attachments"][0]
    assert detail["placeholder"] is True


def test_two_consecutive_chat_fallbacks_reuse_one_shared_captioner(monkeypatch):
    import workers.multimodal as multimodal

    captured = []
    created = []
    logo_path = (
        Path(__file__).resolve().parents[3] / "docs" / "resources" / "floatlogo.png"
    )
    image_bytes = logo_path.read_bytes()
    with Image.open(io.BytesIO(image_bytes)) as decoded:
        assert decoded.width > 32 and decoded.height > 32
        assert decoded.getbbox() is not None

    class CountingCaptioner:
        def __init__(self, model):
            self.model = model
            self.runs = 0
            created.append(self)

        def run(self, raw):
            assert raw == image_bytes
            self.runs += 1
            return {"image_caption": "The Float logo.", "placeholder": False}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.append(json)
        return DummyApiResponse(
            {
                "model": "text-only-model",
                "choices": [{"message": {"content": "fallback ok"}}],
            }
        )

    multimodal.reset_shared_vision_captioner()
    monkeypatch.setattr(multimodal, "VisionCaptioner", CountingCaptioner)
    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    monkeypatch.setattr("app.base_services.load_blob", lambda _hash: image_bytes)
    content_hash = hashlib.sha256(image_bytes).hexdigest()
    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/chat/completions",
            "api_key": "test-key",
            "api_model": "text-only-model",
            "vision_model": "test/local-caption-model",
            "image_caption_engine": "local",
        },
    )
    attachment = {
        "name": "floatlogo.png",
        "type": "image/png",
        "content_hash": content_hash,
    }

    try:
        svc.generate("first", attachments=[attachment], vision_workflow="caption")
        svc.generate("second", attachments=[attachment], vision_workflow="caption")
    finally:
        multimodal.reset_shared_vision_captioner()

    assert len(captured) == 2
    assert len(created) == 1
    assert created[0].runs == 2


def test_generate_api_uses_placeholder_caption_without_hashlib_crash(monkeypatch):
    captured = {}

    def empty_captioner(raw, *, model=None, **_kwargs):
        assert raw == b"fallback-image"
        return SimpleNamespace(model=model), {
            "image_caption": "",
            "placeholder": False,
        }

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
    monkeypatch.setattr(
        "app.base_services.run_shared_vision_captioner", empty_captioner
    )

    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/chat/completions",
            "api_key": "test-key",
            "api_model": "text-only-model",
            "vision_model": "local-caption-model",
        },
    )

    content_hash = _content_hash("fallback-without-caption")
    result = svc.generate(
        "describe the image",
        attachments=[
            {
                "name": "fallback-no-caption.png",
                "type": "image/png",
                "url": f"/api/attachments/{content_hash}/fallback-no-caption.png",
                "content_hash": content_hash,
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
            and "Image delivery notice" in str(part.get("text", ""))
        ),
        "",
    )
    assert "selected model did not receive visual content" in fallback_text
    assert "Do not infer visual details" in fallback_text
    vision_meta = result["metadata"]["vision"]
    assert vision_meta["native_image_input"] is False
    assert vision_meta["fallback_used"] is True
    assert vision_meta["fallback_attachments"][0]["placeholder"] is True


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

    recalled_hash = _content_hash("recalled-sequence")
    svc.generate(
        [],
        attachments=[
            {
                "name": "recalled.png",
                "type": "image/png",
                "url": f"/api/attachments/{recalled_hash}/recalled.png",
                "content_hash": recalled_hash,
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
    logo_path = (
        Path(__file__).resolve().parents[3] / "docs" / "resources" / "floatlogo.png"
    )
    image_bytes = logo_path.read_bytes()
    with Image.open(io.BytesIO(image_bytes)) as decoded:
        assert decoded.width > 32 and decoded.height > 32
        assert decoded.getbbox() is not None

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
        "app.base_services.load_blob", lambda _content_hash: image_bytes
    )

    svc = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/responses",
            "api_key": "test-key",
            "api_model": "gpt-5.4",
        },
    )
    recalled_hash = hashlib.sha256(image_bytes).hexdigest()
    attachment = {
        "name": "recalled.png",
        "type": "image/png",
        "url": f"/api/attachments/{recalled_hash}/recalled.png",
        "content_hash": recalled_hash,
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
    assert any(
        isinstance(part, dict) and part.get("type") == "input_image"
        for part in (input_items[-1].get("content") or [])
    )
    assert all(
        not any(
            isinstance(part, dict) and part.get("type") == "input_image"
            for part in (item.get("content") or [])
        )
        for item in input_items[:-1]
        if isinstance(item, dict)
    )
    encoded = image_parts[0]["image_url"].split(",", 1)[1]
    assert hashlib.sha256(base64.b64decode(encoded)).hexdigest() == recalled_hash
