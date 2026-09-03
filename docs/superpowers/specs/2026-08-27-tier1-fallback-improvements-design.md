# Tier 1 Fallback Improvements

**Date:** 2026-08-27
**Status:** Implemented

## Problem

Agent8088's `_create_completion_with_fallback` (engine.py:1762-1816) falls
through to the next provider on the first retryable error — zero retries on the
primary. A single transient 429 or 5xx kills the primary for the entire turn.

## Design

Four improvements, all scoped to `_create_completion_with_fallback` and its
helpers. No new files, no new modules, no state persistence.

### 1. Retry before failover

Retry the same provider up to `api_max_retries` (default 3) with exponential
backoff before falling through the `fallback_models` chain.

```
Primary fails (429/5xx/timeout)
  retry 1: wait 500ms × 2^0 × jitter
  retry 2: wait 500ms × 2^1 × jitter
  retry 3: wait 500ms × 2^2 × jitter (capped at 10s)
  → all retries exhausted → fall through to fallback_models chain (single pass, no retry)
```

Backoff formula:
```python
delay = min(initial_delay_ms * 2 ** min(retry - 1, 1024), max_delay_ms)
delay *= 1 - jitter_ratio + 2 * jitter_ratio * random()
```

Defaults: `initial_delay_ms=500`, `max_delay_ms=10000`, `jitter_ratio=0.1` (±10%).

### 2. Honor Retry-After header

When the provider returns a `Retry-After` header (429 responses), use that value
instead of our computed backoff.

- Parse from `error.response.headers['retry-after']` — supports seconds (`"5"`)
  and HTTP-date (`"Wed, 21 Oct 2026 07:28:00 GMT"`)
- If ≤ `max_delay_ms`: wait that exact value, unjittered
- If > `max_delay_ms`: skip remaining retries — fall through to the next provider
- If header absent or unparseable: use exponential backoff

### 3. Per-turn scoping (no change needed)

Each turn calls `_create_completion_with_fallback()` fresh at engine.py:7251.
The primary is always tried first. Fallback only lasts for the turn that failed.
This is already the behavior — no code change.

### 4. Auth/404 skip retries

| Error | Retry? | Why |
|---|---|---|
| 429 | Yes | Transient |
| 500/502/503/504 | Yes | Server error |
| 408 | Yes | Timeout |
| 401 | No | Bad key |
| 403 | No | Forbidden |
| 404 | No | Model gone |
| 400 | No | Bad request |

The existing `_retryable_model_error()` already handles this (only 429 and ≥500
return True). Extended to also extract `Retry-After` and pass it to the loop.

## Config keys

```ini
# Retry before failover (0 = immediate failover, no retry)
api_max_retries=3
# api_retry_initial_delay_ms=500
# api_retry_max_delay_ms=10000
# api_retry_jitter_ratio=0.1
```

All read from `APP_CONFIG` as module globals at the top of
`_create_completion_with_fallback`, matching the existing pattern
(`MAX_COMPLETION_TOKENS`, `CONTEXT_WINDOW`, etc.).

## Code changes

### engine.py — three additions

**New helper: `_extract_retry_after(error) -> int | None`**
```python
def _extract_retry_after(error):
    """Parse Retry-After header (seconds or HTTP-date) from an OpenAI SDK error."""
    resp = getattr(error, "response", None)
    if not resp or not hasattr(resp, "headers"):
        return None
    raw = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0, int(raw) * 1000)  # header is in seconds; we work in ms
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        return max(0, int((dt.timestamp() - time.time()) * 1000))
    except Exception:
        return None
```

**New helper: `_retry_delay(retry_attempt, retry_after_ms) -> float`**
```python
def _retry_delay(retry_attempt, retry_after_ms=None):
    if retry_after_ms and retry_after_ms <= API_RETRY_MAX_DELAY_MS:
        return retry_after_ms / 1000.0
    exponent = min(retry_attempt - 1, 1024)
    delay = min(API_RETRY_INITIAL_DELAY_MS * 2 ** exponent, API_RETRY_MAX_DELAY_MS)
    jitter = 1 - API_RETRY_JITTER_RATIO + 2 * API_RETRY_JITTER_RATIO * random.random()
    return (delay * jitter) / 1000.0
```

**Rewrite: `_create_completion_with_fallback`**
- Wrap the primary attempt in a retry loop (up to `API_MAX_RETRIES`)
- On each retryable error: extract Retry-After, compute delay, sleep, retry
- On non-retryable error: raise immediately (no retry)
- After all retries exhausted: fall through to the existing fallback chain walk
- The fallback chain walk itself is unchanged (single pass, no retry on fallbacks)

### config.txt — documented keys

Add `api_max_retries` and the three tuning keys to the commented config template.

## What's NOT changing

- `_fallback_targets()` — unchanged
- `fallback_models` config key format — unchanged
- Fallback chain walk (single pass, no retry on fallbacks) — unchanged
- Per-turn scoping — already works
- No cooldowns, no per-key rate tracking, no context handoff, no circuit breaker
  (those are Tier 2/3/4, out of scope)

## Testing

- Unit: retry loop retries N times on 429, then falls through
- Unit: 401/403/404 raise immediately (no retry)
- Unit: Retry-After header honored (seconds + HTTP-date)
- Unit: Retry-After > max_delay_ms skips remaining retries
- Unit: `api_max_retries=0` means immediate failover (no retry)
- Unit: exponential backoff respects jitter and cap
- Integration: existing fallback chain tests still pass (back-compat)

## Implementation notes

- `_extract_retry_after` returns milliseconds. The numeric-seconds branch
  multiplies by 1000 (`int(raw) * 1000`) — the draft above under-normalized
  this to raw seconds, which would have compared seconds against
  `API_RETRY_MAX_DELAY_MS` and slept for milliseconds' worth of seconds.
  Fixed before implementation landed.
- Config globals (`API_MAX_RETRIES`, `API_RETRY_INITIAL_DELAY_MS`,
  `API_RETRY_MAX_DELAY_MS`, `API_RETRY_JITTER_RATIO`) were already present in
  `engine.py` at the top-level config block; only the helpers and the
  retry loop inside `_create_completion_with_fallback` were missing and have
  now been added (engine.py, `_extract_retry_after`, `_retry_delay`,
  `_create_completion_with_fallback`).
- `random` added to the stdlib import line at the top of `engine.py`.
- `fallback_models` and the four retry keys documented in `config.txt`
  (previously undocumented).
- Tests added in `tests/test_fallback_retry.py`: retry-then-succeed,
  non-retryable raises immediately, retries exhausted falls through to
  fallback, `api_max_retries=0` skips retries, an over-cap Retry-After skips
  remaining retries, `_extract_retry_after` seconds parsing, and
  `_retry_delay` cap/backoff math. All pass; full existing suite unaffected
  (verified modulo pre-existing, unrelated collection errors in
  tests caused by direct `from agent8088 import`
  outside the conftest sys.path fixture — not touched by this change).

## Usage after the change

```ini
# config.txt
api_max_retries=3
fallback_models=ollama-cloud:glm-5.3-flash,openrouter:anthropic/claude-sonnet-4
```

```
# Inside the agent — set live:
/limits api_max_retries 5
```
