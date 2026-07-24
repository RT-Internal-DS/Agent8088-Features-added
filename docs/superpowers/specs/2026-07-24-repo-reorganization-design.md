# Agent8088 Repo Cleanup & Reorganization Design

**Date:** 2026-07-24
**Status:** Approved (design sections confirmed in conversation)
**Scope:** Single repo restructure — no code logic changes to the agent runtime

## Goal

Reorganize the Agent8088 repo for daily-use CLI operation. The structure must:
1. Keep the agent runtime zero-config — `./agent8088` runs with no env vars or code changes.
2. Group non-runtime concerns (research, training, model config variants, repo ops scripts) into clear top-level dirs.
3. Remove files with no relationship to the agent (toy algorithms, unrelated web projects, one-time deployment docs).
4. Correct the one real defect in `config.txt`.

## Non-Goals

- Refactoring the agent's Python code (the 766-line `agent8088` script stays as-is).
- Changing the tool-calling protocol, model backend, or SkillOpt algorithm.
- Publishing to PyPI / adopting a `src/` layout (rejected as Approach B during brainstorming).

## Constraints From the Agent Runtime

The agent resolves its runtime files relative to the script's own directory (`agent8088:14,33-44`):

| File | Resolution | Default |
|---|---|---|
| `config.txt` | `AGENT8088_CONFIG` env var, else `APP_DIR/config.txt` | `./config.txt` |
| `tools.txt` | `config.tools_file`, else `APP_DIR/tools.txt` | `./tools.txt` |
| `system.md` | `config.system_file`, else `APP_DIR/system.md` | `./system.md` |
| `banner.txt` | `config.banner_file`, else `APP_DIR/banner.txt` | absent → falls back to default banner |

`APP_DIR = Path(__file__).resolve().parent` — the directory containing the `agent8088` executable. Therefore the four runtime files (`agent8088`, `config.txt`, `tools.txt`, `system.md`) must remain siblings at the repo root. Moving any of them would require either an env var or a code change.

## Target Structure

```
agent8088/                          # repo root
├── agent8088                       # main executable (stays at root)
├── config.txt                      # runtime config (stays - agent reads ./config.txt)
├── configb.txt                     # alt config (stays at root, sibling of config.txt)
├── tools.txt                       # tool specs (stays - agent reads ./tools.txt)
├── system.md                       # system prompt (stays - agent reads ./system.md)
├── README.md                       # stays
├── LICENSE                         # stays
├── pyproject.toml                  # stays
├── requirements.txt                # stays
├── .gitignore                      # stays
├── AGENTS.md                       # stays (graphify always-on integration)
│
├── configs/                        # NEW - model-config variants you swap into config.txt
│   ├── reality7b_config_colossus.py
│   └── reality7b_config_ollama.py
│
├── scripts/                        # NEW - one-off repo ops
│   ├── configure.sh
│   ├── push-to-github.sh
│   └── verify-push.sh
│
├── research/                       # NEW - non-runtime, one cd away
│   ├── skillopt.py                 # SkillOpt self-improver
│   ├── run_benchmark.py            # benchmark runner (used by skillopt)
│   ├── data_cleanup/               # dataset curation (17 files)
│   ├── vast-training/              # vast.ai training (4 files)
│   └── paper/                      # research paper (5 files)
│
├── skills/                         # stays - 20 agent skill YAMLs
└── docs/                           # NEW - for ARCHITECTURE.md etc. README references these
    └── superpowers/specs/          # this spec lives here
```

### Why each new dir

- **`configs/`** — `reality7b_config_colossus.py` and `reality7b_config_ollama.py` are alternative model-backend setups (Ollama vs Colossus endpoint) that you copy/symlink into `config.txt` when switching. Grouping them makes the swap obvious.
- **`scripts/`** — `configure.sh` (config wizard), `push-to-github.sh`, `verify-push.sh` are repo-ops one-offs, not runtime. Moving them out of root reduces clutter.
- **`research/`** — `skillopt.py`, `data_cleanup/`, `vast-training/`, `paper/`, `run_benchmark.py` are the research/training pipeline. Not part of daily CLI use; one `cd research/` away. `skillopt.py` stays executable; only its path changes.
- **`docs/`** — `README.md:250-256` already references `docs/ARCHITECTURE.md`, `docs/TRAINING.md`, `docs/DEVELOPMENT.md`, `docs/API.md`, `docs/TOOL_SCHEMA.md`. The dir doesn't exist yet; creating it fulfills the README's promises (content to be added later, out of scope here).

## Files to Remove

### Toy algorithm scripts at root (16 files)

Confirmed safe to remove by the user. The graphify analysis found zero edges between these and the agent. They are standalone algorithm-practice / experiment scripts:

`factorial.py`, `fibonacci.py`, `gcd.py`, `is_prime.py`, `palindrome.py`, `flatten.py`, `reverse_str.py`, `count_words.py`, `find_max.py`, `test_find_max.py`, `tic.py`, `get_title.py`, `download_title.py`, `get_page_title.py`, `scraper.py`, `hello.txt`

### Unrelated web projects (2 dirs, 6 files)

Confirmed delete by the user. The graph shows no edges to the agent. These are unrelated web-dev work:

- `amirweb/` — `index.html`, `server.py`, `styles.css`
- `projects/ahweb3/` — `about.html`, `index.html`, `style.css`
- `projects/` — the now-empty parent dir

### One-time deployment artifacts (2 files)

Per `DEPLOYMENT_STATUS.md:4-6`, the GitHub prep is already complete ("✅ Repository Prepared"). These docs are historical:

- `DEPLOYMENT_STATUS.md` — 220-line status doc for the one-time push
- `GITHUB_SETUP_INSTRUCTIONS.md` — account-creation walkthrough, already followed

### Total removed: ~24 files + 2 dirs

## Files Kept (With Rationale)

| File/Dir | Decision | Why |
|---|---|---|
| `skills/` (20 YAMLs) | Keep | Agent loads these as tool definitions per README. Graph shows them isolated but they're runtime config, not dead code. |
| `paper/8088_agent_paper_draft.pdf` etc. | Move to `research/paper/` | Your research, not deleted. |
| `skillopt.py` | Move to `research/` | SkillOpt ships with the agent per README:187-231, but it's a run-once optimizer, not the runtime. |
| `run_benchmark.py` | Move to `research/` | Referenced by `skillopt.py` (graph community 11); part of the research workflow. |
| `reality7b_config_*.py` | Move to `configs/` | Alternative model configs you swap in; not runtime defaults. |
| `configure.sh`, `push-to-github.sh`, `verify-push.sh` | Move to `scripts/` | Repo-ops tooling; not runtime. |
| `configb.txt` | Keep at root | Alt config, sibling of `config.txt`; swap via `AGENT8088_CONFIG=configb.txt`. |
| `banner.txt` (absent) | No action | Doesn't exist; agent falls back to default banner (`agent8088:131-141`). |

## config.txt Review

The user's `config.txt` is **structurally correct** — every key the agent reads is present and well-formed. One note on the search URL, one minor fix, and one out-of-scope flag:

### Note: `search_base_url` missing `{query}` placeholder

**Current (line 29):**
```
search_base_url=http://localhost:8888/search?q=
```

**Problem:** `tools.txt:4` defines `web_search` with `url={search_base_url}{query_q}&format=json`. The agent's `_format_with_args` (`agent8088:419-427`) substitutes `{search_base_url}` from config and `{query_q}` from args. The config value ends at `q=` with no placeholder, so the final URL becomes `http://localhost:8888/search?q=ACTUAL_QUERY&format=json` — which actually works by accident, since `{query_q}` appends right after. The comment on line 28 even says "Ends at 'q=' with no placeholder; tools.txt appends {query_q}&format=json."

**Verdict:** Not broken — the current concatenation is intentional per the comment. Leave as-is. (Flagging only because the README:29 description implies a `{query}` placeholder that isn't there; the implementation is correct, the naming is just slightly confusing.)

**Resolution: No change needed.** The comment documents the intent clearly.

### Minor note 1: `api_key` is a real key committed to the repo

**Current (line 20):**
```
api_key=YOUR_API_KEY_HERE
```

This is a live API key for the Ornith-35B model on `localhost:8080`. It's currently tracked by git (no `.gitignore` entry for `config.txt`). If this repo ever goes public, the key leaks.

**Resolution (out of scope for this restructure, flagged only):** Consider adding `config.txt` to `.gitignore` and committing a `config.example.txt` template. Not doing this as part of the cleanup since the user marked config review as "check it's correct," not "harden it."

### Minor note 2: `model_base_url` has a leading space

**Current (line 18):**
```
model_base_url= http://localhost:11434/v1
```

There's a space after `=`. The agent's `load_simple_config` (`agent8088:20-30`) does `key, value = line.split("=", 1)` then `config[key.strip()] = value.strip()` — the `.strip()` removes the leading space, so this works. But it's inconsistent with every other line (no space after `=` elsewhere).

**Resolution: Fix during implementation** — remove the leading space for consistency. Trivial one-character edit.

## Run Command (Confirmed)

From `agent8088:745-766`:

```bash
# Interactive REPL (no args)
./agent8088

# One-shot / benchmark mode
./agent8088 "your query here"

# Trace mode (emit full call chain as JSON to stderr)
./agent8088 --trace "your query here"

# Use alternate config
AGENT8088_CONFIG=./configb.txt ./agent8088
```

No changes needed — these commands work as-is after the restructure because the four runtime files stay siblings at root.

## README Updates Required

Two small edits during implementation:

1. `README.md:204` — `python3 skillopt.py` → `python3 research/skillopt.py` (and the `--epochs`/`--dry-run`/`--report`/`--restore` examples on lines 206-216).
2. `README.md:155-176` — the "Repository Structure" block diagram should be updated to match the new structure above.

## Implementation Order

1. Create the four new dirs: `configs/`, `scripts/`, `research/`, `docs/`.
2. Move files into the new dirs (git mv for tracked files).
3. Delete the removed files (git rm).
4. Remove `banner.txt` from `config.txt` line 11? — No, leave it (absent file is handled by the fallback).
5. Fix `config.txt:18` leading space (`model_base_url= http://...` → `model_base_url=http://...`).
6. Update `README.md` (two edits above).
7. Run `graphify update .` to refresh the knowledge graph.
8. Verify `./agent8088` still launches and reads config correctly.

## Verification

- `./agent8088` launches without error and prints the banner.
- `./agent8088 "say hello"` completes a one-shot query (tool-calling works).
- `AGENT8088_CONFIG=./configb.txt ./agent8088` launches with the Ollama backend.
- `python3 research/skillopt.py --report` runs (SkillOpt finds its files).
- `graphify query "Agent8088 entrypoint"` still returns the main loop nodes.

## Risks

- **Low:** `skillopt.py` may have hardcoded paths to `run_benchmark.py` or `system.md`. If so, those paths need updating after the move. The implementation step checks for this before finalizing.
- **Low:** `run_benchmark.py` may reference `skills/` or `config.txt` by relative path. Same check.
- **None for the agent runtime:** The four runtime files stay siblings at root, so the agent's `APP_DIR` resolution is unchanged.