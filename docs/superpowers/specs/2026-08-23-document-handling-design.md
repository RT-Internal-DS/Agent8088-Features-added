# Document Read/Write Capability for Agent8088

## Context

Agent8088 cannot read or write Office/PDF documents at all today. `read_text`
(engine.py:5009) calls `_read_text_limited`, which hard-codes
`data.decode("utf-8")` (engine.py:2303-2308) — handing it a `.docx` raises an
**uncaught `UnicodeDecodeError`**, surfacing to the user as a traceback rather
than a message. `write_file` (engine.py:5025) calls
`target.write_text(..., encoding="utf-8")`, so it cannot emit a valid binary
OOXML/PDF file even if asked.

`terminal-agent-document-handling-research.md` surveyed Hermes (LibreOffice +
code generation), OpenClaw (PDFium WASM, read-only, no creation tools), and
OfficeCLI (a .NET single binary). Its recommended "Option D Hybrid" stacks six
dependencies including a ~500MB system package and multi-GB OCR models. That
recommendation is rejected here: it ignores that this repo has a sandbox, a
layered permission model where every layer can only refuse, and a hand-maintained
cross-platform installer with no CI to catch breakage.

**Intended outcome:** the agent can read `.docx/.xlsx/.pptx/.pdf` through the
tool it already has, and can produce them two ways — by generating code (flexible)
and through one deterministic tool (reliable on a weak model).

**Settled decisions:** reading and writing matter equally; write path is code
generation *plus* a deterministic fallback; pure-Python dependencies preferred;
desktop-only (Termux is already unsupported per `pyproject.toml`), so LibreOffice
is not a dependency.

---

## The two constraints that shape everything

1. **No optional tool arguments exist.** `build_tools_def` (engine.py:1883) emits
   `"required": list(spec["args"])`, and `TOOL_REQUIRED_PARAMS` (engine.py:1900)
   mirrors it. Adding `offset`/`limit` to `read_text` would make them mandatory
   on every single file read — and worse, `execute_plan` **rejects** any step
   whose declared args are missing (engine.py:2896), so every existing saved plan
   that calls `read_text` with just a filename would break. This is why Phase 0
   exists and must land first.
2. **Every tool result is cut to 3,000 characters** by `_tool_result_for_model`
   (engine.py:6534-6542) before reaching the model. A 40-page document extracts
   to ~80,000 characters. **Pagination is mandatory, not a nicety** — without it
   the agent reads page 1 of everything and hallucinates the rest.

Constraint 1 must be solved before constraint 2 can be.

---

## Phase 0 — Optional tool arguments (~10 lines, unblocks everything)

Add an `optional=` field to the `tools.txt` row format, parsed by the existing
`parse_kv_segments`/`_build_spec` path.

- `_build_spec` (engine.py:1802-1837): add `"optional": parse_csv(g("optional", "tool_optional"))`.
- `build_tools_def` (engine.py:1882-1883): `required` becomes
  `[a for a in spec["args"] if a not in spec.get("optional", [])]`.
- `TOOL_REQUIRED_PARAMS` (engine.py:1900, and the identical rebuilds at 1912 and
  2132 — all three must change together) uses the same subtraction. This is what
  keeps `execute_plan`'s missing-arg rejection (engine.py:2896) and
  `_infer_step_args` (engine.py:2329-2335) correct once `read_text` grows args.

**Rejected alternative:** encoding pagination into the filename string
(`report.docx#page=2`) to avoid touching the schema. It is a smaller diff but a
**security regression** — `path_arg` is consumed by `_is_sensitive_path()` and
`_check_path_zone()` *before* the read branch runs, so a crafted suffix changes
the string those guards match on. Never make the security layer's input
model-controlled in a new way to save a schema field.

**Also fixes an existing bug for free:** `schedule_task` (tools.txt:19) declares
`args=action,schedule,task`, forcing the model to invent `schedule` and `task`
values when calling `action=list`. Mark both optional in the same change.

Ship and verify this alone first. It touches the tool schema every model call
depends on.

## Phase 1 — Reading

**New file `src/agent8088/documents.py`** — one public function,
`extract_text(path) -> str | None`, returning `None` when the extension isn't a
document so the caller falls through to normal text reading.

| Format | Method | Dependency |
|---|---|---|
| `.docx` | stdlib `zipfile` + `xml.etree` → `word/document.xml`, `w:p`/`w:t`; tables as pipe rows | none |
| `.pptx` | stdlib → `ppt/slides/slideN.xml` `a:t`, plus `ppt/notesSlides/` speaker notes | none |
| `.xlsx` | **openpyxl** (`read_only=True, data_only=True`) | `openpyxl` |
| `.pdf` | **pypdf** page-by-page text | `pypdf` |

`.docx`/`.pptx` are plain ZIP+XML with no indirection — stdlib is genuinely
sufficient and avoids `lxml`. `.xlsx` is not: cell text lives behind
`sharedStrings.xml` indices, dates are float serials needing number-format
interpretation, and formulas carry both an expression and a cached value.
Hand-rolling that produces confidently wrong numbers, which is worse than an
error. openpyxl is pure Python (only `et_xmlfile`), so it costs nothing on the
platform floor.

