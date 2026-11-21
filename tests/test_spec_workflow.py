from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dual_agent_task.config import HarnessConfig
from dual_agent_task.project import initialize_project
from dual_agent_task.spec_workflow import run_spec_workflow
from dual_agent_task.task_file import read_status


class SpecWorkflowTests(unittest.TestCase):
    def test_required_stage_mode_instructs_both_agents_and_creates_staged_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root)
            config = HarnessConfig.load(root)
            prompts = []

            def author(**arguments: object) -> dict[str, object]:
                prompts.append(str(arguments["prompt"]))
                return {
                    "slug": "staged-feature",
                    "title": "Staged feature",
                    "task_markdown": _staged_task_markdown(),
                    "open_questions": [],
                }

            def reviewer(**arguments: object) -> dict[str, object]:
                prompts.append(str(arguments["prompt"]))
                return {
                    "verdict": "approved",
                    "summary": "Ready.",
                    "blocking": [],
                    "suggestions": [],
                }

            with patch(
                "dual_agent_task.spec_workflow.run_claude_structured",
                side_effect=author,
            ), patch(
                "dual_agent_task.spec_workflow.run_codex_structured",
                side_effect=reviewer,
            ):
                path = run_spec_workflow(
                    config=config,
                    feature_description="Build this in stages.",
                    stage_mode="required",
                    report=lambda _: None,
                )

            self.assertTrue(all("Stage mode: `required`" in prompt for prompt in prompts))
            self.assertIn("stage_id: domain-model", path.read_text(encoding="utf-8"))

    def test_approved_review_creates_task_ready_for_human(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root)
            config = HarnessConfig.load(root)

            with patch(
                "dual_agent_task.spec_workflow.run_claude_structured",
                return_value={
                    "slug": "camera-switching",
                    "title": "Camera switching",
                    "task_markdown": "## Goal\n\nSwitch cameras safely.",
                    "open_questions": [],
                },
            ), patch(
                "dual_agent_task.spec_workflow.run_codex_structured",
                return_value={
                    "verdict": "approved",
                    "summary": "Ready.",
                    "blocking": [],
                    "suggestions": [],
                },
            ):
                path = run_spec_workflow(
                    config=config,
                    feature_description="Add camera switching.",
                    report=lambda _: None,
                )

            self.assertEqual(
                path,
                root.resolve() / "tasks" / "camera-switching.md",
            )
            self.assertEqual(read_status(path), "ready_for_human")

    def test_open_questions_require_human_input_even_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root)
            config = HarnessConfig.load(root)

            with patch(
                "dual_agent_task.spec_workflow.run_claude_structured",
                return_value={
                    "slug": "camera-switching",
                    "title": "Camera switching",
                    "task_markdown": "## Open questions\n\nWhich camera is the fallback?",
                    "open_questions": ["Which camera is the fallback?"],
                },
            ), patch(
                "dual_agent_task.spec_workflow.run_codex_structured",
                return_value={
                    "verdict": "approved",
                    "summary": "No specification defects.",
                    "blocking": [],
                    "suggestions": [],
                },
            ):
                path = run_spec_workflow(
                    config=config,
                    feature_description="Add camera switching.",
                    report=lambda _: None,
                )

            self.assertEqual(read_status(path), "needs_user_input")


if __name__ == "__main__":
    unittest.main()


def _staged_task_markdown() -> str:
    return """## Goal

Build a feature.

## Implementation stages

### Stage 1: Domain model

stage_id: domain-model
depends_on: []

#### Goal

Add the model.

#### Scope

- Model files.

#### Acceptance criteria

- Model compiles.

#### Test plan

- Run model tests.

### Stage 2: User interface

stage_id: user-interface
depends_on: [domain-model]

#### Goal

Add the UI.

#### Scope

- UI files.

#### Acceptance criteria

- UI works.

#### Test plan

- Run UI tests.
"""

