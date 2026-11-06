import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "plugins/research/skills/sweep-supervisor/assets"
LAUNCHER = ASSETS / "launch_supervisor.py"
CACHE = ".claude/plugins/cache/market/research"


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def install(self, version: str, *, check_passes: bool = True, cache: str = CACHE) -> None:
        script = self.home / cache / version / "skills/sweep-supervisor/scripts/supervisor.py"
        script.parent.mkdir(parents=True)
        check_exit = 0 if check_passes else 1
        script.write_text(
            "import sys\n"
            f"if sys.argv[1:] == ['check']: sys.exit({check_exit})\n"
            f"print('ran {version}', sys.argv[1:])\n"
        )

    def launch(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run([sys.executable, str(LAUNCHER), *args], capture_output=True, text=True, env=env, check=False)

    def test_starts_the_newest_version_numerically_and_passes_arguments(self) -> None:
        self.install("9.0.0")
        self.install("16.1.0")
        self.install("16.0.0")
        result = self.launch("serve")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ran 16.1.0 ['serve']", result.stdout)

    def test_looks_in_every_host_cache(self) -> None:
        self.install("1.0.0")
        self.install("2.0.0", cache=".codex/plugins/cache/market/research")
        self.assertIn("ran 2.0.0", self.launch("serve").stdout)

    def test_skips_a_copy_whose_check_fails(self) -> None:
        self.install("15.0.0")
        self.install("16.0.0", check_passes=False)
        result = self.launch("serve")
        self.assertIn("ran 15.0.0", result.stdout)
        self.assertIn("16.0.0", result.stderr)

    def test_ignores_non_version_directories(self) -> None:
        self.install("1.0.0")
        self.install("unknown")
        self.assertIn("ran 1.0.0", self.launch("serve").stdout)

    def test_fails_when_nothing_is_installed(self) -> None:
        result = self.launch("serve")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no installed research plugin", result.stderr)

    def test_fails_when_no_copy_passes_its_check(self) -> None:
        self.install("1.0.0", check_passes=False)
        result = self.launch("serve")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no installed copy passes", result.stderr)

    def test_the_service_unit_leaves_its_target_to_be_filled_in(self) -> None:
        self.assertIn("__LAUNCHER_OR_SUPERVISOR_PY__ serve", (ASSETS / "sweep-supervisor.service").read_text())


if __name__ == "__main__":
    unittest.main()
