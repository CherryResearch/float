import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from app.main import app
    from app.utils import (
        device_registry,
        rendezvous_store,
        sync_review_store,
        user_settings,
    )

    monkeypatch.setattr(
        user_settings, "USER_SETTINGS_PATH", tmp_path / "user_settings.json"
    )
    monkeypatch.setattr(device_registry, "DEVICES_PATH", tmp_path / "devices.json")
    monkeypatch.setattr(
        rendezvous_store, "RENDEZVOUS_PATH", tmp_path / "gateway_rendezvous.json"
    )
    monkeypatch.setattr(
        sync_review_store, "REVIEWS_PATH", tmp_path / "sync_reviews.json"
    )
    return TestClient(app)


def test_pairing_offer_accept_registers_device(client):
    offer_res = client.post(
        "/pairing/offers", json={"requested_scopes": ["sync", "stream"]}
    )
    assert offer_res.status_code == 200
    code = offer_res.json()["offer"]["code"]

    accept_res = client.post(
        "/pairing/offers/accept",
        json={
            "code": code,
            "device_name": "laptop",
            "public_key": "pk-laptop",
            "requested_scopes": ["sync"],
        },
    )
    assert accept_res.status_code == 200
    payload = accept_res.json()
    assert payload["paired_device"]["remote_device_id"]
    assert payload["current_device"]["public_key"]

    devices_res = client.get("/devices")
    devices = devices_res.json()["devices"]
    assert len(devices) == 1
    stored = next(iter(devices.values()))
    assert stored["name"] == "laptop"
    assert stored["public_key"] == "pk-laptop"


def test_pairing_accept_blocks_lan_when_hidden(client):
    offer_res = client.post("/pairing/offers", json={"requested_scopes": ["sync"]})
    assert offer_res.status_code == 200
    code = offer_res.json()["offer"]["code"]

    blocked = client.post(
        "/pairing/offers/accept",
        headers={"x-forwarded-for": "192.168.1.25"},
        json={
            "code": code,
            "device_name": "laptop",
            "public_key": "pk-laptop",
            "requested_scopes": ["sync"],
        },
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "LAN visibility is turned off for this device."


def test_pairing_accept_allows_lan_when_enabled(client):
    client.post("/user-settings", json={"sync_visible_on_lan": True})
    offer_res = client.post("/pairing/offers", json={"requested_scopes": ["sync"]})
    assert offer_res.status_code == 200
    code = offer_res.json()["offer"]["code"]

    accepted = client.post(
        "/pairing/offers/accept",
        headers={"x-forwarded-for": "192.168.1.25"},
        json={
            "code": code,
            "device_name": "laptop",
            "public_key": "pk-laptop",
            "requested_scopes": ["sync"],
        },
    )
    assert accepted.status_code == 200


def test_sync_overview_reports_visibility_and_urls(client):
    client.post("/user-settings", json={"sync_visible_on_lan": True})
    overview = client.get("/sync/overview", headers={"host": "localhost:5000"})
    assert overview.status_code == 200
    payload = overview.json()
    access = payload["device_access"]
    assert access["visibility"]["lan_enabled"] is True
    assert access["visibility"]["online_supported"] is False
    assert payload["sync_defaults"]["visible_on_lan"] is True
    assert access["advertised_urls"]["local"].endswith(":5000")
    assert access["internet_status"] == "coming_soon"
    assert payload["workspaces"]["active_workspace_id"] == "root"
    assert payload["workspaces"]["selected_workspace_ids"] == ["root"]
    assert payload["workspaces"]["profiles"][0]["id"] == "root"


def test_sync_overview_prefers_resolvable_hostname_for_lan_url(client, monkeypatch):
    from app.utils import device_visibility

    monkeypatch.setattr(device_visibility, "_detect_lan_ips", lambda: ["192.168.0.44"])
    monkeypatch.setattr(device_visibility.socket, "gethostname", lambda: "Pear")
    monkeypatch.setattr(
        device_visibility,
        "_resolve_ipv4_addresses",
        lambda host: ["192.168.0.44"]
        if host in {"Pear", "pear", "Pear.local", "pear.local"}
        else [],
    )

    client.post("/user-settings", json={"sync_visible_on_lan": True})
    overview = client.get("/sync/overview", headers={"host": "localhost:5000"})
    assert overview.status_code == 200
    payload = overview.json()
    assert (
        payload["device_access"]["advertised_urls"]["lan"] == "http://pear.local:5000"
    )


def test_sync_pair_stores_saved_peer(client, monkeypatch):
    from app import routes

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "paired_device": {"remote_device_id": "remote-device-1"},
                "current_device": {"display_name": "studio-desktop"},
            }

    monkeypatch.setattr(
        routes.http_session, "post", lambda *args, **kwargs: FakeResponse()
    )

    res = client.post(
        "/sync/pair",
        json={
            "remote_url": "http://example.test:5000",
            "code": "PAIR1234",
            "label": "studio",
            "scopes": ["sync", "files"],
            "local_workspace_ids": ["root"],
            "remote_workspace_ids": ["root"],
            "workspace_mode": "import",
            "local_target_workspace_id": "root",
            "remote_target_workspace_id": "root",
        },
    )
    assert res.status_code == 200
    paired = res.json()["paired_device"]
    assert paired["label"] == "studio"
    assert paired["remote_device_id"] == "remote-device-1"
    assert paired["scopes"] == ["sync", "files"]
    assert paired["workspace_mode"] == "import"
    assert paired["local_workspace_ids"] == ["root"]

    overview = client.get("/sync/overview").json()
    peers = overview["sync_defaults"]["saved_peers"]
    assert len(peers) == 1
    assert peers[0]["remote_url"] == "http://example.test:5000"
    assert peers[0]["remote_device_id"] == "remote-device-1"
    assert peers[0]["workspace_mode"] == "import"


