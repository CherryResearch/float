import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.base_services import LLMService, ModelContext, MemoryManager  # noqa: E402
from app.tools import memory as memory_tools  # noqa: E402
from app.utils import generate_signature  # noqa: E402


class DummyResponse:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def _make_service(monkeypatch, payload):
    service = LLMService(
        config={
            "api_key": "test",
            "api_url": "http://test",
            "api_model": "gpt",
        }
    )

    def fake_post(url, headers=None, json=None, timeout=None):
        return DummyResponse(payload)

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    return service


def test_tool_calls_parsed(monkeypatch):
    payload = {
        "choices": [
            {
                "message": {
                    "content": "done",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "weather",
                                "arguments": json.dumps({"city": "Paris"}),
                            },
                        }
                    ],
                }
            }
        ]
    }
    svc = _make_service(monkeypatch, payload)
    result = svc._generate_via_api("hi", ModelContext())
    expected = [{"name": "weather", "args": {"city": "Paris"}}]
    assert result["tools_used"] == expected


def test_function_call_parsed(monkeypatch):
    payload = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "function_call": {
                        "name": "search",
                        "arguments": json.dumps({"q": "test"}),
                    },
                }
            }
        ]
    }
    svc = _make_service(monkeypatch, payload)
    result = svc._generate_via_api("hi", ModelContext())
    expected = [{"name": "search", "args": {"q": "test"}}]
    assert result["tools_used"] == expected


def test_inline_tool_payload_parsed(monkeypatch):
    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "tool": "memory.save",
                            "params": {"text": "chlorophyll makes plants green", "namespace": "facts"},
                        }
                    )
                }
            }
        ]
    }
    svc = _make_service(monkeypatch, payload)
    result = svc._generate_via_api("hi", ModelContext())
    assert result["text"] == "[[tool_call:0]]"
    assert result["tools_used"] == [
        {"name": "memory.save", "args": {"text": "chlorophyll makes plants green", "namespace": "facts"}}
    ]
    assert result["metadata"].get("inline_tool_payload")


def test_inline_tool_payload_preserves_text_and_multiple(monkeypatch):
    payload = {
        "choices": [
            {
                "message": {
                    "content": (
                        "Here is context. "
                        '{"tool":"recall","args":{"key":"alpha"}} '
                        "Then follow up. "
                        '{"tool":"recall","args":{"key":"beta"}} '
                        "Done."
                    )
                }
            }
        ]
    }
    svc = _make_service(monkeypatch, payload)
    result = svc._generate_via_api("hi", ModelContext())
    assert result["tools_used"] == [
        {"name": "recall", "args": {"key": "alpha"}},
        {"name": "recall", "args": {"key": "beta"}},
    ]
    assert "Here is context." in result["text"]
    assert "Done." in result["text"]
    assert "[[tool_call:0]]" in result["text"]
    assert "[[tool_call:1]]" in result["text"]
    assert '"tool"' not in result["text"]
    payloads = result["metadata"].get("inline_tool_payloads") or []
    assert len(payloads) == 2


def test_harmony_tool_call_parsed(monkeypatch):
    payload = {
        "choices": [
            {
                "message": {
                    "content": (
                        "<|channel|>commentary to=recall <|constrain|>json "
                        '<|message|>{"key":"tea_party_plans"}'
                    )
                }
            }
        ]
    }
    svc = _make_service(monkeypatch, payload)
    result = svc._generate_via_api("hi", ModelContext())
    assert result["tools_used"] == [{"name": "recall", "args": {"key": "tea_party_plans"}}]
    assert "[[tool_call:0]]" in result["text"]


def test_legacy_memory_save_tool(monkeypatch):
    mgr = MemoryManager(config={})
    memory_tools.set_manager(mgr)
    args = {
        "text": "chlorophyll makes plants green",
        "namespace": "facts",
        "tags": ["biology"],
        "privacy": "local",
    }
    signature = generate_signature("tester", "memory.save", args)
    try:
        result = memory_tools.legacy_memory_save(user="tester", signature=signature, **args)
    finally:
        memory_tools.set_manager(None)
    assert result["status"] == "ok"
    key = result["key"]
    assert key.startswith("facts:chlorophyll-makes-plants-green")
    stored = mgr.get_item(key)
    assert stored is not None
    assert stored["value"]["text"] == args["text"]
    assert stored["value"]["tags"] == ["biology"]
    assert stored["sensitivity"] == "personal"
