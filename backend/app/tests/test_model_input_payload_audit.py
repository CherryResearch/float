from __future__ import annotations

import importlib
from typing import Any, Dict

import pytest
from app.tool_names import MODEL_HIDDEN_COMPATIBILITY_NAMES, TOOL_NAME_ALIASES
from fastapi.testclient import TestClient

from scripts.model_input_payload_audit import (
    AuditThresholds,
    PayloadSnapshot,
    _system_message_bodies,
    audit_payload_snapshot,
    build_local_payload_snapshot,
    capture_outbound_payload,
)


def _tool(
    name: str,
    description: str,
    *,
    canonical_note: str = "",
    parameters: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    note = f" Canonical Float tool: {canonical_note}." if canonical_note else ""
    return {
        "type": "function",
        "name": name,
        "description": f"{description}{note}",
        "parameters": parameters or {"type": "object", "properties": {}},
    }


def _catalog(*lines: str) -> str:
    from app import routes

    return "\n".join(
        [
            routes._AVAILABLE_TOOLS_PROMPT_HEADER,
            *lines,
            routes._AVAILABLE_TOOLS_PROMPT_FOOTER,
        ]
    )


def _failed_codes(snapshot: PayloadSnapshot, **kwargs: Any) -> set[str]:
    report = audit_payload_snapshot(snapshot, **kwargs)
    return set(report.failed_codes)


def _capture_route_generate_call(
    captures: list[PayloadSnapshot],
    *,
    prompt: Any,
    context: Any,
    model: str | None,
    native_tool_definitions: Any,
) -> None:
    captures.append(
        capture_outbound_payload(
            system_prompt=context.system_prompt,
            context_messages=context.messages,
            prompt=prompt,
            tool_definitions=list(native_tool_definitions or []),
            model=model or "gpt-5.6-sol",
            label=f"route generation {len(captures) + 1}",
        )
    )


def test_capture_uses_real_responses_payload_builder_without_network() -> None:
    snapshot = capture_outbound_payload(
        system_prompt="Keep answers concise.",
        tool_definitions=[
            _tool("read_file", "Read one local file."),
            _tool(
                "computer.observe",
                "Inspect the visible computer state.",
                parameters={
                    "title": "ObserveArguments",
                    "type": "object",
                    "properties": {
                        "title": {
                            "title": "Window title",
                            "type": "string",
                        }
                    },
                },
            ),
        ],
    )

    payload = snapshot.payload
    assert payload["model"] == "gpt-5.6-sol"
    assert "Keep answers concise." in str(payload["input"])
    assert len(payload["tools"]) == 2
    provider_names = {tool["name"] for tool in payload["tools"]}
    assert "read_file" in provider_names
    assert "computer_observe" in provider_names
    assert snapshot.canonical_by_provider["computer_observe"] == "computer.observe"
    observe_tool = next(
        tool for tool in payload["tools"] if tool["name"] == "computer_observe"
    )
    parameters = observe_tool["parameters"]
    assert "title" not in parameters
    assert "title" in parameters["properties"]
    assert "title" not in parameters["properties"]["title"]
    assert audit_payload_snapshot(snapshot).metrics["schema annotation titles"] == 0


def test_representative_native_payload_passes_contract() -> None:
    snapshot = build_local_payload_snapshot(
        surface="native",
        tool_names=["help", "read_file"],
    )

    report = audit_payload_snapshot(snapshot)

    assert report.passed
    assert report.metrics["provider tools"] == 2
    system_input = "\n".join(_system_message_bodies(snapshot.payload))
    assert "after a tool result, continue from that result" in system_input
    assert "use the provider's native tool-call interface" in system_input
    assert "use `help` or `tool_info` only when tool choice" in system_input
    assert "available tools this turn" not in system_input
    assert "emit direct JSON" not in system_input
    assert "emit Harmony" not in system_input
    rendered = report.render_markdown()
    assert rendered.startswith("# Model Input Payload Contract Audit")
    assert "| Check | Status | Evidence |" in rendered
    assert '{"' not in rendered


def test_route_built_initial_and_continuation_payloads_pass_audit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Capture both endpoint paths at the provider boundary without network I/O."""

    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path / "conversations"))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext
    from app.main import app
    from app.utils import user_settings

    monkeypatch.setattr(
        user_settings,
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
        raising=False,
    )
    user_settings.save_settings(
        {
            "approval_level": "all",
            "default_workflow": "default",
            "enabled_workflow_modules": [],
        }
    )
    monkeypatch.setattr(
        routes.llm_service,
        "contexts",
        {"default": ModelContext(system_prompt="Base Float instructions.")},
    )
    monkeypatch.setattr(routes.llm_service, "mode", "api")
    app.state.pending_tools = {}
    app.state.agent_console_state = {"agents": {}, "resources": {}}

    native_tool = _tool(
        "read_file",
        "Read one local text file.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
    monkeypatch.setattr(
        routes,
        "_registered_prompt_tool_definitions",
        lambda *args, **kwargs: [native_tool],
    )

    captures: list[PayloadSnapshot] = []

    def fake_generate(
        prompt: Any,
        *,
        context: Any = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        assert context is not None
        _capture_route_generate_call(
            captures,
            prompt=prompt,
            context=context,
            model=model,
            native_tool_definitions=kwargs.get("native_tool_definitions"),
        )
        if len(captures) == 1:
            return {
                "text": "",
                "thought": "",
                "tools_used": [
                    {
                        "id": "route-read-1",
                        "name": "read_file",
                        "args": {"path": "README.md"},
                    }
                ],
                "metadata": {},
            }
        return {
            "text": "The file was read.",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)
    client = TestClient(app)
    initial = client.post(
        "/chat",
        json={
            "session_id": "payload-audit-route",
            "message_id": "route-message-1",
            "message": "Read the project overview.",
            "mode": "api",
            "model": "gpt-5.6-sol",
            "use_rag": False,
            "use_text_rag": False,
            "use_vision_rag": False,
        },
    )

    assert initial.status_code == 200
    proposed = initial.json()["tools_used"][0]
    assert proposed["name"] == "read_file"
    assert proposed["status"] == "proposed"

    decision = client.post(
        "/api/tools/decision",
        json={
            "request_id": proposed["id"],
            "decision": "deny",
            "name": "read_file",
            "args": proposed["args"],
            "session_id": "payload-audit-route",
            "message_id": "route-message-1",
            "chain_id": "route-message-1",
        },
    )
    assert decision.status_code == 200, decision.text
    resolved = decision.json()
    assert resolved["status"] == "denied"

    continued = client.post(
        "/chat/continue",
        json={
            "session_id": "payload-audit-route",
            "message_id": "route-message-1",
            "mode": "api",
            "model": "gpt-5.6-sol",
            "tools": [
                {
                    "id": proposed["id"],
                    "name": "read_file",
                    "args": proposed["args"],
                    "result": resolved["result"],
                    "status": resolved["status"],
                }
            ],
        },
    )

    assert continued.status_code == 200, continued.text
    assert len(captures) == 2
    for snapshot in captures:
        report = audit_payload_snapshot(snapshot)
        assert report.passed, report.render_markdown()
        assert report.metrics["provider tools"] == 1


@pytest.mark.parametrize(
    "inline_syntax",
    [
        "Tool call syntax for this turn: emit direct JSON as "
        '{"tool":"<exact_tool_name>"}.',
        "Tool call syntax for this turn: emit Harmony with "
        "<|channel|>commentary to=read_file <|constrain|>json.",
    ],
)
def test_native_tools_cannot_mix_with_inline_call_syntax(
    inline_syntax: str,
) -> None:
    snapshot = PayloadSnapshot(
        payload={
            "messages": [{"role": "system", "content": inline_syntax}],
            "tools": [_tool("read_file", "Read one local file.")],
        }
    )

    assert "tool-call format isolation" in _failed_codes(snapshot)


def test_direct_json_and_harmony_cannot_both_be_advertised() -> None:
    snapshot = PayloadSnapshot(
        payload={
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Tool call syntax for this turn: emit direct JSON as "
                        '{"tool":"<exact_tool_name>"}. Then emit Harmony with '
                        "<|channel|>commentary to=read_file <|constrain|>json."
                    ),
                }
            ]
        }
    )

    assert "tool-call format isolation" in _failed_codes(snapshot)


def test_duplicate_names_and_descriptions_are_reported() -> None:
    snapshot = PayloadSnapshot(
        payload={
            "tools": [
                _tool("read_file", "Read a file."),
                _tool("read_file", "Read another file."),
                _tool("inspect_file", "Read a file."),
            ]
        }
    )

    failed = _failed_codes(snapshot, legacy_aliases={})
    assert "unique advertised names" in failed
    assert "unique advertised descriptions" in failed


@pytest.mark.parametrize(
    ("alias", "canonical"),
    sorted(
        {
            **TOOL_NAME_ALIASES,
            **MODEL_HIDDEN_COMPATIBILITY_NAMES,
        }.items()
    ),
)
def test_every_compatibility_alias_is_not_a_second_advertised_tool(
    alias: str,
    canonical: str,
) -> None:
    snapshot = PayloadSnapshot(
        payload={
            "tools": [
                _tool(canonical, f"Canonical capability for {canonical}."),
                _tool(alias, f"Compatibility alias for {canonical}."),
            ]
        }
    )

    report = audit_payload_snapshot(snapshot)

    assert "legacy aliases hidden" in report.failed_codes
    legacy_check = next(
        check for check in report.checks if check.code == "legacy aliases hidden"
    )
    assert f"{alias} to {canonical}" in legacy_check.evidence


def test_explicit_catalog_budgets_fail_closed() -> None:
    catalog = _catalog(
        "- `read_file`: Read one local file.",
        "- `write_file`: Write one local file.",
    )
    snapshot = PayloadSnapshot(
        payload={
            "messages": [{"role": "system", "content": catalog}],
            "tools": [
                _tool("read_file", "A long provider-visible description."),
                _tool("write_file", "Another long provider-visible description."),
            ],
        }
    )
    thresholds = AuditThresholds(
        max_provider_tools=1,
        max_prose_tools=1,
        max_prose_catalog_chars=1,
        max_provider_catalog_chars=1,
        max_provider_description_chars=1,
        max_catalog_sections=1,
        max_cross_surface_repeats=10,
    )

    failed = _failed_codes(
        snapshot,
        thresholds=thresholds,
        legacy_aliases={},
    )
    assert {
        "provider tool budget",
        "prose tool budget",
        "provider catalog character budget",
        "prose catalog character budget",
        "provider description budget",
    }.issubset(failed)


def test_provider_alias_must_match_canonical_prose_name() -> None:
    snapshot = PayloadSnapshot(
        payload={
            "messages": [
                {
                    "role": "system",
                    "content": _catalog(
                        "- `computer_observe`: Inspect the visible computer state."
                    ),
                }
            ],
            "tools": [
                _tool(
                    "computer_observe",
                    "Inspect the visible computer state.",
                    canonical_note="computer.wrong",
                )
            ],
        },
        canonical_by_provider={"computer_observe": "computer.observe"},
    )
    thresholds = AuditThresholds(max_cross_surface_repeats=10)

    assert "provider and prose aliases align" in _failed_codes(
        snapshot,
        thresholds=thresholds,
        legacy_aliases={},
    )


def test_catalog_repetition_is_captured_and_reported() -> None:
    repeated_catalogs = "\n".join(
        [
            _catalog("- `read_file`: Read one local file."),
            _catalog("- `write_file`: Write one local file."),
        ]
    )
    snapshot = PayloadSnapshot(
        payload={
            "messages": [{"role": "system", "content": repeated_catalogs}],
            "tools": [_tool("read_file", "Read one local file.")],
        }
    )

    report = audit_payload_snapshot(snapshot, legacy_aliases={})

    assert "catalog repetition" in report.failed_codes
    assert report.metrics["catalog sections"] == 2
    assert report.metrics["native/prose repeats"] == 1


def test_inline_only_syntax_is_allowed_when_surfaces_are_not_mixed() -> None:
    snapshot = PayloadSnapshot(
        payload={
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Tool call syntax for this turn: emit direct JSON as "
                        '{"tool":"<exact_tool_name>"}. '
                        + _catalog("- `read_file`: Read one local file.")
                    ),
                }
            ]
        }
    )

    report = audit_payload_snapshot(snapshot, legacy_aliases={})

    assert report.passed


def test_five_distinct_system_layers_do_not_fail_merely_on_count() -> None:
    snapshot = capture_outbound_payload(
        system_prompt="Base behavior and durable product policy.",
        context_messages=[
            {"role": "system", "content": "Turn scope for this request only."},
            {"role": "system", "content": "Approval boundaries for tool actions."},
            {"role": "developer", "content": "Quality criteria for the response."},
            {"role": "system", "content": "Runtime identity and provider details."},
        ],
        tool_definitions=[],
    )

    report = audit_payload_snapshot(snapshot, legacy_aliases={})

    assert report.passed
    assert report.metrics["system messages"] == 5
    assert report.metrics["system characters"] > 0
    assert report.metrics["duplicate system bodies"] == 0
    assert report.metrics["repeated system blocks"] == 0


def test_duplicate_system_bodies_and_blocks_fail() -> None:
    shared_block = (
        "Before every external action, inspect the relevant state and explain "
        "the concrete approval boundary to the user in plain language."
    )
    duplicate_body = "Keep private workspace material inside the workspace."
    snapshot = PayloadSnapshot(
        payload={
            "messages": [
                {
                    "role": "system",
                    "content": f"{shared_block}\n\nFirst layer only.",
                },
                {
                    "role": "developer",
                    "content": f"{shared_block}\n\nSecond layer only.",
                },
                {"role": "system", "content": duplicate_body},
                {"role": "system", "content": duplicate_body},
            ]
        }
    )

    report = audit_payload_snapshot(snapshot, legacy_aliases={})

    assert "unique system message bodies" in report.failed_codes
    assert "unique substantive system blocks" in report.failed_codes
    assert report.metrics["duplicate system bodies"] == 1
    assert report.metrics["repeated system blocks"] == 1


def test_duplicate_available_tools_sections_fail() -> None:
    catalog = _catalog("- `read_file`: Read one local file.")
    snapshot = PayloadSnapshot(
        payload={"messages": [{"role": "system", "content": f"{catalog}\n\n{catalog}"}]}
    )

    report = audit_payload_snapshot(snapshot, legacy_aliases={})

    assert "unique available-tools sections" in report.failed_codes
    assert report.metrics["duplicate catalog sections"] == 1


def test_schema_title_annotations_are_rejected_but_title_property_is_allowed() -> None:
    snapshot = PayloadSnapshot(
        payload={
            "tools": [
                _tool(
                    "create_document",
                    "Create a document.",
                    parameters={
                        "title": "CreateDocumentArguments",
                        "type": "object",
                        "properties": {
                            "title": {
                                "title": "Document title",
                                "type": "string",
                            }
                        },
                    },
                )
            ]
        }
    )

    report = audit_payload_snapshot(snapshot, legacy_aliases={})

    assert "schema annotation titles removed" in report.failed_codes
    assert report.metrics["schema annotation titles"] == 2
