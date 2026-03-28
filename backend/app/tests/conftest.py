import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

CONV_ROOT = Path(__file__).parent / "conversations"


def pytest_sessionstart(session):
    session.test_logs = []
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    session.conversation_dir = CONV_ROOT / timestamp
    session.conversation_dir.mkdir(parents=True, exist_ok=True)


def pytest_runtest_setup(item):
    item._start_time = time.time()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        duration = time.time() - item._start_time
        ctx = getattr(
            item,
            "_context_info",
            {"messages": [], "tools": [], "metadata": {}},
        )
        if not hasattr(item.session, "test_logs"):
            item.session.test_logs = []
        entry = {
            "test": item.nodeid,
            "outcome": report.outcome,
            "duration": duration,
            "provider": os.getenv("OPENAI_PROVIDER", "unknown"),
            "model": os.getenv("OPENAI_MODEL", "unknown"),
            "messages": len(ctx.get("messages") or []),
            "tools": len(ctx.get("tools") or []),
        }
        item.session.test_logs.append(entry)

        conv_dir = getattr(item.session, "conversation_dir", None)
        if conv_dir and ctx.get("messages"):
            sanitized = item.nodeid.replace("/", "_").replace("::", "__")
            filename = f"{sanitized}.json"
            payload = {
                "test": item.nodeid,
                "provider": entry["provider"],
                "model": entry["model"],
                "messages": ctx.get("messages"),
                "tools": ctx.get("tools"),
                "metadata": ctx.get("metadata"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            (conv_dir / filename).write_text(json.dumps(payload, indent=2))


def pytest_sessionfinish(session, exitstatus):
    logs = getattr(session, "test_logs", [])
    conv_dir = getattr(session, "conversation_dir", CONV_ROOT)
    (conv_dir / "summary.json").write_text(json.dumps(logs, indent=2))


@pytest.fixture(autouse=True)
def log_context(monkeypatch, request):
    import sys
    from pathlib import Path

    backend_dir = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(backend_dir))
    from app.base_services import LLMService

    original = LLMService.generate

    def wrapper(self, prompt, context=None, session_id="default", **kwargs):
        ctx = context or self.get_context(session_id)
        result = original(
            self,
            prompt,
            context=context,
            session_id=session_id,
            **kwargs,
        )
        request.node._context_info = {
            "messages": ctx.messages,
            "tools": ctx.tools,
            "metadata": ctx.metadata,
        }
        return result

    monkeypatch.setattr(LLMService, "generate", wrapper)
    yield
    monkeypatch.setattr(LLMService, "generate", original)
