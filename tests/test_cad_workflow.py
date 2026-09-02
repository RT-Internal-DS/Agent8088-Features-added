from __future__ import annotations

import json
from pathlib import Path

from agent8088.cad_workflow import (
    CadPhase, CadWorkflow, parse_components, parse_primary_output, validate_plan,
)


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


def _write_source(job: CadWorkflow, tmp_path: Path, name: str) -> None:
    path = tmp_path / f"{name}.step.py"
    path.write_text("def gen_step(): pass\n")
    job.observe("write_file", {"filename": str(path), "content": "def gen_step(): pass\n"},
                "Wrote source")


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
    assert job.max_completion_tokens(
        31_072, plan_limit=1_500, source_limit=8_192,
        action_limit=2_048, final_limit=4_096) == 1_500
    assert job.validate_call("execute_shell", {"command": "anything"})
    assert job.validate_call("write_file", {"filename": "model.py", "content": "x"})


def test_provider_65536_limit_is_bounded_per_phase(tmp_path):
    limits = dict(plan_limit=1_500, source_limit=4_096,
                  action_limit=2_048, final_limit=4_096)
    job = CadWorkflow(tmp_path)
    args = _write_args(tmp_path)
    Path(args["filename"]).write_text(PLAN, encoding="utf-8")
    job.observe("write_file", args, "Wrote plan")

    assert job.allowed_tools({"write_file", "execute_shell", "read_text"}) == {
        "write_file", "read_text"
    }
    assert job.max_completion_tokens(65_536, **limits) == 4_096

    _write_source(job, tmp_path, "telescope")
    assert job.phase == CadPhase.GEN_REQUIRED
    assert job.allowed_tools({"write_file", "execute_shell", "read_text"}) == {
        "execute_shell", "read_text"
    }
    assert job.max_completion_tokens(65_536, **limits) == 2_048
    # A phase cap only ever narrows the provider ceiling; it never raises it.
    assert job.max_completion_tokens(1_024, **limits) == 1_024


def test_shipped_phase_caps_clear_the_reasoning_floor(engine):
    """Every phase budget must exceed what the model spends before a tool call.

    A reasoning model streams chain-of-thought into the *completion* budget, so
    a phase cap set below that floor truncates the round before any tool call is
    emitted and the phase can never pass. Measured against glm-5.1 on
    ollama-cloud: ~1.2-1.5k completion tokens for a trivial write_file and >10k
    tokens of pure reasoning before a non-trivial generator emits a character.
    Codex's 2048-token action cap and 4096-token source cap sat under that floor
    and deadlocked the workflow, so pin a floor here rather than re-derive it
    from a live run.
    """
    floor = 8_192
    assert engine.CAD_PLAN_MAX_COMPLETION_TOKENS >= floor
    assert engine.CAD_ACTION_MAX_COMPLETION_TOKENS >= floor
    assert engine.CAD_FINAL_MAX_COMPLETION_TOKENS >= floor
    assert engine.CAD_SOURCE_MAX_COMPLETION_TOKENS >= 4 * floor


def test_no_phase_carries_a_wall_clock_deadline():
    """A phase deadline cannot make a reasoning model answer faster.

    It only kills a healthy stream mid-thought, and the caller then has to
    invent a finish_reason to explain the truncation -- which routed a perfectly
    fine round into the "write something smaller" repair path. Measured latency
    for even a trivial CAD write_file is 39-52s, so every deadline short enough
    to bound the runaway case also kills the normal one.
    """
    assert not hasattr(CadWorkflow, "max_completion_seconds")


