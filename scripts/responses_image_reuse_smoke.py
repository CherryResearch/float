from __future__ import annotations

import argparse
import json
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIRST_PROMPT = ""
DEFAULT_FOLLOW_UP_PROMPT = "What about this?"
JSON_TOOL_HINT = (
    "Tool call syntax for this turn: emit direct JSON only in the form "
    '{"tool":"tool_help","args":{}}. '
    "Use exact tool identifiers and valid JSON only. "
    "Do not wrap JSON calls in Harmony markers."
)
HARMONY_TOOL_HINT = (
    "Tool call syntax for this turn: emit Harmony tool calls only in the form "
    "<|channel|>commentary to=tool_help <|constrain|>json <|message|>{}. "
    "Use exact tool identifiers and valid JSON in the message body only. "
    "Do not prepend standalone JSON tool calls outside the Harmony wrapper."
)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


def _print_step(step: Step) -> None:
    mark = "PASS" if step.ok else "FAIL"
    detail = f" - {step.detail}" if step.detail else ""
    print(f"[smoke] {mark} {step.name}{detail}")


def _normalize_base_url(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        dev_state = REPO_ROOT / ".dev_state.json"
        if dev_state.exists():
            try:
                payload = json.loads(dev_state.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            port = payload.get("backend_port")
            if isinstance(port, int) and port > 0:
                value = f"http://127.0.0.1:{port}"
    if not value:
        value = "http://127.0.0.1:8000"
    value = value.rstrip("/")
    if value.endswith("/api"):
        return value
    return f"{value}/api"


def _latest_file(paths: Iterable[Path]) -> Optional[Path]:
    latest: Optional[Path] = None
    latest_mtime = -1.0
    for root in paths:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            try:
                mtime = path.stat().st_mtime
            except Exception:
                continue
            if mtime > latest_mtime:
                latest = path
                latest_mtime = mtime
    return latest


def _resolve_image_path(raw: str) -> Path:
    value = str(raw or "").strip()
    if value:
        path = Path(value).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Image not found: {path}")
        return path

    search_roots = [
        REPO_ROOT / "data" / "files" / "captures" / "transient" / "camera",
        REPO_ROOT / "data" / "files" / "captured",
        REPO_ROOT / "data" / "files" / "uploads",
        REPO_ROOT / "data" / "workspace",
        REPO_ROOT / "data" / "screenshots",
    ]
    for root in search_roots:
        latest = _latest_file([root])
        if latest is not None:
            return latest.resolve()

    fallback = _latest_file([REPO_ROOT / "data"])
    if fallback is not None:
        return fallback.resolve()
    raise FileNotFoundError("Could not find a local image under data/")


def _request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: float,
    **kwargs: Any,
) -> Dict[str, Any]:
    response = session.request(method, url, timeout=timeout, **kwargs)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected a JSON object from {url}, got: {type(payload)}")
    return payload


def _upload_via_capture(
    session: requests.Session,
    base_url: str,
    image_path: Path,
    *,
    timeout: float,
) -> Dict[str, Any]:
    content_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    with image_path.open("rb") as handle:
        uploaded = _request_json(
            session,
            "POST",
            f"{base_url}/captures/upload",
            timeout=timeout,
            files={"file": (image_path.name, handle, content_type)},
            data={"source": "camera"},
        )
    capture_id = str(uploaded.get("capture_id") or "").strip()
    if not capture_id:
        raise RuntimeError(f"Capture upload did not return capture_id: {uploaded}")
    promoted = _request_json(
        session,
        "POST",
        f"{base_url}/captures/{capture_id}/promote",
        timeout=timeout,
        json={"memory_refs": []},
    )
    attachment_ref = promoted.get("attachment")
    capture = promoted.get("capture")
    if not isinstance(attachment_ref, dict):
        raise RuntimeError(
            f"Capture promote did not return attachment data: {promoted}"
        )
    if not isinstance(capture, dict):
        capture = uploaded if isinstance(uploaded, dict) else {}
    return {
        "attachment": {
            "name": attachment_ref.get("filename") or image_path.name,
            "type": attachment_ref.get("content_type") or content_type,
            "url": attachment_ref.get("url"),
            "size": attachment_ref.get("size"),
            "content_hash": attachment_ref.get("content_hash"),
            "origin": attachment_ref.get("origin") or "captured",
            "relative_path": attachment_ref.get("relative_path"),
            "capture_source": capture.get("capture_source") or capture.get("source"),
        },
        "capture_id": capture_id,
        "content_hash": attachment_ref.get("content_hash"),
    }


def _upload_via_attachment(
    session: requests.Session,
    base_url: str,
    image_path: Path,
    *,
    timeout: float,
) -> Dict[str, Any]:
    content_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    with image_path.open("rb") as handle:
        uploaded = _request_json(
            session,
            "POST",
            f"{base_url}/attachments/upload",
            timeout=timeout,
            files={"file": (image_path.name, handle, content_type)},
            data={"origin": "captured", "capture_source": "camera"},
        )
    return {
        "attachment": {
            "name": uploaded.get("filename") or image_path.name,
            "type": uploaded.get("content_type") or content_type,
            "url": uploaded.get("url"),
            "size": uploaded.get("size"),
            "content_hash": uploaded.get("content_hash"),
            "origin": uploaded.get("origin") or "captured",
            "relative_path": uploaded.get("relative_path"),
            "capture_source": uploaded.get("capture_source") or "camera",
        },
        "capture_id": None,
        "content_hash": uploaded.get("content_hash"),
    }


def _chat_payload(
    *,
    message: str,
    session_id: str,
    message_id: str,
    model: str,
    attachments: Optional[List[Dict[str, Any]]] = None,
    vision_workflow: str,
    response_format: Optional[str],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "message": message,
        "session_id": session_id,
        "message_id": message_id,
        "model": model,
        "mode": "api",
        "use_rag": False,
        "vision_workflow": vision_workflow,
    }
    if attachments:
        payload["attachments"] = attachments
    if response_format:
        payload["response_format"] = response_format
    return payload


def _read_capture(path_value: str) -> Dict[str, Any]:
    target = Path(path_value).expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"Capture log not found: {target}")
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Capture log is not a JSON object: {target}")
    return payload


