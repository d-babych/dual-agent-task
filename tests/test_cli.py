import unittest

from dual_agent_task.cli import build_parser


class CliTests(unittest.TestCase):
    def test_implement_uses_live_progress_by_default(self) -> None:
        arguments = build_parser().parse_args(("implement", "task.md"))

        self.assertEqual(arguments.progress, "live")

    def test_progress_aliases_select_expected_modes(self) -> None:
        parser = build_parser()

        self.assertEqual(
            parser.parse_args(("implement", "--quiet", "task.md")).progress,
            "quiet",
        )
        self.assertEqual(
            parser.parse_args(("plan", "--verbose")).progress,
            "verbose",
        )
        self.assertEqual(
            parser.parse_args(
                ("implement", "--progress", "plain", "task.md")
            ).progress,
            "plain",
        )

    def test_stage_planning_modes_and_implementation_selector(self) -> None:
        parser = build_parser()

        self.assertEqual(parser.parse_args(("plan",)).stages, "auto")
        self.assertEqual(
            parser.parse_args(("plan", "--stages", "required")).stages,
            "required",
        )
        self.assertEqual(parser.parse_args(("plan", "--staged")).stages, "required")
        self.assertEqual(parser.parse_args(("plan", "--no-stages")).stages, "disabled")
        self.assertEqual(
            parser.parse_args(("implement", "--stage", "next", "task.md")).stage,
            "next",
        )


if __name__ == "__main__":
    unittest.main()
