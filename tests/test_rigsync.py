from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock
from contextlib import redirect_stdout


SCRIPT = (
    Path(__file__).parents[1]
    / "plugins/research/skills/rig-sync/scripts/rigsync.py"
)
SPEC = importlib.util.spec_from_file_location("rigsync", SCRIPT)
assert SPEC and SPEC.loader
rigsync = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rigsync
SPEC.loader.exec_module(rigsync)


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(value).lstrip())


class RigSyncTests(unittest.TestCase):
    def make_config(self, root: Path, destination: Path) -> tuple[Path, Path]:
        config = root / "sync.toml"
        registry = root / "machines.toml"
        write(
            config,
            f"""
            version = 1
            [artifacts.evaluations]
            path = "evaluations"
            depth = 2
            [machines.test-host]
            repo_path = {str(destination)!r}
            """,
        )
        write(
            registry,
            """
            [machines.test-host]
            ssh = "test-host"
            hostname = "test-host"
            """,
        )
        return config, registry

    def test_load_config_recognizes_local_machine_by_hostname(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config, registry = self.make_config(root, root / "peer")
            with mock.patch.object(rigsync.socket, "gethostname", return_value="test-host"):
                parsed = rigsync.load_config(root, config, registry)
            self.assertTrue(parsed.machines["test-host"].local)

    def test_canonical_machine_name_recognizes_local_despite_stale_hostname(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config, registry = self.make_config(root, root / "peer")
            write(
                registry,
                """
                [machines.test-host]
                ssh = "test-host"
                hostname = "legacy-host"
                """,
            )
            with mock.patch.object(rigsync.socket, "gethostname", return_value="test-host"):
                parsed = rigsync.load_config(root, config, registry)
            self.assertTrue(parsed.machines["test-host"].local)

    def test_source_manifest_excludes_ignored_and_git_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            write(root / ".gitignore", ".env\nlogs/\n")
            write(root / "tracked.txt", "tracked")
            write(root / "untracked.txt", "untracked")
            write(root / ".env", "secret")
            write(root / "logs/run.log", "ignored")
            subprocess.run(["git", "-C", str(root), "add", "tracked.txt", ".gitignore"], check=True)
            self.assertEqual(
                rigsync.source_manifest(root),
                [".gitignore", "tracked.txt", "untracked.txt"],
            )

    def test_selector_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config, registry = self.make_config(root, root / "peer")
            with mock.patch.object(rigsync.socket, "gethostname", return_value="test-host"):
                parsed = rigsync.load_config(root, config, registry)
            with self.assertRaises(rigsync.RigSyncError):
                rigsync.parse_selector(parsed, "evaluations/../../escape")

    def test_local_push_source_is_additive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            root = base / "source"
            destination = base / "destination"
            root.mkdir()
            destination.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            write(root / ".gitignore", "logs/\n")
            write(root / "source.txt", "new")
            write(destination / "keep.txt", "keep")
            config, registry = self.make_config(root, destination)
            with mock.patch.object(rigsync.socket, "gethostname", return_value="test-host"):
                parsed = rigsync.load_config(root, config, registry)
            rigsync.push_source(
                parsed, parsed.machines["test-host"], dry_run=False, confirmed=True
            )
            self.assertEqual((destination / "source.txt").read_text(), "new")
            self.assertEqual((destination / "keep.txt").read_text(), "keep")
            self.assertFalse((destination / ".git").exists())

    def test_source_contains_no_delete_flag(self) -> None:
        self.assertNotIn("--delete", SCRIPT.read_text())

    def test_peer_rsync_uses_bounded_ssh_and_itemized_dry_run(self) -> None:
        machine = rigsync.Machine(
            "peer", "peer-alias", "peer-host", Path("/tmp/peer"), False
        )
        config = rigsync.Config(Path("/tmp/source"), {"evaluations": {"path": "evaluations"}}, {"peer": machine})
        completed = subprocess.CompletedProcess([], 0, b">f+++++++++ source.txt\n", b"")
        with mock.patch.object(rigsync, "source_manifest", return_value=["source.txt"]), mock.patch.object(
            rigsync, "run", return_value=completed
        ) as run_mock:
            rigsync.push_source(config, machine, dry_run=True, confirmed=False)
        command = run_mock.call_args.args[0]
        self.assertIn("--itemize-changes", command)
        self.assertIn("--dry-run", command)
        self.assertIn("-e", command)
        self.assertIn("BatchMode=yes", command[command.index("-e") + 1])
        self.assertIn("ConnectTimeout=10", command[command.index("-e") + 1])
        self.assertTrue(run_mock.call_args.kwargs["show"])

    def test_visible_subprocess_output_is_emitted(self) -> None:
        completed = subprocess.CompletedProcess([], 0, b">f+++++++++ source.txt\n", b"")
        stream = io.StringIO()
        with mock.patch.object(rigsync.subprocess, "run", return_value=completed), redirect_stdout(stream):
            rigsync.run(["rsync", "--dry-run"], show=True)
        self.assertIn("source.txt", stream.getvalue())

    def test_inventory_failure_is_not_reported_as_empty(self) -> None:
        machine = rigsync.Machine("peer", "peer", None, Path("/tmp/missing"), False)
        config = rigsync.Config(Path("/tmp/source"), {"evaluations": {"path": "evaluations"}}, {"peer": machine})
        failed = subprocess.CompletedProcess([], 1, b"", b"permission denied")
        with mock.patch.object(rigsync, "remote", return_value=failed):
            with self.assertRaisesRegex(rigsync.RigSyncError, "inventory failed"):
                rigsync.list_group(config, machine, "evaluations")

    def test_doctor_rejects_missing_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            missing = base / "not-created"
            machine = rigsync.Machine("local", "local", None, missing, True)
            config = rigsync.Config(base, {"evaluations": {"path": "evaluations"}}, {"local": machine})
            with self.assertRaisesRegex(rigsync.RigSyncError, "doctor failed"):
                rigsync.doctor(config, [machine])

    def test_doctor_accepts_matching_hostname(self) -> None:
        machine = rigsync.Machine(
            "peer", "peer-alias", "peer-host", Path("/tmp/peer"), False
        )
        config = rigsync.Config(
            Path("/tmp/source"), {"evaluations": {"path": "evaluations"}}, {"peer": machine}
        )
        hostname = subprocess.CompletedProcess([], 0, b"peer-host\n", b"")
        dependencies = subprocess.CompletedProcess([], 0, b"", b"")
        with mock.patch.object(rigsync, "remote", side_effect=[hostname, dependencies]):
            rigsync.doctor(config, [machine])

    def test_doctor_rejects_hostname_drift(self) -> None:
        machine = rigsync.Machine(
            "peer", "peer-alias", "old-host", Path("/tmp/peer"), False
        )
        config = rigsync.Config(
            Path("/tmp/source"), {"evaluations": {"path": "evaluations"}}, {"peer": machine}
        )
        hostname = subprocess.CompletedProcess([], 0, b"new-host\n", b"")
        dependencies = subprocess.CompletedProcess([], 0, b"", b"")
        stream = io.StringIO()
        with mock.patch.object(
            rigsync, "remote", side_effect=[hostname, dependencies]
        ), redirect_stdout(stream):
            with self.assertRaisesRegex(rigsync.RigSyncError, "doctor failed"):
                rigsync.doctor(config, [machine])
        self.assertIn("expected old-host, got new-host", stream.getvalue())

    def test_doctor_preserves_entries_without_hostname(self) -> None:
        machine = rigsync.Machine("peer", "peer-alias", None, Path("/tmp/peer"), False)
        config = rigsync.Config(
            Path("/tmp/source"), {"evaluations": {"path": "evaluations"}}, {"peer": machine}
        )
        dependencies = subprocess.CompletedProcess([], 0, b"", b"")
        with mock.patch.object(rigsync, "remote", return_value=dependencies) as remote_mock:
            rigsync.doctor(config, [machine])
        remote_mock.assert_called_once()

    def test_write_requires_confirmation_after_dry_run(self) -> None:
        machine = rigsync.Machine("peer", "peer", None, Path("/tmp/peer"), False)
        config = rigsync.Config(Path("/tmp/source"), {"evaluations": {"path": "evaluations"}}, {"peer": machine})
        with self.assertRaisesRegex(rigsync.RigSyncError, "--confirm"):
            rigsync.push_source(config, machine, dry_run=False, confirmed=False)


if __name__ == "__main__":
    unittest.main()
