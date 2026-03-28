"""
Lightweight worker to download a Hugging Face repo snapshot to a target folder.
Run as a separate process so the parent can pause/cancel by terminating us.

Usage:
  python -m app.download_worker --repo <repo_id> --dir <local_dir>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from huggingface_hub import snapshot_download
import os

from app.model_registry import get_download_allow_patterns


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="Hugging Face repo id")
    parser.add_argument("--dir", required=True, help="Destination directory")
    parser.add_argument(
        "--model",
        required=False,
        default=None,
        help="Optional Float model alias (used to apply default download filters).",
    )
    args = parser.parse_args(argv)

    dest = Path(args.dir)
    dest.mkdir(parents=True, exist_ok=True)
    # NOTE:
    # - snapshot_download is resumable; if interrupted, rerunning will fetch
    #   remaining files and finalize the snapshot.
    # - local_dir_use_symlinks=False ensures the destination contains real files
    #   instead of symlinks into the HF cache. This avoids confusion where the
    #   UI does not "see" model_0000* shards when they are only symlinks.
    # - If a token is present in environment it will be used to authenticate
    #   against gated repositories (e.g., Gemma). We support both standard
    #   HUGGINGFACE_HUB_TOKEN and a common HF_TOKEN alias.
    # - If HF transfer is requested but hf_transfer is missing, disable it so
    #   downloads still proceed instead of failing immediately.
    if os.getenv("HF_HUB_ENABLE_HF_TRANSFER") == "1":
        try:
            import importlib.util as _importlib_util

            if _importlib_util.find_spec("hf_transfer") is None:
                os.environ.pop("HF_HUB_ENABLE_HF_TRANSFER", None)
        except Exception:
            os.environ.pop("HF_HUB_ENABLE_HF_TRANSFER", None)
    token = os.getenv("HUGGINGFACE_HUB_TOKEN") or os.getenv("HF_TOKEN")
    allow_patterns = get_download_allow_patterns(args.model)
    snapshot_download(
        repo_id=args.repo,
        local_dir=dest,
        local_dir_use_symlinks=False,
        resume_download=True,
        token=token,
        allow_patterns=allow_patterns,
    )
    # Best-effort cleanup: remove leftover download temp artifacts after success
    try:
        download_cache = dest / ".cache" / "huggingface" / "download"
        if download_cache.exists():
            for path in download_cache.glob("*.incomplete"):
                try:
                    if path.exists():
                        path.unlink()
                except Exception:
                    pass
            for path in download_cache.glob("*.lock"):
                try:
                    if path.exists():
                        path.unlink()
                except Exception:
                    pass
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
