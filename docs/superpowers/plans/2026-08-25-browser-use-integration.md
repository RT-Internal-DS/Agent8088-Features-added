# browser-use Interactive Browsing Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace agent8088's single-shot "fetch a page and read its text" `browse_page` tool with an interactive browsing tool (click, type, scroll, navigate, extract) powered by the `browser-use` library, without weakening today's SSRF guard, budget accounting, or untrusted-content wrapping.

**Architecture:** `_exec_browser` in `engine.py` keeps its existing pre-flight checks (URL required, egress/SSRF check, Playwright/Chromium availability), then hands off to a new `asyncio`-driven helper that runs a `browser_use.Agent` loop. That loop is driven by a thin `ChatLiteLLM` subclass that reuses agent8088's already-configured provider and charges every call to the caller's existing token budget, and every request the browser makes is routed through a small local SSRF-filtering proxy that reuses `_egress_check`/`_ssrf_check` verbatim. Output still goes through the existing `_strip_special_tokens`/`_wrap_untrusted`/size-cap pipeline.

**Tech Stack:** Python, `browser-use==0.13.x`, `litellm` (already a dependency), `playwright` (already a dependency, unchanged), stdlib `asyncio`/`http.server`/`socketserver`/`socket` for the local proxy, `pytest`/`monkeypatch` for tests.

**Spec:** `docs/superpowers/specs/2026-08-25-browser-use-integration-design.md`

## Global Constraints

- Tool name stays `browse_page`; mode string stays `browser` — no changes to permission gating, plan-only blocking, or audit logging in `engine.py`.
- `browser-use` is pinned `>=0.13,<0.14` — this plan was verified line-by-line against the installed 0.13.8 source; a future major/minor bump needs its own re-verification pass, not a silent version bump.
- `litellm` is a **core** dependency (`>=1,<2`), not the pre-existing optional `[litellm]` extra — every provider config's browsing calls route through it now (see Task 1's ruling), matching the existing `browser`/`search` core-promotion precedent already in `pyproject.toml`.
- Every request the browsing session makes must pass through the SSRF-filtering proxy — no code path may launch browser-use's browser without `ProxySettings` pointed at it.
- Every LLM call browser-use's loop makes must be charged to the caller's `_TurnBudget` (the same object `run_agent`/`_exec_subagent` share) via `add_tokens`, using the exact field names `prompt_tokens`/`completion_tokens` from `ChatInvokeUsage`.
- Final output returned to the model is capped at 5000 chars, run through `_strip_special_tokens`, then `_wrap_untrusted(..., url)` — same as today, no exceptions.
- `selector` is removed from `browse_page`'s schema; `task` is added and required alongside `url`.
- The `Agent(...)` construction in `_run_browser_agent` must pass `use_vision=False` — confirmed via live testing (not just reading docs) that the default `True` unconditionally sends a screenshot every step and hard-errors against any model that doesn't accept image input, which breaks `browse_page` outright for a real share of agent8088's supported providers. `"auto"` was checked and rejected too (still exposes an optional screenshot action the model can invoke on its own). `False` is the only mode verified to never send an image.

---

### Task 1: Add browser-use as a core dependency, and promote litellm to core too

**Files:**
- Modify: `pyproject.toml` (core `dependencies` list ~line 27; `[project.optional-dependencies]` block ~lines 48-68)
- Modify: `requirements.txt` (near the existing `playwright>=1.40,<2` line, ~line 30)

**Interfaces:**
- Produces: the `browser_use` and `litellm` packages importable in the venv used by later tasks (`import browser_use`, `import litellm`, `from browser_use import Agent, BrowserProfile`, and `from browser_use.browser import ProxySettings` all succeed — `ProxySettings` is not re-exported from the top-level `browser_use` package in 0.13.8, unlike `Agent`/`BrowserProfile`).

