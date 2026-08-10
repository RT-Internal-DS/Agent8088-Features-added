# Tool-Use Intelligence Implementation Plan

> **For the implementer:** work task-by-task, in order. Each task is 2–5 minutes. Run the test before writing the code, watch it fail, then make it pass, then commit.

**Goal:** Make Agent8088 pick the smallest tool that actually answers the request — never calling one for stable knowledge, never repeating an equivalent search, always date-qualifying time-sensitive queries, and never presenting a stale result as current.

**Architecture:** Three layers, each doing only what it is good at. (1) **Runtime context** — tell the model today's date, which it currently never learns. (2) **Prompt policy** — the judgement calls, in `system.md`. (3) **Engine enforcement** — the mechanical rules a prompt cannot be trusted with: query augmentation, result framing, equivalent-query dedupe, follow-up gating. Enforcement lives in `engine.py` so all four front ends (CLI, gateway, MCP server, cron) inherit it, matching the existing rule that adapters translate transport only.

**Tech Stack:** Python ≥3.10, pytest, `uv`. No new dependencies.

---

## Current context — what already exists

**Read this before writing anything.** Roughly half the brief is already implemented on `development`. Rebuilding it would be wasted work and would churn code the team just shipped.

| Requirement from the brief | Status | Evidence |
|---|---|---|
| Answer directly; don't call a tool for stable knowledge | **Done** (prompt) | `src/agent8088/system.md:7-15`, `:19-22` |
| Auto-search for current/time-sensitive info | **Done** (prompt) | `src/agent8088/system.md:25-30` |
| No browser follow-up after a successful search | **Done** (code) | `src/agent8088/engine.py:4408-4419`, escape hatch `_user_supplied_url` at `:4246` |
| Preserve explicit user requests (URL, page inspect) | **Done** (code) | `_user_supplied_url`, `src/agent8088/engine.py:4246-4251` |
| Exact-duplicate tool call prevention | **Done** (code) | `sig` at `src/agent8088/engine.py:4406`, `:4421` |
| Routine web search must not prompt | **Done** (code) | `_local_searxng_no_prompt_enabled`, `src/agent8088/engine.py:3942`, gate at `:3010` |
| Search queries not exposed in the UI | **Done** (code) | `src/agent8088/cli.py:556` prints `⏺ Searching the web…` only |
| Credentials never leave in a query | **Done** (code) | `_web_search_query_guard`, `src/agent8088/engine.py:3924` |
| **Model knows today's date** | **MISSING** | `_session_system_prompt` (`src/agent8088/cli.py:355-364`) injects permission mode only; no date anywhere |
| **Date-qualify relative-time queries** | **MISSING** | no query rewriting exists |
| **Reject stale results / never call a past event "next"** | **MISSING** | results returned raw, `src/agent8088/engine.py:3024` |
| **Equivalent (not just identical) query dedupe** | **MISSING** | `sig` is `json.dumps(args)` — byte-exact only |
| **No shell/MCP follow-up after search** | **MISSING** | gate at `:4408` covers `browse_page`/`get_page_title` only |
| **MCP tool-selection guidance** | **MISSING** | `system.md` never mentions MCP |
| **Tests write to `artifacts/`, not repo root** | **MISSING** | no `artifacts/` dir; no fixture |

### The root finding

**The model is never told what day it is.** `_session_system_prompt` (`src/agent8088/cli.py:355`) and the gateway's `build_system_prompt` (`src/agent8088/gateway/agent_bridge.py:6`) both assemble base prompt + tool docs + permission mode — and stop. `grep -n "date" src/agent8088/system.md` returns nothing.

Every date requirement in the brief is downstream of this. A model with no clock cannot add "2026" to a query, cannot notice a 2019 page is stale, and cannot know that the "next election" it remembers from training has already happened. Fix this first; several other requirements get much easier once it lands.

---

## Design decisions and tradeoffs

**Where enforcement goes.** Anything with a crisp mechanical rule goes in code (augmentation, dedupe, framing). Anything needing judgement stays in the prompt (is this request time-sensitive?). Code cannot decide "is this stable knowledge"; prompts cannot be relied on to never repeat a query.

**Query rewriting is deliberately narrow.** `_augment_relative_time_query` fires only when the query contains a relative-time marker *and* carries no explicit year. `"iPhone 2019 reviews"` is never touched. Behind a config flag (`search_date_augmentation`, default on) so it can be switched off without a release.

**Shell/MCP follow-up gating is narrower than the brief asks.** The brief says don't use shell/MCP as unnecessary follow-ups to a search. Enforcing that broadly in code is unsafe: after searching for a library version the user may legitimately need `pip install`, and blocking it would be a worse bug than the one being fixed. Task 14 gates only *network-fetch* shell commands (`curl`, `wget`, `httpie`, `lynx`, `w3m`) and MCP tools whose name implies fetching (`search`/`fetch`/`browse`/`web`/`http`), both with an explicit-user-request escape hatch. The rest is left to the prompt. **This is an open question — see the end.**

**Stale-result rejection is framing, not filtering.** Code cannot reliably parse a date out of arbitrary snippets, and dropping results on a bad parse would lose good answers. Instead, results are stamped with the retrieval date and an instruction to check each item's own date. That is honest about what the layer can do; the prompt rule does the rest.

**Timezone:** system local time via `datetime.now().astimezone()`. "Today" means the user's today, not UTC's.

---

## Phase 0 — Test scaffolding

### Task 1: Create the artifacts directory

**Objective:** Give tests a home for generated files so nothing lands in the repo root.

**Files:**
- Create: `artifacts/.gitkeep`
- Modify: `.gitignore`

**Step 1: Create the directory**

```bash
mkdir -p artifacts && touch artifacts/.gitkeep
```

**Step 2: Ignore its contents but keep the directory**

Append to `.gitignore`:

```
# Test and run artifacts — generated files never belong in the repo root
artifacts/*
!artifacts/.gitkeep
```

**Step 3: Verify**

Run: `git status --short`
Expected: `.gitignore` modified and `artifacts/.gitkeep` untracked; no other new files.

**Step 4: Commit**

```bash
git add .gitignore artifacts/.gitkeep
git commit -m "chore: add artifacts/ for generated test files"
```

---

### Task 2: Add the `artifacts_dir` fixture and a root-pollution guard

**Objective:** Give tests a writable path, and make it *fail loudly* if any test writes to the repo root.

**Files:**
- Modify: `tests/conftest.py` (append at end)

**Step 1: Write the failing test**

Create `tests/test_artifacts_fixture.py`:

```python
"""The artifacts fixture, and the guard that keeps the repo root clean."""


def test_artifacts_dir_exists_and_is_writable(artifacts_dir):
    target = artifacts_dir / "sample.txt"
    target.write_text("hello")
    assert target.read_text() == "hello"
    assert artifacts_dir.name == "tests"
    assert artifacts_dir.parent.name == "artifacts"
```

**Step 2: Run it, expect failure**

Run: `AGENT8088_CONFIG=/nonexistent uv run --extra dev python -m pytest tests/test_artifacts_fixture.py -q`
Expected: FAIL — `fixture 'artifacts_dir' not found`

**Step 3: Add the fixtures**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def artifacts_dir():
    """Scratch directory for files a test needs to create.

    Tests that write into the repo root leave droppings that show up in every
    later `git status` and occasionally get committed by accident. Everything
    generated goes here instead; artifacts/ is gitignored.
    """
    path = ROOT / "artifacts" / "tests"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="session", autouse=True)
def _repo_root_stays_clean():
    """Fail the run if a test created a file in the repo root.

    A guard rather than a convention: the rule is only worth having if
    breaking it is noisy, and "don't write to the root" is exactly the kind of
    thing that silently regresses.
    """
    before = set(os.listdir(ROOT))
    yield
    new = set(os.listdir(ROOT)) - before
    # Tooling caches are not test droppings.
    new -= {".pytest_cache", "__pycache__", ".ruff_cache", "artifacts", ".coverage"}
    assert not new, f"tests created files in the repo root: {sorted(new)}"
```

**Step 4: Run to verify pass**

Run: `AGENT8088_CONFIG=/nonexistent uv run --extra dev python -m pytest tests/test_artifacts_fixture.py -q`
Expected: `1 passed`

**Step 5: Run the whole suite — the guard must not fire on existing tests**

Run: `AGENT8088_CONFIG=/nonexistent uv run --extra dev --extra gateway python -m pytest tests/ -q`
Expected: all pass. If the guard fires, an existing test is polluting the root — fix that test to use `tmp_path` or `artifacts_dir` before continuing.

**Step 6: Commit**

```bash
git add tests/conftest.py tests/test_artifacts_fixture.py
git commit -m "test: add artifacts_dir fixture and repo-root pollution guard"
```

---

## Phase 1 — Runtime context (the date)

### Task 3: `render_runtime_context()`

**Objective:** Produce the block that tells the model what "today" is.

**Files:**
- Modify: `src/agent8088/engine.py` (near `render_tool_docs`, ~`:1577`)
- Test: `tests/test_runtime_context.py`

**Step 1: Write the failing test**

```python
"""The runtime-context block: the model's only source of 'today'."""
from datetime import datetime, timezone


def test_runtime_context_states_the_date(engine):
    moment = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    block = engine.render_runtime_context(now=moment)

    assert "Monday, 10 August 2026" in block
    assert "2026" in block
    assert "August 2026" in block


def test_runtime_context_warns_against_answering_from_memory(engine):
    block = engine.render_runtime_context(now=datetime(2026, 8, 10, tzinfo=timezone.utc))
    assert "search" in block.lower()


def test_runtime_context_defaults_to_now(engine):
    block = engine.render_runtime_context()
    assert str(datetime.now().astimezone().year) in block
```

**Step 2: Run, expect failure**

Run: `AGENT8088_CONFIG=/nonexistent uv run --extra dev python -m pytest tests/test_runtime_context.py -q`
Expected: FAIL — `module 'agent8088.engine' has no attribute 'render_runtime_context'`

**Step 3: Implement**

```python
def render_runtime_context(now=None) -> str:
    """Tell the model what day it is.

    Without this it has no clock — only a training cutoff — so "the next
    election" silently means whatever was next while it was trained, and a
    2019 page looks as current as today's. Every date-aware behaviour below
    depends on this block being present.

    Rendered per turn rather than at import: a gateway or cron process runs
    for days and would otherwise keep answering with the date it booted on.
    """
    moment = now or datetime.now().astimezone()
    return (
        "\n\n## Runtime Context\n"
        f"- Today is {moment.strftime('%A, %d %B %Y')}.\n"
        f"- Current year: {moment.year}. Current month: {moment.strftime('%B %Y')}.\n"
        "- Your training data is older than today. For anything current, "
        "time-sensitive, or scheduled, search rather than answering from memory.\n"
    )
```

**Step 4: Run to verify pass**

Expected: `3 passed`

**Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_runtime_context.py
git commit -m "feat: render a runtime-context block with the current date"
```

---

### Task 4: Wire runtime context into all front ends

**Objective:** CLI, gateway, and the engine default all carry the date.

