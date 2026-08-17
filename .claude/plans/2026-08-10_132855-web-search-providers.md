# Web Search Provider Registry Implementation Plan

> **For the implementer:** Execute task-by-task. Each task is 2–5 minutes. Write the failing test first, run it, then implement, then re-run, then commit.

**Goal:** Replace Agent8088's single hardcoded SearXNG `web_search` tool with a provider registry that defaults to a Docker-provisioned SearXNG, falls back to keyless `ddgs` when Docker is absent, and keeps Tavily and Exa available as optional backends for users who add an API key.

**Provider roles (fixed):**

| Provider | Role | Requires |
|---|---|---|
| `searxng` | **Default** | Docker (auto-provisioned) or an existing instance URL |
| `ddgs` | **Fallback** when SearXNG is absent or failing | **Nothing — ships as a dependency of agent8088** |
| `tavily` | **Optional** | `TAVILY_API_KEY` in the `.env` store |
| `exa` | **Optional** | `EXA_API_KEY` in the `.env` store |

**The guarantee this buys:** because `ddgs` is installed with the agent, `web_search` always has somewhere to land. A user whose SearXNG container is stopped, unreachable, or returning HTML instead of JSON still gets results — the chain falls through to ddgs instead of reporting "no web search provider is configured". Web search works on a fresh install with zero setup.

