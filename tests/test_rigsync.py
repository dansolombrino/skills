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


class StorageCheckTests(unittest.TestCase):
    # Columns are 1K blocks: filesystem, used, soft, hard, grace, files, ...
    QUOTA = (
        "Disk quotas for user someone (uid 1000): \n"
        "     Filesystem  blocks   quota   limit   grace   files   quota   limit   grace\n"
        " /dev/small 1210404  99614720 104857600           17504       0       0        \n"
        " /dev/large 788600360  996147200 1073741824          293842       0       0        \n"
    )

    def machine(self, **kwargs) -> rigsync.Machine:
        defaults = dict(
            name="peer",
            ssh="peer-alias",
            hostname=None,
            repo_path=Path("/large/projects/thing"),
            local=False,
            storage_root=Path("/large"),
            quota_fs="/dev/small",
            min_free_gb=50.0,
        )
        defaults.update(kwargs)
        return rigsync.Machine(**defaults)

    def run_check(self, machine, resolved: str, quota: str | None):
        """Drive check_storage with canned readlink/quota/df output."""
        responses = [subprocess.CompletedProcess([], 0, resolved.encode(), b"")]
        if quota is None:
            responses.append(subprocess.CompletedProcess([], 1, b"", b"no quota"))
            responses.append(
                subprocess.CompletedProcess(
                    [],
                    0,
                    b"Filesystem 1024-blocks Used Available Capacity Mounted\n"
                    b"/dev/large 3748905484 1233908892 2514996592 33% /large\n",
                    b"",
                )
            )
        else:
            responses.append(subprocess.CompletedProcess([], 0, quota.encode(), b""))
        with mock.patch.object(rigsync, "remote", side_effect=responses):
            return rigsync.check_storage(machine)

    def test_parses_quota_columns_not_df(self) -> None:
        free, allowance = rigsync._parse_quota(self.QUOTA, "/dev/small")
        self.assertAlmostEqual(allowance / rigsync.KIB_PER_GB, 100.0, places=1)
        self.assertAlmostEqual(free / rigsync.KIB_PER_GB, 98.8, places=1)

    def test_zero_limits_mean_unlimited_not_zero_headroom(self) -> None:
        self.assertIsNone(rigsync._parse_quota("/dev/x 100 0 0 - 5 0 0", "/dev/x"))

    def test_soft_limit_is_the_fallback_when_no_hard_limit(self) -> None:
        self.assertEqual(rigsync._parse_quota("/dev/x 100 500 0 - 5 0 0", "/dev/x"), (400, 500))

    def test_exceeded_soft_limit_star_is_stripped(self) -> None:
        free, _ = rigsync._parse_quota("/dev/x 99000000* 95000000 100000000 7days 5 0 0", "/dev/x")
        self.assertEqual(free, 1000000)

    def test_usage_past_hard_limit_clamps_to_zero(self) -> None:
        free, _ = rigsync._parse_quota("/dev/x 110000000* 95000000 100000000 none 5 0 0", "/dev/x")
        self.assertEqual(free, 0)

    def test_unparseable_input_is_none_not_a_crash(self) -> None:
        self.assertIsNone(rigsync._parse_quota("junk", "/dev/x"))
        self.assertIsNone(rigsync._parse_df("no header"))

    def test_machines_without_storage_root_are_skipped(self) -> None:
        failures, lines = rigsync.check_storage(self.machine(storage_root=None))
        self.assertEqual((failures, lines), (0, []))

    def test_repo_path_outside_storage_root_fails(self) -> None:
        # The exact shape of the incident: repo declared on the small volume.
        failures, lines = self.run_check(
            self.machine(repo_path=Path("/home/someone/projects/thing")),
            "/home/someone/projects/thing\n/large\n",
            self.QUOTA,
        )
        self.assertEqual(failures, 1)
        self.assertTrue(any("outside declared storage_root" in line for line in lines))

    def test_symlink_into_storage_root_passes(self) -> None:
        # ~/projects -> /large/projects must not be reported as misplaced.
        failures, lines = self.run_check(
            self.machine(repo_path=Path("/home/someone/projects/thing")),
            "/large/projects/thing\n/large\n",
            self.QUOTA,
        )
        self.assertEqual(failures, 0)
        self.assertTrue(any("storage root" in line and line.startswith("OK") for line in lines))

    def test_headroom_below_floor_fails(self) -> None:
        failures, lines = self.run_check(
            self.machine(min_free_gb=99.0), "/large/projects/thing\n/large\n", self.QUOTA
        )
        self.assertEqual(failures, 1)
        self.assertTrue(any(line.startswith("FAIL") and "storage free" in line for line in lines))

    def test_falls_back_to_df_when_quota_unavailable(self) -> None:
        failures, lines = self.run_check(
            self.machine(), "/large/projects/thing\n/large\n", None
        )
        self.assertEqual(failures, 0)
        self.assertTrue(any("via df(" in line for line in lines))
        self.assertTrue(any(line.startswith("WARN") for line in lines))


