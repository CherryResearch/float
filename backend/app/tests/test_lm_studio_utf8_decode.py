import json
import sys
from pathlib import Path


def _import_service():
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from app.base_services import LLMService, http_session

    return LLMService, http_session


class _FakeResponse:
    status_code = 200

    def __init__(self, lines, *, decode_to_text=False):
        self._lines = list(lines)
        # Mimic Requests defaulting to latin-1 when charset headers are absent.
        self.encoding = "ISO-8859-1"
        self.decode_to_text = bool(decode_to_text)

    def raise_for_status(self):
        return None

    def iter_lines(self, decode_unicode=False):
        for raw in self._lines:
            if self.decode_to_text and isinstance(raw, (bytes, bytearray)):
                # Simulate a buggy/misconfigured adapter that pre-decodes bytes
                # to text with latin-1 before our parser sees the line.
                yield raw.decode(self.encoding, errors="replace")
                continue
            if decode_unicode and isinstance(raw, (bytes, bytearray)):
                yield raw.decode(self.encoding or "utf-8", errors="replace")
            else:
                yield raw


def _run_stream(monkeypatch, *, decode_to_text=False):
    LLMService, http_session = _import_service()

    service = LLMService()
    service.mode = "server"

    streamed_text = "called \u201cthe heart.\u201d Its door read caf\u00e9 \U0001f600"
    chunk = json.dumps(
        {"choices": [{"delta": {"content": streamed_text}, "finish_reason": None}]},
        ensure_ascii=False,
    )
    sse_lines = [f"data: {chunk}\n".encode("utf-8"), b"data: [DONE]\n"]

    def fake_post(*args, **kwargs):
        return _FakeResponse(sse_lines, decode_to_text=decode_to_text)

    monkeypatch.setattr(http_session, "post", fake_post)

    events = []

    def consumer(event):
        events.append(event)

    out = service._consume_streaming_response(
        "http://example.invalid",
        headers={},
        payload={"stream": True},
        session_id="sess-1",
        stream_consumer=consumer,
        stream_message_id="mid-1",
    )
    return streamed_text, out, events


def test_server_streaming_forces_utf8_decoding_for_byte_lines(monkeypatch):
    streamed_text, out, events = _run_stream(monkeypatch, decode_to_text=False)

    assert out and out.get("text") == streamed_text
    assert any(
        e.get("type") == "content" and e.get("content") == streamed_text for e in events
    )


def test_server_streaming_repairs_latin1_decoded_lines(monkeypatch):
    streamed_text, out, events = _run_stream(monkeypatch, decode_to_text=True)

    assert out and out.get("text") == streamed_text
    assert any(
        e.get("type") == "content" and e.get("content") == streamed_text for e in events
    )
