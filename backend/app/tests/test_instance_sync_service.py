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
        _write_conversation_snapshot,
    )
    from app.utils import (
        blob_store,
        calendar_store,
        conversation_store,
        memory_store,
        user_settings,
    )
    from app.utils.graph_store import GraphStore
    from app.utils.knowledge_store import KnowledgeStore

    return {
        "InstanceSyncService": InstanceSyncService,
        "_write_conversation_snapshot": _write_conversation_snapshot,
        "calendar_store": calendar_store,
        "conversation_store": conversation_store,
        "memory_store": memory_store,
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
    user_settings.save_settings({"theme": "light", "tool_display_mode": "console"})

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
    attachment_target = (
        blob_store._resolve_data_files_root() / attachment_meta["relative_path"]
    )
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
    attachment_meta = modules["sync_module"]._load_attachment_meta(content_hash)
    assert attachment_meta["source_sync_label"] == "remote"
    assert (
        attachment_meta["relative_path"]
        == f"workspace/sync/remote/{content_hash}/note.txt"
    )
    target = blob_store._resolve_data_files_root() / attachment_meta["relative_path"]
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
    attachment_path = (
        blob_store._resolve_data_files_root() / attachment_meta["relative_path"]
    )
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


def test_filter_snapshot_by_item_selections_keeps_selected_records(tmp_path, monkeypatch):
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
            "conversations": ["conv-2"],
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
