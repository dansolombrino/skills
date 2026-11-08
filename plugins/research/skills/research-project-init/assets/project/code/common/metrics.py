"""Append-only per-run metric log: every tracked scalar, kept offline."""

import datetime
import json
import os
from pathlib import Path


class MetricsWriter:
    """Own evaluations/NNN_exp/<run_id path>/metrics.jsonl for one run.

    One JSON object per line: {"step": <int>, "time": <iso>, <key>: <number>, ...}.
    A fresh run truncates the file; a run that restored its training state
    appends, so steps it repeats after the restored checkpoint are logged twice
    and read_metrics() keeps the last value of each (step, key).
    """

    def __init__(self, eval_dir, *, resume=False):
        self.path = Path(eval_dir) / "metrics.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a" if resume else "w", encoding="utf-8")

    def log(self, metrics, *, step):
        """Record one step's scalars; values are anything float() accepts (tensors included)."""
        if not isinstance(step, int) or isinstance(step, bool) or step < 0:
            raise ValueError("step must be a non-negative int")
        if not metrics:
            raise ValueError("metrics must not be empty")
        record = {
            "step": step,
            "time": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        for key, value in metrics.items():
            if not isinstance(key, str) or not key or key in record:
                raise ValueError(f"bad metric key {key!r}")
            if isinstance(value, bool):
                raise ValueError(f"metric {key} is a bool, not a number")
            record[key] = value if isinstance(value, int) else float(value)
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self):
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def read_metrics(eval_dir) -> dict:
    """{key: [(step, value), ...]} sorted by step; the last value of a repeated step wins.

    A torn last line (the run was killed mid-write) is skipped; NaN and inf are kept.
    """
    path = Path(eval_dir) / "metrics.jsonl"
    latest: dict = {}
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for index, line in enumerate(lines):
        try:
            record = json.loads(line)
        except ValueError:
            if index == len(lines) - 1:
                break
            raise
        step = record["step"]
        for key, value in record.items():
            if key not in ("step", "time"):
                latest.setdefault(key, {})[step] = value
    return {key: sorted(values.items()) for key, values in latest.items()}
