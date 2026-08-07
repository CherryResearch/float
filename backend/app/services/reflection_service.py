from __future__ import annotations

import json
import logging
import math
import random
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence
from uuid import uuid4

from app import config as app_config
from app.models import ModelContext
from app.services.conversation_compaction import build_compaction, message_text
from app.services.rag_provider import get_rag_service, try_ingest_text
from app.services.work_run_projection import reflection_run_receipt
from app.services.work_run_store import WorkRunStore
from app.utils import calendar_store, conversation_store

JsonDict = Dict[str, Any]
LlmGenerate = Callable[..., Any]

logger = logging.getLogger(__name__)

DEFAULT_ECHO_DAMPING = 0.25
DEFAULT_EXPLORATION_TEMPERATURE = 0.45
DEFAULT_SURFACE_THRESHOLD = 0.70
DEFAULT_TOP_K = 8
RUNNABLE_STATUSES = {"open", "waiting"}


def _now() -> float:
    return time.time()


def _clean_text(value: Any, *, limit: int = 4000) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _clean_multiline_text(value: Any, *, limit: int = 8000) -> str:
    """Keep intentional markdown line breaks while bounding stored model output."""

    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _skill_markdown_proposal(value: Any, *, limit: int = 8000) -> str:
    """Accept markdown-shaped drafts and reject common provider prompt echoes."""

    text = _clean_multiline_text(value, limit=limit)
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    lowered = text.lower()
    if lowered.startswith(("you said:", "thought task:", "question:")):
        return ""
    if not re.search(r"(?m)^#{1,6}\s+\S", text):
        return ""
    return text


def _slug(value: str, *, fallback: str = "reflection") -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return (text or fallback)[:48]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    if not math.isfinite(parsed):
        return default
    return max(0.0, min(1.0, parsed))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    parsed = _safe_int(value, default)
    return max(minimum, min(maximum, parsed))


def normalize_reflection_patience_budget(value: Any, legacy: Any = None) -> JsonDict:
    """Normalize reflection patience into explicit budgets.

    The legacy integer maps to reasoning passes only. Tool continuations are not
    counted as reasoning turns; they should be budgeted by their own tool/runtime
    limits.
    """

    legacy_patience = max(0, min(3, _safe_int(legacy, 1)))
    defaults: JsonDict = {
        "profile": {0: "low", 1: "medium", 2: "high", 3: "max"}.get(
            legacy_patience, "medium"
        ),
        "max_reasoning_turns": {0: 1, 1: 2, 2: 3, 3: 4}.get(legacy_patience, 2),
        "max_runtime_seconds": 0,
        "max_context_tokens": 0,
        "max_output_tokens": 0,
        "reserve_output_tokens": 0,
    }
    if isinstance(value, dict):
        source = value
    else:
        source = {}
        if value not in (None, ""):
            legacy_patience = max(0, min(3, _safe_int(value, legacy_patience)))
            defaults["profile"] = {0: "low", 1: "medium", 2: "high", 3: "max"}.get(
                legacy_patience, "medium"
            )
            defaults["max_reasoning_turns"] = {0: 1, 1: 2, 2: 3, 3: 4}.get(
                legacy_patience, 2
            )
    profile = str(source.get("profile") or defaults["profile"]).strip().lower()
    if profile not in {"low", "medium", "high", "max", "custom"}:
        profile = "custom"
    return {
        "profile": profile,
        "max_reasoning_turns": _bounded_int(
            source.get("max_reasoning_turns", defaults["max_reasoning_turns"]),
            default=int(defaults["max_reasoning_turns"]),
            minimum=1,
            maximum=20,
        ),
        "max_runtime_seconds": _bounded_int(
            source.get("max_runtime_seconds", defaults["max_runtime_seconds"]),
            default=int(defaults["max_runtime_seconds"]),
            minimum=0,
            maximum=24 * 60 * 60,
        ),
        "max_context_tokens": _bounded_int(
            source.get("max_context_tokens", defaults["max_context_tokens"]),
            default=int(defaults["max_context_tokens"]),
            minimum=0,
            maximum=2_000_000,
        ),
        "max_output_tokens": _bounded_int(
            source.get("max_output_tokens", defaults["max_output_tokens"]),
            default=int(defaults["max_output_tokens"]),
            minimum=0,
            maximum=128_000,
        ),
        "reserve_output_tokens": _bounded_int(
            source.get("reserve_output_tokens", defaults["reserve_output_tokens"]),
            default=int(defaults["reserve_output_tokens"]),
            minimum=0,
            maximum=128_000,
        ),
    }


