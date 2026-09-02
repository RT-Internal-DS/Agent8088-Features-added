# CAD Assembly Generation — Specification-First Redesign

**Date:** 2026-08-31
**Status:** Proposed

## Context

Agent8088's staged CAD workflow (`cad_project_create` → `cad_project_add_component` ×N →
`cad_project_finalize`, added in PR #122) processes exactly one component per model turn and
asks the model to compute final world-space `at`/`rotate` vectors for every occurrence by hand
at finalize time (`cad_project.py:334-335`). On a real run (`robotic_gripper`, logged
2026-08-28 17:58-18:23), this produced **32 turns over 25 minutes with no successful
finalize** in the session window — the model kept adding components and never converged.
The monolithic single-call path (`generate_cad_model`) succeeded on a comparable assembly
(`three_jaw_chuck`) but only after ~10 turns of retry driven by interference failures from
model-computed clearances.

Research into published text-to-CAD systems (ASSEMCAD, ArtiCAD, Nova3D — all 2025/2026)
converges on a different shape than either of these: the model declares the **full assembly
specification** (parts + typed connections) in one pass, and a **deterministic, non-LLM
layer** in the harness computes actual positions from those declared connections. The model
never authors coordinates. ASSEMCAD's own ablation is the load-bearing number: the same
model (Claude Opus 4.8) scored 0.83% Assembly Preservation Rate with no scaffold and 87.5%
inside their specification+deterministic-mate architecture — evidence that harness
architecture, not model capability, is the dominant variable for this task shape.

**Outcome:** replace the incremental per-component discovery loop with a one-shot
specification step, a deterministic mate-transform layer for positioning, and a
`bd_warehouse`-backed factory library for standardized mechanical elements — reserving
model-authored build123d code for genuinely bespoke geometry only, each with a small bounded
repair budget rather than an open turn count.

### Explicitly not solving

- **Kinematic motion / degrees-of-freedom verification.** ArtiCAD's own paper excludes closed
  kinematic loops (scissor linkages, four-bar linkages) because it computes DOF over a
  kinematic tree. This redesign sidesteps that limitation by not attempting it: the harness
  only needs **one static, valid configuration** (interference-free, contacts as declared) —
  matching what CAD requests actually ask for (an isometric snapshot at one jaw-opening
  state), not a proof that a mechanism swings through its full range of motion. A closed loop
  (three jaws + one scroll disc) is simply three more mates in a static interference check.
- **Vision/rendered-image feedback.** Ruled out — text-only models are a hard constraint.
- **Fine-tuning a model.** The strongest published results (Text-to-CadQuery, CADmium) are
  fine-tuning results, and fine-tuning is a different, much larger project (training
  pipeline, labeled dataset) than a harness change. Out of scope here; noted as the honest
  ceiling on what prompting/harness work alone can achieve.

## Design

### 1. New specification schema at `cad_project_create`

Today, `create` takes `name`, `parameters`, `verification` and nothing about the assembly's
shape — the model discovers parts one `add_component` call at a time. It becomes:

```json
{
  "parts": [
    {"name": "Housing", "kind": "custom", "description": "cylindrical housing with T-slots"},
    {"name": "BoltA",   "kind": "warehouse.fastener", "params": {"size": "M6", "kind": "SocketHeadCapScrew", "length": 20}},
    {"name": "Pinion1", "kind": "warehouse.gear", "params": {"module": 1.5, "tooth_count": 10, "thickness": 8}},
    {"name": "Jaw1",    "kind": "custom", "description": "stepped clamping jaw with serration grooves"}
  ],
  "mates": [
    {"type": "coaxial",   "a": "Pinion1.bore", "b": "Housing.pinion_seat_1"},
    {"type": "press_fit", "a": "Jaw1.slot_key", "b": "Housing.tslot_1"}
  ],
  "verification": {"expected_solids": 4, "max_bbox_mm": [120, 120, 50]}
}
```

