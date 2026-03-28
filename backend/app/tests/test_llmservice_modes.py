import json
from types import SimpleNamespace

from app.base_services import LLMService


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


def test_generate_local(monkeypatch):
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
    monkeypatch.setattr("app.base_services.load_blob", lambda _content_hash: b"img-bytes")

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
    monkeypatch.setattr("app.base_services.load_blob", lambda _content_hash: b"img-bytes")

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
        isinstance(part, dict) and part.get("type") == "image_url"
        for part in trailing
    )
