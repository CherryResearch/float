from __future__ import annotations

import pytest
from app.services import reflection_service as reflection_module
from app.services.reflection_service import ReflectionService
from app.services.work_run_store import WorkRunStore
from app.tools import memory as memory_tools
from app.tools import reflections as reflection_tools
from app.utils import generate_signature


def test_reflection_service_crud_scoring_and_run(tmp_path, monkeypatch):
    monkeypatch.setattr(reflection_module, "try_ingest_text", lambda *a, **k: "doc-1")
    monkeypatch.setattr(reflection_module, "get_rag_service", lambda *a, **k: None)

    calls = []

    def fake_generate(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        if kwargs.get("session_id") == "reflection-task-scorer":
            return {"text": '{"utility": 0.80, "uncertainty": 0.60}'}
        if kwargs.get("response_format") == "json_object":
            return {
                "text": (
                    '{"novelty": 0.82, "usefulness": 0.78, '
                    '"uncertainty_delta": 0.50, "repetition": 0.10, '
                    '"continue": true, "should_surface_to_user": true, '
                    '"cooldown_seconds": 60}'
                )
            }
        return {
            "text": (
                "Current best synthesis: the question has a concrete next angle.\n"
                "What changed: a narrower follow-up emerged.\n"
                "Remaining uncertainty: medium.\n"
                "Another pass: yes.\n"
                "Surface to user: yes."
            )
        }

    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=fake_generate,
    )
    task = service.create_task(question="What should Float think about next?")

    assert task["utility"] == 0.8
    assert task["uncertainty"] == 0.6
    assert task["priority"] > 0.4
    assert service.get_task(task["id"])["id"] == task["id"]

    result = service.run_task(task["id"])
    run = result["run"]
    updated = result["task"]
    assert result["status"] == "ran"
    assert run["should_surface_to_user"] is True
    assert "Claim:" in run["compact_note"]
    assert updated["run_count"] == 1
    assert updated["status"] == "open"
    assert any(call.get("session_id") == "reflection-task-scorer" for call in calls)
    reflection_call = next(
        call for call in calls if call.get("session_id") == f"reflection:{task['id']}"
    )
    reflection_context = reflection_call["context"]
    assert reflection_context.tools == []
    assert "no tool access" in reflection_context.system_prompt
    assert "child-subchat control tool" not in reflection_context.system_prompt


def test_reflection_run_writes_compact_shared_ledger_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(reflection_module, "try_ingest_text", lambda *a, **k: None)
    monkeypatch.setattr(reflection_module, "get_rag_service", lambda *a, **k: None)

    def fake_generate(prompt, **kwargs):
        if kwargs.get("response_format") == "json_object":
            return {
                "text": (
                    '{"novelty": 0.8, "usefulness": 0.8, '
                    '"uncertainty_delta": 0.5, "repetition": 0.1, '
                    '"continue": false, "should_surface_to_user": false, '
                    '"cooldown_seconds": 0}'
                )
            }
        return {
            "text": "One bounded synthesis for the Activity receipt.",
            "thought": "private chain-of-thought must not enter the work ledger",
            "metadata": {
                "provider": "test-provider",
                "model_requested": "requested-model",
                "model_received": "received-model",
                "raw_provider_result": "private provider payload",
            },
        }

    work_runs = WorkRunStore(path=tmp_path / "work-runs.sqlite3")
    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=fake_generate,
        now_fn=lambda: 1_700_000_000.0,
        work_run_store=work_runs,
    )
    task = service.create_task(
        title="Ledger-safe reflection",
        question="A private task question that must not enter Activity.",
        source_thread_id="conversation-123",
        event_id="calendar-event-456",
        patience_budget={
            "profile": "custom",
            "max_reasoning_turns": 3,
            "max_runtime_seconds": 900,
        },
        metadata={
            "ownership": {
                "message_id": "message-789",
                "parent_job_id": "parent-job",
                "parent_agent_id": "parent-agent",
            }
        },
        utility=0.8,
        uncertainty=0.7,
    )

    result = service.run_task(task["id"], force=True)
    run = result["run"]
    receipt = work_runs.get(run["id"])

    assert receipt is not None
    assert receipt["id"] == run["id"]
    assert receipt["run_id"] == run["id"]
    assert receipt["source"] == "reflection"
    assert receipt["job_id"] == task["id"]
    assert receipt["event_id"] == "calendar-event-456"
    assert receipt["summary"] == run["compact_note"]
    assert receipt["ownership"] == {
        "owner_kind": "conversation",
        "calendar_event_id": "calendar-event-456",
        "conversation_id": "conversation-123",
        "message_id": "message-789",
        "parent_job_id": "parent-job",
        "parent_agent_id": "parent-agent",
    }
    assert receipt["patience"] == {
        "stop_condition": "reflection_patience_budget",
        "max_attempts": 3,
        "max_runtime_seconds": 900,
    }
    assert receipt["execution"]["provider"] == "test-provider"
    assert receipt["execution"]["requested_model"] == "requested-model"
    assert receipt["execution"]["received_model"] == "received-model"
    assert receipt["execution"]["model"] == "received-model"
    assert receipt["execution"]["thought_trace_length"] == 1
    assert "output" not in receipt
    assert "thought" not in receipt
    assert "thought_trace" not in receipt
    assert "evaluation" not in receipt
    assert "input_context_ids" not in receipt
    assert "raw_provider_result" not in receipt["execution"]
    serialized = str(receipt)
    assert "private chain-of-thought" not in serialized
    assert "private task question" not in serialized
    assert "private provider payload" not in serialized


def test_reflection_commit_survives_work_ledger_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(reflection_module, "try_ingest_text", lambda *a, **k: None)
    monkeypatch.setattr(reflection_module, "get_rag_service", lambda *a, **k: None)

    class FailingWorkRunStore:
        def upsert(self, *args, **kwargs):
            raise OSError("ledger temporarily unavailable")

    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=lambda *a, **k: {"text": "A genuine bounded reflection."},
        work_run_store=FailingWorkRunStore(),  # type: ignore[arg-type]
    )
    task = service.create_task(
        question="Keep the canonical reflection when the ledger is busy.",
        utility=0.8,
        uncertainty=0.7,
    )

    result = service.run_task(task["id"], force=True)

    assert result["status"] == "ran"
    assert service.list_runs(task["id"])[0]["id"] == result["run"]["id"]


@pytest.mark.parametrize(
    "failure", ["empty", "exception", "error_payload", "skill_echo"]
)
def test_reflection_failure_retains_error_receipt_without_fabricated_work(
    tmp_path, monkeypatch, failure
):
    mirrored = []
    calls = []
    monkeypatch.setattr(
        reflection_module, "try_ingest_text", lambda *a, **k: mirrored.append(a)
    )
    monkeypatch.setattr(reflection_module, "get_rag_service", lambda *a, **k: None)

    def failing_generate(prompt, **kwargs):
        calls.append(kwargs)
        if failure == "exception":
            raise RuntimeError("private provider response must not enter Activity")
        if failure == "error_payload":
            return {"error": "provider unavailable", "text": "request failed"}
        if failure == "skill_echo":
            return {"text": "You said: Thought task: draft this."}
        return {"text": "   "}

    work_runs = WorkRunStore(path=tmp_path / "work-runs.sqlite3")
    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=failing_generate,
        work_run_store=work_runs,
    )
    task = service.create_task(
        question="A synthetic reflection question",
        utility=0.8,
        uncertainty=0.7,
        metadata={"proposal_kind": "skill_markdown"} if failure == "skill_echo" else {},
    )

    result = service.run_task(task["id"])
    run = result["run"]
    receipt = work_runs.get(run["id"])

    assert result["status"] == "error"
    assert run["status"] == "error"
    assert run["output"] == ""
    assert run["evaluation"] == {}
    assert result["error"]["category"] in {"provider_error", "empty_output"}
    assert result["task"]["run_count"] == 0
    assert result["task"]["status"] == "waiting"
    assert result["task"]["cooldown_until"] > result["task"]["last_attempt_at"]
    assert service.list_runs(task["id"])[0]["id"] == run["id"]
    assert receipt["status"] == "error"
    assert receipt["phase"] == "error"
    assert "private provider response" not in str(receipt)
    assert len(calls) == 1  # Failed generation must not invoke the evaluator.
    assert not mirrored
    assert service.run_task(task["id"])["status"] == "not_runnable"
    restarted = ReflectionService(
        service.config,
        llm_generate=lambda *a, **k: {"text": "# Useful retry\n\nA bounded next step."},
    )
    retry = restarted.run_task(task["id"], force=True)
    assert retry["status"] == "ran"
    assert retry["task"]["run_count"] == 1