**Architecture:** One `web_search` tool backed by a new `mode=search` dispatch. A new `src/agent8088/web_search.py` holds a provider registry (mirroring Hermes' `agent/web_search_registry.py` precedence model) plus a runtime fallback chain. A new `src/agent8088/searxng_provision.py` auto-provisions a loopback-bound SearXNG container when Docker is available. `engine.py` injects its own security guards into the registry so no provider can bypass the SSRF/egress/secret-leak floor.

**Tech Stack:** Python 3.10+, stdlib `urllib` (existing `_exec_http` pattern), `ddgs>=9,<10` as a bundled dependency, Docker CLI (optional), pytest.

**Target branch:** cut `feat/web-search-providers` off `bug-fixes` (currently `c7bab82 fix: harden public gateway defaults`).

---

## Current Context

Verified against `bug-fixes` @ `c7bab82`:

| Fact | Location |
|---|---|
| `web_search` is a single `http_get` tool hitting `{search_base_url}{query_q}&format=json` | `src/agent8088/tools.txt:4` |
| Default `search_base_url` is `http://127.0.0.1:8888/search?q=` | `src/agent8088/engine.py:255`, defaulted into `APP_CONFIG` at `engine.py:332` |
| `web_search_tavily` / `web_search_exa` exist as separate `http_post` tools | `src/agent8088/tools.txt:24-25` |
| Those two are **non-functional out of the box** — they need `tool_headers.*` / `tool_body.*` / `tool_filter.*` in `config.txt`, and `config.txt` ships none | `engine.py:1439-1441`, `src/agent8088/config.txt` (no `tool_*` keys) |
| `_docker_available()` already exists and is reusable | `engine.py:2333` |
| SSRF host allowlist defaults to `127.0.0.1,localhost` | `src/agent8088/config.txt:28`, read at `engine.py:3672` |
| HTTP tools are gated + executed in one block | `engine.py:2915-2945` |
| MCP server exposes `web_search`, `web_search_tavily`, `web_search_exa` | `src/agent8088/mcp_server.py:59-64` |
| MCP server already adds `search_base_url`'s host to `SSRF_ALLOW_HOSTS` | `mcp_server.py:196-202` |
| Wizard prompts a raw "Web search URL (SearXNG)" string | `src/agent8088/cli.py:2288`, written at `cli.py:2312-2315` |
| Slash commands are registered in a dict | `cli.py:1920-1932`; `cmd_sandbox` at `cli.py:1490` is the pattern to copy |
| `describe_capabilities()` reports live guardrail state | `engine.py:3804-3890` |

### Assumptions

1. Docker presence is detected but **not required**; nothing regresses for users without it.
2. `ddgs` is a **hard dependency** so the fallback is always present (Task 15). `is_available()` still checks importability rather than assuming it, so a stripped environment reports "unavailable" instead of raising on import.
3. Existing users with `search_base_url` set keep working with zero config changes (the `searxng` provider reads that same key).
4. No network calls in tests. No Docker calls in tests. No writes to `~/.agent8088`.

---

## Design Decisions

### D1. Tavily and Exa stay — as backends behind one tool, exactly as Hermes does

**Nothing is losing capability here.** Tavily and Exa remain fully supported web search options, gated on the user adding an API key. What changes is the *tool surface*: instead of three separate tool names, there is one `web_search` tool that dispatches to whichever backend is configured.

This is precisely Hermes' shape. Verified in their source:

- `agent/web_search_registry.py` — "consumed by the `web_search` and `web_extract` tool wrappers in `tools.web_tools` to dispatch each call to the active backend." One tool, many backends.
- `website/docs/integrations/index.md` — `web.backend: firecrawl | searxng | brave-free | ddgs | tavily | exa | parallel | xai`. Tavily and Exa are *values of a backend setting*, never their own tools.
- `website/docs/user-guide/features/web-search.md` — keys live in `~/.hermes/.env` as `TAVILY_API_KEY` / `EXA_API_KEY`. Agent8088's equivalent is the existing `.env` store at `ENV_FILE_PATH`.

Why consolidate: three near-identical tool names invite the model to call the one that has no key configured, and it then reports a failure the user reads as "web search is broken". One tool that always routes to something that works is strictly better for the model and the user.

**Migration:** `web_search_tavily` and `web_search_exa` disappear as *tool names* only. `CHANGELOG.md` gets a line mapping them to `web_search_provider=tavily` / `=exa` plus the env var. Blast radius is effectively zero — those tools required `tool_headers.*` / `tool_body.*` keys that the shipped `config.txt` never contained, so no default install had them working.

### D2. Provider precedence

Mirrors Hermes' precedence ladder, with the roles fixed as requested:

1. `web_search_provider=<name>` in `config.txt` — explicit override, no fallback guessing.
2. Exactly one available provider → use it.
3. Preference order, filtered by availability: **`searxng` → `tavily` → `exa` → `ddgs`**.
4. None available → error naming `/search setup`.

`searxng` leads because it is the default. `ddgs` is last because it is the only backend that scrapes rather than calling an API, and the only one that rate-limits under ordinary agent use (`202 Ratelimit` is the top recurring report on its tracker) — it is the safety net, not the recommendation. A user who added a Tavily or Exa key clearly wants it used over scraping, so the keyed backends sit between the two.

Brave is deliberately **not** included. It appeared in an earlier draft but was never requested; a third paid-vendor integration is surface without a stated need (YAGNI). The registry makes adding it later a single class plus one `PREFERENCE` entry.

### D3. Runtime fallback chain, not just selection

Hermes only *selects* a provider. The user explicitly asked for ddgs to be a **fallback**, so `run_search()` walks the chain at call time: on a provider error (unreachable, HTTP 5xx, rate-limited, empty-after-retry) it tries the next available provider and records which one served the result. Each attempt is independently guarded. A single provider's outage must never mean "no web search".

### D4. Availability is config, not liveness

`is_available()` is a cheap, synchronous, no-network check (key present / URL set / module importable) — same as Hermes' `is_available()`. Liveness is discovered by actually searching and falling through. Rationale: a health-check ping on every `web_search` call doubles latency and still races.

### D5. Guards are injected, never re-implemented

`web_search.py` must not import `engine.py` (circular import). Instead `engine.py` builds a `SearchGuards` object holding `check_url`, `wrap_untrusted`, and `redact` callables and passes it in. Every provider calls `guards.check_url(url)` before every request and returns the guard's error string verbatim if blocked. This keeps `_egress_check` / `_ssrf_check` / `_outbound_secret_check` as the single source of truth.

### D6. Security requirements (non-negotiable)

| Requirement | Why |
|---|---|
| Provisioned container binds `127.0.0.1:8888:8080`, never `0.0.0.0` | SearXNG's JSON API has no auth. Binding to all interfaces publishes an open search proxy to the LAN. |
| `http://` base URL allowed only for loopback/private hosts; public hosts must be `https://` | Matches OpenClaw's documented network guard. Prevents cleartext queries over the internet. |
| `ddgs` runs in-process and therefore bypasses `_exec_http`'s guards — it must be contained per **D8** below | `_exec_http`'s docstring states the http mode exists precisely so the SSRF guard applies; an in-process HTTP client is exactly the bypass it warns about. |
| All results pass through `_wrap_untrusted(..., source=<provider>)` and `_strip_special_tokens` | Search results are attacker-controlled text. Existing `_exec_http` already does this at `engine.py:2198`. |
| API keys resolve from the `.env` store (`ENV_FILE_PATH`), never `config.txt` | The repo already migrates keys out of `config.txt` (`engine.py:240-248`). |
| A provider only ever receives its own key; the existing `_outbound_secret_check` floor still runs per request | Prevents a Tavily key being posted to Exa's host. |
| Generated `settings.yml` gets `secrets.token_hex(32)` as `secret_key`, written `0600` | An upstream-default secret key is a session-forgery vector. |

### D8. Containing `ddgs`, which owns its own HTTP client

**The problem, stated plainly.** Every other network path in this codebase goes through `_exec_http`, which runs `_egress_check` → `_ssrf_check` → `_outbound_secret_check` before the request and re-checks every redirect (`engine.py:2158-2164`). `ddgs` is a library that makes its own HTTP calls, so none of that runs. An operator who set `allowed_domains=` expecting a hard egress boundary would have a library quietly reaching DuckDuckGo outside it. That is a real policy hole, not a theoretical one.

**Decision — the production-practice answer, in order of what actually reduces risk:**

1. **Fail closed, not open.** `ddgs` talks to a *fixed, known* set of hosts (`duckduckgo.com`, `html.duckduckgo.com`, `lite.duckduckgo.com`). Every one is checked against `ctx.check_url()` *before* the library is invoked. If the egress policy rejects any of them, the provider returns a **non-retryable** failure and the library is never called. It does not "try anyway and see". This is what makes the pre-flight sufficient rather than decorative: the host set is closed, so checking it up front is equivalent to checking each request.
2. **Never silently substitute.** A guard denial is marked `retryable=False`, so `run_search()` stops the chain instead of falling through to another provider. Working around an operator's egress policy by picking a different vendor would be worse than failing.
3. **Treat the output as hostile.** Results pass through `_wrap_untrusted(..., source="web_search:ddgs")` and are truncated per field (400 chars) and per result count. Identical handling to `_exec_http`'s at `engine.py:2198`.
4. **Bound the blast radius.** No credential is ever in scope (`ddgs` is keyless), so `_outbound_secret_check` has nothing to leak, and the host set is closed so there is no attacker-influenced destination. Note the library is bundled (Task 15), so this containment is always active rather than only for installs that opted in — which is why it is enforced in code and tested, not documented as guidance.
5. **Verify, don't assume.** Task 5 includes an explicit fail-closed test asserting the library is *not called* when the policy blocks it, and Task 18 routes the diff through the repo's `security-reviewer` agent, which exists specifically for changes to this permission/SSRF surface.

**Rejected alternatives and why:** shelling out to the `ddgs` CLI would add arbitrary-shell surface to replace an HTTP-client concern — strictly worse. Running its FastAPI server as a sidecar puts an unauthenticated local service on a port to solve a problem the pre-flight already solves. Reimplementing DuckDuckGo scraping in-tree to route it through `_exec_http` means owning the brittle part of `ddgs` — the exact thing that makes it a fallback rather than a primary.

### D7. SearXNG JSON must be explicitly enabled

The upstream image ships with JSON output **off** and the bot `limiter` **on** — both block programmatic use. The generated `settings.yml` sets `search.formats: [html, json]` and `server.limiter: false`. This is the single step users miss most often, so `/search doctor` checks for it by name.

---

## Task Breakdown

### Task 1: Branch off bug-fixes

**Objective:** Isolated branch for the feature.

**Step 1:** Create the branch.

```bash
cd /Users/tahawaheed/Documents/Agent8088-Features-added
git checkout bug-fixes && git pull origin bug-fixes && git checkout -b feat/web-search-providers
```

**Step 2:** Confirm clean base.

Run: `git log --oneline -1 && git status --short`
Expected: `c7bab82 fix: harden public gateway defaults` (or newer), no modified files.

---

### Task 2: Provider base + registry skeleton

**Objective:** A registry with precedence resolution and zero providers yet.

**Files:**
- Create: `src/agent8088/web_search.py`
- Test: `tests/test_web_search.py`

**Step 1: Write failing test**

```python
"""Tests for the web search provider registry, precedence, and fallback chain."""
import pytest

from agent8088 import web_search


class _Stub(web_search.WebSearchProvider):
    def __init__(self, name, available=True, results=None, error=None):
        self._name = name
        self._available = available
        self._results = results or []
        self._error = error
        self.calls = 0

    @property
    def name(self):
        return self._name

    def is_available(self, ctx):
        return self._available

    def setup_schema(self):
        return {"name": self._name, "badge": "test", "tag": "stub", "env_vars": []}

    def search(self, query, limit, ctx):
        self.calls += 1
        if self._error:
            return web_search.SearchFailure(self._error)
        return web_search.SearchSuccess(self._results, provider=self._name)


def test_explicit_provider_wins_over_preference_order():
    registry = web_search.Registry([_Stub("searxng"), _Stub("ddgs")])
    chain = registry.chain({"web_search_provider": "ddgs"}, ctx=None)
    assert [p.name for p in chain] == ["ddgs"]


def test_preference_order_filters_unavailable():
    registry = web_search.Registry([
        _Stub("searxng", available=False),
        _Stub("tavily", available=True),
        _Stub("ddgs", available=True),
    ])
    chain = registry.chain({}, ctx=None)
    assert [p.name for p in chain] == ["tavily", "ddgs"]


def test_chain_is_empty_when_nothing_available():
    registry = web_search.Registry([_Stub("searxng", available=False)])
    assert registry.chain({}, ctx=None) == []
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_web_search.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent8088.web_search'`

**Step 3: Implement**

Create `src/agent8088/web_search.py`:

```python
"""Web search provider registry.

One ``web_search`` tool, several interchangeable backends. Which backend
serves a call is decided here, not by the model picking between five
similarly-named tools.

Roles: searxng is the default (self-hosted, no key), ddgs is the keyless
fallback for machines without Docker, and tavily/exa are optional backends a
user enables by adding an API key. Same arrangement as Hermes, where these are
values of one ``web.backend`` setting rather than separate tools.

Selection precedence (mirrors Hermes' agent/web_search_registry.py):

  1. ``web_search_provider=<name>`` in config.txt — explicit, no fallback.
  2. Exactly one available provider — use it.
  3. PREFERENCE order below, filtered by availability.
  4. Nothing available — the tool returns an actionable setup error.

Unlike Hermes, which only *selects* a backend, run_search() also *falls
through* the chain at call time: a provider that is configured but broken
(instance down, rate-limited) must not mean "no web search".

This module deliberately does NOT import engine.py — that would be circular.
Security guards (SSRF, egress policy, untrusted-content wrapping) are injected
via SearchContext so engine.py stays the single source of truth for them.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Callable, Optional

# searxng first: it is the default when self-hosted. tavily/exa next: a user who
# added a key wants it used. ddgs last: it is the only backend that scrapes
# rather than using an API, and the only one that rate-limits under normal use.
PREFERENCE = ("searxng", "tavily", "exa", "ddgs")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


@dataclass
class SearchSuccess:
    results: list
    provider: str


@dataclass
class SearchFailure:
    error: str
    retryable: bool = True


@dataclass
class SearchContext:
    """Config, credentials, and security guards handed to every provider.

    check_url MUST be called by every provider before every outbound request;
    it returns None when the URL is permitted, else an error string to surface
    verbatim. wrap wraps result text as untrusted external content.
    """
    config: dict = field(default_factory=dict)
    get_secret: Callable[[str], str] = lambda name: ""
    check_url: Callable[[str], Optional[str]] = lambda url: None
    wrap: Callable[..., str] = lambda text, source="": text


class WebSearchProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    @abc.abstractmethod
    def is_available(self, ctx: SearchContext) -> bool:
        """Cheap, synchronous, no-network check: is this provider configured?"""

    @abc.abstractmethod
    def setup_schema(self) -> dict:
        """Data the setup UI needs to enable this provider.

        Shape borrowed from Hermes' provider ``get_setup_schema()`` so /search
        and the wizard render from provider-owned data instead of a hardcoded
        list that drifts:

            {"name": "Tavily", "badge": "optional · API key",
             "tag": "Agent-optimized results with citations",
             "env_vars": [{"key": "TAVILY_API_KEY",
                           "prompt": "Tavily API key",
                           "url": "https://tavily.com"}],
             "post_setup": ""}
        """

    def setup_hint(self) -> str:
        """One-line rendering of setup_schema for error messages."""
        schema = self.setup_schema()
        keys = ", ".join(v["key"] for v in schema.get("env_vars") or [])
        detail = f"set {keys}" if keys else schema.get("tag", "")
        return f"{self.name} ({schema.get('badge', '')}) — {detail}"

    @abc.abstractmethod
    def search(self, query: str, limit: int, ctx: SearchContext):
        """Return SearchSuccess or SearchFailure. Must never raise."""


class Registry:
    def __init__(self, providers):
        self._providers = {p.name: p for p in providers}

    def get(self, name: str):
        return self._providers.get(name)

    def names(self):
        return list(self._providers)

    def chain(self, config: dict, ctx) -> list:
        """Ordered providers to attempt for one search call."""
        explicit = str(config.get("web_search_provider") or "").strip().lower()
        if explicit:
            provider = self._providers.get(explicit)
            return [provider] if provider else []
        available = [self._providers[n] for n in PREFERENCE
                     if n in self._providers and self._providers[n].is_available(ctx)]
        return available
```

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_web_search.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
git add src/agent8088/web_search.py tests/test_web_search.py && git commit -m "feat: web search provider registry with precedence resolution"
```

---

### Task 3: `run_search` fallback chain

**Objective:** Walk the chain, fall through failures, report the serving provider.

**Files:**
- Modify: `src/agent8088/web_search.py`
- Test: `tests/test_web_search.py`

**Step 1: Write failing test**

```python
def test_run_search_falls_through_to_next_provider():
    broken = _Stub("searxng", error="instance unreachable")
    working = _Stub("ddgs", results=[web_search.SearchResult("T", "https://e.com", "s")])
    registry = web_search.Registry([broken, working])
    out = web_search.run_search("q", 5, registry, {}, web_search.SearchContext())
    assert broken.calls == 1 and working.calls == 1
    assert "ddgs" in out and "https://e.com" in out


def test_run_search_reports_every_failure_when_all_fail():
    registry = web_search.Registry([
        _Stub("searxng", error="unreachable"),
        _Stub("ddgs", error="rate limited"),
    ])
    out = web_search.run_search("q", 5, registry, {}, web_search.SearchContext())
    assert "unreachable" in out and "rate limited" in out


def test_run_search_with_no_providers_names_setup_command():
    registry = web_search.Registry([_Stub("searxng", available=False)])
    out = web_search.run_search("q", 5, registry, {}, web_search.SearchContext())
    assert "/search setup" in out


def test_run_search_stops_at_non_retryable_failure():
    blocked = _Stub("searxng")
    blocked.search = lambda q, l, c: web_search.SearchFailure("Blocked: egress", retryable=False)
    nxt = _Stub("ddgs", results=[web_search.SearchResult("T", "https://e.com")])
    registry = web_search.Registry([blocked, nxt])
    out = web_search.run_search("q", 5, registry, {}, web_search.SearchContext())
    assert "Blocked: egress" in out and nxt.calls == 0
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_web_search.py -v`
Expected: FAIL — `AttributeError: module 'agent8088.web_search' has no attribute 'run_search'`

**Step 3: Implement** — append to `web_search.py`:

```python
def format_results(success: SearchSuccess, ctx: SearchContext) -> str:
    """Render results as compact text, wrapped as untrusted external content."""
    if not success.results:
        return f"No results from {success.provider}."
    lines = [f"Search results (via {success.provider}):", ""]
    for i, r in enumerate(success.results, 1):
        lines.append(f"{i}. {r.title}")
        lines.append(f"   {r.url}")
        if r.snippet:
            lines.append(f"   {r.snippet}")
    return ctx.wrap("\n".join(lines), source=f"web_search:{success.provider}")


def run_search(query: str, limit: int, registry: Registry, config: dict,
               ctx: SearchContext) -> str:
    """Try each provider in the chain until one returns results.

    A non-retryable failure (an egress/SSRF denial) stops the chain: the guard
    said no, and trying a different provider would be working around a security
    decision rather than around an outage.
    """
    query = (query or "").strip()
    if not query:
        return "Error: web_search requires a non-empty 'query'."

    chain = registry.chain(config, ctx)
    if not chain:
        configured = str(config.get("web_search_provider") or "").strip()
        if configured:
            return (f"web_search_provider={configured} is not a known provider. "
                    f"Known: {', '.join(PREFERENCE)}.")
        hints = "\n".join(f"  - {registry.get(n).setup_hint()}"
                          for n in PREFERENCE if registry.get(n))
        return ("No web search provider is configured. Run `/search setup` to "
                f"provision a local SearXNG, or enable one of:\n{hints}")

    failures = []
    for provider in chain:
        outcome = provider.search(query, limit, ctx)
        if isinstance(outcome, SearchSuccess) and outcome.results:
            return format_results(outcome, ctx)
        if isinstance(outcome, SearchFailure):
            if not outcome.retryable:
                return outcome.error
            failures.append(f"{provider.name}: {outcome.error}")
        else:
            failures.append(f"{provider.name}: no results")
    return ("Every configured web search provider failed:\n"
            + "\n".join(f"  - {f}" for f in failures)
            + "\nRun `/search doctor` to diagnose.")
```

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_web_search.py -v`
Expected: 7 passed

**Step 5: Commit**

```bash
git add -u && git commit -m "feat: runtime fallback chain across web search providers"
```

---

### Task 4: SearXNG provider

**Objective:** Query a configured SearXNG instance's JSON API, guarded.

**Files:**
- Modify: `src/agent8088/web_search.py`
- Test: `tests/test_web_search.py`

**Step 1: Write failing test**

```python
def test_searxng_unavailable_without_base_url():
    p = web_search.SearxngProvider()
    assert p.is_available(web_search.SearchContext(config={})) is False


def test_searxng_available_with_base_url():
    ctx = web_search.SearchContext(config={"search_base_url": "http://127.0.0.1:8888/search?q="})
    assert web_search.SearxngProvider().is_available(ctx) is True


def test_searxng_returns_guard_error_verbatim_and_non_retryable(monkeypatch):
    ctx = web_search.SearchContext(
        config={"search_base_url": "http://10.0.0.5:8888/search?q="},
        check_url=lambda url: "Blocked: private address",
    )
    out = web_search.SearxngProvider().search("q", 5, ctx)
    assert isinstance(out, web_search.SearchFailure)
    assert out.error == "Blocked: private address" and out.retryable is False


def test_searxng_rejects_plaintext_http_to_public_host():
    ctx = web_search.SearchContext(config={"search_base_url": "http://searx.example.com/search?q="})
    out = web_search.SearxngProvider().search("q", 5, ctx)
    assert isinstance(out, web_search.SearchFailure)
    assert "https" in out.error.lower()


def test_searxng_parses_results(monkeypatch):
    payload = {"results": [
        {"title": "A", "url": "https://a.com", "content": "sa", "score": 1.0},
        {"title": "B", "url": "https://b.com", "content": "sb", "score": 9.0},
    ]}
    monkeypatch.setattr(web_search, "_http_get_json", lambda url, timeout: payload)
    ctx = web_search.SearchContext(config={"search_base_url": "http://127.0.0.1:8888/search?q="})
    out = web_search.SearxngProvider().search("q", 1, ctx)
    assert isinstance(out, web_search.SearchSuccess)
    # Highest score first, capped at limit.
    assert [r.title for r in out.results] == ["B"]


def test_searxng_html_response_names_the_json_format_setting(monkeypatch):
    def _raise(url, timeout):
        raise ValueError("not json")
    monkeypatch.setattr(web_search, "_http_get_json", _raise)
    ctx = web_search.SearchContext(config={"search_base_url": "http://127.0.0.1:8888/search?q="})
    out = web_search.SearxngProvider().search("q", 5, ctx)
    assert "formats" in out.error and "json" in out.error
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_web_search.py -v`
Expected: FAIL — no attribute `SearxngProvider`

**Step 3: Implement** — append to `web_search.py`:

```python
import ipaddress
import json as _json
import urllib.error
import urllib.parse
import urllib.request

MAX_SEARCH_BYTES = 2 * 1024 * 1024


def _http_get_json(url: str, timeout: int):
    """Plain GET returning parsed JSON. Raises on transport or parse failure.

    Callers MUST have run ctx.check_url(url) first — this function performs no
    policy checks of its own.
    """
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_SEARCH_BYTES + 1)
    if len(raw) > MAX_SEARCH_BYTES:
        raise ValueError("search response exceeded size limit")
    return _json.loads(raw.decode("utf-8", errors="replace"))


def _is_local_host(host: str) -> bool:
    host = (host or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


class SearxngProvider(WebSearchProvider):
    """Self-hosted SearXNG via its JSON API.

    Reads the existing ``search_base_url`` key so installs that configured
    SearXNG before the registry existed keep working untouched.
    """

    @property
    def name(self):
        return "searxng"

    def setup_schema(self):
        return {"name": "SearXNG", "badge": "default · free · self-hosted",
                "tag": "Meta-search over 70+ engines, no API key, queries stay local",
                "env_vars": [], "post_setup": "searxng_container"}

    def _base(self, ctx):
        return str(ctx.config.get("search_base_url") or "").strip()

    def is_available(self, ctx):
        return bool(self._base(ctx))

    def search(self, query, limit, ctx):
        base = self._base(ctx)
        if not base:
            return SearchFailure("search_base_url is not set")
        url = f"{base}{urllib.parse.quote(query)}&format=json"

        parts = urllib.parse.urlparse(url)
        # Plaintext HTTP is fine to a box you control; over the internet it puts
        # every query on the wire. Same rule OpenClaw's SearXNG plugin enforces.
        if parts.scheme == "http" and not _is_local_host(parts.hostname or ""):
            return SearchFailure(
                f"Refusing plaintext http:// to public host '{parts.hostname}' — "
                "use https:// for a remote SearXNG instance.", retryable=False)

        blocked = ctx.check_url(url)
        if blocked:
            # A guard denial is a policy decision, not an outage: do not fall
            # through to another provider to work around it.
            return SearchFailure(blocked, retryable=False)

        try:
            payload = _http_get_json(url, timeout=20)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                return SearchFailure(
                    f"SearXNG returned HTTP {exc.code} — the bot limiter is likely on. "
                    "Set server.limiter: false in settings.yml for local API use.")
            return SearchFailure(f"SearXNG returned HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return SearchFailure(f"Could not reach SearXNG at {parts.netloc}: {exc}")
        except (ValueError, _json.JSONDecodeError):
            return SearchFailure(
                "SearXNG did not return JSON. Add `json` to search.formats in "
                "settings.yml (JSON output is disabled by default upstream).")

        raw = payload.get("results") or []
        ranked = sorted(raw, key=lambda r: float(r.get("score") or 0), reverse=True)
        results = [SearchResult(title=str(r.get("title") or ""),
                                url=str(r.get("url") or ""),
                                snippet=str(r.get("content") or "")[:400])
                   for r in ranked[:limit] if r.get("url")]
        return SearchSuccess(results, provider=self.name)
```

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_web_search.py -v`
Expected: 13 passed

**Step 5: Commit**

```bash
git add -u && git commit -m "feat: SearXNG web search provider with JSON-format and plaintext guards"
```

---

### Task 5: ddgs provider (keyless fallback)

**Objective:** Keyless search via the optional `ddgs` package, with the egress guard applied even though the library makes its own requests.

**Files:**
- Modify: `src/agent8088/web_search.py`
- Test: `tests/test_web_search.py`

**Step 1: Write failing test**

```python
def test_ddgs_unavailable_when_package_missing(monkeypatch):
    monkeypatch.setattr(web_search, "_ddgs_installed", lambda: False)
    assert web_search.DdgsProvider().is_available(web_search.SearchContext()) is False


def test_ddgs_advertises_itself_as_bundled():
    schema = web_search.DdgsProvider().setup_schema()
    assert "bundled" in schema["badge"] and schema["env_vars"] == []


def test_chain_always_has_a_fallback_with_nothing_configured():
    """The payoff of bundling ddgs: an empty config still yields a usable chain."""
    registry = web_search.default_registry()
    ctx = web_search.SearchContext(config={}, get_secret=lambda n: "")
    assert [p.name for p in registry.chain({}, ctx)] == ["ddgs"]


def test_ddgs_serves_when_searxng_is_configured_but_broken(monkeypatch):
    """The exact scenario that motivated bundling: SearXNG set but not working."""
    monkeypatch.setattr(web_search, "_ddgs_installed", lambda: True)
    monkeypatch.setattr(web_search, "_ddgs_text", lambda q, n: [
        {"title": "T", "href": "https://e.com", "body": "b"}])
    monkeypatch.setattr(web_search, "_http_get_json",
                        lambda url, timeout: (_ for _ in ()).throw(OSError("refused")))
    ctx = web_search.SearchContext(
        config={"search_base_url": "http://127.0.0.1:8888/search?q="},
        get_secret=lambda n: "")
    out = web_search.run_search("q", 5, web_search.default_registry(),
                               {"search_base_url": "http://127.0.0.1:8888/search?q="}, ctx)
    assert "ddgs" in out and "https://e.com" in out


def test_ddgs_fails_closed_and_never_calls_library_when_egress_blocks(monkeypatch):
    """D8 requirement: the library must not run at all under a blocking policy.

    This is the test that makes the pre-flight meaningful rather than
    decorative — assert BOTH the non-retryable failure and that the library
    was never invoked.
    """
    called = []
    monkeypatch.setattr(web_search, "_ddgs_installed", lambda: True)
    monkeypatch.setattr(web_search, "_ddgs_text",
                        lambda q, n: called.append(q) or [])
    ctx = web_search.SearchContext(check_url=lambda url: "Blocked: not in allowed_domains")
    out = web_search.DdgsProvider().search("q", 5, ctx)
    assert isinstance(out, web_search.SearchFailure)
    assert out.retryable is False and called == []


def test_ddgs_checks_every_upstream_host(monkeypatch):
    """A partial check would leave an unguarded host reachable."""
    checked = []
    monkeypatch.setattr(web_search, "_ddgs_installed", lambda: True)
    monkeypatch.setattr(web_search, "_ddgs_text", lambda q, n: [])
    ctx = web_search.SearchContext(check_url=lambda url: checked.append(url))
    web_search.DdgsProvider().search("q", 5, ctx)
    assert set(checked) == set(web_search._DDGS_HOSTS)


def test_ddgs_maps_library_result_keys(monkeypatch):
    monkeypatch.setattr(web_search, "_ddgs_installed", lambda: True)
    monkeypatch.setattr(web_search, "_ddgs_text", lambda q, n: [
        {"title": "T", "href": "https://e.com", "body": "snippet"}])
    out = web_search.DdgsProvider().search("q", 5, web_search.SearchContext())
    assert isinstance(out, web_search.SearchSuccess)
    assert out.results[0].url == "https://e.com" and out.results[0].snippet == "snippet"


def test_ddgs_rate_limit_is_retryable(monkeypatch):
    monkeypatch.setattr(web_search, "_ddgs_installed", lambda: True)

    def _boom(q, n):
        raise RuntimeError("202 Ratelimit")

    monkeypatch.setattr(web_search, "_ddgs_text", _boom)
    out = web_search.DdgsProvider().search("q", 5, web_search.SearchContext())
    assert isinstance(out, web_search.SearchFailure)
    assert "rate" in out.error.lower() and out.retryable is True
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_web_search.py -v`
Expected: FAIL — no attribute `DdgsProvider`

**Step 3: Implement** — append to `web_search.py`:

```python
import importlib.util

# ddgs reaches these hosts. Listed so the egress policy can be enforced even
# though the library owns its own HTTP client — see _DDGS_HOSTS use below.
_DDGS_HOSTS = ("https://duckduckgo.com", "https://html.duckduckgo.com",
               "https://lite.duckduckgo.com")


def _ddgs_installed() -> bool:
    return importlib.util.find_spec("ddgs") is not None


def _ddgs_text(query: str, limit: int):
    """Call the ddgs library. Isolated so tests can patch it without the
    package installed."""
    from ddgs import DDGS

    return DDGS().text(query, max_results=limit)


class DdgsProvider(WebSearchProvider):
    """Keyless metasearch via the ddgs package.

    Last in PREFERENCE deliberately: ddgs scrapes result pages rather than
    using an API, so it is the only provider that rate-limits under ordinary
    agent use (its tracker's top recurring report is `202 Ratelimit`). It earns
    its place as the fallback that needs no key and no hosting.

    SECURITY: the library owns its HTTP client, so requests do not pass through
    _exec_http's SSRF/egress guard. We therefore check its fixed upstream hosts
    against the policy BEFORE calling it, and refuse rather than silently
    bypassing an operator's egress allowlist.
    """

    @property
    def name(self):
        return "ddgs"

    def setup_schema(self):
        return {"name": "DuckDuckGo (ddgs)",
                "badge": "fallback · free · no key · bundled",
                "tag": "Keyless metasearch, no hosting, no setup — ships with agent8088",
                "env_vars": [], "post_setup": ""}

    def is_available(self, ctx):
        # Ships as a dependency, so this is normally True. Still checked rather
        # than assumed: a stripped or partially-installed environment should
        # report "unavailable" instead of raising ImportError mid-search.
        return _ddgs_installed()

    def search(self, query, limit, ctx):
        if not _ddgs_installed():
            return SearchFailure("ddgs is not installed — " + self.setup_hint())
        for host in _DDGS_HOSTS:
            blocked = ctx.check_url(host)
            if blocked:
                return SearchFailure(
                    f"ddgs cannot run under the current egress policy ({blocked})",
                    retryable=False)
        try:
            raw = _ddgs_text(query, limit) or []
        except Exception as exc:  # the library raises a wide range of types
            message = str(exc)
            if "ratelimit" in message.lower().replace(" ", "") or "202" in message:
                return SearchFailure(
                    "ddgs is rate limited (DuckDuckGo throttled the request). "
                    "Configure SearXNG or an API-key provider for sustained use.")
            return SearchFailure(f"ddgs search failed: {message}")
        results = [SearchResult(title=str(r.get("title") or ""),
                                url=str(r.get("href") or ""),
                                snippet=str(r.get("body") or "")[:400])
                   for r in raw[:limit] if r.get("href")]
        return SearchSuccess(results, provider=self.name)
```

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_web_search.py -v`
Expected: 21 passed

**Step 5: Commit**

```bash
git add -u && git commit -m "feat: keyless ddgs fallback provider that fails closed under egress policy"
```

---

### Task 6: Optional API-key providers (Tavily, Exa)

**Objective:** Two optional keyed backends sharing one request helper. Both stay fully supported web search options — they simply report themselves unavailable until the user adds a key, so they never appear in the chain unasked.

**Files:**
- Modify: `src/agent8088/web_search.py`
- Test: `tests/test_web_search.py`

**Step 1: Write failing test**

```python
@pytest.mark.parametrize("cls,env", [
    (web_search.TavilyProvider, "TAVILY_API_KEY"),
    (web_search.ExaProvider, "EXA_API_KEY"),
])
def test_keyed_provider_availability_follows_secret(cls, env):
    empty = web_search.SearchContext(get_secret=lambda n: "")
    assert cls().is_available(empty) is False
    present = web_search.SearchContext(get_secret=lambda n: "k" if n == env else "")
    assert cls().is_available(present) is True


@pytest.mark.parametrize("cls,env", [
    (web_search.TavilyProvider, "TAVILY_API_KEY"),
    (web_search.ExaProvider, "EXA_API_KEY"),
])
def test_keyed_provider_setup_schema_names_env_var(cls, env):
    schema = cls().setup_schema()
    assert schema["env_vars"][0]["key"] == env
    assert "optional" in schema["badge"]


def test_optional_providers_absent_from_chain_without_keys():
    """A user who never added a key must not see tavily/exa in the chain."""
    registry = web_search.default_registry()
    ctx = web_search.SearchContext(config={}, get_secret=lambda n: "")
    assert [p.name for p in registry.chain({}, ctx)] not in ([("tavily")], ["exa"])
    assert "tavily" not in [p.name for p in registry.chain({}, ctx)]


def test_optional_provider_enters_chain_once_key_is_set():
    registry = web_search.default_registry()
    ctx = web_search.SearchContext(
        config={}, get_secret=lambda n: "k" if n == "TAVILY_API_KEY" else "")
    assert "tavily" in [p.name for p in registry.chain({}, ctx)]


def test_tavily_parses_results(monkeypatch):
    monkeypatch.setattr(web_search, "_http_json", lambda **kw: {
        "results": [{"title": "T", "url": "https://e.com", "content": "c"}]})
    ctx = web_search.SearchContext(get_secret=lambda n: "key")
    out = web_search.TavilyProvider().search("q", 5, ctx)
    assert out.results[0].url == "https://e.com"


def test_exa_parses_results(monkeypatch):
    monkeypatch.setattr(web_search, "_http_json", lambda **kw: {
        "results": [{"title": "T", "url": "https://e.com", "text": "t"}]})
    ctx = web_search.SearchContext(get_secret=lambda n: "key")
    out = web_search.ExaProvider().search("q", 5, ctx)
    assert out.results[0].snippet == "t"


def test_keyed_provider_honors_guard(monkeypatch):
    ctx = web_search.SearchContext(get_secret=lambda n: "key",
                                   check_url=lambda url: "Blocked: blocked_domains")
    out = web_search.ExaProvider().search("q", 5, ctx)
    assert isinstance(out, web_search.SearchFailure) and out.retryable is False


def test_keyed_provider_401_is_not_retryable(monkeypatch):
    def _boom(**kw):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(web_search, "_http_json", _boom)
    ctx = web_search.SearchContext(get_secret=lambda n: "bad")
    out = web_search.TavilyProvider().search("q", 5, ctx)
    assert out.retryable is False and "TAVILY_API_KEY" in out.error
```

Add `import urllib.error` to the test file imports.

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_web_search.py -v`
Expected: FAIL — no attribute `TavilyProvider`

**Step 3: Implement** — append to `web_search.py`:

```python
def _http_json(*, url, method="GET", headers=None, body=None, timeout=20):
    """JSON request helper for keyed providers. Callers MUST have run
    ctx.check_url(url) first."""
    data = _json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_SEARCH_BYTES + 1)
    if len(raw) > MAX_SEARCH_BYTES:
        raise ValueError("search response exceeded size limit")
    return _json.loads(raw.decode("utf-8", errors="replace"))


