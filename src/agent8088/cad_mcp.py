"""Supervised build123d-mcp sessions for Agent8088's CAD workflow.

The build123d MCP server intentionally lives in Agent8088's isolated CAD
environment: its MCP v2 dependency must never replace the core agent's MCP v1
client.  This module speaks the small JSON-RPC subset required by stdio MCP so
Agent8088 owns the server process, can terminate a wedged OpenCascade kernel,
and can replay only previously successful modelling transactions.
"""
from __future__ import annotations

import ast
import atexit
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from agent8088 import cad


BUILD123D_MCP_VERSION = "0.3.83"
MAX_CODE_BYTES = 24 * 1024
MAX_EXECUTE_CALLS = 80
DEFAULT_CALL_TIMEOUT = 150
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_SUPPORTED_FORMATS = {"step", "stl", "3mf"}
_IMPORTABLE_SUFFIXES = {".step", ".stp", ".stl", ".3mf"}
_MASKED_TOOL_ERROR = re.compile(r"^Error executing tool [A-Za-z0-9_]+:?\s*$")
_MATH_SEED = "from math import cos, degrees, pi, radians, sin, sqrt, tan"

# Primitives that only exist inside the MCP execute namespace. They report on
# geometry rather than build it, so the canonical source drops the bare calls
# and refuses the constrained replay if a value from one is still referenced.
_SESSION_ONLY_NAMES = frozenset({
    "print", "measure", "clearance", "cross_sections", "find_holes",
    "find_bosses", "find_bored_bosses", "find_countersinks", "annotate",
    "find_hole_patterns", "find_edges", "align_check", "save_json",
    "set_page", "register_centerline", "named_face",
})
# Mirrors cad_worker._ALLOWED_IMPORT_ROOTS / _BLOCKED_CALLS / _BLOCKED_METHODS.
# Checked here so an inapplicable constrained replay is reported as such rather
# than surfacing as a confusing "CAD generation failed" at the end of a session.
_REPLAY_IMPORT_ROOTS = frozenset({"build123d", "math", "dataclasses", "typing"})
_REPLAY_BLOCKED_CALLS = frozenset({
    "breakpoint", "compile", "dir", "eval", "exec", "getattr", "globals",
    "hasattr", "help", "input", "locals", "open", "print", "setattr",
    "delattr", "vars", "__import__",
})
_REPLAY_BLOCKED_METHODS = frozenset({
    "communicate", "dump", "dumps", "load", "loads", "popen", "read", "run",
    "save", "saveas", "send", "system", "write",
})
_REPLAY_MAX_AST_NODES = 12_000
_REPLAY_MAX_ABS_DIMENSION = 1_000_000.0
_REPLAY_POLICY_MARKERS = (
    "is not allowed", "must define gen_step", "complexity bound",
    "iteration bound", "outside the finite CAD bound", "may not replace",
    "generator syntax error",
)

# inspect_part's real expectation contract, plus the synonyms models reach for
# first. Without this an "expected" object with a plausible-but-wrong key comes
# back as an opaque masked server error instead of a fixable message.
_EXPECTATION_KEYS = frozenset({
    "bbox", "solid_count", "holes", "bosses", "patterns", "section_varying",
    "tolerance",
})
_EXPECTATION_ALIASES = {
    "bounding_box": "bbox", "bounding_box_mm": "bbox", "bbox_mm": "bbox",
    "size": "bbox", "dimensions": "bbox", "extents": "bbox",
    "solids": "solid_count", "solid": "solid_count", "n_solids": "solid_count",
    "hole": "holes", "boss": "bosses", "pattern": "patterns",
}
_METRIC_TOLERANCE = 1e-6
# inspect_part wants a direction vector; a model writes "Z". Translating is the
# adapter's job -- otherwise a correct expectation fails on notation.
_AXIS_VECTORS = {
    "x": [1, 0, 0], "+x": [1, 0, 0], "-x": [-1, 0, 0],
    "y": [0, 1, 0], "+y": [0, 1, 0], "-y": [0, -1, 0],
    "z": [0, 0, 1], "+z": [0, 0, 1], "-z": [0, 0, -1],
}
_EXCEPTION_LINE = re.compile(
    r"^(?:[A-Za-z_][\w.]*\.)?([A-Za-z_]\w*(?:Error|Exception))\b:?\s*(.*)$"
)


def _safe_name(value: str, fallback: str = "cad_design") -> str:
    name = _SAFE_NAME.sub("_", str(value or "").strip()).strip("._")
    return (name or fallback)[:80]


def _identifier(value: str, fallback: str = "imported") -> str:
    """A build123d-safe Python name, so imported geometry can be referenced."""
    name = re.sub(r"\W+", "_", str(value or "")).strip("_")
    if not name:
        name = fallback
    if name[0].isdigit():
        name = "part_" + name
    return name[:60]


def _formats(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = value.split(",")
    elif isinstance(value, (list, tuple)):
        raw = value
    else:
        raw = [value]
    result = []
    for item in raw:
        fmt = str(item or "").strip().lower().lstrip(".")
        if not fmt:
            continue
        if fmt not in _SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported CAD format {fmt!r}; use step, stl, or 3mf."
            )
        if fmt not in result:
            result.append(fmt)
    if "step" not in result:
        result.insert(0, "step")
    return result or ["step", "stl"]


