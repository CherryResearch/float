from __future__ import annotations

from app.services.background_autonomy_service import BackgroundAutonomyService
from app.services.reflection_service import ReflectionService


def _fake_reflection_generate(prompt, **kwargs):
    if kwargs.get("session_id") == "reflection-task-scorer":
        return {"text": '{"utility": 0.82, "uncertainty": 0.74}'}
    if kwargs.get("response_format") == "json_object":
        return {
            "text": (
                '{"novelty": 0.88, "usefulness": 0.80, '
                '"uncertainty_delta": 0.42, "repetition": 0.05, '
                '"continue": false, "should_surface_to_user": false, '
                '"cooldown_seconds": 0}'
            )
        }
    return {
        "text": (
            "Current best synthesis: keep this background thread bounded.\n"
            "What changed: the next action is clearer.\n"
            "Remaining uncertainty: medium.\n"
            "Another pass: no.\n"
            "Surface to user: no."
        )
    }


def _services(tmp_path):
    reflection = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=_fake_reflection_generate,
    )
    autonomy = BackgroundAutonomyService(
        {
            "background_autonomy_store_path": str(tmp_path / "autonomy.json"),
            "background_autonomy_enabled": False,
            "background_autonomy_sandbox_processes": True,
            "background_autonomy_mode": "overnight",
            "background_autonomy_max_reflections_per_tick": 1,
            "background_autonomy_max_runtime_seconds": 1800,
            "background_autonomy_satisfied_threshold": 0.80,
            "background_autonomy_basic_tick_count": 2,
            "background_autonomy_basic_tick_seconds": 300,
            "background_autonomy_min_priority": 0.01,
        },
        reflection_service=reflection,
    )
    return reflection, autonomy


def test_background_autonomy_status_exposes_attention_snapshot(tmp_path):
    reflection, autonomy = _services(tmp_path)
    task = reflection.create_task(
        question="Which unresolved background thread deserves a pass?",
        title="Autonomy focus",
        source="user",
        utility=0.80,
        uncertainty=0.70,
    )

    status = autonomy.status()

    assert status["enabled"] is False
    assert status["sandbox_processes"] is True
    assert status["capabilities"]["sandbox_background_processes"] is True
    assert status["configured_mode"] == "overnight"
    assert status["max_runtime_seconds"] == 1800
    assert status["basic_tick_count"] == 2
    assert status["basic_tick_seconds"] == 300
    assert status["satisfied_threshold"] == 0.80
    assert status["reflection"]["candidate_count"] == 1
    top = status["reflection"]["top_attention"][0]
    assert top["id"] == task["id"]
    assert top["runnable"] is True
    assert top["attention"]["short_term_importance"] == top["priority"]
    assert "long_term_importance" in top["attention"]


def test_background_autonomy_dry_run_plans_without_running(tmp_path):
    reflection, autonomy = _services(tmp_path)
    task = reflection.create_task(
        question="Should this be processed later?",
        title="Dry run task",
        source="user",
        utility=0.75,
        uncertainty=0.60,
    )

    result = autonomy.tick(dry_run=True, seed=4)

    assert result["status"] == "planned"
    assert result["dry_run"] is True
    assert result["ran_reflections"] == 0
    assert reflection.get_task(task["id"])["run_count"] == 0
    assert autonomy.status()["state"]["last_status"] == "planned"


def test_background_autonomy_tick_runs_one_bounded_reflection(tmp_path):
    reflection, autonomy = _services(tmp_path)
    task = reflection.create_task(
        question="Run one background pass.",
        title="Runnable task",
        source="user",
        utility=0.90,
        uncertainty=0.80,
    )

    result = autonomy.tick(seed=1)

    assert result["status"] == "ran"
    assert result["ran_reflections"] == 1
    updated = reflection.get_task(task["id"])
    assert updated["run_count"] == 1
    assert updated["status"] in {"resolved", "cooling", "open", "waiting"}
    assert autonomy.status()["state"]["last_result"]["ran_reflections"] == 1


def test_background_autonomy_routine_mode_requires_enable_or_force(tmp_path):
    _reflection, autonomy = _services(tmp_path)

    disabled = autonomy.tick(mode="idle")
    forced = autonomy.tick(mode="idle", force=True, dry_run=True)

    assert disabled["status"] == "disabled"
    assert forced["status"] == "planned"


def test_background_autonomy_extended_mode_stops_when_satisfied(tmp_path):
    reflection, autonomy = _services(tmp_path)
    autonomy.update_config(
        {
            **autonomy.config,
            "background_autonomy_enabled": True,
            "background_autonomy_mode": "extended",
            "background_autonomy_satisfied_threshold": 0.75,
        },
        reset_session=True,
    )
    reflection.create_task(
        question="Run until the result is useful enough.",
        title="Satisfied task",
        source="user",
        utility=0.90,
        uncertainty=0.80,
    )

    result = autonomy.tick(mode="extended", seed=1)

    assert result["status"] == "ran"
    assert result["satisfied"] is True
    assert result["satisfaction_score"] >= 0.75
    assert result["stop_reason"] == "satisfied_threshold"
    assert autonomy.should_stop_session("extended") is True
    assert autonomy.status()["session"]["stop_reason"] == "satisfied_threshold"
