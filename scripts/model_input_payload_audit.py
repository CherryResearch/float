"""Audit Float's provider-visible model input without calling a provider.

The capture helper runs the real ``LLMService._generate_via_api`` payload
builder behind a temporary in-process HTTP stub.  The resulting payload is
then checked for conflicting tool-call surfaces, repeated or ambiguous tool
advertisements, legacy aliases, and explicit size budgets.

Console output is Markdown rather than a raw payload dump so this is safe to
use as a focused local contract check.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import base_services, routes  # noqa: E402
from app.base_services import LLMService, ModelContext  # noqa: E402
from app.tool_names import (  # noqa: E402
    MODEL_HIDDEN_COMPATIBILITY_NAMES,
    TOOL_NAME_ALIASES,
)
from app.tools import BUILTIN_TOOLS  # noqa: E402

DIRECT_JSON_HINTS = (
    "tool call syntax for this turn: emit direct json",
    '"tool":"<exact_tool_name>"',
)
HARMONY_HINTS = (
    "tool call syntax for this turn: emit harmony",
    "<|channel|>commentary to=",
    "<|constrain|>json",
)
CANONICAL_NOTE_RE = re.compile(
    r"Canonical Float tool:\s*([A-Za-z0-9_.-]+)\.", re.IGNORECASE
)
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
CAPTURE_LOCK = threading.Lock()


@dataclass(frozen=True)
class AuditThresholds:
    """Explicit payload budgets; callers and the CLI may override every value."""

    max_provider_tools: int = 48
    max_prose_tools: int = 36
    max_prose_catalog_chars: int = 3600
    max_provider_catalog_chars: int = 24000
    max_provider_description_chars: int = 16000
    max_catalog_sections: int = 1
    max_cross_surface_repeats: int = 0
    max_duplicate_system_bodies: int = 0
    max_repeated_system_blocks: int = 0
    max_duplicate_catalog_sections: int = 0


@dataclass(frozen=True)
class PayloadSnapshot:
    payload: Dict[str, Any]
    canonical_by_provider: Dict[str, str] = field(default_factory=dict)
    label: str = "local payload"


@dataclass(frozen=True)
class AuditCheck:
    code: str
    passed: bool
    evidence: str


@dataclass
class AuditReport:
    label: str
    checks: List[AuditCheck]
    metrics: Dict[str, int]
    limits: Dict[str, int]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed_codes(self) -> List[str]:
        return [check.code for check in self.checks if not check.passed]

    def render_markdown(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [
            "# Model Input Payload Contract Audit",
            "",
            f"Result: **{status}**",
            f"Snapshot: {self.label}",
            "",
            "| Check | Status | Evidence |",
            "|---|---:|---|",
        ]
        for check in self.checks:
            lines.append(
                f"| {_markdown_cell(check.code)} | "
                f"{'PASS' if check.passed else 'FAIL'} | "
                f"{_markdown_cell(check.evidence)} |"
            )
        lines.extend(
            [
                "",
                "| Metric | Observed | Limit |",
                "|---|---:|---:|",
            ]
        )
        for name, observed in self.metrics.items():
            limit = self.limits.get(name)
            limit_text = str(limit) if limit is not None else "n/a"
            lines.append(f"| {_markdown_cell(name)} | {observed} | {limit_text} |")
        return "\n".join(lines)


@dataclass(frozen=True)
class _ProviderTool:
    provider_name: str
    description: str
    canonical_name: str


@dataclass(frozen=True)
class _CatalogEntry:
    name: str
    description: str


class _CapturedResponse:
    status_code = 200
    text = ""

    def json(self) -> Dict[str, Any]:
        return {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "captured"}],
                }
            ]
        }

    def raise_for_status(self) -> None:
        return None


def _markdown_cell(value: Any) -> str:
    return " ".join(str(value or "").replace("|", "\\|").split())


def _short_list(values: Iterable[str], *, limit: int = 8) -> str:
    items = sorted({str(value).strip() for value in values if str(value).strip()})
    if not items:
        return "none"
    if len(items) <= limit:
        return ", ".join(items)
    return f"{', '.join(items[:limit])}, plus {len(items) - limit} more"


def _normalize_description(value: Any) -> str:
    text = " ".join(str(value or "").split())
    text = CANONICAL_NOTE_RE.sub("", text).strip(" .")
    return text.casefold()


def _content_text(value: Any) -> str:
    fragments: List[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, str):
            fragments.append(item)
            return
        if isinstance(item, list):
            for nested in item:
                collect(nested)
            return
        if not isinstance(item, dict):
            return
        for key in ("text", "input_text", "output_text", "content"):
            if key in item:
                collect(item.get(key))

    collect(value)
    return "\n".join(fragment for fragment in fragments if fragment).strip()


def _system_message_bodies(payload: Mapping[str, Any]) -> List[str]:
    """Extract system/developer bodies from Chat and Responses payload shapes."""

    bodies: List[str] = []
    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        bodies.append(instructions.strip())

    for field_name in ("messages", "input"):
        value = payload.get(field_name)
        if not isinstance(value, list):
            continue
        for message in value:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip().lower()
            if role not in {"system", "developer"}:
                continue
            body = _content_text(message.get("content"))
            if body:
                bodies.append(body)

    # Float collapses unstructured Responses inputs into role-prefixed text.
    responses_input = payload.get("input")
    if isinstance(responses_input, str):
        boundary = re.compile(
            r"(?m)^(system|developer|user|assistant|tool):[ \t]*",
            re.IGNORECASE,
        )
        matches = list(boundary.finditer(responses_input))
        for index, match in enumerate(matches):
            if match.group(1).lower() not in {"system", "developer"}:
                continue
            end = matches[index + 1].start() if index + 1 < len(matches) else None
            body = responses_input[match.end() : end].strip()
            if body:
                bodies.append(body)
    return bodies


def _normalized_body(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def _repetition_counts(values: Iterable[str]) -> tuple[int, int]:
    counts = Counter(value for value in values if value)
    repeated = [count for count in counts.values() if count > 1]
    return len(repeated), sum(count - 1 for count in repeated)


def _substantive_system_blocks(bodies: Sequence[str]) -> List[str]:
    blocks: List[str] = []
    for body in bodies:
        for raw_block in re.split(r"\n\s*\n+", body):
            normalized = _normalized_body(raw_block)
            if len(normalized) >= 80 and len(normalized.split()) >= 8:
                blocks.append(normalized)
    return blocks


def _provider_catalog_characters(payload: Mapping[str, Any]) -> int:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return 0
    compact = json.dumps(
        tools,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return len(compact)


_NAMED_SCHEMA_MAP_KEYS = {
    "$defs",
    "definitions",
    "dependentSchemas",
    "patternProperties",
    "properties",
}


def _schema_annotation_title_paths(payload: Mapping[str, Any]) -> List[str]:
    """Find schema ``title`` annotations without flagging a property named title."""

    found: List[str] = []

    def walk_schema(value: Any, path: str) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                walk_schema(item, f"{path}[{index}]")
            return
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            if key == "title":
                found.append(f"{path}.title")
                continue
            if key in _NAMED_SCHEMA_MAP_KEYS and isinstance(item, dict):
                for schema_name, nested_schema in item.items():
                    walk_schema(nested_schema, f"{path}.{key}.{schema_name}")
                continue
            walk_schema(item, f"{path}.{key}")

    raw_tools = payload.get("tools")
    if not isinstance(raw_tools, list):
        return found
    for index, raw_tool in enumerate(raw_tools):
        if not isinstance(raw_tool, dict):
            continue
        function = (
            raw_tool.get("function")
            if isinstance(raw_tool.get("function"), dict)
            else raw_tool
        )
        schema = function.get("parameters")
        if isinstance(schema, dict):
            walk_schema(schema, f"tool[{index}].parameters")
    return found


def _provider_tools(
    payload: Mapping[str, Any], canonical_by_provider: Mapping[str, str]
) -> List[_ProviderTool]:
    raw_tools = payload.get("tools")
    if not isinstance(raw_tools, list):
        return []
    tools: List[_ProviderTool] = []
    for index, raw in enumerate(raw_tools):
        if not isinstance(raw, dict):
            continue
        function = raw.get("function") if isinstance(raw.get("function"), dict) else raw
        provider_name = str(function.get("name") or "").strip()
        if not provider_name:
            tool_type = str(raw.get("type") or "native").strip() or "native"
            provider_name = f"@{tool_type}:{index + 1}"
        description = str(function.get("description") or "").strip()
        note_match = CANONICAL_NOTE_RE.search(description)
        note_name = note_match.group(1) if note_match else ""
        canonical_name = str(
            canonical_by_provider.get(provider_name) or note_name or provider_name
        ).strip()
        tools.append(
            _ProviderTool(
                provider_name=provider_name,
                description=description,
                canonical_name=canonical_name,
            )
        )
    return tools


def _catalog_sections(text: str) -> List[str]:
    header = re.escape(str(routes._AVAILABLE_TOOLS_PROMPT_HEADER))
    footer = re.escape(str(routes._AVAILABLE_TOOLS_PROMPT_FOOTER))
    pattern = re.compile(f"{header}.*?{footer}", re.IGNORECASE | re.DOTALL)
    return [match.group(0) for match in pattern.finditer(text)]


def _catalog_entries(sections: Sequence[str]) -> List[_CatalogEntry]:
    entries: List[_CatalogEntry] = []
    for section in sections:
        for raw_line in section.splitlines():
            match = re.match(r"^\s*-\s*`([^`]+)`(?:\s*:\s*(.*))?$", raw_line)
            if not match:
                continue
            name = match.group(1).strip()
            description = str(match.group(2) or "").strip()
            if name.endswith(".*"):
                prefix = name[:-2]
                for chunk in description.split(","):
                    candidate = chunk.strip().strip("`")
                    if candidate.startswith("..."):
                        continue
                    candidate = candidate.split()[0] if candidate else ""
                    if (
                        candidate
                        and TOOL_NAME_RE.fullmatch(candidate)
                        and candidate.startswith(prefix + ".")
                    ):
                        entries.append(_CatalogEntry(candidate, "grouped catalog"))
                continue
            if TOOL_NAME_RE.fullmatch(name):
                entries.append(_CatalogEntry(name, description))
    return entries


def _duplicates(values: Iterable[str]) -> List[str]:
    counter = Counter(value for value in values if value)
    return sorted(name for name, count in counter.items() if count > 1)


def _duplicate_descriptions(
    pairs: Iterable[tuple[str, str]],
) -> List[str]:
    names_by_description: Dict[str, List[str]] = defaultdict(list)
    for name, description in pairs:
        normalized = _normalize_description(description)
        if normalized:
            names_by_description[normalized].append(name)
    duplicates = []
    for names in names_by_description.values():
        unique_names = sorted(set(names))
        if len(unique_names) > 1:
            duplicates.append(" / ".join(unique_names))
    return sorted(duplicates)


def capture_outbound_payload(
    *,
    system_prompt: str,
    tool_definitions: Sequence[Dict[str, Any]],
    context_messages: Optional[Sequence[Dict[str, Any]]] = None,
    prompt: str | Sequence[Dict[str, Any]] = "Inspect the local payload contract.",
    model: str = "gpt-5.6-sol",
    label: str = "captured Responses payload",
) -> PayloadSnapshot:
    """Run the real outbound payload builder behind a no-network HTTP stub."""

    prepared_tools, canonical_by_provider = routes._provider_native_tool_definitions(
        list(copy.deepcopy(tool_definitions))
    )
    captured: Dict[str, Any] = {}

    def fake_post(
        url: str,
        headers: Optional[Dict[str, str]] = None,
        json: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        **_: Any,
    ) -> _CapturedResponse:
        captured["url"] = url
        captured["payload"] = copy.deepcopy(json or {})
        return _CapturedResponse()

    service = LLMService(
        mode="api",
        config={
            "api_key": "local-payload-audit",
            "api_url": "http://payload-audit.invalid/v1/responses",
            "api_model": model,
            "enable_responses_stream": False,
            "openai_responses_ws_enabled": False,
        },
    )
    context = ModelContext(
        system_prompt=system_prompt,
        messages=list(copy.deepcopy(context_messages or [])),
        tools=[],
    )
    with CAPTURE_LOCK:
        with patch.object(base_services.http_session, "post", side_effect=fake_post):
            service._generate_via_api(
                prompt,
                context,
                model=model,
                native_tool_definitions=prepared_tools,
            )
    payload = captured.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("outbound payload was not captured")
    return PayloadSnapshot(
        payload=payload,
        canonical_by_provider=dict(canonical_by_provider),
        label=label,
    )


def audit_payload_snapshot(
    snapshot: PayloadSnapshot,
    *,
    thresholds: Optional[AuditThresholds] = None,
    legacy_aliases: Optional[Mapping[str, str]] = None,
) -> AuditReport:
    limits = thresholds or AuditThresholds()
    alias_map = (
        {
            **TOOL_NAME_ALIASES,
            **MODEL_HIDDEN_COMPATIBILITY_NAMES,
        }
        if legacy_aliases is None
        else dict(legacy_aliases)
    )
    system_bodies = _system_message_bodies(snapshot.payload)
    system_text = "\n\n".join(system_bodies)
    lowered_text = system_text.casefold()
    tools = _provider_tools(snapshot.payload, snapshot.canonical_by_provider)
    sections = _catalog_sections(system_text)
    catalog_entries = _catalog_entries(sections)

    normalized_system_bodies = [_normalized_body(body) for body in system_bodies]
    duplicate_system_bodies, duplicate_system_body_copies = _repetition_counts(
        normalized_system_bodies
    )
    system_blocks = _substantive_system_blocks(system_bodies)
    repeated_system_blocks, repeated_system_block_copies = _repetition_counts(
        system_blocks
    )
    normalized_sections = [_normalized_body(section) for section in sections]
    duplicate_catalog_sections, duplicate_catalog_section_copies = _repetition_counts(
        normalized_sections
    )
    schema_title_paths = _schema_annotation_title_paths(snapshot.payload)

    provider_names = [tool.provider_name for tool in tools]
    canonical_names = [tool.canonical_name for tool in tools]
    prose_names = [entry.name for entry in catalog_entries]
    provider_name_duplicates = _duplicates(provider_names)
    canonical_name_duplicates = _duplicates(canonical_names)
    prose_name_duplicates = _duplicates(prose_names)
    provider_description_duplicates = _duplicate_descriptions(
        (tool.provider_name, tool.description) for tool in tools
    )
    prose_description_duplicates = _duplicate_descriptions(
        (entry.name, entry.description) for entry in catalog_entries
    )

    direct_json = any(marker in lowered_text for marker in DIRECT_JSON_HINTS)
    harmony = any(marker in lowered_text for marker in HARMONY_HINTS)
    mixed_formats = bool(tools) and (direct_json or harmony)
    format_evidence = (
        "native tools are isolated from inline syntax"
        if not mixed_formats and not (direct_json and harmony)
        else "provider tools and inline syntax are both present"
        if mixed_formats
        else "direct JSON and Harmony syntax are both present"
    )

    duplicate_name_evidence = (
        provider_name_duplicates + canonical_name_duplicates + prose_name_duplicates
    )
    duplicate_description_evidence = (
        provider_description_duplicates + prose_description_duplicates
    )

    advertised_names = set(canonical_names).union(prose_names)
    legacy_seen = sorted(name for name in advertised_names if name in alias_map)

    alias_mismatches: List[str] = []
    provider_name_set = set(provider_names)
    for mapped_provider in snapshot.canonical_by_provider:
        if mapped_provider not in provider_name_set:
            alias_mismatches.append(f"orphan map {mapped_provider}")
    for tool in tools:
        note_match = CANONICAL_NOTE_RE.search(tool.description)
        note_name = note_match.group(1) if note_match else ""
        mapped = snapshot.canonical_by_provider.get(tool.provider_name)
        if mapped and note_name and note_name != mapped:
            alias_mismatches.append(f"{tool.provider_name} note does not name {mapped}")
        elif note_name and note_name != tool.canonical_name:
            alias_mismatches.append(f"{tool.provider_name} note names {note_name}")

    canonical_set = set(canonical_names)
    provider_to_canonical = {tool.provider_name: tool.canonical_name for tool in tools}
    for prose_name in prose_names:
        mapped_name = provider_to_canonical.get(prose_name)
        if mapped_name and mapped_name != prose_name:
            alias_mismatches.append(
                f"prose exposes provider alias {prose_name} for {mapped_name}"
            )
        elif canonical_set and prose_name not in canonical_set:
            alias_mismatches.append(f"prose-only tool {prose_name}")

    cross_surface = sorted(canonical_set.intersection(prose_names))
    prose_catalog_chars = sum(len(section) for section in sections)
    provider_catalog_chars = _provider_catalog_characters(snapshot.payload)
    provider_description_chars = sum(len(tool.description) for tool in tools)
    metrics = {
        "provider tools": len(tools),
        "prose tools": len(set(prose_names)),
        "provider catalog characters": provider_catalog_chars,
        "prose catalog characters": prose_catalog_chars,
        "provider description characters": provider_description_chars,
        "schema annotation titles": len(schema_title_paths),
        "catalog sections": len(sections),
        "duplicate catalog sections": duplicate_catalog_sections,
        "native/prose repeats": len(cross_surface),
        "system messages": len(system_bodies),
        "system characters": sum(len(body) for body in system_bodies),
        "duplicate system bodies": duplicate_system_bodies,
        "repeated system blocks": repeated_system_blocks,
    }
    report_limits = {
        "provider tools": limits.max_provider_tools,
        "prose tools": limits.max_prose_tools,
        "provider catalog characters": limits.max_provider_catalog_chars,
        "prose catalog characters": limits.max_prose_catalog_chars,
        "provider description characters": limits.max_provider_description_chars,
        "schema annotation titles": 0,
        "catalog sections": limits.max_catalog_sections,
        "duplicate catalog sections": limits.max_duplicate_catalog_sections,
        "native/prose repeats": limits.max_cross_surface_repeats,
        "duplicate system bodies": limits.max_duplicate_system_bodies,
        "repeated system blocks": limits.max_repeated_system_blocks,
    }

    checks = [
        AuditCheck(
            "tool-call format isolation",
            not mixed_formats and not (direct_json and harmony),
            format_evidence,
        ),
        AuditCheck(
            "unique advertised names",
            not duplicate_name_evidence,
            (
                "all provider, canonical, and prose names are unique"
                if not duplicate_name_evidence
                else _short_list(duplicate_name_evidence)
            ),
        ),
        AuditCheck(
            "unique advertised descriptions",
            not duplicate_description_evidence,
            (
                "all nonempty descriptions are distinct"
                if not duplicate_description_evidence
                else _short_list(duplicate_description_evidence)
            ),
        ),
        AuditCheck(
            "legacy aliases hidden",
            not legacy_seen,
            (
                "no legacy alias is model-visible"
                if not legacy_seen
                else "; ".join(f"{name} to {alias_map[name]}" for name in legacy_seen)
            ),
        ),
        AuditCheck(
            "provider tool budget",
            len(tools) <= limits.max_provider_tools,
            f"{len(tools)} provider tools; limit {limits.max_provider_tools}",
        ),
        AuditCheck(
            "prose tool budget",
            len(set(prose_names)) <= limits.max_prose_tools,
            f"{len(set(prose_names))} prose tools; limit {limits.max_prose_tools}",
        ),
        AuditCheck(
            "provider catalog character budget",
            provider_catalog_chars <= limits.max_provider_catalog_chars,
            (
                f"{provider_catalog_chars} characters; "
                f"limit {limits.max_provider_catalog_chars}"
            ),
        ),
        AuditCheck(
            "prose catalog character budget",
            prose_catalog_chars <= limits.max_prose_catalog_chars,
            (
                f"{prose_catalog_chars} characters; "
                f"limit {limits.max_prose_catalog_chars}"
            ),
        ),
        AuditCheck(
            "provider description budget",
            provider_description_chars <= limits.max_provider_description_chars,
            (
                f"{provider_description_chars} characters; "
                f"limit {limits.max_provider_description_chars}"
            ),
        ),
        AuditCheck(
            "schema annotation titles removed",
            not schema_title_paths,
            (
                "provider schemas contain no title annotations"
                if not schema_title_paths
                else _short_list(schema_title_paths)
            ),
        ),
        AuditCheck(
            "provider and prose aliases align",
            not alias_mismatches,
            (
                "provider-safe names map cleanly to prose names"
                if not alias_mismatches
                else _short_list(alias_mismatches)
            ),
        ),
        AuditCheck(
            "unique system message bodies",
            duplicate_system_bodies <= limits.max_duplicate_system_bodies,
            (
                "all system/developer message bodies are distinct"
                if not duplicate_system_bodies
                else (
                    f"{duplicate_system_bodies} repeated body value(s), "
                    f"{duplicate_system_body_copies} extra copy/copies"
                )
            ),
        ),
        AuditCheck(
            "unique substantive system blocks",
            repeated_system_blocks <= limits.max_repeated_system_blocks,
            (
                "no exact substantive paragraph repeats across system inputs"
                if not repeated_system_blocks
                else (
                    f"{repeated_system_blocks} repeated block value(s), "
                    f"{repeated_system_block_copies} extra copy/copies"
                )
            ),
        ),
        AuditCheck(
            "unique available-tools sections",
            duplicate_catalog_sections <= limits.max_duplicate_catalog_sections,
            (
                "available-tools section bodies are distinct"
                if not duplicate_catalog_sections
                else (
                    f"{duplicate_catalog_sections} repeated section value(s), "
                    f"{duplicate_catalog_section_copies} extra copy/copies"
                )
            ),
        ),
        AuditCheck(
            "catalog repetition",
            len(sections) <= limits.max_catalog_sections
            and len(cross_surface) <= limits.max_cross_surface_repeats,
            (
                f"{len(sections)} section(s), {len(cross_surface)} native/prose repeat(s)"
            ),
        ),
    ]
    return AuditReport(
        label=snapshot.label,
        checks=checks,
        metrics=metrics,
        limits=report_limits,
    )


def _default_prompt_tool_definitions(
    tool_names: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    names = list(tool_names or BUILTIN_TOOLS.keys())

    class _Manager:
        def list_tools(self) -> List[str]:
            return list(names)

    app = SimpleNamespace(state=SimpleNamespace(memory_manager=_Manager()))
    return routes._registered_prompt_tool_definitions(
        app,
        allow_computer_capture=False,
    )


def build_local_payload_snapshot(
    *,
    model: str = "gpt-5.6-sol",
    surface: str = "native",
    tool_names: Optional[Sequence[str]] = None,
    base_prompt: Optional[str] = None,
) -> PayloadSnapshot:
    """Build a representative payload using Float's real prompt/tool helpers."""

    normalized_surface = str(surface or "native").strip().lower()
    if normalized_surface not in {"native", "inline-json", "inline-harmony"}:
        raise ValueError("surface must be native, inline-json, or inline-harmony")
    definitions = _default_prompt_tool_definitions(tool_names)
    if base_prompt is None:
        base_prompt = (
            BACKEND_ROOT / "app" / "prompts" / "system_prompt.txt"
        ).read_text(encoding="utf-8")

    native = normalized_surface == "native"
    response_format = "harmony" if normalized_surface == "inline-harmony" else None
    prompt = routes._effective_system_prompt(
        base_prompt,
        response_format=response_format,
        include_tool_guidance=True,
    )
    if native:
        prompt = routes._without_inline_tool_protocol_prompt(prompt)
    else:
        prompt = routes._with_available_tools_prompt(prompt, definitions)
    return capture_outbound_payload(
        system_prompt=prompt,
        tool_definitions=definitions if native else [],
        model=model,
        label=f"{normalized_surface} payload for {model}",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and audit a local provider payload without network access."
    )
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument(
        "--surface",
        choices=("native", "inline-json", "inline-harmony"),
        default="native",
    )
    parser.add_argument("--tool-name", action="append", default=[])
    parser.add_argument("--max-provider-tools", type=int, default=48)
    parser.add_argument("--max-prose-tools", type=int, default=36)
    parser.add_argument("--max-prose-catalog-chars", type=int, default=3600)
    parser.add_argument("--max-provider-catalog-chars", type=int, default=24000)
    parser.add_argument("--max-provider-description-chars", type=int, default=16000)
    parser.add_argument("--max-catalog-sections", type=int, default=1)
    parser.add_argument("--max-cross-surface-repeats", type=int, default=0)
    parser.add_argument("--max-duplicate-system-bodies", type=int, default=0)
    parser.add_argument("--max-repeated-system-blocks", type=int, default=0)
    parser.add_argument("--max-duplicate-catalog-sections", type=int, default=0)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    thresholds = AuditThresholds(
        max_provider_tools=args.max_provider_tools,
        max_prose_tools=args.max_prose_tools,
        max_prose_catalog_chars=args.max_prose_catalog_chars,
        max_provider_catalog_chars=args.max_provider_catalog_chars,
        max_provider_description_chars=args.max_provider_description_chars,
        max_catalog_sections=args.max_catalog_sections,
        max_cross_surface_repeats=args.max_cross_surface_repeats,
        max_duplicate_system_bodies=args.max_duplicate_system_bodies,
        max_repeated_system_blocks=args.max_repeated_system_blocks,
        max_duplicate_catalog_sections=args.max_duplicate_catalog_sections,
    )
    snapshot = build_local_payload_snapshot(
        model=args.model,
        surface=args.surface,
        tool_names=args.tool_name or None,
    )
    report = audit_payload_snapshot(snapshot, thresholds=thresholds)
    print(report.render_markdown())
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
