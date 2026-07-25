from datetime import date

from app.model_catalog.lifecycle import build_model_catalog, model_lifecycle


def test_model_lifecycle_does_not_treat_gpt_5_4_family_as_deprecated():
    assert model_lifecycle("gpt-5.4", as_of=date(2026, 7, 9))["status"] == "fallback"
    assert model_lifecycle("gpt-5.4-mini", as_of=date(2026, 7, 9))["selectable"] is True


def test_model_lifecycle_changes_deprecated_snapshot_to_removed_after_shutdown():
    before = model_lifecycle("gpt-5-chat-latest", as_of=date(2026, 7, 9))
    after = model_lifecycle("gpt-5-chat-latest", as_of=date(2026, 7, 24))

    assert before["status"] == "deprecated"
    assert after["status"] == "removed"
    assert before["replacement"] == "gpt-5.5"


def test_catalog_preserves_unavailable_persisted_selection_for_migration():
    catalog = build_model_catalog(
        ["chat-latest", "gpt-5.6-terra"],
        selected_model="gpt-5.5-2026-04-23",
        as_of=date(2026, 7, 9),
    )

    assert catalog["selectable_models"] == ["chat-latest", "gpt-5.6-terra"]
    assert catalog["selection"]["id"] == "gpt-5.5-2026-04-23"
    assert catalog["selection"]["available"] is False
    assert catalog["migration"] == {
        "from": "gpt-5.5-2026-04-23",
        "to": "chat-latest",
        "kind": "upgrade",
        "required": False,
        "shutdown_at": None,
    }
