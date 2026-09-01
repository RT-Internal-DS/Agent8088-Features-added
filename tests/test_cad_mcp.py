"""Contract tests for the supervised build123d-mcp integration."""
import json
import os
from pathlib import Path

import pytest

from agent8088 import cad_mcp


EMPTY_SEED = (
    "from build123d import *\n"
    "from math import cos, degrees, pi, radians, sin, sqrt, tan\n"
    "PARAMS = {}"
)


class FakeRPC:
    calls = []
    instances = []

    def __init__(self, python, cwd, timeout=0):
        self.cwd = Path(cwd)
        self.tools = set(cad_mcp.CadSessionRuntime.REQUIRED_TOOLS)
        self.stopped = False
        self.alive = True
        # Per-instance too: the class-level log spans every child a test spawns,
        # which is exactly what a replay assertion must not look at.
        self.own_calls = []
        FakeRPC.instances.append(self)

    def start(self):
        return None

    def stop(self):
        self.stopped = True
        self.alive = False

    def call_tool(self, name, arguments, timeout=None):
        self.calls.append((name, arguments))
        self.own_calls.append((name, arguments))
        if name == "version":
            return "build123d-mcp: 0.3.83"
        if name == "execute" and "BROKEN" in arguments.get("code", ""):
            return "Error: execution failed"
        if name == "script":
            return json.dumps({"script": "from build123d import *\npart = Box(1,2,3)"})
        if name == "session_state":
            return json.dumps({"objects": {"Left": {}, "Right": {}}})
        return json.dumps({"ok": True, "passes_gate": True})


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    FakeRPC.calls = []
    FakeRPC.instances = []
    monkeypatch.setattr(cad_mcp, "_StdioMCP", FakeRPC)
    monkeypatch.setattr(cad_mcp.cad, "cad_runtime_status", lambda: {
        "available": True, "mcp_available": True, "python": str(tmp_path / "python"),
    })
    value = cad_mcp.CadSessionRuntime()
    yield value
    value.close()


def test_formats_accept_array_or_csv_and_force_step():
    assert cad_mcp._formats(["stl", "step"]) == ["stl", "step"]
    assert cad_mcp._formats("stl,3mf") == ["step", "stl", "3mf"]
    with pytest.raises(ValueError, match="Unsupported CAD format"):
        cad_mcp._formats(["fcstd"])


def test_begin_writes_manifest_and_starts_reduced_supervised_session(runtime, tmp_path):
    result = runtime.begin(tmp_path / "robot", "robot", {"width": 20}, {"solids": 2})
    manifest = json.loads((tmp_path / "robot" / "robot.session.json").read_text())
    assert "started" in result
    assert manifest["parameters"] == {"width": 20}
    assert manifest["engine"]["build123d_mcp"] == "0.3.83"
    assert [name for name, _ in FakeRPC.calls[:2]] == ["version", "reset"]
    assert runtime.blocks[0] == (
        "from build123d import *\n"
        "from math import cos, degrees, pi, radians, sin, sqrt, tan\n"
        "PARAMS = {'width': 20}"
    )
    assert "Available PARAMS keys: width" in result


def test_model_style_single_quoted_parameter_objects_are_safe_and_normalised():
    assert cad_mcp._json_object(
        "{'plate_od': 120, 'ring_positions': (10, 20)}", "parameters"
    ) == {"plate_od": 120, "ring_positions": [10, 20]}
    with pytest.raises(ValueError, match="must be a JSON object"):
        cad_mcp._json_object("__import__('os').system('echo unsafe')", "parameters")


def test_invalid_begin_does_not_destroy_the_active_session(runtime, tmp_path):
    runtime.begin(tmp_path / "good", "good", {"width": 20})
    live = runtime._rpc
    workspace = runtime.workspace
    with pytest.raises(ValueError, match="must be a JSON object"):
        runtime.begin(tmp_path / "bad", "bad", "{'width': Box(20)}")
    assert runtime._rpc is live
    assert runtime.workspace == workspace
    assert live.stopped is False


