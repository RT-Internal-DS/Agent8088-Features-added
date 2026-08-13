# `/plan` Becomes Plan Mode Implementation Plan

> **For the implementer:** work task-by-task, in order. Each task is 2–5 minutes.
> Run the test after every task. Commit after every task.

**Goal:** Make `/plan` in Agent8088 mean what it means in Claude Code, Hermes and
Codex — enter plan mode, research read-only, propose one plan, wait for the
user's approval, execute the approved plan, then drop back to the default chat
mode — and fix the failures that make today's plan-only mode unusable.

**Architecture:** Today `execute_plan` is *both* the plan and the executor: the
model must emit a JSON array of fully-specified tool calls, which is the only
thing that gets approved and run. Split those two jobs. A new `present_plan`
tool (mode=plan) carries the plan as **markdown prose**, asks the user to
approve it, and on approval changes `PERMISSION_MODE` so the model executes the
plan with **ordinary tool calls**. `execute_plan` stays for structured step
sequencing and plan auditing. `/plan` becomes sticky (a mode, not a one-shot
wrapper) and the mode is restored automatically once the approved plan finishes.

**Tech stack:** Python 3.10+, `rich` (CLI rendering), `prompt_toolkit` (input),
`pytest`. No new dependencies.

---

## Reproduction Evidence

Run against the live Agent8088 backend (`ornith-1.0-35b` at
`http://192.168.3.67:8080/v1`) with an isolated `AGENT8088_CONFIG` /
`AGENT8088_HOME` in a scratch workspace, so nothing outside the temp dir was
touched. Verbatim observations:

**Repro A — happy path works, so the bug is not "plan mode is broken":**

```
/mode plan-only
create a file hello.txt containing the word hi
```
→ `execute_plan(steps=[{write_file …}])` → approval prompt → `Wrote 2 bytes` →
`read_text` → correct answer. **This case passes.**

**Repro B — a real plan silently does nothing, then reports success:**

```
/mode plan-only
refactor: create three files a.txt b.txt c.txt each containing its own letter,
then read them all back and summarize
```
Turn 1 output was **prose**, with no tool call at all:

```
│ Here's the plan to create the three files, read them back, and summarize:  │
│ Step 1: Write a.txt containing "a" Step 2: Write b.txt … Step 6: Read c.txt│
```

No approval prompt, no execution. The next turn then said:

```
│ Done! Here's the summary:                                                 │
│  • a.txt contains: a    • b.txt contains: b    • c.txt contains: c        │
│ All three files were created successfully … and verified by reading them back.
```

`ls` of the workspace afterwards: **empty**. Zero files were created. The agent
reported a completed, verified plan that never ran.

**Repro C — plan mode is a one-shot; every later turn errors:**

After one successful plan in the same session, the next request produced:

```
⏺ write_file(filename="d.txt", content="d")
  ⎿  Error: plan-only mode — direct tool execution blocked. Call the
     execute_plan tool with a JSON steps array, e.g.: …
  ⎿  No answer produced — asking the model to respond.
╭──── Agent8088 ────╮
│ I wasn't able to produce an answer to that. │
```

…repeated for four consecutive turns. **This is the error the user reported.**

**Repro D — two approval prompts in one turn:** a task with a write and a shell
step produced `execute_plan` → approve → then a *direct* `execute_shell` → hard
block → then a *second* `execute_plan` → a second approval prompt. One user
request, two plans, two prompts.

**Repro E — a natural-language plan always fails at step 1.** Direct probe of
`_exec_plan` (no model involved):

```
_exec_plan({"steps": "Step 1: Write a.txt containing a\nStep 2: Read a.txt"})
→ [1] write_file: Error: plan step requires arguments for write_file:
     filename, content. Use a JSON step with tool and arguments.
  Plan halted at step 1/2 …
```

**Repro F — prose becomes a shell command.** `_exec_plan({"steps": "[1,2,3]"})`
→ `execute_shell(command="1")`. `classify_plan_component("")` ties every tool at
score 0 and returns the first one iterated (`execute_shell`), then
`_infer_step_args` puts the step *text* in `command` because `execute_shell` has
exactly one required arg. A prose step like `Delete the old backups` is therefore
eligible to be handed to a shell as a literal command string.

**Baseline test state (before any change):**

```bash
PYTHONPATH=src AGENT8088_CONFIG=/nonexistent python3 -m pytest tests/ -q \
  --ignore=tests/test_mcp.py --ignore=tests/test_mcp_server.py --ignore=tests/gateway
```
→ `2 failed, 668 passed`. Both failures are pre-existing and unrelated
(Windows-only assertions that cannot hold on macOS):
`tests/test_classic_banner.py::test_logo_falls_back_to_ascii_on_a_legacy_console`
and `tests/test_plan_audit.py::test_private_file_protection_does_not_use_a_shadowable_whoami`.
Do not try to fix those here; just keep the count at 2 failed.

---

## Diagnosis — Six Root Causes

| # | Cause | Where | Symptom |
|---|-------|-------|---------|
| 1 | Plan presentation and plan execution are the same tool, so the *only* approvable plan is a fully-specified JSON tool array | [engine.py:2116](src/agent8088/engine.py:2116) `_exec_plan` | Repro B, E |
| 2 | Nothing detects "the turn ended in plan mode and no plan tool ran", so a prose plan looks identical to an executed one | [cli.py:763](src/agent8088/cli.py:763) `do_chat` | Repro B (fabricated success) |
| 3 | Approval sets a temporary `_plan_execution_grant` that is cleared at the end of `_exec_plan`, and `PERMISSION_MODE` never leaves `plan-only` | [engine.py:2242](src/agent8088/engine.py:2242), [engine.py:891](src/agent8088/engine.py:891) | Repro C, D |
| 4 | The plan-only block message tells the model to call `execute_plan` with JSON, which is the thing it cannot reliably produce; the model retries the direct call until the loop gives up | [engine.py:3177](src/agent8088/engine.py:3177), [engine.py:3369](src/agent8088/engine.py:3369) | Repro C ("No answer produced") |
| 5 | `classify_plan_component` has no "I don't know" answer — it falls back to an arbitrary tool and `_infer_step_args` fills the one required arg with the step prose | [engine.py:1809](src/agent8088/engine.py:1809), [engine.py:1823](src/agent8088/engine.py:1823) | Repro F |
| 6 | `/plan` is a one-task wrapper that restores the old mode in a `finally`, so it is not a mode at all | [cli.py:1216](src/agent8088/cli.py:1216) `cmd_plan` | user's core complaint |

**Not a cause:** `/model plan-only` (what the user typed) is simply rejected —
`cmd_model` prints `unknown provider 'plan-only'`. The mode command is `/mode`.
Task 14 makes `/model plan-only` point at the right command instead of a bare
"unknown provider".

---

## Target Behavior (from Context7 — Claude Code docs)

Per `code.claude.com/docs/en/commands` and `/permission-modes`:

- `/plan` "enables users to enter plan mode directly from the prompt. Users can
  include an optional description to initiate the mode and immediately begin
  working on a specific task."
- Plan mode "restricts the agent to exploration and planning without executing
  file edits".
- "When a plan is ready, you can choose to proceed by starting auto mode,
  manually approving individual edits, or refining the plan further. **Approving
  a plan exits plan mode and transitions the session into the selected execution
  mode.**"
- The exit point is a dedicated tool (`ExitPlanMode`) whose output carries the
  plan text, not a step array.

Mapped onto Agent8088:

| Claude Code | Agent8088 |
|---|---|
| `/plan [description]` | `/plan [task]` — enter plan mode, optionally send the task |
| plan permission mode | `PERMISSION_MODE = "plan-only"` |
| `ExitPlanMode(plan=…)` | `present_plan(plan=…)` (new, `mode=plan`) |
| approve → auto mode | approve `a` → `full-auto` |
| approve → manual edits | approve `e` → `readonly` (each write prompts) |
| keep planning | `d` → stays in plan mode |