**Files:**
- Modify: `src/agent8088/cli.py:355-358` (`_session_system_prompt`)
- Modify: `src/agent8088/gateway/agent_bridge.py:10` (`build_system_prompt`)
- Modify: `src/agent8088/engine.py:1715` (module `SYSTEM_PROMPT`)
- Test: `tests/test_runtime_context.py` (append)

**Step 1: Write the failing tests**

```python
def test_cli_session_prompt_includes_the_date():
    from agent8088 import cli
    assert "Runtime Context" in cli._session_system_prompt()


def test_gateway_prompt_includes_the_date():
    from agent8088.gateway import agent_bridge
    assert "Runtime Context" in agent_bridge.build_system_prompt()
```

**Step 2: Run, expect failure** — both assert False.

**Step 3: Implement**

`src/agent8088/cli.py`, in `_session_system_prompt`:

```python
    prompt = (A.BASE_SYSTEM_PROMPT + "\n" + A.render_tool_docs(specs)
              + A.render_skill_docs(_active_skills()) + A.render_persona(A.USER_FILE)
              + A.render_runtime_context())
```

`src/agent8088/gateway/agent_bridge.py`, in `build_system_prompt`:

```python
    prompt = (A.BASE_SYSTEM_PROMPT + "\n" + A.render_tool_docs(A.TOOL_SPECS)
              + A.render_runtime_context())
```

`src/agent8088/engine.py:1715` — append `+ render_runtime_context()` to the `SYSTEM_PROMPT` assignment.

**Step 4: Run to verify pass**

**Step 5: Commit**

```bash
git add src/agent8088/cli.py src/agent8088/gateway/agent_bridge.py src/agent8088/engine.py tests/test_runtime_context.py
git commit -m "feat: give every front end the current date"
```

---

### Task 5: Stop the module-level prompt from freezing the date

**Objective:** A long-lived process must not answer Thursday's question with Monday's date.

**Context:** `create_completion` falls back to `system_prompt or SYSTEM_PROMPT` (`src/agent8088/engine.py:1187`). `SYSTEM_PROMPT` is computed once at import, so after Task 4 a gateway running for a week carries a week-old date on any call that doesn't pass an explicit prompt.

**Files:**
- Modify: `src/agent8088/engine.py` (~`:1187`)
- Test: `tests/test_runtime_context.py` (append)

**Step 1: Write the failing test**

```python
def test_default_system_prompt_is_rebuilt_per_call(engine, monkeypatch):
    """A process that runs for days must not keep the date it booted with."""
    monkeypatch.setattr(engine, "SYSTEM_PROMPT", "STALE PROMPT no date here")
    assert "Runtime Context" in engine.current_system_prompt()
```

**Step 2: Run, expect failure** — no `current_system_prompt`.

**Step 3: Implement**

```python
def current_system_prompt() -> str:
    """The default system prompt, with today's date rather than import day's.

    SYSTEM_PROMPT is built once at import. That is fine for a CLI invocation
    and wrong for the gateway and cron, which stay up long enough for the date
    to move underneath them.
    """
    base = SYSTEM_PROMPT.split("\n\n## Runtime Context\n")[0]
    return base + render_runtime_context()
```

Then in `create_completion`, replace `system_prompt or SYSTEM_PROMPT` with `system_prompt or current_system_prompt()`.

**Step 4: Run to verify pass**

**Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_runtime_context.py
git commit -m "fix: rebuild the default system prompt per call so the date stays current"
```

---

## Phase 2 — Date-aware search queries

### Task 6: `_augment_relative_time_query()`

**Objective:** Pin relative-time queries to the calendar, without touching queries that already name a year.

**Files:**
- Modify: `src/agent8088/engine.py` (near `_web_search_query_guard`, ~`:3924`)
- Test: `tests/test_search_dates.py`

**Step 1: Write the failing test**

```python
"""Date handling in outbound search queries and returned results."""
from datetime import datetime, timezone

import pytest

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)


@pytest.mark.parametrize("query,expected", [
    ("latest python release", "latest python release 2026"),
    ("current UK prime minister", "current UK prime minister 2026"),
    ("who won the most recent world cup", "who won the most recent world cup 2026"),
    ("next SpaceX launch", "next SpaceX launch 2026"),
])
def test_relative_queries_get_the_year(engine, query, expected):
    assert engine._augment_relative_time_query(query, now=NOW) == expected


@pytest.mark.parametrize("query", [
    ("today's football fixtures"),
    ("what happened this week in tech"),
])
def test_finer_grained_markers_get_the_month(engine, query):
    assert engine._augment_relative_time_query(query, now=NOW).endswith("August 2026")


@pytest.mark.parametrize("query", [
    "iPhone 2019 reviews",                 # already dated
    "who wrote Pride and Prejudice",       # not time-sensitive
    "python list comprehension syntax",    # stable knowledge
    "world cup 1998 final score",          # historical, explicit year
])
def test_untouched_queries(engine, query):
    assert engine._augment_relative_time_query(query, now=NOW) == query


def test_augmentation_can_be_switched_off(engine, monkeypatch):
    monkeypatch.setitem(engine.APP_CONFIG, "search_date_augmentation", "0")
    assert engine._augment_relative_time_query("latest python release", now=NOW) == \
        "latest python release"
```

**Step 2: Run, expect failure**

**Step 3: Implement**

```python
# Markers that make a query mean "as of now" — the ones where an undated search
# happily returns a 2019 blog post ranked above this month's news.
_RELATIVE_TIME_MARKERS = re.compile(
    r"\b(?:today|tonight|latest|newest|current|currently|now|recent|recently|"
    r"upcoming|next|this\s+(?:week|month|year|season)|as\s+of\s+now|"
    r"right\s+now|so\s+far)\b", re.IGNORECASE)

