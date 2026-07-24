# Subagents Design

**Date:** 2026-07-24
**Status:** Implemented
**Scope:** Add model-driven sub-agents to the Agent8088 harness — the model can delegate a self-contained task to an independent agent instance that runs its own loop with fresh context, a specialized prompt, a restricted tool set, and its own turn budget, returning a concise summary. Includes an animated nested live view in the Rich CLI.

## Problem

Complex multi-step sub-tasks (deep search, reading many files, multi-hop research) bloat the main context and derail the primary task. Claude Code, Hermes, and Codex all solve this with sub-agents: a discretionary tool the model calls to spin up a fresh agent for a bounded job. Agent8088 had no such capability.

## Solution

Sub-agents are a new tool **mode** (`subagent`), declared like every other tool in `tools.txt`. The model sees one more tool — `spawn_subagent(agent_type, task)` — and calls it only when it judges a task warrants delegation (identical to how it decides to call `web_search`). Delegation is model-driven, not harness-heuristic.

When called, the engine looks up a **profile** (from `agents/*.md`), seeds a fresh message list with only `task`, gives the sub-agent its own system prompt + tool subset + turn budget, and calls the existing `run_agent()` **recursively** (bounded by a depth guard). The sub-agent's final answer becomes the parent's tool result. No agent-loop logic is duplicated.

### Profiles: `agents/*.md`

Each profile is a markdown file with a `---` frontmatter block and a body used as the system prompt:

```markdown
---
name: explore
description: Read-only exploration sub-agent for searching and reading the codebase.
tools: execute_shell, read_text, web_search, get_page_title, last_output
max_turns: 6
---
You are a read-only exploration sub-agent. Locate and read the relevant files...
```

Shipped profiles: `general-purpose` (full tool set) and `explore` (read-only). A built-in `general-purpose` fallback is synthesized if `agents/` is missing, so the feature never hard-fails on a fresh checkout.

### Enforcement (Ollama has no `tools=` param)

The backend rejects the OpenAI `tools` param, so the **system prompt is the model's only source of tool knowledge**. A restricted sub-agent therefore gets a system prompt whose tool section (`render_tool_docs`) lists only its allowed tools, AND `find_tool_calls(text, allowed)` rejects any tool outside that set. Both layers are required.

### Safety

- **Bounded recursion:** a `depth` counter threads through `exec_tool → run_tool → _exec_subagent → run_agent(depth+1)`. `_exec_subagent` refuses when `depth >= SUBAGENT_MAX_DEPTH` (config `subagent_max_depth`, default `1`).
- **No self-spawn:** profiles never include `spawn_subagent` in their tool list (belt); the depth guard is the suspenders.
- **Parent-state isolation:** `_last_tool_output` / `_last_tool_name` / `_last_write_diff` are saved and restored around the sub-run so the sub-agent's intermediate tool calls don't clobber the parent's "last output" store.
- **Fault isolation:** a sub-run that raises is caught and returned as a `Sub-agent failed: …` string, never killing the parent turn.

## Changes

- `agent8088`:
  - `find_tool_calls(text, allowed=None)` — allowed-tool set, defaults to `TOOL_NAMES` (5 match sites).
  - `run_agent(..., system_prompt=None, tools_def=None, allowed_tools=None, depth=0)` — all default to today's globals (backward-compatible).
  - `depth` threaded through `run_tool`, `exec_tool`, `_exec_plan`.
  - New: `AGENTS_DIR`, `DEFAULT_SUBAGENT`, `SUBAGENT_MAX_DEPTH`, `_parse_frontmatter_md`, `load_subagent_specs`, `SUBAGENT_SPECS`, `_exec_subagent`, `subagent` branch in `run_tool`, and the `subagent_ui` presentation hook.
- `tools.txt`: one new line declaring `spawn_subagent` (`mode=subagent`).
- `agents/general-purpose.md`, `agents/explore.md`: shipped profiles.
- `system.md`: a "Subagents" guidance section (when to delegate — advice, not a rule).
- `agent8088_cli.py`: `_SubStatusLine`, `_make_subagent_ui` (animated nested trace), `on_result` subagent panel, `/agents` command.
- `tests/`: `conftest.py` (hermetic engine loader + `ScriptedModel`) and `test_subagents.py` (9 tests).
- `requirements.txt`: `pytest>=7.0` (dev).

## Backward Compatibility

Every new `run_agent` param defaults to the existing global; the Ollama non-streaming path is untouched; `subagent_ui` defaults to `None` (sub-agents run silently for benchmark / one-shot / plain REPL). Existing callers (`./agent8088` REPL, `run_benchmark.py`, one-shot mode) pass none of the new params and behave exactly as before.

## Verification

- `python -m pytest tests/ -q` → all green (9 tests): allowed-set restriction, `run_agent` custom prompt/depth, profile loader (+ default fallback), depth guard, happy-path + parent-state isolation, unknown-type fallback, UI-hook firing.
- Engine load (repo-relative config): `spawn_subagent in TOOL_NAMES`, profiles `['explore', 'general-purpose']`, tool doc present in `SYSTEM_PROMPT`, default profile excludes `spawn_subagent`.
- CLI: `/agents` lists profiles; a delegated task renders the animated nested view and a `[subagent:…]` result panel.
- Live end-to-end (needs the configured model backend): a query that delegates shows a `spawn_subagent` call whose result starts with `[subagent:…]`.

## Risks / Open Questions

- **`timeout=300` on the tool spec is advisory** — the real bound is `profile["max_turns"]` × per-call model timeout, not the shell timeout.
- **Shared module globals** are safe under synchronous, one-at-a-time sub-runs (handled by save/restore); parallel sub-agents (not built — YAGNI) would need per-run state.
- **Fresh context by design:** the sub-agent sees only `task`, not the parent conversation. `system.md` instructs the model to write fully standalone tasks. Revisit only if delegated tasks routinely lack needed context.
