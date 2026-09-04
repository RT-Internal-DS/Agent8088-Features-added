"""The /ws receive loop must keep draining while an agent turn is in flight.

approval, plan_approval and interrupt are only ever sent *during* a turn: the
escalation card appears mid-turn and the Stop button exists for a turn that is
already running. If websocket_endpoint awaits the turn inline, none of those
frames is read until the turn is over -- the escalation waits out its own
timeout and is denied, and uvicorn's keepalive closes the socket at
ws_ping_interval + ws_ping_timeout because the unread frames stall its reader.
"""

import asyncio
import threading

import pytest
from starlette.websockets import WebSocketDisconnect

from agent8088 import web_server


class _Socket:
    """Scripted client: hands over `inbound`, then waits for `hangup`.

    A frame may be a dict, or a ("await", type) marker meaning "don't send the
    next frame until the server has sent an event of this type" — the real
    client only clicks Approve on an escalation card it has been shown.
    """

    def __init__(self, inbound, hangup):
        self._inbound = list(inbound)
        self._hangup = hangup
        self.events = []
        self._seen = asyncio.Event()
        self._awaiting = None

    async def receive_json(self):
        while self._inbound:
            frame = self._inbound.pop(0)
            if isinstance(frame, tuple) and frame[0] == "await":
                self._awaiting = frame[1]
                if not any(e.get("type") == frame[1] for e in self.events):
                    self._seen.clear()
                    await asyncio.wait_for(self._seen.wait(), timeout=5)
                continue
            return frame
        await self._hangup.wait()
        raise WebSocketDisconnect(1000)

    async def send_json(self, event):
        self.events.append(event)
        if self._awaiting and event.get("type") == self._awaiting:
            self._seen.set()


def _escalating_turn(verdicts, hangup, esc_id="esc-1", timeout=2.0):
    """A fake chat turn that escalates and blocks a worker thread on the verdict.

    Mirrors the real on_escalation: the engine runs off the event loop, so the
    wait is a threading.Event in an executor, not an await.
    """

    async def handler(ws, msg, engine, cli):
        entry = {"event": threading.Event(), "approved": False, "session_scope": False}
        web_server._pending_approvals[esc_id] = entry
        await ws.send_json({"type": "escalation", "tool_name": "write_file", "id": esc_id})
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: entry["event"].wait(timeout))
        settled = web_server._pending_approvals.pop(esc_id, entry)
        verdicts.append(settled.get("approved"))
        hangup.set()

    return handler


@pytest.fixture(autouse=True)
def _clean_pending():
    web_server._pending_approvals.clear()
    web_server._pending_plan_approvals.clear()
    web_server._interrupt_event.clear()
    yield
    web_server._pending_approvals.clear()
    web_server._pending_plan_approvals.clear()
    web_server._interrupt_event.clear()


def _run(monkeypatch, inbound, handler, hangup):
    monkeypatch.setattr(web_server, "_handle_chat", handler)
    monkeypatch.setattr(web_server, "_eng", lambda: object())
    monkeypatch.setattr(web_server, "_cl", lambda: object())
    monkeypatch.setattr(web_server.manager, "connect", _noop_connect)
    monkeypatch.setattr(web_server.manager, "disconnect", lambda ws: None)
    socket = _Socket(inbound, hangup)
    asyncio.run(asyncio.wait_for(web_server.websocket_endpoint(socket), timeout=15))
    return socket


async def _noop_connect(ws):
    return None


def test_approval_sent_during_a_turn_reaches_the_waiting_escalation(monkeypatch):
    verdicts = []
    hangup = asyncio.Event()
    inbound = [
        {"type": "chat", "text": "write a file"},
        ("await", "escalation"),          # the card has to be on screen first
        {"type": "approval", "id": "esc-1", "approved": True},
    ]

    _run(monkeypatch, inbound, _escalating_turn(verdicts, hangup), hangup)

    assert verdicts == [True], (
        "the approval was sent while the turn was running and never reached the "
        "escalation -- the receive loop was blocked on the turn"
    )


def test_interrupt_sent_during_a_turn_is_observed(monkeypatch):
    seen = []
    hangup = asyncio.Event()

    async def handler(ws, msg, engine, cli):
        loop = asyncio.get_running_loop()
        # Poll the way interrupt_check does, from off the loop.
        await loop.run_in_executor(None, lambda: _wait_flag(web_server._interrupt_event, 2.0))
        seen.append(web_server._interrupt_event.is_set())
        hangup.set()

    inbound = [{"type": "chat", "text": "long essay"}, {"type": "interrupt"}]

    _run(monkeypatch, inbound, handler, hangup)

    assert seen == [True], "interrupt sent mid-turn was not read until the turn ended"


def test_second_chat_while_a_turn_runs_is_refused_not_interleaved(monkeypatch):
    started = []
    hangup = asyncio.Event()
    release = threading.Event()

    async def handler(ws, msg, engine, cli):
        started.append(msg.get("text"))
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: release.wait(2.0))
        hangup.set()

    inbound = [
        {"type": "chat", "text": "first"},
        {"type": "chat", "text": "second"},
    ]

    async def drive():
        monkeypatch.setattr(web_server, "_handle_chat", handler)
        monkeypatch.setattr(web_server, "_eng", lambda: object())
        monkeypatch.setattr(web_server, "_cl", lambda: object())
        monkeypatch.setattr(web_server.manager, "connect", _noop_connect)
        monkeypatch.setattr(web_server.manager, "disconnect", lambda ws: None)
        socket = _Socket(inbound, hangup)
        task = asyncio.create_task(web_server.websocket_endpoint(socket))
        # Give the loop time to read both frames before the first turn ends.
        await asyncio.sleep(0.3)
        release.set()
        await asyncio.wait_for(task, timeout=15)
        return socket

    socket = asyncio.run(drive())

    assert started == ["first"], f"the second turn should not have started: {started}"
    refusals = [e for e in socket.events if e.get("type") == "error"]
    assert refusals, "the client got no feedback that its second message was dropped"


def _wait_flag(event, timeout):
    """threading.Event.wait, spelled out so the lambda above stays readable."""
    return event.wait(timeout)
