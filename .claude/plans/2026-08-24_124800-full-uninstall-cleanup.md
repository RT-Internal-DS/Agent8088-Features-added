# Full Uninstall Cleanup Implementation Plan

> **Implementation status:** Implemented on branch `feature/uninstall-full-cleanup` (off `development` @ 607c940). The data-removal flag design was finalized as OpenClaw-style opt-in flags rather than this plan's original `--keep-data` opt-out: `--workspace` (remove trace logs + WhatsApp session dir), `--all` (shorthand for `--workspace`), `--yes` (skip confirmation), `--non-interactive` (requires `--yes`, matching OpenClaw's exact constraint), `--dry-run` (preview only). Function names in the actual code differ slightly from the drafts below (`_remove_agent8088_workspace_data` instead of `_remove_agent8088_user_data`, etc.) — the code is the source of truth for exact names; this document is kept for the reasoning and audit trail.

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make `agent8088 --uninstall` (macOS, Linux, Windows) reverse everything the installer created outside the main install directory, not just delete `$AGENT8088_HOME` — restoring the OS as close as possible to its pre-install state, without ever touching state that might belong to other software.

**Architecture:** Two-pronged fix. (1) Change what *new* installs create, so Playwright's Chromium browser lands inside `$AGENT8088_HOME/playwright-browsers` instead of the shared `~/.cache/ms-playwright`-style cache — this makes it get auto-deleted by the uninstall code that already wipes the whole home directory, with zero new cleanup logic needed. (2) Extend the uninstall routines to clean up the handful of deterministic, agent8088-owned side effects that live outside `$AGENT8088_HOME` today: the PATH line `install.sh` appends to shell rc files, crontab entries, Windows Task Scheduler entries, and Windows PATH registry entries for the bundled Git/Node. Anything that is genuinely shared with other software on the machine — an already-installed (pre-fix) Playwright browser cache, the Ollama embedding model, OS packages installed via apt/dnf/brew — is deliberately left alone and reported with the exact manual command to remove it, because deleting shared state by guesswork is how "uninstall" turns into "broke someone else's tool."

**Tech Stack:** Bash (`install.sh`), PowerShell (`install.ps1`), Python (`src/agent8088/cli.py`, `src/agent8088/engine.py`), pytest.

---

## Current context / audit findings

Verified by reading `install.sh`, `install.ps1`, `src/agent8088/cli.py`, and `src/agent8088/engine.py` directly (line numbers below are from `main` at the time of writing).

**What the installer creates today, and what `--uninstall` currently does with it:**

