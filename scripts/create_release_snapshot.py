#!/usr/bin/env python3
"""Build or validate the manifest-driven public release snapshot."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "data" / "workspace" / "release-public-alpha"
BUILD_RECEIPT_NAME = ".float-build.json"
DEPLOYMENT_MANIFEST_NAME = ".float-deployment-manifest.json"
BUILD_CODE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

INCLUDE_PATHS = [
    ".flake8",
    ".gitattributes",
    ".github",
    ".gitignore",
    ".pre-commit-config.yaml",
    "CHANGELOG.md",
    "CLA.md",
    "CONTRIBUTOR_ASSIGNMENT_AGREEMENT.md",
    "LICENSE",
    "README.md",
    "backend",
    "docker",
    "docker-compose.yml",
    "docs/Float_Model_Catalog.csv",
    "docs/api_reference.md",
    "docs/architecture_map.md",
    "docs/data_directory.md",
    "docs/environment setup.md",
    "docs/feature_overviews",
    "docs/open_source_licenses.md",
    "docs/resources",
    "docs/ui-snapshot-2026-04-12.png",
    "frontend",
    "main.py",
    "makefile",
    "modules",
    "package-lock.json",
    "package.json",
    "poetry.lock",
    "pyproject.toml",
    "scripts",
]

OPTIONAL_PATHS = {
    "frontend/requirements.txt",
}

EXCLUDED_PARTS = {
    ".cache_smoketest",
    ".chroma",
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "venv",
}

EXCLUDED_PREFIXES = (
    "AGENTS.md",
    BUILD_RECEIPT_NAME,
    DEPLOYMENT_MANIFEST_NAME,
    ".dev_state.json",
    ".env",
    ".env.example",
    "backend/.env",
    "backend/.env.example",
    "backend/app/sae/train.py",
    "backend/app/services/tests/test_threads_embedding_bakeoff_eval.py",
    "backend/conversations/",
    "backend/logs/",
    "backend/models/",
    "backend/venv/",
    "backend/app/tests/conversations/",
    "blobs/",
    "conversations/",
    "data/",
    "frontend/.env",
    "frontend/.env.example",
    "devices.json",
    "docs/function descriptions/",
    "docs/internal/",
    "logs/",
    "models/",
    "notebooks/",
    "test_conversations/",
    "test_logs.json",
    "user_settings.json",
)

EXCLUDED_GLOBS = ("scripts/*_eval.py",)

TEXT_SUFFIXES = {
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

REFERENCE_SCAN_SUFFIXES = {
    ".md",
    ".txt",
}

FORBIDDEN_TEXT_SNIPPETS = (
    "docs/function descriptions/",
    "docs/internal/",
    "notebooks/",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create the manifest-driven release snapshot used to seed the clean "
            "public repo, or validate that the allowlisted files are release-safe."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output directory for the copied snapshot (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the allowlisted source tree without copying files.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Remove an existing output directory before copying.",
    )
    parser.add_argument(
        "--build-code",
        default=os.getenv("FLOAT_BUILD_CODE", ""),
        help=(
            "Human build checkpoint to store separately from the release version. "
            "Leave empty for an unassigned development snapshot."
        ),
    )
    parser.add_argument(
        "--require-build-code",
        action="store_true",
        help="Fail unless --build-code supplies an intentional build checkpoint.",
    )
    return parser.parse_args()


def rel_path(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def is_relative_excluded(relative: str) -> bool:
    relative = str(relative or "").replace("\\", "/").strip().strip("/")
    if any(
        relative == prefix.rstrip("/") or relative.startswith(prefix)
        for prefix in EXCLUDED_PREFIXES
    ):
        return True
    if any(fnmatch.fnmatchcase(relative, pattern) for pattern in EXCLUDED_GLOBS):
        return True
    return any(part in EXCLUDED_PARTS for part in Path(relative).parts)


def is_excluded(path: Path) -> bool:
    return is_relative_excluded(rel_path(path))


def is_manifest_relative_path(relative: str) -> bool:
    normalized = str(relative or "").replace("\\", "/").strip().strip("/")
    if not normalized or is_relative_excluded(normalized):
        return False
    for item in INCLUDE_PATHS:
        prefix = item.rstrip("/")
        if normalized == prefix or normalized.startswith(f"{prefix}/"):
            return True
    return normalized in OPTIONAL_PATHS


def iter_dir_files(src: Path) -> list[Path]:
    files: list[Path] = []
    stack = [src]
    while stack:
        current = stack.pop()
        for child in sorted(current.iterdir(), reverse=True):
            if is_excluded(child):
                continue
            if child.is_dir():
                stack.append(child)
                continue
            files.append(child)
    return files


def iter_manifest_files() -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    missing: list[str] = []
    for item in INCLUDE_PATHS:
        src = REPO_ROOT / item
        if not src.exists():
            missing.append(item)
            continue
        if src.is_file():
            if not is_excluded(src):
                files.append(src)
            continue
        files.extend(iter_dir_files(src))
    for item in OPTIONAL_PATHS:
        src = REPO_ROOT / item
        if src.exists() and src.is_file() and not is_excluded(src):
            files.append(src)
    unique_files = sorted(set(files), key=lambda path: rel_path(path))
    return unique_files, missing


def copy_snapshot(files: list[Path], output_dir: Path, force: bool) -> None:
    if output_dir.exists():
        if not force:
            raise SystemExit(
                f"Output directory already exists: {output_dir}\n"
                "Re-run with --force to replace it."
            )
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for src in files:
        dest = output_dir / rel_path(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def release_version() -> str:
    payload = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(payload["tool"]["poetry"]["version"]).strip()


def source_revision() -> str:
    override = str(os.getenv("FLOAT_SOURCE_REVISION") or "").strip()
    if override:
        return override
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def _is_manifest_scope(relative: str) -> bool:
    normalized = relative.replace("\\", "/").strip().strip('"')
    return is_manifest_relative_path(normalized)


def snapshot_source_dirty() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    for raw in result.stdout.split(b"\0"):
        if len(raw) < 4:
            continue
        relative = raw[3:].decode("utf-8", errors="replace")
        if _is_manifest_scope(relative):
            return True
    return False


def snapshot_digest(files: list[Path]) -> str:
    manifest = hashlib.sha256()
    for path in sorted(files, key=rel_path):
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest.update(f"{rel_path(path)}\t{content_hash}\n".encode("utf-8"))
    return manifest.hexdigest()


def write_build_receipt(
    output_dir: Path,
    files: list[Path],
    build_code: str,
) -> dict[str, object]:
    code = str(build_code or "").strip()
    if code and not BUILD_CODE_PATTERN.fullmatch(code):
        raise ValueError(
            "Build code must start with a letter or number and use only letters, "
            "numbers, dots, underscores, or hyphens (maximum 64 characters)."
        )
    payload: dict[str, object] = {
        "schema_version": 1,
        "release_version": release_version(),
        "build_code": code,
        "source_revision": source_revision(),
        "source_dirty": snapshot_source_dirty(),
        "snapshot_digest": snapshot_digest(files),
        "built_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    (output_dir / BUILD_RECEIPT_NAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def scan_text_files(files: list[Path], root: Path) -> list[str]:
    errors: list[str] = []
    for path in files:
        suffix = path.suffix.lower()
        if suffix not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="ignore")
        relative = path.relative_to(root).as_posix()
        if suffix in REFERENCE_SCAN_SUFFIXES:
            for snippet in FORBIDDEN_TEXT_SNIPPETS:
                if snippet in text:
                    errors.append(f"{relative}: references excluded path `{snippet}`")
        if (
            relative
            in {
                "pyproject.toml",
                "frontend/package.json",
                "backend/app/config.py",
            }
            and "0.0.0" in text
        ):
            errors.append(f"{relative}: still contains placeholder version `0.0.0`")
    return errors


def validate_source(files: list[Path], missing: list[str]) -> list[str]:
    errors: list[str] = []
    if missing:
        errors.extend(f"Missing manifest path: {item}" for item in missing)
    errors.extend(scan_text_files(files, REPO_ROOT))
    return errors


def validate_snapshot(output_dir: Path) -> list[str]:
    snapshot_files = [path for path in output_dir.rglob("*") if path.is_file()]
    errors = scan_text_files(snapshot_files, output_dir)
    for required in (
        BUILD_RECEIPT_NAME,
        "LICENSE",
        "CLA.md",
        "CONTRIBUTOR_ASSIGNMENT_AGREEMENT.md",
        "README.md",
    ):
        if not (output_dir / required).exists():
            errors.append(f"snapshot missing required file `{required}`")
    return errors


def main() -> int:
    args = parse_args()
    build_code = str(args.build_code or "").strip()
    if args.require_build_code and not build_code:
        print("Release snapshot requires an intentional --build-code.", file=sys.stderr)
        return 1
    if build_code and not BUILD_CODE_PATTERN.fullmatch(build_code):
        print("Release snapshot build code is invalid.", file=sys.stderr)
        return 1
    files, missing = iter_manifest_files()
    source_errors = validate_source(files, missing)
    if source_errors:
        print("Release snapshot validation failed:", file=sys.stderr)
        for error in source_errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    if args.check:
        print(f"Release snapshot check passed for {len(files)} files.")
        return 0
    copy_snapshot(files, args.output.resolve(), args.force)
    receipt = write_build_receipt(args.output.resolve(), files, build_code)
    snapshot_errors = validate_snapshot(args.output.resolve())
    if snapshot_errors:
        print("Copied snapshot failed validation:", file=sys.stderr)
        for error in snapshot_errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(
        f"Release snapshot copied to {args.output.resolve()} "
        f"({len(files)} files, build={receipt['build_code'] or 'unassigned'}, "
        f"digest={receipt['snapshot_digest']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