def test_only_successful_execute_blocks_are_replayable(runtime, tmp_path):
    runtime.begin(tmp_path / "part", "part")
    assert "passes_gate" in runtime.execute("part = Box(1,2,3)")
    assert "Use cad_last_error" in runtime.execute("BROKEN")
    assert runtime.blocks == [EMPTY_SEED, "part = Box(1,2,3)"]
    assert runtime.execute_calls == 2
    assert "PARAMS is empty" in runtime.execute("BROKEN AGAIN")


def test_code_size_and_call_budget_are_bounded(runtime, tmp_path, monkeypatch):
    runtime.begin(tmp_path / "part", "part")
    with pytest.raises(cad_mcp.CadMCPError, match="maximum"):
        runtime.execute("x" * (cad_mcp.MAX_CODE_BYTES + 1))
    runtime.execute_calls = cad_mcp.MAX_EXECUTE_CALLS
    with pytest.raises(cad_mcp.CadMCPError, match="bounded execute-call limit"):
        runtime.execute("part = Box(1,2,3)")


def test_restart_replays_only_committed_blocks(runtime, tmp_path):
    runtime.begin(tmp_path / "part", "part")
    runtime.execute("part = Box(1,2,3)")
    runtime._rpc.stop()
    runtime._rpc = None
    runtime.state()
    execute_calls = [args["code"] for name, args in FakeRPC.calls if name == "execute"]
    assert execute_calls == [
        EMPTY_SEED, "part = Box(1,2,3)",
        EMPTY_SEED, "part = Box(1,2,3)",
    ]


def test_mcp_read_only_annotation_does_not_override_save_path_policy():
    # Agent8088 does not expose upstream render_view/script directly. Their
    # save paths are always supplied internally from the active workspace.
    assert "render_view" in cad_mcp.CadSessionRuntime.REQUIRED_TOOLS
    assert "script" in cad_mcp.CadSessionRuntime.REQUIRED_TOOLS
    assert not hasattr(cad_mcp.CadSessionRuntime, "call_arbitrary_tool")


def test_canonical_source_wraps_show_registry_in_gen_step():
    statements, _ = cad_mcp._geometry_statements([
        "from build123d import *\npart = Box(1,2,3)\nshow(part, 'Part')",
    ])
    source = cad_mcp._canonical_source(statements, "Part")
    compile(source, "part.step.py", "exec")
    assert "def gen_step():" in source
    assert "return _shown['Part']" in source
    assert source.count("from build123d import *") == 1


def test_a_rejected_call_does_not_tear_down_a_live_session(runtime, tmp_path,
                                                           monkeypatch):
    """A server that says no is not a server that needs restarting.

    Treating every failure as a crash replayed the whole session for an answer
    that never changes, and threw away live geometry on a typo.
    """
    runtime.begin(tmp_path / "part", "part")
    runtime.execute("part = Box(1,2,3)")
    live = runtime._rpc

    def refuse(name, arguments, timeout=None):
        raise cad_mcp.CadToolError("Error executing tool measure: bad object")

    monkeypatch.setattr(live, "call_tool", refuse)
    with pytest.raises(cad_mcp.CadToolError):
        runtime.measure("Nope")
    assert runtime._rpc is live
    assert live.stopped is False


def test_a_dead_child_is_restarted_rather_than_reported_as_a_rejected_call(
        runtime, tmp_path):
    runtime.begin(tmp_path / "part", "part")
    runtime.execute("part = Box(1,2,3)")
    first = runtime._rpc
    first.stop()
    runtime.state()
    assert runtime._rpc is not first
    assert runtime._rpc.alive is True


def test_restore_rewinds_history_and_rebuilds_the_whole_session(runtime, tmp_path):
    """restore_snapshot leaves the execute namespace at the rolled-back state.

    Rebuilding from verified history is what makes `part` in the next block mean
    the restored geometry instead of the geometry the user just discarded.
    """
    runtime.begin(tmp_path / "part", "part")
    runtime.execute("part = Box(10,10,10)", checkpoint="good")
    runtime.execute("part = part + Pos(0,0,50) * Box(5,5,5)")
    assert len(runtime.blocks) == 3
    before = runtime._rpc
    message = runtime.restore("good")
    assert "Restored checkpoint" in message and "Dropped 1" in message
    assert runtime.blocks == [EMPTY_SEED, "part = Box(10,10,10)"]
    assert runtime._rpc is not before
    replayed = [args["code"] for name, args in runtime._rpc.own_calls
                if name == "execute"]
    assert replayed == [EMPTY_SEED, "part = Box(10,10,10)"]


