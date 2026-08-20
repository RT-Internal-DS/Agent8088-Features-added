# Operational Logging for Agent8088

**Date:** 2026-08-20
**Status:** Approved design, pending implementation plan
**Modeled on:** Hermes Agent (`/nousresearch/hermes-agent`) and OpenClaw (`/openclaw/openclaw`) logging systems

## Problem

`_log = logging.getLogger("agent8088.engine")` (`src/agent8088/engine.py:24`) and every sibling subsystem logger — `agent8088.gateway`, `agent8088.memory`, `agent8088.mcp`, `agent8088.mcp_server`, `agent8088.gateway.platforms.*` — has **no configured sink**. The audit log's own docstring says so: *"`_log` goes to a logger with no configured sink"*. Operational signals evaporate:

- Sandbox startup failure warning — `engine.py:3636`
- Docker sandbox unavailable — `engine.py:3730`, `engine.py:4003`
- Turn-budget hit — `engine.py:6598`
- Model tool-call traces — `engine.py:6681`, `engine.py:6684`
- Gateway rate-limit — `gateway/runner.py:121`
- MCP teardown failures — `mcp.py:199`, `mcp.py:275`

The existing audit log (`engine.py:5621`) captures only **permission decisions** (`tool_call`, `escalation_requested`, `denied`/`allowed`). It is a record of what the agent was *permitted* to do, not of what the agent *did* or what happened to it while running. Both Hermes and OpenClaw have an operational log distinct from any audit trail; Agent8088 has the audit trail but not the operational log.

There is no `agent8088 logs` CLI command, no daily-rotating runtime file, no subsystem names on output, no follow/tail, and no redaction on operational records.

## Goal

Add operational logging modeled on Hermes and OpenClaw, closing the operability gap. v1 scope: a daily-rotating JSONL file, subsystem names derived from the existing logger hierarchy, redaction on every record, never-raise guarantee matching the audit log, and a `agent8088 --logs` CLI with follow/filter.

## Non-goals (out of v1)

- `--since 1h` / `--session ID` time and session filters (Hermes has them; the file is JSONL so `jq` covers ad-hoc needs)
- `hermes logs list` equivalent (file listing with sizes)
- `--json` / `--plain` / `--no-color` output styles (OpenClaw has them)
- `redactPatterns` config (OpenClaw has user-configurable redaction regexes; v1 reuses the existing `_redact_secrets`)
- RPC/remote tail (OpenClaw tails a running gateway over WebSocket; v1 reads the file directly)
- `/logs` REPL slash command (redundant with the flag; the REPL occupies the terminal so follow has nowhere to stream)
- Per-subsystem log files (Hermes uses `agent.log`, `gateway.log`, `errors.log`; v1 uses one file with a `--subsystem` filter — YAGNI for a single-user CLI)

## Background: how Hermes and OpenClaw do it

### Hermes Agent (`/nousresearch/hermes-agent`)

- Log files in `~/.hermes/logs/`: `agent.log` (general agent activity, API calls, tool dispatch), `errors.log` (warnings and errors), `gateway.log` (messaging gateway and platform connections), `gui.log` (dashboard/websocket), `desktop.log` (Electron app). Distinct per-subsystem files.
- CLI: `hermes logs [-f] [name] [-n N] [--level WARNING] [--since 1h] [--session abc123]`. `hermes logs list` shows files and sizes.
- Rotation: Python `RotatingFileHandler` → `agent.log.1`, `agent.log.2`.
- Verbosity: `hermes gateway run -vv` bumps stderr log level (0=WARNING, 1=INFO, 2+=DEBUG).
- Service: `journalctl --user -u hermes-gateway` for systemd deployments.

### OpenClaw (`/openclaw/openclaw`)

