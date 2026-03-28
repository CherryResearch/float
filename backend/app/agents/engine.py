from __future__ import annotations

from typing import Any, Dict, List, Mapping

from app.tasks import (celery_app, execute_etl_pipeline, execute_tool,
                       generate_embedding_task, long_running_task)
from celery import chain
from celery.result import AsyncResult


class MultiAgentEngine:
    """Simple engine that orchestrates a sequence of Celery tasks."""

    def __init__(self) -> None:
        self.agents: Mapping[str, Any] = {
            "execute_tool": execute_tool,
            "long_running_task": long_running_task,
            "execute_etl_pipeline": execute_etl_pipeline,
            "generate_embedding": generate_embedding_task,
        }

    def plan_and_execute(self, plan: List[Dict[str, Any]]) -> AsyncResult:
        """Schedule a chain of agents according to *plan*.

        Each plan item must contain ``agent`` and optional ``args``/``kwargs``.
        Returns the Celery ``AsyncResult`` for the chain.
        """
        if not plan:
            raise ValueError("Plan must contain at least one step")

        tasks = []
        for step in plan:
            name = step.get("agent")
            if name not in self.agents:
                raise ValueError(f"Unknown agent: {name}")
            args = step.get("args", [])
            kwargs = step.get("kwargs", {})
            tasks.append(self.agents[name].s(*args, **kwargs))

        return chain(*tasks).apply_async()

    def result(self, task_id: str) -> AsyncResult:
        """Retrieve result info for a scheduled task."""
        return AsyncResult(task_id, app=celery_app)


def get_engine() -> MultiAgentEngine:
    """Return a shared ``MultiAgentEngine`` instance."""
    return MultiAgentEngine()