def test_a_supervised_restart_recreates_checkpoints_at_their_own_positions(
        runtime, tmp_path):
    runtime.begin(tmp_path / "part", "part")
    runtime.execute("a = Box(1,1,1)", checkpoint="first")
    runtime.execute("b = Box(2,2,2)", checkpoint="second")
    runtime._rpc.stop()
    runtime._rpc = None
    runtime.state()
    ordered = [(name, args.get("code") or args.get("name"))
               for name, args in runtime._rpc.own_calls
               if name in {"execute", "save_snapshot"}]
    assert ordered == [
        ("execute", EMPTY_SEED),
        ("execute", "a = Box(1,1,1)"),
        ("save_snapshot", "first"),
        ("execute", "b = Box(2,2,2)"),
        ("save_snapshot", "second"),
    ]


def test_expectations_are_mapped_onto_the_real_inspect_contract(runtime, tmp_path):
    runtime.begin(tmp_path / "part", "part")
    runtime.inspect("Part", {"bounding_box": [80, 50, 8], "solids": 1})
    _, arguments = next((call for call in reversed(FakeRPC.calls)
                         if call[0] == "inspect_part"))
    assert json.loads(arguments["expected"]) == {"bbox": [80, 50, 8], "solid_count": 1}
    with pytest.raises(ValueError, match="unsupported key"):
        runtime.inspect("Part", {"wall_thickness": 3})
    with pytest.raises(ValueError, match="at least one measurable"):
        runtime.inspect("Part", {"tolerance": 0.5})


def test_assembly_export_validates_every_object_because_validate_rejects_star(
        runtime, tmp_path, monkeypatch):
    """`export` accepts '*'; `validate` does not, and refused every assembly."""
    runtime.begin(tmp_path / "asm", "asm")
    runtime.execute("a = Box(1,1,1)")
    passed, report = runtime._validation_gate("*")
    assert passed is True
    validated = [args["object_name"] for name, args in FakeRPC.calls
                 if name == "validate"]
    assert validated == ["Left", "Right"]
    assert "--- Left ---" in report and "--- Right ---" in report


def test_assembly_render_expands_star_because_render_view_rejects_it(runtime, tmp_path):
    runtime.begin(tmp_path / "asm", "asm")
    runtime.render("*", "iso")
    _, arguments = next(call for call in reversed(FakeRPC.calls)
                        if call[0] == "render_view")
    assert arguments["objects"] == "Left,Right"


def test_render_reports_a_real_svg_fallback_as_success(runtime, tmp_path):
    workspace = tmp_path / "asm"
    runtime.begin(workspace, "asm")
    (workspace / "asm.preview.svg").write_text("<svg/>", encoding="utf-8")
    result = runtime.render("Left", "iso")
    assert "rendered successfully" in result
    assert "ImportError" not in result


def test_analysis_only_statements_are_dropped_from_the_canonical_source():
    kept, dropped = cad_mcp._geometry_statements([
        "from build123d import *\n"
        "from math import cos, radians\nPARAMS = {'a': 1}",
        "from build123d import *\npart = Box(1,2,3)\nprint(part)\n"
        "measure(part)\nshow(part, 'Part')",
    ])
    body = "\n".join(kept)
    assert "print" not in body and "measure(" not in body
    assert "Box(1, 2, 3)" in body and "show(part, 'Part')" in body
    assert set(dropped) == {"print", "measure"}
    assert "PARAMS" not in body
    assert "from math" not in body


def test_constrained_replay_is_reported_as_inapplicable_not_as_a_failure():
    importing = cad_mcp._canonical_source(
        ["part = import_step('/tmp/a.step')", "show(part, 'P')"], "P")
    blockers = cad_mcp._constrained_replay_blockers(importing)
    assert any("import_step" in reason for reason in blockers)
    clean = cad_mcp._canonical_source(
        ["part = Box(1, 2, 3)", "show(part, 'P')"], "P")
    assert cad_mcp._constrained_replay_blockers(clean) == []


