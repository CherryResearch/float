from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.reflection_service import ReflectionService, build_reflection_service
from app.utils import verify_signature

_SERVICE: Optional[ReflectionService] = None
_DEFAULT = object()


def set_reflection_service(service: Optional[ReflectionService]) -> None:
    global _SERVICE
    _SERVICE = service


def _service() -> ReflectionService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = build_reflection_service()
    return _SERVICE


def reflect(
    question: str,
    title: str | object = _DEFAULT,
    source_thread_id: str | object = _DEFAULT,
    source: str | object = _DEFAULT,
    patience: int | object = _DEFAULT,
    memory_keys: Optional[list[str]] | object = _DEFAULT,
    event_id: str | object = _DEFAULT,
    run_now: bool | object = _DEFAULT,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    """Create a bounded thought task and optionally run one reflection pass."""

    payload: Dict[str, Any] = {"question": question}
    if title is not _DEFAULT:
        payload["title"] = title
    if source_thread_id is not _DEFAULT:
        payload["source_thread_id"] = source_thread_id
    if source is not _DEFAULT:
        payload["source"] = source
    if patience is not _DEFAULT:
        payload["patience"] = patience
    if memory_keys is not _DEFAULT:
        payload["memory_keys"] = memory_keys
    if event_id is not _DEFAULT:
        payload["event_id"] = event_id
    if run_now is not _DEFAULT:
        payload["run_now"] = run_now
    verify_signature(signature, user, "reflect", payload)
    title_text = "" if title is _DEFAULT else str(title or "")
    thread_id = "" if source_thread_id is _DEFAULT else str(source_thread_id or "")
    source_text = "user" if source is _DEFAULT else str(source or "user")
    patience_value = 1 if patience is _DEFAULT else int(patience or 0)
    keys = [] if memory_keys is _DEFAULT or memory_keys is None else list(memory_keys)
    event_text = "" if event_id is _DEFAULT else str(event_id or "")
    run_now_flag = False if run_now is _DEFAULT else bool(run_now)
    task = _service().create_task(
        title=title_text,
        question=question,
        source_thread_id=thread_id,
        source=source_text,
        patience=patience_value,
        memory_keys=keys,
        event_id=event_text,
        metadata={"created_by": "tool", "user": user},
    )
    result: Dict[str, Any] = {"status": "created", "task": task}
    if run_now_flag:
        result["run"] = _service().run_task(str(task["id"]), force=True)
        result["status"] = "ran"
    return result


def list_reflections(
    status: str | object = _DEFAULT,
    limit: int | object = _DEFAULT,
    include_runs: bool | object = _DEFAULT,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    """List recent/open reflection tasks."""

    payload: Dict[str, Any] = {}
    if status is not _DEFAULT:
        payload["status"] = status
    if limit is not _DEFAULT:
        payload["limit"] = limit
    if include_runs is not _DEFAULT:
        payload["include_runs"] = include_runs
    verify_signature(signature, user, "list_reflections", payload)
    status_text = "" if status is _DEFAULT else str(status or "")
    limit_value = 20 if limit is _DEFAULT else int(limit or 20)
    include_runs_flag = False if include_runs is _DEFAULT else bool(include_runs)
    tasks = _service().list_tasks(
        status=status_text,
        limit=limit_value,
        include_runs=include_runs_flag,
    )
    return {"status": "ok", "count": len(tasks), "tasks": tasks}


__all__ = ["reflect", "list_reflections", "set_reflection_service"]
