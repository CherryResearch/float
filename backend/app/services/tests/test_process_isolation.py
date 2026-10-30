import asyncio
import json
import multiprocessing
import time

import pytest
from app.services.process_isolation import (
    IsolatedProcessError,
    IsolatedProcessTerminated,
    generate_with_fresh_service,
    run_isolated_process,
)


def _graph_transport_probe(request):
    """Importable synthetic child: the real service ends at a fake HTTP boundary."""
    from app import base_services

    calls = []

    class Response:
        status_code = 200
        headers = {}
        text = ""

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [{"message": {"content": "Synthetic child answer"}}],
                "model": "synthetic-model",
            }

    def post(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return Response()

    original = base_services.http_session.post
    base_services.http_session.post = post
    try:
        try:
            result = generate_with_fresh_service(request)
            return {"result": result, "calls": calls}
        except (PermissionError, ValueError) as exc:
            return {"error": type(exc).__name__, "calls": calls}
    finally:
        base_services.http_session.post = original


@pytest.mark.asyncio
@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("marker", ["policy", "context"])
async def test_spawned_provider_enforces_serialized_graph_custody(local, marker):
    request = {
        "mode": "server" if local else "api",
        "model": "synthetic-model",
        "config": {
            "server_url": "http://127.0.0.1:9876/v1/chat/completions",
            "api_url": "https://external.invalid/v1/chat/completions",
            "api_model": "synthetic-model",
            "api_key": "synthetic-key",
        },
        "context": {
            "messages": [
                {"role": "assistant", "content": "SYNTHETIC_GRAPH_LOCAL_ONLY"}
            ],
            "metadata": {},
        },
    }
    if marker == "policy":
        request["data_policy"] = {
            "external": False,
            "allow_remote_query": False,
            "local_only": True,
        }
    else:
        request["context"]["metadata"]["graph_local_only"] = True
    result = await asyncio.wait_for(
        run_isolated_process(
            "app.services.tests.test_process_isolation",
            "_graph_transport_probe",
            request,
        ),
        timeout=120,
    )
    if local:
        assert result["result"]["text"] == "Synthetic child answer"
        assert len(result["calls"]) == 1
        call = result["calls"][0]
        assert call["url"].startswith("http://127.0.0.1:")
        assert call["kwargs"]["allow_redirects"] is False
        assert "SYNTHETIC_GRAPH_LOCAL_ONLY" in json.dumps(call["kwargs"]["json"])
    else:
        assert result == {"error": "PermissionError", "calls": []}


@pytest.mark.parametrize(
    "policy",
    [
        True,
        {},
        {"external": False},
        {"external": "false", "allow_remote_query": False},
        {"external": False, "allow_remote_query": False, "local_only": "true"},
    ],
)
def test_fresh_service_rejects_invalid_policy_before_transport(policy):
    result = _graph_transport_probe(
        {
            "mode": "api",
            "config": {"api_key": "synthetic-key", "api_model": "synthetic-model"},
            "data_policy": policy,
        }
    )
    assert result == {"error": "ValueError", "calls": []}


def test_fresh_service_rechecks_endpoint_after_local_preflight(monkeypatch):
    from app import services
    from app.base_services import LLMService
    from app.utils.privacy_policy import DataPolicy, data_policy_scope

    class DriftingService(LLMService):
        def generate(self, *args, **kwargs):
            self.config["server_url"] = "https://external.invalid/v1/chat/completions"
            return super().generate(*args, **kwargs)

    monkeypatch.setattr(services, "LLMService", DriftingService)
    request = {
        "mode": "server",
        "model": "synthetic-model",
        "config": {"server_url": "http://127.0.0.1:9876/v1/chat/completions"},
        "context": {
            "messages": [
                {"role": "assistant", "content": "SYNTHETIC_GRAPH_LOCAL_ONLY"}
            ],
            "metadata": {"graph_local_only": True},
        },
    }
    with data_policy_scope(DataPolicy(True, True)):
        result = _graph_transport_probe(request)
    assert result == {"error": "PermissionError", "calls": []}


@pytest.mark.parametrize("model", ["  scheduled-model  ", "", None])
def test_fresh_service_preserves_execution_model_override(monkeypatch, model):
    from app import services

    captured = {}

    class RecordingService:
        def __init__(self, *, mode, config):
            assert mode == "server"
            assert config == {"api_model": "default-model"}

        def generate(self, messages, **kwargs):
            assert messages == []
            captured.update(kwargs)
            return {"text": "provider response"}

    monkeypatch.setattr(services, "LLMService", RecordingService)
    result = generate_with_fresh_service(
        {
            "mode": "server",
            "config": {"api_model": "default-model"},
            "model": model,
            "session_id": "scheduled-session",
            "context": {"messages": [{"role": "user", "content": "Hello"}]},
        }
    )

    assert result == {"text": "provider response"}
    assert captured["session_id"] == "scheduled-session"
    assert captured["context"].messages == [{"role": "user", "content": "Hello"}]
    if model:
        assert captured["model"] == "scheduled-model"
    else:
        assert "model" not in captured


@pytest.mark.asyncio
async def test_isolated_process_returns_pickled_result():
    result = await run_isolated_process("builtins", "pow", 7, 3)

    assert result == 343


@pytest.mark.asyncio
async def test_isolated_process_preserves_safe_exception_identity():
    with pytest.raises(IsolatedProcessError) as exc_info:
        await run_isolated_process("builtins", "int", "not-a-number")

    assert exc_info.value.error_type == "ValueError"
    assert exc_info.value.error_module == "builtins"


@pytest.mark.asyncio
async def test_non_cooperative_child_is_terminated_without_orphaning():
    started_at = time.monotonic()

    def termination_check():
        if time.monotonic() - started_at >= 0.15:
            return "control_changed"
        return None

    with pytest.raises(IsolatedProcessTerminated, match="control_changed"):
        await run_isolated_process(
            "time",
            "sleep",
            30,
            termination_check=termination_check,
            poll_interval=0.02,
        )

    assert time.monotonic() - started_at < 5
    await asyncio.sleep(0)
    assert not any(
        child.name == "float-isolated-sleep"
        for child in multiprocessing.active_children()
    )


@pytest.mark.asyncio
async def test_parent_cancellation_terminates_child_before_propagating():
    task = asyncio.create_task(
        run_isolated_process("time", "sleep", 30, poll_interval=0.02)
    )
    deadline = time.monotonic() + 5
    while not any(
        child.name == "float-isolated-sleep"
        for child in multiprocessing.active_children()
    ):
        assert time.monotonic() < deadline
        await asyncio.sleep(0.02)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not any(
        child.name == "float-isolated-sleep"
        for child in multiprocessing.active_children()
    )
