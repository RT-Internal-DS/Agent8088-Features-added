# Sandboxing

[← Wiki index](README.md)

Shell commands and `run_sandboxed` execute inside an isolation layer so a bad
command can't reach your whole filesystem or network.

## Backends

`sandbox_backend` in `config.txt`, or `AGENT8088_SANDBOX`:

| Value | Behaviour |
|---|---|
| `auto` *(default)* | Native runtime first, Docker if unavailable, otherwise refuse execution |
| `native` | Force the free OS-level sandbox |
| `docker` | Force the Docker fallback |

Check what's active:

```
/sandbox
```

## Native sandbox (recommended)

```sh
agent8088 --sandbox-setup
```

Installs the open-source Anthropic sandbox runtime — no Docker daemon, no
container images, low overhead.

**Prerequisites:**

| Platform | Needs |
|---|---|
| macOS | Node.js 20.11+, `ripgrep` |
| Linux | Node.js 20.11+, `bubblewrap`, `socat`, `ripgrep` |
| Windows | Node.js 20.11+, one UAC prompt to create a restricted sandbox account |

The Windows prompt provisions a low-privilege local account that sandboxed
commands run as — that's why it's a one-time elevation.

## Docker fallback

Used automatically under `auto` when the native runtime is missing:

```ini
docker_image=python:3.11-slim
docker_network=none
```

`docker_network=none` is the safer default — no network from inside the
container at all.

Every Docker run is also hardened regardless of config: `--memory 512m --cpus 1
--pids-limit 256 --cap-drop ALL --security-opt no-new-privileges`. Before the
container starts, the mounted workspace is walked and an empty read-only file
is bind-mounted over every path matching the sensitive-file floor (credential
files, shell startup files, etc.), so a command inside the container cannot
read them even though the workspace itself is mounted in. If more than 128 such
paths would need masking, the run is refused outright rather than masking a
subset and calling it safe.

## Network egress

Sandboxed commands have no network unless you allow specific domains:

```ini
sandbox_allowed_domains=api.example.com,pypi.org
```

This is separate from the SSRF allowlist: `sandbox_allowed_domains` governs what
a *sandboxed command* may reach; `ssrf_allow_hosts` governs what the *HTTP
tools* may reach. Shell commands that invoke a web client such as `curl` or
`wget` must contain an explicit HTTP(S) URL; that URL is checked by the same
domain and SSRF policies before the command can run. Both layers apply
independently.

## No unsandboxed fallback

When neither backend is available, Agent8088 refuses shell and code execution
and explains how to install the native runtime or Docker. Approval cannot bypass
this requirement.

Commands start in `artifacts/`, the only project directory they may write. A
read-only auditor runs tests in a disposable copy, so runtime files created by a
test disappear afterward and the real workspace remains unchanged.

## What sandboxing does *not* cover

Worth being precise, because it's easy to over-trust:

- **The permission layer is separate.** Sandboxing limits what a command can
  reach; `check_permission()` decides whether it runs at all. A dangerous
  command is refused before the sandbox is even consulted.
- **File tools don't go through it.** `read_text` / `write_file` are gated by
  path zones and the sensitive-file floor, not by the sandbox.
- **Host-side workflow tools remain explicit.** Structured operations such as a
  user-approved commit or push are permission-gated separately; arbitrary code
  never uses that path.

## Interaction with git tools

The dedicated `git_status` / `git_diff` / `git_log` tools always run directly
on the host, without a prompt and regardless of sandbox availability — reading
a repo's history isn't something a sandbox needs to mediate, and refusing them
for lack of a sandbox made `git_status` demand approval in readonly, the mode
it's most useful in. `git show HEAD:.env` is separately blocked outright in
every backend.

This host bypass is specific to those three fixed tools. Running the
equivalent command through `execute_shell` (e.g.
`execute_shell({"command": "git status"})`) is a generic shell invocation and
follows the normal rule: allowed under the native sandbox, refused if no
sandbox is available.

## Verifying it works

```sh
VERIFY_HOME="$(mktemp -d)"
AGENT8088_CONFIG=/nonexistent AGENT8088_HOME="$VERIFY_HOME" \
  python scripts/verify_features.py
rm -rf -- "$VERIFY_HOME"
```

Section 3 covers sandboxing and reports the resolved backend. If the native
runtime isn't installed you get an explicit `⊘ SKIP` naming the missing
dependency rather than a silent pass — see
[Testing & Verification](12-testing-and-verification.md).
