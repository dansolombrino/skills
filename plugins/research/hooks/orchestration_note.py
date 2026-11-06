#!/usr/bin/env python3
"""SessionStart: state this project's working mode, only in a project tagged `research`.

The plugin can end up enabled outside research projects, so the tag decides rather than the
plugin's presence. Any problem means saying nothing: a session must never fail because of this."""
from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path

NOTE = Path(__file__).with_name("orchestration.md")


def is_research_project(cwd: Path) -> bool:
    for directory in (cwd, *cwd.parents):
        tag_file = directory / ".project.toml"
        if tag_file.is_file():
            return "research" in tomllib.loads(tag_file.read_text()).get("categories", [])
    return False


def main() -> None:
    if os.environ.get("SWEEP_SUPERVISOR_MODEL"):  # a supervisor agent works alone: it has no main chat to orchestrate from
        return
    payload = json.load(sys.stdin)
    if is_research_project(Path(payload["cwd"])):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": NOTE.read_text().strip()}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - fail silent, by design
        pass
    sys.exit(0)
