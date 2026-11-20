import json
import unittest

from dual_agent_task.agents import (
    _claude_runtime_arguments,
    _codex_runtime_arguments,
    _extract_claude_structured_output,
)


class ClaudeOutputTests(unittest.TestCase):
    def test_extracts_structured_output(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "structured_output": {"slug": "camera-switching"},
            }
        )

        value = _extract_claude_structured_output(stdout)

        self.assertEqual(value, {"slug": "camera-switching"})

    def test_extracts_json_encoded_result(self) -> None:
        stdout = json.dumps({"result": json.dumps({"verdict": "approved"})})

        value = _extract_claude_structured_output(stdout)

        self.assertEqual(value, {"verdict": "approved"})

    def test_builds_claude_model_and_effort_arguments(self) -> None:
        self.assertEqual(
            _claude_runtime_arguments(model="sonnet", effort="high"),
            ("--model", "sonnet", "--effort", "high"),
        )

    def test_builds_codex_model_and_effort_arguments(self) -> None:
        self.assertEqual(
            _codex_runtime_arguments(model="gpt-5.6-sol", effort="medium"),
            (
                "--model",
                "gpt-5.6-sol",
                "--config",
                'model_reasoning_effort="medium"',
            ),
        )


if __name__ == "__main__":
    unittest.main()

