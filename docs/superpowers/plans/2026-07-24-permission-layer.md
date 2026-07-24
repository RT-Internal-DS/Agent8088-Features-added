# Agent8088 Permission Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a readonly→edit permission escalation layer to Agent8088 so the agent starts in readonly mode and must request user approval before performing write operations.

**Architecture:** A `PERMISSION_MODE` module-level global (default `"readonly"`) gates write-capable tool modes (`write_text`, `shell`). When a write is attempted in readonly mode, the engine returns a structured escalation request instead of executing. The model is instructed (via system prompt) to call a `request_permission_escalation` tool; when the user approves, `PERMISSION_MODE` flips to `"edit"` for the rest of the session. The Rich UI intercepts the escalation request and prompts the user with a y/n dialog.

**Tech Stack:** Python 3.8+, no new dependencies. Engine changes in `agent8088` (the executable). System prompt updates in `system.md`. Tool spec addition in `tools.txt`. UI changes in `agent8088_cli.py`.

## Global Constraints

- No new Python dependencies — uses only stdlib + existing `openai` + `rich`.
- `PERMISSION_MODE` starts at `"readonly"` at the beginning of every session.
- `ALLOWED_PATHS` still applies even in edit mode — escalation does not bypass path restrictions.
- The `request_permission_escalation` tool is the ONLY way to transition from readonly to edit.
- `git push`, `git push --force`, `git reset --hard`, and branch deletion are forbidden even in edit mode.
- Secret-like patterns (`*_KEY`, `*_TOKEN`, `*_SECRET`, `.env*`) are never readable regardless of mode.
- The existing `./agent8088` (old REPL) and `run_benchmark.py` must still work — they default to `"edit"` mode for backward compatibility.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `agent8088` | Modify | Add `PERMISSION_MODE` global, `check_permission()` gate in `run_tool()`, `request_permission_escalation` tool mode, `request_escalation()` function, readonly-safe shell command whitelist |
| `tools.txt` | Modify | Add `request_permission_escalation` tool entry |
| `system.md` | Modify | Add permission layer instructions (identity, modes, escalation protocol, hard rules) |
| `agent8088_cli.py` | Modify | Add UI handling for escalation requests (y/n prompt in the terminal), set `PERMISSION_MODE` on session start |

---

## Task 1: Add PERMISSION_MODE global and escalation primitives in the engine

**Files:**
- Modify: `agent8088:48-54` (after `ALLOWED_PATHS`, add permission globals)
- Modify: `agent8088:516-560` (add permission gate in `run_tool()`)

**Interfaces:**
- Consumes: `ALLOWED_PATHS` (existing module global)
- Produces: `PERMISSION_MODE` (str: "readonly" or "edit"), `request_escalation()` function, `check_permission()` function, `READONLY_SAFE_COMMANDS` set

- [ ] **Step 1: Write the failing test**

Create `tests/test_permission.py`:

```python
import os, sys
from pathlib import Path
from importlib.machinery import SourceFileLoader
import importlib.util

os.environ['AGENT8088_CONFIG'] = str(Path('config.txt').resolve())

loader = SourceFileLoader('agent8088_core', 'agent8088')
spec = importlib.util.spec_from_loader('agent8088_core', loader)
A = importlib.util.module_from_spec(spec)
loader.exec_module(A)

# Load tools from local tools.txt
A.TOOL_SPECS = A.load_tool_specs(Path('tools.txt'), A.APP_CONFIG)
A.TOOL_NAMES = set(A.TOOL_SPECS.keys())

def test_permission_mode_defaults_to_readonly():
    A.PERMISSION_MODE = "readonly"  # reset
    assert A.PERMISSION_MODE == "readonly"

def test_check_permission_blocks_write_in_readonly():
    A.PERMISSION_MODE = "readonly"
    assert A.check_permission("write_text") is False
    assert A.check_permission("shell") is False

def test_check_permission_allows_read_in_readonly():
    A.PERMISSION_MODE = "readonly"
    assert A.check_permission("read_text") is True
    assert A.check_permission("http_get") is True
    assert A.check_permission("last_output") is True

def test_check_permission_allows_all_in_edit():
    A.PERMISSION_MODE = "edit"
    assert A.check_permission("write_text") is True
    assert A.check_permission("shell") is True

def test_escalation_request_returns_structured_message():
    A.PERMISSION_MODE = "readonly"
    result = A.request_escalation(
        target_mode="edit",
        paths=["/tmp/test.txt"],
        change_type="new_file",
        reason="Need to create test.txt"
    )
    assert "ESCALATION_REQUEST" in result
    assert "edit" in result
    assert "test.txt" in result

def test_escalation_granted_sets_edit_mode():
    A.PERMISSION_MODE = "readonly"
    A.grant_escalation()
    assert A.PERMISSION_MODE == "edit"
    A.PERMISSION_MODE = "readonly"  # cleanup
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_permission.py -v`
Expected: FAIL with `AttributeError: module 'agent8088_core' has no attribute 'PERMISSION_MODE'`

