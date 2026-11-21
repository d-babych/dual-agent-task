import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from dual_agent_task.process_runner import run_streaming_process
from dual_agent_task.progress import (
    ConsoleProgress,
    format_duration,
    summarize_claude_event,
    summarize_codex_event,
)


class ProgressTests(unittest.TestCase):
    def test_live_progress_prints_timestamped_stage_activity_and_result(self) -> None:
        stream = io.StringIO()
        progress = ConsoleProgress("live", stream=stream)

        progress.start("[1/3] Codex implementing · model/medium")
        progress.activity("Editing Sport.swift")
        progress.heartbeat(83)
        progress.finish("Codex finished · 01:23 · 2 files changed")

        output = stream.getvalue()
        self.assertIn("[1/3] Codex implementing", output)
        self.assertIn("Editing Sport.swift", output)
        self.assertIn("Still working… 01:23", output)
        self.assertIn("✓", output)

    def test_quiet_progress_hides_intermediate_output(self) -> None:
        stream = io.StringIO()
        progress = ConsoleProgress("quiet", stream=stream)

        progress.start("Starting")
        progress.activity("Reading")
        progress.finish("Finished")

        self.assertEqual(stream.getvalue(), "")

    def test_summarizes_agent_tool_events(self) -> None:
        codex = summarize_codex_event(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "file_change",
                        "changes": [{"path": "Sport.swift"}],
                    },
                }
            )
        )
        claude = summarize_claude_event(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Read",
                                "input": {"file_path": "Sport.swift"},
                            }
                        ]
                    },
                }
            )
        )

        self.assertEqual(codex, "Editing Sport.swift")
        self.assertEqual(claude, ["Read Sport.swift"])

    def test_streaming_process_captures_both_streams(self) -> None:
        lines = []
        with tempfile.TemporaryDirectory() as directory:
            result = run_streaming_process(
                (
                    sys.executable,
                    "-c",
                    "import sys; print('out'); print('err', file=sys.stderr)",
                ),
                cwd=Path(directory),
                timeout_seconds=5,
                on_line=lambda source, line: lines.append((source, line)),
            )

        self.assertEqual(result.return_code, 0)
        self.assertIn(("stdout", "out"), lines)
        self.assertIn(("stderr", "err"), lines)
        self.assertEqual(format_duration(83), "01:23")


if __name__ == "__main__":
    unittest.main()