| Side effect | Created by | Currently cleaned up? |
|---|---|---|
| `$AGENT8088_HOME` (venv, uv, node, config.txt, sandbox runtime) | `install.sh` / `install.ps1` | ✅ `shutil.rmtree` / `_purge_install_tree` |
| Command shim (`~/.local/bin/agent8088` or Windows launcher dir) | `install.sh:1376-1384`, `install.ps1` | ✅ `_remove_agent8088_shim` / `_remove_windows_launcher_dir` |
| `AGENT8088_CONFIG` line in shell rc files | `install.sh:1449` (`drop_config`) | ✅ `_remove_agent8088_config_exports` (cli.py:4145) |
| `AGENT8088_CONFIG` in Windows user env | `install.ps1` | ✅ `_remove_windows_user_environment` (cli.py:4161) |
| **PATH line** (`export PATH="$link_dir:$PATH"`) in shell rc files | `install.sh:1396` (`setup_path`) | ❌ never removed — no sentinel, only `AGENT8088_CONFIG` lines are matched |
| **Crontab entries** marked `# agent8088` | `engine.py:4357` (`_CRON_MARKER`), added at runtime via `cron_mode` | ❌ never inspected/removed by uninstall |
| **Windows PATH registry entries for bundled Git** (`git\cmd`, `git\bin`, `git\usr\bin`) | `install.ps1:1456-1466` | ❌ `_run_windows_uninstall` (cli.py:4611) only passes `link_dir`, `home/bin`, `home/agent8088/venv/Scripts` to `_remove_windows_user_environment` — git's 3 entries are never in that list |
| **Windows PATH registry entry for bundled Node** (`node\`) | `install.ps1:1877-1948` | ❌ same gap — not in the owned-entries list |
| **Windows Task Scheduler entries** (`Agent8088-<16 hex>`) | `engine.py:4358` (`_WINDOWS_TASK_PREFIX`), added at runtime via `cron_mode` | ❌ the `.ps1` script and `scheduled-tasks.json` registry live inside home and get purged, but the *registered* `schtasks` entry itself is never deleted — leaves an orphaned task invoking a deleted `agent8088.exe` |
| **Playwright Chromium browser binary** (~280 MB) | `install.sh:965`, `install.ps1:1793` via `playwright install chromium`, no `PLAYWRIGHT_BROWSERS_PATH` override → lands in the OS-default shared cache (`~/.cache/ms-playwright` Linux, `~/Library/Caches/ms-playwright` macOS, `%LOCALAPPDATA%\ms-playwright` Windows) | ❌ never touched — and rightly cautious to auto-delete, since other tools on the machine can share that same cache directory (confirmed via Playwright's own docs: `PLAYWRIGHT_BROWSERS_PATH` is the only thing that changes this, and Playwright's own registry code computes one shared default per OS) |
| Trace dir default `~/Documents/agent8088/traces` (cli.py:271, `AGENT8088_TRACE_DIR`) | created at runtime, not by the installer | ❌ never removed |
| WhatsApp session dir default `~/.local/share/agent8088/whatsapp/session` (cli.py:5348) | created at runtime, not by the installer | ❌ never removed |
| Ollama embedding model `nomic-embed-text` (274 MB) | `install.sh:1216` (`install_embedding_model`) — only runs `ollama pull` if Ollama already exists | **Out of scope** — lives in Ollama's own store, Ollama is the user's separate tool, other Ollama-using projects may depend on the same model |
| System packages (bubblewrap, socat, ripgrep, git, node, cron/cronie via apt/dnf/pacman/brew) | `install.sh:660-1341` | **Out of scope** — shared system state; other software may depend on them |

**Existing test coverage (do not duplicate):**
- `tests/test_posix_uninstall.py` — `_run_uninstall()` resilience to `EPERM` on individual files (foreign-owned files, e.g. Docker bind mounts), `_clear_readonly` OR-ing permission bits instead of clobbering them. Pattern: `monkeypatch.setattr(cli, "_agent8088_home", lambda: home)`, fake HOME via `tmp_path`, mocks `os.unlink`/`os.chmod`.
- `tests/test_windows_uninstall.py` — `_run_windows_uninstall` orchestration, `_purge_install_tree`, deferred cleanup helper, `_remove_windows_user_environment` registry filtering via a fake `winreg` module (`monkeypatch.setitem(sys.modules, "winreg", fake)`), launcher self-preservation.
- `tests/test_installer_partial_cleanup.py` — extracts PowerShell functions from `install.ps1` via regex and runs them via `subprocess.run([pwsh, "-Command", ...])` with stub `Write-Err`/`Write-Info` overrides. Covers pending-uninstall markers and launcher wait loop only.
- `tests/test_installer_sudo_prompt_foreground.py` — extracts bash functions from `install.sh` via regex, stubs commands as fake `PATH` binaries, runs via `subprocess.run([BASH, "-c", script], env=...)`. This is the pattern to reuse for testing the `PLAYWRIGHT_BROWSERS_PATH` export.

None of the above assert on: PATH rc-line removal, crontab cleanup, Windows Task Scheduler cleanup, git/node Windows registry PATH entries, trace/WhatsApp dir cleanup, or the Playwright browsers-path redirect. This plan adds exactly that coverage.

**Prior art (checked via Context7 against the projects' own docs):**

- **Hermes Agent** (`hermes uninstall`, docs: `nousresearch/hermes-agent`) defaults to the *opposite* of this plan: it removes the program (scheduled task, shim, PATH trim, agent directory) but **preserves** config/auth/skills/sessions/logs by default, requiring a separate full-deletion flag or a manual `Remove-Item` for complete removal. Its `profile delete` command shows an itemized confirmation before deleting — "This will permanently delete: • All config, API keys, memories, sessions, skills, cron jobs • Command alias (~/.local/bin/research-bot)" — and requires typing the profile name (not just "yes") to confirm.
- **OpenClaw** (`openclaw uninstall`, docs: `openclaw/openclaw`) uses granular opt-in flags — `--service`, `--state`, `--workspace`, `--app`, with `--all` as a shorthand — plus a `--dry-run` mode that prints planned actions without deleting anything, and recommends `backup create` before removing state/workspace. It also documents, per OS, exactly how to manually remove the gateway service (launchd plist, systemd unit, Windows scheduled task) if the CLI itself is already gone — the same class of orphaned-scheduled-task gap this plan's Task 9 fixes for agent8088.

Two things from that prior art are worth folding in here even though neither project solves the exact "shared Playwright cache" problem this plan is about (their browser-automation features work differently and don't call this out): an itemized "here's what this will remove" preview before the confirmation prompt, and a `--dry-run` flag. Both are cheap given the plan already has functions that *detect* what needs removing — see Task 10. The "preserve data by default" default Hermes uses is called out as an explicit alternative in Design Decision 6 below, but this plan keeps full-removal-by-default since that's what was explicitly asked for.

---

## Design decisions (read before implementing)

1. **Default `--uninstall` behavior removes trace logs and the WhatsApp session directory** (matches "restore to pre-install state"), but **only when they're still at the compiled-in default path** — if the user pointed `AGENT8088_TRACE_DIR` or the `whatsapp_session_dir` config setting somewhere else, uninstall leaves it alone and prints its location instead of guessing. Add a `--keep-data` flag as an opt-out for the default paths, since this is user-generated content, not just program files.
2. **The Playwright Chromium browser cache is never auto-deleted for already-installed (pre-fix) users**, because it may be shared with other projects on the machine that also use Playwright. Uninstall prints an informational message with the exact manual command instead. New installs (after this fix ships) avoid the problem entirely by installing into a dedicated `playwright-browsers` directory inside `$AGENT8088_HOME`, which the existing home-wipe already covers — no new deletion logic needed for the new-install case.
3. **Ollama's embedding model and OS package-manager installs stay explicitly out of scope** — they're the user's own shared infrastructure, not agent8088-exclusive.
4. **Windows Task Scheduler and POSIX crontab entries are identified by the existing markers already in the codebase** (`_WINDOWS_TASK_PREFIX = "Agent8088-"`, `_CRON_MARKER = "# agent8088"`) — no new manifest/bookkeeping needed, since these markers already exist for other reasons (listing/matching agent8088's own entries at runtime).
5. **The rc-file PATH line has no existing sentinel.** It's removed by matching the *exact* line install.sh writes (`export PATH="<link_dir>:$PATH"`) rather than a substring match on the directory — a substring match risks deleting an unrelated line a user wrote by hand that happens to mention the same directory in a different form. This is a best-effort, documented limitation: if a user hand-edited that line, it won't match and won't be removed (flagged in Risks below).
6. **Full removal is the default, not opt-in.** Hermes Agent takes the opposite stance (preserve config/data by default, require a flag or manual step for full deletion) — see Prior Art above. This plan keeps full-removal-by-default with `--keep-data` as the opt-out, because that's what was explicitly asked for ("everything needs to be uninstalled to the state before... installation"), but it's flagged again in the open questions at the end since it's a real, defensible alternative.
7. **Preview and confirm before deleting.** Following OpenClaw's `--dry-run` and Hermes's itemized "this will permanently delete" confirmation, `--uninstall` should show exactly what it found (which PATH lines, which cron/scheduled-task entries, which directories) before asking for confirmation, and support a `--dry-run` flag that prints the same list without touching anything. See Task 10.

---

## Task 1: Redirect Playwright's browser install into `$AGENT8088_HOME` at runtime

**Objective:** Make `_exec_browser` in `engine.py` always point Playwright at a browsers directory inside the agent8088 home, so a *future* install's Chromium download is covered by the existing home-wipe.

**Files:**
- Modify: `src/agent8088/engine.py:3244-3282`
- Test: `tests/test_engine_playwright_browsers_path.py` (new)

**Step 1: Write failing test**

```python
# tests/test_engine_playwright_browsers_path.py
import os
from pathlib import Path
from unittest import mock

from agent8088 import engine


def test_exec_browser_sets_playwright_browsers_path_inside_agent_home(monkeypatch, tmp_path):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "agent8088")
    monkeypatch.setattr(engine, "_egress_check", lambda url: None)
    monkeypatch.setattr(engine, "_ssrf_check", lambda url: None)
    monkeypatch.setattr(engine, "_playwright_available", lambda: False)

    engine._exec_browser({"url": "https://example.com"})

    assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH") == str(tmp_path / "agent8088" / "playwright-browsers")


