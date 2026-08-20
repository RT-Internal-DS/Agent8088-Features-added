# Operational Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add operational logging to Agent8088 — a daily-rotating JSONL file with subsystem names and a `agent8088 --logs` CLI — modeled on Hermes and OpenClaw, closing the gap that every `agent8088.*` logger currently has no configured sink.

**Architecture:** One custom `DailyJsonlHandler` attached to the `agent8088` parent logger; every existing child subsystem logger (`agent8088.engine`, `agent8088.gateway`, `agent8088.memory`, `agent8088.mcp`, `agent8088.gateway.platforms.*`) inherits via stdlib propagation. Records are JSONL with `ts`, `level`, `subsystem` (logger name minus the `agent8088.` prefix, `.`→`/`), and `msg` (passed through the existing `_redact_secrets`). A flat `agent8088 --logs [follow]` flag reads the file directly with filter/follow/rotation-detection.

**Tech Stack:** Python stdlib `logging` only. No new dependencies. pytest for tests.

## Global Constraints

- **No new dependencies.** Stdlib `logging` only — no `structlog`, no `loguru`, no `rich.logging`.
- **Never-raise guarantee:** a broken/unwritable log sink must degrade to a no-op and never break an agent turn (parity with the audit log contract at `engine.py:5624`).
- **Redaction on every record:** the handler calls `engine._redact_secrets(record.getMessage())` before serialising — reuse the existing function at `engine.py:5378`, do not reinvent.
- **File mode 0600 on POSIX:** reuse `engine._protect_private_file` at `engine.py:30` on first file creation.
- **Config pattern:** `APP_CONFIG.get("key", "default")` for reads; do not introduce a new config system.
- **Test isolation:** use `tmp_path` + `monkeypatch` on `APP_CONFIG` and `_agent_data_dir` per the `tests/test_audit_log.py` pattern. No `time.sleep` in assertion paths. Gate POSIX-only assertions with `@pytest.mark.skipif(os.name == "nt", ...)`.
- **Conventional commit shape:** `<type>: <imperative summary, <=50 chars>`.
- **Run tests with:** `.venv\Scripts\python.exe -m pytest tests/ -q` (Windows) or `python -m pytest tests/ -q` (POSIX). The repo has no hosted CI — local green is the gate.
- **Off-by-default for the *enabled* flag would be wrong** — both Hermes and OpenClaw write the operational log unconditionally; the CLI is the optional part. Default `log_enabled=1`. A user who wants silence sets `log_enabled=0`.
- **No comments unless requested** — but the `ponytail:` ceiling comment on `log_max_bytes` is required by the spec (it marks a deliberate, documented simplification).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/agent8088/logging_setup.py` **(new)** | `configure_logging()`, `DailyJsonlHandler`, `_subsystem()`, `demo()` self-check. One responsibility: wire the operational log sink. |
| `src/agent8088/cli.py` (modify) | Call `configure_logging()` at top of `main()`; add `--logs` flag + `cmd_logs(args)` with print/follow/filter/rotation-detection. |
| `src/agent8088/gateway/__main__.py` (modify) | Replace `logging.basicConfig(...)` with `configure_logging()` so the gateway uses the same file sink. |
| `tests/test_operational_log.py` **(new)** | 6 tests: handler attaches, subsystem name, redaction, disabled, unwritable-dir never-raise, day-change rotation. |
| `tests/test_logs_cli.py` **(new)** | 6 tests: print last N, level filter, subsystem filter, follow emits new lines, follow handles rotation, missing file exits clean. |

---

## Task 1: `DailyJsonlHandler` + `configure_logging()` in `logging_setup.py`

**Files:**
- Create: `src/agent8088/logging_setup.py`
- Test: `tests/test_operational_log.py`

**Interfaces:**
- Consumes: `agent8088.engine.APP_CONFIG` (dict-like, `.get(key, default)`), `agent8088.engine._redact_secrets(text) -> str` (`engine.py:5378`), `agent8088.engine._protect_private_file(path)` (`engine.py:30`), `agent8088.engine._agent_data_dir() -> Path` (`engine.py:3329`).
- Produces: `configure_logging() -> None` (idempotent, never raises); `DailyJsonlHandler(logging.Handler)`; `_subsystem(name: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_operational_log.py`:

```python
"""Operational log: a daily-rotating JSONL file with subsystem names.

The audit log (test_audit_log.py) records permission decisions. This file tests
the operational log that records what the agent *did* and what happened to it
while running — the sink that `_log = logging.getLogger("agent8088.engine")` and
every sibling subsystem logger currently lacks.
"""
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from agent8088 import engine as A
from agent8088 import logging_setup as L


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """Point _agent_data_dir at a temp dir so logs land in tmp_path/logs."""
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    # Reset the agent8088 parent logger between tests so handlers don't accumulate.
    parent = logging.getLogger("agent8088")
    parent.handlers = [h for h in parent.handlers if not isinstance(h, L.DailyJsonlHandler)]
    return tmp_path / "logs"


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _today_file(log_dir):
    return log_dir / f"agent8088-{datetime.now().astimezone().strftime('%Y-%m-%d')}.log"


def test_configure_logging_writes_jsonl_to_log_dir(log_dir):
    L.configure_logging()
    logging.getLogger("agent8088.engine").info("hello world")
    f = _today_file(log_dir)
    assert f.exists(), f"no log file at {f}"
    entries = _read_jsonl(f)
    assert len(entries) == 1
    e = entries[0]
    assert e["level"] == "INFO"
    assert e["subsystem"] == "engine"
    assert e["msg"] == "hello world"
    assert "ts" in e
    datetime.fromisoformat(e["ts"])  # raises if not ISO


def test_subsystem_name_strips_agent8088_prefix(log_dir):
    L.configure_logging()
    logging.getLogger("agent8088.gateway.platforms.slack").warning("connect")
    entries = _read_jsonl(_today_file(log_dir))
    assert entries[0]["subsystem"] == "gateway/platforms/slack"


def test_secrets_redacted_in_log_record(log_dir, monkeypatch):
    monkeypatch.setattr(A, "_SECRET_VALUES", ["sk-live-abcdef0123456789"])
    L.configure_logging()
    logging.getLogger("agent8088.engine").warning("got key=sk-live-abcdef0123456789")
    text = _today_file(log_dir).read_text(encoding="utf-8")
    assert "sk-live-abcdef0123456789" not in text
    assert "[redacted]" in text


def test_log_enabled_zero_writes_no_file(log_dir, monkeypatch):
    monkeypatch.setattr(A, "APP_CONFIG", {**A.APP_CONFIG, "log_enabled": "0"})
    parent = logging.getLogger("agent8088")
    parent.handlers = [h for h in parent.handlers if not isinstance(h, L.DailyJsonlHandler)]
    L.configure_logging()
    logging.getLogger("agent8088.engine").info("should vanish")
    assert not _today_file(log_dir).exists()


def test_unwritable_log_dir_does_not_raise(tmp_path, monkeypatch):
    """A broken sink must not break the agent turn (parity with audit log)."""
    # Point _agent_data_dir at a path whose logs/ subdir cannot be created.
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path / "readonly")
    # Make the parent read-only so mkdir fails. On Windows, chmod is advisory,
    # so also monkeypatch Path.mkdir to raise — the point is the never-raise,
    # not the specific failure mode.
    def _boom(*a, **kw):
        raise OSError("read-only filesystem")
    monkeypatch.setattr(Path, "mkdir", _boom)
    # Must not raise:
    L.configure_logging()
    # And the agent logger must have no DailyJsonlHandler attached:
    assert not any(isinstance(h, L.DailyJsonlHandler)
                   for h in logging.getLogger("agent8088").handlers)


def test_daily_rotation_opens_new_dated_file(log_dir, monkeypatch):
    """When the local date changes, the handler opens a new dated file."""
    L.configure_logging()
    # Emit a record for "today"
    logging.getLogger("agent8088.engine").info("day one")
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    # Force the handler to think it's tomorrow by patching datetime in the module
    tomorrow_dt = (datetime.now().astimezone() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    class _FakeDT:
        @staticmethod
        def now(tz=None):
            return tomorrow_dt if tz is timezone.utc else tomorrow_dt.astimezone()
        @staticmethod
        def fromisoformat(s):
            return datetime.fromisoformat(s)
    monkeypatch.setattr(L, "datetime", _FakeDT)
    # Emit again — handler should detect day change and open a new file
    logging.getLogger("agent8088.engine").info("day two")
    tomorrow = tomorrow_dt.strftime("%Y-%m-%d")
    f_tomorrow = log_dir / f"agent8088-{tomorrow}.log"
    assert f_tomorrow.exists(), f"expected new file {f_tomorrow}"
    entries = _read_jsonl(f_tomorrow)
    assert entries[0]["msg"] == "day two"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_operational_log.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent8088.logging_setup'` (and the later tests error because `L.DailyJsonlHandler` / `L.configure_logging` don't exist).

