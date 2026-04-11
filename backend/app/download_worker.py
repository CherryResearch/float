"""
Lightweight worker to download a Hugging Face repo snapshot to a target folder.
Run as a separate process so the parent can pause/cancel by terminating us.

Usage:
  python -m app.download_worker --repo <repo_id> --dir <local_dir>
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from app.model_registry import get_download_allow_patterns
from huggingface_hub import snapshot_download


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
    # - huggingface-hub 1.x uses Xet for high-performance transfers; translate
    #   the legacy HF transfer flag if it is still set in the environment.
    if os.getenv("HF_HUB_ENABLE_HF_TRANSFER") == "1":
        os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")
        os.environ.pop("HF_HUB_ENABLE_HF_TRANSFER", None)
    else:
        os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")
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
