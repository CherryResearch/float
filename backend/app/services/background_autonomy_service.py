from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from app import config as app_config
from app.services.reflection_service import ReflectionService
from app.utils import calendar_store

JsonDict = Dict[str, Any]

DEFAULT_INTERVAL_SECONDS = 15 * 60
DEFAULT_MAX_REFLECTIONS_PER_TICK = 1
DEFAULT_MIN_PRIORITY = 0.05
DEFAULT_MODE = "overnight"
DEFAULT_MAX_RUNTIME_SECONDS = 30 * 60
DEFAULT_SATISFIED_THRESHOLD = 0.80
DEFAULT_BASIC_TICK_COUNT = 2
DEFAULT_BASIC_TICK_SECONDS = 5 * 60
BACKGROUND_AUTONOMY_MODES = {"manual", "basic", "overnight", "extended", "always_on"}
RUNNABLE_REFLECTION_STATUSES = {"open", "waiting"}
MANUAL_MODES = {"manual", "tool", "user"}
BUDGETED_MODES = {"basic", "overnight"}
SATISFACTION_MODES = {"extended"}


def _now() -> float:
    return time.time()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    if not math.isfinite(parsed):
        return default
    return parsed


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    parsed = _safe_int(value, default)
    return max(minimum, min(maximum, parsed))