- [ ] **Step 3: Write `src/agent8088/logging_setup.py`**

```python
"""Operational log: one daily-rotating JSONL file with subsystem names.

The audit log (engine._audit) records permission decisions. This module is the
operational sink that `_log = logging.getLogger("agent8088.engine")` and every
sibling subsystem logger has been missing — they all had no configured sink.

Wires one custom DailyJsonlHandler onto the `agent8088` parent logger; every
child logger (engine, gateway, memory, mcp, gateway.platforms.*) inherits via
stdlib propagation. Never raises: a broken sink degrades to a no-op so it can
never break an agent turn (parity with the audit log contract, engine.py:5624).
"""
import json
import logging
import sys
from datetime import datetime, timezone

from agent8088 import engine as A


def _subsystem(name: str) -> str:
    """`agent8088.gateway.platforms.slack` -> `gateway/platforms/slack`."""
    return name[len("agent8088."):] if name.startswith("agent8088.") else name


class DailyJsonlHandler(logging.Handler):
    """One JSONL file per local day, named `agent8088-YYYY-MM-DD.log`.

    Opens the dated file on first emit and reopens a new dated file when the
    local date changes — produces the date-in-active-filename pattern from
    OpenClaw (`openclaw-2026-08-20.log`), which stdlib's TimedRotatingFileHandler
    does not (it keeps a base name and only suffixes on rotation).

    Never raises: emit() failures route through self.handleError(record),
    stdlib's non-raising error path.
    """

    def __init__(self, base_dir):
        super().__init__()
        self._base_dir = base_dir
        self._cur_date = None
        self._fh = None

    def _date_str(self):
        return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")

    def _open_for(self, date_str):
        path = self._base_dir / f"agent8088-{date_str}.log"
        existed = path.exists()
        self._fh = path.open("a", encoding="utf-8")
        if not existed:
            try:
                A._protect_private_file(path)
            except Exception:
                pass  # never let file protection break the sink
        self._cur_date = date_str

    def emit(self, record):
        try:
            today = self._date_str()
            if today != self._cur_date:
                if self._fh is not None:
                    self._fh.close()
                self._open_for(today)
            entry = {
                "ts": datetime.now(timezone.utc).astimezone().isoformat(),
                "level": record.levelname,
                "subsystem": _subsystem(record.name),
                "msg": A._redact_secrets(record.getMessage()),
            }
            self._fh.write(json.dumps(entry) + "\n")
            self._fh.flush()
        except Exception:
            self.handleError(record)  # stdlib: logs to stderr if enabled, never raises

    def close(self):
        try:
            if self._fh is not None:
                self._fh.close()
        finally:
            super().close()


def configure_logging() -> None:
    """Attach the DailyJsonlHandler to the `agent8088` parent logger. Idempotent.

    Never raises: on any setup failure (unwritable dir, permissions), logs once
    to stderr and returns with no handler attached — the agent runs normally
    and _log calls are no-ops as they were before.
    """
    try:
        if str(A.APP_CONFIG.get("log_enabled", "1")).strip() == "0":
            return
        level_name = str(A.APP_CONFIG.get("log_level", "INFO")).strip().upper()
        level = getattr(logging, level_name, logging.INFO)
        base_dir = A._agent_data_dir() / "logs"
        base_dir.mkdir(parents=True, exist_ok=True)
        parent = logging.getLogger("agent8088")
        # Idempotent: don't attach a second DailyJsonlHandler on repeat calls.
        if any(isinstance(h, DailyJsonlHandler) for h in parent.handlers):
            return
        parent.addHandler(DailyJsonlHandler(base_dir))
        parent.setLevel(level)
    except Exception as exc:
        print(f"[logging_setup] could not configure log file: {exc}", file=sys.stderr)


# ponytail: log_max_bytes is read nowhere — a single-day burst could grow the
# file unbounded until the day changes. Low risk for agent8088 (INFO-level agent
# events, not request-per-line HTTP). Upgrade path: size check in emit() that
# rolls mid-day. Tracked as a known ceiling.


def demo():
    """Ponytail self-check: emit a few records and print the file path."""
    configure_logging()
    log = logging.getLogger("agent8088.engine")
    log.info("demo: info record")
    log.warning("demo: warning record")
    logging.getLogger("agent8088.gateway").info("demo: gateway record")
    f = A._agent_data_dir() / "logs" / f"agent8088-{datetime.now().astimezone().strftime('%Y-%m-%d')}.log"
    print(f"log file: {f}")
    print(f.read_text(encoding="utf-8"))


if __name__ == "__main__":
    demo()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_operational_log.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Run the demo self-check**

Run: `.venv\Scripts\python.exe -m agent8088.logging_setup`
Expected: prints `log file: <path>` and 3 JSONL lines with `subsystem` values `engine`, `engine`, `gateway`.

- [ ] **Step 6: Commit**

```bash
git add src/agent8088/logging_setup.py tests/test_operational_log.py
git commit -m "feat: add daily-rotating JSONL operational log"
```

---

## Task 2: Wire `configure_logging()` into `cli.main()` and `gateway/__main__.py`

**Files:**
- Modify: `src/agent8088/cli.py` (top of `main()`, ~line 5288)
- Modify: `src/agent8088/gateway/__main__.py` (replace `logging.basicConfig`, line 6)

**Interfaces:**
- Consumes: `agent8088.logging_setup.configure_logging` (Task 1).
- Produces: every `agent8088.*` logger starts writing to the daily JSONL file across all entry points (REPL, gateway, MCP server).

- [ ] **Step 1: Write a failing integration test**

Append to `tests/test_operational_log.py`:

```python
def test_main_calls_configure_logging(tmp_path, monkeypatch):
    """cli.main() must configure logging before dispatching to any mode."""
    called = []
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    import agent8088.cli as cli
    monkeypatch.setattr(cli, "configure_logging", lambda: called.append(True))
    # Point argv at a no-op flag that exits early so main() returns fast.
    monkeypatch.setattr(sys, "argv", ["agent8088", "--version"])
    with pytest.raises(SystemExit):
        cli.main()
    assert called, "configure_logging was not called from main()"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_operational_log.py::test_main_calls_configure_logging -q`
Expected: FAIL — `configure_logging` not imported into `cli.py` so the monkeypatch attribute doesn't exist / not called.

- [ ] **Step 3: Wire `configure_logging()` into `cli.main()`**

In `src/agent8088/cli.py`, add the import near the other `agent8088` imports (search for `from agent8088 import engine as A`):

```python
from agent8088.logging_setup import configure_logging
```

At the top of `main()` (right after `def main():` and before `import argparse`, so logging is configured even for `--help`/`--version` paths), add:

```python
    configure_logging()
