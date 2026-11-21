"""Parsing and local lifecycle state for staged implementation tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Optional

from dual_agent_task.errors import HarnessError


STAGES_HEADING = re.compile(r"(?im)^##\s+Implementation stages\s*$")
NEXT_LEVEL_TWO_HEADING = re.compile(r"(?m)^##\s+")
STAGE_HEADING = re.compile(
    r"(?im)^###\s+Stage(?:\s+\d+)?\s*[:—-]\s*(?P<title>.+?)\s*$"
)
STAGE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TASK_ID = re.compile(r"(?m)^id:\s*([a-z0-9]+(?:-[a-z0-9]+)*)\s*$")
REQUIRED_STAGE_SECTIONS = ("Goal", "Scope", "Acceptance criteria", "Test plan")
STAGE_MODES = ("auto", "required", "disabled")


@dataclass(frozen=True)
class TaskStage:
    index: int
    stage_id: str
    title: str
    depends_on: tuple[str, ...]
    markdown: str


@dataclass(frozen=True)
class StageStatus:
    stage: TaskStage
    status: str


def parse_task_stages(content: str) -> tuple[TaskStage, ...]:
    heading = STAGES_HEADING.search(content)
    if heading is None:
        return ()
    section_start = heading.end()
    next_heading = NEXT_LEVEL_TWO_HEADING.search(content, section_start)
    section_end = next_heading.start() if next_heading is not None else len(content)
    section = content[section_start:section_end]
    matches = list(STAGE_HEADING.finditer(section))
    if not matches:
        raise HarnessError(
            "`Implementation stages` exists but contains no valid `### Stage N: Title` entries"
        )

    stages: list[TaskStage] = []
    identifiers: set[str] = set()
    for offset, match in enumerate(matches):
        block_end = matches[offset + 1].start() if offset + 1 < len(matches) else len(section)
        block = section[match.start():block_end].strip()
        stage_id = _metadata_value(block, "stage_id")
        if stage_id is None or not STAGE_ID.fullmatch(stage_id):
            raise HarnessError(
                f"Stage {offset + 1} must contain a valid `stage_id: kebab-case-id`"
            )
        if stage_id in identifiers:
            raise HarnessError(f"Duplicate stage_id: {stage_id}")
        identifiers.add(stage_id)

        raw_dependencies = _metadata_value(block, "depends_on")
        if raw_dependencies is None:
            raise HarnessError(f"Stage `{stage_id}` must contain `depends_on: [...]`")
        dependencies = _parse_dependencies(raw_dependencies, stage_id)
        missing_sections = [
            name
            for name in REQUIRED_STAGE_SECTIONS
            if re.search(rf"(?im)^####\s+{re.escape(name)}\s*$", block) is None
        ]
        if missing_sections:
            raise HarnessError(
                f"Stage `{stage_id}` is missing sections: {', '.join(missing_sections)}"
            )
        stages.append(
            TaskStage(
                index=offset + 1,
                stage_id=stage_id,
                title=match.group("title").strip(),
                depends_on=dependencies,
                markdown=block,
            )
        )

    indexes = {stage.stage_id: stage.index for stage in stages}
    for stage in stages:
        for dependency in stage.depends_on:
            if dependency not in indexes:
                raise HarnessError(
                    f"Stage `{stage.stage_id}` depends on unknown stage `{dependency}`"
                )
            if indexes[dependency] >= stage.index:
                raise HarnessError(
                    f"Stage `{stage.stage_id}` dependency `{dependency}` must appear earlier"
                )
    return tuple(stages)


def validate_stage_mode(content: str, mode: str) -> tuple[TaskStage, ...]:
    if mode not in STAGE_MODES:
        raise HarnessError(f"Unsupported stage mode: {mode}")
    stages = parse_task_stages(content)
    if mode == "required" and len(stages) < 2:
        raise HarnessError("Stage mode `required` requires at least two implementation stages")
    if mode == "disabled" and STAGES_HEADING.search(content) is not None:
        raise HarnessError("Stage mode `disabled` forbids an `Implementation stages` section")
    return stages


def stage_planning_instructions(mode: str) -> str:
    behavior = {
        "auto": (
            "Decide whether stages materially improve delivery. Use stages for work with "
            "multiple independently reviewable parts, meaningful dependencies, or useful "
            "human checkpoints. Otherwise keep the task atomic."
        ),
        "required": "The task MUST contain at least two implementation stages.",
        "disabled": "The task MUST remain atomic and MUST NOT contain implementation stages.",
    }[mode]
    return f"""
## Harness stage policy

Stage mode: `{mode}`.

{behavior}

When stages are used, include exactly this machine-readable Markdown structure:

```markdown
## Implementation stages

### Stage 1: Short title

stage_id: kebab-case-id
depends_on: []

#### Goal

Stage-specific goal.

#### Scope

- Files or behavior belonging to this stage.

#### Acceptance criteria

- Observable stage-specific result.

#### Test plan

- Focused checks for this stage.
```