**One deliberate difference from Claude Code, per the user's instruction:** "once
that task is done the plan mode is disabled and default chat is back." Claude
Code *stays* in the mode you selected. Agent8088 will restore the mode the
session had before `/plan` (normally `readonly`) as soon as the approved plan's
turn ends. This is implemented by `finish_plan_session()` (Task 5) and is the
behavior asserted by the tests.

---

## Files That Will Change

| File | What |
|---|---|
| `src/agent8088/engine.py` | plan-session state, `set_permission_mode`, `enter_plan_mode`, `finish_plan_session`, `cancel_plan_session`, `_exec_present_plan`, `plan_tool_ran`, block-message helper, `classify_plan_component` fallback, `_exec_plan` no-tool guard, `run_tool` dispatch, `reset_turn_counters` |
| `src/agent8088/tools.txt` | new `present_plan` line |
| `src/agent8088/system.md` | rewrite `## Plan-Only Mode` → `## Plan Mode` |
| `src/agent8088/cli.py` | `cmd_plan`, `cmd_mode`, `cmd_model` hint, `_plan_on_approval`, `do_chat` bookkeeping + turn budget, `_prompt_label`, `_read_line` label, help table, module docstring |
| `tests/test_plan_command.py` | **new** — 16 tests |
| `tests/test_classic_banner.py` | replace the 3 tests that encode the old one-shot `/plan` |
| `README.md`, `docs/wiki/10-cli-reference.md`, `docs/wiki/13-troubleshooting.md`, `docs/FEATURES_ADDED.md`, `CHANGELOG.md` | documentation |

---

## Task 0: Branch and Baseline

**Objective:** Start from a known-green baseline on a fresh branch.

**Step 1: Create the branch**

```bash
git checkout -b feat/plan-command-plan-mode
```

**Step 2: Record the baseline**

```bash
PYTHONPATH=src AGENT8088_CONFIG=/nonexistent python3 -m pytest tests/ -q \
  --ignore=tests/test_mcp.py --ignore=tests/test_mcp_server.py --ignore=tests/gateway 2>&1 | tail -3
```
Expected: `2 failed, 668 passed` — the two pre-existing Windows-only failures.
If you see anything else, stop and investigate before changing code.

---

## Task 1: Plan-session state and a single place that changes the mode

**Objective:** Give the engine one funnel for mode changes, so no grant outlives
the mode that authorized it, and add the state a plan session needs.

**Files:**
- Modify: `src/agent8088/engine.py:382-385` (the plan globals block)
- Test: `tests/test_plan_command.py` (new)

**Step 1: Write the failing test**

Create `tests/test_plan_command.py`:

```python
"""`/plan` is a mode, not a one-shot.

A plan is text a human reads and approves; execution is what happens after. The
tests below pin that separation, and pin the two ways it used to leak: a grant
that outlived its mode, and a plan that was never run being reported as done.
"""
import io

import pytest
from rich.console import Console

import agent8088.cli as cli


def test_setting_a_mode_drops_every_grant_tied_to_the_old_one(engine):
    engine.PERMISSION_MODE = "plan-only"
    engine._plan_execution_grant = True
    engine._one_shot_grant = True

    engine.set_permission_mode("readonly")

    assert engine.PERMISSION_MODE == "readonly"
    assert engine._plan_execution_grant is False
    assert engine._one_shot_grant is False
```

**Step 2: Run it to verify failure**

```bash
PYTHONPATH=src AGENT8088_CONFIG=/nonexistent python3 -m pytest tests/test_plan_command.py -q
```
Expected: FAIL — `AttributeError: module 'agent8088.engine' has no attribute 'set_permission_mode'`

**Step 3: Implement**

In `src/agent8088/engine.py`, replace the block at lines 382–385:

```python
_plan_on_step = None        # set by CLI do_chat so _exec_plan can render the checklist
_plan_on_escalation = None  # set by CLI do_chat so _exec_plan escalations reach _handle_escalation
_plan_execution_grant = False  # temporary: set True when user approves a plan; cleared after plan completes
```

with:

```python
_plan_on_step = None        # set by CLI do_chat so _exec_plan can render the checklist
_plan_on_escalation = None  # set by CLI do_chat so _exec_plan escalations reach _handle_escalation
_plan_on_approval = None    # set by CLI do_chat; shows the plan and returns the mode to run it in
_plan_execution_grant = False  # temporary: set True when user approves a plan; cleared after plan completes
# A plan session spans turns: plan mode is entered once, and left once — when the
# work it authorized is done. Keeping the return mode here rather than in the CLI
# means an embedder driving run_agent directly gets the same lifecycle.
_plan_return_mode = ""      # mode to restore when an approved plan finishes
_plan_approved = False      # the user approved this session's plan; execution is live
_plan_tool_ran = False      # turn-scoped: did a plan tool actually run this turn?


def set_permission_mode(mode: str) -> None:
    """The one place PERMISSION_MODE changes, so every grant tied to the old mode
    is dropped with it. A grant that outlives its mode is a hole: an approval the
    user gave for a plan step must not still be spendable after the mode moved on."""
    global PERMISSION_MODE, _one_shot_grant, _plan_execution_grant, _pending_approval_key
    PERMISSION_MODE = mode
    _one_shot_grant = False
    _plan_execution_grant = False
    _pending_approval_key = ""
```

**Step 4: Run the test**

```bash
PYTHONPATH=src AGENT8088_CONFIG=/nonexistent python3 -m pytest tests/test_plan_command.py -q
```
Expected: `1 passed`

**Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_plan_command.py
git commit -m "refactor: funnel permission-mode changes through set_permission_mode"
```

---

## Task 2: Enter and cancel a plan session

**Objective:** `enter_plan_mode()` remembers where to return; `cancel_plan_session()`
abandons a plan session without executing it.

**Files:**
- Modify: `src/agent8088/engine.py` (after `set_permission_mode`)
- Test: `tests/test_plan_command.py`

**Step 1: Write the failing tests**

Append to `tests/test_plan_command.py`:

```python
def test_entering_plan_mode_remembers_where_to_return(engine):
    engine.PERMISSION_MODE = "full-auto"

    engine.enter_plan_mode()

    assert engine.PERMISSION_MODE == "plan-only"
    assert engine._plan_return_mode == "full-auto"


def test_entering_plan_mode_twice_keeps_the_original_return_mode(engine):
    """Re-entering must not make plan-only itself the place to come back to."""
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    engine.enter_plan_mode()

    assert engine._plan_return_mode == "readonly"


def test_cancelling_a_plan_session_forgets_the_pending_plan(engine):
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    engine._plan_approved = True

    engine.cancel_plan_session()

    assert engine._plan_return_mode == ""
    assert engine._plan_approved is False
```

**Step 2: Run to verify failure**

```bash
PYTHONPATH=src AGENT8088_CONFIG=/nonexistent python3 -m pytest tests/test_plan_command.py -q
```
Expected: FAIL — `has no attribute 'enter_plan_mode'`

**Step 3: Implement**

Add directly after `set_permission_mode`:

```python
def enter_plan_mode() -> None:
    """Enter plan mode and remember the mode to come back to.

    Idempotent on purpose: `/plan` twice in a row must not record `plan-only` as
    the destination, which would strand the session in plan mode forever."""
    global _plan_return_mode, _plan_approved
    if PERMISSION_MODE != "plan-only":
        _plan_return_mode = PERMISSION_MODE
    _plan_approved = False
    set_permission_mode("plan-only")


def cancel_plan_session() -> None:
    """Abandon a plan session without running it — the user changed mode by hand."""
    global _plan_return_mode, _plan_approved
    _plan_return_mode = ""
    _plan_approved = False
```

**Step 4: Run**

```bash
PYTHONPATH=src AGENT8088_CONFIG=/nonexistent python3 -m pytest tests/test_plan_command.py -q
```
Expected: `4 passed`

**Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_plan_command.py
git commit -m "feat: plan sessions know the mode they came from"
```

