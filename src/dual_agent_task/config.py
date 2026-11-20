from __future__ import annotations

import configparser
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9 and 3.10
    tomllib = None  # type: ignore[assignment]

from dual_agent_task.errors import HarnessError


CONFIG_DIRECTORY = ".dual-agent-task"
CONFIG_FILE = "config.toml"


@dataclass(frozen=True)
class AgentConfig:
    spec_author: str
    spec_reviewer: str
    implementer: str
    code_reviewer: str


@dataclass(frozen=True)
class ModelConfig:
    spec_author: Optional[str]
    spec_reviewer: Optional[str]
    implementer: Optional[str]
    code_reviewer: Optional[str]


@dataclass(frozen=True)
class EffortConfig:
    spec_author: Optional[str]
    spec_reviewer: Optional[str]
    implementer: Optional[str]
    code_reviewer: Optional[str]


@dataclass(frozen=True)
class LimitConfig:
    max_spec_rounds: int
    max_code_rounds: int
    agent_timeout_seconds: int


@dataclass(frozen=True)
class GitConfig:
    require_clean_worktree: bool
    allow_commits: bool
    allow_push: bool


@dataclass(frozen=True)
class HarnessConfig:
    project_root: Path
    agents: AgentConfig
    models: ModelConfig
    effort: EffortConfig
    limits: LimitConfig
    git: GitConfig
    verification_commands: tuple[str, ...]

    @property
    def harness_directory(self) -> Path:
        return self.project_root / CONFIG_DIRECTORY

    @property
    def prompts_directory(self) -> Path:
        return self.harness_directory / "prompts"

    @property
    def schemas_directory(self) -> Path:
        return self.harness_directory / "schemas"

    @property
    def runs_directory(self) -> Path:
        return self.harness_directory / "runs"

    @classmethod
    def load(cls, project_root: Path) -> "HarnessConfig":
        root = project_root.expanduser().resolve()
        path = root / CONFIG_DIRECTORY / CONFIG_FILE
        if not path.is_file():
            raise HarnessError(
                f"Harness is not initialized in {root}. Run `dual-agent-task init {root}`."
            )

        try:
            raw = _load_configuration(path)
            agents = raw["agents"]
            models = raw.get("models", {})
            effort = raw.get("effort", {})
            limits = raw["limits"]
            git = raw["git"]
            verification = raw.get("verification", {})
            config = cls(
                project_root=root,
                agents=AgentConfig(
                    spec_author=str(agents["spec_author"]),
                    spec_reviewer=str(agents["spec_reviewer"]),
                    implementer=str(agents["implementer"]),
                    code_reviewer=str(agents["code_reviewer"]),
                ),
                models=ModelConfig(
                    spec_author=_optional_string(models.get("spec_author")),
                    spec_reviewer=_optional_string(models.get("spec_reviewer")),
                    implementer=_optional_string(models.get("implementer")),
                    code_reviewer=_optional_string(models.get("code_reviewer")),
                ),
                effort=EffortConfig(
                    spec_author=_optional_string(effort.get("spec_author")),
                    spec_reviewer=_optional_string(effort.get("spec_reviewer")),
                    implementer=_optional_string(effort.get("implementer")),
                    code_reviewer=_optional_string(effort.get("code_reviewer")),
                ),
                limits=LimitConfig(
                    max_spec_rounds=int(limits["max_spec_rounds"]),
                    max_code_rounds=int(limits["max_code_rounds"]),
                    agent_timeout_seconds=int(limits["agent_timeout_seconds"]),
                ),
                git=GitConfig(
                    require_clean_worktree=bool(git["require_clean_worktree"]),
                    allow_commits=bool(git["allow_commits"]),
                    allow_push=bool(git["allow_push"]),
                ),
                verification_commands=tuple(
                    str(command) for command in verification.get("commands", [])
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise HarnessError(f"Invalid configuration at {path}: {error}") from error

        if config.limits.max_spec_rounds < 1:
            raise HarnessError("limits.max_spec_rounds must be at least 1")
        if config.limits.max_code_rounds < 1:
            raise HarnessError("limits.max_code_rounds must be at least 1")
        if config.limits.agent_timeout_seconds < 1:
            raise HarnessError("limits.agent_timeout_seconds must be at least 1")
        _validate_effort(config.effort)

        return config


def _optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    string = str(value).strip()
    return string or None


def _validate_effort(effort: EffortConfig) -> None:
    supported = {"low", "medium", "high", "xhigh", "max"}
    for role in ("spec_author", "spec_reviewer", "implementer", "code_reviewer"):
        value = getattr(effort, role)
        if value is not None and value not in supported:
            choices = ", ".join(sorted(supported))
            raise HarnessError(f"effort.{role} must be one of: {choices}")


def _load_configuration(path: Path) -> dict[str, Any]:
    if tomllib is not None:
        try:
            with path.open("rb") as file:
                return tomllib.load(file)
        except tomllib.TOMLDecodeError as error:
            raise HarnessError(f"Invalid configuration at {path}: {error}") from error

    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open("r", encoding="utf-8") as file:
            parser.read_file(file)
    except configparser.Error as error:
        raise HarnessError(f"Invalid configuration at {path}: {error}") from error

    result: dict[str, Any] = {}
    for section in parser.sections():
        values: dict[str, Any] = {}
        for key, raw_value in parser.items(section):
            try:
                values[key] = json.loads(raw_value)
            except json.JSONDecodeError:
                values[key] = raw_value.strip()
        result[section] = values
    return result