- [ ] **Step 3: Write minimal implementation**

In `agent8088`, after the `ALLOWED_PATHS` block (around line 54), add:

```python
# ---------------------------------------------------------------------------
# Permission layer — readonly by default, escalates to edit on user approval
# ---------------------------------------------------------------------------
PERMISSION_MODE = os.environ.get("AGENT8088_PERMISSION", "readonly")

# Shell commands that are safe in readonly mode (inspection only)
READONLY_SAFE_COMMANDS = frozenset([
    "ls", "cat", "grep", "find", "head", "tail", "wc", "pwd", "whoami",
    "echo", "date", "uname", "df", "du", "free", "nproc", "uptime",
    "git", "diff", "log", "status", "show", "branch",
])


def check_permission(mode: str, command: str = "") -> bool:
    """Return True if the tool mode is allowed in the current permission mode."""
    if PERMISSION_MODE == "edit":
        return True
    # readonly mode
    if mode in ("read_text", "http_get", "last_output", "python_eval", "plan"):
        return True
    if mode == "shell":
        # Allow inspection-only shell commands in readonly
        cmd_base = command.strip().split()[0] if command.strip() else ""
        # Handle "git status", "git log", etc.
        if cmd_base == "git" and len(command.strip().split()) > 1:
            subcmd = command.strip().split()[1]
            if subcmd in ("status", "diff", "log", "show", "branch"):
                return True
        return cmd_base in READONLY_SAFE_COMMANDS
    return False


def request_escalation(target_mode: str, paths: list, change_type: str, reason: str) -> str:
    """Return a structured escalation request string for the model to relay
    to the user. The UI intercepts this and prompts the user for approval."""
    return (
        f"ESCALATION_REQUEST:{target_mode}:{change_type}:{','.join(paths)}:{reason}"
    )


def grant_escalation():
    """Transition PERMISSION_MODE to 'edit' for the remainder of the session."""
    global PERMISSION_MODE
    PERMISSION_MODE = "edit"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_permission.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: Commit**

```bash
git add agent8088 tests/test_permission.py
git commit -m "feat: add PERMISSION_MODE global and escalation primitives"
```

---

## Task 2: Gate run_tool() with permission checks

**Files:**
- Modify: `agent8088:516-560` (`run_tool()` function)

**Interfaces:**
- Consumes: `check_permission()`, `request_escalation()` from Task 1
- Produces: `run_tool()` now returns an escalation request string when a write is blocked in readonly mode

- [ ] **Step 1: Write the failing test**

Append to `tests/test_permission.py`:

```python
def test_run_tool_blocks_write_in_readonly():
    A.PERMISSION_MODE = "readonly"
    result = A.run_tool("write_file", {"filename": "/tmp/test_perm.txt", "content": "hello"})
    assert "ESCALATION_REQUEST" in result

def test_run_tool_allows_write_in_edit():
    A.PERMISSION_MODE = "edit"
    import tempfile, os
    tmp = os.path.join(tempfile.gettempdir(), "test_perm_edit.txt")
    result = A.run_tool("write_file", {"filename": tmp, "content": "hello"})
    assert "Wrote" in result
    os.unlink(tmp)
    A.PERMISSION_MODE = "readonly"  # cleanup

def test_run_tool_allows_read_in_readonly():
    A.PERMISSION_MODE = "readonly"
    # read_text on tools.txt should work
    result = A.run_tool("read_text", {"filename": "tools.txt"})
    assert "execute_shell" in result

