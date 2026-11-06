"""research-project-init's scaffold script, the project it renders, and the rendered pre-commit hook."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "plugins/research/skills"
INIT = SKILLS / "research-project-init"
ASSETS = INIT / "assets/project"
SCRIPT = INIT / "scripts/scaffold.py"
ENVIRONMENT_HELPER = SKILLS / "environment-sync/assets/environment.py"
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}
HASH_A = "rid-" + "a" * 32
HASH_B = "rid-" + "b" * 32


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], env=GIT_ENV, capture_output=True, text=True, check=check)


def scaffold(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(root), "--python", "3.12.7", "--uv", "0.9.1", *extra],
        env=GIT_ENV,
        capture_output=True,
        text=True,
    )


class ProjectCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "my-project"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")


class ScaffoldScriptTests(ProjectCase):
    def test_dry_run_is_the_default_and_changes_nothing(self) -> None:
        result = scaffold(self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("would write code/common/run_id.py", result.stdout)
        self.assertIn("would write .env", result.stdout)
        self.assertEqual([path.name for path in self.root.iterdir()], [".git"])
        self.assertEqual(git(self.root, "config", "core.hooksPath", check=False).stdout, "")

    def test_confirm_renders_the_whole_project(self) -> None:
        result = scaffold(self.root, "--confirm")
        self.assertEqual(result.returncode, 0, result.stderr)
        for relative in (
            "README.md", "AGENTS.md", "CLAUDE.md", "JOURNAL.md", "EXPERIMENTS.md", ".env.example", ".env",
            ".gitignore", ".vscode/settings.json", "sync.toml", "pyproject.toml", ".python-version",
            ".githooks/pre-commit", "code/common/__init__.py",
        ):
            self.assertTrue((self.root / relative).is_file(), relative)
        for directory in ("checkpoints", "config", "evaluations", "logs", "plots", "scripts", "tests",
                          "visualizations", "shitpads", "references"):
            self.assertTrue((self.root / directory / ".gitkeep").is_file(), directory)
        self.assertFalse((self.root / "uv.lock").exists())
        rendered = [path for path in self.root.rglob("*") if path.is_file() and ".git" not in path.parts[len(self.root.parts):][0:1]]
        for path in rendered:
            if "code" not in path.relative_to(self.root).parts:
                self.assertNotRegex(path.read_text(), r"\{\{[A-Z_]+\}\}", path)
        self.assertIn("# my-project", (self.root / "README.md").read_text())
        self.assertEqual((self.root / ".python-version").read_text(), "3.12.7\n")
        self.assertEqual(git(self.root, "config", "core.hooksPath").stdout.strip(), ".githooks")

    def test_code_is_copied_byte_for_byte(self) -> None:
        scaffold(self.root, "--confirm")
        for name in ("paths.py", "run_id.py", "status.py", "metrics.py", "environment_smoke.py"):
            self.assertEqual(
                (self.root / "code/common" / name).read_bytes(), (ASSETS / "code/common" / name).read_bytes(), name
            )
        self.assertEqual((self.root / "code/common/environment.py").read_bytes(), ENVIRONMENT_HELPER.read_bytes())

    def test_modes_and_ignores(self) -> None:
        scaffold(self.root, "--confirm")
        self.assertEqual(stat.S_IMODE((self.root / ".env").stat().st_mode), 0o600)
        self.assertTrue(os.access(self.root / ".githooks/pre-commit", os.X_OK))
        self.assertEqual(git(self.root, "check-ignore", ".env").stdout.strip(), ".env")
        self.assertEqual(git(self.root, "check-ignore", ".venv/x", check=False).stdout.strip(), ".venv/x")
        self.assertEqual(git(self.root, "check-ignore", "evaluations/.gitkeep", check=False).stdout, "")

    def test_refuses_without_a_repository_or_over_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            result = scaffold(Path(raw), "--confirm")
            self.assertEqual(result.returncode, 1)
            self.assertIn("git init", result.stderr)
        (self.root / "README.md").write_text("mine\n")
        result = scaffold(self.root, "--confirm")
        self.assertEqual(result.returncode, 1)
        self.assertIn("README.md", result.stderr)
        self.assertEqual((self.root / "README.md").read_text(), "mine\n")
        self.assertFalse((self.root / "JOURNAL.md").exists(), "a refusal writes nothing")

    def test_leaves_the_tag_files_alone(self) -> None:
        (self.root / ".project.toml").write_text('categories = ["research"]\n')
        (self.root / ".claude").mkdir()
        (self.root / ".claude/settings.json").write_text("{}\n")
        result = scaffold(self.root, "--confirm")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / ".project.toml").read_text(), 'categories = ["research"]\n')

    def test_refuses_bad_values(self) -> None:
        bad = subprocess.run(
            [sys.executable, str(SCRIPT), str(self.root), "--python", "3.12", "--uv", "0.9.1"],
            capture_output=True, text=True,
        )
        self.assertEqual(bad.returncode, 1)
        self.assertEqual(scaffold(self.root, "--environment-name", "..").returncode, 1)
        self.assertEqual(scaffold(self.root, "--project-name", "has space").returncode, 1)

    def test_rendered_contract_files_satisfy_environment_sync(self) -> None:
        scaffold(self.root, "--confirm", "--environment-name", "env1")
        (self.root / "uv.lock").write_text("")
        spec = importlib.util.spec_from_file_location("envsync_for_scaffold", SKILLS / "environment-sync/scripts/envsync.py")
        envsync = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = envsync
        spec.loader.exec_module(envsync)
        settings = envsync.load_environment_settings(self.root, self.root / "sync.toml")
        self.assertEqual(settings.gpu_smoke, ("python", "code/common/environment_smoke.py"))
        pyproject = tomllib.loads((self.root / "pyproject.toml").read_text())
        names = {re.split(r"[<>=]", dep)[0] for dep in pyproject["project"]["dependencies"]}
        self.assertLessEqual({"torch", "hydra-core", "plotly"}, names)
        self.assertNotIn("build-system", pyproject)
        self.assertEqual(pyproject["tool"]["uv"]["required-version"], "==0.9.1")
        self.assertIn("env1/", (self.root / ".gitignore").read_text().splitlines())


class PreCommitHookTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(scaffold(self.root, "--confirm").returncode, 0)
        git(self.root, "add", "-A")
        first = git(self.root, "commit", "-qm", "scaffold", check=False)
        self.assertEqual(first.returncode, 0, "the scaffold's own first commit must pass: " + first.stderr)
        self.journal = self.root / "JOURNAL.md"

    def commit(self, **files: str) -> subprocess.CompletedProcess:
        for relative, text in files.items():
            path = self.root / relative.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        git(self.root, "add", "-A")
        return git(self.root, "commit", "-qm", "x", check=False)

    def append_journal(self, text: str = "## entry\n") -> None:
        self.journal.write_text(self.journal.read_text() + text)

    def test_guarded_change_needs_a_journal_entry(self) -> None:
        blocked = self.commit(config__a="1\n")
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("JOURNAL.md is not part of the commit", blocked.stderr)
        self.append_journal()
        self.assertEqual(self.commit().returncode, 0)

    def test_tests_and_docs_need_no_entry(self) -> None:
        self.assertEqual(self.commit(tests__test_a="1\n", notes="1\n").returncode, 0)

    def test_journal_rewrite_is_blocked_and_append_passes(self) -> None:
        self.append_journal("## one\n\nfirst thought\n")
        self.assertEqual(self.commit().returncode, 0)
        self.append_journal("## two\n")
        self.assertEqual(self.commit().returncode, 0)
        self.journal.write_text(self.journal.read_text().replace("first thought", "better thought"))
        blocked = self.commit()
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("first thought", blocked.stderr)
        self.assertIn("append-only", blocked.stderr)

    def test_journal_deletion_and_truncation_are_blocked(self) -> None:
        self.append_journal("## one\n")
        self.assertEqual(self.commit().returncode, 0)
        self.journal.write_text("")
        self.assertEqual(self.commit().returncode, 1)
        git(self.root, "checkout", "HEAD", "--", "JOURNAL.md")
        self.journal.unlink()
        self.assertEqual(self.commit().returncode, 1)

    def test_a_migrations_hash_swap_passes(self) -> None:
        self.append_journal(f"ran {HASH_A} and {HASH_A}, label k=v\n")
        self.assertEqual(self.commit().returncode, 0)
        self.journal.write_text(self.journal.read_text().replace(HASH_A, HASH_B))
        self.assertEqual(self.commit().returncode, 0)
        self.journal.write_text(self.journal.read_text().replace("label k=v", "label k=w"))
        self.assertEqual(self.commit().returncode, 1)

    def test_a_missing_final_newline_can_still_be_appended_to(self) -> None:
        self.journal.write_text(self.journal.read_text() + "no newline")
        self.assertEqual(self.commit().returncode, 0)
        self.append_journal("\nnext\n")
        self.assertEqual(self.commit().returncode, 0)

    def test_storage_lint(self) -> None:
        for path, line, code in (
            ("code/001_a/x.py", "out = Path(__file__).parent / 'out'\n", 1),
            ("visualizations/001_a/p.py", "base = os.getcwd()\n", 1),
            ("code/001_a/y.py", "base = Path.cwd()\n", 1),
            ("code/001_a/z.py", "sys.path.insert(0, str(Path(__file__).parent))\n", 0),
            ("code/001_a/w.py", "# never Path(__file__)\n", 0),
            ("code/001_a/v.py", "name = Path(__file__).stem  # paths-lint: ok\n", 0),
            ("tests/code/t.py", "x = Path(__file__)\n", 0),
            ("code/common/paths.py", "y = Path(__file__)\n", 0),
        ):
            with self.subTest(path=path):
                target = self.root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text((target.read_text() if target.exists() else "") + line)
                self.append_journal(f"{path}\n")
                git(self.root, "add", "-A")
                result = git(self.root, "commit", "-qm", "x", check=False)
                self.assertEqual(result.returncode, code, result.stderr)
                if code:
                    self.assertIn(path, result.stderr)
                    git(self.root, "reset", "-q", "--hard")
                    git(self.root, "clean", "-qfd")

    def test_only_added_lines_are_linted(self) -> None:
        target = self.root / "code/001_a/x.py"
        target.parent.mkdir(parents=True)
        target.write_text("out = Path(__file__)  # paths-lint: ok\n")
        self.append_journal()
        self.assertEqual(self.commit().returncode, 0)
        target.write_text(target.read_text() + "y = 1\n")
        self.append_journal()
        self.assertEqual(self.commit().returncode, 0)


class TemplateBehaviourTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        scaffold(self.root, "--confirm")
        templates = (INIT / "references/templates.md").read_text()
        entry = templates.split("## Experiment entrypoint")[1]
        self.bootstrap = re.search(r"```python\n(.*?)```", entry, re.DOTALL).group(1)
        self.smoke_block = re.search(r"```python\n(.*?)```", templates.split("## Smoke outputs")[1], re.DOTALL).group(1)

    def run_script(self, relative: str, body: str, *, env=None, cwd=None) -> subprocess.CompletedProcess:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.bootstrap.replace("from common.status import StatusWriter\n", "") + body)
        environ = {key: value for key, value in os.environ.items() if key != "RESEARCH_PROJECT_ROOT"}
        return subprocess.run(
            [sys.executable, str(path)], env={**environ, **(env or {})}, cwd=cwd or tempfile.gettempdir(),
            capture_output=True, text=True,
        )

    def test_the_documented_import_works_at_any_depth_without_shadowing_the_stdlib(self) -> None:
        body = "import code\nimport common.paths\nprint(code.__name__, hasattr(code, 'InteractiveConsole'), common.paths.source_root())\n"
        for relative in ("code/001_a/x.py", "code/001_a/002_b/x.py", "visualizations/001_a/p.py"):
            with self.subTest(relative=relative):
                result = self.run_script(relative, body)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.split(), ["code", "True", str(self.root.resolve())])

    def test_paths_loads_dot_env_from_the_project_root(self) -> None:
        (self.root / ".env").write_text("# note\nCACHE_DIR=storage/x\nexport QUOTED='a b'\nHF_TOKEN=\nSHELL_WINS=\nREPLACED=env\n")
        body = "import os, json\nfrom common.paths import storage_path\nprint(json.dumps([str(storage_path('CACHE_DIR', 'd')), os.environ['QUOTED'], 'HF_TOKEN' in os.environ, os.environ['SHELL_WINS'], os.environ['REPLACED']]))\n"
        result = self.run_script("code/001_a/x.py", body, env={"SHELL_WINS": "shell", "REPLACED": "shell"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [str(self.root.resolve() / "storage/x"), "a b", False, "shell", "env"])

    def test_a_wave_reads_dot_env_from_the_shared_root(self) -> None:
        (self.root / ".env").write_text("WHICH=shared\n")
        tree = self.root / ".waves/w1"
        (tree / "code").mkdir(parents=True)
        (tree / "pyproject.toml").write_text("[project]\n")
        (tree / "code/common").symlink_to(self.root / "code/common")
        script = tree / "code/x.py"
        script.write_text(
            "import sys\nsys.path.insert(0, sys.argv[1])\nimport os\nimport importlib.util as u\n"
            "s = u.spec_from_file_location('p', sys.argv[2])\nm = u.module_from_spec(s)\ns.loader.exec_module(m)\nprint(os.environ['WHICH'])\n"
        )
        result = subprocess.run(
            [sys.executable, str(script), str(tree / "code"), str(self.root / "code/common/paths.py")],
            env={**os.environ, "RESEARCH_PROJECT_ROOT": str(self.root)}, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "shared")

    def test_a_smoke_run_is_guarded_and_invisible_to_the_plan_check(self) -> None:
        (self.root / "config/001_a").mkdir(parents=True)
        (self.root / "config/001_a/RUN_ID_VERSIONS.json").write_text(
            json.dumps([{"version": 1, "params": ["seed"], "date": "2026-01-01", "reason": "r"}])
        )
        body = (
            "import json\nfrom common.run_id import check_run_id_plan\n"
            "EXPERIMENT = '001_a'\nRUN_ID_PATH_LAYOUT = 'hashed-v2'\n"
            "class C(dict):\n    smoke = True\n"
            "def smoke(default):\n"
            "    cfg = C(seed=0, steps=default)\n"
            "    version, params = load_run_id_spec(source_path(f'config/{EXPERIMENT}'))\n"
            + "".join("    " + line + "\n" for line in self.smoke_block.splitlines())
            + "    return run_dir, params\n"
            "run_dir, params = smoke(1)\n"
            "run_dir, params = smoke(2)  # a changed default: reset_smoke keeps the guard from tripping\n"
            "root = output_root('evaluations', EXPERIMENT)\n"
            "print(json.dumps({'dir': str(run_dir), 'plan': [str(p) for p in check_run_id_plan([{'seed': 0, 'steps': 9}], params, None, root, layout='hashed-v2', spec_root=source_path(f'config/{EXPERIMENT}'))]}))\n"
        )
        result = self.run_script("code/001_a/train.py", body)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        run_dir = Path(out["dir"])
        self.assertEqual(run_dir.parent, self.root.resolve() / "storage/smoke/evaluations/001_a")
        self.assertTrue((run_dir / ".run_config.json").is_file())
        self.assertEqual(json.loads((run_dir / ".run_config.json").read_text())["steps"], 2)
        self.assertFalse((self.root / "evaluations/001_a").exists())
        self.assertEqual(len(out["plan"]), 1)
        self.assertEqual(git(self.root, "status", "--short", "--", "storage").stdout, "")


class ScaffoldDocsTests(unittest.TestCase):
    def test_skill_gives_one_order_for_one_unattended_run(self) -> None:
        skill = " ".join((INIT / "SKILL.md").read_text().split())
        order = skill.split("## Order")[1]
        positions = [
            order.index(step)
            for step in ("`git init -b main`", "`apply --categories research`", "scripts/scaffold.py", "[machines.<rig>]",
                         "`uv lock`", "first commit", "`provision --staged`", "Bring up every intended rig")
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("No step waits for the user or a new session", order)
        self.assertIn("tag files", order)

    def test_templates_page_points_at_assets_instead_of_holding_code(self) -> None:
        templates = (INIT / "references/templates.md").read_text()
        self.assertLess(len(templates), 10_000)
        for marker in ("def run_id_hash_v2", "class StatusWriter", "class MetricsWriter", "def project_path"):
            self.assertNotIn(marker, templates)

    def test_guard_rule_is_stated_once_without_the_old_exemption(self) -> None:
        journal = " ".join((SKILLS / "research-journal/SKILL.md").read_text().split())
        self.assertNotIn("Trivial mechanical edits need no entry", journal)
        self.assertIn("always carries an entry", journal)
        self.assertIn("git config core.hooksPath", journal)
        self.assertIn("dot_githooks/pre-commit", journal)
        self.assertTrue((ASSETS / "dot_githooks/pre-commit").is_file())

    def test_shipped_tree_has_no_live_dotfiles_or_host_notes(self) -> None:
        names = [path.name for path in ASSETS.rglob("*") if "__pycache__" not in path.parts]
        self.assertFalse([name for name in names if name.startswith(".")])
        self.assertNotIn("CLAUDE.md", names)
        self.assertNotIn("AGENTS.md", names)


if __name__ == "__main__":
    unittest.main()
