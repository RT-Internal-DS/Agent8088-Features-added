# Discord Gateway Adapter — Design

**Date:** 2026-08-04
**Status:** Accepted
**Deciders:** T. Imam

## Context

The Agent8088 gateway currently supports Slack (pure-Python `slack-bolt` Socket Mode) and WhatsApp (Python adapter + Node.js Baileys bridge). Discord is the next messaging platform to add, following the same adapter pattern.

Reference implementations: Hermes Agent and OpenClaw both support Discord as a first-class channel with DMs, @mentions, guild channels, threads, and reactions.

## Decision

Add a Discord adapter using `discord.py` (the official Python library) following the same pure-Python pattern as the Slack adapter. No Node.js bridge needed — `discord.py` handles the WebSocket gateway natively.

**Scope:** DMs + @mentions in guild channels (matches Slack adapter scope).

## Architecture

### New files

| File | Purpose |
|---|---|
| `src/agent8088/gateway/platforms/discord.py` | `DiscordAdapter`, `DiscordStreamSink`, `markdown_to_discord()` |
| `tests/gateway/platforms/test_discord.py` | Unit tests |

### Modified files

| File | Change |
|---|---|
| `src/agent8088/gateway/runner.py` | Register Discord in `build_runner()` |
| `src/agent8088/gateway/auth.py` | Add `discord_allowed_users` to `Allowlist.from_config()` |
| `src/agent8088/cli.py` | Add Discord step to `--gateway-setup` wizard |
| `pyproject.toml` | Add `discord.py>=2.3.0,<3` to gateway extras |

### Config keys

| Key | Type | Purpose |
|---|---|---|
| `discord_enabled` | `0`/`1` | Enable/disable Discord adapter |
| `discord_bot_token` | string | Bot token from Discord Developer Portal |
| `discord_allowed_users` | comma-separated IDs or `*` | Allowlist for inbound messages |

### Adapter class

```python
class DiscordAdapter(BaseChannelAdapter):
    platform = "discord"

    def __init__(self, config: dict, runner: GatewayRunner):
        self._token = config["discord_bot_token"]
        self._runner = runner
        self._client: discord.Client | None = None
        self._stream_sinks: dict[str, DiscordStreamSink] = {}

    async def connect(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        self._client.event(self._on_ready)
        self._client.event(self._on_message)
        await self._client.start(self._token)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.close()

    async def _on_ready(self): ...
    async def _on_message(self, message: discord.Message): ...
    async def send_message(self, chat_id: str, text: str, **meta) -> str: ...
    async def edit_message(self, chat_id: str, msg_id: str, text: str) -> None: ...
```

### Message flow

1. `_on_message` fires → skip self-messages, skip non-DM/non-@mention in guilds
2. Normalize to `MessageEvent(platform="discord", chat_id=str(channel.id), chat_type="private"|"channel", user_id=str(author.id), text=content)`
3. Call `self._runner.on_message(event)` → engine processes → calls `adapter.send_message(chat_id, text)`
4. `send_message` chunks at 2000 chars (Discord limit), sends via `channel.send()`
5. Streaming: `DiscordStreamSink` calls `edit_message` on each delta (same pattern as Slack)

### markdown_to_discord()

Convert `**bold**` → `**bold**` (same), `*italic*` → `*italic*` (same), code blocks → triple backticks, strip unsupported formatting (headings, tables, links).

### Registration (runner.py)

```python
if config.get("discord_enabled", "0") in ("1", "true", "True"):
    try:
        from agent8088.gateway.platforms.discord import DiscordAdapter
        runner.register_adapter(DiscordAdapter(config, runner))
    except ImportError:
        log.warning("Discord enabled but discord.py not installed. "
                    "Install with: pip install agent8088[gateway]")
```

## Error handling

- Missing `discord_bot_token` → warning on startup, adapter skipped
- `discord.py` not installed → warning with install command, adapter skipped
- Discord connection lost → `discord.py` auto-reconnects; on 5 consecutive failures, log warning
- Message > 2000 chars → chunk at paragraph boundaries
- Rate limited → `discord.py` handles retry internally
- Self-messages → skip (echo loop prevention)
- Missing `message_content` intent → log warning at startup

## Testing

- Mock `discord.Client` with `AsyncMock` (same pattern as Slack tests)
- Test `markdown_to_discord()` conversion
- Test `MessageEvent` normalization from `discord.Message`
- Test `build_runner()` registration with `discord_enabled=1`
- Test allowlist filtering via `discord_allowed_users`
- Test chunking at 2000 char boundary

## Dependencies

- `discord.py>=2.3.0,<3` added to `gateway` extras in `pyproject.toml`

## Consequences

- **+** Pure Python, no bridge process needed (unlike WhatsApp)
- **+** Follows existing Slack adapter pattern exactly — easy to review and maintain
- **+** `discord.py` is the official, well-maintained library
- **-** Adds ~1MB to gateway install size (discord.py + aiohttp)
- **-** Discord requires `message_content` privileged intent to be enabled in the Developer Portal
