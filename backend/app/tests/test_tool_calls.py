import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.base_services import (  # noqa: E402
    LLMService,
    MemoryManager,
    ModelContext,
    _convert_tools_for_openai,
    _extract_native_responses_tool_calls,
)
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


def _make_responses_service(monkeypatch, payload):
    service = LLMService(
        config={
            "api_key": "test",
            "api_url": "http://test/v1/responses",
            "api_model": "gpt-5.5",
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
                            "params": {
                                "text": "chlorophyll makes plants green",
                                "namespace": "facts",
                            },
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
        {
            "name": "memory.save",
            "args": {"text": "chlorophyll makes plants green", "namespace": "facts"},
        }
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


def test_responses_commentary_tool_text_is_not_visible(monkeypatch):
    payload = {
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "phase": "commentary",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {
                                "tool": "remember",
                                "args": {
                                    "key": "snack",
                                    "value": "saffron toast",
                                },
                            }
                        ),
                    }
                ],
            },
            {
                "type": "message",
                "phase": "final_answer",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Remembered the snack note.",
                    }
                ],
            },
        ]
    }
    svc = _make_responses_service(monkeypatch, payload)
    result = svc._generate_via_api("hi", ModelContext())

    assert result["text"] == "Remembered the snack note."
    assert "[[tool_call" not in result["text"]
    assert result["tools_used"] == [
        {"name": "remember", "args": {"key": "snack", "value": "saffron toast"}}
    ]
    assert result["metadata"].get("inline_tool_payload")


def test_responses_tool_only_output_stays_continuable(monkeypatch):
    payload = {
        "output": [
            {
                "type": "message",
                "phase": "final_answer",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {"tool": "recall", "args": {"key": "snack"}}
                        ),
                    }
                ],
            }
        ]
    }
    svc = _make_responses_service(monkeypatch, payload)
    result = svc._generate_via_api("hi", ModelContext())

    assert result["text"] == "[[tool_call:0]]"
    assert result["tools_used"] == [{"name": "recall", "args": {"key": "snack"}}]
    assert result["metadata"].get("inline_tool_payload")


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
    assert result["tools_used"] == [
        {"name": "recall", "args": {"key": "tea_party_plans"}}
    ]
    assert "[[tool_call:0]]" in result["text"]


def test_convert_tools_for_openai_preserves_native_computer_tool():
    tools = [
        {
            "type": "computer_use_preview",
            "display_width": 1280,
            "display_height": 720,
            "environment": "browser",
        }
    ]

    converted = _convert_tools_for_openai(tools)

    assert converted == tools


def test_convert_tools_for_openai_uses_responses_function_shape():
    tools = [
        {
            "name": "weather",
            "description": "Look up a forecast.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }
    ]

    converted = _convert_tools_for_openai(tools, responses_api=True)

    assert converted == [
        {
            "type": "function",
            "name": "weather",
            "description": "Look up a forecast.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }
    ]


def test_convert_tools_for_openai_removes_schema_titles_without_mutating_input():
    tools = [
        {
            "name": "remember",
            "parameters": {
                "type": "object",
                "title": "RememberInput",
                "properties": {
                    "title": {
                        "type": "string",
                        "title": "Memory title",
                    },
                    "tags": {
                        "type": "array",
                        "title": "Tags",
                        "items": {"type": "string", "title": "Tag"},
                    },
                },
                "required": ["title"],
            },
        }
    ]

    converted = _convert_tools_for_openai(tools, responses_api=True)

    assert converted[0]["parameters"] == {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title"],
    }
    assert tools[0]["parameters"]["title"] == "RememberInput"
    assert tools[0]["parameters"]["properties"]["title"]["title"] == "Memory title"


def test_generate_via_api_uses_responses_tool_shape_for_gpt5(monkeypatch):
    captured = {}
    service = LLMService(
        config={
            "api_key": "test",
            "api_url": "http://test/v1/chat/completions",
            "api_model": "gpt-5",
        }
    )
    context = ModelContext(
        tools=[
            {
                "name": "weather",
                "description": "Look up a forecast.",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }
        ]
    )

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return DummyResponse({"output_text": "done"})

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)

    result = service._generate_via_api("hi", context)

    assert result["text"] == "done"
    assert captured["url"] == "http://test/v1/responses"
    assert captured["payload"]["tools"] == [
        {
            "type": "function",
            "name": "weather",
            "description": "Look up a forecast.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }
    ]


def test_generate_via_api_parses_responses_function_call(monkeypatch):
    function_call = {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call_1",
        "name": "tool_help",
        "arguments": json.dumps({"tool_name": "recall"}),
    }
    service = _make_responses_service(
        monkeypatch,
        {
            "output": [
                function_call,
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Checking that tool."}],
                },
            ]
        },
    )

    result = service._generate_via_api("help me use recall", ModelContext())

    assert result["text"] == "Checking that tool."
    assert result["tools_used"] == [
        {
            "name": "tool_help",
            "args": {"tool_name": "recall"},
            "call_id": "call_1",
            "response_item": function_call,
        }
    ]


def test_generate_via_api_uses_experimental_responses_ws_transport(monkeypatch):
    captured = {}

    class FakeTransport:
        def __init__(self, **kwargs):
            captured["transport_kwargs"] = kwargs
            self.api_key = kwargs["api_key"]
            self.url = kwargs["url"]

        def run_response(self, **kwargs):
            captured["run_kwargs"] = kwargs
            return {
                "text": "done over ws",
                "thought": "",
                "tools_used": [],
                "metadata": {"transport": "openai_responses_ws"},
            }

    def fail_post(*args, **kwargs):
        raise AssertionError("HTTP path should not be used")

    monkeypatch.setattr(
        "app.base_services.OpenAIResponsesWebSocketTransport", FakeTransport
    )
    monkeypatch.setattr("app.base_services.http_session.post", fail_post)

    service = LLMService(
        config={
            "api_key": "test",
            "api_url": "https://api.openai.com/v1/responses",
            "api_model": "gpt-5.4",
            "openai_responses_ws_enabled": True,
            "openai_responses_ws_url": "wss://example.test/v1/responses",
        }
    )

    result = service._generate_via_api(
        "hi",
        ModelContext(),
        session_id="sess",
        stream_message_id="m1",
        previous_response_id="resp_prev",
        tool_executor=lambda call: {"ok": True},
    )

    assert result["text"] == "done over ws"
    assert captured["transport_kwargs"]["url"] == "wss://example.test/v1/responses"
    assert captured["transport_kwargs"]["api_key"] == "test"
    assert captured["run_kwargs"]["session_id"] == "sess"
    assert captured["run_kwargs"]["stream_message_id"] == "m1"
    assert captured["run_kwargs"]["payload"]["previous_response_id"] == "resp_prev"
    assert callable(captured["run_kwargs"]["tool_executor"])


def test_extract_native_responses_tool_calls_parses_computer_call():
    payload = {
        "output": [
            {
                "type": "computer_call",
                "call_id": "call-computer-1",
                "action": {"type": "click", "x": 18, "y": 44},
            }
        ]
    }

    tools_used = _extract_native_responses_tool_calls(payload)

    assert tools_used == [
        {
            "name": "computer.act",
            "args": {
                "session_id": "call-computer-1",
                "actions": [{"type": "click", "x": 18, "y": 44}],
                "native_call_id": "call-computer-1",
            },
            "native": payload["output"][0],
        }
    ]


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
        result = memory_tools.legacy_memory_save(
            user="tester", signature=signature, **args
        )
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
