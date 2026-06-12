import csv
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
RUNNER = ROOT / "notebooks" / "evaluations" / "threads_embedding_bakeoff.py"


def _load_runner_module():
    spec = importlib.util.spec_from_file_location(
        "threads_embedding_bakeoff_eval", RUNNER
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_threads_embedding_bakeoff_writes_eval_artifacts_not_live_summary(tmp_path):
    env = os.environ.copy()
    env["FLOAT_DATA_DIR"] = str(tmp_path / "live_data")
    run_root = tmp_path / "runs"
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--models",
            "hash:16,hash:32",
            "--run-root",
            str(run_root),
            "--run-id",
            "pytest",
            "--k-option",
            "2",
            "--preferred-k",
            "2",
            "--max-k",
            "4",
            "--top-n",
            "4",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    run_dir = run_root / "pytest" / "threads_embedding_bakeoff"
    assert (run_dir / "run_spec.json").exists()
    assert (run_dir / "summary.csv").exists()
    assert (run_dir / "report.md").exists()
    assert (run_dir / "readable_report.md").exists()
    assert not (tmp_path / "live_data" / "threads" / "threads_summary.json").exists()

    spec = json.loads((run_dir / "run_spec.json").read_text(encoding="utf-8"))
    assert spec["corpus"]["conversation_count"] == 4
    assert spec["models"] == ["hash:16", "hash:32"]

    with (run_dir / "summary.csv").open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["model"] for row in rows] == ["hash:16", "hash:32"]
    assert all(row["status"] == "ok" for row in rows)
    assert all(row["summary_path"] for row in rows)
    assert all(row["structural_score"] for row in rows)
    assert all(row["singleton_thread_ratio"] for row in rows)
    assert all(row["label_token_diversity"] for row in rows)
    assert all(row["thread_purity"] for row in rows)
    assert all(row["mixed_thread_count"] for row in rows)

    for model_slug in ("hash-16", "hash-32"):
        summary_path = run_dir / f"threads_summary__{model_slug}.json"
        response_path = run_dir / f"response__{model_slug}.json"
        assert summary_path.exists()
        assert response_path.exists()
        response = json.loads(response_path.read_text(encoding="utf-8"))
        assert response["status"] == "ok"
        assert response["thread_count"] >= 1
        assert response["structural_metrics"]["structural_score"] is not None

    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "## Ranking Signal" in report
    readable = (run_dir / "readable_report.md").read_text(encoding="utf-8")
    assert "Readable Masked Report" in readable
    assert "chat_01:" in readable