# "today" or "this week" needs the month to be useful; "latest" only needs the year.
_MONTH_GRANULARITY = re.compile(
    r"\b(?:today|tonight|this\s+week|this\s+month|right\s+now|as\s+of\s+now)\b",
    re.IGNORECASE)

_EXPLICIT_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def _augment_relative_time_query(query: str, now=None) -> str:
    """Add the current year (or month) to a query that means "as of now".

    Search engines rank an undated "latest X" query on popularity, not
    recency, so a well-linked 2019 page routinely beats this month's. Adding
    the year is the cheapest change that measurably shifts what comes back.

    Fires only when the query is relative AND names no year of its own, so an
    explicit "iPhone 2019 reviews" or "world cup 1998" is never rewritten.
    """
    if APP_CONFIG.get("search_date_augmentation", "1") != "1":
        return query
    if not _RELATIVE_TIME_MARKERS.search(query) or _EXPLICIT_YEAR.search(query):
        return query
    moment = now or datetime.now().astimezone()
    suffix = (moment.strftime("%B %Y") if _MONTH_GRANULARITY.search(query)
              else str(moment.year))
    return f"{query} {suffix}"
```

**Step 4: Run to verify pass**

**Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_search_dates.py
git commit -m "feat: date-qualify relative-time search queries"
```

---

### Task 7: Apply augmentation in the search path — before the safety guards

**Objective:** The augmented query is what actually goes out, and the guards inspect the final string.

**Files:**
- Modify: `src/agent8088/engine.py:2989-3025` (the `mode == "search"` branch of `run_tool`)
- Test: `tests/test_search_dates.py` (append)

**Ordering matters.** Augment first, then run `_web_search_query_guard` and `_outbound_secret_check` on the augmented text, then dispatch. Guarding the pre-augmentation string would leave the actual outbound query uninspected.

**Step 1: Write the failing test**

```python
def test_search_dispatches_the_augmented_query(engine, monkeypatch):
    sent = {}
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda q, *a, **k: sent.setdefault("query", q) or "results")
    monkeypatch.setattr(engine, "_local_searxng_no_prompt_enabled", lambda: True)

    engine.run_tool("web_search", {"query": "latest python release"})

    assert sent["query"].endswith(str(engine.datetime.now().astimezone().year))


def test_length_guard_sees_the_augmented_query(engine, monkeypatch):
    """A query just under the cap must not slip past it once the year is added."""
    monkeypatch.setattr(engine, "_local_searxng_no_prompt_enabled", lambda: True)
    long_query = "latest " + ("x" * (engine._WEB_SEARCH_MAX_QUERY_CHARS - 10))

    result = engine.run_tool("web_search", {"query": long_query})

    assert "limited to" in result
```

**Step 2: Run, expect failure**

**Step 3: Implement** — in the `mode == "search"` branch, immediately after `query` is read:

```python
        query = _augment_relative_time_query(query)
```

placed *before* the `_web_search_query_guard(query)` call at `:2993`.

**Step 4: Run to verify pass**, then the full search suite:

Run: `AGENT8088_CONFIG=/nonexistent uv run --extra dev python -m pytest tests/test_web_search_engine.py tests/test_search_dates.py tests/test_http_search.py -q`
Expected: all pass (37 + new + 16).

**Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_search_dates.py
git commit -m "feat: send the date-qualified query and guard the final string"
```

---

## Phase 3 — Stale-result rejection

### Task 8: Stamp results with the retrieval date

**Objective:** Give the model the one fact it needs to spot a stale result.

**Files:**
- Modify: `src/agent8088/engine.py` (new helper + the `return web_search.run_search(...)` at `:3024`)
- Test: `tests/test_search_dates.py` (append)

**Step 1: Write the failing test**

```python
def test_results_are_stamped_with_the_retrieval_date(engine):
    framed = engine._frame_search_results("1. Some result", now=NOW)

    assert "2026-08-10" in framed
    assert "Some result" in framed
    assert "date" in framed.lower()


def test_framing_is_skipped_for_errors(engine):
    """An error is not a result set — stamping it just buries the message."""
    assert engine._frame_search_results("Error: no provider", now=NOW) == \
        "Error: no provider"
```

**Step 2: Run, expect failure**

**Step 3: Implement**

```python
def _frame_search_results(results: str, now=None) -> str:
    """Stamp results with when they were fetched.

    Code cannot reliably date-check arbitrary snippets — parsing a date out of
    every provider's format and dropping what fails to parse would lose good
    answers. What it can do is give the model the comparison point it lacks,
    so "next launch" is checked against today rather than against training.
    """
    if results.startswith("Error:"):
        return results
    moment = now or datetime.now().astimezone()
    return (f"[Retrieved {moment:%Y-%m-%d}. Check each result's own date before "
            f"calling anything current, latest, or upcoming — search results "
            f"routinely include older pages.]\n\n{results}")
```

Then wrap the dispatch at `:3024`:

```python
        return _frame_search_results(web_search.run_search(
            query, _web_search_limit(), WEB_SEARCH_REGISTRY, _search_config(), ...))
```

**Step 4: Run to verify pass**

**Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_search_dates.py
git commit -m "feat: stamp search results with their retrieval date"
```

---

### Task 9: Prompt rule for validating dates

**Objective:** Turn the stamp into behaviour.

**Files:**
- Modify: `src/agent8088/system.md` (in `## Tool Usage`, after the `web_search` bullet at `:31-35`)

**Step 1: Add the rules**

