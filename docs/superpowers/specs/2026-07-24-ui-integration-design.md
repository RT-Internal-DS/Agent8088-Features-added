# Agent8088 Rich UI Integration Design

**Date:** 2026-07-24
**Status:** Approved (user confirmed all sections in conversation)
**Scope:** Connect CLI_UI/agent8088_cli.py to the agent8088 engine, move to root, add 6 missing engine APIs, add rich dependency, commit to GitHub.

## Goal

Wire the Rich-based CLI UI into the Agent8088 engine so every UI feature works fully: live token streaming, ESC-to-interrupt, rich diff display for writes, live plan checklist, and context-window progress hint. Then commit the restructure + UI + engine changes to GitHub.

## File Changes

| File | Action |
|---|---|
| `CLI_UI/agent8088_cli.py` -> `agent8088_cli.py` | Move to repo root (sibling of agent8088 engine) |
| `agent8088` (engine) | Add 6 missing APIs (see Engine Changes) |
| `requirements.txt` | Add `rich>=13.0.0` |
| `.gitignore` | Add `graphify-out/`, `.opencode/` |
| `CLI_UI/` | Remove (empty after move) |

## Engine Changes (6 APIs, all backward-compatible)

### 2.1 on_token streaming in create_completion()

Add optional `on_token=None` param. When None (old REPL, benchmark), existing non-streaming path unchanged. When set (Rich UI), use `stream=True` and call `on_token("reasoning", delta)` / `on_token("content", delta)` per token. Reconstruct a response-like object via `_build_response()` so `run_agent()` reads `.choices[0].message.content` uniformly.

### 2.2 on_token + interrupt_check in run_agent()

Add `on_token=None, interrupt_check=None` to signature. Pass `on_token` to `create_completion()`. Check `interrupt_check()` at the top of each turn; if True, raise `AgentInterrupted`.

### 2.3 AgentInterrupted exception

New exception class raised when `interrupt_check()` returns True (user pressed ESC in the Rich UI).

### 2.4 _last_write_diff

Module-level global. After `write_text` tool runs, store a unified diff of old vs new content. The UI's `on_result()` reads `A._last_write_diff` to render a colored diff. Uses `difflib.unified_diff` (stdlib).

### 2.5 CONTEXT_WINDOW

Module global set from `config.context_window` (default 32768). The UI's `_estimate_context_pct()` reads it for the `% ctx` prompt hint.

### 2.6 on_step in _exec_plan()

Add optional `on_step=None` param. Call `on_step(idx, total, step_text, tool_name, "running", None)` before each step and `on_step(..., "done", result)` after. The UI's `cmd_plan()` renders a live checklist.

## Backward Compatibility

Old callers (`./agent8088` REPL, `run_benchmark.py`, one-shot mode) pass none of the new params. The non-streaming path in `create_completion()` is unchanged. Zero impact on existing behavior.

## Verification

- `python agent8088_cli.py` launches and shows the banner + config table.
- `/tools` lists all 7 tools.
- `/tool execute_shell command=hostname` runs and shows the result panel.
- A plain chat query streams tokens live (on_token fires).
- ESC interrupts a running turn (AgentInterrupted caught).
- `/plan` shows a live checklist (on_step fires).
- `/config` shows the context window.
- `python agent8088` (old REPL) still works unchanged.
- `python research/run_benchmark.py` still works unchanged.

## Commit

Single commit to GitHub including: repo restructure (staged), modified files (README, config.txt, skillopt.py, run_benchmark.py), design specs (docs/), AGENTS.md, CLI_UI integration, .gitignore update, requirements.txt update. graphify-out/ and .opencode/ ignored.