from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
from uuid import uuid4

from app.utils.deployment_status import ensure_deployment_descriptor, resolve_data_root

LEDGER_SCHEMA_VERSION = 1
LEDGER_FILENAME = "deployment_events.sqlite3"
ACTION_ID_PREFIX = "deployment-event:"
EVENT_TYPES = {
    "software.install",
    "data.sync",
    "data.revision",
    "data.update",
    "data.delete",
    "data.bulk_replace",
    "data.restore",
    "data.safety_snapshot",
}
EVENT_STATUSES = {"completed", "failed", "pending", "cancelled"}
SYNC_DIRECTIONS = {"", "pull", "push", "incoming_push", "outgoing_pull"}
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,191}$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SOFTWARE_KEYS = {
    "release_version",
    "build_code",
    "snapshot_digest",
    "source_revision",
}
_REVISION_KEYS = {"code", "digest"}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def ledger_path(data_root: Optional[Path | str] = None) -> Path:
    return resolve_data_root(data_root) / "databases" / LEDGER_FILENAME


def _safe_token(value: Any, *, field: str, required: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        if required:
            raise ValueError(f"{field} is required")
        return ""
    if not _TOKEN_RE.fullmatch(text):
        raise ValueError(f"{field} must be a content-free identifier")
    return text


def _safe_token_list(values: Optional[Iterable[Any]], *, field: str) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        token = _safe_token(value, field=field)
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return sorted(normalized)


def _safe_counts(values: Optional[Mapping[str, Any]]) -> Dict[str, int]:
    normalized: Dict[str, int] = {}
    for key, value in (values or {}).items():
        name = str(key or "").strip().lower()
        if not _KEY_RE.fullmatch(name):
            raise ValueError("count keys must be content-free identifiers")
        if isinstance(value, bool):
            raise ValueError(f"count {name} must be a non-negative integer")
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"count {name} must be a non-negative integer") from exc
        if number < 0:
            raise ValueError(f"count {name} must be a non-negative integer")
        normalized[name] = number
    return dict(sorted(normalized.items()))


def _safe_named_tokens(
    values: Optional[Mapping[str, Any]],
    *,
    allowed_keys: set[str],
    field: str,
) -> Dict[str, str]:
    normalized: Dict[str, str] = {}
    for key, value in (values or {}).items():
        name = str(key or "").strip()
        if name not in allowed_keys:
            raise ValueError(f"{field} contains an unsupported field: {name}")
        token = _safe_token(value, field=f"{field}.{name}")
        if token:
            normalized[name] = token
    return dict(sorted(normalized.items()))


def _event_payload(
    *,
    event_id: Any,
    recorded_at: str,
    event_type: Any,
    status: Any,
    deployment_id: Any,
    peer_deployment_id: Any = "",
    workspace_lineage_ids: Optional[Iterable[Any]] = None,
    direction: Any = "",
    operation_id: Any = "",
    origin_event_id: Any = "",
    caused_by_event_id: Any = "",
    sections: Optional[Iterable[Any]] = None,
    counts: Optional[Mapping[str, Any]] = None,
    local_revision_before: Optional[Mapping[str, Any]] = None,
    local_revision_after: Optional[Mapping[str, Any]] = None,
    peer_revision_before: Optional[Mapping[str, Any]] = None,
    peer_revision_after: Optional[Mapping[str, Any]] = None,
    software_before: Optional[Mapping[str, Any]] = None,
    software_after: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_type = str(event_type or "").strip().lower()
    if normalized_type not in EVENT_TYPES:
        raise ValueError(f"Unsupported deployment event type: {normalized_type}")
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in EVENT_STATUSES:
        raise ValueError(f"Unsupported deployment event status: {normalized_status}")
    normalized_direction = str(direction or "").strip().lower()
    if normalized_direction not in SYNC_DIRECTIONS:
        raise ValueError(
            f"Unsupported deployment event direction: {normalized_direction}"
        )
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "event_id": _safe_token(event_id, field="event_id", required=True),
        "recorded_at": recorded_at,
        "event_type": normalized_type,
        "status": normalized_status,
        "deployment_id": _safe_token(
            deployment_id, field="deployment_id", required=True
        ),
        "peer_deployment_id": _safe_token(
            peer_deployment_id, field="peer_deployment_id"
        ),
        "workspace_lineage_ids": _safe_token_list(
            workspace_lineage_ids, field="workspace_lineage_id"
        ),
        "direction": normalized_direction,
        "operation_id": _safe_token(operation_id, field="operation_id"),
        "origin_event_id": _safe_token(origin_event_id, field="origin_event_id"),
        "caused_by_event_id": _safe_token(
            caused_by_event_id, field="caused_by_event_id"
        ),
        "sections": _safe_token_list(sections, field="section"),
        "counts": _safe_counts(counts),
        "local_revision_before": _safe_named_tokens(
            local_revision_before,
            allowed_keys=_REVISION_KEYS,
            field="local_revision_before",
        ),
        "local_revision_after": _safe_named_tokens(
            local_revision_after,
            allowed_keys=_REVISION_KEYS,
            field="local_revision_after",
        ),
        "peer_revision_before": _safe_named_tokens(
            peer_revision_before,
            allowed_keys=_REVISION_KEYS,
            field="peer_revision_before",
        ),
        "peer_revision_after": _safe_named_tokens(
            peer_revision_after,
            allowed_keys=_REVISION_KEYS,
            field="peer_revision_after",
        ),
        "software_before": _safe_named_tokens(
            software_before,
            allowed_keys=_SOFTWARE_KEYS,
            field="software_before",
        ),
        "software_after": _safe_named_tokens(
            software_after,
            allowed_keys=_SOFTWARE_KEYS,
            field="software_after",
        ),
    }


