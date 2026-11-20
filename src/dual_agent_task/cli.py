from __future__ import annotations

import argparse
from pathlib import Path
import sys

from dual_agent_task import __version__
from dual_agent_task.config import HarnessConfig
from dual_agent_task.errors import HarnessError
from dual_agent_task.implementation_workflow import run_implementation_workflow
from dual_agent_task.project import initialize_project, run_doctor, upgrade_project
from dual_agent_task.spec_workflow import run_spec_workflow
from dual_agent_task.task_file import approve_task, read_status


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
    implement_parser.set_defaults(handler=_handle_implement)

    task_parser = subparsers.add_parser("task", help="Inspect or update task files")
    task_subparsers = task_parser.add_subparsers(dest="task_command", required=True)

    show_parser = task_subparsers.add_parser("show", help="Show a task and its status")
    show_parser.add_argument("path", type=Path)
    show_parser.set_defaults(handler=_handle_task_show)

    approve_parser = task_subparsers.add_parser("approve", help="Approve a reviewed task")
    approve_parser.add_argument("path", type=Path)
    approve_parser.set_defaults(handler=_handle_task_approve)

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
    )
    status = read_status(task_path)
    print(f"Task created: {task_path}")
    print(f"Status: {status}")
    if status == "ready_for_human":
        print(f"Approve with: dual-agent-task task approve {task_path}")
    return 0


def _handle_implement(arguments: argparse.Namespace) -> int:
    config = HarnessConfig.load(Path(arguments.project))
    outcome = run_implementation_workflow(
        config=config,
        task_path=arguments.task,
    )
    print(f"Implementation status: {outcome.status}")
    print(f"Rounds: {outcome.rounds}")
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


if __name__ == "__main__":
    raise SystemExit(main())