```markdown
- Search results carry a retrieval date. Before you call anything "current",
  "latest", "next", or "upcoming", check the date on the result itself. If a
  scheduled event has already passed, say so and give the actual next one —
  never repeat a past event as though it were upcoming. If the results only
  support an older answer, say how old it is rather than presenting it as
  current.
- Include the year in a search query for anything time-sensitive, and the
  month too for "today" or "this week" questions. For a historical question,
  include the year or range you are asking about so results don't mix the
  period you want with the present day.
- Never repeat a search you already ran, and never re-run a reworded version of
  it. If the first search answered the question, answer from it. Search again
  only if the first attempt errored or genuinely returned nothing usable, and
  then change the query meaningfully rather than rephrasing it.
```

**Step 2: Verify the prompt still renders**

Run: `AGENT8088_CONFIG=/nonexistent uv run --extra dev python -c "from agent8088 import engine as A; print(len(A.BASE_SYSTEM_PROMPT))"`
Expected: a length larger than before; no exception.

**Step 3: Commit**

```bash
git add src/agent8088/system.md
git commit -m "docs: add date-validation and no-repeat-search rules to the prompt"
```

---

## Phase 4 — Equivalent-query deduplication

### Task 10: `_search_signature()`

**Objective:** Recognise that "latest Python release" and "Latest Python release?" are the same search.

**Files:**
- Modify: `src/agent8088/engine.py` (near `_augment_relative_time_query`)
- Test: `tests/test_search_dedupe.py`

**Step 1: Write the failing test**

```python
"""Equivalent-query detection: the exact-match guard was never enough."""


def test_case_and_punctuation_do_not_make_a_new_query(engine):
    assert engine._search_signature("Latest Python release?") == \
        engine._search_signature("latest python release")


def test_word_order_does_not_make_a_new_query(engine):
    assert engine._search_signature("python latest release") == \
        engine._search_signature("latest python release")


def test_filler_words_do_not_make_a_new_query(engine):
    assert engine._search_signature("what is the latest python release") == \
        engine._search_signature("latest python release")


def test_genuinely_different_queries_differ(engine):
    assert engine._search_signature("latest python release") != \
        engine._search_signature("latest ruby release")
```

**Step 2: Run, expect failure**

**Step 3: Implement**

```python
# Words that carry no search intent; dropping them stops a reworded repeat
# from reading as a fresh query.
_SEARCH_FILLER = frozenset({
    "the", "a", "an", "of", "in", "on", "at", "for", "to", "is", "are", "was",
    "were", "what", "whats", "who", "whos", "when", "where", "which", "how",
    "do", "does", "did", "tell", "me", "about", "please", "current", "currently",
})


def _search_signature(query: str) -> tuple:
    """Reduce a query to its meaning-bearing tokens, order-independent.

    The loop's existing guard compares `json.dumps(args)`, so a single changed
    character reads as a brand-new call and the model can burn its whole turn
    budget rephrasing one question. Sorting the tokens catches word-order
    variants too.
    """
    words = re.findall(r"[a-z0-9]+", query.lower())
    return tuple(sorted(w for w in words if w not in _SEARCH_FILLER))
```

**Step 4: Run to verify pass**

**Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_search_dedupe.py
git commit -m "feat: add order-independent search-query signatures"
```

---

### Task 11: Block repeated searches in the agent loop

**Objective:** A second equivalent search returns the first one's results instead of re-running — unless the first failed.

**Files:**
- Modify: `src/agent8088/engine.py:4277-4282` (loop state) and `:4406-4430` (dispatch)
- Test: `tests/test_search_dedupe.py` (append)

**Step 1: Write the failing tests**

```python
def test_reworded_search_is_not_re_run(engine, monkeypatch, scripted):
    runs = []
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda q, *a, **k: runs.append(q) or "Python 3.14 is out")
    monkeypatch.setattr(engine, "_local_searxng_no_prompt_enabled", lambda: True)
    model = scripted([
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest python release"}',
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "Latest Python release?"}',
        "Python 3.14.",
    ])
    monkeypatch.setattr(engine, "_create_completion_with_fallback", model)

    engine.run_agent([{"role": "user", "content": "latest python?"}], max_turns=5)

    assert len(runs) == 1, f"the same search ran twice: {runs}"


def test_a_failed_search_may_be_retried(engine, monkeypatch, scripted):
    """Dedupe must not trap the agent when the first attempt errored."""
    runs = []

    def _search(q, *a, **k):
        runs.append(q)
        return "Error: provider unavailable" if len(runs) == 1 else "Python 3.14 is out"

    monkeypatch.setattr(engine.web_search, "run_search", _search)
    monkeypatch.setattr(engine, "_local_searxng_no_prompt_enabled", lambda: True)
    model = scripted([
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest python release"}',
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest python release"}',
        "Python 3.14.",
    ])
    monkeypatch.setattr(engine, "_create_completion_with_fallback", model)

    engine.run_agent([{"role": "user", "content": "latest python?"}], max_turns=5)

    assert len(runs) == 2, "a failed search must be retryable"
```

**Step 2: Run, expect failure** — the first test sees 2 runs.

**Step 3: Implement**

At `:4282`, alongside `searched`:

```python
    search_results = {}   # query signature -> result of the search that ran
```

In the dispatch loop, before the existing `sig in seen` check:

```python
            if name == "web_search":
                query_sig = _search_signature(str(args.get("query") or ""))
                previous = search_results.get(query_sig)
                # A failed search is worth retrying; a successful one is not.
                if previous and not previous.startswith("Error:"):
                    result = (f"This search already ran. Answer from these "
                              f"results:\n\n{previous}")
                    tool_outputs.append(result)
                    if on_result:
                        on_result(name, result)
                    messages.append({"role": "user",
                                     "content": f"Tool result ({name}):\n{result}"})
                    continue