def _canonical_payload(payload: Mapping[str, Any], previous_event_hash: str) -> str:
    return json.dumps(
        {**payload, "previous_event_hash": previous_event_hash},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _event_hash(payload: Mapping[str, Any], previous_event_hash: str) -> str:
    return hashlib.sha256(
        _canonical_payload(payload, previous_event_hash).encode("utf-8")
    ).hexdigest()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deployment_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            recorded_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL,
            deployment_id TEXT NOT NULL,
            peer_deployment_id TEXT NOT NULL,
            workspace_lineage_ids_json TEXT NOT NULL,
            direction TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            origin_event_id TEXT NOT NULL,
            caused_by_event_id TEXT NOT NULL,
            sections_json TEXT NOT NULL,
            counts_json TEXT NOT NULL,
            local_revision_before_json TEXT NOT NULL,
            local_revision_after_json TEXT NOT NULL,
            peer_revision_before_json TEXT NOT NULL,
            peer_revision_after_json TEXT NOT NULL,
            software_before_json TEXT NOT NULL,
            software_after_json TEXT NOT NULL,
            previous_event_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_deployment_events_recorded_at "
        "ON deployment_events(recorded_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_deployment_events_type "
        "ON deployment_events(event_type, sequence DESC)"
    )
    return conn


_JSON_COLUMNS = {
    "workspace_lineage_ids": "workspace_lineage_ids_json",
    "sections": "sections_json",
    "counts": "counts_json",
    "local_revision_before": "local_revision_before_json",
    "local_revision_after": "local_revision_after_json",
    "peer_revision_before": "peer_revision_before_json",
    "peer_revision_after": "peer_revision_after_json",
    "software_before": "software_before_json",
    "software_after": "software_after_json",
}


