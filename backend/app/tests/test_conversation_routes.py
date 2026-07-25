import sys
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from app.main import app
    from app.utils import conversation_store, user_settings

    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path)
    settings_file = tmp_path / "user_settings.json"
    monkeypatch.setattr(user_settings, "USER_SETTINGS_PATH", settings_file)
    return TestClient(app)


def test_nested_conversation_name_roundtrip_and_rename(client):
    nested_name = "projects/alpha"
    encoded = quote(nested_name, safe="")
    payload = {
        "name": "Alpha conversation",
        "messages": [{"role": "user", "content": "hello folders"}],
    }

    save_resp = client.post(f"/conversations/{encoded}", json=payload)
    assert save_resp.status_code == 200

    get_resp = client.get(f"/conversations/{encoded}")
    assert get_resp.status_code == 200
    assert get_resp.json()["messages"][0]["content"] == "hello folders"

    rename_resp = client.post(
        f"/conversations/{encoded}/rename",
        json={"new_name": "projects/archive/alpha"},
    )
    assert rename_resp.status_code == 200

    detailed = client.get("/conversations", params={"detailed": True})
    assert detailed.status_code == 200
    names = {entry["name"] for entry in detailed.json()["conversations"]}
    assert "projects/archive/alpha" in names
    assert "projects/alpha" not in names

    encoded_new = quote("projects/archive/alpha", safe="")
    delete_resp = client.delete(f"/conversations/{encoded_new}")
    assert delete_resp.status_code == 200

    listed = client.get("/conversations")
    assert listed.status_code == 200
    assert "projects/archive/alpha" not in listed.json()["conversations"]


def test_conversations_listing_counts_real_json_not_sidecars(client):
    payload = {
        "name": "Counted",
        "messages": [{"role": "user", "content": "hello"}],
    }
    assert client.post("/conversations/counted", json=payload).status_code == 200

    listed = client.get("/conversations", params={"detailed": True})

    assert listed.status_code == 200
    body = listed.json()
    assert body["count"] == 1
    assert body["counts"]["real_conversation_json"] == 1
    assert body["counts"]["metadata_sidecars_excluded"] is True
    assert [entry["name"] for entry in body["conversations"]] == ["counted"]


