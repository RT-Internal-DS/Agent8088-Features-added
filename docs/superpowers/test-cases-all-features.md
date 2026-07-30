# Agent8088 — Comprehensive Test Case Prompts

> Manual end-to-end test prompts covering every feature.  
> Run these in the Rich UI (`agent8088`).

---

## 1. Tool Basics — one call per tool

| # | Prompt | Expected | Feature |
|---|---|---|---|
| 1.1 | `what is 17 * 23 + 4` | Tool call `calculate`, returns 395 | calculate / python_eval |
| 1.2 | `read tools.txt` | Tool call `read_text`, shows the 7 tool lines | read_text |
| 1.3 | `what was the last tool output` | Returns the previous tool call's result | last_output |
| 1.4 | `run ls in the current directory` | Tool call `execute_shell` with `ls`, lists files | execute_shell |
| 1.5 | `write a file called /tmp/hello.txt with content "hi from 8088"` | Tool call `write_file`, writes to /tmp (no prompt — no_prompt zone) | write_file |
| 1.6 | `search for the capital of France` | `web_search` → y/n prompt (network gate) | web_search |
| 1.7 | `get the page title of https://example.com` | `get_page_title` → y/n prompt (network gate) | get_page_title |

---

## 2. Tool Alias Resolution

| # | Prompt | Expected | Feature |
|---|---|---|---|
| 2.1 | `bash echo hello` | `bash` → `execute_shell`, runs `echo hello` | Alias: bash |
| 2.2 | `sh echo hello` | `sh` → `execute_shell` | Alias: sh |
| 2.3 | `shell echo hello` | `shell` → `execute_shell` | Alias: shell |
| 2.4 | `run whoami` | `run` → `execute_shell` | Alias: run |
| 2.5 | `cat tools.txt` | `cat` → `read_text` (alias map, not shell) | Alias: cat |
| 2.6 | `read tools.txt` | `read` → `read_text` | Alias: read |
| 2.7 | `search the web for 2026 world series winner` | `search` → `web_search` (prompts y/n) | Alias: search |
| 2.8 | `google the web for 2026 world series winner` | `google` → `web_search` | Alias: google |
| 2.9 | `web query python release` | `web` → `web_search` | Alias: web |
| 2.10 | `calc 2+2` | `calc` → `calculate`, returns 4 | Alias: calc |
| 2.11 | `eval 2+2` | `eval` → `calculate` | Alias: eval |
| 2.12 | `math 2+2` | `math` → `calculate` | Alias: math |
| 2.13 | `last output` | `last` → `last_output` | Alias: last |
| 2.14 | `prev_output` | `prev_output` → `last_output` | Alias: prev_output |
| 2.15 | `write /tmp/alias_test.txt with content "test"` | `write` → `write_file` | Alias: write |
| 2.16 | `create_file /tmp/alias_test.txt with content "test"` | `create_file` → `write_file` | Alias: create_file |

---

## 3. Tool Arg Transforms

