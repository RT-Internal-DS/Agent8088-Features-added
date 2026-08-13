# testmail.app E2E Email Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a gated end-to-end test that sends a real email through the adapter's SMTP path to testmail.app and verifies it arrived via the testmail JSON API.

**Architecture:** Mock `imaplib.IMAP4_SSL` at the stdlib boundary so all adapter parsing/allowlist/dispatch code runs for real. Use a fake runner whose `on_message()` echoes the inbound text back via `adapter.send_message()` (real SMTP). Poll the testmail.app JSON API with `livequery=true` using stdlib `urllib.request` to assert the reply arrived with correct body and headers. Zero new dependencies, zero adapter changes.

**Tech Stack:** Python stdlib (`imaplib`, `smtplib`, `email`, `urllib.request`, `uuid`, `time`, `asyncio`), pytest, monkeypatch. testmail.app JSON API.

## Global Constraints

- Zero new dependencies — stdlib only for the testmail API client (`urllib.request`).
- Zero changes to `src/agent8088/gateway/platforms/email.py`.
- Zero changes to `tests/gateway/platforms/test_email.py` (existing 17 unit tests untouched).
- All e2e tests gated on `TESTMAIL_APIKEY` and `TESTMAIL_NAMESPACE` env vars — `pytest.skip()` if missing.
- Tests read `EMAIL_*` credentials via `A.get_secret(config, key, env_var)` (same path the adapter uses), so they pick up `~/.agent8088/.env` automatically.
- Patch `POLL_INTERVAL` to `0.5s` in every e2e test so the adapter processes the mock IMAP fetch quickly.
- Each test uses a unique tag: `e2e-{test_name}-{uuid4().hex[:8]}`.
- Follow AGENTS.md: ponytail (shortest working diff), engineering (one behavior per test, deterministic, mock external services not code under test), testing rules.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `tests/gateway/platforms/test_email_e2e.py` | Create | Gated e2e tests: helpers + 5 test cases |

One file. No source changes. No config changes.

---

### Task 1: Test scaffolding + helpers + gating

**Files:**
- Create: `tests/gateway/platforms/test_email_e2e.py`

**Interfaces:**
- Produces: `_require_testmail_creds()` -> `(apikey, namespace)` tuple or skips; `_testmail_fetch(apikey, namespace, tag, ts_from, timeout=60)` -> dict; `_FakeIMAP` class; `_EchoRunner` class; `_make_inbound_email(from_addr, subject, body)` -> `(msg, raw_bytes)`; `_build_adapter(monkeypatch, fake_imap, allow_addrs)` -> `EmailAdapter`.

- [ ] **Step 1: Create the test file with imports, gating, and helpers**

