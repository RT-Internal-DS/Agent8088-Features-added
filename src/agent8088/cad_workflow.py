"""Deterministic orchestration around the vendored text-to-cad skill.

The skill remains the authority for geometry generation and inspection.  This
module owns only the lifecycle that language-model instructions cannot enforce
reliably: plan first, generate, inspect refs, validate, update the checklist,
snapshot, and hand the finished artifact to the Viewer.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class CadPhase(str, Enum):
    PLAN_REQUIRED = "plan_required"
    SOURCE_REQUIRED = "source_required"
    GEN_REQUIRED = "gen_required"
    REFS_REQUIRED = "refs_required"
    VALIDATE_REQUIRED = "validate_required"
    PLAN_UPDATE_REQUIRED = "plan_update_required"
    SNAPSHOT_REQUIRED = "snapshot_required"
    VIEWER_REQUIRED = "viewer_required"
    COMPLETE = "complete"


_CHECKBOX_RE = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])\s*\[([ xX])\]\s+(.+?)\s*$", re.MULTILINE)
_PLAN_PATH_RE = re.compile(r"([^\s\"']+\.plan\.md)\b", re.IGNORECASE)
_PRIMARY_OUTPUT_RE = re.compile(
    r"(?im)^\s*-?\s*Primary\s+output\s*:\s*`?([^`\s]+\.(?:step|stp))`?\s*$"
)


def parse_primary_output(content: str) -> str:
    """Accept both an inline field and a Markdown Primary Output section."""
    inline = _PRIMARY_OUTPUT_RE.search(content or "")
    if inline:
        return inline.group(1)
    heading = re.search(
        r"(?im)^##+\s+Primary\s+Output\s*$\s*\n\s*(?:[-*+]\s*)?"
        r"`?([^`\s]+\.(?:step|stp))`?",
        content or "",
    )
    return heading.group(1) if heading else ""


def parse_components(content: str) -> list[tuple[str, bool]]:
    """Return build-item checkboxes while ignoring verification checklists.

    Models naturally use headings such as ``Build checklist``, ``Parts``, or
    ``Geometry`` even when asked for ``Components``. Requiring one exact heading
    made otherwise valid plans impossible to start. Keep validation/snapshot
    checkboxes out of the component state machine, but accept ordinary Markdown
    heading and list variants for the build section.
    """
    items: list[tuple[str, bool]] = []
    heading = ""
    excluded = re.compile(
        r"\b(?:validat\w*|verif\w*|required\s+checks?|snapshot\w*|"
        r"visual\s+review|viewer\w*|handoff\w*|tests?)\b",
        re.IGNORECASE,
    )
    for line in (content or "").splitlines():
        header = re.match(r"^#{2,6}\s+(.+?)\s*$", line)
        if header:
            heading = header.group(1)
            continue
        match = _CHECKBOX_RE.match(line)
        if match and not excluded.search(heading):
            items.append((match.group(2).strip(), match.group(1).lower() == "x"))
    return items


def validate_plan(content: str) -> str | None:
    """Return a focused validation error, or None for a usable CAD plan."""
    lowered = (content or "").casefold()
    components = parse_components(content)
    if not re.search(r"(?m)^#\s+\S", content or ""):
        return "the plan needs a Markdown title"
    if "unit" not in lowered:
        return "the plan must state its units"
    if ".step" not in lowered and ".stp" not in lowered:
        return "the plan must name the primary STEP output"
    if not components:
        return ("the plan needs at least one build checklist item formatted like "
                "`## Components` followed by `- [ ] Main solid`")
    if any(checked for _label, checked in components):
        return "new plan component items must start unchecked"
    if "validation" not in lowered and "required checks" not in lowered:
        return "the plan needs a validation or required-checks section"
    return None


def _successful(result: str) -> bool:
    text = (result or "").strip()
    lowered = text.casefold()
    if not text or text.startswith("ESCALATION_REQUEST"):
        return False
    if "traceback (most recent call last)" in lowered:
        return False
    if re.search(r'"ok"\s*:\s*false', lowered):
        return False
    return not lowered.startswith(("error:", "failed:", "blocked:"))


def _cad_script(command: str) -> tuple[str, str]:
    """Return (entry point, subcommand) from an approved CAD script command."""
    normal = (command or "").replace("\\", "/")
    match = re.search(r"/scripts/(gen|inspect|snapshot|export|artifact)(?:[\"']|\s|/)",
                      normal, re.IGNORECASE)
    if not match:
        return "", ""
    entry = match.group(1).lower()
    tail = normal[match.end():].strip().lstrip("\"'")
    subcommand = tail.split(None, 1)[0].strip("\"'").lower() if tail else ""
    return entry, subcommand


@dataclass
class CadWorkflow:
    workspace: Path
    phase: CadPhase = CadPhase.PLAN_REQUIRED
    plan_path: Path | None = None
    components: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    primary_output: str = ""
    requires_generation: bool = True
    current_source: str = ""
    current_step: str = ""
    last_generated_command: str = ""
    last_validated_command: str = ""

    @property
    def state_path(self) -> Path | None:
        if self.plan_path is None:
            return None
        return self.plan_path.with_suffix("").with_suffix(".cad-state.json")

    @classmethod
    def for_messages(cls, workspace: Path, messages: list[dict]) -> "CadWorkflow":
        """Resume an explicitly mentioned plan; otherwise start a fresh job."""
        workspace = Path(workspace).resolve()
        joined = "\n".join(str(m.get("content") or "") for m in messages)
        matches = _PLAN_PATH_RE.findall(joined)
        for raw in reversed(matches):
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = workspace / candidate
            state = candidate.with_suffix("").with_suffix(".cad-state.json")
            loaded = cls._load_state(workspace, state)
            if loaded is not None:
                return loaded
        latest = str(messages[-1].get("content") or "") if messages else ""
        direct_artifact = bool(
            re.search(r"\b(?:export|render|preview|inspect|measure|open)\b", latest,
                      re.IGNORECASE)
            and re.search(r"\.(?:step|stp)\b", latest, re.IGNORECASE)
        )
        return cls(workspace=workspace, requires_generation=not direct_artifact)

    @classmethod
    def _load_state(cls, workspace: Path, path: Path) -> "CadWorkflow | None":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            plan_path = Path(data["plan_path"]).resolve()
            plan_path.relative_to(workspace)
            return cls(
                workspace=workspace,
                phase=CadPhase(
                    "source_required" if data["phase"] == "build" else data["phase"]
                ),
                plan_path=plan_path,
                components=list(data.get("components") or []),
                checked=list(data.get("checked") or []),
                primary_output=str(data.get("primary_output") or ""),
                requires_generation=bool(data.get("requires_generation", True)),
                current_source=str(data.get("current_source") or ""),
                current_step=str(data.get("current_step") or ""),
                last_generated_command=str(data.get("last_generated_command") or ""),
                last_validated_command=str(data.get("last_validated_command") or ""),
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def _persist(self) -> None:
        state = self.state_path
        if state is None:
            return
        payload = {
            "version": 1,
            "phase": self.phase.value,
            "plan_path": str(self.plan_path),
            "components": self.components,
            "checked": self.checked,
            "primary_output": self.primary_output,
            "requires_generation": self.requires_generation,
            "current_source": self.current_source,
            "current_step": self.current_step,
            "last_generated_command": self.last_generated_command,
            "last_validated_command": self.last_validated_command,
        }
        state.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def autorun(self, *, cad_python: str, scripts_dir: str) -> tuple[str, dict] | None:
        """The tool call for a phase that needs no judgement, or None.

        Upstream text-to-cad has no phase machine at all: the agent composes
        source -> gen -> inspect -> repair, often several calls in one round.
        Splitting that into one enforced phase per model round buys determinism
        but pays for it in latency, because every phase is a fresh reasoning
        cycle -- and on a reasoning model those are the entire cost. Measured
        here: 1-20 minutes per round, times four command-only phases, times
        every component in the plan.

        The command-only phases are already fully determined -- this module
        builds the exact string and then spent a round asking the model to type
        it back verbatim. Run them directly instead and keep the model for the
        steps that genuinely need it: planning, authoring source, repairing a
        failure, and the final report. The phase machine, its ordering, and its
        audit trail are unchanged; only the round-trip disappears.
        """
        if self.phase == CadPhase.GEN_REQUIRED and self.current_source:
            return ("execute_shell", {"command": (
                f'"{cad_python}" "{scripts_dir}/gen" '
                f'"{self.current_source}" --write --json')})
        if self.phase == CadPhase.REFS_REQUIRED and self.current_step:
            return ("execute_shell", {"command": (
                f'"{cad_python}" "{scripts_dir}/inspect" refs '
                f'"{self.current_step}" --facts --planes --positioning')})
        if self.phase == CadPhase.VALIDATE_REQUIRED and self.current_step:
            return ("execute_shell", {"command": (
                f'"{cad_python}" "{scripts_dir}/inspect" validate '
                f'"{self.current_step}"')})
        if self.phase == CadPhase.PLAN_UPDATE_REQUIRED:
            updated = self.plan_text_with_next_checked()
            if updated and self.plan_path is not None:
                return ("write_file", {"filename": str(self.plan_path),
                                       "content": updated})
            return None
        if self.phase == CadPhase.SNAPSHOT_REQUIRED:
            target = self.primary_output or self.current_step
            if target:
                # scripts/snapshot takes no positional target and has no
                # --json: it is --input/--output, unlike gen and inspect.
                # Verified against the vendored CLI's own --help.
                return ("execute_shell", {"command": (
                    f'"{cad_python}" "{scripts_dir}/snapshot" '
                    f'--input "{target}" --output "{self.snapshot_output()}"')})
            return None
        if self.phase == CadPhase.VIEWER_REQUIRED:
            target = self.primary_output or self.current_step
            if target:
                # open_cad_viewer resolves through resolve_user_path, which does
                # not share execute_shell's cwd, so hand it an absolute path.
                return ("open_cad_viewer",
                        {"filename": str(self.workspace / target)})
            return None
        return None

    def snapshot_output(self) -> str:
        """Where the mandatory snapshot PNG for the primary artifact goes."""
        target = self.primary_output or self.current_step
        if not target:
            return ""
        stem = target
        for suffix in (".step.py", ".step", ".stp"):
            if stem.casefold().endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        return f"{stem}.png"

    def plan_text_with_next_checked(self) -> str:
        """The plan file with exactly the just-validated item ticked off.

        Ticking one checkbox is bookkeeping the controller already has the
        information to do -- it knows which item is in flight -- so it does not
        need a model round. Returns "" when the edit cannot be made safely.
        """
        if self.plan_path is None:
            return ""
        target = self.next_component()
        if not target:
            return ""
        try:
            content = self.plan_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return ""
        lines = content.splitlines(keepends=True)
        for index, line in enumerate(lines):
            match = _CHECKBOX_RE.match(line.rstrip("\r\n"))
            if match and match.group(1).lower() != "x" and match.group(2).strip() == target:
                lines[index] = line.replace("[ ]", "[x]", 1)
                return "".join(lines)
        return ""

    def next_component(self) -> str:
        """The one checklist item this source round is allowed to build."""
        remaining = [item for item in self.components if item not in self.checked]
        return remaining[0] if remaining else ""

    def allowed_tools(self, available: set[str]) -> set[str]:
        phase_tools = {
            CadPhase.PLAN_REQUIRED: {"write_file"},
            CadPhase.SOURCE_REQUIRED: {"write_file", "read_text", "view_skill", "web_search"},
            CadPhase.GEN_REQUIRED: {"execute_shell", "read_text"},
            CadPhase.REFS_REQUIRED: {"execute_shell", "read_text"},
            CadPhase.VALIDATE_REQUIRED: {"execute_shell", "read_text"},
            CadPhase.PLAN_UPDATE_REQUIRED: {"write_file", "read_text"},
            CadPhase.SNAPSHOT_REQUIRED: {"execute_shell", "read_text"},
            CadPhase.VIEWER_REQUIRED: {"open_cad_viewer", "read_text"},
            CadPhase.COMPLETE: set(available),
        }[self.phase]
        return set(available) & phase_tools

    def max_completion_tokens(self, default: int, *, plan_limit: int,
                              source_limit: int, action_limit: int,
                              final_limit: int) -> int:
        configured = {
            CadPhase.PLAN_REQUIRED: plan_limit,
            CadPhase.SOURCE_REQUIRED: source_limit,
            CadPhase.COMPLETE: final_limit,
        }.get(self.phase, action_limit)
        return min(default, configured) if configured > 0 else default

    #: Reference a phase cannot proceed without, injected into that round's
    #: system prompt. Everything else stays on demand via ``view_skill``, the
    #: way upstream's progressive-disclosure skill intends. Only the phase that
    #: authors source gets an injection, and only the one file that defines the
    #: mandatory ``gen_step()`` contract -- the 24KB modeling cookbook is named
    #: in the instruction instead of pasted into every round.
    def resources(self) -> tuple[str, ...]:
        """Upstream reference the current phase cannot proceed without."""
        if self.phase == CadPhase.SOURCE_REQUIRED:
            return ("references/step-generation.md",)
        return ()

    def instruction(self, *, skill_text: str = "", cad_python: str = "",
                    scripts_dir: str = "") -> str:
        plan = str(self.plan_path) if self.plan_path else "<descriptive-name>.plan.md"
        common = "CAD WORKFLOW CONTROLLER: obey the current phase; do not skip ahead."
        if self.phase == CadPhase.PLAN_REQUIRED:
            return common + f"""
