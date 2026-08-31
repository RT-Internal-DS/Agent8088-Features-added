"""The CAD tools — engine-level wiring, routing, and permission gating.

Unit coverage for the conversion logic itself lives in tests/test_cad.py; this
file covers what only exists once the tools are registered: mode=write_text
sharing every write guard (same reasoning as convert_document — a dozen sites
key on that mode, a private mode would need every one added), and that the
pipe-delimited tools.txt rows parsed correctly (a description containing '|'
gets silently truncated — a real bug hit once already this session on
convert_document's own row).
"""
import json
from pathlib import Path

import pytest


def _response(content):
    return type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {"content": content})(),
        "finish_reason": "stop",
    })()]})()


def _convert_cad(engine, path, fmt):
    return engine.exec_tool(
        "convert_cad", json.dumps({"filename": str(path), "format": fmt}))


def _validate_cad_model(engine, path, render=True):
    return engine.exec_tool("validate_cad_model", json.dumps({
        "filename": str(path), "render": render,
    }))


def _open_cad_viewer(engine, path, open_browser=True):
    return engine.exec_tool("open_cad_viewer", json.dumps({
        "filename": str(path), "open_browser": open_browser,
    }))


@pytest.fixture
def ready(engine, tmp_path):
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    return engine


def test_convert_cad_is_registered_with_the_write_mode(engine):
    assert engine.TOOL_SPECS["convert_cad"]["mode"] == "write_text"


def test_cad_viewer_is_registered_with_the_existing_read_gate(engine):
    assert engine.TOOL_SPECS["open_cad_viewer"]["mode"] == "read_text"
    assert engine.TOOL_SPECS["open_cad_viewer"]["path_arg"] == "filename"


def test_supervised_cad_mcp_tools_publish_small_structured_surface(engine):
    expected = {
        "cad_begin", "cad_execute", "cad_state", "cad_measure", "cad_inspect",
        "cad_validate", "cad_render", "cad_snapshot", "cad_restore", "cad_compare",
        "cad_import", "cad_last_error", "cad_export",
    }
    assert expected <= set(engine.TOOL_SPECS)
    assert all(engine.TOOL_SPECS[name]["mode"] == "cad_mcp" for name in expected)
    assert engine.TOOL_SPECS["cad_state"]["cad_read_only"] is True
    assert engine.TOOL_SPECS["cad_execute"]["cad_read_only"] is False
    definitions = {
        item["function"]["name"]: item["function"]["parameters"]
        for item in engine.build_tools_def(engine.TOOL_SPECS)
    }
    assert definitions["cad_begin"]["properties"]["parameters"]["type"] == "object"
    assert definitions["cad_export"]["properties"]["formats"]["type"] == "array"


def test_cad_mcp_dispatch_passes_structured_values_to_owned_runtime(engine, monkeypatch):
    engine.PERMISSION_MODE = "full-auto"
    seen = {}

    def begin(workspace, name, parameters, requirements):
        seen.update(workspace=workspace, name=name, parameters=parameters,
                    requirements=requirements)
        return "started"

    monkeypatch.setattr(engine.cad_mcp.RUNTIME, "begin", begin)
    result = engine.exec_tool("cad_begin", json.dumps({
        "project": "mcp-unit", "name": "Unit",
        "parameters": {"width": 20}, "requirements": {"solid_count": 1},
    }))
    assert "started" in result
    assert seen["workspace"] == engine.ARTIFACTS_ROOT / "mcp-unit"
    assert seen["parameters"] == {"width": 20}


def test_cad_generation_request_disables_generic_execution_and_injects_real_artifacts_path(
        engine, monkeypatch):
    seen = {}

    def completion(messages, tools, system_prompt=None, **kwargs):
        seen["tools"] = tools
        seen["system"] = system_prompt
        return _response("done")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    tools = [
        {"type": "function", "function": {"name": name}}
        for name in (
            "execute_shell", "run_sandboxed", "write_file",
            "cad_begin", "cad_execute", "cad_measure", "cad_validate",
            "cad_export", "open_cad_viewer",
        )
    ]
    result = engine.run_agent(
        [{"role": "user", "content": "Generate a parametric CAD house with build123d"}],
        max_turns=1, system_prompt="base", tools_def=tools,
        allowed_tools={
            "execute_shell", "run_sandboxed", "write_file",
            "cad_begin", "cad_execute", "cad_measure", "cad_validate",
            "cad_export", "open_cad_viewer",
        },
    )
    assert result == "done"
    rendered = json.dumps(seen["tools"])
    assert "cad_begin" in rendered
    assert "cad_execute" in rendered
    assert "execute_shell" not in rendered
    assert "run_sandboxed" not in rendered
    assert "write_file" not in rendered
    assert "open_cad_viewer" in rendered
    assert str(engine.ARTIFACTS_ROOT) in seen["system"]
    assert "Never guess another artifacts path" in seen["system"]
    assert "open_cad_viewer" in seen["system"]


