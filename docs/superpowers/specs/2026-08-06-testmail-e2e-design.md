# testmail.app E2E Email Test Design

Date: 2026-08-06
Status: Approved

## Problem

The email adapter (`src/agent8088/gateway/platforms/email.py`) has 17 unit tests
that mock IMAP and SMTP at the stdlib boundary. They verify parsing, allowlist
filtering, automated-sender skipping, and config reading — but never send a
real email through a real SMTP server to a real mailbox. A regression in the
SMTP path (TLS negotiation, login, `send_message`, header construction) would
not be caught.

We need an end-to-end test that exercises the full outbound path against a
real mail receiver, runnable locally, skippable in CI.

## Solution

Use [testmail.app](https://testmail.app) as the real email receiver.

testmail.app is a receive-only catch-all inbox: any mail sent to
`{namespace}.{tag}@inbox.testmail.app` is captured and queryable via a simple
JSON API. It does not provide an IMAP server and does not send mail out.

### Key insight

The sender of the synthetic inbound email IS the testmail inbox address. The
adapter replies to the sender (normal email behavior), so the reply lands in
testmail.app where we verify it via API. No adapter changes needed.

### Flow

```
[Mock IMAP returns synthetic email]
  From: {ns}.{tag}@inbox.testmail.app   (tag unique per test run)
    |
    v
[Adapter._process_message: parse, allowlist, dispatch]   (all real code)
    |
    v
[Mock runner.on_message -> adapter.send_message(sender, "Echo: ...")]   (real SMTP)
    |
    v
[SMTP relay (Gmail) -> {ns}.{tag}@inbox.testmail.app]
    |
    v
[Poll testmail JSON API: livequery=true, tag={tag}, timestamp_from=now]   (stdlib urllib)
    |
    v
[Assert: reply arrived, subject has "Re:", In-Reply-To header matches]
```

## Scope

### In scope
- New file `tests/gateway/platforms/test_email_e2e.py`
- ~5 gated test cases
- Zero changes to `email.py` adapter
- Zero changes to existing `test_email.py` unit tests
- Zero new dependencies (stdlib `urllib.request` for testmail API)

### Out of scope
- Testing inbound against a real IMAP server (testmail has no IMAP)
- Testing the LLM agent's reply content (mocked runner)
- CI integration (tests are gated on env vars, skip without them)
- Adapter modifications

## Components

### 1. `_testmail_fetch(apikey, namespace, tag, ts_from, timeout=60)`

Stdlib `urllib.request` GET to:
```
https://api.testmail.app/api/json?apikey={apikey}&namespace={namespace}&tag={tag}&timestamp_from={ts_from}&livequery=true&headers=true
```

- `livequery=true` makes the API wait up to 60s for a matching email, then
  returns HTTP 307 redirect to itself. `urllib.request` follows 307s by
  default, so the call blocks until a match or the overall `timeout`.
- Returns parsed JSON dict on success.
- Raises `TimeoutError` if no match within `timeout`.
- Raises on API error (`result: "fail"`).

### 2. `_FakeIMAP`

Mimics `imaplib.IMAP4_SSL` at the lowest boundary so all adapter parsing runs
for real. Implements:
- `.login(addr, pw)` -> no-op
- `.select("INBOX")` -> `("OK", [b"1"])`
- `.uid("search", None, "UNSEEN")` -> `("OK", [b"1234"])`
- `.uid("fetch", uid_b, "(RFC822)")` -> `("OK", [(None, raw_bytes)])`
- `.logout()` -> no-op

Patched via:
```python
monkeypatch.setattr("imaplib.IMAP4_SSL", lambda *a, **k: fake_imap)
```

### 3. `_EchoRunner`

Fake runner with:
- `.allowlist` — an object with `.is_allowed(addr, platform)` returning
  `True` for the testmail sender address (configurable per test).
- async `.on_message(event)` — calls
  `await adapter.send_message(event.chat_id, f"Echo: {event.text}")`.

### 4. `_make_inbound_email(from_addr, subject, body)`

Builds a real `MIMEText` RFC822 message with `From`, `Subject`, `Message-ID`
headers using `email.mime.text` + `email.utils.make_msgid()`.
Returns `(msg, raw_bytes)` where `raw_bytes = msg.as_bytes()`.

## Test cases

All gated on `TESTMAIL_APIKEY` and `TESTMAIL_NAMESPACE` env vars (or
`~/.agent8088/.env`). Skip via `pytest.skip()` if missing.

| Test | What it verifies |
|---|---|
| `test_e2e_happy_path` | Inbound -> dispatch -> SMTP -> reply arrives in testmail with correct body |
| `test_e2e_threading_headers` | Reply has `In-Reply-To` header matching inbound `Message-ID` |
| `test_e2e_subject_prefix` | Reply subject is `Re: {original}` when original didn't start with `Re:` |
| `test_e2e_unauthorized_sender_dropped` | Allowlist blocks sender -> no reply in testmail (negative test, short timeout) |
| `test_e2e_automated_sender_skipped` | `noreply@...` sender -> no reply in testmail (negative test, short timeout) |

### Poll interval

Patch `POLL_INTERVAL` to `0.5s` via `monkeypatch.setattr` so the adapter
processes the mock IMAP fetch quickly (15s default would make tests crawl).

### Negative tests

`test_e2e_unauthorized_sender_dropped` and `test_e2e_automated_sender_skipped`
assert that NO email arrives in testmail within a short window (e.g. 10s).
They use `pytest.raises(TimeoutError)` around `_testmail_fetch` with a short
timeout. The allowlist/automated-sender filters drop the message before
dispatch, so no SMTP send occurs, and testmail never receives a reply.

### Tag uniqueness

Each test uses a unique tag: `e2e-{test_name}-{uuid4().hex[:8]}`. This
prevents cross-test interference and lets `timestamp_from` filter to only
emails from this test run.

## Configuration

### Required env vars (in `~/.agent8088/.env` or environment)

| Var | Purpose |
|---|---|
| `TESTMAIL_APIKEY` | testmail.app API key (sign up at https://testmail.app/signup) |
| `TESTMAIL_NAMESPACE` | testmail.app namespace from console |
| `EMAIL_ADDRESS` | Gmail address to send FROM (real SMTP) |
| `EMAIL_PASSWORD` | Gmail app-specific password |
| `EMAIL_SMTP_HOST` | e.g. `smtp.gmail.com` |
| `EMAIL_IMAP_HOST` | e.g. `imap.gmail.com` (only needed for mock IMAP construction, not real polling) |

### Gating

```python
apikey = os.environ.get("TESTMAIL_APIKEY")
namespace = os.environ.get("TESTMAIL_NAMESPACE")
if not apikey or not namespace:
    pytest.skip("TESTMAIL_APIKEY and TESTMAIL_NAMESPACE required for e2e tests")
```

Tests read `EMAIL_*` vars via `A.get_secret()` (same path the adapter uses),
so they pick up `~/.agent8088/.env` automatically.

## What's NOT changing

- `src/agent8088/gateway/platforms/email.py` — zero changes
- `tests/gateway/platforms/test_email.py` — existing 17 unit tests untouched
- `pyproject.toml` — no new dependencies
- No CI integration required (tests self-skip without secrets)

## Verification

After implementation:
1. `pytest tests/gateway/platforms/test_email.py -q` — existing unit tests still pass (17)
2. `pytest tests/gateway/platforms/test_email_e2e.py -q` — skips without creds, runs with creds
3. With creds: full suite green, real email sent to testmail.app and verified via API

## Future

- Could add HTML body, attachment, and long-body tests later
- Could wire into CI with secrets injection when needed
- Could test the MCP server's email-related tools end-to-end