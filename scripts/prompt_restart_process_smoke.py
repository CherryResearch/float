"""Exercise prompt-only Calendar recovery across an abrupt process restart.

The parent process never imports Float. Each child receives an explicit, isolated
``FLOAT_DATA_DIR`` (and explicit store paths beneath it), then uses the normal
Calendar due scan. The first fake provider blocks after the durable prompt
checkpoint; the parent kills that process and waits for the real lease to expire
before a fresh child resumes with a deterministic provider.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
EVENT_ID = "prompt-restart-process-smoke"
ACTION_ID = "prompt-restart-process-action"
SESSION_ID = "prompt-restart-process-session"
PROMPT_TEXT = "Return the deterministic prompt restart smoke result."
FINAL_TEXT = "prompt-restart-process-smoke-ok"
MIN_RUNTIME_SECONDS = 30.0
LEASE_PADDING_SECONDS = 60.0
DEFAULT_STALE_MARGIN_SECONDS = 1.5


class SmokeFailure(RuntimeError):
    """The isolated restart smoke did not satisfy its durability contract."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run an isolated true-process smoke for prompt-only Calendar restart "
            "recovery. The smoke uses deterministic fake providers, performs no "
            "tool or external-provider call, and normally takes a little over 90 "
            "seconds because it waits for the real minimum stale lease."
        )
    )
    parser.add_argument(
        "--retain-artifacts",
        action="store_true",
        help="Keep the isolated data root, child output, and JSON evidence.",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        help=(
            "Create the retained run directory beneath this path. Supplying this "
            "option implies --retain-artifacts."
        ),
    )
    parser.add_argument(
        "--signal-timeout-seconds",
        type=float,
        default=45.0,
        help="Maximum time to wait for the blocking provider checkpoint signal.",
    )
    parser.add_argument(
        "--resume-timeout-seconds",
        type=float,
        default=75.0,
        help="Maximum time to wait for the fresh recovery child to finish.",
    )
    parser.add_argument(
        "--stale-margin-seconds",
        type=float,
        default=DEFAULT_STALE_MARGIN_SECONDS,
        help="Wall-clock margin added after the runner-reported lease expiry.",
    )
    parser.add_argument(
        "--_child-mode",
        choices=("seed", "block", "resume"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--_data-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--_signal-path", type=Path, help=argparse.SUPPRESS)
    return parser


def _require(condition: Any, detail: str) -> None:
    if not condition:
        raise SmokeFailure(detail)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SmokeFailure(f"could not read JSON evidence from {path.name}") from exc
    if not isinstance(payload, dict):
        raise SmokeFailure(f"JSON evidence in {path.name} is not an object")
    return payload


def _parse_child_json(output: str, *, label: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    raise SmokeFailure(f"{label} child emitted no JSON object")


def _isolated_environment(data_dir: Path) -> dict[str, str]:
    """Return a child environment whose mutable Float paths stay task-owned."""

    root = data_dir.resolve()
    databases = root / "databases"
    env = dict(os.environ)
    env.update(
        {
            "FLOAT_DATA_DIR": str(root),
            # An explicit conversation path prevents the legacy repo transcript
            # migration probe from running during module import.
            "FLOAT_CONV_DIR": str(root / "conversations"),
            "FLOAT_CALENDAR_DIR": str(databases / "calendar_events"),
            "FLOAT_MEMORY_FILE": str(databases / "memory.sqlite3"),
            "FLOAT_REFLECTION_STORE": str(databases / "reflections.sqlite3"),
            "FLOAT_WORK_RUN_STORE": str(databases / "work_runs.sqlite3"),
            "FLOAT_ENV_FILE": str(root / "isolated.env"),
            "FLOAT_DEV_MODE": "false",
            "FLOAT_TELEMETRY_ENABLED": "false",
            "FLOAT_METRICS_ENABLED": "false",
        }
    )
    # The smoke replaces the provider before dispatch, and dropping common API
    # credentials makes an accidental external call fail closed as well.
    for key in (
        "ANTHROPIC_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
    ):
        env.pop(key, None)
    return env


def _child_command(
    mode: str,
    *,
    data_dir: Path,
    signal_path: Path | None = None,
) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_child-mode",
        mode,
        "--_data-dir",
        str(data_dir.resolve()),
    ]
    if signal_path is not None:
        command.extend(("--_signal-path", str(signal_path.resolve())))
    return command


def _run_child(
    mode: str,
    *,
    data_dir: Path,
    timeout: float,
    signal_path: Path | None = None,
) -> tuple[dict[str, Any], subprocess.CompletedProcess[str]]:
    try:
        completed = subprocess.run(
            _child_command(mode, data_dir=data_dir, signal_path=signal_path),
            cwd=REPO_ROOT,
            env=_isolated_environment(data_dir),
            text=True,
            capture_output=True,
            timeout=max(1.0, timeout),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SmokeFailure(
            f"{mode} child exceeded its {timeout:g}-second limit"
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-1500:]
        raise SmokeFailure(
            f"{mode} child exited {completed.returncode}"
            + (f": {detail}" if detail else "")
        )
    return _parse_child_json(completed.stdout, label=mode), completed


def _wait_for_signal(
    path: Path,
    process: subprocess.Popen[str],
    *,
    timeout: float,
    now_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    deadline = now_fn() + max(0.1, timeout)
    while now_fn() < deadline:
        if path.exists():
            return _read_json(path)
        return_code = process.poll()
        if return_code is not None:
            raise SmokeFailure(
                f"blocking child exited {return_code} before checkpoint signal"
            )
        sleep_fn(0.05)
    raise SmokeFailure(f"blocking provider did not signal within {timeout:g} seconds")


def _wait_until_epoch(
    target_epoch: float,
    *,
    now_fn: Callable[[], float] = time.time,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> float:
    """Wait for an actual wall-clock boundary without changing stored timestamps."""

    started = now_fn()
    while True:
        remaining = target_epoch - now_fn()
        if remaining <= 0:
            break
        sleep_fn(min(0.25, remaining))
    return max(0.0, now_fn() - started)


def _create_artifact_dir(args: argparse.Namespace) -> tuple[Path, bool]:
    retain = bool(args.retain_artifacts or args.artifact_root)
    if args.artifact_root:
        root = args.artifact_root.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        path = Path(
            tempfile.mkdtemp(
                prefix="prompt-restart-smoke-",
                dir=str(root),
            )
        )
    else:
        path = Path(tempfile.mkdtemp(prefix="float-prompt-restart-smoke-"))
    return path.resolve(), retain


def _validate_restart_contract(
    signal: Mapping[str, Any],
    resumed: Mapping[str, Any],
    *,
    killed_return_code: int,
    resume_started_at: float,
    stale_margin_seconds: float,
    waited_after_kill_seconds: float,
) -> dict[str, Any]:
    identity_keys = ("run_id", "receipt_id", "session_id", "assistant_message_id")
    for key in identity_keys:
        _require(bool(signal.get(key)), f"checkpoint signal is missing {key}")
        _require(
            signal.get(key) == resumed.get(key),
            f"{key} changed across the process restart",
        )

    lease_expires_at = float(signal.get("lease_expires_at") or 0.0)
    lease_seconds = float(signal.get("lease_seconds") or 0.0)
    _require(
        lease_seconds >= MIN_RUNTIME_SECONDS + LEASE_PADDING_SECONDS,
        "lease was shortened",
    )
    _require(
        resume_started_at > lease_expires_at,
        "fresh child started before the real stale-lease boundary",
    )
    _require(killed_return_code != 0, "blocking child was not abruptly terminated")
    _require(
        signal.get("checkpoint_persisted") is True, "prompt checkpoint was not durable"
    )
    _require(signal.get("receipt_persisted") is True, "running receipt was not durable")
    _require(
        signal.get("placeholder_count") == 1, "assistant placeholder was not canonical"
    )

    attempts = resumed.get("attempts")
    _require(isinstance(attempts, list), "provider attempt evidence is missing")
    statuses = [item.get("status") for item in attempts if isinstance(item, Mapping)]
    numbers = [
        item.get("attempt_number") for item in attempts if isinstance(item, Mapping)
    ]
    _require(
        statuses == ["interrupted_unknown", "complete"],
        "unexpected provider attempt states",
    )
    _require(numbers == [1, 2], "provider attempt numbers were not monotonic")
    _require(
        attempts[0].get("retry_reason_code") == "worker_restart",
        "interrupted provider attempt did not record worker_restart",
    )
    _require(
        attempts[1].get("retry_of_attempt_id") == attempts[0].get("id"),
        "replacement provider attempt does not reference the interrupted attempt",
    )
    _require(resumed.get("effect_count") == 0, "prompt-only restart recorded an effect")
    _require(
        resumed.get("effects") == [], "prompt-only restart retained effect entries"
    )
    _require(
        resumed.get("tool_invocation_count") == 0, "prompt-only restart invoked a tool"
    )
    _require(
        resumed.get("canonical_output_count") == 1,
        "canonical assistant output was duplicated",
    )
    _require(
        resumed.get("canonical_output_complete") is True,
        "canonical output is not complete",
    )
    _require(
        resumed.get("canonical_output_text") == FINAL_TEXT,
        "canonical output text changed",
    )
    _require(
        resumed.get("user_message_count") == 1, "scheduled user entry was duplicated"
    )
    _require(
        resumed.get("history_receipt_ids") == [signal["receipt_id"]],
        "receipt identity forked",
    )
    _require(
        resumed.get("due_scan_completed_count") == 1,
        "fresh due scan did not complete one prompt",
    )
    _require(resumed.get("action_status") == "prompted", "prompt action did not finish")

    provider_calls = resumed.get("provider_calls")
    _require(
        isinstance(provider_calls, list) and len(provider_calls) == 1,
        "final fake provider call count was not one",
    )
    final_call = provider_calls[0]
    _require(
        final_call.get("tool_argument_count") == 0, "provider received tool arguments"
    )
    _require(
        final_call.get("context_tool_count") == 0, "provider context exposed tools"
    )
    _require(
        final_call.get("recovery_envelope_count") == 1,
        "recovery envelope was missing or duplicated",
    )
    _require(
        final_call.get("mentions_assistant_message_id") is True,
        "recovery envelope lost the stable assistant id",
    )

    return {
        "ok": True,
        "event_id": EVENT_ID,
        "action_id": ACTION_ID,
        "run_id": signal["run_id"],
        "receipt_id": signal["receipt_id"],
        "session_id": signal["session_id"],
        "assistant_message_id": signal["assistant_message_id"],
        "canonical_output_count": resumed["canonical_output_count"],
        "provider_attempt_statuses": statuses,
        "provider_attempt_numbers": numbers,
        "effect_count": resumed["effect_count"],
        "tool_invocation_count": resumed["tool_invocation_count"],
        "lease_seconds": lease_seconds,
        "stale_margin_seconds": stale_margin_seconds,
        "waited_after_kill_seconds": round(waited_after_kill_seconds, 3),
        "timestamps_mutated": False,
        "blocking_child_return_code": killed_return_code,
    }


def _persist_process_output(
    artifact_dir: Path,
    label: str,
    completed: subprocess.CompletedProcess[str],
) -> None:
    (artifact_dir / f"{label}.stdout.txt").write_text(
        completed.stdout or "", encoding="utf-8"
    )
    (artifact_dir / f"{label}.stderr.txt").write_text(
        completed.stderr or "", encoding="utf-8"
    )


def _orchestrate(args: argparse.Namespace, artifact_dir: Path) -> dict[str, Any]:
    data_dir = artifact_dir / "float-data"
    signal_path = artifact_dir / "checkpoint-signal.json"
    data_dir.mkdir(parents=True, exist_ok=False)

    seeded, seed_process = _run_child(
        "seed",
        data_dir=data_dir,
        timeout=30.0,
    )
    _persist_process_output(artifact_dir, "seed", seed_process)
    _require(seeded.get("seeded") is True, "seed child did not save the event")

    block_stdout_path = artifact_dir / "block.stdout.txt"
    block_stderr_path = artifact_dir / "block.stderr.txt"
    block_stdout = block_stdout_path.open("w", encoding="utf-8")
    block_stderr = block_stderr_path.open("w", encoding="utf-8")
    blocking_process: subprocess.Popen[str] | None = None
    try:
        blocking_process = subprocess.Popen(
            _child_command("block", data_dir=data_dir, signal_path=signal_path),
            cwd=REPO_ROOT,
            env=_isolated_environment(data_dir),
            text=True,
            stdout=block_stdout,
            stderr=block_stderr,
        )
        signal = _wait_for_signal(
            signal_path,
            blocking_process,
            timeout=max(1.0, args.signal_timeout_seconds),
        )
        blocking_process.kill()
        try:
            killed_return_code = blocking_process.wait(timeout=10.0)
        except subprocess.TimeoutExpired as exc:
            raise SmokeFailure("blocking child did not terminate after kill") from exc
    finally:
        if blocking_process is not None and blocking_process.poll() is None:
            blocking_process.kill()
            blocking_process.wait(timeout=10.0)
        block_stdout.close()
        block_stderr.close()

    killed_at = time.time()
    lease_expires_at = float(signal.get("lease_expires_at") or 0.0)
    stale_margin = max(0.25, float(args.stale_margin_seconds))
    _require(lease_expires_at > killed_at, "checkpoint signal carried an expired lease")
    waited = _wait_until_epoch(lease_expires_at + stale_margin)

    resume_started_at = time.time()
    resumed, resume_process = _run_child(
        "resume",
        data_dir=data_dir,
        timeout=max(1.0, args.resume_timeout_seconds),
    )
    _persist_process_output(artifact_dir, "resume", resume_process)
    evidence = _validate_restart_contract(
        signal,
        resumed,
        killed_return_code=killed_return_code,
        resume_started_at=resume_started_at,
        stale_margin_seconds=stale_margin,
        waited_after_kill_seconds=waited,
    )
    _write_json_atomic(artifact_dir / "evidence.json", evidence)
    return evidence


def _configure_child(data_dir: Path) -> Path:
    resolved = data_dir.expanduser().resolve()
    expected = Path(os.environ.get("FLOAT_DATA_DIR") or "").expanduser().resolve()
    _require(expected == resolved, "child FLOAT_DATA_DIR is not the task-owned root")
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    return resolved


def _seed_child(data_dir: Path) -> dict[str, Any]:
    _configure_child(data_dir)
    from app.utils import calendar_store

    start_time = time.time() - 5.0
    event = {
        "id": EVENT_ID,
        "title": "Prompt restart process smoke",
        "start_time": start_time,
        "timezone": "UTC",
        "status": "scheduled",
        "background_job": {
            "patience": {
                "max_runtime_seconds": MIN_RUNTIME_SECONDS,
                "max_provider_retries": 1,
            }
        },
        "actions": [
            {
                "id": ACTION_ID,
                "kind": "prompt",
                "prompt": PROMPT_TEXT,
                "conversation_mode": "inline",
                "session_id": SESSION_ID,
                "chain_id": SESSION_ID,
                "status": "scheduled",
            }
        ],
    }
    calendar_store.save_event(EVENT_ID, event)
    stored = calendar_store.load_event(EVENT_ID)
    return {
        "seeded": stored == event,
        "event_id": EVENT_ID,
        "action_id": ACTION_ID,
        "start_time": start_time,
    }


def _find_action(event: Mapping[str, Any]) -> dict[str, Any]:
    actions = event.get("actions")
    if not isinstance(actions, list):
        raise SmokeFailure("stored event has no actions")
    matches = [
        dict(item)
        for item in actions
        if isinstance(item, Mapping) and item.get("id") == ACTION_ID
    ]
    if len(matches) != 1:
        raise SmokeFailure("stored event does not contain exactly one smoke action")
    return matches[0]


def _build_child_app(data_dir: Path, provider: Any) -> tuple[Any, Any]:
    from app import routes as routes_module
    from app.services.work_run_store import WorkRunStore
    from fastapi import FastAPI

    app = FastAPI()
    app.state.config = {
        "data_dir": str(data_dir),
        "harmony_format": False,
        "work_run_store_path": str(data_dir / "databases" / "work_runs.sqlite3"),
    }
    app.state.agent_console_state = {"agents": {}}
    app.state.work_run_store = WorkRunStore(data_dir=data_dir)
    routes_module.llm_service = provider
    return app, app.state.work_run_store


class _BlockingProvider:
    def __init__(self, signal_path: Path, store: Any):
        from app.services import ModelContext

        self.signal_path = signal_path
        self.store = store
        self.context = ModelContext()

    def get_context(self, _session_id: str) -> Any:
        return self.context

    def generate(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        from app.utils import calendar_store, conversation_store
        from workers import scheduled_tool_runner as runner

        event = calendar_store.load_event(EVENT_ID)
        action = _find_action(event)
        checkpoint = action.get("prompt_checkpoint")
        _require(isinstance(checkpoint, Mapping), "provider entered before checkpoint")
        run_id = str(checkpoint.get("run_id") or "")
        receipt_id = str(checkpoint.get("receipt_id") or "")
        session_id = str(checkpoint.get("session_id") or "")
        assistant_message_id = str(checkpoint.get("output_message_id") or "")
        _require(run_id == action.get("run_id"), "checkpoint run does not own action")
        receipt = self.store.get(receipt_id)
        attempts = self.store.list_attempts(receipt_id, limit=10)
        _require(
            isinstance(receipt, Mapping), "provider entered before running receipt"
        )
        _require(receipt.get("run_id") == run_id, "running receipt has another run")
        _require(
            len(attempts) == 1 and attempts[0].get("status") == "running",
            "provider entered before running attempt journal",
        )
        conversation = conversation_store.load_conversation(session_id)
        placeholders = [
            item
            for item in conversation
            if isinstance(item, Mapping) and item.get("id") == assistant_message_id
        ]
        started_at = float(action.get("started_at") or 0.0)
        lease_seconds = float(runner._running_lease_seconds(event))
        _write_json_atomic(
            self.signal_path,
            {
                "checkpoint_persisted": True,
                "receipt_persisted": True,
                "event_id": EVENT_ID,
                "action_id": ACTION_ID,
                "run_id": run_id,
                "receipt_id": receipt_id,
                "session_id": session_id,
                "assistant_message_id": assistant_message_id,
                "checkpoint_id": checkpoint.get("checkpoint_id"),
                "attempt_id": attempts[0].get("id"),
                "attempt_status": attempts[0].get("status"),
                "placeholder_count": len(placeholders),
                "started_at": started_at,
                "lease_seconds": lease_seconds,
                "lease_expires_at": started_at + lease_seconds,
                "provider_entered_at": time.time(),
            },
        )
        while True:
            time.sleep(1.0)


class _FinalProvider:
    def __init__(self, assistant_message_id: str):
        from app.services import ModelContext

        self.assistant_message_id = assistant_message_id
        self.context = ModelContext()
        self.calls: list[dict[str, Any]] = []

    def get_context(self, _session_id: str) -> Any:
        return self.context

    def generate(self, tools: Sequence[Any], **kwargs: Any) -> dict[str, Any]:
        context = kwargs.get("context")
        messages = list(getattr(context, "messages", []) or [])
        recovery_messages = [
            item
            for item in messages
            if isinstance(item, Mapping)
            and isinstance(item.get("metadata"), Mapping)
            and item["metadata"].get("provider_retry") is True
        ]
        recovery_text = "\n".join(
            str(item.get("content") or "") for item in recovery_messages
        )
        self.calls.append(
            {
                "session_id": kwargs.get("session_id"),
                "tool_argument_count": len(list(tools)),
                "context_tool_count": len(list(getattr(context, "tools", []) or [])),
                "recovery_envelope_count": len(recovery_messages),
                "mentions_assistant_message_id": (
                    self.assistant_message_id in recovery_text
                ),
            }
        )
        return {
            "text": FINAL_TEXT,
            "thought": "",
            "response_id": "deterministic-prompt-restart-response",
            "finish_reason": "stop",
        }


def _block_child(data_dir: Path, signal_path: Path) -> None:
    _configure_child(data_dir)
    from app.services.work_run_store import WorkRunStore
    from workers import scheduled_tool_runner as runner

    store = WorkRunStore(data_dir=data_dir)
    provider = _BlockingProvider(signal_path, store)
    app, _ = _build_child_app(data_dir, provider)
    asyncio.run(runner.run_due_scheduled_tools_once(app))
    raise SmokeFailure("blocking fake provider returned unexpectedly")


def _resume_child(data_dir: Path) -> dict[str, Any]:
    _configure_child(data_dir)
    from app.utils import calendar_store, conversation_store
    from workers import scheduled_tool_runner as runner

    before = calendar_store.load_event(EVENT_ID)
    before_action = _find_action(before)
    checkpoint = before_action.get("prompt_checkpoint")
    _require(isinstance(checkpoint, Mapping), "resume child found no checkpoint")
    assistant_message_id = str(checkpoint.get("output_message_id") or "")
    provider = _FinalProvider(assistant_message_id)
    app, store = _build_child_app(data_dir, provider)
    completed_count = asyncio.run(runner.run_due_scheduled_tools_once(app))

    event = calendar_store.load_event(EVENT_ID)
    action = _find_action(event)
    checkpoint = action.get("prompt_checkpoint")
    checkpoint = dict(checkpoint) if isinstance(checkpoint, Mapping) else {}
    receipt_id = str(checkpoint.get("receipt_id") or "")
    session_id = str(checkpoint.get("session_id") or "")
    assistant_message_id = str(checkpoint.get("output_message_id") or "")
    receipt = store.get(receipt_id) or {}
    attempts = store.list_attempts(receipt_id, limit=10)
    effects = store.list_effects(receipt_id, limit=10)
    conversation = conversation_store.load_conversation(session_id)
    outputs = [
        item
        for item in conversation
        if isinstance(item, Mapping) and item.get("id") == assistant_message_id
    ]
    users = [
        item
        for item in conversation
        if isinstance(item, Mapping)
        and item.get("id") == checkpoint.get("user_message_id")
    ]
    output = dict(outputs[0]) if len(outputs) == 1 else {}
    output_metadata = output.get("metadata")
    output_metadata = (
        dict(output_metadata) if isinstance(output_metadata, Mapping) else {}
    )
    history = event.get("run_history")
    history = history if isinstance(history, list) else []
    history_receipt_ids = list(
        dict.fromkeys(
            str(item.get("id"))
            for item in history
            if isinstance(item, Mapping) and item.get("id")
        )
    )
    return {
        "due_scan_completed_count": completed_count,
        "event_id": EVENT_ID,
        "action_id": ACTION_ID,
        "action_status": action.get("status"),
        "run_id": action.get("run_id"),
        "receipt_id": receipt_id,
        "receipt_run_id": receipt.get("run_id"),
        "session_id": session_id,
        "assistant_message_id": assistant_message_id,
        "canonical_output_count": len(outputs),
        "canonical_output_complete": output_metadata.get("status") == "complete",
        "canonical_output_text": output.get("text"),
        "user_message_count": len(users),
        "history_receipt_ids": history_receipt_ids,
        "attempts": attempts,
        "effect_count": store.count_effects(receipt_id),
        "effects": effects,
        "tool_invocation_count": sum(
            1
            for item in history
            if isinstance(item, Mapping) and item.get("tool_invoked") is True
        ),
        "provider_calls": provider.calls,
    }


def _run_internal_child(args: argparse.Namespace) -> int:
    if args._data_dir is None:
        raise SmokeFailure("internal child mode requires --_data-dir")
    if args._child_mode == "seed":
        result = _seed_child(args._data_dir)
    elif args._child_mode == "block":
        if args._signal_path is None:
            raise SmokeFailure("blocking child requires --_signal-path")
        _block_child(args._data_dir, args._signal_path)
        return 1
    elif args._child_mode == "resume":
        result = _resume_child(args._data_dir)
    else:  # pragma: no cover - argparse controls the choices
        raise SmokeFailure("unknown child mode")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args._child_mode:
            return _run_internal_child(args)
        if args.signal_timeout_seconds <= 0 or args.resume_timeout_seconds <= 0:
            raise SmokeFailure("child timeouts must be positive")
        artifact_dir, retain = _create_artifact_dir(args)
        try:
            evidence = _orchestrate(args, artifact_dir)
            evidence["artifacts_retained"] = retain
            if retain:
                evidence["artifact_dir"] = str(artifact_dir)
            print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
            return 0
        except Exception as exc:
            failure = {
                "ok": False,
                "error": str(exc),
                "artifacts_retained": retain,
            }
            if retain:
                failure["artifact_dir"] = str(artifact_dir)
                _write_json_atomic(artifact_dir / "failure.json", failure)
            print(json.dumps(failure, sort_keys=True, separators=(",", ":")))
            return 1
        finally:
            if not retain:
                shutil.rmtree(artifact_dir, ignore_errors=True)
    except (OSError, SmokeFailure, ValueError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc), "artifacts_retained": False},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
