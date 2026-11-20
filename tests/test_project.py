from pathlib import Path
import tempfile
import unittest

from dual_agent_task.config import HarnessConfig
from dual_agent_task.project import initialize_project


class InitializeProjectTests(unittest.TestCase):
    def test_init_creates_expected_structure_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            first_write = initialize_project(root)
            second_write = initialize_project(root)

            self.assertTrue(first_write)
            self.assertEqual(second_write, [])
            self.assertTrue((root / ".dual-agent-task" / "config.toml").is_file())
            self.assertTrue(
                (
                    root
                    / ".dual-agent-task"
                    / "schemas"
                    / "implementation-result.schema.json"
                ).is_file()
            )
            self.assertTrue(
                (
                    root
                    / ".dual-agent-task"
                    / "schemas"
                    / "code-review.schema.json"
                ).is_file()
            )
            self.assertTrue((root / "PROJECT_RULES.md").is_file())
            self.assertTrue((root / "tasks").is_dir())
            self.assertIn(
                ".dual-agent-task/runs/",
                (root / ".gitignore").read_text(encoding="utf-8").splitlines(),
            )

            config = HarnessConfig.load(root)
            self.assertEqual(config.agents.spec_author, "claude")
            self.assertEqual(config.agents.spec_reviewer, "codex")
            self.assertEqual(config.limits.max_spec_rounds, 3)


if __name__ == "__main__":
    unittest.main()