def test_import_binds_a_session_variable_and_is_replayable(runtime, tmp_path):
    source = tmp_path / "in.step"
    source.write_text("ISO-10303-21;", encoding="utf-8")
    runtime.begin(tmp_path / "edit", "edit")
    message = runtime.import_file(source, "Imported")
    assert "Bound as the session variable Imported" in message
    assert (tmp_path / "edit" / "Imported.step").is_file()
    binding = runtime.blocks[-1]
    assert "Imported = import_step(" in binding
    assert "show(Imported, 'Imported')" in binding
    assert runtime.imports == ["Imported"]


def test_import_refuses_a_format_the_server_cannot_read(runtime, tmp_path):
    other = tmp_path / "notes.txt"
    other.write_text("x", encoding="utf-8")
    runtime.begin(tmp_path / "edit", "edit")
    with pytest.raises(cad_mcp.CadMCPError, match="supported CAD imports"):
        runtime.import_file(other)


def test_metric_comparison_flags_a_diverging_replay():
    live = {"volume": 1000.0, "area": 600.0, "xsize": 10.0}
    assert cad_mcp._metrics_match(live, dict(live)) == ""
    assert "volume" in cad_mcp._metrics_match(live, {**live, "volume": 1001.0})


def test_an_axis_written_as_a_letter_is_translated_to_a_vector(runtime, tmp_path):
    """inspect_part wants [0, 0, 1]; a model writes "Z"."""
    runtime.begin(tmp_path / "part", "part")
    runtime.inspect("Part", {"holes": {"count": 5, "axis": "Z", "diameter": 10}})
    _, arguments = next(call for call in reversed(FakeRPC.calls)
                        if call[0] == "inspect_part")
    assert json.loads(arguments["expected"]) == {
        "holes": [{"count": 5, "axis": [0, 0, 1], "diameter": 10}]
    }
    runtime.inspect("Part", {"bosses": [{"count": 1, "axis": "-x"}]})
    _, arguments = next(call for call in reversed(FakeRPC.calls)
                        if call[0] == "inspect_part")
    assert json.loads(arguments["expected"])["bosses"][0]["axis"] == [-1, 0, 0]
    with pytest.raises(ValueError, match="must be x, y, z"):
        runtime.inspect("Part", {"holes": [{"count": 1, "axis": "diagonal"}]})


def test_a_masked_server_error_reports_the_root_cause_not_the_wrapper():
    """MCP v2 hides an unexpected exception behind a generic sentence.

    Reporting only that sentence leaves the model with nothing to repair, so the
    innermost exception from the server's own log is recovered instead.
    """
    log = [
        'File "build123d_mcp/worker.py", line 1124, in _do_call',
        "RuntimeError: ValueError: expected.holes[0].axis must be a 3-number JSON array",
        "The above exception was the direct cause of the following exception:",
        'File "mcp/server/mcpserver/tools/base.py", line 210, in run',
        "mcp.server.mcpserver.exceptions.UnexpectedToolError: "
        "Error executing tool inspect_part",
    ]
    cause = cad_mcp._StdioMCP._root_cause(log)
    assert cause == ("RuntimeError: ValueError: expected.holes[0].axis must be a "
                     "3-number JSON array")
    assert cad_mcp._StdioMCP._root_cause(["nothing useful here"]) == ""


