"""Atomic per-run lifecycle and progress signaling."""

import datetime
import json
import os
import threading
import time
from numbers import Real
from pathlib import Path


class StatusWriter:
    """Own evaluations/NNN_exp/<run_id path>/.status.json for one run.

    Schema v3 adds ``rig`` (from LANE_RIG, which the claiming lane exports);
    readers treat a missing field in a v2 file as None.
    """

    def __init__(self, eval_dir, heartbeat_interval_s=60):
        if heartbeat_interval_s <= 0:
            raise ValueError("heartbeat_interval_s must be positive")
        self.path = Path(eval_dir) / ".status.json"
        self.heartbeat_interval_s = heartbeat_interval_s
        self.t0 = None
        self.status = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None

    def _now(self):
        return datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    def _elapsed(self):
        return round(time.monotonic() - self.t0, 1)

    def _write_locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.status))
        tmp.replace(self.path)

    def _refresh_liveness_locked(self):
        self.status["heartbeat"] = self._now()
        self.status["elapsed_s"] = self._elapsed()
        self._write_locked()

    def _heartbeat_loop(self):
        while not self._stop.wait(self.heartbeat_interval_s):
            with self._lock:
                if self.status.get("state") != "running":
                    return
                self._refresh_liveness_locked()

    def __enter__(self):
        self.t0 = time.monotonic()
        started = self._now()
        with self._lock:
            self.status = {
                "schema_version": 3,
                "state": "running",
                "started": started,
                "ended": None,
                "elapsed_s": 0.0,
                "heartbeat": started,
                "progress": None,
                "progress_completed": None,
                "progress_total": None,
                "progress_unit": None,
                "wave_id": os.environ.get("WAVE_ID"),
                "gpu": os.environ.get("CUDA_VISIBLE_DEVICES"),
                "rig": os.environ.get("LANE_RIG"),
                "source_revision": os.environ.get("SOURCE_REVISION"),
                "source_tag": os.environ.get("SOURCE_TAG"),
                "environment_fingerprint": os.environ.get("ENVIRONMENT_FINGERPRINT"),
            }
            self._write_locked()
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name="run-status-heartbeat",
            daemon=True,
        )
        self._thread.start()
        print(
            f"[status] RUN START {self.status['started']} "
            f"wave={self.status['wave_id']} rig={self.status['rig']} gpu={self.status['gpu']} "
            f"source={self.status['source_revision']} "
            f"environment={self.status['environment_fingerprint']}",
            flush=True,
        )
        return self

    def heartbeat(self, *, completed=None, total=None, unit=None):
        """Refresh liveness and optionally record machine-readable progress."""
        structured = completed is not None or total is not None or unit is not None
        if structured:
            if not isinstance(completed, Real) or isinstance(completed, bool):
                raise ValueError("completed must be numeric")
            if not isinstance(total, Real) or isinstance(total, bool) or total <= 0:
                raise ValueError("total must be a positive number")
            if completed < 0 or completed > total:
                raise ValueError("completed must be between zero and total")
            if not isinstance(unit, str) or not unit.strip():
                raise ValueError("unit must be a non-empty string")

        with self._lock:
            if structured:
                self.status.update(
                    progress=f"{unit} {completed:g}/{total:g}",
                    progress_completed=completed,
                    progress_total=total,
                    progress_unit=unit,
                )
            self._refresh_liveness_locked()

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            ended = self._now()
            self.status.update(
                state="failed" if exc_type else "done",
                ended=ended,
                heartbeat=ended,
                elapsed_s=self._elapsed(),
            )
            self._write_locked()
        print(
            f"[status] RUN END {self.status['ended']} "
            f"state={self.status['state']} elapsed={self.status['elapsed_s']}s",
            flush=True,
        )
        return False
