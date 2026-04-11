from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


def shutdown_server_resources(
    app: Any,
    *,
    provider_manager: Any = None,
    terminate_job_proc: Optional[Callable[[dict], None]] = None,
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "computer": None,
        "providers": None,
        "terminated_model_jobs": 0,
        "errors": [],
    }

    app_state = getattr(app, "state", None)
    computer_service = getattr(app_state, "computer_service", None)
    shutdown_runtime = getattr(computer_service, "shutdown", None)
    if callable(shutdown_runtime):
        try:
            summary["computer"] = shutdown_runtime()
        except Exception as exc:
            logger.exception("Computer service shutdown failed.")
            summary["errors"].append({"component": "computer", "error": str(exc)})

    shutdown_providers = getattr(provider_manager, "shutdown", None)
    if callable(shutdown_providers):
        try:
            summary["providers"] = shutdown_providers()
        except Exception as exc:
            logger.exception("Local provider shutdown failed.")
            summary["errors"].append({"component": "providers", "error": str(exc)})

    jobs = getattr(app_state, "model_jobs", {}) or {}
    if callable(terminate_job_proc) and isinstance(jobs, dict):
        for job in jobs.values():
            if not isinstance(job, dict):
                continue
            proc = job.get("_proc")
            is_running = proc is not None and getattr(proc, "poll", lambda: 0)() is None
            if not is_running:
                continue
            try:
                terminate_job_proc(job)
                summary["terminated_model_jobs"] += 1
                job["status"] = "canceled"
                job["updated_at"] = time.time()
            except Exception as exc:
                logger.exception("Model job shutdown failed.")
                summary["errors"].append(
                    {
                        "component": "model_jobs",
                        "job_id": str(job.get("id") or ""),
                        "error": str(exc),
                    }
                )

    return summary