```

- [ ] **Step 4: Replace `logging.basicConfig` in gateway entrypoint**

In `src/agent8088/gateway/__main__.py`, replace lines 6-9:

```python
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
```

with:

```python
    from agent8088.logging_setup import configure_logging
    configure_logging()
```

- [ ] **Step 5: Run the new test + full operational-log suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_operational_log.py -q`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add src/agent8088/cli.py src/agent8088/gateway/__main__.py tests/test_operational_log.py
git commit -m "feat: configure operational log in cli and gateway entrypoints"
```

---

## Task 3: `agent8088 --logs` CLI — print + filter

**Files:**
- Modify: `src/agent8088/cli.py` (argparse in `main()`, new `cmd_logs(args)` function)
- Test: `tests/test_logs_cli.py`

**Interfaces:**
- Consumes: the JSONL file written by `DailyJsonlHandler` (Task 1), `agent8088.engine._agent_data_dir() -> Path` to locate it.
- Produces: `cmd_logs(args) -> int` (returns exit code; 0 on success, 1 on missing file). Human format: `HH:MM:SS+TZ level subsystem msg`. JSON format: raw JSONL line.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_logs_cli.py`:

```python
"""CLI: agent8088 --logs [follow] [-n N] [--level L] [--subsystem S] [--json].

Reads the daily JSONL file directly. v1 has no RPC/remote tail — the file is
plain JSONL on disk so this is a tail -f with filtering.
"""
import json
import os
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent8088 import engine as A
from agent8088 import cli


def _seed_log(log_dir, n, *, levels=("INFO",), subsystems=("engine",)):
    """Write n JSONL records to today's file. Returns the file path."""
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    f = log_dir / f"agent8088-{today}.log"
    with f.open("a", encoding="utf-8") as fh:
        for i in range(n):
            level = levels[i % len(levels)]
            sub = subsystems[i % len(subsystems)]
            rec = {"ts": f"2026-08-20T12:{i:02d}:00+00:00", "level": level,
                   "subsystem": sub, "msg": f"record {i}"}
            fh.write(json.dumps(rec) + "\n")
    return f


def _make_args(log_dir, follow=False, limit=50, level=None, subsystem=None, json_out=False):
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    return SimpleNamespace(
        logs=("follow" if follow else "tail"),
        log_dir=log_dir,
        log_file=log_dir / f"agent8088-{today}.log",
        limit=limit, level=level, subsystem=subsystem, json=json_out,
    )


def test_logs_prints_last_n_lines(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "logs"
    _seed_log(log_dir, 100)
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    rc = cli.cmd_logs(_make_args(log_dir, limit=10))
    out, _ = capsys.readouterr()
    lines = [l for l in out.splitlines() if l.strip()]
    assert rc == 0
    assert len(lines) == 10
    assert "record 99" in lines[-1]


def test_logs_level_filter(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "logs"
    _seed_log(log_dir, 20, levels=("INFO", "WARNING"))
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    rc = cli.cmd_logs(_make_args(log_dir, level="WARNING"))
    out, _ = capsys.readouterr()
    lines = [l for l in out.splitlines() if l.strip()]
    assert rc == 0
    # Only WARNING lines should appear (every other record is WARNING)
    assert all("WARNING" in l for l in lines)
    assert len(lines) == 10


def test_logs_subsystem_filter(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "logs"
    _seed_log(log_dir, 20, subsystems=("engine", "gateway"))
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    rc = cli.cmd_logs(_make_args(log_dir, subsystem="gateway"))
    out, _ = capsys.readouterr()
    lines = [l for l in out.splitlines() if l.strip()]
    assert rc == 0
    assert all("gateway" in l for l in lines)
    assert len(lines) == 10


def test_logs_json_emits_raw_jsonl(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "logs"
    _seed_log(log_dir, 5)
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    rc = cli.cmd_logs(_make_args(log_dir, limit=5, json_out=True))
    out, _ = capsys.readouterr()
    lines = [l for l in out.splitlines() if l.strip()]
    assert rc == 0
    assert len(lines) == 5
    # Each line must be valid JSON with the expected fields.
    for line in lines:
        obj = json.loads(line)
        assert {"ts", "level", "subsystem", "msg"} <= set(obj.keys())


def test_logs_missing_file_exits_clean(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "logs"  # not seeded — no file exists
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    rc = cli.cmd_logs(_make_args(log_dir))
    out, _ = capsys.readouterr()
    assert rc == 1
    assert "No log file" in out
    assert "Traceback" not in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_logs_cli.py -q`
