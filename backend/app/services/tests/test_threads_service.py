import json

from app.services import threads_service
from app.utils import conversation_store


def _setup_conversation(tmp_path):
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir()
    conversation_store.CONV_DIR = conv_dir
    (conv_dir / "conv1.json").write_text(
        json.dumps([{"content": "hello world"}]), encoding="utf-8"
    )
    return conv_dir


def _setup_conversations_map(tmp_path, mapping):
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir()
    conversation_store.CONV_DIR = conv_dir
    for name, messages in mapping.items():
        path = conv_dir / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(messages), encoding="utf-8")
    return conv_dir


def _patch_semantic(monkeypatch):
    def fake_embed_texts(texts):
        return [[1.0] for _ in texts], object()

    def fake_cluster_texts(embeddings, **_kwargs):
        return [0 for _ in embeddings], 1

    def fake_summarize(
        nuggets,
        labels,
        embeddings,
        embedder,
        k,
        *args,
        **kwargs,
    ):
        summary = {
            "tag_counts": {},
            "cluster_count": 1,
            "clusters": {},
            "threads": {
                "auto": [
                    {
                        "conversation": "conv1",
                        "message_index": 0,
                        "date": "",
                        "score": 0.0,
                        "excerpt": "hello world",
                    }
                ]
            },
            "metadata": {},
        }
        return summary, {}

    monkeypatch.setattr(threads_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(threads_service, "cluster_texts", fake_cluster_texts)
    monkeypatch.setattr(threads_service, "summarize_clusters", fake_summarize)


def test_generate_threads_writes_summary_file(monkeypatch, tmp_path):
    _setup_conversation(tmp_path)
    _patch_semantic(monkeypatch)
    out_path = tmp_path / "summary.json"
    result = threads_service.generate_threads(
        summary_out=out_path,
        infer_topics=False,
        operation_id="op-threads-test",
        operation_owner="/api/threads/generate",
    )
    assert out_path.exists()
    on_disk = json.loads(out_path.read_text())
    assert on_disk["threads"] == result["threads"]
    assert on_disk.get("metadata", {}).get("ui_hints", {}).get("k_option") == "auto"
    assert on_disk.get("metadata", {}).get("ui_hints", {}).get("k_selected") == 1
    generated_at = on_disk.get("metadata", {}).get("generated_at_utc")
    assert isinstance(generated_at, str) and generated_at
    assert (
        on_disk.get("metadata", {}).get("ui_hints", {}).get("generated_at_utc")
        == generated_at
    )
    assert (
        on_disk.get("metadata", {})
        .get("ui_hints", {})
        .get("experimental_sae", {})
        .get("enabled")
        is False
    )
    operation = on_disk.get("metadata", {}).get("operation", {})
    assert operation.get("id") == "op-threads-test"
    assert operation.get("owner") == "/api/threads/generate"
    assert operation.get("source") == "/api/threads/generate"
    assert operation.get("topic_labeler_used") == "local:heuristic"
    assert result.get("thread_overview", {}).get("total_threads") == 1
    assert on_disk.get("schema", {}).get("threads_summary_version") == 2


def test_manual_thread_labels_override(monkeypatch, tmp_path):
    _setup_conversation(tmp_path)
    _patch_semantic(monkeypatch)
    out_path = tmp_path / "summary.json"
    result = threads_service.generate_threads(
        summary_out=out_path,
        infer_topics=False,
        manual_threads=["Manual"],
    )
    assert "Manual" in result.get("threads", {})
    assert "auto" not in result.get("threads", {})
    assert result.get("metadata", {}).get("ui_hints", {}).get("manual_threads") == [
        "Manual"
    ]


def test_generate_threads_keeps_topic_inference_without_key(monkeypatch, tmp_path):
    _setup_conversation(tmp_path)

    def fake_embed_texts(texts):
        return [[1.0] for _ in texts], object()

    def fake_cluster_texts(embeddings, **_kwargs):
        return [0 for _ in embeddings], 1

    observed = {}

    def fake_summarize(
        nuggets,
        labels,
        embeddings,
        embedder,
        k,
        tags,
        infer_topics_flag,
        openai_key,
        *rest,
    ):
        observed["infer_topics"] = infer_topics_flag
        return {
            "tag_counts": {},
            "cluster_count": 1,
            "clusters": {},
            "threads": {},
            "metadata": {},
        }, {}

    monkeypatch.setattr(threads_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(threads_service, "cluster_texts", fake_cluster_texts)
    monkeypatch.setattr(threads_service, "summarize_clusters", fake_summarize)

    out_path = tmp_path / "summary.json"
    threads_service.generate_threads(
        summary_out=out_path,
        infer_topics=True,
        openai_key=None,
    )
    assert observed["infer_topics"] is True


def test_generate_threads_records_embedding_model_and_sensitive_mode(
    monkeypatch, tmp_path
):
    _setup_conversation(tmp_path)

    observed = {}

    def fake_embed_texts(texts, model_name=None):
        observed["embedding_model"] = model_name
        return [[1.0] for _ in texts], object()

    def fake_cluster_texts(embeddings, **_kwargs):
        return [0 for _ in embeddings], 1

    def fake_summarize(
        nuggets,
        labels,
        embeddings,
        embedder,
        k,
        tags,
        infer_topics_flag,
        openai_key,
        *rest,
    ):
        observed["openai_key"] = openai_key
        return {
            "tag_counts": {},
            "cluster_count": 1,
            "clusters": {},
            "threads": {},
            "metadata": {},
        }, {}

    monkeypatch.setattr(threads_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(threads_service, "cluster_texts", fake_cluster_texts)
    monkeypatch.setattr(threads_service, "summarize_clusters", fake_summarize)

    result = threads_service.generate_threads(
        summary_out=tmp_path / "summary.json",
        infer_topics=True,
        openai_key="sk-test",
        embedding_model="sentence-transformers/all-mpnet-base-v2",
        sensitive_mode=True,
    )

    hints = result.get("metadata", {}).get("ui_hints", {})
    assert observed["embedding_model"] == "sentence-transformers/all-mpnet-base-v2"
    assert observed["openai_key"] is None
    assert hints.get("embedding_model_requested") == (
        "sentence-transformers/all-mpnet-base-v2"
    )
    assert hints.get("sensitive_mode") is True
    assert hints.get("external_topic_labeling") is False


def test_generate_threads_blocks_api_labeling_for_sensitive_scope(
    monkeypatch, tmp_path
):
    _setup_conversation(tmp_path)
    conversation_store.merge_metadata(
        "conv1",
        {
            "privacy_mode": "protected",
            "sensitivity": "protected",
            "sensitivity_source": "user",
        },
    )

    observed = {}

    def fake_embed_texts(texts, model_name=None):
        return [[1.0] for _ in texts], object()

    def fake_cluster_texts(embeddings, **_kwargs):
        return [0 for _ in embeddings], 1

    def fake_summarize(
        nuggets,
        labels,
        embeddings,
        embedder,
        k,
        tags,
        infer_topics_flag,
        openai_key,
        *rest,
    ):
        observed["openai_key"] = openai_key
        return {
            "tag_counts": {},
            "cluster_count": 1,
            "clusters": {},
            "conversations": {
                "conv1": {"nugget_count": 1, "topics": {"private admin": 1}}
            },
            "threads": {},
            "metadata": {},
        }, {}

    monkeypatch.setattr(threads_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(threads_service, "cluster_texts", fake_cluster_texts)
    monkeypatch.setattr(threads_service, "summarize_clusters", fake_summarize)

    result = threads_service.generate_threads(
        summary_out=tmp_path / "summary.json",
        infer_topics=True,
        openai_key="sk-test",
        topic_suggestion_provider="api",
        topic_suggestion_model="gpt-4o-mini",
        sensitive_mode=True,
    )

    hints = result.get("metadata", {}).get("ui_hints", {})
    generation = result.get("metadata", {}).get("generation", {})
    assert observed["openai_key"] is None
    assert hints.get("topic_suggestion_provider_requested") == "api"
    assert hints.get("topic_suggestion_provider") == "local"
    assert hints.get("api_topic_labeling_blocked") is True
    assert hints.get("api_topic_labeling_blocked_reason") == "sensitive_conversation"
    assert hints.get("sensitive_conversation_count") == 1
    assert hints.get("external_topic_labeling") is False
    assert generation.get("topic_seeds") == []
    assert generation.get("suggested_topics") == [
        {"topic": "private admin", "score": 1.0}
    ]


def test_generate_threads_tolerates_string_message_entries(monkeypatch, tmp_path):
    _setup_conversations_map(
        tmp_path,
        {
            "conv1": [
                "legacy string message",
                {"content": "dict message", "source_message_index": 7},
                123,
            ]
        },
    )

    observed = {}

    def fake_embed_texts(texts):
        observed["texts"] = list(texts)
        return [[1.0] for _ in texts], object()

    def fake_cluster_texts(embeddings, **_kwargs):
        return [0 for _ in embeddings], 1

    def fake_summarize(
        nuggets,
        labels,
        embeddings,
        embedder,
        k,
        *args,
        **kwargs,
    ):
        observed["message_indices"] = list(args[6])
        observed["dates"] = list(args[7])
        return {
            "tag_counts": {},
            "cluster_count": 1,
            "clusters": {},
            "threads": {},
            "metadata": {},
        }, {}

    monkeypatch.setattr(threads_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(threads_service, "cluster_texts", fake_cluster_texts)
    monkeypatch.setattr(threads_service, "summarize_clusters", fake_summarize)

    result = threads_service.generate_threads(
        summary_out=tmp_path / "summary.json",
        infer_topics=False,
    )

    assert result.get("cluster_count") == 1
    assert observed["texts"] == ["legacy string message", "dict message"]
    assert observed["message_indices"] == [0, 7]
    assert observed["dates"] == ["", ""]


def test_generate_threads_respects_explicit_k_option(monkeypatch, tmp_path):
    _setup_conversation(tmp_path)

    def fake_embed_texts(_texts):
        return [[1.0], [2.0], [3.0], [4.0], [5.0]], object()

    def fake_cluster_texts(_embeddings):
        raise AssertionError(
            "auto cluster selection should not run when k_option is set"
        )

    def fake_cluster_embeddings(embeddings, k, **_kwargs):
        assert k == 4
        return [0 for _ in embeddings], object()

    observed = {}

    def fake_summarize(
        nuggets,
        labels,
        embeddings,
        embedder,
        k,
        *args,
        **kwargs,
    ):
        observed["k"] = k
        return {
            "tag_counts": {},
            "cluster_count": k,
            "clusters": {},
            "threads": {},
            "metadata": {},
        }, {}

    monkeypatch.setattr(threads_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(threads_service, "cluster_texts", fake_cluster_texts)
    monkeypatch.setattr(threads_service, "cluster_embeddings", fake_cluster_embeddings)
    monkeypatch.setattr(threads_service, "summarize_clusters", fake_summarize)

    out_path = tmp_path / "summary.json"
    result = threads_service.generate_threads(
        summary_out=out_path,
        infer_topics=False,
        k_option=4,
    )
    assert observed["k"] == 4
    assert result.get("metadata", {}).get("ui_hints", {}).get("k_option") == 4


def test_generate_threads_passes_auto_k_preferences(monkeypatch, tmp_path):
    _setup_conversation(tmp_path)

    def fake_embed_texts(_texts):
        return [[1.0], [2.0], [3.0]], object()

    observed = {}

    def fake_cluster_texts(embeddings, **kwargs):
        observed["preferred_k"] = kwargs.get("preferred_k")
        observed["max_k"] = kwargs.get("max_k")
        return [0 for _ in embeddings], 1

    def fake_summarize(
        nuggets,
        labels,
        embeddings,
        embedder,
        k,
        *args,
        **kwargs,
    ):
        return {
            "tag_counts": {},
            "cluster_count": k,
            "clusters": {},
            "threads": {},
            "metadata": {},
        }, {}

    monkeypatch.setattr(threads_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(threads_service, "cluster_texts", fake_cluster_texts)
    monkeypatch.setattr(threads_service, "summarize_clusters", fake_summarize)

    result = threads_service.generate_threads(
        summary_out=tmp_path / "summary.json",
        infer_topics=False,
        preferred_k=14,
        max_k=22,
    )
    assert observed["preferred_k"] == 14
    assert observed["max_k"] == 22
    hints = result.get("metadata", {}).get("ui_hints", {})
    assert hints.get("preferred_k") == 14
    assert hints.get("max_k") == 22


def test_generate_threads_records_cluster_backend_hints(monkeypatch, tmp_path):
    _setup_conversation(tmp_path)

    def fake_embed_texts(_texts):
        return [[1.0], [2.0], [3.0]], object()

    observed = {}

    def fake_cluster_texts(embeddings, **kwargs):
        observed["cluster_backend"] = kwargs.get("cluster_backend")
        observed["cluster_device"] = kwargs.get("cluster_device")
        return [0 for _ in embeddings], 1

    def fake_summarize(
        nuggets,
        labels,
        embeddings,
        embedder,
        k,
        *args,
        **kwargs,
    ):
        return {
            "tag_counts": {},
            "cluster_count": k,
            "clusters": {},
            "threads": {},
            "metadata": {},
        }, {}

    monkeypatch.setattr(threads_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(threads_service, "cluster_texts", fake_cluster_texts)
    monkeypatch.setattr(threads_service, "summarize_clusters", fake_summarize)
    monkeypatch.setattr(
        threads_service,
        "resolve_cluster_backend",
        lambda backend, device: {
            "requested_backend": "torch",
            "requested_device": "cuda",
            "backend": "torch",
            "device": "cpu",
            "fallback": True,
            "reason": "cuda_unavailable",
            "torch_available": True,
        },
    )

    result = threads_service.generate_threads(
        summary_out=tmp_path / "summary.json",
        infer_topics=False,
        cluster_backend="torch",
        cluster_device="cuda",
    )

    assert observed["cluster_backend"] == "torch"
    assert observed["cluster_device"] == "cpu"
    hints = result.get("metadata", {}).get("ui_hints", {})
    assert hints.get("cluster_backend") == "torch"
    assert hints.get("cluster_device") == "cpu"
    assert hints.get("cluster_backend_requested") == "torch"
    assert hints.get("cluster_device_requested") == "cuda"
    assert hints.get("cluster_backend_fallback") is True
    assert hints.get("cluster_backend_reason") == "cuda_unavailable"


def test_generate_threads_persists_experimental_sae_options(monkeypatch, tmp_path):
    _setup_conversation(tmp_path)
    _patch_semantic(monkeypatch)
    out_path = tmp_path / "summary.json"

    result = threads_service.generate_threads(
        summary_out=out_path,
        infer_topics=False,
        thread_signal_mode="hybrid",
        thread_signal_blend=0.75,
        sae_model_combo="google/gemma-2-2b :: Gemma Scope",
        sae_embeddings_fallback=True,
        sae_live_inspect_console=True,
        sae_options={
            "enabled": True,
            "mode": "steer",
            "layer": 12,
            "topk": 24,
            "token_positions": "last",
            "features": "123:+0.8,91:-0.4",
            "dry_run": True,
        },
    )

    hints = result.get("metadata", {}).get("ui_hints", {})
    sae = hints.get("experimental_sae", {})
    assert sae.get("enabled") is True
    assert sae.get("mode") == "steer"
    assert sae.get("layer") == 12
    assert sae.get("topk") == 24
    assert sae.get("token_positions") == "last"
    assert sae.get("dry_run") is True
    assert sae.get("retrieval_mode") == "hybrid"
    assert sae.get("retrieval_blend") == 0.75
    assert sae.get("model_combo") == "google/gemma-2-2b :: Gemma Scope"
    assert sae.get("embeddings_fallback") is True
    assert sae.get("live_inspect_console") is True
    assert hints.get("thread_signal_mode") == "hybrid"
    assert hints.get("thread_signal_blend") == 0.75
    assert hints.get("sae_model_combo") == "google/gemma-2-2b :: Gemma Scope"
    assert hints.get("sae_embeddings_fallback") is True
    assert hints.get("sae_live_inspect_console") is True


def test_generate_threads_hybrid_blend_changes_manual_assignment(monkeypatch, tmp_path):
    _setup_conversation(tmp_path)

    def fake_embed_texts(texts):
        values = list(texts)
        if values == ["Topic Broad", "Topic Specific"]:
            return [[0.8, 0.8, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]], object()
        return [[0.6, 0.9, 0.0, 0.0] for _ in values], object()

    def fake_cluster_texts(embeddings, **_kwargs):
        return [0 for _ in embeddings], 1

    def fake_summarize(
        nuggets,
        labels,
        embeddings,
        embedder,
        k,
        *args,
        **kwargs,
    ):
        return {
            "tag_counts": {"auto": len(nuggets)},
            "cluster_count": 1,
            "clusters": {"0": "auto"},
            "threads": {
                "auto": [
                    {
                        "conversation": "conv1",
                        "message_index": 0,
                        "date": "",
                        "score": 0.0,
                        "excerpt": "hello world",
                    }
                ]
            },
            "metadata": {},
        }, {}

    monkeypatch.setattr(threads_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(threads_service, "cluster_texts", fake_cluster_texts)
    monkeypatch.setattr(threads_service, "summarize_clusters", fake_summarize)

    high_blend = threads_service.generate_threads(
        summary_out=tmp_path / "high.json",
        infer_topics=False,
        manual_threads=["Topic Broad", "Topic Specific"],
        thread_signal_mode="hybrid",
        thread_signal_blend=0.9,
        sae_options={"topk": 1},
    )
    low_blend = threads_service.generate_threads(
        summary_out=tmp_path / "low.json",
        infer_topics=False,
        manual_threads=["Topic Broad", "Topic Specific"],
        thread_signal_mode="hybrid",
        thread_signal_blend=0.1,
        sae_options={"topk": 1},
    )

    assert high_blend.get("threads", {}).get("Topic Specific")
    assert not high_blend.get("threads", {}).get("Topic Broad")
    assert low_blend.get("threads", {}).get("Topic Broad")
    assert not low_blend.get("threads", {}).get("Topic Specific")
    assert (
        low_blend.get("metadata", {}).get("ui_hints", {}).get("thread_signal_blend")
        == 0.1
    )


def test_generate_threads_coalesces_meal_party_labels(monkeypatch, tmp_path):
    _setup_conversation(tmp_path)

    def fake_embed_texts(_texts):
        return [[1.0], [2.0]], object()

    def fake_cluster_texts(embeddings, **_kwargs):
        return [0 for _ in embeddings], 1

    def fake_summarize(
        nuggets,
        labels,
        embeddings,
        embedder,
        k,
        *args,
        **kwargs,
    ):
        return {
            "tag_counts": {"Tea Party": 2, "Tea Party Menu": 1},
            "cluster_count": 2,
            "clusters": {"0": "Tea Party", "1": "Tea Party Menu"},
            "threads": {
                "Tea Party": [
                    {
                        "conversation": "conv1",
                        "message_index": 0,
                        "date": "",
                        "score": 0.9,
                        "excerpt": "planning tea",
                    }
                ],
                "Tea Party Menu": [
                    {
                        "conversation": "conv1",
                        "message_index": 1,
                        "date": "",
                        "score": 0.8,
                        "excerpt": "menu draft",
                    }
                ],
            },
            "conversations": {
                "conv1": {
                    "nugget_count": 2,
                    "topics": {"Tea Party": 1, "Tea Party Menu": 1},
                }
            },
            "metadata": {},
        }, {}

    monkeypatch.setattr(threads_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(threads_service, "cluster_texts", fake_cluster_texts)
    monkeypatch.setattr(threads_service, "summarize_clusters", fake_summarize)

    result = threads_service.generate_threads(
        summary_out=tmp_path / "summary.json",
        infer_topics=False,
        coalesce_related=True,
    )
    assert "Meal Party" in result.get("threads", {})
    assert "Tea Party" not in result.get("threads", {})
    assert "Tea Party Menu" not in result.get("threads", {})
    assert result.get("metadata", {}).get("ui_hints", {}).get("merged_label_count") == 2


def test_generate_threads_scopes_to_folder(monkeypatch, tmp_path):
    _setup_conversations_map(
        tmp_path,
        {
            "events/tea_party": [{"content": "tea planning details"}],
            "engineering/debug": [{"content": "stack trace context"}],
        },
    )

    def fake_embed_texts(texts):
        return [[float(i + 1)] for i, _ in enumerate(texts)], object()

    def fake_cluster_texts(embeddings, **_kwargs):
        return [0 for _ in embeddings], 1

    observed = {}

    def fake_summarize(
        nuggets,
        labels,
        embeddings,
        embedder,
        k,
        tags,
        infer_topics_flag,
        openai_key,
        nug_sources,
        nug_speakers,
        nug_conversations,
        nug_msg_indices,
        nug_datestamps,
    ):
        observed["conversations"] = list(nug_conversations)
        return {
            "tag_counts": {},
            "cluster_count": 1,
            "clusters": {},
            "threads": {},
            "metadata": {},
        }, {}

    monkeypatch.setattr(threads_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(threads_service, "cluster_texts", fake_cluster_texts)
    monkeypatch.setattr(threads_service, "summarize_clusters", fake_summarize)

    result = threads_service.generate_threads(
        summary_out=tmp_path / "summary.json",
        infer_topics=False,
        scope_folder="events",
    )
    assert observed["conversations"] == ["events/tea_party"]
    assert result.get("metadata", {}).get("ui_hints", {}).get("scope_mode") == "folder"
    assert (
        result.get("metadata", {}).get("ui_hints", {}).get("scope_folder") == "events"
    )


def test_generate_threads_scopes_to_existing_thread(monkeypatch, tmp_path):
    _setup_conversations_map(
        tmp_path,
        {
            "conv1": [{"content": "first message"}, {"content": "second message"}],
            "conv2": [{"content": "other conversation"}],
        },
    )
    out_path = tmp_path / "summary.json"
    out_path.write_text(
        json.dumps(
            {
                "threads": {
                    "Parent": [
                        {
                            "conversation": "conv1",
                            "message_index": 1,
                            "excerpt": "second message",
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_embed_texts(texts):
        return [[float(i + 1)] for i, _ in enumerate(texts)], object()

    def fake_cluster_texts(embeddings, **_kwargs):
        return [0 for _ in embeddings], 1

    observed = {}

    def fake_summarize(
        nuggets,
        labels,
        embeddings,
        embedder,
        k,
        tags,
        infer_topics_flag,
        openai_key,
        nug_sources,
        nug_speakers,
        nug_conversations,
        nug_msg_indices,
        nug_datestamps,
    ):
        observed["pairs"] = list(zip(nug_conversations, nug_msg_indices))
        return {
            "tag_counts": {},
            "cluster_count": 1,
            "clusters": {},
            "threads": {},
            "metadata": {},
        }, {}

    monkeypatch.setattr(threads_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(threads_service, "cluster_texts", fake_cluster_texts)
    monkeypatch.setattr(threads_service, "summarize_clusters", fake_summarize)

    result = threads_service.generate_threads(
        summary_out=out_path,
        infer_topics=False,
        scope_thread="Parent",
    )
    assert observed["pairs"] == [("conv1", 1)]
    hints = result.get("metadata", {}).get("ui_hints", {})
    assert hints.get("scope_mode") == "thread"
    assert hints.get("scope_thread") == "Parent"


def test_generate_threads_scope_thread_missing_raises(monkeypatch, tmp_path):
    _setup_conversation(tmp_path)
    _patch_semantic(monkeypatch)
    out_path = tmp_path / "summary.json"
    out_path.write_text(json.dumps({"threads": {}}), encoding="utf-8")
    try:
        threads_service.generate_threads(
            summary_out=out_path,
            infer_topics=False,
            scope_thread="Missing",
        )
    except ValueError as exc:
        assert "refine" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError when scope thread is missing")


def test_generate_threads_normalizes_conversation_summary_keys(monkeypatch, tmp_path):
    _setup_conversation(tmp_path)

    def fake_embed_texts(_texts):
        return [[1.0], [2.0]], object()

    def fake_cluster_texts(embeddings, **_kwargs):
        return [0 for _ in embeddings], 1

    def fake_summarize(
        nuggets,
        labels,
        embeddings,
        embedder,
        k,
        *args,
        **kwargs,
    ):
        return {
            "tag_counts": {"planning": 2},
            "cluster_count": 1,
            "clusters": {"0": "planning"},
            "conversations": {
                "events/tea_party.json#msg=0": {
                    "nugget_count": 1,
                    "topics": {"planning": 1},
                },
                "events/tea_party.json#msg=1": {
                    "nugget_count": 2,
                    "topics": {"planning": 2},
                },
            },
            "threads": {
                "planning": [
                    {
                        "conversation": "events/tea_party.json#msg=1",
                        "message_index": 1,
                        "date": "2026-02-01",
                        "score": 0.9,
                        "excerpt": "menu draft",
                    }
                ]
            },
            "metadata": {},
        }, {}

    monkeypatch.setattr(threads_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(threads_service, "cluster_texts", fake_cluster_texts)
    monkeypatch.setattr(threads_service, "summarize_clusters", fake_summarize)

    result = threads_service.generate_threads(
        summary_out=tmp_path / "summary.json",
        infer_topics=False,
    )
    conversations = result.get("conversations", {})
    assert "events/tea_party" in conversations
    assert conversations["events/tea_party"]["nugget_count"] == 3
    overview = result.get("thread_overview", {}).get("threads", [])
    assert overview
    assert (
        overview[0]["conversation_breakdown"][0]["conversation"] == "events/tea_party"
    )


def test_thread_overview_compacts_source_ranges_and_dates():
    assert threads_service._compact_message_ranges(
        [7, "3", 2, 3, 4, 7, 9, 10, None, "bad", -1]
    ) == [
        {"start": 2, "end": 4},
        {"start": 7, "end": 7},
        {"start": 9, "end": 10},
    ]
    assert threads_service._compact_message_ranges([None, "bad", -1]) == []

    summary = threads_service._ensure_summary_schema(
        {
            "threads": {
                "planning": [
                    {
                        "conversation": "events/tea_party.json#msg=2",
                        "message_index": 2,
                        "message_indices": [2, 3, 4, 7, 7],
                        "date": "2026-02-02",
                        "first_date": "2026-02-01",
                        "latest_date": "2026-02-03",
                    },
                    {
                        "conversation": "events/tea_party",
                        "message_index": 10,
                        "message_indices": [9, 10],
                        "date": "2026-02-05",
                    },
                ],
                "undated": [
                    {
                        "conversation": "notes/undated",
                        "message_index": 5,
                        "message_indices": [5],
                    }
                ],
            }
        }
    )

    assert summary["schema"]["thread_overview_version"] == 2
    rows = {row["label"]: row for row in summary["thread_overview"]["threads"]}
    planning = rows["planning"]["conversation_breakdown"][0]
    assert planning["message_count"] == 6
    assert planning["message_ranges"] == [
        {"start": 2, "end": 4},
        {"start": 7, "end": 7},
        {"start": 9, "end": 10},
    ]
    assert planning["first_date"] == "2026-02-01"
    assert planning["latest_date"] == "2026-02-05"
    undated = rows["undated"]["conversation_breakdown"][0]
    assert undated["message_ranges"] == [{"start": 5, "end": 5}]
    assert undated["first_date"] == ""
    assert undated["latest_date"] == ""


def test_read_summary_migrates_legacy_repo_root_summary(monkeypatch, tmp_path):
    legacy_path = tmp_path / "summary.json"
    migrated_path = tmp_path / "data" / "threads" / "threads_summary.json"
    legacy_payload = {
        "tag_counts": {"Meal Party": 2},
        "cluster_count": 1,
        "clusters": {"0": "Meal Party"},
        "threads": {"Meal Party": []},
    }
    legacy_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    monkeypatch.setattr(threads_service, "DEFAULT_SUMMARY_PATH", migrated_path)
    monkeypatch.setattr(threads_service, "LEGACY_SUMMARY_PATH", legacy_path)

    result = threads_service.read_summary()

    assert result.get("threads") == legacy_payload.get("threads")
    assert result.get("schema", {}).get("threads_summary_version") == 2
    assert result.get("thread_overview", {}).get("total_threads") == 1
    assert migrated_path.exists()
    assert not legacy_path.exists()


def test_read_summary_adds_generated_timestamp_from_file_mtime(tmp_path):
    out_path = tmp_path / "threads_summary.json"
    out_path.write_text(
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

    result = threads_service.read_summary(out_path)
    generated_at = result.get("metadata", {}).get("generated_at_utc")
    assert isinstance(generated_at, str) and generated_at
    assert (
        result.get("metadata", {}).get("ui_hints", {}).get("generated_at_utc")
        == generated_at
    )