```python
"""End-to-end tests for the Email adapter using testmail.app.

Gated on TESTMAIL_APIKEY and TESTMAIL_NAMESPACE env vars (or ~/.agent8088/.env).
Skips automatically when these are missing, so this runs locally with creds and
is safe in CI without secrets.

Flow: mock IMAP returns a synthetic email whose From is a testmail inbox
address -> adapter parses + dispatches for real -> fake runner echoes the
text back via real SMTP -> poll testmail JSON API -> assert the reply arrived.
"""
import asyncio
import email as email_lib
import os
import time
import urllib.parse
import urllib.request
import uuid
from email.mime.text import MIMEText
from email.utils import make_msgid
from unittest.mock import MagicMock

import pytest

from agent8088 import engine as A
from agent8088.gateway.platforms.email import EmailAdapter, POLL_INTERVAL


def _require_testmail_creds():
    """Return (apikey, namespace) from env or ~/.agent8088/.env, or skip."""
    _env = A.load_env_file()
    apikey = os.environ.get("TESTMAIL_APIKEY") or _env.get("TESTMAIL_APIKEY", "")
    namespace = os.environ.get("TESTMAIL_NAMESPACE") or _env.get("TESTMAIL_NAMESPACE", "")
    if not apikey or not namespace:
        pytest.skip("TESTMAIL_APIKEY and TESTMAIL_NAMESPACE required for e2e tests")
    return apikey, namespace


def _testmail_fetch(apikey, namespace, tag, ts_from, timeout=60):
    """Poll testmail.app JSON API for emails matching tag since ts_from (ms).

    Uses livequery=true so the API waits up to 60s for a match, then 307
    redirects to itself. urllib follows 307s by default. Returns the parsed
    JSON dict. Raises TimeoutError if no match within `timeout` seconds.
    """
    params = {
        "apikey": apikey,
        "namespace": namespace,
        "tag": tag,
        "timestamp_from": str(ts_from),
        "livequery": "true",
        "headers": "true",
    }
    url = "https://api.testmail.app/api/json?" + urllib.parse.urlencode(params)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=max(5, int(deadline - time.time()))) as resp:
                data = __import__("json").loads(resp.read().decode("utf-8"))
        except Exception as e:
            if time.time() >= deadline:
                raise TimeoutError(f"testmail fetch failed within {timeout}s: {e}")
            time.sleep(1)
            continue
        if data.get("result") == "fail":
            raise RuntimeError(f"testmail API error: {data.get('message')}")
        if data.get("count", 0) > 0:
            return data
        # livequery returned 0 (shouldn't happen with livequery=true, but guard)
        time.sleep(1)
    raise TimeoutError(f"no email matched tag={tag} within {timeout}s")


class _FakeIMAP:
    """Mimics imaplib.IMAP4_SSL at the lowest boundary.

    Returns one synthetic unread email on .uid("search",...) and serves its
    raw RFC822 bytes on .uid("fetch",...). All adapter parsing runs for real.
    """
    def __init__(self, raw_bytes):
        self._raw = raw_bytes
        self._uid = b"1234"

    def login(self, addr, pw):
        pass

    def select(self, mailbox="INBOX"):
        return ("OK", [b"1"])

    def uid(self, command, *args):
        if command == "search":
            return ("OK", [self._uid])
        if command == "fetch":
            return ("OK", [(None, self._raw)])
        return ("OK", [b""])

    def logout(self):
        pass


class _Allowlist:
    """Fake allowlist: allows only the given addresses."""
    def __init__(self, addrs):
        self._addrs = set(a.lower() for a in addrs)

    def is_allowed(self, addr, platform):
        return addr.lower() in self._addrs


class _EchoRunner:
    """Fake runner: echoes inbound text back via adapter.send_message.

    Has .allowlist (so the adapter's allowlist check runs) and an async
    on_message(event) that calls adapter.send_message(chat_id, "Echo: ...").
    """
    def __init__(self, adapter, allow_addrs):
        self.adapter = adapter
        self.allowlist = _Allowlist(allow_addrs)

    async def on_message(self, event):
        await self.adapter.send_message(event.chat_id, f"Echo: {event.text}")


def _make_inbound_email(from_addr, subject, body):
    """Build a real MIMEText RFC822 email. Returns (msg, raw_bytes)."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = from_addr
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    return msg, msg.as_bytes()


def _build_adapter(monkeypatch, fake_imap, allow_addrs):
    """Build a real EmailAdapter with mocked IMAP and a patched POLL_INTERVAL.

    Reads EMAIL_* creds from .env via A.get_secret (same path the adapter uses).
    Returns (adapter, runner, loop) where loop is the running event loop.
    """
    monkeypatch.setattr("imaplib.IMAP4_SSL", lambda *a, **k: fake_imap)
    monkeypatch.setattr("agent8088.gateway.platforms.email.POLL_INTERVAL", 0.5)
    config = {
        "email_enabled": "1",
        "email_smtp_port": "587",
        "email_imap_port": "993",
    }
    adapter = EmailAdapter(config, runner=None)
    runner = _EchoRunner(adapter, allow_addrs)
    adapter.runner = runner
    return adapter, runner
```

- [ ] **Step 2: Verify the file imports and skips cleanly without creds**

Run: `.venv\Scripts\python.exe -m pytest tests/gateway/platforms/test_email_e2e.py --co -q`
Expected: collects 0 tests (no test functions yet) or skips. No import errors.

- [ ] **Step 3: Commit**

```bash
git add tests/gateway/platforms/test_email_e2e.py
git commit -m "test: add testmail.app e2e email test scaffolding and helpers"
```

---

### Task 2: Happy path test (inbound → dispatch → SMTP → testmail)

**Files:**
- Modify: `tests/gateway/platforms/test_email_e2e.py` (append test)

**Interfaces:**
- Consumes: `_require_testmail_creds()`, `_testmail_fetch()`, `_FakeIMAP`, `_EchoRunner`, `_make_inbound_email()`, `_build_adapter()` from Task 1.

- [ ] **Step 1: Append the happy path test**

Append to the end of `tests/gateway/platforms/test_email_e2e.py`:

```python
def test_e2e_happy_path(monkeypatch):
    """Inbound email -> adapter parses + dispatches -> real SMTP reply
    arrives in testmail.app with the echoed body."""
    apikey, namespace = _require_testmail_creds()

    tag = f"e2e-happy-{uuid.uuid4().hex[:8]}"
    sender = f"{namespace}.{tag}@inbox.testmail.app"
    subject = "Hello Agent"
    body = "Please echo this back."
    ts_from = int(time.time() * 1000)

    msg, raw = _make_inbound_email(sender, subject, body)
    fake_imap = _FakeIMAP(raw)
    adapter, runner = _build_adapter(monkeypatch, fake_imap, [sender])

    async def _run():
        await adapter.connect()
        await asyncio.sleep(1.5)  # let one poll cycle process the mock IMAP
        await adapter.disconnect()

    asyncio.run(_run())

    data = _testmail_fetch(apikey, namespace, tag, ts_from, timeout=90)
    emails = data["emails"]
    assert len(emails) >= 1
    received = emails[0]
    assert "Echo: Please echo this back." in received.get("text", "")
```

- [ ] **Step 2: Run the test (without creds — should skip)**

Run: `.venv\Scripts\python.exe -m pytest tests/gateway/platforms/test_email_e2e.py::test_e2e_happy_path -v`
Expected: SKIPPED (no TESTMAIL creds on this machine).

- [ ] **Step 3: Commit**

```bash
git add tests/gateway/platforms/test_email_e2e.py
git commit -m "test: add e2e happy path test for email adapter via testmail.app"
```

---

### Task 3: Threading headers test (In-Reply-To matches Message-ID)

**Files:**
- Modify: `tests/gateway/platforms/test_email_e2e.py` (append test)

- [ ] **Step 1: Append the threading headers test**

```python
def test_e2e_threading_headers(monkeypatch):
    """Reply has In-Reply-To header matching the inbound Message-ID."""
    apikey, namespace = _require_testmail_creds()

    tag = f"e2e-thread-{uuid.uuid4().hex[:8]}"
    sender = f"{namespace}.{tag}@inbox.testmail.app"
    subject = "Thread me"
    body = "Test threading."
    ts_from = int(time.time() * 1000)

    msg, raw = _make_inbound_email(sender, subject, body)
    inbound_msg_id = msg["Message-ID"]
    fake_imap = _FakeIMAP(raw)
    adapter, runner = _build_adapter(monkeypatch, fake_imap, [sender])

    async def _run():
        await adapter.connect()
        await asyncio.sleep(1.5)
        await adapter.disconnect()

    asyncio.run(_run())

    data = _testmail_fetch(apikey, namespace, tag, ts_from, timeout=90)
    emails = data["emails"]
    assert len(emails) >= 1
    received = emails[0]
    headers = {h["key"].lower(): h["line"] for h in received.get("headers", [])}
    assert "in-reply-to" in headers
    assert inbound_msg_id in headers["in-reply-to"]
```

- [ ] **Step 2: Run (should skip without creds)**

