import json
import unittest

from dual_agent_task.agents import _extract_claude_structured_output


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


if __name__ == "__main__":
    unittest.main()

