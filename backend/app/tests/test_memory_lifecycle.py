import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from app.base_services import MemoryManager
from app.utils import generate_signature, time_resolution

_MEMORY_SPEC = importlib.util.spec_from_file_location(
    "memory_tool_module",
    Path(__file__).resolve().parents[1] / "tools" / "memory.py",
)
memory_tools = importlib.util.module_from_spec(_MEMORY_SPEC)
assert _MEMORY_SPEC and _MEMORY_SPEC.loader
_MEMORY_SPEC.loader.exec_module(memory_tools)  # type: ignore[arg-type]


@pytest.fixture
def memory_manager(monkeypatch) -> MemoryManager:
    manager = MemoryManager({})
    monkeypatch.setattr(memory_tools, "_MANAGER", manager)
    monkeypatch.setattr(memory_tools, "get_rag_service", lambda raise_http=False: None)
    return manager


def test_recall_respects_external_flags(memory_manager):
    memory_manager.upsert_item("mundane", "hello")
    memory_manager.upsert_item("protected", "keep", sensitivity="protected")
    memory_manager.upsert_item("secret", "top", sensitivity="secret")

    user = "alice"

    sig = generate_signature(
        user,
        "recall",
        {"key": "secret", "for_external": True, "allow_protected": False},
    )
    assert (
        memory_tools.recall(
            "secret",
            user=user,
            signature=sig,
            for_external=True,
            allow_protected=False,
        )
        is None
    )

    sig = generate_signature(
        user,
        "recall",
        {"key": "protected", "for_external": True, "allow_protected": False},
    )
    assert (
        memory_tools.recall(
            "protected",
            user=user,
            signature=sig,
            for_external=True,
            allow_protected=False,
        )
        is None
    )

    sig = generate_signature(
        user,
        "recall",
        {"key": "protected", "for_external": True, "allow_protected": True},
    )
    assert (
        memory_tools.recall(
            "protected",
            user=user,
            signature=sig,
            for_external=True,
            allow_protected=True,
        )
        == "keep"
    )

    sig = generate_signature(
        user,
        "recall",
        {"key": "secret", "for_external": False, "allow_protected": False},
    )
    assert (
        memory_tools.recall(
            "secret",
            user=user,
            signature=sig,
            for_external=False,
            allow_protected=False,
        )
        == "top"
    )


def test_export_redacts_secret_and_preserves_hint(memory_manager):
    memory_manager.upsert_item(
        "secret",
        {"pwd": "hunter2"},
        sensitivity="secret",
        hint="use passphrase",
    )
    memory_manager.upsert_item("protected", "value", sensitivity="protected")

    exported = memory_manager.export_items()
    assert "secret" in exported
    secret_entry = exported["secret"]
    assert secret_entry.get("redacted") is True
    assert "value" not in secret_entry
    assert secret_entry.get("hint") == "use passphrase"
    assert "protected" in exported

    external = memory_manager.export_items(for_external=True)
    assert "secret" not in external
    assert "protected" not in external

    external_allowed = memory_manager.export_items(
        for_external=True,
        allow_protected=True,
    )
    assert "protected" in external_allowed
    assert external_allowed["protected"]["value"] == "value"


def test_remember_normalizes_relative_dates_and_sets_prunable_fields(
    memory_manager, monkeypatch
):
    monkeypatch.setattr(
        time_resolution.user_settings,
        "load_settings",
        lambda: {"user_timezone": "America/New_York"},
    )
    user = "alice"
    source_ts = datetime.now(tz=timezone.utc).timestamp()
    expected_date = (
        datetime.fromtimestamp(source_ts, tz=timezone.utc)
        .astimezone(ZoneInfo("America/New_York"))
        .date()
        + timedelta(days=1)
    ).isoformat()
    args = {
        "key": "karate_class",
        "value": "user has a karate class tomorrow",
        "grounded_at": source_ts,
    }
    sig = generate_signature(user, "remember", args)

    assert (
        memory_tools.remember(
            "karate_class",
            "user has a karate class tomorrow",
            grounded_at=source_ts,
            user=user,
            signature=sig,
        )
        == "ok"
    )

    stored = memory_manager.get_item("karate_class", touch=False)
    assert stored is not None
    assert stored["lifecycle"] == "prunable"
    assert stored["grounded_at"] == source_ts
    assert "tomorrow" not in stored["value"].lower()
    assert expected_date in stored["value"]
    assert stored["occurs_at"] < stored["review_at"]
    assert stored["decay_at"] == pytest.approx(
        stored["review_at"] + memory_manager.prunable_decay_grace_seconds
    )


