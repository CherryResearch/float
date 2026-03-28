import json


def _build_sse_lines(*chunks):
    lines = []
    for chunk in chunks:
        payload = json.dumps(chunk)
        lines.append(f"data: {payload}\n".encode("utf-8"))
    lines.append(b"data: [DONE]\n")
    return lines


class FakeResponse:
    status_code = 200

    def __init__(self, lines):
        self._lines = list(lines)
        self.encoding = "utf-8"

    def raise_for_status(self):
        return None

    def iter_lines(self, decode_unicode=False):
        for raw in self._lines:
            if decode_unicode and isinstance(raw, (bytes, bytearray)):
                yield raw.decode(self.encoding or "utf-8")
            else:
                yield raw


def _run_stream(monkeypatch, lines):
    from app.base_services import LLMService, http_session

    service = LLMService()

    def fake_post(*_args, **_kwargs):
        return FakeResponse(lines)

    monkeypatch.setattr(http_session, "post", fake_post)
    return service._consume_streaming_response(
        "http://example.invalid",
        headers={},
        payload={"stream": True},
        session_id="sess-1",
        stream_consumer=None,
        stream_message_id="mid-1",
    )


def test_streaming_final_message_reasoning(monkeypatch):
    lines = _build_sse_lines(
        {
            "choices": [
                {"delta": {"content": "Hello"}, "finish_reason": None},
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "content": "Hello",
                        "reasoning": [{"text": "Thought"}],
                    },
                    "finish_reason": "stop",
                }
            ]
        },
    )

    out = _run_stream(monkeypatch, lines)
    assert out and out.get("text") == "Hello"
    assert out.get("thought") == "Thought"


def test_streaming_reasoning_channel_items(monkeypatch):
    lines = _build_sse_lines(
        {
            "choices": [
                {
                    "delta": {
                        "content": [
                            {"type": "reasoning", "text": "Plan"},
                            {"type": "text", "text": "Result"},
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        }
    )

    out = _run_stream(monkeypatch, lines)
    assert out and out.get("text") == "Result"
    assert out.get("thought") == "Plan"


def test_streaming_prefers_first_output_source(monkeypatch):
    lines = _build_sse_lines(
        {
            "choices": [
                {"delta": {"content": "Hello"}, "finish_reason": None},
            ]
        },
        {"type": "response.output_text.delta", "delta": "Hello"},
        {"type": "response.reasoning.delta", "delta": "Think"},
    )

    out = _run_stream(monkeypatch, lines)
    assert out and out.get("text") == "Hello"
    assert out.get("thought") == "Think"