def test_source_phase_injects_the_contract_not_the_whole_playbook(tmp_path):
    """Upstream text-to-cad is progressive-disclosure: load a reference when its
    trigger fires, not the whole set every round. Injecting the 24KB modeling
    cookbook on each of dozens of CAD rounds is pure prompt overhead.
    """
    job = CadWorkflow(tmp_path)
    args = _write_args(tmp_path)
    Path(args["filename"]).write_text(PLAN, encoding="utf-8")
    job.observe("write_file", args, "Wrote plan")

    assert job.phase == CadPhase.SOURCE_REQUIRED
    assert job.resources() == ("references/step-generation.md",)
    # The cookbook is named for on-demand loading, never pasted in.
    instruction = job.instruction(cad_python="py", scripts_dir="/skill/scripts")
    assert "references/build123d-modeling.md" in instruction
    assert "view_skill" in instruction

    _write_source(job, tmp_path, "telescope")
    assert job.resources() == ()


def test_action_phases_spell_out_the_absolute_command(tmp_path):
    """The CAD interpreter and scripts directory reach the model only through
    these instructions, so a pathless phase leaves it guessing shell commands.
    """
    job = CadWorkflow(tmp_path)
    args = _write_args(tmp_path)
    Path(args["filename"]).write_text(PLAN, encoding="utf-8")
    job.observe("write_file", args, "Wrote plan")
    _write_source(job, tmp_path, "telescope")

    for phase in (CadPhase.GEN_REQUIRED, CadPhase.REFS_REQUIRED,
                  CadPhase.VALIDATE_REQUIRED, CadPhase.SNAPSHOT_REQUIRED):
        job.phase = phase
        instruction = job.instruction(
            cad_python="C:/cad/python.exe", scripts_dir="C:/skill/scripts")
        assert "C:/cad/python.exe" in instruction, phase
        assert "C:/skill/scripts" in instruction, phase


def test_primary_output_accepts_heading_form_from_live_plan():
    assert parse_primary_output(PLAN) == "telescope.step"
    heading = "## Primary Output\n`hilbert_cube.step` — generated model\n"
    assert parse_primary_output(heading) == "hilbert_cube.step"


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

    assert job.phase == CadPhase.SOURCE_REQUIRED
    assert job.components == ["Main tube", "Final telescope assembly"]
    state = json.loads((tmp_path / "telescope.cad-state.json").read_text())
    assert state["phase"] == "source_required"


def test_generation_inspection_validation_and_plan_update_are_ordered(tmp_path):
    job = CadWorkflow(tmp_path)
    plan_args = _write_args(tmp_path)
    Path(plan_args["filename"]).write_text(PLAN, encoding="utf-8")
    job.observe("write_file", plan_args, "Wrote plan")

    refs = _command(tmp_path, "inspect", "refs tube.step --facts --planes --positioning")
    assert "blocked" in job.validate_call("execute_shell", refs).lower()

    gen = _command(tmp_path, "gen", "tube.step.py --write --json")
    _write_source(job, tmp_path, "tube")
    assert job.phase == CadPhase.GEN_REQUIRED
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
    assert job.phase == CadPhase.SOURCE_REQUIRED
    assert job.checked == ["Main tube"]


def test_all_items_require_snapshot_then_viewer(tmp_path):
    one_item = PLAN.replace("- [ ] Main tube\n", "")
    job = CadWorkflow(tmp_path)
    args = _write_args(tmp_path, one_item)
    Path(args["filename"]).write_text(one_item, encoding="utf-8")
    job.observe("write_file", args, "Wrote plan")

    gen = _command(tmp_path, "gen", "telescope.step.py --write --json")
    _write_source(job, tmp_path, "telescope")
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
    _write_source(job, tmp_path, "tube")
    job.observe("execute_shell", gen, "Error: generator failed")
    assert job.phase == CadPhase.SOURCE_REQUIRED

    resumed = CadWorkflow.for_messages(
        tmp_path, [{"role": "user", "content": "continue telescope.plan.md"}])
    assert resumed.phase == CadPhase.SOURCE_REQUIRED
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


def _response(content: str, finish_reason: str = "stop"):
    message = type("Message", (), {"content": content})()
    choice = type("Choice", (), {"message": message,
                                 "finish_reason": finish_reason})()
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
    efforts = []

    def completion(_messages, _tools, max_tokens=None, **_kwargs):
        calls.append(max_tokens)
        prompts.append(_kwargs["system_prompt"])
        efforts.append(_kwargs["reasoning_effort"])
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
    assert efforts == ["low"] * 3
    assert "stopped in phase 'plan_required'" in answer