def test_sync_pair_updates_existing_saved_peer_when_peer_id_supplied(
    client, monkeypatch
):
    from app import routes

    client.post(
        "/user-settings",
        json={
            "sync_saved_peers": [
                {
                    "id": "peer-1",
                    "label": "studio",
                    "remote_url": "http://wrong-host:5000",
                    "scopes": ["sync"],
                }
            ]
        },
    )

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "paired_device": {"remote_device_id": "remote-device-2"},
                "current_device": {"display_name": "studio-desktop"},
            }

    monkeypatch.setattr(
        routes.http_session, "post", lambda *args, **kwargs: FakeResponse()
    )

    res = client.post(
        "/sync/pair",
        json={
            "peer_id": "peer-1",
            "remote_url": "http://correct-host:5000",
            "code": "PAIR1234",
            "label": "studio",
            "scopes": ["sync"],
        },
    )
    assert res.status_code == 200
    paired = res.json()["paired_device"]
    assert paired["id"] == "peer-1"
    assert paired["remote_url"] == "http://correct-host:5000"

    overview = client.get("/sync/overview").json()
    peers = overview["sync_defaults"]["saved_peers"]
    assert len(peers) == 1
    assert peers[0]["id"] == "peer-1"
    assert peers[0]["remote_url"] == "http://correct-host:5000"


def test_sync_pair_rejects_incomplete_private_ip(client):
    res = client.post(
        "/sync/pair",
        json={
            "remote_url": "10.5.2:59175",
            "code": "PAIR1234",
            "label": "studio",
            "scopes": ["sync"],
        },
    )
    assert res.status_code == 400
    assert "full private address" in res.json()["detail"]


def test_sync_pair_revoke_removes_local_pair(client, monkeypatch):
    from app import routes

    paired = {
        "id": "peer-1",
        "label": "studio",
        "remote_url": "http://example.test:5000",
        "scopes": ["sync"],
        "remote_device_id": "remote-device-1",
        "public_key": "pk-local",
    }
    client.post("/user-settings", json={"sync_saved_peers": [paired]})

    called = {"value": False}

    def _fake_delete(self):
        called["value"] = True

    monkeypatch.setattr(routes.RemoteFloatClient, "delete_remote_device", _fake_delete)

    res = client.post(
        "/sync/pair/revoke",
        json={"paired_device": paired, "remove_local_pair": True},
    )
    assert res.status_code == 200
    assert called["value"] is True
    overview = client.get("/sync/overview").json()
    assert overview["sync_defaults"]["saved_peers"] == []