def _json_object(value: Any, label: str) -> dict:
    """Return a bounded JSON-compatible object from model-emitted input.

    Native tool calling normally supplies a ``dict``.  Text-mode models
    occasionally wrap that object in a string and use Python's single-quoted
    literal spelling.  Treating that harmless representation difference as a
    lost CAD session caused the model to retry ``cad_begin`` with no parameters
    at all.  ``ast.literal_eval`` is deliberately the only fallback: it accepts
    data literals without executing names, calls, attributes, or imports.
    The JSON round trip then rejects non-finite/non-serializable values and
    normalises tuples to arrays before anything reaches the MCP child.
    """
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        parsed = value
    else:
        raw = str(value).strip()
        if len(raw.encode("utf-8")) > MAX_CODE_BYTES:
            raise ValueError(f"{label} object is too large")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as json_exc:
            try:
                parsed = ast.literal_eval(raw)
            except (SyntaxError, ValueError, TypeError, MemoryError) as literal_exc:
                raise ValueError(
                    f"{label} must be a JSON object (single-quoted data literals "
                    f"are also accepted): {json_exc}"
                ) from literal_exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object")
    try:
        encoded = json.dumps(parsed, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain only JSON-compatible values: {exc}") from exc
    return json.loads(encoded)


def _normalise_group(group: Any) -> Any:
    """Accept axis/direction as "Z" as well as [0, 0, 1]."""
    if not isinstance(group, dict):
        return group
    result = dict(group)
    for key in ("axis", "direction"):
        raw = result.get(key)
        if isinstance(raw, str):
            vector = _AXIS_VECTORS.get(raw.strip().lower())
            if vector is None:
                raise ValueError(
                    f"expected.{key} must be x, y, z (optionally signed) or a "
                    f"3-number array; got {raw!r}"
                )
            result[key] = vector
    return result


def _normalise_expectation(value: Any) -> dict:
    """Map an expectation object onto inspect_part's documented keys."""
    expected = _json_object(value, "expected")
    if not expected:
        return {}
    normalised: dict[str, Any] = {}
    unknown: list[str] = []
    for key, item in expected.items():
        target = str(key).strip().lower()
        target = _EXPECTATION_ALIASES.get(target, target)
        if target not in _EXPECTATION_KEYS:
            unknown.append(str(key))
            continue
        if target == "bbox" and isinstance(item, dict):
            axes = {str(axis).lower(): number for axis, number in item.items()}
            if set(axes) <= {"x", "y", "z"}:
                item = axes
        if target in {"holes", "bosses", "patterns"}:
            if isinstance(item, dict):
                item = [item]
            if isinstance(item, list):
                item = [_normalise_group(group) for group in item]
        normalised[target] = item
    if unknown:
        raise ValueError(
            "expected contains unsupported key(s): " + ", ".join(sorted(unknown))
            + ". Supported keys are bbox (3-number array or {x,y,z}), "
            "solid_count, holes, bosses, patterns, section_varying, tolerance."
        )
    if not (set(normalised) - {"tolerance"}):
        raise ValueError(
            "expected must state at least one measurable expectation besides "
            "tolerance, for example {\"bbox\": [80, 50, 8], \"solid_count\": 1}."
        )
    return normalised


def _failed(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in (
        "execution failed", "securityerror", "traceback (most recent call last)",
        '"error":', "error:", "timed out", 'passes_gate": false',
        "error executing tool", "validity gate: fail",
    ))


def _metrics(payload: str) -> dict:
    """Volume/area/bbox from a measure payload, for replay equality checks."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    bbox = data.get("bbox") if isinstance(data.get("bbox"), dict) else {}
    return {
        "volume": data.get("volume"),
        "area": data.get("area"),
        "xsize": bbox.get("xsize"),
        "ysize": bbox.get("ysize"),
        "zsize": bbox.get("zsize"),
    }


def _metrics_match(live: dict, replayed: dict) -> str:
    """Empty string when the two measurements agree, else what differed."""
    problems = []
    for key, expected in live.items():
        actual = replayed.get(key)
        if expected is None or actual is None:
            if expected != actual:
                problems.append(f"{key}: {expected!r} vs {actual!r}")
            continue
        try:
            scale = max(1.0, abs(float(expected)))
            if abs(float(expected) - float(actual)) > _METRIC_TOLERANCE * scale:
                problems.append(f"{key}: {expected} vs {actual}")
        except (TypeError, ValueError):
            if expected != actual:
                problems.append(f"{key}: {expected!r} vs {actual!r}")
    return "; ".join(problems)


def _geometry_statements(blocks: list[str]) -> tuple[list[str], list[str]]:
    """Split committed blocks into geometry source and dropped analysis calls.

    A modelling session legitimately mixes construction with inspection --
    ``print(...)``, ``measure(part)``, ``find_holes(part)``. Those primitives
    exist only inside the MCP execute namespace, so carrying them into the
    canonical source would make it fail to replay for no geometric reason.
    """
    kept: list[str] = []
    dropped: list[str] = []
    for block in blocks:
        text = str(block or "")
        if text.strip().startswith("PARAMS ="):
            # Injected at module scope by the canonical file and by the
            # constrained worker; reassigning it inside gen_step is refused.
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            kept.append(text.rstrip())
            continue
        body = []
        for node in tree.body:
            call = node.value if isinstance(node, ast.Expr) else None
            name = (call.func.id if isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name) else "")
            if name in _SESSION_ONLY_NAMES:
                dropped.append(name)
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(target, ast.Name) and target.id == "PARAMS"
                       for target in targets):
                    continue
            if (isinstance(node, ast.ImportFrom) and node.module == "build123d"
                    and any(alias.name == "*" for alias in node.names)):
                continue
            if isinstance(node, ast.ImportFrom) and node.module == "math":
                seeded = {"cos", "degrees", "pi", "radians", "sin", "sqrt", "tan"}
                if all(alias.name in seeded for alias in node.names):
                    continue
            if isinstance(node, ast.Import) and any(
                    alias.name == "build123d" for alias in node.names):
                continue
            body.append(node)
        if body:
            kept.append(ast.unparse(ast.Module(body=body, type_ignores=[])))
    return kept, dropped


def _constrained_replay_blockers(source: str) -> list[str]:
    """Why Agent8088's constrained gen_step() worker cannot replay this source.

    Reported before the fact so an unreplayable-by-design session (an imported
    STEP, an in-namespace analysis value) gets the fresh-process MCP replay gate
    and an honest report entry, instead of a late, misleading generation error.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"canonical source does not parse: {exc}"]
    reasons: list[str] = []
    nodes = list(ast.walk(tree))
    if len(nodes) > _REPLAY_MAX_AST_NODES:
        reasons.append(
            f"source has {len(nodes)} AST nodes, over the "
            f"{_REPLAY_MAX_AST_NODES}-node constrained-generator bound"
        )
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] not in _REPLAY_IMPORT_ROOTS:
                    reasons.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if node.level or root not in _REPLAY_IMPORT_ROOTS:
                reasons.append(f"import from {node.module or '<relative>'}")
            for alias in node.names:
                if alias.name.startswith(("import_", "export_")):
                    reasons.append(f"I/O import {alias.name}")
        elif isinstance(node, ast.Name):
            if node.id.startswith(("import_", "export_")):
                reasons.append(f"file I/O call {node.id}()")
            elif node.id in _SESSION_ONLY_NAMES:
                reasons.append(f"MCP-only primitive {node.id}()")
            elif node.id in _REPLAY_BLOCKED_CALLS:
                reasons.append(f"blocked call {node.id}()")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith(("import_", "export_")):
                reasons.append(f"file I/O call .{node.attr}()")
            elif node.attr.startswith("_"):
                reasons.append(f"private attribute .{node.attr}")
            elif node.attr.lower() in _REPLAY_BLOCKED_METHODS:
                reasons.append(f"file-capable method .{node.attr}()")
        elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            if abs(float(node.value)) > _REPLAY_MAX_ABS_DIMENSION:
                reasons.append("numeric literal outside the finite CAD bound")
        elif isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.While,
                               ast.Yield, ast.YieldFrom)):
            reasons.append("async, while-loop, or generator execution")
    ordered: list[str] = []
    for reason in reasons:
        if reason not in ordered:
            ordered.append(reason)
    return ordered[:8]


