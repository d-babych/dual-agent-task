from importlib.resources import files


def asset_text(relative_path: str) -> str:
    return files("dual_agent_task").joinpath("assets", relative_path).read_text(
        encoding="utf-8"
    )

