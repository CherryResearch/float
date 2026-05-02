from __future__ import annotations

import copy
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:  # pragma: no cover - exercised through dependency availability tests
    import websocket as websocket_client
except Exception:  # pragma: no cover - optional transport dependency
    websocket_client = None


logger = logging.getLogger(__name__)

StreamConsumer = Callable[[Dict[str, Any]], None]
ToolExecutor = Callable[[Dict[str, Any]], Any]
ConnectFactory = Callable[..., Any]


class OpenAIResponsesWebSocketError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        retryable: bool = False,
        event: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.event = event or {}


@dataclass
class OpenAIResponsesWebSocketSession:
    key: str
    url: str
    float_session_id: str
    ws: Any
    created_at: float
    model: Optional[str] = None
    previous_response_id: Optional[str] = None
    last_input_signatures: List[str] = field(default_factory=list)
    sent_event_ids: List[str] = field(default_factory=list)
    completed_response_ids: List[str] = field(default_factory=list)
    tool_outputs_by_call_id: Dict[str, str] = field(default_factory=dict)
    response_count: int = 0
    updated_at: float = field(default_factory=time.time)
    closed: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)


@dataclass
class _CollectedResponse:
    text: str
    thought: str
    tools_used: List[Dict[str, Any]]
    response_id: Optional[str]
    previous_response_id: Optional[str]
    model: Optional[str]
    output_ids: List[str]
    usage: Optional[Dict[str, Any]]
    finish_reason: Optional[str]
    events: List[Dict[str, Any]]
    final_response: Dict[str, Any]
    thought_trace: List[Dict[str, Any]]


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except Exception:
        return str(value)


