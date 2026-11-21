"""Small dependency-free subprocess streamer used by agents and verification."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from typing import Callable, Mapping, Optional, Sequence, TextIO


LineHandler = Callable[[str, str], None]
HeartbeatHandler = Callable[[float], None]


@dataclass(frozen=True)
class ProcessResult:
    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float


def run_streaming_process(
    command: Sequence[str],
    *,
    cwd: Path,
    input_text: Optional[str] = None,
    timeout_seconds: int,
    environment: Optional[Mapping[str, str]] = None,
    on_line: Optional[LineHandler] = None,
    on_heartbeat: Optional[HeartbeatHandler] = None,
    heartbeat_seconds: float = 15.0,
) -> ProcessResult:
    """Run a process while draining both output streams and emitting heartbeats."""

    started = time.monotonic()
    process = subprocess.Popen(
        tuple(command),
        cwd=cwd,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=dict(environment) if environment is not None else os.environ.copy(),
    )

    if input_text is not None and process.stdin is not None:
        try:
            process.stdin.write(input_text)
            process.stdin.close()
        except BrokenPipeError:
            pass

    messages: "queue.Queue[tuple[str, Optional[str]]]" = queue.Queue()

    def drain(source: str, stream: TextIO) -> None:
        try:
            for line in stream:
                messages.put((source, line.rstrip("\n")))
        finally:
            stream.close()
            messages.put((source, None))

    assert process.stdout is not None
    assert process.stderr is not None
    threads = (
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    )
    for thread in threads:
        thread.start()

    output: dict[str, list[str]] = {"stdout": [], "stderr": []}
    completed_streams = 0
    next_heartbeat = started + heartbeat_seconds
    deadline = started + timeout_seconds
    timed_out = False

    while completed_streams < 2:
        now = time.monotonic()
        if now >= deadline:
            timed_out = True
            break
        wait_until = min(deadline, next_heartbeat)
        try:
            source, line = messages.get(timeout=max(0.01, wait_until - now))
        except queue.Empty:
            now = time.monotonic()
            if now >= next_heartbeat:
                if on_heartbeat is not None:
                    on_heartbeat(now - started)
                next_heartbeat = now + heartbeat_seconds
            continue

        if line is None:
            completed_streams += 1
            continue
        output[source].append(line)
        if on_line is not None:
            on_line(source, line)

    if timed_out:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    else:
        process.wait()

    for thread in threads:
        thread.join(timeout=1)
    while True:
        try:
            source, line = messages.get_nowait()
        except queue.Empty:
            break
        if line is not None:
            output[source].append(line)
            if on_line is not None:
                on_line(source, line)

    stdout = "\n".join(output["stdout"])
    stderr = "\n".join(output["stderr"])
    if stdout:
        stdout += "\n"
    if stderr:
        stderr += "\n"
    duration = time.monotonic() - started

    if timed_out:
        raise subprocess.TimeoutExpired(
            cmd=tuple(command),
            timeout=timeout_seconds,
            output=stdout,
            stderr=stderr,
        )
    return ProcessResult(
        return_code=process.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration,
    )
