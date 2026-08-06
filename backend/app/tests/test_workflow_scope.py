from app.workflow_scope import (
    build_capability_scope,
    filter_tool_definitions_for_scope,
    normalize_capability_scope,
)


def test_capability_scope_is_compact_stable_and_filters_new_tools():
    original = [
        {
            "name": "remember",
            "description": "Save a memory.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "type": "function",
            "function": {
                "name": "search_web",
                "description": "Search the web.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]
    reversed_scope = build_capability_scope(
        workflow="default",
        channel="text",
        modules=["computer_use", "computer_use"],
        tool_definitions=list(reversed(original)),
    )
    scope = build_capability_scope(
        workflow="default",
        channel="text",
        modules=["computer_use"],
        tool_definitions=original,
    )

    assert scope == reversed_scope
    assert scope["tool_names"] == ["remember", "search_web"]
    assert len(scope["tool_catalog_sha256"]) == 64
    assert "description" not in str(scope)

    expanded = [*original, {"name": "shell.exec", "parameters": {}}]
    filtered = filter_tool_definitions_for_scope(expanded, scope)
    assert [
        item.get("name") or item.get("function", {}).get("name") for item in filtered
    ] == ["remember", "search_web"]


def test_empty_scope_is_valid_and_filters_all_definitions():
    definitions = [{"name": "remember"}]
    scope = build_capability_scope(
        workflow="default",
        channel="text",
        modules=[],
        tool_definitions=[],
    )

    assert normalize_capability_scope(scope) == scope
    assert filter_tool_definitions_for_scope(definitions, scope) == []


def test_capability_scope_normalizes_and_deduplicates_compatibility_aliases():
    alias_definition = {
        "name": "memory.read",
        "description": "Read memory.",
        "parameters": {"type": "object", "properties": {}},
    }
    canonical_definition = {**alias_definition, "name": "recall"}

    alias_scope = build_capability_scope(
        workflow="default",
        channel="text",
        modules=[],
        tool_definitions=[alias_definition],
    )
    duplicate_scope = build_capability_scope(
        workflow="default",
        channel="text",
        modules=[],
        tool_definitions=[alias_definition, canonical_definition],
    )

    assert alias_scope == duplicate_scope
    assert alias_scope["tool_names"] == ["recall"]
    assert normalize_capability_scope(
        {**alias_scope, "tool_names": ["memory.read", "recall"]}
    )["tool_names"] == ["recall"]


def test_invalid_or_legacy_scope_does_not_filter_definitions():
    definitions = [{"name": "remember"}]

    assert normalize_capability_scope({"tool_names": []}) is None
    assert filter_tool_definitions_for_scope(definitions, None) == definitions
