# Dynamic Per-Provider Token Limits

**Date:** 2026-08-27
**Status:** Draft — pending user review

## Problem

`max_completion_tokens` (default 8192) and `context_window` (default 32768) are
flat global config keys read once at engine import time (`engine.py:318-321`).
Switching models with `/model` calls `activate_model` (`engine.py:1425-1457`),
which surgically updates the provider, model name, and client — but never touches
the token limits. A 1M-context model (e.g. GLM-5.2) is capped at 8192 completion
tokens, the wall users hit on complex tasks. There is no per-provider override
for these two settings, and neither is in `LIMIT_SPECS`, so `/limits` cannot
adjust them at runtime.

The existing `subagent_max_turns.<profile>` and `tool_timeout.<tool>` override
patterns prove the codebase already supports per-name dotted config keys with
live mutation and persistence. The `provider.<name>.<field>` parser
(`load_providers`, `engine.py:1318-1352`) already accepts arbitrary fields — it
just never reads `context_window` or `max_completion_tokens` back. The plumbing
is there; only the read sites and a setter are missing.

## Goals

1. Per-provider `context_window` and `max_completion_tokens` in config.txt,
   auto-applied when `/model` switches to that provider.
2. Live override via `/limits provider <name> <key> <value>`, persisted to
   config.txt.
3. Best-effort endpoint probe for unknown models (no hardcoded model list).
4. Safe fallback to existing global defaults when no per-provider value is set
   and the probe finds nothing.
5. No hardcoded model preset list — matches the user's explicit requirement and
   Hermes's approach (declare per-model in provider config, not a builtin dict).

## Non-Goals

- No per-provider `temperature` or `max_turns` in this change (user confirmed
  only `context_window` and `max_completion_tokens`). The pattern is reusable if
  those are wanted later.
- No builtin MODEL_LIMITS dict. The user explicitly rejected a prebuilt list.
- No change to the flat global `context_window` / `max_completion_tokens` keys
  — they remain the fallback.
- No change to `LIMIT_SPECS` or the flat `/limits <key> <value>` path. The
  per-provider path gets its own setter, modeled on `set_subagent_turns`.

## CWD config.txt isolation (companion change)

### Problem

Config path resolution (`engine.py:232-243`) has this precedence:

```
1. AGENT8088_CONFIG env var
2. ~/.agent8088/config.txt
3. %LOCALAPPDATA%/agent8088/config.txt   ← global install wins here
4. APP_DIR/config.txt
```

There is no CWD check. When the user runs the agent from a project directory
(e.g. Desktop) but the global install lives at `%LOCALAPPDATA%\agent8088\`, the
global `config.txt` is used — not a `config.txt` in the current directory. This
means per-provider token limits set in a local `config.txt` are ignored, and
`/limits provider` writes to the global config, polluting it for every other
project.

### Design

If `./config.txt` exists in the current working directory, use it exclusively.
No merge with the global config — the two files never interact. If no
`./config.txt` exists in CWD, the existing resolution chain runs unchanged.

New precedence:

```
1. AGENT8088_CONFIG env var             (unchanged — explicit override)
2. ./config.txt in CWD                   (NEW — if present, exclusive)
3. ~/.agent8088/config.txt              (unchanged)
4. %LOCALAPPDATA%/agent8088/config.txt  (unchanged)
5. APP_DIR/config.txt                   (unchanged)
```

### Code change

`engine.py:232-243` gains one branch:

```python
# Config path: AGENT8088_CONFIG env > CWD ./config.txt > ~/.agent8088/config.txt
#             > %LOCALAPPDATA%/agent8088/config.txt > APP_DIR/config.txt
_cwd_config = Path.cwd() / "config.txt"
_user_config = Path.home() / ".agent8088" / "config.txt"
_win_config = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "agent8088" / "config.txt"
if os.environ.get("AGENT8088_CONFIG"):
    CONFIG_PATH = Path(os.environ["AGENT8088_CONFIG"]).expanduser()
elif _cwd_config.exists():
    CONFIG_PATH = _cwd_config
elif _user_config.exists():
    CONFIG_PATH = _user_config
elif _win_config.exists():
    CONFIG_PATH = _win_config
else:
    CONFIG_PATH = Path(str(APP_DIR / "config.txt")).expanduser()