---

## Task 3: The `present_plan` tool spec

**Objective:** Declare the tool so the model can see it.

**Files:**
- Modify: `src/agent8088/tools.txt` (add after the `execute_plan` line, line 10)
- Test: `tests/test_plan_command.py`

**Step 1: Write the failing test**

```python
def test_present_plan_is_a_plan_mode_tool_taking_prose(engine):
    spec = engine.TOOL_SPECS["present_plan"]

    assert spec["mode"] == "plan"
    assert spec["args"] == ["plan"], "the plan is text, not a step array"
```

**Step 2: Run to verify failure**

Expected: FAIL — `KeyError: 'present_plan'`

**Step 3: Implement**

Add one line to `src/agent8088/tools.txt`, immediately after the `execute_plan`
line (mind the `|` field separator — no literal `|` inside the description):

```
present_plan|Present a finished plan to the user for approval. Use this in plan mode as soon as you know what to do. Pass the whole plan as markdown text in "plan": the goal, numbered steps, and the files each step touches. The user approves it, the permission mode changes, and you then carry out the steps with ordinary tool calls. Do NOT pass JSON steps and do NOT use this outside plan mode.|mode=plan|args=plan|timeout=600
```

The 600s timeout is deliberate: the tool's runtime is a human reading a plan.

**Step 4: Run**

Expected: `5 passed`

**Step 5: Commit**

```bash
git add src/agent8088/tools.txt tests/test_plan_command.py
git commit -m "feat: declare the present_plan tool"
```

---

## Task 4: `_exec_present_plan` — approval flips the mode

**Objective:** The plan-mode exit point. Refuses empty plans and wrong modes;
on approval changes the mode and tells the model to execute normally.

**Files:**
- Modify: `src/agent8088/engine.py` (add above `_exec_plan`, ~line 2116)
- Test: `tests/test_plan_command.py`

**Step 1: Write the failing tests**

```python
PLAN = "## Goal\nAdd a greeting file.\n\n1. Write a.txt containing 'a'\n2. Read it back"


def test_present_plan_needs_the_plan_text(engine):
    engine.PERMISSION_MODE = "plan-only"
    engine._plan_on_approval = lambda text: "full-auto"

    assert "requires a non-empty 'plan'" in engine._exec_present_plan({})


def test_present_plan_is_refused_outside_plan_mode(engine):
    engine.PERMISSION_MODE = "full-auto"
    engine._plan_on_approval = lambda text: "full-auto"

    out = engine._exec_present_plan({"plan": PLAN})

    assert "only applies in plan mode" in out


def test_present_plan_says_so_when_there_is_nobody_to_approve(engine):
    """A non-interactive run must not silently self-approve."""
    engine.PERMISSION_MODE = "plan-only"
    engine._plan_on_approval = None

    out = engine._exec_present_plan({"plan": PLAN})

    assert "not approved" in out
    assert engine.PERMISSION_MODE == "plan-only"


def test_approval_switches_the_mode_and_tells_the_model_to_execute(engine):
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    seen = []
    engine._plan_on_approval = lambda text: seen.append(text) or "full-auto"

    out = engine._exec_present_plan({"plan": PLAN})

    assert seen == [PLAN], "the user is shown the plan, verbatim"
    assert engine.PERMISSION_MODE == "full-auto"
    assert engine._plan_approved is True
    assert "ordinary tool calls" in out


def test_denial_keeps_plan_mode_so_the_plan_can_be_revised(engine):
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    engine._plan_on_approval = lambda text: ""

    out = engine._exec_present_plan({"plan": PLAN})

    assert engine.PERMISSION_MODE == "plan-only"
    assert engine._plan_approved is False
    assert "revise" in out
```

**Step 2: Run to verify failure**

Expected: FAIL — `has no attribute '_exec_present_plan'`

**Step 3: Implement**

Insert immediately above `def _exec_plan(` in `src/agent8088/engine.py`:

```python
def _exec_present_plan(args: dict) -> str:
    """Show a finished plan and ask the user to approve it.

    This is plan mode's exit point, not an executor. Presentation and execution
    were the same tool before, which forced every approvable plan to be a JSON
    array of fully-specified tool calls — so a plan written the way a human reads
    it halted on its first step, and a model that wrote prose instead had nothing
    approved and nothing run while still sounding finished. Here the plan is
    text, the user picks the mode the work runs in, and the ordinary tool path
    does the work.
    """
    global _plan_approved, _plan_tool_ran
    _plan_tool_ran = True
    plan_text = str(args.get("plan") or args.get("text") or args.get("steps") or "").strip()
    if not plan_text:
        return ("Error: present_plan requires a non-empty 'plan' — the plan itself, "
                "as markdown text the user can read.")
    if PERMISSION_MODE != "plan-only":
        return (f"Error: present_plan only applies in plan mode; this session is in "
                f"{PERMISSION_MODE} mode. Do the work with ordinary tool calls.")
    if not callable(_plan_on_approval):
        return ("Plan not approved: this session has no way to ask the user "
                "(non-interactive). Nothing was written or run. Report the plan "
                "to the user as your answer instead.")
    chosen = _plan_on_approval(plan_text)
    if not chosen:
        return ("Plan not approved — still in plan mode. Revise the plan and call "
                "present_plan again, or answer the user's questions about it. "
                "Nothing has been written or run.")
    set_permission_mode(chosen)
    _plan_approved = True
    return (f"Plan approved. Permission mode is now {chosen}. Carry out the plan now, "
            "in order, with ordinary tool calls, and report what each step actually "
            "did. Do not call present_plan again for this plan.")
```

**Step 4: Run**

Expected: `10 passed`

**Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_plan_command.py
git commit -m "feat: present_plan asks for approval and hands execution to normal tool calls"
```

---

## Task 5: `finish_plan_session` — the mode comes back by itself

**Objective:** When the approved plan's turn ends, restore the pre-`/plan` mode.

**Files:**
- Modify: `src/agent8088/engine.py` (after `cancel_plan_session`)
- Test: `tests/test_plan_command.py`

**Step 1: Write the failing tests**

```python
def test_finishing_an_approved_plan_returns_to_the_pre_plan_mode(engine):
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    engine._plan_on_approval = lambda text: "full-auto"
    engine._exec_present_plan({"plan": PLAN})

    restored = engine.finish_plan_session()

    assert restored == "readonly"
    assert engine.PERMISSION_MODE == "readonly"
    assert engine._plan_return_mode == ""


def test_finishing_does_nothing_while_the_plan_is_still_unapproved(engine):
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()

    assert engine.finish_plan_session() == ""
    assert engine.PERMISSION_MODE == "plan-only", "an unapproved plan stays in plan mode"


def test_plan_tool_ran_is_turn_scoped(engine):
    engine.PERMISSION_MODE = "plan-only"
    engine._plan_on_approval = lambda text: ""
    engine._exec_present_plan({"plan": PLAN})
    assert engine.plan_tool_ran() is True

    engine.reset_turn_counters()

    assert engine.plan_tool_ran() is False
```

**Step 2: Run to verify failure**

Expected: FAIL — `has no attribute 'finish_plan_session'`

**Step 3: Implement**

Add after `cancel_plan_session`:

```python
def finish_plan_session() -> str:
    """Leave plan mode once the approved plan's turn is over.

    Returns the mode restored to, or "" if nothing changed. An unapproved plan
    stays in plan mode: the user asked for a plan and has not agreed to anything,
    so nothing about the session's permissions should have moved."""
    global _plan_return_mode, _plan_approved
    if not _plan_approved:
        return ""
    target = _plan_return_mode or "readonly"
    _plan_approved = False
    _plan_return_mode = ""
    set_permission_mode(target)
    return target


def plan_tool_ran() -> bool:
    """True if a plan tool ran during the current turn. The CLI uses this to tell
    an executed plan apart from a model that only described one."""
    return _plan_tool_ran