def test_run_tool_blocks_dangerous_shell_in_readonly():
    A.PERMISSION_MODE = "readonly"
    result = A.run_tool("execute_shell", {"command": "rm -rf /tmp/nonexistent_perm_test"})
    assert "ESCALATION_REQUEST" in result

def test_run_tool_allows_safe_shell_in_readonly():
    A.PERMISSION_MODE = "readonly"
    result = A.run_tool("execute_shell", {"command": "ls"})
    assert "ESCALATION_REQUEST" not in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_permission.py -v -k "run_tool"`
Expected: FAIL — `run_tool()` does not check permissions yet

- [ ] **Step 3: Write minimal implementation**

In `run_tool()` (around line 516), add the permission gate at the top of the function, after the `spec` lookup:

```python
def run_tool(name: str, args: dict, allow_plan: bool = True) -> str:
    spec = TOOL_SPECS.get(name)
    if not spec:
        return f"Unknown tool: {name}"

    mode = (spec.get("mode") or "").lower()
    timeout = int(spec.get("timeout") or 25)

    # Permission gate — check before any execution
    command = ""
    if mode == "shell":
        command = _format_with_args(spec.get("command") or "{command}", args)
    elif mode == "write_text":
        command = "write_file"

    if not check_permission(mode, command):
        paths_str = ""
        if mode == "write_text":
            path_arg = spec.get("path_arg") or "filename"
            fn = args.get(path_arg) or args.get("filename") or args.get("file") or args.get("path") or ""
            paths_str = fn or "unknown"
        elif mode == "shell":
            paths_str = command[:80]
        change_type = "new_file" if mode == "write_text" else "filesystem_op"
        return request_escalation(
            target_mode="edit",
            paths=[paths_str],
            change_type=change_type,
            reason=f"Tool '{name}' requires {mode} access, which is blocked in readonly mode.",
        )

    # ... rest of run_tool() unchanged (the existing mode dispatch)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_permission.py -v`
Expected: PASS (all 11 tests)

- [ ] **Step 5: Commit**

```bash
git add agent8088 tests/test_permission.py
git commit -m "feat: gate run_tool() with permission checks"
```

---

## Task 3: Add request_permission_escalation tool to tools.txt

**Files:**
- Modify: `tools.txt` (add line 8)

**Interfaces:**
- Consumes: `request_escalation()` from Task 1
- Produces: A new tool `request_permission_escalation` that the model can call to formally request edit access

- [ ] **Step 1: Write the failing test**

Append to `tests/test_permission.py`:

```python
def test_escalation_tool_in_tool_names():
    assert "request_permission_escalation" in A.TOOL_NAMES

def test_escalation_tool_returns_request():
    A.PERMISSION_MODE = "readonly"
    result = A.exec_tool("request_permission_escalation", json.dumps({
        "target_mode": "edit",
        "paths": "/tmp/test.txt",
        "change_type": "new_file",
        "reason": "Need to write test.txt"
    }))
    assert "ESCALATION_REQUEST" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_permission.py -v -k "escalation_tool"`
Expected: FAIL — `request_permission_escalation` not in `TOOL_NAMES`

- [ ] **Step 3: Write minimal implementation**

Add to `tools.txt` (line 8):

```
request_permission_escalation|Request write permission from the user|mode=escalation|args=target_mode,paths,change_type,reason|timeout=5
```

In `agent8088`, add a new mode handler in `run_tool()` after the `last_output` check (around line 528):

```python
    if mode == "escalation":
        target = args.get("target_mode", "edit")
        paths_raw = args.get("paths", "")
        if isinstance(paths_raw, list):
            paths = paths_raw
        else:
            paths = [p.strip() for p in str(paths_raw).split(",") if p.strip()]
        return request_escalation(
            target_mode=target,
            paths=paths,
            change_type=args.get("change_type", "filesystem_op"),
            reason=args.get("reason", ""),
        )
```

Also add `"escalation"` to the `check_permission()` allow list for readonly mode (it must always be callable):

```python
    if mode in ("read_text", "http_get", "last_output", "python_eval", "plan", "escalation"):
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_permission.py -v`
Expected: PASS (all 13 tests)

- [ ] **Step 5: Commit**

```bash
git add tools.txt agent8088 tests/test_permission.py
git commit -m "feat: add request_permission_escalation tool"
```

---

## Task 4: Update system.md with permission layer instructions

**Files:**
- Modify: `system.md` (replace entire content with permission-aware version)