APP_CONFIG = load_simple_config(CONFIG_PATH)
```

### Why exclusive, not merge

The user explicitly chose "CWD exclusive if present, global untouched" over
merge or full-replacement. Exclusive means:

- The local `config.txt` must contain every key the run needs (provider, model,
  api_key_env, token limits, etc.) — but it is a complete, self-contained
  config.
- `/limits provider` writes to the local `CONFIG_PATH`, never the global.
- The `.env` file lives next to `CONFIG_PATH` (`ENV_FILE_PATH =
  CONFIG_PATH.parent / ".env"`, `engine.py:246`), so API keys stay local too.
- The global `config.txt` is never read, never written, never mutated.

### What's NOT changing

- The `AGENT8088_CONFIG` env var stays the highest priority.
- The global config resolution chain is unchanged when no CWD `config.txt`
  exists — backward compatible.
- `load_simple_config`, `update_simple_config`, and `ENV_FILE_PATH` all derive
  from `CONFIG_PATH`, so they automatically follow the CWD file.

## Design

### Resolution order (each setting resolves independently, on every turn)

`context_window`:
```
1. provider.<name>.context_window  (config.txt / PROVIDERS dict — highest priority)
2. Best-effort endpoint probe       (/v1/models — session-only, not persisted)
3. Global context_window            (flat config key, default 32768)
```

`max_completion_tokens` (no probe — this is an output budget, not a model
capability an endpoint reports):
```
1. provider.<name>.max_completion_tokens  (config.txt / PROVIDERS dict)
2. Global max_completion_tokens           (flat config key, default 8192)
```

Both values are clamped: `max_completion_tokens` is clamped to the resolved
`context_window` (an output budget cannot exceed the model's context).

### Config format

Uses the existing `provider.<name>.<field>` pattern. The parser
(`load_providers`, `engine.py:1318-1352`) already stores arbitrary fields via
`split(".", 2)` — no parser change needed.

```ini
# Per-provider token limits — auto-applied on /model switch
provider.glm.context_window=1048576
provider.glm.max_completion_tokens=32768

provider.custom.context_window=131072
provider.custom.max_completion_tokens=16384
```

### New helpers in engine.py

Two resolution functions that read the active provider first, fall back to the
flat global. Modeled on how `load_subagent_specs` reads
`APP_CONFIG.get(f"subagent_max_turns.{name}")` before falling back to frontmatter
(`engine.py:2255-2259`).

```python
def _active_context_window() -> int:
    """Context window for the active provider, falling back to the global."""
    if ACTIVE_PROVIDER and ACTIVE_PROVIDER in PROVIDERS:
        v = PROVIDERS[ACTIVE_PROVIDER].get("context_window")
        if v:
            try:
                return int(v)
            except (TypeError, ValueError):
                pass
    return CONTEXT_WINDOW  # global fallback


def _active_max_completion_tokens() -> int:
    """Max completion tokens for the active provider, clamped to its context window."""
    cw = _active_context_window()
    if ACTIVE_PROVIDER and ACTIVE_PROVIDER in PROVIDERS:
        v = PROVIDERS[ACTIVE_PROVIDER].get("max_completion_tokens")
        if v:
            try:
                return max(1, min(int(v), cw))
            except (TypeError, ValueError):
                pass
    return max(1, min(MAX_COMPLETION_TOKENS, cw))
```

### Consumption sites (two in engine.py)

**Site 1 — `_create_completion_with_fallback` (line 1649):**

Before:
```python
max_tokens = max_tokens if max_tokens is not None else MAX_COMPLETION_TOKENS
```

After:
```python
max_tokens = max_tokens if max_tokens is not None else _active_max_completion_tokens()
```

**Site 2 — turn loop (lines 7125-7127):**

Before:
```python
turn_max_tokens = (
    min(MAX_COMPLETION_TOKENS * 2, CONTEXT_WINDOW)
    if length_retries else MAX_COMPLETION_TOKENS
)
```

After:
```python
_mct = _active_max_completion_tokens()
_cw = _active_context_window()
turn_max_tokens = (
    min(_mct * 2, _cw)
    if length_retries else _mct
)
```

### `activate_model` hook (engine.py:1425-1457)

When `/model` switches providers, `activate_model` already mutates
`ACTIVE_PROVIDER` and `MODEL_NAME`. No new globals are needed — the resolution
helpers read `PROVIDERS[ACTIVE_PROVIDER]` live, so switching providers
automatically changes the resolved limits on the next turn. No change to
`activate_model` itself is required for the resolution path.

One addition: after the switch, print the resolved limits so the user knows
what changed (in `cmd_model` / `cmd_models` in cli.py, near the existing
confirmation line).

### Best-effort endpoint probe

When switching to a provider with no `context_window` set in config.txt or the
PROVIDERS dict, try `client.models.list()` and inspect the returned model
objects for a non-standard `context_window` / `max_context_length` /
`max_input_tokens` field. Most OpenAI-compatible endpoints do not report this,
so the probe is best-effort:

- On hit: store the discovered value in `PROVIDERS[name]["context_window"]` for
  the current session only (not persisted to config.txt — the user can persist
  it with `/limits provider <name> context_window <value>` if they want it to
  stick across restarts). The session-only value is overwritten by any
  config.txt value on next startup.
- On miss: fall back to the global default and print a one-line hint:

```
Switched to custom:ornith-1.0-35b. Context: 32768 (default — set provider.custom.context_window to override).
```

The probe runs inside `activate_model` (or a helper called from it), wrapped in
a try/except with a short timeout (reuse `MODEL_LIST_TIMEOUT_SECONDS = 5` from
`providers.py:46`). It never blocks or fails the model switch.

### Live override: `/limits provider` branch

New branch in `cmd_limits` (cli.py:3825-3861), modeled on the existing
`subagent` branch (cli.py:3833-3838):

```
/limits provider glm max_completion_tokens 65536
/limits provider custom context_window 262144
```

New setter in engine.py, modeled on `set_subagent_turns` (engine.py:442-456):

```python
def set_provider_limit(provider: str, key: str, value: str) -> dict:
    if provider not in PROVIDERS:
        raise KeyError(provider)
    if key not in ("context_window", "max_completion_tokens"):
        raise ValueError(f"unknown provider limit: {key}")
    new = int(value)
    if new < 1:
        raise ValueError("must be >= 1")
    old = PROVIDERS[provider].get(key)
    PROVIDERS[provider][key] = str(new)
    config_key = f"provider.{provider}.{key}"
    APP_CONFIG[config_key] = str(new)
    update_simple_config(CONFIG_PATH, {config_key: new})
    return {"key": config_key, "old": old, "new": new, "provider": provider}
