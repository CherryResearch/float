import json
import sys
from pathlib import Path

import pytest
import requests
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
        sync_store,
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
    monkeypatch.setattr(sync_store, "SYNC_PATH", tmp_path / "sync_state.json")
    monkeypatch.setattr(
        sync_store, "LEGACY_SYNC_PATH", tmp_path / "legacy_sync_state.json"
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
    assert payload["egress_summary"]["private_network_only"] is True
    assert payload["egress_summary"]["inbound_visibility"]["lan_enabled"] is True
    assert payload["egress_summary"]["outbound_target"]["mode"] == "none"
    assert payload["egress_summary"]["push_review_mode"] == "review_required"
    assert payload["egress_summary"]["auto_sync"]["enabled"] is False
    assert payload["egress_summary"]["auto_sync"]["available"] is False
    assert payload["egress_summary"]["background_owner"]["mode"] == "idle"
    assert (
        "automatic stop-kill safeguards"
        in payload["egress_summary"]["unfinished_notice"].lower()
    )
    assert payload["sync_operations"]["active_operation"] is None
    assert payload["sync_operations"]["last_attempt"] is None
    assert payload["sync_suggestions"][0]["id"] == "pair-a-device"
    assert payload["sync_suggestions"][0]["auto_sync_enabled"] is False


def test_mobile_float_serve_start_uses_current_frontend_port(
    client, tmp_path, monkeypatch
):
    from app import routes

    state_path = tmp_path / ".dev_state.json"
    state_path.write_text(
        json.dumps({"frontend_port": 5173, "backend_port": 8000}),
        encoding="utf-8",
    )
    monkeypatch.setenv("FLOAT_DEV_STATE_PATH", str(state_path))
    monkeypatch.setattr(routes.shutil, "which", lambda name: "tailscale")
    calls = []
    started = {"value": False}

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        args = cmd[1:]
        if args == ["status", "--self", "--json"]:
            return routes.subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "Self": {
                            "DNSName": "studio.tail.ts.net.",
                            "HostName": "studio",
                            "TailscaleIPs": ["100.100.100.10"],
                        }
                    }
                ),
                stderr="",
            )
        if args == ["serve", "status"]:
            stdout = (
                "http://studio.tail.ts.net:64345 -> http://127.0.0.1:5173"
                if started["value"]
                else "No serve config"
            )
            return routes.subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        if args == [
            "serve",
            "--http=64345",
            "--bg",
            "--yes",
            "localhost:5173",
        ]:
            started["value"] = True
            return routes.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"Unexpected tailscale command: {args}")

    monkeypatch.setattr(routes.subprocess, "run", fake_run)

    res = client.post("/sync/mobile-serve/start", json={"serve_port": 64345})

    assert res.status_code == 200
    payload = res.json()
    assert payload["running"] is True
    assert payload["serve_port"] == 64345
    assert payload["frontend_port"] == 5173
    assert payload["url"] == "http://studio.tail.ts.net:64345/"
    assert payload["target"] == "localhost:5173"
    assert [
        "tailscale",
        "serve",
        "--http=64345",
        "--bg",
        "--yes",
        "localhost:5173",
    ] in calls


def test_mobile_float_serve_status_reports_missing_tailscale(client, monkeypatch):
    from app import routes

    monkeypatch.setattr(routes.shutil, "which", lambda name: None)

    res = client.get("/sync/mobile-serve/status")

    assert res.status_code == 200
    payload = res.json()
    assert payload["installed"] is False
    assert payload["running"] is False
    assert "Tailscale" in payload["warning"]


