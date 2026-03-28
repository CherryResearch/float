import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    data_root = tmp_path / "data_root"
    monkeypatch.setenv("FLOAT_DATA_DIR", str(data_root))

    from app import routes
    from app.main import app

    monkeypatch.setattr(routes.subprocess, "Popen", lambda *_args, **_kwargs: None)
    return TestClient(app)


def test_memory_graph_returns_hybrid_and_explicit_edges(client):
    first_payload = {
        "value": {
            "text": "roadmap tests graph design and follow-up plan",
            "conversation": "project/session-a.json",
            "source": "workspace/notes/project-roadmap.md",
            "tools": ["search_docs", "open_file"],
        },
        "importance": 1.2,
        "hint": "project planning",
    }
    second_payload = {
        "value": {
            "text": "graph design follow-up with roadmap tests and UI plan",
            "conversation": "project/session-a.json",
            "source": "workspace/notes/project-roadmap.md",
            "tools": ["search_docs"],
        },
        "importance": 1.0,
        "hint": "graph follow-up",
    }

    first_resp = client.post("/memory/memory-one", json=first_payload)
    second_resp = client.post("/memory/memory-two", json=second_payload)
    assert first_resp.status_code == 200
    assert second_resp.status_code == 200

    graph_resp = client.get("/memory/graph")
    assert graph_resp.status_code == 200
    graph = graph_resp.json()["graph"]

    nodes = graph.get("nodes") or []
    links = graph.get("links") or []
    metadata = graph.get("metadata") or {}

    node_types = {node.get("type") for node in nodes if isinstance(node, dict)}
    assert "memory" in node_types
    assert "conversation_anchor" in node_types
    assert "file_anchor" in node_types
    assert "tool_anchor" in node_types

    explicit_categories = {
        link.get("category")
        for link in links
        if isinstance(link, dict) and link.get("type") == "explicit"
    }
    assert {"conversation", "file", "tool"}.issubset(explicit_categories)

    semantic_links = [
        link for link in links if isinstance(link, dict) and link.get("type") == "semantic"
    ]
    assert semantic_links
    assert all("embedding_score" in link for link in semantic_links)
    assert all("sae_score" in link for link in semantic_links)

    assert metadata.get("signal_mode") == "hybrid"
    assert metadata.get("memory_count", 0) >= 2


def test_memory_graph_projects_thread_summary_when_available(client, monkeypatch):
    from app import routes

    monkeypatch.setattr(
        routes.threads_service,
        "read_summary",
        lambda *_args, **_kwargs: {
            "thread_overview": {
                "threads": [
                    {
                        "label": "Meetup Planning",
                        "item_count": 3,
                        "conversation_count": 1,
                        "conversation_breakdown": [
                            {
                                "conversation": "events/codex-meetup.json",
                                "item_count": 3,
                                "latest_date": "2026-03-21",
                            }
                        ],
                    }
                ]
            }
        },
    )

    resp = client.post(
        "/memory/meetup-note",
        json={
            "value": {
                "text": "remember the catering order",
                "conversation": "events/codex-meetup.json",
            },
            "importance": 1.0,
        },
    )
    assert resp.status_code == 200

    graph_resp = client.get("/memory/graph")
    assert graph_resp.status_code == 200
    graph = graph_resp.json()["graph"]

    node_types = {node.get("type") for node in graph.get("nodes") or []}
    assert "thread" in node_types
    assert any(
        link.get("type") == "projection" and link.get("category") == "thread"
        for link in (graph.get("links") or [])
    )
    assert graph.get("metadata", {}).get("thread_projection_count") == 1


def test_memory_graph_can_skip_thread_projection(client, monkeypatch):
    from app import routes

    monkeypatch.setattr(
        routes.threads_service,
        "read_summary",
        lambda *_args, **_kwargs: {
            "thread_overview": {
                "threads": [
                    {
                        "label": "Meetup Planning",
                        "item_count": 3,
                        "conversation_count": 1,
                        "conversation_breakdown": [
                            {
                                "conversation": "events/codex-meetup.json",
                                "item_count": 3,
                                "latest_date": "2026-03-21",
                            }
                        ],
                    }
                ]
            }
        },
    )

    resp = client.post(
        "/memory/meetup-note-two",
        json={
            "value": {
                "text": "remember the room setup",
                "conversation": "events/codex-meetup.json",
            },
            "importance": 1.0,
        },
    )
    assert resp.status_code == 200

    graph_resp = client.get("/memory/graph", params={"include_thread_projection": False})
    assert graph_resp.status_code == 200
    graph = graph_resp.json()["graph"]

    assert all(
        str(node.get("type")) != "thread" for node in (graph.get("nodes") or [])
    )
    assert graph.get("metadata", {}).get("thread_projection_count") == 0