class _KeyedProvider(WebSearchProvider):
    """Shared plumbing for the OPTIONAL API-key backends.

    Optional means exactly one thing: is_available() is False until the key is
    present, so the provider never enters the chain for a user who did not opt
    in — and needs no removal or disabling to stay out of the way.

    Subclasses declare their env var, endpoint, and response shape. Each only
    ever receives its OWN key — engine.py's _outbound_secret_check floor still
    runs per request, so a key cannot be posted to another vendor's host.
    """
    env_var = ""
    endpoint = ""
    label = ""
    signup_url = ""
    blurb = ""

    def setup_schema(self):
        return {"name": self.label, "badge": "optional · API key",
                "tag": self.blurb,
                "env_vars": [{"key": self.env_var,
                              "prompt": f"{self.label} API key",
                              "url": self.signup_url}],
                "post_setup": ""}

    def is_available(self, ctx):
        return bool(ctx.get_secret(self.env_var))

    def _request(self, query, limit, key):
        raise NotImplementedError

    def _parse(self, payload):
        raise NotImplementedError

    def search(self, query, limit, ctx):
        key = ctx.get_secret(self.env_var)
        if not key:
            return SearchFailure(f"{self.env_var} is not set")
        blocked = ctx.check_url(self.endpoint)
        if blocked:
            return SearchFailure(blocked, retryable=False)
        try:
            payload = _http_json(**self._request(query, limit, key))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return SearchFailure(
                    f"{self.label} rejected the credential — check {self.env_var}.",
                    retryable=False)
            if exc.code == 429:
                return SearchFailure(f"{self.label} rate limit reached (HTTP 429).")
            return SearchFailure(f"{self.label} returned HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return SearchFailure(f"Could not reach {self.label}: {exc}")
        except (ValueError, _json.JSONDecodeError):
            return SearchFailure(f"{self.label} returned a malformed response")
        return SearchSuccess(self._parse(payload)[:limit], provider=self.name)


class TavilyProvider(_KeyedProvider):
    env_var = "TAVILY_API_KEY"
    endpoint = "https://api.tavily.com/search"
    label = "Tavily"
    signup_url = "https://tavily.com"
    blurb = "Agent-optimized results with citations"

    @property
    def name(self):
        return "tavily"

    def _request(self, query, limit, key):
        return {"url": self.endpoint, "method": "POST",
                "headers": {"Content-Type": "application/json",
                            "Authorization": f"Bearer {key}"},
                "body": {"query": query, "max_results": limit}}

    def _parse(self, payload):
        return [SearchResult(str(r.get("title") or ""), str(r.get("url") or ""),
                             str(r.get("content") or "")[:400])
                for r in (payload.get("results") or []) if r.get("url")]


class ExaProvider(_KeyedProvider):
    env_var = "EXA_API_KEY"
    endpoint = "https://api.exa.ai/search"
    label = "Exa"
    signup_url = "https://exa.ai"
    blurb = "Semantic/neural search — finds pages by meaning"

    @property
    def name(self):
        return "exa"

    def _request(self, query, limit, key):
        return {"url": self.endpoint, "method": "POST",
                "headers": {"Content-Type": "application/json", "x-api-key": key},
                "body": {"query": query, "numResults": limit,
                         "contents": {"text": {"maxCharacters": 400}}}}

    def _parse(self, payload):
        return [SearchResult(str(r.get("title") or ""), str(r.get("url") or ""),
                             str(r.get("text") or "")[:400])
                for r in (payload.get("results") or []) if r.get("url")]


def default_registry() -> Registry:
    """All four backends. Order here is irrelevant — PREFERENCE decides."""
    return Registry([SearxngProvider(), TavilyProvider(), ExaProvider(),
                     DdgsProvider()])
```

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_web_search.py -v`
Expected: 32 passed

**Step 5: Commit**

```bash
git add -u && git commit -m "feat: optional Tavily and Exa API-key search backends"
```

---

### Task 7: SearXNG settings generation

**Objective:** Generate a `settings.yml` with JSON enabled, limiter off, and a random secret — without touching real user state.

**Files:**
- Create: `src/agent8088/searxng_provision.py`
- Test: `tests/test_searxng_provision.py`

**Step 1: Write failing test**

```python
"""Tests for SearXNG container provisioning. Never runs docker; never writes
outside tmp_path (see the repo convention on verify scripts and user state)."""
import stat

from agent8088 import searxng_provision as sp


def test_settings_enable_json_and_disable_limiter(tmp_path):
    path = sp.write_settings(tmp_path)
    text = path.read_text()
    assert "json" in text and "limiter: false" in text


def test_settings_secret_key_is_random_and_not_the_upstream_placeholder(tmp_path):
    a = sp.write_settings(tmp_path / "a").read_text()
    b = sp.write_settings(tmp_path / "b").read_text()
    assert "ultrasecretkey" not in a
    assert a != b


def test_settings_file_is_owner_only(tmp_path):
    path = sp.write_settings(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_settings_is_idempotent_and_preserves_secret(tmp_path):
    first = sp.write_settings(tmp_path).read_text()
    second = sp.write_settings(tmp_path).read_text()
    assert first == second
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_searxng_provision.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement**

Create `src/agent8088/searxng_provision.py`:

```python
"""Provision a local SearXNG instance for web_search.

Why this exists: the upstream image ships with JSON output DISABLED and the
bot limiter ENABLED, so `docker run searxng/searxng` alone produces an
instance the agent cannot query. This module writes the one settings file that
makes the JSON API usable, then starts the container bound to loopback.

SECURITY: the container is published to 127.0.0.1 only. SearXNG's JSON API has
no authentication — binding it to 0.0.0.0 would put an open search proxy on the
local network.
"""
from __future__ import annotations

import re
import secrets
import shutil
import subprocess
from pathlib import Path

CONTAINER_NAME = "agent8088-searxng"
IMAGE = "searxng/searxng:latest"
HOST_PORT = 8888
BASE_URL = f"http://127.0.0.1:{HOST_PORT}/search?q="

_SETTINGS_TEMPLATE = """# Generated by agent8088 — safe to edit.
use_default_settings: true
server:
  secret_key: "{secret}"
  # Off so the local JSON API is reachable; the instance is bound to loopback.
  limiter: false
  image_proxy: true
search:
  # `json` is NOT enabled upstream by default — without it the agent gets HTML.
  formats:
    - html
    - json
"""


def settings_dir(home: Path) -> Path:
    return Path(home) / "searxng"


def write_settings(home: Path) -> Path:
    """Create (or reuse) settings.yml under *home*. Idempotent: an existing
    file's secret_key is preserved so restarts don't invalidate sessions."""
    directory = settings_dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "settings.yml"
    if path.exists():
        existing = re.search(r'secret_key:\s*"([^"]+)"', path.read_text())
        if existing:
            return path
    path.write_text(_SETTINGS_TEMPLATE.format(secret=secrets.token_hex(32)))
    path.chmod(0o600)
    return path
```

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_searxng_provision.py -v`
Expected: 4 passed

**Step 5: Commit**

```bash
git add src/agent8088/searxng_provision.py tests/test_searxng_provision.py && git commit -m "feat: generate SearXNG settings with JSON API enabled"
```

---

### Task 8: Container start/status/stop

**Objective:** Start, inspect, and stop the container via structured argv — never a shell string.

**Files:**
- Modify: `src/agent8088/searxng_provision.py`
- Test: `tests/test_searxng_provision.py`

**Step 1: Write failing test**

```python
def test_start_binds_loopback_only(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(sp.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.setattr(sp.subprocess, "run",
                        lambda argv, **kw: seen.setdefault("argv", argv)
                        or _ok())
    sp.start(tmp_path)
    argv = seen["argv"]
    assert "-p" in argv
    publish = argv[argv.index("-p") + 1]
    assert publish == "127.0.0.1:8888:8080"
    assert "0.0.0.0" not in " ".join(argv)


def test_start_reports_missing_docker(tmp_path, monkeypatch):
    monkeypatch.setattr(sp.shutil, "which", lambda n: None)
    result = sp.start(tmp_path)
    assert result["ok"] is False and "docker" in result["detail"].lower()


def test_status_reports_not_running_when_inspect_fails(monkeypatch):
    monkeypatch.setattr(sp.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.setattr(sp.subprocess, "run",
                        lambda argv, **kw: _fail())
    assert sp.status()["running"] is False


def test_stop_uses_container_name(monkeypatch):
    seen = {}
    monkeypatch.setattr(sp.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.setattr(sp.subprocess, "run",
                        lambda argv, **kw: seen.setdefault("argv", argv) or _ok())
    sp.stop()
    assert sp.CONTAINER_NAME in seen["argv"]
```

Add at the top of the test file:

```python
from types import SimpleNamespace


def _ok(stdout="true"):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _fail(stderr="no such container"):
    return SimpleNamespace(returncode=1, stdout="", stderr=stderr)
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_searxng_provision.py -v`
Expected: FAIL — no attribute `start`

**Step 3: Implement** — append to `searxng_provision.py`:

```python
def _docker() -> str | None:
    return shutil.which("docker")


def _run(argv, timeout=90):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def status() -> dict:
    """Whether the managed container exists and is running."""
    docker = _docker()
    if not docker:
        return {"running": False, "detail": "docker is not installed"}
    try:
        result = _run([docker, "inspect", "-f", "{{.State.Running}}",
                       CONTAINER_NAME], timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"running": False, "detail": str(exc)}
    if result.returncode != 0:
        return {"running": False, "detail": "container does not exist"}
    running = result.stdout.strip() == "true"
    return {"running": running,
            "detail": "running" if running else "container exists but is stopped"}


def start(home: Path) -> dict:
    """Write settings, then start (or restart) the container on loopback."""
    docker = _docker()
    if not docker:
        return {"ok": False,
                "detail": "docker is not installed — the bundled keyless ddgs fallback "
                          "is already handling web_search, or point search_base_url at "
                          "a remote instance"}
    settings = write_settings(home)
    existing = status()
    if existing["running"]:
        return {"ok": True, "detail": "already running", "base_url": BASE_URL}
    # Remove a stopped leftover so `run` does not fail on a name collision.
    if existing["detail"] == "container exists but is stopped":
        _run([docker, "rm", "-f", CONTAINER_NAME], timeout=30)
    argv = [
        docker, "run", "-d", "--name", CONTAINER_NAME, "--restart", "unless-stopped",
        # Loopback ONLY: the JSON API is unauthenticated.
        "-p", f"127.0.0.1:{HOST_PORT}:8080",
        "-v", f"{settings.parent}:/etc/searxng",
        IMAGE,
    ]
    try:
        result = _run(argv)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "detail": f"docker run failed: {exc}"}
    if result.returncode != 0:
        return {"ok": False, "detail": (result.stderr or result.stdout).strip()[:400]}
    return {"ok": True, "detail": f"started on 127.0.0.1:{HOST_PORT}",
            "base_url": BASE_URL}


def stop() -> dict:
    docker = _docker()
    if not docker:
        return {"ok": False, "detail": "docker is not installed"}
    try:
        result = _run([docker, "rm", "-f", CONTAINER_NAME], timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": result.returncode == 0,
            "detail": "removed" if result.returncode == 0
                      else (result.stderr or "").strip()[:400]}
```

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_searxng_provision.py -v`
Expected: 8 passed

**Step 5: Commit**

```bash
git add -u && git commit -m "feat: start, inspect, and stop the managed SearXNG container"
```

---

### Task 9: Readiness poll

**Objective:** After start, wait until the JSON API actually answers — and name the exact cause when it does not.

**Files:**
- Modify: `src/agent8088/searxng_provision.py`
- Test: `tests/test_searxng_provision.py`

**Step 1: Write failing test**

```python
def test_wait_ready_succeeds_once_json_answers(monkeypatch):
    calls = {"n": 0}

    def _probe(url, timeout):
        calls["n"] += 1
        if calls["n"] < 2:
            raise OSError("connection refused")
        return {"results": []}

    monkeypatch.setattr(sp, "_probe_json", _probe)
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    assert sp.wait_ready(attempts=5)["ok"] is True


def test_wait_ready_reports_json_disabled(monkeypatch):
    monkeypatch.setattr(sp, "_probe_json", lambda url, timeout: (_ for _ in ()).throw(ValueError("not json")))
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    result = sp.wait_ready(attempts=2)
    assert result["ok"] is False and "formats" in result["detail"]


def test_wait_ready_gives_up_and_says_so(monkeypatch):
    monkeypatch.setattr(sp, "_probe_json", lambda url, timeout: (_ for _ in ()).throw(OSError("refused")))
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    result = sp.wait_ready(attempts=2)
    assert result["ok"] is False and "did not become ready" in result["detail"]
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_searxng_provision.py -v`
Expected: FAIL — no attribute `wait_ready`

**Step 3: Implement** — append to `searxng_provision.py` (add `import json`, `import time`, `import urllib.request` at the top):

```python
def _probe_json(url: str, timeout: int):
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(65536).decode("utf-8", errors="replace"))


