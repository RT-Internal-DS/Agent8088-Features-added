# CAD Plan-then-Emit Contract — Design

Date: 2026-08-28
Status: approved (scope: Approach 1 + failure hints; Approach 2 optional; Approach 3 deferred)

## Problem

Complex CAD briefs on reasoning models (GLM-5.3) fail with
`Model output reached its 32768-token limit` because one completion must carry
both extended reasoning AND a 150-line generator. When a retry does emit code,
common build123d API mistakes (e.g. `'Align' - int`) produce terse errors that
trigger another expensive re-think instead of a targeted repair.

## Goals

1. Thinking stays ON (user requirement), but is spent ONCE at plan time, not
   interleaved while writing code.
2. A complex generator fits comfortably in the 32k completion budget.
3. A failed generation repairs the named error cheaply.

## Non-goals

- Disabling or truncating thinking (rejected: user wants thinking on).
- Continuation-resume of truncated tool calls (deferred; fragile splicing).
- Changes to the CAD worker's validation pipeline.

## Design

### 1. Two-turn plan-then-emit contract (engine.py)

`_cad_runtime_instruction` gains a mandatory rhythm for generation tools:

- Turn 1: reply with a BUILD PLAN in plain text, ≤20 lines, no tool call:
  components (labeled solids), parameter table with derived dims,
  allowed_contact pairs, datum/orientation convention, expected bounding box.
- Turn 2: exactly one `generate_cad_model` (or `generate_cad_design`) call
  implementing that plan. No re-derivation, no narration.

Engine enforcement (`_run_agent_loop`):
- New counter `cad_plan_pending`. Set when a CAD request is active and the
  model produced text without tool calls on the first CAD turn.
- When set, the injected follow-up message is:
  `Plan received. Now emit the single generate_cad_model call implementing it
  — one call, no further prose.`
- The gate is bounded by the existing missing-args-retry pattern (≤2 nudges),
  after which the model is allowed to proceed without a plan (never a
  dead-end).
- A CAD turn that starts with a tool call skips the plan gate entirely (small
  requests must not pay the two-turn tax): the gate only fires when the model
  itself chose to reply with text instead of calling.

State: `cad_plan_pending` boolean, reset on successful generation. No
persistence; per-turn only.

### 2. Enriched CAD failure hints (cad.py `_worker_failure`)

Map common build123d runtime errors to one-line repair hints appended to the
failure text:

- `'Align' and 'int'` / align arithmetic → build the shape first, then
  `Pos(...) * shape`; never subtract from an Align enum.
- `is_valid() not callable` → `Face.is_valid` is a property.
- `BRep_API: command not done` → fillet/boolean failure; reduce radius,
  filter edges (references/build123d-modeling.md).
- `no attribute 'make_face'` → use `Plane(...) * Polygon(...)` + `extrude`.
- `Null TopoDS_Shape` → a prior boolean produced an invalid intermediate;
  repair the named feature.
- `volume is 0` after wrap → use the Solid directly, do not re-wrap.

Hints are appended to the failure line; the retry loop already treats these as
repairable.

### 3. Optional reasoning_effort passthrough (engine.py, providers)

Config: `provider.<name>.reasoning_effort=low|medium|high`.
If set, the completion request passes `extra_body={"reasoning_effort": ...}`.
On HTTP 400 mentioning the parameter, retry the call once WITHOUT extra_body
(tolerant transport — some compat layers reject unknown fields).
No worker/CAD changes.

## Testing

- `test_cad_plan_gate_requests_plan_before_code`: first CAD turn with text-only
  reply → no exec, instruction appended, next turn proceeds.
- `test_cad_plan_gate_does_not_fire_when_tool_called`: plan-free immediate tool
  call still executes.
- `test_cad_failure_hints_map_common_errors`: `'Align' and 'int'` → hint text.
- `reasoning_effort passthrough + tolerant retry` (unit, monkeypatched client).
- Existing suite stays green (74 CAD tests).

## Risks

- Plan-gate could loop with a stubborn model → bounded to 1 injection, then
  proceeds normally.
- reasoning_effort unknown to some endpoints → send only when configured;
  on 400, retry clean.