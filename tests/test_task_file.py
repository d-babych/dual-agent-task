from pathlib import Path
import tempfile
import unittest

from dual_agent_task.errors import HarnessError
from dual_agent_task.task_file import approve_task, read_status, render_task


class TaskFileTests(unittest.TestCase):
    def test_approve_changes_ready_task_to_approved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task.md"
            path.write_text(
                render_task(
                    slug="camera-switching",
                    status="ready_for_human",
                    title="Camera switching",
                    markdown="## Goal\n\nSwitch cameras.",
                ),
                encoding="utf-8",
            )

            approve_task(path)

            self.assertEqual(read_status(path), "approved")

    def test_approve_rejects_incomplete_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task.md"
            path.write_text(
                render_task(
                    slug="camera-switching",
                    status="review_incomplete",
                    title="Camera switching",
                    markdown="## Goal\n\nSwitch cameras.",
                ),
                encoding="utf-8",
            )

            with self.assertRaises(HarnessError):
                approve_task(path)


if __name__ == "__main__":
    unittest.main()

