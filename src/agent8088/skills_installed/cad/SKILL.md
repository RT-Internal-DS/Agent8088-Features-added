---
name: cad
description: Build, verify, export, and visually review parametric mechanical CAD through Agent8088's supervised build123d-mcp and text-to-cad workflow.
---

# CAD

Agent8088 uses one CAD route:

1. Agent8088 owns reasoning, permissions, timeouts, recovery, and artifacts.
2. The isolated build123d-mcp server owns incremental build123d geometry state.
3. text-to-cad independently reopens, validates, snapshots, and displays final artifacts.

Never generate CAD with `execute_shell`, `write_file`, `run_sandboxed`, or FreeCAD
Python. There is no one-shot CAD generation tool to fall back on: the supervised
session is the only route. Never claim success from an exit code.

## Required workflow

1. Convert the request into editable parameters and measurable requirements.
2. Call `cad_begin` once with a short artifact-directory name.
3. Call `cad_execute` with one coherent feature or component only. Wait for its result.
4. Register meaningful geometry with `show(shape, "StableName")`.
5. Read the automatic compact numeric checkpoint returned by every successful
   `cad_execute`. Call `cad_measure` only when the full face inventory is needed.
6. Save `cad_snapshot` before risky fillets, shells, lofts, or complex booleans.
7. Repeat one tool call per model response until construction is complete.
8. Call `cad_verify` on the final named part or final Compound assembly. It checks
   the current revision against immutable request requirements, structural
   validity, aperture continuity, and parametric robustness. Treat robustness
   brittleness as an advisory warning because independently perturbed coupled
   parameters can create false failures; request and structural failures remain
   blocking.
9. Repair any failure; never weaken the requested checks.
10. Call `cad_render` for an isometric preview and inspect it for gross errors.
11. Call `cad_export` for STEP and requested secondary formats. Export repeats all
    deterministic gates and saves source,
    parameters, reports, and preview, then independently reopens the STEP through
    text-to-cad.
12. Call `open_cad_viewer` on the exported STEP unless the user declined visual review.

`cad_export` is the only successful completion path for generated CAD.

## Correct build123d patterns

The MCP execute environment already exposes build123d. These are valid patterns:

```python
from build123d import *

profile = RegularPolygon(PARAMS["radius"], 6) - Circle(PARAMS["bore"])
spacer = extrude(profile, PARAMS["height"])
show(spacer, "Spacer")
```

Use:

- `RegularPolygon`, not an invented `Hexagon` helper.
- `Vector`, not `Vec`.
- `extrude(profile, amount)`, not a CadQuery-style `.extrude()` guess.
- `Pos(x, y, z) * shape` for translation, `Rot(rx, ry, rz) * shape` for rotation.
- `PolarLocations(radius, count) * feature` for a bolt circle or radial pattern.
- `PARAMS["name"]` for every dimension, so the design stays editable.

Prefer simple, inspectable construction steps. Reuse variables already present in
the persistent session rather than resending prior code.

When unsure about feature ordering or a failed boolean, call
`cad_guidance(topic="modeling")` or `cad_guidance(topic="repair")`. These read the
approved guidance bundled with the installed build123d-mcp version. Do not pass a
`build123d://...` URI to `view_skill` or invent a replacement API.

Feature order is part of correctness. Additive ribs, bosses, pads, and fused
components can refill or obstruct a hole cut earlier. After the final additive
union, reapply bores, mounting holes, slots, and other openings that must remain
clear, then run `cad_verify`. Never assume an earlier successful cut remains valid.

`cad_execute` blocks must **build geometry only**. Read the model with the
`cad_measure`, `cad_inspect`, `cad_validate`, and `cad_compare` tools instead of
`print(...)` or the server's in-namespace analysis helpers (`measure`,
`find_holes`, `clearance`, ...). Bare analysis calls are stripped from the
exported source; a value taken from one makes the design unreplayable outside
the live session and downgrades the strict export gate.

## Measuring and checking

- The automatic checkpoint and `cad_measure` report volume, area, bounding box, centre of mass, and
  face/edge/vertex counts. It does **not** report a solid count.