def test_reflection_evaluator_failure_preserves_real_output_and_labels_fallback(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(reflection_module, "try_ingest_text", lambda *a, **k: None)
    monkeypatch.setattr(reflection_module, "get_rag_service", lambda *a, **k: None)

    def generate(prompt, **kwargs):
        if kwargs.get("response_format") == "json_object":
            raise RuntimeError("synthetic evaluator unavailable")
        return {"text": "A real useful reflection was generated."}

    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=generate,
    )
    task = service.create_task(
        question="Evaluate useful work", utility=0.8, uncertainty=0.7
    )
    result = service.run_task(task["id"])

    assert result["status"] == "ran"
    assert result["run"]["status"] == "complete"
    assert result["run"]["output"] == "A real useful reflection was generated."
    assert result["run"]["evaluation"]["method"] == "deterministic"
    assert result["run"]["evaluation"]["fallback_reason"] == "provider_error"
    assert result["task"]["run_count"] == 1


def test_reflection_default_provider_exception_becomes_safe_failed_run(
    tmp_path, monkeypatch
):
    from app import routes

    monkeypatch.setattr(reflection_module, "get_rag_service", lambda *a, **k: None)

    def fail(*args, **kwargs):
        raise RuntimeError("private provider transport detail")

    monkeypatch.setattr(routes.llm_service, "generate", fail)
    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")}
    )
    task = service.create_task(question="Synthetic task", utility=0.8, uncertainty=0.7)
    result = service.run_task(task["id"])

    assert result["status"] == "error"
    assert result["error"]["category"] == "provider_error"
    assert "private provider transport detail" not in str(result)


@pytest.mark.parametrize(
    "response",
    [
        "Useful synthesis.",
        {"message": {"content": "Useful synthesis."}},
        {"text": "", "content": "Useful synthesis."},
    ],
)
def test_reflection_accepts_supported_alternative_success_output(
    tmp_path, monkeypatch, response
):
    monkeypatch.setattr(reflection_module, "try_ingest_text", lambda *a, **k: None)
    monkeypatch.setattr(reflection_module, "get_rag_service", lambda *a, **k: None)
    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=lambda *a, **k: response,
    )
    task = service.create_task(question="Synthetic task", utility=0.8, uncertainty=0.7)
    result = service.run_task(task["id"])

    assert result["status"] == "ran"
    assert result["run"]["output"] == "Useful synthesis."
    assert result["run"]["evaluation"]["method"] == "deterministic"