def record_event(
    *,
    event_type: str,
    data_root: Optional[Path | str] = None,
    status: str = "completed",
    deployment_id: Optional[str] = None,
    event_id: Optional[str] = None,
    peer_deployment_id: str = "",
    workspace_lineage_ids: Optional[Iterable[str]] = None,
    direction: str = "",
    operation_id: str = "",
    origin_event_id: str = "",
    caused_by_event_id: str = "",
    sections: Optional[Iterable[str]] = None,
    counts: Optional[Mapping[str, Any]] = None,
    local_revision_before: Optional[Mapping[str, Any]] = None,
    local_revision_after: Optional[Mapping[str, Any]] = None,
    peer_revision_before: Optional[Mapping[str, Any]] = None,
    peer_revision_after: Optional[Mapping[str, Any]] = None,
    software_before: Optional[Mapping[str, Any]] = None,
    software_after: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    root = resolve_data_root(data_root)
    descriptor = ensure_deployment_descriptor(root)
    descriptor_id = str(descriptor.get("deployment_id") or "").strip()
    requested_deployment_id = str(deployment_id or "").strip()
    if requested_deployment_id and requested_deployment_id != descriptor_id:
        raise ValueError("deployment_id does not match this deployment data root")
    payload = _event_payload(
        event_id=event_id or str(uuid4()),
        recorded_at=_now_iso(),
        event_type=event_type,
        status=status,
        deployment_id=requested_deployment_id or descriptor_id,
        peer_deployment_id=peer_deployment_id,
        workspace_lineage_ids=workspace_lineage_ids,
        direction=direction,
        operation_id=operation_id,
        origin_event_id=origin_event_id,
        caused_by_event_id=caused_by_event_id,
        sections=sections,
        counts=counts,
        local_revision_before=local_revision_before,
        local_revision_after=local_revision_after,
        peer_revision_before=peer_revision_before,
        peer_revision_after=peer_revision_after,
        software_before=software_before,
        software_after=software_after,
    )
    path = ledger_path(root)
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT event_hash FROM deployment_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = str(row["event_hash"] if row else "")
        digest = _event_hash(payload, previous_hash)
        columns = [
            "event_id",
            "recorded_at",
            "event_type",
            "status",
            "deployment_id",
            "peer_deployment_id",
            "workspace_lineage_ids_json",
            "direction",
            "operation_id",
            "origin_event_id",
            "caused_by_event_id",
            "sections_json",
            "counts_json",
            "local_revision_before_json",
            "local_revision_after_json",
            "peer_revision_before_json",
            "peer_revision_after_json",
            "software_before_json",
            "software_after_json",
            "previous_event_hash",
            "event_hash",
        ]
        values = [
            payload["event_id"],
            payload["recorded_at"],
            payload["event_type"],
            payload["status"],
            payload["deployment_id"],
            payload["peer_deployment_id"],
            json.dumps(payload["workspace_lineage_ids"], separators=(",", ":")),
            payload["direction"],
            payload["operation_id"],
            payload["origin_event_id"],
            payload["caused_by_event_id"],
            json.dumps(payload["sections"], separators=(",", ":")),
            json.dumps(payload["counts"], separators=(",", ":")),
            json.dumps(payload["local_revision_before"], separators=(",", ":")),
            json.dumps(payload["local_revision_after"], separators=(",", ":")),
            json.dumps(payload["peer_revision_before"], separators=(",", ":")),
            json.dumps(payload["peer_revision_after"], separators=(",", ":")),
            json.dumps(payload["software_before"], separators=(",", ":")),
            json.dumps(payload["software_after"], separators=(",", ":")),
            previous_hash,
            digest,
        ]
        placeholders = ", ".join("?" for _ in values)
        cursor = conn.execute(
            f"INSERT INTO deployment_events ({', '.join(columns)}) "
            f"VALUES ({placeholders})",
            values,
        )
        sequence = int(cursor.lastrowid)
        conn.commit()
    return {
        **payload,
        "sequence": sequence,
        "previous_event_hash": previous_hash,
        "event_hash": digest,
    }


def _row_event(row: sqlite3.Row) -> Dict[str, Any]:
    event: Dict[str, Any] = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "sequence": int(row["sequence"]),
        "event_id": str(row["event_id"]),
        "recorded_at": str(row["recorded_at"]),
        "event_type": str(row["event_type"]),
        "status": str(row["status"]),
        "deployment_id": str(row["deployment_id"]),
        "peer_deployment_id": str(row["peer_deployment_id"]),
        "direction": str(row["direction"]),
        "operation_id": str(row["operation_id"]),
        "origin_event_id": str(row["origin_event_id"]),
        "caused_by_event_id": str(row["caused_by_event_id"]),
        "previous_event_hash": str(row["previous_event_hash"]),
        "event_hash": str(row["event_hash"]),
    }
    for key, column in _JSON_COLUMNS.items():
        try:
            event[key] = json.loads(str(row[column]))
        except (TypeError, ValueError):
            event[key] = [] if key in {"workspace_lineage_ids", "sections"} else {}
    try:
        event["recorded_at_ts"] = datetime.fromisoformat(
            event["recorded_at"].replace("Z", "+00:00")
        ).timestamp()
    except ValueError:
        event["recorded_at_ts"] = 0.0
    return event


def list_events(
    *,
    data_root: Optional[Path | str] = None,
    limit: int = 200,
    event_type: Optional[str] = None,
) -> list[Dict[str, Any]]:
    path = ledger_path(data_root)
    if not path.is_file():
        return []
    bounded_limit = max(1, min(int(limit), 1000))
    with _connect(path) as conn:
        if event_type:
            normalized_type = str(event_type).strip().lower()
            if normalized_type not in EVENT_TYPES:
                raise ValueError(
                    f"Unsupported deployment event type: {normalized_type}"
                )
            rows = conn.execute(
                "SELECT * FROM deployment_events WHERE event_type = ? "
                "ORDER BY sequence DESC LIMIT ?",
                (normalized_type, bounded_limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM deployment_events ORDER BY sequence DESC LIMIT ?",
                (bounded_limit,),
            ).fetchall()
    return [_row_event(row) for row in rows]


def get_event(
    event_id: str, *, data_root: Optional[Path | str] = None
) -> Optional[Dict[str, Any]]:
    token = _safe_token(event_id, field="event_id", required=True)
    path = ledger_path(data_root)
    if not path.is_file():
        return None
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM deployment_events WHERE event_id = ?", (token,)
        ).fetchone()
    return _row_event(row) if row else None