def wait_ready(attempts: int = 20, delay: float = 1.5) -> dict:
    """Poll the JSON API until it answers.

    A ValueError means the endpoint served HTML — i.e. `json` is missing from
    search.formats. That is the single most common misconfiguration, so it gets
    its own message instead of a generic timeout.
    """
    probe = f"{BASE_URL}agent8088-readiness-check&format=json"
    last = ""
    for _ in range(max(1, attempts)):
        try:
            _probe_json(probe, timeout=5)
            return {"ok": True, "detail": "JSON API responding"}
        except ValueError:
            return {"ok": False,
                    "detail": "instance is up but did not return JSON — add `json` to "
                              "search.formats in settings.yml"}
        except Exception as exc:  # noqa: BLE001 — transport variety during boot
            last = str(exc)
        time.sleep(delay)
    return {"ok": False,
            "detail": f"instance did not become ready ({last or 'no response'})"}
```

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_searxng_provision.py -v`
Expected: 11 passed

**Step 5: Commit**

```bash
git add -u && git commit -m "feat: poll SearXNG readiness and name the json-format misconfiguration"
```

---

### Task 10: Wire `mode=search` into the engine

**Objective:** `web_search` executes through the registry with engine guards injected.

**Files:**
- Modify: `src/agent8088/engine.py` (new block after the `http_get`/`http_post` block ending at `engine.py:2945`)
- Modify: `src/agent8088/tools.txt:4`
- Test: `tests/test_web_search_engine.py`