**Ruling carried into this task (recorded during plan setup):** `litellm` is
currently an *optional* extra (`pip install agent8088[litellm]`, only meant
for a provider profile with `api_mode=litellm`) — see the comment directly
above its line in `pyproject.toml`. Task 3's adapter always builds a
`browser_use.llm.litellm.ChatLiteLLM` regardless of which provider the main
agent loop is configured with, and that class unconditionally does
`from litellm import acompletion` inside `ainvoke`. If `litellm` stays
optional, `browse_page` breaks for any user who never opted into that
extra — independent of which provider they actually use. This repo already
has precedent for exactly this situation: `browser` and `search` were
promoted from optional to core for the identical reason (see the comment
above `browser = [...]` in `pyproject.toml`: "optional was the wrong
trade... it made X a property of whether a best-effort install step
happened to succeed, and both degrade silently"). This task applies the
same fix to `litellm`.

- [ ] **Step 1: Add both dependencies to `pyproject.toml`'s core list**

Add these two lines directly after the existing `"playwright>=1.40,<2",` entry (~line 27) in the core `dependencies` list:

```toml
    "browser-use>=0.13,<0.14",
    "litellm>=1,<2",
```

- [ ] **Step 2: Turn the `litellm` extra into a back-compat alias**

In `pyproject.toml`'s `[project.optional-dependencies]` block, replace:

```toml
# Only needed for a provider profile with api_mode=litellm. Kept out of the base
# install because it is a large dependency serving one optional backend, but
# declared so `pip install agent8088[litellm]` works instead of leaving the user
# to discover the package name from a runtime error.
litellm = ["litellm>=1,<2"]
```

with:

```toml
# litellm is now a core dependency above (browse_page's interactive
# browsing needs it regardless of which provider the main agent loop uses).
# Kept as an alias, like `browser` and `search` above, so
# `pip install agent8088[litellm]` stays valid instead of erroring on an
# unknown extra.
litellm = ["litellm>=1,<2"]
```

- [ ] **Step 3: Add both dependencies to `requirements.txt`**

Add these two lines directly after the existing `playwright>=1.40,<2` line (~line 30):

```
browser-use>=0.13,<0.14
litellm>=1,<2
```

Also check `requirements.txt` for a separate commented-out `litellm: litellm>=1,<2` extras line (there is one, documenting the `pip install -e ".[litellm]"` extra, mirroring the `pyproject.toml` extras comment) and update its comment the same way Step 2 did, so the two files stay consistent — it should no longer read as "only needed for one optional backend" once litellm is a core dependency.

- [ ] **Step 4: Install and verify**

Run: `pip install -e .`
Then: `python -c "from browser_use import Agent, BrowserProfile; from browser_use.browser import ProxySettings; import litellm; print('ok')"`
Expected: prints `ok` with no import errors.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml requirements.txt
git commit -m "build: add browser-use, promote litellm to a core dependency"
```

---

### Task 2: Local SSRF-filtering forward proxy

**Files:**
- Create: `src/agent8088/browser_proxy.py`
- Test: `tests/test_browser_proxy.py`

**Interfaces:**
- Consumes: a `check_target(url: str) -> str | None` callable (same contract as `_egress_check`/`_ssrf_check` — `None` means allowed, a non-empty string is the block reason). Task 4 will pass `lambda u: _egress_check(u) or _ssrf_check(u)`.
- Produces: `start_ssrf_filtering_proxy(check_target) -> tuple[str, Callable[[], None]]` — returns `(proxy_url, stop_fn)` where `proxy_url` is like `"http://127.0.0.1:54321"` and calling `stop_fn()` shuts the proxy down. Task 4 relies on this exact name and return shape.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_browser_proxy.py`:

```python
"""A small loopback-only HTTP/CONNECT proxy that runs the same host/IP-based
SSRF check on every request browser-use's Chromium makes, not just the first
navigation - see docs/superpowers/specs/2026-08-25-browser-use-integration-design.md
section 4 for why this exists (browser-use has no page.route()-style hook)."""
import http.client
import socket

import pytest

from agent8088.browser_proxy import start_ssrf_filtering_proxy


def _connect_raw(port, target):
    """Send a raw CONNECT request and return (status_code, reason)."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    response = sock.recv(4096).decode(errors="replace")
    sock.close()
    status_line = response.splitlines()[0]
    _, code, *reason = status_line.split(" ", 2)
    return int(code), " ".join(reason)


def test_connect_to_blocked_target_is_refused():
    proxy_url, stop = start_ssrf_filtering_proxy(lambda url: "Blocked: test policy.")
    port = int(proxy_url.rsplit(":", 1)[1])
    try:
        code, reason = _connect_raw(port, "10.0.0.5:443")
        assert code == 403
        assert "Blocked" in reason
    finally:
        stop()


def test_connect_to_allowed_target_establishes_tunnel():
    proxy_url, stop = start_ssrf_filtering_proxy(lambda url: None)
    port = int(proxy_url.rsplit(":", 1)[1])
    # Bind a local "upstream" server to CONNECT through to, so the test has no
    # real network dependency.
    upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)
    upstream_port = upstream.getsockname()[1]
    try:
        code, _ = _connect_raw(port, f"127.0.0.1:{upstream_port}")
        assert code == 200
    finally:
        stop()
        upstream.close()


def test_check_target_receives_a_url_shaped_string_for_connect():
    seen = []

    def check(url):
        seen.append(url)
        return "Blocked: test policy."

    proxy_url, stop = start_ssrf_filtering_proxy(check)
    port = int(proxy_url.rsplit(":", 1)[1])
    try:
        _connect_raw(port, "example.com:443")
    finally:
        stop()
    assert len(seen) == 1
    assert "example.com" in seen[0]
    assert "443" in seen[0]


def test_plain_http_get_to_blocked_target_is_refused():
    proxy_url, stop = start_ssrf_filtering_proxy(lambda url: "Blocked: test policy.")
    port = int(proxy_url.rsplit(":", 1)[1])
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "http://10.0.0.5/some/path")
        resp = conn.getresponse()
        assert resp.status == 403
    finally:
        stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_browser_proxy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent8088.browser_proxy'`

- [ ] **Step 3: Write the implementation**

Create `src/agent8088/browser_proxy.py`:

```python
"""Loopback-only HTTP/CONNECT forward proxy that runs a caller-supplied
host/IP check on every request before forwarding it.

browser-use (the interactive-browsing library _exec_browser delegates to)
has no per-request interception hook equivalent to Playwright's page.route(),
which is what the old single-shot browse_page used to run _egress_check and
_ssrf_check against every request the page made, not just the first
navigation. This proxy restores that guarantee at the network layer instead:
point browser-use's ProxySettings at it and every request - initial nav,
redirects, clicked links, form posts - passes through the same check.

Both existing checks are purely hostname/resolved-IP based (no path or query
dependency), so a CONNECT-level proxy has exactly the granularity needed.
"""
import http.server
import socket
import socketserver
import threading
from typing import Callable, Optional, Tuple


class _SSRFFilteringHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass  # silence default request logging to stderr

    def do_CONNECT(self):
        host, _, port_str = self.path.partition(":")
        port = int(port_str or 443)
        blocked = self.server.check_target(f"https://{host}:{port}/")
        if blocked:
            self.send_error(403, blocked)
            return
        try:
            upstream = socket.create_connection((host, port), timeout=10)
        except OSError as exc:
            self.send_error(502, f"Could not connect to {host}:{port}: {exc}")
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        self._relay(self.connection, upstream)

    def _do_forward(self, method):
        blocked = self.server.check_target(self.path)
        if blocked:
            self.send_error(403, blocked)
            return
        import urllib.parse
        parsed = urllib.parse.urlparse(self.path)
        host = parsed.hostname
        port = parsed.port or 80
        if not host:
            self.send_error(400, "Malformed request target")
            return
        try:
            upstream = socket.create_connection((host, port), timeout=10)
        except OSError as exc:
            self.send_error(502, f"Could not connect to {host}:{port}: {exc}")
            return
        target = urllib.parse.urlunparse(
            ("", "", parsed.path or "/", parsed.params, parsed.query, ""))
        upstream.sendall(f"{method} {target} HTTP/1.1\r\n".encode())
        for key, value in self.headers.items():
            if key.lower() == "proxy-connection":
                continue
            upstream.sendall(f"{key}: {value}\r\n".encode())
        upstream.sendall(b"\r\n")
        content_length = int(self.headers.get("Content-Length", 0) or 0)
        if content_length:
            upstream.sendall(self.rfile.read(content_length))
        self._relay(self.connection, upstream)

    def do_GET(self):
        self._do_forward("GET")

    def do_POST(self):
        self._do_forward("POST")

    def do_HEAD(self):
        self._do_forward("HEAD")

    def do_PUT(self):
        self._do_forward("PUT")

    @staticmethod
    def _relay(client_sock, upstream_sock):
        def pipe(src, dst):
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        t1 = threading.Thread(target=pipe, args=(client_sock, upstream_sock), daemon=True)
        t2 = threading.Thread(target=pipe, args=(upstream_sock, client_sock), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        upstream_sock.close()


class _SSRFFilteringProxyServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, check_target: Callable[[str], Optional[str]]):
        super().__init__(("127.0.0.1", 0), _SSRFFilteringHandler)
        self.check_target = check_target


def start_ssrf_filtering_proxy(
    check_target: Callable[[str], Optional[str]],
) -> Tuple[str, Callable[[], None]]:
    """Start a loopback-only proxy that runs `check_target(url)` (returning
    None if allowed, else an error string - the same contract as
    _egress_check/_ssrf_check) before forwarding every request.

    Returns (proxy_url, stop_fn). Call stop_fn() to shut the proxy down."""
    server = _SSRFFilteringProxyServer(check_target)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def stop():
        server.shutdown()
        server.server_close()

    return f"http://127.0.0.1:{port}", stop
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_browser_proxy.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent8088/browser_proxy.py tests/test_browser_proxy.py
git commit -m "feat(browser): add local SSRF-filtering forward proxy"
```

---

### Task 3: LiteLLM-backed chat model adapter for browser-use

**Files:**
- Create: `src/agent8088/browser_llm.py`
- Test: `tests/test_browser_llm.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `build_browser_chat_model(client, model_name, budget=None) -> Agent8088ChatModel`. Task 4 calls this with `client`/`MODEL_NAME` (engine.py's module-level provider config) and `budget=_active_budget`. `Agent8088ChatModel` is a `browser_use.llm.litellm.ChatLiteLLM` subclass with one extra field, `budget`, which — when not `None` — must expose `.exceeded() -> str | None` and `.add_tokens(prompt: int, completion: int) -> None` (this is exactly `engine._TurnBudget`'s interface; tests use a lightweight fake with the same two methods so this file never has to import `engine`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_browser_llm.py`:

```python
"""Agent8088ChatModel lets browser-use's Agent loop reuse agent8088's own
already-configured provider (no second LLM credential path) and charges
every call to the caller's existing turn budget, so a browsing task can't
spend tokens the user's budget ceiling doesn't know about."""
import pytest

from agent8088.browser_llm import Agent8088ChatModel, build_browser_chat_model


class _FakeUsage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class _FakeCompletion:
    def __init__(self, usage):
        self.completion = "done"
        self.usage = usage


class _FakeBudget:
    def __init__(self, exceeded_reason=None):
        self._exceeded_reason = exceeded_reason
        self.charged = []

    def exceeded(self):
        return self._exceeded_reason

    def add_tokens(self, prompt, completion):
        self.charged.append((prompt, completion))


def test_build_browser_chat_model_from_litellm_style_client():
    client = {"api_mode": "litellm", "api_base": "https://api.example.com", "api_key": "sk-x"}
    model = build_browser_chat_model(client, "openai/gpt-4o", budget=None)
    assert isinstance(model, Agent8088ChatModel)
    assert model.model == "openai/gpt-4o"
    assert model.api_base == "https://api.example.com"
    assert model.api_key == "sk-x"


def test_build_browser_chat_model_from_openai_sdk_style_client():
    class _FakeSDKClient:
        api_key = "sk-y"
        base_url = "http://localhost:11434/v1"

    model = build_browser_chat_model(_FakeSDKClient(), "llama3", budget=None)
    assert model.model == "openai/llama3"
    assert model.api_base == "http://localhost:11434/v1"
    assert model.api_key == "sk-y"


@pytest.mark.asyncio
async def test_ainvoke_charges_the_budget_on_success(monkeypatch):
    budget = _FakeBudget()
    model = Agent8088ChatModel(model="openai/gpt-4o", budget=budget)

    async def fake_super_ainvoke(self, messages, output_format=None, **kwargs):
        return _FakeCompletion(_FakeUsage(prompt_tokens=100, completion_tokens=20))

    monkeypatch.setattr(
        "browser_use.llm.litellm.ChatLiteLLM.ainvoke", fake_super_ainvoke, raising=True)

    result = await model.ainvoke([])

    assert result.completion == "done"
    assert budget.charged == [(100, 20)]


@pytest.mark.asyncio
async def test_ainvoke_refuses_when_budget_already_exceeded():
    budget = _FakeBudget(exceeded_reason="Turn budget exceeded: 9999 tokens used (limit 1000).")
    model = Agent8088ChatModel(model="openai/gpt-4o", budget=budget)

    with pytest.raises(RuntimeError, match="Turn budget exceeded"):
        await model.ainvoke([])

    assert budget.charged == []


@pytest.mark.asyncio
async def test_ainvoke_without_a_budget_still_works(monkeypatch):
    model = Agent8088ChatModel(model="openai/gpt-4o", budget=None)

    async def fake_super_ainvoke(self, messages, output_format=None, **kwargs):
        return _FakeCompletion(_FakeUsage(prompt_tokens=5, completion_tokens=5))

    monkeypatch.setattr(
        "browser_use.llm.litellm.ChatLiteLLM.ainvoke", fake_super_ainvoke, raising=True)

    result = await model.ainvoke([])

    assert result.completion == "done"
```

This suite needs `pytest-asyncio`, which is not yet a dependency of this
repo (confirmed: no `pytest-asyncio` in `pyproject.toml` or
`requirements.txt`, and no `[tool.pytest.ini_options]` section exists yet
either). Add it to `requirements.txt` (next to the other test-only tooling)
and to `pyproject.toml`'s dev/test optional-dependencies group, then add:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

to `pyproject.toml` before running the tests below. (Task 5 adds a
`markers` key under this same `[tool.pytest.ini_options]` table later —
if this section already exists by the time Task 5 runs, extend it rather
than adding a second `[tool.pytest.ini_options]` table.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_browser_llm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent8088.browser_llm'`

- [ ] **Step 3: Write the implementation**

Create `src/agent8088/browser_llm.py`:

```python
"""Bridges browser-use's Agent to agent8088's own already-configured LLM
provider, instead of wiring a second, independent LLM credential path.

Agent8088ChatModel subclasses browser-use's own ChatLiteLLM
(browser_use.llm.litellm.ChatLiteLLM) and adds one thing: every call is
charged against a caller-supplied budget object (engine._TurnBudget, passed
in by _exec_browser as _active_budget) using the exact same add_tokens()
call run_agent()'s own loop uses - so a multi-step browsing task can't spend
tokens outside the user's existing turn budget ceiling.
"""
from dataclasses import dataclass
from typing import Any, Optional

from browser_use.llm.litellm import ChatLiteLLM


@dataclass
class Agent8088ChatModel(ChatLiteLLM):
    budget: Optional[Any] = None  # duck-typed engine._TurnBudget: .exceeded() / .add_tokens()

    async def ainvoke(self, messages, output_format=None, **kwargs):
        if self.budget is not None:
            over = self.budget.exceeded()
            if over:
                raise RuntimeError(over)
        result = await super().ainvoke(messages, output_format, **kwargs)
        if self.budget is not None and result.usage is not None:
            self.budget.add_tokens(result.usage.prompt_tokens, result.usage.completion_tokens)
        return result


def build_browser_chat_model(client, model_name: str, budget=None) -> Agent8088ChatModel:
    """Build a browser-use chat model that targets the exact same
    provider/model engine.py's main loop is already configured for.

    `client` is engine.py's module-level `client` global: either a litellm-
    mode dict ({"api_mode": "litellm", "api_base": ..., "api_key": ...}) or
    an OpenAI-SDK-style object (has .base_url / .api_key attributes) for
    non-litellm provider configs. Both are normalized into a litellm model
    string here, since ChatLiteLLM always calls litellm under the hood -
    an OpenAI-SDK-style client's base_url/api_key describe an
    OpenAI-compatible endpoint, which litellm can also reach via the
    `openai/<model>` provider prefix plus a custom api_base."""
    if isinstance(client, dict) and client.get("api_mode") == "litellm":
        return Agent8088ChatModel(
            model=model_name,
            api_key=client.get("api_key") or None,
            api_base=client.get("api_base") or None,
            budget=budget,
        )
    api_key = getattr(client, "api_key", None)
    base_url = getattr(client, "base_url", None)
    return Agent8088ChatModel(
        model=f"openai/{model_name}",
        api_key=api_key,
        api_base=str(base_url) if base_url else None,
        budget=budget,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_browser_llm.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent8088/browser_llm.py tests/test_browser_llm.py pyproject.toml requirements.txt
git commit -m "feat(browser): add litellm-backed chat model adapter for browser-use"
```

---

### Task 4: Rewrite `_exec_browser` to drive an interactive browser-use Agent

**Files:**
- Modify: `src/agent8088/engine.py:3400-3486` (the `BROWSER_TIMEOUT_MS` constant and `_exec_browser` function)
- Modify: `src/agent8088/tools.txt:32` (the `browse_page` line)
- Modify: `tests/test_browse_page_missing_chromium.py` (second test needs rewriting — see Step 4)
- Test: `tests/test_exec_browser_agent.py` (new)

**Interfaces:**
- Consumes: `start_ssrf_filtering_proxy` (Task 2), `build_browser_chat_model` (Task 3), and engine.py's existing module-level `client`, `MODEL_NAME`, `_active_budget`, `_active_role`, `_egress_check`, `_ssrf_check`, `_playwright_available`, `_agent_data_dir`, `_wrap_untrusted`, `_strip_special_tokens`.
- Produces: `_exec_browser(args: dict) -> str` (same name/signature as today — `args` now needs both `url` and `task` keys) and a new module-level async helper `_run_browser_agent(url: str, task: str) -> str` that Task 4's own tests, and any later test, can monkeypatch directly.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_exec_browser_agent.py`:

```python
"""_exec_browser drives an interactive browser-use Agent instead of a single
page.goto()+read. These tests stub _run_browser_agent (the async helper that
actually talks to browser-use) so they exercise _exec_browser's own argument
validation, pre-flight checks, role/budget bookkeeping, and output wrapping
without needing a real browser or model."""
import sys
import types

import pytest

from agent8088 import engine as A


class _FakeChromium:
    def __init__(self, executable_path):
        self.executable_path = executable_path


class _FakePlaywrightSession:
    def __init__(self, executable_path):
        self.chromium = _FakeChromium(executable_path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_present_chromium(monkeypatch, tmp_path):
    present_path = tmp_path / "chrome.exe"
    present_path.write_text("stub")
    fake_module = types.SimpleNamespace(
        sync_playwright=lambda: _FakePlaywrightSession(str(present_path)))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)
    monkeypatch.setattr(A, "_playwright_available", lambda: True)
    monkeypatch.setattr(A, "_egress_check", lambda url: None)
    monkeypatch.setattr(A, "_ssrf_check", lambda url: None)


def test_missing_task_is_a_clean_error(monkeypatch, tmp_path):
    _install_present_chromium(monkeypatch, tmp_path)

    result = A._exec_browser({"url": "https://example.com"})

    assert result == "Error: browser tool requires 'task'."


def test_runs_the_browser_agent_and_wraps_the_result(monkeypatch, tmp_path):
    _install_present_chromium(monkeypatch, tmp_path)
    calls = []

    async def fake_run_browser_agent(url, task):
        calls.append((url, task))
        return "The heading says Hello."

    monkeypatch.setattr(A, "_run_browser_agent", fake_run_browser_agent)

    result = A._exec_browser({"url": "https://example.com", "task": "read the heading"})

    assert calls == [("https://example.com", "read the heading")]
    assert "The heading says Hello." in result
    assert "<<<EXTERNAL_UNTRUSTED_CONTENT" in result


def test_sets_and_restores_active_role_around_the_run(monkeypatch, tmp_path):
    _install_present_chromium(monkeypatch, tmp_path)
    seen_role = {}

    async def fake_run_browser_agent(url, task):
        seen_role["during"] = A._active_role
        return "ok"

    monkeypatch.setattr(A, "_run_browser_agent", fake_run_browser_agent)
    monkeypatch.setattr(A, "_active_role", "main")

    A._exec_browser({"url": "https://example.com", "task": "read the heading"})

    assert seen_role["during"] == "subagent:browser"
    assert A._active_role == "main"


def test_active_role_restored_even_when_the_run_raises(monkeypatch, tmp_path):
    _install_present_chromium(monkeypatch, tmp_path)

    async def fake_run_browser_agent(url, task):
        raise RuntimeError("boom")

    monkeypatch.setattr(A, "_run_browser_agent", fake_run_browser_agent)
    monkeypatch.setattr(A, "_active_role", "main")

    result = A._exec_browser({"url": "https://example.com", "task": "read the heading"})

    assert "Browser error" in result
    assert A._active_role == "main"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_exec_browser_agent.py -v`
Expected: FAIL — `test_missing_task_is_a_clean_error` fails because today's `_exec_browser` doesn't require `task` at all (it will instead fail later trying to reach the network); the other three fail with `AttributeError: module 'agent8088.engine' has no attribute '_run_browser_agent'`.

- [ ] **Step 3: Rewrite `_exec_browser` and add `_run_browser_agent`**

In `src/agent8088/engine.py`, replace lines 3400-3486 (from `BROWSER_TIMEOUT_MS = ...` through the end of `_exec_browser`) with:

```python
BROWSER_MAX_STEPS = int(APP_CONFIG.get("browser_max_steps", "25"))
BROWSER_TASK_TIMEOUT_SECONDS = int(APP_CONFIG.get("browser_task_timeout_seconds", "300"))


def _playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:
        return False


async def _run_browser_agent(url: str, task: str) -> str:
    """Drive one browser-use Agent run and return its final result text.

    Every request the browser makes passes through a fresh local SSRF-
    filtering proxy (browser_proxy.py) that runs the same _egress_check/
    _ssrf_check the old single-shot tool ran on every request, not just the
    first navigation - see the design spec, section 4. The Agent's own LLM
    calls are charged to the caller's active turn budget via
    Agent8088ChatModel (browser_llm.py), so a multi-step task can't spend
    tokens outside the user's existing budget ceiling.
    """
    from browser_use import Agent, BrowserProfile
    from browser_use.browser import ProxySettings
    from agent8088.browser_llm import build_browser_chat_model
    from agent8088.browser_proxy import start_ssrf_filtering_proxy

    proxy_url, stop_proxy = start_ssrf_filtering_proxy(
        lambda target_url: _egress_check(target_url) or _ssrf_check(target_url))
    try:
        llm = build_browser_chat_model(client, MODEL_NAME, budget=_active_budget)
        profile = BrowserProfile(proxy=ProxySettings(server=proxy_url))
        agent = Agent(
            task=task,
            llm=llm,
            browser_profile=profile,
            initial_actions=[{"navigate": {"url": url, "new_tab": False}}],
            # browser-use defaults to use_vision=True (sends a screenshot with
            # every step) and errors out entirely against a model that
            # doesn't accept image input - which is exactly the situation for
            # a large share of the providers/models agent8088 supports (local
            # or text-only). Since this adapter is required to work with
            # "whatever provider the user already has configured," not just
            # vision-capable ones, screenshots must be off unconditionally.
            # use_vision="auto" was considered and rejected: it still exposes
            # a "screenshot" action the model can choose to call on its own,
            # which would hit the same failure - only False fully disables
            # both the automatic per-step screenshot and that action.
            use_vision=False,
        )
        history = await asyncio.wait_for(
            agent.run(max_steps=BROWSER_MAX_STEPS),
            timeout=BROWSER_TASK_TIMEOUT_SECONDS,
        )
    finally:
        stop_proxy()

    result = history.final_result() or "(The task did not produce a final result.)"
    if not history.is_done():
        result += "\n\n(Note: the browsing task hit its step or time limit before finishing.)"
    return result


def _exec_browser(args: dict) -> str:
    """Load a page and complete a task on it in a real headless browser --
    click, fill forms, navigate, and extract information via natural-
    language instructions. SSRF-guarded on every request the session makes,
    not just the first navigation. Degrades with install instructions when
    Playwright isn't present.

    `playwright` the Python package is a core dependency (always installed),
    but the Chromium *browser binary* is a separate ~280 MB download the
    installer fetches afterward and can fail or be skipped independently
    (network blip, disk space, antivirus). `_playwright_available` alone
    cannot see that gap - it would report available and let the missing-
    binary case fall through to a multi-paragraph "Executable doesn't
    exist" error. Checking the resolved executable_path up front, with the
    same Playwright session browser-use itself will use, catches that case
    with a clear message instead.
    """
    global _active_role

    url = str(args.get("url") or "").strip()
    if not url:
        return "Error: browser tool requires 'url'."
    blocked = _egress_check(url) or _ssrf_check(url)
    if blocked:
        return blocked
    if not _playwright_available():
        return ("Playwright is not installed. Install it with:\n"
                "  pip install playwright && playwright install chromium\n"
                "Until then, use web_search or get_page_title instead.")
    # Keep Chromium's ~280MB download inside $AGENT8088_HOME rather than the
    # OS-default shared cache (~/.cache/ms-playwright etc.) - that shared
    # cache can belong to other Playwright-using projects on the same
    # machine, so `agent8088 --uninstall` cannot safely delete it.
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH", str(_agent_data_dir() / "playwright-browsers")
    )
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            if not os.path.exists(p.chromium.executable_path):
                return ("Playwright's Chromium browser is not installed. Install it with:\n"
                        "  playwright install chromium\n"
                        "Until then, use web_search or get_page_title instead.")
    except Exception as e:
        return f"Browser error: {e}"

    task = str(args.get("task") or "").strip()
    if not task:
        return "Error: browser tool requires 'task'."

    saved_role, _active_role = _active_role, "subagent:browser"
    try:
        result = asyncio.run(_run_browser_agent(url, task))
    except asyncio.TimeoutError:
        result = (f"Browser error: task exceeded the {BROWSER_TASK_TIMEOUT_SECONDS}s "
                  f"time limit (raise browser_task_timeout_seconds in config.txt).")
    except Exception as e:
        result = f"Browser error: {e}"
    finally:
        _active_role = saved_role

    result = re.sub(r'\n{3,}', '\n\n', (result or "").strip())
    return _wrap_untrusted(_strip_special_tokens(result[:5000]), url)
```

Also add `import asyncio` near the top of `engine.py`'s import block if it is not already imported (check first: `grep -n "^import asyncio" src/agent8088/engine.py`).

The `_playwright_available()` function in that block is reproduced verbatim, unchanged, from the original file — it just happens to fall inside the 3400-3486 line range being replaced (it originally sits between `BROWSER_TIMEOUT_MS` and `_exec_browser`). It is not a new or second copy; the file should end up with exactly one `_playwright_available` definition, in the same place it is today. The parts that actually change are: `BROWSER_TIMEOUT_MS`'s replacement (the two new constants), the new `_run_browser_agent` function, and `_exec_browser`'s body.

- [ ] **Step 4: Rewrite the second existing Chromium test that no longer matches the new implementation**

`tests/test_browse_page_missing_chromium.py`'s `test_present_chromium_binary_proceeds_to_launch` currently asserts that `_exec_browser` calls `p.chromium.launch()` directly once Chromium is present. The new implementation never calls `.launch()` itself (browser-use owns the actual launch) — it proceeds to `_run_browser_agent` instead. Replace that one test:

```python
def test_present_chromium_binary_proceeds_to_run_the_browser_agent(monkeypatch, tmp_path):
    present_path = tmp_path / "chrome.exe"
    present_path.write_text("stub")
    _install_fake_sync_playwright(monkeypatch, str(present_path), launch_calls=[])

    calls = []

    async def fake_run_browser_agent(url, task):
        calls.append((url, task))
        return "ok"

    monkeypatch.setattr(A, "_run_browser_agent", fake_run_browser_agent)

    result = A._exec_browser({"url": "https://example.com", "task": "extract the heading"})

    # It gets past the Chromium-presence check and reaches the browser-use
    # agent runner - proving the check does not block a genuinely-installed
    # Chromium.
    assert calls == [("https://example.com", "extract the heading")]
    assert "ok" in result
```

- [ ] **Step 5: Update the `browse_page` tool schema**

In `src/agent8088/tools.txt`, replace line 32:

```
browse_page|Load a user-supplied web page in a real headless browser and return its text. It is not a routine web-search follow-up.|mode=browser|args=url|timeout=60
```

with:

```
browse_page|Load a user-supplied web page and complete a task on it using a real headless browser -- click, fill forms, navigate, and extract information via natural-language instructions, not just read static text. It is not a routine web-search follow-up.|mode=browser|args=url,task|timeout=120
```

- [ ] **Step 6: Run the full browser test suite to verify everything passes**

Run: `pytest tests/test_exec_browser_agent.py tests/test_browse_page_missing_chromium.py tests/test_engine_playwright_browsers_path.py -v`
Expected: PASS — all tests, including the three unmodified pre-existing tests in `test_engine_playwright_browsers_path.py` and the first (unmodified) test in `test_browse_page_missing_chromium.py`, which never send `task` and never get past the Chromium-presence check, so they are unaffected by the schema change.

- [ ] **Step 7: Commit**

```bash
git add src/agent8088/engine.py src/agent8088/tools.txt \
        tests/test_exec_browser_agent.py tests/test_browse_page_missing_chromium.py
git commit -m "feat(browser): drive an interactive browser-use Agent from browse_page"
```

---

### Task 5: End-to-end integration test and SSRF regression test

**Files:**
- Test: `tests/test_browse_page_integration.py` (new)

**Interfaces:**
- Consumes: `A._exec_browser` (Task 4), a local `http.server` test fixture serving a small HTML page with a button/form.

- [ ] **Step 1: Write the integration test**

Create `tests/test_browse_page_integration.py`:

```python
"""End-to-end coverage for browse_page's new interactive path: a real
browser-use Agent run against a local test page, and a regression check
that a private/loopback target is still refused end-to-end (through the
full _exec_browser path, not just the proxy unit tests in
test_browser_proxy.py) - preserving today's SSRF guarantee.

Three of these four tests launch a real headless Chromium and make real LLM
calls against whatever provider is configured in the test environment, so
they are gated behind the AGENT8088_RUN_BROWSER_INTEGRATION=1 env var (see
_run_live below) and SKIPPED by default. The fourth - the loopback SSRF
regression check - needs neither and always runs.
"""
import http.server
import os
import threading

import pytest

from agent8088 import engine as A

pytestmark = pytest.mark.browser_integration

_run_live = pytest.mark.skipif(
    not os.environ.get("AGENT8088_RUN_BROWSER_INTEGRATION"),
    reason="set AGENT8088_RUN_BROWSER_INTEGRATION=1 to run live browser+LLM integration tests",
)


PAGE_HTML = b"""<!doctype html>
<html><body>
<h1 id="heading">Hello from the test page</h1>
<form id="f" onsubmit="document.getElementById('result').innerText='submitted: ' + document.getElementById('name').value; return false;">
  <input id="name" type="text" />
  <button type="submit">Go</button>
</form>
<p id="result"></p>
<button id="probe" onclick="
  fetch('http://169.254.169.254/latest/meta-data/')
    .then(() => { document.getElementById('probe-result').innerText = 'fetch succeeded'; })
    .catch(() => { document.getElementById('probe-result').innerText = 'fetch blocked'; });
">Probe metadata endpoint</button>
<p id="probe-result"></p>
</body></html>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE_HTML)


@pytest.fixture
def local_test_page(monkeypatch):
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # _exec_browser's pre-flight _ssrf_check blocks ALL loopback addresses by
    # design - including this fixture's own test server. Allowlist exactly
    # this dynamic port through the real SSRF_ALLOW_HOSTS mechanism (the same
    # escape hatch _ssrf_check's config already supports) rather than
    # disabling the check: test_ssrf_proxy_blocks_a_request_the_page_itself_makes
    # still needs _ssrf_check to genuinely block the unrelated metadata IP it
    # probes mid-session, so the check itself must stay live.
    monkeypatch.setattr(A, "SSRF_ALLOW_HOSTS", A.SSRF_ALLOW_HOSTS | {f"127.0.0.1:{port}"})
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        server.shutdown()
        server.server_close()


@_run_live
def test_browse_page_reads_a_local_page(local_test_page):
    result = A._exec_browser({
        "url": local_test_page,
        "task": "Read the page and report the exact text of the h1 heading.",
    })
    assert "Hello from the test page" in result


@_run_live
def test_browse_page_can_fill_and_submit_a_form(local_test_page):
    result = A._exec_browser({
        "url": local_test_page,
        "task": ("Type 'Ada' into the text input, click the Go button, then "
                  "report the exact text that appears in the result paragraph."),
    })
    assert "submitted: Ada" in result


@_run_live
def test_ssrf_proxy_blocks_a_request_the_page_itself_makes(local_test_page):
    """The pre-flight _egress_check/_ssrf_check in _exec_browser only ever
    sees the *initial* url. This is the one test that proves the SSRF
    proxy - the reason this whole component exists - also governs a request
    the page makes on its own mid-session, not just the first navigation.
    169.254.169.254 (the cloud-metadata address) is a literal IP so this
    needs no real DNS/network access to be deterministic."""
    result = A._exec_browser({
        "url": local_test_page,
        "task": ("Click the 'Probe metadata endpoint' button, wait a moment, "
                  "then report the exact text in the paragraph with id "
                  "'probe-result'."),
    })
    assert "fetch blocked" in result
    assert "fetch succeeded" not in result


def test_browse_page_refuses_a_loopback_target_end_to_end():
    """Needs neither a real browser nor a real model - the SSRF gate in
    _exec_browser runs before any browser-use code, so this one is not
    marked @_run_live and always runs."""
    result = A._exec_browser({
        "url": "http://127.0.0.1:9/",
        "task": "read the page",
    })
    assert "Blocked" in result
```

This repo has no existing precedent for a real-network/real-LLM-dependent
skippable test (its other external-tool tests, e.g.
`test_convert_document.py`'s LibreOffice coverage, fully mock the external
tool rather than skip around it) — since browser-use launching a real
Chromium and calling a real model is not something worth mocking away for
an end-to-end test, the `_run_live` marker above gates those three
specifically instead of following that mocking convention.

Register the `browser_integration` marker so it does not warn: add to
`pyproject.toml`'s `[tool.pytest.ini_options]` (the same table Task 3
added `asyncio_mode` to — extend it, do not add a second
`[tool.pytest.ini_options]` table):

```toml
markers = [
    "browser_integration: slow end-to-end tests that launch a real headless browser and call a real LLM provider",
]
```

- [ ] **Step 2: Run the regression tests that need neither a browser nor a model**

Run: `pytest tests/test_browse_page_integration.py::test_browse_page_refuses_a_loopback_target_end_to_end -v -m browser_integration`
Expected: PASS — this one only exercises the existing `_egress_check`/`_ssrf_check` pre-flight gate in `_exec_browser`, which runs before any browser-use code, so it needs no real browser or model, and is not gated by `_run_live`.

- [ ] **Step 3: Run the three live browser+LLM-dependent tests against a real configured provider**

Run: `AGENT8088_RUN_BROWSER_INTEGRATION=1 pytest tests/test_browse_page_integration.py -v -m browser_integration`
Expected: PASS, given a working `playwright install chromium` and a configured, reachable model provider in the test environment. Without `AGENT8088_RUN_BROWSER_INTEGRATION=1` set, the three `@_run_live` tests report SKIPPED rather than failing, so plain `pytest` runs (including CI, unless that variable is deliberately set there) stay fast and offline.

- [ ] **Step 4: Commit**

```bash
git add tests/test_browse_page_integration.py pyproject.toml
git commit -m "test(browser): add end-to-end interactive browsing and SSRF regression coverage"
```

---

### Task 6: Documentation updates

**Files:**
- Modify: `docs/wiki/04-tools.md:21`
- Modify: `docs/wiki/11-architecture.md:244-245`
- Modify: `docs/wiki/02-configuration.md:166`

**Interfaces:**
- None (docs only).

- [ ] **Step 1: Update the tools table**

In `docs/wiki/04-tools.md`, replace line 21:

```
| `browse_page` | `browser` | `url` | prompt | Headless browser — renders JS that curl can't. |
```

with:

```
| `browse_page` | `browser` | `url`, `task` | prompt | Headless browser — click, fill forms, navigate, and extract via natural-language instructions, not just read static text. |
```

- [ ] **Step 2: Remove the now-stale "read-only" limitation note**

In `docs/wiki/11-architecture.md`, remove these two lines (~244-245):

```
- **`browse_page` is read-only.** It renders and extracts text; it can't click
  or fill forms.
```

- [ ] **Step 3: Update the configuration reference**

In `docs/wiki/02-configuration.md`, replace line 166:

```
| `browser_timeout_ms` | `browse_page` timeout. |
```

with:

```
| `browser_max_steps` | Max steps `browse_page`'s browsing agent takes on one task (default `25`). |
| `browser_task_timeout_seconds` | Overall wall-clock limit for one `browse_page` call (default `300`). |
```

- [ ] **Step 4: Commit**

```bash
git add docs/wiki/04-tools.md docs/wiki/11-architecture.md docs/wiki/02-configuration.md
git commit -m "docs: describe browse_page's new interactive browsing capability"
```