- One daily-rotating file: `openclaw-YYYY-MM-DD.log` in `%TEMP%/openclaw/` on Windows, `~/.openclaw/logs/` elsewhere. Daily rotation produces the file-per-date pattern.
- JSONL file logs with fields: `hostname`, `message`, `agent_id`, `session_id`, `channel`, plus the original structured log arguments.
- Subsystem-aware console formatter: `{"subsystem":"gateway"}`, `gateway/health-monitor`, `gateway/ws`, `gateway/heartbeat`, `cron`. Stable subsystem colors, TTY-aware, respects `NO_COLOR`.
- CLI: `openclaw logs --follow --interval 2000 --limit 500 --max-bytes 500000 --json --plain --no-color --utc --local-time`. Tails over **RPC** to the running Gateway (`ws://127.0.0.1:18789`); falls back to reading the file directly if the gateway is unreachable. Remote mode via `--url ws://... --token $OPENCLAW_GATEWAY_TOKEN`.
- Config in `openclaw.json`: `logging.level`, `logging.file`, `logging.consoleLevel`, `logging.consoleStyle: pretty|compact|json`, `logging.redactPatterns: ["sk-.*"]`.
- Console/file levels are independent; console formatter is `pretty | compact | json`.
- Linux: `--follow` tries the active user-systemd Gateway journal by PID, retrying the live gateway with backoff if needed.

### What Agent8088 already has (and reuses)

- **Subsystem logger hierarchy** already exists: `agent8088.engine`, `agent8088.gateway`, `agent8088.gateway.platforms.slack|whatsapp|discord|telegram|email`, `agent8088.gateway.auth`, `agent8088.memory`, `agent8088.mcp`, `agent8088.mcp_server`. No new logger names needed; wiring one handler on the `agent8088` parent logger makes every child emit for free via stdlib logger propagation.
- **`_redact_secrets`** (`engine.py:5378`) — scrubs known secret values from `APP_CONFIG` and `_SECRET_VALUES` to `[redacted]`. Already used by the audit log; the operational log reuses it on every record.
- **`_protect_private_file`** (`engine.py:30`) — sets mode 0600 on POSIX, applies a Windows ACL via `whoami.exe` on Windows. Already used by the audit log and the `.env` key store; the operational log reuses it on file creation.
- **`_agent_data_dir`** (`engine.py:3329`) — resolves `AGENT8088_HOME` → `LOCALAPPDATA\agent8088` on Windows → `~/.agent8088` elsewhere. The operational log lives in `<that>/logs/`.
- **`APP_CONFIG.get(...)` + `update_simple_config`** — the existing config-read/config-write pattern. New keys follow it.
- **Audit log never-raise guarantee** (`engine.py:5624`): *"a broken sink must never break an agent turn."* The operational log inherits the same contract.

## Design

### Architecture

One custom `DailyJsonlHandler` attached to the `agent8088` parent logger. Every existing child subsystem logger inherits via stdlib propagation — zero new logger names, zero renames. Records are JSONL: `{"ts": ISO8601 local+offset, "level": "INFO", "subsystem": "engine", "msg": "..."}`. The subsystem is the logger name with the `agent8088.` prefix stripped and `.` → `/` (so `agent8088.gateway.platforms.slack` → `gateway/platforms/slack`), matching OpenClaw's `{"subsystem":"gateway/health-monitor"}` shape.