def _safe_json_loads(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _extract_output_list(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    response = payload.get("response")
    if isinstance(response, dict):
        output = response.get("output")
        if isinstance(output, list):
            return [item for item in output if isinstance(item, dict)]
    output = payload.get("output")
    if isinstance(output, list):
        return [item for item in output if isinstance(item, dict)]
    return []


def extract_response_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    response = payload.get("response")
    if isinstance(response, dict):
        nested = extract_response_text(response)
        if nested:
            return nested
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text:
        return output_text
    collected: List[str] = []
    for item in _extract_output_list(payload):
        content = item.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in {"output_text", "text"}:
                    collected.append(str(part.get("text") or ""))
        elif isinstance(content, str):
            collected.append(content)
    return "".join(collected)


def extract_response_function_calls(payload: Any) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    for item in _extract_output_list(payload):
        if item.get("type") != "function_call":
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        call_id = str(item.get("call_id") or item.get("id") or "").strip()
        arguments = _safe_json_loads(item.get("arguments") or {})
        calls.append(
            {
                "name": name,
                "args": arguments,
                "call_id": call_id,
                "response_item": dict(item),
            }
        )
    return calls


def _extract_response_metadata(
    payload: Any,
) -> Tuple[
    Optional[str],
    Optional[str],
    Optional[str],
    List[str],
    Optional[Dict[str, Any]],
    Optional[str],
]:
    response_id: Optional[str] = None
    previous_response_id: Optional[str] = None
    model: Optional[str] = None
    output_ids: List[str] = []
    usage: Optional[Dict[str, Any]] = None
    finish_reason: Optional[str] = None
    sources: List[Dict[str, Any]] = []
    if isinstance(payload, dict):
        nested = payload.get("response")
        if isinstance(nested, dict):
            sources.append(nested)
        sources.append(payload)
    for source in sources:
        if not isinstance(source, dict):
            continue
        raw_response_id = source.get("id")
        if isinstance(raw_response_id, str) and raw_response_id.strip():
            response_id = raw_response_id.strip()
        raw_previous = source.get("previous_response_id")
        if isinstance(raw_previous, str) and raw_previous.strip():
            previous_response_id = raw_previous.strip()
        raw_model = source.get("model")
        if isinstance(raw_model, str) and raw_model.strip():
            model = raw_model.strip()
        raw_output = source.get("output")
        if isinstance(raw_output, list):
            output_ids = [
                str(item.get("id")).strip()
                for item in raw_output
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            ]
        raw_usage = source.get("usage")
        if isinstance(raw_usage, dict):
            usage = dict(raw_usage)
        raw_status = source.get("status")
        if isinstance(raw_status, str) and raw_status:
            finish_reason = raw_status
    return response_id, previous_response_id, model, output_ids, usage, finish_reason


def _event_error_code(event: Dict[str, Any]) -> Optional[str]:
    error = event.get("error")
    if isinstance(error, dict):
        for key in ("code", "type"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    value = event.get("code")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _event_error_message(event: Dict[str, Any]) -> str:
    error = event.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("code") or error.get("type")
        if message:
            return str(message)
    message = event.get("message")
    if message:
        return str(message)
    return "Responses WebSocket request failed"


def _response_create_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    event = {
        key: copy.deepcopy(value)
        for key, value in payload.items()
        if key not in {"stream", "background"}
    }
    event["type"] = "response.create"
    return event


def _input_signatures(value: Any) -> List[str]:
    if isinstance(value, list):
        return [_stable_json(item) for item in value]
    if isinstance(value, str):
        return [_stable_json(value)]
    if value is None:
        return []
    return [_stable_json(value)]


def _tool_output_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        return str(result)


class OpenAIResponsesWebSocketTransport:
    """Experimental Responses API WebSocket transport.

    This adapter treats the provider connection as a runtime session. It keeps
    only the provider response ID and lightweight input fingerprints needed to
    decide whether a later Float turn can safely use incremental input.
    """

    def __init__(
        self,
        *,
        api_key: str,
        url: str = "wss://api.openai.com/v1/responses",
        connect_timeout: float = 15.0,
        request_timeout: float = 240.0,
        max_tool_rounds: int = 4,
        max_session_age_seconds: int = 3300,
        connect_factory: Optional[ConnectFactory] = None,
    ) -> None:
        self.api_key = api_key
        self.url = url or "wss://api.openai.com/v1/responses"
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self.max_tool_rounds = max(0, int(max_tool_rounds or 0))
        self.max_session_age_seconds = max(60, int(max_session_age_seconds or 3300))
        self.connect_factory = connect_factory
        self._sessions: Dict[str, OpenAIResponsesWebSocketSession] = {}
        self._sessions_lock = threading.RLock()

    def close(self) -> None:
        with self._sessions_lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            self._close_session(session)

    def run_response(
        self,
        *,
        session_id: str,
        payload: Dict[str, Any],
        stream_consumer: Optional[StreamConsumer] = None,
        stream_message_id: Optional[str] = None,
        tool_executor: Optional[ToolExecutor] = None,
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise OpenAIResponsesWebSocketError(
                "Responses WebSocket payload must be a dict"
            )
        session = self._get_session(session_id, payload.get("model"))
        with session.lock:
            return self._run_locked(
                session,
                payload=payload,
                stream_consumer=stream_consumer,
                stream_message_id=stream_message_id,
                tool_executor=tool_executor,
            )

    def _run_locked(
        self,
        session: OpenAIResponsesWebSocketSession,
        *,
        payload: Dict[str, Any],
        stream_consumer: Optional[StreamConsumer],
        stream_message_id: Optional[str],
        tool_executor: Optional[ToolExecutor],
    ) -> Dict[str, Any]:
        request_payload, input_mode, full_input_signatures = self._build_turn_payload(
            session, payload
        )
        all_text: List[str] = []
        all_thought: List[str] = []
        all_tools: List[Dict[str, Any]] = []
        all_events: List[Dict[str, Any]] = []
        all_thought_trace: List[Dict[str, Any]] = []
        tool_rounds = 0
        fallback_from_missing_previous = False
        final_collected: Optional[_CollectedResponse] = None

        while True:
            try:
                collected = self._send_response_create(
                    session,
                    request_payload,
                    stream_consumer=stream_consumer,
                    stream_message_id=stream_message_id,
                )
            except OpenAIResponsesWebSocketError as exc:
                if (
                    exc.code == "previous_response_not_found"
                    and input_mode == "incremental"
                    and not fallback_from_missing_previous
                ):
                    session.previous_response_id = None
                    request_payload = _response_create_payload(payload)
                    request_payload.pop("previous_response_id", None)
                    input_mode = "full_after_missing_previous"
                    fallback_from_missing_previous = True
                    continue
                try:
                    status = int(exc.event.get("status") or 0)
                except (TypeError, ValueError):
                    status = 0
                if status >= 400:
                    session.previous_response_id = None
                raise

            final_collected = collected
            all_events.extend(collected.events)
            if collected.text:
                all_text.append(collected.text)
            if collected.thought:
                all_thought.append(collected.thought)
            if collected.thought_trace:
                offset = len(all_thought_trace)
                for item in collected.thought_trace:
                    merged = dict(item)
                    merged["index"] = offset + int(merged.get("index") or 0)
                    all_thought_trace.append(merged)
            if collected.response_id:
                session.previous_response_id = collected.response_id
                session.completed_response_ids.append(collected.response_id)
            session.response_count += 1
            session.updated_at = time.time()
            if full_input_signatures and input_mode != "tool_continuation":
                session.last_input_signatures = list(full_input_signatures)

            function_calls = extract_response_function_calls(collected.final_response)
            if function_calls:
                all_tools.extend(
                    {
                        "name": call["name"],
                        "args": call.get("args"),
                        "call_id": call.get("call_id"),
                    }
                    for call in function_calls
                )
            if (
                not function_calls
                or tool_executor is None
                or tool_rounds >= self.max_tool_rounds
                or not session.previous_response_id
            ):
                break
            tool_rounds += 1
            output_items = self._execute_tool_calls(
                session,
                function_calls,
                tool_executor,
            )
            if not output_items:
                break
            session.last_input_signatures.extend(_input_signatures(output_items))
            request_payload = self._build_tool_continuation_payload(
                payload,
                previous_response_id=session.previous_response_id,
                output_items=output_items,
            )
            input_mode = "tool_continuation"

        text = "".join(all_text).strip()
        thought = "".join(all_thought).strip()
        if not text and all_tools:
            text = " ".join(f"[[tool_call:{idx}]]" for idx, _ in enumerate(all_tools))

        metadata: Dict[str, Any] = {
            "transport": "openai_responses_ws",
            "provider_runtime": {
                "transport": "openai_responses_ws",
                "session_id": session.key,
                "float_session_id": session.float_session_id,
                "input_mode": input_mode,
                "fallback_from_missing_previous": fallback_from_missing_previous,
            },
            "tool_rounds": tool_rounds,
            "raw_event_count": len(all_events),
        }
        if final_collected is not None:
            if final_collected.response_id:
                metadata["response_id"] = final_collected.response_id
            if final_collected.previous_response_id:
                metadata["previous_response_id"] = final_collected.previous_response_id
            elif request_payload.get("previous_response_id"):
                metadata["previous_response_id"] = request_payload.get(
                    "previous_response_id"
                )
            if final_collected.output_ids:
                metadata["output_ids"] = final_collected.output_ids
            if final_collected.model:
                metadata["model_received"] = final_collected.model
            requested_model = payload.get("model")
            if isinstance(requested_model, str) and requested_model.strip():
                metadata["model_requested"] = requested_model.strip()
                if (
                    final_collected.model
                    and final_collected.model != requested_model.strip()
                ):
                    metadata["model_mismatch"] = True
            if final_collected.usage:
                metadata["usage"] = final_collected.usage
            if final_collected.finish_reason:
                metadata["finish_reason"] = final_collected.finish_reason
        if session.sent_event_ids:
            metadata["client_event_ids"] = session.sent_event_ids[-10:]

        return {
            "text": text,
            "thought": thought,
            "tools_used": all_tools,
            "metadata": metadata,
            "thought_trace": all_thought_trace,
        }

    def _get_session(
        self, session_id: str, model: Any
    ) -> OpenAIResponsesWebSocketSession:
        key = str(session_id or "default")
        with self._sessions_lock:
            existing = self._sessions.get(key)
            if existing and not self._session_stale(existing):
                return existing
            if existing:
                self._close_session(existing)
            ws = self._connect()
            session = OpenAIResponsesWebSocketSession(
                key=key,
                url=self.url,
                float_session_id=key,
                ws=ws,
                created_at=time.time(),
                model=str(model).strip()
                if isinstance(model, str) and model.strip()
                else None,
            )
            self._sessions[key] = session
            return session

    def _session_stale(self, session: OpenAIResponsesWebSocketSession) -> bool:
        if session.closed:
            return True
        if time.time() - session.created_at >= self.max_session_age_seconds:
            return True
        try:
            connected = getattr(session.ws, "connected", True)
        except Exception:
            connected = True
        return connected is False

    def _connect(self) -> Any:
        if self.connect_factory is not None:
            return self.connect_factory(
                self.url,
                header=self._headers(),
                timeout=self.connect_timeout,
            )
        if websocket_client is None:
            raise OpenAIResponsesWebSocketError(
                "websocket-client is required for OpenAI Responses WebSocket mode",
                retryable=False,
            )
        return websocket_client.create_connection(
            self.url,
            header=self._headers(),
            timeout=self.connect_timeout,
        )

    def _headers(self) -> List[str]:
        return [f"Authorization: Bearer {self.api_key}"]

    def _close_session(self, session: OpenAIResponsesWebSocketSession) -> None:
        session.closed = True
        try:
            session.ws.close()
        except Exception:
            pass

    def _build_turn_payload(
        self,
        session: OpenAIResponsesWebSocketSession,
        payload: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], str, List[str]]:
        event = _response_create_payload(payload)
        full_signatures = _input_signatures(payload.get("input"))
        explicit_previous = event.get("previous_response_id")
        if isinstance(explicit_previous, str) and explicit_previous.strip():
            return event, "explicit_previous_response_id", full_signatures
        if not session.previous_response_id or not full_signatures:
            event.pop("previous_response_id", None)
            return event, "full", full_signatures
        if isinstance(payload.get("input"), list) and session.last_input_signatures:
            previous = session.last_input_signatures
            if (
                len(full_signatures) > len(previous)
                and full_signatures[: len(previous)] == previous
            ):
                event["previous_response_id"] = session.previous_response_id
                event["input"] = copy.deepcopy(payload.get("input")[len(previous) :])
                return event, "incremental", full_signatures
        event.pop("previous_response_id", None)
        return event, "full_context_reset", full_signatures

    def _build_tool_continuation_payload(
        self,
        payload: Dict[str, Any],
        *,
        previous_response_id: str,
        output_items: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        event = _response_create_payload(payload)
        event["previous_response_id"] = previous_response_id
        event["input"] = copy.deepcopy(output_items)
        return event

    def _send_response_create(
        self,
        session: OpenAIResponsesWebSocketSession,
        event: Dict[str, Any],
        *,
        stream_consumer: Optional[StreamConsumer],
        stream_message_id: Optional[str],
    ) -> _CollectedResponse:
        event_id = f"float_resp_ws_{uuid.uuid4().hex}"
        session.sent_event_ids.append(event_id)
        try:
            session.ws.send(json.dumps(event, ensure_ascii=False))
        except Exception as exc:
            self._close_session(session)
            raise OpenAIResponsesWebSocketError(
                f"Responses WebSocket send failed: {exc}",
                retryable=True,
            ) from exc
        return self._collect_response(
            session,
            request_event_id=event_id,
            stream_consumer=stream_consumer,
            stream_message_id=stream_message_id,
        )

    def _collect_response(
        self,
        session: OpenAIResponsesWebSocketSession,
        *,
        request_event_id: str,
        stream_consumer: Optional[StreamConsumer],
        stream_message_id: Optional[str],
    ) -> _CollectedResponse:
        deadline = time.time() + self.request_timeout
        text_parts: List[str] = []
        thought_trace: List[Dict[str, Any]] = []
        events: List[Dict[str, Any]] = []
        output_items: List[Dict[str, Any]] = []
        final_response: Dict[str, Any] = {}

        def emit_content(delta: str) -> None:
            if not delta:
                return
            text_parts.append(delta)
            if stream_consumer is None:
                return
            try:
                stream_consumer(
                    {
                        "type": "content",
                        "content": delta,
                        "session_id": session.float_session_id,
                        "message_id": stream_message_id,
                        "transport": "openai_responses_ws",
                    }
                )
            except Exception:
                pass

        def emit_thought(delta: str) -> None:
            if not delta:
                return
            idx = len(thought_trace)
            thought_trace.append({"index": idx, "text": delta})
            if stream_consumer is None:
                return
            try:
                stream_consumer(
                    {
                        "type": "thought",
                        "content": delta,
                        "offset": idx,
                        "session_id": session.float_session_id,
                        "message_id": stream_message_id,
                        "transport": "openai_responses_ws",
                    }
                )
            except Exception:
                pass

        while True:
            if time.time() > deadline:
                raise OpenAIResponsesWebSocketError(
                    "Responses WebSocket receive timed out",
                    retryable=True,
                    code="timeout",
                )
            try:
                raw = session.ws.recv()
            except Exception as exc:
                self._close_session(session)
                raise OpenAIResponsesWebSocketError(
                    f"Responses WebSocket receive failed: {exc}",
                    retryable=True,
                ) from exc
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="replace")
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except Exception:
                logger.debug("Skipping non-JSON Responses WebSocket frame")
                continue
            if not isinstance(event, dict):
                continue
            events.append(copy.deepcopy(event))
            event_type = str(event.get("type") or "").strip()
            if event_type == "error":
                code = _event_error_code(event)
                raise OpenAIResponsesWebSocketError(
                    _event_error_message(event),
                    code=code,
                    retryable=code in {"timeout", "rate_limit_exceeded"},
                    event=event,
                )
            if event_type.endswith(".delta"):
                delta = event.get("delta")
                if isinstance(delta, str):
                    lowered = event_type.lower()
                    if "reasoning" in lowered or "analysis" in lowered:
                        emit_thought(delta)
                    elif "output_text" in lowered or "text" in lowered:
                        emit_content(delta)
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                output_items.append(dict(item))
            if event_type in {"response.completed", "response.done"}:
                response_payload = event.get("response")
                if isinstance(response_payload, dict):
                    final_response = dict(response_payload)
                else:
                    final_response = dict(event)
                break

        if not text_parts:
            fallback_text = extract_response_text(final_response)
            if fallback_text:
                text_parts.append(fallback_text)
        if output_items and isinstance(final_response, dict):
            existing_output = final_response.get("output")
            if isinstance(existing_output, list):
                known = {_stable_json(item) for item in existing_output}
                for item in output_items:
                    marker = _stable_json(item)
                    if marker not in known:
                        existing_output.append(item)
                        known.add(marker)
            else:
                final_response["output"] = list(output_items)

        (
            response_id,
            previous_response_id,
            model,
            output_ids,
            usage,
            finish_reason,
        ) = _extract_response_metadata(final_response)
        return _CollectedResponse(
            text="".join(text_parts).strip(),
            thought="".join(item.get("text", "") for item in thought_trace).strip(),
            tools_used=extract_response_function_calls(final_response),
            response_id=response_id,
            previous_response_id=previous_response_id,
            model=model,
            output_ids=output_ids,
            usage=usage,
            finish_reason=finish_reason,
            events=events,
            final_response=final_response,
            thought_trace=thought_trace,
        )

    def _execute_tool_calls(
        self,
        session: OpenAIResponsesWebSocketSession,
        function_calls: Sequence[Dict[str, Any]],
        tool_executor: ToolExecutor,
    ) -> List[Dict[str, Any]]:
        output_items: List[Dict[str, Any]] = []
        for call in function_calls:
            call_id = str(call.get("call_id") or "").strip()
            if not call_id:
                continue
            if call_id in session.tool_outputs_by_call_id:
                output_text = session.tool_outputs_by_call_id[call_id]
            else:
                try:
                    result = tool_executor(dict(call))
                except Exception as exc:  # keep model loop informed
                    result = {
                        "status": "error",
                        "error": str(exc),
                    }
                output_text = _tool_output_text(result)
                session.tool_outputs_by_call_id[call_id] = output_text
            output_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output_text,
                }
            )
        return output_items
