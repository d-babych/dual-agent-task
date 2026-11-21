from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from dual_agent_task import __version__
from dual_agent_task.config import HarnessConfig
from dual_agent_task.errors import HarnessError
from dual_agent_task.implementation_workflow import (
    recover_implementation_review,
    run_implementation_workflow,
)
from dual_agent_task.project import initialize_project, run_doctor, upgrade_project
from dual_agent_task.progress import ConsoleProgress
from dual_agent_task.spec_workflow import run_spec_workflow
from dual_agent_task.stages import (
    complete_stage,
    parse_task_stages,
    stage_status_payload,
    stage_statuses,
)
from dual_agent_task.task_file import adopt_approved_task, approve_task, read_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dual-agent-task",
        description="Coordinate Codex and Claude Code around explicit task artifacts.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize a project")
    init_parser.add_argument("project", nargs="?", default=".")
    init_parser.set_defaults(handler=_handle_init)

    upgrade_parser = subparsers.add_parser(
        "upgrade", help="Add missing harness config without overwriting custom project files"
    )
    upgrade_parser.add_argument("project", nargs="?", default=".")
    upgrade_parser.set_defaults(handler=_handle_upgrade)

    doctor_parser = subparsers.add_parser("doctor", help="Check project setup")
    doctor_parser.add_argument("project", nargs="?", default=".")
    doctor_parser.set_defaults(handler=_handle_doctor)

    plan_parser = subparsers.add_parser(
        "plan", help="Formulate and review a task without changing project code"
    )
    _add_plan_arguments(plan_parser)
    plan_parser.set_defaults(handler=_handle_plan)

    spec_parser = subparsers.add_parser(
        "spec", help="Alias for `plan` (kept for compatibility)"
    )
    _add_plan_arguments(spec_parser)
    spec_parser.set_defaults(handler=_handle_plan)

    implement_parser = subparsers.add_parser(
        "implement", help="Implement an approved task and run read-only review"
    )
    implement_parser.add_argument("task", type=Path)
    implement_parser.add_argument("--project", default=".")
    implement_parser.add_argument(
        "--stage",
        help="Implementation stage id, number, or `next`",
    )
    implement_parser.add_argument(
        "--resume-review",
        action="store_true",
        help="Keep current changes and resume from verification/review",
    )
    _add_progress_arguments(implement_parser)
    implement_parser.set_defaults(handler=_handle_implement)

    recover_parser = subparsers.add_parser(
        "recover-review",
        help="Recover a completed Claude review from a failed run without calling agents",
    )
    recover_parser.add_argument("run_directory", type=Path)
    recover_parser.add_argument("--project", default=".")
    recover_parser.set_defaults(handler=_handle_recover_review)

    task_parser = subparsers.add_parser("task", help="Inspect or update task files")
    task_subparsers = task_parser.add_subparsers(dest="task_command", required=True)

    show_parser = task_subparsers.add_parser("show", help="Show a task and its status")
    show_parser.add_argument("path", type=Path)
    show_parser.set_defaults(handler=_handle_task_show)

    approve_parser = task_subparsers.add_parser("approve", help="Approve a reviewed task")
    approve_parser.add_argument("path", type=Path)
    approve_parser.set_defaults(handler=_handle_task_approve)

    adopt_parser = task_subparsers.add_parser(
        "adopt", help="Import an already approved task without running planning agents"
    )
    adopt_parser.add_argument("source", type=Path)
    adopt_parser.add_argument("--project", default=".")
    adopt_parser.add_argument("--slug")
    adopt_parser.set_defaults(handler=_handle_task_adopt)

    stages_parser = task_subparsers.add_parser(
        "stages", help="List implementation stages and their local status"
    )
    stages_parser.add_argument("path", type=Path)
    stages_parser.add_argument("--project", default=".")
    stages_parser.add_argument("--json", action="store_true", dest="json_output")
    stages_parser.set_defaults(handler=_handle_task_stages)

    complete_stage_parser = task_subparsers.add_parser(
        "complete-stage",
        help="Mark an approved and manually committed stage as completed",
    )
    complete_stage_parser.add_argument("path", type=Path)
    complete_stage_parser.add_argument("stage")
    complete_stage_parser.add_argument("--project", default=".")
    complete_stage_parser.set_defaults(handler=_handle_task_complete_stage)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        return int(arguments.handler(arguments))
    except HarnessError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


