# Subagent Model Routing & Dynamic Markdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable Claude Code-style subagent model routing and dynamic markdown subagent management in Agent8088, allowing subagents to specify model tiers (`haiku`, `flash`, `pro`) or exact model IDs in their YAML frontmatter, dynamically creating subagents via chat, and executing them on isolated scoped clients.

**Architecture:** Extend `providers.py` with generic `MODEL_TIERS` and `resolve_subagent_target()` helper; update `engine.py` to parse `model` and `provider` from `.md` frontmatter, dynamically reload `SUBAGENT_SPECS` on delegation, and thread scoped `(client, provider_name, model_name)` through `run_agent()` and `_run_agent_loop()`.

**Tech Stack:** Python 3.11+, Pytest, OpenAI SDK compatible clients, YAML frontmatter parsing.

## Global Constraints

- Must maintain 100% backward compatibility with existing subagents and commands.
- `inherit` or missing model field must default cleanly to the active parent conversation's model and provider.
- Subagent model execution must never mutate or leak into the global `ACTIVE_PROVIDER` or `MODEL_NAME`.
- All tests in `tests/` must pass cleanly.

---

### Task 1: Provider Model Tier Matrix & Subagent Target Resolver

**Files:**
- Modify: `src/agent8088/providers.py`
- Test: `tests/test_subagent_model_routing.py`

**Interfaces:**
- Produces: `MODEL_TIERS: dict[str, dict[str, str]]`
- Produces: `resolve_subagent_target(raw_model_str: str, default_provider: str) -> tuple[str, str]`

- [ ] **Step 1: Write failing tests for model tier resolution**

```python
# tests/test_subagent_model_routing.py
import pytest
from agent8088.providers import resolve_subagent_target, MODEL_TIERS

def test_resolve_subagent_target_tier_aliases():
    # Anthropic tier mappings
    prov, model = resolve_subagent_target("haiku", "anthropic")
    assert prov == "anthropic"
    assert model == "claude-haiku-3.5"

    prov, model = resolve_subagent_target("sonnet", "anthropic")
    assert prov == "anthropic"
    assert model == "claude-sonnet-4-6"

    # Gemini tier mappings
    prov, model = resolve_subagent_target("flash", "gemini")
    assert prov == "gemini"
    assert model == "gemini-2.0-flash"

    prov, model = resolve_subagent_target("pro", "gemini")
    assert prov == "gemini"
    assert model == "gemini-2.5-pro"

def test_resolve_subagent_target_inherit_and_empty():
    prov, model = resolve_subagent_target("inherit", "anthropic")
    assert prov == "anthropic"
    assert model == ""

    prov, model = resolve_subagent_target("", "gemini")
    assert prov == "gemini"
    assert model == ""

def test_resolve_subagent_target_cross_provider():
    prov, model = resolve_subagent_target("gemini:gemini-2.0-flash", "anthropic")
    assert prov == "gemini"
    assert model == "gemini-2.0-flash"

    prov, model = resolve_subagent_target("gemini:flash", "anthropic")
    assert prov == "gemini"
    assert model == "gemini-2.0-flash"

def test_resolve_subagent_target_explicit_model_id():
    prov, model = resolve_subagent_target("custom-model-id", "openai")
    assert prov == "openai"
    assert model == "custom-model-id"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_subagent_model_routing.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_subagent_target' from 'agent8088.providers'`

- [ ] **Step 3: Implement `MODEL_TIERS` and `resolve_subagent_target` in `src/agent8088/providers.py`**

```python
MODEL_TIERS = {
    "anthropic": {
        "flash_lite": "claude-haiku-3.5",
        "haiku": "claude-haiku-3.5",
        "flash": "claude-haiku-3.5",
        "sonnet": "claude-sonnet-4-6",
        "pro": "claude-sonnet-4-6",
        "opus": "claude-opus-5",
    },
    "gemini": {
        "flash_lite": "gemini-2.0-flash-lite",
        "haiku": "gemini-2.0-flash",
        "flash": "gemini-2.0-flash",
        "sonnet": "gemini-2.5-pro",
        "pro": "gemini-2.5-pro",
    },
    "openai": {
        "flash_lite": "gpt-4o-mini",
        "haiku": "gpt-4o-mini",
        "flash": "gpt-4o-mini",
        "sonnet": "gpt-4o",
        "pro": "gpt-4o",
    },
    "deepseek": {
        "flash_lite": "deepseek-chat",
        "haiku": "deepseek-chat",
        "flash": "deepseek-chat",
        "pro": "deepseek-reasoner",
    },
    "ollama": {
        "flash_lite": "qwen14b-tooluse-v3",
        "haiku": "qwen14b-tooluse-v3",
        "flash": "qwen14b-tooluse-v3",
        "pro": "qwen2.5-coder:32b",
    },
    "ollama-cloud": {
        "flash_lite": "qwen3:14b",
        "haiku": "qwen3:14b",
        "flash": "qwen3:14b",
        "pro": "gpt-oss:120b",
    },
    "groq": {
        "flash_lite": "llama-3.1-8b-instant",
        "haiku": "llama-3.1-8b-instant",
        "flash": "llama-3.1-8b-instant",
        "pro": "llama-3.3-70b-versatile",
        "sonnet": "llama-3.3-70b-versatile",
    },
}

def resolve_subagent_target(raw_model_str: str, default_provider: str) -> tuple[str, str]:
    """Parse raw model string into (provider_name, model_name)."""
    if not raw_model_str or raw_model_str.strip().lower() in ("inherit", ""):
        return default_provider, ""
    raw = raw_model_str.strip()
    provider = default_provider
    model_or_tier = raw
    if ":" in raw:
        prefix, _, rest = raw.partition(":")
        if prefix.strip() in BUILTIN_PROVIDERS:
            provider = prefix.strip()
            model_or_tier = rest.strip()
    provider_tiers = MODEL_TIERS.get(provider, {})
    resolved_model = provider_tiers.get(model_or_tier.lower(), model_or_tier)
    return provider, resolved_model
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_subagent_model_routing.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent8088/providers.py tests/test_subagent_model_routing.py
git commit -m "feat: add model tiers and resolve_subagent_target to providers"
```