def test_sync_ingest_queues_review_until_approved(client):
    register_res = client.post(
        "/devices/register",
        json={
            "public_key": "pk-laptop",
            "name": "laptop",
            "capabilities": {"sync": True},
        },
    )
    assert register_res.status_code == 200
    device_id = register_res.json()["device"]["id"]

    token_res = client.post(
        "/devices/token",
        json={"device_id": device_id, "scopes": ["sync"]},
    )
    assert token_res.status_code == 200
    token = token_res.json()["token"]

    ingest_res = client.post(
        "/sync/ingest",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "snapshot": {
                "instance": {"hostname": "Pear"},
                "sections": {"settings": {"theme": "dark"}},
            },
            "source_label": "Pear",
        },
    )
    assert ingest_res.status_code == 200
    queued = ingest_res.json()
    assert queued["status"] == "pending_review"

    overview = client.get("/sync/overview").json()
    assert len(overview["sync_reviews"]["pending"]) == 1

    approve_res = client.post(
        f"/sync/reviews/{queued['review_request_id']}/approve", json={}
    )
    assert approve_res.status_code == 200
    assert approve_res.json()["status"] == "approved"

    final_overview = client.get("/sync/overview").json()
    assert final_overview["sync_reviews"]["pending"] == []
    assert final_overview["sync_reviews"]["recent"][0]["status"] == "approved"


def test_prune_legacy_devices_removes_browser_records_only(client):
    client.post(
        "/devices/register",
        json={
            "public_key": "legacy-pk",
            "name": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_4 like Mac OS X) Apple",
            "capabilities": {},
        },
    )
    client.post(
        "/devices/register",
        json={
            "public_key": "real-pk",
            "name": "Pear",
            "capabilities": {"instance_sync": True, "requested_scopes": ["sync"]},
        },
    )

    prune_res = client.post("/devices/prune-legacy")
    assert prune_res.status_code == 200
    assert prune_res.json()["removed"] == 1

    overview = client.get("/sync/overview").json()
    assert len(overview["legacy_inbound_devices"]) == 0
    assert len(overview["inbound_devices"]) == 1


