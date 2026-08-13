# Permission Modes Gap Analysis — Agent8088 vs Hermes Agent & OpenClaw

> **Status:** Analysis only. No implementation yet. For discussion before any code changes.
> **Date:** 2026-08-03
> **Scope:** Capability #1 (Permission modes) from `Agent8088_Scope (1).md` §4.
> **Sources:** Agent8088 `engine.py` / `cli.py`; Hermes Agent security docs (`hermes-agent.nousresearch.com/docs/user-guide/security`); OpenClaw security + sandboxing docs (`docs.openclaw.ai/gateway/security`, `docs.openclaw.ai/gateway/sandboxing`).

---

## 1. What Agent8088 has today

The scope doc says TODAY is "Path allowlist only" — the code has grown beyond that. Four permission layers are implemented:

### 1.1 Operating modes (2 of 5 target modes)

| Mode | Status | How it works |
|---|---|---|
| `readonly` (default) | **Implemented** | Read tools allowed; writes/shell blocked; escalates via y/n prompt |
| `edit` / `full-auto` | **Implemented** | Full auto-approve, no prompts (`--edit` flag or `AGENT8088_PERMISSION=edit`) |
| `ask-per-action` | **Missing** | Prompt y/n on every tool call (even reads) — not a distinct mode |
| `auto-approve-safe` | **Missing** | Auto-approve reads + safe ops; prompt only for writes/destructive |
| `plan-only` | **Missing** | Only `execute_plan` runs; direct tools blocked — forces planning first |

Source: `engine.py:179` (`PERMISSION_MODE`), `engine.py:401-425` (`check_permission`), `cli.py:2341-2342` (`--edit` flag).

### 1.2 Permission layers (4 implemented)

| Layer | Location | What it does |
|---|---|---|
| Sensitive file read protection | `engine.py:187-226` | Blocklist of filenames (`.env`, `config.txt`, `id_rsa`…), extensions (`.pem`, `.key`), globs (`*_SECRET*`), config override via `allowed_sensitive_files` |
| Network / SSRF guard | `engine.py` (http tools) | Blocks internal IPs/bad schemes; `allow_private` opt-out; `web_search`/`get_page_title` always prompt |
| Path-based write zones | `engine.py:244-255` | Three tiers from config: `no_prompt_paths` (auto-approve, e.g. `/tmp`), `prompt_paths` (y/n, e.g. `~`), `blocked_paths` (always blocked, even in edit, e.g. `/etc`) |
| Shell command classification | `engine.py:257-398` | `READONLY_SAFE_COMMANDS` allowlist (ls, cat, grep…), `_hard_blocked_shell()` blocks destructive git (push, reset --hard, branch -D), shell wrappers (bash -c), command substitutions (`$()`, backticks) |

### 1.3 Escalation flow (implemented)

- `check_permission()` → blocks → `request_escalation()` returns `ESCALATION_REQUEST:target_mode:change_type:paths:reason` (`engine.py:428-433`)
- CLI `_handle_escalation()` (`cli.py:671-699`) shows a y/n panel → `grant_escalation()` sets a **one-shot grant** (one blocked tool runs, then reverts to readonly)
- Special grants: `_local_fallback_grant` (sandbox fallback), `_remote_git_grant` (git push) — `engine.py:436-447`

### 1.4 Sandbox integration

- `check_permission()` checks `_resolve_sandbox_backend()` — if no sandbox available in readonly, still escalates for local execution (`engine.py:417-419`)
- Sandbox backends: native (sandbox-runtime), docker, local — `engine.py:1765-1807`

---

## 2. Gaps vs. Hermes Agent and OpenClaw

### A. The always-on floor (both competitors fix this — Agent8088's biggest hole)

**The problem:** `check_permission()` returns `True` immediately in edit mode (`engine.py:407`), bypassing `_hard_blocked_shell` entirely. Edit mode = zero safety floor.

**Hermes Agent:**
- **Hardline blocklist** — `rm -rf /`, fork bombs (`:(){ :|:& };:`), `mkfs.*` on mounted root, `dd if=/dev/zero of=/dev/sd*`, pipe-to-shell at rootfs — refused **regardless of** `--yolo`, `approvals.mode: off`, cron `approve` mode, or "allow always" clicks.
- Blocklist trips **before** the approval layer sees the command. No override flag.
- Source: `tools/approval.py::UNRECOVERABLE_BLOCKLIST`

