import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional


class _CalendarStub:
    @staticmethod
    def from_ical(data):  # type: ignore[unused-argument]
        return SimpleNamespace(walk=lambda: [])


sys.modules.setdefault("icalendar", SimpleNamespace(Calendar=_CalendarStub))

if "langextract" not in sys.modules:
    langextract_module = types.ModuleType("langextract")

    def _mock_extract(
        text, prompt_description=None, examples=None
    ):  # type: ignore[unused-argument]
        return SimpleNamespace(extractions=[])

    langextract_module.extract = _mock_extract

    data_module = types.ModuleType("langextract.data")

    class _Extraction:
        def __init__(
            self,
            extraction_class: str = "",
            extraction_text: str = "",
            attributes: Optional[Dict[str, Any]] = None,
        ):
            self.extraction_class = extraction_class
            self.extraction_text = extraction_text
            self.attributes = attributes or {}

    class _ExampleData:
        pass

    data_module.Extraction = _Extraction
    data_module.ExampleData = _ExampleData
    langextract_module.data = data_module

    sys.modules["langextract"] = langextract_module
    sys.modules["langextract.data"] = data_module

import pytest  # noqa: E402
from app.base_services import LLMService, MemoryManager  # noqa: E402
from app.tools import browser, crawler, local_files  # noqa: E402
from app.tools import memory as memory_tools  # noqa: E402
from app.utils import generate_signature  # noqa: E402


class DummyResponse:
    status_code = 200
    text = ""

    def __init__(self, payload: Dict[str, Any]):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Dict[str, Any]:
        return self._payload


@dataclass
class ToolCase:
    prompt: str
    tool: str
    build_args: Callable[[Path], Dict[str, Any]]
    func: Callable[..., Any]
    verify: Callable[[Path, MemoryManager, Any, Dict[str, Any]], bool]
    setup: Optional[
        Callable[[Path, MemoryManager, pytest.MonkeyPatch, Dict[str, Any]], None]
    ] = None
    signature_args: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None


def _setup_memory_binding(
    manager: MemoryManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(memory_tools, "_MANAGER", manager, raising=False)


def _setup_write_file_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: Dict[str, Any]
) -> None:
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path))
    # `write_file` is intentionally sandboxed to data/workspace.
    args["path"] = "note_from_tool.txt"


TOOL_CASES = [
    ToolCase(
        prompt="Use open_url to load https://example.com and confirm it opens.",
        tool="open_url",
        build_args=lambda tmp: {"url": "https://example.com"},
        func=browser.open_url,
        verify=lambda tmp, mgr, output, args: (output == f"Opened {args['url']}"),
    ),
    ToolCase(
        prompt="Write a quick note via write_file so we can inspect it later.",
        tool="write_file",
        build_args=lambda tmp: {
            "path": "note_from_tool.txt",
            "content": "log entry for oss-20b",
        },
        func=local_files.write_file,
        verify=lambda tmp, mgr, output, args: (
            output == "written"
            and (tmp / "workspace" / "note_from_tool.txt").read_text(encoding="utf-8")
            == args["content"]
        ),
        setup=lambda tmp, mgr, monkeypatch, args: _setup_write_file_tool(
            tmp, monkeypatch, args
        ),
    ),
    ToolCase(
        prompt="Fetch the status summary by calling the crawl tool.",
        tool="crawl",
        build_args=lambda tmp: {
            "url": "https://float.internal/status",
            "timeout": 5,
        },
        func=crawler.crawl,
        verify=lambda tmp, mgr, output, args: output.startswith("status: ok"),
        setup=lambda tmp, mgr, monkeypatch, args: monkeypatch.setattr(
            crawler.http_session,
            "get",
            lambda url, timeout=5, headers=None: SimpleNamespace(
                text="status: ok\nworkers=3", raise_for_status=lambda: None
            ),
        ),
        signature_args=lambda args: {"url": args["url"], "timeout": args["timeout"]},
    ),
    ToolCase(
        prompt="Use search_web to find croissant spots in Vancouver.",
        tool="search_web",
        build_args=lambda tmp: {
            "query": "Vancouver croissant ube bakery",
            "max_results": 3,
            "region": "ca-en",
        },
        func=crawler.search_web,
        verify=lambda tmp, mgr, output, args: (
            output["query"] == args["query"]
            and output["count"] == len(output["results"]) == 1
            and output["results"][0]["url"] == "https://bakery.example/croissant"
        ),
        setup=lambda tmp, mgr, monkeypatch, args: monkeypatch.setattr(
            crawler.http_session,
            "get",
            lambda url, params=None, timeout=10, headers=None: SimpleNamespace(
                text="""
                <div class="result">
                  <a class="result__a" href="https://bakery.example/croissant">Bakery Example</a>
                  <div class="result__snippet">Ube croissant special in Vancouver.</div>
                </div>
                """,
                raise_for_status=lambda: None,
            ),
        ),
    ),
    ToolCase(
        prompt="Remember that the active workflow is developer mode using remember.",
        tool="remember",
        build_args=lambda tmp: {
            "key": "workflow",
            "value": {"mode": "developer"},
            "importance": 0.9,
            "lifecycle": "reviewable",
            "sensitivity": None,
            "hint": None,
            "pinned": None,
            "importance_floor": None,
        },
        func=memory_tools.remember,
        verify=lambda tmp, mgr, output, args: (
            output == "ok"
            and mgr.get_item("workflow")["value"] == args["value"]
            and mgr.get_item("workflow")["lifecycle"] == "reviewable"
        ),
        setup=lambda tmp, mgr, monkeypatch, args: _setup_memory_binding(
            mgr, monkeypatch
        ),
    ),
]


@pytest.mark.parametrize("case", TOOL_CASES)
def test_oss20b_prompts_trigger_tool_proposals(
    case: ToolCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = MemoryManager({})
    manager.register_tool(case.tool, case.func)

    args = case.build_args(tmp_path)
    if case.setup:
        case.setup(tmp_path, manager, monkeypatch, args)

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        assert json is not None
        assert json.get("model") == "gpt-oss-20b"
        payload = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": case.tool,
                                    "arguments": json_module.dumps(args),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 6},
        }
        return DummyResponse(payload)

    json_module = json
    monkeypatch.setattr("app.base_services.http_session.post", fake_post)

    svc = LLMService(
        mode="api",
        config={
            "api_key": "test",
            "api_url": "https://models.internal/v1/chat/completions",
            "api_model": "gpt-oss-20b",
        },
    )

    result = svc.generate(case.prompt, session_id="oss20b")
    assert result["tools_used"] == [{"name": case.tool, "args": args}]

    user = "tester"
    signature_payload = case.signature_args(args) if case.signature_args else args
    signature = generate_signature(user, case.tool, signature_payload)
    output = manager.invoke_tool(case.tool, user=user, signature=signature, **args)

    verification = case.verify(tmp_path, manager, output, args)
    assert verification is True


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            '{"tool":"remember","args":{"key":"k","value":"v"}}\n\nSaved.',
            {"name": "remember", "args": {"key": "k", "value": "v"}},
        ),
        (
            'Tool calls\n- {"tool":"memory.save","params":{"text":"note","namespace":"facts"}}',
            {"name": "memory.save", "args": {"text": "note", "namespace": "facts"}},
        ),
        (
            '{"tool":"recall","args":{"key":"tea_party"}}{"tool":"recall","args":{"key":"tea_party"}}',
            {"name": "recall", "args": {"key": "tea_party"}},
        ),
    ],
)
def test_parse_inline_tool_call_handles_prefixes(
    text: str, expected: Dict[str, Any]
) -> None:
    svc = LLMService(
        mode="api",
        config={
            "api_key": "test",
            "api_url": "https://models.internal/v1/chat/completions",
            "api_model": "gpt-oss-20b",
        },
    )

    parsed = svc._parse_inline_tool_call(text)
    assert parsed is not None
    assert parsed["name"] == expected["name"]
    assert parsed["args"] == expected["args"]
