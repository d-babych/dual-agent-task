from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Callable, Optional

from dual_agent_task.agents import (
    _extract_claude_structured_output,
    run_claude_structured,
    run_codex_write_structured,
)
from dual_agent_task.config import HarnessConfig
from dual_agent_task.errors import HarnessError
from dual_agent_task.process_runner import run_streaming_process
from dual_agent_task.progress import (
    CallbackProgress,
    ProgressReporter,
    compact,
    format_duration,
    summarize_verification_line,
)
from dual_agent_task.task_file import read_status
from dual_agent_task.stages import (
    TaskStage,
    completed_stage_summary,
    parse_task_stages,
    record_stage_outcome,
    select_stage,
)


Reporter = Callable[[str], None]


@dataclass(frozen=True)
class VerificationResult:
    command: str
    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return self.return_code == 0


@dataclass(frozen=True)
class ImplementationOutcome:
    status: str
    rounds: int
    run_directory: Path
    stage_id: Optional[str] = None


def recover_implementation_review(
    *, config: HarnessConfig, run_directory: Path
) -> ImplementationOutcome:
    run_path = run_directory.expanduser().resolve()
    if not run_path.is_dir():
        raise HarnessError(f"Run directory does not exist: {run_path}")

    raw_files = sorted(run_path.glob("claude-review-*.raw.json"))
    if not raw_files:
        raise HarnessError(f"No raw Claude review found in {run_path}")
    raw_path = raw_files[-1]
    match = re.search(r"claude-review-(\d+)\.raw\.json$", raw_path.name)
    if match is None:
        raise HarnessError(f"Cannot determine review round from {raw_path.name}")
    round_number = int(match.group(1))

    captured_diff_path = run_path / f"changes-{round_number:02d}.diff"
    if not captured_diff_path.is_file():
        raise HarnessError(f"Captured diff is missing: {captured_diff_path}")
    captured_diff = captured_diff_path.read_text(encoding="utf-8")
    current_diff = _git(
        config.project_root,
        "diff",
        "--no-ext-diff",
        "--binary",
        "HEAD",
        "--",
    )
    if current_diff != captured_diff:
        raise HarnessError(
            "The working tree changed after Claude reviewed it; recovery would be stale. "
            "Run `implement --resume-review` instead."
        )

    schema = json.loads(
        (config.schemas_directory / "code-review.schema.json").read_text(encoding="utf-8")
    )
    required = tuple(str(key) for key in schema.get("required", []))
    review = _extract_claude_structured_output(
        raw_path.read_text(encoding="utf-8"),
        required_keys=required,
    )
    _validate_code_review(review)

    verification_logs = sorted(
        run_path.glob(f"verification-{round_number:02d}-*.log")
    )
    if not verification_logs:
        raise HarnessError("No verification logs found for the reviewed round")
    exit_codes = [_verification_log_exit_code(path) for path in verification_logs]
    checks_passed = all(code == 0 for code in exit_codes)

    verdict = str(review["verdict"])
    if verdict == "approved" and checks_passed:
        status = "ready_for_human_review"
    elif verdict == "needs_user_input":
        status = "needs_user_input"
    elif verdict == "changes_requested":
        status = "changes_requested"
    else:
        status = "review_incomplete"

    _write_json(run_path / f"code-review-{round_number:02d}.json", review)
    stage_metadata = _read_optional_json(run_path / "stage.json")
    stage_id = (
        str(stage_metadata["stage_id"])
        if stage_metadata is not None and stage_metadata.get("stage_id")
        else None
    )
    result_payload: dict[str, object] = {
        "status": status,
        "rounds": round_number,
        "recovered_from_raw_review": True,
        "verification_exit_codes": exit_codes,
        "runtimes": {
            "code_reviewer": {
                "agent": config.agents.code_reviewer,
                "model": config.models.code_reviewer,
                "effort": config.effort.code_reviewer,
            }
        },
    }
    if stage_id is not None:
        result_payload["stage_id"] = stage_id
    _write_json(
        run_path / "result.json",
        result_payload,
    )
    if stage_id is not None and stage_metadata is not None:
        task_path = Path(str(stage_metadata["task_path"]))
        stages = parse_task_stages(task_path.read_text(encoding="utf-8"))
        selected = next(stage for stage in stages if stage.stage_id == stage_id)
        record_stage_outcome(
            state_directory=config.state_directory,
            task_path=task_path,
            stages=stages,
            stage=selected,
            outcome_status=status,
            run_directory=run_path,
            base_head=str(stage_metadata["base_head"]),
        )
    return ImplementationOutcome(
        status=status,
        rounds=round_number,
        run_directory=run_path,
        stage_id=stage_id,
    )


