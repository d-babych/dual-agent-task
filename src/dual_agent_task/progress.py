"""Terminal progress rendering and event summarization."""

from __future__ import annotations

from datetime import datetime
import json
import sys
from typing import Callable, Optional, TextIO


class ProgressReporter:
    def start(self, message: str) -> None:
        raise NotImplementedError

    def activity(self, message: str) -> None:
        raise NotImplementedError

    def heartbeat(self, elapsed_seconds: float) -> None:
        raise NotImplementedError

    def finish(self, message: str, *, passed: bool = True) -> None:
        raise NotImplementedError

    def info(self, message: str) -> None:
        raise NotImplementedError

    def raw(self, source: str, line: str) -> None:
        raise NotImplementedError


class ConsoleProgress(ProgressReporter):
    """Timestamped compact output by default; raw output only in verbose mode."""

    def __init__(self, mode: str = "live", stream: Optional[TextIO] = None) -> None:
        self.mode = mode
        self.stream = stream or sys.stdout

    def start(self, message: str) -> None:
        self._write(message)

    def activity(self, message: str) -> None:
        if self.mode not in {"quiet", "verbose"} and message:
            self._write(f"      {message}")

    def heartbeat(self, elapsed_seconds: float) -> None:
        if self.mode != "quiet":
            self._write(f"      Still working… {format_duration(elapsed_seconds)}")

    def finish(self, message: str, *, passed: bool = True) -> None:
        marker = "✓" if passed else "✗"
        if self.mode == "plain":
            marker = "PASS" if passed else "FAIL"
        self._write(f"{marker}     {message}")

    def info(self, message: str) -> None:
        if self.mode != "quiet":
            self._write(message)

    def raw(self, source: str, line: str) -> None:
        if self.mode == "verbose" and line:
            self._write(f"      [{source}] {line}")

    def _write(self, message: str) -> None:
        if self.mode == "quiet":
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"{timestamp} {message}", file=self.stream, flush=True)


class CallbackProgress(ProgressReporter):
    """Compatibility adapter for workflow callers that pass a simple callback."""

    def __init__(self, callback: Callable[[str], None]) -> None:
        self.callback = callback

    def start(self, message: str) -> None:
        self.callback(message)

    def activity(self, message: str) -> None:
        if message:
            self.callback(message)

    def heartbeat(self, elapsed_seconds: float) -> None:
        self.callback(f"Still working… {format_duration(elapsed_seconds)}")

    def finish(self, message: str, *, passed: bool = True) -> None:
        self.callback(message)

    def info(self, message: str) -> None:
        self.callback(message)

    def raw(self, source: str, line: str) -> None:
        return


def format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    minutes, remaining = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{remaining:02d}"
    return f"{minutes:02d}:{remaining:02d}"


def summarize_codex_event(line: str) -> Optional[str]:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    item = event.get("item")
    if not isinstance(item, dict):
        return None
    item_type = str(item.get("type", ""))
    if item_type in {"command_execution", "command"}:
        command = item.get("command") or item.get("text")
        return f"Running {compact(str(command))}" if command else "Running a command"
    if item_type in {"file_change", "file_changes"}:
        changes = item.get("changes")
        if isinstance(changes, list):
            paths = [str(change.get("path")) for change in changes if isinstance(change, dict)]
            paths = [path for path in paths if path and path != "None"]
            if paths:
                return f"Editing {', '.join(paths[:3])}"
        path = item.get("path")
        return f"Editing {path}" if path else "Editing project files"
    if item_type in {"mcp_tool_call", "tool_call"}:
        name = item.get("tool") or item.get("name")
        return f"Using {name}" if name else "Using a tool"
    return None


def summarize_claude_event(line: str) -> list[str]:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return []
    if not isinstance(event, dict) or event.get("type") != "assistant":
        return []
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    result: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = str(block.get("name", "Tool"))
        inputs = block.get("input")
        detail = ""
        if isinstance(inputs, dict):
            for key in ("file_path", "path", "pattern", "query"):
                if inputs.get(key):
                    detail = f" {compact(str(inputs[key]))}"
                    break
        result.append(f"{name}{detail}")
    return result


def summarize_verification_line(line: str) -> Optional[str]:
    stripped = line.strip()
    if not stripped:
        return None
    lowered = stripped.lower()
    signals = (
        "error:",
        "warning:",
        "build succeeded",
        "build failed",
        "test suite",
        "testing started",
        "tests passed",
        "tests failed",
    )
    if any(signal in lowered for signal in signals):
        return compact(stripped, limit=180)
    return None


def compact(value: str, limit: int = 120) -> str:
    one_line = " ".join(value.split())
    if len(one_line) <= limit:
        return one_line
    return f"{one_line[: limit - 1]}…"