```

The `/limits provider` branch in `cmd_limits`:

```python
if parts[0] == "provider":
    if len(parts) != 4:
        console.print("[red]usage:[/red] /limits provider <name> <key> <value>")
        return
    _, name, key, value = parts
    try:
        _report_limit_change(A.set_provider_limit(name, key, value))
    except (KeyError, ValueError) as e:
        console.print(f"[red]error:[/red] {e}")
    return
```

### `/limits` display

`_show_limits` (cli.py:3539-3557) gains a "Provider limits" section listing each
provider in `PROVIDERS` that has `context_window` or `max_completion_tokens`
set, with the values. Providers with no overrides are not shown.

### What's NOT changing

- No hardcoded model list (user explicitly rejected; matches Hermes).
- Flat global `context_window` / `max_completion_tokens` keys still work as
  the fallback for providers with no per-provider override.
- `LIMIT_SPECS` unchanged — the per-provider path uses its own setter, not
  `set_limit`.
- Existing `provider.<name>.{model,base_url,api_key,...}` fields untouched.
- `/limits` flat-key path (`/limits max_turn_tokens 5000`) unchanged.
- No change to `temperature` (session-scoped) or `max_turns` (global).

## Files touched

| File | Change |
|---|---|
| `engine.py` | Add `_active_context_window()`, `_active_max_completion_tokens()`, `set_provider_limit()`; update 2 consumption sites (lines 1649, 7125-7127); add best-effort probe call in `activate_model` path; add CWD `config.txt` branch in config path resolution (lines 232-243) |
| `cli.py` | Add `/limits provider <name> <key> <value>` branch in `cmd_limits`; extend `_show_limits` with a provider-limits section; print resolved limits after `/model` switch |
| `config.txt` | Document `provider.<name>.context_window` and `provider.<name>.max_completion_tokens` with examples in the commented config template |

## Testing

- Unit test: `set_provider_limit` writes to `PROVIDERS`, `APP_CONFIG`, and
  config.txt; reads back correctly.
- Unit test: `_active_context_window()` and `_active_max_completion_tokens()`
  return per-provider value when set, global fallback when not, clamped to
  context window.
- Unit test: `activate_model` switch changes the resolved limits (provider A
  has 1M/32K, provider B has none → switching A→B falls back to global).
- Unit test: CWD `config.txt` is selected exclusively when present; global
  config is untouched (no read, no write). `CONFIG_PATH` points to CWD, and
  `ENV_FILE_PATH` resolves to CWD's `.env`.
- Integration: manual `/model` switch with per-provider config produces the
  hint line with the resolved context window.
- Integration: `/limits provider <name> <key> <value>` persists and takes
  effect on the next turn without restart.
- Integration: running from a directory with a local `config.txt` uses local
  per-provider limits; `/limits provider` writes to the local file, not the
  global.

## Usage after the change

```ini
# In config.txt — once, per provider:
provider.glm.context_window=1048576
provider.glm.max_completion_tokens=32768
```

```
# Inside the agent:
/model glm:glm-5.2
# → Switched to glm:glm-5.2. Context: 1048576, Max completion: 32768

/limits provider glm max_completion_tokens 65536
# → Persisted to config.txt

/model custom:ornith-1.0-35b
# → Switched to custom:ornith-1.0-35b. Context: 32768 (default — set provider.custom.context_window to override)
```

## Open questions

None — all resolved during brainstorming.

## Design decisions log

1. **Both auto-read + manual override** (not one or the other) — user chose
   "Both — auto-read + manual override".
2. **Only `context_window` and `max_completion_tokens`** — not `temperature` or
   `max_turns` (user selected these two).
3. **No hardcoded model list** — user explicitly said "no prebuild list, check
   how hermes agent have their setup". Hermes declares per-model
   `context_length` in provider config, not a builtin dict.
4. **Best-effort probe included** — user chose "Best-effort probe" over
   "No probe, hint-only".
5. **CWD config.txt exclusive if present** — user chose "CWD exclusive if
   present, global untouched" over merge or separate-override-file. No merge
   logic; the two files never interact. `/limits provider` writes stay local
   when a CWD config exists.