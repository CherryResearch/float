from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from app.model_registry import (
    canonical_model_alias,
    get_download_allow_patterns,
    model_supports_download_job,
    resolve_model_alias,
)
from app.services.model_download_service import get_jobs_state as _get_jobs_state
from app.services.model_download_service import job_progress as _job_progress
from app.services.model_download_service import path_matches_any as _path_matches_any
from app.services.model_download_service import (
    refresh_job_status as _refresh_job_status,
)
from app.services.model_download_service import (
    resolve_models_dir as _resolve_models_dir,
)
from app.services.model_download_service import (
    start_download_process as _start_download_process,
)
from app.services.model_download_service import terminate_proc as _terminate_proc
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class ModelJobRequest(BaseModel):
    model: str
    path: Optional[str] = None


@router.post("/models/jobs")
async def create_model_job(request: Request, body: ModelJobRequest):
    cfg = request.app.state.config
    token = cfg.get("hf_token") if isinstance(cfg, dict) else None
    model_alias = str(canonical_model_alias(body.model) or body.model).strip()
    repo_id = resolve_model_alias(model_alias)
    if not repo_id or str(repo_id).startswith("TODO"):
        raise HTTPException(status_code=400, detail="Unsupported model")
    if not model_supports_download_job(model_alias):
        raise HTTPException(
            status_code=400,
            detail="Model is not available for background download jobs.",
        )

    models_root = _resolve_models_dir(cfg, body.path)
    target_dir = models_root / model_alias
    target_dir.mkdir(parents=True, exist_ok=True)
    allow_patterns = get_download_allow_patterns(model_alias)

    def _norm_path(value: str) -> str:
        try:
            return str(Path(value).expanduser().resolve())
        except Exception:
            return str(Path(value).expanduser())

    # Repeated clicks must reuse one process for the same model and target.
    jobs = _get_jobs_state(request.app)
    target_key = _norm_path(str(target_dir))
    candidates = [
        job
        for job in jobs.values()
        if job.get("model") == model_alias
        and _norm_path(str(job.get("path") or "")) == target_key
    ]
    if candidates:
        candidates.sort(
            key=lambda job: job.get(
                "updated_at",
                job.get("started_at", 0),
            ),
            reverse=True,
        )
        job = candidates[0]
        _refresh_job_status(job)
        if job.get("status") == "running":
            progress = _job_progress(job)
            return {
                "job": {
                    key: value for key, value in job.items() if not key.startswith("_")
                },
                **progress,
            }
        if job.get("status") in {"paused", "error"}:
            process = _start_download_process(
                job.get("repo_id") or repo_id,
                Path(job["path"]),
                job.get("model") or model_alias,
            )
            job["_proc"] = process
            job["pid"] = process.pid
            job["status"] = "running"
            job["error"] = None
            job["updated_at"] = time.time()
            progress = _job_progress(job)
            return {
                "job": {
                    key: value for key, value in job.items() if not key.startswith("_")
                },
                **progress,
            }

    total_size = 0
    from huggingface_hub import HfApi

    try:
        from huggingface_hub.utils import GatedRepoError
    except Exception:  # pragma: no cover - fallback if import path changes
        GatedRepoError = None  # type: ignore

    api = HfApi(token=token) if token else HfApi()
    try:
        info = await asyncio.to_thread(api.model_info, repo_id, files_metadata=True)
        total_size = sum(
            int(getattr(sibling, "size", None) or 0)
            for sibling in getattr(info, "siblings", []) or []
            if _path_matches_any(
                str(
                    getattr(sibling, "rfilename", None)
                    or getattr(sibling, "path", "")
                    or ""
                ),
                allow_patterns,
            )
        )
    except Exception as exc:
        if GatedRepoError is not None and isinstance(exc, GatedRepoError):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Model access is gated. Set a Hugging Face token "
                    "(HF_TOKEN/HUGGINGFACE_HUB_TOKEN) and accept the model "
                    "license on the repo page before retrying."
                ),
            ) from exc
        total_size = 0

    job_id = str(uuid4())
    process = _start_download_process(repo_id, target_dir, model_alias)
    job = {
        "id": job_id,
        "model": model_alias,
        "repo_id": repo_id,
        "path": str(Path(target_dir).resolve()),
        "status": "running",
        "total": int(total_size),
        "error": None,
        "pid": process.pid,
        "_proc": process,
        "allow_patterns": allow_patterns,
        "started_at": time.time(),
        "updated_at": time.time(),
    }
    jobs[job_id] = job
    progress = _job_progress(job)
    return {
        "job": {key: value for key, value in job.items() if not key.startswith("_")},
        **progress,
    }


@router.get("/models/jobs")
async def list_model_jobs(
    request: Request,
    limit: int = 50,
    include_finished: bool = True,
):
    jobs = _get_jobs_state(request.app)
    rows: list[dict[str, Any]] = []
    safe_limit = max(1, min(int(limit or 50), 200))
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        _refresh_job_status(job)
        status = str(job.get("status") or "")
        if not include_finished and status in {"completed", "canceled"}:
            continue
        public = {key: value for key, value in job.items() if not key.startswith("_")}
        public.update(_job_progress(job))
        rows.append(public)
    rows.sort(
        key=lambda item: item.get("updated_at", item.get("started_at", 0)) or 0,
        reverse=True,
    )
    return {"jobs": rows[:safe_limit]}


@router.get("/models/jobs/{job_id}")
async def get_model_job(request: Request, job_id: str):
    jobs = _get_jobs_state(request.app)
    job = jobs.get(job_id)
    if not job:
        return {
            "job": {
                "id": job_id,
                "status": "unknown",
                "error": "Job not found",
            },
            "downloaded": 0,
            "total": 0,
            "percent": 0.0,
        }
    _refresh_job_status(job)
    progress = _job_progress(job)
    return {
        "job": {key: value for key, value in job.items() if not key.startswith("_")},
        **progress,
    }


@router.post("/models/jobs/{job_id}/pause")
async def pause_model_job(request: Request, job_id: str):
    jobs = _get_jobs_state(request.app)
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _refresh_job_status(job)
    if job["status"] != "running":
        return {
            "job": {key: value for key, value in job.items() if not key.startswith("_")}
        }
    _terminate_proc(job)
    job["status"] = "paused"
    job["updated_at"] = time.time()
    progress = _job_progress(job)
    return {
        "job": {key: value for key, value in job.items() if not key.startswith("_")},
        **progress,
    }


@router.post("/models/jobs/{job_id}/cancel")
async def cancel_model_job(request: Request, job_id: str):
    jobs = _get_jobs_state(request.app)
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _terminate_proc(job)
    job["status"] = "canceled"
    job["updated_at"] = time.time()
    progress = _job_progress(job)
    return {
        "job": {key: value for key, value in job.items() if not key.startswith("_")},
        **progress,
    }


@router.post("/models/jobs/{job_id}/resume")
async def resume_model_job(request: Request, job_id: str):
    jobs = _get_jobs_state(request.app)
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _refresh_job_status(job)
    if job["status"] not in {"paused", "error"}:
        return {
            "job": {key: value for key, value in job.items() if not key.startswith("_")}
        }
    process = _start_download_process(
        job["repo_id"],
        Path(job["path"]),
        job.get("model"),
    )
    job["_proc"] = process
    job["pid"] = process.pid
    job["status"] = "running"
    job["error"] = None
    job["updated_at"] = time.time()
    progress = _job_progress(job)
    return {
        "job": {key: value for key, value in job.items() if not key.startswith("_")},
        **progress,
    }


__all__ = [
    "ModelJobRequest",
    "router",
]