def test_a_cad_length_cutoff_is_retried_not_fatal(monkeypatch, engine, tmp_path):
    """A truncated round must not end the whole CAD lifecycle.

    Observed live: the plan round passed, the source round spent its entire
    completion budget on reasoning and returned finish_reason="length", and the
    request ended there. A CAD run legitimately spans dozens of bounded rounds,
    so one cutoff has to be recoverable -- the retry re-prompts for a smaller
    complete call instead of abandoning the run.
    """
    calls = []

    def completion(_messages, _tools, max_tokens=None, **_kwargs):
        calls.append(max_tokens)
        return _response("", finish_reason="length")

    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path)
    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    monkeypatch.setattr(engine, "_active_model_token_limits",
                        lambda *_args: (202_752, 65_536))

    engine.run_agent(
        [{"role": "user", "content": "Create a 20 mm CAD cube"}],
        max_turns=6, system_prompt="", tools_def=[],
        allowed_tools={"write_file", "execute_shell"},
    )

    # More than one attempt means the cutoff was retried rather than fatal.
    assert len(calls) > 1, calls
    # Every attempt stays bounded by the phase cap, never the 65k provider
    # ceiling that let a single round stream for 850s.
    assert calls == [engine.CAD_PLAN_MAX_COMPLETION_TOKENS] * len(calls)


def test_source_round_names_one_component_and_forbids_the_rest(tmp_path):
    """The source phase used to say both "only the next unchecked item" and
    "helper modules first, entry last" -- which invites authoring the whole
    program. With a four-item plan the model duly reasoned about all four at
    once and burned a full 32768-token round without emitting a tool call.
    """
    job = CadWorkflow(tmp_path)
    args = _write_args(tmp_path)
    Path(args["filename"]).write_text(PLAN, encoding="utf-8")
    job.observe("write_file", args, "Wrote plan")

    assert job.next_component() == "Main tube"
    instruction = job.instruction(cad_python="py", scripts_dir="/skill/scripts")
    assert "Main tube" in instruction
    assert "ONE write_file call" in instruction
    assert "Do not work the geometry out in your head" in instruction
    # The multi-file invitation is gone.
    assert "helper modules first" not in instruction

    # Once an item is checked off, the phase points at the next one.
    job.checked = ["Main tube"]
    assert job.next_component() == "Final telescope assembly"


def test_command_only_phases_run_without_a_model_round(tmp_path):
    """gen / refs / validate / plan-update / snapshot / viewer need no model.

    Upstream text-to-cad has no phase machine: the agent composes source -> gen
    -> inspect -> repair, several calls per round. Enforcing one phase per model
    round buys determinism but pays a full reasoning cycle for each -- measured
    at 1-20 minutes apiece on a reasoning model, times four command-only phases,
    times every component in the plan. Since the controller builds those command
    strings itself, it can execute them and keep the model for real judgement.
    """
    job = CadWorkflow(tmp_path)
    args = _write_args(tmp_path)
    Path(args["filename"]).write_text(PLAN, encoding="utf-8")
    job.observe("write_file", args, "Wrote plan")

    # Authoring source is judgement: no autorun.
    assert job.phase == CadPhase.SOURCE_REQUIRED
    assert job.autorun(cad_python="py", scripts_dir="/skill/scripts") is None

    _write_source(job, tmp_path, "tube")
    assert job.phase == CadPhase.GEN_REQUIRED

    # Every command-only phase now yields a concrete call.
    seen = []
    for _ in range(6):
        planned = job.autorun(cad_python="py", scripts_dir="/skill/scripts")
        if planned is None:
            break
        name, call_args = planned
        seen.append((job.phase, name))
        if name == "write_file":
            Path(call_args["filename"]).write_text(call_args["content"],
                                                  encoding="utf-8")
        job.observe(name, call_args, '{"ok":true}')

    phases = [phase for phase, _name in seen]
    assert CadPhase.GEN_REQUIRED in phases
    assert CadPhase.REFS_REQUIRED in phases
    assert CadPhase.VALIDATE_REQUIRED in phases
    assert CadPhase.PLAN_UPDATE_REQUIRED in phases
    # The plan check-off happened deterministically, so the component advanced
    # and the workflow is back to the only judgement step: the next source file.
    assert job.checked == ["Main tube"]
    assert job.phase == CadPhase.SOURCE_REQUIRED