def test_conversation_rename_route_updates_privacy_metadata(client):
    nested_name = "projects/privacy-note"
    encoded = quote(nested_name, safe="")
    payload = {
        "name": "Privacy note",
        "messages": [{"role": "user", "content": "stay local"}],
    }

    save_resp = client.post(f"/conversations/{encoded}", json=payload)
    assert save_resp.status_code == 200

    update_resp = client.post(
        f"/conversations/{encoded}/rename",
        json={"new_name": nested_name, "privacy_mode": "secret"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["status"] == "updated"
    assert update_resp.json()["metadata"]["privacy_mode"] == "secret"
    assert update_resp.json()["metadata"]["sensitivity"] == "secret"

    detailed = client.get("/conversations", params={"detailed": True})
    assert detailed.status_code == 200
    conversation = {
        entry["name"]: entry
        for entry in detailed.json()["conversations"]
        if entry.get("name")
    }[nested_name]
    assert conversation["privacy_mode"] == "secret"
    assert conversation["sensitivity"] == "secret"


def test_nested_conversation_name_export_json(client):
    nested_name = "work/planning"
    encoded = quote(nested_name, safe="")
    payload = {
        "name": "Planning",
        "messages": [{"role": "user", "content": "ship it"}],
    }
    assert client.post(f"/conversations/{encoded}", json=payload).status_code == 200

    export_resp = client.get(
        f"/conversations/{encoded}/export",
        params={"format": "json"},
    )
    assert export_resp.status_code == 200
    body = export_resp.json()
    assert isinstance(body.get("messages"), list)
    assert body["messages"][0]["content"] == "ship it"


def test_partial_tail_save_does_not_overwrite_full_conversation(client):
    messages = [
        {"id": f"m-{idx}", "role": "user" if idx % 2 == 0 else "ai", "text": str(idx)}
        for idx in range(8)
    ]
    assert (
        client.post(
            "/conversations/long-window",
            json={"name": "Long window", "messages": messages},
        ).status_code
        == 200
    )

    tail = messages[-3:]
    partial_resp = client.post(
        "/conversations/long-window",
        json={"name": "Long window", "messages": tail},
    )
    assert partial_resp.status_code == 200
    assert partial_resp.json()["status"] == "skipped_partial"

    get_resp = client.get("/conversations/long-window")
    assert get_resp.status_code == 200
    assert get_resp.json()["messages"] == messages


def test_partial_tail_save_merges_new_messages_without_dropping_history(client):
    messages = [
        {"id": f"m-{idx}", "role": "user" if idx % 2 == 0 else "ai", "text": str(idx)}
        for idx in range(8)
    ]
    assert (
        client.post(
            "/conversations/long-window-append",
            json={"name": "Long window append", "messages": messages},
        ).status_code
        == 200
    )

    new_tail = [
        *messages[-4:],
        {"id": "m-8", "role": "user", "text": "new"},
        {"id": "m-9", "role": "ai", "text": "pending"},
    ]
    partial_resp = client.post(
        "/conversations/long-window-append",
        json={"name": "Long window append", "messages": new_tail},
    )
    assert partial_resp.status_code == 200
    assert partial_resp.json()["status"] == "merged_partial"
    assert partial_resp.json()["appended_messages"] == 2

    get_resp = client.get("/conversations/long-window-append")
    assert get_resp.status_code == 200
    assert get_resp.json()["messages"] == [*messages, *new_tail[-2:]]


def test_marked_client_window_without_overlap_is_rejected(client):
    messages = [
        {"id": f"m-{idx}", "role": "user" if idx % 2 == 0 else "ai", "text": str(idx)}
        for idx in range(5)
    ]
    assert (
        client.post(
            "/conversations/long-window-conflict",
            json={"name": "Long window conflict", "messages": messages},
        ).status_code
        == 200
    )

    conflict_resp = client.post(
        "/conversations/long-window-conflict",
        json={
            "name": "Long window conflict",
            "messages": [{"id": "other", "role": "user", "text": "different"}],
            "client_window": {"truncated": True, "total_messages": 5},
        },
    )
    assert conflict_resp.status_code == 409


def test_conversation_compaction_routes_create_copy_and_reject_replace(client):
    messages = [
        {"id": f"m-{idx}", "role": "user" if idx % 2 == 0 else "ai", "text": str(idx)}
        for idx in range(9)
    ]
    assert (
        client.post(
            "/conversations/compact-source",
            json={"name": "Compact source", "messages": messages},
        ).status_code
        == 200
    )

    preview_resp = client.post(
        "/conversations/compact/preview",
        json={
            "conversation_id": "compact-source",
            "keep_last": 3,
            "max_summary_chars": 1000,
            "summary_mode": "deterministic",
        },
    )
    assert preview_resp.status_code == 200
    preview = preview_resp.json()
    assert preview["status"] == "preview"
    assert preview["source_message_count"] == 9
    assert preview["omitted_messages"] == 6
    assert preview["retained_messages"] == 3

    write_resp = client.post(
        "/conversations/compact/write",
        json={
            "conversation_id": "compact-source",
            "keep_last": 3,
            "summary_mode": "deterministic",
            "target_conversation_id": "compact-source-copy",
            "replace": False,
        },
    )
    assert write_resp.status_code == 200
    assert write_resp.json()["target_conversation_id"] == "compact-source-copy"
    copy_resp = client.get("/conversations/compact-source-copy")
    assert copy_resp.status_code == 200
    assert len(copy_resp.json()["messages"]) == 4
    source_after_write = client.get("/conversations/compact-source")
    assert source_after_write.status_code == 200
    source_after_write_messages = source_after_write.json()["messages"]
    assert len(source_after_write_messages) == 10
    assert (
        source_after_write_messages[-1]["metadata"]["context_compaction_marker"][
            "target_conversation_id"
        ]
        == "compact-source-copy"
    )

    replace_resp = client.post(
        "/conversations/compact/write",
        json={
            "conversation_id": "compact-source",
            "keep_last": 3,
            "summary_mode": "deterministic",
            "replace": True,
        },
    )
    assert replace_resp.status_code == 403
    source_resp = client.get("/conversations/compact-source")
    assert source_resp.status_code == 200
    assert len(source_resp.json()["messages"]) == 10


def test_conversation_compaction_llm_route_passes_workflow_options(client, monkeypatch):
    from app import routes

    messages = [
        {"id": f"m-{idx}", "role": "user" if idx % 2 == 0 else "ai", "text": str(idx)}
        for idx in range(6)
    ]
    assert (
        client.post(
            "/conversations/compact-llm-source",
            json={"name": "Compact llm source", "messages": messages},
        ).status_code
        == 200
    )

    captured = {}

    def fake_generate(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return {"text": "LLM compaction summary"}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    preview_resp = client.post(
        "/conversations/compact/preview",
        json={
            "conversation_id": "compact-llm-source",
            "keep_last": 2,
            "summary_mode": "llm",
            "summary_workflow": "decision_focus",
            "summary_format_notes": "Use tight bullets.",
            "summary_model": "summary-model",
        },
    )
    assert preview_resp.status_code == 200
    preview = preview_resp.json()
    assert preview["summary_method"] == "llm"
    assert preview["summary_workflow"] == "decision_focus"
    assert preview["summary_model"] == "summary-model"
    assert preview["summary_format_notes"] == "Use tight bullets."
    assert "Primary focus:" in captured["prompt"]
    assert captured["kwargs"]["model"] == "summary-model"
    assert (
        captured["kwargs"]["context"].metadata["compaction_workflow"]["name"]
        == "decision_focus"
    )


def test_conversation_compaction_plan_route_returns_budget_guidance(client):
    messages = [
        {
            "id": f"m-{idx}",
            "role": "user" if idx % 2 == 0 else "ai",
            "text": f"message {idx} " * 24,
        }
        for idx in range(18)
    ]
    assert (
        client.post(
            "/conversations/compact-plan-source",
            json={"name": "Compact plan source", "messages": messages},
        ).status_code
        == 200
    )

    plan_resp = client.post(
        "/conversations/compact/plan",
        json={
            "conversation_id": "compact-plan-source",
            "context_window_tokens": 10000,
            "summary_mode": "deterministic",
        },
    )
    assert plan_resp.status_code == 200
    plan = plan_resp.json()
    assert plan["context_window_tokens"] == 10000
    assert plan["context_profile"] == "short"
    assert plan["recommended_keep_last"] >= 1
    assert plan["recommended_summary_chars"] >= 500
    assert (
        plan["recommended_preview_payload"]["conversation_id"] == "compact-plan-source"
    )


def test_branch_context_inherits_only_visible_compaction_snapshots(client):
    from app.utils import conversation_store

    messages = [
        {"id": f"m-{idx}", "role": "user" if idx % 2 == 0 else "ai", "text": str(idx)}
        for idx in range(8)
    ]
    assert (
        client.post(
            "/conversations/branch-source",
            json={"name": "Branch source", "messages": messages},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/context/branch-source", json={"system_prompt": "Base"}
        ).status_code
        == 200
    )
    write_resp = client.post(
        "/conversations/compact/write",
        json={
            "conversation_id": "branch-source",
            "keep_last": 3,
            "summary_mode": "deterministic",
            "target_conversation_id": "branch-source-compacted",
            "replace": False,
        },
    )
    assert write_resp.status_code == 200
    source_resp = client.get("/conversations/branch-source")
    source_messages = source_resp.json()["messages"]
    marker_id = source_messages[-1]["id"]

    earlier_branch_resp = client.post(
        "/context/branch-source/branch",
        json={"new_id": "branch-child-earlier", "parent_message_id": "m-7"},
    )
    assert earlier_branch_resp.status_code == 200
    earlier_meta = conversation_store.get_metadata("branch-child-earlier")
    assert earlier_meta["context_snapshots"] == []
    assert earlier_meta["active_context_snapshot_id"] is None

    marker_branch_resp = client.post(
        "/context/branch-source/branch",
        json={"new_id": "branch-child-late", "parent_message_id": marker_id},
    )
    assert marker_branch_resp.status_code == 200
    late_meta = conversation_store.get_metadata("branch-child-late")
    assert len(late_meta["context_snapshots"]) == 1
    assert (
        late_meta["active_context_snapshot_id"]
        == late_meta["context_snapshots"][0]["id"]
    )
    late_snapshot = conversation_store.load_context_snapshot(
        "branch-child-late",
        late_meta["context_snapshots"][0]["id"],
    )
    assert late_snapshot["copied_from"]["conversation_id"] == "branch-source"


def test_suggest_name_route_for_nested_conversation(client):
    nested_name = "projects/title_suggestion"
    encoded = quote(nested_name, safe="")
    payload = {
        "name": "Untitled",
        "messages": [
            {"role": "user", "content": "plan release checklist and QA sequence"},
            {"role": "assistant", "content": "Sure, here is a release plan."},
        ],
    }
    assert client.post(f"/conversations/{encoded}", json=payload).status_code == 200

    suggest_resp = client.get(f"/conversations/{encoded}/suggest-name")
    assert suggest_resp.status_code == 200
    suggested = str(suggest_resp.json().get("suggested_name") or "").strip()
    assert suggested
    assert "/" not in suggested
    assert suggested != "title_suggestion"


def test_reveal_conversation_supports_nested_name(client, monkeypatch):
    from app import routes

    nested_name = "project/reports"
    encoded = quote(nested_name, safe="")
    payload = {"name": "Reports", "messages": [{"role": "user", "content": "q1"}]}
    assert client.post(f"/conversations/{encoded}", json=payload).status_code == 200

    monkeypatch.setattr(routes.subprocess, "Popen", lambda *_args, **_kwargs: None)
    reveal_resp = client.get(f"/conversations/reveal/{encoded}")
    assert reveal_resp.status_code == 200
    reveal_payload = reveal_resp.json()
    assert Path(reveal_payload["path"]).as_posix().endswith("project/reports.json")


def test_import_route_not_shadowed_by_conversation_save(client):
    import_resp = client.post(
        "/conversations/import",
        json={
            "name": "imports/demo",
            "format": "json",
            "messages": [{"role": "user", "content": "from import"}],
        },
    )
    assert import_resp.status_code == 200
    assert import_resp.json()["status"] == "imported"

    encoded = quote("imports/demo", safe="")
    get_resp = client.get(f"/conversations/{encoded}")
    assert get_resp.status_code == 200
    assert get_resp.json()["messages"][0]["content"] == "from import"


def test_markdown_preview_classifies_without_writing(client, monkeypatch):
    from app import routes
    from app.utils import conversation_store

    save_calls = []
    monkeypatch.setattr(
        conversation_store,
        "save_conversation",
        lambda *args, **kwargs: save_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        routes,
        "_get_rag_service",
        lambda: pytest.fail("Markdown preview must not touch knowledge storage"),
    )

    preview_resp = client.post(
        "/conversations/import/preview",
        files={
            "file": (
                "profile.md",
                b"# Profile\n\nKai likes local-first software.",
                "text/markdown",
            )
        },
    )

    assert preview_resp.status_code == 200
    payload = preview_resp.json()
    assert payload["classification"] == "document"
    assert payload["message_count"] == 0
    assert payload["role_counts"] == {}
    assert payload["preview"].startswith("# Profile")
    assert payload["warnings"] == []
    assert payload["suggested_action"] == "document"
    assert payload["allowed_actions"] == ["document"]
    assert payload["detected_files"][0]["classification"] == "document"

    text_preview = client.post(
        "/conversations/import/preview",
        files={"file": ("profile.txt", b"Plain profile notes.", "text/plain")},
    )
    assert text_preview.status_code == 200
    assert text_preview.json()["classification"] == "document"

    ambiguous_preview = client.post(
        "/conversations/import/preview",
        files={
            "file": (
                "mixed.md",
                b"Profile preamble.\n### [user]\nExample\n### [ai]\nReply",
                "text/markdown",
            )
        },
    )
    assert ambiguous_preview.status_code == 200
    assert ambiguous_preview.json()["classification"] == "ambiguous"
    assert ambiguous_preview.json()["message_count"] == 2
    assert save_calls == []


def test_float_text_export_previews_and_reimports_as_conversation(client):
    from app.utils import conversation_io

    source_messages = [
        {
            "role": "user",
            "text": (
                "Example line:\n```text\n[assistant] This is literal content "
                "inside an unclosed fence."
            ),
        },
        {
            "role": "ai",
            "text": "Understood.",
            "thought_trace": [
                {"text": "internal detail", "timestamp": 1.0},
            ],
            "tools": [
                {
                    "name": "lookup",
                    "status": "complete",
                    "args": {"key": "value"},
                    "result": {"ok": True},
                }
            ],
        },
    ]
    exported = conversation_io.export_conversation_text(
        name="project (draft)",
        messages=source_messages,
        metadata={
            "display_name": "Project (draft)",
            "created_at": "2026-07-22T00:00:00Z",
        },
    )

    preview = client.post(
        "/conversations/import/preview",
        files={"file": ("text-round-trip.txt", exported, "text/plain")},
    )

    assert preview.status_code == 200
    assert preview.json()["classification"] == "conversation"
    assert preview.json()["message_count"] == 2

    imported = client.post(
        "/conversations/import",
        files={"file": ("text-round-trip.txt", exported, "text/plain")},
        data={"name": "imports/text-round-trip"},
    )

    assert imported.status_code == 200
    assert imported.json()["message_count"] == 2
    fetched = client.get("/conversations/imports%2Ftext-round-trip")
    assert fetched.status_code == 200
    assert [
        (message["role"], message.get("text")) for message in fetched.json()["messages"]
    ] == [
        ("user", source_messages[0]["text"]),
        ("ai", source_messages[1]["text"]),
    ]


def test_legacy_text_unterminated_metadata_fence_requires_confirmation(client):
    legacy = """Legacy notes (legacy-notes)
created_at: 2026-07-22T00:00:00Z

[ai] Answer before an uncertain fence
```text
thoughts: 4 tokens, 1s, 1 responses: derived or literal
- lookup (complete) args={"query": "value"} result={"ok": true}
"""

    preview = client.post(
        "/conversations/import/preview",
        files={"file": ("legacy-notes.txt", legacy, "text/plain")},
    )

    assert preview.status_code == 200
    payload = preview.json()
    assert payload["classification"] == "ambiguous"
    assert any("exporter-metadata-shaped" in warning for warning in payload["warnings"])

    rejected = client.post(
        "/conversations/import",
        files={"file": ("legacy-notes.txt", legacy, "text/plain")},
        data={"name": "imports/legacy-notes"},
    )

    assert rejected.status_code == 400
    assert "confirm_ambiguous=true" in str(rejected.json().get("detail", ""))
    assert client.get("/conversations/imports%2Flegacy-notes").json()["messages"] == []


def test_markdown_document_import_is_actionable_and_no_write(client, monkeypatch):
    from app.utils import conversation_store

    save_calls = []
    monkeypatch.setattr(
        conversation_store,
        "save_conversation",
        lambda *args, **kwargs: save_calls.append((args, kwargs)),
    )

    import_resp = client.post(
        "/conversations/import",
        files={
            "file": (
                "profile.md",
                b"# Profile\n\nThis belongs in knowledge.",
                "text/markdown",
            )
        },
        data={"name": "imports/profile"},
    )

    assert import_resp.status_code == 400
    assert "knowledge document" in str(import_resp.json().get("detail", "")).lower()
    assert save_calls == []


def test_valid_single_message_float_export_import_remains_compatible(client):
    markdown = """# Conversation Export
- name: one-message

## Messages
### [user]
Only message
"""

    import_resp = client.post(
        "/conversations/import",
        files={"file": ("one.md", markdown, "text/markdown")},
        data={"name": "imports/one-message"},
    )

    assert import_resp.status_code == 200
    assert import_resp.json()["message_count"] == 1
    fetched = client.get("/conversations/imports%2Fone-message")
    assert fetched.status_code == 200
    assert fetched.json()["messages"][0]["text"] == "Only message"


def test_bom_prefixed_markdown_import_keeps_first_message(client):
    markdown = "\ufeff### [user]\nHello\n### [assistant]\nHi"

    import_resp = client.post(
        "/conversations/import",
        files={"file": ("bom.md", markdown.encode("utf-8"), "text/markdown")},
        data={"name": "imports/bom"},
    )

    assert import_resp.status_code == 200
    assert import_resp.json()["message_count"] == 2
    fetched = client.get("/conversations/imports%2Fbom")
    messages = fetched.json()["messages"]
    assert [(message["role"], message["text"]) for message in messages] == [
        ("user", "Hello"),
        ("ai", "Hi"),
    ]


def test_invalid_or_oversized_markdown_rejected_before_write(client, monkeypatch):
    from app import routes
    from app.utils import conversation_store

    save_calls = []
    monkeypatch.setattr(
        conversation_store,
        "save_conversation",
        lambda *args, **kwargs: save_calls.append((args, kwargs)),
    )

    invalid_cases = [
        (b"\xff### [user]\nInvalid UTF-8", "utf-8"),
        (b"### [user]\x00Null byte", "null bytes"),
    ]
    for payload, expected_detail in invalid_cases:
        preview = client.post(
            "/conversations/import/preview",
            files={"file": ("invalid.md", payload, "application/octet-stream")},
        )
        imported = client.post(
            "/conversations/import",
            files={"file": ("invalid.md", payload, "application/octet-stream")},
            data={"name": "imports/invalid"},
        )
        assert preview.status_code == 400
        assert imported.status_code == 400
        assert expected_detail in str(preview.json().get("detail", "")).lower()
        assert expected_detail in str(imported.json().get("detail", "")).lower()

    oversized = b"x" * (routes.MAX_UPLOAD_SIZE + 1)
    oversized_preview = client.post(
        "/conversations/import/preview",
        files={"file": ("large.md", oversized, "application/octet-stream")},
    )
    oversized_import = client.post(
        "/conversations/import",
        files={"file": ("large.md", oversized, "application/octet-stream")},
        data={"name": "imports/large"},
    )
    assert oversized_preview.status_code == 413
    assert oversized_import.status_code == 413
    assert save_calls == []


def test_ambiguous_markdown_requires_confirmation_and_preserves_content(client):
    markdown = """### [user]
Keep these notes.
### [notes]
This body must survive.
### [assistant]
Understood.
"""
    upload = {"file": ("ambiguous.md", markdown, "text/markdown")}

    rejected = client.post(
        "/conversations/import",
        files=upload,
        data={"name": "imports/ambiguous"},
    )

    assert rejected.status_code == 400
    assert "confirm_ambiguous=true" in str(rejected.json().get("detail", ""))
    assert client.get("/conversations/imports%2Fambiguous").json()["messages"] == []

    confirmed = client.post(
        "/conversations/import",
        files=upload,
        data={
            "name": "imports/ambiguous",
            "intent": "conversation",
            "confirm_ambiguous": "true",
        },
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["message_count"] == 2
    fetched = client.get("/conversations/imports%2Fambiguous")
    messages = fetched.json()["messages"]
    assert "### [notes]\nThis body must survive." in messages[0]["text"]


def test_legacy_markdown_unterminated_fence_requires_explicit_confirmation(client):
    legacy = """# Conversation Export
- name: legacy-fence

## Messages
### [user]
Example starts here:
```markdown
### [assistant]
This may be a real second message or a fenced example.
"""

    preview = client.post(
        "/conversations/import/preview",
        files={"file": ("legacy-fence.md", legacy, "text/markdown")},
    )

    assert preview.status_code == 200
    payload = preview.json()
    assert payload["classification"] == "ambiguous"
    assert any(
        "unterminated code fence" in warning.lower() for warning in payload["warnings"]
    )

    rejected = client.post(
        "/conversations/import",
        files={"file": ("legacy-fence.md", legacy, "text/markdown")},
        data={"name": "imports/legacy-fence"},
    )

    assert rejected.status_code == 400
    assert "confirm_ambiguous=true" in str(rejected.json().get("detail", ""))
    assert client.get("/conversations/imports%2Flegacy-fence").json()["messages"] == []


def test_import_route_zip_payload(client, monkeypatch):
    import io
    import json
    import zipfile

    payload = {
        "messages": [
            {"role": "user", "text": "Hello from zip"},
            {"role": "ai", "text": "Hello from model"},
        ]
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("chat-export.json", json.dumps(payload))
    archive.seek(0)

    import_resp = client.post(
        "/conversations/import",
        files={"file": ("openai-export.zip", archive.read(), "application/zip")},
        data={"format": "zip", "name": "imports/zip-test"},
    )
    assert import_resp.status_code == 200
    data = import_resp.json()
    assert data["status"] == "imported"
    assert data["name"] == "imports/zip-test"

    encoded = quote("imports/zip-test", safe="")
    get_resp = client.get(f"/conversations/{encoded}")
    assert get_resp.status_code == 200
    messages = get_resp.json()["messages"]
    assert messages[0]["content"] == "Hello from zip"
    assert messages[1]["content"] == "Hello from model"


def test_import_route_zip_preview(client):
    import io
    import json
    import zipfile

    first = {
        "messages": [
            {"role": "user", "text": "first message"},
            {"role": "assistant", "text": "second"},
        ]
    }
    second = {
        "messages": [
            {"role": "user", "text": "only message"},
        ]
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("chats/folder/first.json", json.dumps(first))
        zf.writestr("chats/folder/second.json", json.dumps(second))
    archive.seek(0)

    preview_resp = client.post(
        "/conversations/import/preview",
        files={"file": ("openai-export.zip", archive.read(), "application/zip")},
    )
    assert preview_resp.status_code == 200
    detected = preview_resp.json()["detected_files"]
    assert [item["path"] for item in detected] == [
        "chats/folder/first.json",
        "chats/folder/second.json",
    ]
    assert detected[0]["message_count"] == 2
    assert detected[1]["message_count"] == 1


def test_import_route_json_preview(client):
    import json

    payload = {
        "conversations": [
            {
                "id": "chatgpt-session-1",
                "title": "Session 1",
                "messages": [
                    {"role": "user", "text": "first"},
                    {"role": "assistant", "text": "reply"},
                ],
            },
            {
                "id": "chatgpt-session-2",
                "title": "Session 2",
                "messages": [{"role": "user", "text": "only one"}],
            },
            {
                "id": "chatgpt-session-3",
                "title": "Session 3",
                "messages": [
                    {"role": "user", "text": "history"},
                    {"role": "assistant", "text": "restored"},
                    {"role": "user", "text": "done"},
                ],
            },
        ]
    }
    preview_resp = client.post(
        "/conversations/import/preview",
        files={"file": ("openai-export.json", json.dumps(payload), "application/json")},
    )
    assert preview_resp.status_code == 200
    detected = preview_resp.json()["detected_files"]
    assert detected[0]["path"] == "chatgpt-session-3"
    assert detected[0]["message_count"] == 3
    assert detected[1]["path"] == "chatgpt-session-1"
    assert detected[1]["message_count"] == 2
    assert detected[2]["path"] == "chatgpt-session-2"
    assert detected[2]["message_count"] == 1


def test_import_route_json_multi_selection_with_destination_folder(client):
    import json

    payload = {
        "conversations": [
            {
                "id": "chatgpt-session-1",
                "title": "Session 1",
                "messages": [
                    {"role": "user", "text": "first"},
                    {"role": "assistant", "text": "reply"},
                ],
            },
            {
                "id": "chatgpt-session-2",
                "title": "Session 2",
                "messages": [{"role": "user", "text": "only one"}],
            },
        ]
    }
    import_resp = client.post(
        "/conversations/import",
        files={"file": ("openai-export.json", json.dumps(payload), "application/json")},
        data={
            "format": "json",
            "selected_files": json.dumps(["chatgpt-session-1", "chatgpt-session-2"]),
            "destination_folder": "chatgpt",
        },
    )
    assert import_resp.status_code == 200
    payload = import_resp.json()
    assert payload["status"] == "imported"
    assert payload["count"] == 2
    assert {entry["name"] for entry in payload["imports"]} == {
        "chatgpt/chatgpt-session-1",
        "chatgpt/chatgpt-session-2",
    }


def test_import_route_json_multi_requires_preview_when_unselected(client):
    import json

    payload = {
        "conversations": [
            {
                "id": "chatgpt-session-1",
                "messages": [{"role": "user", "text": "first"}],
            },
            {
                "id": "chatgpt-session-2",
                "messages": [{"role": "user", "text": "second"}],
            },
        ]
    }
    import_resp = client.post(
        "/conversations/import",
        files={"file": ("openai-export.json", json.dumps(payload), "application/json")},
        data={"format": "json"},
    )
    assert import_resp.status_code == 400
    assert "multiple conversations" in str(import_resp.json().get("detail", "")).lower()


def test_import_route_zip_multi_selection_with_destination_folder(client):
    import io
    import json
    import zipfile

    first = {
        "messages": [
            {"role": "user", "text": "first message"},
            {"role": "assistant", "text": "reply"},
        ]
    }
    second = {"messages": [{"role": "user", "text": "single message"}]}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("chats/first.json", json.dumps(first))
        zf.writestr("chats/second.json", json.dumps(second))
    archive.seek(0)

    import_resp = client.post(
        "/conversations/import",
        files={"file": ("openai-export.zip", archive.read(), "application/zip")},
        data={
            "format": "zip",
            "selected_files": json.dumps(["chats/first.json", "chats/second.json"]),
            "destination_folder": "chatgpt",
        },
    )
    assert import_resp.status_code == 200
    payload = import_resp.json()
    assert payload["status"] == "imported"
    assert payload["count"] == 2
    assert {entry["name"] for entry in payload["imports"]} == {
        "chatgpt/first",
        "chatgpt/second",
    }


def test_import_route_zip_destination_sanitizes_unsafe_segments(client):
    import io
    import json
    import zipfile

    payload = {
        "messages": [
            {"role": "user", "text": "safety test"},
        ]
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("first.json", json.dumps(payload))
    archive.seek(0)

    import_resp = client.post(
        "/conversations/import",
        files={"file": ("openai-export.zip", archive.read(), "application/zip")},
        data={
            "format": "zip",
            "selected_files": json.dumps(["first.json"]),
            "destination_folder": "../chatgpt/..//",
        },
    )
    assert import_resp.status_code == 200
    payload = import_resp.json()
    assert payload["status"] == "imported"
    assert payload["name"] == "chatgpt/first"
