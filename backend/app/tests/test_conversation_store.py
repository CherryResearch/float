import importlib
import json
import threading
from pathlib import Path


def setup_store(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    if "app.utils.conversation_store" in importlib.sys.modules:
        module = importlib.reload(
            importlib.import_module("app.utils.conversation_store")
        )
    else:
        module = importlib.import_module("app.utils.conversation_store")
    return module


def test_save_and_load(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    messages = [{"role": "user", "content": "hi"}]
    store.save_conversation("abc", messages)
    assert Path(tmp_path, "abc.json").exists()
    loaded = store.load_conversation("abc")
    assert loaded == messages


def test_save_prunes_runtime_receipts_for_removed_messages(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("scoped", [{"id": "m1", "role": "ai", "text": "x"}])
    store.merge_metadata(
        "scoped",
        {
            store.SERVER_RUNTIME_RECEIPTS_KEY: {
                "messages": {
                    "m1": {
                        store.CONTINUATION_TRUST_KEY: store.CONTINUATION_TRUST_SERVER,
                        "capability_scope": {"version": 1},
                    }
                }
            }
        },
    )

    store.save_conversation("scoped", [])

    assert store.SERVER_RUNTIME_RECEIPTS_KEY not in store.get_metadata("scoped")


def test_atomic_mutation_preserves_distinct_concurrent_appends(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("shared", [])
    start = threading.Barrier(3)
    observed_lengths = []
    errors = []

    def append_message(message_id):
        try:
            start.wait()

            def append(messages):
                observed_lengths.append(len(messages))
                messages.append({"id": message_id, "role": "user", "text": message_id})

            store.mutate_conversation("shared", append)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=append_message, args=("m1",)),
        threading.Thread(target=append_message, args=("m2",)),
    ]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(observed_lengths) == [0, 1]
    assert {message["id"] for message in store.load_conversation("shared")} == {
        "m1",
        "m2",
    }


def test_atomic_mutation_supports_nested_metadata_write_and_prunes_receipts(
    monkeypatch, tmp_path
):
    store = setup_store(tmp_path, monkeypatch)
    messages = [
        {"id": "m1", "role": "ai", "text": "old"},
        {"id": "m2", "role": "ai", "text": "keep"},
    ]
    store.save_conversation("scoped", messages)
    store.merge_metadata(
        "scoped",
        {
            store.SERVER_RUNTIME_RECEIPTS_KEY: {
                "messages": {
                    message["id"]: {
                        store.CONTINUATION_TRUST_KEY: store.CONTINUATION_TRUST_SERVER,
                        "capability_scope": {"version": 1},
                    }
                    for message in messages
                }
            }
        },
    )

    def keep_latest(current):
        store.merge_metadata("scoped", {"nested_write": {"completed": True}})
        return [message for message in current if message["id"] == "m2"]

    saved = store.mutate_conversation("scoped", keep_latest)

    assert [message["id"] for message in saved] == ["m2"]
    metadata = store.get_metadata("scoped")
    assert metadata["nested_write"] == {"completed": True}
    assert set(metadata[store.SERVER_RUNTIME_RECEIPTS_KEY]["messages"]) == {"m2"}
    assert metadata["message_count"] == 1


def test_nested_atomic_mutation_shares_outer_working_copy(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("nested", [])

    def append_outer(messages):
        store.mutate_conversation(
            "nested",
            lambda current: current.append(
                {"id": "inner", "role": "user", "text": "inner"}
            ),
        )
        messages.append({"id": "outer", "role": "user", "text": "outer"})

    store.mutate_conversation("nested", append_outer)

    assert [message["id"] for message in store.load_conversation("nested")] == [
        "inner",
        "outer",
    ]


def test_replacement_content_invalidates_embedded_runtime_authority(
    monkeypatch, tmp_path
):
    store = setup_store(tmp_path, monkeypatch)
    store.replace_conversation_content(
        "imported",
        [
            {
                "id": "m1",
                "role": "ai",
                "metadata": {"capability_scope": {"version": 1}},
                "tools": [
                    {
                        "id": "t1",
                        "name": "recall",
                        "status": "invoked",
                        "server_recorded": True,
                    }
                ],
            }
        ],
    )

    saved = store.load_conversation("imported")[0]
    assert "capability_scope" not in (saved.get("metadata") or {})
    assert saved["tools"][0][store.CLIENT_SAVED_TOOL_MARKER] is True
    assert "server_recorded" not in saved["tools"][0]
    receipt = store.get_metadata("imported")[store.SERVER_RUNTIME_RECEIPTS_KEY][
        "messages"
    ]["m1"]
    assert receipt[store.CONTINUATION_TRUST_KEY] == (
        store.CONTINUATION_TRUST_INVALIDATED
    )


def test_legacy_migration_replaces_runtime_receipts_with_invalidations(
    monkeypatch, tmp_path
):
    store = setup_store(tmp_path / "active", monkeypatch)
    legacy = tmp_path / "legacy"
    target = tmp_path / "migrated"
    legacy.mkdir()
    (legacy / "session.json").write_text(
        json.dumps([{"id": "m1", "role": "ai", "text": "old"}]),
        encoding="utf-8",
    )
    (legacy / "session.meta.json").write_text(
        json.dumps(
            {
                "display_name": "Old session",
                store.SERVER_RUNTIME_RECEIPTS_KEY: {
                    "messages": {
                        "m1": {
                            store.CONTINUATION_TRUST_KEY: (
                                store.CONTINUATION_TRUST_SERVER
                            ),
                            "capability_scope": {"version": 1},
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    store._migrate_legacy_conversations(legacy_dir=legacy, target_dir=target)

    migrated_meta = json.loads(
        (target / "session.meta.json").read_text(encoding="utf-8")
    )
    migrated_receipt = migrated_meta[store.SERVER_RUNTIME_RECEIPTS_KEY]["messages"][
        "m1"
    ]
    assert migrated_receipt == {
        store.CONTINUATION_TRUST_KEY: store.CONTINUATION_TRUST_INVALIDATED,
        "reason": "legacy_migration",
    }
    assert (
        json.loads((target / "session.json").read_text(encoding="utf-8"))[0]["id"]
        == "m1"
    )


def test_legacy_migration_invalidates_unscoped_assistant_message(monkeypatch, tmp_path):
    store = setup_store(tmp_path / "active", monkeypatch)
    legacy = tmp_path / "legacy"
    target = tmp_path / "migrated"
    legacy.mkdir()
    (legacy / "plain.json").write_text(
        json.dumps([{"id": "assistant-1", "role": "ai", "text": "legacy"}]),
        encoding="utf-8",
    )

    store._migrate_legacy_conversations(legacy_dir=legacy, target_dir=target)

    migrated_meta = json.loads((target / "plain.meta.json").read_text(encoding="utf-8"))
    receipt = migrated_meta[store.SERVER_RUNTIME_RECEIPTS_KEY]["messages"][
        "assistant-1"
    ]
    assert receipt[store.CONTINUATION_TRUST_KEY] == (
        store.CONTINUATION_TRUST_INVALIDATED
    )
    assert receipt["reason"] == "legacy_migration"


def test_list_and_delete(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("one", [{"role": "user", "content": "1"}])
    store.save_conversation("empty", [])
    names = store.list_conversations()
    assert "one" in names and "empty" not in names
    store.delete_conversation("one")
    assert Path(tmp_path, "one.json").exists() is False


def test_list_conversations_sorted(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    # Save conversations out of alphabetical order
    store.save_conversation("sess-2", [{"role": "user", "content": "2"}])
    store.save_conversation("sess-1", [{"role": "user", "content": "1"}])
    names = store.list_conversations()
    assert names == ["sess-1", "sess-2"]


def test_list_conversations_with_metadata(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("sess-1", [{"role": "user", "content": "1"}])
    detailed = store.list_conversations(include_metadata=True)
    assert isinstance(detailed, list)
    assert isinstance(detailed[0], dict)
    assert detailed[0]["name"] == "sess-1"
    assert detailed[0]["updated_at"]
    assert detailed[0]["created_at"]
    assert detailed[0]["display_name"]


def test_display_name_flags(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("sess-1", [{"role": "user", "content": "1"}])
    store.set_display_name("sess-1", "Project kickoff", auto_generated=True)
    meta = store.get_metadata("sess-1")
    assert meta["display_name"] == "Project kickoff"
    assert meta["auto_title_applied"] is True
    store.set_display_name("sess-1", "Manual Title", manual=True)
    meta = store.get_metadata("sess-1")
    assert meta["manual_title"] is True


def test_rename(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("src", [{"role": "user", "content": "x"}])
    store.rename_conversation("src", "dest")
    assert Path(tmp_path, "dest.json").exists()
    assert store.load_conversation("dest")[0]["content"] == "x"
    meta = store.get_metadata("dest")
    assert meta["display_name"] == "dest"
    assert meta["manual_title"] is True


def test_rename_preserves_metadata(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("src", [{"role": "user", "content": "x"}])
    before = store.list_conversations(include_metadata=True)[0]["updated_at"]
    store.rename_conversation("src", "dest")
    detailed = store.list_conversations(include_metadata=True)
    assert detailed[0]["name"] == "dest"
    assert detailed[0]["updated_at"] == before


def test_nested_conversation_paths(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("projects/alpha", [{"role": "user", "content": "x"}])
    assert (tmp_path / "projects" / "alpha.json").exists()
    names = store.list_conversations()
    assert names == ["projects/alpha"]
    detailed = store.list_conversations(include_metadata=True)
    assert detailed[0]["name"] == "projects/alpha"
    assert detailed[0]["display_name"] == "alpha"


def test_list_conversations_skips_object_artifacts(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("demo/sess-1", [{"role": "user", "content": "x"}])
    (tmp_path / "demo").mkdir(exist_ok=True)
    (tmp_path / "demo" / "demo_manifest.json").write_text(
        '{"created_at_utc":"2026-04-10T00:00:00Z"}',
        encoding="utf-8",
    )
    names = store.list_conversations()
    assert names == ["demo/sess-1"]


def test_list_conversations_skips_context_snapshot_tree(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("demo/sess-1", [{"role": "user", "content": "x"}])
    snapshot_dir = tmp_path / ".context_snapshots" / "demo" / "sess-1"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "snap-1.json").write_text(
        '[{"role":"user","content":"snapshot copy"}]',
        encoding="utf-8",
    )
    names = store.list_conversations()
    assert names == ["demo/sess-1"]


def test_load_conversation_returns_empty_for_object_payload(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    (tmp_path / "demo_manifest.json").write_text(
        '{"created_at_utc":"2026-04-10T00:00:00Z"}',
        encoding="utf-8",
    )
    assert store.load_conversation("demo_manifest") == []


def test_move_preserves_display_name(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("sess-1", [{"role": "user", "content": "1"}])
    store.set_display_name("sess-1", "Project kickoff", auto_generated=True)
    store.rename_conversation("sess-1", "work/sess-1")
    meta = store.get_metadata("work/sess-1")
    assert meta["display_name"] == "Project kickoff"
    assert meta["auto_title_applied"] is True


def test_dev_mode_uses_test_folder(monkeypatch):
    monkeypatch.delenv("FLOAT_CONV_DIR", raising=False)
    monkeypatch.setenv("FLOAT_DEV_MODE", "true")
    module = importlib.import_module("app.utils.conversation_store")
    module = importlib.reload(module)
    assert module.CONV_DIR.name == "test_conversations"


def test_conversation_id_sidecar(monkeypatch, tmp_path):
    store = setup_store(tmp_path, monkeypatch)
    store.save_conversation("abc", [{"role": "user", "content": "hi"}])
    cid1 = store.get_or_create_conversation_id("abc")
    cid2 = store.get_or_create_conversation_id("abc")
    assert cid1 == cid2
    meta = tmp_path / "abc.meta.json"
    assert meta.exists()
    # Rename should move meta
    store.rename_conversation("abc", "renamed")
    cid3 = store.get_or_create_conversation_id("renamed")
    assert cid3 == cid1
    assert not meta.exists()
    assert (tmp_path / "renamed.meta.json").exists()