| # | Prompt | Expected | Feature |
|---|---|---|---|
| 3.1 | `mkdir a folder called /tmp/arg_test` | Model emits `mkdir({path:"/tmp/arg_test"})` → transforms to `execute_shell({command:"mkdir /tmp/arg_test"})`. No prompt (/tmp = no_prompt). | mkdir transform |
| 3.2 | `touch a file at /tmp/x_arg.txt` | `touch({path:...})` → `execute_shell({command:"touch /tmp/x_arg.txt"})` | touch transform |
| 3.3 | `copy /tmp/hello.txt to /tmp/hello_arg.txt` | `cp({src:"/tmp/hello.txt",dst:"/tmp/hello_arg.txt"})` → `execute_shell({command:"cp /tmp/hello.txt /tmp/hello_arg.txt"})` | cp transform (two args) |
| 3.4 | `move /tmp/x_arg.txt to /tmp/y_arg.txt` | `mv({src,dst})` → `execute_shell({command:"mv ... ..."})` | mv transform |
| 3.5 | `delete the file /tmp/hello_arg.txt` | `rm({path:...})` → `execute_shell({command:"rm ..."})`. Prompts y/n (rm not in readonly-safe). | rm transform + escalation |
| 3.6 | `remove the empty directory /tmp/arg_test` | `rmdir` → `execute_shell({command:"rmdir ..."})` | rmdir transform |
| 3.7 | `echo "hello from 8088"` | `echo({text:"hello from 8088"})` → `execute_shell({command:"echo hello from 8088"})` | echo transform |
| 3.8 | `show the current directory path` | `pwd` → `execute_shell({command:"pwd"})` | pwd transform |
| 3.9 | `chmod 755 /tmp/y_arg.txt` | `chmod({mode:"755", path:"/tmp/y_arg.txt"})` → `execute_shell({command:"chmod 755 /tmp/y_arg.txt"})` | chmod transform |
| 3.10 | `find files in /tmp` | `find({path:"/tmp"})` → `execute_shell({command:"find /tmp"})` | find transform |
| 3.11 | `git status` | `git({subcommand:"status"})` → `execute_shell({command:"git status"})`. No prompt (readonly-safe). | git transform + readonly whitelist |
| 3.12 | `pip list` | `pip({command:"list"})` → `execute_shell({command:"pip list"})` | pip transform |
| 3.13 | `python --version` | `python({command:"--version"})` → `execute_shell({command:"python --version"})` | python transform |
| 3.14 | `node --eval "console.log('hi')"` | `node({command:"--eval \"console.log('hi')\""})` → `execute_shell` | node transform |

---

## 4. Permission Layer — Readonly Gate

| # | Prompt | Steps | Expected | Feature |
|---|---|---|---|---|
| 4.1 | `delete the file /tmp/hello.txt` | Type `y` when prompted | Escalation panel (yellow, rounded). After approval, runs `rm /tmp/hello.txt`. Reverts to readonly after. | One-shot grant + escalation panel |
| 4.2 | `what files are in the current directory` | — | `ls` is in READONLY_SAFE_COMMANDS → runs with **no** prompt | Readonly-safe commands |
| 4.3 | `show git log of last 3 commits` | — | `git log` subcommand allowed → no prompt | Git subcommand whitelist |
| 4.4 | `show the git diff` | — | `git diff` allowed → no prompt | Git subcommand whitelist |
| 4.5 | `show the git branch` | — | `git branch` allowed → no prompt | Git subcommand whitelist |
| 4.6 | After 4.1 completes, immediately ask `delete /tmp/hello_again.txt too` | Type `y` again | Prompts y/n **again** — one-shot grant reverted after 4.1 | Revert to readonly |
| 4.7 | `read tools.txt` | — | `read_text` always allowed in readonly | Read-only allowed modes |
| 4.8 | `calculate 2^10` | — | `calculate` always allowed in readonly | Read-only allowed modes |
| 4.9 | Run `agent8088 --edit` then `delete /tmp/hello.txt` | — | No escalation — edit mode from start | `--edit` flag |
| 4.10 | Run `AGENT8088_PERMISSION=edit agent8088`, then enter `delete /tmp/hello.txt` | — | No permission prompt | Env var override |

---

## 5. Security Layer 1 — Sensitive File Blocklist

| # | Prompt | Expected | Feature |
|---|---|---|---|
| 5.1 | `read the .env file` | Returns `"Access to sensitive file denied"` — blocked immediately, no escalation | Exact filename match |
| 5.2 | `show me config.txt` | Blocked (config.txt in blocklist) | Exact filename match |
| 5.3 | `read /tmp/id_rsa` | Blocked (id_rsa in blocklist) | Exact filename match |
| 5.4 | `cat the file at /tmp/my_API_KEY.txt` | Blocked (glob match `*_KEY*`) | Sensitive glob |
| 5.5 | `read /tmp/creds.pem` | Blocked (extension `.pem`) | Sensitive extension |
| 5.6 | `read configb.txt` | Blocked (configb.txt in blocklist) | Exact filename match |
| 5.7 | `read /tmp/notes.txt` | **Allowed** — no sensitive match, runs normally | Negative case |
| 5.8 | `read /tmp/ssh_info` | **Allowed** — only `.ssh/` as directory is blocked, `/tmp/ssh_info` is a file | Negative case |
| 5.9 | Add `allowed_sensitive_files=.env` to config, restart, then `read .env` | **Allowed** — config override unblocks it | Config override |