def _find_capture_path(
    *,
    capture_path: str,
    response_id: str,
    session_id: str,
    message_id: str,
    started_at: float,
) -> Optional[str]:
    if capture_path:
        target = Path(capture_path).expanduser().resolve()
        if target.exists():
            return str(target)
    if response_id:
        target = REPO_ROOT / "logs" / "oai_api" / f"{response_id}.json"
        if target.exists():
            return str(target.resolve())

    log_dir = REPO_ROOT / "logs" / "oai_api"
    if not log_dir.exists():
        return None

    candidates = sorted(
        (path for path in log_dir.glob("*.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates[:80]:
        try:
            if path.stat().st_mtime + 5 < started_at:
                break
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("session_id") or "").strip() == session_id:
            return str(path.resolve())
        request_payload = payload.get("request_payload")
        if not isinstance(request_payload, dict):
            continue
        metadata = request_payload.get("metadata")
        if not isinstance(metadata, dict):
            continue
        meta_session = str(
            metadata.get("session_name") or metadata.get("session_id") or ""
        ).strip()
        meta_message = str(metadata.get("message_id") or "").strip()
        if meta_session == session_id and (
            not message_id or meta_message == message_id
        ):
            return str(path.resolve())
    return None


def _count_input_images(capture_payload: Dict[str, Any]) -> int:
    request_payload = capture_payload.get("request_payload")
    if not isinstance(request_payload, dict):
        return 0
    raw_input = request_payload.get("input")
    if not isinstance(raw_input, list):
        return 0
    total = 0
    for entry in raw_input:
        if not isinstance(entry, dict):
            continue
        content = entry.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "input_image":
                total += 1
    return total


def _system_prompt_text(capture_payload: Dict[str, Any]) -> str:
    request_payload = capture_payload.get("request_payload")
    if not isinstance(request_payload, dict):
        return ""
    raw_input = request_payload.get("input")
    if not isinstance(raw_input, list):
        return ""
    chunks: List[str] = []
    for entry in raw_input:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("role") or "").strip().lower() != "system":
            continue
        content = entry.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text)
    return "\n".join(chunks)


def _count_exact(text: str, fragment: str) -> int:
    if not text or not fragment:
        return 0
    return text.count(fragment)