def test_reflection_initial_judgment_obeys_budget_and_labels_fallback(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(reflection_module, "try_ingest_text", lambda *a, **k: None)
    calls = []

    def fail(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        raise RuntimeError("synthetic scoring unavailable")

    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=fail,
    )
    task = service.create_task(
        question="A candidate reflection",
        metadata={"optional_context": "synthetic context " * 2000},
        patience_budget={"max_context_tokens": 512, "max_output_tokens": 16},
    )

    assert len(calls) == 1
    assert calls[0]["output_token_limit"] == 16
    assert calls[0]["context"].metadata["patience_budget"]["input_tokens"] + 16 <= 512
    assert task["initial_judgment"]["method"] == "deterministic"
    assert task["initial_judgment"]["reason"] == "provider_error"
    assert task["utility"] == 0.55
    constrained = service.create_task(
        question="Required task instructions cannot fit",
        patience_budget={"max_context_tokens": 1},
    )
    assert len(calls) == 1
    assert constrained["initial_judgment"]["reason"] == "context_budget_exceeded"


@pytest.mark.parametrize(
    "context_text", ["A repeated synthetic context. ", "多言語の合成文脈🌊 "]
)
def test_reflection_context_and_output_caps_apply_to_generation_and_evaluation(
    tmp_path, monkeypatch, context_text
):
    from app.utils.tokenizer import CustomTokenizer

    monkeypatch.setattr(reflection_module, "try_ingest_text", lambda *a, **k: None)
    calls = []

    def generate(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return {"text": "Useful reflection."}

    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=generate,
    )
    monkeypatch.setattr(
        service,
        "_retrieve_context",
        lambda task: {
            "text": context_text * 2000,
            "input_context_ids": [],
        },
    )
    task = service.create_task(
        question="Preserve this question.",
        utility=0.8,
        uncertainty=0.7,
        patience_budget={
            "max_context_tokens": 512,
            "max_output_tokens": 16,
            "reserve_output_tokens": 32,
        },
    )
    result = service.run_task(task["id"])

    assert result["status"] == "ran"
    assert len(calls) == 2
    tokenizer = CustomTokenizer()
    for call in calls:
        assert call["output_token_limit"] == 16
        count = len(tokenizer.encode(call["context"].system_prompt)) + len(
            tokenizer.encode(call["prompt"])
        )
        assert count + 24 + 32 <= 512
        assert "Preserve this question." in call["prompt"]
    assert result["run"]["generation"]["patience_budget"]["context_truncated"] is True


def test_reflection_too_small_context_budget_fails_before_provider_dispatch(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(reflection_module, "get_rag_service", lambda *a, **k: None)
    calls = []
    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=lambda *a, **k: calls.append(a),
    )
    task = service.create_task(
        question="Keep required instructions.",
        utility=0.8,
        uncertainty=0.7,
        patience_budget={"max_context_tokens": 1},
    )
    result = service.run_task(task["id"])

    assert result["status"] == "error"
    assert result["error"]["category"] == "context_budget_exceeded"
    assert not calls
    assert result["task"]["run_count"] == 0


def test_reflection_runtime_budget_stops_before_evaluation_but_keeps_real_output(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(reflection_module, "try_ingest_text", lambda *a, **k: None)
    monkeypatch.setattr(reflection_module, "get_rag_service", lambda *a, **k: None)
    current = [1000.0]
    calls = []

    def generate(prompt, **kwargs):
        calls.append(kwargs)
        current[0] += 2
        return {"text": "Useful output returned after the cooperative deadline."}

    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=generate,
        now_fn=lambda: current[0],
    )
    task = service.create_task(
        question="One timed reflection",
        utility=0.8,
        uncertainty=0.7,
        patience_budget={"max_runtime_seconds": 1},
    )
    result = service.run_task(task["id"])

    assert result["status"] == "ran"
    assert len(calls) == 1
    assert result["run"]["output"].startswith("Useful output")
    assert result["run"]["evaluation"]["fallback_reason"] == "runtime_budget_reached"
    assert (
        result["run"]["generation"]["termination_category"] == "runtime_budget_reached"
    )
    assert result["run"]["started_at"] == 1000.0
    assert result["run"]["finished_at"] == 1002.0


def test_reflection_context_retrieval_can_exhaust_cooperative_runtime_budget(
    tmp_path, monkeypatch
):
    current = [1000.0]
    calls = []
    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=lambda *a, **k: calls.append(a),
        now_fn=lambda: current[0],
    )

    def retrieve(task):
        current[0] += 2
        return {"text": "Synthetic context", "input_context_ids": []}

    monkeypatch.setattr(service, "_retrieve_context", retrieve)
    task = service.create_task(
        question="Runtime-limited reflection",
        utility=0.8,
        uncertainty=0.7,
        patience_budget={"max_runtime_seconds": 1},
    )
    result = service.run_task(task["id"])

    assert result["status"] == "error"
    assert result["error"]["category"] == "runtime_budget_reached"
    assert not calls
    assert result["task"]["run_count"] == 0


def test_reflection_priority_echo_damping_and_seeded_sampling(tmp_path, monkeypatch):
    monkeypatch.setattr(reflection_module, "try_ingest_text", lambda *a, **k: None)
    monkeypatch.setattr(reflection_module, "get_rag_service", lambda *a, **k: None)
    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=lambda *a, **k: None,
        now_fn=lambda: 1_700_000_000.0,
    )
    low_echo = service.create_task(
        question="User-originated question",
        utility=0.8,
        uncertainty=0.7,
    )
    high_echo = service.create_task(
        question="Self-echo question",
        utility=0.8,
        uncertainty=0.7,
    )
    low_echo["user_recurrence"] = 1.0
    low_echo["self_recurrence"] = 0.0
    high_echo["user_recurrence"] = 0.0
    high_echo["self_recurrence"] = 1.0
    service._refresh_task_scores(low_echo)
    service._refresh_task_scores(high_echo)

    assert low_echo["priority"] > high_echo["priority"]
    assert high_echo["self_echo_ratio"] == 1.0
    first = service._sample_task([low_echo, high_echo], seed=42)
    second = service._sample_task([low_echo, high_echo], seed=42)
    assert first["id"] == second["id"]