def test_sync_overview_suggests_reviewed_sync_without_auto_sync(client):
    paired = {
        "id": "peer-pear",
        "label": "Pear",
        "remote_url": "http://pear.float:5000",
        "scopes": ["sync"],
        "remote_device_id": "remote-device-1",
        "remote_public_key": "pk-pear",
        "local_workspace_ids": ["root"],
        "remote_workspace_ids": ["root"],
        "workspace_mode": "merge",
    }
    client.post(
        "/user-settings",
        json={
            "sync_visible_on_lan": True,
            "sync_remote_url": paired["remote_url"],
            "sync_saved_peers": [paired],
        },
    )

    payload = client.get("/sync/overview").json()

    assert payload["egress_summary"]["outbound_target"]["mode"] == "saved_peer"
    assert payload["egress_summary"]["auto_sync"]["enabled"] is False
    assert payload["egress_summary"]["auto_sync"]["mode"] == "manual_review_only"
    suggestions = payload["sync_suggestions"]
    ready = next(
        item for item in suggestions if item["id"] == "ready-reviewed-sync-peer-pear"
    )
    assert ready["action"] == "check_then_preview"
    assert ready["manual_review_required"] is True
    assert ready["auto_sync_enabled"] is False
    assert ready["auto_sync_available"] is False
    rows = {row["label"]: row["value"] for row in ready["state_explanation"]["rows"]}
    assert rows["Automatic sync"] == "Off"
    assert rows["Review"] == "Manual approval required"


def test_sync_peer_status_records_operation_lifecycle(client, monkeypatch):
    from app import routes

    monkeypatch.setattr(
        routes,
        "_peer_connectivity_status",
        lambda _remote_url: {
            "reachable": True,
            "instance_base": "http://peer.float:5000",
            "identity": {"public_key": "pk-peer", "display_name": "Peer"},
            "display_name": "Peer",
        },
    )

    res = client.post(
        "/sync/peer/status",
        json={
            "remote_url": "http://peer.float:5000",
            "operation_id": "check-1",
            "operation_owner": "QA",
        },
    )

    assert res.status_code == 200
    overview = client.get("/sync/overview").json()
    last_attempt = overview["sync_operations"]["last_attempt"]
    assert last_attempt["id"] == "check-1"
    assert last_attempt["kind"] == "check"
    assert last_attempt["status"] == "completed"
    assert last_attempt["owner"] == "QA"
    assert last_attempt["remote_url"] == "http://peer.float:5000"
    assert last_attempt["cancel_requested"] is False
    assert "Stop records cancel intent" in last_attempt["stop_effect"]
    rows = {
        row["label"]: row["value"] for row in last_attempt["state_explanation"]["rows"]
    }
    assert rows["Source"] == "sync operation ledger"
    assert rows["Operation"] == "check / check-1"
    assert rows["Owner"] == "QA"
    assert rows["Remote"] == "http://peer.float:5000"


def test_sync_operation_records_failure_and_cancel_intent(client, monkeypatch):
    from app import routes

    def _raise_timeout(_remote_url):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(routes, "_peer_connectivity_status", _raise_timeout)

    failed = client.post(
        "/sync/peer/status",
        json={
            "remote_url": "http://peer.float:5000",
            "operation_id": "check-failed",
            "operation_owner": "QA",
        },
    )
    assert failed.status_code == 502
    overview = client.get("/sync/overview").json()
    assert overview["sync_operations"]["last_attempt"]["id"] == "check-failed"
    assert overview["sync_operations"]["last_attempt"]["status"] == "failed"
    assert "timed out" in overview["sync_operations"]["last_attempt"]["error"]
    failed_rows = {
        row["label"]: row["value"]
        for row in overview["sync_operations"]["last_attempt"]["state_explanation"][
            "rows"
        ]
    }
    assert failed_rows["Evidence"] == "timed out"

    cancelled = client.post("/sync/operations/preview-123/cancel")
    assert cancelled.status_code == 200
    payload = cancelled.json()
    assert payload["operation"]["id"] == "preview-123"
    assert payload["operation"]["cancel_requested"] is True
    cancel_rows = {
        row["label"]: row["value"]
        for row in payload["operation"]["state_explanation"]["rows"]
    }
    assert cancel_rows["Operation"] == "sync / preview-123"
    assert "Stop records cancel intent" in cancel_rows["Next"]

    overview = client.get("/sync/overview").json()
    assert overview["sync_operations"]["active_operation"]["id"] == "preview-123"
    assert (
        overview["sync_operations"]["active_operation"]["status"] == "cancel_requested"
    )


