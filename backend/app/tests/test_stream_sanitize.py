import pytest


@pytest.mark.parametrize(
    "fragment,expected",
    [
        ("", ""),
        ("no tool here", "no tool here"),
        (
            'thinking... {"tool":"remember","args":{"key":"k","value":"v"}}',
            "thinking... ",
        ),
        (
            'things."}{"tool":"remember","args":{"key":"k","value":"v"}}',
            'things."}',
        ),
    ],
)
def test_strip_inline_tool_json(fragment: str, expected: str) -> None:
    from app.utils.stream_sanitize import strip_inline_tool_json

    assert strip_inline_tool_json(fragment) == expected


def test_inline_tool_stream_filter_strips_across_chunks() -> None:
    from app.utils.stream_sanitize import InlineToolStreamFilter

    flt = InlineToolStreamFilter()
    first = '{"tool":"remember","args":{"key":"k","value":"things.'
    second = '"}} after'
    assert flt.filter(first) == ""
    assert flt.filter(second) == " after"


def test_inline_tool_stream_filter_strips_pretty_printed_tool_json() -> None:
    from app.utils.stream_sanitize import InlineToolStreamFilter

    flt = InlineToolStreamFilter()
    fragment = (
        'thinking...\n{\n  "tool": "remember",\n  "args": {"key": "k", "value": "v"}\n}\n'
        "after"
    )
    assert flt.filter(fragment) == "thinking...\n\nafter"


def test_inline_tool_stream_filter_strips_pretty_printed_across_chunks() -> None:
    from app.utils.stream_sanitize import InlineToolStreamFilter

    flt = InlineToolStreamFilter()
    first = 'thinking...\n{\n  "tool": "remember",\n  "args": {"key": "k", "value": "things.'
    second = 'v"}\n}\n after'
    assert flt.filter(first) == "thinking...\n"
    assert flt.filter(second) == "\n after"
