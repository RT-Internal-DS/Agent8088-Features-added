# Subagent Model Routing & Dynamic Markdown Specification

**Date:** 2026-08-27  
**Status:** Approved  

## 1. Problem & Objectives

Agent8088 currently discovers subagents from Markdown files in `src/agent8088/agents/*.md` and executes them via `_exec_subagent` in an isolated tool loop. However, the execution currently relies on the global session model and provider (`ACTIVE_PROVIDER` / `MODEL_NAME`).

### Objectives
1. **Model-Aware Subagent Definitions:** Allow subagent markdown files to declare their own `model:` in YAML frontmatter (Claude Code style).
2. **Generic Tier Aliases & Explicit IDs:** Support aliases (`haiku`, `flash`, `flash_lite`, `sonnet`, `pro`, `opus`, `inherit`) that auto-map to the active provider's models, while also accepting explicit model IDs (e.g., `gemini-2.0-flash`, `deepseek-chat`, `gpt-4o-mini`) and cross-provider prefixes (`provider:model`).
3. **Dynamic Markdown Management by Main Agent:** Allow the main agent to create and update subagent `.md` files dynamically using standard file tools (`write_file`), with immediate re-scan on delegation.
4. **Isolated Model Execution:** Subagents run on their scoped client/model instance without mutating the parent conversation's model or active provider state.

---

## 2. Architecture & Design

```
                     ┌──────────────────────────────────────────────┐
                     │          Parent Conversation Loop           │
                     │          (e.g., Anthropic Sonnet)            │
                     └──────────────────────┬───────────────────────┘
                                            │
                           calls spawn_subagent(type="explore")
                                            │
                                            ▼
                     ┌──────────────────────────────────────────────┐
                     │            _exec_subagent (engine.py)        │
                     │  1. Re-scans AGENTS_DIR/*.md                 │
                     │  2. Reads explore.md frontmatter             │
                     │     -> model: "haiku"                        │
                     │  3. Resolves to "claude-haiku-3.5"           │
                     │  4. Gets scoped client for subagent          │
                     └──────────────────────┬───────────────────────┘
                                            │
                                            ▼
                     ┌──────────────────────────────────────────────┐
                     │           Subagent run_agent Loop            │
                     │        - Uses Scoped Subagent Client         │
                     │        - Model: "claude-haiku-3.5"           │
                     │        - Isolated Context & Budget           │
                     └──────────────────────┬───────────────────────┘
                                            │
                                            ▼ (Report Only)
                     ┌──────────────────────────────────────────────┐
                     │   Parent Session Context Window Preserved    │
                     └──────────────────────────────────────────────┘
```

---

## 3. Detailed Component Specifications

### 3.1 Model Tier Matrix (`src/agent8088/providers.py`)

Define provider-specific model tier resolution mappings and resolver helper:

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
    if not raw_model_str or raw_model_str.strip().lower() in ("inherit", ""):
        return default_provider, ""
    raw = raw_model_str.strip()
    provider = default_provider
    model_or_tier = raw
    if ":" in raw:
        prefix, _, rest = raw.partition(":")
        if prefix.strip() in BUILTIN_PROVIDERS or prefix.strip() in PROVIDERS:
            provider = prefix.strip()
            model_or_tier = rest.strip()
    provider_tiers = MODEL_TIERS.get(provider, {})
    resolved_model = provider_tiers.get(model_or_tier.lower(), model_or_tier)
    return provider, resolved_model
```

---

### 3.2 Subagent Frontmatter Parsing & Dynamic Reloading (`src/agent8088/engine.py`)

1. **`load_subagent_specs(agents_dir: Path)`**:
   - Extract `meta.get("model", "")` and `meta.get("provider", "")`.
   - Store in the specification dictionary for each profile.

2. **`_exec_subagent(args: dict, depth: int = 0)`**:
   - Call `load_subagent_specs(AGENTS_DIR)` at the start of delegation so any subagent file recently written/edited by the main agent via `write_file` is immediately loaded.
   - Extract `model` and `provider` from profile frontmatter (or tool arguments).
   - Use `resolve_subagent_target()` to compute `(sub_provider, sub_model)`.
   - Retrieve `sub_client, default_m = get_client(sub_provider)`.
   - Pass `sub_client`, `sub_provider`, and `sub_model or default_m` into `run_agent()`.

3. **`run_agent()` and `_run_agent_loop()`**:
   - Accept optional parameters: `client=None`, `provider_name=None`, `model_name=None`.
   - Forward them to `_create_completion_with_fallback()` and `create_completion()`.
   - If not passed, default to global `client`, `ACTIVE_PROVIDER`, and `MODEL_NAME`.

---

### 3.3 Default Built-in Subagent Profiles Updated

- **`explore.md`**: `model: haiku` (or fast/flash tier for fast codebase exploration).
- **`auditor.md`**: `model: inherit` (or `pro` / `sonnet` for deep scrutiny).
- **`coder.md`**: `model: inherit` (matches developer's primary coding model).
- **`researcher.md`**: `model: flash` (efficient research and summarization).
- **`general-purpose.md`**: `model: inherit`.

---

## 4. Verification & Testing Strategy

1. **Unit Tests (`tests/test_subagent_model_routing.py`):**
   - Test `resolve_subagent_target` across all tier aliases (`haiku`, `flash`, `pro`, `inherit`).
   - Test `provider:model` prefix parsing (e.g. `gemini:gemini-2.0-flash`).
   - Test `load_subagent_specs` reading `model:` from Markdown frontmatter.
   - Test dynamic reloading: simulate writing a new subagent `.md` file and verify `_exec_subagent` picks it up without restart.
2. **Integration Verification:**
   - Execute an end-to-end `spawn_subagent` invocation ensuring completion calls use the scoped model parameters and client without leaking into the global active model.
