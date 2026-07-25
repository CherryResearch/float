"""Conversation import/export helpers for readable formats."""

from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional

_HEADER_RE = re.compile(r"^### \[(?P<role>[^\]]+)\](?P<rest>.*)$")
_TEXT_HEADER_RE = re.compile(r"^\[(?P<role>[^\]]+)\](?:[ \t](?P<text>.*))?$")
_MARKDOWN_EXPORT_VERSION_LINE = "- format_version: 2"
_TEXT_EXPORT_BANNER = "Float Conversation Text Export"
_TEXT_EXPORT_VERSION_LINE = "format_version: 2"
_MARKDOWN_CONTROL_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<slashes>\\*)(?P<control>"
    r"### \[[^\]]+\].*|#### thoughts|#### tools)(?P<trailing>[ \t]*)$",
    re.IGNORECASE,
)
_TEXT_ROLE_CONTROL_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<slashes>\\*)(?P<control>"
    r"\[[^\]]+\](?:[ \t].*)?)(?P<trailing>[ \t]*)$"
)


def _escape_control_line(line: str, pattern: re.Pattern[str]) -> str:
    match = pattern.match(line)
    if not match:
        return line
    return "".join(
        (
            match.group("indent"),
            "\\",
            match.group("slashes"),
            match.group("control"),
            match.group("trailing"),
        )
    )


def _unescape_control_line(line: str, pattern: re.Pattern[str]) -> str:
    match = pattern.match(line)
    if not match or not match.group("slashes"):
        return line
    return "".join(
        (
            match.group("indent"),
            match.group("slashes")[1:],
            match.group("control"),
            match.group("trailing"),
        )
    )


def _escape_markdown_message_text(text: str) -> str:
    return "\n".join(
        _escape_control_line(line, _MARKDOWN_CONTROL_LINE_RE)
        for line in str(text or "").split("\n")
    )


def _unescape_markdown_message_line(line: str) -> str:
    return _unescape_control_line(line, _MARKDOWN_CONTROL_LINE_RE)


def _escape_text_message_text(text: str) -> str:
    escaped_lines: List[str] = []
    for line in str(text or "").split("\n"):
        escaped = _escape_control_line(line, _TEXT_ROLE_CONTROL_LINE_RE)
        if escaped == line:
            escaped = _escape_control_line(line, _TEXT_METADATA_CONTROL_LINE_RE)
        if escaped == line and line.strip() == _TEXT_EXPORT_BANNER:
            escaped = line.replace(_TEXT_EXPORT_BANNER, f"\\{_TEXT_EXPORT_BANNER}", 1)
        escaped_lines.append(escaped)
    return "\n".join(escaped_lines)


def _unescape_text_message_line(line: str) -> str:
    unescaped = _unescape_control_line(line, _TEXT_ROLE_CONTROL_LINE_RE)
    if unescaped == line:
        unescaped = _unescape_control_line(line, _TEXT_METADATA_CONTROL_LINE_RE)
    if unescaped == line and line.strip() == f"\\{_TEXT_EXPORT_BANNER}":
        return line.replace(f"\\{_TEXT_EXPORT_BANNER}", _TEXT_EXPORT_BANNER, 1)
    return unescaped


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _single_line_export_value(value: Any, fallback: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value or fallback)).strip()
    return normalized or fallback