def test_sync_overview_advertises_https_when_request_is_https(client, monkeypatch):
    from app.utils import device_visibility

    monkeypatch.setattr(device_visibility, "_detect_lan_ips", lambda: ["192.168.0.44"])

    client.post("/user-settings", json={"sync_visible_on_lan": True})
    overview = client.get(
        "/sync/overview",
        headers={"host": "float.example.test", "x-forwarded-proto": "https"},
    )
    assert overview.status_code == 200
    urls = overview.json()["device_access"]["advertised_urls"]
    assert urls["local"] == "https://127.0.0.1"
    assert urls["lan"] == "https://192.168.0.44"


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
    assert overview["egress_summary"]["outbound_target"]["mode"] == "saved_peer"
    assert overview["egress_summary"]["outbound_target"]["peer_label"] == "studio"
    assert overview["egress_summary"]["saved_peer_count"] == 1


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


def test_device_token_requires_device_proof_and_caps_scopes(client):
    from app.utils import device_registry, user_settings

    register_res = client.post(
        "/devices/register",
        json={
            "public_key": "pk-laptop",
            "name": "laptop",
            "capabilities": {"requested_scopes": ["sync"]},
        },
    )
    assert register_res.status_code == 200
    device_id = register_res.json()["device"]["id"]

    missing_proof = client.post(
        "/devices/token",
        json={"device_id": device_id, "scopes": ["sync"]},
    )
    assert missing_proof.status_code == 403

    wrong_proof = client.post(
        "/devices/token",
        json={
            "device_id": device_id,
            "scopes": ["sync"],
            "public_key": "wrong-key",
        },
    )
    assert wrong_proof.status_code == 403

    token_res = client.post(
        "/devices/token",
        json={
            "device_id": device_id,
            "scopes": ["sync", "files"],
            "public_key": "pk-laptop",
        },
    )
    assert token_res.status_code == 200
    token = token_res.json()["token"]
    claims = device_registry.decode_device_token(token)
    assert claims["scopes"] == ["sync"]
    assert user_settings.load_settings().get("device_jwt_secret")

    refreshed = client.post(
        "/devices/token",
        headers={"Authorization": f"Bearer {token}"},
        json={"device_id": device_id, "scopes": ["sync"]},
    )
    assert refreshed.status_code == 200


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
        json={
            "device_id": device_id,
            "scopes": ["sync"],
            "public_key": "pk-laptop",
        },
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


def test_sync_overview_buckets_browser_shaped_records_as_legacy_even_with_scopes(
    client,
):
    client.post(
        "/devices/register",
        json={
            "public_key": "legacy-pk",
            "name": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "capabilities": {"requested_scopes": ["sync", "stream"], "sync": True},
        },
    )
    client.post(
        "/devices/register",
        json={
            "public_key": "real-pk",
            "name": "Pear Laptop",
            "capabilities": {"requested_scopes": ["sync"]},
        },
    )

    overview = client.get("/sync/overview").json()
    assert len(overview["inbound_devices"]) == 1
    assert overview["inbound_devices"][0]["name"] == "Pear Laptop"
    assert len(overview["legacy_inbound_devices"]) == 1
    assert overview["legacy_inbound_devices"][0]["name"].startswith("Mozilla/5.0")


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