def test_remember_uses_shared_relative_weekday_resolution(memory_manager, monkeypatch):
    monkeypatch.setattr(
        time_resolution.user_settings,
        "load_settings",
        lambda: {"user_timezone": "America/New_York"},
    )
    user = "alice"
    source_ts = datetime(2026, 3, 15, 14, 0, tzinfo=timezone.utc).timestamp()
    args = {
        "key": "karate_series",
        "value": "user has karate next tuesday at 6pm",
        "grounded_at": source_ts,
    }
    sig = generate_signature(user, "remember", args)

    result = memory_tools.remember(
        "karate_series",
        "user has karate next tuesday at 6pm",
        grounded_at=source_ts,
        user=user,
        signature=sig,
    )

    assert result == "ok"
    stored = memory_manager.get_item("karate_series", touch=False)
    assert stored is not None
    assert "next tuesday" not in stored["value"].lower()
    assert "2026-03-17" in stored["value"]
    assert stored["occurs_at"] == pytest.approx(
        datetime(2026, 3, 17, 22, 0, tzinfo=timezone.utc).timestamp()
    )
    assert stored["review_at"] == pytest.approx(stored["occurs_at"])


def test_evergreen_memories_do_not_receive_review_or_decay_by_default(memory_manager):
    stored = memory_manager.upsert_item("name", "user name is Kai")
    assert stored["lifecycle"] == "evergreen"
    assert stored["review_at"] is None
    assert stored["decay_at"] is None
    assert stored["occurs_at"] is None


def test_reviewable_memory_is_downranked_but_not_deleted(memory_manager):
    now = datetime.now(tz=timezone.utc).timestamp()
    memory_manager.upsert_item(
        "current_city",
        "user lives in Toronto",
        lifecycle="reviewable",
        review_at=now - 60,
    )
    stored = memory_manager.get_item("current_city", touch=False)
    assert stored is not None
    assert stored["lifecycle"] == "reviewable"
    assert stored["pruned_at"] is None
    assert memory_manager.lifecycle_multiplier(stored, now=now) == pytest.approx(0.6)


def test_prunable_memory_is_excluded_after_decay(memory_manager):
    now = datetime.now(tz=timezone.utc).timestamp()
    memory_manager.upsert_item(
        "karate_class",
        "user has karate class on 2026-03-16",
        lifecycle="prunable",
        review_at=now - 120,
        decay_at=now - 60,
    )

    memory_manager.sweep_lifecycle(now)

    assert memory_manager.get_item("karate_class", touch=False) is None
    stored = memory_manager.get_item(
        "karate_class",
        include_pruned=True,
        touch=False,
    )
    assert stored is not None
    assert stored["pruned_at"] is not None


def test_exact_key_recall_returns_review_due_but_not_pruned(memory_manager):
    user = "alice"
    now = datetime.now(tz=timezone.utc).timestamp()
    memory_manager.upsert_item(
        "employer",
        "user works at Float",
        lifecycle="reviewable",
        review_at=now - 30,
    )
    memory_manager.upsert_item(
        "old_event",
        "user had an appointment on 2026-03-16",
        lifecycle="prunable",
        review_at=now - 120,
        decay_at=now - 60,
    )
    memory_manager.sweep_lifecycle(now)

    sig = generate_signature(user, "recall", {"key": "employer"})
    assert (
        memory_tools.recall("employer", user=user, signature=sig)
        == "user works at Float"
    )

    sig = generate_signature(user, "recall", {"key": "old_event"})
    result = memory_tools.recall("old_event", user=user, signature=sig)
    assert result is None or result.get("error") == "not_found"