**Step 1: Write failing test**

```python
"""Engine-side wiring for mode=search: guard injection and permission gating."""


def test_web_search_declares_search_mode(engine):
    assert engine.TOOL_SPECS["web_search"]["mode"] == "search"
    assert engine.TOOL_SPECS["web_search"]["args"] == ["query"]


def test_legacy_provider_tools_are_gone(engine):
    assert "web_search_tavily" not in engine.TOOL_SPECS
    assert "web_search_exa" not in engine.TOOL_SPECS


def test_search_context_guard_chains_egress_then_ssrf(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "_egress_check", lambda url: calls.append(("egress", url)) or None)
    monkeypatch.setattr(engine, "_ssrf_check", lambda url: calls.append(("ssrf", url)) or None)
    ctx = engine._search_context()
    assert ctx.check_url("https://example.com") is None
    assert [c[0] for c in calls] == ["egress", "ssrf"]


def test_search_context_guard_blocks_outbound_secret(engine, monkeypatch):
    monkeypatch.setattr(engine, "_egress_check", lambda url: None)
    monkeypatch.setattr(engine, "_ssrf_check", lambda url: None)
    monkeypatch.setattr(engine, "_outbound_secret_check", lambda text: "Blocked: credential in URL")
    assert "credential" in engine._search_context().check_url("https://e.com?k=secret")


def test_search_results_are_wrapped_untrusted(engine, monkeypatch):
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda q, l, r, c, ctx: ctx.wrap("body", source="web_search:stub"))
    out = engine.execute_tool("web_search", {"query": "hi"})
    assert "EXTERNAL_UNTRUSTED_CONTENT" in out


def test_search_requires_permission_in_readonly(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(engine, "check_permission", lambda mode, target: False)
    out = engine.execute_tool("web_search", {"query": "hi"})
    assert "ESCALATION" in out.upper() or "permission" in out.lower()
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_web_search_engine.py -v`
Expected: FAIL — `web_search` mode is still `http_get`

