from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any, Optional

from dual_agent_task.errors import HarnessError


def run_claude_structured(
    *,
    prompt: str,
    schema_path: Path,
    project_root: Path,
    timeout_seconds: int,
    tools: tuple[str, ...] = (),
    model: Optional[str] = None,
    effort: Optional[str] = None,
) -> dict[str, Any]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    command = (
        "claude",
        "-p",
        *_claude_runtime_arguments(model=model, effort=effort),
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema, separators=(",", ":")),
        "--permission-mode",
        "plan",
        "--tools",
        ",".join(tools),
        "--no-session-persistence",
    )
    result = _run(command, prompt, project_root, timeout_seconds)
    return _extract_claude_structured_output(result.stdout)


def run_codex_structured(
    *,
    prompt: str,
    schema_path: Path,
    output_path: Path,
    project_root: Path,
    timeout_seconds: int,
    model: Optional[str] = None,
    effort: Optional[str] = None,
) -> dict[str, Any]:
    return _run_codex_structured(
        prompt=prompt,
        schema_path=schema_path,
        output_path=output_path,
        project_root=project_root,
        timeout_seconds=timeout_seconds,
        sandbox="read-only",
        approve_for_me=False,
        model=model,
        effort=effort,
    )


def run_codex_write_structured(
    *,
    prompt: str,
    schema_path: Path,
    output_path: Path,
    project_root: Path,
    timeout_seconds: int,
    model: Optional[str] = None,
    effort: Optional[str] = None,
) -> dict[str, Any]:
    return _run_codex_structured(
        prompt=prompt,
        schema_path=schema_path,
        output_path=output_path,
        project_root=project_root,
        timeout_seconds=timeout_seconds,
        sandbox="workspace-write",
        approve_for_me=True,
        model=model,
        effort=effort,
    )


def _run_codex_structured(
    *,
    prompt: str,
    schema_path: Path,
    output_path: Path,
    project_root: Path,
    timeout_seconds: int,
    sandbox: str,
    approve_for_me: bool,
    model: Optional[str],
    effort: Optional[str],
) -> dict[str, Any]:
    approval_arguments = ("--approve-for-me",) if approve_for_me else ()
    command = (
        "codex",
        "exec",
        *_codex_runtime_arguments(model=model, effort=effort),
        "--ephemeral",
        "--sandbox",
        sandbox,
        *approval_arguments,
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "-",
    )
    _run(command, prompt, project_root, timeout_seconds)
    if not output_path.is_file():
        raise HarnessError(f"Codex did not create structured output at {output_path}")
    try:
        value = json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise HarnessError(f"Codex returned invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise HarnessError("Codex structured output must be a JSON object")
    return value


def _claude_runtime_arguments(
    *, model: Optional[str], effort: Optional[str]
) -> tuple[str, ...]:
    arguments: list[str] = []
    if model:
        arguments.extend(("--model", model))
    if effort:
        arguments.extend(("--effort", effort))
    return tuple(arguments)


def _codex_runtime_arguments(
    *, model: Optional[str], effort: Optional[str]
) -> tuple[str, ...]:
    arguments: list[str] = []
    if model:
        arguments.extend(("--model", model))
    if effort:
        arguments.extend(("--config", f'model_reasoning_effort="{effort}"'))
    return tuple(arguments)


def _run(
    command: tuple[str, ...],
    prompt: str,
    project_root: Path,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("OPENAI_API_KEY", None)
    environment.pop("ANTHROPIC_API_KEY", None)

    try:
        result = subprocess.run(
            command,
            cwd=project_root,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=environment,
            check=False,
        )
    except FileNotFoundError as error:
        raise HarnessError(f"Agent CLI was not found: {command[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise HarnessError(
            f"{command[0]} timed out after {timeout_seconds} seconds"
        ) from error

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise HarnessError(f"{command[0]} failed with exit code {result.returncode}: {detail}")
    return result


def _extract_claude_structured_output(stdout: str) -> dict[str, Any]:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise HarnessError(f"Claude returned invalid JSON: {error}") from error

    candidates: list[Any] = []
    if isinstance(envelope, dict):
        candidates.extend(
            (
                envelope.get("structured_output"),
                envelope.get("result"),
                envelope,
            )
        )
    else:
        candidates.append(envelope)

    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
        if isinstance(candidate, str):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    raise HarnessError("Claude response did not contain a structured JSON object")