def test_recall_fuzzy_matches_close_key(memory_manager):
    memory_manager.upsert_item("tea_party_menu_ideas_2025-11-24", "menu ideas")
    user = "alice"
    sig = generate_signature(user, "recall", {"key": "tea_party_menu_ideas_2025-11-2"})
    result = memory_tools.recall(
        "tea_party_menu_ideas_2025-11-2",
        user=user,
        signature=sig,
    )
    assert isinstance(result, dict)
    assert result.get("resolved_key") == "tea_party_menu_ideas_2025-11-24"
    assert result.get("value") == "menu ideas"


def test_recall_returns_suggestions_when_ambiguous(memory_manager):
    memory_manager.upsert_item("alpha_one", "a1")
    memory_manager.upsert_item("alpha_two", "a2")
    user = "alice"
    sig = generate_signature(user, "recall", {"key": "alpha"})
    result = memory_tools.recall(
        "alpha",
        user=user,
        signature=sig,
    )
    assert isinstance(result, dict)
    assert result.get("error") == "not_found"
    assert "alpha_one" in result.get("suggestions", [])
    assert "alpha_two" in result.get("suggestions", [])


def test_recall_returns_recent_keys_when_missing(memory_manager):
    memory_manager.upsert_item("beach_day", "wore a blue shirt")
    memory_manager.upsert_item("tea_party_menu", "scones and tea")
    user = "alice"
    sig = generate_signature(user, "recall", {"key": ""})
    result = memory_tools.recall("", user=user, signature=sig)
    assert isinstance(result, dict)
    assert result.get("error") == "missing_key"
    recent = set(result.get("recent_keys", []))
    assert {"beach_day", "tea_party_menu"}.issubset(recent)
    details = result.get("suggestions_detail", [])
    assert any(entry.get("key") == "beach_day" for entry in details)


def test_recall_searches_value_snippets(memory_manager):
    memory_manager.upsert_item(
        "beach_day",
        "Wore the favorite blue shirt and spilled mustard.",
    )
    user = "alice"
    sig = generate_signature(user, "recall", {"key": "blue shirt"})
    result = memory_tools.recall(
        "blue shirt",
        user=user,
        signature=sig,
    )
    assert isinstance(result, dict)
    assert result.get("error") == "not_found"
    assert "beach_day" in result.get("suggestions", [])
    details = {
        entry.get("key"): entry for entry in result.get("suggestions_detail", [])
    }
    snippet = details.get("beach_day", {}).get("snippet", "").lower()
    assert "blue shirt" in snippet


def test_recall_hybrid_filters_expired_memory_backed_matches(
    memory_manager, monkeypatch
):
    now = datetime.now(tz=timezone.utc).timestamp()
    memory_manager.upsert_item("laundry_note", "Blue shirt laundry note")
    memory_manager.upsert_item(
        "packing_list",
        "Blue shirt packing checklist",
        lifecycle="prunable",
        review_at=now - 120,
        decay_at=now - 60,
    )
    memory_manager.sweep_lifecycle(now)

    class DummyRAG:
        def search_canonical(self, query, top_k=5):
            return [
                {
                    "id": "doc-canonical",
                    "text": "Blue shirt laundry note",
                    "metadata": {
                        "source": "memory:laundry_note",
                        "kind": "memory",
                        "memory_key": "laundry_note",
                        "retrieved_via": "canonical",
                    },
                    "score": 0.96,
                    "retrieved_via": "canonical",
                }
            ]

        def query(self, query, top_k=5):
            return [
                {
                    "id": "doc-vector",
                    "text": "Blue shirt packing checklist",
                    "metadata": {
                        "source": "memory:packing_list",
                        "kind": "memory",
                        "memory_key": "packing_list",
                    },
                    "score": 0.91,
                }
            ]

    monkeypatch.setattr(
        memory_tools,
        "get_rag_service",
        lambda raise_http=False: DummyRAG(),
    )

    user = "alice"
    sig = generate_signature(
        user,
        "recall",
        {"key": "blue shirt", "mode": "hybrid", "top_k": 3},
    )
    result = memory_tools.recall(
        "blue shirt",
        user=user,
        signature=sig,
        mode="hybrid",
        top_k=3,
    )
    assert isinstance(result, dict)
    assert result.get("mode") == "hybrid"
    matches = result.get("matches") or []
    assert len(matches) == 1
    assert matches[0].get("key") == "laundry_note"
    assert matches[0].get("match") == "canonical"