---

### Task 2: Subagent Frontmatter Parsing & Dynamic Reloading

**Files:**
- Modify: `src/agent8088/engine.py`
- Test: `tests/test_subagent_model_routing.py`

**Interfaces:**
- Consumes: `resolve_subagent_target` from `agent8088.providers`
- Modifies: `load_subagent_specs(agents_dir: Path) -> dict`
- Modifies: `_exec_subagent(args: dict, depth: int = 0) -> str`

- [ ] **Step 1: Write test for frontmatter parsing and dynamic reloading**

```python
# In tests/test_subagent_model_routing.py
from pathlib import Path
from agent8088.engine import load_subagent_specs

def test_load_subagent_specs_parses_model(tmp_path):
    agent_file = tmp_path / "fast-explorer.md"
    agent_file.write_text("""---
name: fast-explorer
description: Fast explorer agent
tools: read_text, execute_shell
max_turns: 5
model: haiku
---
Fast explorer prompt
""", encoding="utf-8")

    specs = load_subagent_specs(tmp_path)
    assert "fast-explorer" in specs
    assert specs["fast-explorer"]["model"] == "haiku"
    assert specs["fast-explorer"]["max_turns"] == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_subagent_model_routing.py::test_load_subagent_specs_parses_model -v`
Expected: FAIL with `KeyError: 'model'`

- [ ] **Step 3: Update `load_subagent_specs` in `src/agent8088/engine.py`**

Include `"model": meta.get("model", "").strip()` and `"provider": meta.get("provider", "").strip()` in the returned profile dictionary.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_subagent_model_routing.py::test_load_subagent_specs_parses_model -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_subagent_model_routing.py
git commit -m "feat: parse model and provider in load_subagent_specs"
```

---

### Task 3: Scoped Subagent Model Execution

**Files:**
- Modify: `src/agent8088/engine.py`
- Test: `tests/test_subagent_model_routing.py`

**Interfaces:**
- Updates: `run_agent(..., client=None, provider_name=None, model_name=None)`
- Updates: `_run_agent_loop(..., client=None, provider_name=None, model_name=None)`
- Updates: `_exec_subagent(args: dict, depth: int = 0)`

- [ ] **Step 1: Write test for isolated model execution in subagents**

```python
# In tests/test_subagent_model_routing.py
from unittest.mock import patch, MagicMock
from agent8088.engine import _exec_subagent, SUBAGENT_SPECS

def test_exec_subagent_uses_scoped_client_and_model(tmp_path):
    test_specs = {
        "custom_haiku": {
            "name": "custom_haiku",
            "description": "Test subagent",
            "tools": ["read_text"],
            "max_turns": 3,
            "permission": "readonly",
            "model": "haiku",
            "system_prompt": "You are a test subagent",
        }
    }
    with patch("agent8088.engine.SUBAGENT_SPECS", test_specs), \
         patch("agent8088.engine.load_subagent_specs", return_value=test_specs), \
         patch("agent8088.engine.run_agent") as mock_run:
        mock_run.return_value = "Subagent task completed"
        res = _exec_subagent({"agent_type": "custom_haiku", "task": "Check code"})
        assert "Subagent task completed" in res
        assert mock_run.called
        kwargs = mock_run.call_args.kwargs
        assert kwargs.get("model_name") == "claude-haiku-3.5" or "haiku" in kwargs.get("model_name", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_subagent_model_routing.py::test_exec_subagent_uses_scoped_client_and_model -v`
Expected: FAIL

- [ ] **Step 3: Update `_exec_subagent`, `run_agent`, and `_run_agent_loop` in `src/agent8088/engine.py`**

1. In `_exec_subagent`:
   - Call `global SUBAGENT_SPECS; SUBAGENT_SPECS = load_subagent_specs(AGENTS_DIR)` to reload specs dynamically.
   - Resolve subagent provider and model using `resolve_subagent_target(profile.get("model", ""), ACTIVE_PROVIDER or DEFAULT_PROVIDER)`.
   - Call `sub_client, default_m = get_client(sub_provider)`.
   - Pass `client=sub_client, provider_name=sub_provider, model_name=sub_model or default_m` to `run_agent()`.
2. In `run_agent` and `_run_agent_loop`:
   - Accept `client=None, provider_name=None, model_name=None` and forward to `_create_completion_with_fallback`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_subagent_model_routing.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_subagent_model_routing.py
git commit -m "feat: route subagent execution through scoped client and model"
```

---

### Task 4: Update Built-in Subagent Profiles & Verify End-to-End Suite

**Files:**
- Modify: `src/agent8088/agents/explore.md`
- Modify: `src/agent8088/agents/researcher.md`
- Modify: `src/agent8088/agents/coder.md`
- Modify: `src/agent8088/agents/auditor.md`
- Modify: `src/agent8088/agents/general-purpose.md`
- Run: Full test suite

- [ ] **Step 1: Update Markdown frontmatter in built-in subagents**

Set `model: haiku` in `explore.md`, `model: flash` in `researcher.md`, and `model: inherit` in `coder.md`, `auditor.md`, `general-purpose.md`.

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/`
Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add src/agent8088/agents/
git commit -m "feat: configure model tiers in default subagent markdown profiles"
```