Every later stage must use `depends_on: [earlier-stage-id]` where appropriate.
Dependencies may reference only earlier stages. Do not put requirements exclusively
belonging to a later stage into an earlier stage.
""".strip()


def task_id(path: Path) -> str:
    match = TASK_ID.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise HarnessError(f"Task file has no valid id field: {path}")
    return match.group(1)


def state_path(state_directory: Path, task_path: Path) -> Path:
    return state_directory / f"{task_id(task_path)}.json"


def stage_statuses(
    *, state_directory: Path, task_path: Path, stages: tuple[TaskStage, ...]
) -> tuple[StageStatus, ...]:
    state = _load_state(state_directory, task_path, stages, create=False)
    records = state.get("stages", {})
    return tuple(
        StageStatus(
            stage=stage,
            status=str(records.get(stage.stage_id, {}).get("status", "pending")),
        )
        for stage in stages
    )


def select_stage(
    *,
    state_directory: Path,
    task_path: Path,
    stages: tuple[TaskStage, ...],
    selector: Optional[str],
) -> Optional[TaskStage]:
    if not stages:
        if selector is not None:
            raise HarnessError("This task has no implementation stages")
        return None
    if selector is None:
        if len(stages) == 1:
            selected = stages[0]
        else:
            available = _available_stage_ids(state_directory, task_path, stages)
            detail = ", ".join(available) or "none"
            raise HarnessError(
                "This task has multiple implementation stages. Use `--stage <id|number|next>`. "
                f"Available: {detail}"
            )
    elif selector == "next":
        state = _load_state(state_directory, task_path, stages, create=False)
        records = state.get("stages", {})
        awaiting = [
            stage.stage_id
            for stage in stages
            if records.get(stage.stage_id, {}).get("status") == "ready_for_human_review"
        ]
        if awaiting:
            raise HarnessError(
                "Complete the reviewed stage before continuing: " + ", ".join(awaiting)
            )
        available = _available_stages(state, stages)
        if not available:
            if all(
                records.get(stage.stage_id, {}).get("status") == "completed"
                for stage in stages
            ):
                raise HarnessError("All implementation stages are completed")
            raise HarnessError("No implementation stage is currently available")
        selected = available[0]
    elif selector.isdigit():
        number = int(selector)
        selected = next((stage for stage in stages if stage.index == number), None)
        if selected is None:
            raise HarnessError(f"Task has no stage number {number}")
    else:
        selected = next((stage for stage in stages if stage.stage_id == selector), None)
        if selected is None:
            raise HarnessError(f"Task has no stage `{selector}`")

    state = _load_state(state_directory, task_path, stages, create=False)
    records = state.get("stages", {})
    record = records.get(selected.stage_id, {})
    status = record.get("status", "pending")
    if status == "completed":
        raise HarnessError(f"Stage `{selected.stage_id}` is already completed")
    if status == "ready_for_human_review":
        raise HarnessError(
            f"Stage `{selected.stage_id}` is ready for human review; commit it and run "
            "`task complete-stage` before continuing"
        )
    incomplete = [
        dependency
        for dependency in selected.depends_on
        if records.get(dependency, {}).get("status") != "completed"
    ]
    if incomplete:
        raise HarnessError(
            f"Stage `{selected.stage_id}` is blocked by: {', '.join(incomplete)}"
        )
    return selected


def completed_stage_summary(
    *, state_directory: Path, task_path: Path, stages: tuple[TaskStage, ...]
) -> str:
    state = _load_state(state_directory, task_path, stages, create=False)
    records = state.get("stages", {})
    completed = [
        f"- {stage.stage_id}: {stage.title}"
        for stage in stages
        if records.get(stage.stage_id, {}).get("status") == "completed"
    ]
    return "\n".join(completed) if completed else "No stages have been completed yet."


def record_stage_outcome(
    *,
    state_directory: Path,
    task_path: Path,
    stages: tuple[TaskStage, ...],
    stage: TaskStage,
    outcome_status: str,
    run_directory: Path,
    base_head: str,
) -> None:
    state = _load_state(state_directory, task_path, stages, create=True)
    status = (
        "ready_for_human_review"
        if outcome_status == "ready_for_human_review"
        else outcome_status
    )
    state["stages"][stage.stage_id] = {
        "status": status,
        "reviewed_run": str(run_directory),
        "base_head": base_head,
        "updated_at": _now(),
    }
    _write_state(state_directory, task_path, state)


def complete_stage(
    *,
    project_root: Path,
    state_directory: Path,
    task_path: Path,
    stages: tuple[TaskStage, ...],
    selector: str,
) -> TaskStage:
    if not stages:
        raise HarnessError("This task has no implementation stages")
    selected = _stage_by_selector(stages, selector)
    state = _load_state(state_directory, task_path, stages, create=False)
    record = state.get("stages", {}).get(selected.stage_id, {})
    if record.get("status") != "ready_for_human_review":
        raise HarnessError(
            f"Stage `{selected.stage_id}` is not ready for completion; current status is "
            f"{record.get('status', 'pending')}"
        )
    _require_clean_worktree(project_root, task_path)
    current_head = _git(project_root, "rev-parse", "HEAD").strip()
    base_head = str(record.get("base_head", ""))
    if not base_head or current_head == base_head:
        raise HarnessError(
            "Commit the reviewed stage changes before marking the stage completed"
        )
    record.update(
        {
            "status": "completed",
            "completed_head": current_head,
            "completed_at": _now(),
        }
    )
    state["stages"][selected.stage_id] = record
    _write_state(state_directory, task_path, state)
    return selected


def stage_status_payload(statuses: tuple[StageStatus, ...]) -> list[dict[str, Any]]:
    completed = {
        item.stage.stage_id for item in statuses if item.status == "completed"
    }
    payload: list[dict[str, Any]] = []
    for item in statuses:
        blocked_by = [
            dependency
            for dependency in item.stage.depends_on
            if dependency not in completed
        ]
        effective_status = item.status
        if effective_status == "pending" and blocked_by:
            effective_status = "blocked"
        elif effective_status == "pending":
            effective_status = "ready"
        payload.append(
            {
                "number": item.stage.index,
                "id": item.stage.stage_id,
                "title": item.stage.title,
                "depends_on": list(item.stage.depends_on),
                "blocked_by": blocked_by,
                "status": effective_status,
            }
        )
    return payload


def _available_stage_ids(
    state_directory: Path, task_path: Path, stages: tuple[TaskStage, ...]
) -> list[str]:
    state = _load_state(state_directory, task_path, stages, create=False)
    return [stage.stage_id for stage in _available_stages(state, stages)]


def _available_stages(
    state: dict[str, Any], stages: tuple[TaskStage, ...]
) -> list[TaskStage]:
    records = state.get("stages", {})
    available: list[TaskStage] = []
    for stage in stages:
        status = records.get(stage.stage_id, {}).get("status", "pending")
        if status not in {"pending", "changes_requested", "review_incomplete"}:
            continue
        if all(
            records.get(dependency, {}).get("status") == "completed"
            for dependency in stage.depends_on
        ):
            available.append(stage)
    return available


def _stage_by_selector(stages: tuple[TaskStage, ...], selector: str) -> TaskStage:
    if selector.isdigit():
        stage = next((item for item in stages if item.index == int(selector)), None)
    else:
        stage = next((item for item in stages if item.stage_id == selector), None)
    if stage is None:
        raise HarnessError(f"Task has no stage `{selector}`")
    return stage


def _load_state(
    state_directory: Path,
    task_path: Path,
    stages: tuple[TaskStage, ...],
    *,
    create: bool,
) -> dict[str, Any]:
    path = state_path(state_directory, task_path)
    fingerprint = _task_hash(task_path)
    if not path.is_file():
        return {
            "version": 1,
            "task_id": task_id(task_path),
            "task_path": str(task_path),
            "task_hash": fingerprint,
            "stages": {
                stage.stage_id: {"status": "pending"} for stage in stages
            },
        }
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HarnessError(f"Cannot read stage state {path}: {error}") from error
    if not isinstance(value, dict) or not isinstance(value.get("stages"), dict):
        raise HarnessError(f"Invalid stage state: {path}")
    if value.get("task_hash") != fingerprint:
        raise HarnessError(
            f"Approved task changed after staged execution began. Inspect or remove {path} "
            "before starting over."
        )
    if create:
        for stage in stages:
            value["stages"].setdefault(stage.stage_id, {"status": "pending"})
    return value


def _write_state(
    state_directory: Path, task_path: Path, state: dict[str, Any]
) -> None:
    state_directory.mkdir(parents=True, exist_ok=True)
    path = state_path(state_directory, task_path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _metadata_value(block: str, key: str) -> Optional[str]:
    match = re.search(rf"(?im)^{re.escape(key)}:\s*(.+?)\s*$", block)
    return match.group(1).strip().strip("`") if match is not None else None


def _parse_dependencies(raw: str, stage_id: str) -> tuple[str, ...]:
    text = raw.strip()
    if not text.startswith("[") or not text.endswith("]"):
        raise HarnessError(
            f"Stage `{stage_id}` depends_on must use `[stage-id, other-stage]` syntax"
        )
    inner = text[1:-1].strip()
    if not inner:
        return ()
    values = tuple(item.strip().strip("`\"'") for item in inner.split(","))
    if any(not STAGE_ID.fullmatch(item) for item in values):
        raise HarnessError(f"Stage `{stage_id}` has an invalid depends_on value")
    if len(values) != len(set(values)):
        raise HarnessError(f"Stage `{stage_id}` has duplicate dependencies")
    return values


def _task_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_clean_worktree(project_root: Path, task_path: Path) -> None:
    output = _git(project_root, "status", "--porcelain", "--untracked-files=all")
    try:
        allowed_task = str(task_path.relative_to(project_root))
    except ValueError:
        allowed_task = None
    disallowed = [
        line
        for line in output.splitlines()
        if not line[3:].strip().startswith(".dual-agent-task/state/")
        and (allowed_task is None or line[3:].strip() != allowed_task)
    ]
    if disallowed:
        raise HarnessError(
            "Working tree must be clean before completing a stage:\n"
            + "\n".join(disallowed)
        )


def _git(project_root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ("git", *arguments),
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise HarnessError(f"Git command failed: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise HarnessError(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout
