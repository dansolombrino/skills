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

    def make_git_fixture(self, base: Path) -> tuple[rigsync.Config, rigsync.Machine, str, str]:
        bare = base / "remote.git"
        hub = base / "hub"
        peer = base / "peer"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(hub)], check=True)
        subprocess.run(["git", "-C", str(hub), "config", "user.name", "Test User"], check=True)
        subprocess.run(["git", "-C", str(hub), "config", "user.email", "test@example.com"], check=True)
        write(hub / ".gitignore", "__pycache__/\n*.pyc\nevaluations/\n")
        write(hub / "code/train.py", "print('v1')\n")
        write(hub / "config/train.yaml", "steps: 1\n")
        write(hub / "scripts/bootstrap.sh", "#!/usr/bin/env bash\n")
        subprocess.run(["git", "-C", str(hub), "add", "."], check=True)
        subprocess.run(["git", "-C", str(hub), "commit", "-qm", "initial"], check=True)
        subprocess.run(["git", "-C", str(hub), "remote", "add", "origin", str(bare)], check=True)
        subprocess.run(["git", "-C", str(hub), "push", "-q", "-u", "origin", "main"], check=True)
        subprocess.run(["git", "clone", "-q", "--branch", "main", str(bare), str(peer)], check=True)
        old_revision = subprocess.run(
            ["git", "-C", str(peer), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()

        write(hub / "code/train.py", "print('v2')\n")
        wave = "20260802-120000"
        subprocess.run(["git", "-C", str(hub), "add", "code/train.py"], check=True)
        subprocess.run(["git", "-C", str(hub), "commit", "-qm", "wave"], check=True)
        revision = subprocess.run(
            ["git", "-C", str(hub), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
        subprocess.run(["git", "-C", str(hub), "tag", "-a", f"wave--{wave}", "-m", "wave"], check=True)
        subprocess.run(["git", "-C", str(hub), "push", "-q", "origin", "main", f"wave--{wave}"], check=True)

        machine = rigsync.Machine("peer", "peer", None, peer, True)
        config = rigsync.Config(
            hub,
            {"evaluations": {"path": "evaluations"}},
            {"peer": machine},
            rigsync.GitSettings("origin", "main"),
        )
        return config, machine, old_revision, revision

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
        script = SCRIPT.read_text()
        self.assertNotIn("--delete", script)
        for command in ('"reset"', '"stash"', '"clean"'):
            self.assertNotIn(command, script)

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

    def test_load_config_parses_git_deployment_settings(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config, registry = self.make_config(root, root / "peer")
            original = config.read_text()
            config.write_text(original + '\n[git]\nremote = "origin"\nbranch = "main"\n')
            parsed = rigsync.load_config(root, config, registry)
            self.assertEqual(parsed.git, rigsync.GitSettings("origin", "main"))

    def test_deploy_revision_dry_run_then_fast_forwards_exact_tagged_commit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, machine, old_revision, revision = self.make_git_fixture(Path(raw))
            wave = "20260802-120000"
            rigsync.deploy_revision(config, [machine], wave, revision, dry_run=True, confirmed=False)
            self.assertEqual(rigsync.git_command(machine, "rev-parse", "HEAD").stdout.decode().strip(), old_revision)
            rigsync.deploy_revision(config, [machine], wave, revision, dry_run=False, confirmed=True)
            self.assertEqual(rigsync.git_command(machine, "rev-parse", "HEAD").stdout.decode().strip(), revision)
            rigsync.verify_revision(config, [machine], wave, revision)

    def test_prepare_git_project_clones_only_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            config, _peer, _old_revision, _revision = self.make_git_fixture(base)
            destination = base / "new-peer"
            machine = rigsync.Machine("new-peer", "new-peer", None, destination, True)
            rigsync.prepare(config, machine, dry_run=True, confirmed=False)
            self.assertFalse(destination.exists())
            rigsync.prepare(config, machine, dry_run=False, confirmed=True)
            self.assertTrue((destination / ".git").is_dir())

    def test_deploy_revision_rejects_dirty_execution_tree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, machine, _old_revision, revision = self.make_git_fixture(Path(raw))
            write(machine.repo_path / "code/train.py", "print('dirty')\n")
            with self.assertRaisesRegex(rigsync.RigSyncError, "execution tree dirty"):
                rigsync.deploy_revision(
                    config, [machine], "20260802-120000", revision, dry_run=True, confirmed=False
                )

    def test_deploy_revision_restores_missing_tag_at_existing_revision(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, machine, _old_revision, revision = self.make_git_fixture(Path(raw))
            wave = "20260802-120000"
            rigsync.deploy_revision(config, [machine], wave, revision, dry_run=False, confirmed=True)
            rigsync.git_command(machine, "tag", "-d", f"wave--{wave}")
            rigsync.deploy_revision(config, [machine], wave, revision, dry_run=False, confirmed=True)
            tag_revision = rigsync.git_command(
                machine, "rev-parse", f"refs/tags/wave--{wave}^{{commit}}"
            ).stdout.decode().strip()
            self.assertEqual(tag_revision, revision)

    def test_deploy_revision_rejects_active_work_before_revision_change(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, machine, _old_revision, revision = self.make_git_fixture(Path(raw))
            with mock.patch.object(rigsync, "project_activity", return_value=["tmux:active"]):
                with self.assertRaisesRegex(rigsync.RigSyncError, "active work blocks"):
                    rigsync.deploy_revision(
                        config, [machine], "20260802-120000", revision, dry_run=True, confirmed=False
                    )

    def test_project_activity_fails_closed_when_tmux_probe_breaks(self) -> None:
        machine = rigsync.Machine("peer", "peer", None, Path("/tmp/peer"), False)
        config = rigsync.Config(
            Path("/tmp/project"), {"evaluations": {"path": "evaluations"}}, {"peer": machine}
        )
        failed = subprocess.CompletedProcess([], 127, b"", b"tmux missing")
        with mock.patch.object(rigsync, "remote", return_value=failed):
            with self.assertRaisesRegex(rigsync.RigSyncError, "cannot inspect tmux"):
                rigsync.project_activity(config, machine)

    def test_verify_revision_rejects_wrong_tag(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, machine, _old_revision, revision = self.make_git_fixture(Path(raw))
            rigsync.git_command(machine, "fetch", "origin", "main")
            rigsync.git_command(machine, "merge", "--ff-only", revision)
            rigsync.git_command(machine, "tag", "-d", "wave--20260802-120000", check=False)
            with self.assertRaisesRegex(rigsync.RigSyncError, "verification failed"):
                rigsync.verify_revision(config, [machine], "20260802-120000", revision)

    def test_execution_drift_allows_runtime_caches_but_rejects_ignored_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, machine, _old_revision, _revision = self.make_git_fixture(Path(raw))
            write(machine.repo_path / "code/__pycache__/train.pyc", "cache")
            self.assertEqual(rigsync.execution_drift(machine, ["code"]), [])
            with (machine.repo_path / ".gitignore").open("a") as handle:
                handle.write("code/local_only.py\n")
            write(machine.repo_path / "code/local_only.py", "SECRET = 1\n")
            drift = rigsync.execution_drift(machine, ["code"])
            self.assertTrue(any("local_only.py" in entry for entry in drift))

    def test_environment_contract_files_are_revision_guarded(self) -> None:
        for path in (".python-version", "pyproject.toml", "uv.toml", "uv.lock", "sync.toml"):
            self.assertIsNotNone(rigsync.DEPENDENCY_FILE_RE.fullmatch(path), path)


if __name__ == "__main__":
    unittest.main()
