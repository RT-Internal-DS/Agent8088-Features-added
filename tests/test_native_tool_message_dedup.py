"""Regression: assistant turns must not store duplicate/malformed tool markup.

Bug: when a native-tools provider returns both prose containing a malformed
✿FUNCTION✿/✿ARGS✿ block AND a structured tool_calls entry for the same call,
the stored assistant message ended up with both copies concatenated. The
malformed copy (e.g. missing the colon after ✿ARGS✿) then poisoned the next
request to the backend ("Failed to parse input at pos N").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent8088.engine import _native_tool_text, strip_tool_json  # noqa: E402


class _Function:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, name, arguments):
        self.function = _Function(name, arguments)


class _Message:
    def __init__(self, content, tool_calls):
        self.content = content
        self.tool_calls = tool_calls


def _build_stored_content(message):
    content = message.content or ""
    native_text = _native_tool_text(message)
    if native_text:
        content = "\n".join(part for part in (strip_tool_json(content), native_text) if part)
    return content


def test_malformed_duplicate_markup_is_not_stored_twice():
    malformed = (
        '✿FUNCTION✿: web_search ✿ARGS✿'
        '{"query": "Amir Husain Austin Texas companies work"}'
    )
    message = _Message(
        content=malformed,
        tool_calls=[_ToolCall("web_search", '{"query": "Amir Husain Austin Texas companies work"}')],
    )
    stored = _build_stored_content(message)
    assert stored.count("web_search") == 1
    assert "✿ARGS✿{" not in stored  # colon-less malformed form must be gone


def test_no_tool_calls_leaves_content_untouched():
    message = _Message(content="plain answer", tool_calls=[])
    assert _build_stored_content(message) == "plain answer"


if __name__ == "__main__":
    test_malformed_duplicate_markup_is_not_stored_twice()
    test_no_tool_calls_leaves_content_untouched()
    print("ok")