def _inspect_capture(
    *,
    name: str,
    capture_payload: Dict[str, Any],
    requested_model: str,
) -> Step:
    request_payload = capture_payload.get("request_payload")
    if not isinstance(request_payload, dict):
        return Step(name, False, "capture is missing request_payload")
    model = str(request_payload.get("model") or "").strip()
    if requested_model and model != requested_model:
        return Step(name, False, f"expected model {requested_model!r}, got {model!r}")
    image_count = _count_input_images(capture_payload)
    if image_count < 1:
        return Step(name, False, "no input_image parts found in Responses payload")
    system_text = _system_prompt_text(capture_payload)
    json_hint_count = _count_exact(system_text, JSON_TOOL_HINT)
    harmony_hint_count = _count_exact(system_text, HARMONY_TOOL_HINT)
    if json_hint_count != 1:
        return Step(
            name,
            False,
            f"expected one JSON tool-call hint, found {json_hint_count}",
        )
    if harmony_hint_count != 0:
        return Step(
            name,
            False,
            f"expected zero Harmony hints for {requested_model}, found {harmony_hint_count}",
        )
    response_format = request_payload.get("response_format")
    if isinstance(response_format, dict):
        fmt_type = str(response_format.get("type") or "").strip().lower()
        if fmt_type == "harmony":
            return Step(
                name, False, "request_payload.response_format unexpectedly used harmony"
            )
    return Step(
        name,
        True,
        f"input_images={image_count}, json_hint={json_hint_count}, harmony_hint={harmony_hint_count}",
    )


