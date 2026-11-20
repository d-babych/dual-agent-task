from __future__ import annotations

from pathlib import Path
import re

from dual_agent_task.errors import HarnessError


STATUS_PATTERN = re.compile(r"(?m)^status:\s*([a-z_]+)\s*$")


def render_task(*, slug: str, status: str, title: str, markdown: str) -> str:
    body = markdown.strip()
    return (
        "---\n"
        f"id: {slug}\n"
        f"status: {status}\n"
        "---\n\n"
        f"# {title.strip()}\n\n"
        f"{body}\n"
    )


def read_status(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    match = STATUS_PATTERN.search(content)
    if match is None:
        raise HarnessError(f"Task file has no status field: {path}")
    return match.group(1)


def approve_task(path: Path) -> None:
    task_path = path.expanduser().resolve()
    if not task_path.is_file():
        raise HarnessError(f"Task file does not exist: {task_path}")
    content = task_path.read_text(encoding="utf-8")
    match = STATUS_PATTERN.search(content)
    if match is None:
        raise HarnessError(f"Task file has no status field: {task_path}")
    current = match.group(1)
    if current == "approved":
        return
    if current != "ready_for_human":
        raise HarnessError(
            f"Task must be ready_for_human before approval; current status is {current}"
        )
    updated = STATUS_PATTERN.sub("status: approved", content, count=1)
    task_path.write_text(updated, encoding="utf-8")