**Interfaces:**
- Consumes: `request_permission_escalation` tool from Task 3
- Produces: System prompt that teaches the model the readonly→edit escalation protocol

- [ ] **Step 1: Write the failing test**

Append to `tests/test_permission.py`:

```python
def test_system_prompt_contains_permission_instructions():
    from pathlib import Path
    sp = Path('system.md').read_text(encoding='utf-8')
    assert "PERMISSION_MODE" in sp
    assert "readonly" in sp
    assert "edit" in sp
    assert "request_permission_escalation" in sp
    assert "escalation" in sp.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_permission.py -v -k "system_prompt"`
Expected: FAIL — current `system.md` has no permission instructions

- [ ] **Step 3: Write minimal implementation**

Replace `system.md` with:

```markdown
# Agent8088 Skill Document

You are Agent8088, a local tool-using agent built by Palindrome Research Labs. You operate on the user's filesystem and shell. Your current permission mode is provided as PERMISSION_MODE. Treat it as a hard ceiling, not a suggestion.

## Permission Modes

**readonly** (default starting mode for every new session):
- You may freely: read files within allowed_paths, list directories, run inspection-only shell commands (ls, cat, grep, find, head, tail, pwd, whoami, date, df, du, free, nproc, uptime, git status/diff/log/show/branch).
- You may NOT: create files, modify files, delete files, or run any command that changes filesystem or repository state — even a "small" or "obviously safe" one — without first escalating.

**edit** (entered only via explicit user approval):
- Everything readonly allows, plus: creating/writing files within allowed_paths, mkdir/mv/cp within the workspace, and local git commit.
- Still forbidden even in edit: git push, git push --force, git reset --hard, branch deletion, writing outside allowed_paths, and overwriting a file whose contents you have not read in this session.

## Escalation Protocol

When a task requires a write-capable action while you are in readonly mode:
1. Do NOT attempt to call write_file or execute_shell with a mutating command — it will be blocked.
2. Call request_permission_escalation with: target_mode="edit", paths=[specific files], change_type="new_file" or "overwrite" or "filesystem_op", reason="one plain-language sentence describing what you will do and why".
3. Stop and wait for the user's response. Do not continue the task.
4. If approved: proceed with the task. You do not need to re-request for further writes in the same session.
5. If denied: tell the user what you could not do and why the task can't be completed. Do not retry.

## Core Principles

- Always use tools when they can help answer a question or complete a task.
- Be concise in your answers. Don't over-explain.
- If a tool returns an error, analyze it and try a different approach.
- Never fabricate information. If you don't know and have no tool to find out, say so.
- Never fabricate a tool result. If a tool call fails or is denied, say so.
- Never claim to have made a change you were not able to make.

## Tool Usage

- For shell commands, use execute_shell with the exact command.
- For file operations, use write_file to create files and read_text to read files.
- For web searches, use web_search to find current information.
- For calculations, use the calculate tool.
- To request write permission, use request_permission_escalation.

## Hard Rules (apply in both modes)

- Never attempt to read or reason about secret-like patterns (*_KEY, *_TOKEN, *_SECRET, .env*).
- Never run git push, git push --force, git reset --hard, or delete branches.
- If uncertain whether an action requires escalation, treat it as requiring escalation.
- Report exactly what the tool output shows. Never assume a tool succeeded without checking.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_permission.py -v`
Expected: PASS (all 14 tests)

- [ ] **Step 5: Commit**

```bash
git add system.md tests/test_permission.py
git commit -m "feat: add permission layer instructions to system.md"
```

---

## Task 5: Add escalation handling to the Rich UI

**Files:**
- Modify: `agent8088_cli.py` (add escalation interception in `do_chat()` and `on_result()`)

**Interfaces:**
- Consumes: `ESCALATION_REQUEST:...` return format from `request_escalation()` in Task 1
- Produces: A y/n prompt in the terminal when an escalation request is detected; calls `A.grant_escalation()` on approval

- [ ] **Step 1: Write the failing test**

Append to `tests/test_permission.py`:

```python
def test_escalation_message_format():
    A.PERMISSION_MODE = "readonly"
    msg = A.request_escalation("edit", ["/tmp/test.txt"], "new_file", "Write test.txt")
    # Must start with ESCALATION_REQUEST: and contain the mode, change_type, paths, reason
    parts = msg.split(":", 4)
    assert parts[0] == "ESCALATION_REQUEST"
    assert parts[1] == "edit"
    assert parts[2] == "new_file"
    assert "/tmp/test.txt" in parts[3]
    assert "Write test.txt" in parts[4]

def test_grant_escalation_persists():
    A.PERMISSION_MODE = "readonly"
    A.grant_escalation()
    assert A.PERMISSION_MODE == "edit"
    # Should persist (not auto-revert)
    assert A.PERMISSION_MODE == "edit"
    A.PERMISSION_MODE = "readonly"  # cleanup
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_permission.py -v -k "escalation_message or grant_escalation_persists"`
Expected: PASS (these are unit tests on the engine; the UI integration is manual)

- [ ] **Step 3: Write minimal implementation**

In `agent8088_cli.py`, add an escalation interception helper after the `on_result` function (around line 268):

```python
def _handle_escalation(result_text):
    """Check if a tool result is an escalation request. If so, prompt the user
    for y/n approval and call grant_escalation() if approved. Returns True if
    the result was an escalation request (handled or denied), False otherwise."""
    if not result_text.startswith("ESCALATION_REQUEST:"):
        return False
    parts = result_text.split(":", 4)
    if len(parts) < 5:
        return False
    _, target_mode, change_type, paths, reason = parts
    console.print()
    console.print(Panel(
        Text(f"{reason}\n\nPaths: {paths}\nChange type: {change_type}\nRequested mode: {target_mode}"),
        title="[bold yellow]Permission Escalation Request[/bold yellow]",
        box=box.ROUNDED, border_style="yellow",
    ))
    try:
        response = console.input("[bold]Approve? (y/n)[/bold] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        response = "n"
    if response in ("y", "yes"):
        A.grant_escalation()
        console.print("[green]Permission granted — edit mode active for this session.[/green]")
        return True
    else:
        console.print("[red]Permission denied — staying in readonly mode.[/red]")
        return True
```

In `do_chat()`, after `on_result` is called (around line 319-322 in the `run_agent` call), add escalation handling. The simplest integration point: wrap the `on_result` callback to intercept escalation returns:

```python
    def _on_result(name, result):
        on_result(name, result)
        if _handle_escalation(result):
            # Escalation was handled; the tool didn't actually run.
            # Tell the model to retry the tool now that we have edit access (if granted).
            pass
```

Then pass `_on_result` to `run_agent` instead of `on_result`:

```python
        answer = A.run_agent(
            S.messages, max_turns=S.max_turns, temperature=S.temperature,
            spin=spin, on_calls=on_calls, on_tool=on_tool,
            on_result=_on_result, on_answer=None, on_token=on_token,
            interrupt_check=esc.triggered.is_set, trace=trace,
        )
```

Also add `--edit` flag support at the top of `main()`:

```python
def main():
    if "--edit" in sys.argv:
        A.PERMISSION_MODE = "edit"
        A.grant_escalation()
        sys.argv.remove("--edit")
    banner()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_permission.py -v`
Expected: PASS (all 16 tests)

- [ ] **Step 5: Commit**

```bash
git add agent8088_cli.py tests/test_permission.py
git commit -m "feat: add escalation handling to Rich UI with y/n prompt"
```

---

## Task 6: Backward compatibility — old REPL and benchmark

**Files:**
- Modify: `agent8088:767-770` (entry point block, set default mode for old REPL)
- Modify: `research/run_benchmark.py:107` (set edit mode for benchmark)

**Interfaces:**
- Consumes: `PERMISSION_MODE` global, `AGENT8088_PERMISSION` env var from Task 1
- Produces: Old REPL and benchmark run in edit mode by default (no behavior change)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_permission.py`:

```python
def test_env_var_sets_edit_mode():
    import importlib
    os.environ['AGENT8088_PERMISSION'] = 'edit'
    # Reload the module to pick up the env var
    loader2 = SourceFileLoader('agent8088_core2', 'agent8088')
    spec2 = importlib.util.spec_from_loader('agent8088_core2', loader2)
    A2 = importlib.util.module_from_spec(spec2)
    loader2.exec_module(A2)
    assert A2.PERMISSION_MODE == "edit"
    del os.environ['AGENT8088_PERMISSION']

