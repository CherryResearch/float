from __future__ import annotations

from app.services import reflection_service as reflection_module
from app.services.reflection_service import ReflectionService
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
    assert "child-subchat control tool" in reflection_call["context"].system_prompt


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