def test_exec_browser_respects_existing_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/custom/path")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "agent8088")
    monkeypatch.setattr(engine, "_egress_check", lambda url: None)
    monkeypatch.setattr(engine, "_ssrf_check", lambda url: None)
    monkeypatch.setattr(engine, "_playwright_available", lambda: False)

    engine._exec_browser({"url": "https://example.com"})

    assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH") == "/custom/path"
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_engine_playwright_browsers_path.py -v`
Expected: FAIL — `PLAYWRIGHT_BROWSERS_PATH` is `None`, not the expected path.

**Step 3: Write minimal implementation**

In `engine.py`, right after the `_playwright_available()` check inside `_exec_browser` (around line 3273-3276):

```python
    if not _playwright_available():
        return ("Playwright is not installed. Install it with:\n"
                "  pip install playwright && playwright install chromium\n"
                "Until then, use web_search or get_page_title instead.")
    # Keep Chromium's ~280MB download inside $AGENT8088_HOME rather than the
    # OS-default shared cache (~/.cache/ms-playwright etc.) - that shared
    # cache can belong to other Playwright-using projects on the same
    # machine, so `agent8088 --uninstall` cannot safely delete it. Installing
    # into our own subdirectory means the existing home-directory wipe
    # already covers it, with no separate cleanup logic needed.
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH", str(_agent_data_dir() / "playwright-browsers")
    )
    selector = str(args.get("selector") or "").strip()
```

**Step 4: Run test to verify pass**

Run: `pytest tests/test_engine_playwright_browsers_path.py -v`
Expected: PASS — 2 passed.

**Step 5: Commit**

```bash
git add src/agent8088/engine.py tests/test_engine_playwright_browsers_path.py
git commit -m "feat: keep Playwright's Chromium download inside AGENT8088_HOME"
```

---

## Task 2: Point `install.sh`'s Chromium download at the same directory

**Objective:** Make the installer's `playwright install chromium` step use the same `playwright-browsers` directory Task 1 makes the runtime look in, so a fresh install's browser download and the runtime's browser lookup agree.

**Files:**
- Modify: `install.sh:963-967`

**Step 1 (no test — this is a shell script; verified via Task 12's regression test and manual run)**

**Step 2: Implement**

```bash
    if [ "$_playwright_installed" = true ]; then
        log_info "Installing Playwright Chromium browser (~280 MB)..."
        _chromium_rc=0
        # Match engine.py's _exec_browser default so the browser this step
        # downloads is the one the runtime actually looks for - and so it
        # lives inside $AGENT8088_HOME, where uninstall already cleans up.
        export PLAYWRIGHT_BROWSERS_PATH="$AGENT8088_HOME/playwright-browsers"
        run_with_timeout "$T_CHROMIUM" "$_py" -m playwright install chromium \
            >/dev/null 2>&1 || _chromium_rc=$?