def _send_chat(
    session: requests.Session,
    base_url: str,
    payload: Dict[str, Any],
    *,
    timeout: float,
) -> Dict[str, Any]:
    started_at = time.time()
    response = _request_json(
        session,
        "POST",
        f"{base_url}/chat",
        timeout=timeout,
        json=payload,
    )
    metadata = response.get("metadata")
    if not isinstance(metadata, dict):
        raise RuntimeError(f"/chat returned no metadata: {response}")
    requested_capture_path = str(metadata.get("oai_api_log_path") or "").strip()
    response_id = str(metadata.get("response_id") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    message_id = str(payload.get("message_id") or "").strip()
    capture_path = _find_capture_path(
        capture_path=requested_capture_path,
        response_id=response_id,
        session_id=session_id,
        message_id=message_id,
        started_at=started_at,
    )
    return {
        "response": response,
        "metadata": metadata,
        "capture_path": capture_path,
        "requested_capture_path": requested_capture_path,
        "response_id": response_id,
    }


def run(args: argparse.Namespace) -> int:
    base_url = _normalize_base_url(args.base_url)
    image_path = _resolve_image_path(args.image)
    session = requests.Session()

    health = _request_json(session, "GET", f"{base_url}/health", timeout=args.timeout)
    health_status = str(health.get("status") or "").strip()
    health_step = Step("health", health_status == "healthy", health_status or "unknown")
    _print_step(health_step)
    if not health_step.ok:
        return 1

    image_step = Step("image", True, str(image_path))
    _print_step(image_step)

    upload_start = time.time()
    if args.upload_mode == "capture":
        upload_result = _upload_via_capture(
            session,
            base_url,
            image_path,
            timeout=args.timeout,
        )
    else:
        upload_result = _upload_via_attachment(
            session,
            base_url,
            image_path,
            timeout=args.timeout,
        )
    attachment = upload_result["attachment"]
    upload_detail = (
        f"mode={args.upload_mode}, content_hash={upload_result.get('content_hash')}, "
        f"elapsed={time.time() - upload_start:.2f}s"
    )
    _print_step(Step("upload", True, upload_detail))

    first_payload = _chat_payload(
        message=args.first_prompt,
        session_id=args.session_id,
        message_id=f"{args.session_id}-m1",
        model=args.model,
        attachments=[attachment],
        vision_workflow=args.vision_workflow,
        response_format=args.response_format,
    )
    first_result = _send_chat(session, base_url, first_payload, timeout=args.timeout)
    first_capture_path = str(first_result.get("capture_path") or "").strip()
    first_capture_detail = (
        f"response_id={first_result['response_id'] or 'missing'}, "
        f"capture={first_capture_path or 'missing'}"
    )
    if not first_capture_path and first_result.get("requested_capture_path"):
        first_capture_detail = f"{first_capture_detail}, requested_capture={first_result['requested_capture_path']}"
    _print_step(
        Step(
            "first-chat",
            True,
            first_capture_detail,
        )
    )
    if first_capture_path:
        first_capture = _read_capture(first_capture_path)
        first_capture_step = _inspect_capture(
            name="first-capture",
            capture_payload=first_capture,
            requested_model=args.model,
        )
    else:
        first_capture_step = Step(
            "first-capture",
            False,
            "no matching Responses capture log was found for turn 1",
        )
    _print_step(first_capture_step)

    second_payload = _chat_payload(
        message=args.follow_up_prompt,
        session_id=args.session_id,
        message_id=f"{args.session_id}-m2",
        model=args.model,
        attachments=None,
        vision_workflow=args.vision_workflow,
        response_format=args.response_format,
    )
    second_result = _send_chat(session, base_url, second_payload, timeout=args.timeout)
    second_metadata = second_result["metadata"]
    reused_count = None
    if isinstance(second_metadata.get("vision"), dict):
        reused_count = second_metadata["vision"].get("reused_recent_image_attachments")
    second_capture_path = str(second_result.get("capture_path") or "").strip()
    detail = (
        f"response_id={second_result['response_id'] or 'missing'}, "
        f"capture={second_capture_path or 'missing'}"
    )
    if not second_capture_path and second_result.get("requested_capture_path"):
        detail = (
            f"{detail}, requested_capture={second_result['requested_capture_path']}"
        )
    if reused_count is not None:
        detail = f"{detail}, reused={reused_count}"
    _print_step(Step("follow-up-chat", True, detail))
    if second_capture_path:
        second_capture = _read_capture(second_capture_path)
        second_capture_step = _inspect_capture(
            name="follow-up-capture",
            capture_payload=second_capture,
            requested_model=args.model,
        )
    else:
        second_capture_step = Step(
            "follow-up-capture",
            False,
            "no matching Responses capture log was found for turn 2",
        )
    _print_step(second_capture_step)

    ok = all(
        step.ok
        for step in (
            health_step,
            first_capture_step,
            second_capture_step,
        )
    )
    print(f"[smoke] session_id={args.session_id}")
    print(f"[smoke] first_capture={first_capture_path or 'missing'}")
    print(f"[smoke] second_capture={second_capture_path or 'missing'}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Upload one image, run a two-turn API chat repro on the Responses API, "
            "and verify that gpt-5.4 gets exactly one JSON tool-call hint while "
            "image input survives the no-new-image follow-up turn."
        )
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="Backend base URL. Defaults to .dev_state.json backend_port or http://127.0.0.1:8000/api.",
    )
    parser.add_argument(
        "--image",
        default="",
        help="Optional image path. Defaults to the latest local image under data/.",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.4",
        help="Model to request for both turns.",
    )
    parser.add_argument(
        "--session-id",
        default=f"responses-image-smoke-{int(time.time())}",
        help="Conversation session id to use for the repro.",
    )
    parser.add_argument(
        "--first-prompt",
        default=DEFAULT_FIRST_PROMPT,
        help="Prompt for the first turn with an image attachment. Defaults to an attachment-only send.",
    )
    parser.add_argument(
        "--follow-up-prompt",
        default=DEFAULT_FOLLOW_UP_PROMPT,
        help="Prompt for the second turn without attaching a new image.",
    )
    parser.add_argument(
        "--vision-workflow",
        default="auto",
        choices=["auto", "image_qa", "ocr", "compare", "caption"],
        help="vision_workflow value to send to /api/chat.",
    )
    parser.add_argument(
        "--response-format",
        default="",
        help="Optional explicit response_format override. Leave empty to test normal route selection.",
    )
    parser.add_argument(
        "--upload-mode",
        default="capture",
        choices=["capture", "attachment"],
        help="Whether to exercise /captures/upload + promote or direct /attachments/upload.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-request timeout in seconds.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except requests.HTTPError as exc:
        response = exc.response
        status = getattr(response, "status_code", "unknown")
        body = ""
        try:
            body = response.text[:500] if response is not None else ""
        except Exception:
            body = ""
        print(f"[smoke] FAIL http - status={status} body={body}")
        return 1
    except Exception as exc:
        print(f"[smoke] FAIL error - {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
