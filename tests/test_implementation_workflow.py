from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from dual_agent_task.config import HarnessConfig
from dual_agent_task.errors import HarnessError
from dual_agent_task.implementation_workflow import (
    recover_implementation_review,
    run_implementation_workflow,
)
from dual_agent_task.project import initialize_project
from dual_agent_task.task_file import render_task


class ImplementationWorkflowTests(unittest.TestCase):
    def test_staged_task_runs_only_selected_stage_and_records_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _initialize_git_repository(root)
            initialize_project(root)
            task_path = root / "tasks" / "staged-feature.md"
            task_path.write_text(
                render_task(
                    slug="staged-feature",
                    status="approved",
                    title="Staged feature",
                    markdown=_staged_markdown(),
                ),
                encoding="utf-8",
            )
            _git(root, "add", ".")
            _git(root, "commit", "-m", "Add staged task")
            config = HarnessConfig.load(root)
            prompts = []

            def implement(**arguments: object) -> dict[str, object]:
                prompts.append(str(arguments["prompt"]))
                (root / "domain.txt").write_text("implemented\n", encoding="utf-8")
                return {"summary": "Done.", "tests_run": [], "remaining_risks": []}

            def review(**arguments: object) -> dict[str, object]:
                prompts.append(str(arguments["prompt"]))
                return {
                    "verdict": "approved",
                    "summary": "Stage is ready.",
                    "findings": [],
                    "verification_assessment": "Passed.",
                }

            with patch(
                "dual_agent_task.implementation_workflow.run_codex_write_structured",
                side_effect=implement,
            ), patch(
                "dual_agent_task.implementation_workflow.run_claude_structured",
                side_effect=review,
            ):
                outcome = run_implementation_workflow(
                    config=config,
                    task_path=task_path,
                    stage_selector="domain-model",
                    report=lambda _: None,
                )

            self.assertEqual(outcome.status, "ready_for_human_review")
            self.assertEqual(outcome.stage_id, "domain-model")
            self.assertTrue(all("exactly one stage: `domain-model`" in prompt for prompt in prompts))
            result = json.loads(
                (outcome.run_directory / "result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(result["stage_id"], "domain-model")
            state = json.loads(
                (config.state_directory / "staged-feature.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                state["stages"]["domain-model"]["status"],
                "ready_for_human_review",
            )

            with self.assertRaisesRegex(HarnessError, "blocked by"):
                run_implementation_workflow(
                    config=config,
                    task_path=task_path,
                    stage_selector="user-interface",
                    report=lambda _: None,
                )

    def test_approved_task_runs_implementation_and_review_separately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _initialize_git_repository(root)
            initialize_project(root)
            _git(root, "add", ".")
            _git(root, "commit", "-m", "Initialize harness")

            task_path = root / "tasks" / "camera-switching.md"
            task_path.write_text(
                render_task(
                    slug="camera-switching",
                    status="approved",
                    title="Camera switching",
                    markdown="## Goal\n\nSwitch cameras safely.",
                ),
                encoding="utf-8",
            )
            config = HarnessConfig.load(root)

            def implement(**_: object) -> dict[str, object]:
                (root / "feature.txt").write_text("implemented\n", encoding="utf-8")
                return {
                    "summary": "Implemented the feature.",
                    "tests_run": [],
                    "remaining_risks": [],
                }

            with patch(
                "dual_agent_task.implementation_workflow.run_codex_write_structured",
                side_effect=implement,
            ), patch(
                "dual_agent_task.implementation_workflow.run_claude_structured",
                return_value={
                    "verdict": "approved",
                    "summary": "Implementation matches the task.",
                    "findings": [],
                    "verification_assessment": "All configured checks passed.",
                },
            ):
                outcome = run_implementation_workflow(
                    config=config,
                    task_path=task_path,
                    report=lambda _: None,
                )

            self.assertEqual(outcome.status, "ready_for_human_review")
            self.assertEqual(outcome.rounds, 1)
            self.assertTrue((root / "feature.txt").is_file())
            self.assertEqual(_git(root, "log", "-1", "--format=%s"), "Initialize harness")

    def test_resume_review_keeps_existing_changes_and_skips_first_codex_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _initialize_git_repository(root)
            initialize_project(root)
            (root / "feature.txt").write_text("before\n", encoding="utf-8")
            _git(root, "add", ".")
            _git(root, "commit", "-m", "Initial state")
            (root / "feature.txt").write_text("after\n", encoding="utf-8")

            task_path = root / "tasks" / "feature.md"
            task_path.write_text(
                render_task(
                    slug="feature",
                    status="approved",
                    title="Feature",
                    markdown="## Goal\n\nUpdate the feature.",
                ),
                encoding="utf-8",
            )
            config = HarnessConfig.load(root)

            with patch(
                "dual_agent_task.implementation_workflow.run_codex_write_structured"
            ) as codex, patch(
                "dual_agent_task.implementation_workflow.run_claude_structured",
                return_value={
                    "verdict": "approved",
                    "summary": "Existing implementation is ready.",
                    "findings": [],
                    "verification_assessment": "All configured checks passed.",
                },
            ):
                outcome = run_implementation_workflow(
                    config=config,
                    task_path=task_path,
                    report=lambda _: None,
                    resume_review=True,
                )

            codex.assert_not_called()
            self.assertEqual(outcome.status, "ready_for_human_review")
            self.assertEqual(
                (root / "feature.txt").read_text(encoding="utf-8"),
                "after\n",
            )

    def test_recovers_approved_prose_review_without_calling_agents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _initialize_git_repository(root)
            initialize_project(root)
            (root / "feature.txt").write_text("before\n", encoding="utf-8")
            _git(root, "add", ".")
            _git(root, "commit", "-m", "Initial state")
            (root / "feature.txt").write_text("after\n", encoding="utf-8")
            config = HarnessConfig.load(root)

            run_directory = root / ".dual-agent-task" / "runs" / "test-implement"
            run_directory.mkdir(parents=True)
            diff = subprocess.run(
                ("git", "diff", "--no-ext-diff", "--binary", "HEAD", "--"),
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            (run_directory / "changes-01.diff").write_text(diff, encoding="utf-8")
            (run_directory / "verification-01-01.log").write_text(
                "$ git diff --check\nexit: 0\n",
                encoding="utf-8",
            )
            (run_directory / "claude-review-01.raw.json").write_text(
                '{"type":"result","result":"**Verdict: `approved`**\\n\\nBuild passed. No blocking issues."}',
                encoding="utf-8",
            )

            outcome = recover_implementation_review(
                config=config,
                run_directory=run_directory,
            )

            self.assertEqual(outcome.status, "ready_for_human_review")
            self.assertTrue((run_directory / "code-review-01.json").is_file())
            self.assertTrue((run_directory / "result.json").is_file())


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


def _staged_markdown() -> str:
    return """## Goal

Deliver a staged feature.

## Implementation stages

### Stage 1: Domain model

stage_id: domain-model
depends_on: []

#### Goal

Add domain behavior.

#### Scope

- Domain files.

#### Acceptance criteria

- Domain behavior works.

#### Test plan

- Run domain tests.

### Stage 2: User interface

stage_id: user-interface
depends_on: [domain-model]

#### Goal

Add UI behavior.

#### Scope

- UI files.

#### Acceptance criteria

- UI behavior works.

#### Test plan

- Run UI tests.
"""


if __name__ == "__main__":
    unittest.main()

