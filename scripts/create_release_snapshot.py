#!/usr/bin/env python3
"""Build or validate the manifest-driven public release snapshot."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "data" / "workspace" / "release-public-alpha"

INCLUDE_PATHS = [
    ".flake8",
    ".gitattributes",
    ".github",
    ".gitignore",
    ".pre-commit-config.yaml",
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
    "frontend",
    "jwt.py",
    "main.py",
    "makefile",
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
    ".dev_state.json",
    ".env",
    ".env.example",
    "backend/venv/",
    "blobs/",
    "conversations/",
    "data/",
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
    return parser.parse_args()


def rel_path(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def is_excluded(path: Path) -> bool:
    relative = rel_path(path)
    if any(
        relative == prefix.rstrip("/") or relative.startswith(prefix)
        for prefix in EXCLUDED_PREFIXES
    ):
        return True
    return any(part in EXCLUDED_PARTS for part in path.parts)


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
        if relative in {
            "pyproject.toml",
            "frontend/package.json",
            "backend/app/config.py",
        } and "0.0.0" in text:
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
    for required in ("LICENSE", "CLA.md", "CONTRIBUTOR_ASSIGNMENT_AGREEMENT.md", "README.md"):
        if not (output_dir / required).exists():
            errors.append(f"snapshot missing required file `{required}`")
    return errors


def main() -> int:
    args = parse_args()
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
    snapshot_errors = validate_snapshot(args.output.resolve())
    if snapshot_errors:
        print("Copied snapshot failed validation:", file=sys.stderr)
        for error in snapshot_errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(
        f"Release snapshot copied to {args.output.resolve()} "
        f"({len(files)} files)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