```

**Step 3: Verify manually**

Run: `bash -n install.sh` (syntax check)
Expected: no output (no syntax errors).

**Step 4: Commit**

```bash
git add install.sh
git commit -m "feat: install Playwright's Chromium into AGENT8088_HOME"
```

---

## Task 3: Point `install.ps1`'s Chromium download at the same directory

**Objective:** Windows equivalent of Task 2.

**Files:**
- Modify: `install.ps1:1791-1794`

**Step 1: Implement**

```powershell
            if ($pwResult.ExitCode -eq 0) {
                Write-Info "Installing Playwright Chromium browser (~280 MB)..."
                # Match engine.py's _exec_browser default: browsers live inside
                # $Agent8088Home so `agent8088 --uninstall` already covers them
                # without touching the OS-shared ms-playwright cache.
                $env:PLAYWRIGHT_BROWSERS_PATH = "$Agent8088Home\playwright-browsers"
                $chromiumResult = Invoke-WithTimeout -FilePath $py `
                    -Arguments @("-m", "playwright", "install", "chromium") `
                    -TimeoutSec $TChromium -Activity "Installing Playwright Chromium"
```

**Step 2: Verify manually**

Run: `pwsh -NoProfile -Command "$null = [System.Management.Automation.PSParser]::Tokenize((Get-Content install.ps1 -Raw), [ref]$null)"` (or your existing PowerShell syntax-check step in CI)
Expected: no parse errors.

**Step 3: Commit**

```bash
git add install.ps1
git commit -m "feat: install Playwright's Chromium into Agent8088Home on Windows"
```

---

## Task 4: Remove the PATH rc-file line on POSIX uninstall

**Objective:** Add a sibling to `_remove_agent8088_config_exports` that strips the exact PATH line `setup_path()` appended, and wire it into `_run_uninstall()`.

**Files:**
- Modify: `src/agent8088/cli.py` (add function near `_remove_agent8088_config_exports` at line 4145; wire into `_run_uninstall` at line 4660)
- Test: `tests/test_posix_uninstall_full_cleanup.py` (new)

**Step 1: Write failing test**

```python
# tests/test_posix_uninstall_full_cleanup.py
import sys
from pathlib import Path
from unittest import mock

import pytest

from agent8088 import cli

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX uninstall path only")


def test_remove_agent8088_path_exports_strips_exact_line(tmp_path, monkeypatch):
    link_dir = tmp_path / ".local" / "bin"
    rc = tmp_path / ".zshrc"
    rc.write_text(
        'export SOME_VAR=1\n'
        f'export PATH="{link_dir}:$PATH"\n'
        'export ANOTHER=2\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli, "_agent8088_link_dir", lambda: link_dir)

    removed = cli._remove_agent8088_path_exports()

    assert removed == 1
    kept = rc.read_text(encoding="utf-8")
    assert "SOME_VAR" in kept and "ANOTHER" in kept
    assert str(link_dir) not in kept


def test_remove_agent8088_path_exports_leaves_hand_edited_lines(tmp_path, monkeypatch):
    link_dir = tmp_path / ".local" / "bin"
    rc = tmp_path / ".zshrc"
    # A user who customized the line by hand (different quoting/order) is not
    # touched - matching only the exact installer-written line is the safe
    # default (see plan's design decisions).
    rc.write_text(f'export PATH="$PATH:{link_dir}"  # my custom order\n', encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli, "_agent8088_link_dir", lambda: link_dir)

    removed = cli._remove_agent8088_path_exports()

    assert removed == 0
    assert str(link_dir) in rc.read_text(encoding="utf-8")
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_posix_uninstall_full_cleanup.py -v`
Expected: FAIL — `AttributeError: module 'agent8088.cli' has no attribute '_remove_agent8088_path_exports'`.

**Step 3: Write minimal implementation**

Add right after `_remove_agent8088_config_exports` (cli.py:4158):

```python
def _remove_agent8088_path_exports():
    """Remove the exact PATH line install.sh's setup_path() appended.

    Matched by exact line content, not a substring on link_dir - a user's own
    hand-written PATH edit that happens to mention the same directory in a
    different form (quoting, order, appended comment) is left alone rather
    than guessed at.
    """
    link_dir = _agent8088_link_dir()
    path_line = f'export PATH="{link_dir}:$PATH"'
    removed = 0
    for rc in (Path.home() / ".zshrc", Path.home() / ".zprofile",
               Path.home() / ".bashrc", Path.home() / ".bash_profile",
               Path.home() / ".profile"):
        if not rc.exists() or not rc.is_file():
            continue
        lines = rc.read_text(encoding="utf-8", errors="ignore").splitlines()
        kept = [line for line in lines if line.strip() != path_line]
        if kept != lines:
            rc.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            removed += 1
    return removed
```

Then in `_run_uninstall()` (cli.py:4660), right after the existing `_remove_agent8088_config_exports()` call:

```python
    if os.name != "nt":
        _remove_agent8088_config_exports()
        _remove_agent8088_path_exports()
```

**Step 4: Run test to verify pass**

Run: `pytest tests/test_posix_uninstall_full_cleanup.py -v`
Expected: PASS — 2 passed.

**Step 5: Commit**

```bash
git add src/agent8088/cli.py tests/test_posix_uninstall_full_cleanup.py
git commit -m "feat: remove agent8088's PATH rc-file line on uninstall"
```

---

## Task 5: Remove crontab entries on POSIX uninstall

**Objective:** Strip any crontab lines carrying `_CRON_MARKER` on uninstall, mirroring how `engine.py`'s `_exec_cron` already filters by that marker when listing.

**Files:**
- Modify: `src/agent8088/cli.py` (add function; wire into `_run_uninstall`)
- Test: `tests/test_posix_uninstall_full_cleanup.py` (extend from Task 4)

**Step 1: Write failing test**

Append to `tests/test_posix_uninstall_full_cleanup.py`:

```python
def test_remove_agent8088_crontab_entries_filters_by_marker(monkeypatch):
    existing = "0 9 * * * /usr/bin/backup.sh\n* * * * * agent8088 --gateway # agent8088\n"

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["crontab", "-l"]:
            return mock.Mock(returncode=0, stdout=existing, stderr="")
        if cmd[:2] == ["crontab", "-"]:
            calls.append(("stdin", kwargs.get("input")))
            return mock.Mock(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    removed = cli._remove_agent8088_crontab_entries()

    assert removed == 1
    written = calls[-1][1]
    assert "backup.sh" in written
    assert "agent8088" not in written or "# agent8088" not in written


def test_remove_agent8088_crontab_entries_noop_when_no_crontab(monkeypatch):
    def fake_run(cmd, **kwargs):
        return mock.Mock(returncode=1, stdout="", stderr="no crontab for user")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    # Must not raise.
    removed = cli._remove_agent8088_crontab_entries()
    assert removed == 0
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_posix_uninstall_full_cleanup.py -v`
Expected: FAIL — `_remove_agent8088_crontab_entries` doesn't exist.

**Step 3: Write minimal implementation**

Add near the new `_remove_agent8088_path_exports` in `cli.py` (needs `_CRON_MARKER` — currently defined in `engine.py:4357`, import it):

```python
def _remove_agent8088_crontab_entries():
    """Remove crontab lines this process added (marked with engine._CRON_MARKER).

    Leaves every other line - including ones from other software - untouched.
    """
    from agent8088.engine import _CRON_MARKER

    try:
        current = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if current.returncode != 0:
        return 0  # no crontab for this user, or `crontab` unavailable

    lines = current.stdout.splitlines()
    kept = [line for line in lines if _CRON_MARKER not in line]
    if kept == lines:
        return 0

    payload = "\n".join(kept) + ("\n" if kept else "")
    try:
        subprocess.run(["crontab", "-"], input=payload, capture_output=True,
                        text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    return len(lines) - len(kept)
```

Wire into `_run_uninstall()` next to the PATH-exports call:

```python
    if os.name != "nt":
        _remove_agent8088_config_exports()
        _remove_agent8088_path_exports()
        _remove_agent8088_crontab_entries()
```

**Step 4: Run test to verify pass**

Run: `pytest tests/test_posix_uninstall_full_cleanup.py -v`
Expected: PASS — 4 passed.

**Step 5: Commit**

```bash
git add src/agent8088/cli.py tests/test_posix_uninstall_full_cleanup.py
git commit -m "feat: remove agent8088's crontab entries on uninstall"
```

---

## Task 6: Add `--keep-data` flag and clean up default trace/WhatsApp dirs (shared POSIX + Windows)

**Objective:** By default, delete the trace-log directory and WhatsApp session directory *only if they're still at the compiled-in default path*; skip with `--keep-data`. Shared helper so both `_run_uninstall` and `_run_windows_uninstall` call the same code.

**Files:**
- Modify: `src/agent8088/cli.py` (new function, new argparse flag, wire into both uninstall paths at lines ~4660 and ~4594)
- Test: `tests/test_posix_uninstall_full_cleanup.py` (extend)

**Step 1: Write failing test**

Append to `tests/test_posix_uninstall_full_cleanup.py`:

```python
def test_remove_agent8088_user_data_removes_default_dirs(tmp_path, monkeypatch):
    trace_dir = tmp_path / "Documents" / "agent8088" / "traces"
    trace_dir.mkdir(parents=True)
    (trace_dir / "trace.json").write_text("{}", encoding="utf-8")
    wa_dir = tmp_path / ".local" / "share" / "agent8088" / "whatsapp" / "session"
    wa_dir.mkdir(parents=True)

    monkeypatch.delenv("AGENT8088_TRACE_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    removed = cli._remove_agent8088_user_data(keep_data=False)

    assert removed == 2
    assert not trace_dir.exists()
    assert not wa_dir.exists()


def test_remove_agent8088_user_data_respects_keep_data(tmp_path, monkeypatch):
    trace_dir = tmp_path / "Documents" / "agent8088" / "traces"
    trace_dir.mkdir(parents=True)
    monkeypatch.delenv("AGENT8088_TRACE_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    removed = cli._remove_agent8088_user_data(keep_data=True)

    assert removed == 0
    assert trace_dir.exists()


def test_remove_agent8088_user_data_skips_customized_trace_dir(tmp_path, monkeypatch):
    custom = tmp_path / "elsewhere" / "traces"
    custom.mkdir(parents=True)
    monkeypatch.setenv("AGENT8088_TRACE_DIR", str(custom))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    removed = cli._remove_agent8088_user_data(keep_data=False)

    assert removed == 0
    assert custom.exists()
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_posix_uninstall_full_cleanup.py -v`
Expected: FAIL — `_remove_agent8088_user_data` doesn't exist.

**Step 3: Write minimal implementation**

Add to `cli.py`:

```python
def _remove_agent8088_user_data(keep_data=False):
    """Remove the trace-log and WhatsApp session directories, but only when
    they're still at the compiled-in default path. A path the user pointed
    somewhere else (AGENT8088_TRACE_DIR, or a custom whatsapp_session_dir in
    config.txt) is left alone rather than guessed at - it may not even be
    agent8088-exclusive storage.
    """
    if keep_data:
        return 0
    import shutil as _shutil

    removed = 0
    default_trace_dir = Path.home() / "Documents" / "agent8088" / "traces"
    if "AGENT8088_TRACE_DIR" not in os.environ and default_trace_dir.exists():
        _shutil.rmtree(default_trace_dir, ignore_errors=True)
        removed += 1

    default_wa_dir = Path.home() / ".local" / "share" / "agent8088" / "whatsapp" / "session"
    if default_wa_dir.exists():
        _shutil.rmtree(default_wa_dir, ignore_errors=True)
        removed += 1

    return removed
```

Add the flag next to `--uninstall` in the argparse block (cli.py:5607):

```python
    parser.add_argument("--uninstall", "-uninstall", action="store_true", help="remove agent8088 install dir + env vars, then exit")
    parser.add_argument("--keep-data", action="store_true",
                        help="with --uninstall: keep trace logs and the WhatsApp session directory")
```

Wire into the call site (cli.py:5650-5652):

```python
    if args.uninstall:
        uninstall_ok = _run_uninstall(keep_data=args.keep_data)
        return (0 if uninstall_ok else 1) if os.name == "nt" else None
```

Update `_run_uninstall` and `_run_windows_uninstall` signatures to accept and thread `keep_data`:

```python
def _run_uninstall(keep_data=False):
    ...
    if os.name == "nt":
        return _run_windows_uninstall(home, keep_data=keep_data)
    ...
    if os.name != "nt":
        _remove_agent8088_config_exports()
        _remove_agent8088_path_exports()
        _remove_agent8088_crontab_entries()
    _remove_agent8088_user_data(keep_data=keep_data)
    print("Done. Open a NEW terminal for PATH to refresh.")
    return True
```

```python
def _run_windows_uninstall(home, keep_data=False):
    ...
    # near the end, right before the final success returns:
    _remove_agent8088_user_data(keep_data=keep_data)
```

**Step 4: Run test to verify pass**

Run: `pytest tests/test_posix_uninstall_full_cleanup.py -v`
Expected: PASS — 7 passed.

**Step 5: Commit**

```bash
git add src/agent8088/cli.py tests/test_posix_uninstall_full_cleanup.py
git commit -m "feat: add --keep-data and clean up default trace/WhatsApp dirs on uninstall"
```

---

## Task 7: Warn (don't delete) about a shared Playwright browser cache

**Objective:** For machines that already installed agent8088 before Task 1-3 shipped, the Chromium binary sits in the OS-default shared cache. Inform the user instead of guessing whether it's safe to delete.

**Files:**
- Modify: `src/agent8088/cli.py` (new function; call from both uninstall paths)
- Test: `tests/test_posix_uninstall_full_cleanup.py` (extend)

**Step 1: Write failing test**

```python
def test_warn_shared_playwright_cache_reports_when_present(tmp_path, monkeypatch, capsys):
    cache_dir = tmp_path / ".cache" / "ms-playwright"
    cache_dir.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")

    cli._warn_shared_playwright_cache()

    out = capsys.readouterr().out
    assert str(cache_dir) in out
    assert cache_dir.exists()  # never deleted


def test_warn_shared_playwright_cache_silent_when_absent(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")

    cli._warn_shared_playwright_cache()

    assert capsys.readouterr().out == ""
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_posix_uninstall_full_cleanup.py -v`
Expected: FAIL — function doesn't exist.

**Step 3: Write minimal implementation**

```python
def _warn_shared_playwright_cache():
    """Playwright's default browser cache can be shared with other projects
    on this machine - never delete it automatically. New agent8088 installs
    avoid this entirely (see _exec_browser's PLAYWRIGHT_BROWSERS_PATH), but a
    pre-existing install's Chromium download still lives there.
    """
    if sys.platform == "win32":
        cache_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "ms-playwright"
    elif sys.platform == "darwin":
        cache_dir = Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        cache_dir = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "ms-playwright"

    if cache_dir.exists():
        print(f"Note: Playwright's Chromium browser was left in place at {cache_dir}")
        print("  It may be shared with other projects on this machine, so it wasn't removed.")
        print(f"  To remove it yourself: rm -rf \"{cache_dir}\"" if os.name != "nt"
              else f"  To remove it yourself: Remove-Item -Recurse -Force \"{cache_dir}\"")
```

Call it from both `_run_uninstall()` and `_run_windows_uninstall()`, right next to the `_remove_agent8088_user_data(...)` call added in Task 6.

**Step 4: Run test to verify pass**

Run: `pytest tests/test_posix_uninstall_full_cleanup.py -v`
Expected: PASS — 9 passed.

**Step 5: Commit**

```bash
git add src/agent8088/cli.py tests/test_posix_uninstall_full_cleanup.py
git commit -m "feat: warn about (never auto-delete) a shared Playwright browser cache"
```

---

## Task 8: Clean up bundled Git/Node PATH registry entries on Windows uninstall

**Objective:** `_run_windows_uninstall` already calls `_remove_windows_user_environment(*owned_path_entries)` — it's just missing two of the entries `install.ps1` actually writes.

**Files:**
- Modify: `src/agent8088/cli.py:4611-4615` (`_run_windows_uninstall`)
- Test: `tests/test_windows_uninstall.py` (extend)

**Step 1: Write failing test**

Add to `tests/test_windows_uninstall.py`, following its existing fake-`winreg` pattern (check the file's existing fixtures for the exact fake-module shape before writing this — reuse it rather than re-deriving it):

```python
def test_run_windows_uninstall_removes_git_and_node_path_entries(tmp_path, monkeypatch, fake_winreg):
    home = tmp_path / "agent8088"
    home.mkdir()
    fake_winreg.path_value = ";".join([
        str(home / "git" / "cmd"),
        str(home / "git" / "bin"),
        str(home / "git" / "usr" / "bin"),
        str(home / "node"),
        r"C:\Windows\System32",
    ])
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(cli, "_windows_processes_in_tree", lambda _home: [])
    monkeypatch.setattr(cli, "_purge_install_tree", lambda _home: [])
    monkeypatch.setattr(cli, "_remove_windows_launcher_dir", lambda _link_dir: None)

    cli._run_windows_uninstall(home)

    assert fake_winreg.path_value == r"C:\Windows\System32"
```

(Adapt `fake_winreg` to whatever fixture name/shape `test_windows_uninstall.py` already uses — read the file first; don't invent a second fake-registry pattern.)

**Step 2: Run test to verify failure**

Run: `pytest tests/test_windows_uninstall.py -v -k git_and_node`
Expected: FAIL — git/node entries remain in `path_value`.

**Step 3: Write minimal implementation**

In `_run_windows_uninstall` (cli.py:4611-4615):

```python
    link_dir = _agent8088_link_dir()
    managed_bin = home / "bin"
    legacy_scripts = home / "agent8088" / "venv" / "Scripts"
    bundled_git_cmd = home / "git" / "cmd"
    bundled_git_bin = home / "git" / "bin"
    bundled_git_usr_bin = home / "git" / "usr" / "bin"
    bundled_node = home / "node"

    environment_result = _remove_windows_user_environment(
        link_dir, managed_bin, legacy_scripts,
        bundled_git_cmd, bundled_git_bin, bundled_git_usr_bin, bundled_node,
    )
```

**Step 4: Run test to verify pass**

Run: `pytest tests/test_windows_uninstall.py -v`
Expected: PASS, including the new test.

**Step 5: Commit**

```bash
git add src/agent8088/cli.py tests/test_windows_uninstall.py
git commit -m "fix: remove bundled Git/Node PATH entries on Windows uninstall"
```

---

## Task 9: Delete registered Windows Task Scheduler entries on uninstall

**Objective:** `scheduled-tasks.json` (inside home, about to be purged) lists every `Agent8088-<id>` task this install registered. Read it before the purge and delete each one via `schtasks`.

**Files:**
- Modify: `src/agent8088/cli.py` (new function; wire into `_run_windows_uninstall` before the purge)
- Test: `tests/test_windows_uninstall.py` (extend)

**Step 1: Write failing test**

```python
def test_remove_windows_scheduled_tasks_deletes_each_registered_task(tmp_path, monkeypatch):
    home = tmp_path / "agent8088"
    home.mkdir()
    registry = home / "scheduled-tasks.json"
    registry.write_text(json.dumps([
        {"id": "abc1234567890def", "schedule": "0 9 * * *", "task": "check inbox"},
        {"id": "1112223334445556", "schedule": "*/5 * * * *", "task": "poll"},
    ]), encoding="utf-8")

    calls = []
    monkeypatch.setattr(cli.shutil, "which", lambda name: "schtasks.exe" if "schtasks" in name else None)

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return mock.Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    removed = cli._remove_windows_scheduled_tasks(home)

    assert removed == 2
    deleted_names = {c[c.index("/TN") + 1] for c in calls if "/TN" in c}
    assert deleted_names == {"Agent8088-abc1234567890def", "Agent8088-1112223334445556"}


def test_remove_windows_scheduled_tasks_noop_without_registry(tmp_path, monkeypatch):
    home = tmp_path / "agent8088"
    home.mkdir()

    removed = cli._remove_windows_scheduled_tasks(home)

    assert removed == 0
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_windows_uninstall.py -v -k scheduled_tasks`
Expected: FAIL — function doesn't exist.

**Step 3: Write minimal implementation**

```python
def _remove_windows_scheduled_tasks(home):
    """Delete every Task Scheduler entry this install registered.

    scheduled-tasks.json (inside home) is the authoritative list of task IDs
    this install created - read it before home gets purged, and delete each
    task by its exact `Agent8088-<id>` name so nothing else on the machine's
    task list is touched.
    """
    registry = home / "scheduled-tasks.json"
    try:
        entries = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(entries, list):
        return 0

    scheduler = shutil.which("schtasks.exe") or shutil.which("schtasks") or "schtasks.exe"
    removed = 0
    for entry in entries:
        task_id = str(entry.get("id", ""))
        if not re.fullmatch(r"[0-9a-f]{16}", task_id):
            continue
        task_name = f"Agent8088-{task_id}"
        try:
            subprocess.run([scheduler, "/Delete", "/TN", task_name, "/F"],
                            capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            continue
        removed += 1
    return removed
```

Wire into `_run_windows_uninstall`, before the process-blocker check (home still exists at this point):

```python
    if not home.exists():
        _say(f"Install directory not found: {home}")
        _remove_windows_launcher_dir(link_dir)
        _say("Agent8088 user environment entries removed.")
        return True

    removed_tasks = _remove_windows_scheduled_tasks(home)
    if removed_tasks:
        _say(f"Removed {removed_tasks} scheduled task(s) from Windows Task Scheduler.")

    blockers = _windows_processes_in_tree(home)
```

**Step 4: Run test to verify pass**

Run: `pytest tests/test_windows_uninstall.py -v`
Expected: PASS, including the 2 new tests.

**Step 5: Commit**

```bash
git add src/agent8088/cli.py tests/test_windows_uninstall.py
git commit -m "feat: delete registered Task Scheduler entries on Windows uninstall"
```

---

## Task 10: Add `--dry-run` and an itemized confirmation prompt

**Objective:** Inspired by OpenClaw's `uninstall --dry-run` and Hermes's itemized `profile delete` confirmation (see Prior Art above) — show the user exactly what was found (PATH lines, cron/scheduled-task entries, data directories) before asking them to confirm, and let them preview the same list without deleting anything via `--dry-run`. This reuses the detection logic Tasks 4-9 already wrote; it does not duplicate it.

**Files:**
- Modify: `src/agent8088/cli.py` (new `_describe_agent8088_side_effects()` helper; new `--dry-run` flag; update the confirmation prompt in `_run_uninstall`)
- Test: `tests/test_posix_uninstall_full_cleanup.py` (extend)

**Step 1: Write failing test**

```python
def test_describe_agent8088_side_effects_lists_what_exists(tmp_path, monkeypatch):
    link_dir = tmp_path / ".local" / "bin"
    rc = tmp_path / ".zshrc"
    rc.write_text(f'export PATH="{link_dir}:$PATH"\n', encoding="utf-8")
    trace_dir = tmp_path / "Documents" / "agent8088" / "traces"
    trace_dir.mkdir(parents=True)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli, "_agent8088_link_dir", lambda: link_dir)
    monkeypatch.delenv("AGENT8088_TRACE_DIR", raising=False)
    monkeypatch.setattr(cli, "_remove_agent8088_crontab_entries", None, raising=False)
    monkeypatch.setattr(cli.subprocess, "run",
                         lambda cmd, **kw: mock.Mock(returncode=1, stdout="", stderr=""))

    description = cli._describe_agent8088_side_effects()

    assert any("PATH" in line and str(rc) in line for line in description)
    assert any(str(trace_dir) in line for line in description)


def test_run_uninstall_dry_run_deletes_nothing(tmp_path, monkeypatch, capsys):
    home = tmp_path / "agent8088"
    home.mkdir()
    (home / "config.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(cli, "_agent8088_home", lambda: home)
    monkeypatch.setattr(cli, "_describe_agent8088_side_effects", lambda: ["- PATH line in ~/.zshrc"])

    result = cli._run_uninstall(dry_run=True)

    assert result is True
    assert home.exists()
    assert (home / "config.txt").exists()
    assert "PATH line in ~/.zshrc" in capsys.readouterr().out
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_posix_uninstall_full_cleanup.py -v -k "describe_agent8088_side_effects or dry_run"`
Expected: FAIL — neither `_describe_agent8088_side_effects` nor the `dry_run` parameter exist yet.

**Step 3: Write minimal implementation**

```python
def _describe_agent8088_side_effects():
    """List every agent8088-owned side effect found outside $AGENT8088_HOME,
    for the pre-delete confirmation prompt and --dry-run. Read-only - detects,
    never removes.
    """
    lines = []
    if os.name != "nt":
        link_dir = _agent8088_link_dir()
        path_line = f'export PATH="{link_dir}:$PATH"'
        for rc in (Path.home() / ".zshrc", Path.home() / ".zprofile",
                   Path.home() / ".bashrc", Path.home() / ".bash_profile",
                   Path.home() / ".profile"):
            if rc.exists() and path_line in rc.read_text(encoding="utf-8", errors="ignore").splitlines():
                lines.append(f"- PATH line in {rc}")
        try:
            current = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=20)
            if current.returncode == 0:
                from agent8088.engine import _CRON_MARKER
                marked = [l for l in current.stdout.splitlines() if _CRON_MARKER in l]
                if marked:
                    lines.append(f"- {len(marked)} crontab entr{'y' if len(marked) == 1 else 'ies'}")
        except (OSError, subprocess.TimeoutExpired):
            pass

    default_trace_dir = Path.home() / "Documents" / "agent8088" / "traces"
    if "AGENT8088_TRACE_DIR" not in os.environ and default_trace_dir.exists():
        lines.append(f"- Trace log directory: {default_trace_dir}")
    default_wa_dir = Path.home() / ".local" / "share" / "agent8088" / "whatsapp" / "session"
    if default_wa_dir.exists():
        lines.append(f"- WhatsApp session directory: {default_wa_dir}")
    return lines
```

Add `--dry-run` next to `--keep-data`:

```python
    parser.add_argument("--dry-run", action="store_true",
                        help="with --uninstall: print what would be removed, remove nothing")
```

Thread it through the call site and `_run_uninstall`:

```python
    if args.uninstall:
        uninstall_ok = _run_uninstall(keep_data=args.keep_data, dry_run=args.dry_run)
        return (0 if uninstall_ok else 1) if os.name == "nt" else None
```

```python
def _run_uninstall(keep_data=False, dry_run=False):
    import shutil
    import stat
    home = _agent8088_home()
    side_effects = _describe_agent8088_side_effects()
    print(f"This will permanently remove Agent8088 from: {home}")
    if side_effects:
        print("It will also remove:")
        for line in side_effects:
            print(f"  {line}")
    if dry_run:
        print("(--dry-run: nothing was removed)")
        return True
    try:
        answer = input("Are you sure you want to remove Agent8088? Type yes to continue: ")
    except EOFError:
        print("Uninstall cancelled.")
        return False
    if answer.strip() != "yes":
        print("Uninstall cancelled.")
        return False
    if not _safe_uninstall_home(home):
        print(f"Refusing to remove unsafe path: {home}")
        return False
    ...  # unchanged below
```

(`_run_windows_uninstall` gets the same `dry_run` parameter and an early-return-after-printing, in the same spot.)

**Step 4: Run test to verify pass**

Run: `pytest tests/test_posix_uninstall_full_cleanup.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/agent8088/cli.py tests/test_posix_uninstall_full_cleanup.py
git commit -m "feat: add --dry-run and an itemized uninstall confirmation"
```

---

## Task 11: Regression test for the `install.sh` `PLAYWRIGHT_BROWSERS_PATH` export

**Objective:** Prove the exact env var and value are set before the chromium install command, using the same bash-function-extraction pattern as `tests/test_installer_sudo_prompt_foreground.py`.

**Files:**
- Test: `tests/test_installer_playwright_browsers_path.py` (new) — read `tests/test_installer_sudo_prompt_foreground.py` first and copy its `_extract`/fake-bin/subprocess-invocation helpers rather than re-deriving them.

**Step 1: Write failing test**

```python
# tests/test_installer_playwright_browsers_path.py
"""install.sh must point PLAYWRIGHT_BROWSERS_PATH at $AGENT8088_HOME before
downloading Chromium, so the browser lands somewhere `--uninstall` already
cleans up instead of the OS-shared ms-playwright cache."""
import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).parent.parent / "install.sh"


def _extract(function_names):
    # Mirror tests/test_installer_sudo_prompt_foreground.py's extraction
    # helper exactly - see that file for the regex/implementation.
    ...


def test_playwright_browsers_path_exported_before_chromium_install(tmp_path):
    script = _extract(["install_python_dependencies"])  # or whichever function wraps this stage
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "calls.log"
    (fake_bin / "python3").write_text(
        f'#!/bin/sh\necho "PLAYWRIGHT_BROWSERS_PATH=$PLAYWRIGHT_BROWSERS_PATH" >> "{log}"\nexit 0\n'
    )
    (fake_bin / "python3").chmod(0o755)

    subprocess.run(
        ["bash", "-c", script],
        env={"PATH": f"{fake_bin}:/usr/bin:/bin", "AGENT8088_HOME": str(tmp_path / "home")},
        timeout=30,
    )

    assert f"PLAYWRIGHT_BROWSERS_PATH={tmp_path / 'home' / 'playwright-browsers'}" in log.read_text()
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_installer_playwright_browsers_path.py -v`
Expected: FAIL — before Task 2, no such export exists.

**Step 3: Implementation**

Already done in Task 2 — this task is test-only, added *after* Task 2's implementation per this plan's ordering. (If executing tasks in strict order, write this test right after Task 2 instead of here — listed separately for clarity of what it covers.)

**Step 4: Run test to verify pass**

Run: `pytest tests/test_installer_playwright_browsers_path.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_installer_playwright_browsers_path.py
git commit -m "test: cover PLAYWRIGHT_BROWSERS_PATH export in install.sh"
```

---

## Task 12: Regression test for the `install.ps1` `PLAYWRIGHT_BROWSERS_PATH` env var

**Objective:** Windows equivalent of Task 10, using the PowerShell-function-extraction + stub pattern from `tests/test_installer_partial_cleanup.py`.

**Files:**
- Test: `tests/test_installer_playwright_browsers_path_windows.py` (new) — read `tests/test_installer_partial_cleanup.py` first and reuse its extraction/stub approach.

**Step 1-4:** Same shape as Task 10 — extract the relevant `install.ps1` function/block, run it under `pwsh` with `$py` stubbed to a script that echoes `$env:PLAYWRIGHT_BROWSERS_PATH`, assert it equals `<Agent8088Home>\playwright-browsers`. Skip with `@pytest.mark.skipif` if `pwsh`/`powershell` isn't available, matching the existing file's skip pattern.

**Step 5: Commit**

```bash
git add tests/test_installer_playwright_browsers_path_windows.py
git commit -m "test: cover PLAYWRIGHT_BROWSERS_PATH env var in install.ps1"
```

---

## Task 13: Manual end-to-end verification (not automatable in CI)

**Objective:** Confirm the real installer + uninstaller round-trip on each OS, since sandboxed CI can't fully exercise sudo prompts, real Task Scheduler, or a real shell rc reload.

**Steps (run once per OS before merging, or ask a teammate on that OS to run it):**

1. **Linux/macOS:**
   ```bash
   bash install.sh
   grep -c "agent8088" ~/.zshrc ~/.bashrc 2>/dev/null   # note the count
   crontab -l | grep "# agent8088"                       # if you set up a schedule, confirm it's listed
   ls ~/.cache/ms-playwright 2>/dev/null || ls ~/Library/Caches/ms-playwright 2>/dev/null  # should be ABSENT now (Task 1-2 redirect it into $AGENT8088_HOME)
   ls "$HOME/.agent8088/playwright-browsers"             # should exist instead
   agent8088 --uninstall   # type "yes"
   grep "agent8088" ~/.zshrc ~/.bashrc 2>/dev/null       # should be empty
   crontab -l 2>/dev/null | grep "# agent8088"           # should be empty
   ls "$HOME/.agent8088" 2>/dev/null                      # should not exist
   ```
2. **Windows:** run `install.ps1`, confirm `[Environment]::GetEnvironmentVariable("Path","User")` contains the git/node entries, set up a schedule (`agent8088` cron-equivalent) and confirm `schtasks /Query | findstr Agent8088` shows it, then run `agent8088.exe --uninstall` and confirm both the PATH entries and the scheduled task are gone (`schtasks /Query | findstr Agent8088` returns nothing).
3. Confirm `agent8088 --uninstall --keep-data` leaves `~/Documents/agent8088/traces` and the WhatsApp session directory in place on both platforms.

Record the results in the PR description; this task has no code changes of its own.

---

## Files likely to change (summary)

- `src/agent8088/engine.py` — Task 1
- `install.sh` — Task 2
- `install.ps1` — Task 3
- `src/agent8088/cli.py` — Tasks 4, 5, 6, 7, 8, 9, 10
- `tests/test_engine_playwright_browsers_path.py` (new) — Task 1
- `tests/test_posix_uninstall_full_cleanup.py` (new) — Tasks 4, 5, 6, 7, 10
- `tests/test_windows_uninstall.py` (extended) — Tasks 8, 9
- `tests/test_installer_playwright_browsers_path.py` (new) — Task 11
- `tests/test_installer_playwright_browsers_path_windows.py` (new) — Task 12

## Validation

- `pytest tests/ -v` (full suite) after each task, not just the new file — Tasks 4-10 all touch `_run_uninstall`/`_run_windows_uninstall`, which existing tests in `test_posix_uninstall.py` and `test_windows_uninstall.py` already exercise; a regression there means an existing assumption broke.
- `bash -n install.sh` after Task 2.
- A PowerShell parse check after Task 3 (whatever the repo's existing CI step for `install.ps1` syntax is — check `.github/workflows/` for it before assuming a specific command).
- Task 13's manual round-trip before merging, since none of the above can prove the real shell-rc / registry / Task Scheduler state changes end-to-end. Include `agent8088 --uninstall --dry-run` in that manual pass to confirm it lists everything the real run would remove and deletes nothing.

## Risks, tradeoffs, and open questions

1. **The PATH rc-line removal (Task 4) only matches the exact line install.sh currently writes.** A user who hand-edited that line (different quoting, reordered `$PATH`, added a trailing comment) keeps a now-dead PATH entry after uninstall. This is a deliberate safety tradeoff (see Design Decision 5) — flag it in the PR description rather than trying to get clever with fuzzy matching, which risks the opposite failure (deleting an unrelated line).
2. **Pre-fix installs' Chromium cache is never auto-removed** (Task 7 only warns). If product wants a stronger guarantee for existing users, a follow-up could diff the cache directory's chromium revision folders against the specific revision this install's `playwright` package pinned (available via the installed package's metadata) and delete only that exact revision folder — deferred out of this plan's scope since it adds real complexity for a one-time legacy-user gap that new installs no longer create.
3. **`--keep-data`'s default-path check is exact-match only** (Task 6) — if a user's `AGENT8088_TRACE_DIR` happens to equal the literal default string, it's still treated as "default" and removed. This matches current behavior elsewhere in the codebase (e.g. `_remove_agent8088_shim`'s content-sniffing) and is an acceptable edge case.
4. **Confirm before implementing:** should `--uninstall` (no flags) really default to deleting trace logs and the WhatsApp session by default, or should it default to *keeping* user data and require an explicit `--purge-data` to remove it? This plan chose "delete by default, `--keep-data` opts out" to match the literal ask ("everything needs to be uninstalled to the state before... installation"), but it's worth a second opinion before shipping — see the Hermes Agent comparison in Prior Art, which defaults the other way, since it's the one part of this plan that deletes user-generated content rather than program artifacts.
5. **Not adopted from prior art:** OpenClaw's fully granular `--service`/`--state`/`--workspace`/`--app` flag set and Hermes's "type the name to confirm" (vs. "type yes") pattern were both considered and left out — agent8088's uninstall surface is small enough today (one install dir, no separate service/app to target) that granular component flags would be premature, and "type yes" already matches the rest of this codebase's existing confirmation style. Revisit if agent8088 grows a separate long-running service component later.