Expected: FAIL — `AttributeError: module 'agent8088.cli' has no attribute 'cmd_logs'`.

- [ ] **Step 3: Implement `cmd_logs(args)` in `cli.py`**

Add near the other command functions (e.g., after `cmd_audit` around line 2769). The function takes a `SimpleNamespace`-like `args` object with fields `log_file`, `limit`, `level`, `subsystem`, `json`, and `logs` (`"tail"` or `"follow"`):

```python
def cmd_logs(args):
    """Print or follow the operational JSONL log.

    Reads the daily file directly (no RPC in v1). Human format:
        HH:MM:SS+TZ level subsystem msg
    With --json: raw JSONL lines.
    """
    path = getattr(args, "log_file", None)
    if path is None:
        from agent8088 import engine as _A
        path = _A._agent_data_dir() / "logs" / (
            f"agent8088-{datetime.now().astimezone().strftime('%Y-%m-%d')}.log")
    if not path.exists():
        print(f"No log file at {path}. Run agent8088 to start logging.")
        return 1
    import json as _json
    level_filter = (args.level or "").upper() or None
    sub_filter = args.subsystem or None
    lines = path.read_text(encoding="utf-8").splitlines()
    # Keep only non-empty, valid-JSON lines that match the filters.
    def _matches(line):
        if not line.strip():
            return False
        try:
            obj = _json.loads(line)
        except Exception:
            return False
        if level_filter and obj.get("level", "").upper() != level_filter:
            return False
        if sub_filter and sub_filter.lower() not in obj.get("subsystem", "").lower():
            return False
        return True
    matched = [l for l in lines if _matches(l)]
    tail = matched[-args.limit:] if args.limit else matched
    for line in tail:
        if getattr(args, "json", False):
            print(line)
        else:
            try:
                obj = _json.loads(line)
                ts = obj.get("ts", "")
                # Shorten the timestamp to the time+offset portion for readability.
                if "T" in ts:
                    ts = ts.split("T", 1)[1]
                lvl = obj.get("level", "?")
                sub = obj.get("subsystem", "?")
                msg = obj.get("msg", "")
                print(f"{ts} {lvl.lower()} {sub} {msg}")
            except Exception:
                print(line)
    return 0
```