def _verification_log_exit_code(path: Path) -> int:
    match = re.search(r"(?m)^exit:\s*(\d+)\s*$", path.read_text(encoding="utf-8"))
    if match is None:
        raise HarnessError(f"Verification log has no exit code: {path}")
    return int(match.group(1))


def run_implementation_workflow(
    *,
    config: HarnessConfig,
    task_path: Path,
    report: Reporter = print,
    resume_review: bool = False,
    progress: ProgressReporter | None = None,
    stage_selector: Optional[str] = None,
) -> ImplementationOutcome:
    output = progress or CallbackProgress(report)
    if config.agents.implementer != "codex" or config.agents.code_reviewer != "claude":
        raise HarnessError(
            "The MVP currently supports `codex` as implementer and `claude` "
            "as code_reviewer"
        )

    resolved_task = task_path.expanduser()
    if not resolved_task.is_absolute():
        candidate = (config.project_root / resolved_task).resolve()
        resolved_task = candidate if candidate.is_file() else resolved_task.resolve()
    else:
        resolved_task = resolved_task.resolve()
    if not resolved_task.is_file():
        raise HarnessError(f"Task file does not exist: {resolved_task}")
    if read_status(resolved_task) != "approved":
        raise HarnessError("Implementation requires a task with status `approved`")

    approved_task = resolved_task.read_text(encoding="utf-8")
    stages = parse_task_stages(approved_task)
    selected_stage = select_stage(
        state_directory=config.state_directory,
        task_path=resolved_task,
        stages=stages,
        selector=stage_selector,
    )
    completed_stages = completed_stage_summary(
        state_directory=config.state_directory,
        task_path=resolved_task,
        stages=stages,
    )

    base_head = _git(config.project_root, "rev-parse", "HEAD").strip()
    if config.git.require_clean_worktree and not resume_review:
        _require_clean_worktree(config.project_root, resolved_task)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-implement")
    run_directory = config.runs_directory / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    (run_directory / "approved-task.md").write_text(approved_task, encoding="utf-8")
    stage_context = _stage_execution_context(
        selected_stage=selected_stage,
        stages=stages,
        completed_stages=completed_stages,
    )
    if selected_stage is not None:
        (run_directory / "current-stage.md").write_text(
            f"{selected_stage.markdown}\n", encoding="utf-8"
        )
        _write_json(
            run_directory / "stage.json",
            {
                "stage_id": selected_stage.stage_id,
                "stage_number": selected_stage.index,
                "stage_total": len(stages),
                "task_path": str(resolved_task),
                "base_head": base_head,
            },
        )
        output.info(
            f"Stage {selected_stage.index}/{len(stages)} · "
            f"{selected_stage.stage_id} · {selected_stage.title}"
        )

    rules = _read(config.project_root / "PROJECT_RULES.md")
    implementer_template = _read(config.prompts_directory / "implementer.md")
    reviewer_template = _read(config.prompts_directory / "code-reviewer.md")
    implementation_schema = config.schemas_directory / "implementation-result.schema.json"
    review_schema = config.schemas_directory / "code-review.schema.json"

    latest_review = "No previous review exists."
    latest_verification = "No verification has run yet."
    final_status = "review_incomplete"
    rounds_completed = 0

    for round_number in range(1, config.limits.max_code_rounds + 1):
        rounds_completed = round_number
        if resume_review and round_number == 1:
            output.info(
                f"[{round_number}/{config.limits.max_code_rounds}] Resuming existing "
                "working tree · skipping Codex implementation"
            )
            implementation_result = {
                "summary": "Resumed from an existing implementation after a reviewer parsing failure.",
                "tests_run": [],
                "remaining_risks": [
                    "The prior implementer summary is available in the previous run."
                ],
            }
        else:
            output.start(
                f"[{round_number}/{config.limits.max_code_rounds}] Codex implementing · "
                f"{_runtime_label(config.models.implementer, config.effort.implementer)}"
            )
            stage_started = time.monotonic()
            implementer_prompt = _render_prompt(
                implementer_template,
                PROJECT_RULES=rules,
                APPROVED_TASK=approved_task,
                LATEST_REVIEW=latest_review,
                VERIFICATION_RESULTS=latest_verification,
            )
            implementer_prompt = f"{implementer_prompt.rstrip()}\n\n{stage_context}\n"
            implementer_output = run_directory / f"implementer-{round_number:02d}.json"
            try:
                implementation_result = run_codex_write_structured(
                    prompt=implementer_prompt,
                    schema_path=implementation_schema,
                    output_path=implementer_output,
                    project_root=config.project_root,
                    timeout_seconds=config.limits.agent_timeout_seconds,
                    model=config.models.implementer,
                    effort=config.effort.implementer,
                    on_activity=output.activity,
                    on_heartbeat=output.heartbeat,
                    on_raw=output.raw,
                    raw_output_path=run_directory
                    / f"codex-implementer-{round_number:02d}.raw.jsonl",
                )
            except HarnessError as error:
                output.finish(
                    f"Codex failed · {format_duration(time.monotonic() - stage_started)}",
                    passed=False,
                )
                raise HarnessError(
                    f"{error}\nRun artifacts: {run_directory}"
                ) from error
            _assert_head_unchanged(config.project_root, base_head)
            changed_files = _changed_file_count(config.project_root)
            output.finish(
                f"Codex finished · {format_duration(time.monotonic() - stage_started)} · "
                f"{changed_files} files changed"
            )

        verification = _run_verification(
            config,
            run_directory,
            round_number,
            progress=output,
            total_rounds=config.limits.max_code_rounds,
        )
        latest_verification = json.dumps(
            [asdict(result) | {"passed": result.passed} for result in verification],
            ensure_ascii=False,
            indent=2,
        )

        status = _git(config.project_root, "status", "--short", "--untracked-files=all")
        diff = _git(config.project_root, "diff", "--no-ext-diff", "--binary", "HEAD", "--")
        (run_directory / f"changes-{round_number:02d}.diff").write_text(
            diff, encoding="utf-8"
        )
        (run_directory / f"status-{round_number:02d}.txt").write_text(
            status, encoding="utf-8"
        )

        output.start(
            f"[{round_number}/{config.limits.max_code_rounds}] Claude reviewing · "
            f"{_runtime_label(config.models.code_reviewer, config.effort.code_reviewer)}"
        )
        review_started = time.monotonic()
        reviewer_prompt = _render_prompt(
            reviewer_template,
            PROJECT_RULES=rules,
            APPROVED_TASK=approved_task,
            IMPLEMENTER_SUMMARY=json.dumps(
                implementation_result, ensure_ascii=False, indent=2
            ),
            GIT_STATUS=status or "Working tree has no visible changes.",
            CURRENT_DIFF=diff or "No tracked diff is available. Inspect files from git status.",
            VERIFICATION_RESULTS=latest_verification,
        )
        reviewer_prompt = f"{reviewer_prompt.rstrip()}\n\n{stage_context}\n"
        try:
            review = run_claude_structured(
                prompt=reviewer_prompt,
                schema_path=review_schema,
                project_root=config.project_root,
                timeout_seconds=config.limits.agent_timeout_seconds,
                tools=("Read", "Glob", "Grep"),
                model=config.models.code_reviewer,
                effort=config.effort.code_reviewer,
                raw_output_path=run_directory
                / f"claude-review-{round_number:02d}.raw.json",
                on_activity=output.activity,
                on_heartbeat=output.heartbeat,
                on_raw=output.raw,
            )
        except HarnessError as error:
            output.finish(
                f"Claude review failed · "
                f"{format_duration(time.monotonic() - review_started)}",
                passed=False,
            )
            raise HarnessError(
                f"{error}\nRun artifacts: {run_directory}"
            ) from error
        _validate_code_review(review)
        _write_json(run_directory / f"code-review-{round_number:02d}.json", review)
        latest_review = json.dumps(review, ensure_ascii=False, indent=2)

        all_checks_passed = all(result.passed for result in verification)
        verdict = str(review["verdict"])
        output.finish(
            f"Review finished · {verdict} · "
            f"{format_duration(time.monotonic() - review_started)}",
            passed=verdict == "approved",
        )
        if verdict == "approved" and all_checks_passed:
            final_status = "ready_for_human_review"
            break
        if verdict == "needs_user_input":
            final_status = "needs_user_input"
            break
        if verdict == "approved" and not all_checks_passed:
            latest_review = _verification_failure_review(verification)

    _assert_head_unchanged(config.project_root, base_head)
    result_payload: dict[str, object] = {
        "status": final_status,
        "rounds": rounds_completed,
        "base_head": base_head,
        "task_path": str(resolved_task),
        "runtimes": {
            "implementer": {
                "agent": config.agents.implementer,
                "model": config.models.implementer,
                "effort": config.effort.implementer,
            },
            "code_reviewer": {
                "agent": config.agents.code_reviewer,
                "model": config.models.code_reviewer,
                "effort": config.effort.code_reviewer,
            },
        },
    }
    if selected_stage is not None:
        result_payload.update(
            {
                "stage_id": selected_stage.stage_id,
                "stage_number": selected_stage.index,
                "stage_total": len(stages),
            }
        )
    _write_json(run_directory / "result.json", result_payload)
    if selected_stage is not None:
        record_stage_outcome(
            state_directory=config.state_directory,
            task_path=resolved_task,
            stages=stages,
            stage=selected_stage,
            outcome_status=final_status,
            run_directory=run_directory,
            base_head=base_head,
        )
    return ImplementationOutcome(
        status=final_status,
        rounds=rounds_completed,
        run_directory=run_directory,
        stage_id=selected_stage.stage_id if selected_stage is not None else None,
    )


