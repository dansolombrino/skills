"""Stable project-environment fingerprinting (standard library only)."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
from pathlib import Path


SCHEMA_VERSION = 1


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def environment_payload(lock_path: Path) -> dict:
    packages: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        raw_name = distribution.metadata.get("Name")
        if not raw_name:
            raise RuntimeError("installed distribution has no Name metadata")
        name = canonical_name(raw_name)
        version = distribution.version
        previous = packages.get(name)
        if previous is not None:
            raise RuntimeError(
                f"multiple installed versions for {name}: {previous!r} and {version!r}"
            )
        packages[name] = version
    return {
        "schema": SCHEMA_VERSION,
        "lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "packages": sorted(packages.items()),
    }


def fingerprint(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fingerprint", "report"))
    parser.add_argument("--lock", type=Path, required=True)
    args = parser.parse_args()
    payload = environment_payload(args.lock)
    if args.command == "report":
        print(json.dumps({"fingerprint": fingerprint(payload), **payload}, sort_keys=True))
    else:
        print(fingerprint(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