**Step 3: Implement**

3a. `src/agent8088/tools.txt` — replace line 4 and delete lines 22–25 (the "Search fallbacks" comment block and the two provider tools):

```
web_search|Search the web. Routes to the configured backend (SearXNG by default, Tavily or Exa when an API key is set, keyless ddgs as fallback) and falls back automatically when one is unavailable.|mode=search|args=query|timeout=30
```

3b. `src/agent8088/engine.py` — add the import near the other first-party imports and build the context + dispatch. Insert immediately after the `http_get`/`http_post` block (after `engine.py:2945`):

```python
    # --- Layer 2b: web search (mode=search) ---
    # Its own block rather than a branch of http_get: the destination URL is not
    # known until the provider chain is resolved, and a fallback may contact a
    # different host. Guards are therefore applied per attempt, inside the
    # provider, via the injected check_url.
    if mode == "search":
        query = str(args.get("query") or "").strip()
        if not query:
            return "Error: web_search requires 'query'."
        if not check_permission(mode, f"web_search: {query[:80]}"):
            _audit("escalation_requested", tool=name, mode=mode,
                   decision="blocked", detail=query[:120],
                   change_type="network_request")
            return request_escalation(
                target_mode="edit",
                paths=[f"web_search: {query[:100]}"],
                change_type="network_request",
                reason=f"Tool '{name}' wants to search the web for: {query[:160]}",
            )
        _audit("tool_call", tool=name, mode=mode, decision="allowed",
               detail=query[:200])
        return web_search.run_search(
            query, int(APP_CONFIG.get("web_search_results", "5")),
            WEB_SEARCH_REGISTRY, APP_CONFIG, _search_context())
```

Add near the SSRF section (after `_egress_check`, so both guards are defined):

```python
def _search_context():
    """Build the guard bundle handed to every web search provider.

    Providers live in web_search.py, which must not import engine.py. Passing
    the guards in keeps _egress_check / _ssrf_check / _outbound_secret_check as
    the single enforcement point — a provider cannot accidentally skip them.
    """
    def check_url(url: str):
        return (_egress_check(url) or _ssrf_check(url)
                or _outbound_secret_check(url))

    return web_search.SearchContext(
        config=APP_CONFIG,
        get_secret=lambda name: read_env_value(ENV_FILE_PATH, name) or os.environ.get(name, ""),
        check_url=check_url,
        wrap=_wrap_untrusted,
    )


WEB_SEARCH_REGISTRY = web_search.default_registry()
```

> Confirm the exact name of the `.env` reader before writing this (`grep -n "def read_env_value\|def _read_env" src/agent8088/engine.py`) and use the real one.

Also add `"search"` to the gated-mode tuples so the permission layer treats it like a network action — `engine.py:3037` (`gated_modes`) and `engine.py:3102`.

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_web_search_engine.py -v`
Expected: 6 passed

**Step 5: Commit**

```bash
git add -u && git commit -m "feat: route web_search through the provider registry via mode=search"
```

---

### Task 11: Repair the existing suites

**Objective:** Tests that asserted the old three-tool surface now assert the new one.

**Files:**
- Modify: `tests/test_http_search.py` (`test_search_tools_declared`, ~line 145)
- Modify: `tests/test_mcp_server.py`
- Modify: `src/agent8088/mcp_server.py:59-64`

**Step 1: Run the full suite to see what breaks**

Run: `python -m pytest tests/ -q 2>&1 | tail -30`
Expected: failures in `test_search_tools_declared` and the MCP exposed-tool assertions.

**Step 2: Update `mcp_server.py`** — remove `"web_search_tavily"` and `"web_search_exa"` from `EXPOSED_TOOLS`, keeping `"web_search"`. Leave the loopback SSRF allowance at `mcp_server.py:196-202` untouched — it is still exactly what the SearXNG provider needs.

**Step 3: Update the assertions** in `tests/test_http_search.py` and `tests/test_mcp_server.py` to expect the single `web_search` tool with `mode=search`.

**Step 4: Run to verify pass**

Run: `python -m pytest tests/ -q`
Expected: all pass, 0 failures.

**Step 5: Commit**

```bash
git add -u && git commit -m "test: update tool-surface assertions for the single web_search tool"
```

---

### Task 12: `/search` slash command

**Objective:** `status`, `setup`, `stop`, `doctor`, and `use <provider>`.

**Files:**
- Modify: `src/agent8088/cli.py` (add `cmd_search` near `cmd_sandbox` at `cli.py:1490`; register in `COMMANDS` at `cli.py:1920`)
- Test: `tests/test_cli_search.py`

**Step 1: Write failing test**

```python
def test_search_command_is_registered():
    from agent8088 import cli
    assert "search" in cli.COMMANDS