def _serialize_tool_export_value(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return _single_line_export_value(value, "null")


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
    concatenated = re.sub(r"\s+", " ", concatenated).strip()
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
    lines.append(_MARKDOWN_EXPORT_VERSION_LINE)
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
                lines.append(_escape_markdown_message_text(text.strip()))
            lines.append("")
        if include_thoughts:
            thought_summary = _summarize_thought_trace(msg.get("thought_trace"))
            if thought_summary:
                thought_summary = dict(thought_summary)
                thought_summary["text"] = _escape_markdown_message_text(
                    str(thought_summary.get("text") or "")
                )
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
                    name_value = _single_line_export_value(tool.get("name"), "tool")
                    status_value = _single_line_export_value(
                        tool.get("status"), "event"
                    )
                    args_value = tool.get("args")
                    result_value = tool.get("result")
                    args_text = _serialize_tool_export_value(args_value)
                    result_text = _serialize_tool_export_value(result_value)
                    tool_line = (
                        f"- [x] {name_value} ({status_value}) "
                        f"args={args_text} result={result_text}"
                    )
                    lines.append(tool_line)
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
    lines: List[str] = [_TEXT_EXPORT_BANNER, _TEXT_EXPORT_VERSION_LINE]
    if metadata:
        title = _single_line_export_value(metadata.get("display_name"), name)
        export_name = _single_line_export_value(name, "conversation")
        lines.append(_escape_text_message_text(f"{title} ({export_name})"))
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
            escaped_text = _escape_text_message_text(text)
            lines.append(f"[{role}] {escaped_text}".rstrip())
        else:
            lines.append(f"[{role}]")
        if include_thoughts:
            thought_summary = _summarize_thought_trace(msg.get("thought_trace"))
            if thought_summary:
                thought_summary = dict(thought_summary)
                thought_summary["text"] = _escape_text_message_text(
                    str(thought_summary.get("text") or "")
                )
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
                    name_value = _single_line_export_value(tool.get("name"), "tool")
                    status_value = _single_line_export_value(
                        tool.get("status"), "event"
                    )
                    args_value = tool.get("args")
                    result_value = tool.get("result")
                    args_text = _serialize_tool_export_value(args_value)
                    result_text = _serialize_tool_export_value(result_value)
                    tool_line = (
                        f"- {name_value} ({status_value}) "
                        f"args={args_text} result={result_text}"
                    )
                    lines.append(tool_line)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


_ID_RE = re.compile(r"\bid=([^\s]+)")
_TS_RE = re.compile(r"\bts=([^\s]+)")
_FENCE_RE = re.compile(r"^\s{0,3}(?P<marker>`{3,}|~{3,})")
_THOUGHT_EXPORT_RE = re.compile(
    r"^thoughts:\s+\d+\s+tokens,\s+-?\d+(?:\.\d+)?s,\s+\d+\s+responses:",
    re.IGNORECASE,
)
_TOOL_EXPORT_RE = re.compile(
    r"^-\s+\[x\]\s+.+\s+args=.*\s+result=.*$",
    re.IGNORECASE,
)
_TEXT_TOOL_EXPORT_RE = re.compile(
    r"^-\s+.+\s+\([^\r\n]*\)\s+args=.*\s+result=.*$",
    re.IGNORECASE,
)
_TEXT_METADATA_CONTROL_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<slashes>\\*)(?P<control>"
    r"thoughts:\s+\d+\s+tokens,\s+-?\d+(?:\.\d+)?s,\s+\d+\s+responses:.*"
    r"|-\s+.+\s+\([^\r\n]*\)\s+args=.*\s+result=.*)(?P<trailing>[ \t]*)$",
    re.IGNORECASE,
)
_IMPORT_ROLE_ALIASES = {
    "assistant": "ai",
    "ai": "ai",
    "model": "ai",
    "user": "user",
    "system": "system",
    "tool": "tool",
}
_MARKDOWN_IMPORT_PREVIEW_CHARS = 800


def _normalize_import_role(role: str) -> Optional[str]:
    """Normalize only roles that are valid in a conversation transcript."""

    return _IMPORT_ROLE_ALIASES.get(str(role or "").strip().lower())


def _markdown_import_preview(text: str) -> str:
    cleaned = str(text or "").lstrip("\ufeff").strip()
    if len(cleaned) <= _MARKDOWN_IMPORT_PREVIEW_CHARS:
        return cleaned
    return cleaned[:_MARKDOWN_IMPORT_PREVIEW_CHARS].rstrip() + "..."


