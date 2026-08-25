# Design: Upgrade `browse_page` to interactive browsing via browser-use

## Motivation

agent8088's current `browse_page` tool (mode=`browser`, `_exec_browser` in
`src/agent8088/engine.py`) is a single-shot fetch-and-read: given a URL, it
loads the page in headless Chromium via Playwright's sync API and returns the
page text, optionally scoped to a CSS selector. It cannot click, fill forms,
navigate multi-step, or otherwise interact with a page — it reads one static
snapshot.

[browser-use](https://github.com/browser-use/browser-use) is an LLM-driven
browser-automation library: given a natural-language task, its `Agent` class
runs its own step loop (observe page -> decide next action -> click/type/
scroll/extract -> repeat) until the task is done or a step limit is hit. This
design replaces `_exec_browser`'s implementation with a browser-use-backed
agent loop, while keeping the same tool name, mode string, and permission/
audit/plan-only gating already wired into the engine.

## Current state (for reference)

- Tool exposed to the model: `browse_page` -> mode `browser`, args
  `url` (required), `selector` (optional). Declared in
  `src/agent8088/tools.txt`.
- Implementation: `_exec_browser` (`engine.py:3411`) launches Chromium via
  `playwright.sync_api`, intercepts every network request via
  `page.route("**/*", guard_request)` and runs `_egress_check` +
  `_ssrf_check` (`engine.py:6189`, `engine.py:6253`) against each one —
  not just the top-level navigation. Blocks non-http(s) schemes and any host
  resolving to a private/loopback/link-local/reserved address (including the
  `169.254.169.254` cloud-metadata endpoint), plus a configurable domain
  allow/deny list.
- Output: page text is capped at 5000 chars, run through
  `_strip_special_tokens` and `_wrap_untrusted(..., url)` before returning to
  the model — marking it as untrusted content for prompt-injection defense.
- Playwright's Chromium binary is installed into
  `$AGENT8088_HOME/playwright-browsers` (not the OS-shared cache) so
  `--uninstall` can fully clean it up.
- Permission gating: `mode == "browser"` is one of the gated modes
  (`check_permission("browser", ...)`), blocked outright in `plan-only`
  mode, and audited via `_audit("tool_call", ..., mode="browser", ...)`.
- The whole engine/cli is synchronous today — no asyncio use anywhere.
- browser-use itself is asyncio-only and is built on CDP (via `cdp-use`)
  rather than Playwright's Python API, as of the version researched via
  Context7 (`/browser-use/browser-use`). It exposes no documented
  per-request interception hook — only coarse `allowed_domains` /
  `prohibited_domains` lists and a `ProxySettings` passthrough.

## Goals

- `browse_page` can complete multi-step tasks (click, type, scroll, extract)
  driven by natural language, not just read one page.
- The existing SSRF/egress guard applies to *every* request the browsing
  session makes, not just the first navigation — matching today's guarantee.
- No new, separate LLM credential/config path: browsing steps are driven by
  the same provider/model already configured for the main agent loop.
- Browsing-loop LLM calls are bounded and charged against the user's
  existing token/turn budget, the same way subagent calls are.
- Web content returned to the model remains wrapped as untrusted and capped
  in size, exactly as today.
- Same tool name (`browse_page`) and mode string (`browser`) so permission
  gating, audit logging, and plan-only blocking require no changes.

## Non-goals

- Keeping the old `selector`-only fast path. Task-driven extraction
  supersedes it; dropped from the tool schema.
- A separate/parallel "quick fetch" tool alongside the new interactive one
  (the user chose full upgrade-in-place over "new tool alongside").
- General async migration of the rest of the engine/cli. The asyncio usage
  introduced here is fully contained inside `_exec_browser`.

## Architecture

### 1. Tool interface

`browse_page` keeps its name and `mode=browser`. New args:

- `url` (required) — starting page.
- `task` (required) — natural-language instruction for what to do once
  there (e.g. "Click 'Sign in', fill the form with these credentials,
  extract the confirmation text").

`selector` is removed from the schema. `tools.txt`'s description is updated
to describe interactive multi-step capability instead of "load a page and
return its text."

### 2. Async bridging

browser-use requires an asyncio event loop; the engine is otherwise fully
synchronous. `_exec_browser` wraps the call as:

```python
result = asyncio.run(_run_browser_agent(url, task))
```

Contained entirely inside this one function — nothing else in the engine
needs to become async, and since no event loop is running elsewhere in the
process, `asyncio.run()` here is safe.

### 3. LLM bridging

browser-use's `Agent` takes an `llm` object implementing its own chat-model
interface (`browser_use.llm.*`). Rather than give it a second, independently
configured LLM client, we wrap agent8088's existing litellm-based call path
(`engine.py` around line 1545) in a thin adapter conforming to that
interface (based on `browser_use.llm.litellm.ChatLiteLLM`, subclassed or
wrapped so its actual completions route through agent8088's own model-calling
function). This reuses whatever provider/model/base_url the user already has
configured — no new credential surface.

*Exact class name/import path to be confirmed against the installed
browser-use version during implementation — Context7 docs surfaced
`browser_use.llm.litellm.ChatLiteLLM` but library internals shift between
releases.*

### 4. SSRF/egress guard: local filtering proxy

Since browser-use has no per-request interception hook, protection moves to
the network layer: a small loopback-only HTTP/CONNECT proxy is started for
the duration of each `browse_page` call.

- Binds to `127.0.0.1` on an ephemeral port.
- For a CONNECT request (HTTPS), extracts the `host:port` target and runs
  `_egress_check`/`_ssrf_check` against it before splicing the tunnel;
  refuses with a clear error otherwise.
- For a plain HTTP request line, runs the same checks against the parsed
  URL before forwarding.
- Both existing checks are purely hostname/resolved-IP based (no path or
  query dependency), so a CONNECT-level proxy has exactly the granularity
  needed — this is a faithful port of the existing guard, not a weaker
  approximation.
- Passed to browser-use via `ProxySettings(server="http://127.0.0.1:<port>")`
  so every request the browsing session makes — initial nav, redirects,
  clicked links, form submissions — passes through it.
- Torn down when the call completes (started fresh per call, matching the
  existing pattern of a fresh Chromium instance per call).
- Chromium binary/cache path is unchanged
  (`PLAYWRIGHT_BROWSERS_PATH` under `$AGENT8088_HOME`), since browser-use
  reuses the same Playwright-installed Chromium.

### 5. Bounding and budget accounting

- New config `browser_max_steps` (same pattern as existing
  `browser_timeout_ms`), passed to `Agent.run(max_steps=...)`. Default
  chosen to keep a single call's cost bounded (proposed: 25).
- Every LLM call browser-use's loop makes goes through the adapter from
  step 3, and is charged to `_active_budget` under a `subagent:browser`-style
  role — the same accounting pattern used for the auditor subagent
  (`_active_budget.role_total(...)`) — so a browsing task cannot spend
  tokens outside the user's existing budget ceiling.
- `browser_timeout_ms` (existing config) continues to bound the whole call
  via `asyncio.wait_for()` around `agent.run(...)`, since browser-use has no
  single overall wall-clock timeout parameter of its own (only per-step/
  per-LLM-call timeouts).

### 6. Output handling

`AgentHistoryList.final_result()` (browser-use's answer text) is:

1. Capped at the existing 5000-char limit.
2. Run through `_strip_special_tokens`.
3. Wrapped with `_wrap_untrusted(..., url)`.

Exactly the same trust boundary as today — content from the web is never
handed to the model unmarked, regardless of how many intermediate steps
produced it.

### 7. Dependencies

- Add `browser-use` to `pyproject.toml` and `requirements.txt` as a core
  dependency, alongside the existing `playwright>=1.40,<2` (no separate
  browser binary install needed — same Chromium).
- `litellm` is already a dependency used elsewhere in the engine.

## Error handling

- Playwright/Chromium missing: same degrade-with-install-instructions
  behavior as today (`_playwright_available()` check preserved).
- SSRF proxy refusal: surfaces as a clear "Blocked: ..." string from the
  underlying check, same wording as today, not a generic browser error.
- browser-use step/time limit reached without completion: return whatever
  partial result/history is available plus a note that the task did not
  fully complete, rather than a bare error.
- Any exception during the agent run: caught and returned as
  `"Browser error: {e}"`, matching today's catch-all.

## Testing

- Unit tests for the new SSRF-filtering proxy: CONNECT to a private/
  loopback/link-local target is refused; CONNECT to a normal public host is
  allowed; blocked/allowed domain list interacts correctly with the proxy.
- Unit tests for the LLM adapter: calls route through agent8088's existing
  litellm call path and are visible to budget accounting (mock the
  underlying call, assert `_active_budget` is charged).
- Integration test (mirroring the existing `test_cli_anything_integration.py`
  style): a `browse_page` call against a local test HTTP server, asserting
  the returned text is wrapped/capped correctly and that a task requiring a
  click/form-fill against that local server succeeds end-to-end.
- Regression test: a `browse_page` call targeting a private/loopback address
  is refused end-to-end (through the full tool-dispatch path, not just the
  proxy unit test), preserving today's SSRF regression coverage.
- Existing `browser_timeout_ms` / plan-only-blocking / permission-gating
  tests continue to pass unmodified, since the mode string and gating logic
  are unchanged.

## Open questions / risks to verify during implementation

- Confirm the exact `browser_use.llm.litellm.ChatLiteLLM` (or equivalent)
  import path and constructor signature against the pinned browser-use
  version — Context7 docs may lag a specific release.
- Confirm `ProxySettings` is honored for *all* traffic browser-use's
  CDP-launched Chromium makes (not just document navigation) — verify with
  an integration test that deliberately tries a background XHR to a blocked
  host, not just a top-level navigation.
- Decide the default for `browser_max_steps` based on observed cost during
  implementation testing (25 is a starting proposal, not a measured value).
