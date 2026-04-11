import importlib
import json


def setup_store(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    if "app.utils.conversation_store" in importlib.sys.modules:
        module = importlib.reload(importlib.import_module("app.utils.conversation_store"))
    else:
        module = importlib.import_module("app.utils.conversation_store")
    return module


def test_auto_title_hook_sets_display_name(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)

    # Ensure the hook is registered.
    auto_title = importlib.import_module("app.hooks_auto_title")
    hooks = importlib.import_module("app.hooks")

    session_name = "sess-123"
    store.save_conversation(
        session_name,
        [
            {"role": "user", "content": "hello from float"},
            {"role": "ai", "content": "hi"},
        ],
    )

    hooks.emit(
        hooks.AFTER_LLM_RESPONSE_EVENT,
        hooks.LLMResponseEvent(
            session_id=store.get_or_create_conversation_id(session_name),
            response_text="hi",
            metadata={"session_name": session_name},
            raw_response={"text": "hi"},
        ),
    )

    meta = store.get_metadata(session_name)
    assert meta["auto_title_applied"] is True
    assert meta["display_name"] == "Hello From Float"

    pending = auto_title.consume_pending_title(session_name)
    assert pending and pending.get("display_name") == "Hello From Float"


def test_auto_title_hook_handles_missing_message_count(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)

    auto_title = importlib.import_module("app.hooks_auto_title")
    hooks = importlib.import_module("app.hooks")

    session_name = "sess-456"
    messages = [
        {"role": "user", "content": "float roadmap summary"},
        {"role": "ai", "content": "ok"},
    ]

    conversation_path = store.CONV_DIR / f"{session_name}.json"
    conversation_path.write_text(json.dumps(messages), encoding="utf-8")

    meta = store.get_metadata(session_name)
    meta.pop("message_count", None)
    meta_path = store.CONV_DIR / f"{session_name}.meta.json"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    hooks.emit(
        hooks.AFTER_LLM_RESPONSE_EVENT,
        hooks.LLMResponseEvent(
            session_id=store.get_or_create_conversation_id(session_name),
            response_text="ok",
            metadata={"session_name": session_name},
            raw_response={"text": "ok"},
        ),
    )

    meta_after = store.get_metadata(session_name)
    assert meta_after["auto_title_applied"] is True
    assert meta_after["display_name"] == "Float Roadmap Summary"

    pending = auto_title.consume_pending_title(session_name)
    assert pending and pending.get("display_name") == "Float Roadmap Summary"


def test_auto_title_hook_strips_request_filler_and_keeps_technical_casing(
    monkeypatch, tmp_path
):
    store = setup_store(tmp_path, monkeypatch)

    auto_title = importlib.import_module("app.hooks_auto_title")
    hooks = importlib.import_module("app.hooks")

    session_name = "sess-789"
    store.save_conversation(
        session_name,
        [
            {
                "role": "user",
                "content": "Please show me the API provider polling status in LM Studio",
            },
            {"role": "ai", "content": "Checking the provider state now."},
        ],
    )

    hooks.emit(
        hooks.AFTER_LLM_RESPONSE_EVENT,
        hooks.LLMResponseEvent(
            session_id=store.get_or_create_conversation_id(session_name),
            response_text="Checking the provider state now.",
            metadata={"session_name": session_name},
            raw_response={"text": "Checking the provider state now."},
        ),
    )

    meta = store.get_metadata(session_name)
    assert meta["display_name"] == "API Provider Polling Status LM Studio"

    pending = auto_title.consume_pending_title(session_name)
    assert pending and pending.get("display_name") == meta["display_name"]


def test_auto_title_hook_removes_leading_help_phrasing(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)

    auto_title = importlib.import_module("app.hooks_auto_title")
    hooks = importlib.import_module("app.hooks")

    session_name = "sess-790"
    store.save_conversation(
        session_name,
        [
            {
                "role": "user",
                "content": "Can you help me debug /chat/continue persistence",
            },
            {"role": "ai", "content": "I'll inspect the continuation flow."},
        ],
    )

    hooks.emit(
        hooks.AFTER_LLM_RESPONSE_EVENT,
        hooks.LLMResponseEvent(
            session_id=store.get_or_create_conversation_id(session_name),
            response_text="I'll inspect the continuation flow.",
            metadata={"session_name": session_name},
            raw_response={"text": "I'll inspect the continuation flow."},
        ),
    )

    meta = store.get_metadata(session_name)
    assert meta["display_name"] == "Debug Chat/Continue Persistence"

    pending = auto_title.consume_pending_title(session_name)
    assert pending and pending.get("display_name") == meta["display_name"]
