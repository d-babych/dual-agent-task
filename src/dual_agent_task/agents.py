from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Optional

from dual_agent_task.errors import HarnessError
from dual_agent_task.process_runner import run_streaming_process
from dual_agent_task.progress import summarize_claude_event, summarize_codex_event


ActivityHandler = Callable[[str], None]
HeartbeatHandler = Callable[[float], None]
RawHandler = Callable[[str, str], None]


def run_claude_structured(
    *,
    prompt: str,
    schema_path: Path,
    project_root: Path,
    timeout_seconds: int,
    tools: tuple[str, ...] = (),
    model: Optional[str] = None,
    effort: Optional[str] = None,
    raw_output_path: Optional[Path] = None,
    on_activity: Optional[ActivityHandler] = None,
    on_heartbeat: Optional[HeartbeatHandler] = None,
    on_raw: Optional[RawHandler] = None,
) -> dict[str, Any]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    captured_stdout: list[str] = []
    command = (
        "claude",
        "-p",
        *_claude_runtime_arguments(model=model, effort=effort),
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-hook-events",
        "--json-schema",
        json.dumps(schema, separators=(",", ":")),
        "--permission-mode",
        "plan",
        "--tools",
        ",".join(tools),
        "--no-session-persistence",
    )

    def handle_line(source: str, line: str) -> None:
        if source == "stdout":
            captured_stdout.append(line)
        if on_raw is not None:
            on_raw(source, line)
        if source == "stdout" and on_activity is not None:
            for activity in summarize_claude_event(line):
                on_activity(activity)

    try:
        result = _run(
            command,
            prompt,
            project_root,
            timeout_seconds,
            on_line=handle_line,
            on_heartbeat=on_heartbeat,
        )
    except HarnessError:
        _write_captured_output(raw_output_path, captured_stdout)
        raise
    if raw_output_path is not None:
        raw_output_path.write_text(result.stdout, encoding="utf-8")
    required = tuple(str(key) for key in schema.get("required", []))
    return _extract_claude_structured_output(result.stdout, required_keys=required)