def _canonical_source(statements: list[str], object_name: str) -> str:
    """Turn successful MCP transactions into text-to-cad's gen_step contract."""
    body = []
    for chunk in statements:
        for line in str(chunk).splitlines():
            body.append("    " + line if line.strip() else "")
    selected = str(object_name or "").strip()
    if selected == "*":
        return_line = (
            "    return Compound(children=list(_shown.values()), label='assembly')"
        )
    elif selected:
        return_line = f"    return _shown[{selected!r}]"
    else:
        return_line = "    return list(_shown.values())[-1]"
    return (
        "from build123d import *\n"
        f"{_MATH_SEED}\n\n"
        "def gen_step():\n"
        "    _shown = {}\n"
        "    def show(shape, name='shape'):\n"
        "        _shown[str(name)] = shape\n"
        "        return shape\n"
        + "\n".join(body).rstrip() + "\n"
        + "    if not _shown:\n"
        + "        raise RuntimeError('CAD source registered no geometry with show()')\n"
        + return_line + "\n"
    )


class CadMCPError(RuntimeError):
    """A bounded, user-safe CAD MCP failure."""


class CadToolError(CadMCPError):
    """The server is healthy and rejected this call.

    Deterministic: repeating it restarts nothing and changes nothing, so the
    supervisor must not tear down a live session's geometry over it.
    """


class CadTransportError(CadMCPError):
    """The owned process timed out, died, or broke protocol: restart and replay."""