def _handle_init(arguments: argparse.Namespace) -> int:
    root = Path(arguments.project).expanduser().resolve()
    written = initialize_project(root)
    if written:
        print(f"Initialized dual-agent-task in {root}")
        for path in written:
            print(f"  created {path.relative_to(root)}")
    else:
        print(f"dual-agent-task is already initialized in {root}")
    print("Next: edit PROJECT_RULES.md, then run `dual-agent-task doctor`.")
    return 0


def _handle_doctor(arguments: argparse.Namespace) -> int:
    results = run_doctor(Path(arguments.project))
    for result in results:
        marker = "✓" if result.passed else "✗"
        print(f"{marker} {result.name}: {result.detail}")
    return 0 if results and all(result.passed for result in results) else 1


def _handle_upgrade(arguments: argparse.Namespace) -> int:
    root = Path(arguments.project).expanduser().resolve()
    changed = upgrade_project(root)
    if changed:
        print(f"Upgraded dual-agent-task configuration in {root}")
        for path in changed:
            print(f"  updated {path.relative_to(root)}")
    else:
        print(f"dual-agent-task configuration is already current in {root}")
    print("Next: review config.toml, then run `dual-agent-task doctor`.")
    return 0


def _add_plan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", default=".")
    parser.add_argument("--input", type=Path)
    stage_group = parser.add_mutually_exclusive_group()
    stage_group.add_argument(
        "--stages",
        choices=("auto", "required", "disabled"),
        default="auto",
        help="Stage planning policy (default: auto)",
    )
    stage_group.add_argument(
        "--staged",
        dest="stages",
        action="store_const",
        const="required",
        help="Alias for `--stages required`",
    )
    stage_group.add_argument(
        "--no-stages",
        dest="stages",
        action="store_const",
        const="disabled",
        help="Alias for `--stages disabled`",
    )
    _add_progress_arguments(parser)


def _add_progress_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--progress",
        choices=("live", "plain", "quiet", "verbose"),
        default="live",
        help="Terminal progress style (default: live)",
    )
    group.add_argument(
        "--quiet",
        dest="progress",
        action="store_const",
        const="quiet",
        help="Hide intermediate progress",
    )
    group.add_argument(
        "--verbose",
        dest="progress",
        action="store_const",
        const="verbose",
        help="Show raw agent and verification output",
    )


def _handle_plan(arguments: argparse.Namespace) -> int:
    config = HarnessConfig.load(Path(arguments.project))
    if arguments.input is not None:
        description = arguments.input.expanduser().read_text(encoding="utf-8")
    else:
        if sys.stdin.isatty():
            print("Describe the feature, then press Ctrl-D:")
        description = sys.stdin.read()

    task_path = run_spec_workflow(
        config=config,
        feature_description=description,
        progress=ConsoleProgress(arguments.progress),
        stage_mode=arguments.stages,
    )
    status = read_status(task_path)
    print(f"Task created: {task_path}")
    print(f"Status: {status}")
    stages = parse_task_stages(task_path.read_text(encoding="utf-8"))
    if stages:
        print(f"Stages: {len(stages)}")
        print(
            "List with: dual-agent-task task stages "
            f"--project {config.project_root} {task_path}"
        )
    if status == "ready_for_human":
        print(f"Approve with: dual-agent-task task approve {task_path}")
    return 0


