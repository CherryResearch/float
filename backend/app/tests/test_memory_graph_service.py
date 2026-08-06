from app.services import memory_graph_service


def test_build_memory_graph_keeps_focus_key_with_small_limit(monkeypatch):
    monkeypatch.setattr(
        memory_graph_service,
        "get_rag_service",
        lambda raise_http=False: None,
    )
    graph = memory_graph_service.build_memory_graph(
        [
            {
                "key": "high_priority",
                "value": {"topic": "status"},
                "importance": 10,
                "updated_at": 200,
            },
            {
                "key": "focused_memory",
                "value": {"topic": "family"},
                "importance": 1,
                "updated_at": 100,
            },
        ],
        limit=1,
        focus_key="focused_memory",
    )

    labels = {
        node["label"] for node in graph["nodes"] if str(node.get("type")) == "memory"
    }

    assert labels == {"focused_memory"}
    assert graph["metadata"]["focus_key"] == "focused_memory"
    assert graph["metadata"]["focused_included"] is True


def test_build_memory_graph_projects_threads_onto_conversation_anchors(monkeypatch):
    monkeypatch.setattr(
        memory_graph_service,
        "get_rag_service",
        lambda raise_http=False: None,
    )
    graph = memory_graph_service.build_memory_graph(
        [
            {
                "key": "meetup_note",
                "value": {
                    "text": "remember to finalize the meetup catering order",
                    "conversation": "events/codex-meetup.json",
                },
                "importance": 2,
                "updated_at": 200,
            }
        ],
        thread_summary={
            "thread_overview": {
                "threads": [
                    {
                        "label": "Meetup Planning",
                        "item_count": 5,
                        "conversation_count": 1,
                        "conversation_breakdown": [
                            {
                                "conversation": "events/codex-meetup.json",
                                "item_count": 5,
                                "latest_date": "2026-03-21",
                            }
                        ],
                    }
                ]
            }
        },
    )

    thread_nodes = [
        node for node in graph["nodes"] if str(node.get("type")) == "thread"
    ]
    assert len(thread_nodes) == 1
    assert thread_nodes[0]["label"] == "Meetup Planning"
    assert thread_nodes[0]["match_key"] == "thread:meetup planning"

    projection_links = [
        link for link in graph["links"] if str(link.get("type")) == "projection"
    ]
    assert projection_links
    assert projection_links[0]["category"] == "thread"
    assert graph["metadata"]["thread_count"] == 1
    assert graph["metadata"]["thread_projection_count"] == 1


def test_build_memory_graph_does_not_cold_start_or_call_remote_embedder(monkeypatch):
    class ColdRagService:
        _embedding_encoder = None

        def _embed_text(self, _text):
            raise AssertionError(
                "graph rendering must not initialize the configured embedder"
            )

    monkeypatch.setattr(
        memory_graph_service,
        "get_rag_service",
        lambda raise_http=False: ColdRagService(),
    )

    graph = memory_graph_service.build_memory_graph(
        [{"key": "fast_graph", "value": {"topic": "performance"}}]
    )

    assert graph["metadata"]["embeddings_source"] == "hash_fallback"


def test_build_memory_graph_batches_an_already_loaded_encoder(monkeypatch):
    class LoadedEncoder:
        def __init__(self):
            self.calls = 0

        def encode(self, texts):
            self.calls += 1
            return [[float(index), 1.0] for index, _text in enumerate(texts, start=1)]

    encoder = LoadedEncoder()
    rag_service = type("LoadedRagService", (), {"_embedding_encoder": encoder})()
    monkeypatch.setattr(
        memory_graph_service,
        "get_rag_service",
        lambda raise_http=False: rag_service,
    )

    graph = memory_graph_service.build_memory_graph(
        [
            {"key": "first", "value": "one"},
            {"key": "second", "value": "two"},
        ]
    )

    assert encoder.calls == 1
    assert graph["metadata"]["embeddings_source"] == "rag_service"


def test_build_memory_graph_fast_mode_skips_loaded_encoder(monkeypatch):
    class LoadedEncoder:
        def encode(self, _texts):
            raise AssertionError("fast graph projection must skip model work")

    rag_service = type(
        "LoadedRagService", (), {"_embedding_encoder": LoadedEncoder()}
    )()
    monkeypatch.setattr(
        memory_graph_service,
        "get_rag_service",
        lambda raise_http=False: rag_service,
    )

    graph = memory_graph_service.build_memory_graph(
        [{"key": "fast", "value": "projection"}],
        use_loaded_embeddings=False,
    )

    assert graph["metadata"]["embeddings_source"] == "hash_fallback"
