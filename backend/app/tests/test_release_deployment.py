import json
from pathlib import Path

import pytest
from app.utils import deployment_event_store

from scripts import create_release_snapshot, deploy_release_snapshot


def _snapshot(tmp_path: Path) -> Path:
    snapshot = tmp_path / "snapshot"
    (snapshot / "backend").mkdir(parents=True)
    (snapshot / "backend" / "main.py").write_text(
        "print('new')\n",
        encoding="utf-8",
    )
    shipped = ["backend/main.py"]
    digest = deploy_release_snapshot._snapshot_digest(snapshot, shipped)
    (snapshot / create_release_snapshot.BUILD_RECEIPT_NAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "release_version": "0.1.0a1",
                "build_code": "b-test",
                "source_revision": "abc123",
                "snapshot_digest": digest,
            }
        ),
        encoding="utf-8",
    )
    return snapshot


def test_deploy_snapshot_preserves_runtime_and_prunes_bootstrap_stale_file(tmp_path):
    snapshot = _snapshot(tmp_path)
    target = tmp_path / "target"
    (target / "backend").mkdir(parents=True)
    (target / "backend" / "main.py").write_text("print('old')\n", encoding="utf-8")
    (target / "backend" / "stale.py").write_text("stale\n", encoding="utf-8")
    (target / "data").mkdir()
    (target / "data" / "deployment.json").write_text(
        '{"deployment_id":"stable"}\n',
        encoding="utf-8",
    )
    (target / ".env").write_text("SECRET=preserved\n", encoding="utf-8")

    plan = deploy_release_snapshot.build_deployment_plan(
        snapshot=snapshot,
        target=target,
        bootstrap_prune=True,
    )
    manifest = deploy_release_snapshot.apply_deployment_plan(plan)

    assert (target / "backend" / "main.py").read_text(encoding="utf-8") == (
        "print('new')\n"
    )
    assert not (target / "backend" / "stale.py").exists()
    assert (target / ".env").read_text(encoding="utf-8") == "SECRET=preserved\n"
    assert (
        json.loads((target / "data" / "deployment.json").read_text(encoding="utf-8"))[
            "deployment_id"
        ]
        == "stable"
    )
    assert manifest["build_code"] == "b-test"
    assert manifest["installed_files"] == [
        create_release_snapshot.BUILD_RECEIPT_NAME,
        "backend/main.py",
    ]
    events = deployment_event_store.list_events(data_root=target / "data")
    assert len(events) == 1
    assert events[0]["event_type"] == "software.install"
    assert events[0]["deployment_id"] == "stable"
    assert events[0]["software_after"]["build_code"] == "b-test"


def test_deploy_snapshot_uses_installed_manifest_for_later_stale_cleanup(tmp_path):
    snapshot = _snapshot(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    (target / "README.md").write_text("unmanaged\n", encoding="utf-8")
    (target / "backend").mkdir()
    (target / "backend" / "retired.py").write_text("old\n", encoding="utf-8")
    (target / create_release_snapshot.DEPLOYMENT_MANIFEST_NAME).write_text(
        json.dumps(
            {
                "installed_files": [
                    create_release_snapshot.BUILD_RECEIPT_NAME,
                    "backend/retired.py",
                ]
            }
        ),
        encoding="utf-8",
    )

    plan = deploy_release_snapshot.build_deployment_plan(
        snapshot=snapshot,
        target=target,
    )
    deploy_release_snapshot.apply_deployment_plan(plan)

    assert not (target / "backend" / "retired.py").exists()
    assert (target / "README.md").read_text(encoding="utf-8") == "unmanaged\n"


def test_deploy_snapshot_prunes_explicitly_retired_shipped_path(tmp_path):
    snapshot = _snapshot(tmp_path)
    target = tmp_path / "target"
    retired_relative = next(iter(deploy_release_snapshot.RETIRED_SHIPPED_PATHS))
    retired = target / retired_relative
    retired.parent.mkdir(parents=True)
    retired.write_text("internal eval\n", encoding="utf-8")
    (target / create_release_snapshot.DEPLOYMENT_MANIFEST_NAME).write_text(
        json.dumps(
            {
                "installed_files": [
                    create_release_snapshot.BUILD_RECEIPT_NAME,
                    retired_relative,
                ]
            }
        ),
        encoding="utf-8",
    )

    plan = deploy_release_snapshot.build_deployment_plan(
        snapshot=snapshot,
        target=target,
    )
    assert plan["stale_files"] == [retired_relative]

    deploy_release_snapshot.apply_deployment_plan(plan)

    assert not retired.exists()


def test_deploy_snapshot_rejects_non_shipped_paths(tmp_path):
    snapshot = _snapshot(tmp_path)
    (snapshot / ".env").write_text("SECRET=bad\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()

    with pytest.raises(ValueError, match="non-shipped path"):
        deploy_release_snapshot.build_deployment_plan(
            snapshot=snapshot,
            target=target,
        )


@pytest.mark.parametrize(
    "runtime_path",
    [
        ".dev_state.json",
        ".env",
        "AGENTS.md",
        "blobs/private.bin",
        "conversations/private.json",
        "data/conversations/private.json",
        "logs/server.log",
        "models/local-model.bin",
        "node_modules/runtime/package.json",
        "user_settings.json",
    ],
)
def test_deploy_snapshot_rejects_runtime_paths_in_installed_manifest(
    tmp_path, runtime_path
):
    snapshot = _snapshot(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    protected = target / runtime_path
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("preserved\n", encoding="utf-8")
    (target / create_release_snapshot.DEPLOYMENT_MANIFEST_NAME).write_text(
        json.dumps(
            {
                "installed_files": [
                    create_release_snapshot.BUILD_RECEIPT_NAME,
                    runtime_path,
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="non-shipped path"):
        deploy_release_snapshot.build_deployment_plan(
            snapshot=snapshot,
            target=target,
        )

    assert protected.read_text(encoding="utf-8") == "preserved\n"