def test_search_status_lists_every_provider_including_unconfigured(monkeypatch, capsys):
    """Optional backends must be DISCOVERABLE even when they have no key —
    otherwise a user never learns Tavily/Exa are available to them."""
    from agent8088 import cli
    cli.cmd_search("status")
    out = capsys.readouterr().out.lower()
    for name in ("searxng", "tavily", "exa", "ddgs"):
        assert name in out
    assert "tavily_api_key" in out  # the hint tells them how to enable it


def test_search_use_rejects_unknown_provider(capsys):
    from agent8088 import cli
    cli.cmd_search("use nope")
    assert "nope" in capsys.readouterr().out


def test_search_setup_without_docker_recommends_ddgs(monkeypatch, capsys):
    from agent8088 import cli
    monkeypatch.setattr(cli.A, "_docker_available", lambda: False)
    cli.cmd_search("setup")
    assert "ddgs" in capsys.readouterr().out.lower()
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_cli_search.py -v`
Expected: FAIL — `cmd_search` does not exist

**Step 3: Implement** `cmd_search` following the `cmd_sandbox` shape (Rich `Table`, `status_cm` for the slow provisioning step):

- `status` (default) — table rendered from each provider's `setup_schema()`: name / badge / available / setup hint, plus the resolved chain. Every backend is listed, configured or not, so optional ones are discoverable.
- `setup` — branch on `A._docker_available()`:
  - **Docker present:** `searxng_provision.start(_agent8088_home())` → `wait_ready()` → on success persist `search_base_url` and `web_search_provider=searxng` via the existing `update_simple_config`.
  - **Docker absent:** report that the keyless ddgs fallback is already active (it ships with the agent — nothing to install), then offer the optional-key path and the remote-instance path for users who want better results.
- `stop` — `searxng_provision.stop()`.
- `doctor` — container state, `wait_ready()` detail, `ssrf_allow_hosts` coverage of the configured host, whether `ddgs` imports, and which optional keys are present (**names only, never values** — `_redact_secrets` covers the output regardless).
- `use <provider>` — validate against `web_search.PREFERENCE`, then persist `web_search_provider`. Warn, but still write, when the chosen provider is not currently available, so pinning `tavily` before pasting the key is not a dead end.

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_cli_search.py -v`
Expected: 4 passed

**Step 5: Commit**

```bash
git add -u && git commit -m "feat: /search command for provider status, setup, and diagnosis"
```

---

### Task 13: Setup wizard integration

**Objective:** Replace the raw URL prompt with a real choice, defaulting to SearXNG when Docker is present.

**Files:**
- Modify: `src/agent8088/cli.py:2282-2293` (prompt) and `cli.py:2312-2315` (write)
- Test: `tests/test_cli_setup.py`

**Step 1: Write failing test**

```python
def test_wizard_offers_searxng_first_when_docker_present(monkeypatch):
    """With Docker available, the recommended default is SearXNG."""
    # Assert on the option list the wizard builds, not on interactive I/O.
    from agent8088 import cli
    monkeypatch.setattr(cli.A, "_docker_available", lambda: True)
    options = cli._search_setup_options()
    assert options[0].startswith("SearXNG")


def test_wizard_offers_ddgs_first_without_docker(monkeypatch):
    from agent8088 import cli
    monkeypatch.setattr(cli.A, "_docker_available", lambda: False)
    options = cli._search_setup_options()
    assert "ddgs" in options[0].lower()
    # It ships with the agent — the wizard must not imply an install step.
    assert "install" not in options[0].lower()
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_cli_setup.py -k search -v`
Expected: FAIL — `_search_setup_options` does not exist

**Step 3: Implement**

Add `_search_setup_options()` returning a Docker-aware ordered list, and replace the `_custom_prompt("Web search URL (SearXNG):" …)` call with a `_choice_prompt` over it:

1. `SearXNG (recommended — provision locally with Docker)` — only when `A._docker_available()`
2. `ddgs (keyless fallback — already installed, nothing to do)`
3. `Existing SearXNG / remote instance URL` → falls back to today's free-text prompt
4. `Tavily (optional — API key)` → key written to the `.env` store, never `config.txt`
5. `Exa (optional — API key)` → same
6. `None (disable web search)`

Options 4 and 5 are rendered from each provider's `setup_schema()["env_vars"]`, so the prompt text and signup URL live with the provider rather than in the wizard.

Keep the existing `search_base_url` write path (`cli.py:2312-2315`) for options 1 and 3 so behavior for current users is unchanged; add a `web_search_provider=` line for 2, 4, and 5.

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_cli_setup.py -v`
Expected: all pass

**Step 5: Commit**

```bash
git add -u && git commit -m "feat: wizard offers Docker-aware web search setup"
```

---

### Task 14: `describe_capabilities` reports search state

**Objective:** `/capabilities` shows the live provider chain, not a guess.

**Files:**
- Modify: `src/agent8088/engine.py:3804-3890`
- Test: `tests/test_capabilities.py`

**Step 1: Write failing test**

```python
def test_capabilities_reports_active_search_provider(engine, monkeypatch):
    monkeypatch.setitem(engine.APP_CONFIG, "search_base_url", "http://127.0.0.1:8888/search?q=")
    report = engine.describe_capabilities()
    assert "Web search" in report and "searxng" in report


def test_capabilities_reports_no_search_provider(engine, monkeypatch):
    monkeypatch.setitem(engine.APP_CONFIG, "search_base_url", "")
    monkeypatch.setattr(engine.web_search, "_ddgs_installed", lambda: False)
    report = engine.describe_capabilities()
    assert "none configured" in report.lower()
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_capabilities.py -k search -v`
Expected: FAIL — no "Web search" line

**Step 3: Implement** — in the guardrails block near `engine.py:3871`, add:

```python
        f"- Web search: {_search_chain_summary()}",
```

with a helper beside `_search_context()`:

```python
def _search_chain_summary() -> str:
    """Which providers would serve web_search right now, in order."""
    try:
        chain = WEB_SEARCH_REGISTRY.chain(APP_CONFIG, _search_context())
    except Exception:
        return "unavailable"
    if not chain:
        return "none configured (run /search setup)"
    return " -> ".join(p.name for p in chain)
```

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_capabilities.py -v`
Expected: all pass

**Step 5: Commit**

```bash
git add -u && git commit -m "feat: report the live web search provider chain in capabilities"
```

---

### Task 15: Packaging — `ddgs` ships with the agent

**Objective:** `ddgs` is a first-class dependency so the fallback is always present.

**Decision:** `ddgs` goes in `[project] dependencies`, not an optional extra. A fallback that may not be installed is not a fallback — if SearXNG is down and the keyless backend needs a `pip install` first, the user gets an error exactly when they most needed a result. Bundling it means `web_search` works on a fresh install with no Docker, no key, and no setup step.

**Consequences to accept, deliberately:**
- Every install now carries a scraping library, including gateway and MCP-server deployments that may never search. Accepted for the always-works guarantee.
- **D8's containment matters more, not less**, now that the library is always importable — the fail-closed egress pre-flight is the only thing standing between it and an operator's `allowed_domains` policy. Task 18's security review is mandatory, not optional.
- `is_available()` still checks importability rather than assuming presence. Cheap, and it keeps a stripped or partially-installed environment reporting "unavailable" instead of raising ImportError mid-search.

**Files:**
- Modify: `pyproject.toml:10-16`

**Step 1: Implement**

```toml
dependencies = [
    "openai>=1.0.0,<3",
    "rich>=13.0.0,<16",
    "InquirerPy>=0.3.4,<1",
    "mcp>=1.27,<2",
    # Keyless web search fallback. A hard dependency on purpose: web_search must
    # always have a backend that needs no Docker, no key, and no setup step.
    "ddgs>=9,<10",
]
```

Leave `[project.optional-dependencies]` unchanged — no `search` extra is needed now. If one was already added in an earlier pass, remove it.

**Step 2: Verify the dependency resolves and the provider is live**

```bash
pip install -e . && python -c "
import agent8088.web_search as w
print('available:', w.DdgsProvider().is_available(w.SearchContext()))"
```
Expected: `available: True`

**Step 3: Confirm the always-works guarantee with nothing configured**

```bash
python -c "
import agent8088.web_search as w
r = w.default_registry()
ctx = w.SearchContext(config={}, get_secret=lambda n: '')
print([p.name for p in r.chain({}, ctx)])"
```
Expected: `['ddgs']` — never an empty chain.

**Step 4: Commit**

```bash
git add pyproject.toml && git commit -m "build: ship ddgs as a dependency so web search always has a fallback"
```

---

### Task 16: Config template

**Objective:** Document every new key where users will look.

**Files:**
- Modify: `src/agent8088/config.txt:76-78`

**Step 1: Implement** — replace the `--- search (SearXNG) ---` block:

```
# --- web search ---
# One web_search tool, four interchangeable backends. With nothing set below,
# the agent picks the first available: searxng -> tavily -> exa -> ddgs.
#   searxng  default   self-hosted, no key   (`/search setup` provisions it)
#   tavily   optional  needs TAVILY_API_KEY
#   exa      optional  needs EXA_API_KEY
#   ddgs     fallback  keyless, bundled — always available, needs no setup
# Run `/search setup` to provision a local SearXNG (needs Docker), install the
# keyless fallback, or paste an API key.
#
# Pin a specific backend (skips auto-selection and the fallback chain):
# web_search_provider=searxng
#
# SearXNG instance. `/search setup` writes this for you. A remote instance
# must use https:// — plaintext http:// is only accepted for loopback/private
# hosts. The host must also appear in ssrf_allow_hosts above.
# search_base_url=http://127.0.0.1:8888/search?q=
#
# Results per search (default 5):
# web_search_results=5
#
# Optional backends. Add a key and they join the chain automatically; leave
# them unset and they stay out of it entirely. Keys live in the .env store
# next to this file, NEVER here:
#   TAVILY_API_KEY   agent-optimized results with citations  (https://tavily.com)
#   EXA_API_KEY      semantic/neural search                  (https://exa.ai)
#
# The keyless ddgs fallback needs nothing: it ships with agent8088 and is used
# automatically whenever SearXNG is unset, unreachable, or failing.
```

**Step 2: Verify the template still parses**

Run: `python -m pytest tests/test_http_search.py -v`
Expected: all pass (including `test_config_defaults_are_visible_to_templates`).

**Step 3: Commit**

```bash
git add -u && git commit -m "docs: document web search provider keys in config.txt"
```

---

### Task 17: Documentation

**Objective:** Wiki and README match the shipped behavior.

**Files:**
- Modify: `docs/wiki/04-tools.md:19` — one `web_search` row, `mode=search`, note the provider chain
- Modify: `docs/wiki/02-configuration.md` — add `web_search_provider`, `web_search_results`, `search_base_url` rows next to the existing `ssrf_allow_hosts` row (line ~57)
- Modify: `docs/wiki/01-getting-started.md` — a "Web search" section: Docker path, keyless path, API-key path
- Modify: `docs/wiki/13-troubleshooting.md` — "SearXNG returns HTML not JSON" (`search.formats`), "HTTP 429/403 from SearXNG" (`limiter`), "ddgs rate limited", "host not in `ssrf_allow_hosts`"
- Modify: `README.md` — replace any SearXNG-only web search mention
- Modify: `CHANGELOG.md` — feature entry **plus** the `web_search_tavily`/`web_search_exa` removal note with the migration line

**Step 1:** Make the edits.

**Step 2: Verify no stale references remain**

Run: `grep -rn "web_search_tavily\|web_search_exa" --include="*.md" --include="*.py" --include="*.txt" . | grep -v CHANGELOG`
Expected: no output.

**Step 3: Commit**

```bash
git add -u && git commit -m "docs: document the web search provider registry and migration"
```

---

### Task 18: Full verification

**Objective:** Prove the whole thing before opening a PR.

**Step 1:** Unit + feature + integration suites.

```bash
python -m pytest tests/ -q
```
Expected: all pass, 0 failures.

**Step 2:** Lint.

```bash
python -m ruff check src/ tests/
```
Expected: no findings.

**Step 3:** Repo verification scripts.

```bash
python scripts/verify_everything.py
```
Expected: pass. If it exercises web search, confirm it mocks `subprocess.run` and writes only to a temp `AGENT8088_HOME` — the repo convention is that verify scripts never touch real user state.

**Step 4:** Manual smoke — keyless path, nothing configured, nothing installed by hand.

```bash
python -c "
from agent8088 import engine as A
print(A.execute_tool('web_search', {'query': 'searxng json api'}))"
```
Expected: wrapped results tagged `via ddgs`.

**Step 4b:** Manual smoke — the motivating scenario: SearXNG configured but down.

```bash
AGENT8088_CONFIG=/dev/null python -c "
from agent8088 import engine as A
A.APP_CONFIG['search_base_url'] = 'http://127.0.0.1:9/search?q='  # nothing listening
print(A.execute_tool('web_search', {'query': 'fallback check'}))"
```
Expected: results served `via ddgs`, not an error. This is the guarantee bundling buys.

**Step 5:** Manual smoke — Docker path (only on a machine with Docker).

```bash
python -c "
from agent8088 import cli; cli.cmd_search('setup'); cli.cmd_search('doctor')"
```
Expected: container starts, readiness passes, `search_base_url` written, `/search status` shows `searxng` first in the chain.

**Step 6:** Security review — the diff touches the network guard surface, so run the repo's dedicated reviewer.

Ask for the `security-reviewer` agent over the diff, specifically checking: no provider path skips `check_url`; the container is loopback-bound; keys never reach `config.txt` or a foreign host; results are wrapped untrusted.

**Step 7: Commit and open the PR** (ask first — do not push unprompted).

```bash
git push -u origin feat/web-search-providers
```

---

## Files Changed

| File | Change |
|---|---|
| `src/agent8088/web_search.py` | **new** — registry, 4 providers, fallback chain |
| `src/agent8088/searxng_provision.py` | **new** — settings generation, container lifecycle, readiness |
| `src/agent8088/engine.py` | `mode=search` dispatch, `_search_context()`, `_search_chain_summary()`, gated-mode tuples, capabilities line |
| `src/agent8088/tools.txt` | one `web_search` (`mode=search`); remove tavily/exa tools |
| `src/agent8088/config.txt` | new `--- web search ---` block |
| `src/agent8088/cli.py` | `cmd_search`, `COMMANDS` entry, wizard `_search_setup_options()` |
| `src/agent8088/mcp_server.py` | trim `EXPOSED_TOOLS` |
| `pyproject.toml` | `ddgs>=9,<10` added to `[project] dependencies` |
| `tests/test_web_search.py` | **new** — 32 tests |
| `tests/test_searxng_provision.py` | **new** — 11 tests |
| `tests/test_web_search_engine.py` | **new** — 6 tests |
| `tests/test_cli_search.py` | **new** — 4 tests |
| `tests/test_http_search.py`, `tests/test_mcp_server.py`, `tests/test_cli_setup.py`, `tests/test_capabilities.py` | updated assertions |
| `docs/wiki/{01,02,04,13}-*.md`, `README.md`, `CHANGELOG.md` | documentation |

## Test Isolation Rules

Non-negotiable, per this repo's conventions:

1. **No real Docker.** Patch `searxng_provision.subprocess.run` and `shutil.which`. No test may start a container.
2. **No network.** Patch `_http_get_json`, `_http_json`, `_ddgs_text`, `_probe_json`. No test may reach DuckDuckGo, Tavily, or a SearXNG instance.
3. **No real user state.** Settings tests use `tmp_path`. Never write to `~/.agent8088`. `conftest.py` already points `AGENT8088_CONFIG` at a non-existent file — keep it that way.
4. **No `time.sleep`.** Patch it in readiness tests.

## Risks and Tradeoffs

| Risk | Mitigation |
|---|---|
| Consolidating `web_search_tavily`/`web_search_exa` into one tool is a surface change | Both backends remain fully supported and key-gated — only the tool *names* go away. They never worked out of the box (no `tool_headers.*` shipped in `config.txt`), so real-world impact is ~zero. `CHANGELOG.md` gets the migration line. |
| `ddgs` bypasses `_exec_http`'s guard because it owns its HTTP client — and it is now always installed, so this is always in play | Contained per **D8**: fail-closed pre-flight over its complete fixed host set, non-retryable on denial, untrusted-wrapped output, keyless so nothing to leak, plus dedicated tests and the **mandatory** `security-reviewer` pass in Task 18. Bundling raises the stakes on this guard; it does not weaken it. |
| Shipping a scraping library to every install (incl. gateway/MCP deployments that never search) | Accepted trade for the always-works guarantee, per your call. Documented in `CHANGELOG.md` so operators with dependency-policy constraints can see it. `web_search_provider=searxng` pins away from it for anyone who wants that. |
| An optional backend is invisible, so users never discover Tavily/Exa exist | `/search status` lists every backend with its badge and the env var that enables it, whether configured or not; the wizard offers both explicitly. |
| A provisioned container is a long-lived background service the user may forget | `--restart unless-stopped` is deliberate, but `/search status` and `/search stop` make it visible and removable; `/search doctor` reports it. |
| `ddgs` scrapes and can break when DuckDuckGo changes markup | It is last in `PREFERENCE`, and its failures are retryable so the chain continues. Documented as a fallback, never the recommended primary. |
| Port 8888 may already be in use | `start()` surfaces the docker error verbatim; `/search doctor` reports it. A configurable port is deliberately deferred (YAGNI) — a user with a conflict can set `search_base_url` by hand. |
| Fallback chain could mask a misconfigured primary | `format_results` always names the serving provider, so "via ddgs" when SearXNG was expected is visible in the output. |

## Resolved Decisions

1. **Tavily and Exa are kept** as optional key-gated backends behind the single `web_search` tool — Hermes' exact arrangement (`web.backend: … | tavily | exa`, keys in `.env`). Only the separate tool names are consolidated. → **D1**
2. **`ddgs` containment** follows the fail-closed pre-flight over its complete fixed host set, with non-retryable denial, untrusted-wrapped output, two dedicated tests, and a mandatory security review. Shell-out and sidecar-server alternatives were considered and rejected. → **D8**
3. **Roles are fixed:** searxng default, ddgs fallback, tavily/exa optional. Brave dropped as unrequested surface. → **D2**
4. **`ddgs` ships as a dependency of agent8088**, not an optional extra. A fallback that might need installing first is not a fallback — when SearXNG is reported broken, the keyless backend has to already be there. Web search therefore works on a fresh install with no Docker, no key, and no setup. → **Task 15**

## Open Questions

1. **Auto-provision without asking?** The plan makes `/search setup` and the wizard explicit rather than silently starting a container on first run. Starting a long-lived background Docker service unprompted is the kind of side effect that should be a user's choice. Say so if you want first-run auto-provisioning instead.
2. **`web_extract` / page fetching** is out of scope — `browse_page` and `get_page_title` already cover it. Hermes pairs a search backend with a separate extract backend; if that pairing is wanted later, the registry's capability flags are the natural place for it.