def test_recall_clip_returns_image_attachments(memory_manager, monkeypatch):
    class DummyRAG:
        def trace(self, doc_id):
            assert doc_id == "caption-doc"
            return {
                "text": "Orange cat sitting on stairs",
                "metadata": {
                    "source": "image:hash-cat",
                    "filename": "cat.png",
                    "content_hash": "hash-cat",
                    "content_type": "image/png",
                    "url": "/api/attachments/hash-cat/cat.png",
                },
            }

    class DummyClipRAG:
        def query(self, query, top_k=5):
            assert query == "cat on the stairs"
            return [
                {
                    "id": "clip-doc",
                    "text": "cat",
                    "metadata": {
                        "caption_doc_id": "caption-doc",
                        "source": "image:hash-cat",
                        "filename": "cat.png",
                        "content_hash": "hash-cat",
                        "content_type": "image/png",
                        "url": "/api/attachments/hash-cat/cat.png",
                    },
                    "score": 0.97,
                }
            ]

    monkeypatch.setattr(
        memory_tools, "get_rag_service", lambda raise_http=False: DummyRAG()
    )
    monkeypatch.setattr(
        memory_tools,
        "get_clip_rag_service",
        lambda raise_http=False: DummyClipRAG(),
    )

    user = "alice"
    args = {
        "key": "cat on the stairs",
        "mode": "clip",
        "include_images": True,
        "image_top_k": 2,
    }
    sig = generate_signature(user, "recall", args)
    result = memory_tools.recall(
        "cat on the stairs",
        user=user,
        signature=sig,
        mode="clip",
        include_images=True,
        image_top_k=2,
    )
    assert isinstance(result, dict)
    assert result.get("mode") == "clip"
    image_matches = result.get("image_matches") or []
    assert len(image_matches) == 1
    assert image_matches[0].get("caption") == "Orange cat sitting on stairs"
    attachments = result.get("image_attachments") or []
    assert attachments == [
        {
            "name": "cat.png",
            "url": "/api/attachments/hash-cat/cat.png",
            "content_hash": "hash-cat",
            "type": "image/png",
        }
    ]


def test_legacy_non_evergreen_with_past_end_time_migrates_to_prunable(memory_manager):
    now = datetime.now(tz=timezone.utc).timestamp()
    end_time = now - (2 * 24 * 60 * 60)
    memory_manager.store["legacy"] = {
        "value": "legacy event",
        "importance": 1.0,
        "evergreen": False,
        "end_time": end_time,
        "updated_at": now - (3 * 24 * 60 * 60),
    }

    memory_manager.sweep_lifecycle(now)

    stored = memory_manager.get_item("legacy", include_pruned=True, touch=False)
    assert stored is not None
    assert stored["lifecycle"] == "prunable"
    assert stored["pruned_at"] is not None


def test_review_executor_can_rewrite_due_prunable_memory(memory_manager):
    now = datetime.now(tz=timezone.utc).timestamp()
    memory_manager.upsert_item(
        "karate_schedule",
        "user has karate class on 2026-03-16",
        lifecycle="prunable",
        review_at=now - 120,
        decay_at=now - 60,
    )

    def review_executor(key, item, reason):
        assert key == "karate_schedule"
        assert reason == "decay"
        return {
            "action": "rewrite",
            "value": "user has karate every Tuesday",
            "lifecycle": "reviewable",
            "review_at": now + memory_manager.review_interval_seconds,
            "decay_at": None,
        }

    memory_manager.set_review_executor(review_executor)
    memory_manager.sweep_lifecycle(now)

    stored = memory_manager.get_item("karate_schedule", touch=False)
    assert stored is not None
    assert stored["value"] == "user has karate every Tuesday"
    assert stored["lifecycle"] == "reviewable"
    assert stored["pruned_at"] is None


def test_archive_item_prunes_and_restore_clears_pruned_at(memory_manager):
    memory_manager.upsert_item("note", "value")
    first = memory_manager.archive_item("note", True)
    assert first is not None
    assert first["pruned_at"] is not None

    second = memory_manager.archive_item("note", False)
    assert second is not None
    assert second["pruned_at"] is None
