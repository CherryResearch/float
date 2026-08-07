import json
from pathlib import Path

import pytest

from scripts import prompt_restart_process_smoke as smoke


def _successful_contract():
    signal = {
        "checkpoint_persisted": True,
        "receipt_persisted": True,
        "run_id": "run-one",
        "receipt_id": "receipt-one",
        "session_id": "session-one",
        "assistant_message_id": "message-one",
        "placeholder_count": 1,
        "lease_seconds": 90.0,
        "lease_expires_at": 100.0,
    }
    resumed = {
        "run_id": "run-one",
        "receipt_id": "receipt-one",
        "session_id": "session-one",
        "assistant_message_id": "message-one",
        "attempts": [
            {
                "id": "attempt-one",
                "attempt_number": 1,
                "status": "interrupted_unknown",
                "retry_reason_code": "worker_restart",
            },
            {
                "id": "attempt-two",
                "attempt_number": 2,
                "status": "complete",
                "retry_of_attempt_id": "attempt-one",
            },
        ],
        "effect_count": 0,
        "effects": [],
        "tool_invocation_count": 0,
        "canonical_output_count": 1,
        "canonical_output_complete": True,
        "canonical_output_text": smoke.FINAL_TEXT,
        "user_message_count": 1,
        "history_receipt_ids": ["receipt-one"],
        "due_scan_completed_count": 1,
        "action_status": "prompted",
        "provider_calls": [
            {
                "tool_argument_count": 0,
                "context_tool_count": 0,
                "recovery_envelope_count": 1,
                "mentions_assistant_message_id": True,
            }
        ],
    }
    return signal, resumed


def test_help_describes_isolation_and_real_lease_wait():
    help_text = smoke.build_parser().format_help()

    assert "true-process" in help_text
    assert "deterministic fake providers" in help_text
    assert "little over 90 seconds" in help_text
    assert "--retain-artifacts" in help_text
    assert "--_child-mode" not in help_text


def test_isolated_environment_routes_every_mutable_store_under_temp_root(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-child")
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path / "wrong-conversations"))
    data_dir = tmp_path / "task-owned-data"

    env = smoke._isolated_environment(data_dir)

    assert env["FLOAT_DATA_DIR"] == str(data_dir.resolve())
    assert "OPENAI_API_KEY" not in env
    for key in (
        "FLOAT_CONV_DIR",
        "FLOAT_CALENDAR_DIR",
        "FLOAT_MEMORY_FILE",
        "FLOAT_REFLECTION_STORE",
        "FLOAT_WORK_RUN_STORE",
        "FLOAT_ENV_FILE",
    ):
        assert Path(env[key]).resolve().is_relative_to(data_dir.resolve())


def test_wait_until_epoch_uses_elapsed_wall_clock_without_state_mutation():
    clock = [10.0]

    def now():
        return clock[0]

    def sleep(duration):
        clock[0] += duration

    waited = smoke._wait_until_epoch(10.75, now_fn=now, sleep_fn=sleep)

    assert waited == pytest.approx(0.75)
    assert clock[0] == pytest.approx(10.75)


def test_main_removes_task_owned_artifacts_by_default(tmp_path, monkeypatch, capsys):
    artifact_dir = tmp_path / "float-prompt-restart-smoke-test"
    artifact_dir.mkdir()
    (artifact_dir / "evidence.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        smoke,
        "_create_artifact_dir",
        lambda _args: (artifact_dir, False),
    )
    monkeypatch.setattr(
        smoke,
        "_orchestrate",
        lambda _args, _artifact_dir: {"ok": True},
    )

    assert smoke.main([]) == 0

    assert not artifact_dir.exists()
    output = json.loads(capsys.readouterr().out)
    assert output == {"artifacts_retained": False, "ok": True}


def test_restart_contract_accepts_one_stable_output_and_two_provider_attempts():
    signal, resumed = _successful_contract()

    evidence = smoke._validate_restart_contract(
        signal,
        resumed,
        killed_return_code=-9,
        resume_started_at=101.0,
        stale_margin_seconds=1.0,
        waited_after_kill_seconds=88.0,
    )

    assert evidence["ok"] is True
    assert evidence["provider_attempt_statuses"] == [
        "interrupted_unknown",
        "complete",
    ]
    assert evidence["canonical_output_count"] == 1
    assert evidence["effect_count"] == 0
    assert evidence["timestamps_mutated"] is False


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("run_id", "run-two", "run_id changed"),
        ("canonical_output_count", 2, "canonical assistant output was duplicated"),
        ("effect_count", 1, "recorded an effect"),
    ],
)
def test_restart_contract_rejects_identity_output_or_effect_forks(field, value, match):
    signal, resumed = _successful_contract()
    resumed[field] = value

    with pytest.raises(smoke.SmokeFailure, match=match):
        smoke._validate_restart_contract(
            signal,
            resumed,
            killed_return_code=1,
            resume_started_at=101.0,
            stale_margin_seconds=1.0,
            waited_after_kill_seconds=88.0,
        )
