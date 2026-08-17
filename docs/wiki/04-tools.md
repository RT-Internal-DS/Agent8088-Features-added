# Tools

[← Wiki index](README.md)

21 built-in tools, registered from `src/agent8088/tools.txt`. The `mode` column
is what the permission layer gates on — see
[Permissions & Security](03-permissions-and-security.md).

## Full inventory

| Tool | Mode | Args | readonly? | What it does |
|---|---|---|---|---|
| `read_text` | `read_text` | `filename` | ✅ | Read a file. Refuses credential files. |
| `write_file` | `write_text` | `filename`, `content` | prompt | Write a file. Path-zone + sensitive + shell-rc checked. |
| `execute_shell` | `shell` | `command` | safe list only | Run a shell command. |
| `calculate` | `python_eval` | `expression` | ✅ | Evaluate a maths expression. |
| `last_output` | `last_output` | — | ✅ | Re-read the previous tool's output without re-running it. |
| `describe_capabilities` | `introspect` | — | ✅ | Report own tools, MCP servers, skills, subagents, mode, sandbox, and active guardrails. |
| `web_search` | `search` | `query` | prompt by default | Routes to the configured backend and falls back automatically. A pinned loopback or allowlisted private-LAN SearXNG can opt into no-prompt search with `web_search_no_prompt=1`. See [Web search backends](#web-search-backends). |
| `get_page_title` | `http_get` | `url` | prompt | Fetch just a page's `<title>`. |
| `browse_page` | `browser` | `url` | prompt | Headless browser — renders JS that curl can't. |
| `run_sandboxed` | `docker` | `code` | prompt | Run a Python snippet with native OS isolation and no network, using Docker only as a fallback. Use for untrusted or risky code instead of `execute_shell`. |
| `schedule_task` | `cron` | `action`, `schedule`, `task` | `action=list` only | Add/list/remove a scheduled run. Listing is readonly-safe; adding or removing is a scheduled side effect and needs approval. |
| `spawn_subagent` | `subagent` | `agent_type`, `task` | prompt | Delegate to an isolated sub-agent. |
| `present_plan` | `plan` | `plan` | ✅ | Show a plan as markdown and ask the user to approve it (plan mode's exit point). |
| `execute_plan` | `plan` | `steps` | ✅ | Run an already-decided sequence of tool calls, verified step by step. |
| `git_status` | `shell` (host) | — | ✅ | `git status`. |
| `git_diff` | `shell` (host) | — | ✅ | `git diff`. |
| `git_log` | `shell` (host) | — | ✅ | `git log`. |
| `git_clone` | `shell` | `url`, `directory` | prompt | Clone a repo. |
| `git_commit` | `shell` | `message` | prompt | Commit staged changes. |
| `git_push` | `shell` | — | **blocked** | Refused at the always-on floor. |
| `git_create_pr` | `shell` | `title`, `body` | prompt | Open a PR via `gh`. |

> `git_status`/`git_diff`/`git_log` are declared `host=1` in `tools.txt` and
> always run directly on the host, without a prompt, regardless of sandbox
> availability or backend — see
> [Sandboxing § Interaction with git tools](06-sandboxing.md#interaction-with-git-tools).
> The equivalent command run through `execute_shell` (e.g.
> `execute_shell({"command": "git status"})`) is a generic shell call instead,
> and follows the normal sandbox-dependent rule.

## Aliases

The model can call tools by natural names; they resolve to the canonical tool:

| Says | Runs |
|---|---|
| `bash`, `sh`, `shell`, `run` | `execute_shell` |
| `search`, `web`, `google` | `web_search` |
| `read`, `cat` | `read_text` |
| `write`, `create_file`, `writefile` | `write_file` |
| `calc`, `eval`, `math` | `calculate` |
| `last`, `prev_output` | `last_output` |

## Argument fallbacks

There is no shape-rewriting resolver — a tool called with a fictitious shape
(e.g. an invented `mkdir` tool) still fails. What *does* recover is per-tool
argument-name fallbacks, so a model that reaches for a plausible but wrong key
still gets through:

- `read_text` / `write_file` accept `path` or `filepath` as fallbacks for
  `filename`.
- `run_sandboxed` accepts `script`, `python`, `source`, `snippet`, or `command`
  as fallbacks for `code`, and strips a wrapping markdown code fence
  (` ```python ... ``` `) before running it.

Combined with the tool-name aliases above, this is why the agent recovers
instead of looping when the model invents a plausible-sounding argument name
for a real tool.

## Tool modes explained

`mode` is the contract between a tool and the permission layer. Adding a tool to
`tools.txt` with an existing mode inherits that mode's gating automatically.

| Mode | Gated as |
|---|---|
| `read_text` | read — allowed in readonly |
| `write_text` | write — path zones, sensitive + shell-rc floor |
| `shell` | command classifier + sandbox |
| `http_get` / `http_post` | network + SSRF + content wrapping |
| `browser` | network + SSRF |
| `docker` | sandbox |
| `cron` | scheduled side effect |
| `subagent` | recursion-depth guarded |
| `python_eval` | pure computation — allowed in readonly |
| `last_output` | pure recall — allowed in readonly |
| `plan` | the plan-only entry point |
| `introspect` | self-report — allowed in **every** mode; touches no file, socket, or process |
| `mcp` | external MCP tool — see [MCP](07-mcp.md) |

## Adding a tool

`tools.txt` is pipe-delimited:

```
name|description|mode=<mode>|args=a,b|timeout=25
```

HTTP tools take extra fields:

```
url=https://api.example.com/search
headers=Authorization: Bearer {my_api_key};;Content-Type: application/json
body={"q": "{query}"}
filter=.results[]        # jq expression applied to the response
extract=title            # or: return only the page <title>
```

Notes that save time:

- `{placeholders}` interpolate from config *and* tool args. `{query_q}` is the
  URL-encoded variant of `{query}`.
- Headers are split on `;;`, then on the first `:` — so a `User-Agent`
  containing semicolons works fine.
- An unresolved `{placeholder}` produces a message naming the missing key,
  rather than a confusing SSRF error.
- Everything stays behind the SSRF guard, which is exactly why HTTP is a *mode*
  rather than something you'd shell out to `curl` for.

### Disabling a built-in

There is **no `disabled_tools` config key** — the loader has no such filter, so
setting one has no effect. To drop a tool, comment out its line (the parser
skips blank lines and `#`):

```
# browse_page|Load a user-supplied web page…
```

To do it without editing the installed package, copy `tools.txt`, remove the
line, and point config at your copy:

```ini
tools_file=~/.agent8088/tools.txt
```

Either way, confirm it is gone with `/tools` — the registry is what the model is
offered, so a tool absent there cannot be called at all.

## `describe_capabilities`

Ask the agent what it can do and it answers from fact, not from its own reading
of the prompt:

> **you:** what tools and MCP servers do you have?
> **agent:** *(calls `describe_capabilities`)* …

The report is generated from live state — `TOOL_SPECS` grouped by access mode,
`MCP_RUNTIME.statuses` with per-server connection state and tool lists, installed
skills, configured subagents, the resolved sandbox backend, every limit including
the ones **not** set, and the always-on floor. Because it is generated rather
than hand-maintained, it cannot drift from what the agent actually has.

Available on every surface, all from the same function, so a human and the model
never get different answers:

| Surface | How |
|---|---|
| Model | the `describe_capabilities` tool |
| CLI | `/capabilities` |
| Gateway chat | `/capabilities` |
| MCP client | exposed in the default non-mutating server surface |

It is permitted in **every** permission mode, including `readonly` and
`plan-only`: an agent that cannot say what it can do is least useful exactly when
it is most restricted. Safe to allow because it opens no file, makes no request,
and starts no process — and its output goes through the same secret redaction as
any other tool result, with no system-prompt text in it.

## Inspecting tools at runtime

```
/tools                        # list all with mode, args, description
/capabilities                 # tools + MCP + skills + limits + guardrails
/tool read_text {"filename": "README.md"}   # invoke one directly
```


## Web search backends

`web_search` is one tool with four interchangeable backends, chosen by
configuration rather than by the model picking a per-vendor tool:

| Backend | Role | Requires |
|---|---|---|
| `searxng` | **default** | Docker (`/search setup` provisions it) or an instance URL in `search_base_url` |
| `ddgs` | **fallback** | nothing — ships with agent8088 |
| `tavily` | optional — **first priority once its key is set** | `TAVILY_API_KEY` in the `.env` store |
| `exa` | optional — **priority once its key is set**, behind `tavily` | `EXA_API_KEY` in the `.env` store |

`web_search_provider` decides which one serves:

- **`auto` (the shipped default)** — at startup, probe and pick the
  highest-priority backend that can actually serve: a keyed `tavily`/`exa`
  first, else `searxng` **if it answers**, else `ddgs`. The winner is then
  pinned for the session. SearXNG has to pass a real liveness probe because a
  pin has no fallback, so pinning a stopped instance would mean no web search.
- **An explicit name** — pins exactly that backend. No auto-selection, no
  fallback. `/search use <name>` writes it.

Adding an API key is the signal to prefer that backend, so a configured
`tavily` or `exa` outranks both keyless backends; with both keys set, `tavily`
goes first. An optional backend whose key is absent stays out entirely.

**`auto` pins rather than staying dynamic, and that is deliberate.**
`web_search_no_prompt=1` only takes effect while a *local* SearXNG is the
effective pin, because approval-free search is safe only when the query cannot
leave your network. So under `auto`: SearXNG up means silent searches; SearXNG
down means `ddgs` serves and each search asks, because those queries do reach a
third party. Silent *and* external is the one combination this cannot produce.
If startup resolution is skipped entirely (an embedder calling the engine
directly), the unresolved value never matches the exemption, so it fails closed
to prompting.

If the chosen backend fails at call time — instance stopped, rate limited — the
next available one serves the request, so a broken primary does not mean "no web
search". The result always names which backend served it, so a silent fallback
is visible.

Because `ddgs` needs no key, no hosting, and no setup, web search works on a
fresh install. Run `/search status` for the live chain, `/search doctor` to
diagnose, and `/search use <backend>` to pin one.

## Self-hosting SearXNG locally

SearXNG is a free, self-hosted meta-search engine — it queries 70+ other
search engines and merges the results, with no API key and no query ever
leaving a machine you control. It's the default `web_search` backend because
of that: no signup, no per-query cost, and no third party sees what the agent
searched for.

The upstream Docker image is not usable out of the box for this purpose — it
ships with JSON output **disabled** and a bot limiter **enabled**, so a bare
`docker run searxng/searxng` produces an instance that returns HTML pages
agent8088 can't parse, and eventually starts answering with HTTP 429. Agent8088's
provisioning writes the one settings file that fixes both, then starts the
container — that's `src/agent8088/searxng_provision.py` if you want to read
the exact logic.

### Option 1 — let agent8088 provision it (recommended)

Requires Docker. Everything else is automatic:

```
/search setup
```

This:

1. Writes `~/.agent8088/searxng/settings.yml` with `search.formats: [html, json]`,
   `server.limiter: false`, and a freshly generated random `secret_key` (mode
   `0600`). If the file already exists, its `secret_key` is preserved — a
   restart doesn't invalidate anything or clobber a file you've customized.
2. Runs the `searxng/searxng:latest` image as a container named
   `agent8088-searxng`, published to **`127.0.0.1:8888` only** — never
   `0.0.0.0`. SearXNG's JSON API has no authentication, so binding it to all
   interfaces would put an unauthenticated search proxy on your local network.
3. Polls the JSON API until it answers (up to ~30s), and reports readiness.
4. Sets `search_base_url=http://127.0.0.1:8888/search?q=` in `config.txt` and
   picks `searxng` as the active backend.

Check on it any time:

```
/search status     # which backend is active, and why
/search doctor      # diagnose a broken instance
/search stop        # remove the container
```

The container restarts automatically (`--restart unless-stopped`) across
reboots and agent8088 upgrades — you generally never need to touch Docker
directly.

### Option 2 — provision it by hand

Useful if you want to run SearXNG under your own orchestration (systemd,
Compose, a NAS app store, a remote box) instead of the container agent8088
manages. The two settings that matter are the ones the upstream defaults get
wrong for this use case:

```yaml
# settings.yml
use_default_settings: true
server:
  secret_key: "<a long random string — do not use the upstream placeholder>"
  limiter: false        # off, so the JSON API doesn't get rate-limited/blocked
  image_proxy: true
search:
  formats:
    - html
    - json               # NOT enabled by default upstream — required
```

Then run the container yourself, bound to loopback (or your own LAN with a
firewall in front of it — the JSON API has no auth of its own):

```bash
docker run -d --name my-searxng --restart unless-stopped \
  -p 127.0.0.1:8888:8080 \
  -v /path/to/settings-dir:/etc/searxng \
  searxng/searxng:latest
```

Verify the JSON API actually answers before pointing agent8088 at it:

```bash
curl "http://127.0.0.1:8888/search?q=test&format=json"
```

If that returns HTML instead of JSON, `search.formats` doesn't include `json`
yet — this is the single most common misconfiguration. If it returns HTTP 403
or 429, `server.limiter` is still `true`.

### Pointing agent8088 at your instance

Whether it's the container you ran by hand, one on another machine, or a
public/shared instance someone else operates:

```ini
# config.txt
search_base_url=http://127.0.0.1:8888/search?q=
web_search_provider=auto      # or: searxng, to pin it explicitly
```

Rules enforced regardless of how the instance was provisioned:

- **A remote (non-loopback, non-private) host must use `https://`.** Plaintext
  `http://` is only accepted for `localhost`/`127.0.0.1`/private-network
  addresses — SearXNG puts every query on the wire in cleartext, which is fine
  on a box you control and not fine over the open internet.
- **The host must be reachable under the SSRF policy** — present in
  `ssrf_allow_hosts`, or a private/loopback address if `ssrf_allow_private=1`.
  Otherwise requests to it are blocked as an internal-network access attempt
  before they're even sent. See [Configuration § Security](02-configuration.md).
- **Approval-free search (`web_search_no_prompt=1`) only applies to a pinned
  loopback or explicitly allowlisted private-LAN instance.** It can never
  apply to a public host, because that would mean silently sending queries to
  a third party. See [Permissions & Security](03-permissions-and-security.md).

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "SearXNG did not return JSON" | `search.formats` doesn't include `json` | Add `json` to `search.formats` in `settings.yml`, restart the container |
| HTTP 403 / 429 from SearXNG | The bot limiter is on | Set `server.limiter: false` in `settings.yml` |
| "Could not reach SearXNG at …" | Container stopped, wrong port, or firewall | `/search doctor`, or `docker logs agent8088-searxng` if hand-run |
| Container keeps restarting | Usually a malformed `settings.yml` | `docker logs agent8088-searxng` for the crash reason; delete and let `/search setup` regenerate it if you didn't hand-edit it |
| Search silently falls back to `ddgs` | `web_search_provider=auto` and SearXNG failed its startup liveness probe | Fix the instance, then restart the CLI/gateway so `auto` re-probes and re-pins |
| Every search asks for approval even though SearXNG is up | `web_search_no_prompt` is `0`, or the pinned instance isn't loopback/allowlisted | Set `web_search_no_prompt=1` and confirm the host is loopback or in `ssrf_allow_hosts` |

## How the agent chooses a tool

Tool choice is enforced in three places, each doing only what it is good at.

**The prompt** carries the judgement calls: use the smallest tool that answers
the request, never call one for text you already have (summarizing,
translating, reasoning about readable code, writing), treat MCP tools as
belonging to the system they wrap, and always follow an explicit instruction
over any of these preferences.

**Runtime context** gives the model the current date. Without it there is only
a training cutoff, so "the next election" means whatever was next during
training and an old page reads as current.

**The engine** enforces what a prompt cannot be trusted with:

| Behaviour | What happens |
|---|---|
| Date-qualified queries | A query meaning "as of now" with no year of its own gets the current year appended — or the month, for "today"/"this week". Controlled by `search_date_augmentation` |
| Result dating | Results are stamped with their retrieval date so the model can spot a stale one |
| Repeat searches | A reworded or reordered repeat is answered from the first search's results instead of re-running. A failed or empty search stays retryable |
| Follow-up fetches | After a search succeeds, an *unsolicited* `browse_page`, `curl`-style shell command, or fetch-shaped MCP call is refused |

An **approved plan** lifts the follow-up gate for the rest of that turn. A
plan-mode turn researches with a search and then carries out the approved steps in
the same turn, so the gate would otherwise refuse work the user had just said yes
to — and naming a tool is not how they said it, so the explicit-request escape
below cannot cover it. The exemption ends when the plan's turn does.

Every gate yields to an explicit request: give a URL, name a command, or name
an MCP tool and it runs. The gates only catch tools the model reached for on
its own.
