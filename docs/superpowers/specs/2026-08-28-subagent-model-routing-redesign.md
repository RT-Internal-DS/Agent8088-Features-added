# Subagent Model Routing — Redesign

**Date:** 2026-08-28
**Status:** Implemented
**Supersedes:** `2026-08-27-subagent-model-routing-design.md`

## Problem

The first implementation took the design doc's *example* tier table literally. That shipped
three things nobody wanted:

1. **Fictional tiers.** `MODEL_TIERS` mapped `haiku`/`flash`/`pro`/`sonnet`/`opus` to
   hardcoded model IDs. `explore.md` shipped with `model: haiku` — meaningless on
   `ollama-cloud`.
2. **Cross-provider routing.** `resolve_subagent_target` parsed `gemini:flash` and opened a
   second provider client mid-session, against the user's selected provider.
3. **Custom subagents could not be created.** `write_file` redirects invented paths into
   `artifacts/` (`resolve_write_path`), so a profile written to `src/agent8088/agents/`
   silently landed in `artifacts/` instead. `tools.txt` also named only two agent types as
   examples, so the model concluded named markdown agents could not be spawned at all.

## Design

**Model resolution — active provider only.** `MODEL_TIERS` and `resolve_subagent_target` are
deleted. `providers.resolve_subagent_model(raw, provider, client=None) -> (model_id, warning)`
returns an empty `model_id` to mean "use the session model". Empty or `inherit` inherits; a
`provider:model` string whose prefix names a known provider is rejected with a warning; any
other value is validated against `list_models(provider, client=client, fallback=False)`.

An **empty** model list — the shape of a network failure — skips validation rather than
rejecting a valid model against the stale `FALLBACK_MODELS` stub. A failed fetch must never
brick a working subagent. A model the provider does not offer falls back to the session model
with a warning, logged and prepended to the sub-agent's report, so a provider switch degrades
instead of breaking.

`_exec_subagent` no longer calls `get_client` a second time: it passes the parent's client
through and scopes only `model_name`. The `provider:` frontmatter key and the
`AGENT8088_SUBAGENT_MODEL` / `CLAUDE_CODE_SUBAGENT_MODEL` env overrides are gone.

**Storage — package built-ins plus a user directory.** `AGENTS_DIR` remains the package's
read-only built-ins. `USER_AGENTS_DIR` (default `_agent_data_dir()/agents`, i.e.
`%LOCALAPPDATA%\agent8088\agents`) holds custom profiles. `load_subagent_specs(agents_dir,
user_agents_dir=None)` merges them, user winning on name collision, and stamps each profile
with `builtin: bool` so the CLI can refuse to delete or edit a built-in. This mirrors the
precedence `config.txt` already uses, survives package upgrades, and keeps the repo tree
non-writable at runtime.

**Creation — a dedicated tool.** `create_subagent` (`mode=write_text`, deliberately **no**
`path_arg`) writes `USER_AGENTS_DIR/<name>.md` directly. Because the destination is derived
from `name` under a fixed directory rather than from a caller-supplied path, there is nothing
for `resolve_write_path` to resolve — which is precisely why `write_file` could not do this
job. It is dispatched in `run_tool` *after* the plan-only gate and behind its own
`check_permission("write_text", ...)` / `request_escalation`, so it escalates in readonly mode
like every other write.

Validation is strict at creation and lenient at run time: a name must match
`[a-z0-9][a-z0-9_-]*`, built-in names are refused, tools are checked against `TOOL_NAMES`
(`spawn_subagent` is always dropped), `max_turns` clamps to 1..20, and a named `model` is
rejected up front with a list of real model IDs. At spawn time an invalid model only warns,
because the active provider may have changed since the profile was written.

**Discoverability.** `render_tool_docs` appends the live sorted agent list to the
`spawn_subagent` entry, so custom agents reach the model without a prompt edit.

**CLI — one command.** `/agent` runs one; `/agents` manages them. `/agents` lists profiles
with Source and Model columns plus a one-line sentence naming the active provider and ~10 of
its models; `/agents models` prints the full list; `/agents new|edit|delete` create, open in
`$EDITOR`, and remove custom profiles. `cmd_agents` reloads specs on entry, so an agent
created this session appears without a restart.

## Verification

`tests/test_subagent_model_routing.py` — 20 tests covering tier/alias removal, the
network-failure passthrough, Ollama-style colon IDs (`gpt-oss:120b`) not being mistaken for
cross-provider syntax, directory merge and override precedence, the parent client never being
re-fetched, and — the regression guard for the original bug — that a created profile's path
contains no `artifacts` component.

Full suite: 492 passed, 39 skipped, 2 failed. The two failures are in
`tests/test_installer_timeouts.py` (partial-file cleanup) and predate this work.
