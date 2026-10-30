import json

import pytest
from app.base_services import LLMService
from app.provider_transports.model_capabilities import (
    supports_openai_reasoning_controls,
    supports_openai_reasoning_summary,
)
from app.provider_transports.openai_responses_ws import (
    extract_response_reasoning_summary,
)


class ProviderResponse:
    status_code = 200
    headers = {}

    def __init__(self, payload, streaming=False):
        self.payload = payload
        self.streaming = streaming
        self.text = json.dumps(payload)

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload

    def iter_lines(self, **_kwargs):
        yield json.dumps({"type": "response.completed", "response": self.payload})
        yield "[DONE]"


@pytest.mark.parametrize("streaming", [False, True])
def test_gpt6_summary_request_and_final_metadata(monkeypatch, streaming):
    captured = {}
    usage = {
        "input_tokens": 4615,
        "output_tokens": 113,
        "output_tokens_details": {"reasoning_tokens": 70},
        "total_tokens": 4728,
    }
    response = {
        "id": "resp-summary",
        "model": "gpt-6.1-sol",
        "status": "completed",
        "usage": usage,
        "output": [
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "Checked the request."}],
                "encrypted_content": "must-not-be-displayed",
            },
            {"type": "message", "content": [{"type": "output_text", "text": "Done."}]},
        ],
    }

    def fake_post(url, headers=None, json=None, **_kwargs):
        captured.update(url=url, payload=json)
        response_for_request = dict(response)
        response_for_request["output"] = [dict(item) for item in response["output"]]
        if json.get("reasoning", {}).get("summary") != "auto":
            response_for_request["output"][0]["summary"] = []
        return ProviderResponse(response_for_request, streaming)

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    service = LLMService(
        mode="api",
        config={
            "api_url": "https://example.test/v1/chat/completions",
            "api_key": "fixture-key",
            "api_model": "gpt-6.1-sol",
        },
    )
    events = []
    result = service.generate(
        "Check this.",
        stream_consumer=events.append if streaming else None,
        stream_message_id="summary-turn",
        reasoning={"effort": "high"},
    )
    assert captured["url"].endswith("/responses")
    assert captured["payload"]["reasoning"] == {"effort": "high", "summary": "auto"}
    assert result["text"] == "Done."
    assert result["thought"] == "Checked the request."
    assert result["metadata"]["usage"] == usage
    assert result["metadata"]["model_received"] == "gpt-6.1-sol"


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-6.1-sol", "gpt-5.6-sol", "o3"])
def test_known_reasoning_models(model):
    assert supports_openai_reasoning_controls(model)


@pytest.mark.parametrize(
    "model",
    ["gpt-60", "gpt-6.2-unknown", "gpt-5-chat-latest", "chat-latest", "custom-model"],
)
def test_unknown_or_chat_models_do_not_claim_reasoning_controls(model):
    assert not supports_openai_reasoning_controls(model)


@pytest.mark.parametrize(
    "model,supported",
    [
        ("gpt-realtime-2", True),
        ("gpt-realtime-2.1", True),
        ("gpt-realtime-2.1-2026-10-01", True),
        ("gpt-realtime-20", False),
        ("gpt-realtime-2.preview", False),
        ("gpt-realtime-3", False),
    ],
)
def test_realtime_reasoning_controls_preserve_minor_versions_without_summaries(
    model, supported
):
    assert supports_openai_reasoning_controls(model) is supported
    assert not supports_openai_reasoning_summary(model)


def test_compatible_server_keeps_chat_endpoint_and_unknown_summary_options(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, **_kwargs):
        captured.update(url=url, payload=json)
        return ProviderResponse(
            {
                "model": "gpt-6.1-sol",
                "choices": [{"message": {"content": "Done."}}],
            }
        )

    monkeypatch.setattr("app.base_services.http_session.post", fake_post)
    service = LLMService(
        mode="server",
        config={"server_url": "http://127.0.0.1:12345/v1/chat/completions"},
    )
    result = service.generate("Check this.", model="gpt-6.1-sol")
    assert captured["url"].endswith("/chat/completions")
    assert "reasoning" not in captured["payload"]
    assert result["text"] == "Done."


def test_only_exposed_summary_text_is_extracted():
    payload = {
        "output": [
            {
                "type": "reasoning",
                "encrypted_content": "private",
                "content": [{"text": "private"}],
                "summary": [],
            }
        ]
    }
    assert extract_response_reasoning_summary(payload) == ""
