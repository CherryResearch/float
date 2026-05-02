import sys
from pathlib import Path


def _install_backend_path():
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))


def _seed_messages(count=12):
    return [
        {
            "id": f"m-{idx}",
            "role": "user" if idx % 2 == 0 else "ai",
            "text": f"message {idx}",
            "tools": [{"name": "tool_info"}] if idx % 5 == 0 else [],
        }
        for idx in range(count)
    ]


def test_compact_conversation_preview_and_write(tmp_path, monkeypatch):
    _install_backend_path()

    from app.tools.conversations import (
        compact_conversation_plan,
        compact_conversation_preview,
        compact_conversation_write,
    )
    from app.utils import conversation_store, generate_signature

    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path)
    conversation_store.save_conversation("source", _seed_messages())

    preview_args = {
        "conversation_id": "source",
        "keep_last": 3,
        "max_summary_chars": 1000,
        "summary_mode": "deterministic",
    }
    preview = compact_conversation_preview(
        **preview_args,
        user="tester",
        signature=generate_signature(
            "tester",
            "compact_conversation_preview",
            preview_args,
        ),
    )
    assert preview["status"] == "preview"
    assert preview["total_messages"] == 12
    assert preview["source_conversation_name"] == "source"
    assert preview["omitted_messages"] == 9
    assert preview["retained_start_index"] == 9
    assert preview["elapsed_ms"] >= 0
    assert len(preview["messages"]) == 4
    assert preview["proposed_target_conversation_id"].startswith("compacted/source-")
    assert preview["proposed_target_conversation_name"] == "Compacted - source"
    assert (
        preview["messages"][0]["metadata"]["conversation_compaction"][
            "source_conversation_id"
        ]
        == "source"
    )
    assert (
        preview["messages"][0]["metadata"]["conversation_compaction"][
            "source_conversation_name"
        ]
        == "source"
    )
    assert (
        preview["messages"][0]["metadata"]["conversation_compaction"]["preview"] is True
    )

    write_args = {
        **preview_args,
        "target_conversation_id": "source-compacted",
        "replace": False,
    }
    written = compact_conversation_write(
        **write_args,
        user="tester",
        signature=generate_signature(
            "tester",
            "compact_conversation_write",
            write_args,
        ),
    )
    assert written["status"] == "written"
    assert written["target_conversation_id"] == "source-compacted"
    assert written["elapsed_ms"] >= 0
    assert "messages" not in written
    compacted = conversation_store.load_conversation("source-compacted")
    assert len(compacted) == 4
    assert compacted[-1]["id"] == "m-11"
    assert compacted[0]["metadata"]["conversation_compaction"]["preview"] is False
    assert (
        compacted[0]["metadata"]["conversation_compaction"]["target_conversation_id"]
        == "source-compacted"
    )
    assert (
        conversation_store.get_metadata("source-compacted")["display_name"]
        == "Compacted - source"
    )

    plan_args = {
        "conversation_id": "source",
        "context_window_tokens": 12000,
        "summary_mode": "deterministic",
    }
    plan = compact_conversation_plan(
        **plan_args,
        user="tester",
        signature=generate_signature(
            "tester",
            "compact_conversation_plan",
            plan_args,
        ),
    )
    assert plan["context_profile"] == "short"
    assert plan["recommended_preview_payload"]["conversation_id"] == "source"
    assert plan["recommended_write_payload"]["replace"] is False


def test_compaction_llm_mode_uses_provider_summary(tmp_path, monkeypatch):
    _install_backend_path()

    from app.services.conversation_compaction import build_compaction
    from app.utils import conversation_store

    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path)
    conversation_store.save_conversation("source", _seed_messages())
    captured = {}

    def fake_summarizer(request):
        captured.update(request)
        return {"text": "Semantic recap with decisions, tool outcomes, and next steps."}

    result = build_compaction(
        "source",
        keep_last=2,
        max_summary_chars=1000,
        summary_mode="llm",
        summary_workflow="task_state",
        summary_format_notes="Keep it compact and concrete.",
        summary_model="summary-model",
        llm_summarizer=fake_summarizer,
    )

    assert result["summary_mode"] == "llm"
    assert result["summary_method"] == "llm"
    assert result["summary_workflow"] == "task_state"
    assert result["summary_format_notes"] == "Keep it compact and concrete."
    assert result["summary_model"] == "summary-model"
    assert result["messages"][0]["text"].startswith("Semantic recap")
    assert (
        result["messages"][0]["metadata"]["conversation_compaction"]["method"]
        == "llm_summary"
    )
    assert captured["summary_workflow"] == "task_state"
    assert captured["summary_format_notes"] == "Keep it compact and concrete."
    assert captured["model"] == "summary-model"
    assert captured["context"].metadata["compaction_workflow"]["kind"] == (
        "conversation_context_compaction"
    )