```

Then in `reset_turn_counters` ([engine.py:395](src/agent8088/engine.py:395)) add
`_plan_tool_ran` to the reset:

```python
def reset_turn_counters() -> None:
    """Clear the per-turn blast-radius counters. Called by run_agent at the start
    of each turn; exposed so an embedder driving run_tool directly can reset too."""
    global _turn_writes, _plan_tool_ran
    _turn_writes = 0
    _plan_tool_ran = False
```

Finally, set `_plan_tool_ran = True` at the top of `_exec_plan` too. Change its
`global _plan_execution_grant` declaration (currently at
[engine.py:2137](src/agent8088/engine.py:2137)) to be declared at the top of the
function instead, together with the new flag:

```python
def _exec_plan(args: dict, on_step=None, on_escalation=None, depth: int = 0) -> str:
    global _plan_execution_grant, _plan_tool_ran
    _plan_tool_ran = True
    raw = args.get("steps") or args.get("plan") or ""
```

and delete the now-duplicate `global _plan_execution_grant` line further down
(the one above `if PERMISSION_MODE == "plan-only" and on_step and on_escalation:`).

**Step 4: Run**

```bash
PYTHONPATH=src AGENT8088_CONFIG=/nonexistent python3 -m pytest tests/test_plan_command.py tests/test_plan_state_transitions.py tests/test_plan_audit.py -q
```
Expected: `13 passed` in the new file; `test_plan_state_transitions.py` fully
green; `test_plan_audit.py` at its pre-existing 1 failure.

**Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_plan_command.py
git commit -m "feat: an approved plan returns the session to its previous mode"
```

---

## Task 6: Route `present_plan` in `run_tool`

**Objective:** `mode == "plan"` currently always calls `_exec_plan`. Dispatch by name.

**Files:**
- Modify: `src/agent8088/engine.py:3441-3445`
- Test: `tests/test_plan_command.py`

**Step 1: Write the failing test**

```python
def test_run_tool_routes_present_plan_to_the_approval_path(engine):
    engine.PERMISSION_MODE = "plan-only"
    engine._plan_on_approval = lambda text: "full-auto"

    out = engine.run_tool("present_plan", {"plan": PLAN})

    assert "Plan approved" in out
    assert engine.PERMISSION_MODE == "full-auto"
```

**Step 2: Run to verify failure**

Expected: FAIL — the call lands in `_exec_plan`, which treats the markdown as
newline steps and halts on step 1 with a missing-arguments error.

**Step 3: Implement**

Replace [engine.py:3441-3445](src/agent8088/engine.py:3441):

```python
    if mode == "plan":
        if not allow_plan:
            return "Error: Nested plan tool execution is not allowed."
        if name == "present_plan":
            return _exec_present_plan(args)
        return _exec_plan(args, on_step=_plan_on_step,
                          on_escalation=_plan_on_escalation, depth=depth)
```

**Step 4: Run**

Expected: `14 passed`

**Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_plan_command.py
git commit -m "fix: run_tool dispatches present_plan instead of the step executor"
```

---

## Task 7: The block message points at `present_plan`

**Objective:** Fix cause #4 — the message that sent the model into the
"No answer produced" loop (Repro C).

**Files:**
- Modify: `src/agent8088/engine.py:3175-3182` and `:3369-3374`
- Test: `tests/test_plan_command.py`

**Step 1: Write the failing test**

```python
@pytest.mark.parametrize("tool,args", [
    ("write_file", {"filename": "x.txt", "content": "x"}),
    ("execute_shell", {"command": "ls"}),
])
def test_a_blocked_tool_in_plan_mode_points_at_present_plan(engine, tool, args):
    """The old message named execute_plan and a JSON step array — the one thing a
    model reliably gets wrong — so it retried the direct call until the loop died."""
    engine.PERMISSION_MODE = "plan-only"

    out = engine.run_tool(tool, args)

    assert "present_plan" in out
    assert "execute_plan" not in out
```

**Step 2: Run to verify failure**

Expected: FAIL — `assert 'present_plan' in "Error: plan-only mode — direct tool
execution blocked. Call the execute_plan tool with a JSON steps array…"`

**Step 3: Implement**

Add a helper just above `def run_tool(` in `src/agent8088/engine.py`:

```python
def _plan_mode_block_message() -> str:
    """What a model is told when it reaches for a mutation inside plan mode.

    It used to be told to call execute_plan with a JSON array of fully-specified
    tool calls. Models do not reliably produce that, so they re-issued the direct
    call until the loop gave up and the user saw "I wasn't able to produce an
    answer". Naming the one tool that does work, and saying what happens after
    approval, is what makes the block recoverable."""
    return ("Error: plan mode — nothing is written or run until the user approves a "
            "plan. Keep reading if you still need facts. Once you know what to do, "
            "call present_plan(plan=\"...\") with the plan written out as markdown: "
            "the goal, numbered steps, and the files each step touches. The user "
            "approves it, the permission mode changes, and THEN you make this tool "
            "call normally. Do not claim any of it is done before that happens.")
```

Replace the body of both plan-only branches with `return _plan_mode_block_message()`
— at [engine.py:3177](src/agent8088/engine.py:3177) and
[engine.py:3369](src/agent8088/engine.py:3369). Keep the surrounding `if`
conditions exactly as they are.

**Step 4: Run**

```bash
PYTHONPATH=src AGENT8088_CONFIG=/nonexistent python3 -m pytest tests/test_plan_command.py -q
```
Expected: `16 passed`

Then check nothing asserted the old wording:

```bash
grep -rn "direct tool execution blocked" tests/ docs/
```
Fix any hit in `tests/`; note any hit in `docs/` for Task 15.

**Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_plan_command.py
git commit -m "fix: plan-mode block tells the model to present a plan, not to guess JSON steps"
```

---

## Task 8: A step that names no tool never becomes a shell command

**Objective:** Fix cause #5 (Repro F).

**Files:**
- Modify: `src/agent8088/engine.py:1809-1821` and the two `_exec_plan` loops
- Test: `tests/test_plan_command.py`

**Step 1: Write the failing tests**

```python
def test_an_unclassifiable_step_names_no_tool(engine):
    """A tie at score zero used to return whatever tool iterated first, and
    _infer_step_args then handed the step's prose to it as its one argument."""
    assert engine.classify_plan_component("") == ""


def test_prose_steps_do_not_become_shell_commands(engine, tmp_path):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    ran = []
    engine._exec_process = lambda *a, **k: ran.append(a) or "ok"

    out = engine._exec_plan({"steps": "[1, 2, 3]"})

    assert ran == [], "a bare number is not a command to run"
    assert "names no tool" in out
```

**Step 2: Run to verify failure**

Expected: first test FAILs (`assert 'execute_shell' == ''`); second FAILs
because the step is executed as `execute_shell(command="1")`.

**Step 3: Implement**

In `classify_plan_component` ([engine.py:1809](src/agent8088/engine.py:1809)),
replace the final line:

```python
    return best_tool or next(iter(TOOL_NAMES), "")
```

with:

```python
    # No signal means no answer. Guessing here used to pick whichever tool
    # iterated first at score zero, and _infer_step_args then filled its single
    # required argument with the step's own prose — which is how "Delete the old
    # backups" became a literal shell command.
    return best_tool if best_score > 0 else ""
```

Then guard both `_exec_plan` loops. In the **pre-parse** loop
([engine.py:2141-2151](src/agent8088/engine.py:2141)), change the spec lookup so
an empty name renders honestly:

```python
            spec = TOOL_SPECS.get(tool_name, {})
            if spec.get("mode") in ("write_text", "shell", "docker", "cron", "browser"):
                has_gated = True
            pre_parsed.append((idx, step_text, tool_name or "(no tool)"))
