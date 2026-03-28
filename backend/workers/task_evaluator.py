import asyncio
import logging
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from app import routes as routes_module

logger = logging.getLogger(__name__)


async def evaluate_pending_tasks(app: FastAPI) -> None:
    """Background task to evaluate queued tasks.

    Tasks pushed onto ``app.state.pending_tasks`` are processed
    sequentially.  For each completed task an entry is emitted to
    ``app.state.thought_queue`` so the frontend can display updates in
    the thoughts panel.
    """
    queue: asyncio.Queue = app.state.pending_tasks
    while True:
        try:
            task: Any = queue.get_nowait()
        except asyncio.QueueEmpty:
            await asyncio.sleep(1)
            continue
        try:
            # Placeholder for real evaluation logic
            result = {"status": "done", "task": task}
            task_id = None
            agent_label = None
            if isinstance(task, dict):
                task_id = (
                    task.get("id")
                    or task.get("task_id")
                    or task.get("agent_id")
                    or task.get("name")
                    or task.get("title")
                )
                agent_label = task.get("agent") or task.get("name") or task.get("title")
            if not task_id:
                task_id = f"worker-{uuid4()}"
            await routes_module.publish_console_event(  # type: ignore[attr-defined]
                app,
                {
                    "type": "task",
                    "task_id": task_id,
                    "content": f"completed: {task}",
                    "result": result,
                    "status": "completed",
                    "agent_label": agent_label or str(task_id),
                },
                default_agent=str(task_id),
            )
            # Also emit a generic notification for completed tasks
            try:
                routes_module.emit_notification(  # type: ignore[attr-defined]
                    app,
                    title="Task completed",
                    body=str(task),
                    category="task",
                    data={"result": "done"},
                )
            except Exception:
                pass
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("failed to evaluate task")