def _has_unparsed_markdown_preamble(
    lines: List[str], *, has_float_export_banner: bool
) -> bool:
    meaningful = [line.strip().lstrip("\ufeff") for line in lines if line.strip()]
    if not meaningful:
        return False
    if not has_float_export_banner:
        return True

    saw_banner = False
    saw_messages_heading = False
    for line in meaningful:
        if not saw_banner:
            if line == "# Conversation Export":
                saw_banner = True
                continue
            return True
        if line == "## Messages" and not saw_messages_heading:
            saw_messages_heading = True
            continue
        if not saw_messages_heading and line.startswith("- "):
            continue
        return True
    return False


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
        messages = import_openai_conversation_json(conversation)
        if not messages:
            continue
        candidates.append(
            {
                "path": key,
                "name": conversation.get("title")
                or conversation.get("name")
                or conversation.get("id")
                or key,
                "message_count": len(messages),
            }
        )
    candidates.sort(key=lambda item: item["message_count"], reverse=True)
    return candidates


def summarize_openai_conversation_json_candidates(
    data: bytes, *, filename: Optional[str] = None
) -> Dict[str, Any]:
    del filename
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        return {
            "detected_files": [],
            "importable_conversation_count": 0,
            "ignored_json_entry_count": 0,
        }
    parsed = _safe_json_loads(text)
    candidates = list_openai_conversation_json_candidates_from_object(parsed)
    ignored_count = 0
    total_count = 0
    if isinstance(parsed, dict) and isinstance(parsed.get("conversations"), list):
        conversations = parsed.get("conversations") or []
        total_count = sum(1 for item in conversations if isinstance(item, dict))
        ignored_count = max(0, total_count - len(candidates))
    return {
        "detected_files": candidates,
        "importable_conversation_count": len(candidates),
        "ignored_json_entry_count": ignored_count,
        "json_entry_count": total_count,
    }


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
    data: bytes,
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


def summarize_openai_conversation_zip_candidates(
    data: bytes, *, filename: Optional[str] = None
) -> Dict[str, Any]:
    del filename
    parsed_payloads, parsed_messages = _collect_openai_zip_message_map(data)
    candidates = [
        {
            "path": path,
            "message_count": len(messages),
        }
        for path, messages in parsed_messages.items()
    ]
    candidates.sort(key=lambda item: item["message_count"], reverse=True)
    ignored_files = sorted(
        path for path in parsed_payloads.keys() if path not in parsed_messages
    )
    return {
        "detected_files": candidates,
        "importable_conversation_count": len(candidates),
        "ignored_json_file_count": len(ignored_files),
        "ignored_json_files": ignored_files[:20],
        "json_file_count": len(parsed_payloads),
    }


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