class _StdioMCP:
    """Owned stdio JSON-RPC process with hard timeout and process-tree cleanup."""

    def __init__(self, python: Path, cwd: Path, timeout: int = DEFAULT_CALL_TIMEOUT):
        self.python = Path(python)
        self.cwd = Path(cwd)
        self.timeout = max(10, int(timeout))
        self.process: subprocess.Popen | None = None
        self._responses: dict[int, queue.Queue] = {}
        self._responses_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._request_id = 0
        self._stderr = deque(maxlen=200)
        self._stderr_total = 0
        self.tools: set[str] = set()

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        self.cwd.mkdir(parents=True, exist_ok=True)
        env = {
            key: value for key, value in os.environ.items()
            if key.upper() in {
                "PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATHEXT",
                "COMSPEC", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
                "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LANG", "LC_ALL",
            }
        }
        env.update({
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "BUILD123D_IN_PROCESS": "1",
            "BUILD123D_DISABLE_TOOL_GROUPS": "drawing",
            "BUILD123D_EXEC_TIMEOUT": str(self.timeout),
        })
        flags = 0
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        self.process = subprocess.Popen(
            [str(self.python), "-I", "-m", "build123d_mcp.cli", "--in-process",
             "--disable-tool-groups", "drawing", "--exec-timeout", str(self.timeout)],
            cwd=str(self.cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            bufsize=1, creationflags=flags, **kwargs,
        )
        threading.Thread(target=self._read_stdout, daemon=True,
                         name="agent8088-cad-mcp-out").start()
        threading.Thread(target=self._read_stderr, daemon=True,
                         name="agent8088-cad-mcp-err").start()
        initialized = self.request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "agent8088-cad", "version": "1"},
        }, timeout=30)
        if not isinstance(initialized, dict):
            raise CadTransportError("build123d-mcp returned an invalid initialize response")
        self.notify("notifications/initialized", {})
        listed = self.request("tools/list", {}, timeout=30)
        self.tools = {
            str(item.get("name")) for item in listed.get("tools", [])
            if isinstance(item, dict) and item.get("name")
        }

    def _read_stdout(self) -> None:
        process = self.process
        if not process or not process.stdout:
            return
        for line in process.stdout:
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            request_id = payload.get("id")
            if request_id is None:
                continue
            with self._responses_lock:
                response_queue = self._responses.get(int(request_id))
            if response_queue:
                response_queue.put(payload)

    def _read_stderr(self) -> None:
        process = self.process
        if not process or not process.stderr:
            return
        for line in process.stderr:
            clean = line.rstrip()
            if clean:
                self._stderr.append(clean)
                self._stderr_total += 1

    def _send(self, payload: dict) -> None:
        if not self.process or self.process.poll() is not None or not self.process.stdin:
            raise CadTransportError("build123d-mcp process is not running")
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        with self._write_lock:
            self.process.stdin.write(encoded + "\n")
            self.process.stdin.flush()

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict, timeout: int | None = None) -> dict:
        self._request_id += 1
        request_id = self._request_id
        response_queue: queue.Queue = queue.Queue(maxsize=1)
        with self._responses_lock:
            self._responses[request_id] = response_queue
        try:
            self._send({"jsonrpc": "2.0", "id": request_id,
                        "method": method, "params": params})
            try:
                response = response_queue.get(timeout=timeout or self.timeout)
            except queue.Empty as exc:
                details = self.stderr_tail()
                self.stop()
                raise CadTransportError(
                    f"build123d-mcp timed out after {timeout or self.timeout}s"
                    + (f" ({details})" if details else "")
                ) from exc
            if response.get("error"):
                error = response["error"]
                message = error.get("message") if isinstance(error, dict) else error
                raise CadTransportError(f"build123d-mcp RPC error: {message}")
            result = response.get("result")
            if not isinstance(result, dict):
                raise CadTransportError("build123d-mcp returned a malformed result")
            return result
        finally:
            with self._responses_lock:
                self._responses.pop(request_id, None)

    @property
    def alive(self) -> bool:
        return bool(self.process) and self.process.poll() is None

    def call_tool(self, name: str, arguments: dict, timeout: int | None = None) -> str:
        if not self.alive:
            # Classified as transport, not as a rejected call: a dead child is
            # exactly the case the supervisor exists to restart and replay.
            raise CadTransportError("build123d-mcp process is not running")
        if name not in self.tools:
            raise CadToolError(f"required build123d-mcp tool {name!r} is unavailable")
        mark = self._stderr_total
        result = self.request("tools/call", {"name": name, "arguments": arguments}, timeout)
        if result.get("isError"):
            raise CadToolError(self._detail(name, self._content_text(result), mark))
        structured = result.get("structuredContent")
        if isinstance(structured, dict) and "result" in structured:
            value = structured["result"]
            return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return self._content_text(result)

    def _detail(self, name: str, text: str, mark: int = 0) -> str:
        """Recover the cause when MCP masks an unexpected tool exception.

        The v2 server deliberately reports a crash as a bare
        ``Error executing tool <name>`` and logs the real exception instead, so
        the model would otherwise be told only that something went wrong. The
        real exception is recovered from the log this call produced -- the
        innermost one, not the MCP wrapper that re-raised it.
        """
        message = str(text or "").strip() or f"{name} failed"
        if not _MASKED_TOOL_ERROR.match(message):
            return message
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline and self._stderr_total <= mark:
            time.sleep(0.05)
        fresh = list(self._stderr)[-max(0, self._stderr_total - mark):]
        cause = self._root_cause(fresh)
        if cause:
            return f"{message}: {cause}"
        details = " | ".join(fresh[-4:])[-800:]
        return message + (f": {details}" if details else
                          " (the server masked the cause; call cad_last_error)")

    @staticmethod
    def _root_cause(lines: list[str]) -> str:
        """The innermost exception line, skipping the MCP re-raise wrappers."""
        candidates = []
        for line in lines:
            match = _EXCEPTION_LINE.match(line.strip())
            if not match:
                continue
            if "UnexpectedToolError" in line or "mcp.server" in line:
                continue
            detail = match.group(2).strip()
            candidates.append(f"{match.group(1)}: {detail}" if detail else match.group(1))
        return candidates[-1][:600] if candidates else ""

    @staticmethod
    def _content_text(result: dict) -> str:
        parts = []
        for item in result.get("content", []):
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(filter(None, parts))

    def stderr_tail(self) -> str:
        return " | ".join(list(self._stderr)[-4:])[-800:]

    def stop(self) -> None:
        process, self.process = self.process, None
        self.tools = set()
        if not process or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=10, check=False,
                    )
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close() if stream else None
                except OSError:
                    pass