def test_threads_embedding_bakeoff_can_freeze_source_conversations(tmp_path):
    source = tmp_path / "source_conversations"
    recent_dir = source / "recent"
    recent_dir.mkdir(parents=True)
    recent_path = recent_dir / "alpha.json"
    recent_path.write_text(
        json.dumps(
            [
                {
                    "role": "user",
                    "timestamp": "2026-05-28T10:00:00Z",
                    "content": "tiny",
                },
                {
                    "role": "assistant",
                    "timestamp": "2026-05-28T10:01:00Z",
                    "content": "Runtime debug trace shows provider startup failure and port collision.",
                },
                {
                    "role": "user",
                    "timestamp": "2026-05-28T10:02:00Z",
                    "content": "Topic threads mixed tea party notes with provider errors.",
                },
                {
                    "role": "assistant",
                    "timestamp": "2026-05-28T10:03:00Z",
                    "content": "Sync approval review queue was missing the pending action.",
                },
            ]
        ),
        encoding="utf-8",
    )
    old_path = source / "old.json"
    old_path.write_text(
        json.dumps(
            [
                {
                    "role": "user",
                    "content": "Older conversation should be skipped by the recent-first limit.",
                }
            ]
        ),
        encoding="utf-8",
    )
    (recent_dir / "alpha.meta.json").write_text("{}", encoding="utf-8")
    snapshot_dir = source / ".context_snapshots" / "alpha"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "snap.json").write_text(
        json.dumps([{"content": "ignored"}]),
        encoding="utf-8",
    )
    (source / "settings.json").write_text(
        json.dumps({"not": "a conversation"}),
        encoding="utf-8",
    )
    os.utime(old_path, (1000, 1000))
    os.utime(recent_path, (2000, 2000))

    env = os.environ.copy()
    env["FLOAT_DATA_DIR"] = str(tmp_path / "live_data")
    run_root = tmp_path / "runs"
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--models",
            "hash:16",
            "--run-root",
            str(run_root),
            "--run-id",
            "source-freeze",
            "--source-conversations",
            str(source),
            "--limit-conversations",
            "1",
            "--max-messages-per-conversation",
            "2",
            "--min-message-chars",
            "12",
            "--k-option",
            "2",
            "--preferred-k",
            "2",
            "--max-k",
            "4",
            "--top-n",
            "4",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    run_dir = run_root / "source-freeze" / "threads_embedding_bakeoff"
    frozen_path = run_dir / "corpus" / "conversations" / "recent" / "alpha.json"
    template_path = run_dir / "expected_topics_template.json"
    assert frozen_path.exists()
    assert template_path.exists()
    assert not (run_dir / "corpus" / "conversations" / "old.json").exists()
    assert not (
        run_dir / "corpus" / "conversations" / "recent" / "alpha.meta.json"
    ).exists()
    assert not (
        run_dir
        / "corpus"
        / "conversations"
        / ".context_snapshots"
        / "alpha"
        / "snap.json"
    ).exists()
    assert not (tmp_path / "live_data" / "threads" / "threads_summary.json").exists()

    frozen_messages = json.loads(frozen_path.read_text(encoding="utf-8"))
    assert len(frozen_messages) == 2
    assert frozen_messages[0]["content"].startswith("Topic threads mixed")
    assert frozen_messages[1]["content"].startswith("Sync approval")

    spec = json.loads((run_dir / "run_spec.json").read_text(encoding="utf-8"))
    assert spec["corpus"]["conversation_count"] == 1
    assert spec["expected_topics"] == []
    assert spec["corpus_freeze"]["source_dir"] == str(source.resolve())
    assert spec["corpus_freeze"]["selected_count"] == 1
    assert spec["corpus_freeze"]["selection"] == "recent"
    assert spec["corpus_freeze"]["files"][0]["source_path"] == "recent/alpha.json"
    assert spec["corpus_freeze"]["files"][0]["frozen_message_count"] == 2
    assert spec["corpus_freeze"]["files"][0]["truncated"] is True

    with (run_dir / "summary.csv").open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["model"] for row in rows] == ["hash:16"]
    assert rows[0]["status"] == "ok"
    assert rows[0]["score"] == ""
    assert rows[0]["structural_score"]

    template = json.loads(template_path.read_text(encoding="utf-8"))
    assert template["expected_topics"] == []
    assert template["models"] == ["hash:16"]
    assert template["candidate_labels_by_model"]["hash:16"]

    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "expected_topics_template.json" in report
    readable = (run_dir / "readable_report.md").read_text(encoding="utf-8")
    assert "Selected Chats" in readable
    assert "older conversation should be skipped" not in readable.lower()


def test_threads_embedding_bakeoff_can_select_legible_source_conversations(tmp_path):
    module = _load_runner_module()
    source = tmp_path / "source_conversations"
    source.mkdir(parents=True)
    short = source / "recent_short.json"
    short.write_text(
        json.dumps([{"role": "user", "content": "ok"}]),
        encoding="utf-8",
    )
    rich = source / "older_rich.json"
    rich.write_text(
        json.dumps(
            [
                {
                    "role": "user",
                    "content": "Detailed runtime notes explain provider logs, websocket traces, and the concrete failure sequence.",
                },
                {
                    "role": "assistant",
                    "content": "The follow-up compares sync review handling with topic thread grouping and records clear next steps.",
                },
            ]
        ),
        encoding="utf-8",
    )
    os.utime(short, (3000, 3000))
    os.utime(rich, (1000, 1000))

    freeze = module._freeze_source_conversations(
        source_dir=source,
        run_dir=tmp_path / "run",
        limit_conversations=1,
        max_messages_per_conversation=10,
        min_message_chars=1,
        prefer_recent=True,
        source_selection="legible",
    )

    assert freeze.manifest["selection"] == "legible"
    assert freeze.manifest["files"][0]["source_path"] == "older_rich.json"
    assert freeze.manifest["files"][0]["selection_score"] > 0


