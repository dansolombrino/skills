"""Behavior of core's agent-fleet-orca helper against a fake Orca CLI."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "plugins/core/skills/agent-fleet-orca/scripts/orca_fleet.py"

FAKE_ORCA = r'''
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_ORCA_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
responses = json.loads(open(os.environ["FAKE_ORCA_RESPONSES"]).read())
reply = responses.get(" ".join(args[:2]), {"stdout": {"ok": False, "error": {"code": "unknown", "message": "no canned reply"}}, "exit": 1})
out = reply["stdout"]
print(out if isinstance(out, str) else json.dumps(out))
if reply.get("stderr"):
    print(reply["stderr"], file=sys.stderr)
sys.exit(reply.get("exit", 0))
'''

REPO = "repo-1"
MAIN = {"id": f"{REPO}::/src/app", "path": "/src/app", "displayName": "main", "isMainWorktree": True,
        "parentWorktreeId": None, "branch": "refs/heads/main"}
L0 = {"id": f"{REPO}::/wt/l0-alpha", "path": "/wt/l0-alpha", "displayName": "l0-alpha",
      "isMainWorktree": False, "parentWorktreeId": None, "branch": "refs/heads/l0-alpha"}


def load_fleet():
    spec = importlib.util.spec_from_file_location("orca_fleet", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ok(result: dict) -> dict:
    return {"stdout": {"id": "x", "ok": True, "result": result}}


class AgentFleetOrcaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_fleet()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        fake = self.dir / "fake_orca.py"
        fake.write_text(FAKE_ORCA)
        self.log = self.dir / "orca.log"
        self.responses = self.dir / "responses.json"
        self.state_root = self.dir / "state"
        self.brief = self.dir / "brief-src.md"
        self.brief.write_text("# brief\n")
        env = mock.patch.dict(os.environ, {
            "ORCA_CLI_COMMAND": f"{shlex.quote(sys.executable)} {shlex.quote(str(fake))}",
            "ORCA_TERMINAL_HANDLE": "term_1",
            "ORCA_WORKTREE_ID": MAIN["id"],
            "AGENT_FLEET_STATE_DIR": str(self.state_root),
            "FAKE_ORCA_LOG": str(self.log),
            "FAKE_ORCA_RESPONSES": str(self.responses),
        })
        env.start()
        self.addCleanup(env.stop)
        self.reply(current=MAIN)

    # helpers ---------------------------------------------------------------------------------

    def reply(self, *, current: dict, run_id: str | None = None, **extra: dict) -> None:
        canned = {
            "worktree current": ok({"worktree": current}),
            "orchestration run-current": {"stdout": {"ok": True, "result": {"runId": run_id}}},
            "orchestration run-use": {"stdout": {"ok": True, "result": {"runId": run_id}}},
        }
        canned.update({key.replace("_", " ", 1).replace("_", "-"): value for key, value in extra.items()})
        self.responses.write_text(json.dumps(canned))

    def cli(self, *args: str) -> tuple[int, dict]:
        out = StringIO()
        with redirect_stdout(out):
            code = self.module.main(list(args))
        return code, json.loads(out.getvalue())

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def mutating(self) -> list[list[str]]:
        """Calls other than the read-only `worktree current` and `orchestration run-current`."""
        reads = (["worktree", "current"], ["orchestration", "run-current"])
        return [call for call in self.calls() if call[:2] not in reads]

    def group_dir(self, group: str = "alpha") -> Path:
        return self.state_root / REPO / group

    def write_state(self, **fields) -> None:
        state = {"version": 1, "group": "alpha", "repo_id": REPO, "workspace_id": None,
                 "worktree_path": None, "created_at": "2026-01-01T00:00:00+00:00",
                 "run_id": None, "items": {}}
        state.update(fields)
        self.group_dir().mkdir(parents=True, exist_ok=True)
        (self.group_dir() / "state.json").write_text(json.dumps(state))

    def read_state(self) -> dict:
        return json.loads((self.group_dir() / "state.json").read_text())

    def in_l0_terminal(self, run_id: str | None = None) -> None:
        os.environ["ORCA_WORKTREE_ID"] = L0["id"]
        self.reply(current=L0, run_id=run_id)

    def bootstrap(self, *extra: str) -> tuple[int, dict]:
        return self.cli("bootstrap", "--group", "alpha", "--brief", str(self.brief), "--agent", "claude", *extra)

    # bootstrap -------------------------------------------------------------------------------

    def test_bootstrap_creates_top_level_l0_workspace(self) -> None:
        # The terminal belongs to another repo than the cwd: state follows the cwd's repo,
        # which is the one `worktree create` uses.
        os.environ["ORCA_WORKTREE_ID"] = "other-repo::/elsewhere"
        self.reply(current=MAIN, worktree_create=ok({"worktree": L0, "launch": {"worktreeId": L0["id"]}}))
        code, out = self.bootstrap("--branch-prefix", "fleet", "--model", "opus")
        self.assertEqual(code, 0, out)
        [create] = self.mutating()
        self.assertEqual(create[:4], ["worktree", "create", "--name", "l0-alpha"])
        self.assertIn("--no-parent", create)
        self.assertEqual(create[create.index("--repo") + 1], f"id:{REPO}")
        self.assertIn("--activate", create)
        self.assertEqual(create[create.index("--branch") + 1], "fleet/l0-alpha")
        self.assertEqual(create[create.index("--model") + 1], "opus")
        self.assertNotIn("--base-branch", create)
        brief = (self.group_dir() / "brief.md").absolute()
        prompt = create[create.index("--prompt") + 1]
        self.assertIn(f"fleet group alpha. Your brief is {brief}.", prompt)
        self.assertEqual(brief.read_text(), "# brief\n")
        self.assertTrue((self.group_dir() / "specs").is_dir())
        state = self.read_state()
        self.assertEqual((state["workspace_id"], state["worktree_path"]), (L0["id"], L0["path"]))
        self.assertEqual(out["workspace_id"], L0["id"])

    def test_bootstrap_without_prefix_passes_no_branch(self) -> None:
        self.reply(current=MAIN, worktree_create=ok({"lineage": None, "launch": {"worktreeId": L0["id"]}}))
        code, out = self.bootstrap("--base-branch", "origin/main")
        self.assertEqual(code, 0, out)
        [create] = self.mutating()
        self.assertNotIn("--branch", create)
        self.assertEqual(create[create.index("--base-branch") + 1], "origin/main")
        self.assertEqual(self.read_state()["worktree_path"], L0["path"])

    def test_dry_run_creates_nothing(self) -> None:
        code, out = self.bootstrap("--dry-run", "--branch-prefix", "fleet")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["dry_run"])
        self.assertIn("--no-parent", out["argv"])
        self.assertIsNone(out["state"]["workspace_id"])
        self.assertEqual(self.mutating(), [])
        self.assertFalse(self.state_root.exists())

    def test_bootstrap_refuses_existing_group(self) -> None:
        self.write_state(workspace_id=L0["id"])
        code, out = self.bootstrap()
        self.assertEqual((code, out["error"]), (1, "group-exists"))
        self.assertIn("l0-alpha", out["next"])
        self.assertEqual(self.mutating(), [])

    def test_bootstrap_refuses_group_whose_workspace_id_was_not_recorded(self) -> None:
        # An earlier create whose reply carried no id leaves state with workspace_id null;
        # a second bootstrap would create a duplicate l0 workspace.
        self.write_state(workspace_id=None)
        code, out = self.bootstrap()
        self.assertEqual((code, out["error"]), (1, "group-exists"))
        self.assertIn("resume-check", out["next"])
        self.assertEqual(self.mutating(), [])

    def test_create_failure_removes_new_state_dir(self) -> None:
        failure = {"stdout": {"ok": False, "error": {"code": "invalid_argument", "message": "bad agent"}},
                   "stderr": "error: invalid_argument", "exit": 1}
        self.reply(current=MAIN, worktree_create=failure)
        code, out = self.bootstrap()
        self.assertEqual((code, out["error"]), (1, "orca-cli-failed"))
        self.assertIn("bad agent", out["message"])
        self.assertIn("stderr: error: invalid_argument", out["message"])
        self.assertFalse(self.group_dir().exists())

    def test_bad_group_is_refused(self) -> None:
        code, out = self.cli("bootstrap", "--group", "Bad_Name", "--brief", str(self.brief), "--agent", "claude")
        self.assertEqual((code, out["error"]), (1, "bad-group"))

    # resume-check ----------------------------------------------------------------------------

    def test_resume_check_adopts_null_workspace(self) -> None:
        self.write_state()
        self.in_l0_terminal()
        code, out = self.cli("resume-check")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["adopted"])
        self.assertFalse(out["rebound"])
        state = self.read_state()
        self.assertEqual((state["workspace_id"], state["worktree_path"]), (L0["id"], L0["path"]))

    def test_resume_check_refuses_wrong_terminal(self) -> None:
        self.write_state(workspace_id=L0["id"])
        self.reply(current=L0)  # cwd is in l0-alpha, the terminal belongs to the main checkout
        code, out = self.cli("resume-check")
        self.assertEqual((code, out["error"]), (1, "wrong-terminal"))
        self.assertIn("open a terminal tab in workspace l0-alpha", out["next"].lower())

    def test_resume_check_outside_l0_workspace(self) -> None:
        code, out = self.cli("resume-check")
        self.assertEqual((code, out["error"]), (1, "not-l0-workspace"))

    def test_resume_check_without_state(self) -> None:
        self.in_l0_terminal()
        code, out = self.cli("resume-check")
        self.assertEqual((code, out["error"]), (1, "no-state"))
        self.assertIn("adopt", out["next"])

    def test_run_use_only_when_unbound_with_stored_run(self) -> None:
        self.write_state(workspace_id=L0["id"], run_id="run_a")
        self.in_l0_terminal(run_id=None)
        code, out = self.cli("resume-check")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["rebound"])
        self.assertIn(["orchestration", "run-use", "--id", "run_a", "--json"], self.calls())

        self.log.unlink()
        self.in_l0_terminal(run_id="run_a")
        code, out = self.cli("resume-check")
        self.assertEqual(code, 0, out)
        self.assertFalse(out["rebound"])
        self.assertNotIn("run-use", [call[1] for call in self.calls()])

        self.log.unlink()
        self.write_state(workspace_id=L0["id"], run_id=None)
        self.in_l0_terminal(run_id=None)
        code, out = self.cli("resume-check")
        self.assertEqual((code, out["run_id"]), (0, None))
        self.assertNotIn("run-use", [call[1] for call in self.calls()])

    def test_run_use_failure_is_reported(self) -> None:
        self.write_state(workspace_id=L0["id"], run_id="run_a")
        os.environ["ORCA_WORKTREE_ID"] = L0["id"]
        failure = {"stdout": {"ok": False, "error": {"code": "not_found", "message": "run ended"}}, "exit": 1}
        self.reply(current=L0, orchestration_run_use=failure)
        code, out = self.cli("resume-check")
        self.assertEqual((code, out["error"]), (1, "run-use-failed"))
        self.assertIn("run ended", out["message"])

    def test_resume_check_refuses_other_run(self) -> None:
        self.write_state(workspace_id=L0["id"], run_id="run_a")
        self.in_l0_terminal(run_id="run_b")
        code, out = self.cli("resume-check")
        self.assertEqual((code, out["error"]), (1, "bound-to-other-run"))
        self.assertEqual(self.read_state()["run_id"], "run_a")

    def test_resume_check_records_run_when_state_has_none(self) -> None:
        self.write_state(workspace_id=L0["id"])
        self.in_l0_terminal(run_id="run_c")
        code, out = self.cli("resume-check")
        self.assertEqual((code, out["run_id"]), (0, "run_c"))
        self.assertEqual(self.read_state()["run_id"], "run_c")

    # outside Orca, adopt, records ------------------------------------------------------------

    def test_outside_orca_is_refused(self) -> None:
        del os.environ["ORCA_TERMINAL_HANDLE"]
        for args in (["resume-check"], ["bootstrap", "--group", "alpha", "--brief", str(self.brief),
                                        "--agent", "claude"], ["adopt", "--group", "alpha"]):
            code, out = self.cli(*args)
            self.assertEqual((code, out["error"]), (1, "outside-orca"), args)
        code, out = self.cli("detect")
        self.assertEqual(code, 0)
        self.assertEqual((out["in_orca"], out["role_hint"]), (False, "outside-orca"))
        self.assertEqual(self.calls(), [])

    def test_detect_reports_l0_workspace_and_state(self) -> None:
        self.write_state(workspace_id=L0["id"])
        self.in_l0_terminal()
        code, out = self.cli("detect")
        self.assertEqual(code, 0)
        self.assertEqual((out["role_hint"], out["group"]), ("l0-workspace", "alpha"))
        self.assertEqual(out["state"]["workspace_id"], L0["id"])
        self.reply(current=MAIN)
        self.assertEqual(self.cli("detect")[1]["role_hint"], "main-checkout")

    def test_adopt_refuses_main_checkout(self) -> None:
        code, out = self.cli("adopt", "--group", "alpha")
        self.assertEqual((code, out["error"]), (1, "main-checkout"))
        self.assertFalse(self.group_dir().exists())

    def test_adopt_hand_made_workspace(self) -> None:
        self.in_l0_terminal()
        code, out = self.cli("adopt", "--group", "alpha", "--brief", str(self.brief))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.read_state()["workspace_id"], L0["id"])
        self.assertTrue((self.group_dir() / "brief.md").is_file())
        code, out = self.cli("adopt", "--group", "beta")
        self.assertEqual((code, out["error"]), (1, "not-l0-workspace"))

    def test_record_run_and_worker_merge_into_state(self) -> None:
        self.write_state(workspace_id=L0["id"])
        self.in_l0_terminal(run_id=None)
        code, out = self.cli("record-run")
        self.assertEqual((code, out["error"]), (1, "no-run"))
        self.assertEqual(self.cli("record-run", "--run", "run_d")[0], 0)
        self.assertEqual(self.read_state()["run_id"], "run_d")
        self.cli("record-worker", "--item", "a", "--dispatch", "d1", "--agent", "codex")
        self.cli("record-worker", "--item", "a", "--dispatch", "d2", "--status", "done")
        self.assertEqual(self.read_state()["items"]["a"], {"dispatch": "d2", "agent": "codex", "status": "done"})
        code, out = self.cli("show")
        self.assertEqual((code, out["state"]["run_id"]), (0, "run_d"))

    # CLI resolution --------------------------------------------------------------------------

    def test_cli_resolution_never_picks_bare_orca_on_linux(self) -> None:
        del os.environ["ORCA_CLI_COMMAND"]
        which = {"orca": "/usr/bin/orca", "orca-ide": "/opt/orca/orca-ide"}
        with mock.patch.object(self.module.sys, "platform", "linux"), \
                mock.patch.object(self.module.shutil, "which", side_effect=which.get):
            self.assertEqual(self.module.resolve_cli(), ["/opt/orca/orca-ide"])
        with mock.patch.object(self.module.sys, "platform", "linux"), \
                mock.patch.object(self.module.shutil, "which", side_effect={"orca": "/usr/bin/orca"}.get):
            self.assertIsNone(self.module.resolve_cli())
        with mock.patch.object(self.module.sys, "platform", "darwin"), \
                mock.patch.object(self.module.shutil, "which", side_effect=which.get):
            self.assertEqual(self.module.resolve_cli(), ["/usr/bin/orca"])

    def test_usage_errors_are_json(self) -> None:
        code, out = self.cli("bootstrap", "--group", "alpha")
        self.assertEqual((code, out["error"]), (1, "usage"))


if __name__ == "__main__":
    unittest.main()