def _scan_conversation_markdown(text: str) -> Dict[str, Any]:
    messages: List[Dict[str, Any]] = []
    recognized_roles: List[str] = []
    unknown_roles: List[str] = []
    unparsed_preamble_lines: List[str] = []
    current: Optional[Dict[str, Any]] = None
    buffer: List[str] = []
    in_thoughts = False
    in_tools = False
    empty_message_count = 0
    fence_marker: Optional[str] = None
    recognized_headers_in_active_fence = 0
    has_float_export_banner = False
    uses_escaped_control_lines = False

    def _flush() -> None:
        nonlocal current, buffer, empty_message_count
        if current is None:
            buffer = []
            return
        content_lines = (
            [_unescape_markdown_message_line(line) for line in buffer]
            if uses_escaped_control_lines
            else buffer
        )
        content = _join_lines(content_lines)
        if content:
            current["text"] = content
        else:
            empty_message_count += 1
        messages.append(current)
        current = None
        buffer = []

    source_text = str(text or "")
    if source_text.startswith("\ufeff"):
        source_text = source_text[1:]

    source_lines = source_text.splitlines()
    first_meaningful = next((line.strip() for line in source_lines if line.strip()), "")
    messages_heading_index = next(
        (
            index
            for index, line in enumerate(source_lines)
            if line.strip() == "## Messages"
        ),
        len(source_lines),
    )
    uses_escaped_control_lines = (
        first_meaningful == "# Conversation Export"
        and _MARKDOWN_EXPORT_VERSION_LINE
        in {line.strip() for line in source_lines[:messages_heading_index]}
    )
    for line_index, raw in enumerate(source_lines):
        line = raw.rstrip()
        next_line = (
            source_lines[line_index + 1].strip()
            if line_index + 1 < len(source_lines)
            else ""
        )
        if in_thoughts:
            # Canonical exports terminate this derived metadata block with a blank
            # line before the next real message header.
            if line.strip() == "":
                in_thoughts = False
            continue
        if in_tools:
            if line.strip() == "":
                in_tools = False
            continue
        header_match = _HEADER_RE.match(line)
        header_role = (
            _normalize_import_role(header_match.group("role")) if header_match else None
        )
        normalized_line = line.strip().lower()
        structural_header = bool(uses_escaped_control_lines and header_role is not None)
        structural_metadata = bool(
            uses_escaped_control_lines
            and has_float_export_banner
            and current is not None
            and (
                (
                    normalized_line == "#### thoughts"
                    and _THOUGHT_EXPORT_RE.match(next_line)
                )
                or (
                    normalized_line == "#### tools" and _TOOL_EXPORT_RE.match(next_line)
                )
            )
        )
        fence_match = _FENCE_RE.match(line)
        if fence_marker is not None:
            if header_role is not None:
                recognized_headers_in_active_fence += 1
            if structural_header or structural_metadata:
                fence_marker = None
                recognized_headers_in_active_fence = 0
            else:
                if current is not None:
                    buffer.append(line)
                elif line.strip():
                    unparsed_preamble_lines.append(line)
                if fence_match:
                    marker = fence_match.group("marker")
                    if marker[0] == fence_marker[0] and len(marker) >= len(
                        fence_marker
                    ):
                        fence_marker = None
                        recognized_headers_in_active_fence = 0
                continue
        if fence_match:
            fence_marker = fence_match.group("marker")
            recognized_headers_in_active_fence = 0
            if current is not None:
                buffer.append(line)
            elif line.strip():
                unparsed_preamble_lines.append(line)
            continue
        if (
            not recognized_roles
            and current is None
            and line.strip().lstrip("\ufeff") == "# Conversation Export"
        ):
            has_float_export_banner = True
        if header_match:
            raw_role = str(header_match.group("role") or "").strip().lower()
            role = header_role
            if role is None:
                if in_thoughts or in_tools:
                    continue
                if raw_role:
                    unknown_roles.append(raw_role)
                if current is not None:
                    buffer.append(line)
                elif line.strip():
                    unparsed_preamble_lines.append(line)
                continue
            _flush()
            in_thoughts = False
            in_tools = False
            recognized_roles.append(role)
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
            continue
        if (
            has_float_export_banner
            and current is not None
            and (uses_escaped_control_lines or current.get("role") == "ai")
            and normalized_line == "#### thoughts"
            and _THOUGHT_EXPORT_RE.match(next_line)
        ):
            in_thoughts = True
            continue
        if (
            has_float_export_banner
            and current is not None
            and (uses_escaped_control_lines or current.get("role") == "ai")
            and normalized_line == "#### tools"
            and _TOOL_EXPORT_RE.match(next_line)
        ):
            in_tools = True
            continue
        if current is not None:
            buffer.append(line)
        elif line.strip():
            unparsed_preamble_lines.append(line)
    _flush()
    legacy_unterminated_fence = bool(
        has_float_export_banner
        and not uses_escaped_control_lines
        and fence_marker is not None
        and recognized_headers_in_active_fence > 0
    )
    has_unparsed_preamble = _has_unparsed_markdown_preamble(
        unparsed_preamble_lines,
        has_float_export_banner=has_float_export_banner,
    )
    return {
        "messages": messages,
        "recognized_roles": recognized_roles,
        "unknown_roles": unknown_roles,
        "empty_message_count": empty_message_count,
        "has_float_export_banner": has_float_export_banner,
        "has_unparsed_preamble": has_unparsed_preamble,
        "legacy_unterminated_fence": legacy_unterminated_fence,
    }