def test_sync_peer_status_reports_remote_visibility(client, monkeypatch):
    from app import routes

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "current_device": {
                    "display_name": "Pear",
                    "hostname": "pear-host",
                    "source_namespace": "Pear",
                },
                "device_access": {
                    "visibility": {"lan_enabled": True},
                    "advertised_urls": {
                        "lan": "http://192.168.0.8:59175",
                        "local": "http://localhost:59175",
                    },
                },
                "sync_defaults": {"visible_on_lan": True},
                "workspaces": {
                    "active_workspace_id": "root",
                    "selected_workspace_ids": ["root"],
                    "profiles": [
                        {
                            "id": "root",
                            "name": "Main workspace",
                            "slug": "main",
                            "namespace": "",
                            "root_path": "data/files/workspace",
                            "kind": "root",
                            "is_root": True,
                        }
                    ],
                },
            }

    monkeypatch.setattr(
        routes.http_session, "get", lambda *args, **kwargs: FakeResponse()
    )

    res = client.post(
        "/sync/peer/status",
        json={"remote_url": "http://192.168.0.8:59175"},
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["reachable"] is True
    assert payload["display_name"] == "Pear"
    assert payload["visible_on_lan"] is True
    assert payload["workspaces"]["profiles"][0]["id"] == "root"


def test_gateway_offer_accept_and_session(client):
    offer_res = client.post(
        "/gateway/rendezvous/offers",
        json={
            "device_name": "desktop",
            "public_key": "pk-desktop",
            "requested_scopes": ["sync"],
            "candidate_urls": ["http://desktop.local:5000"],
        },
    )
    assert offer_res.status_code == 200
    code = offer_res.json()["code"]

    accept_res = client.post(
        "/gateway/rendezvous/accept",
        json={
            "code": code,
            "device_name": "laptop",
            "public_key": "pk-laptop",
            "candidate_urls": ["http://laptop.local:5000"],
        },
    )
    assert accept_res.status_code == 200
    accepted = accept_res.json()
    assert accepted["peer_device_name"] == "desktop"
    assert accepted["candidate_urls"] == ["http://desktop.local:5000"]

    session_res = client.post(
        "/gateway/sessions",
        json={
            "peer_device_id": "remote-device-1",
            "scopes": ["sync", "stream"],
            "candidate_urls": ["http://desktop.local:5000"],
        },
    )
    assert session_res.status_code == 200
    session = session_res.json()
    assert session["session_token"]
    assert session["candidate_urls"] == ["http://desktop.local:5000"]


def test_sync_apply_pull_import_adds_synced_workspace_profile(client, monkeypatch):
    from app import routes

    paired = {
        "id": "peer-1",
        "label": "pear",
        "remote_url": "http://example.test:5000",
        "scopes": ["sync"],
        "remote_device_id": "remote-device-1",
        "public_key": "pk-local",
        "remote_device_name": "Pear",
        "local_workspace_ids": ["root"],
        "remote_workspace_ids": ["root"],
        "workspace_mode": "import",
        "local_target_workspace_id": "root",
        "remote_target_workspace_id": "root",
    }
    client.post("/user-settings", json={"sync_saved_peers": [paired]})

    monkeypatch.setattr(
        routes.RemoteFloatClient,
        "get_sync_overview",
        lambda self: {
            "workspaces": {
                "active_workspace_id": "root",
                "selected_workspace_ids": ["root"],
                "profiles": [
                    {
                        "id": "root",
                        "name": "Main workspace",
                        "slug": "main",
                        "namespace": "",
                        "root_path": "data/files/workspace",
                        "kind": "root",
                        "is_root": True,
                    }
                ],
            }
        },
    )
    monkeypatch.setattr(
        routes.RemoteFloatClient,
        "export_snapshot",
        lambda self, sections, workspace_ids=None: {
            "instance": {
                "display_name": "Pear",
                "hostname": "pear-host",
                "source_namespace": "",
            },
            "sections": {
                "conversations": [
                    {
                        "sync_id": "conv-1",
                        "name": "pear-notes",
                        "metadata": {
                            "id": "conv-1",
                            "created_at": "2026-03-24T22:00:00+00:00",
                            "updated_at": "2026-03-24T22:01:00+00:00",
                            "display_name": "Pear notes",
                        },
                        "messages": [{"role": "user", "content": "remote"}],
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(
        routes.RemoteFloatClient,
        "get_pairing_state",
        lambda self: dict(paired),
    )

    res = client.post(
        "/sync/apply",
        json={
            "remote_url": "http://example.test:5000",
            "direction": "pull",
            "sections": ["conversations"],
            "paired_device": paired,
            "workspace_mode": "import",
            "local_workspace_ids": ["root"],
            "remote_workspace_ids": ["root"],
            "local_target_workspace_id": "root",
            "remote_target_workspace_id": "root",
        },
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["workspace_mode"] == "import"
    settings = client.get("/user-settings").json()
    profiles = settings["workspace_profiles"]
    imported = next(
        profile
        for profile in profiles
        if profile.get("source_peer_id") == "peer-1"
        and profile.get("source_device_name") == "Pear"
    )
    assert imported["name"] == "Pear"
    assert imported["namespace"] == "Pear"
    assert imported["root_path"] == "data/sync/Pear/workspace"


def test_sync_apply_push_filters_selected_items(client, monkeypatch):
    from app import routes

    captured = {}

    class FakeSyncService:
        def normalize_sections(self, sections):
            return list(sections or [])

        def normalize_item_selections(self, sections, selections):
            return selections or {}

        def current_instance_identity(self, source_namespace=None):
            return {
                "display_name": "Local",
                "hostname": "local-host",
                "source_namespace": source_namespace or "",
            }

        def build_snapshot(self, sections, workspace_ids=None):
            return {
                "sections": {
                    "conversations": [
                        {
                            "sync_id": "conv-1",
                            "name": "notes/one",
                            "metadata": {"id": "conv-1"},
                            "messages": [{"role": "user", "content": "one"}],
                        },
                        {
                            "sync_id": "conv-2",
                            "name": "notes/two",
                            "metadata": {"id": "conv-2"},
                            "messages": [{"role": "user", "content": "two"}],
                        },
                    ]
                }
            }

        def filter_snapshot_by_item_selections(self, snapshot, item_selections=None):
            captured["item_selections"] = item_selections or {}
            selected_ids = set((item_selections or {}).get("conversations") or [])
            return {
                **snapshot,
                "sections": {
                    "conversations": [
                        record
                        for record in snapshot["sections"]["conversations"]
                        if record["sync_id"] in selected_ids
                    ]
                },
            }

    monkeypatch.setattr(routes, "_sync_service", lambda: FakeSyncService())
    monkeypatch.setattr(
        routes.RemoteFloatClient,
        "get_sync_overview",
        lambda self: {
            "workspaces": {
                "active_workspace_id": "root",
                "selected_workspace_ids": ["root"],
                "profiles": [
                    {
                        "id": "root",
                        "name": "Main workspace",
                        "slug": "main",
                        "namespace": "",
                        "root_path": "data/files/workspace",
                        "kind": "root",
                        "is_root": True,
                    }
                ],
            }
        },
    )

    def _fake_ingest(self, snapshot, **_kwargs):
        captured["snapshot"] = snapshot
        return {"status": "applied", "effective_namespace": None}

    monkeypatch.setattr(routes.RemoteFloatClient, "ingest_snapshot", _fake_ingest)
    monkeypatch.setattr(routes.RemoteFloatClient, "get_pairing_state", lambda self: {})

    res = client.post(
        "/sync/apply",
        json={
            "remote_url": "http://example.test:5000",
            "direction": "push",
            "sections": ["conversations"],
            "item_selections": {"conversations": ["conv-2"]},
        },
    )

    assert res.status_code == 200
    assert captured["item_selections"] == {"conversations": ["conv-2"]}
    assert [
        record["sync_id"]
        for record in captured["snapshot"]["sections"]["conversations"]
    ] == ["conv-2"]


def test_sync_apply_pull_refreshes_search_mirrors(client, monkeypatch):
    from app import routes

    class FakeSyncService:
        def normalize_sections(self, sections):
            return list(sections or [])

        def normalize_item_selections(self, sections, selections):
            return selections or {}

        def current_instance_identity(self, source_namespace=None):
            return {
                "display_name": "Local",
                "hostname": "local-host",
                "source_namespace": source_namespace or "",
            }

        def build_snapshot(self, sections, workspace_ids=None):
            return {"sections": {section: [] for section in sections or []}}

        def filter_snapshot_by_item_selections(self, snapshot, item_selections=None):
            return snapshot

        def merge_snapshot(self, snapshot, **_kwargs):
            return {
                "applied_at": "2026-03-25T00:00:00+00:00",
                "effective_namespace": None,
                "sections": {
                    "knowledge": {"applied": 2, "skipped": 0},
                    "attachments": {"applied": 1, "skipped": 0},
                    "calendar": {"applied": 1, "skipped": 0},
                },
                "notes": [
                    "Knowledge rows were synced into the canonical SQLite store.",
                    "Attachment files and captions were synced.",
                    "Calendar files were synced.",
                ],
            }

    async def fake_refresh(result):
        result["post_refresh"] = {
            "knowledge": {"scanned": 2, "reindexed": 2},
            "attachments": {"scanned": 1, "reindexed": 1},
            "calendar": {"scanned": 1, "reindexed": 1},
        }
        result["notes"] = [
            "Semantic search refreshed for 2 synced knowledge items (2 scanned).",
            "Attachment search mirrors refreshed for 1 synced image attachments (1 scanned).",
            "Calendar retrieval refreshed for 1 synced events (1 scanned).",
        ]
        return result["post_refresh"]

    monkeypatch.setattr(routes, "_sync_service", lambda: FakeSyncService())
    monkeypatch.setattr(
        routes.RemoteFloatClient,
        "get_sync_overview",
        lambda self: {
            "workspaces": {
                "active_workspace_id": "root",
                "selected_workspace_ids": ["root"],
                "profiles": [
                    {
                        "id": "root",
                        "name": "Main workspace",
                        "slug": "main",
                        "namespace": "",
                        "root_path": "data/files/workspace",
                        "kind": "root",
                        "is_root": True,
                    }
                ],
            }
        },
    )
    monkeypatch.setattr(
        routes.RemoteFloatClient,
        "export_snapshot",
        lambda self, sections, workspace_ids=None: {
            "instance": {
                "display_name": "Pear",
                "hostname": "pear-host",
                "source_namespace": "",
            },
            "sections": {section: [] for section in sections or []},
        },
    )
    monkeypatch.setattr(
        routes.RemoteFloatClient,
        "get_pairing_state",
        lambda self: {"id": "peer-1", "remote_url": "http://example.test:5000"},
    )
    monkeypatch.setattr(routes, "_refresh_sync_result_indexes", fake_refresh)
    monkeypatch.setattr(
        routes,
        "_persist_saved_peer_state",
        lambda pair_state, remote_label=None: pair_state,
    )
    monkeypatch.setattr(routes, "sync_record_changes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "_record_sync_action", lambda *_args, **_kwargs: None)

    res = client.post(
        "/sync/apply",
        json={
            "remote_url": "http://example.test:5000",
            "direction": "pull",
            "sections": ["knowledge", "attachments", "calendar"],
        },
    )

    assert res.status_code == 200
    payload = res.json()
    assert payload["result"]["post_refresh"]["knowledge"]["reindexed"] == 2
    assert payload["result"]["post_refresh"]["attachments"]["reindexed"] == 1
    assert payload["result"]["post_refresh"]["calendar"]["reindexed"] == 1
    assert payload["result"]["notes"] == [
        "Semantic search refreshed for 2 synced knowledge items (2 scanned).",
        "Attachment search mirrors refreshed for 1 synced image attachments (1 scanned).",
        "Calendar retrieval refreshed for 1 synced events (1 scanned).",
    ]


def test_sync_plan_rejects_recursive_workspace_selection(client):
    synced_workspace_id = "sync-peer-1-main"
    client.post(
        "/user-settings",
        json={
            "workspace_profiles": [
                {
                    "id": synced_workspace_id,
                    "name": "Pear / Main workspace",
                    "slug": "pear-main",
                    "namespace": "pear/main",
                    "root_path": "data/files/workspace/pear/main",
                    "kind": "synced",
                    "imported": True,
                    "source_peer_id": "peer-1",
                    "source_device_name": "Pear",
                    "source_workspace_id": "root",
                    "source_workspace_name": "Main workspace",
                }
            ],
            "active_workspace_id": "root",
            "sync_selected_workspace_ids": [synced_workspace_id],
            "sync_saved_peers": [
                {
                    "id": "peer-1",
                    "label": "Pear",
                    "remote_url": "http://example.test:5000",
                    "scopes": ["sync"],
                    "remote_device_id": "remote-device-1",
                    "remote_device_name": "Pear",
                    "workspace_mode": "merge",
                    "local_workspace_ids": [synced_workspace_id],
                    "remote_workspace_ids": ["root"],
                    "local_target_workspace_id": "root",
                    "remote_target_workspace_id": "root",
                }
            ],
        },
    )

    res = client.post(
        "/sync/plan",
        json={
            "remote_url": "http://example.test:5000",
            "sections": ["conversations"],
            "paired_device": {
                "id": "peer-1",
                "label": "Pear",
                "remote_url": "http://example.test:5000",
                "scopes": ["sync"],
                "remote_device_id": "remote-device-1",
                "remote_device_name": "Pear",
            },
            "local_workspace_ids": [synced_workspace_id],
            "remote_workspace_ids": ["root"],
            "workspace_mode": "merge",
            "local_target_workspace_id": "root",
            "remote_target_workspace_id": "root",
        },
    )

    assert res.status_code == 400
    assert (
        "ignored to avoid syncing a workspace back to its source device"
        in res.json()["detail"]
    )
