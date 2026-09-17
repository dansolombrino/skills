from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from contextlib import redirect_stdout


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "plugins/research/skills/environment-sync/scripts/envsync.py"
ASSET = ROOT / "plugins/research/skills/environment-sync/assets/environment.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


envsync = load_module("envsync", SCRIPT)
environment = load_module("environment_fingerprint", ASSET)


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(value).lstrip())


class EnvironmentSyncTests(unittest.TestCase):
    def make_contract(self, root: Path, *, required_uv: str = "==0.11.32") -> Path:
        config = root / "sync.toml"
        write(
            config,
            """
            version = 1
            [environment]
            manager = "uv"
            name = ".venv"
            gpu_smoke = ["python", "code/common/environment_smoke.py"]
            [artifacts.evaluations]
            path = "evaluations"
            depth = 2
            [machines.hub]
            repo_path = "/tmp/hub"
            """,
        )
        write(
            root / "pyproject.toml",
            f"""
            [project]
            name = "fixture"
            version = "0.1.0"
            requires-python = "==3.12.*"
            dependencies = []
            [tool.uv]
            required-version = {required_uv!r}
            """,
        )
        write(root / ".python-version", "3.12.11\n")
        write(root / "uv.lock", "version = 1\n")
        write(root / "code/common/environment.py", "print('helper')\n")
        write(root / "code/common/environment_smoke.py", "print('gpu ok')\n")
        return config

    def target(self) -> envsync.Target:
        return envsync.Target("0123456789abcdef", "20260802-120000", "a" * 40)

    def settings(self, root: Path) -> envsync.EnvironmentSettings:
        return envsync.EnvironmentSettings(
            root=root,
            environment_name="research-env",
            uv_version="0.11.32",
            python_version="3.12.11",
            gpu_smoke=("python", "code/common/environment_smoke.py"),
        )

    def test_load_contract_requires_exact_uv_and_python_pins(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_contract(root)
            settings = envsync.load_environment_settings(root, config)
            self.assertEqual(settings.uv_version, "0.11.32")
            self.assertEqual(settings.python_version, "3.12.11")
            self.assertEqual(settings.environment_name, ".venv")
            self.assertEqual(settings.gpu_smoke[0], "python")

    def test_load_contract_requires_user_chosen_safe_environment_name(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_contract(root)
            contents = config.read_text()
            for value in (None, "../shared", ".", "name with spaces", "code", ".git"):
                if value is None:
                    changed = contents.replace('name = ".venv"\n', "")
                else:
                    changed = contents.replace('name = ".venv"', f'name = "{value}"')
                config.write_text(changed)
                with self.assertRaisesRegex(envsync.EnvironmentSyncError, "environment.name"):
                    envsync.load_environment_settings(root, config)
                config.write_text(contents)

    def test_load_contract_rejects_uv_range(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_contract(root, required_uv=">=0.11")
            with self.assertRaisesRegex(envsync.EnvironmentSyncError, "exact"):
                envsync.load_environment_settings(root, config)

    def test_lane_parser_rejects_duplicate_or_malformed_gpus(self) -> None:
        self.assertEqual(
            envsync.parse_lanes(["rig-4090:0", "behemoth:0,3"]),
            [envsync.Lane("rig-4090", "0"), envsync.Lane("behemoth", "0,3")],
        )
        for value in ("rig-4090", "rig-4090:x", "rig-4090:0,0"):
            with self.assertRaises(envsync.EnvironmentSyncError):
                envsync.parse_lanes([value])

    def test_command_output_redacts_index_credentials(self) -> None:
        value = "https://alice:hunter2@example.com/simple?token=abc123&x=1"
        redacted = envsync.redact(value)
        self.assertNotIn("alice", redacted)
        self.assertNotIn("hunter2", redacted)
        self.assertNotIn("abc123", redacted)
        self.assertIn("https://***:***@example.com", redacted)

    def test_fingerprint_payload_is_canonical_and_stable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            lock = Path(raw) / "uv.lock"
            lock.write_text("locked\n")
            distributions = [
                SimpleNamespace(metadata={"Name": "zeta_pkg"}, version="2.0"),
                SimpleNamespace(metadata={"Name": "Alpha.Pkg"}, version="1.0"),
            ]
            with mock.patch.object(
                environment.importlib.metadata,
                "distributions",
                return_value=distributions,
            ):
                payload = environment.environment_payload(lock)
            self.assertEqual(payload["packages"], sorted(payload["packages"]))
            self.assertEqual(environment.canonical_name("My_Package.Name"), "my-package-name")
            self.assertRegex(environment.fingerprint(payload), r"^[0-9a-f]{64}$")
            self.assertEqual(environment.fingerprint(payload), environment.fingerprint(payload))

    def test_provision_dry_run_with_missing_uv_does_not_write(self) -> None:
        root = Path("/tmp/hub")
        machine = envsync.rigsync.Machine("hub", "hub", None, root, True)
        config = envsync.rigsync.Config(
            root, {"evaluations": {"path": "evaluations"}}, {"hub": machine}
        )
        with mock.patch.object(envsync, "verify_source"), mock.patch.object(
            envsync, "uv_version", return_value=None
        ), mock.patch.object(envsync, "remote") as remote_mock:
            envsync.provision(
                self.settings(root),
                config,
                [machine],
                self.target(),
                dry_run=True,
                confirmed=False,
            )
        remote_mock.assert_not_called()

    def test_sync_command_is_frozen_exact_and_uses_pinned_python(self) -> None:
        root = Path("/tmp/hub")
        machine = envsync.rigsync.Machine("hub", "hub", None, root, True)
        command = envsync.sync_command(self.settings(root), machine, self.target(), dry_run=True)
        self.assertIn("--frozen", command)
        self.assertIn("--exact", command)
        self.assertIn("--managed-python", command)
        self.assertIn("--dry-run", command)
        self.assertEqual(command[command.index("--python") + 1], "3.12.11")
        self.assertIn("--no-install-project", command)
        self.assertIn("UV_PROJECT_ENVIRONMENT=/tmp/hub/.envs/0123456789abcdef", command)
        self.assertEqual(
            command[command.index("--project") + 1], "/tmp/hub/.waves/20260802-120000"
        )
        staged = envsync.sync_command(
            self.settings(root), machine, envsync.Target("0123456789abcdef"), dry_run=False
        )
        self.assertEqual(staged[staged.index("--project") + 1], "/tmp/hub")

    def test_uv_install_is_versioned_user_local_and_does_not_use_sudo(self) -> None:
        root = Path("/tmp/hub")
        machine = envsync.rigsync.Machine("hub", "hub", None, root, True)
        completed = subprocess.CompletedProcess([], 0, b"", b"")
        with mock.patch.object(envsync, "remote", return_value=completed) as remote_mock, mock.patch.object(
            envsync, "uv_version", return_value="0.11.32"
        ):
            envsync.install_uv(self.settings(root), machine)
        command = remote_mock.call_args.args[1]
        rendered = " ".join(command)
        self.assertIn("https://astral.sh/uv/0.11.32/install.sh", rendered)
        self.assertIn("UV_UNMANAGED_INSTALL", rendered)
        self.assertIn("UV_NO_MODIFY_PATH", rendered)
        self.assertNotIn("sudo", rendered)

    def test_provision_refuses_to_resync_an_environment_an_active_wave_uses(self) -> None:
        root = Path("/tmp/hub")
        machine = envsync.rigsync.Machine("hub", "hub", None, root, True)
        config = envsync.rigsync.Config(
            root, {"evaluations": {"path": "evaluations"}}, {"hub": machine}
        )
        other = "20260801-000000"
        with mock.patch.object(envsync, "verify_source"), mock.patch.object(
            envsync, "uv_version", return_value="0.11.32"
        ), mock.patch.object(envsync, "environment_exists", return_value=True), mock.patch.object(
            envsync.rigsync, "wave_activity", return_value={other: ["tmux:active"]}
        ), mock.patch.object(
            envsync.rigsync, "worktrees", return_value={other: Path("/tmp/hub/.waves") / other}
        ), mock.patch.object(envsync.rigsync, "env_key_for_tree", return_value="0123456789abcdef"):
            with self.assertRaisesRegex(envsync.EnvironmentSyncError, "active work blocks"):
                envsync.provision(
                    self.settings(root),
                    config,
                    [machine],
                    self.target(),
                    dry_run=False,
                    confirmed=True,
                )

    def test_provision_builds_a_new_key_while_other_waves_run(self) -> None:
        root = Path("/tmp/hub")
        machine = envsync.rigsync.Machine("hub", "hub", None, root, True)
        config = envsync.rigsync.Config(
            root, {"evaluations": {"path": "evaluations"}}, {"hub": machine}
        )
        ok = subprocess.CompletedProcess([], 0, b"", b"")
        with mock.patch.object(envsync, "verify_source"), mock.patch.object(
            envsync, "uv_version", return_value="0.11.32"
        ), mock.patch.object(envsync, "environment_exists", return_value=False), mock.patch.object(
            envsync.rigsync, "wave_activity", side_effect=AssertionError("not consulted")
        ), mock.patch.object(envsync, "remote", return_value=ok) as remote_mock, mock.patch.object(
            envsync, "verify_environments"
        ) as verify:
            envsync.provision(
                self.settings(root), config, [machine], self.target(), dry_run=False, confirmed=True
            )
        self.assertTrue(any("sync" in call.args[1] for call in remote_mock.call_args_list))
        verify.assert_called_once()

    def test_staged_target_is_hub_only(self) -> None:
        root = Path("/tmp/hub")
        hub = envsync.rigsync.Machine("hub", "hub", None, root, True)
        peer = envsync.rigsync.Machine("peer", "peer", None, Path("/tmp/peer"), False)
        config = envsync.rigsync.Config(root, {}, {"hub": hub, "peer": peer})
        with self.assertRaisesRegex(envsync.EnvironmentSyncError, "local hub only"):
            envsync.provision(
                self.settings(root), config, [peer], envsync.Target("0123456789abcdef"), dry_run=True, confirmed=False
            )

    def test_verify_rejects_cross_rig_fingerprint_drift(self) -> None:
        root = Path("/tmp/hub")
        hub = envsync.rigsync.Machine("hub", "hub", None, root, True)
        peer = envsync.rigsync.Machine("peer", "peer", None, Path("/tmp/peer"), False)
        config = envsync.rigsync.Config(
            root,
            {"evaluations": {"path": "evaluations"}},
            {"hub": hub, "peer": peer},
        )
        facts = envsync.HostFacts("Linux", "x86_64", "glibc 2.39", ("GPU, 555.1",))

        def fingerprint(_settings, machine, _target):
            return "a" * 64 if machine.name == "hub" else "b" * 64

        with mock.patch.object(envsync, "verify_source"), mock.patch.object(
            envsync, "host_facts", return_value=facts
        ), mock.patch.object(
            envsync, "uv_version", return_value="0.11.32"
        ), mock.patch.object(envsync, "lock_check"), mock.patch.object(
            envsync, "environment_fingerprint", side_effect=fingerprint
        ):
            with self.assertRaisesRegex(envsync.EnvironmentSyncError, "fingerprint mismatch"):
                envsync.verify_environments(
                    self.settings(root), config, [hub, peer], self.target(), []
                )

    def test_verify_rejects_host_architecture_drift(self) -> None:
        root = Path("/tmp/hub")
        hub = envsync.rigsync.Machine("hub", "hub", None, root, True)
        peer = envsync.rigsync.Machine("peer", "peer", None, Path("/tmp/peer"), False)
        config = envsync.rigsync.Config(
            root,
            {"evaluations": {"path": "evaluations"}},
            {"hub": hub, "peer": peer},
        )

        def facts(machine):
            architecture = "x86_64" if machine.name == "hub" else "aarch64"
            return envsync.HostFacts("Linux", architecture, "glibc", ("GPU, 555.1",))

        with mock.patch.object(envsync, "verify_source"), mock.patch.object(
            envsync, "host_facts", side_effect=facts
        ):
            with self.assertRaisesRegex(envsync.EnvironmentSyncError, "aarch64"):
                envsync.verify_environments(
                    self.settings(root), config, [peer], self.target(), []
                )

    def test_environment_source_check_ignores_experiment_code_but_rejects_lock_drift(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
            self.make_contract(root)
            write(root / "code/train.py", "print('clean')\n")
            write(root / ".gitignore", ".waves/\n.envs/\n")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "contract"], check=True)
            subprocess.run(["git", "-C", str(root), "remote", "add", "origin", str(root / "remote.git")], check=True)
            revision = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            machine = envsync.rigsync.Machine("hub", "hub", None, root, True)
            config = envsync.rigsync.Config(
                root,
                {"evaluations": {"path": "evaluations"}},
                {"hub": machine},
                envsync.rigsync.GitSettings("origin", "main"),
            )
            wave = "20260802-120000"
            tree = root / ".waves" / wave
            subprocess.run(["git", "-C", str(root), "worktree", "add", "-q", "--detach", str(tree), revision], check=True)
            target = envsync.Target("0123456789abcdef", wave, revision)
            write(tree / "code/train.py", "print('experiment work')\n")
            envsync.verify_source(config, machine, target)
            write(tree / "uv.lock", "changed = true\n")
            with self.assertRaisesRegex(envsync.EnvironmentSyncError, "environment contract dirty"):
                envsync.verify_source(config, machine, target)
            with self.assertRaisesRegex(envsync.EnvironmentSyncError, "missing wave worktree"):
                envsync.verify_source(config, machine, envsync.Target("0123456789abcdef", "20260803-000000", revision))
            staged = envsync.Target("0123456789abcdef")
            write(root / "uv.lock", "staged = true\n")
            subprocess.run(["git", "-C", str(root), "add", "uv.lock"], check=True)
            envsync.verify_source(config, machine, staged)
            write(root / "uv.lock", "unstaged = true\n")
            with self.assertRaisesRegex(envsync.EnvironmentSyncError, "environment contract dirty"):
                envsync.verify_source(config, machine, staged)

    def test_staged_key_equals_the_key_of_the_commit_made_from_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True)
            self.make_contract(root)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            staged = envsync.staged_target(root)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "c"], check=True)
            revision = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
            ).stdout.strip()
            wave = envsync.wave_target(root, "20260802-120000", revision)
            self.assertEqual(staged.key, wave.key)
            self.assertTrue(staged.staged)
            self.assertFalse(wave.staged)

    def test_cli_target_needs_wave_and_revision_or_staged(self) -> None:
        parser = envsync.build_parser()
        for argv in (["verify", "--machines", "hub"], ["verify", "--machines", "hub", "--wave", "20260802-120000"],
                     ["verify", "--machines", "hub", "--staged", "--wave", "20260802-120000"]):
            with self.assertRaises(envsync.EnvironmentSyncError):
                envsync.parse_target(Path("/nonexistent"), parser.parse_args(argv))

    def test_gpu_smoke_uses_project_venv_and_assigned_devices(self) -> None:
        root = Path("/tmp/hub")
        machine = envsync.rigsync.Machine("hub", "hub", None, root, True)
        completed = subprocess.CompletedProcess([], 0, b"ok\n", b"")
        with mock.patch.object(envsync, "remote", return_value=completed) as remote_mock:
            envsync.run_gpu_smoke(
                self.settings(root), machine, envsync.Lane("hub", "0,1"), self.target()
            )
        command = remote_mock.call_args.args[1]
        self.assertEqual(
            command[:3],
            ["env", "CUDA_VISIBLE_DEVICES=0,1", "/tmp/hub/.envs/0123456789abcdef/bin/python"],
        )
        self.assertNotIn("uv", command)

    def test_gpu_smoke_script_resolves_against_the_target_machine_repo(self) -> None:
        # The remote shell has no cwd, so a script left relative is read from $HOME
        # on every peer. Assert the script element itself, not merely that repo_path
        # appears somewhere in the argv.
        settings = self.settings(Path("/tmp/hub"))
        machine = envsync.rigsync.Machine("peer", "peer", None, Path("/srv/proj"), False)
        completed = subprocess.CompletedProcess([], 0, b"ok\n", b"")
        with mock.patch.object(envsync, "remote", return_value=completed) as remote_mock:
            envsync.run_gpu_smoke(settings, machine, envsync.Lane("peer", "0"), self.target())
        command = remote_mock.call_args.args[1]
        self.assertEqual(
            command,
            [
                "env",
                "CUDA_VISIBLE_DEVICES=0",
                "/srv/proj/.envs/0123456789abcdef/bin/python",
                "/srv/proj/.waves/20260802-120000/code/common/environment_smoke.py",
            ],
        )

    def test_gpu_smoke_passes_trailing_arguments_through_unchanged(self) -> None:
        root = Path("/tmp/hub")
        settings = envsync.EnvironmentSettings(
            root=root,
            environment_name="research-env",
            uv_version="0.11.32",
            python_version="3.12.11",
            gpu_smoke=("python", "code/common/environment_smoke.py", "--strict", "fp8"),
        )
        machine = envsync.rigsync.Machine("peer", "peer", None, Path("/srv/proj"), False)
        completed = subprocess.CompletedProcess([], 0, b"ok\n", b"")
        with mock.patch.object(envsync, "remote", return_value=completed) as remote_mock:
            envsync.run_gpu_smoke(settings, machine, envsync.Lane("peer", "0"), self.target())
        command = remote_mock.call_args.args[1]
        self.assertEqual(command[3], "/srv/proj/.waves/20260802-120000/code/common/environment_smoke.py")
        self.assertEqual(command[4:], ["--strict", "fp8"])

    def test_gpu_smoke_rejects_configs_with_no_resolvable_script(self) -> None:
        rejected = {
            "option in the script slot": '["python", "-c", "print(1)"]',
            "absolute path": '["python", "/srv/proj/code/common/environment_smoke.py"]',
            "escaping path": '["python", "../other/environment_smoke.py"]',
            "no script at all": '["python"]',
        }
        for label, value in rejected.items():
            with self.subTest(label):
                with tempfile.TemporaryDirectory() as raw:
                    root = Path(raw)
                    config = self.make_contract(root)
                    config.write_text(
                        config.read_text().replace(
                            '["python", "code/common/environment_smoke.py"]', value
                        )
                    )
                    with self.assertRaises(envsync.EnvironmentSyncError):
                        envsync.load_environment_settings(root, config)

    def test_job_gpu_machine_skips_the_probe_and_reports_deferred(self) -> None:
        cluster = envsync.rigsync.Machine(
            "cluster", "cluster", None, Path("/work/project"), False, gpus_in_job=True
        )
        outputs = {"uname -s": b"Linux\n", "uname -m": b"x86_64\n", "getconf GNU_LIBC_VERSION": b"glibc 2.28\n"}

        def fake_remote(machine, argv, *, check=True):
            key = " ".join(argv)
            if "nvidia-smi" in key:
                raise AssertionError("nvidia-smi must not be probed on a job-GPU machine")
            return subprocess.CompletedProcess([], 0, outputs[key], b"")

        with mock.patch.object(envsync, "remote", side_effect=fake_remote):
            facts = envsync.host_facts(cluster)
        self.assertEqual((facts.system, facts.architecture, facts.libc), ("Linux", "x86_64", "glibc 2.28"))
        self.assertEqual(facts.gpus, ())
        self.assertEqual(envsync.gpu_summary(cluster, facts), "deferred to job")

    def test_verify_accepts_job_gpu_machine_but_refuses_a_lane_on_it(self) -> None:
        root = Path("/tmp/hub")
        hub = envsync.rigsync.Machine("hub", "hub", None, root, True)
        cluster = envsync.rigsync.Machine(
            "cluster", "cluster", None, Path("/work/project"), False, gpus_in_job=True
        )
        config = envsync.rigsync.Config(
            root, {"evaluations": {"path": "evaluations"}}, {"hub": hub, "cluster": cluster}
        )

        def facts(machine):
            gpus = () if machine.gpus_in_job else ("GPU, 555.1",)
            return envsync.HostFacts("Linux", "x86_64", "glibc", gpus)

        stream = io.StringIO()
        with mock.patch.object(envsync, "verify_source"), mock.patch.object(
            envsync, "host_facts", side_effect=facts
        ), mock.patch.object(envsync, "uv_version", return_value="0.11.32"), mock.patch.object(
            envsync, "lock_check"
        ), mock.patch.object(envsync, "environment_fingerprint", return_value="a" * 64), mock.patch.object(
            envsync, "run_gpu_smoke"
        ) as smoke, redirect_stdout(stream):
            envsync.verify_environments(self.settings(root), config, [cluster], self.target(), [])
            with self.assertRaisesRegex(envsync.EnvironmentSyncError, "no GPU on the ssh target"):
                envsync.verify_environments(
                    self.settings(root), config, [], self.target(), [envsync.Lane("cluster", "0")]
                )
        self.assertIn("OK cluster environment: " + "a" * 64 + "; gpus=deferred to job", stream.getvalue())
        smoke.assert_not_called()

    def test_verify_still_rejects_architecture_drift_on_a_job_gpu_machine(self) -> None:
        root = Path("/tmp/hub")
        hub = envsync.rigsync.Machine("hub", "hub", None, root, True)
        cluster = envsync.rigsync.Machine(
            "cluster", "cluster", None, Path("/work/project"), False, gpus_in_job=True
        )
        config = envsync.rigsync.Config(
            root, {"evaluations": {"path": "evaluations"}}, {"hub": hub, "cluster": cluster}
        )

        def facts(machine):
            if machine.gpus_in_job:
                return envsync.HostFacts("Linux", "aarch64", "glibc", ())
            return envsync.HostFacts("Linux", "x86_64", "glibc", ("GPU, 555.1",))

        with mock.patch.object(envsync, "verify_source"), mock.patch.object(
            envsync, "host_facts", side_effect=facts
        ):
            with self.assertRaisesRegex(envsync.EnvironmentSyncError, "aarch64"):
                envsync.verify_environments(self.settings(root), config, [cluster], self.target(), [])

    def test_doctor_reports_deferred_gpus_for_a_job_gpu_machine(self) -> None:
        root = Path("/tmp/hub")
        hub = envsync.rigsync.Machine("hub", "hub", None, root, True)
        cluster = envsync.rigsync.Machine(
            "cluster", "cluster", None, Path("/work/project"), False, gpus_in_job=True
        )
        config = envsync.rigsync.Config(
            root, {"evaluations": {"path": "evaluations"}}, {"hub": hub, "cluster": cluster}
        )

        def facts(machine):
            gpus = () if machine.gpus_in_job else ("GPU, 555.1",)
            return envsync.HostFacts("Linux", "x86_64", "glibc", gpus)

        ok = subprocess.CompletedProcess([], 0, b"", b"")
        stream = io.StringIO()
        with mock.patch.object(envsync.rigsync, "doctor"), mock.patch.object(
            envsync, "local_hub", return_value=hub
        ), mock.patch.object(envsync, "remote", return_value=ok), mock.patch.object(
            envsync, "host_facts", side_effect=facts
        ), mock.patch.object(envsync, "uv_version", return_value="0.11.32"), redirect_stdout(stream):
            envsync.doctor(self.settings(root), config, [cluster])
        self.assertIn("OK cluster: host=Linux/x86_64 libc=glibc; gpus=deferred to job; uv=0.11.32 (ready)", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