class CadSessionRuntime:
    """One bounded, replayable CAD session owned by Agent8088."""

    REQUIRED_TOOLS = {
        "execute", "session_state", "measure", "inspect_part", "validate",
        "render_view", "save_snapshot", "restore_snapshot", "compare", "export",
        "import_cad_file", "last_error", "script", "health_check", "reset", "version",
    }

    def __init__(self):
        self._lock = threading.RLock()
        self._rpc: _StdioMCP | None = None
        self.workspace: Path | None = None
        self.name = ""
        self.parameters: dict = {}
        self.requirements: dict = {}
        self.blocks: list[str] = []
        self.snapshots: dict[str, int] = {}
        self.imports: list[str] = []
        self.execute_calls = 0
        self.session_id = ""

    # ------------------------------------------------------------ process
    def _ensure_runtime(self) -> Path:
        status = cad.cad_runtime_status()
        if not status.get("available") or not status.get("mcp_available"):
            raise CadMCPError(
                "Advanced CAD MCP runtime is unavailable. Re-run the Agent8088 installer "
                f"to install build123d-mcp {BUILD123D_MCP_VERSION}."
            )
        return Path(status["python"])

    def _spawn(self) -> _StdioMCP:
        if self.workspace is None:
            raise CadMCPError("No CAD session is active; call cad_begin first.")
        rpc = _StdioMCP(self._ensure_runtime(), self.workspace)
        rpc.start()
        missing = sorted(self.REQUIRED_TOOLS - rpc.tools)
        if missing:
            rpc.stop()
            raise CadMCPError("build123d-mcp is missing required tools: " + ", ".join(missing))
        return rpc

    def _start(self, replay: bool = False) -> None:
        self._rpc = self._spawn()
        if not replay:
            return
        # Checkpoints are process state too. Re-saving each one at the point in
        # history it was taken means a supervised restart leaves cad_restore
        # working, instead of silently losing every checkpoint on recovery.
        by_count: dict[int, list[str]] = {}
        for label, count in self.snapshots.items():
            by_count.setdefault(count, []).append(label)
        for index, block in enumerate(self.blocks):
            output = self._rpc.call_tool("execute", {"code": block})
            if _failed(output):
                raise CadMCPError("CAD recovery replay failed: " + output[:800])
            for label in sorted(by_count.get(index + 1, ())):
                self._rpc.call_tool("save_snapshot", {"name": label})

    def _call(self, tool: str, arguments: dict | None = None,
              timeout: int = DEFAULT_CALL_TIMEOUT, retry: bool = True) -> str:
        with self._lock:
            if self._rpc and not self._rpc.alive:
                self._rpc.stop()
                self._rpc = None
            if not self._rpc:
                self._start(replay=bool(self.blocks))
            try:
                return self._rpc.call_tool(tool, arguments or {}, timeout=timeout)
            except CadToolError:
                # The server is alive and said no. Restarting would discard live
                # geometry and replay the whole session for an answer that never
                # changes, so surface it and let the model repair the call.
                raise
            except Exception as first:
                if self._rpc:
                    self._rpc.stop()
                self._rpc = None
                if not retry:
                    raise CadTransportError(str(first)) from first
                try:
                    self._start(replay=True)
                    return self._rpc.call_tool(tool, arguments or {}, timeout=timeout)
                except Exception as second:
                    if self._rpc:
                        self._rpc.stop()
                    self._rpc = None
                    raise CadMCPError(
                        f"CAD MCP operation failed after supervised restart: {second}"
                    ) from second

    # ------------------------------------------------------------ session
    def begin(self, workspace: Path, name: str, parameters: Any = None,
              requirements: Any = None) -> str:
        # Parse first.  An invalid retry must not tear down a healthy session or
        # leave a half-initialised workspace behind.
        parsed_parameters = _json_object(parameters, "parameters")
        parsed_requirements = _json_object(requirements, "requirements")
        with self._lock:
            self.close()
            self.workspace = Path(workspace).resolve()
            self.workspace.mkdir(parents=True, exist_ok=True)
            self.name = _safe_name(name or self.workspace.name)
            self.parameters = parsed_parameters
            self.requirements = parsed_requirements
            self.blocks = []
            self.snapshots = {}
            self.imports = []
            self.execute_calls = 0
            self.session_id = uuid.uuid4().hex
            self._start()
            version = self._call("version", retry=False)
            self._call("reset", retry=False)
            # The server intentionally starts with an empty namespace.  Seed the
            # supported modelling API once so the first feature can use Box,
            # Cylinder, Pos, etc. without spending a fragile extra model turn on
            # an import.  This block is replayed after supervised restarts too.
            parameter_code = (
                "from build123d import *\n"
                f"{_MATH_SEED}\n"
                "PARAMS = " + repr(self.parameters)
            )
            parameter_result = self._call(
                "execute", {"code": parameter_code}, retry=False
            )
            if _failed(parameter_result):
                raise CadMCPError("Could not seed CAD parameters: " + parameter_result)
            self.blocks.append(parameter_code)
            manifest = {
                "schema_version": 1,
                "session_id": self.session_id,
                "name": self.name,
                "parameters": self.parameters,
                "requirements": self.requirements,
                "engine": {"build123d_mcp": BUILD123D_MCP_VERSION,
                           "text_to_cad": cad.CADGEN_VERSION},
            }
            (self.workspace / f"{self.name}.session.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            parameter_keys = ", ".join(sorted(self.parameters)) or "(none)"
            requirement_keys = ", ".join(sorted(self.requirements)) or "(none)"
            return (
                f"CAD session {self.session_id[:8]} started in {self.workspace}. "
                f"build123d-mcp: {version.strip()}. Build incrementally with cad_execute; "
                "call cad_measure after booleans and cad_validate before cad_export.\n"
                f"Available PARAMS keys: {parameter_keys}.\n"
                f"Acceptance requirement keys: {requirement_keys}."
            )

    def execute(self, code: str, checkpoint: str = "") -> str:
        encoded = str(code or "").encode("utf-8")
        if not encoded:
            raise CadMCPError("cad_execute requires non-empty build123d code")
        if len(encoded) > MAX_CODE_BYTES:
            raise CadMCPError(
                f"CAD code block is {len(encoded)} bytes; maximum is {MAX_CODE_BYTES}. "
                "Split the model into smaller feature operations."
            )
        if self.execute_calls >= MAX_EXECUTE_CALLS:
            raise CadMCPError("CAD session reached its bounded execute-call limit")
        try:
            output = self._call("execute", {"code": str(code)},
                                timeout=DEFAULT_CALL_TIMEOUT)
        except CadToolError as exc:
            self.execute_calls += 1
            return (f"CAD execution rejected: {exc}\n"
                    f"{self._parameter_hint()}\n"
                    "Use cad_last_error, repair only this feature, and retry.")
        self.execute_calls += 1
        if _failed(output):
            return (output + f"\n{self._parameter_hint()}\n"
                    "Use cad_last_error, repair only this feature, and retry.")
        self.blocks.append(str(code))
        if checkpoint:
            output = output + "\n" + self.snapshot(checkpoint)
        return output

    def _parameter_hint(self) -> str:
        keys = sorted(self.parameters)
        if not keys:
            return ("PARAMS is empty. Restart with cad_begin and include the "
                    "request's editable dimensions in parameters before building.")
        return "Available PARAMS keys: " + ", ".join(keys) + "."

    def state(self) -> str:
        return self._call("session_state")

    def measure(self, object_name: str = "", material: str = "") -> str:
        return self._call("measure", {"object_name": object_name, "material": material})

    def inspect(self, object_name: str = "", expected: Any = None) -> str:
        expectation = _normalise_expectation(expected)
        return self._call("inspect_part", {
            "object_name": object_name,
            "expected": json.dumps(expectation, separators=(",", ":")) if expectation else "",
        })

    def validate(self, object_name: str = "") -> str:
        return self._call("validate", {"object_name": object_name})

    def render(self, object_names: str = "", direction: str = "iso") -> str:
        self._require_active()
        requested = str(object_names or "").strip()
        if requested == "*":
            names = self._registered_objects()
            if not names:
                raise CadMCPError("No named geometry is registered; nothing to render.")
            requested = ",".join(names)
        preview = self.workspace / f"{self.name}.preview.png"
        result = self._call("render_view", {
            "objects": requested, "direction": direction,
            "quality": "high", "save_to": str(preview), "format": "png",
            "label_objects": True,
        }, timeout=240)
        # On Windows hosts without a complete VTK DLL stack, upstream performs a
        # successful build123d hidden-line SVG fallback but includes the original
        # ImportError in its message.  Returning that raw text makes the model
        # misclassify a real preview as a failed operation.  Trust the bounded
        # output file, not the diagnostic wording, and report the actual format.
        vector_preview = preview.with_suffix(".svg")
        if (not preview.is_file() or preview.stat().st_size == 0) \
                and vector_preview.is_file() and vector_preview.stat().st_size > 0:
            return ("CAD preview rendered successfully through the build123d "
                    f"vector fallback: {vector_preview}")
        return result

    def snapshot(self, name: str) -> str:
        label = _safe_name(name, "checkpoint")
        result = self._call("save_snapshot", {"name": label})
        if not _failed(result):
            # Remember how much verified history this checkpoint stands for, so
            # a later restore rewinds the replay log with the geometry.
            self.snapshots[label] = len(self.blocks)
        return result

    def restore(self, name: str) -> str:
        """Roll the whole session back to a checkpoint, not just its named objects.

        ``restore_snapshot`` rewinds the server's registered objects and current
        shape but leaves the execute namespace untouched, so the very next
        ``part = part - ...`` silently continues from the geometry that was
        supposed to be discarded. Rebuilding from the verified history instead
        makes the namespace, the named objects, the checkpoints and the source
        that will be exported all agree.
        """
        label = _safe_name(name, "checkpoint")
        with self._lock:
            if label not in self.snapshots:
                return self._call("restore_snapshot", {"name": label})
            committed = self.snapshots[label]
            discarded = len(self.blocks) - committed
            self.blocks = self.blocks[:committed]
            self.snapshots = {
                key: index for key, index in self.snapshots.items()
                if index <= committed
            }
            self.snapshots[label] = committed
            if self._rpc:
                self._rpc.stop()
            self._rpc = None
            self._start(replay=True)
            return (
                f"Restored checkpoint '{label}' by rebuilding the session from its "
                f"{committed} verified operation(s) in a fresh CAD process. Dropped "
                f"{discarded} rolled-back operation(s); variables, named objects, "
                "checkpoints, and the source that will be exported now all match "
                "this checkpoint."
            )

    def compare(self, a: str, b: str = "", kind: str = "shape") -> str:
        return self._call("compare", {"a": a, "b": b, "kind": kind, "format": "json"})

    def import_file(self, path: Path, name: str = "") -> str:
        """Bring an existing artifact into the session as usable, replayable geometry.

        ``import_cad_file`` alone registers the shape for measurement but binds no
        variable in the execute namespace and records nothing replayable, so a
        modelling step that edits it fails and a supervised restart loses it. The
        file is copied into the session workspace (the server's only allowed read
        root) and bound through a committed execute block.
        """
        self._require_active()
        source = Path(path).resolve()
        if not source.is_file():
            raise CadMCPError(f"CAD import does not exist: {source}")
        suffix = source.suffix.lower()
        if suffix not in _IMPORTABLE_SUFFIXES:
            raise CadMCPError(
                f"Cannot import {suffix or 'this file'}; supported CAD imports are "
                "step, stp, stl, and 3mf."
            )
        variable = _identifier(name or source.stem, "imported")
        local = self.workspace / f"{variable}{suffix}"
        if source != local.resolve():
            shutil.copy2(source, local)
        summary = self._call("import_cad_file",
                             {"path": str(local), "name": variable}, timeout=300)
        if _failed(summary):
            return summary
        loader = ("import_step" if suffix in {".step", ".stp"}
                  else "import_stl" if suffix == ".stl" else "")
        location = local.as_posix()
        if loader:
            code = (f"from build123d import {loader}\n"
                    f"{variable} = {loader}({location!r})\n"
                    f"show({variable}, {variable!r})\n")
        else:
            code = ("from build123d import Compound, Mesher\n"
                    f"_parts = Mesher().read({location!r})\n"
                    f"{variable} = _parts[0] if len(_parts) == 1 else "
                    "Compound(children=list(_parts))\n"
                    f"show({variable}, {variable!r})\n")
        bound = self.execute(code)
        if "Use cad_last_error" in bound:
            return ("CAD import parsed but could not be bound for editing:\n" + bound)
        if variable not in self.imports:
            self.imports.append(variable)
        return (summary + f"\nBound as the session variable {variable} and registered as "
                f"'{variable}'. Reference {variable} directly in cad_execute code.")

    def last_error(self) -> str:
        return self._call("last_error")

    # ------------------------------------------------------------ export
    def _registered_objects(self) -> list[str]:
        try:
            data = json.loads(self.state())
        except (json.JSONDecodeError, CadMCPError):
            return []
        objects = data.get("objects") if isinstance(data, dict) else None
        if isinstance(objects, dict):
            return [str(key) for key in objects]
        if isinstance(objects, list):
            return [item if isinstance(item, str) else str(item.get("name"))
                    for item in objects]
        return []

    def _validation_gate(self, object_name: str) -> tuple[bool, str]:
        """Run the validity gate, expanding '*' into every registered object.

        Upstream ``export`` accepts ``*`` to mean "the whole assembly" but
        ``validate`` does not, so passing it through refused every multi-part
        export with "Unknown object '*'".
        """
        if object_name != "*":
            report = self.validate(object_name)
            return ('"passes_gate": true' in report.lower()), report
        names = self._registered_objects()
        if not names:
            return False, "No named geometry is registered; nothing to validate."
        reports, ok = [], True
        for name in names:
            report = self.validate(name)
            passed = '"passes_gate": true' in report.lower()
            ok = ok and passed
            reports.append(f"--- {name} ---\n{report}")
        return ok, "\n".join(reports)

    def _fresh_process_replay(self, object_name: str, live: dict[str, dict]) -> str:
        """Rebuild the committed history in a brand-new server and compare metrics."""
        rpc = self._spawn()
        try:
            rpc.call_tool("reset", {})
            for index, block in enumerate(self.blocks):
                output = rpc.call_tool("execute", {"code": block},
                                       timeout=DEFAULT_CALL_TIMEOUT)
                if _failed(output):
                    raise CadMCPError(
                        f"clean-process replay failed at operation {index + 1}: "
                        + output[:600]
                    )
            for name, expected in live.items():
                replayed = _metrics(rpc.call_tool("measure", {"object_name": name}))
                difference = _metrics_match(expected, replayed)
                if difference:
                    raise CadMCPError(
                        f"clean-process replay of {name} diverged ({difference})"
                    )
                gate = rpc.call_tool("validate", {"object_name": name})
                if '"passes_gate": true' not in gate.lower():
                    raise CadMCPError(
                        f"clean-process replay of {name} failed the validity gate"
                    )
        finally:
            rpc.stop()
        return (f"replayed {len(self.blocks)} operation(s) in a clean build123d-mcp "
                f"process; {len(live)} object(s) matched the live session exactly")

    def _constrained_replay(self, canonical: str, stem: str) -> tuple[str, bool]:
        """Rebuild the generated gen_step() source in Agent8088's own worker."""
        blockers = _constrained_replay_blockers(canonical)
        if blockers:
            return ("not applicable: " + "; ".join(blockers)), False
        replay_path = self.workspace / f".{stem}.source-replay.step"
        result = cad.generate_cad_model(
            replay_path, canonical, json.dumps(self.parameters), "step",
            timeout=300, verification=None,
        )
        for artefact in (
            replay_path, replay_path.with_suffix(".step.py"),
            replay_path.with_suffix(".params.json"),
            replay_path.with_suffix(".report.json"),
            replay_path.with_suffix(".preview.png"),
        ):
            try:
                artefact.unlink(missing_ok=True)
            except OSError:
                pass
        if result.startswith("Generated and verified"):
            return "rebuilt the generated gen_step() source in a clean process", True
        lowered = result.lower()
        if any(marker in lowered for marker in _REPLAY_POLICY_MARKERS):
            # The constrained one-shot generator is deliberately narrower than
            # the MCP modelling sandbox. That is a scope answer, not a defect in
            # the geometry, so the clean-process MCP replay carries the gate.
            return ("not applicable: constrained generator policy rejected the "
                    "session source (" + result[:300] + ")"), False
        # An independent engine rebuilt the design and rejected it. Naming that
        # rather than "replay failed" is the difference between a fixable report
        # ("these two solids overlap") and a mystery.
        raise CadMCPError(
            "Independent rebuild of the generated CAD source rejected this design, "
            "so nothing was exported: " + result
        )

    def export(self, filename: str, formats: Any = "step,stl", object_name: str = "*") -> str:
        self._require_active()
        requested = _formats(formats)
        stem = _safe_name(Path(str(filename or self.name)).stem, self.name)

        passed, validation = self._validation_gate(object_name)
        if not passed:
            return "CAD export refused because validation did not pass:\n" + validation

        targets = ([object_name] if object_name != "*"
                   else self._registered_objects())
        live = {name: _metrics(self.measure(name)) for name in targets}

        # Artifacts become visible only once every gate has passed, so a failed
        # export never leaves a half-verified STEP where the next turn (or the
        # user) would treat it as the finished design.
        staging = self.workspace / f".{stem}.staging"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            exported: list[Path] = []
            for fmt in requested:
                target = (staging / f"{stem}.{fmt}").resolve()
                if staging.resolve() != target.parent:
                    raise CadMCPError("CAD export path escaped the staging directory")
                result = self._call("export", {
                    "filename": str(target), "format": fmt, "object_name": object_name,
                }, timeout=300)
                if _failed(result) or not target.is_file() or target.stat().st_size == 0:
                    raise CadMCPError(f"{fmt.upper()} export failed: {result[:1000]}")
                exported.append(target)

            statements, dropped = _geometry_statements(self.blocks)
            canonical = _canonical_source(statements, object_name)
            (staging / f"{stem}.cad.py").write_text(
                "# Verified build123d operations, in the order Agent8088 committed them.\n"
                f"PARAMS = {self.parameters!r}\n\n"
                + "\n\n".join(statements).rstrip() + "\n",
                encoding="utf-8",
            )
            (staging / f"{stem}.step.py").write_text(
                "# Canonical parametric build123d source generated by Agent8088.\n"
                f"PARAMS = {self.parameters!r}\n\n" + canonical,
                encoding="utf-8",
            )
            (staging / f"{stem}.params.json").write_text(
                json.dumps(self.parameters, indent=2) + "\n", encoding="utf-8"
            )

            # A live MCP namespace is not a durable design: nothing may be called
            # finished until its recorded history rebuilds the same geometry
            # outside the process that produced it.
            fresh = self._fresh_process_replay(object_name, live)
            constrained, strict = self._constrained_replay(canonical, stem)

            step_path = next(path for path in exported
                             if path.suffix.lower() == ".step")
            independent = cad.validate_cad_model(step_path, render=True, timeout=300)
            if independent.startswith("CAD validation failed"):
                raise CadMCPError("Independent text-to-cad validation failed: " + independent)

            report = {
                "schema_version": 2,
                "session_id": self.session_id,
                "name": self.name,
                "formats": [path.suffix.lstrip(".") for path in exported],
                "parameters": self.parameters,
                "requirements": self.requirements,
                "objects": targets,
                "imported_geometry": list(self.imports),
                "committed_operations": len(self.blocks),
                "analysis_calls_dropped_from_source": sorted(set(dropped)),
                "mcp_validation": validation,
                "clean_process_replay": fresh,
                "canonical_source_replay": constrained,
                "canonical_source_replay_strict": strict,
                "independent_validation": independent,
            }
            (staging / f"{stem}.mcp-report.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )

            published = []
            for item in sorted(staging.iterdir()):
                if item.is_file():
                    destination = self.workspace / item.name
                    os.replace(item, destination)
                    published.append(destination)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        artifacts = [item for item in published
                     if item.suffix.lower() in {".step", ".stl", ".3mf"}]
        return (
            "CAD export completed through the supervised build123d-mcp session, "
            "replayed in a clean process, and independently reopened by text-to-cad.\n"
            f"Artifacts: {', '.join(str(path) for path in artifacts)}\n"
            f"Source: {self.workspace / (stem + '.step.py')}\n"
            f"Transactions: {self.workspace / (stem + '.cad.py')}\n"
            f"Report: {self.workspace / (stem + '.mcp-report.json')}\n"
            f"Clean-process replay: {fresh}\n"
            f"Canonical source replay: {constrained}\n{independent}"
        )

    # ------------------------------------------------------------ misc
    def _require_active(self) -> None:
        if self.workspace is None:
            raise CadMCPError("No CAD session is active; call cad_begin first.")

    def status(self) -> dict:
        status = cad.cad_runtime_status()
        status.update({
            "mcp_version": BUILD123D_MCP_VERSION,
            "session_active": self.workspace is not None,
            "session_id": self.session_id,
            "workspace": str(self.workspace or ""),
            "execute_calls": self.execute_calls,
            "replay_blocks": len(self.blocks),
            "checkpoints": sorted(self.snapshots),
            "imported_geometry": list(self.imports),
            "supervised": True,
        })
        return status

    def close(self) -> None:
        if self._rpc:
            self._rpc.stop()
        self._rpc = None


RUNTIME = CadSessionRuntime()
atexit.register(RUNTIME.close)
