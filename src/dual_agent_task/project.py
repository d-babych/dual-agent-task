from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
from typing import Iterable

from dual_agent_task.config import CONFIG_DIRECTORY, HarnessConfig
from dual_agent_task.resources import asset_text


PROMPT_FILES = (
    "spec-author.md",
    "spec-reviewer.md",
    "implementer.md",
    "code-reviewer.md",
)
SCHEMA_FILES = (
    "task-result.schema.json",
    "review.schema.json",
    "implementation-result.schema.json",
    "code-review.schema.json",
)

MODEL_SECTION = """[models]
spec_author = "sonnet"
spec_reviewer = "gpt-5.6-sol"
implementer = "gpt-5.6-sol"
code_reviewer = "sonnet"
"""

EFFORT_SECTION = """[effort]
spec_author = "high"
spec_reviewer = "high"
implementer = "medium"
code_reviewer = "high"
"""


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def initialize_project(project_root: Path) -> list[Path]:
    root = project_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    harness_directory = root / CONFIG_DIRECTORY
    prompts_directory = harness_directory / "prompts"
    schemas_directory = harness_directory / "schemas"
    runs_directory = harness_directory / "runs"
    tasks_directory = root / "tasks"

    for directory in (
        prompts_directory,
        schemas_directory,
        runs_directory,
        tasks_directory,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    written.extend(
        _write_if_missing(
            harness_directory / "config.toml",
            asset_text("templates/config.toml"),
        )
    )
    written.extend(
        _write_if_missing(
            root / "PROJECT_RULES.md",
            asset_text("templates/PROJECT_RULES.md"),
        )
    )

    for name in PROMPT_FILES:
        written.extend(
            _write_if_missing(
                prompts_directory / name,
                asset_text(f"prompts/{name}"),
            )
        )

    for name in SCHEMA_FILES:
        written.extend(
            _write_if_missing(
                schemas_directory / name,
                asset_text(f"schemas/{name}"),
            )
        )

    written.extend(_ensure_gitignore_entry(root, f"{CONFIG_DIRECTORY}/runs/"))
    return written


def upgrade_project(project_root: Path) -> list[Path]:
    """Add missing harness assets and config sections without replacing custom files."""
    root = project_root.expanduser().resolve()
    changed = initialize_project(root)
    config_path = root / CONFIG_DIRECTORY / "config.toml"
    content = config_path.read_text(encoding="utf-8")
    additions: list[str] = []
    if "[models]" not in content:
        additions.append(MODEL_SECTION)
    if "[effort]" not in content:
        additions.append(EFFORT_SECTION)
    if additions:
        separator = "" if content.endswith("\n\n") else "\n"
        appended_sections = "\n".join(additions)
        config_path.write_text(
            f"{content}{separator}{appended_sections}",
            encoding="utf-8",
        )
        if config_path not in changed:
            changed.append(config_path)
    return changed


def run_doctor(project_root: Path) -> list[CheckResult]:
    root = project_root.expanduser().resolve()
    results: list[CheckResult] = []

    results.append(_git_repository_check(root))

    try:
        config = HarnessConfig.load(root)
        results.append(CheckResult("configuration", True, "config.toml is valid"))
    except Exception as error:
        results.append(CheckResult("configuration", False, str(error)))
        return results

    results.extend(_file_checks(config))
    results.extend(_runtime_checks(config))
    results.extend(
        (
            _command_check("Codex CLI", "codex", ("--version",)),
            _command_check("Codex authentication", "codex", ("login", "status")),
            _command_check("Claude Code CLI", "claude", ("--version",)),
            _command_check("Claude authentication", "claude", ("auth", "status")),
        )
    )
    return results


def _runtime_checks(config: HarnessConfig) -> Iterable[CheckResult]:
    roles = (
        ("spec_author", config.agents.spec_author),
        ("spec_reviewer", config.agents.spec_reviewer),
        ("implementer", config.agents.implementer),
        ("code_reviewer", config.agents.code_reviewer),
    )
    for role, agent in roles:
        model = getattr(config.models, role) or "CLI default"
        effort = getattr(config.effort, role) or "CLI default"
        yield CheckResult(
            f"Runtime {role}",
            True,
            f"agent={agent}, model={model}, effort={effort}",
        )


def _write_if_missing(path: Path, content: str) -> list[Path]:
    if path.exists():
        return []
    path.write_text(content, encoding="utf-8")
    return [path]


def _ensure_gitignore_entry(root: Path, entry: str) -> list[Path]:
    path = root / ".gitignore"
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = current.splitlines()
    if entry in lines:
        return []
    separator = "" if not current or current.endswith("\n") else "\n"
    path.write_text(f"{current}{separator}{entry}\n", encoding="utf-8")
    return [path]


def _git_repository_check(root: Path) -> CheckResult:
    try:
        result = subprocess.run(
            ("git", "rev-parse", "--show-toplevel"),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return CheckResult("Git repository", False, str(error))

    if result.returncode != 0:
        return CheckResult("Git repository", False, "not inside a Git repository")
    return CheckResult("Git repository", True, result.stdout.strip())


def _file_checks(config: HarnessConfig) -> Iterable[CheckResult]:
    required = [
        config.project_root / "PROJECT_RULES.md",
        *(config.prompts_directory / name for name in PROMPT_FILES),
        *(config.schemas_directory / name for name in SCHEMA_FILES),
    ]
    for path in required:
        if not path.is_file():
            yield CheckResult("Required file", False, f"missing {path}")
            continue
        if path.suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                yield CheckResult("JSON schema", False, f"{path}: {error}")
                continue
        yield CheckResult("Required file", True, str(path.relative_to(config.project_root)))


def _command_check(name: str, executable: str, arguments: tuple[str, ...]) -> CheckResult:
    if shutil.which(executable) is None:
        return CheckResult(name, False, f"`{executable}` was not found on PATH")
    try:
        result = subprocess.run(
            (executable, *arguments),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(name, False, "command timed out")

    raw_output = (result.stdout or result.stderr).strip()
    detail = _summarize_command_output(raw_output, result.returncode)
    return CheckResult(name, result.returncode == 0, detail)


def _summarize_command_output(output: str, return_code: int) -> str:
    if not output:
        return f"exit code {return_code}"
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        return output.splitlines()[-1]
    if isinstance(value, dict):
        return ", ".join(
            f"{key}={json.dumps(item, ensure_ascii=False)}"
            for key, item in value.items()
        )
    return str(value)