- `cad_validate` reports `n_solids` plus watertight/manifold/B-rep status.
- `cad_inspect` compares the model to expectations. Its `expected` object accepts
  only these keys:
  - `bbox` — a 3-number `[x, y, z]` array in mm, or an object with any of
    `x`, `y`, `z`
  - `solid_count` — integer
  - `holes` — array of `{count, axis, diameter, depth, bottom, cbore, spotface}`
  - `bosses` — array of `{count, axis, diameter, height}`
  - `patterns` — array
  - `section_varying` — boolean
  - `tolerance` — mm, default `0.1`

  Anything else is rejected. Write `{"bbox": [120, 120, 32], "solid_count": 1}`,
  never `{"bounding_box_mm": ...}` with invented sibling keys. `axis` and
  `direction` may be written as `"Z"`/`"-Z"` or as `[0, 0, 1]`.

  `holes`, `bosses`, and `patterns` expectations are **exhaustive**: every
  recognised group must match one expectation, and one expectation must not match
  two distinct groups. A part with five lightening holes *and* a central bore
  needs both declared, with a distinguishing `diameter` or `depth` on each. When
  you only want dimensions checked, use `bbox` and `solid_count` and read the
  feature inventory from a plain `cad_inspect` call with no `expected`.

Remember that `Box`, `Cylinder`, and `Sphere` are centred on the origin while
`extrude()` grows from the sketch plane. Mixing them without an explicit `Pos`
shifts the part; `cad_inspect` with a `bbox` expectation is what catches it.

## Multi-component designs

Build and `show()` each component under a stable name. Measure parts after
construction and use `cad_compare(kind="fit")` for clearances or unintended
interference.

Prefer creating one final `Compound(children=[...], label="Assembly")`, register it
as `show(assembly, "Assembly")`, and export that explicit name. `object_name="*"`
remains available for deliberately separate registered components, but means
everything currently registered. Do not leave scratch geometry under a `show()`
name. Keep intermediates unregistered, overwrite a stable name, or build the final
Compound.

Do not write an entire robot, house, telescope, or assembly in one tool call.
Large requests must be decomposed into feature clusters so model output stays well
below its completion limit and every operation is based on real prior results.

## Editing an existing model

`cad_import` copies the file into the session workspace, registers it, and binds
it to a session variable named after it. Use that variable directly:

```python
edited = drive_flange - Pos(0, 0, 0) * Box(200, PARAMS["slot_w"], 6)
show(edited, "Edited")
```

Imported geometry cannot be rebuilt from parameters alone, so such a session is
gated by the clean-process replay rather than the constrained source replay. The
report says which gate ran.

## Failures and recovery

After a failed `cad_execute`:

1. Call `cad_last_error` immediately.
2. Correct the named line or feature only.
3. Use `cad_restore` if the previous known-good snapshot is needed.
4. Retry once with a smaller block.

A failed block is never committed, so it is never replayed. `cad_restore` also
rewinds the replayable history to that checkpoint, which means the exported
source always matches the geometry you kept.

The supervised runtime kills and restarts a wedged server and replays only
previously successful blocks. Do not blindly repeat a timed-out operation.

## Output contract

`cad_export` publishes nothing until every required gate passes. It stages the files, then
requires: immutable request acceptance on the final revision, cylindrical-aperture
continuity for declared bores and holes, the MCP validity gate, a clean-process
rebuild whose geometry and
request checks match the live session, a replay of the generated `gen_step()`
source where the constrained generator can run it, and an independent text-to-cad
reopen of the STEP with its own envelope/solid-count checks and snapshot.
The bounded upstream parameter robustness audit is recorded alongside these
results as advisory evidence rather than a release blocker.

The published bundle is canonical STEP, the requested STL/3MF, the generated
`<design>.step.py` source, the committed `<design>.cad.py` transaction log,
`<design>.params.json`, `<design>.report.json`, `<design>.mcp-report.json`, and a
PNG preview. STEP is the canonical portable B-rep. STL is a tessellated
manufacturing/printing derivative, not the editable source of truth.

All dimensions are millimetres unless the user explicitly states otherwise.