Run: `.venv\Scripts\python.exe -m pytest tests/gateway/platforms/test_email_e2e.py::test_e2e_threading_headers -v`
Expected: SKIPPED.

- [ ] **Step 3: Commit**

```bash
git add tests/gateway/platforms/test_email_e2e.py
git commit -m "test: add e2e threading headers test (In-Reply-To)"
```

---

### Task 4: Subject prefix test (Re: prefix added)

**Files:**
- Modify: `tests/gateway/platforms/test_email_e2e.py` (append test)

- [ ] **Step 1: Append the subject prefix test**

```python
def test_e2e_subject_prefix(monkeypatch):
    """Reply subject is 'Re: {original}' when original didn't start with Re:."""
    apikey, namespace = _require_testmail_creds()

    tag = f"e2e-subject-{uuid.uuid4().hex[:8]}"
    sender = f"{namespace}.{tag}@inbox.testmail.app"
    subject = "Question for you"
    body = "What is 2+2?"
    ts_from = int(time.time() * 1000)

    msg, raw = _make_inbound_email(sender, subject, body)
    fake_imap = _FakeIMAP(raw)
    adapter, runner = _build_adapter(monkeypatch, fake_imap, [sender])

    async def _run():
        await adapter.connect()
        await asyncio.sleep(1.5)
        await adapter.disconnect()

    asyncio.run(_run())

    data = _testmail_fetch(apikey, namespace, tag, ts_from, timeout=90)
    emails = data["emails"]
    assert len(emails) >= 1
    received = emails[0]
    assert received.get("subject", "") == f"Re: {subject}"
```

- [ ] **Step 2: Run (should skip without creds)**

Run: `.venv\Scripts\python.exe -m pytest tests/gateway/platforms/test_email_e2e.py::test_e2e_subject_prefix -v`
Expected: SKIPPED.

- [ ] **Step 3: Commit**

```bash
git add tests/gateway/platforms/test_email_e2e.py
git commit -m "test: add e2e subject prefix test (Re: added)"
```

---

### Task 5: Negative test — unauthorized sender dropped

**Files:**
- Modify: `tests/gateway/platforms/test_email_e2e.py` (append test)

- [ ] **Step 1: Append the unauthorized sender test**

```python
def test_e2e_unauthorized_sender_dropped(monkeypatch):
    """Allowlist blocks sender -> no reply in testmail (negative test)."""
    apikey, namespace = _require_testmail_creds()

    tag = f"e2e-unauth-{uuid.uuid4().hex[:8]}"
    sender = f"{namespace}.{tag}@inbox.testmail.app"
    # Allowlist is EMPTY — sender is not allowed.
    subject = "Should be dropped"
    body = "No reply expected."
    ts_from = int(time.time() * 1000)

    msg, raw = _make_inbound_email(sender, subject, body)
    fake_imap = _FakeIMAP(raw)
    adapter, runner = _build_adapter(monkeypatch, fake_imap, [])  # empty allowlist

    async def _run():
        await adapter.connect()
        await asyncio.sleep(1.5)
        await adapter.disconnect()

    asyncio.run(_run())

    # Assert no email arrives in testmail within a short window.
    with pytest.raises(TimeoutError):
        _testmail_fetch(apikey, namespace, tag, ts_from, timeout=15)
```

- [ ] **Step 2: Run (should skip without creds)**

Run: `.venv\Scripts\python.exe -m pytest tests/gateway/platforms/test_email_e2e.py::test_e2e_unauthorized_sender_dropped -v`
Expected: SKIPPED.

- [ ] **Step 3: Commit**

```bash
git add tests/gateway/platforms/test_email_e2e.py
git commit -m "test: add e2e negative test (unauthorized sender dropped)"
```

---

### Task 6: Negative test — automated sender skipped

**Files:**
- Modify: `tests/gateway/platforms/test_email_e2e.py` (append test)

- [ ] **Step 1: Append the automated sender test**