```

and after `result` is produced, record it:

```python
            if name == "web_search":
                search_results[_search_signature(str(args.get("query") or ""))] = result
```

**Step 4: Run to verify pass** — both tests.

**Step 5: Run the full suite** to confirm the loop change broke nothing.

Run: `AGENT8088_CONFIG=/nonexistent uv run --extra dev --extra gateway python -m pytest tests/ -q`

**Step 6: Commit**

```bash
git add src/agent8088/engine.py tests/test_search_dedupe.py
git commit -m "feat: reuse results instead of re-running an equivalent search"
```

---

## Phase 5 — Follow-up gating

### Task 12: `_user_requested_tool()` — the escape hatch

**Objective:** Never block something the user explicitly asked for.

**Files:**
- Modify: `src/agent8088/engine.py` (beside `_user_supplied_url` at `:4246`)
- Test: `tests/test_tool_intelligence.py`

**Step 1: Write the failing test**

```python
"""Tool selection: choosing the smallest tool, and not piling on after a search."""


def _user(text):
    return [{"role": "user", "content": text}]


def test_named_tool_counts_as_a_request(engine):
    assert engine._user_requested_tool(_user("run execute_shell for me"), "execute_shell")


def test_plain_language_request_counts(engine):
    assert engine._user_requested_tool(_user("run `ls -la` in the repo"), "execute_shell")


def test_unrelated_message_is_not_a_request(engine):
    assert not engine._user_requested_tool(_user("who is the UK PM?"), "execute_shell")
```

**Step 2: Run, expect failure**

**Step 3: Implement**

```python
# Phrases that mean the user asked for this class of tool themselves. Kept
# deliberately literal: the gates below only fire when the model reached for a
# tool on its own, and a false "user asked" is safer than blocking a request.
_EXPLICIT_TOOL_PHRASES = {
    "execute_shell": ("run ", "execute ", "shell", "command", "terminal", "`"),
    "browse_page": ("browse", "open the page", "visit", "inspect the page"),
}


def _user_requested_tool(messages, name: str) -> bool:
    """Whether the user asked for this tool, by name or in plain language."""
    phrases = (name, *_EXPLICIT_TOOL_PHRASES.get(name, ()))
    for message in messages:
        if message.get("role") != "user":
            continue
        text = str(message.get("content", "")).lower()
        if any(phrase in text for phrase in phrases):
            return True
    return False
```

**Step 4: Run to verify pass**

**Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_tool_intelligence.py
git commit -m "feat: detect explicitly user-requested tools"
```

---

### Task 13: Gate network-fetch shell commands after a search

**Objective:** Stop `curl`-after-search, without touching legitimate post-search shell work.

**Files:**
- Modify: `src/agent8088/engine.py:4408` (extend the existing gate)
- Test: `tests/test_tool_intelligence.py` (append)

**Step 1: Write the failing tests**

```python
def test_curl_after_a_search_is_refused(engine, monkeypatch, scripted):
    monkeypatch.setattr(engine.web_search, "run_search", lambda *a, **k: "Python 3.14")
    monkeypatch.setattr(engine, "_local_searxng_no_prompt_enabled", lambda: True)
    ran = []
    monkeypatch.setattr(engine, "_exec_shell", lambda *a, **k: ran.append(a) or "out")
    model = scripted([
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest python release"}',
        '✿FUNCTION✿: execute_shell ✿ARGS✿: {"command": "curl https://python.org"}',
        "Python 3.14.",
    ])
    monkeypatch.setattr(engine, "_create_completion_with_fallback", model)

    engine.run_agent([{"role": "user", "content": "latest python?"}], max_turns=5)

    assert ran == [], "a web fetch ran as a follow-up to a successful search"


def test_ordinary_shell_after_a_search_still_runs(engine, monkeypatch, scripted):
    """Only web fetches are gated — the agent must still be able to work."""
    monkeypatch.setattr(engine.web_search, "run_search", lambda *a, **k: "Python 3.14")
    monkeypatch.setattr(engine, "_local_searxng_no_prompt_enabled", lambda: True)
    ran = []
    monkeypatch.setattr(engine, "exec_tool",
                        lambda n, a, **k: ran.append(n) or "ok")
    # ... drive web_search then `ls -la`; assert execute_shell reached exec_tool
```

**Step 2: Run, expect failure**

**Step 3: Implement** — extend the condition at `:4408`:

```python
# Web fetching dressed up as a shell command. Narrow on purpose: after a
# search the agent may still legitimately need to install a package or read a
# file, and blocking that would be a worse bug than the one being fixed.
_WEB_FETCH_SHELL = re.compile(r"\b(?:curl|wget|httpie|http|lynx|w3m)\b", re.IGNORECASE)
```

```python
            fetch_followup = (
                name in {"browse_page", "get_page_title"}
                and not _user_supplied_url(messages, args.get("url"))
            ) or (
                name == "execute_shell"
                and _WEB_FETCH_SHELL.search(str(args.get("command") or ""))
                and not _user_requested_tool(messages, "execute_shell")
            )
            if searched and fetch_followup:
                result = ("Follow-up fetch was not run. Use the web_search results, "
                          "or ask the user for a specific page URL.")
```

**Step 4: Run to verify pass**

**Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_tool_intelligence.py
git commit -m "feat: gate web-fetch shell commands after a successful search"
```

---

### Task 14: Gate fetch-shaped MCP tools after a search

**Objective:** Same rule for MCP tools whose job is fetching.

**Files:**
- Modify: `src/agent8088/engine.py` (same gate)
- Test: `tests/test_tool_intelligence.py` (append)

Add to the `fetch_followup` expression:

```python
            ) or (
                (TOOL_SPECS.get(name, {}).get("mode") == "mcp")
                and _MCP_FETCH_NAME.search(name)
                and not _user_requested_tool(messages, name)
            )
```

with:

```python
# MCP tools whose name says they fetch. Name-based because MCP specs carry no
# capability metadata to key off.
_MCP_FETCH_NAME = re.compile(r"(?:search|fetch|browse|web|http|scrape)", re.IGNORECASE)
```

Tests: an MCP tool named `brave_web_search` is refused after a search; one named `github_create_issue` is not; a user asking for it by name bypasses the gate.

**Commit:**

```bash
git commit -m "feat: gate fetch-shaped MCP tools after a successful search"
```

---

## Phase 6 — Prompt policy for tool selection

### Task 15: Smallest-tool and MCP guidance

**Objective:** Cover the judgement calls code cannot make.

**Files:**
- Modify: `src/agent8088/system.md` (`## Tool Usage`)

Add:

```markdown
- Pick the smallest tool that answers the request. read_text beats
  execute_shell for reading a file; calculate beats run_sandboxed for
  arithmetic; one web_search beats a search plus a page fetch. If two tools
  would both work, use the one with the narrower blast radius.
- Never call a tool to confirm something the user already told you, to
  summarize or translate text you already have, to reason about code you can
  read, or to produce writing. Those need no tool.
- MCP tools are for the specific system they wrap. Use one only when the
  request is about that system and a built-in tool cannot do it — not as a
  second opinion on a web_search result, and not to explore what the server
  offers. If the user names an MCP tool or its server, use that one.
- When the user gives you a URL, asks you to inspect a page, run a specific
  command, or use a named tool, do that — the preferences above describe what
  to reach for unprompted, not permission to substitute your own plan for a
  direct instruction.
```

**Step 1: Verify the prompt renders**, as in Task 9.

**Step 2: Commit**

```bash
git add src/agent8088/system.md
git commit -m "docs: add smallest-tool, no-tool, and MCP selection rules"
```

---

## Phase 7 — Permission testing

### Task 16: Permission matrix for the search path

**Objective:** Prove routine search doesn't prompt, and that the hard floors still hold.

**Files:**
- Test: `tests/test_permission_search.py` (new)

Cases:

| Scenario | Expected |
|---|---|
| `web_search_no_prompt=1` + loopback SearXNG, readonly | runs, no escalation |
| `web_search_no_prompt=1` + public provider | escalates (opt-in must not silently cover third parties) |
| `web_search_no_prompt=0`, readonly | escalates |
| `full-auto` | runs, no escalation |
| Query containing an API key, any mode incl. `full-auto` | hard-blocked, not escalatable |
| Query over the char cap, after augmentation | hard-blocked |
| `plan-only` | blocked with the execute_plan message |
| Augmented query | never surfaced in `on_calls` output |

```bash
git commit -m "test: cover the permission matrix for the search path"
```

---

### Task 17: Permission matrix for write/shell/MCP

**Objective:** Extensive permission coverage, writing only into `artifacts/`.

**Files:**
- Test: `tests/test_permission_matrix.py` (new), using the `artifacts_dir` fixture

Cases: `write_text` in each zone (`blocked`/`no_prompt`/`prompt`/`default`) × each mode (`readonly`/`edit`/`full-auto`/`plan-only`); shell readonly-safe vs mutating; `mcp_read_only` vs write MCP tools; sensitive-path floor unlocked by nothing, `full-auto` included; escalation grant is one-shot.

Every file written goes to `artifacts_dir`; the Task 2 guard proves the root stays clean.

```bash
git commit -m "test: extend the permission matrix across modes and path zones"
```

---

## Phase 8 — Real-model evaluation

### Task 18: Scenario table

**Objective:** A data file of prompts and expected tool behaviour, usable by both the scripted and live harnesses.

**Files:**
- Create: `tests/data/tool_intelligence_cases.py`

```python
"""Prompt -> expected tool behaviour. Shared by the scripted and live harnesses."""

CASES = [
    # (prompt, expectation)
    ("hello there",                                    "no_tool"),
    ("summarize this: the cat sat on the mat",         "no_tool"),
    ("translate 'good morning' to French",             "no_tool"),
    ("what is a python list comprehension",            "no_tool"),
    ("rewrite this sentence to be shorter: ...",       "no_tool"),
    ("who is the current UK prime minister",           "web_search"),
    ("latest python release",                          "web_search+year"),
    ("what are today's top tech headlines",            "web_search+month"),
    ("when is the next SpaceX launch",                 "web_search+year"),
    ("who won the 1998 world cup final",               "web_search"),
    ("what is 17 * 23 + 4",                            "calculate"),
    ("read the file artifacts/tests/sample.txt",       "read_text"),
    ("run `ls -la` here",                              "execute_shell"),
    ("open https://example.com and tell me the title", "browse_page"),
]
```

```bash
git commit -m "test: add the tool-intelligence scenario table"
```

---

### Task 19: Scripted-model conformance test

**Objective:** Assert the *enforcement* layer on every case, deterministically, in the default suite.

**Files:**
- Test: `tests/test_tool_intelligence.py` (append)

For each `web_search+year` case, drive `run_tool` and assert the outbound query gained the year; for `+month`, the month. These are deterministic and belong in the normal suite. Model *choice* is not asserted here — a scripted model has no judgement to test.

```bash
git commit -m "test: assert query augmentation across the scenario table"
```

