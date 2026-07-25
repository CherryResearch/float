from app.utils import sync_checkpoint_store


def _manifest(*items):
    return {
        "sections": {
            "conversations": {
                "items": list(items),
            }
        }
    }


def test_manifest_revision_is_order_independent():
    alpha = {"sync_id": "alpha", "updated_at": 10.0, "label": "Alpha"}
    beta = {"sync_id": "beta", "updated_at": 20.0, "label": "Beta"}

    forward = sync_checkpoint_store.manifest_revision(_manifest(alpha, beta))
    reverse = sync_checkpoint_store.manifest_revision(_manifest(beta, alpha))

    assert forward["digest"] == reverse["digest"]
    assert forward["code"].startswith("d-")
    assert forward["updated_at"] == 20.0
    assert forward["item_count"] == 2


def test_verified_common_state_does_not_claim_a_successful_sync(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sync_checkpoint_store,
        "CHECKPOINTS_PATH",
        tmp_path / "sync_checkpoints.json",
    )
    manifest = _manifest({"sync_id": "alpha", "updated_at": 10.0, "label": "Alpha"})
    view, selections = sync_checkpoint_store.build_equal_view_baseline(
        local_manifest=manifest,
        remote_manifest=manifest,
        sections=["conversations"],
    )

    checkpoint = sync_checkpoint_store.record_checkpoint(
        peer_deployment_id="peer-deployment",
        peer_id="peer-1",
        peer_label="Pear",
        direction="preview_verified",
        sections=["conversations"],
        item_selections=selections,
        local_revision=sync_checkpoint_store.manifest_revision(manifest),
        remote_revision=sync_checkpoint_store.manifest_revision(manifest),
        scope={"workspace_mode": "merge"},
        views={"pull": view, "push": view},
        synced_at=100.0,
        successful_sync=False,
    )
    summary = sync_checkpoint_store.checkpoint_summary(checkpoint)

    assert summary["state"] == "verified_common"
    assert "last_synced_at" not in checkpoint
    assert checkpoint["last_verified_at"] == 100.0
    assert (
        sync_checkpoint_store.comparison_baseline(checkpoint, "pull")["sections"][
            "conversations"
        ]["items"]["alpha"]["local_present"]
        is True
    )


def test_successful_partial_sync_merges_item_baselines(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sync_checkpoint_store,
        "CHECKPOINTS_PATH",
        tmp_path / "sync_checkpoints.json",
    )
    scope = {"workspace_mode": "merge"}
    first_manifest = _manifest(
        {"sync_id": "alpha", "updated_at": 10.0, "label": "Alpha"}
    )
    second_manifest = _manifest(
        {"sync_id": "beta", "updated_at": 20.0, "label": "Beta"}
    )

    for timestamp, manifest, selection_id in (
        (100.0, first_manifest, "alpha"),
        (200.0, second_manifest, "beta"),
    ):
        view = sync_checkpoint_store.build_view_baseline(
            local_manifest=manifest,
            remote_manifest=manifest,
            item_selections={"conversations": [selection_id]},
        )
        sync_checkpoint_store.record_checkpoint(
            peer_deployment_id="peer-deployment",
            peer_id="peer-1",
            peer_label="Pear",
            direction="pull",
            sections=["conversations"],
            item_selections={"conversations": [selection_id]},
            local_revision=sync_checkpoint_store.manifest_revision(manifest),
            remote_revision=sync_checkpoint_store.manifest_revision(manifest),
            scope=scope,
            views={"pull": view},
            synced_at=timestamp,
        )

    saved = sync_checkpoint_store.load_checkpoint(
        peer_deployment_id="peer-deployment",
        peer_id="peer-1",
        scope=scope,
    )
    items = saved["views"]["pull"]["sections"]["conversations"]["items"]

    assert set(items) == {"alpha", "beta"}
    assert saved["last_synced_at"] == 200.0
    assert saved["item_selections"]["conversations"] == ["alpha", "beta"]


def test_observed_baseline_records_complete_section_without_claiming_sync(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        sync_checkpoint_store,
        "CHECKPOINTS_PATH",
        tmp_path / "sync_checkpoints.json",
    )
    local = _manifest({"sync_id": "local-only", "updated_at": 10.0, "label": "Local"})
    remote = _manifest(
        {"sync_id": "remote-only", "updated_at": 20.0, "label": "Remote"}
    )
    observed = sync_checkpoint_store.build_observed_view_baseline(
        local_manifest=local,
        remote_manifest=remote,
        sections=["conversations"],
    )

    checkpoint = sync_checkpoint_store.record_checkpoint(
        peer_deployment_id="peer-deployment",
        peer_id="peer-1",
        peer_label="Pear",
        direction="preview_observed",
        sections=["conversations"],
        item_selections={},
        local_revision=sync_checkpoint_store.manifest_revision(local),
        remote_revision=sync_checkpoint_store.manifest_revision(remote),
        scope={"workspace_mode": "merge"},
        views={"push": observed},
        synced_at=100.0,
        successful_sync=False,
    )

    section = checkpoint["views"]["push"]["sections"]["conversations"]
    assert section["complete"] is True
    assert set(section["items"]) == {"local-only", "remote-only"}
    assert sync_checkpoint_store.checkpoint_summary(checkpoint)["state"] == "observed"


def test_later_preview_does_not_accept_new_difference_into_complete_baseline(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        sync_checkpoint_store,
        "CHECKPOINTS_PATH",
        tmp_path / "sync_checkpoints.json",
    )
    empty = _manifest()
    scope = {"workspace_mode": "merge"}
    first_view = sync_checkpoint_store.build_observed_view_baseline(
        local_manifest=empty,
        remote_manifest=empty,
        sections=["conversations"],
    )
    sync_checkpoint_store.record_checkpoint(
        peer_deployment_id="peer-deployment",
        peer_id="peer-1",
        peer_label="Pear",
        direction="preview_observed",
        sections=["conversations"],
        item_selections={},
        local_revision=sync_checkpoint_store.manifest_revision(empty),
        remote_revision=sync_checkpoint_store.manifest_revision(empty),
        scope=scope,
        views={"push": first_view},
        synced_at=100.0,
        successful_sync=False,
    )
    local = _manifest(
        {"sync_id": "created-downstream", "updated_at": 20.0, "label": "New"}
    )
    later_view = sync_checkpoint_store.build_observed_view_baseline(
        local_manifest=local,
        remote_manifest=empty,
        sections=["conversations"],
    )
    sync_checkpoint_store.record_checkpoint(
        peer_deployment_id="peer-deployment",
        peer_id="peer-1",
        peer_label="Pear",
        direction="preview_observed",
        sections=["conversations"],
        item_selections={},
        local_revision=sync_checkpoint_store.manifest_revision(local),
        remote_revision=sync_checkpoint_store.manifest_revision(empty),
        scope=scope,
        views={"push": later_view},
        synced_at=200.0,
        successful_sync=False,
    )

    saved = sync_checkpoint_store.load_checkpoint(
        peer_deployment_id="peer-deployment",
        peer_id="peer-1",
        scope=scope,
    )
    baseline = saved["views"]["push"]["sections"]["conversations"]
    assert baseline["complete"] is True
    assert baseline["items"] == {}