---

## 6. Security Layer 2 — Network Access Gate

| # | Prompt | Steps | Expected |
|---|---|---|---|
| 6.1 | `search for the population of Japan` | Type `y` | `web_search` called → yellow prompt showing SearXNG URL. Runs once. |
| 6.2 | Immediately after 6.1: `now search for the capital of Japan` | Type `y` | **Prompts y/n again** — one-shot per request, not per session |
| 6.3 | `get the page title of https://example.com` | Type `y` | `get_page_title` → y/n with URL → runs `python -c "urllib..."` cross-platform |
| 6.4 | In the 6.3 prompt, type `n` | — | Model told permission denied, says the task can't be completed |

---

## 7. Security Layer 3 — Path-Based Write Zones

| # | Prompt | Expected | Feature |
|---|---|---|---|
| 7.1 | `create a file at /tmp/zone_test.txt with content "x"` | Writes to `/tmp` — **no prompt** (in `no_prompt_paths`) | no_prompt zone |
| 7.2 | `write a file called ./zone_test.txt with content "x"` | Prompts y/n (in `prompt_paths=.`) | prompt zone |
| 7.3 | (uncomment `blocked_paths=/etc,/home` in config first) `write "x" to /etc/blocked_test.txt` | Always blocked — denied even after y/n approval | blocked zone |
| 7.4 | (with blocked_paths uncommented) `mkdir /home/agent_test` | Blocked — shell mutating commands also check blocked paths | Blocked path + shell |

---

## 8. Hard Rules — Always Forbidden

| # | Prompt | Expected | Feature |
|---|---|---|---|
| 8.1 | `git push the current branch` | Blocked — git push never allowed | Hard rule |
| 8.2 | `do a git push --force` | Blocked | Hard rule |
| 8.3 | `run git reset --hard` | Blocked | Hard rule |
| 8.4 | `delete the current git branch` | Blocked | Hard rule |
| 8.5 | `read the .ssh/id_ed25519 file` | Blocked (sensitive path) | Sensitive file hard rule |
| 8.6 | `write to /var/log/x.log` | Blocked (outside allowed_paths) unless `/var/log` is in allowed_paths | Path restriction |

---

## 9. Cross-Platform Shell

| # | Prompt | Expected | Feature |
|---|---|---|---|
| 9.1 | `list files in this directory` | On Windows → `dir` command. On Linux → `ls`. | Platform detection in `_exec_shell_command` |
| 9.2 | `show system info` | On Windows → `systeminfo` or `ver`. On Linux → `uname -a`. | Cross-platform readonly commands |
| 9.3 | `show currently running processes` | On Windows → `tasklist`. On Linux → `ps aux`. | Cross-platform commands |
| 9.4 | `get the page title of https://python.org` | Uses Python `urllib` + `re`, not Linux-only `grep -oP`. Works on both platforms. | Cross-platform get_page_title |

---

## 10. Rich UI Slash Commands

Run these inside `agent8088`:

| # | Slash Command | Expected | Feature |
|---|---|---|---|
| 10.1 | `/tools` | Lists all 7 tools with args, mode, and description | Tool listing |
| 10.2 | `/tool execute_shell {"command":"echo hi"}` | Direct tool call, result shown in rich panel | Direct tool invocation |
| 10.3 | `/tool write_file {"filename":"/tmp/slash_test.txt","content":"hello"}` | Direct write_file call via /tool | Direct tool invocation |
| 10.4 | `/plan read tools.txt\nwrite summary to /tmp/plan_summary.txt` | Live checklist with on_step for each step, mixed readonly/write | Plan executor |
| 10.5 | `/plan ["read tools.txt", "write summary to /tmp/plan_summary2.txt"]` | Same, but JSON array format | Plan executor (JSON) |
| 10.6 | `/raw what is the capital of Mongolia` | One raw model call showing content + reasoning + tool_calls fields | Raw model call |
| 10.7 | `/model` | Shows current backend model | Model display |
| 10.8 | `/model gemma` | Switches to Gemma backend (if configured) | Model switch |
| 10.9 | `/model ornith` | Switches back to Ornith | Model switch |
| 10.10 | `/config` | Shows current config values | Config display |
| 10.11 | `/system` | Shows the full system prompt including rendered tool docs | System prompt display |
| 10.12 | `/history` | Shows the conversation history | History display |
| 10.13 | `/trace on` | Enables JSON trace; next query shows step-by-step trace | Trace |
| 10.14 | `/trace off` | Disables trace | Trace |
| 10.15 | `/temp 0.5` | Sets temperature to 0.5 | Temperature control |
| 10.16 | `/maxturns 3` | Limits agent to 3 turns | Max turns control |
| 10.17 | `/save /tmp/session.json` | Saves conversation + last trace to JSON file | Session save |
| 10.18 | `/clear` | Clears conversation context | Context clear |
| 10.19 | Press **ESC** during a long generation | `AgentInterrupted` raised, agent stops, returns to prompt | EscListener |

---

## 11. Escalation Flow — Full Cycle

| # | Steps | Expected | Feature |
|---|---|---|---|
| 11.1 | Start `agent8088` (readonly). Ask `create a folder ./test_perm`. | Escalation panel (yellow, rounded). Type `y`. Model receives "Permission granted. Retry the tool call that was blocked." Retries `mkdir` → succeeds. | Full escalation cycle |
| 11.2 | Same as 11.1 but type **`n`** | "Permission denied — staying in readonly mode." Model tells you the task can't be completed. Does not retry. | Denial path |
| 11.3 | Start `agent8088 --edit`. Ask `create a folder ./test_perm2`. | No escalation — edit mode from start. | `--edit` flag |
| 11.4 | Start `AGENT8088_PERMISSION=edit agent8088`, then enter `list files` | Edit mode, no prompts | Env var override |

---

## 12. Backward Compatibility

| # | Command | Expected | Feature |
|---|---|---|---|
| 12.1 | Start `agent8088`, then enter `what is 2+2` | Answers 4 | Interactive execution |
| 12.2 | `python research/run_benchmark.py` | Runs benchmarks without escalation prompts (sets `AGENT8088_PERMISSION=edit` internally) | Benchmark backward compat |

---

## 13. Error / Edge Cases

| # | Prompt | Expected | Feature |
|---|---|---|---|
| 13.1 | `read /tmp/nonexistent_file_xyz.txt` | Tool returns file-not-found error. Model says it failed — never fabricates. | Error handling |
| 13.2 | Write `./CHANGELOG.md` with `"lol"` **without reading it first** | Blocked — "overwriting a file whose contents you have not read" is forbidden even in edit mode | Unread-file overwrite rule |
| 13.3 | `write to /var/log/x.log` | `resolve_user_path()` raises ValueError → blocked (outside `allowed_paths`) | Allowed_paths enforcement |
| 13.4 | `what is the meaning of life?` | Plain answer, no tool call | No-tool path |
| 13.5 | `run the frobnicate tool` | Unknown tool name → silently skipped in `find_tool_calls()` or reported as unknown | Unknown tool handling |
| 13.6 | `write a file exactly named "" ` | Empty filename → error handling | Edge: empty filename |
| 13.7 | `read a file with a path like ~/nonexistent` | Tilde expansion works, file not found error | Tilde expansion |

---

## Quick Smoke Test — 5 Prompts

For a fast pass/fail across the most features:

| Step | Prompt | What It Covers |
|---|---|---|
| 1 | `read tools.txt` | read_text + allowed_paths + not sensitive |
| 2 | `search the web for python release notes 2026` | web_search alias + network gate y/n + one-shot |
| 3 | `mkdir /tmp/smoke_test_quick` | arg transform + no_prompt zone |
| 4 | `write a file ./smoke_quick.txt with content "hello"` | write_file + prompt zone + escalation y/n + one-shot reverts |
| 5 | `/plan read tools.txt\nwrite a summary to /tmp/smoke_summary.txt` | plan executor + on_step + mixed readonly/write steps |