@pytest.mark.skipif(
    os.environ.get("AGENT8088_RUN_CAD_E2E") != "1",
    reason="set AGENT8088_RUN_CAD_E2E=1 after installing the isolated CAD runtime",
)
def test_real_agent_tool_wiring_generates_replays_and_reopens_step(tmp_path):
    from agent8088 import engine

    old_mode, old_paths = engine.PERMISSION_MODE, engine.ALLOWED_PATHS
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    try:
        begin = engine.exec_tool("cad_begin", json.dumps({
            "project": str(tmp_path / "hex"), "name": "hex",
            "parameters": {"radius": 30, "bore_radius": 6, "height": 15},
            "requirements": {"solid_count": 1, "bounding_box": [60, 51.9615, 15]},
        }))
        assert "started" in begin
        built = engine.exec_tool("cad_execute", json.dumps({
            "code": (
                "from build123d import *\n"
                "profile = RegularPolygon(PARAMS['radius'], 6) - Circle(PARAMS['bore_radius'])\n"
                "part = extrude(profile, PARAMS['height'])\n"
                "show(part, 'HexSpacer')"
            ),
            "checkpoint": "complete",
        }))
        assert "Error:" not in built
        measured = engine.exec_tool(
            "cad_measure", json.dumps({"object_name": "HexSpacer"})
        )
        assert "33377" in measured
        validated = engine.exec_tool(
            "cad_validate", json.dumps({"object_name": "HexSpacer"})
        )
        assert '"passes_gate": true' in validated.lower()
        exported = engine.exec_tool("cad_export", json.dumps({
            "filename": "hex_spacer.step", "formats": ["step", "stl"],
            "object_name": "HexSpacer",
        }))
        assert "independently reopened" in exported
        expected = (
            "hex_spacer.step", "hex_spacer.stl", "hex_spacer.step.py",
            "hex_spacer.cad.py", "hex_spacer.params.json",
            "hex_spacer.report.json", "hex_spacer.preview.png",
        )
        assert all((tmp_path / "hex" / name).is_file() for name in expected)
    finally:
        cad_mcp.RUNTIME.close()
        engine.PERMISSION_MODE, engine.ALLOWED_PATHS = old_mode, old_paths


@pytest.mark.skipif(
    os.environ.get("AGENT8088_RUN_CAD_E2E") != "1",
    reason="set AGENT8088_RUN_CAD_E2E=1 after installing the isolated CAD runtime",
)
def test_real_drive_flange_accepts_model_style_params_and_exports_every_format(tmp_path):
    """Regression for the exact production failure reported on testing_CAD.

    The text-mode model supplied single-quoted nested objects, then authored its
    first feature without an import. Both representations must work, and STEP,
    STL and 3MF must all survive the independent export gate.
    """
    from agent8088 import engine

    old_mode, old_paths = engine.PERMISSION_MODE, engine.ALLOWED_PATHS
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    project = tmp_path / "drive_flange"
    try:
        begin = engine.exec_tool("cad_begin", json.dumps({
            "project": str(project),
            "name": "drive_flange",
            "parameters": (
                "{'plate_od': 120, 'plate_thickness': 10, 'hub_od': 50, "
                "'hub_height': 22, 'bore_diameter': 25, 'keyway_width': 6, "
                "'keyway_depth': 3, 'bolt_hole_diameter': 9, "
                "'bolt_circle_diameter': 90, 'bolt_count': 6}"
            ),
            "requirements": "{'bbox': [120, 120, 32], 'solid_count': 1}",
        }))
        assert "Available PARAMS keys: bolt_circle_diameter" in begin

        operations = [
            (
                "plate = Cylinder(PARAMS['plate_od']/2, PARAMS['plate_thickness'], "
                "align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "hub = Pos(0, 0, PARAMS['plate_thickness']) * "
                "Cylinder(PARAMS['hub_od']/2, PARAMS['hub_height'], "
                "align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "flange = plate + hub\nshow(flange, 'DriveFlange')"
            ),
            (
                "bore = Cylinder(PARAMS['bore_diameter']/2, "
                "PARAMS['plate_thickness'] + PARAMS['hub_height'], "
                "align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "flange = flange - bore\nshow(flange, 'DriveFlange')"
            ),
            (
                "keyway = Pos(PARAMS['bore_diameter']/2 - 0.1, "
                "-PARAMS['keyway_width']/2, 0) * "
                "Box(PARAMS['keyway_depth'] + 0.2, PARAMS['keyway_width'], "
                "PARAMS['plate_thickness'] + PARAMS['hub_height'], "
                "align=(Align.MIN, Align.MIN, Align.MIN))\n"
                "flange = flange - keyway\nshow(flange, 'DriveFlange')"
            ),
            (
                "for index in range(PARAMS['bolt_count']):\n"
                "    angle = radians(index * 360 / PARAMS['bolt_count'])\n"
                "    x = (PARAMS['bolt_circle_diameter']/2) * cos(angle)\n"
                "    y = (PARAMS['bolt_circle_diameter']/2) * sin(angle)\n"
                "    hole = Pos(x, y, 0) * Cylinder(PARAMS['bolt_hole_diameter']/2, "
                "PARAMS['plate_thickness'], align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "    flange = flange - hole\n"
                "show(flange, 'DriveFlange')"
            ),
        ]
        for index, code in enumerate(operations):
            result = engine.exec_tool("cad_execute", json.dumps({
                "code": code,
                "checkpoint": "features_complete" if index == len(operations) - 1 else "",
            }))
            assert "Error:" not in result and "rejected" not in result.lower(), result

        inspected = engine.exec_tool("cad_inspect", json.dumps({
            "object_name": "DriveFlange",
            "expected": {"bbox": [120, 120, 32], "solid_count": 1, "tolerance": 0.05},
        }))
        assert "FAIL" not in inspected.upper(), inspected
        validated = engine.exec_tool(
            "cad_validate", json.dumps({"object_name": "DriveFlange"})
        )
        assert '"passes_gate": true' in validated.lower(), validated

        exported = engine.exec_tool("cad_export", json.dumps({
            "filename": "drive_flange.step",
            "formats": ["step", "stl", "3mf"],
            "object_name": "DriveFlange",
        }))
        assert "independently reopened" in exported, exported
        for suffix in (".step", ".stl", ".3mf"):
            assert (project / f"drive_flange{suffix}").is_file()
    finally:
        cad_mcp.RUNTIME.close()
        engine.PERMISSION_MODE, engine.ALLOWED_PATHS = old_mode, old_paths


