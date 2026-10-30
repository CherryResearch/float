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
    monkeypatch.setenv(oai_api_capture.CAPTURE_OPT_IN, "true")
    monkeypatch.setattr(oai_api_capture, "_capture_directory", lambda: tmp_path)
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
    monkeypatch.setenv(oai_api_capture.CAPTURE_OPT_IN, "true")
    monkeypatch.setattr(oai_api_capture, "_capture_directory", lambda: tmp_path)
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


def _write_synthetic(**kwargs):
    return oai_api_capture.write_capture(
        endpoint="synthetic:test",
        request_payload=kwargs.get("request", {"input": "synthetic private content"}),
        response_payload=kwargs.get("response", {"id": "../../outside"}),
    )


@pytest.mark.parametrize("value", [None, "", "false", "1", "yes", "invalid"])
def test_content_capture_requires_explicit_opt_in(value, tmp_path, monkeypatch):
    if value is None:
        monkeypatch.delenv(oai_api_capture.CAPTURE_OPT_IN, raising=False)
    else:
        monkeypatch.setenv(oai_api_capture.CAPTURE_OPT_IN, value)

    def unexpected(*args, **kwargs):
        pytest.fail("Disabled capture touched content or storage")

    monkeypatch.setattr(oai_api_capture, "_capture_directory", unexpected)
    monkeypatch.setattr(
        oai_api_capture, "_deduplicate_response_tool_catalogs", unexpected
    )
    assert _write_synthetic() is None
    assert list(tmp_path.iterdir()) == []


def test_capture_uses_configured_data_root_and_safe_unique_names(tmp_path, monkeypatch):
    monkeypatch.setenv(oai_api_capture.CAPTURE_OPT_IN, "true")
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path / "isolated-data"))
    first, second = _write_synthetic(), _write_synthetic()
    assert first != second
    assert Path(first).parent == tmp_path / "isolated-data/diagnostics/oai_api"
    assert oai_api_capture._CAPTURE_NAME.fullmatch(Path(first).name)
    assert (
        json.loads(Path(first).read_text())["response_payload"]["id"] == "../../outside"
    )
    assert not (tmp_path / "outside.json").exists()
    assert not (Path(first).parent / ".capture.lock").exists()


@pytest.fixture
def capture_directory(tmp_path, monkeypatch):
    monkeypatch.setenv(oai_api_capture.CAPTURE_OPT_IN, "true")
    monkeypatch.setattr(oai_api_capture, "_capture_directory", lambda: tmp_path)
    return tmp_path


def test_capture_count_retention_preserves_unrelated_files(
    capture_directory, monkeypatch
):
    import os

    monkeypatch.setattr(oai_api_capture, "MAX_CAPTURE_FILES", 2)
    unrelated = capture_directory / "user-note.json"
    unrelated.write_text("keep")
    first = _write_synthetic()
    second = _write_synthetic()
    # Establish the oldest capture even when rapid writes share a timestamp.
    older = Path(second).stat().st_mtime - 1
    os.utime(first, (older, older))
    assert Path(first).stat().st_mtime < Path(second).stat().st_mtime
    third = _write_synthetic()
    assert not Path(first).exists()
    assert Path(second).exists() and Path(third).exists()
    assert unrelated.read_text() == "keep"


def test_capture_byte_retention(capture_directory, monkeypatch):
    first = _write_synthetic()
    size = Path(first).stat().st_size
    monkeypatch.setattr(oai_api_capture, "MAX_TOTAL_BYTES", size + 20)
    second = _write_synthetic()
    assert second and not Path(first).exists()
    assert (
        sum(p.stat().st_size for p in capture_directory.glob("capture-*.json"))
        <= size + 20
    )


def test_capture_age_retention(capture_directory):
    import os
    import time

    first = _write_synthetic()
    past = time.time() - oai_api_capture.MAX_AGE_SECONDS - 10
    os.utime(first, (past, past))
    assert _write_synthetic()
    assert not Path(first).exists()


def test_oversized_capture_is_dropped_not_truncated(capture_directory, monkeypatch):
    monkeypatch.setattr(oai_api_capture, "MAX_CAPTURE_BYTES", 100)
    assert _write_synthetic(request={"input": "x" * 101}) is None
    assert list(capture_directory.iterdir()) == []


def test_capture_busy_lock_drops_without_removing_other_lock(capture_directory):
    lock = capture_directory / ".capture.lock"
    lock.write_text("another process")
    assert _write_synthetic() is None
    assert lock.read_text() == "another process"


def test_capture_serialization_errors_do_not_break_requests(capture_directory):
    assert _write_synthetic(request={"input": object()}) is None
    assert list(capture_directory.iterdir()) == []


def _create_symlink_or_skip(link, target, *, target_is_directory=False):
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows host does not grant symlink creation privilege")
        raise


def test_capture_symlink_destination_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(oai_api_capture.CAPTURE_OPT_IN, "true")
    root, outside = tmp_path / "data", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    _create_symlink_or_skip(root / "diagnostics", outside, target_is_directory=True)
    monkeypatch.setenv("FLOAT_DATA_DIR", str(root))
    assert _write_synthetic() is None
    assert list(outside.iterdir()) == []


def test_retention_ignores_symlinked_files(capture_directory, tmp_path):
    outside = tmp_path / "unrelated.txt"
    outside.write_text("keep")
    link = capture_directory / ("capture-" + "a" * 32 + ".json")
    _create_symlink_or_skip(link, outside)
    assert _write_synthetic()
    assert outside.read_text() == "keep"
    assert link.is_symlink()


def test_retention_failure_drops_capture_and_releases_lock(
    capture_directory, monkeypatch
):
    def failed(*args):
        raise OSError("synthetic full disk")

    monkeypatch.setattr(oai_api_capture, "_prune_captures", failed)
    assert _write_synthetic() is None
    assert not (capture_directory / ".capture.lock").exists()


def test_filename_collision_does_not_delete_existing_capture(
    capture_directory, monkeypatch
):
    from types import SimpleNamespace

    monkeypatch.setattr(
        oai_api_capture.uuid, "uuid4", lambda: SimpleNamespace(hex="b" * 32)
    )
    first = _write_synthetic()
    original = Path(first).read_bytes()
    assert _write_synthetic() is None
    assert Path(first).read_bytes() == original
