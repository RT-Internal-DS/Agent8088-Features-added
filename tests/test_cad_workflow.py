from __future__ import annotations

import json
from pathlib import Path

from agent8088.cad_workflow import CadPhase, CadWorkflow, parse_components, validate_plan


PLAN = """# Telescope CAD Plan

## Brief
- Units: mm
- Primary output: telescope.step

## Components
- [ ] Main tube
- [ ] Final telescope assembly

## Required validation
- Validate every solid
- Review snapshot
- Open in CAD Viewer
"""


def _write_args(tmp_path: Path, content: str = PLAN):
    return {"filename": str(tmp_path / "telescope.plan.md"), "content": content}


def _command(tmp_path: Path, entry: str, tail: str) -> dict:
    scripts = tmp_path / "skills" / "cad" / "scripts" / entry
    python = tmp_path / "cad python.exe"
    return {"command": f'"{python}" "{scripts}" {tail}'}


def test_plan_parser_and_validation_require_a_real_unchecked_plan():
    assert parse_components(PLAN) == [
        ("Main tube", False), ("Final telescope assembly", False)
    ]
    assert validate_plan(PLAN) is None
    assert "title" in validate_plan(PLAN.removeprefix("# Telescope CAD Plan\n"))
    assert "units" in validate_plan(PLAN.replace("Units", "Scale"))
    assert "primary STEP" in validate_plan(PLAN.replace("telescope.step", "telescope.obj"))
    assert "start unchecked" in validate_plan(PLAN.replace("[ ] Main", "[x] Main"))


def test_plan_parser_accepts_natural_build_headings_and_list_markers():
    variant = PLAN.replace("## Components", "## Build Checklist").replace(
        "- [ ] Main tube", "1. [ ] Hilbert curve bars").replace(
        "- [ ] Final telescope assembly", "* [ ] Outer cube assembly")
    assert parse_components(variant) == [
        ("Hilbert curve bars", False), ("Outer cube assembly", False)
    ]
    assert validate_plan(variant) is None


def test_validation_checkboxes_are_not_treated_as_components():
    plan = PLAN.replace(
        "- Validate every solid\n- Review snapshot\n- Open in CAD Viewer",
        "- [ ] Validate every solid\n- [ ] Review snapshot\n- [ ] Open in CAD Viewer",
    )
    assert parse_components(plan) == [
        ("Main tube", False), ("Final telescope assembly", False)
    ]


def test_first_phase_exposes_only_write_file_and_caps_completion(tmp_path):
    job = CadWorkflow(tmp_path)
    assert job.allowed_tools({"write_file", "execute_shell", "read_text"}) == {"write_file"}
    assert job.max_completion_tokens(31_072, 1_500) == 1_500
    assert job.validate_call("execute_shell", {"command": "anything"})
    assert job.validate_call("write_file", {"filename": "model.py", "content": "x"})


def test_plan_must_live_in_workspace(tmp_path):
    job = CadWorkflow(tmp_path)
    outside = tmp_path.parent / "outside.plan.md"
    error = job.validate_call("write_file", {"filename": str(outside), "content": PLAN})
    assert "artifacts workspace" in error


def test_successful_plan_creates_persistent_state_and_enters_build(tmp_path):
    job = CadWorkflow(tmp_path)
    args = _write_args(tmp_path)
    assert job.validate_call("write_file", args) is None
    Path(args["filename"]).write_text(PLAN, encoding="utf-8")
    job.observe("write_file", args, "Wrote 220 bytes")

    assert job.phase == CadPhase.BUILD
    assert job.components == ["Main tube", "Final telescope assembly"]
    state = json.loads((tmp_path / "telescope.cad-state.json").read_text())
    assert state["phase"] == "build"


def test_generation_inspection_validation_and_plan_update_are_ordered(tmp_path):
    job = CadWorkflow(tmp_path)
    plan_args = _write_args(tmp_path)
    Path(plan_args["filename"]).write_text(PLAN, encoding="utf-8")
    job.observe("write_file", plan_args, "Wrote plan")

    refs = _command(tmp_path, "inspect", "refs tube.step --facts --planes --positioning")
    assert "blocked" in job.validate_call("execute_shell", refs).lower()

    gen = _command(tmp_path, "gen", "tube.step.py --write --json")
    (tmp_path / "tube.step.py").write_text("def gen_step(): pass\n")
    assert job.validate_call("execute_shell", gen) is None
    job.observe("execute_shell", gen, '{"ok":true,"outcome":"built"}')
    assert job.phase == CadPhase.REFS_REQUIRED

    validate = _command(tmp_path, "inspect", "validate tube.step")
    assert "blocked" in job.validate_call("execute_shell", validate).lower()
    job.observe("execute_shell", refs, '{"ok":true}')
    assert job.phase == CadPhase.VALIDATE_REQUIRED
    job.observe("execute_shell", validate, '{"ok":true,"failureCount":0}')
    assert job.phase == CadPhase.PLAN_UPDATE_REQUIRED

    both = PLAN.replace("[ ] Main tube", "[x] Main tube").replace(
        "[ ] Final telescope assembly", "[x] Final telescope assembly")
    assert "exactly" in job.validate_call(
        "write_file", _write_args(tmp_path, both)).lower()

    first = PLAN.replace("[ ] Main tube", "[x] Main tube")
    update = _write_args(tmp_path, first)
    assert job.validate_call("write_file", update) is None
    job.observe("write_file", update, "Wrote updated plan")
    assert job.phase == CadPhase.BUILD
    assert job.checked == ["Main tube"]