def _handle_implement(arguments: argparse.Namespace) -> int:
    config = HarnessConfig.load(Path(arguments.project))
    outcome = run_implementation_workflow(
        config=config,
        task_path=arguments.task,
        resume_review=arguments.resume_review,
        progress=ConsoleProgress(arguments.progress),
        stage_selector=arguments.stage,
    )
    print(f"Result: {outcome.status}")
    print(f"Rounds: {outcome.rounds}")
    print(f"Run artifacts: {outcome.run_directory}")
    if outcome.stage_id is not None:
        print(f"Stage: {outcome.stage_id}")
    if outcome.status == "ready_for_human_review":
        print("Review the working tree with `git status` and `git diff`, then commit manually.")
        if outcome.stage_id is not None:
            task_path = arguments.task.expanduser()
            if not task_path.is_absolute():
                task_path = (config.project_root / task_path).resolve()
            print(
                "After the commit: dual-agent-task task complete-stage "
                f"--project {config.project_root} {task_path} {outcome.stage_id}"
            )
        return 0
    return 2


def _handle_recover_review(arguments: argparse.Namespace) -> int:
    config = HarnessConfig.load(Path(arguments.project))
    outcome = recover_implementation_review(
        config=config,
        run_directory=arguments.run_directory,
    )
    print(f"Recovered review status: {outcome.status}")
    print(f"Run artifacts: {outcome.run_directory}")
    if outcome.status == "ready_for_human_review":
        print("Review the working tree with `git status` and `git diff`, then commit manually.")
        return 0
    return 2


def _handle_task_show(arguments: argparse.Namespace) -> int:
    path = arguments.path.expanduser().resolve()
    if not path.is_file():
        raise HarnessError(f"Task file does not exist: {path}")
    print(path.read_text(encoding="utf-8"), end="")
    print(f"\nStatus: {read_status(path)}")
    return 0


def _handle_task_approve(arguments: argparse.Namespace) -> int:
    path = arguments.path.expanduser().resolve()
    approve_task(path)
    print(f"Approved: {path}")
    return 0


def _handle_task_adopt(arguments: argparse.Namespace) -> int:
    path = adopt_approved_task(
        source=arguments.source,
        project_root=Path(arguments.project),
        slug=arguments.slug,
    )
    print(f"Adopted approved task: {path}")
    print(f"Status: {read_status(path)}")
    print(
        "Implement with: dual-agent-task implement "
        f"--project {Path(arguments.project).expanduser().resolve()} {path}"
    )
    return 0


def _handle_task_stages(arguments: argparse.Namespace) -> int:
    path = arguments.path.expanduser().resolve()
    if not path.is_file():
        raise HarnessError(f"Task file does not exist: {path}")
    project_root = Path(arguments.project).expanduser().resolve()
    stages = parse_task_stages(path.read_text(encoding="utf-8"))
    statuses = stage_statuses(
        state_directory=project_root / ".dual-agent-task" / "state",
        task_path=path,
        stages=stages,
    )
    payload = stage_status_payload(statuses)
    if arguments.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if not payload:
        print("Task has no implementation stages.")
        return 0
    for item in payload:
        suffix = ""
        if item.get("blocked_by"):
            suffix = f" · blocked by: {', '.join(item['blocked_by'])}"
        print(
            f"{item['number']}. {item['id']} · {item['status']}{suffix} · "
            f"{item['title']}"
        )
    return 0


def _handle_task_complete_stage(arguments: argparse.Namespace) -> int:
    path = arguments.path.expanduser().resolve()
    if not path.is_file():
        raise HarnessError(f"Task file does not exist: {path}")
    if read_status(path) != "approved":
        raise HarnessError("Only an approved task can complete implementation stages")
    project_root = Path(arguments.project).expanduser().resolve()
    stages = parse_task_stages(path.read_text(encoding="utf-8"))
    completed = complete_stage(
        project_root=project_root,
        state_directory=project_root / ".dual-agent-task" / "state",
        task_path=path,
        stages=stages,
        selector=arguments.stage,
    )
    print(f"Completed stage: {completed.stage_id}")
    print(f"Next: dual-agent-task implement --stage next --project {project_root} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