def _safe_timestamp(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        numeric = float(value)
        if math.isfinite(numeric):
            return numeric / 1000.0 if numeric > 1e12 else numeric
    except Exception:
        pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _json_loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        parsed = json.loads(str(value))
    except Exception:
        return default
    return parsed


def _extract_json_object(text: str) -> Optional[JsonDict]:
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    candidates = [stripped]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _response_text(response: Any) -> str:
    if isinstance(response, dict):
        for key in ("text", "message", "content", "output"):
            value = response.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                nested = _response_text(value)
                if nested:
                    return nested
        return ""
    return str(response or "")


def _response_thought_trace(response: Any) -> List[JsonDict]:
    """Bound and normalize provider reasoning so reflection runs can retain it."""

    if not isinstance(response, dict):
        return []
    raw_trace = response.get("thought_trace")
    entries = raw_trace if isinstance(raw_trace, list) else []
    normalized: List[JsonDict] = []
    total_chars = 0
    for index, item in enumerate(entries[:128]):
        source = item if isinstance(item, dict) else {"text": item}
        text = _clean_multiline_text(source.get("text"), limit=4000)
        if not text:
            continue
        remaining = 32_000 - total_chars
        if remaining <= 0:
            break
        text = text[:remaining]
        entry: JsonDict = {"index": len(normalized), "text": text}
        for key in ("timestamp", "created_at", "type"):
            if source.get(key) is not None:
                entry[key] = source.get(key)
        normalized.append(entry)
        total_chars += len(text)
    if normalized:
        return normalized
    thought = _clean_multiline_text(response.get("thought"), limit=32_000)
    return [{"index": 0, "text": thought}] if thought else []


def _response_generation_metadata(response: Any) -> JsonDict:
    if not isinstance(response, dict):
        return {}
    metadata = (
        response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
    )
    allowed = (
        "provider",
        "server_provider",
        "finish_reason",
        "termination_category",
        "thought_trace_length",
        "usage",
    )
    result = {
        key: metadata.get(key) for key in allowed if metadata.get(key) is not None
    }
    requested_model = metadata.get("requested_model") or metadata.get("model_requested")
    received_model = metadata.get("received_model") or metadata.get("model_received")
    if requested_model is not None:
        result["requested_model"] = requested_model
    if received_model is not None:
        result["received_model"] = received_model
    return result


def reflection_store_path(config: Optional[JsonDict] = None) -> Path:
    cfg = dict(config or {})
    raw = (
        cfg.get("reflection_store_path")
        or cfg.get("reflections_store_path")
        or cfg.get("reflection_store")
    )
    if not raw:
        data_dir = Path(cfg.get("data_dir") or app_config.DEFAULT_DATA_DIR)
        raw = data_dir / "databases" / "reflections.sqlite3"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = app_config.REPO_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class ReflectionService:
    """Bounded background-thinking state machine for manual reflection runs."""

    def __init__(
        self,
        config: Optional[JsonDict] = None,
        *,
        llm_generate: Optional[LlmGenerate] = None,
        now_fn: Callable[[], float] = _now,
        work_run_store: Optional[WorkRunStore] = None,
    ) -> None:
        self.config = dict(config or {})
        self.path = reflection_store_path(self.config)
        self.llm_generate = llm_generate
        self.now = now_fn
        self.work_run_store = work_run_store
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS thought_tasks (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_thread_id TEXT,
                    cluster_id TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_run_at REAL,
                    cooldown_until REAL,
                    run_count INTEGER NOT NULL DEFAULT 0,
                    priority REAL NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_thought_tasks_status ON thought_tasks(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_thought_tasks_priority ON thought_tasks(priority)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reflection_runs (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    usefulness REAL NOT NULL DEFAULT 0,
                    should_surface_to_user INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reflection_runs_task ON reflection_runs(task_id, created_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduler_ticks (
                    id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    task_id TEXT,
                    candidate_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    completed_at REAL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Public CRUD
    # ------------------------------------------------------------------
    def create_task(
        self,
        *,
        title: str = "",
        question: str,
        source_thread_id: str = "",
        cluster_id: str = "",
        source: str = "user",
        patience: Any = 1,
        patience_budget: Optional[JsonDict] = None,
        memory_keys: Optional[Sequence[str]] = None,
        event_id: str = "",
        metadata: Optional[JsonDict] = None,
        utility: Optional[float] = None,
        uncertainty: Optional[float] = None,
    ) -> JsonDict:
        question_text = _clean_text(question, limit=2000)
        if not question_text:
            raise ValueError("question is required")
        now = self.now()
        task_id = f"thought-{int(now * 1000)}-{uuid4().hex[:8]}"
        normalized_patience = max(0, min(3, _safe_int(patience, 1)))
        normalized_budget = normalize_reflection_patience_budget(
            patience_budget if patience_budget is not None else patience,
            legacy=normalized_patience,
        )
        judgment: JsonDict = {}
        if utility is None or uncertainty is None:
            judgment = self._initial_task_judgment(
                question=question_text,
                title=_clean_text(title, limit=160),
                metadata=metadata or {},
            )
        task: JsonDict = {
            "id": task_id,
            "title": _clean_text(title, limit=160) or question_text[:80],
            "question": question_text,
            "source_thread_id": _clean_text(source_thread_id, limit=200),
            "cluster_id": _clean_text(cluster_id, limit=120),
            "source": _clean_text(source, limit=40) or "user",
            "status": "open",
            "utility": _safe_float(
                utility if utility is not None else judgment.get("utility"), 0.55
            ),
            "uncertainty": _safe_float(
                uncertainty if uncertainty is not None else judgment.get("uncertainty"),
                0.55,
            ),
            "user_recurrence": 0.0,
            "self_recurrence": 0.0,
            "saturation": 0.0,
            "progress_slope": 0.0,
            "staleness": 0.0,
            "priority": 0.0,
            "patience": normalized_patience,
            "patience_budget": normalized_budget,
            "created_at": now,
            "updated_at": now,
            "last_run_at": None,
            "cooldown_until": None,
            "run_count": 0,
            "memory_keys": [
                str(item).strip()
                for item in (memory_keys or [])
                if str(item or "").strip()
            ],
            "event_id": _clean_text(event_id, limit=200),
            "metadata": dict(metadata or {}),
        }
        self._refresh_task_scores(task)
        self._save_task(task)
        return task

    def get_task(self, task_id: str) -> Optional[JsonDict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM thought_tasks WHERE id = ?", (str(task_id),)
            ).fetchone()
        if not row:
            return None
        return _json_loads(row["payload"], {})

    def list_tasks(
        self,
        *,
        status: str = "",
        limit: int = 50,
        include_runs: bool = False,
    ) -> List[JsonDict]:
        safe_limit = max(1, min(200, _safe_int(limit, 50)))
        params: list[Any] = []
        where = ""
        if status:
            where = "WHERE status = ?"
            params.append(str(status).strip().lower())
        params.append(safe_limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload FROM thought_tasks
                {where}
                ORDER BY priority DESC, updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        tasks = [_json_loads(row["payload"], {}) for row in rows]
        if include_runs:
            for task in tasks:
                task["runs"] = self.list_runs(str(task.get("id") or ""), limit=10)
        return tasks

    def list_runs(
        self,
        task_id: str = "",
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> List[JsonDict]:
        safe_limit = max(1, min(200, _safe_int(limit, 50)))
        safe_offset = max(0, _safe_int(offset, 0))
        params: list[Any] = []
        where = ""
        if task_id:
            where = "WHERE task_id = ?"
            params.append(str(task_id))
        params.extend((safe_limit, safe_offset))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload FROM reflection_runs
                {where}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        return [_json_loads(row["payload"], {}) for row in rows]

    def run_task(self, task_id: str, *, force: bool = False) -> JsonDict:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"reflection task not found: {task_id}")
        if not force and not self._task_is_runnable(task):
            return {
                "status": "not_runnable",
                "task": task,
                "reason": self._not_runnable_reason(task),
            }
        context = self._retrieve_context(task)
        reflection = self._run_reflection_pass(task, context)
        output = str(reflection.get("output") or "")
        evaluation = self._evaluate_reflection(task, output, context)
        run = self._save_run(task, output, evaluation, context, reflection=reflection)
        updated_task = self._update_task_after_run(task, run, evaluation)
        return {"status": "ran", "task": updated_task, "run": run}

    def scheduler_tick(
        self,
        *,
        mode: str = "manual",
        force: bool = False,
        seed: Optional[int] = None,
    ) -> JsonDict:
        tick_id = f"tick-{int(self.now() * 1000)}-{uuid4().hex[:8]}"
        started = self.now()
        seeded = self._seed_candidate_tasks(mode=mode)
        candidates = self._runnable_candidates(force=force)
        selected = self._sample_task(candidates, seed=seed)
        tick: JsonDict = {
            "id": tick_id,
            "mode": _clean_text(mode, limit=40) or "manual",
            "task_id": selected.get("id") if selected else None,
            "candidate_count": len(candidates),
            "status": "no_task" if not selected else "running",
            "created_at": started,
            "completed_at": None,
            "error": None,
            "metadata": {
                "exploration_temperature": DEFAULT_EXPLORATION_TEMPERATURE,
                "top_k": DEFAULT_TOP_K,
                "seeded_candidates": len(seeded),
            },
        }
        self._save_tick(tick)
        if not selected:
            tick["completed_at"] = self.now()
            self._save_tick(tick)
            return tick
        try:
            result = self.run_task(str(selected["id"]), force=force)
            tick["status"] = result.get("status") or "complete"
            tick["result"] = {
                "task_id": result.get("task", {}).get("id"),
                "run_id": result.get("run", {}).get("id"),
            }
        except Exception as exc:
            tick["status"] = "error"
            tick["error"] = str(exc)
        tick["completed_at"] = self.now()
        self._save_tick(tick)
        return tick

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _save_task(self, task: JsonDict) -> None:
        task["updated_at"] = self.now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO thought_tasks (
                    id, status, source, source_thread_id, cluster_id,
                    created_at, updated_at, last_run_at, cooldown_until,
                    run_count, priority, payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    source=excluded.source,
                    source_thread_id=excluded.source_thread_id,
                    cluster_id=excluded.cluster_id,
                    updated_at=excluded.updated_at,
                    last_run_at=excluded.last_run_at,
                    cooldown_until=excluded.cooldown_until,
                    run_count=excluded.run_count,
                    priority=excluded.priority,
                    payload=excluded.payload
                """,
                (
                    task["id"],
                    task.get("status") or "open",
                    task.get("source") or "user",
                    task.get("source_thread_id") or None,
                    task.get("cluster_id") or None,
                    float(task.get("created_at") or self.now()),
                    float(task.get("updated_at") or self.now()),
                    task.get("last_run_at"),
                    task.get("cooldown_until"),
                    int(task.get("run_count") or 0),
                    float(task.get("priority") or 0.0),
                    _json_dumps(task),
                ),
            )
            conn.commit()

    def _save_run(
        self,
        task: JsonDict,
        output: str,
        evaluation: JsonDict,
        context: JsonDict,
        *,
        reflection: Optional[JsonDict] = None,
    ) -> JsonDict:
        created = self.now()
        reflection_payload = reflection if isinstance(reflection, dict) else {}
        thought_trace = (
            reflection_payload.get("thought_trace")
            if isinstance(reflection_payload.get("thought_trace"), list)
            else []
        )
        run: JsonDict = {
            "id": f"reflection-run-{int(created * 1000)}-{uuid4().hex[:8]}",
            "task_id": task["id"],
            "input_context_ids": context.get("input_context_ids") or [],
            "output": output,
            "compact_note": self._compact_note(task, output, evaluation),
            "novelty": _safe_float(evaluation.get("novelty"), 0.45),
            "uncertainty_delta": _safe_float(evaluation.get("uncertainty_delta"), 0.35),
            "usefulness": _safe_float(evaluation.get("usefulness"), 0.45),
            "repetition": _safe_float(evaluation.get("repetition"), 0.0),
            "should_continue": bool(evaluation.get("continue")),
            "should_surface_to_user": bool(evaluation.get("should_surface_to_user")),
            "created_at": created,
            "evaluation": evaluation,
            "thought": str(reflection_payload.get("thought") or ""),
            "thought_trace": thought_trace,
            "thought_trace_count": len(thought_trace),
            "generation": (
                reflection_payload.get("generation")
                if isinstance(reflection_payload.get("generation"), dict)
                else {}
            ),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reflection_runs (
                    id, task_id, created_at, usefulness,
                    should_surface_to_user, payload
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run["id"],
                    task["id"],
                    created,
                    float(run["usefulness"]),
                    1 if run["should_surface_to_user"] else 0,
                    _json_dumps(run),
                ),
            )
            conn.commit()
        self._record_work_run(task, run)
        self._mirror_reflection_note(task, run)
        return run

    def _record_work_run(self, task: JsonDict, run: JsonDict) -> None:
        """Project one compact reflection receipt into the shared work ledger."""

        if self.work_run_store is None:
            return
        try:
            self.work_run_store.upsert(
                reflection_run_receipt(task, run), source="reflection"
            )
        except Exception:
            # The canonical reflection row is already committed. A future
            # Activity read can backfill this receipt if the ledger is busy.
            logger.warning(
                "Could not record reflection run %s in the work ledger",
                run.get("id"),
                exc_info=True,
            )

    def _save_tick(self, tick: JsonDict) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO scheduler_ticks (
                    id, mode, task_id, candidate_count, status,
                    created_at, completed_at, payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    task_id=excluded.task_id,
                    candidate_count=excluded.candidate_count,
                    status=excluded.status,
                    completed_at=excluded.completed_at,
                    payload=excluded.payload
                """,
                (
                    tick["id"],
                    tick.get("mode") or "manual",
                    tick.get("task_id"),
                    int(tick.get("candidate_count") or 0),
                    tick.get("status") or "unknown",
                    float(tick.get("created_at") or self.now()),
                    tick.get("completed_at"),
                    _json_dumps(tick),
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Scoring and selection
    # ------------------------------------------------------------------
    def _refresh_task_scores(self, task: JsonDict) -> JsonDict:
        now = self.now()
        last_run_at = task.get("last_run_at")
        if last_run_at:
            age_days = max(0.0, (now - float(last_run_at)) / 86400.0)
        else:
            age_days = max(0.0, (now - float(task.get("created_at") or now)) / 86400.0)
        staleness = min(1.0, age_days / 14.0)
        task["staleness"] = staleness

        run_count = max(0, int(task.get("run_count") or 0))
        task["saturation"] = min(1.0, run_count / max(1, self._depth_budget(task)))
        user_recurrence = _safe_float(task.get("user_recurrence"), 0.0)
        self_recurrence = _safe_float(task.get("self_recurrence"), 0.0)
        total_recurrence = max(1.0, user_recurrence + self_recurrence)
        self_echo_ratio = self_recurrence / total_recurrence
        task["self_echo_ratio"] = round(self_echo_ratio, 4)
        echo_penalty = min(0.20, DEFAULT_ECHO_DAMPING * self_echo_ratio)
        priority = (
            0.35 * _safe_float(task.get("utility"), 0.55)
            + 0.25 * _safe_float(task.get("uncertainty"), 0.55)
            + 0.20 * user_recurrence
            + 0.10 * _safe_float(task.get("staleness"), 0.0)
            + 0.10 * _safe_float(task.get("progress_slope"), 0.0)
            - 0.30 * _safe_float(task.get("saturation"), 0.0)
            - echo_penalty
        )
        task["priority"] = round(max(0.0, min(1.0, priority)), 4)
        return task

    def _runnable_candidates(self, *, force: bool = False) -> List[JsonDict]:
        tasks = self.list_tasks(limit=200)
        candidates: List[JsonDict] = []
        for task in tasks:
            self._refresh_task_scores(task)
            self._save_task(task)
            if force or self._task_is_runnable(task):
                candidates.append(task)
        candidates.sort(key=lambda item: float(item.get("priority") or 0), reverse=True)
        return candidates[:DEFAULT_TOP_K]

    def _sample_task(
        self,
        candidates: Sequence[JsonDict],
        *,
        seed: Optional[int] = None,
    ) -> Optional[JsonDict]:
        if not candidates:
            return None
        rng = random.Random(seed)
        weights: List[float] = []
        for task in candidates:
            priority = float(task.get("priority") or 0.0)
            weights.append(math.exp(priority / DEFAULT_EXPLORATION_TEMPERATURE))
        total = sum(weights)
        if total <= 0:
            return candidates[0]
        target = rng.random() * total
        running = 0.0
        for task, weight in zip(candidates, weights):
            running += weight
            if running >= target:
                return task
        return candidates[-1]

    def _task_is_runnable(self, task: JsonDict) -> bool:
        status = str(task.get("status") or "").strip().lower()
        if status not in RUNNABLE_STATUSES:
            return False
        cooldown = task.get("cooldown_until")
        if cooldown and float(cooldown) > self.now():
            return False
        return int(task.get("run_count") or 0) < self._depth_budget(task)

    def _not_runnable_reason(self, task: JsonDict) -> str:
        status = str(task.get("status") or "").strip().lower()
        if status not in RUNNABLE_STATUSES:
            return f"status:{status or 'unknown'}"
        cooldown = task.get("cooldown_until")
        if cooldown and float(cooldown) > self.now():
            return "cooldown"
        if int(task.get("run_count") or 0) >= self._depth_budget(task):
            return "depth_budget"
        return "unknown"

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

    def _seed_candidate_tasks(self, *, mode: str = "manual") -> List[JsonDict]:
        normalized_mode = str(mode or "manual").strip().lower()
        if normalized_mode not in {"manual", "tool", "user", "idle", "nightly"}:
            return []
        existing_keys = {
            str((task.get("metadata") or {}).get("candidate_key") or "")
            for task in self.list_tasks(limit=200)
            if isinstance(task.get("metadata"), dict)
        }
        seeded: List[JsonDict] = []
        for candidate in self._recent_conversation_candidates(limit=2):
            key = f"conversation:{candidate['name']}"
            if key in existing_keys:
                continue
            seeded.append(
                self.create_task(
                    title=f"Reflect on recent thread: {candidate['display_name']}",
                    question=(
                        "Find one unresolved question, useful follow-up, or "
                        "conversation starter from this recent thread."
                    ),
                    source_thread_id=candidate["name"],
                    source="reflection",
                    cluster_id=key,
                    metadata={
                        "candidate_key": key,
                        "candidate_source": "recent_thread",
                    },
                    utility=0.50,
                    uncertainty=0.55,
                )
            )
            existing_keys.add(key)
        for candidate in self._memory_candidate_items(limit=4):
            key = f"memory:{candidate['key']}"
            if key in existing_keys:
                continue
            seeded.append(
                self.create_task(
                    title=f"Reflect on memory: {candidate['key']}",
                    question=(
                        "Review this memory for one useful follow-up, unresolved "
                        "question, or connection to investigate later."
                    ),
                    source="reflection",
                    cluster_id=key,
                    memory_keys=[candidate["key"]],
                    metadata={"candidate_key": key, "candidate_source": "memory"},
                    utility=candidate["utility"],
                    uncertainty=0.55,
                )
            )
            existing_keys.add(key)
        return seeded

    def _recent_conversation_candidates(self, *, limit: int = 2) -> List[JsonDict]:
        try:
            conversations = conversation_store.list_conversations(include_metadata=True)
        except Exception:
            return []
        candidates: List[JsonDict] = []
        for item in conversations:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("path") or "").strip()
            if not name:
                continue
            sensitivity = str(item.get("sensitivity") or "").strip().lower()
            privacy = str(item.get("privacy_mode") or "").strip().lower()
            if sensitivity in {"protected", "secret"} or privacy in {
                "protected",
                "secret",
            }:
                continue
            if int(item.get("message_count") or 0) <= 0:
                continue
            candidates.append(
                {
                    "name": name,
                    "display_name": str(item.get("display_name") or name),
                    "updated_at": _safe_timestamp(
                        item.get("updated_at") or item.get("created_at")
                    ),
                }
            )
        candidates.sort(
            key=lambda item: float(item.get("updated_at") or 0), reverse=True
        )
        return candidates[: max(0, limit)]

    def _memory_candidate_items(self, *, limit: int = 4) -> List[JsonDict]:
        try:
            from app.tools import memory as memory_tools

            manager = getattr(memory_tools, "_MANAGER", None)
        except Exception:
            manager = None
        if manager is None or not hasattr(manager, "iter_items"):
            return []
        try:
            items = manager.iter_items(include_pruned=False, touch=False)
        except Exception:
            return []
        now = self.now()
        candidates: List[JsonDict] = []
        for key, item in items:
            if not isinstance(item, dict):
                continue
            sensitivity = str(item.get("sensitivity") or "mundane").lower()
            if sensitivity in {"protected", "secret"}:
                continue
            lifecycle = str(item.get("lifecycle") or "").lower()
            importance = _safe_float(item.get("importance"), 0.0)
            review_at = _safe_timestamp(item.get("review_at"))
            updated_at = max(
                _safe_timestamp(item.get("updated_at")),
                _safe_timestamp(item.get("last_confirmed_at")),
                _safe_timestamp(item.get("created_at")),
            )
            is_recent = updated_at > 0 and (now - updated_at) <= 7 * 86400
            is_review_due = review_at > 0 and review_at <= now
            if not (
                item.get("pinned")
                or importance >= 0.70
                or lifecycle == "reviewable"
                or is_review_due
                or is_recent
            ):
                continue
            utility = max(
                0.45,
                min(
                    0.85,
                    importance
                    + (0.10 if item.get("pinned") else 0.0)
                    + (0.10 if is_review_due or lifecycle == "reviewable" else 0.0),
                ),
            )
            candidates.append(
                {"key": str(key), "utility": utility, "updated_at": updated_at}
            )
        candidates.sort(
            key=lambda item: (
                float(item.get("utility") or 0),
                item.get("updated_at") or 0,
            ),
            reverse=True,
        )
        return candidates[: max(0, limit)]

    # ------------------------------------------------------------------
    # Context and LLM
    # ------------------------------------------------------------------
    def _initial_task_judgment(
        self,
        *,
        question: str,
        title: str,
        metadata: JsonDict,
    ) -> JsonDict:
        ctx = ModelContext(
            system_prompt=(
                "Judge whether a candidate Float thought task is worth one "
                "bounded reflection pass. Return only JSON with utility and "
                "uncertainty scores from 0 to 1."
            ),
            metadata={
                "workflow": {
                    "id": "background_reflection",
                    "role": "scorer",
                    "supports_background": True,
                }
            },
        )
        prompt = (
            f"Title: {title or '(none)'}\n"
            f"Question: {question}\n"
            f"Metadata: {_clean_text(_json_dumps(metadata), limit=800)}\n\n"
            'Return JSON: {"utility": number, "uncertainty": number, '
            '"reason": string}.'
        )
        response = self._generate(
            prompt,
            context=ctx,
            session_id="reflection-task-scorer",
            response_format="json_object",
        )
        parsed = _extract_json_object(_response_text(response)) or {}
        return {
            "utility": _safe_float(parsed.get("utility"), 0.55),
            "uncertainty": _safe_float(parsed.get("uncertainty"), 0.55),
            "reason": _clean_text(parsed.get("reason"), limit=300),
        }

    def _retrieve_context(self, task: JsonDict) -> JsonDict:
        context_parts: List[str] = []
        input_ids: List[str] = []
        source_thread_id = str(task.get("source_thread_id") or "").strip()
        if not source_thread_id and isinstance(task.get("metadata"), dict):
            source_mode = str(task["metadata"].get("source_mode") or "").lower()
            if source_mode == "recent":
                recent = self._recent_conversation_candidates(limit=1)
                if recent:
                    source_thread_id = str(recent[0].get("name") or "")
        if source_thread_id:
            try:
                compaction = build_compaction(
                    source_thread_id,
                    keep_last=8,
                    max_summary_chars=3000,
                    summary_mode="deterministic",
                    summary_workflow="conversation_handoff",
                )
                summary = _clean_text(compaction.get("summary_preview"), limit=3000)
                if summary:
                    context_parts.append(f"Conversation carry-over:\n{summary}")
                    input_ids.append(f"conversation:{source_thread_id}:summary")
                messages = compaction.get("messages") or []
                tail_lines = []
                for message in messages[-8:]:
                    if not isinstance(message, dict):
                        continue
                    role = str(message.get("role") or "unknown")
                    text = _clean_text(message_text(message), limit=500)
                    if text:
                        tail_lines.append(f"{role}: {text}")
                if tail_lines:
                    context_parts.append(
                        "Recent thread tail:\n" + "\n".join(tail_lines)
                    )
                    input_ids.append(f"conversation:{source_thread_id}:tail")
            except Exception:
                pass

        memory_context = self._memory_context(task)
        if memory_context:
            context_parts.append(memory_context["text"])
            input_ids.extend(memory_context["ids"])

        event_id = str(task.get("event_id") or "").strip()
        if event_id:
            event = calendar_store.load_event(event_id)
            if isinstance(event, dict) and event:
                context_parts.append(
                    "Linked calendar task/event:\n"
                    + _clean_text(json.dumps(event, ensure_ascii=False), limit=1200)
                )
                input_ids.append(f"calendar_event:{event_id}")

        query = str(task.get("question") or "")
        rag_snippets = self._rag_context(query)
        if rag_snippets:
            context_parts.append(rag_snippets["text"])
            input_ids.extend(rag_snippets["ids"])

        prior_runs = self.list_runs(str(task.get("id") or ""), limit=4)
        if prior_runs:
            notes = [
                _clean_text(run.get("compact_note") or run.get("output"), limit=700)
                for run in reversed(prior_runs)
            ]
            notes = [item for item in notes if item]
            if notes:
                context_parts.append("Prior reflection notes:\n" + "\n\n".join(notes))
                input_ids.extend(
                    [f"reflection_run:{run.get('id')}" for run in prior_runs]
                )

        return {
            "text": "\n\n".join(part for part in context_parts if part).strip(),
            "input_context_ids": input_ids,
        }

    def _memory_context(self, task: JsonDict) -> Optional[JsonDict]:
        keys = [
            str(item).strip()
            for item in (task.get("memory_keys") or [])
            if str(item or "").strip()
        ]
        if not keys:
            return None
        try:
            from app.tools import memory as memory_tools

            manager = getattr(memory_tools, "_MANAGER", None)
        except Exception:
            manager = None
        if manager is None:
            return None
        lines: List[str] = []
        ids: List[str] = []
        for key in keys[:8]:
            try:
                item = manager.get_item(key, include_pruned=False, touch=False)
            except TypeError:
                item = manager.get_item(key)
            except Exception:
                item = None
            if not isinstance(item, dict):
                continue
            sensitivity = str(item.get("sensitivity") or "mundane").lower()
            if sensitivity in {"protected", "secret"}:
                continue
            value = item.get("value")
            lines.append(f"- {key}: {_clean_text(value, limit=800)}")
            ids.append(f"memory:{key}")
        if not lines:
            return None
        return {"text": "Linked memories:\n" + "\n".join(lines), "ids": ids}

    def _rag_context(self, query: str) -> Optional[JsonDict]:
        service = get_rag_service(self.config, raise_http=False)
        if not service or not query:
            return None
        matches: List[JsonDict] = []
        try:
            matches = service.query(query, top_k=4) or []
        except Exception:
            matches = []
        lines: List[str] = []
        ids: List[str] = []
        for match in matches[:4]:
            if not isinstance(match, dict):
                continue
            meta = (
                match.get("metadata") if isinstance(match.get("metadata"), dict) else {}
            )
            sensitivity = str(meta.get("sensitivity") or "mundane").lower()
            if sensitivity in {"protected", "secret"}:
                continue
            source = meta.get("source") or meta.get("root_source") or match.get("id")
            text = _clean_text(match.get("text"), limit=500)
            if text:
                lines.append(f"- {source}: {text}")
                ids.append(str(source or match.get("id") or "rag"))
        if not lines:
            return None
        return {
            "text": "Retrieved knowledge snippets:\n" + "\n".join(lines),
            "ids": ids,
        }

    def _generate(
        self,
        prompt: str,
        *,
        context: ModelContext,
        session_id: str,
        response_format: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        if self.llm_generate is not None:
            return self.llm_generate(
                prompt,
                context=context,
                session_id=session_id,
                response_format=response_format,
                **kwargs,
            )
        try:
            from app import routes as routes_module

            return routes_module.llm_service.generate(
                prompt,
                context=context,
                session_id=session_id,
                response_format=response_format,
                **kwargs,
            )
        except Exception:
            return None

    def _run_reflection_pass(self, task: JsonDict, context: JsonDict) -> JsonDict:
        task_metadata = (
            task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        )
        proposal_kind = str(task_metadata.get("proposal_kind") or "").strip().lower()
        is_skill_proposal = proposal_kind == "skill_markdown"
        if is_skill_proposal:
            system_prompt = (
                "You are Float's bounded skill-drafting worker. Produce one proposed "
                "Hermes-style markdown skill document for the requested skill id. "
                "Return markdown only, without a code fence or surrounding commentary. "
                "Use a clear title and concise operational sections. Preserve useful "
                "existing guidance when supplied, but do not invent unavailable tools or "
                "permissions. This is a proposal only: never claim it was saved, enabled, "
                "or applied."
            )
        else:
            system_prompt = (
                "You are Float's bounded reflection worker. Given one unresolved "
                "thought task and relevant memory, do exactly one thinking pass. "
                "Do not solve everything. Do not start unrelated threads. Try to "
                "reduce tension. Return concise plain text with: current best "
                "synthesis, what changed, remaining uncertainty, whether another "
                "pass is worth it, and whether this should surface to the user. "
                "This reflection pass has no tool access. Do not claim that you "
                "saved, changed, delegated, or executed anything."
            )
        ctx = ModelContext(
            system_prompt=system_prompt,
            metadata={
                "workflow": {
                    "id": "background_reflection",
                    "role": "background",
                    "supports_background": True,
                },
                "reflection_task_id": task.get("id"),
                "proposal_kind": proposal_kind or None,
                "proposal_target": task_metadata.get("skill_id") or None,
            },
        )
        prompt = (
            f"Thought task: {task.get('title')}\n"
            f"Question: {task.get('question')}\n\n"
            f"Relevant context:\n{context.get('text') or '(none)'}"
        )
        generation_kwargs: JsonDict = {}
        requested_model = _clean_text(task_metadata.get("requested_model"), limit=500)
        if requested_model:
            generation_kwargs["model"] = requested_model
        response = self._generate(
            prompt,
            context=ctx,
            session_id=f"reflection:{task.get('id')}",
            response_format=None,
            **generation_kwargs,
        )
        cleaner = _skill_markdown_proposal if is_skill_proposal else _clean_text
        text = cleaner(_response_text(response), limit=8000)
        thought_trace = _response_thought_trace(response)
        reflection = {
            "output": text,
            "thought": "".join(str(item.get("text") or "") for item in thought_trace),
            "thought_trace": thought_trace,
            "generation": _response_generation_metadata(response),
        }
        if text:
            return reflection
        if is_skill_proposal:
            return reflection
        reflection["output"] = (
            "Current best synthesis: not enough model output was available for a "
            "substantive pass.\nWhat changed: no new structure.\nRemaining "
            "uncertainty: high.\nAnother pass: no.\nSurface to user: no."
        )
        return reflection

    def _evaluate_reflection(
        self, task: JsonDict, output: str, context: JsonDict
    ) -> JsonDict:
        ctx = ModelContext(
            system_prompt=(
                "Evaluate a Float reflection harshly. Return only JSON with keys: "
                "novelty, usefulness, uncertainty_delta, repetition, continue, "
                "should_surface_to_user, reason, cooldown_seconds."
            ),
            metadata={
                "workflow": {
                    "id": "background_reflection",
                    "role": "verifier",
                    "supports_background": True,
                },
                "reflection_task_id": task.get("id"),
            },
        )
        prompt = (
            f"Task question: {task.get('question')}\n\n"
            f"Reflection output:\n{output}\n\n"
            "Score 0-1: novelty, usefulness, uncertainty_delta, repetition. "
            "Set continue and should_surface_to_user as booleans."
        )
        response = self._generate(
            prompt,
            context=ctx,
            session_id=f"reflection-eval:{task.get('id')}",
            response_format="json_object",
        )
        parsed = _extract_json_object(_response_text(response)) or {}
        if not parsed:
            # Deterministic fallback used when the model cannot provide JSON.
            word_count = len(output.split())
            parsed = {
                "novelty": 0.45 if word_count >= 80 else 0.25,
                "usefulness": 0.45 if word_count >= 80 else 0.30,
                "uncertainty_delta": 0.35
                if "remaining uncertainty" in output.lower()
                else 0.25,
                "repetition": 0.25,
                "continue": False,
                "should_surface_to_user": False,
                "reason": "fallback evaluator",
                "cooldown_seconds": 24 * 60 * 60,
            }
        evaluation = {
            "novelty": _safe_float(parsed.get("novelty"), 0.35),
            "usefulness": _safe_float(parsed.get("usefulness"), 0.35),
            "uncertainty_delta": _safe_float(parsed.get("uncertainty_delta"), 0.25),
            "repetition": _safe_float(parsed.get("repetition"), 0.25),
            "continue": bool(parsed.get("continue")),
            "should_surface_to_user": bool(parsed.get("should_surface_to_user")),
            "reason": _clean_text(parsed.get("reason"), limit=500),
            "cooldown_seconds": max(
                0, _safe_int(parsed.get("cooldown_seconds"), 86400)
            ),
        }
        if evaluation["usefulness"] < DEFAULT_SURFACE_THRESHOLD:
            evaluation["should_surface_to_user"] = False
        return evaluation

    # ------------------------------------------------------------------
    # Post-run state
    # ------------------------------------------------------------------
    def _update_task_after_run(
        self,
        task: JsonDict,
        run: JsonDict,
        evaluation: JsonDict,
    ) -> JsonDict:
        now = self.now()
        run_count = int(task.get("run_count") or 0) + 1
        task["run_count"] = run_count
        task["last_run_at"] = now
        task["self_recurrence"] = min(
            1.0, _safe_float(task.get("self_recurrence")) + 0.12
        )
        task["progress_slope"] = max(
            0.0,
            min(
                1.0,
                (
                    _safe_float(evaluation.get("novelty"), 0.0)
                    + _safe_float(evaluation.get("uncertainty_delta"), 0.0)
                )
                / 2.0,
            ),
        )
        cooldown_seconds = max(0, int(evaluation.get("cooldown_seconds") or 0))
        budget = self._depth_budget(task)
        status = str(task.get("status") or "open")
        hard_stopped = False
        if run_count >= budget:
            status = "cooling"
            hard_stopped = True
            cooldown_seconds = max(cooldown_seconds, 24 * 60 * 60)
        if _safe_float(evaluation.get("novelty"), 0.0) < 0.35:
            status = "cooling"
            hard_stopped = True
            cooldown_seconds = max(cooldown_seconds, 24 * 60 * 60)
        if _safe_float(evaluation.get("usefulness"), 0.0) < 0.40:
            status = "cooling"
            hard_stopped = True
            cooldown_seconds = max(cooldown_seconds, 24 * 60 * 60)
        if _safe_float(evaluation.get("repetition"), 0.0) > 0.70:
            status = "archived"
        if (
            not evaluation.get("continue")
            and status not in {"archived"}
            and not hard_stopped
        ):
            status = "resolved"
        task["status"] = status
        task["cooldown_until"] = (now + cooldown_seconds) if cooldown_seconds else None
        self._refresh_task_scores(task)
        self._save_task(task)
        return task

    def _compact_note(self, task: JsonDict, output: str, evaluation: JsonDict) -> str:
        return "\n".join(
            [
                f"Claim: {_clean_text(output, limit=650)}",
                f"Why it matters: usefulness={_safe_float(evaluation.get('usefulness')):.2f}; novelty={_safe_float(evaluation.get('novelty')):.2f}.",
                f"Remaining uncertainty: {_safe_float(task.get('uncertainty'), 0.55):.2f}; delta={_safe_float(evaluation.get('uncertainty_delta')):.2f}.",
                f"Next useful action: {'continue after cooldown' if evaluation.get('continue') else 'pause processing for now'}.",
            ]
        )

    def _mirror_reflection_note(self, task: JsonDict, run: JsonDict) -> None:
        note = str(run.get("compact_note") or "").strip()
        if not note:
            return
        metadata = {
            "kind": "reflection",
            "type": "reflection",
            "source": f"reflection:{run.get('id')}",
            "task_id": task.get("id"),
            "title": task.get("title"),
            "self_originated": True,
            "sensitivity": "personal",
        }
        try_ingest_text(note, metadata, config=self.config, mirror_vector=True)


def build_reflection_service(config: Optional[JsonDict] = None) -> ReflectionService:
    return ReflectionService(config or app_config.load_config())