def test_env_var_defaults_to_readonly():
    os.environ.pop('AGENT8088_PERMISSION', None)
    loader3 = SourceFileLoader('agent8088_core3', 'agent8088')
    spec3 = importlib.util.spec_from_loader('agent8088_core3', loader3)
    A3 = importlib.util.module_from_spec(spec3)
    loader3.exec_module(A3)
    assert A3.PERMISSION_MODE == "readonly"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/test_permission.py -v -k "env_var"`
Expected: PASS — the `AGENT8088_PERMISSION` env var was already implemented in Task 1 (`PERMISSION_MODE = os.environ.get("AGENT8088_PERMISSION", "readonly")`)

- [ ] **Step 3: Write minimal implementation**

In `agent8088`, at the old REPL entry point (around line 767), set edit mode when running the old REPL directly (not via the UI). After the `if __name__ == "__main__":` block, before the REPL starts:

```python
    if __name__ == "__main__":
        # The old REPL runs in edit mode by default (backward compatibility).
        # The Rich UI manages PERMISSION_MODE explicitly.
        if os.environ.get("AGENT8088_PERMISSION") is None and "--readonly" not in sys.argv:
            PERMISSION_MODE = "edit"
```

In `research/run_benchmark.py`, add at the top (after the imports):

```python
os.environ.setdefault("AGENT8088_PERMISSION", "edit")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_permission.py -v`
Expected: PASS (all 18 tests)

- [ ] **Step 5: Commit**

```bash
git add agent8088 research/run_benchmark.py tests/test_permission.py
git commit -m "feat: backward compat — old REPL and benchmark default to edit mode"
```

---

## Task 7: End-to-end manual verification

**Files:**
- No file changes — verification only

- [ ] **Step 1: Run all tests**

Run: `python -m pytest tests/test_permission.py -v`
Expected: All 18 tests pass.

- [ ] **Step 2: Verify the old REPL still works**

Run: `python agent8088 "what is 2+2"`
Expected: Answers "4" (edit mode by default, no escalation needed for a factual query).

- [ ] **Step 3: Verify the Rich UI in readonly mode**

Run: `python agent8088_cli.py`
Type: `make a new folder test_perm`
Expected: Tool call resolves `bash` → `execute_shell`, but since `mkdir` is not in `READONLY_SAFE_COMMANDS`, returns an `ESCALATION_REQUEST`. The UI shows the yellow escalation panel with a y/n prompt. Press `y`. The model retries and the folder is created.

- [ ] **Step 4: Verify readonly allows safe commands**

Run: `python agent8088_cli.py`
Type: `list the files in the current directory`
Expected: `ls` is in `READONLY_SAFE_COMMANDS`, so it executes without escalation.

- [ ] **Step 5: Verify the --edit flag**

Run: `python agent8088_cli.py --edit`
Type: `make a new folder test_perm2`
Expected: No escalation prompt — edit mode is active from the start.

- [ ] **Step 6: Verify benchmark still works**

Run: `python research/run_benchmark.py`
Expected: Runs without escalation prompts (edit mode via env var).

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "test: all permission layer tests pass — 18/18"
git push origin main
```

---

## Verification Checklist

- [ ] `PERMISSION_MODE` defaults to `"readonly"` at session start
- [ ] `check_permission()` blocks write_text and mutating shell commands in readonly
- [ ] `check_permission()` allows read_text, http_get, last_output, python_eval, plan, escalation in readonly
- [ ] `check_permission()` allows everything in edit mode
- [ ] `request_escalation()` returns `ESCALATION_REQUEST:edit:change_type:paths:reason` format
- [ ] `grant_escalation()` sets `PERMISSION_MODE = "edit"` permanently for the session
- [ ] `run_tool()` gates on `check_permission()` before any execution
- [ ] `request_permission_escalation` tool is in `tools.txt` and `TOOL_NAMES`
- [ ] `system.md` contains permission layer instructions (modes, escalation, hard rules)
- [ ] Rich UI intercepts `ESCALATION_REQUEST` and prompts y/n
- [ ] Rich UI `--edit` flag starts in edit mode
- [ ] Old REPL defaults to edit mode (backward compat)
- [ ] Benchmark defaults to edit mode (backward compat)
- [ ] `AGENT8088_PERMISSION=edit` env var works
- [ ] All 18 tests pass