def test_threads_embedding_bakeoff_openai_embedding_spec_batches(monkeypatch):
    module = _load_runner_module()
    monkeypatch.setattr(
        module,
        "_resolve_env_value",
        lambda key: "test-key" if key == "OPENAI_API_KEY" else "",
    )
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                    {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                ]
            }

    def fake_post(url, json, headers, timeout):
        calls.append((url, json, headers, timeout))
        return FakeResponse()

    monkeypatch.setattr(module.requests, "post", fake_post)
    embed_texts = module._build_embed_texts("api:openai/text-embedding-3-small")
    embeddings, embedder = embed_texts(["alpha", "beta"])

    assert embeddings == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert embedder.model_name == "api:openai/text-embedding-3-small"
    assert embedder.model.get_sentence_embedding_dimension() == 3
    assert calls[0][0] == "https://api.openai.com/v1/embeddings"
    assert calls[0][1]["model"] == "text-embedding-3-small"


def test_threads_embedding_bakeoff_blocks_external_source_into_repo_by_default(
    tmp_path,
):
    module = _load_runner_module()
    source = tmp_path / "outside_source"
    source.mkdir()

    with pytest.raises(ValueError, match="outside-repo source corpus"):
        module.run_bakeoff(
            corpus_dir=ROOT
            / "notebooks"
            / "evaluations"
            / "variants"
            / "threads_embedding_bakeoff"
            / "corpus",
            models=["hash:16"],
            run_root=ROOT / "notebooks" / "evaluations" / "runs",
            run_id="should-not-write-private-corpus",
            source_conversations=source,
        )


