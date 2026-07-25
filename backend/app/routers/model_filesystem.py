from __future__ import annotations

import asyncio
import errno
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import config as app_config
from app.local_providers import LocalProviderManager
from app.model_registry import (
    canonical_model_alias,
    get_download_allow_patterns,
    get_local_loader,
    get_model_lane,
    get_model_metadata,
    model_allowed_in_mobile_catalog,
    model_supports_download_job,
    model_supports_images,
    model_supports_provider_lane,
    resolve_model_alias,
)
from app.services import LLMService
from app.services.model_download_service import folder_size_bytes as _folder_size_bytes
from app.services.model_download_service import get_jobs_state as _get_jobs_state
from app.services.model_download_service import job_progress as _job_progress
from app.services.model_download_service import path_matches_any as _path_matches_any
from app.services.model_download_service import (
    refresh_job_status as _refresh_job_status,
)
from app.utils.local_model_registry import (
    list_local_model_entries,
    remove_local_model_entry,
    resolve_registered_model_path,
)
from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

_llm_service: Optional[LLMService] = None
_provider_manager: Optional[LocalProviderManager] = None


def configure_model_filesystem_runtime(
    *,
    llm_service: LLMService,
    provider_manager: LocalProviderManager,
) -> None:
    """Bind the aggregate route runtime without importing ``app.routes`` back."""

    global _llm_service, _provider_manager
    _llm_service = llm_service
    _provider_manager = provider_manager