- **File**: `~/.agent8088/logs/agent8088-YYYY-MM-DD.log` (JSONL), one file per local day. A small custom handler opens the date-named file and reopens a new dated file when the local date changes — this produces the date-in-active-filename pattern from the OpenClaw sample (`openclaw-2026-08-20.log`), which stdlib's `TimedRotatingFileHandler` does not (it keeps a base name and only suffixes on rotation). ~30 lines, still stdlib-`logging`-compatible (subclasses `logging.Handler`, uses `handleError` for the never-raise guarantee).
- **Path**: `<_agent_data_dir()>/logs/agent8088-%Y-%m-%d.log`. Respects `AGENT8088_HOME`.
- **Format**: one JSON object per line with `ts`, `level`, `subsystem`, `msg`. `ts` is `datetime.now(timezone.utc).astimezone().isoformat()` — local time with offset, matching the OpenClaw sample's `16:35:59+05:00` shape.
- **Redaction**: the `Formatter.format()` override calls `_redact_secrets(record.getMessage())` before JSON-encoding, so every record is scrubbed the same way audit entries are. One string scan per record, negligible at agent log volumes.
- **Level**: `INFO` default; configurable via `log_level`.
- **Default-on**: an operational log is a baseline expectation of any daemon-grade agent (both Hermes and OpenClaw write unconditionally; the CLI is the optional part). A `log_enabled=0` config key disables it for silent/embedded runs.
- **Mode 0600** on POSIX via `_protect_private_file`, applied on file creation.

### New file: `src/agent8088/logging_setup.py` (~80 lines)

`configure_logging()` — called once from `cli.main()` and from `gateway/__main__.py`. Idempotent (safe to call twice). Responsibilities:

1. Read `log_enabled` (default `1`), `log_level` (default `INFO`), `log_file` (default `<data_dir>/logs/agent8088-%Y-%m-%d.log`), `log_max_bytes` (default 10 MB) from `APP_CONFIG`.
2. If `log_enabled == "0"`, return early — no handler attached.
3. Resolve the log directory, `mkdir(parents=True, exist_ok=True)`.
4. Build a custom `DailyJsonlHandler` (subclass of `logging.Handler`):
   - `__init__(base_dir)`: store `base_dir`; open `base_dir / f"agent8088-{today}.log"` in append mode; call `_protect_private_file` on first creation.
   - `emit(record)`: compute today's date; if it differs from the open file's date, close and reopen a new dated file. Serialize the record as one JSON line — `{"ts": iso_local, "level": record.levelname, "subsystem": _subsystem(record.name), "msg": _redact_secrets(record.getMessage())}` — write it, flush. Wrap the whole body in `try/except` → `self.handleError(record)` (stdlib's non-raising error path).
   - `_subsystem(name)` → strip `agent8088.` prefix, replace `.` with `/`.
5. Attach to `logging.getLogger("agent8088")`, set level from `log_level`.
6. Wrap everything in `try/except`. On any failure (unwritable dir, permissions), log *once* to stderr and continue with no handler — the agent runs normally and `_log` calls are no-ops as they are today. **Never raises.**

The `log_max_bytes` config key is read and noted as a `ponytail:` ceiling comment: a runaway logger on a single-day burst could grow unbounded until the day changes. The custom handler could check size in `emit()` and roll, but v1 does not — agent8088 log volume is low (INFO-level agent events, not request-per-line HTTP traffic). Marked as a known ceiling with a clear upgrade path.

Also: `if __name__ == "__main__":` `demo()` that emits a few records at different levels from different subsystem loggers and prints the file path — the ponytail self-check, runnable without pytest.

### CLI: `agent8088 --logs` (in `src/agent8088/cli.py`)

The existing `main()` uses flat argparse flags (`--gateway`, `--setup`, `--mcp-serve`), not subcommands. The operational log follows the same convention.

**Flag**: `agent8088 --logs [follow] [-n N] [--level L] [--subsystem S] [--json]`

`--logs` takes one optional positional argument: `follow` (the only value). All other options are argparse flags.

- `--logs` alone — print the last 50 lines of today's file, formatted for console.
- `--logs follow` — print last 50, then poll the file every 1s for new lines (stdlib `time.sleep` in a loop; no new deps). Ctrl+C exits.
- `-n N` / `--limit N` — last N lines (default 50, like Hermes).
- `--level L` — filter by `DEBUG|INFO|WARNING|ERROR` (case-insensitive).
- `--subsystem S` — substring match on the `subsystem` field (`gateway`, `memory`, `engine`).
- `--json` — emit raw JSONL (for piping into `jq`); default is a human line: `12:05:15+05:00 info gateway/ws <message>` matching the OpenClaw sample shape.

Argparse wiring in `main()`: `parser.add_argument("--logs", nargs="?", const="tail", default=None)` to accept the optional `follow` positional, plus `-n`/`--limit`, `--level`, `--subsystem`, `--json` as flags gated on `args.logs is not None`. The dispatch branch in `main()` calls `cmd_logs(args)` and returns before the REPL starts.

**Implementation**: one new function `cmd_logs(args)` plus argparse wiring in `main()`. Reads the file directly (no RPC in v1). Follow mode re-stats the file for size and reads new bytes; on size shrink (rotation), prints `Log cursor reset (file rotated).` and re-opens — matches the OpenClaw sample's `Log cursor reset (file rotated).` behaviour verbatim.

`configure_logging()` is called at the top of `main()`, **before** the `--gateway` / `--mcp-serve` / REPL branches, so every mode logs to the same file. `gateway/__main__.py` replaces its `logging.basicConfig(...)` with `configure_logging()` so the gateway uses the same file sink instead of stderr-only.

### Config keys (existing `APP_CONFIG.get` pattern)

| Key | Default | Purpose |
|---|---|---|
| `log_enabled` | `1` | `0` disables the file handler entirely (silent/embedded runs) |
| `log_level` | `INFO` | `DEBUG\|INFO\|WARNING\|ERROR` — file handler level |
| `log_file` | `<_agent_data_dir()>/logs/agent8088-YYYY-MM-DD.log` | Override path; `agent8088 --setup` could expose this later, not in v1 |
| `log_max_bytes` | `10485760` (10 MB) | Safety-net size cap (see error handling) |

No `consoleStyle`, no `redactPatterns` (out of v1). Redaction reuses `_redact_secrets`.

### Error handling

The audit log's hard rule (`engine.py:5624`) — *"Never raises: a broken sink must not break the agent turn"* — applies identically to the operational log:

- `configure_logging()` wraps handler attachment in `try/except`. If the log dir is unwritable, it logs once to stderr and continues with no file handler; the agent runs normally, `_log` calls are no-ops as they are today.
- The handler's `emit()` relies on stdlib's `Handler.handleError(record)` (already non-raising). A full disk or a rotated-file rename failure never breaks a turn.
- `--logs` on a missing file: print `No log file at <path>. Run agent8088 to start logging.` and exit 1, not a traceback.
- `--logs follow` on rotation mid-follow: detect the size shrink, print `Log cursor reset (file rotated).`, re-open the new file, continue. Matches the OpenClaw sample.

**Rotation — daily + size safety.** The custom `DailyJsonlHandler` reopens a new dated file when the local date changes, producing the `agent8088-YYYY-MM-DD.log` pattern. The `log_max_bytes` config key is read but noted as a `ponytail:` ceiling comment: a runaway logger on a single-day burst could grow unbounded until the day changes. Upgrade path: a size check in `emit()` that rolls mid-day. Not needed for v1 — agent8088 log volume is low (INFO-level agent events, not request-per-line HTTP traffic). Marked as a known ceiling.

## Files touched

| File | Change | ~Lines |
|---|---|---|
| `src/agent8088/logging_setup.py` **(new)** | `configure_logging()` + custom `DailyJsonlHandler` + `_subsystem()` + `demo()` self-check | ~80 |
| `src/agent8088/cli.py` | Import + call `configure_logging()` at top of `main()`; add `--logs` argparse flag + `cmd_logs(args)` with print/follow/filter/rotation-detection | ~70 |
| `src/agent8088/gateway/__main__.py` | Replace `logging.basicConfig(...)` with `configure_logging()` | ~3 |
| `tests/test_operational_log.py` **(new)** | 6 handler/config/redaction/rotation tests | ~150 |
| `tests/test_logs_cli.py` **(new)** | 6 CLI tests including follow+rotation | ~100 |

## Testing

Per `AGENTS.md`: every change needs tests that fail if the logic breaks. Test contracts, not implementation. One behavior per test. Independent and order-free (use `tmp_path` and `monkeypatch`). Deterministic (no `time.sleep` in assertion paths; bounded poll for the follow test). Mock external services, not the code under test. Gate POSIX-specific assertions with `skipif`.

### `tests/test_operational_log.py`

1. `test_configure_logging_writes_jsonl_to_log_dir` — call `configure_logging()`, emit via `logging.getLogger("agent8088.engine").info("x")`, assert one JSONL line exists in the temp log dir with `subsystem == "engine"`, `level == "INFO"`, `msg == "x"`, parseable `ts`. *(Fails if handler not attached or JSON shape wrong.)*
2. `test_subsystem_name_strips_agent8088_prefix` — emit from `agent8088.gateway.platforms.slack`, assert `subsystem == "gateway/platforms/slack"`. *(Fails if prefix stripping or `.`→`/` broken.)*
3. `test_secrets_redacted_in_log_record` — set `_SECRET_VALUES=["sk-live-abcdef0123456789"]`, log a warning containing the secret, assert the file does not contain the secret and contains `[redacted]`. *(Fails if redaction skipped in the handler.)*
4. `test_log_enabled_zero_writes_no_file` — `monkeypatch` `APP_CONFIG["log_enabled"]="0"`, call `configure_logging()`, emit a record, assert no file created. *(Fails if the gate is missing.)*
5. `test_unwritable_log_dir_does_not_raise` — point `log_file` at an unwritable path, call `configure_logging()`, assert it returns normally and the `agent8088` logger has no file handler. *(Fails if the never-raise guarantee is broken — the audit-log parity test.)*
6. `test_daily_rotation_opens_new_dated_file` — seed the handler with a file for yesterday's date, emit a record, assert a *new* file with today's date was created and the record landed in it. *(Fails if day-change detection broken.)*

### `tests/test_logs_cli.py`

7. `test_logs_prints_last_n_lines` — seed a temp log file with 100 JSONL lines, run the `cmd_logs` path with `-n 10`, assert exactly the last 10 are printed in human format.
8. `test_logs_level_filter` — seed records at INFO and WARNING, run with `--level WARNING`, assert only WARNING lines printed.
9. `test_logs_subsystem_filter` — seed `engine` and `gateway` records, run with `--subsystem gateway`, assert only gateway lines printed.
10. `test_logs_follow_emits_new_lines` — seed 5 lines, start follow in a thread, append 3 more lines to the file, assert the 3 new lines appear (bounded poll, no `time.sleep` in assertion). *(Deterministic.)*
11. `test_logs_follow_handles_rotation` — start follow, move the file aside (simulate rotation), create a new file with 2 lines, assert `Log cursor reset (file rotated).` printed and the 2 new lines follow. *(Fails if rotation detection broken.)*
12. `test_logs_missing_file_exits_clean` — run `cmd_logs` against a nonexistent path, assert exit code 1 and the friendly message, no traceback.

No new deps, no mocks of the code under test. Mock the filesystem via `tmp_path` and `monkeypatch` on `APP_CONFIG` / `_agent_data_dir` per the existing `test_audit_log.py` pattern. The `--logs follow` thread test uses a bounded poll (not `time.sleep`) in the assertion path.

## Verification

- `pytest tests/test_operational_log.py tests/test_logs_cli.py -q` must pass.
- Full suite: `.venv\Scripts\python.exe -m pytest tests/ -q` must stay green (no regressions).
- Manual: `python -m agent8088.logging_setup` runs the `demo()` self-check and prints the log path; `type <path>` shows the JSONL.
- Manual: `agent8088 --logs follow` in one terminal, run an agent turn in another, observe lines streaming; simulate rotation by moving the file aside and confirm `Log cursor reset (file rotated).` appears and streaming resumes.