def classify_conversation_markdown(text: str) -> Dict[str, Any]:
    """Classify Markdown/text before choosing a durable import destination."""

    scan = _scan_conversation_markdown(text)
    messages = scan["messages"]
    recognized_roles = scan["recognized_roles"]
    unknown_roles = scan["unknown_roles"]
    empty_message_count = int(scan["empty_message_count"] or 0)
    has_float_export_banner = bool(scan["has_float_export_banner"])
    has_unparsed_preamble = bool(scan["has_unparsed_preamble"])
    legacy_unterminated_fence = bool(scan["legacy_unterminated_fence"])
    role_counts: Dict[str, int] = {}
    for role in recognized_roles:
        role_counts[role] = role_counts.get(role, 0) + 1

    warnings: List[str] = []
    if not recognized_roles:
        classification = "document"
        suggested_action = "document"
        allowed_actions = ["document"]
        if unknown_roles:
            warnings.append(
                "Bracketed headings were found, but none use a recognized "
                "conversation role."
            )
    elif (
        (len(recognized_roles) == 1 and not has_float_export_banner)
        or bool(unknown_roles)
        or empty_message_count > 0
        or has_unparsed_preamble
        or legacy_unterminated_fence
    ):
        classification = "ambiguous"
        suggested_action = "review"
        allowed_actions = ["conversation", "document"]
        if len(recognized_roles) == 1:
            warnings.append("Only one recognized conversation role heading was found.")
        if unknown_roles:
            unique_unknown = ", ".join(sorted(set(unknown_roles)))
            warnings.append(
                "Recognized conversation roles are mixed with unsupported bracketed "
                f"headings: {unique_unknown}."
            )
        if empty_message_count:
            warnings.append(
                f"{empty_message_count} recognized message section(s) have no content."
            )
        if has_unparsed_preamble:
            warnings.append(
                "Meaningful text outside recognized message sections would be omitted."
            )
        if legacy_unterminated_fence:
            warnings.append(
                "An unterminated code fence contains recognized conversation role "
                "headings, so legacy message boundaries cannot be recovered safely."
            )
    else:
        classification = "conversation"
        suggested_action = "conversation"
        allowed_actions = ["conversation", "document"]

    return {
        "classification": classification,
        "message_count": len(messages),
        "role_counts": role_counts,
        "preview": _markdown_import_preview(text),
        "warnings": warnings,
        "suggested_action": suggested_action,
        "allowed_actions": allowed_actions,
        "recognized_header_count": len(recognized_roles),
        "unknown_header_count": len(unknown_roles),
        "canonical_float_export": has_float_export_banner,
        "unparsed_content_present": has_unparsed_preamble,
    }


def import_conversation_markdown(text: str) -> List[Dict[str, Any]]:
    return _scan_conversation_markdown(text)["messages"]


_LEGACY_TEXT_TITLE_RE = re.compile(r"^.+\s+\(.+\)$")


def _is_float_text_export_preamble(
    lines: List[str], *, has_text_export_banner: bool
) -> bool:
    meaningful = [line.strip().lstrip("\ufeff") for line in lines if line.strip()]
    if has_text_export_banner:
        if meaningful[:1] != [_TEXT_EXPORT_BANNER]:
            return False
        meaningful = meaningful[1:]
        if meaningful[:1] == [_TEXT_EXPORT_VERSION_LINE]:
            meaningful = meaningful[1:]
        if not meaningful:
            return True
        return len(meaningful) == 1 or (
            len(meaningful) == 2 and meaningful[1].startswith("created_at: ")
        )
    if not meaningful:
        return False
    if not _LEGACY_TEXT_TITLE_RE.match(meaningful[0]):
        return False
    return len(meaningful) == 1 or (
        len(meaningful) == 2 and meaningful[1].startswith("created_at: ")
    )


