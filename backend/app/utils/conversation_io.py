"""Conversation import/export helpers for readable formats."""

from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _normalize_role(role: str) -> str:
    cleaned = (role or "").strip().lower()
    if cleaned in {"assistant", "ai", "model"}:
        return "ai"
    if cleaned in {"user", "system", "tool"}:
        return cleaned
    return "ai" if cleaned else "ai"


def _join_lines(lines: List[str]) -> str:
    if not lines:
        return ""
    # Preserve paragraph breaks, trim trailing whitespace.
    text = "\n".join(lines).rstrip()
    return text


def _summarize_thought_trace(thought_trace: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(thought_trace, list) or not thought_trace:
        return None
    texts: List[str] = []
    timestamps: List[float] = []
    for item in thought_trace:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            texts.append(text)
        ts = item.get("timestamp")
        if isinstance(ts, (int, float)):
            timestamps.append(float(ts))
    if not texts:
        return None
    concatenated = " ".join(t.strip() for t in texts if t is not None).strip()
    tokens = len(concatenated.split()) if concatenated else 0
    seconds = 0
    if timestamps:
        seconds = int(round(max(timestamps) - min(timestamps)))
    return {
        "tokens": tokens,
        "seconds": seconds,
        "responses": len(texts),
        "text": concatenated,
    }


def export_conversation_json(
    *,
    name: str,
    messages: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
    include_chat: bool = True,
    include_thoughts: bool = True,
    include_tools: bool = True,
) -> Dict[str, Any]:
    if not include_chat or not include_thoughts or not include_tools:
        filtered: List[Dict[str, Any]] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            clone = dict(msg)
            if not include_chat:
                clone.pop("text", None)
                clone.pop("content", None)
            if not include_thoughts:
                clone.pop("thought", None)
                clone.pop("thought_trace", None)
            if not include_tools:
                clone.pop("tools", None)
            filtered.append(clone)
        messages = filtered
    summary: Dict[str, Any] = {
        "name": name,
        "exported_at": _now_iso(),
        "message_count": len(messages),
        "messages": messages,
    }
    if metadata:
        summary["metadata"] = metadata
    return summary


def export_conversation_markdown(
    *,
    name: str,
    messages: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
    include_chat: bool = True,
    include_thoughts: bool = True,
    include_tools: bool = True,
) -> str:
    lines: List[str] = []
    lines.append("# Conversation Export")
    lines.append(f"- name: {name}")
    if metadata:
        for key in ("id", "display_name", "created_at", "updated_at", "message_count"):
            if key in metadata and metadata[key] is not None:
                lines.append(f"- {key}: {metadata[key]}")
    lines.append(f"- exported_at: {_now_iso()}")
    lines.append("")
    lines.append("## Messages")
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = _normalize_role(str(msg.get("role") or "ai"))
        msg_id = msg.get("id") or ""
        ts = msg.get("iso_timestamp") or msg.get("timestamp") or ""
        header = f"### [{role}]"
        if msg_id:
            header += f" id={msg_id}"
        if ts:
            header += f" ts={ts}"
        if role == "ai":
            status = None
            meta = msg.get("metadata")
            if isinstance(meta, dict):
                status = meta.get("status")
            if status:
                header += f" status={status}"
        lines.append(header)
        if include_chat:
            text = msg.get("text") or msg.get("content") or ""
            if text:
                if not isinstance(text, str):
                    try:
                        text = json.dumps(text, ensure_ascii=False)
                    except Exception:
                        text = str(text)
                lines.append(text.strip())
            lines.append("")
        if include_thoughts:
            thought_summary = _summarize_thought_trace(msg.get("thought_trace"))
            if thought_summary:
                lines.append("#### thoughts")
                lines.append(
                    "thoughts: {tokens} tokens, {seconds}s, {responses} responses: {text}".format(
                        **thought_summary
                    )
                )
                lines.append("")
        if include_tools:
            tools = msg.get("tools")
            if isinstance(tools, list) and tools:
                lines.append("#### tools")
                for tool in tools:
                    if not isinstance(tool, dict):
                        continue
                    name_value = tool.get("name") or "tool"
                    status_value = tool.get("status") or "event"
                    args_value = tool.get("args")
                    result_value = tool.get("result")
                    try:
                        args_text = json.dumps(args_value, ensure_ascii=False)
                    except Exception:
                        args_text = str(args_value)
                    try:
                        result_text = json.dumps(result_value, ensure_ascii=False)
                    except Exception:
                        result_text = str(result_value)
                    lines.append(
                        f"- [x] {name_value} ({status_value}) args={args_text} result={result_text}"
                    )
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def export_conversation_text(
    *,
    name: str,
    messages: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
    include_chat: bool = True,
    include_thoughts: bool = True,
    include_tools: bool = True,
) -> str:
    lines: List[str] = []
    if metadata:
        title = metadata.get("display_name") or name
        lines.append(f"{title} ({name})")
        created = metadata.get("created_at")
        if created:
            lines.append(f"created_at: {created}")
        lines.append("")
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = _normalize_role(str(msg.get("role") or "ai"))
        if include_chat:
            text = msg.get("text") or msg.get("content") or ""
            if not isinstance(text, str):
                try:
                    text = json.dumps(text, ensure_ascii=False)
                except Exception:
                    text = str(text)
            lines.append(f"[{role}] {text}".rstrip())
        else:
            lines.append(f"[{role}]")
        if include_thoughts:
            thought_summary = _summarize_thought_trace(msg.get("thought_trace"))
            if thought_summary:
                lines.append(
                    "thoughts: {tokens} tokens, {seconds}s, {responses} responses: {text}".format(
                        **thought_summary
                    )
                )
        if include_tools:
            tools = msg.get("tools")
            if isinstance(tools, list) and tools:
                for tool in tools:
                    if not isinstance(tool, dict):
                        continue
                    name_value = tool.get("name") or "tool"
                    status_value = tool.get("status") or "event"
                    args_value = tool.get("args")
                    result_value = tool.get("result")
                    try:
                        args_text = json.dumps(args_value, ensure_ascii=False)
                    except Exception:
                        args_text = str(args_value)
                    try:
                        result_text = json.dumps(result_value, ensure_ascii=False)
                    except Exception:
                        result_text = str(result_value)
                    lines.append(
                        f"- {name_value} ({status_value}) args={args_text} result={result_text}"
                    )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


_HEADER_RE = re.compile(r"^### \[(?P<role>[^\]]+)\](?P<rest>.*)$")
_ID_RE = re.compile(r"\bid=([^\s]+)")
_TS_RE = re.compile(r"\bts=([^\s]+)")


def _safe_json_loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return None


def _coerce_role(value: Any) -> str:
    role = str(value or "").strip().lower()
    if role in {"assistant", "ai", "model"}:
        return "ai"
    if role in {"user", "system", "tool"}:
        return role
    return "ai"


def _coerce_text_parts(parts: Any) -> Optional[str]:
    if isinstance(parts, str):
        text = parts.strip()
        return text or None
    if isinstance(parts, list):
        pieces: List[str] = []
        for item in parts:
            if isinstance(item, str):
                candidate = item.strip()
                if candidate:
                    pieces.append(candidate)
                continue
            if not isinstance(item, dict):
                continue
            for key in ("text", "content", "value"):
                nested = item.get(key)
                if isinstance(nested, str):
                    candidate = nested.strip()
                    if candidate:
                        pieces.append(candidate)
                    break
        if pieces:
            return " ".join(pieces).strip()
    return None


def _parse_openai_message_content(
    content: Any,
) -> tuple[Optional[str], list[Dict[str, Any]]]:
    if content is None:
        return None, []

    if isinstance(content, str):
        return _coerce_text_parts(content), []

    text = None
    if not isinstance(content, dict):
        return None, []

    raw_parts = content.get("parts")
    text = _coerce_text_parts(raw_parts)

    if text is None:
        text_value = content.get("text")
        if isinstance(text_value, dict):
            text = _coerce_text_parts(text_value.get("value"))
        elif isinstance(text_value, str):
            text = _coerce_text_parts(text_value)
        elif isinstance(text_value, list):
            text = _coerce_text_parts(text_value)

    if text is None:
        nested_content = content.get("content")
        if isinstance(nested_content, dict):
            text = _coerce_text_parts(nested_content.get("text"))

    attachments: list[Dict[str, Any]] = []
    for attachment in content.get("attachments", []) or []:
        if not isinstance(attachment, dict):
            continue
        attachments.append(
            {
                "name": attachment.get("file_name")
                or attachment.get("filename")
                or attachment.get("name")
                or "attachment",
                "type": attachment.get("mime_type")
                or attachment.get("content_type")
                or "application/octet-stream",
                "asset_id": attachment.get("asset_id") or attachment.get("id"),
                "source": attachment.get("source")
                or attachment.get("path")
                or "attachment",
                "status": "imported",
            }
        )

    return text, attachments


def _normalise_openai_content(content_obj: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(content_obj, dict):
        return None
    message_content = content_obj.get("message") or content_obj
    if not isinstance(message_content, dict):
        return None
    role = _coerce_role(
        (message_content.get("author") or {}).get("role")
        if isinstance(message_content.get("author"), dict)
        else message_content.get("role")
    )
    text, attachments = _parse_openai_message_content(message_content.get("content"))
    if text is None:
        text, attachments = _parse_openai_message_content(message_content)
    if text is None:
        return None
    out: Dict[str, Any] = {"role": role, "content": text, "text": text}
    if attachments:
        out["attachments"] = attachments
    return out


def _score_openai_payload(candidate: Any) -> int:
    if not isinstance(candidate, dict):
        return 0
    if isinstance(candidate.get("messages"), list):
        return 10
    if isinstance(candidate.get("mapping"), dict):
        return 9
    if isinstance(candidate.get("conversations"), list):
        return 6
    if isinstance(candidate.get("data"), dict):
        return 3
    return 1


def _pick_openai_json_candidate(files: List[str], data_by_name: Dict[str, Any]) -> str:
    if not files:
        return ""
    if len(files) == 1:
        return files[0]

    def _score(name: str) -> int:
        lower = (name or "").lower()
        if "conversation" in lower:
            return 3
        if re.search(r"\b(messages?|threads?)\b", lower):
            return 1
        return 0

    best = sorted(
        files,
        key=lambda name: (
            _score_openai_payload(data_by_name.get(name, None)),
            _score(name),
        ),
        reverse=True,
    )[0]
    return best


def _openai_conversation_selector(
    conversation: Dict[str, Any], index: int, used: Optional[set[str]] = None
) -> str:
    if used is None:
        used = set()
    raw_key = (
        conversation.get("id")
        or conversation.get("uuid")
        or conversation.get("conversation_id")
    )
    key = str(raw_key or f"index:{index}").strip()
    if not key:
        key = f"index:{index}"
    key = key.replace("/", "-").replace("\\", "-")
    candidate = key
    counter = 1
    while candidate in used:
        counter += 1
        candidate = f"{key}-{counter}"
    return candidate


def _openai_conversation_message_count(conversation: Dict[str, Any]) -> int:
    if isinstance(conversation.get("messages"), list):
        return len(conversation.get("messages") or [])
    mapping = conversation.get("mapping")
    if isinstance(mapping, dict):
        return len(mapping)
    if isinstance(conversation.get("export"), dict):
        messages = conversation["export"].get("messages")
        if isinstance(messages, list):
            return len(messages)
    return 0


def list_openai_conversation_json_candidates(
    data: bytes, *, filename: Optional[str] = None
) -> List[Dict[str, Any]]:
    del filename
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        return []
    parsed = _safe_json_loads(text)
    return list_openai_conversation_json_candidates_from_object(parsed)


def list_openai_conversation_json_candidates_from_object(
    payload_obj: Any,
) -> List[Dict[str, Any]]:
    if not isinstance(payload_obj, dict):
        return []
    conversations = payload_obj.get("conversations")
    if not isinstance(conversations, list):
        return []
    candidates: List[Dict[str, Any]] = []
    used: set[str] = set()
    for index, conversation in enumerate(conversations):
        if not isinstance(conversation, dict):
            continue
        key = _openai_conversation_selector(conversation, index, used=used)
        used.add(key)
        candidates.append(
            {
                "path": key,
                "name": conversation.get("title")
                or conversation.get("name")
                or conversation.get("id")
                or key,
                "message_count": _openai_conversation_message_count(conversation),
            }
        )
    candidates.sort(key=lambda item: item["message_count"], reverse=True)
    return candidates


def extract_openai_json_conversations(
    data: bytes, *, selected_files: Optional[Any] = None
) -> Dict[str, List[Dict[str, Any]]]:
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        return {}
    parsed = _safe_json_loads(text)
    if not isinstance(parsed, dict):
        return {}
    conversations = parsed.get("conversations")
    if not isinstance(conversations, list):
        return {}
    normalized_selected: List[str] = []
    if not selected_files:
        return {}
    seen = set()
    for item in selected_files:
        candidate = str(item).strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            normalized_selected.append(candidate)
    if not normalized_selected:
        return {}
    selected_set = set(normalized_selected)
    output: Dict[str, List[Dict[str, Any]]] = {}
    used: set[str] = set()
    for index, conversation in enumerate(conversations):
        if not isinstance(conversation, dict):
            continue
        key = _openai_conversation_selector(conversation, index, used=used)
        used.add(key)
        if key not in selected_set:
            continue
        messages = import_openai_conversation_json(conversation)
        if messages:
            output[key] = messages
    return output


def import_openai_conversation_json(payload_obj: Any) -> List[Dict[str, Any]]:
    if isinstance(payload_obj, list):
        parsed_messages = []
        for item in payload_obj:
            msg = _normalise_openai_content(item)
            if not msg:
                msg = _normalise_openai_content(
                    item.get("message") if isinstance(item, dict) else None
                )
            if msg:
                parsed_messages.append(msg)
        if parsed_messages:
            return parsed_messages

    if isinstance(payload_obj, dict):
        if isinstance(payload_obj.get("messages"), list):
            messages: List[Dict[str, Any]] = []
            for item in payload_obj["messages"]:
                msg = _normalise_openai_content(item)
                if msg:
                    messages.append(msg)
            if messages:
                return messages
        if isinstance(payload_obj.get("mapping"), dict):
            messages: List[Dict[str, Any]] = []
            nodes = payload_obj.get("mapping") or {}
            for _, node in sorted(
                nodes.items(),
                key=lambda item: (
                    float(
                        ((item[1] or {}).get("message") or {}).get("create_time") or 0.0
                    )
                    if isinstance(item, tuple) and isinstance(item[1], dict)
                    else 0.0
                ),
            ):
                if not isinstance(node, dict):
                    continue
                content_msg = node.get("message")
                msg = _normalise_openai_content(content_msg)
                if msg:
                    msg["id"] = node.get("id") or msg.get("id")
                    if "id" in node and not msg.get("id"):
                        msg["id"] = node["id"]
                    if isinstance(node.get("create_time"), (int, float)):
                        msg["timestamp"] = node["create_time"]
                        msg["iso_timestamp"] = str(node["create_time"])
                    messages.append(msg)
            if messages:
                return messages
        if (
            isinstance(payload_obj.get("conversations"), list)
            and payload_obj["conversations"]
        ):
            first = payload_obj["conversations"][0]
            return import_openai_conversation_json(first)
        if isinstance(payload_obj.get("data"), dict):
            return import_openai_conversation_json(payload_obj["data"])

    return []


def _extract_openai_zip_messages(parsed: Any) -> List[Dict[str, Any]]:
    parsed_messages = import_openai_conversation_json(parsed)
    if parsed_messages:
        return parsed_messages
    raw_messages = import_conversation_json_raw(parsed)
    return raw_messages if isinstance(raw_messages, list) else []


def _collect_openai_zip_message_map(
    data: bytes
) -> tuple[Dict[str, Any], Dict[str, List[Dict[str, Any]]]]:
    parsed_payloads: Dict[str, Any] = {}
    parsed_messages: Dict[str, List[Dict[str, Any]]] = {}
    with zipfile.ZipFile(BytesIO(data)) as archive:
        json_members = [
            name for name in archive.namelist() if name.lower().endswith(".json")
        ]
        for name in json_members:
            try:
                text = archive.read(name).decode("utf-8", errors="ignore")
            except Exception:
                continue
            parsed = _safe_json_loads(text)
            if parsed is None:
                continue
            parsed_payloads[name] = parsed
            extracted = _extract_openai_zip_messages(parsed)
            if isinstance(extracted, list) and extracted:
                parsed_messages[name] = extracted
    return parsed_payloads, parsed_messages


def extract_openai_zip_messages(
    data: bytes, *, selected_files: Optional[Any] = None
) -> Dict[str, List[Dict[str, Any]]]:
    _, parsed_messages = _collect_openai_zip_message_map(data)
    if not selected_files:
        return parsed_messages
    normalized_selected = []
    seen = set()
    for member in selected_files:
        candidate = str(member).strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized_selected.append(candidate)
    return {
        member: parsed_messages[member]
        for member in normalized_selected
        if member in parsed_messages
    }


def list_openai_conversation_zip_candidates(
    data: bytes, *, filename: Optional[str] = None
) -> List[Dict[str, Any]]:
    del filename
    _, parsed_messages = _collect_openai_zip_message_map(data)
    candidates = [
        {
            "path": path,
            "message_count": len(messages),
        }
        for path, messages in parsed_messages.items()
    ]
    candidates.sort(key=lambda item: item["message_count"], reverse=True)
    return candidates


def import_openai_conversation_zip(
    data: bytes, *, filename: Optional[str] = None
) -> List[Dict[str, Any]]:
    try:
        parsed_payloads, parsed_messages = _collect_openai_zip_message_map(data)
        if not parsed_payloads:
            return []
        if parsed_messages:
            # Prefer the most populated candidate by message count. This avoids
            # accidentally picking a small metadata-only JSON file when a full
            # conversation export is also present.
            candidate_name = max(
                parsed_messages.items(), key=lambda item: len(item[1])
            )[0]
            return parsed_messages[candidate_name]
        candidate_name = _pick_openai_json_candidate(
            list(parsed_payloads.keys()), parsed_payloads
        )
        if not candidate_name:
            return []
        fallback = import_openai_conversation_json(parsed_payloads[candidate_name])
        if fallback:
            return fallback
        fallback_raw = import_conversation_json_raw(parsed_payloads[candidate_name])
        return fallback_raw if isinstance(fallback_raw, list) else []
    except Exception:
        return []


def import_conversation_json_raw(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        return data.get("messages") or []
    if isinstance(data, list):
        return data
    return []


def import_conversation_markdown(text: str) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    buffer: List[str] = []
    in_thoughts = False
    in_tools = False

    def _flush() -> None:
        nonlocal current, buffer
        if current is None:
            buffer = []
            return
        content = _join_lines(buffer)
        if content:
            current["text"] = content
        messages.append(current)
        current = None
        buffer = []

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        header_match = _HEADER_RE.match(line)
        if header_match:
            _flush()
            role = _normalize_role(header_match.group("role"))
            rest = header_match.group("rest") or ""
            msg: Dict[str, Any] = {"role": role}
            id_match = _ID_RE.search(rest)
            if id_match:
                msg["id"] = id_match.group(1)
            ts_match = _TS_RE.search(rest)
            if ts_match:
                msg["timestamp"] = ts_match.group(1)
                msg["iso_timestamp"] = ts_match.group(1)
            current = msg
            in_thoughts = False
            continue
        if line.strip().lower().startswith("#### thoughts"):
            in_thoughts = True
            continue
        if line.strip().lower().startswith("#### tools"):
            in_tools = True
            continue
        if in_thoughts:
            # Ignore thought summary lines on import.
            if line.strip() == "":
                in_thoughts = False
            continue
        if in_tools:
            if line.strip() == "":
                in_tools = False
            continue
        if current is not None:
            buffer.append(line)
    _flush()
    return messages
