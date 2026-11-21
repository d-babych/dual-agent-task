from pathlib import Path
import tempfile
import unittest

from dual_agent_task.config import HarnessConfig
from dual_agent_task.project import (
    _summarize_command_output,
    initialize_project,
    upgrade_project,
)


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
            self.assertIn(
                ".dual-agent-task/state/",
                (root / ".gitignore").read_text(encoding="utf-8").splitlines(),
            )
            self.assertTrue((root / ".dual-agent-task" / "state").is_dir())

            config = HarnessConfig.load(root)
            self.assertEqual(config.agents.spec_author, "claude")
            self.assertEqual(config.agents.spec_reviewer, "codex")
            self.assertEqual(config.models.spec_author, "sonnet")
            self.assertEqual(config.models.spec_reviewer, "gpt-5.6-sol")
            self.assertEqual(config.models.implementer, "gpt-5.6-sol")
            self.assertEqual(config.models.code_reviewer, "sonnet")
            self.assertEqual(config.effort.spec_author, "high")
            self.assertEqual(config.effort.spec_reviewer, "high")
            self.assertEqual(config.effort.implementer, "medium")
            self.assertEqual(config.effort.code_reviewer, "high")
            self.assertEqual(config.limits.max_spec_rounds, 3)

    def test_upgrade_adds_runtime_sections_without_replacing_project_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root)
            config_path = root / ".dual-agent-task" / "config.toml"
            content = config_path.read_text(encoding="utf-8")
            content = content.split("[models]", maxsplit=1)[0] + "[limits]\n" + content.split(
                "[limits]", maxsplit=1
            )[1]
            config_path.write_text(content, encoding="utf-8")
            rules_path = root / "PROJECT_RULES.md"
            rules_path.write_text("custom rules\n", encoding="utf-8")

            changed = upgrade_project(root)

            upgraded = config_path.read_text(encoding="utf-8")
            self.assertIn("[models]", upgraded)
            self.assertIn("[effort]", upgraded)
            self.assertIn('spec_reviewer = "gpt-5.6-sol"', upgraded)
            self.assertEqual(rules_path.read_text(encoding="utf-8"), "custom rules\n")
            self.assertIn(config_path.resolve(), changed)

    def test_command_output_summarizes_json_auth_status(self) -> None:
        detail = _summarize_command_output(
            '{"loggedIn": false, "authMethod": "none"}',
            return_code=1,
        )

        self.assertEqual(detail, 'loggedIn=false, authMethod="none"')


if __name__ == "__main__":
    unittest.main()