def run_codex_structured(
    *,
    prompt: str,
    schema_path: Path,
    output_path: Path,
    project_root: Path,
    timeout_seconds: int,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    on_activity: Optional[ActivityHandler] = None,
    on_heartbeat: Optional[HeartbeatHandler] = None,
    on_raw: Optional[RawHandler] = None,
    raw_output_path: Optional[Path] = None,
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
        on_activity=on_activity,
        on_heartbeat=on_heartbeat,
        on_raw=on_raw,
        raw_output_path=raw_output_path,
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
    on_activity: Optional[ActivityHandler] = None,
    on_heartbeat: Optional[HeartbeatHandler] = None,
    on_raw: Optional[RawHandler] = None,
    raw_output_path: Optional[Path] = None,
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
        on_activity=on_activity,
        on_heartbeat=on_heartbeat,
        on_raw=on_raw,
        raw_output_path=raw_output_path,
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
    on_activity: Optional[ActivityHandler],
    on_heartbeat: Optional[HeartbeatHandler],
    on_raw: Optional[RawHandler],
    raw_output_path: Optional[Path],
) -> dict[str, Any]:
    command = (
        "codex",
        "exec",
        *_codex_runtime_arguments(model=model, effort=effort),
        "--ephemeral",
        "--json",
        *_codex_permission_arguments(
            sandbox=sandbox,
            approve_for_me=approve_for_me,
        ),
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "-",
    )
    captured_stdout: list[str] = []

    def handle_line(source: str, line: str) -> None:
        if source == "stdout":
            captured_stdout.append(line)
        if on_raw is not None:
            on_raw(source, line)
        if source == "stdout" and on_activity is not None:
            activity = summarize_codex_event(line)
            if activity is not None:
                on_activity(activity)

    try:
        result = _run(
            command,
            prompt,
            project_root,
            timeout_seconds,
            on_line=handle_line,
            on_heartbeat=on_heartbeat,
        )
    except HarnessError:
        _write_captured_output(raw_output_path, captured_stdout)
        raise
    if raw_output_path is not None:
        raw_output_path.write_text(result.stdout, encoding="utf-8")
    if not output_path.is_file():
        raise HarnessError(f"Codex did not create structured output at {output_path}")
    try:
        value = json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise HarnessError(f"Codex returned invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise HarnessError("Codex structured output must be a JSON object")
    return value


def _write_captured_output(path: Optional[Path], lines: list[str]) -> None:
    if path is not None:
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


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


def _codex_permission_arguments(
    *, sandbox: str, approve_for_me: bool
) -> tuple[str, ...]:
    if approve_for_me:
        # In Codex CLI, --approve-for-me already selects the workspace-write
        # sandbox and conflicts with an explicit --sandbox argument.
        return ("--approve-for-me",)
    return ("--sandbox", sandbox)


def _run(
    command: tuple[str, ...],
    prompt: str,
    project_root: Path,
    timeout_seconds: int,
    on_line: Optional[RawHandler] = None,
    on_heartbeat: Optional[HeartbeatHandler] = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("OPENAI_API_KEY", None)
    environment.pop("ANTHROPIC_API_KEY", None)

    try:
        streamed = run_streaming_process(
            command,
            cwd=project_root,
            input_text=prompt,
            timeout_seconds=timeout_seconds,
            environment=environment,
            on_line=on_line,
            on_heartbeat=on_heartbeat,
        )
    except FileNotFoundError as error:
        raise HarnessError(f"Agent CLI was not found: {command[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise HarnessError(
            f"{command[0]} timed out after {timeout_seconds} seconds"
        ) from error

    if streamed.return_code != 0:
        detail = streamed.stderr.strip() or streamed.stdout.strip() or "no output"
        raise HarnessError(
            f"{command[0]} failed with exit code {streamed.return_code}: {detail}"
        )
    return subprocess.CompletedProcess(
        command,
        streamed.return_code,
        stdout=streamed.stdout,
        stderr=streamed.stderr,
    )


def _extract_claude_structured_output(
    stdout: str, *, required_keys: tuple[str, ...] = ()
) -> dict[str, Any]:
    envelope = _decode_claude_output(stdout)

    structured = _find_structured_object(envelope, required_keys=required_keys)
    if structured is not None:
        return structured
    if isinstance(envelope, dict) and isinstance(envelope.get("result"), str):
        review = _parse_prose_review(
            envelope["result"],
            required_keys=required_keys,
        )
        if review is not None:
            return review
    keys = sorted(envelope.keys()) if isinstance(envelope, dict) else []
    raise HarnessError(
        "Claude response did not contain the required structured object; "
        f"required={list(required_keys)}, envelope_keys={keys}"
    )


def _decode_claude_output(stdout: str) -> Any:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as single_error:
        events: list[Any] = []
        for line in stdout.splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise HarnessError(f"Claude returned invalid JSON stream: {error}") from error
        if not events:
            raise HarnessError(f"Claude returned invalid JSON: {single_error}") from single_error
        for event in reversed(events):
            if isinstance(event, dict) and event.get("type") == "result":
                return event
        return events[-1]


def _find_structured_object(
    value: Any, *, required_keys: tuple[str, ...]
) -> Optional[dict[str, Any]]:
    if isinstance(value, dict):
        if required_keys and all(key in value for key in required_keys):
            return value
        preferred = ("structured_output", "result", "response", "data", "content")
        for key in preferred:
            if key in value:
                found = _find_structured_object(value[key], required_keys=required_keys)
                if found is not None:
                    return found
        if not required_keys and not {"type", "subtype", "session_id"}.intersection(value):
            return value
        for key, nested in value.items():
            if key not in preferred:
                found = _find_structured_object(nested, required_keys=required_keys)
                if found is not None:
                    return found
        return None
    if isinstance(value, list):
        for item in value:
            found = _find_structured_object(item, required_keys=required_keys)
            if found is not None:
                return found
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1]).strip()
        candidates = [text]
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            candidates.append(text[first_brace : last_brace + 1])
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            found = _find_structured_object(parsed, required_keys=required_keys)
            if found is not None:
                return found
    return None


def _parse_prose_review(
    text: str, *, required_keys: tuple[str, ...]
) -> Optional[dict[str, Any]]:
    review_keys = {"verdict", "summary", "findings", "verification_assessment"}
    if not review_keys.issubset(set(required_keys)):
        return None
    match = re.search(
        r"(?im)verdict\s*:\s*[`*_\s]*(approved|changes_requested|needs_user_input)",
        text,
    )
    if match is None:
        return None
    verdict = match.group(1).lower()
    summary = _first_prose_summary(text, match.end())
    findings: list[dict[str, Any]] = []
    if verdict != "approved":
        findings.append(
            {
                "id": "CLAUDE-REVIEW-1",
                "severity": "blocking",
                "path": None,
                "line": None,
                "issue": text.strip(),
                "expected_fix": "Address the reviewer feedback in full.",
            }
        )
    verification_line = next(
        (
            line.strip()
            for line in text.splitlines()
            if "build passed" in line.lower() or "verification" in line.lower()
        ),
        "See the prose review summary.",
    )
    return {
        "verdict": verdict,
        "summary": summary,
        "findings": findings,
        "verification_assessment": verification_line,
    }


def _first_prose_summary(text: str, start: int) -> str:
    for line in text[start:].splitlines():
        candidate = line.strip().strip("#-* `_\t")
        if candidate and candidate.lower() not in {"review findings", "findings"}:
            return candidate
    return "Claude completed a prose code review."

