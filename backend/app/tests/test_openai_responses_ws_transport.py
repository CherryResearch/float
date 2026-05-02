import json

from app.provider_transports.openai_responses_ws import (
    OpenAIResponsesWebSocketTransport,
    extract_response_function_calls,
    extract_response_text,
)


class FakeWebSocket:
    connected = True

    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []
        self.closed = False

    def send(self, data):
        self.sent.append(json.loads(data))

    def recv(self):
        if not self.frames:
            raise TimeoutError("no frames")
        return json.dumps(self.frames.pop(0))

    def close(self):
        self.closed = True
        self.connected = False


def test_extract_response_helpers_parse_text_and_function_calls():
    payload = {
        "id": "resp_1",
        "output": [
            {
                "id": "msg_1",
                "content": [{"type": "output_text", "text": "hello"}],
            },
            {
                "type": "function_call",
                "name": "weather",
                "call_id": "call_1",
                "arguments": '{"city":"Paris"}',
            },
        ],
    }

    assert extract_response_text(payload) == "hello"
    assert extract_response_function_calls(payload) == [
        {
            "name": "weather",
            "args": {"city": "Paris"},
            "call_id": "call_1",
            "response_item": payload["output"][1],
        }
    ]


def test_transport_sends_response_create_and_streams_text():
    ws = FakeWebSocket(
        [
            {"type": "response.output_text.delta", "delta": "hi"},
            {
                "type": "response.done",
                "response": {
                    "id": "resp_1",
                    "model": "gpt-5.4",
                    "output": [],
                },
            },
        ]
    )
    events = []
    transport = OpenAIResponsesWebSocketTransport(
        api_key="test",
        connect_factory=lambda *args, **kwargs: ws,
    )

    result = transport.run_response(
        session_id="sess",
        payload={"model": "gpt-5.4", "input": "user: hello"},
        stream_consumer=events.append,
        stream_message_id="m1",
    )

    assert result["text"] == "hi"
    assert result["metadata"]["response_id"] == "resp_1"
    assert result["metadata"]["transport"] == "openai_responses_ws"
    assert ws.sent[0]["type"] == "response.create"
    assert ws.sent[0]["model"] == "gpt-5.4"
    assert events == [
        {
            "type": "content",
            "content": "hi",
            "session_id": "sess",
            "message_id": "m1",
            "transport": "openai_responses_ws",
        }
    ]


def test_transport_uses_previous_response_id_for_incremental_list_turn():
    first_input = {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "hello"}],
    }
    second_input = {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "again"}],
    }
    ws = FakeWebSocket(
        [
            {"type": "response.done", "response": {"id": "resp_1", "output": []}},
            {"type": "response.done", "response": {"id": "resp_2", "output": []}},
        ]
    )
    transport = OpenAIResponsesWebSocketTransport(
        api_key="test",
        connect_factory=lambda *args, **kwargs: ws,
    )

    transport.run_response(
        session_id="sess",
        payload={"model": "gpt-5.4", "input": [first_input]},
    )
    transport.run_response(
        session_id="sess",
        payload={"model": "gpt-5.4", "input": [first_input, second_input]},
    )

    assert ws.sent[1]["previous_response_id"] == "resp_1"
    assert ws.sent[1]["input"] == [second_input]


def test_transport_executes_tool_call_and_continues_with_output():
    ws = FakeWebSocket(
        [
            {
                "type": "response.done",
                "response": {
                    "id": "resp_tool",
                    "output": [
                        {
                            "type": "function_call",
                            "name": "weather",
                            "call_id": "call_1",
                            "arguments": '{"city":"Paris"}',
                        }
                    ],
                },
            },
            {"type": "response.output_text.delta", "delta": "sunny"},
            {
                "type": "response.done",
                "response": {"id": "resp_final", "output": []},
            },
        ]
    )
    calls = []
    transport = OpenAIResponsesWebSocketTransport(
        api_key="test",
        connect_factory=lambda *args, **kwargs: ws,
    )

    result = transport.run_response(
        session_id="sess",
        payload={
            "model": "gpt-5.4",
            "input": "user: weather?",
            "tools": [{"type": "function", "name": "weather"}],
        },
        tool_executor=lambda call: calls.append(call) or {"forecast": "sunny"},
    )

    assert result["text"] == "sunny"
    assert result["tools_used"] == [
        {"name": "weather", "args": {"city": "Paris"}, "call_id": "call_1"}
    ]
    assert calls[0]["name"] == "weather"
    assert calls[0]["args"] == {"city": "Paris"}
    assert ws.sent[1]["previous_response_id"] == "resp_tool"
    assert ws.sent[1]["input"] == [
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"forecast": "sunny"}',
        }
    ]