def _scan_conversation_text(text: str) -> Dict[str, Any]:
    messages: List[Dict[str, Any]] = []
    recognized_roles: List[str] = []
    unknown_roles: List[str] = []
    preamble_lines: List[str] = []
    current: Optional[Dict[str, Any]] = None
    buffer: List[str] = []
    empty_message_count = 0
    fence_marker: Optional[str] = None
    recognized_headers_in_active_fence = 0
    metadata_candidates_in_active_fence = 0

    source_text = str(text or "").lstrip("\ufeff")
    source_lines = source_text.splitlines()
    first_meaningful = next((line.strip() for line in source_lines if line.strip()), "")
    has_text_export_banner = first_meaningful == _TEXT_EXPORT_BANNER
    meaningful_lines = [line.strip() for line in source_lines if line.strip()]
    uses_escaped_control_lines = has_text_export_banner and meaningful_lines[:2] == [
        _TEXT_EXPORT_BANNER,
        _TEXT_EXPORT_VERSION_LINE,
    ]
    has_export_envelope = has_text_export_banner

    def _flush() -> None:
        nonlocal current, buffer, empty_message_count
        if current is None:
            buffer = []
            return
        content_lines = (
            [_unescape_text_message_line(line) for line in buffer]
            if uses_escaped_control_lines
            else buffer
        )
        content = _join_lines(content_lines)
        if content:
            current["text"] = content
        else:
            empty_message_count += 1
        messages.append(current)
        current = None
        buffer = []

    for line in source_lines:
        raw_line = line.rstrip()
        header_match = _TEXT_HEADER_RE.match(raw_line)
        header_role = (
            _normalize_import_role(header_match.group("role")) if header_match else None
        )
        structural_header = bool(
            header_role is not None and current is not None and has_export_envelope
        )
        exporter_metadata_line = bool(
            has_export_envelope
            and current is not None
            and (
                _THOUGHT_EXPORT_RE.match(raw_line.strip())
                or _TEXT_TOOL_EXPORT_RE.match(raw_line.strip())
            )
        )
        fence_match = _FENCE_RE.match(raw_line)
        if fence_marker is not None:
            if header_role is not None:
                recognized_headers_in_active_fence += 1
            if exporter_metadata_line:
                metadata_candidates_in_active_fence += 1
            if uses_escaped_control_lines and (
                structural_header or exporter_metadata_line
            ):
                fence_marker = None
                recognized_headers_in_active_fence = 0
                metadata_candidates_in_active_fence = 0
            else:
                if current is not None:
                    buffer.append(raw_line)
                elif raw_line.strip():
                    preamble_lines.append(raw_line)
                if fence_match:
                    marker = fence_match.group("marker")
                    if marker[0] == fence_marker[0] and len(marker) >= len(
                        fence_marker
                    ):
                        fence_marker = None
                        recognized_headers_in_active_fence = 0
                        metadata_candidates_in_active_fence = 0
                continue
        if fence_match:
            fence_marker = fence_match.group("marker")
            recognized_headers_in_active_fence = 0
            metadata_candidates_in_active_fence = 0
            if current is not None:
                buffer.append(raw_line)
            elif raw_line.strip():
                preamble_lines.append(raw_line)
            continue

        if (
            has_text_export_banner
            and current is None
            and not recognized_roles
            and raw_line.strip() == _TEXT_EXPORT_BANNER
        ):
            preamble_lines.append(raw_line)
            continue

        if header_match:
            raw_role = str(header_match.group("role") or "").strip().lower()
            role = header_role
            if role is None:
                if raw_role:
                    unknown_roles.append(raw_role)
                if current is not None:
                    buffer.append(raw_line)
                elif raw_line.strip():
                    preamble_lines.append(raw_line)
                continue
            if not recognized_roles:
                has_export_envelope = has_text_export_banner or (
                    _is_float_text_export_preamble(
                        preamble_lines,
                        has_text_export_banner=has_text_export_banner,
                    )
                )
            _flush()
            recognized_roles.append(role)
            current = {"role": role}
            inline_text = header_match.group("text")
            if inline_text:
                buffer.append(inline_text)
            continue

        if exporter_metadata_line:
            continue
        if current is not None:
            buffer.append(raw_line)
        elif raw_line.strip():
            preamble_lines.append(raw_line)
    _flush()

    has_export_preamble = _is_float_text_export_preamble(
        preamble_lines,
        has_text_export_banner=has_text_export_banner,
    )
    legacy_unterminated_fence = bool(
        (has_export_envelope or has_export_preamble)
        and not uses_escaped_control_lines
        and fence_marker is not None
        and (
            recognized_headers_in_active_fence > 0
            or metadata_candidates_in_active_fence > 0
        )
    )
    has_unparsed_preamble = bool(preamble_lines) and not has_export_preamble
    return {
        "messages": messages,
        "recognized_roles": recognized_roles,
        "unknown_roles": unknown_roles,
        "empty_message_count": empty_message_count,
        "has_export_envelope": has_export_envelope or has_export_preamble,
        "has_unparsed_preamble": has_unparsed_preamble,
        "legacy_unterminated_fence": legacy_unterminated_fence,
    }


