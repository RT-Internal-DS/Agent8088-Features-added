"""The browser gets escalation fields, not the raw \\x1f wire record.

cli.py splits ESCALATION_REQUEST into target_mode / change_type / paths /
reason in five places and renders a readable panel. The web bridge forwarded
the record verbatim as `description`, so the approval card showed
"ESCALATION_REQUESTeditnew_file/Users/..." -- the separators are unprintable,
so the fields ran together into one string.
"""

from agent8088 import engine
from agent8088 import web_server


def _record(target_mode="edit", change_type="new_file",
            paths=("/tmp/a.txt",), reason="Tool 'write_file' requires write_text access."):
    return engine.request_escalation(target_mode, list(paths), change_type, reason)


def test_parses_every_field_of_the_wire_record():
    parsed = web_server._parse_escalation(_record())

    assert parsed == {
        "target_mode": "edit",
        "change_type": "new_file",
        "paths": ["/tmp/a.txt"],
        "reason": "Tool 'write_file' requires write_text access.",
    }


def test_keeps_windows_paths_intact():
    """The record uses \\x1f precisely so a drive letter's colon survives."""
    parsed = web_server._parse_escalation(
        _record(paths=(r"C:\Users\me\notes.txt",)))

    assert parsed["paths"] == [r"C:\Users\me\notes.txt"]


def test_splits_multiple_paths():
    parsed = web_server._parse_escalation(_record(paths=("/tmp/a", "/tmp/b")))

    assert parsed["paths"] == ["/tmp/a", "/tmp/b"]


def test_reason_containing_the_separator_is_not_truncated():
    """split(..., 4) keeps the tail whole; the reason is last for that reason."""
    parsed = web_server._parse_escalation(_record(reason="needs write\x1faccess"))

    assert parsed["reason"] == "needs write\x1faccess"


def test_returns_none_for_anything_that_is_not_an_escalation():
    assert web_server._parse_escalation("Error: no such file") is None
    assert web_server._parse_escalation("") is None
    # Truncated record: better to fall back than to render half the fields.
    assert web_server._parse_escalation("ESCALATION_REQUEST\x1fedit") is None


def test_escalation_event_carries_the_parsed_fields():
    """What the ApprovalCard actually receives."""
    event = web_server._escalation_event("write_file", _record(), "esc-1")

    assert event["type"] == "escalation"
    assert event["tool_name"] == "write_file"
    assert event["id"] == "esc-1"
    assert event["change_type"] == "new_file"
    assert event["target_mode"] == "edit"
    assert event["paths"] == ["/tmp/a.txt"]
    assert event["reason"] == "Tool 'write_file' requires write_text access."
    # description is the human-readable summary, never the raw record
    assert "ESCALATION_REQUEST" not in event["description"]
    assert "\x1f" not in event["description"]


def test_unparseable_result_still_produces_a_usable_card():
    event = web_server._escalation_event("write_file", "something went sideways", "esc-2")

    assert event["type"] == "escalation"
    assert event["id"] == "esc-2"
    assert event["description"] == "something went sideways"
    assert event["change_type"] == "write"      # the pre-existing default
    assert event["paths"] == []