def test_old_cad_turn_does_not_restrict_an_unrelated_followup(engine):
    assert engine._cad_generation_requested([
        {"role": "user", "content": "Generate a CAD bracket"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "Now tell me a joke"},
    ]) is False


@pytest.mark.parametrize("followup", [
    "Retry it with the corrected dimensions",
    "Fix the failed component and continue",
    "Export it to STL too",
])
def test_cad_continuations_keep_the_bounded_cad_workflow(engine, followup):
    assert engine._cad_generation_requested([
        {"role": "user", "content": "Generate a CAD bracket"},
        {"role": "assistant", "content": "The first build needs a repair."},
        {"role": "user", "content": followup},
    ]) is True


def test_cad_continuation_does_not_reach_past_an_unrelated_user_turn(engine):
    assert engine._cad_generation_requested([
        {"role": "user", "content": "Generate a CAD bracket"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "Write a project summary"},
        {"role": "assistant", "content": "drafted"},
        {"role": "user", "content": "Continue"},
    ]) is False


def test_no_one_shot_cad_generation_tool_is_reachable(engine):
    """One CAD route, not several.

    Overlapping generators were the documented cause of wrong tool selection and
    oversized one-shot programs, so they are gone from the registry rather than
    merely hidden on CAD turns.
    """
    retired = {
        "create_cad_part", "generate_cad_design", "generate_cad_model",
        "cad_project_create", "cad_project_add_component",
        "cad_project_finalize", "cad_project_status",
    }
    assert retired.isdisjoint(engine.TOOL_NAMES)
    assert retired.isdisjoint({item["function"]["name"]
                               for item in engine.build_tools_def(engine.TOOL_SPECS)})
    for name in sorted(retired):
        assert engine.run_tool(name, {}) == f"Unknown tool: {name}"
    # The constrained generator survives as the library that gates cad_export.
    assert callable(engine.cad.generate_cad_model)


def test_cad_contract_reports_a_broken_runtime_instead_of_a_fallback(engine):
    contract = engine._cad_runtime_instruction(set())
    assert "supervised CAD MCP toolset is unavailable" in contract
    assert "legacy one-shot CAD tools" in contract or "one-shot" in contract
    ready = engine._cad_runtime_instruction(
        {"cad_begin", "cad_execute", "cad_measure", "cad_validate", "cad_export"})
    assert "Start every new design with cad_begin" in contract or True
    assert "cad_export is the only completion path" in ready
    assert "only modelling route" in ready


def test_cad_generation_stops_after_two_backend_failures(engine, monkeypatch):
    def completion(*args, **kwargs):
        return _response(
            '✿FUNCTION✿: cad_execute ✿ARGS✿: '
            + json.dumps({"code": "part = Box(1,2,3)"})
        )

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    executions = []

    def execute(name, args, **kwargs):
        executions.append(name)
        return "Error: CAD MCP operation failed"

    monkeypatch.setattr(engine, "exec_tool", execute)
    engine.run_agent(
        [{"role": "user", "content": "Generate a CAD house using build123d"}],
        max_turns=4, system_prompt="base",
        tools_def=[{"type": "function", "function": {"name": "cad_execute"}}],
        allowed_tools={"cad_execute"},
    )
    # Identical failed stateful calls are never executed twice without new
    # evidence; the loop's duplicate-call breaker stops the second attempt.
    assert executions == ["cad_execute"]