def verify_chain(*, data_root: Optional[Path | str] = None) -> Dict[str, Any]:
    path = ledger_path(data_root)
    if not path.is_file():
        return {"valid": True, "event_count": 0, "broken_sequence": None}
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM deployment_events ORDER BY sequence ASC"
        ).fetchall()
    previous_hash = ""
    for row in rows:
        event = _row_event(row)
        payload = {
            key: value
            for key, value in event.items()
            if key
            not in {
                "sequence",
                "recorded_at_ts",
                "previous_event_hash",
                "event_hash",
            }
        }
        expected = _event_hash(payload, previous_hash)
        if (
            event["previous_event_hash"] != previous_hash
            or event["event_hash"] != expected
        ):
            return {
                "valid": False,
                "event_count": len(rows),
                "broken_sequence": event["sequence"],
            }
        previous_hash = event["event_hash"]
    return {"valid": True, "event_count": len(rows), "broken_sequence": None}


def action_id_for_event(event_id: str) -> str:
    return f"{ACTION_ID_PREFIX}{event_id}"


def event_id_from_action_id(action_id: str) -> str:
    value = str(action_id or "")
    return value[len(ACTION_ID_PREFIX) :] if value.startswith(ACTION_ID_PREFIX) else ""


def _event_summary(event: Mapping[str, Any]) -> str:
    event_type = str(event.get("event_type") or "")
    counts = event.get("counts") if isinstance(event.get("counts"), dict) else {}
    if event_type == "software.install":
        software = (
            event.get("software_after")
            if isinstance(event.get("software_after"), dict)
            else {}
        )
        version = str(software.get("release_version") or "")
        build = str(software.get("build_code") or "")
        label = f"{version} // {build}" if build else version
        return f"Installed Float {label}" if label else "Installed Float build"
    if event_type == "data.sync":
        direction = str(event.get("direction") or "sync").replace("_", " ")
        sections = (
            event.get("sections") if isinstance(event.get("sections"), list) else []
        )
        section_label = ", ".join(str(value) for value in sections)
        return (
            f"{direction.capitalize()} sync: {section_label}"
            if section_label
            else f"{direction.capitalize()} sync"
        )
    if event_type == "data.revision":
        revision = (
            event.get("local_revision_after")
            if isinstance(event.get("local_revision_after"), dict)
            else {}
        )
        code = str(revision.get("code") or "")
        return (
            f"Observed local data revision {code}"
            if code
            else "Observed local data revision"
        )
    before_count = int(counts.get("before_count") or 0)
    after_count = int(counts.get("after_count") or 0)
    deleted_count = int(counts.get("deleted_count") or 0)
    changed_count = int(counts.get("changed_count") or 0)
    if event_type == "data.bulk_replace":
        return (
            f"Bulk data replacement: {before_count} -> {after_count} records "
            f"({deleted_count} removed)"
        )
    if event_type == "data.delete":
        return f"Deleted {deleted_count} data record{'s' if deleted_count != 1 else ''}"
    if event_type == "data.restore":
        return (
            f"Restored {changed_count} data record{'s' if changed_count != 1 else ''}"
        )
    if event_type == "data.safety_snapshot":
        return "Recorded a data safety snapshot"
    return f"Updated {changed_count} data record{'s' if changed_count != 1 else ''}"


def event_to_action_summary(event: Mapping[str, Any]) -> Dict[str, Any]:
    event_type = str(event.get("event_type") or "")
    counts = event.get("counts") if isinstance(event.get("counts"), dict) else {}
    item_count = int(counts.get("changed_count") or counts.get("deleted_count") or 0)
    if event_type == "software.install":
        kind = "deployment"
    elif event_type == "data.sync":
        kind = "sync"
    else:
        kind = "data"
    return {
        "id": action_id_for_event(str(event.get("event_id") or "")),
        "kind": kind,
        "name": event_type.replace(".", "_"),
        "summary": _event_summary(event),
        "status": str(event.get("status") or "completed"),
        "created_at_ts": float(event.get("recorded_at_ts") or 0.0),
        "timestamp": float(event.get("recorded_at_ts") or 0.0),
        "item_count": item_count,
        "revertible": False,
        "metadata_only": True,
        "deployment_event": {
            key: event.get(key)
            for key in (
                "event_id",
                "event_type",
                "deployment_id",
                "peer_deployment_id",
                "workspace_lineage_ids",
                "direction",
                "operation_id",
                "origin_event_id",
                "caused_by_event_id",
                "sections",
                "counts",
                "local_revision_before",
                "local_revision_after",
                "peer_revision_before",
                "peer_revision_after",
                "software_before",
                "software_after",
                "event_hash",
            )
        },
    }


__all__ = [
    "ACTION_ID_PREFIX",
    "EVENT_TYPES",
    "LEDGER_FILENAME",
    "action_id_for_event",
    "event_id_from_action_id",
    "event_to_action_summary",
    "get_event",
    "ledger_path",
    "list_events",
    "record_event",
    "verify_chain",
]
