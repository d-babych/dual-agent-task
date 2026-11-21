"""Package configuration compatible with the macOS system Python toolchain."""

from pathlib import Path

from setuptools import find_packages, setup


ROOT = Path(__file__).parent


setup(
    name="dual-agent-task",
    version="0.3.0",
    description=(
        "A small local orchestrator for collaborative Codex and Claude Code workflows."
    ),
    long_description=(ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    python_requires=">=3.9",
    package_dir={"": "src"},
    packages=find_packages("src"),
    include_package_data=True,
    package_data={
        "dual_agent_task": [
            "assets/prompts/*.md",
            "assets/schemas/*.json",
            "assets/templates/*",
        ]
    },
    entry_points={
        "console_scripts": [
            "dual-agent-task=dual_agent_task.cli:main",
        ]
    },
)
