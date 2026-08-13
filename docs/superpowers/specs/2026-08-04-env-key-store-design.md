# Separate .env Key Store + Masked Hints — Design

**Date:** 2026-08-04
**Status:** Accepted
**Deciders:** T. Imam

## Context

Agent8088 stores API keys and gateway tokens as plaintext in `config.txt`. When re-running `--model-setup` or `--gateway-setup`, secret prompts show no hint that a key is already saved, so users think they need to re-enter it. Gateway tokens are written via raw `write_text` (no ACL protection), unlike model keys which use `_write_private_text`.

Hermes Agent uses a separate `~/.hermes/.env` file (0600) for API keys, with `config.yaml` holding only model/provider settings. OpenClaw uses a SQLite auth-profile store. Both persist keys across model switches without re-entry.

## Decision

Move all secrets (API keys + gateway tokens) to a separate `~/.agent8088/.env` file with 0600 perms. `config.txt` keeps only `*_env=VARNAME` references. Add masked-hint UI so users see existing keys are saved.

## Storage layout

**`~/.agent8088/.env`** (new, 0600 perms):
```
OPENROUTER_API_KEY=sk-or-v1-1234...
OPENAI_API_KEY=sk-proj-5678...
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
DISCORD_BOT_TOKEN=MjQ...
```

**`~/.agent8088/config.txt`** (no secrets, just settings):
```
default_provider=openrouter
provider.openrouter.model=qwen14b-tooluse-v3
provider.openrouter.base_url=https://openrouter.ai/api/v1
provider.openrouter.api_key_env=OPENROUTER_API_KEY
slack_enabled=1
slack_bot_token_env=SLACK_BOT_TOKEN
discord_enabled=1
discord_bot_token_env=DISCORD_BOT_TOKEN
gateway_permission_mode=readonly
```

## Key resolution precedence

```
1. ~/.agent8088/.env (highest)
2. os.environ (existing env vars still work)
3. config.txt provider.<name>.api_key (legacy fallback)
```

## Changes

| File | Change |
|---|---|
| `engine.py` | `load_env_file()`, `update_env_file()` (0600), `_migrate_keys_to_env()`, update `_provider_api_key()` |
| `cli.py` | `_custom_prompt()` masked hint, `_run_setup()` writes to .env, `_run_gateway_setup()` writes to .env + ACL fix |
| `tests/` | env file load/save, migration, masked hint |

## Migration

On first load, if `.env` doesn't exist but config.txt has `provider.*.api_key` or `*_token` keys:
1. Extract to `.env` with standard env var names
2. Replace in config.txt with `*_env=VARNAME`
3. Write `.env` with 0600 perms
4. Log: "Migrated N keys to ~/.agent8088/.env"

## Masking format

```
sk-or-v1-1234567890abcdef  →  sk-...cdef
xoxb-1234567890-1234       →  xox-...1234
< 4 chars                  →  (set, too short to mask)
empty                       →  (not set yet)
```

## Consequences

- **+** Secrets separated from settings — switching models never touches keys
- **+** 0600 perms on .env (consistent with Hermes)
- **+** Users see existing keys are saved (masked hint)
- **+** Gateway tokens get ACL protection (fixes existing inconsistency)
- **-** One-time migration on startup (transparent, logged)
- **-** Two files to manage instead of one