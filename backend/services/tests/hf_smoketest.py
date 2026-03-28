"""Minimal smoke test for huggingface-hub snapshot_download.

This downloads a tiny public repo to a local folder to validate that
`huggingface-hub` + extras (`hf-xet`, `hf-transfer`) work end-to-end.

Note: This requires network access.
"""

from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import snapshot_download


def main() -> None:
    # Speed up large files when available (optional)
    os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    repo = os.getenv("HF_SMOKETEST_REPO", "sshleifer/tiny-gpt2")
    out = Path(".cache_smoketest").resolve()
    out.mkdir(exist_ok=True)
    print(f"Downloading {repo} -> {out}")

    path = snapshot_download(
        repo_id=repo,
        local_dir=out,
    )
    print(f"OK: {path}")


if __name__ == "__main__":
    main()
