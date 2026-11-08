"""Release tooling: the Codex enabled state, the bump check, the validator rules, and release.sh."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
RESEARCH = "research@dansolombrino-skills"
CORE = "core@dansolombrino-skills"
COPIED = (".agents", ".claude", ".claude-plugin", ".githooks", "plugins", "scripts")


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True)


def copy_repo(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in COPIED:
        shutil.copytree(ROOT / name, target / name, ignore=shutil.ignore_patterns("__pycache__", "results"))


class CodexPluginStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load("codex_plugin_state")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "config.toml"
        env = mock.patch.dict(os.environ, {"CODEX_HOME": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *args: str) -> tuple[int, str]:
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = self.module.main(list(args))
        return code, out.getvalue().strip()

    def test_get_reports_each_state_and_never_raises(self) -> None:
        self.assertEqual(self.cli("get", RESEARCH), (0, "absent"), "no file")
        self.config.write_text(f'[plugins."{RESEARCH}"]\nenabled = false\n')
        self.assertEqual(self.cli("get", RESEARCH), (0, "false"))
        self.config.write_text(f'[plugins."{RESEARCH}"]\nenabled = true\n')
        self.assertEqual(self.cli("get", RESEARCH), (0, "true"))
        self.config.write_text(f'[plugins."{RESEARCH}"]\nother = 1\n')
        self.assertEqual(self.cli("get", RESEARCH), (0, "unknown"))
        self.config.write_text("not = [toml")
        self.assertEqual(self.cli("get", RESEARCH), (0, "unknown"))
        self.config.write_bytes(b"\xff\xfe")
        self.assertEqual(self.cli("get", RESEARCH), (0, "unknown"))
        self.config.write_text('plugins = "odd"\n')
        self.assertEqual(self.cli("get", RESEARCH), (0, "absent"))

    def test_set_changes_only_the_one_line(self) -> None:
        text = (
            '# my config\nmodel = "m"  # keep\n\n'
            f'[plugins."{CORE}"]\nenabled = true\n\n'
            f'[plugins."{RESEARCH}"]\nenabled = true  # note\n\n'
            '[projects."/x"]\ntrust_level = "trusted"\n'
        )
        self.config.write_text(text)
        self.config.chmod(0o600)
        self.assertEqual(self.cli("set", RESEARCH, "false")[0], 0)
        self.assertEqual(self.config.read_text(), text.replace("enabled = true  # note", "enabled = false  # note"))
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)

    def test_set_adds_a_missing_table_or_key(self) -> None:
        self.assertEqual(self.cli("set", RESEARCH, "false")[0], 0, "no file yet")
        self.assertIs(tomllib.loads(self.config.read_text())["plugins"][RESEARCH]["enabled"], False)
        self.config.write_text('model = "m"')
        self.assertEqual(self.cli("set", RESEARCH, "false")[0], 0)
        data = tomllib.loads(self.config.read_text())
        self.assertEqual(data["model"], "m")
        self.assertIs(data["plugins"][RESEARCH]["enabled"], False)
        self.config.write_text(f'[plugins."{RESEARCH}"]\nsource = "x"\n')
        self.assertEqual(self.cli("set", RESEARCH, "true")[0], 0)
        self.assertEqual(
            tomllib.loads(self.config.read_text())["plugins"][RESEARCH], {"source": "x", "enabled": True}
        )

    def test_set_refuses_what_it_cannot_edit_and_leaves_it_alone(self) -> None:
        for text in ("not = [toml", f'plugins."{RESEARCH}" = {{ enabled = true }}\n'):
            self.config.write_text(text)
            code, output = self.cli("set", RESEARCH, "false")
            self.assertEqual(code, 1, text)
            self.assertIn("by hand", output)
            self.assertEqual(self.config.read_text(), text)

    def test_check_global_names_only_non_core_plugins(self) -> None:
        root = ("--root", str(ROOT))
        self.assertEqual(self.cli("check-global", *root), (0, ""), "no file")
        self.config.write_text(f'[plugins."{CORE}"]\nenabled = true\n')
        self.assertEqual(self.cli("check-global", *root), (0, ""))
        self.config.write_text(f'[plugins."{RESEARCH}"]\nenabled = true\n')
        self.assertEqual(self.cli("check-global", *root), (1, RESEARCH))
        self.config.write_text("not = [toml")
        self.assertEqual(self.cli("check-global", *root)[0], 2)


class VersionBumpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve() / "repo"
        copy_repo(self.repo)
        run_git(self.repo, "init", "-q", "-b", "main")
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "-qm", "base")
        self.version = json.loads((ROOT / "plugins/core/.codex-plugin/plugin.json").read_text())["version"]
        run_git(self.repo, "tag", f"core--v{self.version}")
        self.skill = self.repo / "plugins/core/skills/project-init/SKILL.md"

    def check(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["python3", "scripts/check_version_bump.py", "--plugin", "core", *args],
            cwd=self.repo, capture_output=True, text=True,
        )

    def bump(self) -> None:
        for host in (".codex-plugin", ".claude-plugin"):
            path = self.repo / f"plugins/core/{host}/plugin.json"
            data = json.loads(path.read_text())
            data["version"] = "999.0.0"
            path.write_text(json.dumps(data, indent=2) + "\n")

    def test_unchanged_passes(self) -> None:
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(self.check("--staged").returncode, 0)

    def test_change_without_a_bump_fails_in_both_modes(self) -> None:
        self.skill.write_text(self.skill.read_text() + "\nmore\n")
        run_git(self.repo, "add", "-A")
        result = self.check("--staged")
        self.assertEqual(result.returncode, 1)
        self.assertIn("changed since", result.stderr)
        self.assertEqual(self.check().returncode, 0, "HEAD has not changed yet")
        run_git(self.repo, "commit", "-qm", "unbumped")
        self.assertEqual(self.check().returncode, 1)

    def test_unstaged_edits_do_not_count_for_staged(self) -> None:
        self.skill.write_text(self.skill.read_text() + "\nmore\n")
        self.assertEqual(self.check("--staged").returncode, 0)

    def test_a_bump_clears_it_and_later_edits_stay_allowed(self) -> None:
        self.skill.write_text(self.skill.read_text() + "\nmore\n")
        self.bump()
        run_git(self.repo, "add", "-A")
        self.assertEqual(self.check("--staged").returncode, 0)
        run_git(self.repo, "commit", "-qm", "bumped")
        self.skill.write_text(self.skill.read_text() + "\neven more\n")
        run_git(self.repo, "add", "-A")
        self.assertEqual(self.check("--staged").returncode, 0)

    def test_evals_need_no_bump(self) -> None:
        case = self.repo / "plugins/core/evals/ignores-plain-repo-setup/prompt.md"
        case.write_text(case.read_text() + "\nmore\n")
        run_git(self.repo, "add", "-A")
        self.assertEqual(self.check("--staged").returncode, 0)

    def test_pre_commit_hook_blocks_an_unbumped_commit(self) -> None:
        run_git(self.repo, "config", "core.hooksPath", ".githooks")
        (self.repo / ".githooks/pre-commit").chmod(0o755)
        self.skill.write_text(self.skill.read_text() + "\nmore\n")
        run_git(self.repo, "add", "-A")
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            run_git(self.repo, "commit", "-qm", "unbumped")
        self.assertIn("changed since", caught.exception.stderr)
        self.bump()
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "-qm", "bumped")

    def test_the_hook_is_executable_in_git(self) -> None:
        listed = subprocess.run(
            ["git", "ls-files", "-s", ".githooks/pre-commit"], cwd=ROOT, capture_output=True, text=True
        ).stdout
        if listed:  # Not yet committed in a working copy under edit: nothing to assert.
            self.assertTrue(listed.startswith("100755"), listed)


class ValidatorRuleTests(unittest.TestCase):
    """Each rule must actually fire: a validator that always passes proves nothing."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve() / "repo"
        copy_repo(self.repo)

    def validate(self) -> str:
        result = subprocess.run(
            ["python3", str(ROOT / "scripts/validate_repo.py"), "--root", str(self.repo)],
            capture_output=True, text=True,
        )
        return result.stderr if result.returncode else ""

    def edit_json(self, relative: str, change) -> None:
        path = self.repo / relative
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data, indent=2))

    def append(self, relative: str, text: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((path.read_text() if path.exists() else "") + text)

    def test_the_real_tree_passes(self) -> None:
        self.assertEqual(self.validate(), "")

    def test_non_core_plugin_must_be_off_by_default(self) -> None:
        self.edit_json("plugins/research/.claude-plugin/plugin.json", lambda d: d.pop("defaultEnabled"))
        self.assertIn("must set defaultEnabled: false", self.validate())

    def test_catalog_must_not_carry_a_version(self) -> None:
        self.edit_json(".claude-plugin/marketplace.json", lambda d: d["plugins"][0].update(version="1.0.0"))
        self.assertIn("must not set version", self.validate())

    def test_host_name_in_a_distributed_skill_fails_outside_the_allowlist(self) -> None:
        self.append("plugins/core/skills/progress-updates/SKILL.md", "\nUse Claude Code here.\n")
        self.assertIn("host name 'Claude'", self.validate())

    def test_host_name_in_an_allowlisted_file_or_evals_passes(self) -> None:
        self.append("plugins/core/skills/project-init/SKILL.md", "\nCodex again.\n")
        self.append("plugins/core/evals/ignores-plain-repo-setup/note.md", "\nClaude only.\n")
        self.assertEqual(self.validate(), "")

    def test_pointer_may_add_a_guardrail_but_a_distributed_skill_may_not(self) -> None:
        self.assertIn("disallowed-tools", (self.repo / ".claude/skills/marketplace-skill-reviewer/SKILL.md").read_text())
        self.assertEqual(self.validate(), "")
        skill = self.repo / "plugins/core/skills/progress-updates/SKILL.md"
        skill.write_text(skill.read_text().replace("---\n", "---\ndisallowed-tools: Edit\n", 1))
        self.assertIn("frontmatter keys must be", self.validate())

    def test_pointer_description_must_equal_the_body(self) -> None:
        pointer = self.repo / ".claude/skills/marketplace-skill-creator/SKILL.md"
        pointer.write_text(pointer.read_text().replace("Create or update", "Make or update", 1))
        self.assertIn("description must equal", self.validate())

    def test_angle_brackets_in_a_description_fail(self) -> None:
        skill = self.repo / "plugins/core/skills/service-hub/SKILL.md"
        skill.write_text(skill.read_text().replace("http://host:port/service/", "http://<host>/", 1))
        self.assertIn("angle brackets", self.validate())

    def test_plugin_agent_frontmatter(self) -> None:
        agent = self.repo / "plugins/research/agents/plot-checker.md"
        original = agent.read_text()
        agent.write_text(original.replace("  - visualizations", "  - no-such-skill"))
        self.assertIn("'no-such-skill'", self.validate())
        agent.write_text(original.replace("name: plot-checker", "name: other"))
        self.assertIn("name must match the file name", self.validate())
        agent.write_text(original.replace("description: Render", "descr: Render"))
        self.assertIn("description is too short", self.validate())
        agent.write_text("no frontmatter\n")
        self.assertIn("missing YAML frontmatter", self.validate())

    def test_eval_cases(self) -> None:
        case = self.repo / "plugins/core/evals/ignores-plain-repo-setup"
        grader = case / "graders/not-project-init.md"
        original = grader.read_text()
        grader.write_text(original.replace("project-init", "no-such-skill"))
        self.assertIn("names skill 'no-such-skill'", self.validate())
        grader.write_text(original.replace("type: tool_used", "type: wrong"))
        self.assertIn("type must be one of", self.validate())
        grader.unlink()
        self.assertIn("at least one graders/*.md", self.validate())
        grader.write_text(original)
        prompt = case / "prompt.md"
        prompt.write_text(prompt.read_text().replace("tags:", "tag:"))
        self.assertIn("unknown frontmatter keys ['tag']", self.validate())


FAKE_CLAUDE = """#!/usr/bin/env bash
echo "claude $*" >> "$FAKE_LOG"
case "$1 $2" in
  "plugin list") cat "$FAKE_STATE/claude.json" ;;
  "plugin tag")
    case " $* " in
      *" --dry-run "*) if [ -n "${FAKE_TAG_REFUSES:-}" ]; then echo "tag refused" >&2; exit 1; fi ;;
      *) python3 -c 'import json,os,sys; p=os.path.join(os.environ["FAKE_STATE"],"target"); json.dump(open(p).read().strip(),open(p+".tagged","w"))' ;;
    esac ;;
  "plugin update"|"plugin install")
    python3 - <<'EOF'
import json, os
state = os.environ["FAKE_STATE"]
version = open(os.path.join(state, "target")).read().strip()
json.dump([{"id": os.environ["FAKE_ID"], "version": version, "enabled": False}], open(os.path.join(state, "claude.json"), "w"))
EOF
    ;;
esac
"""

# Behaves as Codex 0.163.0 does: `remove` drops the table, `add` writes `enabled = true`.
FAKE_CODEX = """#!/usr/bin/env bash
echo "codex $*" >> "$FAKE_LOG"
case "$1 $2" in
  "plugin list") cat "$FAKE_STATE/codex.json" ;;
  "plugin remove")
    python3 - "$3" <<'EOF'
import os, re, sys
path = os.path.join(os.environ["CODEX_HOME"], "config.toml")
text = open(path).read() if os.path.exists(path) else ""
open(path, "w").write(re.sub(r'\\[plugins\\."%s"\\]\\nenabled = \\w+\\n\\n?' % re.escape(sys.argv[1]), "", text))
EOF
    ;;
  "plugin add")
    python3 - "$3" <<'EOF'
import json, os, sys
home, state = os.environ["CODEX_HOME"], os.environ["FAKE_STATE"]
with open(os.path.join(home, "config.toml"), "a") as f:
    f.write(f'[plugins."{sys.argv[1]}"]\\nenabled = true\\n')
version = open(os.path.join(state, "target")).read().strip()
json.dump({"installed": [{"pluginId": sys.argv[1], "version": version}]}, open(os.path.join(state, "codex.json"), "w"))
EOF
    ;;
esac
"""


class ReleaseScriptTests(unittest.TestCase):
    """release.sh end to end, against fake hosts and a local bare remote. Nothing leaves the temp dir."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.repo = base / "repo"
        copy_repo(self.repo)
        # The real suite runs elsewhere; running it here would recurse.
        (self.repo / "tests").mkdir()
        (self.repo / "tests/test_ok.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n"
        )
        self.remote = base / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        run_git(self.repo, "init", "-q", "-b", "main")
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "-qm", "base")
        run_git(self.repo, "remote", "add", "origin", str(self.remote))
        run_git(self.repo, "push", "-q", "origin", "main")

        self.bin = base / "bin"
        self.bin.mkdir()
        for name, body in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX)):
            path = self.bin / name
            path.write_text(body)
            path.chmod(0o755)
        self.state = base / "state"
        self.state.mkdir()
        self.codex_home = base / "codex"
        self.codex_home.mkdir()
        self.log = base / "log"
        self.log.write_text("")
        self.config = self.codex_home / "config.toml"
        self.version = json.loads((ROOT / "plugins/research/.codex-plugin/plugin.json").read_text())["version"]
        (self.state / "target").write_text(self.version)
        self.installed("0.0.1")

    def installed(self, version: str) -> None:
        (self.state / "claude.json").write_text(json.dumps([{"id": RESEARCH, "version": version}]))
        (self.state / "codex.json").write_text(
            json.dumps({"installed": [{"pluginId": RESEARCH, "version": version}]})
        )

    def release(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(self.repo / "scripts/release.sh"), "--plugin", "research", *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{self.bin}:{os.environ['PATH']}",
                "CODEX_HOME": str(self.codex_home),
                "FAKE_LOG": str(self.log),
                "FAKE_STATE": str(self.state),
                "FAKE_ID": RESEARCH,
                "RELEASE_TMPDIR": "/tmp",
                **env,
            },
        )

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()

    def test_release_restores_an_off_plugin_around_remove_and_add(self) -> None:
        self.config.write_text(f'model = "m"\n\n[plugins."{RESEARCH}"]\nenabled = false\n')
        result = self.release()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = tomllib.loads(self.config.read_text())
        self.assertIs(data["plugins"][RESEARCH]["enabled"], False)
        self.assertEqual(data["model"], "m")
        self.assertIn(f"codex plugin add {RESEARCH}", self.calls())

    def test_a_first_install_of_a_domain_plugin_ends_off(self) -> None:
        result = self.release()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIs(tomllib.loads(self.config.read_text())["plugins"][RESEARCH]["enabled"], False)

    def test_verify_fails_if_a_domain_plugin_ends_up_on(self) -> None:
        # A second `add`-like write after the restore: the fake codex turns it on at `list` time.
        fake = self.bin / "codex"
        fake.write_text(fake.read_text().replace(
            '"plugin list") cat "$FAKE_STATE/codex.json" ;;',
            '"plugin list") cat "$FAKE_STATE/codex.json"; '
            'if [ -f "$FAKE_STATE/codex-added" ]; then sed -i "s/enabled = false/enabled = true/" "$CODEX_HOME/config.toml"; fi ;;',
        ).replace('"plugin add")', '"plugin add")\n    touch "$FAKE_STATE/codex-added"'))
        result = self.release()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("global Codex config", result.stderr)
        self.assertIn("a host is not right", result.stderr)

    def test_a_domain_plugin_already_on_stops_a_real_release_before_the_push(self) -> None:
        self.config.write_text(f'[plugins."{RESEARCH}"]\nenabled = true\n')
        result = self.release()
        self.assertEqual(result.returncode, 1)
        self.assertIn(RESEARCH, result.stderr)
        self.assertFalse(self.pushed_or_tagged())

    def pushed_or_tagged(self) -> bool:
        return any(" plugin tag " in c and "--dry-run" not in c for c in self.calls()) or bool(
            subprocess.run(
                ["git", "--git-dir", str(self.remote), "tag"], capture_output=True, text=True
            ).stdout.strip()
        )

    def test_a_refused_tag_stops_the_release_before_the_push(self) -> None:
        (self.repo / "scripts/x").write_text("")
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "-qm", "local only")
        result = self.release(FAKE_TAG_REFUSES="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("tag refused", result.stderr)
        remote = subprocess.run(
            ["git", "--git-dir", str(self.remote), "rev-parse", "main"], capture_output=True, text=True
        ).stdout.strip()
        self.assertNotEqual(remote, run_git(self.repo, "rev-parse", "HEAD").stdout.strip(), "main was pushed")
        self.assertFalse(any("plugin update" in c or "codex plugin add" in c for c in self.calls()))

    def test_native_validation_runs_before_any_push(self) -> None:
        self.assertEqual(self.release().returncode, 0)
        calls = self.calls()
        self.assertIn("claude plugin validate --strict .", calls)
        self.assertIn("claude plugin validate --strict plugins/research/", calls)
        self.assertLess(calls.index("claude plugin validate --strict ."), next(
            i for i, c in enumerate(calls) if " plugin tag " in c and "--dry-run" not in c
        ))

    def test_dry_run_changes_nothing_and_reports_drift(self) -> None:
        self.config.write_text(f'[plugins."{RESEARCH}"]\nenabled = false\n')
        before = self.config.read_text()
        result = self.release("--dry-run")
        self.assertEqual(result.returncode, 1, "hosts are behind")
        self.assertIn("host(s) not on", result.stderr)
        self.assertEqual(self.config.read_text(), before)
        self.assertFalse(self.pushed_or_tagged())
        self.assertFalse(any(("plugin remove" in c or "plugin update" in c) for c in self.calls()))

    def test_dry_run_on_target_passes_and_warns_when_changed_since_the_tag(self) -> None:
        self.installed(self.version)
        run_git(self.repo, "tag", f"research--v{self.version}")
        result = self.release("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("unchanged since", result.stdout)
        skill = self.repo / "plugins/research/skills/rig-board/SKILL.md"
        skill.write_text(skill.read_text() + "\nmore\n")
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "-qm", "unbumped")
        result = self.release("--dry-run")
        self.assertEqual(result.returncode, 0, "the bump check is advisory on a dry run")
        self.assertIn("changed since", result.stderr)

    def test_dry_run_counts_a_domain_plugin_on_as_drift(self) -> None:
        self.installed(self.version)
        self.config.write_text(f'[plugins."{RESEARCH}"]\nenabled = true\n')
        result = self.release("--dry-run")
        self.assertEqual(result.returncode, 1)
        self.assertIn("the global Codex config has a domain plugin on", result.stderr)


if __name__ == "__main__":
    unittest.main()
