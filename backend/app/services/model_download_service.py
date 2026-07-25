from __future__ import annotations

import os
import subprocess
import sys
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, Optional

from app import config as app_config


def path_matches_any(rel_posix: str, patterns: Optional[list[str]]) -> bool:
    if not patterns:
        return True
    return any(fnmatch(rel_posix, pattern) for pattern in patterns)


def folder_size_bytes(
    root: Path,
    *,
    include_patterns: Optional[list[str]] = None,
) -> int:
    try:
        if root.is_file():
            return int(root.stat().st_size)
    except Exception:
        return 0
    total = 0
    for path in root.rglob("*"):
        try:
            if not path.is_file():
                continue
            if ".cache" in path.parts:
                continue
            name = path.name.lower()
            if (
                name.endswith(".incomplete")
                or name.endswith(".lock")
                or name.endswith(".metadata")
            ):
                continue
            relative_path = path.relative_to(root).as_posix()
            if not path_matches_any(relative_path, include_patterns):
                continue
            total += path.stat().st_size
        except Exception:
            continue
    return total


def get_jobs_state(app: Any) -> Dict[str, dict]:
    if not hasattr(app.state, "model_jobs"):
        app.state.model_jobs = {}
    return app.state.model_jobs


def resolve_models_dir(cfg: dict, requested_path: Optional[str]) -> Path:
    requested = requested_path or cfg.get(
        "models_folder",
        str(app_config.DEFAULT_MODELS_DIR),
    )
    try:
        path = Path(requested)
    except Exception:
        return Path(cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR))
    if (not path.is_absolute()) and (not path.exists()):
        return Path(cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR))
    return path


def start_download_process(
    repo_id: str,
    target_dir: Path,
    model_alias: Optional[str] = None,
) -> subprocess.Popen:
    command = [
        sys.executable,
        "-m",
        "app.download_worker",
        "--repo",
        repo_id,
        "--dir",
        str(target_dir),
    ]
    if model_alias:
        command.extend(["--model", model_alias])

    env = os.environ.copy()
    if env.get("HF_HUB_ENABLE_HF_TRANSFER") == "1":
        env.setdefault("HF_XET_HIGH_PERFORMANCE", "1")
        env.pop("HF_HUB_ENABLE_HF_TRANSFER", None)
    env.setdefault("HF_XET_HIGH_PERFORMANCE", "1")
    return subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


def job_progress(job: dict) -> dict:
    downloaded = 0
    try:
        path = Path(job["path"])
        if path.exists():
            allow_patterns = job.get("allow_patterns")
            downloaded = folder_size_bytes(
                path,
                include_patterns=(
                    allow_patterns if isinstance(allow_patterns, list) else None
                ),
            )
    except Exception:
        downloaded = 0
    total = int(job.get("total", 0) or 0)
    percent = (downloaded / total) if total > 0 else 0.0
    return {
        "downloaded": downloaded,
        "total": total,
        "percent": min(1.0, percent),
    }


def refresh_job_status(job: dict) -> None:
    process: Optional[subprocess.Popen] = job.get("_proc")
    if process is None:
        return
    code = process.poll()
    if code is None:
        job["status"] = "running"
        return
    job["_proc"] = None
    job["pid"] = None
    job["updated_at"] = time.time()
    if code == 0:
        job["status"] = "completed"
    else:
        job["status"] = "error"
        job["error"] = f"process exited with code {code}"


def terminate_proc(job: dict) -> None:
    process: Optional[subprocess.Popen] = job.get("_proc")
    if process is not None and process.poll() is None:
        try:
            process.terminate()
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        job["_proc"] = None
        job["pid"] = None


__all__ = [
    "folder_size_bytes",
    "get_jobs_state",
    "job_progress",
    "path_matches_any",
    "refresh_job_status",
    "resolve_models_dir",
    "start_download_process",
    "terminate_proc",
]