def test_reflection_hard_stop_and_json_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(reflection_module, "try_ingest_text", lambda *a, **k: None)
    monkeypatch.setattr(reflection_module, "get_rag_service", lambda *a, **k: None)

    def fake_generate(prompt, **kwargs):
        if kwargs.get("response_format") == "json_object":
            return {"text": "not json"}
        return {"text": "short"}

    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=fake_generate,
    )
    task = service.create_task(
        question="A bounded low-value question",
        utility=0.3,
        uncertainty=0.3,
        patience=0,
    )
    result = service.run_task(task["id"])

    assert result["run"]["novelty"] < 0.35
    assert result["task"]["status"] == "cooling"
    assert result["task"]["run_count"] == 1
    blocked = service.run_task(task["id"])
    assert blocked["status"] == "not_runnable"


def test_reflection_patience_budget_counts_reasoning_turns(tmp_path, monkeypatch):
    monkeypatch.setattr(reflection_module, "try_ingest_text", lambda *a, **k: None)
    monkeypatch.setattr(reflection_module, "get_rag_service", lambda *a, **k: None)

    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=lambda *a, **k: None,
    )
    task = service.create_task(
        question="How much room should this reflection get?",
        patience=0,
        patience_budget={
            "profile": "custom",
            "max_reasoning_turns": 5,
            "max_runtime_seconds": 900,
            "max_context_tokens": 24000,
            "reserve_output_tokens": 1200,
        },
        utility=0.8,
        uncertainty=0.8,
    )

    assert task["patience"] == 0
    assert task["patience_budget"]["profile"] == "custom"
    assert task["patience_budget"]["max_reasoning_turns"] == 5
    assert service._depth_budget(task) == 5


def test_scheduler_tick_seeds_safe_memory_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(reflection_module, "try_ingest_text", lambda *a, **k: None)
    monkeypatch.setattr(reflection_module, "get_rag_service", lambda *a, **k: None)
    monkeypatch.setattr(
        reflection_module.conversation_store,
        "list_conversations",
        lambda include_metadata=False: [],
    )

    class DummyMemoryManager:
        def iter_items(self, **kwargs):
            return [
                (
                    "pinned-idea",
                    {
                        "value": "Pinned idea for reflection.",
                        "pinned": True,
                        "importance": 0.8,
                        "sensitivity": "personal",
                    },
                ),
                (
                    "secret-idea",
                    {
                        "value": "Do not reflect on this.",
                        "pinned": True,
                        "importance": 1.0,
                        "sensitivity": "secret",
                    },
                ),
            ]

        def get_item(self, key, **kwargs):
            return {
                "value": "Pinned idea for reflection.",
                "sensitivity": "personal",
            }

    def fake_generate(prompt, **kwargs):
        if kwargs.get("response_format") == "json_object":
            return {
                "text": (
                    '{"novelty": 0.75, "usefulness": 0.70, '
                    '"uncertainty_delta": 0.40, "repetition": 0.10, '
                    '"continue": false, "should_surface_to_user": false, '
                    '"cooldown_seconds": 0}'
                )
            }
        return {
            "text": (
                "Current best synthesis: the memory has one clear follow-up.\n"
                "What changed: the follow-up was separated from the memory.\n"
                "Remaining uncertainty: low.\n"
                "Another pass: no.\n"
                "Surface to user: no."
            )
        }

    memory_tools.set_manager(DummyMemoryManager())
    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=fake_generate,
    )

    tick = service.scheduler_tick(mode="manual", seed=7)

    assert tick["metadata"]["seeded_candidates"] == 1
    tasks = service.list_tasks(include_runs=True)
    assert len(tasks) == 1
    assert tasks[0]["memory_keys"] == ["pinned-idea"]
    assert tasks[0]["runs"]


