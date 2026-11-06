"""The research PreToolUse guard: what it flags, what it must not, its modes, and how it is wired."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/research"
GUARD = PLUGIN / "hooks/guard.py"

spec = importlib.util.spec_from_file_location("research_guard", GUARD)
guard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = guard
spec.loader.exec_module(guard)

CTX = {"cwd": "/home/me/project", "scratch": ("/tmp/", "/home/me/scratch/")}


def rules(command: str) -> set[str]:
    return set(guard.check_command(command, CTX))


class BashRuleTests(unittest.TestCase):
    def test_flagged(self) -> None:
        cases = {
            "git push --force": "git-push-force",
            "git push origin main -f": "git-push-force",
            "git push -uf origin main": "git-push-force",
            "git push --force-with-lease origin main": "git-push-force",
            "git push origin +main": "git-push-force",
            "git -C /some/repo push --force": "git-push-force",
            "git reset --hard HEAD~1": "git-reset-hard",
            "cd sub && git reset --hard": "git-reset-hard",
            "git clean -fdx": "git-clean",
            "git stash": "git-stash",
            "git stash pop": "git-stash",
            "git commit --no-verify -m x": "git-commit-no-verify",
            "git commit -n -m x": "git-commit-no-verify",
            "git commit -nm x": "git-commit-no-verify",
            "git commit -am x -n": "git-commit-no-verify",
            "rm -rf checkpoints/000_x": "rm-mv-run-data",
            "rm -rf ./evaluations": "rm-mv-run-data",
            "sudo rm -rf evaluations/000_x/rid-abc": "rm-mv-run-data",
            "mv evaluations/000_x/rid-abc elsewhere": "rm-mv-run-data",
            "mv rid-abc rid-def": "rm-mv-run-data",
            "rm -- rid-abc": "rm-mv-run-data",
            "/bin/rm -r checkpoints": "rm-mv-run-data",
            "find checkpoints/000_x -name '*.pt' -delete": "rm-mv-run-data",
            "find evaluations -type d -exec rm -rf {} +": "rm-mv-run-data",
            "scp model.pt rig-4090:/data": "remote-copy",
            "scp user@rig-4090:/data/model.pt .": "remote-copy",
            "rsync -av -e 'ssh -p 22' a/ rig-4090:/data/": "remote-copy",
            "python3 scripts/runid.py --project . migrate rollback 2026 --force": "runid-rollback-force",
            "runid migrate rollback 2026 --force": "runid-rollback-force",
            "uv run runid.py migrate rollback 2026 --force": "runid-rollback-force",
            "scancel 1234": "kill-by-hand",
            "kill -9 1234": "kill-by-hand",
            "pkill -f train": "kill-by-hand",
            "pgrep train | xargs kill": "kill-by-hand",
            "cat .env": "read-env",
            "cat ./config/.env.production": "read-env",
            "grep TOKEN .env": "read-env",
            "grep -e TOKEN .env": "read-env",
            "sed -n 1p .env": "read-env",
            "cat < .env": "read-env",
            "echo '{}' > .waves/_state/20260908-101500/queue.json": "wave-state",
            "echo '{}' >> ~/board/lanes/rig-4090/gpu0.json": "board-lane",
            "rm .waves/_state/20260908-101500/queue.json": "wave-state",
            "echo x > evaluations/000_x/rid-a/.run_id.json": "run-record",
        }
        for command, rule in cases.items():
            with self.subTest(command=command):
                self.assertIn(rule, rules(command))

    def test_ssh_payloads(self) -> None:
        cases = {
            "ssh rig-4090 'rm -rf checkpoints/000_x'": "ssh:rm-mv-run-data",
            "ssh rig-4090 rm -rf checkpoints/000_x": "ssh:rm-mv-run-data",
            "ssh -o BatchMode=yes -p 22 rig-4090 'cd proj && git reset --hard'": "ssh:git-reset-hard",
            'ssh rig-4090 "pkill -f train"': "ssh:kill-by-hand",
            "ssh leonardo 'scancel 12'": "ssh:kill-by-hand",
            "ssh rig-4090 bash -c \"'git stash'\"": "ssh:git-stash",
            "ssh rig-4090 cat proj/.env": "ssh:read-env",
            "ssh rig-4090 bash -s <<'EOF'\ncd proj\nkill 12\nEOF": "ssh:kill-by-hand",
            "ssh rig-4090 <<EOF\nrm -rf evaluations\nEOF": "ssh:rm-mv-run-data",
        }
        for command, rule in cases.items():
            with self.subTest(command=command):
                self.assertIn(rule, rules(command))

    def test_shell_indirection(self) -> None:
        cases = {
            "bash -c 'git stash'": "git-stash",
            "sh -lc 'rm -rf checkpoints'": "rm-mv-run-data",
            "eval 'git reset --hard'": "git-reset-hard",
            "bash <<'EOF'\ngit clean -fd\nEOF": "git-clean",
            "bash <<< 'kill 1'": "kill-by-hand",
            "(cd x && git stash)": "git-stash",
            "if true; then git stash; fi": "git-stash",
            "X=1 git stash": "git-stash",
            "nohup timeout 5 kill 12 &": "kill-by-hand",
            "echo $(git stash)": "git-stash",
            'echo "$(git reset --hard)"': "git-reset-hard",
            "echo `kill 1`": "kill-by-hand",
            "cmd1; git stash\ncmd2": "git-stash",
            "cmd1 | git stash": "git-stash",
            "a \\\n && git stash": "git-stash",
        }
        for command, rule in cases.items():
            with self.subTest(command=command):
                self.assertIn(rule, rules(command))

    def test_words_that_only_mention_a_rule_do_not_match(self) -> None:
        commands = [
            'git commit -m "never use git reset --hard or git push --force"',
            "git commit -m 'rm -rf checkpoints/ and kill 12'",
            "git commit -m 'x\n\ngit stash\nrm -rf evaluations\n'",
            "git commit -m \"$(cat <<'EOF'\nfix: don't git stash, or rm -rf checkpoints/\n\nssh rig 'kill 1'\nEOF\n)\"",
            "git commit -F - <<'EOF'\ngit push --force\nrm -rf checkpoints\nEOF",
            'git commit -m "-n"',
            "git commit -m -n",
            "git commit --message=-n",
            "git commit -am 'no verify'",
            "git commit -mnope",
            "git commit -- -n",
            "git commit --amend --no-edit",
            'grep -rn "git reset --hard" plugins/',
            'grep -rn "kill -9" .',
            "grep -n 'rm -rf checkpoints' README.md",
            "rg 'git stash' -g '*.md'",
            "echo git stash",
            "echo rm -rf checkpoints",
            "echo 'scp a host:b'",
            "printf '%s\\n' 'git push --force'",
            "cat <<EOF > notes.md\ngit reset --hard\nrm -rf checkpoints\nkill 12\nEOF",
            "python3 -c 'print(\"git stash\")'",
            "git log --grep='git reset --hard'",
            "git log -S'git stash'",
            "gh pr create --body 'git push --force is forbidden'",
            "tmux new -d -s kill-session sleep 1",
            "# git stash\nls",
            "ls  # git reset --hard",
            "ssh rig-4090 'grep -rn \"git stash\" .'",
            "ssh rig-4090 nvidia-smi",
            "ssh rig-4090",
            "ssh rig-4090 'echo rm -rf checkpoints'",
            "which kill",
            "command -v kill",
            "man scp",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(rules(command), set())

    def test_read_only_and_local_forms_pass(self) -> None:
        commands = [
            "git push origin main",
            "git push --follow-tags",
            "git push origin main:main",
            "git reset HEAD file",
            "git reset --soft HEAD~1",
            "git clean -n",
            "git clean -dn",
            "git clean --dry-run",
            "git stash list",
            "git stash show -p",
            "git checkout -b checkpoints",
            "git rm --cached checkpoints",
            "ls checkpoints/",
            "du -sh evaluations/",
            "cp -r evaluations/x /tmp/y",
            "rm -rf build/ checkpoints_old/ my-evaluations",
            "rm -rf /tmp/t/checkpoints",
            "rm -rf /home/me/scratch/evaluations/x",
            "find checkpoints -name '*.pt'",
            "mv a.txt b.txt",
            "rsync -a a/ b/",
            "rsync -a --exclude 'x:y' a/ b/",
            "scp a b",
            "kill -0 1234",
            "kill -l",
            "python3 runid.py migrate rollback 2026 --dry-run",
            "python3 runid.py migrate rollback 2026 --confirm",
            "python3 train.py --force rollback",
            "echo runid rollback --force",
            "ls -a .env",
            "test -f .env",
            "[ -f .env ]",
            "source .env",
            ". .env",
            "set -a; source .env; set +a",
            "cat .env.example",
            "cat .env.sample",
            "cat .envrc",
            "cat env",
            "cp .env.example .env",
            "grep .env .gitignore",
            "grep -n '.env' README.md",
            "sed 's/.env/x/' notes.md",
            "rg .env",
            "echo x > /dev/null",
            "echo x > notes/queue.json",
            "echo x 2>&1 | tee out.log",
            "python3 plugins/research/skills/rig-sync/scripts/rigsync.py prune --confirm",
            "python3 plugins/research/skills/rig-board/scripts/board.py status",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(rules(command), set())

    def test_odd_input_never_raises(self) -> None:
        for command in ("", "'", '"', "$(", "`", "(((", ")))", "<<", "<<EOF", "a <", "\\", "$(echo '", "&&&", "|", ">"):
            with self.subTest(command=command):
                rules(command)

    def test_a_scratch_path_inside_the_project_is_still_the_project(self) -> None:
        ctx = {"cwd": "/tmp/project", "scratch": ("/tmp/",)}
        self.assertIn("rm-mv-run-data", guard.check_command("rm -rf /tmp/project/checkpoints", ctx))
        self.assertEqual(guard.check_command("rm -rf /tmp/other/checkpoints", ctx), [])


class FileRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name in ("SWEEP_SUPERVISOR_SESSION", "SWEEP_SUPERVISOR_MODEL"):
            os.environ.pop(name, None)

    def test_records_state_and_lanes(self) -> None:
        cases = {
            "/p/evaluations/e/rid-a/.run_id.json": "run-record",
            "/p/evaluations/e/rid-a/.run_config.json": "run-record",
            "/p/evaluations/e/RUN_ID_MAP.json": "run-record",
            "/p/config/e/RUN_ID_HISTORY.json": "run-record",
            "/p/checkpoints/e/rid-a.offloaded.json": "run-record",
            "/p/.waves/_state/20260908-101500/queue.json": "wave-state",
            "/board/lanes/rig-4090/gpu0.json": "board-lane",
            "/board/lanes/rig-4090/gpu0,1.json": "board-lane",
        }
        for path, rule in cases.items():
            with self.subTest(path=path):
                self.assertEqual(guard.check_edit(path), [rule])

    def test_unrelated_files_pass(self) -> None:
        for path in ("/p/src/train.py", "/p/.waves/README.md", "/p/waves/_state/x", "/p/lanes/notes.md", "/p/lanes/a/notes.json", "/p/run_id.json", "/p/config/e/config.yaml"):
            with self.subTest(path=path):
                self.assertEqual(guard.check_edit(path), [])

    def test_run_id_versions_only_once_outputs_exist(self) -> None:
        for experiment in ("000_x", "000_x/001_y"):
            with self.subTest(experiment=experiment):
                root = self.root / experiment.replace("/", "-")
                versions = root / "config" / experiment / "RUN_ID_VERSIONS.json"
                versions.parent.mkdir(parents=True)
                self.assertEqual(guard.check_edit(str(versions)), [])
                (root / "evaluations" / experiment).mkdir(parents=True)
                (root / "evaluations" / experiment / ".gitkeep").write_text("")
                self.assertEqual(guard.check_edit(str(versions)), [], "an empty area is no output")
                (root / "checkpoints" / experiment / "rid-a").mkdir(parents=True)
                self.assertEqual(guard.check_edit(str(versions)), ["run-id-versions"])

    def test_another_experiments_outputs_do_not_count(self) -> None:
        versions = self.root / "config/000_x/RUN_ID_VERSIONS.json"
        versions.parent.mkdir(parents=True)
        (self.root / "evaluations/001_y/rid-a").mkdir(parents=True)
        self.assertEqual(guard.check_edit(str(versions)), [])

    def wave(self, finished: bool) -> Path:
        queue = self.root / ".waves/_state/20260908-101500/queue.json"
        queue.parent.mkdir(parents=True, exist_ok=True)
        queue.write_text(json.dumps({"finished_at": "2026-09-08" if finished else None}))
        return self.root / "EXPERIMENTS.md"

    def test_experiments_only_during_a_live_wave(self) -> None:
        self.assertEqual(guard.check_edit(str(self.root / "EXPERIMENTS.md")), [])
        self.assertEqual(guard.check_edit(str(self.wave(finished=True))), [])
        self.assertEqual(guard.check_edit(str(self.wave(finished=False))), ["experiments-during-wave"])

    def test_a_supervisor_agent_may_edit_experiments(self) -> None:
        # The session variable is set only when the configured command takes {session}; the model always is.
        path = self.wave(finished=False)
        for name in ("SWEEP_SUPERVISOR_SESSION", "SWEEP_SUPERVISOR_MODEL"):
            with self.subTest(variable=name):
                os.environ[name] = "x"
                self.addCleanup(os.environ.pop, name, None)
                self.assertEqual(guard.check_edit(str(path)), [])
                del os.environ[name]

    def test_a_broken_queue_is_not_a_live_wave(self) -> None:
        queue = self.root / ".waves/_state/20260908-101500/queue.json"
        queue.parent.mkdir(parents=True)
        queue.write_text("{not json")
        self.assertEqual(guard.check_edit(str(self.root / "EXPERIMENTS.md")), [])

    def test_read_tool_only_flags_env(self) -> None:
        ctx = {"cwd": "/p", "scratch": ()}
        self.assertEqual(guard.check_call("Read", {"file_path": "/p/.env"}, ctx)[0], ["read-env"])
        self.assertEqual(guard.check_call("Read", {"file_path": "/p/.env.local"}, ctx)[0], ["read-env"])
        for path in ("/p/.env.example", "/p/README.md", "/p/.run_id.json", "/p/.waves/_state/x/queue.json"):
            self.assertEqual(guard.check_call("Read", {"file_path": path}, ctx)[0], [], path)

    def test_a_write_logs_its_path_never_its_content(self) -> None:
        hits, logged = guard.check_call("Write", {"file_path": "/p/.run_id.json", "content": "SECRET"}, {"cwd": "/p", "scratch": ()})
        self.assertEqual((hits, logged), (["run-record"], "/p/.run_id.json"))


class HookRunTests(unittest.TestCase):
    """The script as the host runs it: JSON on stdin, decision on stdout, always exit 0."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.project = self.home / "project"
        self.project.mkdir()
        (self.project / ".project.toml").write_text('categories = ["research"]\n')
        self.env = {key: value for key, value in os.environ.items() if key not in ("RESEARCH_GUARD", "SWEEP_SUPERVISOR_SESSION", "SWEEP_SUPERVISOR_MODEL", "CLAUDE_PROJECT_DIR")}
        self.env.update(XDG_STATE_HOME=str(self.home / "state"), XDG_CONFIG_HOME=str(self.home / "config"))
        self.log = self.home / "state/research-guard/guard.jsonl"

    def payload(self, command: str = "git stash", **overrides) -> dict:
        return {"session_id": "s1", "cwd": str(self.project), "permission_mode": "bypassPermissions", "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}, "tool_use_id": "t1", **overrides}

    def run_hook(self, stdin: str | dict, **env) -> subprocess.CompletedProcess:
        text = stdin if isinstance(stdin, str) else json.dumps(stdin)
        result = subprocess.run([sys.executable, str(GUARD)], input=text, capture_output=True, text=True, env={**self.env, **env}, cwd=self.home, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result

    def entries(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_log_only_is_the_default_and_the_call_proceeds(self) -> None:
        result = self.run_hook(self.payload())
        self.assertEqual(result.stdout, "")
        entry = self.entries()[0]
        self.assertEqual(entry["decision"], "logged")
        self.assertEqual(entry["rules"], ["git-stash"])
        self.assertEqual(entry["input"], "git stash")
        self.assertEqual(entry["permission_mode"], "bypassPermissions")
        self.assertFalse(entry["supervisor"])
        self.assertEqual(self.log.stat().st_mode & 0o777, 0o600)

    def test_a_clean_call_logs_nothing(self) -> None:
        self.assertEqual(self.run_hook(self.payload("ls -la")).stdout, "")
        self.assertEqual(self.entries(), [])

    def assert_denied(self, result: subprocess.CompletedProcess) -> None:
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("git-stash", output["permissionDecisionReason"])

    def test_enforce_by_environment(self) -> None:
        self.assert_denied(self.run_hook(self.payload(), RESEARCH_GUARD="enforce"))
        self.assertEqual(self.entries()[0]["decision"], "denied")

    def test_enforce_by_mode_file_and_the_environment_wins(self) -> None:
        mode = self.home / "config/research-guard/mode"
        mode.parent.mkdir(parents=True)
        mode.write_text("enforce\n")
        self.assert_denied(self.run_hook(self.payload()))
        self.assertEqual(self.run_hook(self.payload(), RESEARCH_GUARD="log").stdout, "")

    def test_off_does_nothing(self) -> None:
        self.assertEqual(self.run_hook(self.payload(), RESEARCH_GUARD="off").stdout, "")
        self.assertEqual(self.entries(), [])

    def test_unknown_mode_means_log_only(self) -> None:
        self.assertEqual(self.run_hook(self.payload(), RESEARCH_GUARD="yes").stdout, "")
        self.assertEqual(self.entries()[0]["decision"], "logged")

    def test_only_in_projects_tagged_research(self) -> None:
        (self.project / ".project.toml").write_text('categories = ["web"]\n')
        self.assertEqual(self.run_hook(self.payload(), RESEARCH_GUARD="enforce").stdout, "")
        (self.project / ".project.toml").unlink()
        self.assertEqual(self.run_hook(self.payload(), RESEARCH_GUARD="enforce").stdout, "")
        self.assertEqual(self.entries(), [])

    def test_the_project_directory_counts_after_a_cd_elsewhere(self) -> None:
        elsewhere = self.home / "elsewhere"
        elsewhere.mkdir()
        self.run_hook(self.payload(cwd=str(elsewhere)), CLAUDE_PROJECT_DIR=str(self.project))
        self.assertEqual(len(self.entries()), 1)

    def test_an_edit_in_a_tagged_project_counts_from_any_directory(self) -> None:
        elsewhere = self.home / "elsewhere"
        elsewhere.mkdir()
        call = self.payload(cwd=str(elsewhere), tool_name="Edit", tool_input={"file_path": str(self.project / ".run_id.json"), "old_string": "a", "new_string": "b"})
        self.run_hook(call)
        self.assertEqual(self.entries()[0]["rules"], ["run-record"])

    def test_read_of_env(self) -> None:
        self.run_hook(self.payload(tool_name="Read", tool_input={"file_path": str(self.project / ".env")}))
        self.assertEqual(self.entries()[0]["rules"], ["read-env"])

    def test_a_supervisor_run_is_marked(self) -> None:
        self.run_hook(self.payload(), SWEEP_SUPERVISOR_MODEL="sonnet")
        self.assertTrue(self.entries()[0]["supervisor"])

    def test_fails_open(self) -> None:
        for stdin in ("", "not json", "[]", "null", json.dumps({"tool_name": "Bash"}), json.dumps(self.payload(tool_input="x")), json.dumps(self.payload(tool_input={})), json.dumps(self.payload(tool_input={"command": 3})), json.dumps(self.payload(cwd=3)), json.dumps(self.payload(hook_event_name="PostToolUse")), json.dumps(self.payload(tool_name="NewTool", tool_input={"anything": 1}))):
            with self.subTest(stdin=stdin):
                self.assertEqual(self.run_hook(stdin, RESEARCH_GUARD="enforce").stdout, "")
        self.assertEqual(self.entries(), [])

    def test_a_broken_tag_file_fails_open(self) -> None:
        (self.project / ".project.toml").write_text("categories = [")
        self.assertEqual(self.run_hook(self.payload(), RESEARCH_GUARD="enforce").stdout, "")

    def test_an_unwritable_log_does_not_change_the_decision(self) -> None:
        blocked = self.home / "state"
        blocked.write_text("a file where the directory should be")
        self.assertEqual(self.run_hook(self.payload()).stdout, "")
        self.assert_denied(self.run_hook(self.payload(), RESEARCH_GUARD="enforce"))

    def test_long_commands_are_truncated_in_the_log(self) -> None:
        self.run_hook(self.payload("git stash; echo " + "x" * 5000))
        self.assertEqual(len(self.entries()[0]["input"]), guard.MAX_LOGGED_INPUT)

    def cli(self, *args: str) -> str:
        result = subprocess.run([sys.executable, str(GUARD), *args], capture_output=True, text=True, env=self.env, cwd=self.home, check=True)
        return result.stdout

    def test_report_summarizes_per_rule(self) -> None:
        self.assertIn("no entries", self.cli("report"))
        self.run_hook(self.payload())
        self.run_hook(self.payload("git stash pop"))
        self.run_hook(self.payload("ssh rig 'kill 1'"), RESEARCH_GUARD="enforce")
        output = self.cli("report")
        self.assertIn("3 entries", output)
        self.assertIn("2 logged, 1 denied", output)
        self.assertIn("git-stash: 2", output)
        self.assertIn("ssh:kill-by-hand: 1", output)
        self.assertIn("git stash pop", output)
        only = self.cli("report", "--rule", "ssh:kill-by-hand")
        self.assertIn("1 entries", only)
        self.assertNotIn("git stash", only)
        self.assertIn("3 entries", self.cli("report", "--days", "1"))

    def test_mode_command_sets_and_prints_the_mode(self) -> None:
        self.assertTrue(self.cli("mode").startswith("log"))
        self.cli("mode", "enforce")
        self.assertTrue(self.cli("mode").startswith("enforce"))
        self.assert_denied(self.run_hook(self.payload()))
        self.cli("mode", "off")
        self.assertEqual(self.run_hook(self.payload()).stdout, "")
        self.cli("mode", "log")
        self.assertEqual(self.run_hook(self.payload()).stdout, "")

    def test_help_says_how_to_use_it(self) -> None:
        output = self.cli("--help")
        for word in ("report", "mode", "not a security boundary", "RESEARCH_GUARD", "guard.jsonl"):
            self.assertIn(word, output.replace("\n", " ").replace("  ", " "))


class WiringTests(unittest.TestCase):
    def test_every_rule_used_has_a_reason(self) -> None:
        # A rule missing from RULES would raise while building the reason, and failing open would then let the call through.
        tree = ast.parse(GUARD.read_text())
        emitted = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.fullmatch(r"[a-z]+(-[a-z]+)+", node.value)}
        emitted -= {"heredoc-strip", "research-guard"}  # a redirect kind and the name in the reason
        self.assertLessEqual(emitted, set(guard.RULES))
        self.assertGreaterEqual(emitted, set(guard.RULES))

    def test_guard_loads_only_through_the_claude_manifest(self) -> None:
        claude = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text())
        hooks_file = PLUGIN / claude["hooks"]
        self.assertNotEqual(hooks_file.resolve(), (PLUGIN / "hooks/hooks.json").resolve(), "the default file auto-loads; naming it again would load it twice")
        spec_ = json.loads(hooks_file.read_text())
        self.assertEqual(set(spec_["hooks"]), {"PreToolUse"}, "the session-start hooks stay in hooks/hooks.json")
        group = spec_["hooks"]["PreToolUse"][0]
        self.assertEqual(set(group["matcher"].split("|")), {"Bash", "Edit", "Write", "Read"})
        self.assertEqual(group["hooks"][0]["command"], 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/guard.py"')
        self.assertNotIn("onFailure", group["hooks"][0], "the guard must fail open")
        self.assertNotIn("guard", (PLUGIN / "hooks/hooks.json").read_text())
        self.assertNotIn("hooks", json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text()))

    def test_it_uses_the_same_tag_check_as_the_session_note(self) -> None:
        self.assertIn("from orchestration_note import is_research_project", GUARD.read_text())


if __name__ == "__main__":
    unittest.main()