@pytest.mark.skipif(
    os.environ.get("AGENT8088_RUN_CAD_E2E") != "1",
    reason="set AGENT8088_RUN_CAD_E2E=1 after installing the isolated CAD runtime",
)
def test_real_complex_multisolid_gripper_replays_and_exports_as_an_assembly(tmp_path):
    """Exercise a complex, incremental, named multi-solid CAD lifecycle."""
    from agent8088 import engine

    old_mode, old_paths = engine.PERMISSION_MODE, engine.ALLOWED_PATHS
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    project = tmp_path / "robotic_gripper"
    params = {
        "base_length": 120, "base_width": 80, "base_thickness": 8,
        "support_width": 18, "support_depth": 20, "support_height": 25,
        "support_offset": 35, "finger_length": 70, "finger_width": 14,
        "finger_thickness": 8, "pivot_radius": 3, "pin_clearance": 0.3,
        "link_length": 45, "link_width": 8, "link_thickness": 5,
        "actuator_radius": 14, "actuator_thickness": 6,
    }
    try:
        begin = engine.exec_tool("cad_begin", json.dumps({
            "project": str(project), "name": "robotic_gripper",
            "parameters": params,
            "requirements": {"solid_count": 9},
        }))
        assert "Available PARAMS keys: actuator_radius" in begin

        operations = [
            (
                "base = Box(PARAMS['base_length'], PARAMS['base_width'], "
                "PARAMS['base_thickness'], align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "for x in (-50, 50):\n"
                "    for y in (-30, 30):\n"
                "        base = base - Pos(x, y, 0) * Cylinder(3.5, "
                "PARAMS['base_thickness'], align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "show(base, 'Base')"
            ),
            (
                "left_support = Pos(-PARAMS['support_offset'], 0, PARAMS['base_thickness']) * "
                "Box(PARAMS['support_width'], PARAMS['support_depth'], PARAMS['support_height'], "
                "align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "left_support = left_support - Pos(-PARAMS['support_offset'], 0, "
                "PARAMS['base_thickness']) * "
                "Cylinder(PARAMS['pivot_radius'] + PARAMS['pin_clearance'], "
                "PARAMS['support_height'], align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "show(left_support, 'LeftPivotSupport')"
            ),
            (
                "right_support = Pos(PARAMS['support_offset'], 0, PARAMS['base_thickness']) * "
                "Box(PARAMS['support_width'], PARAMS['support_depth'], PARAMS['support_height'], "
                "align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "right_support = right_support - Pos(PARAMS['support_offset'], 0, "
                "PARAMS['base_thickness']) * "
                "Cylinder(PARAMS['pivot_radius'] + PARAMS['pin_clearance'], "
                "PARAMS['support_height'], align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "show(right_support, 'RightPivotSupport')"
            ),
            (
                "finger_z = PARAMS['base_thickness'] + PARAMS['support_height']\n"
                "left_finger = Pos(-PARAMS['support_offset'], PARAMS['finger_length']/2 - 5, "
                "finger_z) * Box(PARAMS['finger_width'], PARAMS['finger_length'], "
                "PARAMS['finger_thickness'], align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "left_finger = left_finger - Pos(-PARAMS['support_offset'], 0, finger_z) * "
                "Cylinder(PARAMS['pivot_radius'] + PARAMS['pin_clearance'], "
                "PARAMS['finger_thickness'], align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "show(left_finger, 'LeftFinger')"
            ),
            (
                "right_finger = Pos(PARAMS['support_offset'], PARAMS['finger_length']/2 - 5, "
                "finger_z) * Box(PARAMS['finger_width'], PARAMS['finger_length'], "
                "PARAMS['finger_thickness'], align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "right_finger = right_finger - Pos(PARAMS['support_offset'], 0, finger_z) * "
                "Cylinder(PARAMS['pivot_radius'] + PARAMS['pin_clearance'], "
                "PARAMS['finger_thickness'], align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "show(right_finger, 'RightFinger')"
            ),
            (
                "link_z = finger_z + PARAMS['finger_thickness'] + 2\n"
                "left_link = Pos(-40, 0, link_z) * Box(PARAMS['link_length'], "
                "PARAMS['link_width'], PARAMS['link_thickness'], "
                "align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "right_link = Pos(40, 0, link_z) * Box(PARAMS['link_length'], "
                "PARAMS['link_width'], PARAMS['link_thickness'], "
                "align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "show(left_link, 'LeftLink')\nshow(right_link, 'RightLink')"
            ),
            (
                "actuator = Pos(0, -25, link_z) * Cylinder(PARAMS['actuator_radius'], "
                "PARAMS['actuator_thickness'], align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "show(actuator, 'ActuatorDisc')"
            ),
            (
                "left_pin = Pos(-PARAMS['support_offset'], 0, finger_z - 2) * "
                "Cylinder(PARAMS['pivot_radius'], PARAMS['finger_thickness'] + 4, "
                "align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "right_pin = Pos(PARAMS['support_offset'], 0, finger_z - 2) * "
                "Cylinder(PARAMS['pivot_radius'], PARAMS['finger_thickness'] + 4, "
                "align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
                "pivot_pins = Compound(children=[left_pin, right_pin])\n"
                "show(pivot_pins, 'PivotPins')"
            ),
        ]
        for index, code in enumerate(operations):
            result = engine.exec_tool("cad_execute", json.dumps({
                "code": code,
                "checkpoint": "assembly_complete" if index == len(operations) - 1 else "",
            }))
            assert "Error:" not in result and "rejected" not in result.lower(), result

        state = engine.exec_tool("cad_state", "{}")
        for name in (
            "Base", "LeftPivotSupport", "RightPivotSupport", "LeftFinger",
            "RightFinger", "LeftLink", "RightLink", "ActuatorDisc", "PivotPins",
        ):
            assert name in state
        rendered = engine.exec_tool("cad_render", json.dumps({
            "object_names": "*", "direction": "iso",
        }))
        assert "preview rendered" in rendered.lower(), rendered
        exported = engine.exec_tool("cad_export", json.dumps({
            "filename": "robotic_gripper.step", "formats": ["step", "stl"],
            "object_name": "*",
        }))
        assert "9 object(s) matched" in exported, exported
        assert (project / "robotic_gripper.step").is_file()
        assert (project / "robotic_gripper.stl").is_file()
        assert (project / "robotic_gripper.preview.png").is_file()
    finally:
        cad_mcp.RUNTIME.close()
        engine.PERMISSION_MODE, engine.ALLOWED_PATHS = old_mode, old_paths