def _env_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def _clean_text(value: Any, *, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _json_load(path: Path) -> JsonDict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_dump(path: Path, payload: JsonDict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    tmp.replace(path)


def background_autonomy_store_path(config: Optional[JsonDict] = None) -> Path:
    cfg = dict(config or {})
    raw = cfg.get("background_autonomy_store_path")
    if not raw:
        data_dir = Path(cfg.get("data_dir") or app_config.DEFAULT_DATA_DIR)
        raw = data_dir / "databases" / "background_autonomy_state.json"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = app_config.REPO_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class BackgroundAutonomyService:
    """Supervises Float's bounded background work without making it 24/7 by default."""

    def __init__(
        self,
        config: Optional[JsonDict] = None,
        *,
        reflection_service: Optional[ReflectionService] = None,
        now_fn: Callable[[], float] = _now,
    ) -> None:
        self.config = dict(config or {})
        self.reflection_service = reflection_service
        self.now = now_fn
        self.path = background_autonomy_store_path(self.config)
        self._lock = threading.Lock()
        self._state = self._load_state()

    def update_config(
        self, config: Optional[JsonDict], *, reset_session: bool = False
    ) -> None:
        self.config = dict(config or {})
        self.path = background_autonomy_store_path(self.config)
        if reset_session:
            self._state["session_started_at"] = None
            self._state["session_mode"] = None
            self._state["session_tick_count"] = 0
            self._state["session_stop_reason"] = None
            self._state["session_satisfied"] = False
            self._save_state()

    def enabled(self) -> bool:
        return _env_bool(self.config.get("background_autonomy_enabled"), False)

    def sandbox_processes(self) -> bool:
        return _env_bool(
            self.config.get("background_autonomy_sandbox_processes"),
            True,
        )

    def mode(self) -> str:
        raw = str(self.config.get("background_autonomy_mode") or DEFAULT_MODE)
        mode = raw.strip().lower().replace("-", "_")
        return mode if mode in BACKGROUND_AUTONOMY_MODES else DEFAULT_MODE

    def routine_enabled(self) -> bool:
        return self.enabled() and self.mode() != "manual"

    def interval_seconds(self) -> float:
        raw = self.config.get("background_autonomy_interval_seconds")
        seconds = _safe_float(raw, DEFAULT_INTERVAL_SECONDS)
        return max(30.0, min(24 * 60 * 60.0, seconds))

    def current_interval_seconds(self) -> float:
        if self.mode() == "basic":
            return self.basic_tick_seconds()
        return self.interval_seconds()

    def max_reflections_per_tick(self) -> int:
        raw = self.config.get("background_autonomy_max_reflections_per_tick")
        return max(0, min(5, _safe_int(raw, DEFAULT_MAX_REFLECTIONS_PER_TICK)))

    def max_runtime_seconds(self) -> float:
        raw = self.config.get("background_autonomy_max_runtime_seconds")
        seconds = _safe_float(raw, DEFAULT_MAX_RUNTIME_SECONDS)
        return max(60.0, min(24 * 60 * 60.0, seconds))

    def satisfied_threshold(self) -> float:
        raw = self.config.get("background_autonomy_satisfied_threshold")
        return max(0.0, min(1.0, _safe_float(raw, DEFAULT_SATISFIED_THRESHOLD)))

    def basic_tick_count(self) -> int:
        raw = self.config.get("background_autonomy_basic_tick_count")
        return max(1, min(20, _safe_int(raw, DEFAULT_BASIC_TICK_COUNT)))

    def basic_tick_seconds(self) -> float:
        raw = self.config.get("background_autonomy_basic_tick_seconds")
        seconds = _safe_float(raw, DEFAULT_BASIC_TICK_SECONDS)
        return max(5.0, min(24 * 60 * 60.0, seconds))

    def min_priority(self) -> float:
        raw = self.config.get("background_autonomy_min_priority")
        return max(0.0, min(1.0, _safe_float(raw, DEFAULT_MIN_PRIORITY)))

    def status(self, app: Any = None) -> JsonDict:
        reflection_service = self._reflection_service(app)
        reflection = self._reflection_snapshot(reflection_service)
        now = self.now()
        state = dict(self._state)
        next_tick_at = None
        last_completed = _safe_float(state.get("last_completed_at"), 0.0)
        if last_completed:
            next_tick_at = last_completed + self.current_interval_seconds()
        session = self._session_status(now=now, state=state)
        return {
            "enabled": self.enabled(),
            "sandbox_processes": self.sandbox_processes(),
            "mode": self.mode() if self.enabled() else "manual",
            "configured_mode": self.mode(),
            "routine_enabled": self.routine_enabled(),
            "interval_seconds": self.interval_seconds(),
            "current_interval_seconds": self.current_interval_seconds(),
            "next_tick_at": next_tick_at,
            "overdue": bool(next_tick_at and next_tick_at <= now),
            "max_reflections_per_tick": self.max_reflections_per_tick(),
            "max_runtime_seconds": self.max_runtime_seconds(),
            "satisfied_threshold": self.satisfied_threshold(),
            "basic_tick_count": self.basic_tick_count(),
            "basic_tick_seconds": self.basic_tick_seconds(),
            "min_priority": self.min_priority(),
            "session": session,
            "state": state,
            "reflection": reflection,
            "scheduled_actions": self._scheduled_action_snapshot(),
            "capabilities": {
                "reflection_ticks": True,
                "dry_run": True,
                "durable_heartbeat": True,
                "scheduled_tool_runner": True,
                "runtime_budget": True,
                "satisfaction_stop": True,
                "sandbox_background_processes": self.sandbox_processes(),
                "opt_in_container_suite": True,
            },
        }

    def start_session(self, mode: Optional[str] = None) -> None:
        normalized_mode = self._normalize_mode(mode or self.mode())
        current_mode = str(self._state.get("session_mode") or "")
        if self._state.get("session_started_at") and current_mode == normalized_mode:
            return
        self._state["session_started_at"] = self.now()
        self._state["session_mode"] = normalized_mode
        self._state["session_tick_count"] = 0
        self._state["session_stop_reason"] = None
        self._state["session_satisfied"] = False
        self._save_state()

    def should_stop_session(self, mode: Optional[str] = None) -> bool:
        return self._session_stop_reason(mode=mode) is not None

    def tick(
        self,
        app: Any = None,
        *,
        mode: str = "manual",
        force: bool = False,
        dry_run: bool = False,
        seed: Optional[int] = None,
        max_reflections: Optional[int] = None,
        max_runtime_seconds: Optional[float] = None,
        satisfied_threshold: Optional[float] = None,
    ) -> JsonDict:
        normalized_mode = self._normalize_mode(mode or "manual")
        if (
            not self.enabled()
            and normalized_mode not in MANUAL_MODES
            and not force
            and not dry_run
        ):
            result = self._tick_result(
                normalized_mode,
                status="disabled",
                dry_run=dry_run,
                error="Background autonomy is disabled.",
            )
            self._record_tick(result)
            return result

        if not self._lock.acquire(blocking=False):
            result = self._tick_result(
                normalized_mode,
                status="busy",
                dry_run=dry_run,
                error="Another background autonomy tick is already running.",
            )
            self._record_tick(result, running=False)
            return result

        tick_id = f"autonomy-{int(self.now() * 1000)}-{uuid4().hex[:8]}"
        self._state.update(
            {
                "running": True,
                "active_tick_id": tick_id,
                "last_started_at": self.now(),
                "last_mode": normalized_mode,
            }
        )
        self.start_session(normalized_mode)
        self._save_state()
        try:
            stop_reason = self._session_stop_reason(
                mode=normalized_mode,
                max_runtime_seconds=max_runtime_seconds,
            )
            if stop_reason:
                result = self._tick_result(
                    normalized_mode,
                    tick_id=tick_id,
                    status="stopped",
                    dry_run=dry_run,
                    stop_reason=stop_reason,
                )
                self._record_tick(result)
                return result
            reflection_service = self._reflection_service(app)
            before = self._reflection_snapshot(reflection_service)
            selected = before.get("top_attention") or []
            if dry_run:
                result = self._tick_result(
                    normalized_mode,
                    tick_id=tick_id,
                    status="planned",
                    dry_run=True,
                    reflection_before=before,
                    reflection_after=before,
                    selected=selected,
                )
                self._record_tick(result)
                return result

            limit = self._resolved_max_reflections(max_reflections)
            if normalized_mode == "basic" and max_reflections is None:
                limit = min(limit or self.basic_tick_count(), self.basic_tick_count())
            budget_seconds = self._resolved_runtime_budget(
                normalized_mode,
                override=max_runtime_seconds,
            )
            threshold = self._resolved_satisfied_threshold(satisfied_threshold)
            started = self.now()
            runs: List[JsonDict] = []
            errors: List[str] = []
            stop_reason = ""
            for index in range(limit):
                if (
                    budget_seconds is not None
                    and (self.now() - started) >= budget_seconds
                ):
                    stop_reason = "runtime_budget_reached"
                    break
                tick_seed = None if seed is None else int(seed) + index
                tick = reflection_service.scheduler_tick(
                    mode=normalized_mode,
                    force=force,
                    seed=tick_seed,
                )
                tick = self._annotate_scheduler_tick(
                    reflection_service,
                    tick,
                    satisfied_threshold=threshold,
                )
                runs.append(tick)
                if tick.get("error"):
                    errors.append(str(tick.get("error")))
                if normalized_mode in SATISFACTION_MODES and tick.get("satisfied"):
                    stop_reason = "satisfied_threshold"
                    break
                if not tick.get("task_id") or tick.get("status") in {
                    "no_task",
                    "not_runnable",
                }:
                    break
            after = self._reflection_snapshot(reflection_service)
            ran_count = sum(1 for item in runs if item.get("task_id"))
            status = "idle"
            if errors:
                status = "error"
            elif ran_count:
                status = "ran"
            result = self._tick_result(
                normalized_mode,
                tick_id=tick_id,
                status=status,
                dry_run=False,
                reflection_before=before,
                reflection_after=after,
                selected=selected,
                runs=runs,
                error="; ".join(errors) if errors else "",
                stop_reason=stop_reason,
            )
            self._record_tick(result)
            return result
        except Exception as exc:
            result = self._tick_result(
                normalized_mode,
                tick_id=tick_id,
                status="error",
                dry_run=dry_run,
                error=str(exc),
            )
            self._record_tick(result)
            return result
        finally:
            self._lock.release()

    def _load_state(self) -> JsonDict:
        state = _json_load(self.path)
        if state.get("running"):
            state["running"] = False
            state["recovered_stale_tick_at"] = self.now()
        state.setdefault("running", False)
        state.setdefault("consecutive_errors", 0)
        state.setdefault("ticks", [])
        state.setdefault("session_started_at", None)
        state.setdefault("session_mode", None)
        state.setdefault("session_tick_count", 0)
        state.setdefault("session_stop_reason", None)
        state.setdefault("session_satisfied", False)
        state.setdefault("last_satisfaction_score", None)
        return state

    def _save_state(self) -> None:
        _json_dump(self.path, self._state)

    def _record_tick(self, result: JsonDict, *, running: bool = False) -> None:
        now = self.now()
        self._state["running"] = running
        self._state["active_tick_id"] = None
        self._state["last_completed_at"] = now
        self._state["last_status"] = result.get("status")
        self._state["last_error"] = result.get("error") or None
        self._state["last_result"] = {
            "id": result.get("id"),
            "mode": result.get("mode"),
            "status": result.get("status"),
            "dry_run": result.get("dry_run"),
            "ran_reflections": result.get("ran_reflections"),
            "candidate_count": result.get("candidate_count"),
            "satisfaction_score": result.get("satisfaction_score"),
            "satisfied": result.get("satisfied"),
            "stop_reason": result.get("stop_reason"),
        }
        self._state["session_tick_count"] = (
            max(0, int(self._state.get("session_tick_count") or 0)) + 1
        )
        self._state["last_satisfaction_score"] = result.get("satisfaction_score")
        self._state["session_satisfied"] = bool(result.get("satisfied"))
        stop_reason = result.get("stop_reason")
        if stop_reason:
            self._state["session_stop_reason"] = stop_reason
        if result.get("status") == "error":
            self._state["consecutive_errors"] = (
                int(self._state.get("consecutive_errors") or 0) + 1
            )
        elif result.get("status") not in {"busy"}:
            self._state["consecutive_errors"] = 0
        ticks = self._state.setdefault("ticks", [])
        if isinstance(ticks, list):
            ticks.append(self._state["last_result"])
            del ticks[:-20]
        self._save_state()

    def _tick_result(
        self,
        mode: str,
        *,
        tick_id: Optional[str] = None,
        status: str,
        dry_run: bool,
        reflection_before: Optional[JsonDict] = None,
        reflection_after: Optional[JsonDict] = None,
        selected: Optional[List[JsonDict]] = None,
        runs: Optional[List[JsonDict]] = None,
        error: str = "",
        stop_reason: str = "",
    ) -> JsonDict:
        before = reflection_before or {"counts": {}, "top_attention": []}
        after = reflection_after or before
        run_items = runs or []
        scores = [
            _safe_float(item.get("satisfaction_score"), 0.0)
            for item in run_items
            if item.get("satisfaction_score") is not None
        ]
        satisfaction_score = max(scores) if scores else None
        satisfied = any(bool(item.get("satisfied")) for item in run_items)
        return {
            "id": tick_id or f"autonomy-{int(self.now() * 1000)}-{uuid4().hex[:8]}",
            "mode": mode,
            "status": status,
            "dry_run": dry_run,
            "created_at": self.now(),
            "candidate_count": int(before.get("candidate_count") or 0),
            "selected": selected or [],
            "ran_reflections": sum(1 for item in run_items if item.get("task_id")),
            "runs": run_items,
            "satisfaction_score": satisfaction_score,
            "satisfied": satisfied,
            "stop_reason": stop_reason or None,
            "reflection_before": before,
            "reflection_after": after,
            "scheduled_actions": self._scheduled_action_snapshot(),
            "error": error or None,
        }

    def _resolved_max_reflections(self, override: Optional[int]) -> int:
        if override is None:
            return self.max_reflections_per_tick()
        return max(0, min(5, _safe_int(override, self.max_reflections_per_tick())))

    def _normalize_mode(self, value: Any) -> str:
        raw = str(value or "").strip().lower().replace("-", "_")
        if raw in {"idle", "routine"}:
            return self.mode()
        if raw in BACKGROUND_AUTONOMY_MODES or raw in MANUAL_MODES:
            return raw
        return "manual"

    def _resolved_runtime_budget(
        self,
        mode: str,
        *,
        override: Optional[float] = None,
    ) -> Optional[float]:
        if override is not None:
            seconds = _safe_float(override, self.max_runtime_seconds())
            return max(0.0, min(24 * 60 * 60.0, seconds))
        normalized_mode = self._normalize_mode(mode)
        if normalized_mode == "overnight":
            return self.max_runtime_seconds()
        if normalized_mode == "basic":
            return self.basic_tick_count() * self.basic_tick_seconds()
        return None

    def _resolved_satisfied_threshold(self, override: Optional[float] = None) -> float:
        if override is None:
            return self.satisfied_threshold()
        return max(0.0, min(1.0, _safe_float(override, self.satisfied_threshold())))

    def _session_status(self, *, now: float, state: JsonDict) -> JsonDict:
        started = _safe_float(state.get("session_started_at"), 0.0)
        mode = self._normalize_mode(state.get("session_mode") or self.mode())
        elapsed = max(0.0, now - started) if started else 0.0
        budget = self._resolved_runtime_budget(mode)
        remaining = None
        if budget is not None and started:
            remaining = max(0.0, budget - elapsed)
        stop_reason = self._session_stop_reason(mode=mode, now=now, state=state)
        return {
            "started_at": started or None,
            "mode": mode,
            "elapsed_seconds": elapsed,
            "runtime_budget_seconds": budget,
            "remaining_seconds": remaining,
            "tick_count": int(state.get("session_tick_count") or 0),
            "stop_reason": stop_reason,
            "satisfied": bool(state.get("session_satisfied")),
            "satisfaction_score": state.get("last_satisfaction_score"),
        }

    def _session_stop_reason(
        self,
        *,
        mode: Optional[str] = None,
        now: Optional[float] = None,
        state: Optional[JsonDict] = None,
        max_runtime_seconds: Optional[float] = None,
    ) -> Optional[str]:
        snapshot = state or self._state
        normalized_mode = self._normalize_mode(
            mode or snapshot.get("session_mode") or self.mode()
        )
        explicit = snapshot.get("session_stop_reason")
        if (
            explicit
            and self._normalize_mode(snapshot.get("session_mode")) == normalized_mode
        ):
            return str(explicit)
        tick_count = int(snapshot.get("session_tick_count") or 0)
        if normalized_mode == "basic" and tick_count >= self.basic_tick_count():
            return "basic_tick_budget_reached"
        started = _safe_float(snapshot.get("session_started_at"), 0.0)
        budget = self._resolved_runtime_budget(
            normalized_mode,
            override=max_runtime_seconds,
        )
        if started and budget is not None:
            current = self.now() if now is None else now
            if (current - started) >= budget:
                return "runtime_budget_reached"
        if normalized_mode in SATISFACTION_MODES and snapshot.get("session_satisfied"):
            return "satisfied_threshold"
        return None

    def _annotate_scheduler_tick(
        self,
        reflection_service: ReflectionService,
        tick: JsonDict,
        *,
        satisfied_threshold: float,
    ) -> JsonDict:
        annotated = dict(tick)
        result = (
            annotated.get("result") if isinstance(annotated.get("result"), dict) else {}
        )
        task_id = str(result.get("task_id") or annotated.get("task_id") or "")
        run_id = str(result.get("run_id") or "")
        run = self._find_reflection_run(reflection_service, task_id, run_id)
        if not run:
            annotated["satisfaction_score"] = None
            annotated["satisfied"] = False
            return annotated
        score = self._satisfaction_score(run)
        should_continue = bool(run.get("should_continue"))
        annotated["reflection_run"] = {
            "id": run.get("id"),
            "usefulness": run.get("usefulness"),
            "novelty": run.get("novelty"),
            "repetition": run.get("repetition"),
            "should_continue": should_continue,
            "should_surface_to_user": run.get("should_surface_to_user"),
        }
        annotated["satisfaction_score"] = score
        annotated["satisfied"] = bool(
            score >= satisfied_threshold and not should_continue
        )
        return annotated

    def _find_reflection_run(
        self,
        reflection_service: ReflectionService,
        task_id: str,
        run_id: str,
    ) -> Optional[JsonDict]:
        if not task_id and not run_id:
            return None
        try:
            runs = reflection_service.list_runs(task_id, limit=10)
        except Exception:
            return None
        if run_id:
            for run in runs:
                if str(run.get("id") or "") == run_id:
                    return run
        return runs[0] if runs else None

    def _satisfaction_score(self, run: JsonDict) -> float:
        usefulness = max(0.0, min(1.0, _safe_float(run.get("usefulness"), 0.0)))
        novelty = max(0.0, min(1.0, _safe_float(run.get("novelty"), 0.0)))
        repetition = max(0.0, min(1.0, _safe_float(run.get("repetition"), 0.0)))
        score = (0.65 * usefulness) + (0.35 * novelty) - (0.25 * repetition)
        return round(max(0.0, min(1.0, score)), 4)

    def _reflection_service(self, app: Any = None) -> ReflectionService:
        if self.reflection_service is not None:
            return self.reflection_service
        service = getattr(getattr(app, "state", None), "reflection_service", None)
        if isinstance(service, ReflectionService):
            self.reflection_service = service
            return service
        config_payload = getattr(getattr(app, "state", None), "config", None)
        service = ReflectionService(
            config_payload if isinstance(config_payload, dict) else self.config
        )
        self.reflection_service = service
        if app is not None:
            try:
                app.state.reflection_service = service
            except Exception:
                pass
        return service

    def _reflection_snapshot(self, reflection_service: ReflectionService) -> JsonDict:
        tasks = reflection_service.list_tasks(limit=200)
        now = self.now()
        counts: Dict[str, int] = {}
        attention: List[JsonDict] = []
        runnable_count = 0
        for task in tasks:
            status = str(task.get("status") or "unknown").strip().lower() or "unknown"
            counts[status] = counts.get(status, 0) + 1
            depth_budget = self._depth_budget(task)
            run_count = max(0, int(task.get("run_count") or 0))
            cooldown_until = _safe_float(task.get("cooldown_until"), 0.0)
            priority = max(0.0, min(1.0, _safe_float(task.get("priority"), 0.0)))
            runnable = (
                status in RUNNABLE_REFLECTION_STATUSES
                and run_count < depth_budget
                and (not cooldown_until or cooldown_until <= now)
                and priority >= self.min_priority()
            )
            if runnable:
                runnable_count += 1
            attention.append(
                {
                    "id": task.get("id"),
                    "title": _clean_text(task.get("title") or task.get("question")),
                    "status": status,
                    "source": task.get("source"),
                    "priority": round(priority, 4),
                    "runnable": runnable,
                    "run_count": run_count,
                    "depth_budget": depth_budget,
                    "cooldown_until": cooldown_until or None,
                    "attention": self._attention_values(task),
                }
            )
        attention.sort(
            key=lambda item: (
                bool(item.get("runnable")),
                float(item.get("priority") or 0.0),
            ),
            reverse=True,
        )
        return {
            "task_count": len(tasks),
            "candidate_count": runnable_count,
            "counts": counts,
            "top_attention": attention[:8],
        }

    def _attention_values(self, task: JsonDict) -> JsonDict:
        priority = max(0.0, min(1.0, _safe_float(task.get("priority"), 0.0)))
        utility = max(0.0, min(1.0, _safe_float(task.get("utility"), 0.0)))
        staleness = max(0.0, min(1.0, _safe_float(task.get("staleness"), 0.0)))
        recurrence = max(
            0.0,
            min(
                1.0,
                _safe_float(task.get("user_recurrence"), 0.0)
                + _safe_float(task.get("self_recurrence"), 0.0),
            ),
        )
        long_term = (0.55 * utility) + (0.25 * recurrence) + (0.20 * staleness)
        return {
            "short_term_importance": round(priority, 4),
            "long_term_importance": round(max(0.0, min(1.0, long_term)), 4),
            "saturation": round(
                max(0.0, min(1.0, _safe_float(task.get("saturation"), 0.0))), 4
            ),
        }

    def _depth_budget(self, task: JsonDict) -> int:
        budget = task.get("patience_budget")
        if isinstance(budget, dict):
            return _bounded_int(
                budget.get("max_reasoning_turns"),
                default=2,
                minimum=1,
                maximum=20,
            )
        patience = max(0, min(3, _safe_int(task.get("patience"), 1)))
        return {0: 1, 1: 2, 2: 3, 3: 4}.get(patience, 2)

    def _scheduled_action_snapshot(self) -> JsonDict:
        now = self.now()
        total = 0
        due = 0
        next_due_at: Optional[float] = None
        try:
            event_ids = calendar_store.list_events()
        except Exception:
            event_ids = []
        for event_id in event_ids:
            try:
                event = calendar_store.load_event(event_id)
            except Exception:
                continue
            if not isinstance(event, dict):
                continue
            actions = (
                event.get("actions") if isinstance(event.get("actions"), list) else []
            )
            active_actions = [
                action
                for action in actions
                if isinstance(action, dict)
                and str(action.get("status") or "scheduled").lower()
                not in {"acknowledged", "skipped", "invoked", "complete", "done"}
            ]
            if not active_actions:
                continue
            total += len(active_actions)
            start_at = self._event_start_time(event)
            if start_at is None:
                continue
            if start_at <= now:
                due += len(active_actions)
            elif next_due_at is None or start_at < next_due_at:
                next_due_at = start_at
        return {"pending": total, "due": due, "next_due_at": next_due_at}

    def _event_start_time(self, event: JsonDict) -> Optional[float]:
        for key in ("start_time", "start"):
            parsed = self._coerce_timestamp(event.get(key))
            if parsed:
                return parsed
        start = event.get("start") if isinstance(event.get("start"), dict) else {}
        return self._coerce_timestamp(start.get("dateTime") or start.get("date"))

    def _coerce_timestamp(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)) and float(value) > 0:
            return float(value)
        if isinstance(value, str) and value.strip():
            try:
                return float(value)
            except Exception:
                pass
            try:
                from datetime import datetime

                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except Exception:
                return None
        if isinstance(value, dict):
            return self._coerce_timestamp(value.get("dateTime") or value.get("date"))
        return None


def build_background_autonomy_service(
    config: Optional[JsonDict] = None,
    *,
    reflection_service: Optional[ReflectionService] = None,
) -> BackgroundAutonomyService:
    return BackgroundAutonomyService(
        config or app_config.load_config(),
        reflection_service=reflection_service,
    )
