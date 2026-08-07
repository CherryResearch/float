import copy
import json
from pathlib import Path

import pytest
from app.utils import oai_api_capture


def _tool_catalog_lists(value):
    catalogs = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "tools" and isinstance(nested, list):
                catalogs.append(nested)
            catalogs.extend(_tool_catalog_lists(nested))
    elif isinstance(value, list):
        for nested in value:
            catalogs.extend(_tool_catalog_lists(nested))
    return catalogs


@pytest.mark.parametrize(
    "lifecycle_event",
    [
        "response.created",
        "response.queued",
        "response.in_progress",
        "response.completed",
        "response.incomplete",
        "response.failed",
        "response.done",
    ],
)
def test_stream_capture_keeps_one_tool_catalog_and_preserves_function_deltas(
    lifecycle_event, tmp_path, monkeypatch
):
    monkeypatch.setattr(oai_api_capture, "LOG_DIR", tmp_path)
    request_tools = [
        {
            "type": "function",
            "name": "read_file",
            "description": "Read a file.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "type": "function",
            "name": "write_file",
            "description": "Write a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    ]
    provider_tools = copy.deepcopy(request_tools)
    provider_tools[0]["strict"] = True
    provider_tools[0]["description"] = "Read a local file."
    provider_tools[1].pop("description")
    provider_tools[1]["output_schema"] = None

    function_deltas = [
        {
            "type": "response.function_call_arguments.delta",
            "sequence_number": 3,
            "item_id": "fc_1",
            "output_index": 0,
            "delta": '{"path":"backend/',
        },
        {
            "type": "response.function_call_arguments.delta",
            "sequence_number": 4,
            "item_id": "fc_1",
            "output_index": 0,
            "delta": 'app/routes.py"}',
        },
    ]
    lifecycle_snapshot = {
        "type": lifecycle_event,
        "response": {
            "id": "resp_capture_dedup",
            "tools": copy.deepcopy(provider_tools),
            "output": [
                {
                    "type": "function_call",
                    "name": "read_file",
                    "call_id": "call_1",
                    "arguments": '{"path":"backend/app/routes.py"}',
                }
            ],
        },
    }
    stream_events = [
        function_deltas[0],
        lifecycle_snapshot,
        function_deltas[1],
    ]

    request_payload = {
        "model": "gpt-5.6-sol",
        "input": "Inspect the route.",
        "tools": request_tools,
        "stream": True,
    }
    response_payload = {
        "id": "resp_capture_dedup",
        "tools": copy.deepcopy(provider_tools),
        "output": lifecycle_snapshot["response"]["output"],
        "stream_events": stream_events,
    }
    original_request = copy.deepcopy(request_payload)
    original_response = copy.deepcopy(response_payload)

    capture_path = oai_api_capture.write_capture(
        endpoint="https://api.openai.com/v1/responses",
        request_payload=request_payload,
        response_payload=response_payload,
        session_id="session-1",
        message_id="message-1",
    )

    assert capture_path is not None
    capture = json.loads(Path(capture_path).read_text(encoding="utf-8"))
    assert request_payload == original_request
    assert response_payload == original_response
    assert _tool_catalog_lists(capture) == [request_tools]

    catalog_metadata = capture["tool_catalog"]
    assert catalog_metadata["ref"] == "#/request_payload/tools"
    assert catalog_metadata["count"] == len(request_tools)
    assert catalog_metadata["response_reference_count"] == 2

    response_capture = capture["response_payload"]
    captured_snapshot = next(
        event
        for event in response_capture["stream_events"]
        if event["type"] == lifecycle_event
    )
    references = [
        response_capture["tools"],
        captured_snapshot["response"]["tools"],
    ]
    assert references[0] == references[1]
    assert references[0]["$ref"] == "#/request_payload/tools"
    variant_ref = references[0]["normalization_ref"]
    assert variant_ref.startswith("#/tool_catalog/variants/")
    variant_id = variant_ref.rsplit("/", 1)[-1]
    variant = catalog_metadata["variants"][variant_id]
    assert variant["base_ref"] == "#/request_payload/tools"
    assert variant["observed_sha256"] == f"sha256:{variant_id}"
    assert variant["observed_count"] == len(provider_tools)
    patch_by_location = {
        (operation["op"], operation["path"]): operation
        for operation in variant["normalization_patch"]
    }
    assert patch_by_location[("add", "/0/strict")]["value"] is True
    assert (
        patch_by_location[("replace", "/0/description")]["value"]
        == "Read a local file."
    )
    assert patch_by_location[("remove", "/1/description")] == {
        "op": "remove",
        "path": "/1/description",
    }
    assert patch_by_location[("add", "/1/output_schema")]["value"] is None

    captured_deltas = [
        event
        for event in response_capture["stream_events"]
        if event["type"] == "response.function_call_arguments.delta"
    ]
    assert captured_deltas == function_deltas


def test_exact_tool_catalog_echo_uses_plain_canonical_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(oai_api_capture, "LOG_DIR", tmp_path)
    request_tools = [
        {
            "type": "function",
            "name": "read_file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        }
    ]
    request_payload = {
        "model": "gpt-5.6-sol",
        "tools": request_tools,
    }
    response_payload = {
        "id": "resp_capture_exact",
        "tools": copy.deepcopy(request_tools),
    }

    capture_path = oai_api_capture.write_capture(
        endpoint="https://api.openai.com/v1/responses",
        request_payload=request_payload,
        response_payload=response_payload,
    )

    assert capture_path is not None
    capture = json.loads(Path(capture_path).read_text(encoding="utf-8"))
    assert _tool_catalog_lists(capture) == [request_tools]
    assert capture["response_payload"]["tools"] == {"$ref": "#/request_payload/tools"}
    assert capture["tool_catalog"]["response_reference_count"] == 1
    assert "variants" not in capture["tool_catalog"]
