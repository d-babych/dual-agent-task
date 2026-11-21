from pathlib import Path
import subprocess
import tempfile
import unittest

from dual_agent_task.errors import HarnessError
from dual_agent_task.project import initialize_project
from dual_agent_task.stages import (
    complete_stage,
    parse_task_stages,
    record_stage_outcome,
    select_stage,
    stage_status_payload,
    stage_statuses,
    validate_stage_mode,
)
from dual_agent_task.task_file import render_task


STAGED_MARKDOWN = """## Problem

Deliver a feature in reviewable parts.

## Implementation stages

### Stage 1: Domain model

stage_id: domain-model
depends_on: []

#### Goal

Add the domain model.

#### Scope

- Domain types.

#### Acceptance criteria

- Domain types compile.

#### Test plan

- Run domain tests.

### Stage 2: User interface

stage_id: user-interface
depends_on: [domain-model]

#### Goal

Expose the model in UI.

#### Scope

- UI controls.

#### Acceptance criteria

- Users can select the new option.

#### Test plan

- Run UI tests.
"""


class StageTests(unittest.TestCase):
    def test_parses_ordered_stages_and_dependencies(self) -> None:
        stages = parse_task_stages(STAGED_MARKDOWN)

        self.assertEqual([stage.stage_id for stage in stages], ["domain-model", "user-interface"])
        self.assertEqual(stages[1].depends_on, ("domain-model",))
        self.assertEqual(validate_stage_mode(STAGED_MARKDOWN, "required"), stages)

    def test_rejects_unknown_or_forward_dependencies(self) -> None:
        invalid = STAGED_MARKDOWN.replace(
            "depends_on: [domain-model]", "depends_on: [missing-stage]"
        )

        with self.assertRaisesRegex(HarnessError, "unknown stage"):
            parse_task_stages(invalid)

    def test_required_and_disabled_stage_modes_are_enforced(self) -> None:
        with self.assertRaisesRegex(HarnessError, "at least two"):
            validate_stage_mode("## Goal\n\nAtomic.", "required")
        with self.assertRaisesRegex(HarnessError, "forbids"):
            validate_stage_mode(STAGED_MARKDOWN, "disabled")

    def test_stage_requires_review_commit_and_completed_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _initialize_git_repository(root)
            initialize_project(root)
            task_path = root / "tasks" / "feature.md"
            task_path.write_text(
                render_task(
                    slug="feature",
                    status="approved",
                    title="Feature",
                    markdown=STAGED_MARKDOWN,
                ),
                encoding="utf-8",
            )
            _git(root, "add", ".")
            _git(root, "commit", "-m", "Add staged task")
            base_head = _git(root, "rev-parse", "HEAD")
            stages = parse_task_stages(task_path.read_text(encoding="utf-8"))
            state_directory = root / ".dual-agent-task" / "state"

            selected = select_stage(
                state_directory=state_directory,
                task_path=task_path,
                stages=stages,
                selector="next",
            )
            self.assertEqual(selected.stage_id, "domain-model")

            (root / "domain.txt").write_text("implemented\n", encoding="utf-8")
            record_stage_outcome(
                state_directory=state_directory,
                task_path=task_path,
                stages=stages,
                stage=stages[0],
                outcome_status="ready_for_human_review",
                run_directory=root / ".dual-agent-task" / "runs" / "test",
                base_head=base_head,
            )
            with self.assertRaisesRegex(HarnessError, "Complete the reviewed stage"):
                select_stage(
                    state_directory=state_directory,
                    task_path=task_path,
                    stages=stages,
                    selector="next",
                )

            _git(root, "add", "domain.txt")
            _git(root, "commit", "-m", "Complete domain stage")
            completed = complete_stage(
                project_root=root,
                state_directory=state_directory,
                task_path=task_path,
                stages=stages,
                selector="domain-model",
            )
            self.assertEqual(completed.stage_id, "domain-model")

            next_stage = select_stage(
                state_directory=state_directory,
                task_path=task_path,
                stages=stages,
                selector="next",
            )
            self.assertEqual(next_stage.stage_id, "user-interface")
            payload = stage_status_payload(
                stage_statuses(
                    state_directory=state_directory,
                    task_path=task_path,
                    stages=stages,
                )
            )
            self.assertEqual(payload[0]["status"], "completed")
            self.assertEqual(payload[1]["status"], "ready")


def _initialize_git_repository(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "Harness Tests")


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", *arguments),
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


if __name__ == "__main__":
    unittest.main()
