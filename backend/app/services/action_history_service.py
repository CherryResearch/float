from __future__ import annotations

import base64
import copy
import difflib
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence
from uuid import uuid4

from app import config as app_config
from app.services import threads_service
from app.services.instance_sync_service import (
    SYNC_SECTION_LABELS,
    InstanceSyncService,
    _now_iso,
    _resolve_attachment_target,
    _write_conversation_snapshot,
    _write_settings_snapshot,
)
from app.services.rag_provider import get_rag_service, ingest_calendar_event
from app.tools import calendar as calendar_tools
from app.tools import local_files, memory as memory_tools
from app.utils import (
    blob_store,
    calendar_store,
    conversation_store,
    memory_store,
    user_settings,
)
from app.utils.graph_store import GraphStore
from app.utils.knowledge_store import KnowledgeStore

DEFAULT_ACTION_HISTORY_RETENTION_DAYS = 7


def _safe_ts(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return float(raw)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str)


def _lookup_snapshot_source(record: Any) -> Optional[str]:
    if not isinstance(record, dict):
        return None
    for key in ("source", "root_source"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get("source") or metadata.get("root_source")
        if isinstance(value, str) and value.strip():
            return value.strip()
    payload = record.get("payload")
    if isinstance(payload, dict):
        value = payload.get("source") or payload.get("root_source")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _file_snapshot(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        text = path.read_text(encoding="utf-8", errors="ignore")
    try:
        stat = path.stat()
        updated_at = float(stat.st_mtime)
        size = int(stat.st_size)
    except Exception:
        updated_at = 0.0
        size = len(text.encode("utf-8"))
    return {
        "path": path.as_posix(),
        "text": text,
        "updated_at": updated_at,
        "size_bytes": size,
    }


def _delete_file_snapshot(path: Path) -> None:
    if path.exists():
        path.unlink()


class ActionHistoryService:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = dict(config or {})
        data_dir = Path(cfg.get("data_dir") or app_config.DEFAULT_DATA_DIR)
        if not data_dir.is_absolute():
            data_dir = (app_config.REPO_ROOT / data_dir).resolve()
        else:
            try:
                data_dir = data_dir.resolve()
            except Exception:
                pass
        self.root = data_dir / "action_history"
        self.actions_dir = self.root / "actions"
        self.index_path = self.root / "index.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.actions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._emit: Optional[Callable[[Dict[str, Any]], None]] = None
        self._prune_expired_actions()

    def set_emitter(self, emitter: Optional[Callable[[Dict[str, Any]], None]]) -> None:
        self._emit = emitter

    def _action_path(self, action_id: str) -> Path:
        return self.actions_dir / f"{action_id}.json"

    def _read_index(self) -> List[Dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _write_index(self, rows: List[Dict[str, Any]]) -> None:
        self.index_path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def _retention_days(self) -> int:
        try:
            settings = user_settings.load_settings()
        except Exception:
            settings = {}
        raw = (
            settings.get("action_history_retention_days")
            if isinstance(settings, dict)
            else DEFAULT_ACTION_HISTORY_RETENTION_DAYS
        )
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = DEFAULT_ACTION_HISTORY_RETENTION_DAYS
        return max(0, min(value, 365))

    def _history_enabled(self) -> bool:
        return self._retention_days() > 0

    def _prune_expired_actions_locked(self, *, now_ts: Optional[float] = None) -> bool:
        retention_days = self._retention_days()
        if retention_days <= 0:
            expired_ids = [
                str(row.get("id") or "").strip()
                for row in self._read_index()
                if str(row.get("id") or "").strip()
            ]
        else:
            cutoff = float(now_ts or time.time()) - retention_days * 86400
            expired_ids = []
            for row in self._read_index():
                action_id = str(row.get("id") or "").strip()
                if not action_id:
                    continue
                created_at_ts = float(row.get("created_at_ts") or row.get("timestamp") or 0.0)
                if created_at_ts and created_at_ts < cutoff:
                    expired_ids.append(action_id)
        if not expired_ids:
            return False
        expired_set = set(expired_ids)
        for action_id in expired_ids:
            try:
                self._action_path(action_id).unlink(missing_ok=True)
            except TypeError:
                path = self._action_path(action_id)
                if path.exists():
                    path.unlink()
            except Exception:
                pass
        remaining = [
            row
            for row in self._read_index()
            if str(row.get("id") or "").strip() not in expired_set
        ]
        self._write_index(remaining)
        return True

    def _prune_expired_actions(self) -> bool:
        with self._lock:
            return self._prune_expired_actions_locked()

    def _summary_from_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": action.get("id"),
            "type": "action",
            "kind": action.get("kind"),
            "name": action.get("name"),
            "summary": action.get("summary"),
            "status": action.get("status"),
            "timestamp": action.get("created_at_ts"),
            "created_at": action.get("created_at"),
            "created_at_ts": action.get("created_at_ts"),
            "request_id": action.get("request_id"),
            "conversation_id": action.get("conversation_id"),
            "conversation_label": action.get("conversation_label"),
            "response_id": action.get("response_id"),
            "response_label": action.get("response_label"),
            "agent_id": action.get("agent_id"),
            "agent_label": action.get("agent_label"),
            "revertible": bool(action.get("revertible")),
            "reverted_at": action.get("reverted_at"),
            "reverted_by_action_id": action.get("reverted_by_action_id"),
            "target_action_ids": list(action.get("target_action_ids") or []),
            "item_count": len(action.get("items") or []),
            "resource_keys": list(action.get("resource_keys") or []),
            "batch_scope": action.get("batch_scope"),
        }

    def _persist_action(self, action: Dict[str, Any], *, emit: bool = True) -> Dict[str, Any]:
        if not self._history_enabled():
            raise ValueError("Action history retention is disabled")
        safe_action = _json_safe(action)
        if not isinstance(safe_action, dict):
            raise ValueError("Action payload must be an object")
        action_id = str(safe_action.get("id") or uuid4())
        safe_action["id"] = action_id
        safe_action["items"] = [
            item for item in (safe_action.get("items") or []) if isinstance(item, dict)
        ]
        safe_action["resource_keys"] = sorted(
            {
                str(item.get("resource_key") or "").strip()
                for item in safe_action["items"]
                if str(item.get("resource_key") or "").strip()
            }
        )
        summary = self._summary_from_action(safe_action)
        with self._lock:
            self._prune_expired_actions_locked()
            self._action_path(action_id).write_text(
                json.dumps(safe_action, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            index = self._read_index()
            replaced = False
            for idx, existing in enumerate(index):
                if str(existing.get("id") or "") == action_id:
                    index[idx] = summary
                    replaced = True
                    break
            if not replaced:
                index.append(summary)
            index.sort(key=lambda item: float(item.get("created_at_ts") or 0.0), reverse=True)
            self._write_index(index)
        if emit and callable(self._emit):
            try:
                self._emit(summary)
            except Exception:
                pass
        return safe_action

    def _load_action(self, action_id: str) -> Optional[Dict[str, Any]]:
        self._prune_expired_actions()
        path = self._action_path(str(action_id or "").strip())
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def list_actions(
        self,
        *,
        conversation_id: Optional[str] = None,
        response_id: Optional[str] = None,
        include_reverted: bool = True,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        self._prune_expired_actions()
        rows = self._read_index()
        conv = str(conversation_id or "").strip()
        resp = str(response_id or "").strip()
        out: List[Dict[str, Any]] = []
        for row in rows:
            if conv and str(row.get("conversation_id") or "") != conv:
                continue
            if resp and str(row.get("response_id") or "") != resp:
                continue
            if not include_reverted and row.get("reverted_at"):
                continue
            out.append(dict(row))
            if len(out) >= max(1, min(int(limit or 200), 500)):
                break
        return out

    def _lookup_conversation_label(self, conversation_id: Optional[str]) -> Optional[str]:
        if not conversation_id:
            return None
        try:
            meta = conversation_store.get_metadata(str(conversation_id))
        except Exception:
            return None
        label = meta.get("display_name") or meta.get("name")
        return str(label).strip() if label else None

    def _normalize_context(self, context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        raw = dict(context or {})
        conversation_id = str(raw.get("conversation_id") or raw.get("session_id") or "").strip()
        response_id = str(
            raw.get("response_id")
            or raw.get("message_id")
            or raw.get("chain_id")
            or ""
        ).strip()
        agent_id = str(
            raw.get("agent_id")
            or raw.get("chain_id")
            or raw.get("message_id")
            or conversation_id
            or "orchestrator"
        ).strip()
        conversation_label = raw.get("conversation_label") or self._lookup_conversation_label(
            conversation_id
        )
        response_label = raw.get("response_label")
        if not response_label and response_id:
            response_label = f"response {response_id[-8:]}"
        agent_label = raw.get("agent_label") or conversation_label or agent_id
        return {
            "conversation_id": conversation_id or None,
            "conversation_label": conversation_label or None,
            "response_id": response_id or None,
            "response_label": response_label or None,
            "agent_id": agent_id or None,
            "agent_label": agent_label or None,
            "request_id": raw.get("request_id"),
            "session_id": raw.get("session_id") or conversation_id or None,
            "message_id": raw.get("message_id") or None,
            "chain_id": raw.get("chain_id") or None,
            "model": raw.get("model"),
            "mode": raw.get("mode"),
        }

    def _capture_section_snapshot(self, sections: Sequence[str]) -> Dict[str, Any]:
        sync = InstanceSyncService()
        return {
            "sections": {
                section: sync._snapshot_for_section(section)  # type: ignore[attr-defined]
                for section in sections
            }
        }

    def _derive_calendar_event_id(self, args: Dict[str, Any]) -> Optional[str]:
        explicit = calendar_tools._normalize_optional_str(args.get("id"))
        if explicit:
            return explicit
        title = calendar_tools._normalize_optional_str(args.get("title"))
        if not title:
            return None
        grounded_at = args.get("grounded_at")
        timezone_name = calendar_tools.resolve_timezone_name(args.get("timezone"))
        start_time = calendar_tools._coerce_timestamp(
            args.get("start_time"),
            timezone_name=timezone_name,
            grounded_at=grounded_at,
        )
        if start_time is None:
            return None
        return f"{calendar_tools._slugify(title) or 'task'}-{int(start_time * 1000)}"

    def prepare_tool_action(
        self,
        name: str,
        args: Dict[str, Any],
        *,
        context: Optional[Dict[str, Any]] = None,
        manager: Any = None,
    ) -> Optional[Dict[str, Any]]:
        if not self._history_enabled():
            return None
        tool_name = str(name or "").strip()
        if tool_name == "revert_actions":
            return None
        normalized_context = self._normalize_context(context)
        if tool_name == "write_file":
            try:
                _, workspace_dir = local_files._get_data_dirs()  # type: ignore[attr-defined]
                normalized = local_files._normalize_tool_path(  # type: ignore[attr-defined]
                    str(args.get("path") or ""),
                    workspace_only=True,
                )
                resolved = local_files._resolve_rooted_path(  # type: ignore[attr-defined]
                    normalized,
                    workspace_dir,
                )
            except Exception:
                return None
            return {
                "kind": "file",
                "name": tool_name,
                "args": copy.deepcopy(args),
                "context": normalized_context,
                "path": resolved.as_posix(),
                "label": resolved.name or resolved.as_posix(),
                "before": _file_snapshot(resolved),
            }
        if tool_name == "generate_threads":
            target = threads_service.DEFAULT_SUMMARY_PATH
            return {
                "kind": "file",
                "name": tool_name,
                "args": copy.deepcopy(args),
                "context": normalized_context,
                "path": target.as_posix(),
                "label": target.name,
                "section": "threads",
                "before": _file_snapshot(target),
            }
        if tool_name == "remember":
            key = str(args.get("key") or "").strip()
            if not key:
                return None
            return {
                "kind": "sections",
                "name": tool_name,
                "args": copy.deepcopy(args),
                "context": normalized_context,
                "sections": ["memories", "knowledge"],
                "before": self._capture_section_snapshot(["memories", "knowledge"]),
                "match": {
                    "memory_key": key,
                    "knowledge_source": f"memory:{key}",
                },
            }
        if tool_name == "memory.save":
            if manager is None:
                return None
            text = str(args.get("text") or "")
            if not text.strip():
                return None
            key = memory_tools._derive_memory_key(
                manager,
                memory_tools._normalize_optional_str(args.get("key")),
                memory_tools._normalize_optional_str(args.get("namespace")),
                text,
            )
            return {
                "kind": "sections",
                "name": tool_name,
                "args": copy.deepcopy(args),
                "context": normalized_context,
                "sections": ["memories", "knowledge"],
                "before": self._capture_section_snapshot(["memories", "knowledge"]),
                "match": {
                    "memory_key": key,
                    "knowledge_source": f"memory:{key}",
                },
            }
        if tool_name == "create_task":
            event_id = self._derive_calendar_event_id(args)
            if not event_id:
                return None
            return {
                "kind": "sections",
                "name": tool_name,
                "args": copy.deepcopy(args),
                "context": normalized_context,
                "sections": ["calendar", "knowledge"],
                "before": self._capture_section_snapshot(["calendar", "knowledge"]),
                "match": {
                    "event_id": event_id,
                    "knowledge_source": f"calendar_event:{event_id}",
                },
            }
        return None

    def _filter_section_items(
        self,
        items: List[Dict[str, Any]],
        match: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        memory_key = str(match.get("memory_key") or "").strip()
        event_id = str(match.get("event_id") or "").strip()
        knowledge_source = str(match.get("knowledge_source") or "").strip()
        out: List[Dict[str, Any]] = []
        for item in items:
            section = str(item.get("section") or "")
            if section == "memories" and memory_key:
                if str(item.get("resource_id") or "") == memory_key:
                    out.append(item)
                continue
            if section == "calendar" and event_id:
                if str(item.get("resource_id") or "") == event_id:
                    out.append(item)
                continue
            if section == "knowledge" and knowledge_source:
                before_source = _lookup_snapshot_source(item.get("before"))
                after_source = _lookup_snapshot_source(item.get("after"))
                if before_source == knowledge_source or after_source == knowledge_source:
                    out.append(item)
                continue
        return out

    def _operation_for(self, before: Any, after: Any) -> Optional[str]:
        if before is None and after is None:
            return None
        if before is None:
            return "create"
        if after is None:
            return "delete"
        if _stable_json(before) == _stable_json(after):
            return None
        return "update"

    def _build_tool_summary(
        self,
        name: str,
        items: List[Dict[str, Any]],
        status: str,
    ) -> str:
        if not items:
            return f"{name} completed"
        labels = [str(item.get("label") or item.get("resource_id") or "") for item in items]
        head = labels[0] if labels else name
        change_count = len(items)
        status_label = "applied" if str(status or "").lower() == "invoked" else status
        if change_count == 1:
            return f"{name} {status_label}: {head}"
        return f"{name} {status_label}: {head} +{change_count - 1} more"

    def _build_sync_summary(self, name: str, items: List[Dict[str, Any]]) -> str:
        if not items:
            return name
        sections = sorted(
            {
                SYNC_SECTION_LABELS.get(
                    str(item.get("section") or ""),
                    str(item.get("section") or ""),
                )
                for item in items
            }
        )
        if len(sections) == 1:
            return f"{name}: {sections[0]} ({len(items)} changes)"
        return f"{name}: {sections[0]} +{len(sections) - 1} sections ({len(items)} changes)"

    def finalize_tool_action(
        self,
        token: Optional[Dict[str, Any]],
        *,
        result: Any,
        status: str,
    ) -> Optional[Dict[str, Any]]:
        if not self._history_enabled():
            return None
        if not isinstance(token, dict):
            return None
        kind = token.get("kind")
        items: List[Dict[str, Any]] = []
        if kind == "file":
            path = Path(str(token.get("path") or ""))
            before = token.get("before")
            after = _file_snapshot(path)
            operation = self._operation_for(before, after)
            if operation:
                section = str(token.get("section") or "files")
                items = [
                    {
                        "id": f"{section}:{path.as_posix()}",
                        "section": section,
                        "resource_type": "file",
                        "resource_id": path.as_posix(),
                        "resource_key": f"file:{path.as_posix()}",
                        "label": token.get("label") or path.name or path.as_posix(),
                        "operation": operation,
                        "before": before,
                        "after": after,
                        "revertible": True,
                    }
                ]
        elif kind == "sections":
            sections = [str(item) for item in (token.get("sections") or []) if item]
            before = token.get("before")
            after = self._capture_section_snapshot(sections)
            items = InstanceSyncService().diff_snapshots(before, after, sections)
            match = token.get("match") if isinstance(token.get("match"), dict) else {}
            items = self._filter_section_items(items, match)
        if not items:
            return None
        context = self._normalize_context(token.get("context"))
        action = {
            "id": str(uuid4()),
            "kind": "tool",
            "name": str(token.get("name") or ""),
            "summary": self._build_tool_summary(str(token.get("name") or ""), items, status),
            "status": status,
            "created_at": _now_iso(),
            "created_at_ts": time.time(),
            "args": _json_safe(token.get("args") or {}),
            "result": _json_safe(result),
            "items": items,
            "revertible": bool(items),
            "reverted_at": None,
            "reverted_by_action_id": None,
            "target_action_ids": [],
            **context,
        }
        return self._persist_action(action)

    def record_snapshot_action(
        self,
        *,
        kind: str,
        name: str,
        before_snapshot: Dict[str, Any],
        after_snapshot: Dict[str, Any],
        sections: Sequence[str],
        context: Optional[Dict[str, Any]] = None,
        args: Optional[Dict[str, Any]] = None,
        result: Any = None,
        summary: Optional[str] = None,
        batch_scope: Optional[Dict[str, Any]] = None,
        target_action_ids: Optional[Sequence[str]] = None,
        emit: bool = True,
    ) -> Optional[Dict[str, Any]]:
        if not self._history_enabled():
            return None
        items = InstanceSyncService().diff_snapshots(before_snapshot, after_snapshot, sections)
        if not items:
            return None
        normalized_context = self._normalize_context(context)
        action = {
            "id": str(uuid4()),
            "kind": kind,
            "name": name,
            "summary": summary or self._build_sync_summary(name, items),
            "status": "applied",
            "created_at": _now_iso(),
            "created_at_ts": time.time(),
            "args": _json_safe(args or {}),
            "result": _json_safe(result),
            "items": items,
            "revertible": bool(items),
            "reverted_at": None,
            "reverted_by_action_id": None,
            "target_action_ids": [str(item) for item in (target_action_ids or []) if item],
            "batch_scope": _json_safe(batch_scope) if batch_scope else None,
            **normalized_context,
        }
        return self._persist_action(action, emit=emit)

    def get_action_detail(self, action_id: str) -> Optional[Dict[str, Any]]:
        action = self._load_action(action_id)
        if action is None:
            return None
        detail = copy.deepcopy(action)
        enriched_items: List[Dict[str, Any]] = []
        for item in detail.get("items") or []:
            if not isinstance(item, dict):
                continue
            next_item = dict(item)
            next_item["diff"] = self._build_item_diff(next_item)
            enriched_items.append(next_item)
        detail["items"] = enriched_items
        return detail

    def _build_item_diff(self, item: Dict[str, Any]) -> Dict[str, Any]:
        before = item.get("before")
        after = item.get("after")
        resource_type = str(item.get("resource_type") or "")
        before_text = self._format_snapshot_for_diff(resource_type, before)
        after_text = self._format_snapshot_for_diff(resource_type, after)
        diff_lines = list(
            difflib.unified_diff(
                before_text.splitlines(),
                after_text.splitlines(),
                fromfile="before",
                tofile="after",
                lineterm="",
            )
        )
        return {
            "before_text": before_text,
            "after_text": after_text,
            "unified": "\n".join(diff_lines),
        }

    def _format_snapshot_for_diff(self, resource_type: str, snapshot: Any) -> str:
        if snapshot is None:
            return ""
        if resource_type == "file":
            if isinstance(snapshot, dict):
                return str(snapshot.get("text") or "")
            return str(snapshot)
        if resource_type == "attachment":
            if not isinstance(snapshot, dict):
                return _stable_json(snapshot)
            descriptor = {
                "content_hash": snapshot.get("content_hash") or snapshot.get("sync_id"),
                "filename": snapshot.get("filename"),
                "updated_at": snapshot.get("updated_at"),
                "metadata": snapshot.get("metadata") or {},
            }
            payload = str(snapshot.get("content_b64") or "")
            if payload:
                try:
                    descriptor["size_bytes"] = len(base64.b64decode(payload))
                except Exception:
                    descriptor["size_bytes"] = None
            return _stable_json(descriptor)
        return _stable_json(snapshot)

    def _select_targets(
        self,
        *,
        action_ids: Optional[Sequence[str]] = None,
        response_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if action_ids:
            selected: List[Dict[str, Any]] = []
            for action_id in action_ids:
                action = self._load_action(str(action_id))
                if isinstance(action, dict):
                    selected.append(action)
            return selected
        rows = self.list_actions(
            conversation_id=conversation_id,
            response_id=response_id,
            include_reverted=False,
            limit=500,
        )
        out: List[Dict[str, Any]] = []
        for row in rows:
            if row.get("kind") == "revert":
                continue
            action = self._load_action(str(row.get("id") or ""))
            if isinstance(action, dict) and action.get("revertible"):
                out.append(action)
        return out

    def _detect_conflicts(self, targets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        target_ids = {str(item.get("id") or "") for item in targets}
        target_keys = {
            str(key)
            for action in targets
            for key in (action.get("resource_keys") or [])
            if str(key or "").strip()
        }
        if not target_keys:
            return []
        earliest_ts = min(float(action.get("created_at_ts") or 0.0) for action in targets)
        conflicts: List[Dict[str, Any]] = []
        for row in self.list_actions(include_reverted=True, limit=1000):
            row_id = str(row.get("id") or "")
            if row_id in target_ids:
                continue
            if float(row.get("created_at_ts") or 0.0) <= earliest_ts:
                continue
            if row.get("reverted_at"):
                continue
            touched = set(str(item) for item in (row.get("resource_keys") or []))
            overlap = sorted(target_keys & touched)
            if not overlap:
                continue
            conflicts.append(
                {
                    "action_id": row_id,
                    "summary": row.get("summary"),
                    "resource_keys": overlap,
                }
            )
        return conflicts

    def _current_snapshot_for_item(self, item: Dict[str, Any]) -> Any:
        resource_type = str(item.get("resource_type") or "")
        resource_id = str(item.get("resource_id") or "")
        section = str(item.get("section") or "")
        if resource_type == "file":
            return _file_snapshot(Path(resource_id))
        sync = InstanceSyncService()
        if section == "conversations":
            payload = sync._conversation_snapshot()  # type: ignore[attr-defined]
            return next(
                (
                    record
                    for record in payload
                    if isinstance(record, dict)
                    and str(record.get("sync_id") or "") == resource_id
                ),
                None,
            )
        if section == "memories":
            payload = sync._memory_snapshot()  # type: ignore[attr-defined]
            return next(
                (
                    record
                    for record in payload
                    if isinstance(record, dict)
                    and str(record.get("key") or record.get("sync_id") or "") == resource_id
                ),
                None,
            )
        if section == "knowledge":
            payload = sync._knowledge_snapshot()  # type: ignore[attr-defined]
            return next(
                (
                    record
                    for record in payload
                    if isinstance(record, dict)
                    and str(record.get("knowledge_id") or record.get("sync_id") or "") == resource_id
                ),
                None,
            )
        if section == "graph":
            payload = sync._graph_snapshot()  # type: ignore[attr-defined]
            if resource_type == "graph_node":
                return next(
                    (
                        record
                        for record in payload.get("nodes", [])
                        if isinstance(record, dict)
                        and f"node:{record.get('node_id')}" == resource_id
                    ),
                    None,
                )
            return next(
                (
                    record
                    for record in payload.get("claims", [])
                    if isinstance(record, dict)
                    and f"claim:{record.get('claim_id')}" == resource_id
                ),
                None,
            )
        if section == "attachments":
            payload = sync._attachment_snapshot()  # type: ignore[attr-defined]
            return next(
                (
                    record
                    for record in payload
                    if isinstance(record, dict)
                    and str(record.get("content_hash") or record.get("sync_id") or "") == resource_id
                ),
                None,
            )
        if section == "calendar":
            payload = sync._calendar_snapshot()  # type: ignore[attr-defined]
            return next(
                (
                    record
                    for record in payload
                    if isinstance(record, dict)
                    and str(record.get("event_id") or record.get("sync_id") or "") == resource_id
                ),
                None,
            )
        if section == "settings":
            return sync._settings_snapshot()  # type: ignore[attr-defined]
        return None

    def _apply_item_snapshot(self, item: Dict[str, Any], snapshot: Any) -> None:
        resource_type = str(item.get("resource_type") or "")
        resource_id = str(item.get("resource_id") or "")
        section = str(item.get("section") or "")
        if resource_type == "file":
            path = Path(resource_id)
            if snapshot is None:
                _delete_file_snapshot(path)
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str((snapshot or {}).get("text") or ""), encoding="utf-8")
            return
        if section == "conversations":
            current = self._current_snapshot_for_item(item)
            current_name = (current or {}).get("name") if isinstance(current, dict) else None
            target_name = (snapshot or {}).get("name") if isinstance(snapshot, dict) else None
            if snapshot is None:
                if current_name:
                    conversation_store.delete_conversation(str(current_name))
                return
            if current_name and target_name and current_name != target_name:
                conversation_store.delete_conversation(str(current_name))
            _write_conversation_snapshot(
                name=str(snapshot.get("name") or target_name or resource_id),
                messages=list(snapshot.get("messages") or []),
                metadata=dict(snapshot.get("metadata") or {}),
            )
            return
        if section == "memories":
            store = memory_store.load()
            if snapshot is None:
                store.pop(resource_id, None)
            else:
                store[resource_id] = copy.deepcopy(snapshot.get("payload"))
            memory_store.save(store)
            manager = getattr(memory_tools, "_MANAGER", None)
            if manager is not None:
                try:
                    manager.store = copy.deepcopy(store)
                except Exception:
                    pass
            return
        if section == "knowledge":
            store = KnowledgeStore()
            if snapshot is None:
                store.delete_identifier(resource_id)
                return
            InstanceSyncService()._merge_knowledge([snapshot])  # type: ignore[attr-defined]
            return
        if section == "graph":
            graph = GraphStore()
            if snapshot is None:
                if resource_type == "graph_node":
                    graph.delete_node(resource_id.split(":", 1)[-1])
                else:
                    graph.delete_claim(resource_id.split(":", 1)[-1])
                return
            if resource_type == "graph_node":
                InstanceSyncService()._merge_graph({"nodes": [snapshot], "claims": []})  # type: ignore[attr-defined]
            else:
                InstanceSyncService()._merge_graph({"nodes": [], "claims": [snapshot]})  # type: ignore[attr-defined]
            return
        if section == "attachments":
            if snapshot is None:
                self._delete_attachment(resource_id)
                return
            InstanceSyncService()._write_attachment_file(  # type: ignore[attr-defined]
                content_hash=str(snapshot.get("content_hash") or snapshot.get("sync_id") or resource_id),
                filename=str(snapshot.get("filename") or resource_id),
                metadata=dict(snapshot.get("metadata") or {}),
                data=base64.b64decode(str(snapshot.get("content_b64") or "")),
            )
            return
        if section == "calendar":
            if snapshot is None:
                calendar_store.delete_event(resource_id)
                try:
                    rag = get_rag_service(raise_http=False)
                    if rag:
                        rag.delete_source(f"calendar_event:{resource_id}")
                except Exception:
                    pass
                return
            payload = dict(snapshot.get("payload") or {})
            calendar_store.save_event(resource_id, payload)
            try:
                ingest_calendar_event(resource_id, payload)
            except Exception:
                pass
            return
        if section == "settings":
            if snapshot is None:
                return
            _write_settings_snapshot(
                dict(snapshot.get("data") or {}),
                snapshot.get("updated_at"),
            )

    def _delete_attachment(self, content_hash: str) -> None:
        target = _resolve_attachment_target(content_hash)
        if target is not None and target.exists():
            try:
                target.unlink()
            except Exception:
                pass
        meta_path = blob_store.BLOBS_DIR / f"{content_hash}.json"
        if meta_path.exists():
            try:
                meta_path.unlink()
            except Exception:
                pass
        blob_target = blob_store.BLOBS_DIR / content_hash
        if blob_target.exists():
            try:
                blob_target.unlink()
            except Exception:
                pass

    def _build_revert_summary(
        self,
        targets: Sequence[Dict[str, Any]],
        response_id: Optional[str],
        conversation_id: Optional[str],
    ) -> str:
        if response_id:
            return f"Reverted response {str(response_id)[-8:]} ({len(targets)} actions)"
        if conversation_id:
            label = self._lookup_conversation_label(conversation_id) or str(conversation_id)
            return f"Reverted conversation {label} ({len(targets)} actions)"
        if len(targets) == 1:
            target = targets[0]
            return f"Reverted {target.get('summary') or target.get('name') or 'action'}"
        return f"Reverted {len(targets)} actions"

    def revert_actions(
        self,
        *,
        action_ids: Optional[Sequence[str]] = None,
        response_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        force: bool = False,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        targets = self._select_targets(
            action_ids=action_ids,
            response_id=response_id,
            conversation_id=conversation_id,
        )
        if not targets:
            return {
                "status": "noop",
                "reverted_action_ids": [],
                "conflicts": [],
                "action": None,
            }
        active_targets = [
            target
            for target in targets
            if not target.get("reverted_at") and bool(target.get("revertible"))
        ]
        if not active_targets:
            return {
                "status": "noop",
                "reverted_action_ids": [],
                "conflicts": [],
                "action": None,
            }
        conflicts = [] if force else self._detect_conflicts(active_targets)
        if conflicts:
            raise ValueError(
                "Cannot revert because newer actions touched the same resources."
            )
        ordered_targets = sorted(
            active_targets,
            key=lambda item: float(item.get("created_at_ts") or 0.0),
            reverse=True,
        )
        revert_items: List[Dict[str, Any]] = []
        for action in ordered_targets:
            items = [item for item in (action.get("items") or []) if isinstance(item, dict)]
            for item in reversed(items):
                current_before = self._current_snapshot_for_item(item)
                desired = item.get("before")
                if self._operation_for(current_before, desired) is None:
                    continue
                self._apply_item_snapshot(item, desired)
                current_after = self._current_snapshot_for_item(item)
                operation = self._operation_for(current_before, current_after)
                if not operation:
                    continue
                revert_items.append(
                    {
                        "id": f"revert:{item.get('resource_key')}",
                        "section": item.get("section"),
                        "resource_type": item.get("resource_type"),
                        "resource_id": item.get("resource_id"),
                        "resource_key": item.get("resource_key"),
                        "label": item.get("label"),
                        "operation": operation,
                        "before": current_before,
                        "after": current_after,
                        "revertible": True,
                    }
                )
        if not revert_items:
            return {
                "status": "noop",
                "reverted_action_ids": [],
                "conflicts": conflicts,
                "action": None,
            }
        revert_action_id = str(uuid4())
        reverted_at = _now_iso()
        for target in ordered_targets:
            target["reverted_at"] = reverted_at
            target["reverted_by_action_id"] = revert_action_id
            self._persist_action(target)
            if str(target.get("kind") or "") == "revert":
                for original_id in target.get("target_action_ids") or []:
                    original = self._load_action(str(original_id))
                    if not isinstance(original, dict):
                        continue
                    original["reverted_at"] = None
                    original["reverted_by_action_id"] = None
                    self._persist_action(original)
        normalized_context = self._normalize_context(context)
        scope = {
            "action_ids": [str(item.get("id") or "") for item in ordered_targets],
            "response_id": response_id or None,
            "conversation_id": conversation_id or None,
        }
        action = {
            "id": revert_action_id,
            "kind": "revert",
            "name": "revert_actions",
            "summary": self._build_revert_summary(ordered_targets, response_id, conversation_id),
            "status": "applied",
            "created_at": reverted_at,
            "created_at_ts": time.time(),
            "args": _json_safe(scope),
            "result": {
                "reverted_action_ids": [str(item.get("id") or "") for item in ordered_targets],
                "conflicts": conflicts,
            },
            "items": revert_items,
            "revertible": True,
            "reverted_at": None,
            "reverted_by_action_id": None,
            "target_action_ids": [str(item.get("id") or "") for item in ordered_targets],
            "batch_scope": scope,
            **normalized_context,
        }
        saved = self._persist_action(action)
        return {
            "status": "reverted",
            "reverted_action_ids": [str(item.get("id") or "") for item in ordered_targets],
            "conflicts": conflicts,
            "action": self._summary_from_action(saved),
        }


__all__ = ["ActionHistoryService"]
