import base64
import hashlib
import sys
from pathlib import Path


def _load_modules():
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    import app.services.instance_sync_service as sync_module
    from app.services.instance_sync_service import (
        InstanceSyncService,
        RemoteFloatClient,
        _write_conversation_snapshot,
    )
    from app.utils import (
        blob_store,
        calendar_store,
        conversation_store,
        memory_store,
        theme_store,
        user_settings,
    )
    from app.utils.graph_store import GraphStore
    from app.utils.knowledge_store import KnowledgeStore

    return {
        "InstanceSyncService": InstanceSyncService,
        "RemoteFloatClient": RemoteFloatClient,
        "_write_conversation_snapshot": _write_conversation_snapshot,
        "calendar_store": calendar_store,
        "conversation_store": conversation_store,
        "memory_store": memory_store,
        "theme_store": theme_store,
        "user_settings": user_settings,
        "blob_store": blob_store,
        "GraphStore": GraphStore,
        "KnowledgeStore": KnowledgeStore,
        "sync_module": sync_module,
    }


def _configure_paths(tmp_path, monkeypatch):
    modules = _load_modules()
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    calendar_dir = tmp_path / "calendar"
    calendar_dir.mkdir(parents=True, exist_ok=True)
    blobs_dir = tmp_path / "blobs"
    blobs_dir.mkdir(parents=True, exist_ok=True)
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir(parents=True, exist_ok=True)
    files_dir = tmp_path / "data" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    for dirname in ("uploads", "captured", "screenshots", "downloaded", "workspace"):
        (files_dir / dirname).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(modules["conversation_store"], "CONV_DIR", conv_dir)
    monkeypatch.setattr(modules["calendar_store"], "EVENTS_DIR", calendar_dir)
    monkeypatch.setattr(
        modules["user_settings"],
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
    )
    monkeypatch.setattr(modules["theme_store"], "THEMES_DIR", themes_dir)
    monkeypatch.setattr(modules["blob_store"], "BLOBS_DIR", blobs_dir)
    monkeypatch.setattr(modules["sync_module"], "BLOBS_DIR", blobs_dir)
    monkeypatch.setattr(
        modules["blob_store"],
        "_resolve_data_files_root",
        lambda: files_dir,
    )
    monkeypatch.setenv(
        "FLOAT_MEMORY_FILE", str(tmp_path / "databases" / "memory.sqlite3")
    )
    return modules


