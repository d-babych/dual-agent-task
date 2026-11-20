from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Callable

from dual_agent_task.agents import run_claude_structured, run_codex_structured
from dual_agent_task.config import HarnessConfig
from dual_agent_task.errors import HarnessError
from dual_agent_task.task_file import render_task


Reporter = Callable[[str], None]


def run_spec_workflow(
    *,
    config: HarnessConfig,
    feature_description: str,
    report: Reporter = print,
) -> Path:
    description = feature_description.strip()
    if not description:
        raise HarnessError("Feature description cannot be empty")
    if config.agents.spec_author != "claude" or config.agents.spec_reviewer != "codex":
        raise HarnessError(
            "The MVP currently supports `claude` as spec_author and `codex` "
            "as spec_reviewer"
        )

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-spec")
    run_directory = config.runs_directory / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    (run_directory / "input.md").write_text(f"{description}\n", encoding="utf-8")

    rules = _read(config.project_root / "PROJECT_RULES.md")
    author_template = _read(config.prompts_directory / "spec-author.md")
    reviewer_template = _read(config.prompts_directory / "spec-reviewer.md")
    task_schema = config.schemas_directory / "task-result.schema.json"
    review_schema = config.schemas_directory / "review.schema.json"

    current_task = "No draft exists yet."
    latest_review = "No review exists yet."
    stable_slug: str | None = None
    final_author: dict[str, Any] | None = None
    final_status = "review_incomplete"

    for round_number in range(1, config.limits.max_spec_rounds + 1):
        report(
            f"[{round_number}/{config.limits.max_spec_rounds}] Claude is drafting "
            f"the task ({_runtime_label(config.models.spec_author, config.effort.spec_author)})..."
        )
        author_prompt = _render_prompt(
            author_template,
            PROJECT_RULES=rules,
            FEATURE_DESCRIPTION=description,
            CURRENT_TASK=current_task,
            LATEST_REVIEW=latest_review,
        )
        author_result = run_claude_structured(
            prompt=author_prompt,
            schema_path=task_schema,
            project_root=config.project_root,
            timeout_seconds=config.limits.agent_timeout_seconds,
            model=config.models.spec_author,
            effort=config.effort.spec_author,
            raw_output_path=run_directory / f"claude-author-{round_number:02d}.raw.json",
        )
        _validate_author_result(author_result)
        if stable_slug is None:
            stable_slug = _safe_slug(str(author_result["slug"]))
            existing_task = config.project_root / "tasks" / f"{stable_slug}.md"
            if existing_task.exists():
                raise HarnessError(
                    f"Task already exists and will not be overwritten: {existing_task}"
                )
        final_author = author_result
        current_task = str(author_result["task_markdown"]).strip()
        _write_json(run_directory / f"spec-round-{round_number:02d}.json", author_result)
        (run_directory / f"task-draft-{round_number:02d}.md").write_text(
            f"{current_task}\n", encoding="utf-8"
        )

        report(
            f"[{round_number}/{config.limits.max_spec_rounds}] Codex is reviewing "
            f"the task ({_runtime_label(config.models.spec_reviewer, config.effort.spec_reviewer)})..."
        )
        reviewer_prompt = _render_prompt(
            reviewer_template,
            PROJECT_RULES=rules,
            FEATURE_DESCRIPTION=description,
            CURRENT_TASK=current_task,
        )
        reviewer_output = run_directory / f"codex-review-{round_number:02d}.json"
        review = run_codex_structured(
            prompt=reviewer_prompt,
            schema_path=review_schema,
            output_path=reviewer_output,
            project_root=config.project_root,
            timeout_seconds=config.limits.agent_timeout_seconds,
            model=config.models.spec_reviewer,
            effort=config.effort.spec_reviewer,
        )
        _validate_review(review)
        _write_json(run_directory / f"review-{round_number:02d}.json", review)
        latest_review = json.dumps(review, ensure_ascii=False, indent=2)

        verdict = review["verdict"]
        report(f"Codex verdict: {verdict}")
        if verdict == "approved":
            if author_result["open_questions"]:
                report("The draft still has open questions; human input is required.")
                final_status = "needs_user_input"
            else:
                final_status = "ready_for_human"
            break
        if verdict == "needs_user_input":
            final_status = "needs_user_input"
            break

    if final_author is None or stable_slug is None:
        raise HarnessError("Spec workflow completed without an author result")

    task_path = config.project_root / "tasks" / f"{stable_slug}.md"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(
        render_task(
            slug=stable_slug,
            status=final_status,
            title=str(final_author["title"]),
            markdown=str(final_author["task_markdown"]),
        ),
        encoding="utf-8",
    )
    (run_directory / "result.json").write_text(
        json.dumps(
            {
                "status": final_status,
                "task_path": str(task_path),
                "runtimes": {
                    "spec_author": {
                        "agent": config.agents.spec_author,
                        "model": config.models.spec_author,
                        "effort": config.effort.spec_author,
                    },
                    "spec_reviewer": {
                        "agent": config.agents.spec_reviewer,
                        "model": config.models.spec_reviewer,
                        "effort": config.effort.spec_reviewer,
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return task_path


def _runtime_label(model: object, effort: object) -> str:
    return f"model={model or 'CLI default'}, effort={effort or 'CLI default'}"


def _render_prompt(template: str, **values: str) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise HarnessError(f"Cannot read required file {path}: {error}") from error


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        raise HarnessError("Agent returned an invalid empty task slug")
    return slug[:80].rstrip("-")


def _validate_author_result(value: dict[str, Any]) -> None:
    for key in ("slug", "title", "task_markdown", "open_questions"):
        if key not in value:
            raise HarnessError(f"Author result is missing `{key}`")
    if not isinstance(value["open_questions"], list):
        raise HarnessError("Author result `open_questions` must be an array")


def _validate_review(value: dict[str, Any]) -> None:
    verdict = value.get("verdict")
    if verdict not in {"approved", "changes_requested", "needs_user_input"}:
        raise HarnessError(f"Reviewer returned an unsupported verdict: {verdict}")
    for key in ("summary", "blocking", "suggestions"):
        if key not in value:
            raise HarnessError(f"Review result is missing `{key}`")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

