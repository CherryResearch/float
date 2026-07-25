import json

import pytest
from app.utils import deployment_event_store, deployment_status

from scripts import create_release_snapshot


def test_deployment_descriptor_is_stable_per_data_root(tmp_path):
    data_root = tmp_path / "data"

    first = deployment_status.ensure_deployment_descriptor(data_root)
    second = deployment_status.ensure_deployment_descriptor(data_root)

    assert first["deployment_id"] == second["deployment_id"]
    saved = json.loads((data_root / "deployment.json").read_text(encoding="utf-8"))
    assert saved["deployment_id"] == first["deployment_id"]
    assert saved["schema_version"] == 1


def test_data_revision_observation_advances_when_digest_changes(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    deployment_status.ensure_deployment_descriptor(data_root)
    observed_times = iter(
        [
            "2026-07-16T10:00:00+00:00",
            "2026-07-16T11:00:00+00:00",
        ]
    )
    monkeypatch.setattr(deployment_status, "_now_iso", lambda: next(observed_times))

    first = deployment_status.observe_data_revision(
        {"digest": "a" * 64, "code": "d-aaaaaaaaaaaa"},
        data_root=data_root,
    )
    unchanged = deployment_status.observe_data_revision(
        {"digest": "a" * 64, "code": "d-aaaaaaaaaaaa"},
        data_root=data_root,
    )
    changed = deployment_status.observe_data_revision(
        {"digest": "b" * 64, "code": "d-bbbbbbbbbbbb"},
        data_root=data_root,
    )

    assert first["observed_at_iso"] == "2026-07-16T10:00:00+00:00"
    assert unchanged["observed_at_iso"] == first["observed_at_iso"]
    assert changed["observed_at_iso"] == "2026-07-16T11:00:00+00:00"
    events = deployment_event_store.list_events(data_root=data_root)
    assert [event["event_type"] for event in events] == [
        "data.revision",
        "data.revision",
    ]
    assert events[0]["local_revision_before"]["digest"] == "a" * 64
    assert events[0]["local_revision_after"]["digest"] == "b" * 64


def test_build_instance_status_registers_software_and_data_as_siblings(
    tmp_path, monkeypatch
):
    receipt = tmp_path / ".float-build.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "release_version": "0.1.0a1",
                "build_code": "b2026.07.15.1",
                "source_revision": "abc123",
                "source_dirty": False,
                "snapshot_digest": "f" * 64,
                "built_at": "2026-07-15T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    registry = tmp_path / "machine" / "deployments.json"
    monkeypatch.setenv("FLOAT_BUILD_RECEIPT", str(receipt))
    monkeypatch.delenv("FLOAT_BUILD_CODE", raising=False)
    monkeypatch.delenv("FLOAT_SERVICE_VERSION", raising=False)

    status = deployment_status.build_instance_status(
        settings={
            "device_display_name": "Cherry self",
            "workspace_profiles": [
                {"id": "project", "name": "Project", "kind": "local"},
                {
                    "id": "laptop-self",
                    "name": "Laptop self",
                    "kind": "synced",
                    "source_peer_id": "laptop",
                    "source_device_name": "Laptop",
                    "source_workspace_id": "self",
                    "lineage_id": "self-lineage-uuid",
                    "origin_deployment_id": "desktop-self-deployment",
                    "upstream_deployment_id": "laptop-self-deployment",
                },
            ],
            "active_workspace_id": "project",
            "sync_selected_workspace_ids": ["project", "laptop-self"],
        },
        data_root=tmp_path / "data",
        register_machine=True,
        registry_path=registry,
    )

    assert list(status) == ["schema_version", "software", "data"]
    assert status["software"]["label"] == "0.1.0a1 // b2026.07.15.1"
    assert status["software"]["snapshot_digest"] == "f" * 64
    assert status["data"]["display_name"] == "Cherry self"
    assert "data_root" not in status["data"]
    deployment_id = status["data"]["deployment_id"]
    roles = {item["id"]: item["custody_role"] for item in status["data"]["workspaces"]}
    assert roles["root"] == "primary"
    assert roles["project"] == "primary"
    assert roles["laptop-self"] == "replica"
    workspaces = {item["id"]: item for item in status["data"]["workspaces"]}
    assert workspaces["root"]["lineage_id"]
    assert workspaces["project"]["origin_deployment_id"] == deployment_id
    assert workspaces["laptop-self"]["lineage_id"] == "self-lineage-uuid"
    assert workspaces["laptop-self"]["sync_back"] == {
        "direction": "push",
        "peer_id": "laptop",
        "deployment_id": "laptop-self-deployment",
        "workspace_id": "self",
    }
    saved_registry = json.loads(registry.read_text(encoding="utf-8"))
    assert saved_registry["deployments"][deployment_id]["build_code"] == (
        "b2026.07.15.1"
    )
    assert saved_registry["deployments"][deployment_id]["data_root"] == str(
        (tmp_path / "data").resolve()
    )
    assert (
        saved_registry["deployments"][deployment_id]["workspace_lineages"][
            "laptop-self"
        ]
        == "self-lineage-uuid"
    )
    assert saved_registry["deployments"][deployment_id]["upstream_deployment_ids"] == [
        "laptop-self-deployment"
    ]


def test_software_comparison_prefers_digest_then_build_then_version():
    local = {
        "release_version": "0.1.0a1",
        "build_code": "b12",
        "snapshot_digest": "a" * 64,
    }

    exact = deployment_status.compare_software_status(
        local,
        {**local, "build_code": "different"},
    )
    same_build = deployment_status.compare_software_status(
        local,
        {
            "release_version": "0.1.0a1",
            "build_code": "b12",
            "snapshot_digest": "b" * 64,
        },
    )
    compatible = deployment_status.compare_software_status(
        local,
        {"release_version": "0.1.0a1", "build_code": "b11"},
    )
    mismatch = deployment_status.compare_software_status(
        local,
        {"release_version": "0.2.0", "build_code": "b1"},
    )

    assert exact["state"] == "exact"
    assert same_build["state"] == "same_build"
    assert compatible["state"] == "compatible"
    assert mismatch["state"] == "version_mismatch"


def test_release_snapshot_writes_separate_build_receipt(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "float-project"\nversion = "0.1.0a1"\n',
        encoding="utf-8",
    )
    source = repo / "main.py"
    source.write_text("print('float')\n", encoding="utf-8")
    output = tmp_path / "snapshot"
    output.mkdir()
    monkeypatch.setattr(create_release_snapshot, "REPO_ROOT", repo)
    monkeypatch.setenv("FLOAT_SOURCE_REVISION", "revision-1")

    receipt = create_release_snapshot.write_build_receipt(
        output,
        [source],
        "b2026.07.15.1",
    )

    assert receipt["release_version"] == "0.1.0a1"
    assert receipt["build_code"] == "b2026.07.15.1"
    assert receipt["source_revision"] == "revision-1"
    assert len(str(receipt["snapshot_digest"])) == 64
    saved = json.loads(
        (output / create_release_snapshot.BUILD_RECEIPT_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert saved == receipt


def test_release_snapshot_excludes_test_for_internal_bakeoff_runner():
    relative = "backend/app/services/tests/test_threads_embedding_bakeoff_eval.py"

    assert create_release_snapshot.is_relative_excluded(relative)
    assert not create_release_snapshot.is_manifest_relative_path(relative)


def test_invalid_build_code_is_rejected(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "float-project"\nversion = "0.1.0a1"\n',
        encoding="utf-8",
    )
    source = repo / "main.py"
    source.write_text("pass\n", encoding="utf-8")
    output = tmp_path / "snapshot"
    output.mkdir()
    monkeypatch.setattr(create_release_snapshot, "REPO_ROOT", repo)

    with pytest.raises(ValueError, match="Build code"):
        create_release_snapshot.write_build_receipt(output, [source], "bad code!")
