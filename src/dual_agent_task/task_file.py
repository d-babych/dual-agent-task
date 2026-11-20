from __future__ import annotations

from pathlib import Path
import json
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


def adopt_approved_task(
    *, source: Path, project_root: Path, slug: str | None = None
) -> Path:
    source_path = source.expanduser().resolve()
    root = project_root.expanduser().resolve()
    if not source_path.is_file():
        raise HarnessError(f"Source task does not exist: {source_path}")

    task_slug = _task_slug(slug or source_path.stem)
    if task_slug.endswith("-task"):
        task_slug = task_slug[: -len("-task")]
    if not task_slug:
        raise HarnessError("Task slug cannot be empty")

    target = root / "tasks" / f"{task_slug}.md"
    if target.exists():
        raise HarnessError(f"Managed task already exists and will not be overwritten: {target}")

    try:
        source_label = str(source_path.relative_to(root))
    except ValueError:
        source_label = str(source_path)

    original = source_path.read_text(encoding="utf-8").strip()
    if not original:
        raise HarnessError(f"Source task is empty: {source_path}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\n"
        f"id: {task_slug}\n"
        "status: approved\n"
        f"source: {json.dumps(source_label, ensure_ascii=False)}\n"
        "---\n\n"
        f"{original}\n",
        encoding="utf-8",
    )
    return target


def _task_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")[:80]