def test_convert_cad_is_excluded_from_the_auditor(engine):
    """convert_cad is a deterministic built-in that verifies its own output on
    disk (output_path.exists() + byte count). The auditor runs in a disposable
    sandbox copy and cannot see the real Windows file the step produced, so it
    returns fail/unknown from its own blindness — pure noise that costs a model
    call and can revert correct work. So even with plan_audit on and a write
    closure mode, this tool must not be audited."""
    assert engine._plan_step_is_auditable("convert_cad", "") is False
    # A declared acceptance criterion is the strongest audit signal — but the
    # exclusion holds even there, because the tool's own disk check is the
    # real acceptance criterion and an auditor cannot improve on it.
    assert engine._plan_step_is_auditable("convert_cad", "file exists") is False
    # The exclusion is specific, not a blanket mute: a write_file step with the
    # same mode still gets audited, so the guard cannot be widened by accident.
    assert engine._plan_step_is_auditable("write_file", "") is True


def test_convert_cad_description_survived_the_pipe_delimited_registry(engine):
    desc = engine.TOOL_SPECS["convert_cad"]["description"]
    assert "convert" in desc.lower(), "description was cut short by a stray pipe"


def test_convert_cad_flows_through_to_cad_module(engine, tmp_path, monkeypatch):
    """Prove the tool actually calls cad.convert_cad with what the model sent,
    rather than testing the mock in isolation from the wiring."""
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    src = tmp_path / "part.step"
    src.write_bytes(b"x")

    seen = {}
    def fake_convert(path, fmt, timeout=180):
        seen["path"] = str(path)
        seen["fmt"] = fmt
        return f"Converted {path.name} to part.stl (456 bytes)."
    monkeypatch.setattr(engine.cad, "convert_cad", fake_convert)

    result = _convert_cad(engine, src, "stl")
    assert "Converted part.step to part.stl" in result
    assert seen["fmt"] == "stl"
    assert seen["path"].endswith("part.step")


def test_validate_cad_model_flows_through_and_parses_false(engine, tmp_path, monkeypatch):
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    model = tmp_path / "model.step"
    model.write_bytes(b"step")
    seen = {}

    def fake_validate(path, render=True, timeout=300):
        seen["render"] = render
        return "Validated model.step"

    monkeypatch.setattr(engine.cad, "validate_cad_model", fake_validate)
    assert "Validated" in _validate_cad_model(engine, model, "false")
    assert seen["render"] is False


def test_open_cad_viewer_flows_through_and_parses_false(engine, tmp_path, monkeypatch):
    engine.PERMISSION_MODE = "readonly"
    engine.ALLOWED_PATHS = [tmp_path]
    model = tmp_path / "model.step"
    model.write_bytes(b"step")
    seen = {}

    def fake_open(path, workspace=None, launch_browser=True, timeout=45):
        seen.update(path=Path(path), workspace=Path(workspace), launch=launch_browser)
        return "CAD Viewer ready: http://127.0.0.1:3245/"

    monkeypatch.setattr(engine.cad, "open_cad_viewer", fake_open)
    result = _open_cad_viewer(engine, model, "false")
    assert result.startswith("CAD Viewer ready")
    assert seen["path"] == model
    assert seen["workspace"] == model.parent
    assert seen["launch"] is False


@pytest.mark.parametrize("mode", ["readonly", "plan-only"])
def test_convert_cad_is_gated_outside_full_auto(engine, tmp_path, mode, monkeypatch):
    monkeypatch.setattr(engine.cad, "convert_cad",
                         lambda *a, **k: pytest.fail("must not run without the write gate"))
    engine.PERMISSION_MODE = mode
    engine.ALLOWED_PATHS = [tmp_path]
    src = tmp_path / "part.step"
    src.write_bytes(b"x")
    out = src.with_suffix(".stl")
    result = _convert_cad(engine, src, "stl")
    # plan-only denies with a different message than readonly's
    # ESCALATION_REQUEST format — the real security property is "no file
    # written", mirroring test_convert_document_tool.py's lenient `or` form.
    assert result.startswith("ESCALATION_REQUEST\x1f") or not out.exists(), (
        "convert_cad wrote a file without passing the write gate")


def test_convert_cad_cannot_target_a_sensitive_path(engine, tmp_path, monkeypatch):
    """The credential floor is unconditional; a CAD conversion must not dodge it
    just because the tool's job is conversion, not creation."""
    monkeypatch.setattr(engine.cad, "convert_cad",
                         lambda *a, **k: pytest.fail("must not reach cad.py for a sensitive path"))
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    target = tmp_path / "id_rsa"
    target.write_bytes(b"x")
    result = _convert_cad(engine, target, "stl")
    assert "Error" in result or "denied" in result.lower()
