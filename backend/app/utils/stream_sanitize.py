from __future__ import annotations


def strip_inline_tool_json(fragment: str) -> str:
    """Best-effort removal of inline tool-call JSON from streamed fragments.

    Some models emit tool calls inline (e.g. {"tool":"remember","args":{...}})
    even when tool calling is enabled. The final response parser already
    extracts these, but the stream can briefly surface the raw JSON in the
    Agent Console; remove it so the stream stays readable.
    """

    if not fragment:
        return fragment
    marker_idx = fragment.find('{"tool"')
    if marker_idx < 0:
        marker_idx = fragment.find('"tool":')
    if marker_idx < 0:
        marker_idx = fragment.find('"tool"')
    if marker_idx < 0:
        return fragment
    brace_idx = fragment.rfind("{", 0, marker_idx + 1)
    cut = brace_idx if brace_idx >= 0 else marker_idx
    return fragment[:cut]


class InlineToolStreamFilter:
    """Stateful filter that removes inline tool-call JSON across streamed chunks."""

    def __init__(self) -> None:
        self._active = False
        self._depth = 0
        self._in_string = False
        self._escape = False

    @staticmethod
    def _find_tool_object_start(fragment: str, start_at: int) -> int:
        """Return the index of a JSON object that starts with a `tool` key.

        Accepts optional whitespace/newlines after the opening `{` so we also
        catch pretty-printed tool call payloads like:

        {
          "tool": "...",
          "args": {...}
        }
        """

        idx = fragment.find("{", start_at)
        while idx >= 0:
            probe = idx + 1
            while probe < len(fragment) and fragment[probe].isspace():
                probe += 1
            if fragment.startswith('"tool"', probe):
                after = probe + len('"tool"')
                while after < len(fragment) and fragment[after].isspace():
                    after += 1
                if after < len(fragment) and fragment[after] == ":":
                    return idx
            idx = fragment.find("{", idx + 1)
        return -1

    def filter(self, fragment: str) -> str:
        if not fragment:
            return fragment

        out: list[str] = []
        i = 0
        while i < len(fragment):
            if not self._active:
                start = self._find_tool_object_start(fragment, i)
                if start < 0:
                    out.append(fragment[i:])
                    break
                out.append(fragment[i:start])
                self._active = True
                self._depth = 0
                self._in_string = False
                self._escape = False
                i = start

            ch = fragment[i]
            if self._escape:
                self._escape = False
                i += 1
                continue
            if self._in_string and ch == "\\":
                self._escape = True
                i += 1
                continue
            if ch == '"':
                self._in_string = not self._in_string
                i += 1
                continue
            if not self._in_string:
                if ch == "{":
                    self._depth += 1
                elif ch == "}":
                    if self._depth > 0:
                        self._depth -= 1
                    if self._depth == 0:
                        self._active = False
            i += 1

        return "".join(out)
