from datetime import datetime, timezone

import pytest
from app.base_services import MemoryManager


def make_manager() -> MemoryManager:
    return MemoryManager({})


def test_decay_shim_does_not_change_importance():
    mgr = make_manager()
    mgr.upsert_item("keep", "v", importance=0.8, pinned=True)

    result = mgr.decay(0.2)
    item = mgr.get_item("keep", touch=False)

    assert isinstance(result, dict)
    assert isinstance(item, dict)
    assert item["importance"] == pytest.approx(0.8)


def test_pinned_and_importance_floor_remain_manual_hints():
    mgr = make_manager()
    mgr.upsert_item("floor", "v", importance=0.6, pinned=True, importance_floor=0.5)

    item = mgr.get_item("floor", touch=False)

    assert isinstance(item, dict)
    assert item["pinned"] is True
    assert item["importance_floor"] == pytest.approx(0.5)
    assert item["importance"] == pytest.approx(0.6)


def test_pinned_item_still_prunes_when_lifecycle_expires():
    mgr = make_manager()
    now = datetime.now(tz=timezone.utc).timestamp()
    mgr.upsert_item(
        "expiring",
        "user has an appointment on 2026-03-16",
        importance=0.9,
        pinned=True,
        importance_floor=0.8,
        lifecycle="prunable",
        review_at=now - 120,
        decay_at=now - 60,
    )

    mgr.sweep_lifecycle(now)

    assert mgr.get_item("expiring", touch=False) is None
    item = mgr.get_item("expiring", include_pruned=True, touch=False)
    assert isinstance(item, dict)
    assert item["pruned_at"] is not None