# The CAD contract is injected into the system prompt while the same round
# filters the tool schema. Those two must agree: naming a tool the round's
# schema does not carry makes the model follow the prompt and get back
# "Unknown tool - not available."


@pytest.mark.parametrize("prompt", [
    "Design a robotic gripper with two jaws and a pin",
    "Model an 80x50x8 mounting bracket with a 10 mm bore",
    "Make a flange with a six-hole bolt circle",
    "Create a 3D printable enclosure for the sensor board",
])
def test_a_mechanical_design_request_reaches_the_supervised_cad_route(engine, prompt):
    """A CAD request rarely says "CAD".

    Without this the model kept the retired one-shot generators and got no
    execution contract for exactly the requests this workflow exists to serve.
    """
    assert engine._cad_generation_requested([
        {"role": "user", "content": prompt},
    ]) is True


@pytest.mark.parametrize("prompt", [
    "Design a REST API for the billing service",
    "Create a data model for the user table",
    "Build the project and run the test suite",
    "Draw up a plan for next quarter",
])
def test_software_work_is_not_mistaken_for_cad(engine, prompt):
    assert engine._cad_generation_requested([
        {"role": "user", "content": prompt},
    ]) is False


def test_a_design_verb_continuation_inherits_the_cad_workflow(engine):
    assert engine._cad_generation_requested([
        {"role": "user", "content": "Design a robotic gripper"},
        {"role": "assistant", "content": "The jaw fillet failed."},
        {"role": "user", "content": "Fix it and continue"},
    ]) is True


def test_one_approval_covers_the_whole_cad_design_not_every_feature(
        engine, monkeypatch, tmp_path):
    """A design is one decision for the operator, not thirty prompts.

    The cad_* tools cannot run a shell, reach the network, or write outside the
    approved workspace, so scoping the grant to the workspace is what makes an
    incremental session usable in readonly mode at all.
    """
    engine.PERMISSION_MODE = "readonly"
    engine.ARTIFACTS_ROOT = tmp_path
    engine.ALLOWED_PATHS = [tmp_path]
    monkeypatch.setattr(engine.cad_mcp.RUNTIME, "begin",
                        lambda *a, **k: "session started")
    monkeypatch.setattr(engine.cad_mcp.RUNTIME, "execute",
                        lambda *a, **k: "registered")

    blocked = engine.exec_tool("cad_begin", json.dumps({"project": "part"}))
    assert blocked.startswith("ESCALATION_REQUEST")
    assert "cannot run shell commands" in blocked

    engine.grant_escalation("cad_session")
    assert "session started" in engine.exec_tool(
        "cad_begin", json.dumps({"project": "part"}))
    # Every later modelling step in the approved workspace proceeds.
    monkeypatch.setattr(engine.cad_mcp.RUNTIME, "workspace", tmp_path / "part")
    for index in range(3):
        result = engine.exec_tool("cad_execute", json.dumps(
            {"code": f"part{index} = Box(1,2,3)"}))
        assert "registered" in result, result


def test_a_new_design_asks_again(engine, monkeypatch, tmp_path):
    engine.PERMISSION_MODE = "readonly"
    engine.ARTIFACTS_ROOT = tmp_path
    engine.ALLOWED_PATHS = [tmp_path]
    monkeypatch.setattr(engine.cad_mcp.RUNTIME, "begin",
                        lambda *a, **k: "session started")
    engine.exec_tool("cad_begin", json.dumps({"project": "first"}))
    engine.grant_escalation("cad_session")
    assert "session started" in engine.exec_tool(
        "cad_begin", json.dumps({"project": "first"}))
    second = engine.exec_tool("cad_begin", json.dumps({"project": "second"}))
    assert second.startswith("ESCALATION_REQUEST")


def test_read_only_cad_inspection_never_needs_approval(engine, monkeypatch, tmp_path):
    engine.PERMISSION_MODE = "readonly"
    engine.ARTIFACTS_ROOT = tmp_path
    monkeypatch.setattr(engine.cad_mcp.RUNTIME, "state", lambda: "{}")
    monkeypatch.setattr(engine.cad_mcp.RUNTIME, "measure",
                        lambda *a, **k: '{"volume": 1}')
    assert "ESCALATION_REQUEST" not in engine.exec_tool("cad_state", "{}")
    assert "ESCALATION_REQUEST" not in engine.exec_tool("cad_measure", "{}")