def test_memory_remember_can_queue_reflection(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_tools, "_vectorize_memory_entry", lambda *a, **k: None)

    class DummyMemoryManager:
        def __init__(self):
            self.items = {}

        def get_item(self, key, **kwargs):
            return self.items.get(key)

        def upsert_item(
            self,
            key,
            value,
            importance,
            created_at,
            updated_at,
            expires_at,
            sensitivity,
            hint,
            **kwargs,
        ):
            self.items[key] = {
                "key": key,
                "value": value,
                "importance": importance,
                "sensitivity": sensitivity or "mundane",
                "hint": hint,
                "pruned_at": None,
                **kwargs,
            }

        def update_item_fields(self, key, updates):
            self.items.setdefault(key, {}).update(updates)

    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=lambda *a, **k: None,
    )
    reflection_tools.set_reflection_service(service)
    memory_tools.set_manager(DummyMemoryManager())
    args = {
        "key": "idea",
        "value": "Test memory worth a later reflection.",
        "reflect_after_save": True,
        "reflection_prompt": "Find one follow-up angle.",
    }
    signature = generate_signature("tester", "remember", args)

    result = memory_tools.remember(user="tester", signature=signature, **args)

    assert "reflection queued" in result
    tasks = service.list_tasks(status="open")
    assert len(tasks) == 1
    assert tasks[0]["memory_keys"] == ["idea"]


def test_skill_markdown_proposal_preserves_format_and_marks_model_context(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(reflection_module, "try_ingest_text", lambda *a, **k: None)
    monkeypatch.setattr(reflection_module, "get_rag_service", lambda *a, **k: None)
    calls = []

    def fake_generate(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        if kwargs.get("response_format") == "json_object":
            return {
                "text": (
                    '{"novelty": 0.8, "usefulness": 0.8, '
                    '"uncertainty_delta": 0.5, "repetition": 0.1, '
                    '"continue": false, "should_surface_to_user": false, '
                    '"cooldown_seconds": 0}'
                )
            }
        return {
            "text": "# Incident Triage\n\n## Core loop\n\n- Inspect impact first.",
            "thought": "Plan the operational sections.",
            "thought_trace": [{"index": 0, "text": "Plan the operational sections."}],
            "metadata": {
                "provider": "tinker",
                "model_requested": "thinkingmachines/Inkling",
                "model_received": "thinkingmachines/Inkling",
            },
        }

    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=fake_generate,
    )
    task = service.create_task(
        title="Skill proposal: incident_triage",
        question="Draft skill markdown.",
        source="user",
        metadata={
            "proposal_kind": "skill_markdown",
            "skill_id": "incident_triage",
            "requires_user_save": True,
            "requested_model": "thinkingmachines/Inkling",
        },
        utility=0.8,
        uncertainty=0.6,
    )

    result = service.run_task(task["id"], force=True)

    assert result["run"]["output"] == (
        "# Incident Triage\n\n## Core loop\n\n- Inspect impact first."
    )
    assert result["run"]["thought_trace"] == [
        {"index": 0, "text": "Plan the operational sections."}
    ]
    assert result["run"]["thought_trace_count"] == 1
    assert result["run"]["generation"]["provider"] == "tinker"
    assert result["run"]["generation"]["requested_model"] == (
        "thinkingmachines/Inkling"
    )
    draft_call = next(call for call in calls if call.get("response_format") is None)
    assert "Return markdown only" in draft_call["context"].system_prompt
    assert draft_call["context"].metadata["proposal_kind"] == "skill_markdown"
    assert draft_call["context"].metadata["proposal_target"] == "incident_triage"
    assert draft_call["model"] == "thinkingmachines/Inkling"


def test_skill_markdown_proposal_rejects_prompt_echo_and_unwraps_code_fence():
    assert (
        reflection_module._skill_markdown_proposal(
            "You said: Thought task: draft this. # Incident Triage"
        )
        == ""
    )
    assert reflection_module._skill_markdown_proposal("plain prose only") == ""
    assert (
        reflection_module._skill_markdown_proposal(
            "```markdown\n# Incident Triage\n\n- Inspect impact first.\n```"
        )
        == "# Incident Triage\n\n- Inspect impact first."
    )
