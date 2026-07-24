# Tool Name Alias Resolution Design

**Date:** 2026-07-24
**Status:** Approved
**Scope:** Fix tool-call execution failure when the model emits natural tool names (e.g. `bash`) instead of canonical names (e.g. `execute_shell`)

## Problem

The model (Ornith-35B) emits `✿FUNCTION✿: bash ✿ARGS✿: {"command": "mkdir testing"}` but `tools.txt` names the tool `execute_shell`. The engine's `find_tool_calls()` does an exact match against `TOOL_NAMES` — `"bash" in {"execute_shell", ...}` fails — so no tool call is parsed and the raw `✿FUNCTION✿` text is displayed as the answer instead of executed.

## Solution

Add a `TOOL_ALIASES` dict + `_resolve()` helper in `find_tool_calls()`. Every name match check resolves through the alias map first. Canonical names still work (the map only redirects non-matching names).

```python
TOOL_ALIASES = {
    "bash": "execute_shell", "sh": "execute_shell",
    "shell": "execute_shell", "run": "execute_shell",
    "search": "web_search", "web": "web_search", "google": "web_search",
    "read": "read_text", "cat": "read_text",
    "write": "write_file", "create_file": "write_file",
    "calc": "calculate", "eval": "calculate", "math": "calculate",
    "last": "last_output", "prev_output": "last_output",
}
```

## Changes

- `agent8088`: add `TOOL_ALIASES` dict (after `TOOL_NAMES` on line 386) and `_resolve()` helper. Update all 5 name-match sites in `find_tool_calls()` (lines 591, 601, 607, 613, 620) to resolve through `_resolve()`.
- No changes to `tools.txt`, system prompt, benchmark tests, or TOOLS_DEF.

## Backward Compatibility

Canonical names (`execute_shell`, `write_file`, etc.) pass through `_resolve()` unchanged — the alias map only redirects names that don't exact-match.

## Verification

- Model emits `bash` -> resolves to `execute_shell` -> tool executes
- Model emits `execute_shell` directly -> still works (passthrough)
- Factual queries (no tools) still answer directly