def _stage_execution_context(
    *,
    selected_stage: Optional[TaskStage],
    stages: tuple[TaskStage, ...],
    completed_stages: str,
) -> str:
    if selected_stage is None:
        return (
            "## Harness execution scope\n\n"
            "This is an atomic task without implementation stages. Implement and review "
            "the full approved task."
        )
    return f"""
## Harness execution scope

This run implements exactly one stage: `{selected_stage.stage_id}`
(stage {selected_stage.index} of {len(stages)}).

### Current stage

{selected_stage.markdown}

### Completed stages

{completed_stages}

Implement and review only the current stage. Requirements belonging exclusively
to pending stages are out of scope. Preserve behavior delivered by completed
stages and flag any regression. Do not pre-implement later stages.
""".strip()


def _runtime_label(model: object, effort: object) -> str:
    return f"{model or 'CLI default'}/{effort or 'CLI default'}"


def _run_verification(
    config: HarnessConfig,
    run_directory: Path,
    round_number: int,
    *,
    progress: ProgressReporter,
    total_rounds: int,
) -> list[VerificationResult]:
    results: list[VerificationResult] = []
    total_commands = len(config.verification_commands)
    for index, command in enumerate(config.verification_commands, start=1):
        progress.start(
            f"[{round_number}/{total_rounds}] Verification {index}/{total_commands} · "
            f"{compact(command)}"
        )

        def handle_line(source: str, line: str) -> None:
            progress.raw(source, line)
            summary = summarize_verification_line(line)
            if summary is not None:
                progress.activity(summary)

        try:
            completed = run_streaming_process(
                ("/bin/zsh", "-lc", command),
                cwd=config.project_root,
                timeout_seconds=config.limits.agent_timeout_seconds,
                environment=os.environ.copy(),
                on_line=handle_line,
                on_heartbeat=progress.heartbeat,
            )
            result = VerificationResult(
                command=command,
                return_code=completed.return_code,
                stdout=completed.stdout,
                stderr=completed.stderr,
                duration_seconds=completed.duration_seconds,
            )
        except subprocess.TimeoutExpired as error:
            result = VerificationResult(
                command=command,
                return_code=124,
                stdout=error.stdout or "",
                stderr=f"Timed out after {config.limits.agent_timeout_seconds} seconds",
                duration_seconds=float(config.limits.agent_timeout_seconds),
            )
        results.append(result)
        log_path = run_directory / f"verification-{round_number:02d}-{index:02d}.log"
        log_path.write_text(
            f"$ {command}\nexit: {result.return_code}\n\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}\n",
            encoding="utf-8",
        )
        progress.finish(
            f"{'Passed' if result.passed else 'Failed'} · "
            f"{format_duration(result.duration_seconds)}"
            + ("" if result.passed else f" · exit {result.return_code}"),
            passed=result.passed,
        )
    return results