**OpenClaw:**
- Blocked bind sources by default: system paths (`/etc`, `/proc`, `/sys`, `/dev`, `/root`, `/boot`), Docker socket dirs, credential roots (`~/.aws`, `~/.ssh`, `~/.gnupg`, `~/.netrc`).
- Symlink-parent escapes resolved through deepest existing ancestor and re-checked — fail closed.
- `tools.exec.strictInlineEval` — block `-c`/`-e` in allowlist mode so inline eval still needs approval.

**Agent8088 gap:** `_hard_blocked_shell()` exists but is skipped in edit mode. No always-on floor. No symlink resolution on path checks. No inline-eval guard.

---

### B. Approval UX (Hermes is far ahead)

| Feature | Hermes | OpenClaw | Agent8088 |
|---|---|---|---|
| **Smart approval** (LLM assesses risk: low→auto-approve, dangerous→auto-deny, uncertain→prompt) | ✅ `approvals.mode: smart` | ❌ | ❌ all-or-nothing y/n |
| **Approval granularity** (once / session / always / deny) | ✅ 4 options | ✅ ask=always/session | ❌ one-shot only |
| **Permanent allowlist** (persists across sessions) | ✅ `command_allowlist` in config | ✅ | ❌ |
| **User-defined deny rules** (`approvals.deny` globs) | ✅ editable, pre-yolo | ✅ `deny` lists | ❌ hardcoded only |
| **Approval timeout** (fail-closed after N seconds) | ✅ `approvals.timeout` (default 300s) | ✅ | ❌ blocks forever (CLI) / impossible (gateway) |
| **Approval history mining** (`approvals suggest`) | ✅ proposes allowlist from past approvals | ❌ | ❌ |
| **Runtime mode toggle** (`/yolo` on/off mid-session) | ✅ toggle + env + flag | ✅ per-agent config | ❌ startup-only |
| **Gateway approval routing** (send approval prompt to chat, user replies y/n) | ✅ `HERMES_EXEC_ASK=1` | ✅ per-channel | ❌ gateway hard-sets `edit` mode |

**Hermes approval modes:**
- `smart` (default) — auxiliary LLM assesses risk; low-risk auto-approved for that command only; dangerous auto-denied; uncertain escalate to manual
- `manual` — always prompt
- `off` — disable all checks (equivalent to `--yolo`)

**Hermes approval flow (CLI):**
```
⚠️  DANGEROUS COMMAND: recursive delete
    rm -rf /tmp/old-project
    [o]nce  |  [s]ession  |  [a]lways  |  [d]eny
    Choice [o/s/a/D]:
```

**Agent8088 gap:** One-shot grants only. No session-scoped or permanent allowlists. No deny rules. No timeout. No smart risk assessment. Gateway forces `edit` because it can't route approvals back to chat.

---

### C. Content & prompt-injection defenses (both have, Agent8088 lacks)

