# Repository instructions

## Purpose

This repository contains `dual-agent-task`, a local Python orchestrator for
Codex and Claude Code.

## Development rules

- Keep the runtime dependency-free unless a dependency has clear value.
- Support Python 3.9 and newer.
- Never invoke live agents from automated tests.
- Keep prompts and JSON schemas editable after project initialization.
- Do not create commits or push from the harness.
- Prefer structured outputs over parsing prose.

## Verification

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

