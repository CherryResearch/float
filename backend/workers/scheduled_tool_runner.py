import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, Optional

from app.utils import calendar_store
from app.utils.security import generate_signature, sanitize_args
from fastapi import FastAPI

logger = logging.getLogger(__name__)

_RUN_LOCK = asyncio.Lock()
_SAFE_CONVERSATION_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_ACTION_KIND_ALIASES = {
    "continue_prompt": "prompt",
    "followup_prompt": "prompt",
    "follow_up_prompt": "prompt",
}
_CONVERSATION_MODE_ALIASES = {
    "current_chat": "inline",
    "current_thread": "inline",
    "inline": "inline",
    "inline_chat": "inline",
    "same_chat": "inline",
    "same_thread": "inline",
    "new": "new_chat",
    "new_chat": "new_chat",
    "new_thread": "new_chat",
    "separate_chat": "new_chat",
    "separate_thread": "new_chat",
    "task_chat": "new_chat",
}


def _coerce_epoch_seconds(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and float(value) > 0:
        return float(value)
    if isinstance(value, str) and value.strip():
        # Support ISO timestamps that may come from external calendar imports.
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp()
        except Exception:
            return None
    if isinstance(value, dict):
        if value.get("dateTime"):
            return _coerce_epoch_seconds(value.get("dateTime"))
        if value.get("date"):
            return _coerce_epoch_seconds(f"{value.get('date')}T00:00:00+00:00")
    return None


def _event_start_time(event: Dict[str, Any]) -> Optional[float]:
    return (
        _coerce_epoch_seconds(event.get("start_time"))
        or _coerce_epoch_seconds(event.get("start"))
        or _coerce_epoch_seconds((event.get("start") or {}).get("dateTime"))
        or _coerce_epoch_seconds((event.get("start") or {}).get("date"))
    )


def _normalize_conversation_mode(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        raw = str(value).strip().lower()
    except Exception:
        return None
    if not raw:
        return None
    key = raw.replace("-", "_").replace(" ", "_")
    return _CONVERSATION_MODE_ALIASES.get(key)


def _normalize_action(action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    normalized = dict(action)
    try:
        raw_kind = str(
            normalized.get("kind") or normalized.get("type") or ""
        ).strip().lower()
    except Exception:
        raw_kind = ""
    kind = _ACTION_KIND_ALIASES.get(
        raw_kind.replace("-", "_").replace(" ", "_"),
        raw_kind.replace("-", "_").replace(" ", "_"),
    )
    if not kind:
        if _normalize_prompt(normalized.get("prompt")) and not normalized.get("name"):
            kind = "prompt"
        elif normalized.get("name"):
            kind = "tool"
    if kind not in {"tool", "prompt"}:
        return None
    normalized["kind"] = kind
    normalized.pop("type", None)
    if kind == "prompt" and "prompt" not in normalized:
        prompt = _normalize_prompt(
            normalized.get("text") or normalized.get("message")
        )
        if prompt:
            normalized["prompt"] = prompt
    mode = _normalize_conversation_mode(
        normalized.get("conversation_mode")
        or normalized.get("run_target")
        or normalized.get("target")
    )
    if not mode:
        mode = "inline" if normalized.get("session_id") else "new_chat"
    normalized["conversation_mode"] = mode
    return normalized


def _resolve_action_conversation(
    action: Dict[str, Any],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    mode = _normalize_conversation_mode(action.get("conversation_mode"))
    if mode == "new_chat":
        return None, None, None
    session_id = action.get("session_id")
    message_id = action.get("message_id")
    chain_id = action.get("chain_id") or message_id or session_id
    return session_id, message_id, chain_id


def _iter_actions(event: Dict[str, Any]) -> list[Dict[str, Any]]:
    actions = event.get("actions")
    if not isinstance(actions, list):
        return []
    out: list[Dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        normalized = _normalize_action(action)
        if normalized is None:
            continue
        action.clear()
        action.update(normalized)
        out.append(action)
    return out


def _fallback_tool_from_description(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Try to recover a scheduled tool payload from the legacy description field."""
    desc = event.get("description")
    if not isinstance(desc, str) or not desc.strip():
        return None
    try:
        parsed = json.loads(desc)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    tool = parsed.get("tool")
    args = parsed.get("args")
    if not isinstance(tool, str) or not tool.strip():
        return None
    if not isinstance(args, dict):
        args = {}
    return {"kind": "tool", "name": tool.strip(), "args": args, "status": "scheduled"}


def _normalize_prompt(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        try:
            value = str(value)
        except Exception:
            return None
    text = value.strip()
    return text or None


def _ensure_task_conversation(
    *,
    session_id: Optional[str],
    event: Dict[str, Any],
    event_id: str,
) -> str:
    """Ensure prompt-only scheduled work is readable as its own conversation thread."""
    if session_id:
        return session_id
    raw_title = event.get("title") or event.get("summary") or ""
    title = str(raw_title).strip() if raw_title is not None else ""
    safe_event_id = _SAFE_CONVERSATION_NAME_RE.sub("-", str(event_id)).strip("-")
    conv_name = (
        f"task-{safe_event_id}" if safe_event_id else f"task-{int(time.time() * 1000)}"
    )
    try:
        from app.utils import conversation_store

        if title:
            conversation_store.set_display_name(
                conv_name, title, auto_generated=True, manual=False
            )
    except Exception:
        pass
    return conv_name


def _mark_event_prompted(event: Dict[str, Any]) -> None:
    raw = event.get("status")
    try:
        status = str(raw or "").strip().lower()
    except Exception:
        status = ""
    if status in {"acknowledged", "skipped"}:
        return
    event["status"] = "prompted"


async def _persist_event(app: FastAPI, event_id: str, payload: Dict[str, Any]) -> None:
    """Persist + re-index the calendar event (best-effort)."""
    try:
        from app import routes as routes_module

        routes_module._persist_calendar_event(event_id, payload)  # type: ignore[attr-defined]
    except Exception:
        try:
            calendar_store.save_event(event_id, payload)
        except Exception:
            pass


async def _publish_tool_status(
    app: FastAPI,
    *,
    tool_id: str,
    name: str,
    args: Dict[str, Any],
    status: str,
    result: Any = None,
    chain_id: Optional[str] = None,
    message_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    try:
        from app import routes as routes_module

        await routes_module.publish_console_event(  # type: ignore[attr-defined]
            app,
            {
                "type": "tool",
                "id": tool_id,
                "name": name,
                "args": args,
                "result": result,
                "chain_id": chain_id,
                "message_id": message_id,
                "status": status,
                "session_id": session_id,
            },
            default_agent=chain_id or session_id,
        )
    except Exception:
        pass


async def _publish_content(
    app: FastAPI,
    *,
    content: str,
    chain_id: Optional[str] = None,
    message_id: Optional[str] = None,
    session_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        from app import routes as routes_module

        payload: Dict[str, Any] = {
            "type": "content",
            "content": content,
            "chain_id": chain_id,
            "message_id": message_id,
            "session_id": session_id,
        }
        if metadata:
            payload["metadata"] = metadata
        await routes_module.publish_console_event(  # type: ignore[attr-defined]
            app,
            payload,
            default_agent=chain_id or session_id,
        )
    except Exception:
        pass


def _append_tool_to_conversation(
    *,
    session_id: Optional[str],
    message_id: Optional[str],
    request_id: str,
    name: str,
    args: Dict[str, Any],
    status: str,
    result: Any,
) -> None:
    if not session_id or not message_id:
        return
    try:
        from app import routes as routes_module

        routes_module._append_tool_event_to_conversation(  # type: ignore[attr-defined]
            session_id,
            message_id,
            name,
            args,
            result,
            status=status,
            request_id=request_id,
        )
    except Exception:
        pass


def _append_conversation_entry(
    *,
    session_id: Optional[str],
    entry: Dict[str, Any],
) -> None:
    if not session_id:
        return
    try:
        from app import routes as routes_module

        routes_module._append_conversation_entry(  # type: ignore[attr-defined]
            session_id, entry
        )
    except Exception:
        pass


def _update_conversation_entry(
    *,
    session_id: Optional[str],
    message_id: str,
    updates: Dict[str, Any],
) -> None:
    if not session_id:
        return
    try:
        from app import routes as routes_module

        routes_module._update_conversation_entry(  # type: ignore[attr-defined]
            session_id, message_id, updates
        )
    except Exception:
        pass


async def _invoke_tool(
    app: FastAPI,
    *,
    name: str,
    args: Dict[str, Any],
    user: str,
    action_context: Optional[Dict[str, Any]] = None,
) -> Any:
    manager = getattr(app.state, "memory_manager", None)
    if manager is None:
        raise RuntimeError("memory manager not available")
    signature = generate_signature(user, name, args)
    return await asyncio.to_thread(
        manager.invoke_tool,
        name,
        user=user,
        signature=signature,
        _action_context=action_context,
        **args,
    )


async def _run_prompt_followup(
    app: FastAPI,
    *,
    session_id: Optional[str],
    chain_id: Optional[str],
    prompt: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_result: Any,
    event_id: str,
    event: Dict[str, Any],
    action_id: str,
) -> None:
    session_id = _ensure_task_conversation(
        session_id=session_id, event=event, event_id=event_id
    )
    chain_id = chain_id or session_id

    try:
        from app import routes as routes_module
        from app.services import ModelContext as ServiceContext
        from app.utils import conversation_store
    except Exception:
        return

    now_ts = time.time()
    followup_id = f"sched-{event_id}-{action_id}-{int(now_ts * 1000)}"
    _append_conversation_entry(
        session_id=session_id,
        entry={
            "id": f"{followup_id}:user",
            "role": "user",
            "text": prompt,
            "timestamp": now_ts,
            "metadata": {
                "scheduled": True,
                "event_id": event_id,
                "action_id": action_id,
                "tool": tool_name,
            },
        },
    )
    _append_conversation_entry(
        session_id=session_id,
        entry={
            "id": followup_id,
            "role": "ai",
            "text": "",
            "thought": "",
            "metadata": {"status": "pending", "scheduled": True},
            "timestamp": now_ts,
        },
    )

    context = routes_module.llm_service.get_context(session_id)
    if not getattr(context, "messages", None):
        try:
            history = conversation_store.load_conversation(session_id)
            for entry in history:
                role = entry.get("role")
                text = entry.get("text") or entry.get("content")
                if not role or not text:
                    continue
                meta = entry.get("metadata") or {}
                context.add_message(role, text, metadata=meta)
        except Exception:
            pass

    generation_ctx = ServiceContext(
        system_prompt=context.system_prompt,
        messages=list(context.messages),
        tools=list(context.tools),
        metadata=dict(context.metadata),
    )
    try:
        args_text = json.dumps(tool_args, ensure_ascii=False)
    except Exception:
        args_text = str(tool_args)
    try:
        result_text = json.dumps(tool_result, ensure_ascii=False)
    except Exception:
        result_text = str(tool_result)
    generation_ctx.add_message(
        "system",
        "Scheduled tool result:\n"
        f"- {tool_name} args={args_text} result={result_text}",
        metadata={"ephemeral": True, "tool_results": True, "scheduled": True},
    )
    generation_ctx.add_message(
        "user",
        prompt,
        metadata={"scheduled": True, "event_id": event_id, "action_id": action_id},
    )

    response_format = None
    try:
        if app.state.config.get("harmony_format"):
            response_format = "harmony"
    except Exception:
        response_format = None

    try:
        response = await asyncio.to_thread(
            routes_module.llm_service.generate,
            [],
            session_id=session_id,
            response_format=response_format,
            context=generation_ctx,
        )
    except Exception as exc:
        await _publish_content(
            app,
            content=f"(scheduled prompt failed) {exc}",
            chain_id=chain_id,
            session_id=session_id,
            metadata={
                "scheduled": True,
                "event_id": event_id,
                "action_id": action_id,
                "tool": tool_name,
                "error": True,
            },
        )
        _update_conversation_entry(
            session_id=session_id,
            message_id=followup_id,
            updates={"text": "", "metadata": {"status": "error", "error": str(exc)}},
        )
        return

    text = str(response.get("text") or "")
    thought = response.get("thought")
    updates: Dict[str, Any] = {"text": text, "metadata": {"status": "complete"}}
    if isinstance(thought, str):
        updates["thought"] = thought
    _update_conversation_entry(
        session_id=session_id, message_id=followup_id, updates=updates
    )
    await _publish_content(
        app,
        content=text,
        chain_id=chain_id,
        message_id=followup_id,
        session_id=session_id,
        metadata={
            "scheduled": True,
            "event_id": event_id,
            "action_id": action_id,
            "tool": tool_name,
        },
    )


async def _run_prompt_action(
    app: FastAPI,
    *,
    event: Dict[str, Any],
    session_id: Optional[str],
    chain_id: Optional[str],
    prompt: str,
    event_id: str,
    action_id: str,
) -> Optional[str]:
    """Execute a scheduled prompt-only action by asking the model."""
    session_id = _ensure_task_conversation(
        session_id=session_id, event=event, event_id=event_id
    )
    chain_id = chain_id or session_id

    try:
        from app import routes as routes_module
        from app.services import ModelContext as ServiceContext
        from app.utils import conversation_store
    except Exception:
        return None

    now_ts = time.time()
    followup_id = f"sched-{event_id}-{action_id}-{int(now_ts * 1000)}"
    _append_conversation_entry(
        session_id=session_id,
        entry={
            "id": f"{followup_id}:user",
            "role": "user",
            "text": prompt,
            "timestamp": now_ts,
            "metadata": {
                "scheduled": True,
                "event_id": event_id,
                "action_id": action_id,
                "prompt_action": True,
            },
        },
    )
    _append_conversation_entry(
        session_id=session_id,
        entry={
            "id": followup_id,
            "role": "ai",
            "text": "",
            "thought": "",
            "metadata": {"status": "pending", "scheduled": True, "prompt_action": True},
            "timestamp": now_ts,
        },
    )

    context = routes_module.llm_service.get_context(session_id)
    if not getattr(context, "messages", None):
        try:
            history = conversation_store.load_conversation(session_id)
            for entry in history:
                role = entry.get("role")
                text = entry.get("text") or entry.get("content")
                if not role or not text:
                    continue
                meta = entry.get("metadata") or {}
                context.add_message(role, text, metadata=meta)
        except Exception:
            pass

    generation_ctx = ServiceContext(
        system_prompt=context.system_prompt,
        messages=list(context.messages),
        tools=list(context.tools),
        metadata=dict(context.metadata),
    )
    generation_ctx.add_message(
        "user",
        prompt,
        metadata={
            "scheduled": True,
            "event_id": event_id,
            "action_id": action_id,
            "prompt_action": True,
        },
    )

    response_format = None
    try:
        if app.state.config.get("harmony_format"):
            response_format = "harmony"
    except Exception:
        response_format = None

    try:
        response = await asyncio.to_thread(
            routes_module.llm_service.generate,
            [],
            session_id=session_id,
            response_format=response_format,
            context=generation_ctx,
        )
    except Exception as exc:
        await _publish_content(
            app,
            content=f"(scheduled prompt failed) {exc}",
            chain_id=chain_id,
            session_id=session_id,
            metadata={
                "scheduled": True,
                "event_id": event_id,
                "action_id": action_id,
                "prompt_action": True,
                "error": True,
            },
        )
        _update_conversation_entry(
            session_id=session_id,
            message_id=followup_id,
            updates={"text": "", "metadata": {"status": "error", "error": str(exc)}},
        )
        return None

    text = str(response.get("text") or "")
    thought = response.get("thought")
    updates: Dict[str, Any] = {"text": text, "metadata": {"status": "complete"}}
    if isinstance(thought, str):
        updates["thought"] = thought
    _update_conversation_entry(
        session_id=session_id, message_id=followup_id, updates=updates
    )
    await _publish_content(
        app,
        content=text,
        chain_id=chain_id,
        message_id=followup_id,
        session_id=session_id,
        metadata={
            "scheduled": True,
            "event_id": event_id,
            "action_id": action_id,
            "prompt_action": True,
        },
    )
    return text


async def _run_tool_action(
    app: FastAPI,
    *,
    event_id: str,
    event: Dict[str, Any],
    action: Dict[str, Any],
    action_id: str,
    force: bool,
) -> Dict[str, Any]:
    now = time.time()
    start = _event_start_time(event)
    if start is not None and not force and start > now:
        return {
            "status": "not_due",
            "event_id": event_id,
            "action_id": action_id,
            "start_time": start,
        }

    status = str(action.get("status") or "").lower()
    executed_at = _coerce_epoch_seconds(action.get("executed_at"))
    if executed_at and not force:
        return {
            "status": "already_executed",
            "event_id": event_id,
            "action_id": action_id,
            "executed_at": executed_at,
        }
    if status in {"running"} and not force:
        return {
            "status": "already_running",
            "event_id": event_id,
            "action_id": action_id,
        }

    name = action.get("name")
    user = str(action.get("user") or "scheduler")
    session_id, message_id, chain_id = _resolve_action_conversation(action)
    prompt = _normalize_prompt(action.get("prompt"))
    if prompt and not session_id:
        session_id = _ensure_task_conversation(
            session_id=session_id, event=event, event_id=event_id
        )
        chain_id = chain_id or session_id
        message_id = message_id or chain_id
        action["session_id"] = session_id
        action["chain_id"] = chain_id
        action["message_id"] = message_id

    if not isinstance(name, str) or not name.strip():
        detail = "scheduled action missing tool name"
        raw_args = action.get("args")
        args = sanitize_args(raw_args if isinstance(raw_args, dict) else {})
        action["status"] = "error"
        action["error"] = detail
        action["executed_at"] = time.time()
        _mark_event_prompted(event)
        await _persist_event(app, event_id, event)
        await _publish_tool_status(
            app,
            tool_id=action_id,
            name="tool",
            args=args,
            status="error",
            result=detail,
            chain_id=chain_id,
            message_id=message_id,
            session_id=session_id,
        )
        _append_tool_to_conversation(
            session_id=session_id,
            message_id=message_id,
            request_id=action_id,
            name="tool",
            args=args,
            status="error",
            result=detail,
        )
        return {
            "status": "error",
            "event_id": event_id,
            "action_id": action_id,
            "error": detail,
        }

    raw_args = action.get("args")
    raw_args = raw_args if isinstance(raw_args, dict) else {}
    try:
        from app.utils.tool_args import normalize_and_sanitize_tool_args

        _, args = normalize_and_sanitize_tool_args(name.strip(), raw_args)
    except ValueError as exc:
        detail = str(exc)
        action["status"] = "error"
        action["error"] = detail
        action["executed_at"] = time.time()
        _mark_event_prompted(event)
        await _persist_event(app, event_id, event)
        await _publish_tool_status(
            app,
            tool_id=action_id,
            name=name.strip(),
            args=sanitize_args(raw_args),
            status="error",
            result=detail,
            chain_id=chain_id,
            message_id=message_id,
            session_id=session_id,
        )
        _append_tool_to_conversation(
            session_id=session_id,
            message_id=message_id,
            request_id=action_id,
            name=name.strip(),
            args=sanitize_args(raw_args),
            status="error",
            result=detail,
        )
        return {
            "status": "error",
            "event_id": event_id,
            "action_id": action_id,
            "error": detail,
        }
    except Exception:
        args = sanitize_args(raw_args)

    action["status"] = "running"
    action["started_at"] = now
    event["status"] = "running"
    await _persist_event(app, event_id, event)
    await _publish_tool_status(
        app,
        tool_id=action_id,
        name=name,
        args=args,
        status="running",
        chain_id=chain_id,
        message_id=message_id,
        session_id=session_id,
    )

    try:
        result = await _invoke_tool(
            app,
            name=name,
            args=args,
            user=user,
            action_context={
                "conversation_id": session_id,
                "session_id": session_id,
                "message_id": message_id,
                "chain_id": chain_id,
                "response_id": chain_id or message_id,
                "request_id": action_id,
                "agent_id": chain_id or message_id or session_id or "scheduler",
                "agent_label": "scheduled action",
            },
        )
    except Exception as exc:
        action["status"] = "error"
        action["error"] = str(exc)
        action["executed_at"] = time.time()
        _mark_event_prompted(event)
        await _persist_event(app, event_id, event)
        await _publish_tool_status(
            app,
            tool_id=action_id,
            name=name,
            args=args,
            status="error",
            result=str(exc),
            chain_id=chain_id,
            message_id=message_id,
            session_id=session_id,
        )
        _append_tool_to_conversation(
            session_id=session_id,
            message_id=message_id,
            request_id=action_id,
            name=name,
            args=args,
            status="error",
            result=str(exc),
        )
        return {
            "status": "error",
            "event_id": event_id,
            "action_id": action_id,
            "error": str(exc),
        }

    action["status"] = "invoked"
    action["result"] = result
    action["executed_at"] = time.time()
    _mark_event_prompted(event)
    await _persist_event(app, event_id, event)
    await _publish_tool_status(
        app,
        tool_id=action_id,
        name=name,
        args=args,
        status="invoked",
        result=result,
        chain_id=chain_id,
        message_id=message_id,
        session_id=session_id,
    )
    _append_tool_to_conversation(
        session_id=session_id,
        message_id=message_id,
        request_id=action_id,
        name=name,
        args=args,
        status="invoked",
        result=result,
    )

    if prompt:
        await _run_prompt_followup(
            app,
            session_id=session_id,
            chain_id=chain_id,
            prompt=prompt,
            tool_name=name,
            tool_args=args,
            tool_result=result,
            event_id=event_id,
            event=event,
            action_id=action_id,
        )
    return {
        "status": "invoked",
        "event_id": event_id,
        "action_id": action_id,
        "result": result,
    }


async def run_scheduled_tools_for_event(
    app: FastAPI,
    event_id: str,
    *,
    action_id: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Run scheduled actions (tools + prompts) attached to a calendar event."""
    async with _RUN_LOCK:
        event = calendar_store.load_event(event_id)
        if not isinstance(event, dict) or not event:
            return {"status": "not_found", "event_id": event_id}
        actions = _iter_actions(event)
        if not actions:
            fallback = _fallback_tool_from_description(event)
            if fallback:
                actions = [fallback]
                event["actions"] = [fallback]
                await _persist_event(app, event_id, event)
            else:
                return {"status": "no_actions", "event_id": event_id}

        results: list[Dict[str, Any]] = []
        ran_any = False
        had_error = False
        for idx, action in enumerate(actions):
            resolved_id = (
                action.get("request_id")
                or action.get("id")
                or (f"{event_id}:tool:{idx}")
            )
            resolved_id = str(resolved_id)
            if action_id and resolved_id != str(action_id):
                continue
            kind = str(action.get("kind") or action.get("type") or "").lower()
            if kind == "prompt":
                now = time.time()
                start = _event_start_time(event)
                if start is not None and not force and start > now:
                    result = {
                        "status": "not_due",
                        "event_id": event_id,
                        "action_id": resolved_id,
                        "start_time": start,
                    }
                else:
                    status = str(action.get("status") or "").lower()
                    executed_at = _coerce_epoch_seconds(action.get("executed_at"))
                    if executed_at and not force:
                        result = {
                            "status": "already_executed",
                            "event_id": event_id,
                            "action_id": resolved_id,
                            "executed_at": executed_at,
                        }
                    elif status in {"running"} and not force:
                        result = {
                            "status": "already_running",
                            "event_id": event_id,
                            "action_id": resolved_id,
                        }
                    else:
                        prompt = _normalize_prompt(action.get("prompt"))
                        if not prompt:
                            action["status"] = "error"
                            action["error"] = "scheduled action missing prompt"
                            action["executed_at"] = time.time()
                            _mark_event_prompted(event)
                            await _persist_event(app, event_id, event)
                            result = {
                                "status": "error",
                                "event_id": event_id,
                                "action_id": resolved_id,
                                "error": "scheduled action missing prompt",
                            }
                        else:
                            action["status"] = "running"
                            action["started_at"] = time.time()
                            event["status"] = "running"
                            await _persist_event(app, event_id, event)
                            session_id, message_id, chain_id = _resolve_action_conversation(
                                action
                            )
                            session_id = _ensure_task_conversation(
                                session_id=session_id, event=event, event_id=event_id
                            )
                            chain_id = chain_id or session_id
                            action["session_id"] = session_id
                            action["chain_id"] = chain_id
                            action["message_id"] = message_id or chain_id
                            try:
                                response_text = await _run_prompt_action(
                                    app,
                                    event=event,
                                    session_id=session_id,
                                    chain_id=chain_id,
                                    prompt=prompt,
                                    event_id=event_id,
                                    action_id=resolved_id,
                                )
                                action["status"] = "prompted"
                                action["result"] = response_text
                                action["executed_at"] = time.time()
                                _mark_event_prompted(event)
                                await _persist_event(app, event_id, event)
                                result = {
                                    "status": "prompted",
                                    "event_id": event_id,
                                    "action_id": resolved_id,
                                    "result": response_text,
                                }
                            except Exception as exc:
                                action["status"] = "error"
                                action["error"] = str(exc)
                                action["executed_at"] = time.time()
                                _mark_event_prompted(event)
                                await _persist_event(app, event_id, event)
                                await _publish_content(
                                    app,
                                    content=f"(scheduled prompt failed) {exc}",
                                    chain_id=chain_id,
                                    session_id=session_id,
                                    metadata={
                                        "scheduled": True,
                                        "event_id": event_id,
                                        "action_id": resolved_id,
                                        "prompt_action": True,
                                        "error": True,
                                    },
                                )
                                result = {
                                    "status": "error",
                                    "event_id": event_id,
                                    "action_id": resolved_id,
                                    "error": str(exc),
                                }
            else:
                result = await _run_tool_action(
                    app,
                    event_id=event_id,
                    event=event,
                    action=action,
                    action_id=resolved_id,
                    force=force,
                )
            results.append(result)
            status_val = str(result.get("status") or "")
            ran_any = ran_any or status_val in {"invoked", "prompted"}
            had_error = had_error or status_val == "error"
        return {
            "status": "error" if had_error else ("invoked" if ran_any else "ok"),
            "event_id": event_id,
            "results": results,
        }


async def run_due_scheduled_tools_once(app: FastAPI) -> int:
    """Scan stored events and run due scheduled actions."""
    now = time.time()
    ran = 0
    for event_id in calendar_store.list_events():
        event = calendar_store.load_event(event_id)
        if not isinstance(event, dict) or not event:
            continue
        actions = _iter_actions(event)
        if not actions:
            continue
        start = _event_start_time(event)
        if start is None or start > now:
            continue
        try:
            res = await run_scheduled_tools_for_event(app, event_id)
        except Exception:
            continue
        for item in res.get("results") or []:
            if isinstance(item, dict) and item.get("status") in {"invoked", "prompted"}:
                ran += 1
    return ran


async def scheduled_tool_runner(app: FastAPI) -> None:
    """Background loop to execute due scheduled tool actions.

    This is a lightweight fallback for local/dev runs where Celery beat/workers
    may not be running yet. It is intentionally conservative and only executes
    actions that were explicitly scheduled into calendar events.
    """

    enabled = os.getenv("FLOAT_SCHEDULED_TOOLS_ENABLED", "true").lower() == "true"
    if not enabled:
        logger.info("Scheduled tool runner disabled via env")
        return

    poll_seconds = float(os.getenv("FLOAT_SCHEDULED_TOOLS_POLL_SECONDS", "10"))
    poll_seconds = max(1.0, poll_seconds)
    logger.info("Scheduled tool runner active (poll=%.1fs)", poll_seconds)

    try:
        while True:
            try:
                ran = await run_due_scheduled_tools_once(app)
                if ran:
                    logger.info("Executed %d scheduled tool action(s)", ran)
            except asyncio.CancelledError:  # pragma: no cover - shutdown path
                raise
            except Exception:
                logger.exception("Scheduled tool runner iteration failed")
            await asyncio.sleep(poll_seconds)
    except asyncio.CancelledError:  # pragma: no cover - shutdown path
        logger.info("Scheduled tool runner cancelled")
        raise