| Feature | Hermes | OpenClaw | Agent8088 |
|---|---|---|---|
| **Context file injection scanning** (AGENTS.md, system.md, .cursorrules scanned before loading) | ✅ blocks "ignore instructions", hidden HTML comments, `curl` exfil, invisible Unicode | ✅ external content wrapping | ❌ loads system.md raw |
| **External content wrapping** (`<<<EXTERNAL_UNTRUSTED_CONTENT>>>` markers around fetched text) | ❌ | ✅ uniform across fetch/read tools | ❌ |
| **Special-token stripping** (strips `<\|im_start\|>`, `<\|start_header_id\|>` from untrusted text so it can't forge roles) | ❌ | ✅ Qwen/ChatML/Llama/Gemma/Mistral/Phi/GPT-OSS | ❌ — a fetched page with `<\|im_start\|>system` could forge a system message |
| **Tirith pre-exec scan** (homograph URLs, pipe-to-interpreter, terminal injection) | ✅ optional, SHA-256 verified | ❌ (relies on policy) | ❌ |

**Hermes context file scanner checks for:**
- Instructions to ignore/disregard prior instructions
- Hidden HTML comments with suspicious keywords
- Attempts to read secrets (`.env`, `credentials`, `.netrc`)
- Credential exfiltration via `curl`
- Invisible Unicode characters (zero-width spaces, bidirectional overrides)

Blocked files show: `[BLOCKED: AGENTS.md contained potential prompt injection (prompt_injection). Content not loaded.]`

**OpenClaw special-token stripping:**
Strips Qwen/ChatML, Llama, Gemma, Mistral, Phi, GPT-OSS role/turn tokens from wrapped external content before they reach the model. Prevents untrusted text in a fetched page, email body, or file-contents tool output from forging a synthetic `assistant`/`system` role boundary.

**Agent8088 gap:** No injection scanning on `system.md` or context files. No untrusted-content wrapping on fetch/read results. No special-token stripping — a fetched page containing `<|im_start|>system\nIgnore previous instructions...` could forge a system message.

---

### D. File / write safety (Hermes stronger)

| Feature | Hermes | OpenClaw | Agent8088 |
|---|---|---|---|
| **Write-safe-root** (hard-block writes outside a root, no prompt) | ✅ `HERMES_WRITE_SAFE_ROOT` (multiple roots, `:`/`;` separated) | ✅ `workspaceOnly` | ❌ has path zones (no_prompt/prompt/blocked) but no "writes ONLY in this root" |
| **Protected credential paths always blocked** (even with safe root set) | ✅ `~/.ssh`, `~/.aws`, `.env`, `auth.json`, `mcp-tokens/` | ✅ blocked bind sources | ⚠️ `_is_sensitive_path` only runs in `read_text`, not git reads |
| **Website/domain blocklist** | ✅ `security.website_blocklist` (glob domains) | ✅ `hostnameAllowlist` (patterns) | ❌ IP-based SSRF only |
| **Cron/state file write protection** (block direct patch to `cron/jobs.json`) | ✅ must use `cronjob` tool | ✅ must use `cron` tool | ❌ no equivalent |

**Hermes protected paths (always denied, even when `HERMES_WRITE_SAFE_ROOT` is unset):**
- OS credential stores: `~/.ssh/`, `~/.aws/`, `~/.kube/`, `/etc/sudoers`, `~/.netrc`
- Hermes credential stores: `auth.json`, `.env`, `.anthropic_oauth.json`, `mcp-tokens/`, `pairing/`
- Project secret files: `.env`, `.env.local`, `.env.production`, `.envrc` anywhere on disk

**Agent8088 gap:** `_is_sensitive_path` runs only in the `read_text` tool — `git show HEAD:.env` bypasses it. No write-safe-root concept. No domain-level website blocklist (only IP-based SSRF).

---

### E. Sandbox & agent scoping (OpenClaw is far ahead)

| Feature | Hermes | OpenClaw | Agent8088 |
|---|---|---|---|
| **Per-agent access profiles** (full / read-only / no-fs-shell) | ❌ (global) | ✅ `agents.entries.*.sandbox + tools` | ❌ one global mode |
| **Sandbox modes** (off / non-main / all) | ❌ | ✅ `agents.defaults.sandbox.mode` | ❌ (one backend, no mode) |
| **Sandbox scopes** (agent / session / shared) | ❌ | ✅ `agents.defaults.sandbox.scope` | ❌ |
| **Workspace access** (none / ro / rw) | ✅ `WRITE_SAFE_ROOT` | ✅ `workspaceAccess` | ❌ |
| **`strictInlineEval`** (block `-c`/`-e` in allowlist mode) | ❌ | ✅ | ❌ — `python_eval` allowed in readonly |
| **Sub-agent delegation guardrail** (`sandbox: "require"`) | ❌ | ✅ fails fast if child not sandboxed | ❌ depth guard only |

**OpenClaw per-agent profiles:**
- `personal` agent: full access, no sandbox
- `family` agent: sandboxed + read-only tools (`deny: ["write", "edit", "apply_patch", "exec", "process", "browser"]`)
- `public` agent: sandboxed + no filesystem/shell tools

**Agent8088 gap:** One global `PERMISSION_MODE` for all agents. `python_eval` is allowed in readonly (`engine.py:412`) — the scope doc itself flags eval as "explicitly not a secure sandbox." No per-agent scoping. No sub-agent sandbox enforcement.

---

### F. Operations & observability (both have, Agent8088 lacks)

| Feature | Hermes | OpenClaw | Agent8088 |
|---|---|---|---|
| **Security audit command** (`security audit --fix --deep`) | ❌ | ✅ `openclaw security audit` | ❌ |
| **DM pairing** (code-based for unknown users) | ✅ 8-char code, 1h TTL, rate limit, lockout | ✅ pairing codes | ❌ allowlist only |
| **DM session isolation** (per-channel-peer) | ✅ | ✅ `session.dmScope` | ⚠️ per-chat keys but no isolation modes |
| **Env var filtering for sandbox** (strip KEY/TOKEN/SECRET from child env) | ✅ | ✅ | ❌ |
| **Supply-chain advisory checking** (flag compromised packages) | ✅ startup banner + `hermes doctor` | ❌ | ❌ |
| **MCP credential filtering** (only safe vars passed to MCP subprocesses) | ✅ | ✅ | ❌ (no MCP support yet) |

**OpenClaw security audit checks:**
- Inbound access (DM/group policies, allowlists)
- Tool blast radius (elevated tools + open rooms)
- Exec filesystem drift, exec approval drift
- Network exposure (bind/auth, Tailscale)
- Browser control exposure
- Local disk hygiene (permissions, symlinks)
- Plugins (loading without allowlist)
- Policy drift (sandbox settings vs. sandbox mode)
- Each finding has a structured `checkId`; `--fix` applies safe remediations

**Agent8088 gap:** No security audit. No DM pairing (allowlist only). No env var filtering for sandboxed processes. No supply-chain checking.

---

## 3. Summary — the convergence pattern

Both competitors converge on the same readonly improvements Agent8088 is missing:

1. **An always-on floor that edit mode can't bypass** (both have it) — Agent8088's `--edit` disables every safety check
2. **Approval granularity beyond one-shot** — session + always + deny rules + timeout (Hermes)
3. **Content-level defenses** — injection scanning + special-token stripping + untrusted-content wrapping (both)
4. **Per-agent scoping** instead of one global mode (OpenClaw)
5. **`strictInlineEval`** — block `python -c`/`python_eval` in restricted modes (OpenClaw)

**Single highest-impact fix (both projects agree):** make `_hard_blocked_shell` run even in edit mode. Today, `--edit` disables every safety check. That's the hole both competitors explicitly close.

---

## 4. Existing readonly-mode holes in Agent8088 (beyond competitor comparison)

These are bugs/gaps in the current implementation independent of the comparison above:

1. **`python_eval` allowed in readonly** (`engine.py:412`) — can do `import os; os.system("rm -rf /")`. Scope doc flags eval as "explicitly not a secure sandbox."
2. **`git show HEAD:.env` bypasses sensitive-file check** — `_is_sensitive_path` only runs in `read_text`. Git read commands can read any tracked file.
3. **Escalation grant is not scoped** (`engine.py:422-424`) — `_one_shot_grant` allows *one* blocked tool, but *any* tool. Approve a `write_text`, the grant also lets the next `shell` call through.
4. **Shell allowlist is hardcoded** (`READONLY_SAFE_COMMANDS` at `engine.py:258`) — `rg`, `fd`, `jq`, `bat` aren't safe in readonly because they're not in the frozenset. Not config-extensible.
5. **Reads aren't zone-restricted** — `_check_path_zone` only runs for `write_text`. A readonly agent can `cat /etc/passwd` (sandboxed) or read outside `allowed_paths`.
6. **`plan` allowed in readonly effectively breaks readonly** — `execute_plan` runs in readonly, but plan steps can include writes that escalate.
7. **No read auditing** — readonly allows reads silently. No trace entry per read.
8. **No rate limiting** — unlimited reads/shell in readonly. No "max N reads per turn" or bandwidth cap.

---

## 5. What "solid permission modes" means (target state)

From `Agent8088_Scope (1).md` §4 row #1:

> Selectable operating modes — read-only, ask-per-action, auto-approve-safe, full-auto, plan-only; per-tool and per-class rules; approval prompts surfaced to the user/app; denials returned as recoverable tool errors.

Decomposed into concrete deliverables:

| # | Deliverable | Category | Effort | Impact |
|---|---|---|---|---|
| 1 | Always-on hardline blocklist (runs in edit mode) | A — floor | Small | Critical |
| 2 | Scoped one-shot grants (grant only the approved tool+path) | B — UX | Small | High |
| 3 | `auto-approve-safe` mode (auto-approve reads, prompt writes) | A — modes | Medium | High |
| 4 | `ask-per-action` mode (prompt every tool, even reads) | A — modes | Small | Medium |
| 5 | `plan-only` mode (only execute_plan, no direct tools) | A — modes | Medium | Medium |
| 6 | `/mode` REPL command + `--mode` flag (runtime switching) | B — UX | Small | High |
| 7 | `permission_mode=` config key (default mode in config.txt) | B — UX | Trivial | Medium |
| 8 | Session-scoped + permanent allowlists (`command_allowlist` in config) | B — UX | Medium | High |
| 9 | User-defined deny rules (`approvals.deny` globs in config) | B — UX | Small | High |
| 10 | Approval timeout (fail-closed after N seconds) | B — UX | Small | Medium |
| 11 | Denials as recoverable errors (structured "permission denied: reason X") | B — UX | Small | High |
| 12 | Gateway approval routing (route y/n to Slack/WhatsApp instead of forcing edit) | B — UX | Medium | High |
| 13 | Context file injection scanning (system.md, AGENTS.md) | C — content | Medium | High |
| 14 | Special-token stripping from untrusted content | C — content | Small | High |
| 15 | External content wrapping (`<<<UNTRUSTED>>>` markers) | C — content | Small | Medium |
| 16 | `_is_sensitive_path` applied to git reads | D — files | Trivial | High |
| 17 | Write-safe-root config (`write_safe_root=` in config.txt) | D — files | Small | Medium |
| 18 | Domain-level website blocklist (`website_blocklist=` in config) | D — files | Small | Medium |
| 19 | `strictInlineEval` (block `python -c`/`python_eval` in restricted modes) | E — sandbox | Small | High |
| 20 | Per-agent access profiles (per-agent mode + tool allow/deny) | E — sandbox | Large | Medium |
| 21 | Config-extensible readonly-safe commands (`readonly_safe_commands=` in config) | B — UX | Trivial | Low |
| 22 | Security audit command (`agent8088 security audit`) | F — ops | Medium | Medium |
| 23 | DM pairing (code-based for unknown gateway users) | F — ops | Medium | Medium |
| 24 | Env var filtering for sandboxed processes | F — ops | Small | Medium |

---

## 6. Suggested grouping for discussion

Before implementation, I suggest discussing which groups to tackle. The groups are independent and can be built in any order:

- **Group 1 — Security floor** (deliverables 1, 2, 16, 19): closes the real escape paths. Smallest diffs, biggest security payoff. Both competitors agree this is the foundation.
- **Group 2 — Approval UX** (deliverables 3-11, 21): turns the permission model from "two fixed switches" into the selectable, configurable system the scope describes. Largest user-visible improvement.
- **Group 3 — Content defenses** (deliverables 13-15): protects against prompt injection and role-forging via fetched content. Both competitors have this.
- **Group 4 — File safety** (deliverables 17, 18): write-safe-root + domain blocklist.
- **Group 5 — Agent scoping** (deliverable 20): per-agent profiles. Largest effort, lowest immediate payoff for a single-user agent.
- **Group 6 — Operations** (deliverables 22-24): audit, pairing, env filtering.

---

## 7. Open questions for discussion

Before any implementation, these need decisions:

1. **Which group first?** Group 1 (security floor) is the smallest diff with the biggest payoff. Group 2 (approval UX) is the largest user-visible improvement. Which matters more right now?

2. **Smart approval mode?** Hermes uses an auxiliary LLM to assess risk (`approvals.mode: smart`). Worth adding to Agent8088, or is `auto-approve-safe` (rule-based) enough? The LLM approach adds latency + cost per command; the rule-based approach is cheaper but less nuanced.

3. **Per-agent scoping scope?** Agent8088 is single-user today. Is per-agent access profiling (OpenClaw's model) needed, or is one global mode + per-tool config rules enough?

4. **Gateway approval routing?** Should the gateway send "Allow shell `rm x`? reply y/n" to Slack/WhatsApp, or is forcing `edit` mode for gateway acceptable? Hermes routes approvals to chat; OpenClaw routes to chat. Both competitors agree gateway should be able to prompt.

5. **Context file scanning aggressiveness?** Hermes blocks files with "ignore instructions" patterns. False positive risk for legitimate instructions in `system.md`? How aggressive should the scanner be?

6. **Special-token stripping scope?** Should it apply to all `http_get`/`read_text`/`web_search` results, or only to explicitly-untrusted content? OpenClaw applies it uniformly across fetch/read tools.

7. **Backward compatibility?** The existing `./agent8088` (old REPL) and `run_benchmark.py` default to `edit` mode. The always-on floor (deliverable 1) would change their behavior — destructive commands would be blocked even in edit. Acceptable, or need an escape hatch?

8. **Config key naming?** `permission_mode=`, `approvals.deny=`, `command_allowlist=`, `write_safe_root=`, `readonly_safe_commands=` — match Hermes's names, or pick Agent8088-native names?

---

## References

- Agent8088 `engine.py` — permission layer (lines 175-447), sandbox (lines 1765-2009)
- Agent8088 `cli.py` — escalation handler (lines 671-699), `--edit` flag (line 2341)
- Agent8088 `config.txt` — path zones, sandbox config
- Agent8088 `Agent8088_Scope (1).md` §4 — capability #1 target
- Hermes Agent security docs: https://hermes-agent.nousresearch.com/docs/user-guide/security
- OpenClaw security docs: https://docs.openclaw.ai/gateway/security
- OpenClaw sandboxing docs: https://docs.openclaw.ai/gateway/sandboxing
- Existing Agent8088 permission plan: `docs/superpowers/plans/2026-07-24-permission-layer.md`