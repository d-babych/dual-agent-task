from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from dual_agent_task.config import HarnessConfig
from dual_agent_task.implementation_workflow import run_implementation_workflow
from dual_agent_task.project import initialize_project
from dual_agent_task.task_file import render_task


class ImplementationWorkflowTests(unittest.TestCase):
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