def test_sync_peer_status_updates_moved_url_when_identity_matches(client, monkeypatch):
    from app import routes

    old_pair = {
        "id": "peer-1",
        "label": "Pear",
        "remote_url": "http://pear.local:59185",
        "scopes": ["sync"],
        "remote_device_id": "remote-device-1",
        "public_key": "pk-local",
        "remote_public_key": "pk-pear",
        "remote_device_name": "Pear",
    }
    client.post(
        "/user-settings",
        json={
            "sync_remote_url": old_pair["remote_url"],
            "sync_saved_peers": [old_pair],
        },
    )

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "current_device": {
                    "display_name": "Pear",
                    "hostname": "pear-host",
                    "public_key": "pk-pear",
                    "source_namespace": "Pear",
                },
                "device_access": {
                    "visibility": {"lan_enabled": True},
                    "advertised_urls": {"lan": "http://pear.local:61234"},
                },
                "workspaces": {
                    "active_workspace_id": "root",
                    "selected_workspace_ids": ["root"],
                    "profiles": [],
                },
            }

    monkeypatch.setattr(
        routes.http_session, "get", lambda *args, **kwargs: FakeResponse()
    )

    res = client.post(
        "/sync/peer/status",
        json={
            "remote_url": "http://pear.local:61234",
            "paired_device": old_pair,
            "update_saved_peer": True,
        },
    )

    assert res.status_code == 200
    payload = res.json()
    assert payload["identity_verified"] is True
    assert payload["identity_state"] == "verified"
    assert payload["paired_device"]["remote_url"] == "http://pear.local:61234"

    overview = client.get("/sync/overview").json()
    assert overview["sync_defaults"]["remote_url"] == "http://pear.local:61234"
    assert (
        overview["sync_defaults"]["saved_peers"][0]["remote_url"]
        == "http://pear.local:61234"
    )


def test_sync_peer_status_anchors_legacy_pair_when_remote_knows_local_device(
    client, monkeypatch
):
    from app import routes

    old_pair = {
        "id": "peer-1",
        "label": "Pear",
        "remote_url": "http://pear.local:59185",
        "scopes": ["sync"],
        "remote_device_id": "remote-device-1",
        "public_key": "pk-local",
        "remote_public_key": "",
        "remote_device_name": "Pear",
    }
    client.post(
        "/user-settings",
        json={
            "sync_remote_url": old_pair["remote_url"],
            "sync_saved_peers": [old_pair],
        },
    )

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "current_device": {
                    "display_name": "Pear",
                    "hostname": "pear-host",
                    "public_key": "pk-pear",
                    "source_namespace": "Pear",
                },
                "inbound_devices": [
                    {
                        "id": "remote-device-1",
                        "name": "Cherry_dev",
                        "public_key": "pk-local",
                        "capabilities": {"sync": True},
                    }
                ],
                "workspaces": {
                    "active_workspace_id": "root",
                    "selected_workspace_ids": ["root"],
                    "profiles": [],
                },
            }

    monkeypatch.setattr(
        routes.http_session, "get", lambda *args, **kwargs: FakeResponse()
    )

    res = client.post(
        "/sync/peer/status",
        json={
            "remote_url": "http://pear.local:61234",
            "paired_device": old_pair,
            "update_saved_peer": True,
        },
    )

    assert res.status_code == 200
    payload = res.json()
    assert payload["identity_verified"] is True
    assert payload["identity_state"] == "verified"
    assert payload["identity_anchor_source"] == "remote_registered_device"
    assert payload["paired_device"]["remote_url"] == "http://pear.local:61234"
    assert payload["paired_device"]["remote_public_key"] == "pk-pear"

    overview = client.get("/sync/overview").json()
    peer = overview["sync_defaults"]["saved_peers"][0]
    assert peer["remote_url"] == "http://pear.local:61234"
    assert peer["remote_public_key"] == "pk-pear"


def test_sync_peer_status_rejects_legacy_pair_when_remote_does_not_know_local_device(
    client, monkeypatch
):
    from app import routes

    old_pair = {
        "id": "peer-1",
        "label": "Pear",
        "remote_url": "http://pear.local:59185",
        "scopes": ["sync"],
        "remote_device_id": "remote-device-1",
        "public_key": "pk-local",
        "remote_public_key": "",
        "remote_device_name": "Pear",
    }
    client.post("/user-settings", json={"sync_saved_peers": [old_pair]})

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "current_device": {
                    "display_name": "Pear",
                    "hostname": "pear-host",
                    "public_key": "pk-pear",
                    "source_namespace": "Pear",
                },
                "inbound_devices": [
                    {
                        "id": "remote-device-1",
                        "name": "Other",
                        "public_key": "wrong-key",
                        "capabilities": {"sync": True},
                    }
                ],
                "workspaces": {
                    "active_workspace_id": "root",
                    "selected_workspace_ids": ["root"],
                    "profiles": [],
                },
            }

    monkeypatch.setattr(
        routes.http_session, "get", lambda *args, **kwargs: FakeResponse()
    )

    res = client.post(
        "/sync/peer/status",
        json={
            "remote_url": "http://pear.local:61234",
            "paired_device": old_pair,
            "update_saved_peer": True,
        },
    )

    assert res.status_code == 409
    assert "stable remote identity" in res.json()["detail"]


def test_sync_peer_status_rejects_legacy_pair_when_remote_label_differs(
    client, monkeypatch
):
    from app import routes

    old_pair = {
        "id": "peer-1",
        "label": "Pear_dev",
        "remote_url": "http://pear.local:59185",
        "scopes": ["sync"],
        "remote_device_id": "remote-device-1",
        "public_key": "pk-local",
        "remote_public_key": "",
        "remote_device_name": "Pear",
    }
    client.post("/user-settings", json={"sync_saved_peers": [old_pair]})

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "current_device": {
                    "display_name": "Pear",
                    "hostname": "Pear",
                    "public_key": "pk-pear",
                    "source_namespace": "Pear",
                },
                "inbound_devices": [
                    {
                        "id": "remote-device-1",
                        "name": "Cherry_dev",
                        "public_key": "pk-local",
                        "capabilities": {"sync": True},
                    }
                ],
                "workspaces": {
                    "active_workspace_id": "root",
                    "selected_workspace_ids": ["root"],
                    "profiles": [],
                },
            }

    monkeypatch.setattr(
        routes.http_session, "get", lambda *args, **kwargs: FakeResponse()
    )

    res = client.post(
        "/sync/peer/status",
        json={
            "remote_url": "http://pear.local:61234",
            "paired_device": old_pair,
            "update_saved_peer": True,
        },
    )

    assert res.status_code == 409
    assert "advertised identity label does not match" in res.json()["detail"]


def test_sync_peer_status_rejects_moved_url_when_identity_changes(client, monkeypatch):
    from app import routes

    old_pair = {
        "id": "peer-1",
        "label": "Pear",
        "remote_url": "http://pear.local:59185",
        "scopes": ["sync"],
        "remote_device_id": "remote-device-1",
        "public_key": "pk-local",
        "remote_public_key": "pk-pear",
        "remote_device_name": "Pear",
    }
    client.post("/user-settings", json={"sync_saved_peers": [old_pair]})

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "current_device": {
                    "display_name": "Demo Float",
                    "hostname": "same-host",
                    "public_key": "pk-demo",
                    "source_namespace": "Demo",
                },
                "workspaces": {
                    "active_workspace_id": "root",
                    "selected_workspace_ids": ["root"],
                    "profiles": [],
                },
            }

    monkeypatch.setattr(
        routes.http_session, "get", lambda *args, **kwargs: FakeResponse()
    )

    res = client.post(
        "/sync/peer/status",
        json={
            "remote_url": "http://pear.local:61234",
            "paired_device": old_pair,
            "update_saved_peer": True,
        },
    )

    assert res.status_code == 409
    assert "does not match this saved pair" in res.json()["detail"]
    overview = client.get("/sync/overview").json()
    assert (
        overview["sync_defaults"]["saved_peers"][0]["remote_url"]
        == "http://pear.local:59185"
    )


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
            "operation_id": "push-1",
            "operation_owner": "QA",
        },
    )

    assert res.status_code == 200
    assert captured["item_selections"] == {"conversations": ["conv-2"]}
    assert [
        record["sync_id"]
        for record in captured["snapshot"]["sections"]["conversations"]
    ] == ["conv-2"]
    last_attempt = client.get("/sync/overview").json()["sync_operations"][
        "last_attempt"
    ]
    assert last_attempt["id"] == "push-1"
    assert last_attempt["kind"] == "push"
    assert last_attempt["status"] == "completed"
    assert last_attempt["sections"] == ["conversations"]


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


def test_sync_apply_pull_supersedes_prior_pending_review_push(client, monkeypatch):
    from app import routes
    from app.utils import sync_store

    sync_store.start_operation(
        kind="push",
        operation_id="push-old",
        remote_url="http://example.test:5000",
        remote_label="Pear",
        sections=["conversations"],
        workspace_mode="merge",
    )
    sync_store.finish_operation(
        "push-old",
        status="completed",
        result={
            "direction": "push",
            "remote": "http://example.test:5000",
            "workspace_mode": "merge",
            "remote_status": "pending_review",
        },
    )

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
                "sections": {"conversations": {"applied": 0, "skipped": 0}},
                "notes": [],
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

    async def fake_refresh(_result):
        return {}

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
            "sections": ["conversations"],
            "operation_id": "pull-new",
        },
    )

    assert res.status_code == 200
    payload = res.json()
    assert payload["superseded_pending_pushes"] == {
        "count": 1,
        "operation_ids": ["push-old"],
    }
    overview = client.get("/sync/overview").json()["sync_operations"]
    assert overview["last_attempt"]["id"] == "pull-new"
    recent_by_id = {item["id"]: item for item in overview["recent"]}
    assert recent_by_id["push-old"]["status"] == "cancelled"
    assert recent_by_id["push-old"]["result"]["remote_status"] == "superseded_by_pull"
    assert (
        recent_by_id["push-old"]["result"]["superseded_by_operation_id"] == "pull-new"
    )


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


def test_sync_plan_rejects_saved_peer_identity_mismatch(client, monkeypatch):
    from app import routes

    paired = {
        "id": "peer-1",
        "label": "Pear",
        "remote_url": "http://pear.local:59185",
        "scopes": ["sync"],
        "remote_device_id": "remote-device-1",
        "public_key": "pk-local",
        "remote_public_key": "pk-pear",
        "remote_device_name": "Pear",
        "local_workspace_ids": ["root"],
        "remote_workspace_ids": ["root"],
        "workspace_mode": "merge",
        "local_target_workspace_id": "root",
        "remote_target_workspace_id": "root",
    }

    monkeypatch.setattr(
        routes.RemoteFloatClient,
        "get_sync_overview",
        lambda self: {
            "current_device": {
                "display_name": "Release demo",
                "hostname": "same-host",
                "public_key": "pk-release-demo",
                "source_namespace": "ReleaseDemo",
            },
            "workspaces": {
                "active_workspace_id": "root",
                "selected_workspace_ids": ["root"],
                "profiles": [],
            },
        },
    )

    res = client.post(
        "/sync/plan",
        json={
            "remote_url": "http://pear.local:61234",
            "sections": ["conversations"],
            "paired_device": paired,
            "local_workspace_ids": ["root"],
            "remote_workspace_ids": ["root"],
            "workspace_mode": "merge",
            "local_target_workspace_id": "root",
            "remote_target_workspace_id": "root",
        },
    )

    assert res.status_code == 409
    assert "does not match this saved pair" in res.json()["detail"]