(`verification` is unchanged from today's schema — the same structured checks `create` already accepts.)

Every part declares a `kind`: either a factory reference (`warehouse.*`, resolved by the new
factory layer below with zero LLM calls) or `"custom"` (model-authored build123d, scoped to
that one part). `mates` is the full set of inter-part connections, declared once, up front —
this is the piece that does not exist today and is the actual fix for the turn-count and
model-authored-coordinate problems simultaneously.

### 2. Factory layer — `bd_warehouse`, not a from-scratch library

`bd_warehouse` (pip-installable, same author as build123d, native build123d objects — e.g.
`SpurGear(module=2, tooth_count=12, pressure_angle=14.5, thickness=5*MM)`) covers Bearing,
Fastener, Flange, Gear, O-ring, Pipe, Retaining_ring, Shaft_key, Sprocket, Thread. This
directly resolves the chuck prompt's M6 bolt circle (`Fastener`) and scroll pinions (`Gear`)
with zero model-authored geometry code.

**Gap:** `bd_warehouse` returns raw geometry with no concept of named attachment points
("ports"). A new thin module, `cad_ports.py`, wraps each `warehouse.*` kind with a small
declaration of its known ports (e.g. a gear's bore axis, a fastener's bearing face) — this is
real but small work, since the geometry itself is already solved.

**Execution-environment detail, verified against the real runtime, not assumed:**
`cad_worker.py` runs as `python -I cad_worker.py request.json` (`cad.py:_run_worker`) — a
bare isolated script, not a package import, and `-I` mode does **not** add the script's own
directory to `sys.path` (confirmed empirically). `cad_mates.py`/`cad_ports.py` therefore need
`cad_worker.py` to explicitly `sys.path.insert(0, str(Path(__file__).parent))` before
importing them — a one-line fix, but a real gap between "drop a file next to cad_worker.py"
and "it actually imports."

**Still bespoke, always `kind: "custom"`:** the housing, the jaws, the scroll disc — anything
unique to the specific design, exactly as today's `cad_project_add_component` handles it, but
now scoped only to parts nothing in the factory layer can produce, with the bounded repair
loop from §4 instead of an open-ended turn budget.

**License/version check — unresolved, flagged, must happen before implementation, not
assumed:** confirm `bd_warehouse`'s license and its compatibility with this repo's pinned
build123d version by installing it directly, not from search-result summaries.

### 3. Deterministic mate transforms replace model-authored `at`/`rotate`

`cad_project_finalize` (`cad_project.py:289-388`) currently takes model-supplied `at`/`rotate`
vectors per occurrence (defaulting to `[0,0,0]`, `cad_project.py:334-335`) — the model computes
world-space placement by hand. This is replaced by a small closed-form transform per mate
type, computed by the harness from the declared ports:

| Mate type | What it does |
|---|---|
| `coaxial` | Aligns two named axes (e.g. a pin in a bore) |
| `face_to_face` | Coincident faces, opposing normals |
| `press_fit` | Like `coaxial`, plus auto-exempts the pair from interference |
| `gear_mesh` | Positions two gears at center distance from tooth counts/module, auto-exempts |

Four mate types, not ASSEMCAD's seven — enough for a bolt circle, a pinion seated in a bore,
a jaw keyed into a T-slot, and a gear pair; `thread_engage`/`snap_to_face`/`coaxial_face`
deferred until a real request needs them (YAGNI — ASSEMCAD needed seven because it targets
arbitrary mechanical assemblies broadly; ship the four this repo's actual CAD prompts use).

### 4. `allowed_contact` is replaced by mate-type-driven exemption

Today `allowed_contact` (`cad_worker.py:586-612`) is a flat list the model must independently
populate and keep in sync with the geometry it already described. Under this design, a
declared `press_fit` or `gear_mesh` mate *is* the contact declaration — `_exempt_interference`
is called with the mate-derived pair set instead of a separately model-authored one. One
source of truth instead of two that can drift apart.

### 5. Bounded repair for `custom` parts

`cad_project_add_component` keeps its current execute-validate-checkpoint behavior for
`kind: "custom"` parts, but capped at a fixed repair budget (default 3 attempts against real
execution/validation errors) rather than the open per-project turn count that let the gripper
run to 32 turns. A `custom` part that exhausts its budget fails that part by name; the
assembly does not proceed until it's fixed or reworked, but nothing else is re-attempted.

### 6. Interaction with existing turn limits

`CAD_PROJECT_MIN_TURNS = 24` (`engine.py:7628`) existed specifically to cover
one-component-per-turn generation. Once generation is specification-first, its rationale
changes: the floor should cover one specification call + repair attempts for whatever
fraction of parts are `custom`, not one call per part regardless of factory coverage. Revisit
the constant's value once real assemblies are run against this design, rather than guessing
a new number now.

## Files

| File | Change |
|---|---|
| `src/agent8088/cad_project.py` | New specification schema at `create`; `finalize` computes positions from mates instead of accepting `at`/`rotate` |
| `src/agent8088/cad_mates.py` | **New.** The four closed-form mate transforms + interference-exemption derivation |
| `src/agent8088/cad_ports.py` | **New.** Port declarations wrapping `bd_warehouse` factory output |
| `src/agent8088/cad_worker.py` | `_exempt_interference` call site takes mate-derived pairs instead of model-authored `allowed_contact` |
| `src/agent8088/tools.txt` | `cad_project_create`/`cad_project_add_component`/`cad_project_finalize` contract text updated for the new schema |
| `src/agent8088/skills_installed/cad/SKILL.md` | Workflow description rewritten around specification-first + factory kinds |
| `src/agent8088/cad_runtime_requirements.txt` | Add `bd_warehouse` dependency, pending the license/version check in §2 |
| `tests/test_cad_project.py` | New coverage: mate transform correctness, mate-driven interference exemption, factory-kind resolution with zero LLM calls, `custom`-part bounded repair exhaustion |

## Verification

- **License/version check on `bd_warehouse`** (blocking, do first): install against this
  repo's pinned build123d version, read the actual `LICENSE` file, confirm no conflict.
- **Unit tests** for each of the four mate transforms against hand-computed expected
  positions (no LLM, no execution — pure geometry math).
- **Regression re-run** of the `three_jaw_chuck` and `robotic_gripper` prompts from the
  original logs, comparing turn count and wall-clock time against the 2026-08-28 baseline
  (chuck: ~10 turns monolithic; gripper: 32+ turns, no completion).
- **Static-configuration check**: confirm the chuck's scroll-disc-plus-three-jaws closed loop
  validates as one consistent interference-free snapshot, without any DOF/motion computation
  — the concrete test of the "explicitly not solving kinematics" decision in Context.