CURRENT PHASE: PLAN_REQUIRED.
Your first and only action is one write_file call creating {plan} under the artifacts workspace.
Do not print a plan in chat. Do not write geometry source yet. Do not call any other tool.
The Markdown must contain: a title; units; primary .step output; assumptions; and required
validation, snapshot-review, and Viewer-handoff checks. The build list MUST use this literal shape:
## Components
- [ ] Main solid
Add further unchecked items in build order; the final assembly/output is last when applicable.

One checklist item per BUILDABLE NAMED SOLID -- each item is one <name>.step.py that will be
generated, inspected and validated on its own. A single part is ONE item even when building it
takes several helper functions or modules. Do NOT list implementation steps, algorithms, helper
modules, unions, or the export itself as items: those are how you build an item, not items.
Wrong: "point generator", "bar extruder", "union of segments", "export to STEP".
Right: one item "Hilbert curve solid" -- or, for a real assembly, one item per component part
plus a final assembly item.
"""
        phase_text = {
            CadPhase.SOURCE_REQUIRED: (
                f"Build exactly one checklist item now: \"{self.next_component()}\".\n"
                "Make ONE write_file call containing ONE complete file, and stop. Do not "
                "write, outline, or plan the other checklist items in this call -- this "
                "phase repeats until the entry generator exists, so you will be asked for "
                "each remaining file in its own round.\n"
                "Name a helper module <name>.py. Name the buildable entry "
                "<name>.step.py, and write it only once its helpers exist; its gen_step() "
                "must return closed positive-volume geometry.\n"
                "Write compact parametric build123d source: compute repeated geometry at "
                "runtime with parameters, helper functions, recursion and loops. Never "
                "unroll a coordinate list or repeat a shape literally.\n"
                "Do not work the geometry out in your head. Do not enumerate, trace, or "
                "verify individual coordinates or path positions while reasoning -- the "
                "code you are writing computes them at runtime, which is the entire "
                "reason to write it. Reason only about the shape of the code, then emit "
                "the write_file call.\n"
                "Do not run generation yet. Read references/build123d-modeling.md with "
                "view_skill only if you need a modeling pattern you do not already know."
            ),
            CadPhase.GEN_REQUIRED: (
                f"Source is written. Make exactly one execute_shell call: \"{cad_python}\" "
                f"\"{scripts_dir}/gen\" \"{self.current_source}\" --write --json"
            ),
            CadPhase.REFS_REQUIRED: (
                f"Generation passed. Make exactly one execute_shell call: \"{cad_python}\" "
                f"\"{scripts_dir}/inspect\" refs \"{self.current_step}\" --facts "
                "--planes --positioning. Do not mark the plan yet."
            ),
            CadPhase.VALIDATE_REQUIRED: (
                f"Reference inspection passed. Make exactly one execute_shell call: "
                f"\"{cad_python}\" \"{scripts_dir}/inspect\" validate "
                f"\"{self.current_step}\". Do not mark the plan yet."
            ),
            CadPhase.PLAN_UPDATE_REQUIRED: f"Validation passed. Update only {plan}, checking exactly the one item just validated. Do not start another component in the same call.",
            CadPhase.SNAPSHOT_REQUIRED: (
                "Every planned item is validated. Make exactly one execute_shell call: "
                f"\"{cad_python}\" \"{scripts_dir}/snapshot\" "
                f"\"{self.primary_output or self.current_step}\" --json, then review the "
                "returned image before continuing."
            ),
            CadPhase.VIEWER_REQUIRED: "Snapshot generation passed. Call open_cad_viewer on the primary STEP. Do not claim completion before that call succeeds.",
            CadPhase.COMPLETE: "The enforced CAD lifecycle is complete. Report files, checks actually run, snapshot, Viewer link, assumptions, and caveats.",
        }[self.phase]
        upstream = f"\n\nRELEVANT UPSTREAM TEXT-TO-CAD REFERENCE:\n{skill_text}" if skill_text else ""
        return f"{common}\nCURRENT PHASE: {self.phase.value.upper()}.\n{phase_text}{upstream}"

    def validate_call(self, name: str, args: dict) -> str | None:
        if self.phase == CadPhase.PLAN_REQUIRED:
            if name != "write_file":
                return "CAD workflow blocked this call: create the Markdown plan with write_file first."
            filename = str(args.get("filename") or "")
            if not filename.casefold().endswith(".plan.md"):
                return "CAD workflow blocked this write: the first file must end in .plan.md."
            candidate = Path(filename)
            candidate = (candidate if candidate.is_absolute()
                         else self.workspace / candidate).resolve()
            try:
                candidate.relative_to(self.workspace)
            except ValueError:
                return "CAD workflow blocked this write: the plan must live in the artifacts workspace."
            problem = validate_plan(str(args.get("content") or ""))
            if problem:
                return f"CAD workflow rejected the plan: {problem}. Correct it with one write_file call."
            return None

        if name == "open_cad_viewer" and self.phase != CadPhase.VIEWER_REQUIRED:
            return "CAD workflow blocked Viewer handoff until generation, inspection, validation, plan completion, and snapshot all pass."

        if name != "execute_shell":
            if (name == "write_file" and self.plan_path is not None
                    and Path(str(args.get("filename") or "")).name == self.plan_path.name):
                if self.phase != CadPhase.PLAN_UPDATE_REQUIRED:
                    return "CAD workflow blocked this plan update: validate the current generated item first."
                parsed = parse_components(str(args.get("content") or ""))
                labels = [label for label, _checked in parsed]
                checked = [label for label, mark in parsed if mark]
                newly_checked = [label for label in checked if label not in self.checked]
                if labels != self.components:
                    return "CAD workflow rejected the plan update: component labels and order must not change."
                if any(label not in checked for label in self.checked):
                    return "CAD workflow rejected the plan update: previously validated items must remain checked."
                if len(newly_checked) != 1:
                    return "CAD workflow rejected the plan update: check exactly the one item just validated."
                if (self.requires_generation
                        and len(checked) == len(self.components) and self.primary_output
                        and Path(self.primary_output).stem.casefold()
                        not in self.last_generated_command.casefold()):
                    return ("CAD workflow rejected the final plan update: the most "
                            "recent validated generator does not produce the plan's "
                            f"primary output {self.primary_output}.")
            return None

        entry, subcommand = _cad_script(str(args.get("command") or ""))
        if not entry:
            return None
        if entry == "gen" and self.phase != CadPhase.GEN_REQUIRED:
            return f"CAD workflow blocked generation during {self.phase.value}. Complete the required phase first."
        if entry == "gen":
            command = str(args.get("command") or "").replace("\\", "/")
            targets = re.findall(r"(?:^|\s)[\"']?([^\s\"']+\.step\.py)[\"']?", command,
                                 re.IGNORECASE)
            if not targets:
                return "CAD workflow blocked generation: scripts/gen needs an explicit .step.py target."
            source = Path(targets[-1])
            source = source if source.is_absolute() else self.workspace / source
            if not source.is_file():
                return ("CAD workflow blocked generation: write the build123d source "
                        f"{source.name} before running scripts/gen.")
        if entry == "inspect" and subcommand == "refs" and self.phase != CadPhase.REFS_REQUIRED:
            return f"CAD workflow blocked refs inspection during {self.phase.value}."
        if entry == "inspect" and subcommand == "validate" and self.phase != CadPhase.VALIDATE_REQUIRED:
            return f"CAD workflow blocked geometry validation during {self.phase.value}."
        if entry == "snapshot" and self.phase != CadPhase.SNAPSHOT_REQUIRED:
            return "CAD workflow blocked snapshot until every planned item has passed validation."
        if entry == "export" and self.phase not in {CadPhase.SNAPSHOT_REQUIRED, CadPhase.VIEWER_REQUIRED, CadPhase.COMPLETE}:
            return "CAD workflow blocked secondary export until the primary STEP has passed validation."
        return None

    def observe(self, name: str, args: dict, result: str) -> None:
        if not _successful(result):
            if name == "execute_shell":
                entry, _subcommand = _cad_script(str(args.get("command") or ""))
                if entry in {"gen", "inspect"}:
                    self.phase = CadPhase.SOURCE_REQUIRED
                    self._persist()
            return
        if self.phase == CadPhase.PLAN_REQUIRED and name == "write_file":
            raw = Path(str(args.get("filename") or ""))
            self.plan_path = (raw if raw.is_absolute() else self.workspace / raw).resolve()
            parsed = parse_components(str(args.get("content") or ""))
            self.components = [label for label, _checked in parsed]
            self.checked = []
            self.primary_output = parse_primary_output(str(args.get("content") or ""))
            if not self.requires_generation:
                self.current_step = self.primary_output
            self.phase = (CadPhase.SOURCE_REQUIRED if self.requires_generation
                          else CadPhase.REFS_REQUIRED)
            self._persist()
            return

        if (name == "write_file" and self.phase == CadPhase.SOURCE_REQUIRED
                and str(args.get("filename") or "").casefold().endswith(".step.py")):
            raw = Path(str(args.get("filename") or ""))
            source = (raw if raw.is_absolute() else self.workspace / raw).resolve()
            try:
                relative = source.relative_to(self.workspace)
            except ValueError:
                return
            self.current_source = str(relative).replace("\\", "/")
            self.current_step = self.current_source[:-3]
            self.phase = CadPhase.GEN_REQUIRED
            self._persist()
            return

        if name == "execute_shell":
            command = str(args.get("command") or "")
            entry, subcommand = _cad_script(command)
            if entry == "gen" and self.phase == CadPhase.GEN_REQUIRED:
                self.last_generated_command = command
                self.phase = CadPhase.REFS_REQUIRED
            elif entry == "inspect" and subcommand == "refs" and self.phase == CadPhase.REFS_REQUIRED:
                self.phase = CadPhase.VALIDATE_REQUIRED
            elif entry == "inspect" and subcommand == "validate" and self.phase == CadPhase.VALIDATE_REQUIRED:
                self.last_validated_command = command
                self.phase = CadPhase.PLAN_UPDATE_REQUIRED
            elif entry == "snapshot" and self.phase == CadPhase.SNAPSHOT_REQUIRED:
                self.phase = CadPhase.VIEWER_REQUIRED
            self._persist()
            return

        if (name == "write_file" and self.phase == CadPhase.PLAN_UPDATE_REQUIRED
                and self.plan_path is not None
                and Path(str(args.get("filename") or "")).name == self.plan_path.name):
            parsed = parse_components(str(args.get("content") or ""))
            labels = [label for label, _checked in parsed]
            newly_checked = [label for label, checked in parsed
                             if checked and label not in self.checked]
            if labels == self.components and len(newly_checked) == 1:
                self.checked.append(newly_checked[0])
                self.phase = (CadPhase.SNAPSHOT_REQUIRED
                              if len(self.checked) == len(self.components)
                              else CadPhase.SOURCE_REQUIRED)
                self._persist()
            return

        if name == "open_cad_viewer" and self.phase == CadPhase.VIEWER_REQUIRED:
            self.phase = CadPhase.COMPLETE
            self._persist()