def _changed_file_count(project_root: Path) -> int:
    status = _git(project_root, "status", "--short", "--untracked-files=all")
    return len([line for line in status.splitlines() if line.strip()])


def _require_clean_worktree(project_root: Path, task_path: Path) -> None:
    output = _git(project_root, "status", "--porcelain", "--untracked-files=all")
    allowed_task: str | None = None
    try:
        allowed_task = str(task_path.relative_to(project_root))
    except ValueError:
        pass

    disallowed: list[str] = []
    for line in output.splitlines():
        path_text = line[3:].strip()
        if allowed_task is not None and path_text == allowed_task:
            continue
        if path_text.startswith(".dual-agent-task/state/"):
            continue
        disallowed.append(line)
    if disallowed:
        detail = "\n".join(disallowed)
        raise HarnessError(
            "Working tree must be clean before implementation. The approved task "
            f"file itself may be uncommitted.\n{detail}"
        )


def _assert_head_unchanged(project_root: Path, expected: str) -> None:
    actual = _git(project_root, "rev-parse", "HEAD").strip()
    if actual != expected:
        raise HarnessError(
            "The implementer changed Git HEAD. Automatic commits and branch changes "
            "are forbidden; inspect the repository manually."
        )


def _git(project_root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ("git", *arguments),
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise HarnessError(f"Git command failed: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise HarnessError(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise HarnessError(f"Cannot read required file {path}: {error}") from error


def _render_prompt(template: str, **values: str) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered


def _validate_code_review(review: dict[str, object]) -> None:
    verdict = review.get("verdict")
    if verdict not in {"approved", "changes_requested", "needs_user_input"}:
        raise HarnessError(f"Reviewer returned an unsupported verdict: {verdict}")
    for key in ("summary", "findings", "verification_assessment"):
        if key not in review:
            raise HarnessError(f"Code review is missing `{key}`")


def _verification_failure_review(results: list[VerificationResult]) -> str:
    failures = [result for result in results if not result.passed]
    return json.dumps(
        {
            "verdict": "changes_requested",
            "summary": "Deterministic verification failed and must be fixed.",
            "findings": [
                {
                    "id": f"VERIFY-{index}",
                    "severity": "blocking",
                    "path": None,
                    "line": None,
                    "issue": f"Command failed with exit code {result.return_code}: {result.command}",
                    "expected_fix": "Fix the implementation or test failure and rerun verification.",
                }
                for index, result in enumerate(failures, start=1)
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_optional_json(path: Path) -> Optional[dict[str, object]]:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HarnessError(f"Cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise HarnessError(f"Expected a JSON object in {path}")
    return value

