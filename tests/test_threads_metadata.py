import importlib.util
import json
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
THREADS_SERVICE_PATH = ROOT / "backend" / "app" / "services" / "threads_service.py"


def _load_threads_service():
    app_pkg = types.ModuleType("app")
    app_pkg.__path__ = [str(ROOT / "backend" / "app")]
    sys.modules.setdefault("app", app_pkg)

    services_pkg = types.ModuleType("app.services")
    services_pkg.__path__ = [str(ROOT / "backend" / "app" / "services")]
    sys.modules["app.services"] = services_pkg

    utils_pkg = types.ModuleType("app.utils")
    utils_pkg.__path__ = [str(ROOT / "backend" / "app" / "utils")]
    sys.modules["app.utils"] = utils_pkg

    conversation_store = types.ModuleType("app.utils.conversation_store")
    conversation_store.CONV_DIR = ROOT / "data" / "conversations"
    conversation_store._messages = {}

    def list_conversations():
        return sorted(conversation_store._messages.keys())

    def load_conversation(name):
        return list(conversation_store._messages.get(name, []))

    conversation_store.list_conversations = list_conversations
    conversation_store.load_conversation = load_conversation
    sys.modules["app.utils.conversation_store"] = conversation_store

    semantic = types.ModuleType("app.services.semantic_tags_service")

    class SemanticTagsService:  # noqa: D401 - test stub
        def summarize_clusters(self, *_args, **_kwargs):
            return {}

    def chunk_text(text):
        return [str(text)]

    def embed_texts(texts):
        return [[float(index + 1)] for index, _ in enumerate(texts)], object()

    def cluster_texts(embeddings, **_kwargs):
        return [0 for _ in embeddings], 1

    def cluster_embeddings(embeddings, _k):
        return [0 for _ in embeddings], object()

    def summarize_clusters(
        nuggets,
        labels,
        embeddings,
        embedder,
        k,
        tags,
        infer_topics,
        openai_key,
        nug_sources,
        nug_speakers,
        nug_conversations,
        nug_msg_indices,
        nug_datestamps,
    ):
        return (
            {
                "tag_counts": {"auto": len(nuggets)},
                "cluster_count": k,
                "clusters": {"0": "auto"},
                "threads": {
                    "auto": [
                        {
                            "conversation": nug_conversations[0],
                            "message_index": nug_msg_indices[0],
                            "date": nug_datestamps[0],
                            "score": 0.9,
                            "excerpt": nuggets[0],
                        }
                    ]
                },
                "metadata": {},
            },
            {},
        )

    semantic.SemanticTagsService = SemanticTagsService
    semantic.chunk_text = chunk_text
    semantic.embed_texts = embed_texts
    semantic.cluster_texts = cluster_texts
    semantic.cluster_embeddings = cluster_embeddings
    semantic.summarize_clusters = summarize_clusters
    sys.modules["app.services.semantic_tags_service"] = semantic

    spec = importlib.util.spec_from_file_location(
        "app.services.threads_service",
        THREADS_SERVICE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["app.services.threads_service"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, conversation_store


def test_generate_threads_stamps_timestamp_and_sae_metadata(tmp_path):
    threads_service, conversation_store = _load_threads_service()
    conversation_store.CONV_DIR = tmp_path / "conversations"
    conversation_store.CONV_DIR.mkdir(parents=True, exist_ok=True)
    conversation_store._messages = {
        "conv1": [{"content": "hello threads", "timestamp": "2026-03-04T12:34:56Z"}]
    }

    out_path = tmp_path / "threads_summary.json"
    result = threads_service.generate_threads(
        summary_out=out_path,
        infer_topics=False,
        thread_signal_mode="hybrid",
        thread_signal_blend=0.8,
        sae_model_combo="openai/gpt-oss-20b :: future SAE pack",
        sae_embeddings_fallback=True,
        sae_live_inspect_console=False,
        sae_options={
            "enabled": True,
            "mode": "steer",
            "layer": 12,
            "topk": 20,
            "token_positions": "last",
            "features": "123:+0.8,91:-0.4",
            "dry_run": True,
        },
    )

    metadata = result.get("metadata", {})
    ui_hints = metadata.get("ui_hints", {})
    assert isinstance(metadata.get("generated_at_utc"), str)
    assert metadata.get("generated_at_utc")
    assert ui_hints.get("generated_at_utc") == metadata.get("generated_at_utc")
    assert ui_hints.get("experimental_sae", {}).get("enabled") is True
    assert ui_hints.get("experimental_sae", {}).get("mode") == "steer"
    assert ui_hints.get("experimental_sae", {}).get("layer") == 12
    assert ui_hints.get("thread_signal_mode") == "hybrid"
    assert ui_hints.get("thread_signal_blend") == 0.8
    assert ui_hints.get("sae_model_combo") == "openai/gpt-oss-20b :: future SAE pack"
    assert ui_hints.get("sae_embeddings_fallback") is True
    assert ui_hints.get("sae_live_inspect_console") is False
    assert ui_hints.get("experimental_sae", {}).get("retrieval_mode") == "hybrid"
    assert ui_hints.get("experimental_sae", {}).get("retrieval_blend") == 0.8
    assert out_path.exists()


def test_read_summary_backfills_generated_timestamp_from_mtime(tmp_path):
    threads_service, _conversation_store = _load_threads_service()
    summary_path = tmp_path / "threads_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "tag_counts": {},
                "cluster_count": 0,
                "clusters": {},
                "threads": {},
                "metadata": {"ui_hints": {}},
            }
        ),
        encoding="utf-8",
    )

    summary = threads_service.read_summary(summary_path)
    generated_at = summary.get("metadata", {}).get("generated_at_utc")
    assert isinstance(generated_at, str)
    assert generated_at
    assert summary.get("metadata", {}).get("ui_hints", {}).get("generated_at_utc") == generated_at