---

### Task 20: Live-model harness

**Objective:** Measure the thing only a real model can be measured on — whether it picks the right tool.

**Files:**
- Create: `scripts/verify_tool_intelligence.py`

Behaviour:
- Refuses to run without `A8088_LIVE_MODEL=1`; **requires** `AGENT8088_HOME` pointing at a temp dir and sets `AGENT8088_CONFIG` itself, so it can never read or write the developer's real `~/.agent8088`.
- For each case, runs one turn with `on_calls` recording the tool names and arguments.
- Reports a table: case, expected, observed, pass/fail — plus a pass rate.
- Exits non-zero below a threshold (start at 80%, raise as the prompt improves).
- Writes its report to `artifacts/tool-intelligence-<timestamp>.md`.

Run:

```bash
A8088_LIVE_MODEL=1 AGENT8088_HOME="$(mktemp -d)" uv run --extra dev python scripts/verify_tool_intelligence.py
```

Keep this out of the default pytest run — it costs tokens and is nondeterministic. Note it in `TESTING.md` as a pre-release check.

```bash
git commit -m "test: add opt-in live-model tool-intelligence harness"
```

---

## Phase 9 — Documentation and final verification

### Task 21: Docs

**Files:**
- Modify: `docs/wiki/04-tools.md` — tool-selection rules, date augmentation, dedupe
- Modify: `docs/wiki/02-configuration.md` — `search_date_augmentation`
- Modify: `docs/wiki/12-testing-and-verification.md` — the live harness and `artifacts/`
- Modify: `CHANGELOG.md`

```bash
git commit -m "docs: document tool-selection behaviour and the new config key"
```

### Task 22: Full verification

```bash
AGENT8088_CONFIG=/nonexistent AGENT8088_HOME="$(mktemp -d)" \
  uv run --extra dev --extra gateway python -m pytest tests/ -q
```
Expected: all pass, no failures, root-pollution guard silent.

```bash
VERIFY_HOME="$(mktemp -d)"; AGENT8088_CONFIG=/nonexistent AGENT8088_HOME="$VERIFY_HOME" \
  uv run --extra dev --extra gateway python scripts/verify_features.py
```
Expected: 88 passed / 1 failed / 1 skipped — **identical to the baseline**. The one failure (`approved git_status returns real output`) is pre-existing on `development`; anything beyond it is a regression.

```bash
uv run python scripts/check_duplicate_defs.py
uv run --extra dev python -m ruff check src/agent8088/engine.py tests/
git status --short   # must be clean; nothing in the repo root
```

---

## Files likely to change

| File | Change |
|---|---|
| `src/agent8088/engine.py` | `render_runtime_context`, `current_system_prompt`, `_augment_relative_time_query`, `_frame_search_results`, `_search_signature`, `_user_requested_tool`, loop dedupe + follow-up gate |
| `src/agent8088/system.md` | date rules, no-repeat rule, smallest-tool rule, MCP guidance |
| `src/agent8088/cli.py` | runtime context in `_session_system_prompt` |
| `src/agent8088/gateway/agent_bridge.py` | runtime context in `build_system_prompt` |
| `tests/conftest.py` | `artifacts_dir` fixture, root-pollution guard |
| `tests/test_runtime_context.py` … `tests/test_permission_matrix.py` | new suites (6 files) |
| `tests/data/tool_intelligence_cases.py` | scenario table |
| `scripts/verify_tool_intelligence.py` | live harness |
| `.gitignore`, `artifacts/.gitkeep` | artifacts directory |
| `docs/wiki/*`, `CHANGELOG.md` | documentation |

---

## Risks and tradeoffs

**Query rewriting can make results worse.** Appending a year helps "latest X" and hurts nothing that already names a year, but there will be queries where it narrows results unhelpfully. Mitigated by the marker+no-year precondition and the `search_date_augmentation` kill switch. Watch the live harness pass rate before and after.

**The shell/MCP gate can fire on legitimate work.** A false block is more annoying than a redundant `curl`. The gate is narrow (fetch verbs only, explicit-request escape hatch) and Task 13's second test exists specifically to prove ordinary shell work still runs. If false positives show up, narrow further rather than widening.

**Dedupe could trap the agent.** If a search "succeeds" but returns useless results, the agent is told to answer from them. The failure check keys on `Error:` only. Consider also treating an empty result set as retryable — worth deciding during Task 11.

**The date is now in every prompt.** Slightly more tokens per turn, and a wrong system clock becomes a visible wrong answer rather than a silent one. That is the right trade, but note it.

**Live tests are nondeterministic.** Hence the pass-rate threshold rather than per-case assertions, and hence they stay out of the default suite. Do not let them gate CI.

**No CI on this repo** (billing). "Green" means the three commands in Task 22 pass locally, plus a baseline comparison on the target branch before calling anything a regression.

---

## Open questions

1. **How aggressive should the MCP follow-up gate be?** Name-matching `search|fetch|browse|web|http` will catch `brave_web_search` and also anything unrelated that happens to contain "search". An allowlist per MCP server would be precise but needs config. **Recommendation: ship the name heuristic, watch for false positives.**
2. **Should `search_date_augmentation` default on?** Planned as on. If the live harness shows it hurting, flip the default and keep the code.
3. **Empty-but-not-error search results** — retryable or not? See the dedupe risk above.
4. **Timezone source** — system local is assumed. If the gateway serves users in other zones, the date in a shared prompt will be the *host's*. Out of scope here; worth a follow-up if gateway users notice.
5. **Live-harness threshold** — 80% is a guess. Run it once against the current `development` prompt to establish the real baseline before setting the gate.