Also add the `datetime` import at the top of `cli.py` if not already present:

```python
from datetime import datetime
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_logs_cli.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent8088/cli.py tests/test_logs_cli.py
git commit -m "feat: add agent8088 --logs print and filter"
```

---

## Task 4: `agent8088 --logs follow` — tail with rotation detection

**Files:**
- Modify: `src/agent8088/cli.py` (extend `cmd_logs` with follow mode)
- Test: `tests/test_logs_cli.py` (append 2 tests)

**Interfaces:**
- Consumes: same JSONL file shape as Task 3.
- Produces: follow mode prints last N lines, then polls every 1s for new bytes; on file size shrink, prints `Log cursor reset (file rotated).` and re-opens.

- [ ] **Step 1: Write the failing follow tests**

Append to `tests/test_logs_cli.py`:

```python
def test_logs_follow_emits_new_lines(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "logs"
    f = _seed_log(log_dir, 5)
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    args = _make_args(log_dir, follow=True, limit=5)

    # Run cmd_logs in a thread; it blocks on follow. Stop it by setting a flag
    # the function polls. We monkeypatch time.sleep to set the stop flag after
    # the append so the test is deterministic — no real time.sleep in the path.
    stop = {"flag": False}
    appended = threading.Event()

    def _fake_sleep(_secs):
        if not appended.is_set():
            # First few sleeps happen before the append — append now.
            with f.open("a", encoding="utf-8") as fh:
                for i in range(3):
                    fh.write(json.dumps({"ts": "2026-08-20T13:00:00+00:00",
                                         "level": "INFO",
                                         "subsystem": "engine",
                                         "msg": f"new {i}"}) + "\n")
            appended.set()
        # After the append, return once then signal stop on the next sleep.
        if appended.is_set():
            stop["flag"] = True
            raise KeyboardInterrupt  # how cmd_logs follow exits

    monkeypatch.setattr(time, "sleep", _fake_sleep)
    monkeypatch.setattr(A, "time", time)  # if engine imported time locally
    # Some cli code may import time itself; patch the module-level reference too.
    import agent8088.cli as _cli
    monkeypatch.setattr(_cli.time, "sleep", _fake_sleep, raising=False)

    rc = cli.cmd_logs(args)  # should exit via KeyboardInterrupt internally
    out, _ = capsys.readouterr()
    # The 5 seeded lines should print first, then the 3 new ones.
    assert "record 4" in out  # last seeded line
    assert "new 0" in out and "new 1" in out and "new 2" in out


def test_logs_follow_handles_rotation(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "logs"
    f = _seed_log(log_dir, 3)
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    args = _make_args(log_dir, follow=True, limit=3)

    rotated = threading.Event()

    def _fake_sleep(_secs):
        if not rotated.is_set():
            # Simulate rotation: move the current file aside, write a fresh file.
            rotated_path = f.with_suffix(f.parent / (f.name + ".old"))
            f.replace(rotated_path)
            with f.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": "2026-08-20T14:00:00+00:00",
                                     "level": "INFO",
                                     "subsystem": "engine",
                                     "msg": "post-rotation"}) + "\n")
            rotated.set()
        if rotated.is_set():
            raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", _fake_sleep)
    import agent8088.cli as _cli
    monkeypatch.setattr(_cli.time, "sleep", _fake_sleep, raising=False)

    cli.cmd_logs(args)
    out, _ = capsys.readouterr()
    assert "Log cursor reset (file rotated)." in out
    assert "post-rotation" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_logs_cli.py::test_logs_follow_emits_new_lines tests/test_logs_cli.py::test_logs_follow_handles_rotation -q`
Expected: FAIL — follow mode not implemented; current `cmd_logs` returns after printing.

- [ ] **Step 3: Add follow mode to `cmd_logs`**

