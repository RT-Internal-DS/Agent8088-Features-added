"""Slash commands run for a non-terminal front end must never read stdin.

sys.stdin.isatty() answers "does this process have a terminal", which is not
the question the prompt helpers actually need: `agent8088 --web` started from a
shell has a tty on stdin, but that terminal belongs to the operator, not to the
browser making the request. Reading it blocks the web request forever on input
the browser cannot supply, and the prompt itself is invisible because
_handle_command has swapped the console for a StringIO buffer.
"""

import asyncio
import io

import pytest

from agent8088 import cli
from agent8088 import web_server


@pytest.fixture
def tty_stdin(monkeypatch):
    """A process that does have a terminal -- the case isatty() gets wrong."""
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)


@pytest.fixture
def no_stdin_reads(monkeypatch):
    """Make any attempt to read stdin a loud failure instead of a hang."""
    calls = []

    def explode(*args, **kwargs):
        calls.append(args)
        raise AssertionError("read stdin while prompts were disabled")

    monkeypatch.setattr(cli.console, "input", explode)
    monkeypatch.setattr("builtins.input", explode)
    return calls


def test_can_prompt_is_false_while_prompts_are_disabled(tty_stdin):
    assert cli._can_prompt() is True

    with cli.no_terminal_prompts():
        assert cli._can_prompt() is False

    assert cli._can_prompt() is True


def test_no_terminal_prompts_restores_the_previous_state_when_nested(tty_stdin):
    with cli.no_terminal_prompts():
        with cli.no_terminal_prompts():
            assert cli._can_prompt() is False
        assert cli._can_prompt() is False
    assert cli._can_prompt() is True


def test_confirm_destructive_proceeds_without_asking(tty_stdin, no_stdin_reads):
    """/reset, /mcp reload and /memory clear all gate on this."""
    with cli.no_terminal_prompts():
        assert cli._confirm_destructive("Discard the conversation", "(3 messages)") is True


def test_agent_without_a_task_cancels_instead_of_prompting(tty_stdin, no_stdin_reads,
                                                            monkeypatch):
    buffer = io.StringIO()
    monkeypatch.setattr(cli, "console", _Recorder(buffer, cli.console))
    monkeypatch.setattr(cli.A, "SUBAGENT_SPECS", {"auditor": {}}, raising=False)
    ran = []
    monkeypatch.setattr(cli, "_run_subagent", lambda name, task: ran.append((name, task)))

    with cli.no_terminal_prompts():
        cli.cmd_agent("auditor")

    assert ran == [], "a sub-agent was launched with no task"
    assert "cancel" in buffer.getvalue().lower()


def test_choice_prompt_returns_the_default_without_reading(tty_stdin, no_stdin_reads):
    with cli.no_terminal_prompts():
        assert cli._choice_prompt("Model:", ["a", "b"], default="b") == "b"


def test_permission_choice_returns_the_default_without_reading(tty_stdin, no_stdin_reads):
    with cli.no_terminal_prompts():
        chosen = cli._permission_choice(
            "Allow?", [("o", "once"), ("d", "deny")], "o/d: ", {"o": "o", "d": "d"}, "d")
    assert chosen == "d"


def test_select_agent_declines_when_prompts_are_disabled(tty_stdin, no_stdin_reads):
    with cli.no_terminal_prompts():
        assert cli.select_agent({"auditor": {}, "coder": {}}) is None


def test_handle_command_disables_prompts_for_the_handler(monkeypatch, tty_stdin):
    """The web bridge is what turns prompts off -- not the handlers themselves."""
    observed = []

    def handler(_rest):
        observed.append(cli._can_prompt())

    monkeypatch.setitem(cli.COMMANDS, "probe", handler)
    socket = _Socket()

    asyncio.run(web_server._handle_command(
        socket, {"command": "probe", "args": ""}, cli.A, cli))

    assert observed == [False], "the handler could still have blocked on stdin"
    assert cli._can_prompt() is True, "the flag leaked past the command"


class _Socket:
    def __init__(self):
        self.events = []

    async def send_json(self, event):
        self.events.append(event)


class _Recorder:
    """Console stand-in that captures print() and refuses input()."""

    def __init__(self, buffer, real):
        self._buffer = buffer
        self._real = real

    def print(self, *args, **kwargs):
        self._buffer.write(" ".join(str(a) for a in args) + "\n")

    def input(self, *args, **kwargs):
        raise AssertionError("read stdin while prompts were disabled")

    def __getattr__(self, name):
        return getattr(self._real, name)


# --- Piped stdin is a real input source and must keep working -------------
#
# A non-tty stdin is not the same condition as "no front end can answer".
# agent8088 is driven non-interactively by piping chat turns and o/s/d
# approvals into it, so the helpers that previously read a piped answer must
# still read it. Only the web bridge, which has no stdin to offer at all,
# turns prompting off.

@pytest.fixture
def piped_stdin(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)


def test_permission_choice_still_reads_a_piped_answer(piped_stdin, monkeypatch):
    monkeypatch.setattr(cli.console, "input", lambda *_a, **_k: "o")

    chosen = cli._permission_choice(
        "Allow?", [("once", "Once"), ("deny", "Deny")], "o/d: ",
        {"o": "once", "d": "deny"}, "deny")

    assert chosen == "once", "a piped approval was ignored in favour of the default"


def test_choice_prompt_still_reads_a_piped_answer(piped_stdin, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "2")

    assert cli._choice_prompt("Model:", ["a", "b"], default="a") == "b"


def test_agent_without_a_task_still_reads_a_piped_task(piped_stdin, monkeypatch):
    monkeypatch.setattr(cli.console, "input", lambda *_a, **_k: "audit the config")
    monkeypatch.setattr(cli.A, "SUBAGENT_SPECS", {"auditor": {}}, raising=False)
    ran = []
    monkeypatch.setattr(cli, "_run_subagent", lambda name, task: ran.append((name, task)))

    cli.cmd_agent("auditor")

    assert ran == [("auditor", "audit the config")]