def _runtime_services() -> tuple[LLMService, LocalProviderManager]:
    if _llm_service is None or _provider_manager is None:
        raise RuntimeError("Model filesystem runtime is not configured")
    return _llm_service, _provider_manager


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 for a file in streaming fashion."""

    digest = hashlib.sha256()
    with open(path, "rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remote_manifest(
    repo_id: str,
    allow_patterns: Optional[list[str]] = None,
    token: Optional[str] = None,
) -> tuple[list[dict], int, str | None]:
    """Return the filtered file manifest, total bytes, and commit for a HF repo."""

    from huggingface_hub import HfApi

    api = HfApi(token=token) if token else HfApi()
    info = api.model_info(repo_id, files_metadata=True)
    manifest: list[dict] = []
    total = 0
    for sibling in getattr(info, "siblings", []) or []:
        path = getattr(sibling, "rfilename", None) or getattr(sibling, "path", None)
        if path is not None:
            path = str(path)
            if not _path_matches_any(path, allow_patterns):
                continue
        size = getattr(sibling, "size", None)
        sha256 = None
        try:
            lfs = getattr(sibling, "lfs", None)
            if isinstance(lfs, dict):
                sha256 = lfs.get("sha256") or lfs.get("oid")
        except Exception:
            sha256 = None
        if not sha256:
            sha = getattr(sibling, "sha", None)
            if isinstance(sha, str) and len(sha) in (40, 64):
                sha256 = sha if len(sha) == 64 else None
        if path is None:
            continue
        manifest.append(
            {
                "path": path,
                "size": int(size or 0),
                "sha256": sha256,
            }
        )
        total += int(size or 0)
    return manifest, int(total), getattr(info, "sha", None)


def _count_local_files(root: Path) -> int:
    try:
        if root.is_file():
            return 1
    except Exception:
        return 0
    count = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                count += 1
        except Exception:
            continue
    return count


def _fallback_verification_from_job(
    request: Request,
    model_name: str,
    local_dir: Optional[Path],
    installed: int,
) -> Optional[dict]:
    if local_dir is None:
        return None
    try:
        jobs = _get_jobs_state(request.app)
    except Exception:
        return None
    for job in jobs.values():
        if job.get("model") != model_name:
            continue
        try:
            _refresh_job_status(job)
        except Exception:
            continue
        if job.get("status") != "completed":
            continue
        guessed_total = int(job.get("total") or 0)
        if guessed_total <= 0:
            guessed_total = int(installed)
        checked = _count_local_files(local_dir)
        verified = checked > 0 and installed > 0
        return {
            "exists": True,
            "verified": verified,
            "expected_bytes": guessed_total,
            "installed_bytes": int(installed),
            "checked_files": int(checked),
        }
    return None


def _naive_local_verification(
    local_dir: Optional[Path],
    installed: int,
) -> Optional[dict]:
    if local_dir is None or installed <= 0:
        return None
    checked = _count_local_files(local_dir)
    if checked <= 0:
        return None
    return {
        "exists": True,
        "verified": True,
        "expected_bytes": int(installed),
        "installed_bytes": int(installed),
        "checked_files": int(checked),
    }


def _is_hf_cache_dir(models_dir: Path) -> bool:
    parts = [part.lower() for part in models_dir.parts]
    return "huggingface" in parts and "hub" in parts


def _hf_cache_model_allowed(name: str, *, allow_extras: bool) -> bool:
    """Filter noisy HF cache entries to keep selectors usable."""

    if allow_extras:
        return True
    lowered = name.lower()
    allowed_prefixes = (
        "gpt-oss",
        "llama",
        "qwen",
        "gemma",
        "mistral",
        "mixtral",
        "phi",
        "falcon",
        "zephyr",
    )
    allowed_exact = {
        "gpt-5.4",
        "gpt-5.1",
        "gpt-4.1",
        "gpt-4o-mini",
    }
    return lowered in allowed_exact or lowered.startswith(allowed_prefixes)


MODEL_PAYLOAD_SUFFIXES = {".gguf", ".bin", ".safetensors", ".onnx", ".npz"}


def _resolve_hf_snapshot(models_root: Path, model_name: str) -> Optional[Path]:
    """Best-effort resolve the active Hugging Face cached snapshot directory."""

    try:
        for candidate in models_root.glob(f"models--*--{model_name}"):
            if not candidate.is_dir():
                continue
            refs = candidate / "refs" / "main"
            snap_root = candidate / "snapshots"
            if refs.exists():
                try:
                    commit = refs.read_text().strip()
                    snap = snap_root / commit
                    if snap.exists() and snap.is_dir():
                        return snap
                except Exception:
                    pass
            try:
                snapshots = [path for path in snap_root.iterdir() if path.is_dir()]
                if snapshots:
                    snapshots.sort(
                        key=lambda path: getattr(path.stat(), "st_mtime", 0),
                        reverse=True,
                    )
                    return snapshots[0]
            except Exception:
                pass
    except Exception:
        pass
    return None


def _resolve_local_model_dir(
    search_roots: list[Path], model_name: str
) -> Optional[Path]:
    """Resolve a registered, direct, or Hugging Face cache model location."""

    resolved_name = str(canonical_model_alias(model_name) or model_name).strip()
    registered = resolve_registered_model_path(resolved_name, for_loading=False)
    if registered is not None:
        return registered
    for root in search_roots:
        direct = root / resolved_name
        try:
            if direct.exists() and direct.is_dir():
                return direct
        except Exception:
            pass
        snapshot = _resolve_hf_snapshot(root, resolved_name)
        if snapshot is not None:
            return snapshot
    return None


@router.get("/transformers/models")
async def list_transformer_models(
    request: Request,
    path: Optional[str] = None,
    include_cache_unfiltered: bool = False,
):  # noqa: E501
    """List available transformer models from all search directories.

    Filters Hugging Face cache noise by default so selectors are not flooded
    with unrelated tiny checkpoints; pass include_cache_unfiltered=true to
    return every cache entry.
    """

    cfg = request.app.state.config
    dirs = app_config.model_search_dirs(
        path or cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR)
    )
    models: set[str] = set()
    for models_dir in dirs:
        if not models_dir.exists():
            continue
        is_cache = _is_hf_cache_dir(models_dir)
        for item in models_dir.iterdir():
            if not item.is_dir():
                continue
            if any(
                file_path.suffix.lower() in MODEL_PAYLOAD_SUFFIXES
                for file_path in item.glob("**/*")
            ):
                name = item.name
                if name.startswith("models--"):
                    parts = name.split("--")
                    if len(parts) >= 3:
                        name = parts[-1]
                if is_cache and not _hf_cache_model_allowed(
                    name, allow_extras=include_cache_unfiltered
                ):
                    continue
                models.add(name)
    for entry in list_local_model_entries(include_missing=False):
        alias = str(entry.get("alias") or "").strip()
        if alias:
            models.add(alias)
    return {"models": sorted(models)}


@router.get("/models/exists/{model_name}")
async def model_exists(
    request: Request, model_name: str, path: Optional[str] = None
):  # noqa: E501
    cfg = request.app.state.config
    dirs = app_config.model_search_dirs(
        path or cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR)
    )
    return {"exists": bool(_resolve_local_model_dir(dirs, model_name))}


@router.get("/models/local-size/{model_name}")
async def model_local_size(
    request: Request, model_name: str, path: Optional[str] = None
):  # noqa: E501
    """
    Return the total on-disk size in bytes for the model folder if present.
    Supports both direct folders and Hugging Face cache snapshots.
    """

    cfg = request.app.state.config
    dirs = app_config.model_search_dirs(
        path or cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR)
    )
    resolved = _resolve_local_model_dir(dirs, model_name)
    if resolved is not None:
        try:
            allow_patterns = get_download_allow_patterns(model_name)
            return {
                "exists": True,
                "size": _folder_size_bytes(resolved, include_patterns=allow_patterns),
            }
        except Exception:
            return {"exists": True, "size": 0}
    return {"exists": False, "size": 0}


@router.get("/models/verify/{model_name}")
async def verify_model(
    request: Request, model_name: str, path: Optional[str] = None
):  # noqa: E501
    """
    Verify on-disk model files against the upstream repository manifest.

    Returns:
      - exists: whether a local model folder exists
      - verified: True if all upstream files are present and checks pass
      - expected_bytes: total bytes from upstream manifest
      - installed_bytes: total bytes found locally (recursive)
      - checked_files: number of files compared
    Notes:
      - Hash checks are only performed once all file sizes match; this keeps
        the common case fast and avoids hashing partial downloads.
    """

    cfg = request.app.state.config
    dirs = app_config.model_search_dirs(
        path or cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR)
    )
    model_alias = str(canonical_model_alias(model_name) or model_name).strip()
    allow_patterns = get_download_allow_patterns(model_alias)
    local_dir = _resolve_local_model_dir(dirs, model_alias)
    installed = 0
    if local_dir:
        try:
            installed = _folder_size_bytes(local_dir, include_patterns=allow_patterns)
        except Exception:
            installed = 0

    repo_id = resolve_model_alias(model_alias)
    if not local_dir:
        return {
            "exists": False,
            "verified": False,
            "expected_bytes": 0,
            "installed_bytes": 0,
            "checked_files": 0,
            "downloadable": model_supports_download_job(model_alias),
            "lane": get_model_lane(model_alias),
        }
    if not repo_id or str(repo_id).startswith("TODO"):
        return {
            "exists": True,
            "verified": False,
            "expected_bytes": 0,
            "installed_bytes": int(installed),
            "checked_files": 0,
            "downloadable": model_supports_download_job(model_alias),
            "lane": get_model_lane(model_alias),
        }

    try:
        token = cfg.get("hf_token") if isinstance(cfg, dict) else None
        manifest, expected, _commit = await asyncio.to_thread(
            _remote_manifest, repo_id, allow_patterns, token
        )
    except Exception:
        fallback = _fallback_verification_from_job(
            request, model_name, local_dir, installed
        )
        if fallback is not None:
            return fallback
        return {
            "exists": True,
            "verified": False,
            "expected_bytes": 0,
            "installed_bytes": int(installed),
            "checked_files": 0,
        }

    if expected > 0 and installed < expected:
        return {
            "exists": True,
            "verified": False,
            "expected_bytes": int(expected),
            "installed_bytes": int(installed),
            "checked_files": 0,
            "downloadable": model_supports_download_job(model_name),
            "lane": get_model_lane(model_name),
        }

    sizes_ok = True
    checked = 0
    for entry in manifest:
        relative_path = entry.get("path") or ""
        if not relative_path:
            continue
        local_path = local_dir / relative_path
        try:
            stat = local_path.stat()
        except Exception:
            sizes_ok = False
            break
        if int(entry.get("size") or 0) != int(getattr(stat, "st_size", 0)):
            sizes_ok = False
            break
        checked += 1

    if not sizes_ok:
        return {
            "exists": True,
            "verified": False,
            "expected_bytes": int(expected),
            "installed_bytes": int(installed),
            "checked_files": int(checked),
            "downloadable": model_supports_download_job(model_name),
            "lane": get_model_lane(model_name),
        }

    if expected <= 0 or not manifest:
        fallback = _fallback_verification_from_job(
            request, model_name, local_dir, installed
        )
        if fallback is not None:
            return fallback

    for entry in manifest:
        sha = entry.get("sha256")
        if not sha:
            continue
        relative_path = entry.get("path") or ""
        if not relative_path:
            continue
        local_path = local_dir / relative_path
        try:
            local_sha = await asyncio.to_thread(_sha256_file, local_path)
        except Exception:
            return {
                "exists": True,
                "verified": False,
                "expected_bytes": int(expected),
                "installed_bytes": int(installed),
                "checked_files": int(checked),
                "downloadable": model_supports_download_job(model_name),
                "lane": get_model_lane(model_name),
            }
        if local_sha != sha:
            return {
                "exists": True,
                "verified": False,
                "expected_bytes": int(expected),
                "installed_bytes": int(installed),
                "checked_files": int(checked),
                "downloadable": model_supports_download_job(model_name),
                "lane": get_model_lane(model_name),
            }

    if checked == 0 and installed > 0:
        fallback = _fallback_verification_from_job(
            request, model_name, local_dir, installed
        )
        if fallback is not None:
            return fallback
    if checked <= 0:
        checked = _count_local_files(local_dir)
    return {
        "exists": True,
        "verified": installed > 0 and expected > 0,
        "expected_bytes": int(expected) if expected > 0 else int(installed),
        "installed_bytes": int(installed),
        "checked_files": int(checked),
        "downloadable": model_supports_download_job(model_name),
        "lane": get_model_lane(model_name),
    }


@router.get("/models/integrity/{model_name}")
async def model_integrity(model_name: str):
    """Return a quick summary of local model files for diagnostics."""

    llm_service, _ = _runtime_services()
    return {"integrity": llm_service.verify_local_model(model_name)}


@router.get("/models/info/{model_name}")
async def model_info(request: Request, model_name: str):
    """Return basic metadata for a supported model.

    For API-only or unknown identifiers, return size=0 with a TODO repo tag
    rather than raising 400. This keeps the UI logic simple and avoids noisy
    errors for provider-only voices (e.g., 'alloy').
    """

    model_alias = str(canonical_model_alias(model_name) or model_name).strip()
    repo_id = resolve_model_alias(model_alias)
    metadata = get_model_metadata(model_alias)
    lane = get_model_lane(model_alias)
    downloadable = model_supports_download_job(model_alias)
    provider_supported = model_supports_provider_lane(model_alias)
    supports_images = model_supports_images(model_alias)
    local_loader = get_local_loader(model_alias)
    mobile_catalog_allowed = model_allowed_in_mobile_catalog(model_alias)
    base = {
        "downloadable": downloadable,
        "provider_supported": provider_supported,
        "supports_images": supports_images,
        "local_loader": local_loader,
        "lane": lane,
        "mobile_catalog_allowed": mobile_catalog_allowed,
        "metadata": metadata,
    }
    if not repo_id:
        return {"repo_id": "TODO: unsupported", "size": 0, **base}
    if str(repo_id).startswith("TODO"):
        return {"repo_id": repo_id, "size": 0, **base}

    from huggingface_hub import HfApi

    cfg = request.app.state.config if request else {}
    token = cfg.get("hf_token") if isinstance(cfg, dict) else None
    try:
        from huggingface_hub.utils import (
            GatedRepoError,
            HfHubHTTPError,
            RepositoryNotFoundError,
        )
    except Exception:  # pragma: no cover
        HfHubHTTPError = RepositoryNotFoundError = GatedRepoError = Exception  # type: ignore
    api = HfApi(token=token) if token else HfApi()
    try:
        info = await asyncio.to_thread(api.model_info, repo_id, files_metadata=True)
        siblings = getattr(info, "siblings", []) or []
        allow_patterns = get_download_allow_patterns(model_alias)
        size = sum(
            int(getattr(sibling, "size", None) or 0)
            for sibling in siblings
            if _path_matches_any(
                str(
                    getattr(sibling, "rfilename", None)
                    or getattr(sibling, "path", "")
                    or ""
                ),
                allow_patterns,
            )
        )
        return {"repo_id": repo_id, "size": int(size), **base}
    except GatedRepoError as exc:
        return {
            "repo_id": repo_id,
            "size": 0,
            "requires_auth": True,
            "error": str(exc),
            **base,
        }
    except (RepositoryNotFoundError, HfHubHTTPError) as exc:
        return {"repo_id": repo_id, "size": 0, "error": str(exc), **base}
    except Exception as exc:
        return {"repo_id": repo_id, "size": 0, "error": str(exc), **base}


@router.get("/models/summary/{model_name}")
async def model_summary(
    request: Request,
    model_name: str,
    verify: bool = False,
    path: Optional[str] = None,
):  # noqa: E501
    """Return a compact, aggregated status for a model.

    Includes local presence and size, upstream expected size, optional
    verification against the upstream manifest, and the most recent download
    job status if present.
    """

    cfg = request.app.state.config
    model_alias = str(canonical_model_alias(model_name) or model_name).strip()
    repo_id = resolve_model_alias(model_alias)
    metadata = get_model_metadata(model_alias)
    dirs = app_config.model_search_dirs(
        path or cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR)
    )
    resolved = _resolve_local_model_dir(dirs, model_alias)
    installed = 0
    if resolved is not None:
        try:
            allow_patterns = get_download_allow_patterns(model_alias)
            installed = _folder_size_bytes(resolved, include_patterns=allow_patterns)
        except Exception:
            installed = 0

    expected = 0
    requires_auth = False
    repo_error: Optional[str] = None
    token = cfg.get("hf_token") if isinstance(cfg, dict) else None
    if repo_id and not str(repo_id).startswith("TODO"):
        from huggingface_hub import HfApi

        try:
            from huggingface_hub.utils import GatedRepoError
        except Exception:  # pragma: no cover
            GatedRepoError = Exception  # type: ignore
        api = HfApi(token=token) if token else HfApi()
        try:
            info = await asyncio.to_thread(api.model_info, repo_id, files_metadata=True)
            siblings = getattr(info, "siblings", []) or []
            allow_patterns = get_download_allow_patterns(model_alias)
            expected = int(
                sum(
                    int(getattr(sibling, "size", None) or 0)
                    for sibling in siblings
                    if _path_matches_any(
                        str(
                            getattr(sibling, "rfilename", None)
                            or getattr(sibling, "path", "")
                            or ""
                        ),
                        allow_patterns,
                    )
                )
            )
        except GatedRepoError as exc:
            requires_auth = True
            repo_error = str(exc)
        except Exception as exc:
            repo_error = str(exc)

    verified: Optional[bool] = None
    checked_files = 0
    if verify:
        try:
            verification = await verify_model(request, model_alias, path=path)
            verified = bool(verification.get("verified"))
            checked_files = int(verification.get("checked_files", 0) or 0)
            expected = int(verification.get("expected_bytes", expected) or expected)
            installed = int(verification.get("installed_bytes", installed) or installed)
        except Exception:
            verified = False

    job_info = None
    try:
        jobs = _get_jobs_state(request.app)
        candidates = [job for job in jobs.values() if job.get("model") == model_alias]
        if candidates:
            candidates.sort(
                key=lambda job: job.get("updated_at", job.get("started_at", 0)),
                reverse=True,
            )
            job = candidates[0]
            _refresh_job_status(job)
            progress = _job_progress(job)
            job_info = {
                "id": job.get("id"),
                "status": job.get("status"),
                "pid": job.get("pid"),
                "downloaded": progress.get("downloaded"),
                "total": progress.get("total"),
                "percent": progress.get("percent"),
                "updated_at": job.get("updated_at"),
            }
    except Exception:
        job_info = None

    target_root = dirs[0] if dirs else app_config.DEFAULT_MODELS_DIR
    target_path = target_root / model_alias
    output = {
        "model": model_alias,
        "repo_id": repo_id or "TODO: unsupported",
        "exists": bool(resolved),
        "path": str(resolved or target_path),
        "installed_bytes": int(installed),
        "expected_bytes": int(expected),
        "verified": verified,
        "checked_files": int(checked_files),
        "job": job_info,
        "requires_auth": requires_auth,
        "downloadable": model_supports_download_job(model_alias),
        "provider_supported": model_supports_provider_lane(model_alias),
        "supports_images": model_supports_images(model_alias),
        "local_loader": get_local_loader(model_alias),
        "lane": get_model_lane(model_alias),
        "mobile_catalog_allowed": model_allowed_in_mobile_catalog(model_alias),
        "metadata": metadata,
    }
    if repo_error:
        output["repo_error"] = repo_error
    return output


@router.get("/models/reveal/{model_name}")
async def reveal_model_directory(
    request: Request, model_name: str, path: Optional[str] = None
):
    """Attempt to reveal/open the model folder on the server host.

    Returns the resolved path and a best-effort 'opened' flag.
    If no GUI is available, 'opened' may be false while still returning the path.
    """

    cfg = request.app.state.config
    dirs = app_config.model_search_dirs(
        path or cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR)
    )
    target = _resolve_local_model_dir(dirs, model_name)
    if not target:
        raise HTTPException(status_code=404, detail="Model not found")
    open_target = target.parent if target.is_file() else target
    opened = False
    try:
        if sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", str(open_target)])
            opened = True
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(open_target)])
            opened = True
        elif sys.platform.startswith("win"):
            subprocess.Popen(["explorer.exe", f"/select,{str(open_target)}"])
            opened = True
    except Exception:
        opened = False
    return {"path": str(open_target), "opened": opened}


def _provider_display_name(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized == "lmstudio":
        return "LM Studio"
    if normalized == "ollama":
        return "Ollama"
    if normalized == "custom-openai-compatible":
        return "custom OpenAI-compatible server"
    return normalized or "provider"


def _local_model_delete_lock_detail(model_name: str) -> Optional[str]:
    llm_service, _ = _runtime_services()
    try:
        runtime = llm_service.local_runtime_status()
    except Exception:
        return None
    if not isinstance(runtime, dict):
        return None
    active_names = {
        str(runtime.get("model") or "").strip(),
        str(runtime.get("effective_model_id") or "").strip(),
    }
    active_names.discard("")
    if model_name not in active_names:
        return None
    loaded = bool(runtime.get("loaded"))
    load_state = str(runtime.get("load_state") or "").strip().lower()
    if not loaded and load_state not in {"loading", "ready", "error"}:
        return None
    return (
        f"Direct local runtime still has '{model_name}' loaded. "
        "Use unload in the local runtime panel first, then try deleting it again."
    )


def _provider_model_delete_lock_details(model_name: str) -> List[str]:
    _, provider_manager = _runtime_services()
    details: List[str] = []
    lock_hints = provider_manager.describe_model_locks(
        model_name,
        providers=["lmstudio"],
    )
    for hint in lock_hints:
        if not isinstance(hint, dict):
            continue
        provider_label = _provider_display_name(str(hint.get("provider") or ""))
        loaded_model = str(hint.get("loaded_model") or model_name).strip() or model_name
        base_url = str(hint.get("base_url") or "").strip()
        location = f" at {base_url}" if base_url else ""
        if bool(hint.get("server_owned_by_float")):
            details.append(
                f"{provider_label} still reports '{loaded_model}' as loaded{location}. "
                "Use unload or stop in Float before deleting the files."
            )
        else:
            details.append(
                f"{provider_label} is reachable{location} and still reports "
                f"'{loaded_model}' as loaded outside Float. "
                "Stop that server directly or switch this lane to External HTTP only "
                "before deleting the files."
            )
    return details


def _build_model_delete_lock_parts(model_name: str) -> List[str]:
    details: List[str] = []
    local_detail = _local_model_delete_lock_detail(model_name)
    if local_detail:
        details.append(local_detail)
    details.extend(_provider_model_delete_lock_details(model_name))
    if not details:
        details.append(
            "Another process still has this model directory open. Close file explorers, "
            "terminals, or model runtimes that may be using it, then try again."
        )
    return details


def _build_model_delete_lock_message(model_name: str) -> str:
    details = _build_model_delete_lock_parts(model_name)
    return (
        f"Couldn't delete model '{model_name}' because one or more files are still in use. "
        + " ".join(details)
    )


def _build_model_delete_lock_detail(model_name: str) -> Dict[str, Any]:
    details = _build_model_delete_lock_parts(model_name)
    message = (
        f"Couldn't delete model '{model_name}' because one or more files are still in use. "
        + " ".join(details)
    )
    rows: List[Dict[str, str]] = [
        {"label": "Source", "value": "model delete guard"},
        {"label": "Model", "value": model_name},
    ]
    for index, detail in enumerate(details, start=1):
        rows.append({"label": f"Evidence {index}", "value": detail})
    rows.append(
        {
            "label": "Next",
            "value": (
                "Unload or stop the runtime that owns this model, then retry delete. "
                "For externally managed providers, switch the lane to External HTTP only "
                "before removing files."
            ),
        }
    )
    return {
        "message": message,
        "state_explanation": {
            "title": "Why this model cannot be deleted",
            "summary": (
                "Float refused the delete because runtime ownership checks still show "
                "the model or its directory in use."
            ),
            "rows": rows,
        },
    }


@router.delete("/models/{model_name}")
async def delete_model(
    request: Request, model_name: str, path: Optional[str] = None
):  # noqa: E501
    if remove_local_model_entry(model_name):
        return {"status": "unregistered"}
    cfg = request.app.state.config
    dirs = app_config.model_search_dirs(
        path or cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR)
    )
    for models_dir in dirs:
        target = models_dir / model_name
        if target.exists():
            try:
                shutil.rmtree(target)
            except PermissionError as exc:
                raise HTTPException(
                    status_code=409,
                    detail=_build_model_delete_lock_detail(model_name),
                ) from exc
            except OSError as exc:
                if getattr(exc, "errno", None) in {errno.EACCES, errno.EPERM}:
                    raise HTTPException(
                        status_code=409,
                        detail=_build_model_delete_lock_detail(model_name),
                    ) from exc
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Model not found")


__all__ = [
    "MODEL_PAYLOAD_SUFFIXES",
    "configure_model_filesystem_runtime",
    "delete_model",
    "list_transformer_models",
    "model_exists",
    "model_info",
    "model_integrity",
    "model_local_size",
    "model_summary",
    "reveal_model_directory",
    "router",
    "verify_model",
]
