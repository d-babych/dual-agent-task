import json
import unittest

from dual_agent_task.agents import (
    _claude_runtime_arguments,
    _codex_permission_arguments,
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

    def test_extracts_required_review_from_nested_envelope(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "result": {
                    "response": {
                        "verdict": "approved",
                        "summary": "Ready.",
                        "findings": [],
                        "verification_assessment": "Build passed.",
                    }
                },
            }
        )

        value = _extract_claude_structured_output(
            stdout,
            required_keys=(
                "verdict",
                "summary",
                "findings",
                "verification_assessment",
            ),
        )

        self.assertEqual(value["verdict"], "approved")

    def test_extracts_required_review_from_fenced_result(self) -> None:
        review = {
            "verdict": "changes_requested",
            "summary": "Fix one issue.",
            "findings": [],
            "verification_assessment": "Build passed.",
        }
        stdout = json.dumps(
            {"type": "result", "result": f"```json\n{json.dumps(review)}\n```"}
        )

        value = _extract_claude_structured_output(
            stdout,
            required_keys=tuple(review.keys()),
        )

        self.assertEqual(value, review)

    def test_converts_prose_approved_review_to_structured_review(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "**Verdict: `approved`**\n\n## Review findings\n\nBuild passed. No blocking issues found.",
            }
        )

        value = _extract_claude_structured_output(
            stdout,
            required_keys=(
                "verdict",
                "summary",
                "findings",
                "verification_assessment",
            ),
        )

        self.assertEqual(value["verdict"], "approved")
        self.assertEqual(value["findings"], [])
        self.assertIn("Build passed", value["summary"])

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

    def test_codex_implementer_does_not_combine_sandbox_and_approval(self) -> None:
        self.assertEqual(
            _codex_permission_arguments(
                sandbox="workspace-write",
                approve_for_me=True,
            ),
            ("--approve-for-me",),
        )

    def test_codex_reviewer_uses_read_only_sandbox(self) -> None:
        self.assertEqual(
            _codex_permission_arguments(
                sandbox="read-only",
                approve_for_me=False,
            ),
            ("--sandbox", "read-only"),
        )


if __name__ == "__main__":
    unittest.main()

