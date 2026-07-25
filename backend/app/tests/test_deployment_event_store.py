import sqlite3

import pytest
from app.utils import deployment_event_store, deployment_status, memory_store


def test_deployment_event_ledgers_are_local_content_free_hash_chains(tmp_path):
    first_root = tmp_path / "first" / "data"
    second_root = tmp_path / "second" / "data"
    first_id = deployment_status.ensure_deployment_descriptor(first_root)[
        "deployment_id"
    ]
    second_id = deployment_status.ensure_deployment_descriptor(second_root)[
        "deployment_id"
    ]

    install = deployment_event_store.record_event(
        event_type="software.install",
        data_root=first_root,
        counts={"changed_file_count": 3},
        software_after={
            "release_version": "0.1.0a1",
            "build_code": "b2026.07.16.7",
            "snapshot_digest": "a" * 64,
        },
    )
    sync = deployment_event_store.record_event(
        event_type="data.sync",
        data_root=first_root,
        direction="pull",
        peer_deployment_id=second_id,
        workspace_lineage_ids=["2b288e13-8593-54f1-9267-0c5f5abc2153"],
        sections=["memories", "conversations"],
        counts={"before_count": 4, "after_count": 7, "section_count": 2},
        local_revision_before={"code": "d-before", "digest": "b" * 64},
        local_revision_after={"code": "d-after", "digest": "c" * 64},
    )

    events = deployment_event_store.list_events(data_root=first_root)
    assert [event["event_id"] for event in events] == [
        sync["event_id"],
        install["event_id"],
    ]
    assert all(event["deployment_id"] == first_id for event in events)
    assert events[0]["previous_event_hash"] == install["event_hash"]
    assert deployment_event_store.list_events(data_root=second_root) == []
    assert deployment_event_store.verify_chain(data_root=first_root) == {
        "valid": True,
        "event_count": 2,
        "broken_sequence": None,
    }


def test_deployment_event_store_rejects_content_bearing_fields(tmp_path):
    data_root = tmp_path / "data"

    with pytest.raises(ValueError, match="content-free identifier"):
        deployment_event_store.record_event(
            event_type="data.sync",
            data_root=data_root,
            direction="pull",
            peer_deployment_id="private memory sentence",
        )

    with pytest.raises(ValueError, match="unsupported field"):
        deployment_event_store.record_event(
            event_type="software.install",
            data_root=data_root,
            software_after={"prompt": "private"},
        )


def test_memory_store_records_counts_without_memory_content(tmp_path):
    data_root = tmp_path / "data"
    deployment_status.ensure_deployment_descriptor(data_root)
    target = data_root / "databases" / "memory.sqlite3"
    initial = {
        f"memory-{index}": {
            "value": f"SENTINEL PRIVATE MEMORY {index}",
            "updated_at": float(index),
        }
        for index in range(12)
    }

    memory_store.save(initial, target)
    memory_store.save({"memory-11": initial["memory-11"]}, target)

    events = deployment_event_store.list_events(data_root=data_root)
    assert events[0]["event_type"] == "data.bulk_replace"
    assert events[0]["counts"] == {
        "after_count": 1,
        "before_count": 12,
        "changed_count": 11,
        "created_count": 0,
        "deleted_count": 11,
        "updated_count": 0,
    }
    ledger_bytes = deployment_event_store.ledger_path(data_root).read_bytes()
    assert b"SENTINEL PRIVATE MEMORY" not in ledger_bytes


def test_chain_verification_detects_modified_metadata(tmp_path):
    data_root = tmp_path / "data"
    deployment_event_store.record_event(
        event_type="data.update",
        data_root=data_root,
        sections=["memories"],
        counts={"changed_count": 1},
    )
    with sqlite3.connect(str(deployment_event_store.ledger_path(data_root))) as conn:
        conn.execute(
            "UPDATE deployment_events SET counts_json = ? WHERE sequence = 1",
            ('{"changed_count":999}',),
        )
        conn.commit()

    result = deployment_event_store.verify_chain(data_root=data_root)
    assert result["valid"] is False
    assert result["broken_sequence"] == 1