**Integration — extend `read_text`, do not add a tool.** At engine.py:5009:

```python
if mode == "read_text":
    text = documents.extract_text(read_target)
    if text is None:
        text = _read_text_limited(read_target)
    return _strip_special_tokens(_paginate(text, args))
```

This inherits `_is_sensitive_path()` (engine.py:4659-4662), `_check_path_zone()`,
and `check_permission()` unchanged — **no new permission mode, no new security
code, no new way to bypass the gate.** A new `read_document` tool would duplicate
all of it. It also inherits, for free, the auditor sub-agent's larger
12,000-char result allowance, which `_tool_result_for_model` (engine.py:6536)
keys on the literal tool name `read_text` — a new tool name would silently lose it.

`extract_text` must NOT be routed through `_read_text_limited`: that function
caps raw bytes before decoding, and a `.docx` must be read whole as a ZIP before
any text exists to truncate. It does its own size guarding (below).

**Pagination** (depends on Phase 0): `read_text` gains optional `offset` and
`limit`. Document reads return a header the model can act on —
`[report.docx — 12 pages, 340 lines. Showing lines 1-80 of 340.]` — so it knows
more exists and how to ask for it.

**Size guard (required, not optional):** `extract_text` opens the path itself and
therefore bypasses `MAX_READ_BYTES` (engine.py:326, 2MB). Add a separate
`max_document_bytes` config (default 25MB) checked in `documents.py` before
parsing. Without it, a crafted 2GB zip is an unbounded-memory hole reachable in
readonly mode.

**Root-cause fix, separable from this feature:** `_read_text_limited`
(engine.py:2303-2308) should catch `UnicodeDecodeError` and raise
`ValueError("Not a text file (binary): ...")`. `ValueError` is already handled by
its callers — including the write path's diff read at engine.py:5019, which today
crashes the *write* when the existing file is binary. Fix it once in the shared
function; both callers benefit.

## Phase 2 — Writing by code generation (skill)

**New `src/agent8088/skills_installed/documents/SKILL.md`** — guidance only, no
`tools.txt`, following the `plan/SKILL.md` precedent. Teaches recipes for
`python-docx`, `openpyxl`, `python-pptx`, and `reportlab` executed through the
existing `execute_shell` tool.

Zero new tools, zero new permission modes. This is OpenClaw's approach and it is
the correct default; the flexibility of arbitrary code is the whole point.

Must include the failure modes the research doc documents: openpyxl writes
formulas with no cached value (so a reader sees the formula string, not a
number), and the agent should verify by re-reading the file it just wrote with
Phase 1's reader.

## Phase 3 — Deterministic fallback (one tool)

Phase 2 leans on multi-step code generation, which the benchmark found is this
model's weakest area. Phase 3 is the floor when it flails. Building it now rather
than on demand, since the weakness is measured, not predicted.

**Exactly one new tool**, not a family:
`create_document|filename,content`, dispatched on the output extension.
`content` is a simple line-oriented spec (`# heading` lines for `.docx`,
`Sheet!A1,value` rows for `.xlsx`, `---`-separated blocks for `.pptx`), not
nested JSON — a weak model produces flat line formats far more reliably than
balanced JSON. Resist a per-format tool set: choosing among four similar tools is
the same multi-step reasoning problem this phase exists to remove.

**Scope: creates new documents only.** In-place editing of an existing document
is explicitly out — that is the iterative, multi-step task Phase 2's skill exists
for, and the exact thing this fallback cannot do reliably in one call.

**Scope: `.docx`/`.xlsx`/`.pptx` only.** PDF *writing* stays in Phase 2's skill
via `reportlab`. Generating a PDF from scratch is a layout problem (fonts, flow,
pagination), not a "fill in these fields" problem, and does not compress into one
declarative tool call.

Implementation notes:
- New mode `write_document`, gated **identically** to `write_text`: add to the
  `gated_modes` tuple (engine.py:4902), to the plan-only block list
  (engine.py:4643), and to `check_permission()` (engine.py:1207-1256) with a test
  for each of the three permission modes, per CLAUDE.md.
- Reuse `resolve_write_path()` (engine.py:2253) and `_shadowed_project_file()`
  (engine.py:2288) exactly as the `write_text` branch does (engine.py:4801-4811),
  so bare filenames land in `artifacts/` and the sensitive-file floor still applies.
- Do **not** reuse the pre-write diff read (engine.py:5019) or `_make_diff` —
  both assume text. Set `_last_write_diff` to a summary line
  (`Created report.docx — 3 headings, 1 table`) instead.

## Dependencies

All five go in `[project.dependencies]` (core), not an extra — decided
deliberately, following `pyproject.toml`'s own stated rule that a feature which
should always exist must not have its availability determined by how someone
installed.

Added: `openpyxl`, `pypdf` (read path); `python-docx`, `python-pptx`,
`reportlab` (write path). All install from wheels on Linux/macOS/Windows.