def test_autorun_commands_match_the_vendored_cli_signatures(tmp_path):
    """Pin each generated command to the real CLI's accepted syntax.

    Autorun hardcodes these strings, so a wrong flag fails every run
    deterministically rather than being recovered by the model. Checked against
    the vendored scripts' own --help: gen takes positional targets plus
    --write/--json; inspect refs takes a positional entry plus the fact flags;
    inspect validate takes a positional entry; snapshot takes NEITHER a
    positional target NOR --json -- it is --input/--output, which is the shape
    this originally got wrong.
    """
    job = CadWorkflow(tmp_path)
    args = _write_args(tmp_path)
    Path(args["filename"]).write_text(PLAN, encoding="utf-8")
    job.observe("write_file", args, "Wrote plan")
    _write_source(job, tmp_path, "tube")

    def command_for(phase):
        job.phase = phase
        planned = job.autorun(cad_python="py", scripts_dir="/skill/scripts")
        return planned[1]["command"] if planned else ""

    gen = command_for(CadPhase.GEN_REQUIRED)
    assert "/skill/scripts/gen" in gen and "--write" in gen and "--json" in gen

    refs = command_for(CadPhase.REFS_REQUIRED)
    assert "/skill/scripts/inspect\" refs" in refs
    assert "--facts" in refs and "--planes" in refs and "--positioning" in refs

    validate = command_for(CadPhase.VALIDATE_REQUIRED)
    assert "/skill/scripts/inspect\" validate" in validate

    snapshot = command_for(CadPhase.SNAPSHOT_REQUIRED)
    assert "--input" in snapshot and "--output" in snapshot
    assert "--json" not in snapshot, "scripts/snapshot has no --json"
    assert snapshot.endswith('.png"'), snapshot
    assert job.snapshot_output() == "hilbert-cube.png" or job.snapshot_output().endswith(".png")


def test_autorun_stops_on_failure_and_hands_back_to_the_model(tmp_path):
    """A failed deterministic step must not be retried in a loop.

    Repair needs the model -- it has to read the error and change the source --
    so a step that does not advance the phase ends the autorun chain.
    """
    job = CadWorkflow(tmp_path)
    args = _write_args(tmp_path)
    Path(args["filename"]).write_text(PLAN, encoding="utf-8")
    job.observe("write_file", args, "Wrote plan")
    _write_source(job, tmp_path, "tube")

    name, call_args = job.autorun(cad_python="py", scripts_dir="/skill/scripts")
    assert name == "execute_shell"
    job.observe(name, call_args, "Traceback (most recent call last): boom")

    # Failure routes back to source authoring, which has no autorun.
    assert job.phase == CadPhase.SOURCE_REQUIRED
    assert job.autorun(cad_python="py", scripts_dir="/skill/scripts") is None


def test_plan_checkoff_ticks_exactly_one_box(tmp_path):
    job = CadWorkflow(tmp_path)
    args = _write_args(tmp_path)
    Path(args["filename"]).write_text(PLAN, encoding="utf-8")
    job.observe("write_file", args, "Wrote plan")

    updated = job.plan_text_with_next_checked()
    assert "- [x] Main tube" in updated
    assert "- [ ] Final telescope assembly" in updated
    # Only the in-flight item changes; the verification checklist is untouched.
    assert updated.count("[x]") == 1


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
    assert state["phase"] == "source_required"