```

In the **execution** loop, insert this immediately before the existing
`if tool_name not in TOOL_SPECS:` check ([engine.py:2181](src/agent8088/engine.py:2181)):

```python
        if not tool_name:
            outputs.append(f"[{idx}] Error: this step names no tool: {step_text[:120]!r}. "
                           'Give every step an explicit "tool" and "arguments", or '
                           "write the plan as prose and call present_plan instead.")
            halted, stopped_at = f"step {idx} named no tool", idx
            break
```

**Step 4: Run**

```bash
PYTHONPATH=src AGENT8088_CONFIG=/nonexistent python3 -m pytest tests/test_plan_command.py tests/test_plan_state_transitions.py tests/test_plan_audit.py -q
```
Expected: `18 passed` in the new file; the other two files at their baseline.

**Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_plan_command.py
git commit -m "fix: a plan step with no tool halts instead of guessing a shell command"
```

---

## Task 9: `cmd_plan` enters plan mode and stays

**Objective:** Fix cause #6. `/plan` becomes a mode.

**Files:**
- Modify: `src/agent8088/cli.py:1216-1226`
- Modify: `tests/test_classic_banner.py:123-156` (the old behavior is asserted there)
- Test: `tests/test_plan_command.py`

**Step 1: Write the failing tests**

Append to `tests/test_plan_command.py`:

```python
@pytest.fixture
def captured(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=120, color_system=None))
    return out


def test_slash_plan_with_no_task_just_enters_plan_mode(monkeypatch, captured):
    calls = []
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(cli, "do_chat", lambda task: calls.append(task))

    cli.cmd_plan("")

    assert calls == [], "no task means no model call"
    assert cli.A.PERMISSION_MODE == "plan-only"
    assert "plan mode" in captured.getvalue()


def test_slash_plan_with_a_task_sends_it_in_plan_mode(monkeypatch, captured):
    seen = []
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(cli, "do_chat",
                        lambda task: seen.append((task, cli.A.PERMISSION_MODE)))

    cli.cmd_plan("add a health endpoint")

    assert seen == [("add a health endpoint", "plan-only")]


def test_slash_plan_does_not_restore_the_mode_when_the_turn_ends(monkeypatch, captured):
    """The whole point: plan mode outlives the turn that started it."""
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(cli, "do_chat", lambda task: None)

    cli.cmd_plan("add a health endpoint")

    assert cli.A.PERMISSION_MODE == "plan-only"
```

**Step 2: Run to verify failure**

Expected: the third test FAILs — `cmd_plan`'s `finally` restores `readonly`.

**Step 3: Implement**

Replace `cmd_plan` at [cli.py:1216](src/agent8088/cli.py:1216):

```python
def cmd_plan(rest):
    """Enter plan mode, the way `/plan` works in Claude Code, Hermes and Codex.

    A mode, not a one-shot: it used to flip to plan-only for exactly one message
    and restore the old mode in a finally, so there was no state in which a plan
    could be reviewed, approved and then run. Now the mode holds until a plan is
    approved (see A.finish_plan_session) or the user changes it by hand."""
    A.enter_plan_mode()
    console.print("[bold #00edff]plan mode[/bold #00edff] — reads only. Agent8088 will "
                  "research, propose a plan, and wait for your approval before "
                  "anything is written or run.")
    task = rest.strip()
    if task:
        do_chat(task)
```

Then replace the three obsolete tests at
[tests/test_classic_banner.py:123-156](tests/test_classic_banner.py:123) —
`test_plan_runs_task_in_temporary_plan_only_mode`,
`test_plan_restores_mode_when_chat_fails`, and
`test_plan_requires_a_natural_language_task` — with a single pointer, since the
behavior now lives in the new file:

```python
def test_plan_command_is_covered_by_the_plan_mode_suite():
    """`/plan` is a mode now, not a one-shot wrapper: see tests/test_plan_command.py.
    Kept as a signpost so the deletion of the old one-shot tests is deliberate."""
    assert classic.cmd_plan.__doc__ and "plan mode" in classic.cmd_plan.__doc__
```

**Step 4: Run**

```bash
PYTHONPATH=src AGENT8088_CONFIG=/nonexistent python3 -m pytest tests/test_plan_command.py tests/test_classic_banner.py -q
```
Expected: `21 passed` in the new file; `test_classic_banner.py` at its
pre-existing 1 failure (the ASCII-logo one).

**Step 5: Commit**

```bash
git add src/agent8088/cli.py tests/test_plan_command.py tests/test_classic_banner.py
git commit -m "feat: /plan enters plan mode and stays until the plan is approved"
```

---

## Task 10: `/mode plan-only` is the same door as `/plan`

**Objective:** Both entrances start a real plan session; leaving by hand cancels it.

**Files:**
- Modify: `src/agent8088/cli.py:1644-1661` (`cmd_mode`)
- Test: `tests/test_plan_command.py`

**Step 1: Write the failing tests**

```python
def test_mode_plan_only_starts_the_same_plan_session_as_slash_plan(monkeypatch, captured):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "full-auto")

    cli.cmd_mode("plan-only")

    assert cli.A.PERMISSION_MODE == "plan-only"
    assert cli.A._plan_return_mode == "full-auto"


def test_leaving_plan_mode_by_hand_cancels_the_pending_plan(monkeypatch, captured):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    cli.cmd_mode("plan-only")

    cli.cmd_mode("full-auto")

    assert cli.A.PERMISSION_MODE == "full-auto"
    assert cli.A._plan_return_mode == "", "no plan is pending, so nothing to return to"
```

**Step 2: Run to verify failure**

Expected: first test FAILs — `cmd_mode` assigns `A.PERMISSION_MODE` directly and
never records a return mode.

**Step 3: Implement**

Replace the tail of `cmd_mode` ([cli.py:1658-1661](src/agent8088/cli.py:1658)):

```python
    A.PERMISSION_MODE = arg
    # Clear plan execution grant when leaving plan-only mode
    A._plan_execution_grant = False
    console.print(f"Permission mode: [bold green]{arg}[/bold green]")
```

with:

```python
    # `/mode plan-only` and `/plan` are the same door: both start a plan session
    # that knows where to return. Leaving by hand abandons it, so a plan the user
    # walked away from cannot restore a mode later.
    if arg == "plan-only":
        A.enter_plan_mode()
    else:
        A.cancel_plan_session()
        A.set_permission_mode(arg)
    console.print(f"Permission mode: [bold green]{arg}[/bold green]")
```

**Step 4: Run**

Expected: `23 passed`

**Step 5: Commit**

```bash
git add src/agent8088/cli.py tests/test_plan_command.py
git commit -m "feat: /mode plan-only starts a plan session like /plan"
```

---

## Task 11: The approval prompt in the CLI

**Objective:** Wire `_plan_on_approval` so the plan renders as markdown and the
user picks the destination mode.

**Files:**
- Modify: `src/agent8088/cli.py:799-840` (inside `do_chat`)
- Test: `tests/test_plan_command.py`

**Step 1: Confirm `Markdown` is imported**

```bash
grep -n "from rich.markdown import Markdown" src/agent8088/cli.py
```
If absent, add it beside the other `rich` imports at the top of the file.

**Step 2: Write the failing test**

```python
@pytest.mark.parametrize("answer,expected", [
    ("a", "full-auto"),
    ("e", "readonly"),
    ("d", ""),
    ("", ""),
])
def test_the_approval_prompt_maps_answers_to_destination_modes(
        monkeypatch, captured, answer, expected):
    monkeypatch.setattr(cli.console, "input", lambda *a, **k: answer)

    approve = cli._make_plan_approval(live=None)

    assert approve("## Goal\nDo the thing\n\n1. Write a.txt") == expected
    assert "Do the thing" in captured.getvalue(), "the user sees the plan"
```

**Step 3: Run to verify failure**

Expected: FAIL — `has no attribute '_make_plan_approval'`

**Step 4: Implement**

Add a module-level factory next to `_handle_escalation` in `src/agent8088/cli.py`
(above `def do_chat`), so it is testable without a live turn:

```python
def _make_plan_approval(live=None):
    """Build the callback present_plan uses to show a plan and get a decision.

    Returns the permission mode the approved work should run in, or "" to stay in
    plan mode. Mirrors Claude Code's exit-plan choice: approving a plan picks the
    mode it executes in rather than granting one blanket step."""
    def approve(plan_text):
        if live is not None:
            live.stop()
        console.print()
        console.print(Panel(Markdown(plan_text), title="[bold #00edff]Plan[/bold #00edff]",
                            box=box.ROUNDED, border_style="#00C8FF"))
        try:
            answer = console.input(
                "[bold yellow]Approve plan? (a=approve and run / "
                "e=approve, ask before each edit / d=keep planning): [/bold yellow]"
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "d"
        if live is not None:
            live.start()
        if answer in ("a", "approve", "y", "yes"):
            console.print("[green]Plan approved — running it now.[/green]")
            return "full-auto"
        if answer in ("e", "edit", "edits", "r", "readonly"):
            console.print("[green]Plan approved — each write will ask first.[/green]")
            return "readonly"
        console.print("[yellow]Still in plan mode. Nothing was written or run — "
                      "say what to change and Agent8088 will revise the plan.[/yellow]")
        return ""
    return approve
```

Then wire it inside `do_chat`, next to the existing plan callbacks
([cli.py:821](src/agent8088/cli.py:821)):

```python
        A._plan_on_step = _plan_on_step
        A._plan_on_escalation = _plan_on_escalation
        A._plan_on_approval = _make_plan_approval(live)
```

and clear it in the same `finally` ([cli.py:838-840](src/agent8088/cli.py:838)):

```python
        finally:
            A.subagent_ui = None
            A._plan_on_step = None
            A._plan_on_escalation = None
            A._plan_on_approval = None
```

**Step 5: Run**

Expected: `27 passed`

**Step 6: Commit**

```bash
git add src/agent8088/cli.py tests/test_plan_command.py
git commit -m "feat: the plan approval prompt renders the plan and picks the run mode"
```

---

## Task 12: End-of-turn bookkeeping kills the fabricated success

**Objective:** Fix causes #2 and #3 — restore the mode after an approved plan,
and say plainly when a turn ended in plan mode with no plan approved (Repro B).

**Files:**
- Modify: `src/agent8088/cli.py:842-866` (`do_chat` tail, both exit paths)
- Test: `tests/test_plan_command.py`

**Step 1: Write the failing tests**

```python
def test_an_approved_plan_reports_the_mode_coming_back(monkeypatch, captured):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    cli.A.enter_plan_mode()
    monkeypatch.setattr(cli.A, "_plan_on_approval", lambda text: "full-auto")
    cli.A._exec_present_plan({"plan": "1. write a.txt"})

    cli._after_turn_plan_state()

    assert cli.A.PERMISSION_MODE == "readonly"
    assert "back to readonly" in captured.getvalue()


def test_a_turn_that_planned_nothing_says_nothing_happened(monkeypatch, captured):
    """Repro B: the model described a plan, ran nothing, then reported it done."""
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    cli.A.enter_plan_mode()
    cli.A.reset_turn_counters()

    cli._after_turn_plan_state()

    assert cli.A.PERMISSION_MODE == "plan-only"
    rendered = captured.getvalue()
    assert "nothing above was written or run" in rendered


def test_a_turn_that_presented_a_plan_does_not_warn(monkeypatch, captured):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    cli.A.enter_plan_mode()
    monkeypatch.setattr(cli.A, "_plan_on_approval", lambda text: "")
    cli.A._exec_present_plan({"plan": "1. write a.txt"})

    cli._after_turn_plan_state()

    assert "nothing above was written or run" not in captured.getvalue()
```

**Step 2: Run to verify failure**

Expected: FAIL — `has no attribute '_after_turn_plan_state'`

**Step 3: Implement**

Add above `def do_chat(` in `src/agent8088/cli.py`:

```python
def _after_turn_plan_state():
    """Close out the turn's plan state.

    Two jobs. An approved plan has now run, so the session goes back to the mode
    it had before /plan. And a turn that ended in plan mode without a plan being
    approved gets said out loud: a model that writes a plan as prose and then
    reports it complete is indistinguishable, in the transcript, from one that
    actually did the work — the only difference the user can see is this line."""
    restored = A.finish_plan_session()
    if restored:
        console.print(f"[dim]plan complete · permission mode back to {restored}[/dim]")
        return
    if A.PERMISSION_MODE == "plan-only" and not A.plan_tool_ran():
        console.print("[yellow]Still in plan mode — no plan was approved, so nothing "
                      "above was written or run. Reply to refine the plan, or leave "
                      "plan mode with /mode full-auto.[/yellow]")
```

Call it on **both** exit paths of `do_chat`. In the interrupted branch
([cli.py:843-851](src/agent8088/cli.py:843)), immediately before `return`:

```python
        _record_trace(query, trace, elapsed, interrupted=True)
        _after_turn_plan_state()
        _save_active_session()
        return
```

and on the normal path, immediately before the final `_save_active_session()`
([cli.py:866](src/agent8088/cli.py:866)):

```python
    _after_turn_plan_state()
    _save_active_session()
```

**Step 4: Run**

Expected: `30 passed`

**Step 5: Commit**

```bash
git add src/agent8088/cli.py tests/test_plan_command.py
git commit -m "fix: a plan that was never approved is reported instead of assumed done"
```

---

## Task 13: Room to execute an approved plan in one turn

**Objective:** Approval happens mid-turn, and the plan's steps then have to fit
in the turns that are left. `S.max_turns` defaults to 10, which a
research + plan + multi-step execution turn will hit.

**Files:**
- Modify: `src/agent8088/cli.py` (the `A.run_agent(...)` call at line ~825)
- Test: `tests/test_plan_command.py`

**Step 1: Write the failing test**

```python
def test_a_plan_mode_turn_gets_room_to_research_plan_and_execute(engine):
    """Approval lands mid-turn, so the plan's steps spend the same turn budget the
    research already spent. 10 rounds is not enough for both."""
    assert cli._turn_max_turns("readonly") == cli.S.max_turns
    assert cli._turn_max_turns("plan-only") >= 25
```

**Step 2: Run to verify failure**

Expected: FAIL — `has no attribute '_turn_max_turns'`

**Step 3: Implement**

Add above `def do_chat(` in `src/agent8088/cli.py`:

```python
PLAN_MODE_MIN_TURNS = 25


def _turn_max_turns(mode):
    """Round budget for this turn. A plan-mode turn does three things in one turn —
    research, propose, then execute everything the user approved — so it needs more
    rounds than a normal exchange. The alternative, raising the cap mid-turn when
    the approval lands, means reaching into the agent loop; this stays outside it."""
    if mode == "plan-only":
        return max(S.max_turns, PLAN_MODE_MIN_TURNS)
    return S.max_turns
```

Then change the `run_agent` call at [cli.py:826](src/agent8088/cli.py:826):

```python
            answer = A.run_agent(
                S.messages, max_turns=_turn_max_turns(A.PERMISSION_MODE),
                temperature=S.temperature,
```

**Step 4: Run**

Expected: `31 passed`

**Step 5: Commit**

```bash
git add src/agent8088/cli.py tests/test_plan_command.py
git commit -m "feat: a plan-mode turn gets enough rounds to research, plan and execute"
```

---

## Task 14: Surface plan mode in the UI and point `/model plan-only` at `/mode`

**Objective:** The user cannot see which mode they are in, and typing
`/model plan-only` (what actually happened) says only "unknown provider".

**Files:**
- Modify: `src/agent8088/cli.py` — `_prompt_label` (line 2090), `_read_line`
  label (line ~2131), `cmd_model` else-branch, help table (line 899), module
  docstring (line 12)
- Test: `tests/test_plan_command.py`