`openpyxl` and `pypdf` are pure Python. `python-docx`/`python-pptx` pull `lxml`
(a C extension, but with prebuilt wheels for every desktop platform and Python
minor) — the one deviation from "pure Python only", accepted because Termux is
already unsupported. **Verify at implementation time** that wheels exist for the
project's minimum Python on win_amd64, macOS, manylinux and musllinux; a source
build here would be a fatal core-install failure (`install.sh:772-776`), not a
degraded optional feature.

Files that must change together for any dependency:
`pyproject.toml` → `requirements.txt` (hand-synced; `scripts/check_requirements_sync.py`
compares names only) → `uv.lock`. Installer scripts need no change; they resolve
from `pyproject.toml` via `uv pip install -e .`.

## Verification

No CI exists — these commands are the gate.

```sh
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/ -q
uv run ruff check --select=E9,F src tests scripts
uv run python scripts/check_duplicate_defs.py
uv run python scripts/check_requirements_sync.py
AGENT8088_CONFIG=/nonexistent AGENT8088_HOME="$(mktemp -d)" uv run python scripts/verify_everything.py
```

`scripts/verify_everything.py`'s `expected_tools` is the single source of truth
for tool inventory — it must gain `create_document` in Phase 3 or the run fails.
`docs/wiki/04-tools.md` updates in the same PR per CLAUDE.md.

New tests (following `tests/conftest.py`'s `engine` fixture, which reloads the
module fresh per test):
- Phase 0: a tool with `optional=` omits those args from `required`; `schedule_task`
  no longer requires `schedule`.
- Phase 1: round-trip each of the four formats from a fixture written into
  `tmp_path`; oversized document rejected by `max_document_bytes`; a sensitive-path
  `.docx` is still refused in **all three** permission modes (the floor must hold —
  CLAUDE.md requires this proof for always-on layers); `_read_text_limited` on
  binary raises `ValueError`, not `UnicodeDecodeError`.
- Phase 3: `create_document` denied in readonly and plan-only, allowed in
  full-auto; bare filename lands in `artifacts/`.

End-to-end check: build a fixture `.xlsx` with a formula and a date, read it back
through `read_text`, confirm the date renders as a date and pagination reports the
true total.

## What this plan does and does not change

**Changes:** document reading through the existing `read_text` tool, a writing
skill, one new `create_document` tool, and the `optional=` field in the tool
registry.

**Leaves untouched:** Agent8088's sub-agent system. `spawn_subagent`
(`tools.txt:9`), the `subagent` mode, and `src/agent8088/agents/*.md` are not
modified, extended, or referenced by any phase.

This record covers the product design only. It deliberately says nothing about
which tools were used to author the code.

---


Written and green on `feat/document-handling`, uncommitted, before this plan was
revised:

- `engine.py` — new `required_params(spec)` helper; `_build_spec` parses
  `optional=`; `build_tools_def` and all three `TOOL_REQUIRED_PARAMS` rebuild
  sites now call the one helper.
- `tools.txt` — `schedule_task` gains `optional=schedule,task`.
- `tests/test_optional_tool_args.py` — 5 tests, all passing.

**Outstanding:** the full suite has not been run clean yet. Two files
(`test_ascii_art_fencing.py`, `test_browse_page_missing_chromium.py`) import
`agent8088` at module scope and fail collection with `ModuleNotFoundError`
because the package isn't installed in this environment. That reproduces
independently of these changes and needs either `pip install -e .` or
`PYTHONPATH=src` to confirm — establish that baseline before trusting any
"tests pass" claim.

---

## Explicitly cut

- **OfficeCLI** — a third-party .NET binary invoked with model-controlled
  arguments is a new unaudited surface in an agent whose architecture is "every
  layer can only refuse"; it cannot read or write PDF, so a second stack is needed
  regardless; install is `curl | bash` from a small project's main branch.
- **LibreOffice / `soffice`** — bought only for Excel formula recalculation and
  render-to-image verification. ~500MB, and Hermes needs a compiled C socket shim
  to make it work under a sandbox. Revisit only if formula recalc is demanded.
- **OCR (`marker-pdf`, `pytesseract`)** — multi-GB models for scanned PDFs. A
  clear "this PDF has no extractable text; it is likely a scan" message covers the
  case honestly until someone asks for more.
- **Native-LLM PDF passthrough** (OpenClaw's raw-bytes mode) — only works on
  Anthropic and Google. This agent's target is a local OpenAI-compatible endpoint,
  where it is unavailable.
- **Legacy `.doc`/`.ppt`/`.xls`** — needs LibreOffice. Detect and say so.
- **In-place document editing as a deterministic tool** — Phase 3 creates only.
  Editing is Phase 2's job.
- **PDF creation as a deterministic tool** — layout is not declarative. Phase 2's
  skill covers it via `reportlab`.
- **A separate `read_document` tool** — would duplicate the whole security chain
  and lose the auditor's 12,000-char allowance keyed on the `read_text` name.
