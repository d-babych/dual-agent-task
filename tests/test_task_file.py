from pathlib import Path
import tempfile
import unittest

from dual_agent_task.errors import HarnessError
from dual_agent_task.task_file import (
    adopt_approved_task,
    approve_task,
    read_status,
    render_task,
)


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

    def test_adopt_creates_approved_managed_copy_without_changing_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "Floorball-Handball-task.md"
            source.write_text("# Existing task\n\nKeep this content.\n", encoding="utf-8")

            target = adopt_approved_task(source=source, project_root=root)

            self.assertEqual(target, root / "tasks" / "floorball-handball.md")
            self.assertEqual(read_status(target), "approved")
            self.assertIn("# Existing task", target.read_text(encoding="utf-8"))
            self.assertEqual(
                source.read_text(encoding="utf-8"),
                "# Existing task\n\nKeep this content.\n",
            )


if __name__ == "__main__":
    unittest.main()