**Step 1: Write the failing tests**

```python
def test_the_prompt_shows_when_you_are_in_plan_mode(monkeypatch):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "plan-only")
    assert "plan" in cli._prompt_label()

    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    assert "plan" not in cli._prompt_label()


def test_model_plan_only_points_at_the_mode_command(monkeypatch, captured):
    cli.cmd_model("plan-only")

    rendered = captured.getvalue()
    assert "/plan" in rendered or "/mode plan-only" in rendered
```

**Step 2: Run to verify failure**

Expected: both FAIL.

**Step 3: Implement**

`_prompt_label` ([cli.py:2090](src/agent8088/cli.py:2090)):

```python
def _prompt_label():
    pct = _estimate_context_pct()
    mode = " [bold #00edff]plan[/bold #00edff]" if A.PERMISSION_MODE == "plan-only" else ""
    return (f"\n[bold #237dd7]8088[/bold #237dd7]{mode} "
            f"[#237dd7]({pct}% ctx) ›[/#237dd7] ")
```

The prompt_toolkit label in `_read_line` ([cli.py:2130](src/agent8088/cli.py:2130))
builds its own ANSI string — give it the same indicator:

```python
    pct = _estimate_context_pct()
    mode = " \x1b[1;38;2;0;237;255mplan\x1b[0m" if A.PERMISSION_MODE == "plan-only" else ""
    label = (f"\n\x1b[1;38;2;35;125;215m8088\x1b[0m{mode} "
             f"\x1b[38;2;35;125;215m({pct}% ctx) ›\x1b[0m ")
```

`cmd_model`'s unknown-provider branch — add the hint before `return`:

```python
    else:
        console.print(f"[red]unknown provider[/red] '{arg}' — known: "
                      + (", ".join(sorted(A.PROVIDERS)) or "(none configured)"))
        # Permission modes are not providers. `/model plan-only` is a common
        # mix-up and used to dead-end here with no route to the real command.
        if arg in ("plan-only", "plan", "readonly", "full-auto", "edit"):
            console.print(f"[dim]'{arg}' is a permission mode, not a provider — "
                          f"use [/dim][#237dd7]/mode {arg}[/#237dd7]"
                          f"[dim], or [/dim][#237dd7]/plan[/#237dd7][dim] for plan mode.[/dim]")
        return
```

Help table row ([cli.py:899](src/agent8088/cli.py:899)):

```python
        ("/plan [task]", "Enter plan mode — propose a plan, approve it, then it runs"),
```

Module docstring line 12:

```python
  • /plan           — enter plan mode: propose a plan, approve it, then it runs.
```

**Step 4: Run**

Expected: `34 passed`

**Step 5: Commit**

```bash
git add src/agent8088/cli.py tests/test_plan_command.py
git commit -m "feat: show plan mode in the prompt and route /model plan-only to /mode"
```

---

## Task 15: Teach the model the new contract

**Objective:** `system.md` still instructs the model to emit JSON steps — the
behavior that produced Repros B, D and E.

**Files:**
- Modify: `src/agent8088/system.md:80-91`
- Test: `tests/test_plan_command.py`

**Step 1: Write the failing test**

```python
def test_the_system_prompt_teaches_present_plan_not_json_steps(engine):
    prompt = engine.SYSTEM_PROMPT

    assert "present_plan" in prompt
    assert "## Plan Mode" in prompt
```

**Step 2: Run to verify failure**

Expected: FAIL — the prompt still says `## Plan-Only Mode` and names `execute_plan`.

**Step 3: Implement**

Replace the `## Plan-Only Mode` section in `src/agent8088/system.md` with:

```markdown
## Plan Mode

When the permission mode is plan-only, the user has asked for a plan, not for work.

- Reads are allowed and encouraged: `read_text`, safe shell (`ls`, `cat`, `grep`,
  `git status`, `git diff`, `git log`), `web_search`. Use them to find out what is
  actually there before you plan anything.
- Every write and mutation is blocked. It will stay blocked until the user approves
  a plan. There is no way around this and no point trying one.
- When you know what to do, call `present_plan` **once**, with the whole plan as
  markdown in the `plan` argument: the goal, numbered steps, and the files each
  step touches. Write it for a person to read, not as JSON.
- The user approves or declines. On approval the permission mode changes and the
  tool result says so — then carry out the steps with **ordinary tool calls**, in
  order, and report what each one actually did. On a decline, you are still in plan
  mode: revise the plan or answer their questions. Nothing has been written.
- Never state or imply that a plan has been carried out before you have made the
  tool calls and seen them succeed. A plan you described is not a plan you ran.
- `execute_plan` still exists for running a fully-specified sequence of tool calls
  with per-step verification. It is not how you propose a plan — `present_plan` is.
```

Then update the CLI's plan-only guidance block at
[cli.py:322-328](src/agent8088/cli.py:322), which repeats the old instruction:

```python
    if A.PERMISSION_MODE == "plan-only":
        prompt += ("You are in plan-only mode RIGHT NOW. Direct writes and mutations "
                   "are blocked until the user approves a plan. Use read-only tools "
                   "(read_text, safe shell such as ls/cat/grep, git status, git diff, "
                   "git log) to find out what is really there, then call present_plan "
                   "with the whole plan as markdown text for the user to approve. Do "
                   "not claim any of it is done before the approval lands and your "
                   "tool calls succeed.\n")
```

**Step 4: Run**

```bash
PYTHONPATH=src AGENT8088_CONFIG=/nonexistent python3 -m pytest tests/test_plan_command.py -q
```
Expected: `35 passed`

**Step 5: Commit**

```bash
git add src/agent8088/system.md src/agent8088/cli.py tests/test_plan_command.py
git commit -m "docs: system prompt teaches present_plan and forbids claiming unrun work"
```

---

## Task 16: Full suite and live verification

**Objective:** Prove it, against tests and against the real backend.

**Step 1: Full unit suite**

```bash
PYTHONPATH=src AGENT8088_CONFIG=/nonexistent python3 -m pytest tests/ -q \
  --ignore=tests/test_mcp.py --ignore=tests/test_mcp_server.py --ignore=tests/gateway 2>&1 | tail -5
```
Expected: `2 failed, 703 passed` — the same two pre-existing Windows-only
failures from Task 0 and nothing else. Any third failure is yours; fix it.

**Step 2: Functional verification**

```bash
VERIFY_HOME="$(mktemp -d)"; trap 'rm -rf -- "$VERIFY_HOME"' EXIT
PYTHONPATH=src AGENT8088_CONFIG=/nonexistent AGENT8088_HOME="$VERIFY_HOME" \
  python3 scripts/verify_features.py
```
Expected: exit 0. `⊘ SKIP` lines for unavailable backends are fine.

**Step 3: Live repro of Repro B — the fabricated success**

Use an isolated config and workspace so nothing real is touched:

```bash
Export the LAN backend key first (`export ORNITH_API_KEY=…`) — it is deliberately
not written into this plan file, which lives in the repo.

```bash
WS="$(mktemp -d)"; CFG="$WS/config.txt"
cat > "$CFG" <<EOF
allowed_paths=.
default_provider=ornith
provider.ornith.base_url=http://192.168.3.67:8080/v1
provider.ornith.api_key=${ORNITH_API_KEY}
provider.ornith.model=ornith-1.0-35b
EOF
mkdir -p "$WS/run" && cd "$WS/run" && printf '/plan create three files a.txt b.txt c.txt each containing its own letter, then read them back and summarize\na\n/exit\n' | AGENT8088_CONFIG="$CFG" AGENT8088_HOME="$WS/home" AGENT8088_SANDBOX=local PYTHONPATH=/Users/tahawaheed/Documents/Agent8088-Features-added/.claude/worktrees/plan-command-impl-78bc6d/src python3 -m agent8088.cli
```

Expected, in order: `plan mode — reads only`; one `Plan` panel containing
readable markdown; one `Approve plan?` prompt; `Plan approved — running it now.`;
three `write_file` results; `plan complete · permission mode back to readonly`.
Then confirm the files really exist — this is the assertion that Repro B is dead:

```bash
ls "$WS/run"
```
Expected: `a.txt  b.txt  c.txt`.

**Step 4: Live repro of Repro C — the second turn**

```bash
cd "$WS/run" && printf '/plan\nnow add d.txt containing the letter d\na\nand now add e.txt containing e\na\n/exit\n' | AGENT8088_CONFIG="$CFG" AGENT8088_HOME="$WS/home" AGENT8088_SANDBOX=local PYTHONPATH=/Users/tahawaheed/Documents/Agent8088-Features-added/.claude/worktrees/plan-command-impl-78bc6d/src python3 -m agent8088.cli
```

Expected: `d.txt` is planned, approved and written; the mode returns to
`readonly`; the second request runs as a normal readonly turn — **no**
`No answer produced` and **no** `I wasn't able to produce an answer`. Grep to be
sure:

```bash
ls "$WS/run"   # expect d.txt present
```

**Step 5: Live repro of decline**

```bash
cd "$WS/run" && printf '/plan delete every txt file here\nd\n/exit\n' | AGENT8088_CONFIG="$CFG" AGENT8088_HOME="$WS/home" AGENT8088_SANDBOX=local PYTHONPATH=/Users/tahawaheed/Documents/Agent8088-Features-added/.claude/worktrees/plan-command-impl-78bc6d/src python3 -m agent8088.cli
ls "$WS/run"
```
Expected: `Still in plan mode`, every `.txt` still present, nothing deleted.

**Step 6: Clean up and commit**

```bash
rm -rf "$WS"
git add -A && git commit -m "test: verified plan mode end to end against the live backend"
```

---

## Task 17: Documentation

**Objective:** The docs describe `/plan` as a step executor. Fix every hit.

**Files:**
- `README.md:176` — `| `/plan <steps>` | Run the plan-executor (multi-step) |`
  → `| `/plan [task]` | Enter plan mode: propose a plan, approve it, then it runs |`
- `docs/wiki/10-cli-reference.md:83` — same change; add a `present_plan` row to
  the tool table if one exists there.
- `docs/wiki/13-troubleshooting.md:105` — rewrite the plan-only entry: the model
  now calls `present_plan`, and the fix for "blocked in plan mode" is to approve
  a plan or `/mode full-auto`, not to hand-write `execute_plan` JSON.
- `docs/FEATURES_ADDED.md:621` — same wording as README.
- `CHANGELOG.md` — new entry at the top:

```markdown
### Changed
- `/plan` is now plan mode, not a one-shot wrapper: it enters a read-only planning
  mode that holds across turns, and `/mode plan-only` starts the same session.
  Approving a plan switches the mode, runs the plan with ordinary tool calls, and
  returns the session to the mode it had before `/plan`.

### Added
- `present_plan` tool: presents a plan as markdown for approval. Plan proposal and
  plan execution used to be the same tool, which meant only a fully-specified JSON
  step array could be approved.

### Fixed
- A plan the model only described is no longer reported as done. A turn that ends in
  plan mode with nothing approved now says so.
- Plan mode is no longer one-shot: after an approved plan finished, every later
  mutation was hard-blocked with a message that sent the model into a retry loop
  ending in "I wasn't able to produce an answer".
- A plan step that names no tool halts with an explicit error instead of being
  classified into an arbitrary tool and handed its own prose as the argument —
  which could route plan prose to a shell as a literal command.
```

**Verify no stale wording is left:**

```bash
grep -rn "plan-executor\|plan executor\|/plan <steps>" README.md docs/ CHANGELOG.md
grep -rn "direct tool execution blocked" README.md docs/
```
Expected: no hits (historical CHANGELOG entries under older version headings stay).

**Commit:**

```bash
git add README.md docs/ CHANGELOG.md
git commit -m "docs: /plan is plan mode"
```

---

## Validation Summary

| Check | Command | Expected |
|---|---|---|
| New suite | `PYTHONPATH=src AGENT8088_CONFIG=/nonexistent python3 -m pytest tests/test_plan_command.py -q` | 35 passed |
| Plan regressions | `… pytest tests/test_plan_state_transitions.py tests/test_plan_audit.py tests/test_classic_banner.py -q` | 2 failed (pre-existing, Windows-only), rest passed |
| Full suite | `… pytest tests/ -q --ignore=tests/test_mcp.py --ignore=tests/test_mcp_server.py --ignore=tests/gateway` | 2 failed, 703 passed |
| Functional | `python3 scripts/verify_features.py` (isolated `AGENT8088_HOME`) | exit 0 |
| Duplicate defs | `python3 scripts/check_duplicate_defs.py` | clean |
| Lint | `python3 -m ruff check src/ tests/` | clean |
| Live: plan runs | Task 16 Step 3 | `a.txt b.txt c.txt` exist; mode back to `readonly` |
| Live: no dead second turn | Task 16 Step 4 | no `No answer produced` |
| Live: decline is safe | Task 16 Step 5 | nothing deleted, still in plan mode |

---

## Risks and Tradeoffs

**The model may not call `present_plan`.** Repro B shows this model will happily
emit prose and stop. Task 12's warning line makes that visible rather than
silently wrong, and Task 15 tells the model directly. It does not *force* a
call. If it turns out the model still skips the tool often, the follow-up is a
preflight nudge in `_run_agent_loop`: when a turn ends in plan mode with
`plan_tool_ran() == False` and the answer reads like a plan, re-prompt once with
"call present_plan with that plan." Deliberately out of scope here — it is a new
loop behavior and should be measured before being added.

**`max_turns` is raised for every plan-mode turn** (Task 13), including turns
that only research. That spends more of the round budget than a normal turn. The
alternative — raising the cap mid-turn when approval lands — means converting
`for turn in range(max_turns)` at [engine.py:4561](src/agent8088/engine.py:4561)
into a `while` with a mutable cap, inside the main agent loop. Not worth the
blast radius for this change. `MAX_TURN_SECONDS` / `MAX_TURN_TOKENS` /
`MAX_TURN_COST_USD` still bound the turn, so the ceiling is not unbounded.

**Approving with `a` gives full-auto for the rest of the turn.** That is exactly
Claude Code's "start auto mode", and the `e` option exists for anyone who wants
per-edit prompts. It is a real widening of what one keypress authorizes, so the
mode reverts as soon as the turn ends (Task 12) rather than persisting.

**Restoring the mode after the turn is a deliberate divergence** from Claude
Code, which stays in the selected mode. It is what the user asked for ("once
that task is done the plan mode is disabled and default chat is back"). If it
turns out to be annoying in practice, `finish_plan_session` is the single place
to change.

**`execute_plan` keeps working unchanged**, so `tests/test_plan_audit.py` and
`tests/test_plan_state_transitions.py` — 231 lines of verification-and-revert
behavior — stay green. The only changes touching it are the `_plan_tool_ran`
flag and the no-tool guard.

**Windows:** no path or process handling changes. The two failing tests are
Windows-only assertions failing on macOS and are unrelated.

---

## Open Questions

1. **Should `/plan` with no task print anything more than the mode banner?**
   Claude Code just shows a mode indicator. This plan prints one line plus the
   prompt indicator. Easy to trim.
2. **Should the approved plan text be saved to disk?** Claude Code's
   `ExitPlanMode` has an optional `filePath`. Agent8088 has `.claude/plans/`
   conventions in this repo but no engine support. Not in scope; would be a small
   follow-up (write the plan to `.claude/plans/<timestamp>-<slug>.md` on
   approval, and report the path).
3. **Does the gateway (Slack/WhatsApp) need plan mode?** `_plan_on_approval`
   is `None` there, so `present_plan` returns "no way to ask the user" and the
   model reports the plan as its answer — a safe default, but the gateway could
   grow an approval reaction. Out of scope.