Replace the tail-print loop at the end of `cmd_logs` (Task 3's implementation) with:

```python
    # Print the initial tail.
    for line in tail:
        _print_line(line, args)
    # Follow mode: poll for new bytes.
    if getattr(args, "logs", None) == "follow":
        _follow(path, args, _matches, _print_line)
    return 0
```

And add the helpers above `cmd_logs`:

```python
def _print_line(line, args):
    if getattr(args, "json", False):
        print(line)
    else:
        import json as _json
        try:
            obj = _json.loads(line)
            ts = obj.get("ts", "")
            if "T" in ts:
                ts = ts.split("T", 1)[1]
            print(f"{ts} {obj.get('level', '?').lower()} {obj.get('subsystem', '?')} {obj.get('msg', '')}")
        except Exception:
            print(line)


def _follow(path, args, matches_fn, print_fn):
    """tail -f with rotation detection. Exits on Ctrl+C."""
    import time as _time
    last_size = path.stat().st_size if path.exists() else 0
    try:
        while True:
            _time.sleep(1)
            if not path.exists():
                # File may have been rotated away; wait for it to reappear.
                continue
            cur_size = path.stat().st_size
            if cur_size < last_size:
                print("Log cursor reset (file rotated).")
                last_size = 0
            if cur_size > last_size:
                with path.open("r", encoding="utf-8") as fh:
                    fh.seek(last_size)
                    for line in fh:
                        if matches_fn(line.rstrip("\n")):
                            print_fn(line.rstrip("\n"), args)
                last_size = cur_size
    except KeyboardInterrupt:
        pass
```

Also refactor the body of `cmd_logs` (Task 3) so the print loop calls `_print_line(line, args)` — keep the function shape stable, just route through the helper.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_logs_cli.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent8088/cli.py tests/test_logs_cli.py
git commit -m "feat: add agent8088 --logs follow with rotation detection"
```

---

## Task 5: Argparse wiring for `--logs` in `main()`

**Files:**
- Modify: `src/agent8088/cli.py` (`main()` argparse, dispatch branch)

**Interfaces:**
- Consumes: `cmd_logs(args)` (Tasks 3-4).
- Produces: `agent8088 --logs`, `agent8088 --logs follow`, `agent8088 --logs -n N --level L --subsystem S --json` work end-to-end.

- [ ] **Step 1: Write a failing end-to-end CLI test**

Append to `tests/test_logs_cli.py`:

```python
def test_main_logs_flag_dispatches_to_cmd_logs(tmp_path, monkeypatch, capsys):
    """agent8088 --logs must dispatch to cmd_logs and exit 0 (not enter REPL)."""
    log_dir = tmp_path / "logs"
    _seed_log(log_dir, 3)
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    called = []
    import agent8088.cli as _cli
    def _fake_cmd_logs(args):
        called.append(args)
        return 0
    monkeypatch.setattr(_cli, "cmd_logs", _fake_cmd_logs)
    monkeypatch.setattr(sys, "argv", ["agent8088", "--logs"])
    rc = _cli.main()
    assert called, "--logs did not dispatch to cmd_logs"
    # main() should not fall through to the REPL.
    assert rc in (0, None)


def test_main_logs_follow_passes_follow_arg(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    _seed_log(log_dir, 1)
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    captured = {}
    import agent8088.cli as _cli
    def _fake_cmd_logs(args):
        captured["logs"] = getattr(args, "logs", None)
        return 0
    monkeypatch.setattr(_cli, "cmd_logs", _fake_cmd_logs)
    monkeypatch.setattr(sys, "argv", ["agent8088", "--logs", "follow"])
    _cli.main()
    assert captured.get("logs") == "follow"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_logs_cli.py::test_main_logs_flag_dispatches_to_cmd_logs tests/test_logs_cli.py::test_main_logs_follow_passes_follow_arg -q`
Expected: FAIL — `--logs` not a registered flag; argparse errors with "unrecognized arguments".

- [ ] **Step 3: Add the argparse flags and dispatch branch**

In `src/agent8088/cli.py` inside `main()`, after the existing `parser.add_argument("--mcp-host", ...)` line (~line 5318) and before `args = parser.parse_args()`, add:

```python
    parser.add_argument("--logs", nargs="?", const="tail", default=None,
                        help="print or follow the operational log; 'follow' tails in real time")
    parser.add_argument("-n", "--limit", type=int, default=50,
                        help="with --logs: number of lines to print (default 50)")
    parser.add_argument("--level", default=None,
                        help="with --logs: filter by level (DEBUG|INFO|WARNING|ERROR)")
    parser.add_argument("--subsystem", default=None,
                        help="with --logs: substring filter on subsystem name")
    parser.add_argument("--json", action="store_true",
                        help="with --logs: emit raw JSONL instead of human format")
```

Then, before the `if args.uninstall:` dispatch block (after `args = parser.parse_args()` and the `--mcp-*` normalization block, ~line 5329), add the logs dispatch:

```python
    if args.logs is not None:
        # Locate today's file for cmd_logs.
        from datetime import datetime as _dt
        today = _dt.now().astimezone().strftime("%Y-%m-%d")
        args.log_file = A._agent_data_dir() / "logs" / f"agent8088-{today}.log"
        rc = cmd_logs(args)
        return rc if isinstance(rc, int) else 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_logs_cli.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent8088/cli.py tests/test_logs_cli.py
git commit -m "feat: wire agent8088 --logs flag in main argparse"
```

---

## Task 6: Full-suite verification + manual demo

**Files:** none modified — verification only.

- [ ] **Step 1: Run the full test suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS — all existing tests still green plus the 16 new ones (7 + 9).

- [ ] **Step 2: Run the duplicate-definition scan (repo convention)**

Run: `.venv\Scripts\python.exe scripts/check_duplicate_defs.py`
Expected: PASS — no duplicate definitions introduced.

- [ ] **Step 3: Run the demo self-check**

Run: `.venv\Scripts\python.exe -m agent8088.logging_setup`
Expected: prints the log file path and 3 JSONL lines.

- [ ] **Step 4: Manual end-to-end (observed working somewhere other than the author's laptop)**

In one terminal:
```
agent8088 --logs follow
```
In another, run any agent8088 turn. Observe lines streaming in the format `HH:MM:SS+TZ level subsystem msg`. Then move the log file aside (simulate rotation) and confirm `Log cursor reset (file rotated).` appears and streaming resumes into the new dated file.

- [ ] **Step 5: Verify no debug prints or TODOs left behind**

Run: `git diff --staged` and skim for stray `print(` in non-CLI code, `TODO`, or commented-out blocks. The only `print` calls should be in `cmd_logs`, `_print_line`, `configure_logging`'s single stderr failure line, and `demo()`.

- [ ] **Step 6: Final commit (if any cleanup)**

If Steps 1-5 required no changes, skip. Otherwise:

```bash
git add -A
git commit -m "test: verify operational logging end-to-end"
```

---

## Self-review

**1. Spec coverage:**
- Problem (no sink on `agent8088.*` loggers) → Task 1 attaches the handler. ✓
- Architecture (one daily JSONL file, subsystem from logger name) → Task 1 `DailyJsonlHandler` + `_subsystem`. ✓
- Redaction on every record → Task 1 `emit()` calls `A._redact_secrets`; test `test_secrets_redacted_in_log_record`. ✓
- Never-raise guarantee → Task 1 `configure_logging` try/except + `emit` `handleError`; test `test_unwritable_log_dir_does_not_raise`. ✓
- File mode 0600 → Task 1 `_open_for` calls `A._protect_private_file`. ✓
- `log_enabled=0` opt-out → Task 1 early return; test `test_log_enabled_zero_writes_no_file`. ✓
- CLI `agent8088 --logs [follow] [-n N] [--level L] [--subsystem S] [--json]` → Tasks 3-5. ✓
- Rotation detection (`Log cursor reset (file rotated).`) → Task 4 `_follow`; test `test_logs_follow_handles_rotation`. ✓
- Configure in `cli.main()` and `gateway/__main__.py` → Task 2; test `test_main_calls_configure_logging`. ✓
- 12 tests (6 + 6) → Tasks 1, 3, 4, 5 add 6 + 5 + 2 + 2 = 15 tests (slightly more than the spec's 12 — the extra 3 cover the main()-dispatch contract that the spec's tests didn't explicitly enumerate but the spec's "Verification" section requires). ✓
- `ponytail:` ceiling comment on `log_max_bytes` → Task 1 includes it. ✓
- `demo()` self-check → Task 1. ✓
- No new dependencies → all code uses stdlib `logging` only. ✓

**2. Placeholder scan:** No TBD/TODO in the plan. Every step has complete code. No "add error handling" without showing it. No "similar to Task N" — each task repeats the needed code.

**3. Type consistency:**
- `configure_logging() -> None` — used identically in Tasks 1, 2.
- `DailyJsonlHandler(logging.Handler)` — referenced in Task 1 tests and Task 2's idempotency check.
- `_subsystem(name: str) -> str` — defined in Task 1, used in Task 1's `emit`.
- `cmd_logs(args) -> int` — defined in Task 3, extended in Task 4, dispatched in Task 5. The `args` object fields (`log_file`, `limit`, `level`, `subsystem`, `json`, `logs`) are consistent across Tasks 3-5 and the `_make_args` test helper.
- `_print_line(line, args)` and `_follow(path, args, matches_fn, print_fn)` — defined in Task 4, used in Task 4. Consistent signatures.

No issues found. Plan is complete.