class MachineEnvTests(unittest.TestCase):
    CACHES = (
        ("HF_HOME", "/large/cache/huggingface"),
        ("UV_CACHE_DIR", "/large/cache/uv"),
    )

    def machine(self, caches=None) -> rigsync.Machine:
        return rigsync.Machine(
            name="peer",
            ssh="peer-alias",
            hostname=None,
            repo_path=Path("/large/projects/thing"),
            local=False,
            storage_root=Path("/large"),
            caches=self.CACHES if caches is None else caches,
        )

    def test_env_file_exports_every_declared_var_quoted(self) -> None:
        body = rigsync.render_env_sh(self.machine())
        self.assertIn("export HF_HOME=/large/cache/huggingface", body)
        self.assertIn("export UV_CACHE_DIR=/large/cache/uv", body)
        self.assertIn("do not hand-edit", body.lower())

    def test_env_file_shell_quotes_awkward_paths(self) -> None:
        body = rigsync.render_env_sh(self.machine(caches=(("TMPDIR", "/large/a b/tmp"),)))
        self.assertIn("export TMPDIR='/large/a b/tmp'", body)

    def test_provision_wires_zshenv_appended_and_bashrc_prepended(self) -> None:
        # bash returns early for non-interactive shells, so the block must go
        # ABOVE that guard; zsh reads .zshenv unconditionally so order is free.
        script = rigsync.provision_env_script(self.machine())
        self.assertIn("wire .zshenv append", script)
        self.assertIn("wire .bashrc prepend", script)
        self.assertIn(".rigsync.bak", script)
        self.assertIn(rigsync.MANAGED_BEGIN, script)

    def test_provision_refuses_without_confirm(self) -> None:
        machine = self.machine()
        config = rigsync.Config(Path("/src"), {"evaluations": {"path": "e"}}, {"peer": machine})
        with redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(rigsync.RigSyncError, "--confirm"):
                rigsync.provision_env(config, [machine], dry_run=False, confirm=False)

    def test_provision_dry_run_writes_nothing(self) -> None:
        machine = self.machine()
        config = rigsync.Config(Path("/src"), {"evaluations": {"path": "e"}}, {"peer": machine})
        stream = io.StringIO()
        with mock.patch.object(rigsync, "remote") as remote_mock, redirect_stdout(stream):
            rigsync.provision_env(config, [machine], dry_run=True, confirm=False)
        remote_mock.assert_not_called()
        self.assertIn("HF_HOME", stream.getvalue())

    def test_machine_env_check_reports_drift(self) -> None:
        machine = self.machine()
        probe = subprocess.CompletedProcess([], 0, b"/wrong/place\n/large/cache/uv\n", b"")
        with mock.patch.object(rigsync, "remote", return_value=probe):
            failures, lines = rigsync.check_machine_env(machine)
        self.assertEqual(failures, 1)
        self.assertTrue(any("HF_HOME=/wrong/place" in line for line in lines))

    def test_machine_env_check_flags_unset_var(self) -> None:
        machine = self.machine()
        probe = subprocess.CompletedProcess([], 0, b"\n/large/cache/uv\n", b"")
        with mock.patch.object(rigsync, "remote", return_value=probe):
            failures, lines = rigsync.check_machine_env(machine)
        self.assertEqual(failures, 1)
        self.assertTrue(any("(unset)" in line for line in lines))

    def test_machine_env_check_passes_when_all_match(self) -> None:
        machine = self.machine()
        probe = subprocess.CompletedProcess(
            [], 0, b"/large/cache/huggingface\n/large/cache/uv\n", b""
        )
        with mock.patch.object(rigsync, "remote", return_value=probe):
            failures, lines = rigsync.check_machine_env(machine)
        self.assertEqual(failures, 0)
        self.assertTrue(lines[0].startswith("OK"))

    def test_project_env_overriding_machine_vars_fails(self) -> None:
        # The exact mechanism that cost 6.5G on the wrong volume.
        env = b"HF_TOKEN=secret\nHF_HOME=/home/someone/.cache/huggingface\nTORCH_NUM_WORKERS=16\n"
        with mock.patch.object(
            rigsync, "remote", return_value=subprocess.CompletedProcess([], 0, env, b"")
        ):
            failures, lines = rigsync.check_project_env(self.machine())
        self.assertEqual(failures, 1)
        self.assertIn("HF_HOME", lines[0])
        self.assertNotIn("HF_TOKEN", lines[0])

    def test_project_env_without_machine_vars_passes(self) -> None:
        env = b"HF_TOKEN=secret\n# HF_HOME=/commented/out\nCACHE_DIR=/large/p/storage/cache\n"
        with mock.patch.object(
            rigsync, "remote", return_value=subprocess.CompletedProcess([], 0, env, b"")
        ):
            self.assertEqual(rigsync.check_project_env(self.machine()), (0, []))

    def test_project_env_check_skipped_when_registry_declares_no_caches(self) -> None:
        with mock.patch.object(rigsync, "remote") as remote_mock:
            self.assertEqual(rigsync.check_project_env(self.machine(caches=())), (0, []))
        remote_mock.assert_not_called()

    def test_env_parser_handles_export_quotes_and_blanks(self) -> None:
        parsed = rigsync.parse_env_assignments(
            '\n# comment\nexport HF_HOME="/a/b"\nEMPTY=\nQUOTED=\'/c/d\'\nnotanenv=1\n'
        )
        self.assertEqual(parsed, {"HF_HOME": "/a/b", "QUOTED": "/c/d"})


if __name__ == "__main__":
    unittest.main()