def test_merge_snapshot_renames_conversation_and_updates_portable_state(
    tmp_path, monkeypatch
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    write_conversation = modules["_write_conversation_snapshot"]
    conversation_store = modules["conversation_store"]
    memory_store = modules["memory_store"]
    user_settings = modules["user_settings"]
    calendar_store = modules["calendar_store"]

    write_conversation(
        name="drafts/alpha",
        messages=[{"role": "user", "content": "older copy"}],
        metadata={
            "id": "conv-1",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
            "display_name": "Alpha",
            "manual_title": True,
        },
    )
    memory_store.save({"alias": {"value": "local", "updated_at": 10.0}})
    user_settings.save_settings(
        {
            "theme": "light",
            "tool_display_mode": "console",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    )

    snapshot = {
        "sections": {
            "conversations": [
                {
                    "sync_id": "conv-1",
                    "name": "projects/alpha",
                    "metadata": {
                        "id": "conv-1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-02-01T00:00:00+00:00",
                        "display_name": "Alpha Project",
                        "manual_title": True,
                    },
                    "messages": [{"role": "user", "content": "newer copy"}],
                }
            ],
            "memories": [
                {
                    "key": "alias",
                    "payload": {
                        "value": "remote",
                        "updated_at": 100.0,
                    },
                }
            ],
            "settings": {
                "sync_id": "settings",
                "updated_at": "2026-04-01T00:00:00+00:00",
                "data": {
                    "theme": "dark",
                    "tool_display_mode": "inline",
                },
            },
            "calendar": [
                {
                    "event_id": "evt-1",
                    "payload": {
                        "title": "Review",
                        "updated_at": "2026-02-01T00:00:00+00:00",
                    },
                }
            ],
        }
    }

    result = service.merge_snapshot(snapshot)

    assert result["sections"]["conversations"]["applied"] == 1
    assert result["sections"]["conversations"]["renamed"] == 1
    assert (
        conversation_store.load_conversation("projects/alpha")[0]["content"]
        == "newer copy"
    )
    assert conversation_store.load_conversation("drafts/alpha") == []
    assert memory_store.load()["alias"]["value"] == "remote"
    assert user_settings.load_settings()["theme"] == "dark"
    assert user_settings.load_settings()["tool_display_mode"] == "inline"
    assert calendar_store.load_event("evt-1")["title"] == "Review"


def test_settings_sync_includes_and_merges_custom_visual_themes(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    theme_store = modules["theme_store"]
    user_settings = modules["user_settings"]
    slots = {
        "c1Light": "#d6f5dd",
        "c1Med": "#3c8f5a",
        "c1Dark": "#173927",
        "c2Light": "#f4efc7",
        "c2Med": "#c6a93e",
        "c2Dark": "#5e4b12",
        "veryLight": "#fcfff8",
        "veryDark": "#08110a",
    }
    user_settings.save_settings(
        {
            "theme": "dark",
            "visual_theme": "forest-glass",
            "updated_at": "2026-04-01T00:00:00+00:00",
        }
    )
    theme_store.save_theme(
        theme_id="forest-glass",
        label="Forest Glass",
        slots=slots,
    )

    manifest = service.build_manifest(["settings"])
    snapshot = service.build_snapshot(["settings"])

    settings_item = manifest["sections"]["settings"]["items"][0]
    assert settings_item["sync_id"] == "settings"
    assert settings_item["themes"] == 1
    settings_snapshot = snapshot["sections"]["settings"]
    assert settings_snapshot["data"]["visual_theme"] == "forest-glass"
    assert settings_snapshot["themes"][0]["id"] == "forest-glass"

    theme_store.delete_theme("forest-glass")
    user_settings.save_settings(
        {
            "theme": "light",
            "visual_theme": "spring",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    )

    result = service.merge_snapshot(snapshot)

    assert result["sections"]["settings"]["applied"] == 2
    assert user_settings.load_settings()["visual_theme"] == "forest-glass"
    assert theme_store.list_themes() == [
        {
            "id": "forest-glass",
            "label": "Forest Glass",
            "slots": slots,
        }
    ]


def test_settings_sync_selects_imported_custom_theme_when_settings_are_newer(
    tmp_path, monkeypatch
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    theme_store = modules["theme_store"]
    user_settings = modules["user_settings"]
    slots = {
        "c1Light": "#d6f5dd",
        "c1Med": "#3c8f5a",
        "c1Dark": "#173927",
        "c2Light": "#f4efc7",
        "c2Med": "#c6a93e",
        "c2Dark": "#5e4b12",
        "veryLight": "#fcfff8",
        "veryDark": "#08110a",
    }
    user_settings.save_settings(
        {
            "theme": "light",
            "visual_theme": "spring",
            "updated_at": "2027-01-01T00:00:00+00:00",
        }
    )
    snapshot = {
        "sections": {
            "settings": {
                "sync_id": "settings",
                "updated_at": "2026-04-01T00:00:00+00:00",
                "data": {
                    "theme": "dark",
                    "visual_theme": "forest-glass",
                },
                "themes": [
                    {
                        "sync_id": "theme:forest-glass",
                        "id": "forest-glass",
                        "label": "Forest Glass",
                        "slots": slots,
                        "updated_at": "2026-04-01T00:00:00+00:00",
                    }
                ],
            }
        }
    }

    result = service.merge_snapshot(snapshot)

    assert result["sections"]["settings"]["applied"] == 2
    assert result["sections"]["settings"]["skipped"] == 1
    assert user_settings.load_settings()["theme"] == "light"
    assert user_settings.load_settings()["visual_theme"] == "forest-glass"
    assert theme_store.list_themes()[0]["id"] == "forest-glass"


def test_remote_client_repairs_saved_pair_when_device_proof_is_required():
    modules = _load_modules()
    sync_module = modules["sync_module"]
    RemoteFloatClient = modules["RemoteFloatClient"]

    class FakeResponse:
        def __init__(self, payload=None, *, status_code=200, text=""):
            self._payload = payload or {}
            self.status_code = status_code
            self.text = text

        def raise_for_status(self):
            if self.status_code < 400:
                return None
            exc = sync_module.requests.HTTPError(f"{self.status_code} error")
            exc.response = self
            raise exc

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.calls = []

        def request(self, method, url, json=None, headers=None, timeout=None):
            self.calls.append(
                {
                    "method": method,
                    "path": url.rsplit("/api/", 1)[-1],
                    "json": json or {},
                    "headers": headers or {},
                    "timeout": timeout,
                }
            )
            path = self.calls[-1]["path"]
            token_calls = [
                call for call in self.calls if call["path"] == "devices/token"
            ]
            if path == "devices/token" and len(token_calls) == 1:
                return FakeResponse(
                    status_code=403,
                    text='{"detail":"Device proof required for token issuance"}',
                )
            if path == "devices/register":
                return FakeResponse({"device": {"id": "fresh-device"}})
            if path == "devices/token":
                return FakeResponse({"token": "fresh-token"})
            if path == "sync/manifest":
                return FakeResponse({"sections": {}})
            raise AssertionError(f"unexpected request path: {path}")

    session = FakeSession()
    client = RemoteFloatClient(
        "http://pear.local:54089",
        session=session,
        paired_device={
            "id": "peer-1",
            "remote_device_id": "stale-device",
            "public_key": "",
            "scopes": ["sync", "files"],
        },
        device_name="Cherry",
    )

    assert client.get_manifest(["settings"]) == {"sections": {}}

    paths = [call["path"] for call in session.calls]
    assert paths == [
        "devices/token",
        "devices/register",
        "devices/token",
        "sync/manifest",
    ]
    assert session.calls[0]["json"]["public_key"] == ""
    assert session.calls[1]["json"]["public_key"]
    assert session.calls[2]["json"]["device_id"] == "fresh-device"
    assert (
        session.calls[2]["json"]["public_key"] == session.calls[1]["json"]["public_key"]
    )
    assert session.calls[3]["headers"]["Authorization"] == "Bearer fresh-token"
    assert client.get_pairing_state()["remote_device_id"] == "fresh-device"


def test_remote_client_enriches_legacy_settings_snapshot_with_themes():
    modules = _load_modules()
    sync_module = modules["sync_module"]
    RemoteFloatClient = modules["RemoteFloatClient"]
    slots = {
        "c1Light": "#a8f5ab",
        "c1Med": "#4aed1d",
        "c1Dark": "#05420f",
        "c2Light": "#f8f7f7",
        "c2Med": "#f7bff3",
        "c2Dark": "#d24ba1",
        "veryLight": "#e8f5f7",
        "veryDark": "#1e3038",
    }

    class FakeResponse:
        def __init__(self, payload=None, *, status_code=200, text=""):
            self._payload = payload or {}
            self.status_code = status_code
            self.text = text

        def raise_for_status(self):
            if self.status_code < 400:
                return None
            exc = sync_module.requests.HTTPError(f"{self.status_code} error")
            exc.response = self
            raise exc

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.calls = []

        def request(self, method, url, json=None, headers=None, timeout=None):
            path = url.rsplit("/api/", 1)[-1]
            self.calls.append({"method": method, "path": path, "json": json or {}})
            if path == "devices/token":
                return FakeResponse({"token": "sync-token"})
            if path == "devices/remote-device":
                return FakeResponse({"device": {"id": "remote-device"}})
            if path == "sync/export":
                return FakeResponse(
                    {
                        "sections": {
                            "settings": {
                                "sync_id": "settings",
                                "updated_at": "2026-04-15T04:43:39+00:00",
                                "data": {"theme": "light"},
                            }
                        }
                    }
                )
            if path == "user-settings":
                return FakeResponse({"visual_theme": "blossom", "theme": "light"})
            if path == "themes":
                return FakeResponse(
                    {
                        "themes": [
                            {
                                "id": "blossom",
                                "label": "Blossom",
                                "slots": slots,
                            }
                        ]
                    }
                )
            raise AssertionError(f"unexpected request path: {path}")

    client = RemoteFloatClient(
        "http://pear.local:54089",
        session=FakeSession(),
        paired_device={
            "remote_device_id": "remote-device",
            "public_key": "pk-local",
            "scopes": ["sync"],
        },
        device_name="Cherry",
    )

    snapshot = client.export_snapshot(["settings"])

    settings = snapshot["sections"]["settings"]
    assert settings["data"]["visual_theme"] == "blossom"
    assert settings["themes"] == [
        {
            "sync_id": "theme:blossom",
            "id": "blossom",
            "label": "Blossom",
            "slots": slots,
            "updated_at": "2026-04-15T04:43:39+00:00",
        }
    ]


def test_merge_snapshot_links_synced_state_to_source_namespace(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    conversation_store = modules["conversation_store"]
    memory_store = modules["memory_store"]
    calendar_store = modules["calendar_store"]
    KnowledgeStore = modules["KnowledgeStore"]
    GraphStore = modules["GraphStore"]
    blob_store = modules["blob_store"]
    sync_module = modules["sync_module"]

    attachment_bytes = b"linked attachment"
    content_hash = hashlib.sha256(attachment_bytes).hexdigest()

    snapshot = {
        "instance": {
            "hostname": "laptop-host",
            "source_namespace": "laptop",
        },
        "sections": {
            "conversations": [
                {
                    "sync_id": "conv-1",
                    "name": "projects/alpha",
                    "metadata": {
                        "id": "conv-1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-02-01T00:00:00+00:00",
                        "display_name": "Alpha Project",
                    },
                    "messages": [{"role": "user", "content": "linked copy"}],
                }
            ],
            "memories": [
                {
                    "key": "alias",
                    "payload": {
                        "value": "remote",
                        "updated_at": 100.0,
                    },
                }
            ],
            "knowledge": [
                {
                    "knowledge_id": "doc-1",
                    "source": "notes/doc-1",
                    "kind": "document",
                    "title": "Doc 1",
                    "text": "canonical text",
                    "summary_text": "canonical summary",
                    "metadata": {"source": "notes/doc-1", "kind": "document"},
                    "version": 3,
                    "created_at": 10.0,
                    "updated_at": 200.0,
                    "chunks": [
                        {
                            "chunk_id": "doc-1",
                            "chunk_index": 0,
                            "chunk_count": 1,
                            "source": "notes/doc-1",
                            "root_source": "notes/doc-1",
                            "text": "canonical text",
                            "metadata": {"source": "notes/doc-1"},
                            "embedding_model": None,
                            "created_at": 10.0,
                            "updated_at": 200.0,
                        }
                    ],
                }
            ],
            "graph": {
                "nodes": [
                    {
                        "node_id": "node-1",
                        "node_kind": "entity",
                        "node_type": "person",
                        "canonical_name": "Kai",
                        "summary_text": "A synced node",
                        "attributes": {"role": "owner"},
                        "status": "active",
                        "created_at": 5.0,
                        "updated_at": 200.0,
                    }
                ],
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "claim_type": "relation",
                        "predicate": "owns",
                        "status": "active",
                        "epistemic_status": "observed",
                        "confidence": 0.9,
                        "valid_from": None,
                        "valid_to": None,
                        "occurred_at": None,
                        "source_kind": "conversation",
                        "source_ref": "projects/alpha",
                        "metadata": {"source": "sync"},
                        "created_at": 5.0,
                        "updated_at": 200.0,
                        "roles": [
                            {
                                "role_name": "subject",
                                "ordinal": 0,
                                "node_id": "node-1",
                                "value": None,
                                "metadata": {},
                            }
                        ],
                    }
                ],
            },
            "attachments": [
                {
                    "content_hash": content_hash,
                    "filename": "note.txt",
                    "updated_at": 200.0,
                    "metadata": {
                        "filename": "note.txt",
                        "origin": "upload",
                        "relative_path": f"uploads/{content_hash}/note.txt",
                        "uploaded_at": "2026-02-01T00:00:00+00:00",
                    },
                    "content_b64": base64.b64encode(attachment_bytes).decode("ascii"),
                }
            ],
            "calendar": [
                {
                    "event_id": "evt-1",
                    "payload": {
                        "title": "Review",
                        "updated_at": "2026-02-01T00:00:00+00:00",
                    },
                }
            ],
        },
    }

    result = service.merge_snapshot(
        snapshot,
        link_to_source=True,
        source_namespace="laptop",
        source_label="Laptop",
    )

    assert result["effective_namespace"] == "laptop"
    assert (
        conversation_store.load_conversation("laptop/projects/alpha")[0]["content"]
        == "linked copy"
    )
    assert (
        conversation_store.get_metadata("laptop/projects/alpha")["id"]
        == "laptop__conv-1"
    )
    assert memory_store.load()["laptop__alias"]["value"] == "remote"

    doc = KnowledgeStore().trace("laptop__doc-1")
    assert doc is not None
    assert doc["metadata"]["source"] == "laptop/notes/doc-1"

    graph = GraphStore()
    node = graph.get_node("laptop__node-1")
    claim = graph.get_claim("laptop__claim-1")
    assert node is not None
    assert node["attributes"]["source_sync_namespace"] == "laptop"
    assert claim is not None
    assert claim["roles"][0]["node_id"] == "laptop__node-1"

    attachment_meta = sync_module._load_attachment_meta(content_hash)
    assert attachment_meta["relative_path"] == f"laptop/uploads/{content_hash}/note.txt"
    attachment_target = blob_store.resolve_managed_path(
        attachment_meta["relative_path"]
    )
    assert attachment_target is not None
    assert attachment_target.read_bytes() == attachment_bytes

    assert calendar_store.load_event("laptop__evt-1")["title"] == "Review"


def test_merge_snapshot_writes_attachment_knowledge_and_graph(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    KnowledgeStore = modules["KnowledgeStore"]
    GraphStore = modules["GraphStore"]
    blob_store = modules["blob_store"]

    attachment_bytes = b"synced attachment"
    content_hash = hashlib.sha256(attachment_bytes).hexdigest()

    snapshot = {
        "sections": {
            "attachments": [
                {
                    "content_hash": content_hash,
                    "filename": "note.txt",
                    "updated_at": 200.0,
                    "metadata": {
                        "filename": "note.txt",
                        "origin": "upload",
                        "uploaded_at": "2026-02-01T00:00:00+00:00",
                    },
                    "content_b64": base64.b64encode(attachment_bytes).decode("ascii"),
                }
            ],
            "knowledge": [
                {
                    "knowledge_id": "doc-1",
                    "source": "notes/doc-1",
                    "kind": "document",
                    "title": "Doc 1",
                    "text": "canonical text",
                    "summary_text": "canonical summary",
                    "metadata": {"source": "notes/doc-1", "kind": "document"},
                    "version": 3,
                    "created_at": 10.0,
                    "updated_at": 200.0,
                    "chunks": [
                        {
                            "chunk_id": "doc-1",
                            "chunk_index": 0,
                            "chunk_count": 1,
                            "source": "notes/doc-1",
                            "root_source": "notes/doc-1",
                            "text": "canonical text",
                            "metadata": {"source": "notes/doc-1"},
                            "embedding_model": None,
                            "created_at": 10.0,
                            "updated_at": 200.0,
                        }
                    ],
                }
            ],
            "graph": {
                "nodes": [
                    {
                        "node_id": "node-1",
                        "node_kind": "entity",
                        "node_type": "person",
                        "canonical_name": "Kai",
                        "summary_text": "A synced node",
                        "attributes": {"role": "owner"},
                        "status": "active",
                        "created_at": 5.0,
                        "updated_at": 200.0,
                    }
                ],
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "claim_type": "relation",
                        "predicate": "owns",
                        "status": "active",
                        "epistemic_status": "observed",
                        "confidence": 0.9,
                        "valid_from": None,
                        "valid_to": None,
                        "occurred_at": None,
                        "source_kind": "conversation",
                        "source_ref": "conv-1",
                        "metadata": {"source": "sync"},
                        "created_at": 5.0,
                        "updated_at": 200.0,
                        "roles": [
                            {
                                "role_name": "subject",
                                "ordinal": 0,
                                "node_id": "node-1",
                                "value": None,
                                "metadata": {},
                            }
                        ],
                    }
                ],
            },
        }
    }

    result = service.merge_snapshot(snapshot)

    assert result["sections"]["attachments"]["applied"] == 1
    assert result["sections"]["attachments"]["applied_ids"] == [content_hash]
    attachment_meta = modules["sync_module"]._load_attachment_meta(content_hash)
    assert attachment_meta["source_sync_label"] == "remote"
    assert (
        attachment_meta["relative_path"]
        == f"sync/remote/workspace/attachments/{content_hash}/note.txt"
    )
    target = blob_store.resolve_managed_path(attachment_meta["relative_path"])
    assert target is not None
    assert target.read_bytes() == attachment_bytes

    doc = KnowledgeStore().trace("doc-1")
    assert doc is not None
    assert doc["text"] == "canonical text"
    assert doc["metadata"]["source"] == "notes/doc-1"

    graph = GraphStore()
    node = graph.get_node("node-1")
    claim = graph.get_claim("claim-1")
    assert node is not None
    assert node["canonical_name"] == "Kai"
    assert claim is not None
    assert claim["predicate"] == "owns"
    assert claim["roles"][0]["node_id"] == "node-1"


def test_merge_snapshot_root_pull_stays_visible_in_root_manifest(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    conversation_store = modules["conversation_store"]
    memory_store = modules["memory_store"]
    calendar_store = modules["calendar_store"]
    KnowledgeStore = modules["KnowledgeStore"]
    blob_store = modules["blob_store"]
    sync_module = modules["sync_module"]

    attachment_bytes = b"root synced attachment"
    content_hash = hashlib.sha256(attachment_bytes).hexdigest()

    snapshot = {
        "instance": {
            "display_name": "Pear",
            "source_namespace": "Pear",
        },
        "sections": {
            "conversations": [
                {
                    "sync_id": "conv-pear",
                    "name": "notes/pear-root",
                    "metadata": {
                        "id": "conv-pear",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-03-01T00:00:00+00:00",
                        "display_name": "Pear root conversation",
                    },
                    "messages": [{"role": "user", "content": "pulled copy"}],
                }
            ],
            "memories": [
                {
                    "key": "pear-note",
                    "payload": {
                        "value": "remote",
                        "updated_at": 100.0,
                    },
                }
            ],
            "knowledge": [
                {
                    "knowledge_id": "pear-doc",
                    "source": "notes/pear-doc",
                    "kind": "document",
                    "title": "Pear doc",
                    "text": "synced text",
                    "summary_text": "synced summary",
                    "metadata": {"source": "notes/pear-doc", "kind": "document"},
                    "version": 1,
                    "created_at": 10.0,
                    "updated_at": 200.0,
                    "chunks": [
                        {
                            "chunk_id": "pear-doc",
                            "chunk_index": 0,
                            "chunk_count": 1,
                            "source": "notes/pear-doc",
                            "root_source": "notes/pear-doc",
                            "text": "synced text",
                            "metadata": {"source": "notes/pear-doc"},
                            "embedding_model": None,
                            "created_at": 10.0,
                            "updated_at": 200.0,
                        }
                    ],
                }
            ],
            "attachments": [
                {
                    "content_hash": content_hash,
                    "filename": "note.txt",
                    "updated_at": 200.0,
                    "metadata": {
                        "filename": "note.txt",
                        "origin": "upload",
                        "uploaded_at": "2026-02-01T00:00:00+00:00",
                    },
                    "content_b64": base64.b64encode(attachment_bytes).decode("ascii"),
                }
            ],
            "calendar": [
                {
                    "event_id": "evt-pear",
                    "payload": {
                        "title": "Review",
                        "updated_at": "2026-03-01T00:00:00+00:00",
                    },
                }
            ],
        },
    }

    result = service.merge_snapshot(snapshot)

    assert result["effective_namespace"] is None
    conversation_meta = conversation_store.get_metadata("notes/pear-root")
    assert conversation_store.load_conversation("notes/pear-root")[0]["content"] == (
        "pulled copy"
    )
    assert conversation_meta["source_sync_label"] == "Pear"
    assert not conversation_meta.get("source_sync_namespace")

    memories = memory_store.load()
    assert memories["pear-note"]["value"] == "remote"
    assert memories["pear-note"]["source_sync_label"] == "Pear"
    assert not memories["pear-note"].get("source_sync_namespace")

    doc = KnowledgeStore().trace("pear-doc")
    assert doc is not None
    assert doc["metadata"]["source_sync_label"] == "Pear"
    assert not doc["metadata"].get("source_sync_namespace")

    attachment_meta = sync_module._load_attachment_meta(content_hash)
    assert attachment_meta["source_sync_label"] == "Pear"
    assert not attachment_meta.get("source_sync_namespace")
    attachment_path = blob_store.resolve_managed_path(attachment_meta["relative_path"])
    assert attachment_path is not None
    assert attachment_path.read_bytes() == attachment_bytes
    attachment_meta["source_sync_namespace"] = "Pear"
    sync_module._write_attachment_meta(content_hash, attachment_meta)

    event = calendar_store.load_event("evt-pear")
    assert event["source_sync_label"] == "Pear"
    assert not event.get("source_sync_namespace")

    root_manifest = service.build_manifest(
        ["conversations", "memories", "knowledge", "attachments", "calendar"],
        workspace_ids=["root"],
    )

    conversation_ids = [
        item["sync_id"] for item in root_manifest["sections"]["conversations"]["items"]
    ]
    memory_ids = [
        item["sync_id"] for item in root_manifest["sections"]["memories"]["items"]
    ]
    knowledge_ids = [
        item["sync_id"] for item in root_manifest["sections"]["knowledge"]["items"]
    ]
    attachment_ids = [
        item["sync_id"] for item in root_manifest["sections"]["attachments"]["items"]
    ]
    calendar_ids = [
        item["sync_id"] for item in root_manifest["sections"]["calendar"]["items"]
    ]

    assert "conv-pear" in conversation_ids
    assert "pear-note" in memory_ids
    assert "pear-doc" in knowledge_ids
    assert content_hash in attachment_ids
    assert "evt-pear" in calendar_ids
    assert (
        root_manifest["sections"]["attachments"]["items"][0]["source_sync_namespace"]
        == ""
    )


def test_compare_manifests_counts_local_and_remote_changes(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()

    local_manifest = {
        "sections": {
            "conversations": {
                "items": [
                    {
                        "sync_id": "a",
                        "updated_at": 10.0,
                        "display_name": "Alpha local",
                        "name": "notes/alpha",
                        "message_count": 4,
                    },
                    {
                        "sync_id": "b",
                        "updated_at": 30.0,
                        "display_name": "Beta local",
                        "name": "notes/beta",
                        "message_count": 2,
                    },
                ]
            }
        }
    }
    remote_manifest = {
        "sections": {
            "conversations": {
                "items": [
                    {
                        "sync_id": "a",
                        "original_sync_id": "conv-a",
                        "updated_at": 20.0,
                        "display_name": "Alpha remote",
                        "name": "pear/alpha",
                        "message_count": 5,
                    },
                    {
                        "sync_id": "c",
                        "updated_at": 5.0,
                        "display_name": "Gamma remote",
                        "name": "pear/gamma",
                        "message_count": 1,
                    },
                ]
            }
        }
    }

    comparison = service.compare_manifests(
        local_manifest, remote_manifest, ["conversations"]
    )

    assert len(comparison) == 1
    section = comparison[0]
    assert section["key"] == "conversations"
    assert section["label"] == "Conversations"
    assert section["local_count"] == 2
    assert section["remote_count"] == 2
    assert section["only_local"] == 1
    assert section["only_remote"] == 1
    assert section["local_newer"] == 0
    assert section["remote_newer"] == 1
    assert section["identical"] == 0
    assert section["change_count"] == 3
    assert section["selected_by_default"] is True
    assert section["items"] == section["all_items"]
    assert section["items"][0]["selection_id"] == "conv-a"
    assert section["items"][0]["detail"] == "pear/alpha | 5 messages"
    assert section["items"][1]["status"] == "only_local"
    assert section["items"][1]["detail"] == "notes/beta | 2 messages"
    assert section["items"][2]["status"] == "only_remote"
    assert section["items"][2]["label"] == "Gamma remote"


def test_namespace_manifest_preserves_original_sync_id(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()

    manifest = {
        "sections": {
            "conversations": {
                "items": [
                    {
                        "sync_id": "conv-1",
                        "name": "notes/demo",
                        "display_name": "Demo",
                        "updated_at": 10.0,
                    }
                ]
            }
        }
    }

    namespaced = service.namespace_manifest(manifest, namespace="Pear")
    item = namespaced["sections"]["conversations"]["items"][0]

    assert item["sync_id"] == "Pear__conv-1"
    assert item["original_sync_id"] == "conv-1"
    assert item["name"] == "Pear/notes/demo"


def test_filter_snapshot_by_item_selections_keeps_selected_records(
    tmp_path, monkeypatch
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()

    snapshot = {
        "sections": {
            "conversations": [
                {"sync_id": "conv-1", "name": "notes/one"},
                {"sync_id": "conv-2", "name": "notes/two"},
            ],
            "graph": {
                "nodes": [
                    {"node_id": "node-1", "canonical_name": "Pear"},
                    {"node_id": "node-2", "canonical_name": "Plum"},
                ],
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "predicate": "owns",
                        "roles": [{"node_id": "node-1"}],
                    },
                    {
                        "claim_id": "claim-2",
                        "predicate": "uses",
                        "roles": [{"node_id": "node-2"}],
                    },
                ],
            },
            "settings": {
                "sync_id": "settings",
                "updated_at": "2026-03-25T00:00:00+00:00",
                "data": {"theme": "dark"},
            },
        }
    }

    filtered = service.filter_snapshot_by_item_selections(
        snapshot,
        {
            "conversations": ["conv-2", "conv-deleted"],
            "graph": ["claim:claim-1"],
            "settings": ["settings"],
        },
    )

    assert [record["sync_id"] for record in filtered["sections"]["conversations"]] == [
        "conv-2"
    ]
    assert [claim["claim_id"] for claim in filtered["sections"]["graph"]["claims"]] == [
        "claim-1"
    ]
    assert [node["node_id"] for node in filtered["sections"]["graph"]["nodes"]] == [
        "node-1"
    ]
    assert filtered["sections"]["settings"]["sync_id"] == "settings"
    assert filtered["deletions"]["conversations"] == ["conv-deleted"]


def test_merge_snapshot_applies_selected_deletions(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    write_conversation = modules["_write_conversation_snapshot"]
    conversation_store = modules["conversation_store"]
    memory_store = modules["memory_store"]
    calendar_store = modules["calendar_store"]
    knowledge_store = modules["KnowledgeStore"]()

    write_conversation(
        name="notes/remove-me",
        messages=[{"role": "user", "content": "remove"}],
        metadata={"id": "conv-remove", "updated_at": "2026-03-25T00:00:00+00:00"},
    )
    memory_store.save({"memory-remove": {"value": "remove", "updated_at": 1.0}})
    knowledge_store.upsert_document(
        source="doc/remove",
        text="remove",
        metadata={"updated_at": 1.0},
        chunk_texts=["remove"],
        knowledge_id="knowledge-remove",
    )
    calendar_store.save_event(
        "event-remove",
        {"title": "Remove", "updated_at": "2026-03-25T00:00:00+00:00"},
    )

    result = service.merge_snapshot(
        {
            "sections": {
                "conversations": [],
                "memories": [],
                "knowledge": [],
                "calendar": [],
            },
            "deletions": {
                "conversations": ["conv-remove"],
                "memories": ["memory-remove"],
                "knowledge": ["knowledge-remove"],
                "calendar": ["event-remove"],
            },
        }
    )

    assert result["sections"]["conversations"]["deleted"] == 1
    assert result["sections"]["memories"]["deleted"] == 1
    assert result["sections"]["knowledge"]["deleted"] == 1
    assert result["sections"]["calendar"]["deleted"] == 1
    assert "notes/remove-me" not in {
        item["name"] for item in conversation_store.list_conversations()
    }
    assert "memory-remove" not in memory_store.load()
    assert knowledge_store.get_item("knowledge-remove") is None
    assert "event-remove" not in calendar_store.list_events()


def test_build_manifest_filters_by_workspace_selection(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    write_conversation = modules["_write_conversation_snapshot"]
    user_settings = modules["user_settings"]

    user_settings.save_settings(
        {
            "workspace_profiles": [
                {
                    "id": "work",
                    "name": "Work",
                    "slug": "work",
                    "namespace": "work",
                    "root_path": "data/files/workspace/work",
                }
            ],
            "active_workspace_id": "root",
            "sync_selected_workspace_ids": ["root", "work"],
        }
    )
    write_conversation(
        name="notes/root",
        messages=[{"role": "user", "content": "root"}],
        metadata={
            "id": "conv-root",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
            "display_name": "Root conversation",
        },
    )
    write_conversation(
        name="work/notes/project",
        messages=[{"role": "user", "content": "work"}],
        metadata={
            "id": "work__conv-1",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-03T00:00:00+00:00",
            "display_name": "Work conversation",
            "source_sync_namespace": "work",
        },
    )

    root_manifest = service.build_manifest(["conversations"], workspace_ids=["root"])
    work_manifest = service.build_manifest(["conversations"], workspace_ids=["work"])

    assert root_manifest["workspace_selection"]["workspace_ids"] == ["root"]
    assert work_manifest["workspace_selection"]["workspace_ids"] == ["work"]
    assert [
        item["sync_id"] for item in root_manifest["sections"]["conversations"]["items"]
    ] == ["conv-root"]
    assert [
        item["sync_id"] for item in work_manifest["sections"]["conversations"]["items"]
    ] == ["work__conv-1"]


def test_build_snapshot_filters_by_workspace_selection(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    write_conversation = modules["_write_conversation_snapshot"]
    user_settings = modules["user_settings"]

    user_settings.save_settings(
        {
            "workspace_profiles": [
                {
                    "id": "personal",
                    "name": "Personal",
                    "slug": "personal",
                    "namespace": "personal",
                    "root_path": "data/files/workspace/personal",
                }
            ],
            "active_workspace_id": "root",
            "sync_selected_workspace_ids": ["root", "personal"],
        }
    )
    write_conversation(
        name="notes/root",
        messages=[{"role": "user", "content": "root"}],
        metadata={
            "id": "conv-root",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
            "display_name": "Root conversation",
        },
    )
    write_conversation(
        name="personal/journal",
        messages=[{"role": "user", "content": "personal"}],
        metadata={
            "id": "personal__conv-2",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-04T00:00:00+00:00",
            "display_name": "Personal conversation",
            "source_sync_namespace": "personal",
        },
    )

    snapshot = service.build_snapshot(["conversations"], workspace_ids=["personal"])

    assert snapshot["workspace_selection"]["workspace_ids"] == ["personal"]
    conversations = snapshot["sections"]["conversations"]
    assert len(conversations) == 1
    assert conversations[0]["metadata"]["id"] == "personal__conv-2"


def test_build_manifest_applies_workspace_privacy_rules(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    write_conversation = modules["_write_conversation_snapshot"]
    user_settings = modules["user_settings"]

    user_settings.save_settings(
        {
            "workspace_profiles": [
                {
                    "id": "root",
                    "name": "Main workspace",
                    "privacy_mode": "default",
                    "private_patterns": ["notes/private/*"],
                },
                {
                    "id": "vault",
                    "name": "Vault",
                    "slug": "vault",
                    "namespace": "vault",
                    "root_path": "data/files/workspace/vault",
                    "privacy_mode": "protected",
                },
            ],
            "active_workspace_id": "root",
            "sync_selected_workspace_ids": ["root", "vault"],
        }
    )
    write_conversation(
        name="notes/shared/alpha",
        messages=[{"role": "user", "content": "share me"}],
        metadata={
            "id": "conv-root-visible",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
            "display_name": "Visible conversation",
        },
    )
    write_conversation(
        name="notes/private/alpha",
        messages=[{"role": "user", "content": "keep local"}],
        metadata={
            "id": "conv-root-private",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-03T00:00:00+00:00",
            "display_name": "Private conversation",
        },
    )
    write_conversation(
        name="vault/ledger",
        messages=[{"role": "user", "content": "blocked workspace"}],
        metadata={
            "id": "conv-vault",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-04T00:00:00+00:00",
            "display_name": "Vault conversation",
        },
    )

    manifest = service.build_manifest(
        ["conversations"], workspace_ids=["root", "vault"]
    )

    assert manifest["workspace_selection"]["workspace_ids"] == ["root"]
    assert manifest["workspace_selection"]["privacy_ignored_workspace_ids"] == ["vault"]
    assert [
        item["sync_id"] for item in manifest["sections"]["conversations"]["items"]
    ] == ["conv-root-visible"]


def test_build_snapshot_excludes_sensitive_memory_items_from_sync(
    tmp_path, monkeypatch
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    memory_store = modules["memory_store"]
    user_settings = modules["user_settings"]

    user_settings.save_settings(
        {
            "workspace_profiles": [
                {
                    "id": "root",
                    "name": "Main workspace",
                    "privacy_mode": "default",
                }
            ],
            "active_workspace_id": "root",
            "sync_selected_workspace_ids": ["root"],
        }
    )
    memory_store.save(
        {
            "visible": {
                "value": "allowed",
                "updated_at": 10.0,
                "sensitivity": "personal",
            },
            "protected-note": {
                "value": "blocked",
                "updated_at": 11.0,
                "sensitivity": "protected",
            },
            "secret-note": {
                "value": "blocked",
                "updated_at": 12.0,
                "sensitivity": "secret",
            },
        }
    )

    manifest = service.build_manifest(["memories"], workspace_ids=["root"])
    snapshot = service.build_snapshot(["memories"], workspace_ids=["root"])

    assert [item["key"] for item in manifest["sections"]["memories"]["items"]] == [
        "visible"
    ]
    assert [record["key"] for record in snapshot["sections"]["memories"]] == ["visible"]
