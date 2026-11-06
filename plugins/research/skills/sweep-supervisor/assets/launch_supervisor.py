#!/usr/bin/env python3
"""Start the newest installed sweep-supervisor whose own `check` passes.

Installed copies live in versioned directories that the hosts delete some time after a newer version
arrives, so a service unit must not name one directly. Copy this file somewhere stable and point the
unit at it; the supervisor's own self-update then takes over from whichever copy this one starts.

Every argument is passed on to the supervisor, for example `launch_supervisor.py serve`.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

CACHES = (".claude/plugins/cache", ".codex/plugins/cache")
SCRIPT = "skills/sweep-supervisor/scripts/supervisor.py"
VERSION_RE = re.compile(r"\d+(?:\.\d+)+")


def installed_copies(home: Path) -> list[Path]:
    """Every installed supervisor.py, newest version first."""
    found: list[tuple[tuple[int, ...], Path]] = []
    for cache in CACHES:
        for version_dir in (home / cache).glob("*/research/*"):
            script = version_dir / SCRIPT
            if VERSION_RE.fullmatch(version_dir.name) and script.is_file():
                found.append((tuple(int(part) for part in version_dir.name.split(".")), script))
    return [script for _, script in sorted(found, key=lambda item: item[0], reverse=True)]


def main() -> int:
    copies = installed_copies(Path.home())
    if not copies:
        print(f"launch_supervisor: no installed research plugin under {' or '.join(CACHES)} in {Path.home()}", file=sys.stderr)
        return 1
    for script in copies:
        probe = subprocess.run([sys.executable, str(script), "check"], capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            print(f"launch_supervisor: starting {script}", flush=True)
            os.execv(sys.executable, [sys.executable, str(script), *sys.argv[1:]])
        print(f"launch_supervisor: skipping {script}: its check failed: {probe.stderr.strip() or probe.stdout.strip()}", file=sys.stderr)
    print("launch_supervisor: no installed copy passes its check", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