```python
def test_e2e_automated_sender_skipped(monkeypatch):
    """noreply@... sender -> adapter skips it -> no reply in testmail."""
    apikey, namespace = _require_testmail_creds()

    tag = f"e2e-auto-{uuid.uuid4().hex[:8]}"
    # Sender is noreply@... which triggers the automated-sender filter.
    sender = f"noreply.{namespace}.{tag}@inbox.testmail.app"
    subject = "Auto notification"
    body = "This is automated."
    ts_from = int(time.time() * 1000)

    msg, raw = _make_inbound_email(sender, subject, body)
    fake_imap = _FakeIMAP(raw)
    # Allowlist DOES include the sender, so the only thing dropping it is the
    # automated-sender filter (the allowlist check happens after).
    adapter, runner = _build_adapter(monkeypatch, fake_imap, [sender])

    async def _run():
        await adapter.connect()
        await asyncio.sleep(1.5)
        await adapter.disconnect()

    asyncio.run(_run())

    # The automated-sender filter drops it before SMTP, so no reply arrives.
    # We query by tag prefix to catch any email to this tag.
    with pytest.raises(TimeoutError):
        _testmail_fetch(apikey, namespace, tag, ts_from, timeout=15)
```

- [ ] **Step 2: Run (should skip without creds)**

Run: `.venv\Scripts\python.exe -m pytest tests/gateway/platforms/test_email_e2e.py::test_e2e_automated_sender_skipped -v`
Expected: SKIPPED.

- [ ] **Step 3: Commit**

```bash
git add tests/gateway/platforms/test_email_e2e.py
git commit -m "test: add e2e negative test (automated sender skipped)"
```

---

### Task 7: Final verification — full suite still green

**Files:**
- None modified — verification only.

- [ ] **Step 1: Run the full gateway email test suite (existing + e2e)**

Run: `.venv\Scripts\python.exe -m pytest tests/gateway/platforms/test_email.py tests/gateway/platforms/test_email_e2e.py -v`
Expected: 17 passed (existing) + 5 skipped (e2e, no creds) = 22 total. No failures, no errors.

- [ ] **Step 2: Confirm no import errors or collection warnings**

Run: `.venv\Scripts\python.exe -m pytest tests/gateway/platforms/test_email_e2e.py --co -q`
Expected: collects 5 tests, no errors.

- [ ] **Step 3: No commit needed — verification only**

---

## Self-Review

**1. Spec coverage:**
- ✅ `_testmail_fetch` with livequery + timeout — Task 1
- ✅ `_FakeIMAP` mocking IMAP4_SSL at stdlib boundary — Task 1
- ✅ `_EchoRunner` with allowlist + canned reply — Task 1
- ✅ `_make_inbound_email` building real MIMEText — Task 1
- ✅ `test_e2e_happy_path` — Task 2
- ✅ `test_e2e_threading_headers` (In-Reply-To) — Task 3
- ✅ `test_e2e_subject_prefix` (Re:) — Task 4
- ✅ `test_e2e_unauthorized_sender_dropped` (negative) — Task 5
- ✅ `test_e2e_automated_sender_skipped` (negative) — Task 6
- ✅ Gating on TESTMAIL_APIKEY + TESTMAIL_NAMESPACE — Task 1 (`_require_testmail_creds`)
- ✅ POLL_INTERVAL patched to 0.5s — Task 1 (`_build_adapter`)
- ✅ Unique tag per test — all test tasks use `uuid.uuid4().hex[:8]`
- ✅ Zero adapter changes — no task touches `email.py`
- ✅ Zero new deps — stdlib `urllib.request` only
- ✅ Final verification — Task 7

**2. Placeholder scan:** No TBD, TODO, or vague steps. All code is complete.

**3. Type consistency:** `_build_adapter` returns `(adapter, runner)` — all test tasks destructure it consistently. `_testmail_fetch` signature `(apikey, namespace, tag, ts_from, timeout=60)` matches all call sites. `_make_inbound_email(from_addr, subject, body)` matches. `_FakeIMAP(raw_bytes)` constructor matches.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-08-06-testmail-e2e.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**