def test_all_items_require_snapshot_then_viewer(tmp_path):
    one_item = PLAN.replace("- [ ] Main tube\n", "")
    job = CadWorkflow(tmp_path)
    args = _write_args(tmp_path, one_item)
    Path(args["filename"]).write_text(one_item, encoding="utf-8")
    job.observe("write_file", args, "Wrote plan")

    gen = _command(tmp_path, "gen", "telescope.step.py --write --json")
    (tmp_path / "telescope.step.py").write_text("def gen_step(): pass\n")
    refs = _command(tmp_path, "inspect", "refs telescope.step --facts --planes --positioning")
    validate = _command(tmp_path, "inspect", "validate telescope.step")
    for call, output in ((gen, '{"ok":true}'), (refs, '{"ok":true}'),
                         (validate, '{"ok":true,"failureCount":0}')):
        assert job.validate_call("execute_shell", call) is None
        job.observe("execute_shell", call, output)

    checked = one_item.replace("[ ] Final", "[x] Final")
    update = _write_args(tmp_path, checked)
    job.observe("write_file", update, "Wrote updated plan")
    assert job.phase == CadPhase.SNAPSHOT_REQUIRED
    assert job.validate_call("open_cad_viewer", {"filename": "telescope.step"})

    snapshot = _command(tmp_path, "snapshot", "telescope.step --json")
    job.observe("execute_shell", snapshot, '{"ok":true,"images":["telescope.png"]}')
    assert job.phase == CadPhase.VIEWER_REQUIRED
    job.observe("open_cad_viewer", {"filename": "telescope.step"},
                "Opened: http://127.0.0.1:3245")
    assert job.phase == CadPhase.COMPLETE


def test_failed_script_does_not_advance_and_state_can_resume(tmp_path):
    job = CadWorkflow(tmp_path)
    args = _write_args(tmp_path)
    Path(args["filename"]).write_text(PLAN, encoding="utf-8")
    job.observe("write_file", args, "Wrote plan")
    gen = _command(tmp_path, "gen", "tube.step.py --write --json")
    (tmp_path / "tube.step.py").write_text("def gen_step(): pass\n")
    job.observe("execute_shell", gen, "Error: generator failed")
    assert job.phase == CadPhase.BUILD

    resumed = CadWorkflow.for_messages(
        tmp_path, [{"role": "user", "content": "continue telescope.plan.md"}])
    assert resumed.phase == CadPhase.BUILD
    assert resumed.plan_path == tmp_path / "telescope.plan.md"


def test_existing_step_export_bypasses_source_generation(tmp_path):
    job = CadWorkflow.for_messages(
        tmp_path,
        [{"role": "user", "content": "Export existing.step to STL"}],
    )
    assert job.requires_generation is False
    args = _write_args(tmp_path)
    Path(args["filename"]).write_text(PLAN, encoding="utf-8")
    job.observe("write_file", args, "Wrote plan")
    assert job.phase == CadPhase.REFS_REQUIRED


def _response(content: str):
    message = type("Message", (), {"content": content})()
    choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
    return type("Response", (), {"choices": [choice]})()


def test_engine_treats_vendored_cad_skill_as_progressive(engine):
    skill = engine.SKILL_PACKAGES["cad"]
    assert skill["progressive"] is True
    rendered = engine.render_skill_docs({"cad": skill})
    assert "view_skill" in rendered
    assert "## Required workflow" not in rendered


def test_engine_caps_plan_turn_and_refuses_to_finish_with_prose(
        monkeypatch, engine, tmp_path):
    calls = []
    prompts = []

    def completion(_messages, _tools, max_tokens=None, **_kwargs):
        calls.append(max_tokens)
        prompts.append(_kwargs["system_prompt"])
        return _response("Here is my proposed CAD plan in prose.")

    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path)
    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    monkeypatch.setattr(engine, "_active_model_token_limits",
                        lambda *_args: (100_000, 31_072))

    answer = engine.run_agent(
        [{"role": "user", "content": "Create a 20 mm CAD cube"}],
        max_turns=4, system_prompt="", tools_def=[],
        allowed_tools={"write_file", "execute_shell"},
    )

    assert calls == [engine.CAD_PLAN_MAX_COMPLETION_TOKENS] * 3
    assert all("- write_file(" in prompt for prompt in prompts)
    assert all("- execute_shell(" not in prompt for prompt in prompts)
    assert all("## Required workflow" not in prompt for prompt in prompts)
    assert "stopped in phase 'plan_required'" in answer


def test_engine_first_cad_action_is_the_persistent_markdown_plan(
        monkeypatch, engine, tmp_path):
    plan_args = {"filename": str(tmp_path / "cube.plan.md"), "content": """# Cube

## Brief
- Units: mm
- Primary output: cube.step

## Components
- [ ] Final cube

## Validation
- validate solid and review snapshot
"""}
    responses = [
        _response(f"✿FUNCTION✿: write_file ✿ARGS✿: {json.dumps(plan_args)}"),
        _response("done"), _response("done"), _response("done"),
    ]
    executed = []

    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path)
    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda *_a, **_kw: responses.pop(0))
    monkeypatch.setattr(
        engine, "exec_tool",
        lambda name, raw, **_kw: executed.append((name, json.loads(raw))) or "Wrote plan",
    )

    engine.run_agent(
        [{"role": "user", "content": "Create a 20 mm CAD cube"}],
        max_turns=5, system_prompt="", tools_def=[],
        allowed_tools={"write_file", "execute_shell"},
    )

    assert executed == [("write_file", plan_args)]
    state = json.loads((tmp_path / "cube.cad-state.json").read_text())
    assert state["phase"] == "build"
