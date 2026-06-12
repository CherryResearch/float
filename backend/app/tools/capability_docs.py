"""Curated runtime documentation lookup for Float capability docs.

This tool sits between lean tool metadata and unrestricted file reads. It
exposes packaged skills and implementation-facing docs from curated roots so
the model can research UI/runtime behavior without broad filesystem access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from app import config as app_config
from app.utils import user_settings, verify_signature
from app.workflow_profiles import (
    list_skills,
    local_skills_root,
    normalize_module_id,
    repo_skills_root,
    workflow_catalog_payload,
)

DOC_SCOPE_ALL = "all"
DOC_SCOPE_SKILLS = "skills"
DOC_SCOPE_FUNCTIONS = "function_descriptions"
DOC_SCOPE_FEATURES = "feature_overviews"
DOC_SCOPES = {
    DOC_SCOPE_ALL,
    DOC_SCOPE_SKILLS,
    DOC_SCOPE_FUNCTIONS,
    DOC_SCOPE_FEATURES,
}
TEXT_SUFFIXES = {".md", ".txt"}


def _coerce_bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _normalize_scope(value: Any) -> str:
    raw = str(value or DOC_SCOPE_ALL).strip().lower()
    if raw in DOC_SCOPES:
        return raw
    aliases = {
        "function-descriptions": DOC_SCOPE_FUNCTIONS,
        "function_descriptions": DOC_SCOPE_FUNCTIONS,
        "functions": DOC_SCOPE_FUNCTIONS,
        "function descriptions": DOC_SCOPE_FUNCTIONS,
        "feature-overview": DOC_SCOPE_FEATURES,
        "feature_overviews": DOC_SCOPE_FEATURES,
        "features": DOC_SCOPE_FEATURES,
        "feature overviews": DOC_SCOPE_FEATURES,
        "skill": DOC_SCOPE_SKILLS,
    }
    return aliases.get(raw, DOC_SCOPE_ALL)


def _read_summary(text: str) -> str:
    for raw_line in text.splitlines():
        line = str(raw_line or "").strip()
        if not line or line.startswith("#"):
            continue
        return line
    return ""


def _doc_id(scope: str, stem: str) -> str:
    return f"{scope}:{stem}"


def _function_descriptions_root() -> Path:
    return (app_config.REPO_ROOT / "docs" / "function descriptions").resolve()


def _feature_overviews_root() -> Path:
    return (app_config.REPO_ROOT / "docs" / "feature_overviews").resolve()


def _iter_root_docs(scope: str, root: Path) -> Iterable[Dict[str, Any]]:
    if not root.exists():
        return []
    entries: List[Dict[str, Any]] = []
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            body = ""
        entries.append(
            {
                "scope": scope,
                "id": _doc_id(scope, path.stem),
                "title": path.stem.replace("_", " "),
                "path": path.name,
                "full_path": str(path),
                "summary": _read_summary(body),
            }
        )
    return entries


def _skills_entries() -> List[Dict[str, Any]]:
    module_by_skill_id = _module_metadata_by_skill_id()
    entries: List[Dict[str, Any]] = []
    for item in list_skills():
        skill_id = str(item.get("id") or "")
        entry = {
            "scope": DOC_SCOPE_SKILLS,
            "id": _doc_id(DOC_SCOPE_SKILLS, skill_id),
            "title": str(item.get("label") or item.get("id") or ""),
            "path": Path(str(item.get("path") or "")).name,
            "full_path": str(item.get("path") or ""),
            "summary": str(item.get("summary") or ""),
            "source": str(item.get("source") or ""),
        }
        module_meta = module_by_skill_id.get(skill_id)
        if module_meta:
            entry.update(module_meta)
        entries.append(entry)
    return entries


def _enabled_module_ids() -> List[str]:
    try:
        settings_payload = user_settings.load_settings()
    except Exception:
        settings_payload = {}
    return [
        normalize_module_id(str(item or "").strip())
        for item in (settings_payload.get("enabled_workflow_modules") or [])
        if normalize_module_id(str(item or "").strip())
    ]


def _module_metadata_by_skill_id() -> Dict[str, Dict[str, Any]]:
    by_skill_id: Dict[str, Dict[str, Any]] = {}
    payload = workflow_catalog_payload(enabled_modules=_enabled_module_ids())
    for module in payload.get("modules") or []:
        if not isinstance(module, dict):
            continue
        skill_id = str(module.get("skill_id") or "").strip()
        module_id = str(module.get("id") or "").strip()
        if not skill_id or not module_id:
            continue
        by_skill_id[skill_id] = {
            "module_id": module_id,
            "module_label": str(module.get("label") or module_id),
            "module_source": str(module.get("source") or ""),
            "module_status": str(module.get("status") or ""),
            "module_enabled": bool(module.get("enabled")),
            "module_tool_names": [
                str(name)
                for name in (module.get("tool_names") or [])
                if str(name).strip()
            ],
        }
    return by_skill_id


def _all_docs(scope: str) -> List[Dict[str, Any]]:
    normalized = _normalize_scope(scope)
    entries: List[Dict[str, Any]] = []
    if normalized in {DOC_SCOPE_ALL, DOC_SCOPE_SKILLS}:
        entries.extend(_skills_entries())
    if normalized in {DOC_SCOPE_ALL, DOC_SCOPE_FUNCTIONS}:
        entries.extend(
            _iter_root_docs(DOC_SCOPE_FUNCTIONS, _function_descriptions_root())
        )
    if normalized in {DOC_SCOPE_ALL, DOC_SCOPE_FEATURES}:
        entries.extend(_iter_root_docs(DOC_SCOPE_FEATURES, _feature_overviews_root()))
    return sorted(
        entries, key=lambda item: (str(item["scope"]), str(item["title"]).lower())
    )


def _resolve_doc(
    *,
    scope: str,
    doc_id: str,
    path: str,
) -> Dict[str, Any]:
    normalized_scope = _normalize_scope(scope)
    requested_id = str(doc_id or "").strip().lower()
    requested_path = str(path or "").strip().replace("\\", "/").lower()
    entries = _all_docs(normalized_scope)
    if requested_id:
        for entry in entries:
            if str(entry.get("id") or "").strip().lower() == requested_id:
                return entry
    if requested_path:
        for entry in entries:
            full_name = str(entry.get("path") or "").strip().lower()
            stem_name = Path(full_name).stem.lower()
            if requested_path in {full_name, stem_name}:
                return entry
    raise FileNotFoundError("Capability doc not found in curated roots.")


def _read_excerpt(
    full_path: str,
    *,
    start_line: int,
    line_count: int,
    max_chars: int,
) -> Tuple[str, int, int, int, bool]:
    raw_text = Path(full_path).read_text(encoding="utf-8", errors="ignore")
    lines = raw_text.splitlines()
    total_lines = len(lines)
    start_index = max(0, start_line - 1)
    selected = lines[start_index : start_index + line_count]

    excerpt_lines: List[str] = []
    chars_used = 0
    truncated_by_chars = False
    for line in selected:
        separator_len = 1 if excerpt_lines else 0
        projected = chars_used + separator_len + len(line)
        if excerpt_lines and projected > max_chars:
            truncated_by_chars = True
            break
        if not excerpt_lines and len(line) > max_chars:
            excerpt_lines.append(line[:max_chars])
            chars_used = len(excerpt_lines[0])
            truncated_by_chars = True
            break
        excerpt_lines.append(line)
        chars_used = projected

    returned_lines = len(excerpt_lines)
    end_line = start_line + returned_lines - 1 if returned_lines else start_line - 1
    has_more = start_index + line_count < total_lines or truncated_by_chars
    return "\n".join(excerpt_lines), total_lines, start_line, end_line, has_more


def read_capability_docs(
    action: str = "",
    scope: str = DOC_SCOPE_ALL,
    doc_id: str = "",
    path: str = "",
    query: str = "",
    start_line: int = 1,
    line_count: int = 200,
    max_chars: int = 12000,
    limit: int = 20,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    normalized_scope = _normalize_scope(scope)
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"", "list", "read", "search"}:
        normalized_action = "list"
    if not normalized_action:
        normalized_action = (
            "search"
            if str(query or "").strip()
            else "read"
            if str(doc_id or path).strip()
            else "list"
        )
    normalized_start = _coerce_bounded_int(
        start_line, default=1, minimum=1, maximum=1_000_000
    )
    normalized_count = _coerce_bounded_int(
        line_count, default=200, minimum=1, maximum=1000
    )
    normalized_chars = _coerce_bounded_int(
        max_chars, default=12000, minimum=200, maximum=20000
    )
    normalized_limit = _coerce_bounded_int(limit, default=20, minimum=1, maximum=100)
    payload = {
        "action": normalized_action,
        "scope": normalized_scope,
        "doc_id": str(doc_id or ""),
        "path": str(path or ""),
        "query": str(query or ""),
        "start_line": normalized_start,
        "line_count": normalized_count,
        "max_chars": normalized_chars,
        "limit": normalized_limit,
    }
    verify_signature(signature, user, "read_capability_docs", payload)

    if normalized_action == "list":
        docs = _all_docs(normalized_scope)[:normalized_limit]
        return {
            "action": "list",
            "scope": normalized_scope,
            "count": len(docs),
            "docs": docs,
            "roots": {
                "skills": [str(repo_skills_root()), str(local_skills_root())],
                "function_descriptions": str(_function_descriptions_root()),
                "feature_overviews": str(_feature_overviews_root()),
            },
        }

    if normalized_action == "search":
        search = str(query or "").strip().lower()
        docs = []
        for entry in _all_docs(normalized_scope):
            body = Path(str(entry.get("full_path") or "")).read_text(
                encoding="utf-8",
                errors="ignore",
            )
            haystacks = [
                str(entry.get("title") or ""),
                str(entry.get("summary") or ""),
                body,
            ]
            joined = "\n".join(haystacks).lower()
            if search and search not in joined:
                continue
            snippet = str(entry.get("summary") or "")
            if search:
                for idx, raw_line in enumerate(body.splitlines(), start=1):
                    if search in raw_line.lower():
                        snippet = raw_line.strip()
                        entry = {**entry, "match_line": idx}
                        break
            docs.append({**entry, "snippet": snippet})
            if len(docs) >= normalized_limit:
                break
        return {
            "action": "search",
            "scope": normalized_scope,
            "query": str(query or ""),
            "count": len(docs),
            "docs": docs,
        }

    entry = _resolve_doc(scope=normalized_scope, doc_id=doc_id, path=path)
    excerpt, total_lines, resolved_start, end_line, has_more = _read_excerpt(
        str(entry.get("full_path") or ""),
        start_line=normalized_start,
        line_count=normalized_count,
        max_chars=normalized_chars,
    )
    return {
        "action": "read",
        "scope": str(entry.get("scope") or normalized_scope),
        "doc": {key: value for key, value in entry.items() if key not in {"full_path"}},
        "start_line": resolved_start,
        "end_line": end_line,
        "total_lines": total_lines,
        "has_more": has_more,
        "content": excerpt,
    }


__all__ = ["read_capability_docs"]
