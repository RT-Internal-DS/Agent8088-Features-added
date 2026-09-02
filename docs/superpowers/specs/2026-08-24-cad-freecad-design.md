# CAD File Support via FreeCAD

> Design record. Written 2026-08-24.

## Context

Agent8088 has no CAD capability. The document subsystem (`documents.py`,
`create_document`, `convert_document`, the `documents` skill, and
`Install-LibreOffice`) established a working pattern for "a file format the
agent needs a heavyweight external binary to handle." CAD is the same shape of
problem with a different binary, so this design deliberately mirrors that
architecture rather than inventing a second one.

**Intended outcome:** the agent can inspect, convert, and generate CAD files,
covering four capability tiers of increasing difficulty.

## The four tiers, and an honest reliability note

| Tier | Example | Reliability |
|---|---|---|
| 1. Inspect | "what are the dimensions of this STEP file?" | High — deterministic |
| 2. Convert | STEP→STL, FCStd→STEP | High — deterministic |
| 3. Simple parts | "50×30×10mm plate, four M4 holes" | Medium — parameters, not freeform code |
| 4. Full modeling | constrained sketches, assemblies | **Low on a weak model** |

Tier 4 was requested with the risk stated and accepted. The evidence for the
concern is from this repo's own history: earlier the same local model
(`ornith-1.0-35b`) could not follow a documented one-line `soffice` command and
instead wrote an unrelated script from scratch — twice, including after the
skill was rewritten to lead with that instruction. That is what motivated
`convert_document` existing as a deterministic tool at all.

FreeCAD's Python API is substantially less forgiving than a shell one-liner.
Tier 4 will work when driven by a strong model and will be unreliable on the
local one. **This design's response is to make tiers 1–3 model-independent**, so
the foundation holds regardless of what drives it, and to confine freeform code
generation to tier 4 where it is unavoidable.

## Architecture

New module `src/agent8088/cad.py`, the CAD analog of `documents.py`.

| Phase | Component | Mirrors |
|---|---|---|
| 0 | `Install-FreeCAD` in `install.ps1`; `cad._freecad_executable()` | `Install-LibreOffice`; `documents._soffice_executable()` |
| 1 | `cad.extract_info()` hooked into the `read_text` branch (`engine.py:5129-5139`) | `documents.extract_text()` |
| 2 | `convert_cad` tool, `mode=write_text` | `convert_document` |
| 3 | `create_cad_part` tool, `mode=write_text` | `create_document` |
| 4 | `src/agent8088/skills_installed/cad/SKILL.md` | the `documents` skill |

Each phase is independently shippable; stopping after any of them leaves
something useful.

### The one real divergence from `documents.py`

Every CAD operation must shell out to `freecadcmd`. `documents.py` parses
`.docx`/`.pptx` with stdlib `zipfile` and only needs a library for `.xlsx`/`.pdf`;
STEP and IGES require OpenCascade, so no dependency-free path exists.

`.fcstd` is itself a zip containing `Document.xml`, so a cheap stdlib
metadata fast-path is possible there. **Not built in this design** — noted so a
later reader knows it was considered and deliberately deferred, not missed.

### Phase 1 — inspection, via the existing read tool

`cad.extract_info(path)` returns a text summary, or `None` when the extension
isn't CAD so the caller falls through to normal reading — the exact contract
`documents.extract_text` already has. Hooked in immediately after it in the
`read_text` branch.

Handled extensions: `.fcstd`, `.step`/`.stp`, `.iges`/`.igs`, `.stl`, `.obj`,
`.brep`, `.dxf`.

Summary contents: object tree (name + type per object), bounding box,
volume, surface area, and units.

Implementation: write a temp Python script, run it under `freecadcmd`, have it
print JSON to stdout, parse that. The temp script is the agent's own generated
code, not model-authored.

**Why inside `read_text` rather than a new tool:** it inherits the sensitive-file
floor, read path zones, and `check_permission()` unchanged — no new security
code and no second gate to keep in sync. It also keeps the auditor's larger
result allowance, which `_tool_result_for_model` keys on the literal name
`read_text`. A `read_cad` tool would have to re-implement all of it.

### Phases 2 and 3 — deterministic write tools

Both declare `mode=write_text` rather than a private mode. Around a dozen sites
key on that mode (sensitive-file floor, path zones, plan-only blocking,
plan-audit revert, closure modes); a new mode would need adding to every one,
and missing a single site is a write that skips a guard.

**`convert_cad(filename, format)`** — target formats: `step`, `stl`, `iges`,
`obj`, `brep`, `dxf`, `pdf`. Output lands next to the source with the new
extension.