def test_compaction_llm_mode_falls_back_without_provider(tmp_path, monkeypatch):
    _install_backend_path()

    from app.services.conversation_compaction import build_compaction
    from app.utils import conversation_store

    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path)
    conversation_store.save_conversation("source", _seed_messages())

    result = build_compaction("source", keep_last=2, summary_mode="llm")

    assert result["summary_mode"] == "llm"
    assert result["summary_method"] == "deterministic"
    assert result["summary_workflow"] == "conversation_handoff"
    assert result["fallback_reason"] == "llm_summarizer_unavailable"
    assert "Compacted earlier conversation context." in result["summary_preview"]


def test_write_compaction_persists_context_snapshot_and_marker(tmp_path, monkeypatch):
    _install_backend_path()

    from app.services.conversation_compaction import (
        build_context_budget_plan,
        write_compaction,
    )
    from app.utils import conversation_store

    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path)
    messages = _seed_messages(14)
    conversation_store.save_conversation("source", messages)
    plan = build_context_budget_plan(
        messages,
        conversation_id="source",
        context_window_tokens=12000,
    )

    written = write_compaction(
        "source",
        keep_last=4,
        summary_mode="deterministic",
        target_conversation_id="source-compacted",
        context_budget_plan=plan,
    )

    assert written["context_snapshot"]["id"].startswith("ccs-")
    refs = conversation_store.list_context_snapshot_refs("source")
    assert len(refs) == 1
    ref = refs[0]
    assert ref["target_conversation_id"] == "source-compacted"
    snapshot = conversation_store.load_context_snapshot("source", ref["id"])
    assert snapshot["budget_plan"]["context_window_tokens"] == 12000
    assert snapshot["budget_status"] in {
        "ok",
        "soft_trigger",
        "hard_trigger",
        "overflow",
    }
    source_messages = conversation_store.load_conversation("source")
    assert len(source_messages) == len(messages) + 1
    marker = source_messages[-1]
    assert marker["metadata"]["context_compaction_marker"]["snapshot_id"] == ref["id"]

    repeated = write_compaction(
        "source",
        keep_last=4,
        summary_mode="deterministic",
        target_conversation_id="source-compacted",
        context_budget_plan=plan,
    )
    assert repeated["context_snapshot"]["id"] == written["context_snapshot"]["id"]
    repeated_source_messages = conversation_store.load_conversation("source")
    marker_count = sum(
        1
        for message in repeated_source_messages
        if isinstance(message, dict)
        and isinstance(message.get("metadata"), dict)
        and isinstance(message["metadata"].get("context_compaction_marker"), dict)
    )
    assert marker_count == 1


def test_repeated_compaction_carries_forward_prior_summary(tmp_path, monkeypatch):
    _install_backend_path()

    from app.services.conversation_compaction import build_compaction, write_compaction
    from app.utils import conversation_store

    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path)
    messages = [
        {
            "id": f"m-{idx}",
            "role": "user" if idx % 2 == 0 else "ai",
            "text": f"message {idx} with detailed state and decisions " * 10,
        }
        for idx in range(18)
    ]
    conversation_store.save_conversation("source", messages)

    write_compaction(
        "source",
        keep_last=4,
        summary_mode="deterministic",
        target_conversation_id="source-compacted-1",
    )

    second = build_compaction(
        "source-compacted-1",
        keep_last=4,
        summary_mode="deterministic",
    )

    summary_message = second["messages"][0]
    summary_meta = summary_message["metadata"]["conversation_compaction"]
    assert summary_meta["prior_compaction_summaries_carried"] == 1
    assert "Prior carried summary:" in summary_message["text"]
    assert "Prior summary 1:" in summary_message["text"]