def test_threads_embedding_bakeoff_custom_corpus_does_not_inherit_default_topics(
    tmp_path,
):
    source = tmp_path / "custom_corpus"
    source.mkdir()
    (source / "focused.json").write_text(
        json.dumps(
            [
                {
                    "role": "user",
                    "content": "A readable custom corpus should stay unscored without explicit expectations.",
                }
            ]
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["FLOAT_DATA_DIR"] = str(tmp_path / "live_data")
    run_root = tmp_path / "runs"
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--models",
            "hash:16",
            "--run-root",
            str(run_root),
            "--run-id",
            "custom-corpus",
            "--corpus",
            str(source),
            "--k-option",
            "1",
            "--preferred-k",
            "2",
            "--max-k",
            "4",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    spec = json.loads(
        (
            run_root / "custom-corpus" / "threads_embedding_bakeoff" / "run_spec.json"
        ).read_text(encoding="utf-8")
    )
    assert spec["expected_topics"] == []


def test_threads_embedding_bakeoff_seed_topics_use_manual_thread_assignment(tmp_path):
    source = tmp_path / "seeded_corpus"
    source.mkdir()
    (source / "mixed.json").write_text(
        json.dumps(
            [
                {
                    "role": "user",
                    "content": "Food notes mention pasta sauce and a dinner recipe.",
                },
                {
                    "role": "assistant",
                    "content": "Tool use notes mention write_file and recall approval.",
                },
            ]
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["FLOAT_DATA_DIR"] = str(tmp_path / "live_data")
    run_root = tmp_path / "runs"
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--models",
            "hash:16",
            "--run-root",
            str(run_root),
            "--run-id",
            "seeded",
            "--corpus",
            str(source),
            "--seed-topics",
            "food, tool use, miscellaneous",
            "--top-n",
            "3",
            "--no-infer-topics",
            "--preferred-k",
            "2",
            "--max-k",
            "4",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    run_dir = run_root / "seeded" / "threads_embedding_bakeoff"
    spec = json.loads((run_dir / "run_spec.json").read_text(encoding="utf-8"))
    assert spec["generation"]["seed_topics"] == ["food", "tool use", "miscellaneous"]

    summary = json.loads(
        (run_dir / "threads_summary__hash-16.json").read_text(encoding="utf-8")
    )
    assert set(summary["threads"]).issubset({"food", "tool use", "miscellaneous"})


def test_threads_embedding_bakeoff_can_compare_topic_labelers(tmp_path):
    source = tmp_path / "labeler_corpus"
    source.mkdir()
    (source / "focused.json").write_text(
        json.dumps(
            [
                {
                    "role": "user",
                    "content": "Food planning compares dinner recipes and grocery prep.",
                },
                {
                    "role": "assistant",
                    "content": "Tool use notes compare recall, write_file, and approval repair.",
                },
            ]
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["FLOAT_DATA_DIR"] = str(tmp_path / "live_data")
    env.pop("OPENAI_API_KEY", None)
    env.pop("API_KEY", None)
    run_root = tmp_path / "runs"
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--models",
            "hash:16",
            "--topic-labelers",
            "local:heuristic,api:gpt-test",
            "--run-root",
            str(run_root),
            "--run-id",
            "labelers",
            "--corpus",
            str(source),
            "--k-option",
            "2",
            "--preferred-k",
            "2",
            "--max-k",
            "4",
            "--top-n",
            "4",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    run_dir = run_root / "labelers" / "threads_embedding_bakeoff"
    assert (run_dir / "threads_summary__hash-16__labeler-local-heuristic.json").exists()
    api_summary_path = run_dir / "threads_summary__hash-16__labeler-api-gpt-test.json"
    assert api_summary_path.exists()
    api_summary = json.loads(api_summary_path.read_text(encoding="utf-8"))
    assert (
        api_summary["metadata"]["operation"]["source"]
        == "notebooks/evaluations/threads_embedding_bakeoff.py"
    )

    spec = json.loads((run_dir / "run_spec.json").read_text(encoding="utf-8"))
    assert [entry["spec"] for entry in spec["topic_labelers"]] == [
        "local:heuristic",
        "api:gpt-test",
    ]

    with (run_dir / "summary.csv").open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["topic_labeler"] for row in rows] == [
        "local:heuristic",
        "api:gpt-test",
    ]
    assert rows[1]["effective_topic_labeler"] == "local:heuristic"


def test_threads_embedding_bakeoff_scores_source_with_expected_topics_file(tmp_path):
    source = tmp_path / "source_conversations"
    source.mkdir(parents=True)
    (source / "focused.json").write_text(
        json.dumps(
            [
                {
                    "role": "user",
                    "content": "Runtime debug notes mention websocket provider logs and startup traces.",
                },
                {
                    "role": "assistant",
                    "content": "Sync review needs approval handling for pending review records.",
                },
            ]
        ),
        encoding="utf-8",
    )
    expected_topics = tmp_path / "expected_topics.json"
    expected_topics.write_text(
        json.dumps(
            {
                "expected_topics": [
                    {
                        "id": "runtime_debugging",
                        "terms": ["runtime debug", "provider logs"],
                    },
                    {
                        "id": "sync_review",
                        "terms": ["sync review", "pending review"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["FLOAT_DATA_DIR"] = str(tmp_path / "live_data")
    run_root = tmp_path / "runs"
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--models",
            "hash:16",
            "--run-root",
            str(run_root),
            "--run-id",
            "source-scored",
            "--source-conversations",
            str(source),
            "--expected-topics-file",
            str(expected_topics),
            "--k-option",
            "2",
            "--preferred-k",
            "2",
            "--max-k",
            "4",
            "--top-n",
            "4",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    run_dir = run_root / "source-scored" / "threads_embedding_bakeoff"
    assert not (run_dir / "expected_topics_template.json").exists()
    spec = json.loads((run_dir / "run_spec.json").read_text(encoding="utf-8"))
    assert [topic["id"] for topic in spec["expected_topics"]] == [
        "runtime_debugging",
        "sync_review",
    ]

    response = json.loads((run_dir / "response__hash-16.json").read_text("utf-8"))
    assert response["status"] == "ok"
    assert response["scoring"]["expected_topic_count"] == 2
    assert response["scoring"]["score"] is not None
    assert response["scoring"]["topic_body_coverage"] is not None
    assert response["structural_metrics"]["structural_score"] is not None

    with (run_dir / "summary.csv").open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["score"]
    assert rows[0]["topic_body_coverage"]
    assert rows[0]["structural_score"]