**`create_cad_part(filename, shape, dimensions, ...)`** — parameters, never
code. Scope is primitives only: box, cylinder, sphere, cone, tube, and
plate-with-holes. Anything richer belongs in the tier-4 skill. This is the
deterministic floor for when code generation defeats the model, exactly as
`create_document` is for documents.

Both join `_NON_AUDITABLE_TOOLS` (`engine.py:2702`). They verify their own
output on disk; the auditor runs in a disposable sandbox copy that cannot see
the real Windows file the step produced, so auditing them yields fail/unknown
verdicts from the auditor's own blindness — noise that costs a model call and
can revert correct work. This reasoning is already documented for
`convert_document`.

### Phase 4 — the `cad` skill

Teaches FreeCAD Python via `execute_shell` for tiers 3–4. Must carry forward
the lessons the `documents` skill learned the hard way from live failures:

- **`execute_shell` runs `cmd.exe` on Windows, not bash.** `dir` not `ls`,
  `2>nul` not `2>/dev/null`, double quotes not single — `python -c '...'` fails
  because cmd does not strip single quotes.
- **Call `freecadcmd` by full quoted path.** It is not on `PATH` after a winget
  install (verify — `soffice` was not).
- **"Modify this file" means the existing file**, not a fresh one generated
  from scratch with similar content. This exact failure happened twice with
  documents.
- **Verify output on disk** after every operation.

## Error handling

Each item below is a failure mode actually observed during the document work,
not a hypothetical:

| Failure | Handling |
|---|---|
| FreeCAD not installed | Actionable message naming the winget command. Never a traceback. Mirrors `_soffice_executable()`. |
| Binary ran, no output produced | Check the file on disk — **not** the exit code or stdout. `soffice` prints a success line and produces nothing on a filter error. |
| Timeout | Clear message, not a traceback. FreeCAD cold-start is slower than `soffice`; default higher than 60s, tuned after measurement. |
| Cloud placeholder input | Reuse `documents.cloud_placeholder_message()` — already built, and applies verbatim to a `.step` sitting in OneDrive. |
| Unsupported target format | Refuse before spawning the subprocess, naming the supported list. |

## Testing

- **Unit** (`tests/test_cad.py`), mirroring `test_convert_document.py` with
  `subprocess.run` monkeypatched so no FreeCAD install is needed: missing
  binary, unsupported format, missing source, timeout, ran-but-produced-nothing,
  success.
- **Engine** (`tests/test_cad_tools.py`), mirroring
  `test_convert_document_tool.py`: registration, `mode == "write_text"`,
  description survived the pipe-delimited registry, gating in `readonly` and
  `plan-only`, sensitive-path refusal, and auditor exclusion via
  `_plan_step_is_auditable`.
- `scripts/verify_everything.py` — `expected_tools` gains `convert_cad` and
  `create_cad_part`.
- `docs/wiki/04-tools.md` and `09-skills-and-subagents.md` updated in the same
  change.
- **Manual, post-install**: a real STEP→STL conversion, and inspection of a
  real CAD file.

## Open items — verified at implementation time, not assumed

FreeCAD's wiki is behind bot protection (Anubis), so these could not be
confirmed while writing this. They get verified empirically after install,
which is the same discipline that caught `soffice` not being on `PATH`:

1. Exact `freecadcmd.exe` filename and install path under `C:\Program Files\`.
2. Whether winget puts it on `PATH`. **Assume not** until shown otherwise.
3. Cold-start time, which sets the timeout default.
4. Whether `freecadcmd` exits non-zero on a script exception, or swallows it —
   determines how much the disk check has to carry.

## Explicitly cut

- **Docker sandbox support.** `freecadcmd` would have to exist inside the Linux
  container; nothing in this repo builds a custom image. Native Windows sandbox
  only, same limitation as LibreOffice.
- **`install.sh` (Linux/macOS).** The winget path and all verification here are
  Windows-specific. Same shape applies there (`apt install freecad` /
  `brew install --cask freecad`) — a natural follow-up, not this change.
- **CadQuery / build123d as an alternative backend.** Genuinely better suited
  to scripted CAD and pip-installable at ~200MB versus FreeCAD's ~1GB, but
  FreeCAD was chosen deliberately for its far wider format support. Recorded so
  the trade-off is not re-litigated from scratch later.
- **A `.fcstd` stdlib fast-path** (see above).
- **Mesh repair, boolean-heavy modeling, drawings/TechDraw output.** Not asked
  for; each is its own project.