def classify_conversation_text(text: str) -> Dict[str, Any]:
    """Classify a plain-text Float export without treating ordinary notes as chat."""

    scan = _scan_conversation_text(text)
    messages = scan["messages"]
    recognized_roles = scan["recognized_roles"]
    unknown_roles = scan["unknown_roles"]
    empty_message_count = int(scan["empty_message_count"] or 0)
    has_export_envelope = bool(scan["has_export_envelope"])
    has_unparsed_preamble = bool(scan["has_unparsed_preamble"])
    legacy_unterminated_fence = bool(scan["legacy_unterminated_fence"])
    role_counts: Dict[str, int] = {}
    for role in recognized_roles:
        role_counts[role] = role_counts.get(role, 0) + 1

    warnings: List[str] = []
    if not recognized_roles:
        classification = "document"
        suggested_action = "document"
        allowed_actions = ["document"]
        if unknown_roles:
            warnings.append(
                "Bracketed lines were found, but none use a recognized conversation role."
            )
    elif (
        (len(recognized_roles) == 1 and not has_export_envelope)
        or bool(unknown_roles)
        or empty_message_count > 0
        or has_unparsed_preamble
        or legacy_unterminated_fence
    ):
        classification = "ambiguous"
        suggested_action = "review"
        allowed_actions = ["conversation", "document"]
        if len(recognized_roles) == 1:
            warnings.append("Only one recognized conversation role line was found.")
        if unknown_roles:
            unique_unknown = ", ".join(sorted(set(unknown_roles)))
            warnings.append(
                "Recognized conversation roles are mixed with unsupported bracketed "
                f"lines: {unique_unknown}."
            )
        if empty_message_count:
            warnings.append(
                f"{empty_message_count} recognized message section(s) have no content."
            )
        if has_unparsed_preamble:
            warnings.append(
                "Meaningful text outside recognized message sections would be omitted."
            )
        if legacy_unterminated_fence:
            warnings.append(
                "An unterminated code fence contains conversation-role or "
                "exporter-metadata-shaped lines, so legacy message boundaries "
                "cannot be recovered safely."
            )
    else:
        classification = "conversation"
        suggested_action = "conversation"
        allowed_actions = ["conversation", "document"]

    return {
        "classification": classification,
        "message_count": len(messages),
        "role_counts": role_counts,
        "preview": _markdown_import_preview(text),
        "warnings": warnings,
        "suggested_action": suggested_action,
        "allowed_actions": allowed_actions,
        "recognized_header_count": len(recognized_roles),
        "unknown_header_count": len(unknown_roles),
        "canonical_float_export": has_export_envelope,
        "unparsed_content_present": has_unparsed_preamble,
    }


def import_conversation_text(text: str) -> List[Dict[str, Any]]:
    return _scan_conversation_text(text)["messages"]
