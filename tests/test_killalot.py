from __future__ import annotations

import importlib.util
import io
import json
import multiprocessing
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "plugins/core/skills/resirch-killalot/scripts/killalot.py"
SPEC = importlib.util.spec_from_file_location("killalot", SCRIPT)
assert SPEC and SPEC.loader
killalot = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = killalot
SPEC.loader.exec_module(killalot)

OWNER = "owner@example"
VIA = "session:test/1"


def make_project(base: Path, *parts: str, categories: str = '["research"]') -> Path:
    path = base.joinpath(*parts)
    path.mkdir(parents=True)
    (path / ".project.toml").write_text(f"categories = {categories}\n")
    return path.resolve()


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.roots = self.base / "Projects"
        self.ladder = make_project(self.roots, "quantization", "ladder", "ladder")
        self.lottery = make_project(self.roots, "quantization", "lottery-qat", "lottery-qat")
        (self.roots / "untagged" / "repo").mkdir(parents=True)
        self.config_path = self.base / "config.toml"
        self.config_path.write_text(textwrap.dedent(f"""
            [store]
            root = "{self.base / 'store'}"
            [projects]
            roots = ["{self.roots}"]
            [web]
            port = 40999
            owner_login = "{OWNER}"
            public_base = "https://hub.example.ts.net"
        """))
        self.config = killalot.load_config(self.config_path)
        self.conn = killalot.connect(self.config)
        killalot.scan(self.conn, self.config)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def run_cli(self, *args: str, stdin: str | None = None) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        patches = [mock.patch.object(sys, "stdin", io.StringIO(stdin))] if stdin is not None else []
        for p in patches:
            p.start()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = killalot.main(["--config", str(self.config_path), *args])
        finally:
            for p in patches:
                p.stop()
        return code, out.getvalue() + err.getvalue()

    def propose(self, title: str = "Update EXPERIMENTS.md", project: Path | None = None, **kw) -> int:
        return killalot.propose(self.conn, project=str(project or self.ladder), kind=kw.pop("kind", "task"), title=title,
                                evidence=kw.pop("evidence", ["JOURNAL.md 2026-09-04"]), **kw)


class ScanTests(Fixture):
    def test_scan_finds_tagged_projects_only(self) -> None:
        names = {r["name"] for r in self.conn.execute("SELECT name FROM projects")}
        self.assertEqual(names, {"ladder", "lottery-qat"})

    def test_duplicate_names_are_disambiguated(self) -> None:
        make_project(self.roots, "other", "ladder")
        killalot.scan(self.conn, self.config)
        names = {r["name"] for r in self.conn.execute("SELECT name FROM projects")}
        self.assertIn("other/ladder", names)
        self.assertIn("ladder/ladder", names)

    def test_vanished_project_is_marked_missing_not_deleted(self) -> None:
        (self.lottery / ".project.toml").unlink()
        killalot.scan(self.conn, self.config)
        row = self.conn.execute("SELECT missing FROM projects WHERE path=?", (str(self.lottery),)).fetchone()
        self.assertEqual(row["missing"], 1)

    def test_skips_artifact_trees(self) -> None:
        make_project(self.ladder.parent, "checkpoints", "nested")  # inside a project: never visited
        make_project(self.roots, "x", "checkpoints", "hidden")
        killalot.scan(self.conn, self.config)
        names = {r["name"] for r in self.conn.execute("SELECT name FROM projects")}
        self.assertNotIn("hidden", names)

    def test_resolve_dot_walks_up_to_the_tag(self) -> None:
        sub = self.ladder / "code" / "000_x"
        sub.mkdir(parents=True)
        self.assertEqual(killalot.resolve_project(self.conn, ".", cwd=sub), str(self.ladder))
        with self.assertRaises(killalot.KillalotError):
            killalot.resolve_project(self.conn, ".", cwd=self.roots / "untagged" / "repo")


class RuleTests(Fixture):
    def test_agent_proposes_owner_accepts(self) -> None:
        item_id = self.propose()
        self.assertEqual(killalot.get_item(self.conn, item_id)["state"], "proposed")
        with self.assertRaisesRegex(killalot.KillalotError, "only the owner"):
            killalot.act(self.conn, item_id, "accept", actor="agent", via=None)
        item = killalot.act(self.conn, item_id, "accept", actor="me", via=VIA)
        self.assertEqual(item["state"], "accepted")

    def test_owner_action_needs_a_via(self) -> None:
        item_id = self.propose()
        with self.assertRaisesRegex(killalot.KillalotError, "--via"):
            killalot.act(self.conn, item_id, "accept", actor="me", via=None)
        with self.assertRaisesRegex(killalot.KillalotError, "--via"):
            killalot.act(self.conn, item_id, "accept", actor="me", via="whatever")

    def test_proposal_needs_evidence(self) -> None:
        with self.assertRaisesRegex(killalot.KillalotError, "evidence"):
            self.propose(evidence=[])

    def test_rejection_is_remembered(self) -> None:
        item_id = self.propose("Run the collapsed-scale audit")
        with self.assertRaisesRegex(killalot.KillalotError, "reason"):
            killalot.act(self.conn, item_id, "reject", actor="me", via=VIA)
        killalot.act(self.conn, item_id, "reject", actor="me", via=VIA, reason="not worth it")
        with self.assertRaisesRegex(killalot.KillalotError, "rejected this as #.*not worth it"):
            self.propose("run the collapsed-scale audit!")

    def test_duplicates_refused_while_open_but_allowed_after_done(self) -> None:
        item_id = self.propose("Write the report")
        with self.assertRaisesRegex(killalot.KillalotError, "already tracked"):
            self.propose("write  the REPORT")
        self.propose("Write the report", project=self.lottery)  # other project: fine
        killalot.act(self.conn, item_id, "accept", actor="me", via=VIA)
        killalot.act(self.conn, item_id, "done", actor="agent", via=None, evidence=["commit abc"])
        self.propose("Write the report")

    def test_agent_works_only_agent_items(self) -> None:
        mine = killalot.add(self.conn, project=str(self.ladder), kind="task", title="Read the figures", via=VIA)
        with self.assertRaisesRegex(killalot.KillalotError, "owner's own item"):
            killalot.act(self.conn, mine, "start", actor="agent", via=None)
        theirs = self.propose("Re-render plots", owner="agent")
        with self.assertRaisesRegex(killalot.KillalotError, "proposed"):
            killalot.act(self.conn, theirs, "start", actor="agent", via=None)
        killalot.act(self.conn, theirs, "accept", actor="me", via=VIA)
        killalot.act(self.conn, theirs, "start", actor="agent", via=None)
        item = killalot.act(self.conn, theirs, "done", actor="agent", via=None, evidence=["commit 123"])
        self.assertEqual(item["state"], "done")
        self.assertIn("commit 123", item["evidence"])

    def test_wait_then_answer_returns_to_accepted(self) -> None:
        item_id = self.propose()
        killalot.act(self.conn, item_id, "accept", actor="me", via=VIA)
        killalot.act(self.conn, item_id, "start", actor="agent", via=None)
        with self.assertRaisesRegex(killalot.KillalotError, "waiting for"):
            killalot.act(self.conn, item_id, "wait", actor="agent", via=None)
        killalot.act(self.conn, item_id, "wait", actor="agent", via=None, reason="which dataset?")
        self.assertIn(item_id, [i["id"] for i in killalot.inbox(self.conn)["blocked"]])
        item = killalot.act(self.conn, item_id, "answer", actor="me", via=VIA, answer="CIFAR10")
        self.assertEqual((item["state"], item["answer"]), ("accepted", "CIFAR10"))

    def test_decision_answer_closes_it(self) -> None:
        item_id = self.propose("Pick among (a)-(d)", kind="decision", owner="me")
        killalot.act(self.conn, item_id, "accept", actor="me", via=VIA)
        self.assertIn(item_id, [i["id"] for i in killalot.inbox(self.conn)["decisions"]])
        item = killalot.act(self.conn, item_id, "answer", actor="me", via=VIA, answer="(b)")
        self.assertEqual(item["state"], "done")

    def test_snooze_wakes_up_into_previous_state(self) -> None:
        item_id = self.propose()
        with self.assertRaisesRegex(killalot.KillalotError, "future"):
            killalot.act(self.conn, item_id, "snooze", actor="me", via=VIA, until=killalot.utcnow() - timedelta(hours=1))
        killalot.act(self.conn, item_id, "snooze", actor="me", via=VIA, until=killalot.utcnow() + timedelta(hours=1))
        self.assertEqual(killalot.get_item(self.conn, item_id)["state"], "snoozed")
        self.conn.execute("UPDATE items SET snooze_until=? WHERE id=?", (killalot.iso(killalot.utcnow() - timedelta(seconds=1)), item_id))
        killalot.list_items(self.conn)
        self.assertEqual(killalot.get_item(self.conn, item_id)["state"], "proposed")

    def test_due_items_surface_in_inbox(self) -> None:
        item_id = killalot.add(self.conn, project=str(self.ladder), kind="reminder", title="Renew certificate", via=VIA,
                               due_at=killalot.iso(killalot.utcnow() - timedelta(minutes=1)))
        self.assertIn(item_id, [i["id"] for i in killalot.inbox(self.conn)["due"]])

    def test_edit_updates_dedupe_key(self) -> None:
        item_id = self.propose("Old title")
        killalot.act(self.conn, item_id, "edit", actor="me", via=VIA, edits={"title": "New title"})
        self.propose("Old title")
        with self.assertRaisesRegex(killalot.KillalotError, "already tracked"):
            self.propose("New title")

    def test_events_are_append_only(self) -> None:
        self.propose()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE events SET actor='me'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM events")

    def test_every_change_is_an_event(self) -> None:
        item_id = self.propose()
        killalot.act(self.conn, item_id, "accept", actor="me", via=VIA)
        events = [(r["actor"], r["via"], r["action"], r["to_state"]) for r in self.conn.execute("SELECT * FROM events WHERE item=? ORDER BY id", (item_id,))]
        self.assertEqual(events, [("agent", None, "create", "proposed"), ("me", VIA, "accept", "accepted")])


class ApprovalTests(Fixture):
    DRY = "[dry-run] rig-4090: remove worktree /x/.waves/20260901-000000\n"

    def file(self, text: str) -> int:
        item_id, new = killalot.request_approval(self.conn, project=str(self.ladder), title="Prune wave", evidence_text=text,
                                                 command="rig-sync prune --confirm")
        return item_id

    def test_cli_and_agents_can_never_approve(self) -> None:
        item_id = self.file(self.DRY)
        fp = killalot.get_item(self.conn, item_id)["fingerprint"]
        for actor, via in (("agent", None), ("me", VIA), ("me", "terminal")):
            with self.assertRaisesRegex(killalot.KillalotError, "paired device"):
                killalot.act(self.conn, item_id, "approve", actor=actor, via=via, presented_fingerprint=fp)
        with self.assertRaisesRegex(killalot.KillalotError, "approval"):
            killalot.act(self.conn, item_id, "accept", actor="me", via=VIA)
        code, out = self.run_cli("approve", str(item_id))
        self.assertEqual(code, 2)
        self.assertIn("paired device", out)

    def test_device_approval_requires_the_seen_fingerprint(self) -> None:
        item_id = self.file(self.DRY)
        with self.assertRaisesRegex(killalot.KillalotError, "fingerprint"):
            killalot.act(self.conn, item_id, "approve", actor="me", via="device:1", presented_fingerprint="sha256:nope")
        fp = killalot.get_item(self.conn, item_id)["fingerprint"]
        item = killalot.act(self.conn, item_id, "approve", actor="me", via="device:1", presented_fingerprint=fp)
        self.assertEqual(item["state"], "accepted")

    def test_check_approval_match_mismatch_expiry_missing(self) -> None:
        item_id = self.file(self.DRY)
        self.assertFalse(killalot.check_approval(self.conn, item_id, self.DRY)[0])  # not approved yet
        fp = killalot.get_item(self.conn, item_id)["fingerprint"]
        killalot.act(self.conn, item_id, "approve", actor="me", via="device:1", presented_fingerprint=fp)
        self.assertTrue(killalot.check_approval(self.conn, item_id, self.DRY + "\n\n")[0])  # trailing blank lines are noise
        ok, why = killalot.check_approval(self.conn, item_id, self.DRY + "[dry-run] behemoth: remove worktree /y\n")
        self.assertFalse(ok)
        self.assertIn("changed", why)
        self.conn.execute("UPDATE items SET approved_until=? WHERE id=?", (killalot.iso(killalot.utcnow() - timedelta(seconds=1)), item_id))
        ok, why = killalot.check_approval(self.conn, item_id, self.DRY)
        self.assertFalse(ok)
        self.assertIn("expired", why)
        with self.assertRaisesRegex(killalot.KillalotError, "expired"):
            killalot.act(self.conn, item_id, "start", actor="agent", via=None)

    def test_same_dry_run_is_filed_once(self) -> None:
        first = self.file(self.DRY)
        second, new = killalot.request_approval(self.conn, project=str(self.ladder), title="again", evidence_text=self.DRY, command=None)
        self.assertEqual((first, new), (second, False))

    def test_cli_check_approval_exit_codes(self) -> None:
        dry = self.base / "dry.txt"
        dry.write_text(self.DRY)
        code, out = self.run_cli("--json", "request-approval", "--project", str(self.ladder), "--title", "Prune", "--evidence-file", str(dry))
        item_id = json.loads(out)["id"]
        self.assertEqual(self.run_cli("check-approval", "--item", str(item_id), "--evidence-file", str(dry))[0], 1)


def _writer(config_path: str, project: str, n: int) -> None:
    mod_spec = importlib.util.spec_from_file_location("killalot_child", SCRIPT)
    mod = importlib.util.module_from_spec(mod_spec)
    sys.modules["killalot_child"] = mod
    mod_spec.loader.exec_module(mod)
    config = mod.load_config(Path(config_path))
    conn = mod.connect(config)
    for i in range(n):
        mod.propose(conn, project=project, kind="task", title=f"{multiprocessing.current_process().name} {i}", evidence=["test"])
    conn.close()


class StoreTests(Fixture):
    def test_concurrent_writers_lose_nothing(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        procs = [ctx.Process(target=_writer, args=(str(self.config_path), str(self.ladder), 25), name=f"w{i}") for i in range(4)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
            self.assertEqual(p.exitcode, 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], 100)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE action='create'").fetchone()[0], 100)

    def test_migration_is_idempotent_and_refuses_newer(self) -> None:
        killalot.connect(self.config).close()
        self.assertEqual(killalot.get_meta(self.conn, "schema_version"), str(killalot.SCHEMA_VERSION))
        self.conn.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
        with self.assertRaisesRegex(killalot.KillalotError, "newer"):
            killalot.connect(self.config)

    def test_backup_once_a_day(self) -> None:
        self.assertIsNotNone(killalot.backup(self.config))
        self.assertIsNone(killalot.backup(self.config))


class ProjectStateTests(Fixture):
    def test_stale_parked_and_unpark_suggestion(self) -> None:
        self.propose(project=self.lottery)
        old = killalot.iso(killalot.utcnow() - timedelta(days=30))
        self.conn.execute("UPDATE projects SET last_activity=? WHERE path=?", (old, str(self.lottery)))
        later = killalot.utcnow() + timedelta(days=60)  # the item's own event is "now": look from far enough ahead
        with mock.patch.object(killalot, "utcnow", return_value=later):
            rows = {r["name"]: r for r in killalot.project_rows(self.conn, self.config)}
        self.assertTrue(rows["lottery-qat"]["stale"])
        self.assertFalse(rows["ladder"]["stale"])  # no open items: never stale
        killalot.park(self.conn, str(self.lottery), reason="waiting for reviews", revisit=None, via=VIA)
        with mock.patch.object(killalot, "utcnow", return_value=later):
            rows = {r["name"]: r for r in killalot.project_rows(self.conn, self.config)}
        self.assertFalse(rows["lottery-qat"]["stale"])
        self.assertTrue(rows["lottery-qat"]["parked"])
        with mock.patch.object(killalot, "last_activity", return_value=killalot.utcnow() + timedelta(minutes=5)):
            killalot.scan(self.conn, self.config)
        rows = {r["name"]: r for r in killalot.project_rows(self.conn, self.config)}
        self.assertTrue(rows["lottery-qat"]["unpark_suggested"])
        self.assertTrue(rows["lottery-qat"]["parked"])  # suggested, never done

    def test_park_with_revisit_creates_reminder_dropped_on_unpark(self) -> None:
        result = killalot.park(self.conn, str(self.ladder), reason=None, revisit=killalot.parse_when("2w"), via=VIA)
        reminder = killalot.get_item(self.conn, result["revisit_item"])
        self.assertEqual((reminder["kind"], reminder["state"]), ("reminder", "accepted"))
        killalot.unpark(self.conn, str(self.ladder), via=VIA)
        self.assertEqual(killalot.get_item(self.conn, result["revisit_item"])["state"], "dropped")

    def test_digest_marks_and_peeks(self) -> None:
        self.propose()
        first = killalot.digest(self.conn, self.config, mark=False)
        self.assertEqual(len(first["new_proposals"]), 1)
        killalot.digest(self.conn, self.config, mark=True)
        later = killalot.digest(self.conn, self.config, mark=False)
        self.assertEqual(later["new_proposals"], [])
        self.assertEqual(len(later["inbox"]["proposals"]), 1)  # still waiting on the owner

    def test_parse_when(self) -> None:
        base = killalot.utcnow()
        self.assertEqual(killalot.parse_when("2h", base), base + timedelta(hours=2))
        self.assertEqual(killalot.parse_when("1w", base), base + timedelta(weeks=1))
        with self.assertRaises(killalot.KillalotError):
            killalot.parse_when("soonish")


class HookTests(Fixture):
    def hook(self, cwd: Path, config_path: Path | None = None) -> dict:
        return json.loads(killalot.hook_session_start(config_path or self.config_path, json.dumps({"cwd": str(cwd), "hook_event_name": "SessionStart"})))

    def test_context_for_tagged_project_with_items(self) -> None:
        item_id = self.propose("Re-render plots", owner="agent")
        killalot.act(self.conn, item_id, "accept", actor="me", via=VIA)
        sub = self.ladder / "code"
        sub.mkdir()
        out = self.hook(sub)
        context = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn(f"#{item_id} [accepted] Re-render plots", context)
        self.assertIn("owner's yes", context)

    def test_silent_everywhere_else(self) -> None:
        self.assertEqual(self.hook(self.lottery), {})  # tagged, no items
        self.assertEqual(self.hook(self.roots / "untagged" / "repo"), {})
        self.assertEqual(self.hook(self.ladder, self.base / "missing.toml"), {})
        self.config.db_path.write_bytes(b"not a database")
        self.assertEqual(self.hook(self.ladder), {})
        self.assertEqual(killalot.hook_session_start(self.config_path, "not json"), "{}")

    def test_context_is_capped(self) -> None:
        for i in range(30):
            item_id = self.propose(f"A rather long task title that keeps going and going number {i}")
            killalot.act(self.conn, item_id, "accept", actor="me", via=VIA)
        context = self.hook(self.ladder)["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(context), killalot.HOOK_CONTEXT_MAX)

    def test_cli_hook_reads_stdin(self) -> None:
        self.propose()
        code, out = self.run_cli("hook", "session-start", stdin=json.dumps({"cwd": str(self.ladder)}))
        self.assertEqual(code, 0)
        self.assertIn("ReSirch Killalot — ladder", json.loads(out)["hookSpecificOutput"]["additionalContext"])

    def test_hook_runs_as_a_subprocess_from_plugin_hooks_json(self) -> None:
        hooks = json.loads((SCRIPT.parents[3] / "hooks" / "hooks.json").read_text())
        command = hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.propose()
        result = subprocess.run(command, shell=True, input=json.dumps({"cwd": str(self.ladder)}), capture_output=True, text=True,
                                env={"CLAUDE_PLUGIN_ROOT": str(SCRIPT.parents[3]), "KILLALOT_CONFIG": str(self.config_path), "PATH": "/usr/bin:/bin"}, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("additionalContext", result.stdout)


class WebTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.server = killalot.ThreadingHTTPServer(("127.0.0.1", 0), killalot.make_handler(self.config))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        code = killalot.create_pairing(self.conn, "phone")
        self.cookie = self.pair(code)

    def request(self, path: str, body=None, login: str | None = OWNER, cookie: str | None = "default", csrf: bool = True):
        headers = {}
        if login:
            headers[killalot.TAILSCALE_LOGIN_HEADER] = login
        cookie = self.cookie if cookie == "default" else cookie
        if cookie:
            headers["Cookie"] = f"{killalot.COOKIE_NAME}={cookie}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
            if csrf:
                headers[killalot.CSRF_HEADER] = "1"
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, res.read(), res.headers
        except urllib.error.HTTPError as err:
            return err.code, err.read(), err.headers

    def pair(self, code: str) -> str | None:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        req = urllib.request.Request(f"{self.base_url}/pair?code={code}", headers={killalot.TAILSCALE_LOGIN_HEADER: OWNER})
        try:
            opener.open(req, timeout=5)
        except urllib.error.HTTPError as err:
            if err.code != 303:
                return None
            cookie = err.headers.get("Set-Cookie", "")
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Lax", cookie)
            return cookie.split(";")[0].split("=", 1)[1]
        return None

    def test_healthz_is_open(self) -> None:
        self.assertEqual(self.request("/healthz", login=None, cookie=None)[0], 200)

    def test_rejections(self) -> None:
        self.assertEqual(self.request("/api/inbox", login=None)[0], 401)
        self.assertEqual(self.request("/api/inbox", login="intruder@example")[0], 401)
        self.assertEqual(self.request("/api/inbox", cookie=None)[0], 401)
        self.assertEqual(self.request("/api/inbox", cookie="forged")[0], 401)
        item_id = self.propose()
        self.assertEqual(self.request("/api/action", {"id": item_id, "action": "accept"}, csrf=False)[0], 403)
        self.assertEqual(killalot.get_item(self.conn, item_id)["state"], "proposed")

    def test_pairing_link_is_single_use_and_expires(self) -> None:
        code = killalot.create_pairing(self.conn, "laptop")
        self.assertIsNotNone(self.pair(code))
        self.assertIsNone(self.pair(code))
        code = killalot.create_pairing(self.conn, "tablet")
        self.conn.execute("UPDATE pairings SET expires_at=? WHERE device_name='tablet'", (killalot.iso(killalot.utcnow() - timedelta(seconds=1)),))
        self.assertIsNone(self.pair(code))

    def test_revoked_device_is_cut_off(self) -> None:
        self.assertEqual(self.request("/api/inbox")[0], 200)
        device = self.conn.execute("SELECT id FROM devices WHERE name='phone'").fetchone()["id"]
        killalot.revoke_device(self.conn, device, "terminal")
        self.assertEqual(self.request("/api/inbox")[0], 401)

    def test_accept_flow_records_the_device(self) -> None:
        item_id = self.propose()
        status, body, _ = self.request("/api/inbox")
        self.assertEqual(status, 200)
        self.assertIn(item_id, [i["id"] for i in json.loads(body)["inbox"]["proposals"]])
        status, body, _ = self.request("/api/action", {"id": item_id, "action": "accept"})
        self.assertEqual(status, 200, body)
        event = self.conn.execute("SELECT via FROM events WHERE item=? AND action='accept'", (item_id,)).fetchone()
        self.assertTrue(event["via"].startswith("device:"))

    def test_approval_needs_confirm_and_fingerprint(self) -> None:
        item_id, _ = killalot.request_approval(self.conn, project=str(self.ladder), title="Prune", evidence_text="[dry-run] x\n", command=None)
        fp = killalot.get_item(self.conn, item_id)["fingerprint"]
        status, body, _ = self.request("/api/action", {"id": item_id, "action": "approve", "fingerprint": fp})
        self.assertEqual(status, 400)  # first tap only: no confirm
        status, body, _ = self.request("/api/action", {"id": item_id, "action": "approve", "fingerprint": "sha256:other", "confirm": True})
        self.assertEqual(status, 400)
        status, body, _ = self.request("/api/action", {"id": item_id, "action": "approve", "fingerprint": fp, "confirm": True})
        self.assertEqual(status, 200, body)
        self.assertTrue(killalot.check_approval(self.conn, item_id, "[dry-run] x\n")[0])

    def test_page_and_views(self) -> None:
        status, body, headers = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"ReSirch Killalot", body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        for path in ("/api/projects", "/api/digest", "/api/devices", f"/api/project?path={self.ladder}"):
            self.assertEqual(self.request(path)[0], 200, path)
        status, body, _ = self.request("/api/devices")
        self.assertTrue(all(d["token_sha256"] is None for d in json.loads(body)["devices"]))

    def test_page_flattens_nested_view_lists(self) -> None:
        # Views return lists that contain lists of cards; replaceChildren must receive them flat,
        # or the browser prints "[object HTMLDivElement]" instead of the cards (seen on iOS).
        page = (SCRIPT.parents[1] / "assets" / "app.html").read_text()
        self.assertIn("(await view()).flat(Infinity)", page)

    def test_add_park_and_digest_over_http(self) -> None:
        status, body, _ = self.request("/api/add", {"project": str(self.ladder), "title": "Email the advisor", "kind": "task", "due": "2d"})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.request("/api/park", {"project": str(self.lottery), "reason": "paused"})[0], 200)
        self.assertEqual(self.request("/api/digest", {})[0], 200)
        self.assertIsNotNone(killalot.get_meta(self.conn, "last_digest_at"))

    def test_links_comments_and_detail_over_http(self) -> None:
        a = killalot.add(self.conn, project=str(self.ladder), kind="task", title="A", via=VIA)
        b = killalot.add(self.conn, project=str(self.ladder), kind="task", title="B", via=VIA)
        self.assertEqual(self.request("/api/link", {"src": a, "dst": b, "type": "depends_on"})[0], 200)
        self.assertEqual(self.request("/api/comment", {"id": a, "text": "hello"})[0], 200)
        status, body, _ = self.request("/api/action", {"id": a, "action": "edit", "edits": {"priority": 1, "due_at": "2d"}})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.request("/api/action", {"id": a, "action": "edit", "edits": {"state": "done"}})[0], 400)
        detail = json.loads(self.request(f"/api/item?id={a}")[1])
        self.assertEqual(detail["item"]["blocked_by"], [b])
        self.assertEqual([c["text"] for c in detail["comments"]], ["hello"])
        self.assertEqual(detail["item"]["priority"], 1)

    def test_chat_routes_stay_in_the_device_conversation(self) -> None:
        device = self.conn.execute("SELECT id FROM devices WHERE name='phone'").fetchone()["id"]
        with mock.patch.object(killalot, "assistant_turn", return_value={"reply": "ok", "pending": []}) as turn:
            status, body, _ = self.request("/api/assistant", {"text": "hi"})
        self.assertEqual(status, 200, body)
        self.assertEqual(turn.call_args.kwargs["conv"], f"web-d{device}")
        mine = killalot.stage(self.conn, conv=f"web-d{device}", via=f"assistant:web/web-d{device}", name="add_item", args={"project": "ladder", "title": "Mine"})
        theirs = killalot.stage(self.conn, conv="tg-1", via="assistant:telegram/tg-1", name="add_item", args={"project": "ladder", "title": "Theirs"})
        self.assertEqual(self.request("/api/pending", {"id": theirs["change"], "confirm": True})[0], 400)
        status, body, _ = self.request("/api/pending", {"id": mine["change"], "confirm": True})
        self.assertEqual(json.loads(body)["outcome"], "applied")


class PublicHelpers:
    def public_config(self, extra: str = "") -> object:
        path = self.base / "public.toml"
        path.write_text(textwrap.dedent(f"""
            [store]
            root = "{self.base / 'store'}"
            [projects]
            roots = ["{self.roots}"]
            [web]
            access = "public"
            port = 49149
            [public]
            domain = "example.duckdns.org"
            port = 49147
        """) + extra)
        return killalot.load_config(path)

    def serve(self, config):
        server = killalot.ThreadingHTTPServer(("127.0.0.1", 0), killalot.make_handler(config))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"


class PublicModeTests(PublicHelpers, Fixture):
    def test_config_rules(self) -> None:
        config = self.public_config()
        self.assertEqual(config.public_base, "https://example.duckdns.org:49147")
        bad = self.base / "bad.toml"
        for body, message in (
            ('[web]\nbind = "0.0.0.0"\n', "localhost"),
            ('[web]\naccess = "public"\n', "domain"),
            ('[web]\naccess = "public"\nport = 49147\n[public]\ndomain = "x.duckdns.org"\n', "must differ"),
            ('[web]\naccess = "open"\n', "access"),
        ):
            bad.write_text(f'[store]\nroot = "{self.base}/s"\n[projects]\nroots = ["{self.roots}"]\n' + body)
            with self.assertRaisesRegex(killalot.KillalotError, message):
                killalot.load_config(bad)

    def get(self, url: str, cookie: str | None = None, client: str = "203.0.113.7"):
        headers = {"X-Forwarded-For": client}
        if cookie:
            headers["Cookie"] = f"{killalot.COOKIE_NAME}={cookie}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5) as res:
                return res.status, res.headers
        except urllib.error.HTTPError as err:
            return err.code, err.headers

    def test_cookie_is_the_key_and_failures_lock_out(self) -> None:
        config = self.public_config()
        conn = killalot.connect(config)
        self.addCleanup(conn.close)
        base = self.serve(config)
        code = killalot.create_pairing(conn, "phone")
        paired = killalot.redeem_pairing(conn, code)
        token = paired[1]
        status, headers = self.get(base + "/api/inbox", cookie=token)
        self.assertEqual(status, 200)  # no Tailscale header needed
        self.assertIn("max-age", headers.get("Strict-Transport-Security", ""))
        for _ in range(killalot.FAILED_AUTH_LIMIT):
            self.assertEqual(self.get(base + "/api/inbox", cookie="forged")[0], 401)
        self.assertEqual(self.get(base + "/api/inbox", cookie=token)[0], 429)  # that client is locked out
        self.assertEqual(self.get(base + "/api/inbox", cookie=token, client="198.51.100.9")[0], 200)  # others are not

    def test_tailscale_mode_still_requires_identity(self) -> None:
        base = self.serve(self.config)
        code = killalot.create_pairing(self.conn, "phone")
        token = killalot.redeem_pairing(self.conn, code)[1]
        self.assertEqual(self.get(base + "/api/inbox", cookie=token)[0], 401)

    def test_duckdns_update(self) -> None:
        config = self.public_config()
        config.duckdns_env.write_text("DUCKDNS_TOKEN=abc\n")
        seen = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(url, timeout):
            seen.append(url)
            return Response(b"OK")
        with mock.patch.dict(killalot.os.environ, {}, clear=False):
            killalot.os.environ.pop("DUCKDNS_TOKEN", None)
            self.assertEqual(killalot.duckdns_update(config, opener=opener), "OK")
            self.assertIn("domains=example&token=abc&ip=", seen[0])
            with self.assertRaisesRegex(killalot.KillalotError, "refused"):
                killalot.duckdns_update(config, opener=lambda url, timeout: Response(b"KO"))
            config.duckdns_env.write_text("DUCKDNS_TOKEN=PASTE_TOKEN\n")
            with self.assertRaisesRegex(killalot.KillalotError, "no DuckDNS token"):
                killalot.duckdns_update(config, opener=opener)

    def test_public_setup_renders_everything(self) -> None:
        config = self.public_config()
        config.duckdns_env.write_text("DUCKDNS_TOKEN=abc\n")
        home = self.base / "home"
        killalot.deploy(config, start_service=False, home=home)
        config.proxy_dir.mkdir(parents=True, exist_ok=True)
        fake = config.proxy_dir / "caddy"
        fake.write_text("#!/bin/sh\necho dns.providers.duckdns\n")
        fake.chmod(0o755)
        result = killalot.public_setup(config, home=home, start=False, download=False)
        caddyfile = Path(result["caddyfile"]).read_text()
        self.assertIn("https://example.duckdns.org:49147", caddyfile)
        self.assertIn("reverse_proxy 127.0.0.1:49149", caddyfile)
        for unit in result["units"]:
            self.assertNotRegex(Path(unit).read_text(), r"__[A-Z_]+__")
        self.assertIn("duckdns-update", (home / ".config/systemd/user/killalot-duckdns.service").read_text())


class R3ItemTests(Fixture):
    def add(self, title: str, project: Path | None = None, **kw) -> int:
        return killalot.add(self.conn, project=str(project or self.ladder), kind=kw.pop("kind", "task"), title=title, via=VIA, **kw)

    def test_v1_store_migrates_and_keeps_its_items(self) -> None:
        old = self.base / "old"
        old.mkdir()
        raw = sqlite3.connect(old / "killalot.db", isolation_level=None)
        for statement in killalot.split_sql(killalot.MIGRATIONS[0]):
            raw.execute(statement)
        raw.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
        raw.execute("INSERT INTO projects(path, name, first_seen, last_seen) VALUES ('/p', 'p', 'x', 'x')")
        raw.execute("INSERT INTO items(project, kind, title, owner, state, origin, dedupe_key, created_at, updated_at) "
                    "VALUES ('/p', 'task', 'Old one', 'me', 'accepted', 'me', 'task:old one', 'x', 'x')")
        raw.close()
        config = killalot.Config(root=old, project_roots=(self.roots,))
        conn = killalot.connect(config)
        self.addCleanup(conn.close)
        self.assertEqual(killalot.get_meta(conn, "schema_version"), str(len(killalot.MIGRATIONS)))
        item = killalot.get_item(conn, 1)
        self.assertEqual((item["title"], item["priority"]), ("Old one", 2))

    def test_priority_orders_lists_and_inbox_buckets(self) -> None:
        low = self.propose("Someday thing", priority=3)
        high = self.propose("Urgent thing", priority=0)
        mid = self.propose("Normal thing")
        self.assertEqual([i["id"] for i in killalot.inbox(self.conn)["proposals"]], [high, mid, low])
        with self.assertRaisesRegex(killalot.KillalotError, "P0"):
            self.propose("Bad", priority=7)
        self.assertEqual(killalot.check_priority("p1"), 1)

    def test_links_refuse_cycles_and_block_until_done(self) -> None:
        a, b, c = self.add("Write paper"), self.add("Run ablation"), self.add("Get GPUs", project=self.lottery)
        killalot.link(self.conn, a, b, "depends_on", actor="me", via=VIA)
        killalot.link(self.conn, b, c, "depends_on", actor="agent", via=None)  # cross-project; agents may link
        with self.assertRaisesRegex(killalot.KillalotError, "cycle"):
            killalot.link(self.conn, c, a, "depends_on", actor="me", via=VIA)
        with self.assertRaisesRegex(killalot.KillalotError, "itself"):
            killalot.link(self.conn, a, a, "relates_to", actor="me", via=VIA)
        with self.assertRaisesRegex(killalot.KillalotError, "already"):
            killalot.link(self.conn, a, b, "depends_on", actor="me", via=VIA)
        items = {i["id"]: i for i in killalot.list_items(self.conn)}
        self.assertEqual((items[a]["blocked_by"], items[b]["blocked_by"]), ([b], [c]))
        rows = {r["name"]: r for r in killalot.project_rows(self.conn, self.config)}
        self.assertIsNone(rows["ladder"]["next"])  # both ladder items are blocked
        killalot.act(self.conn, c, "done", actor="me", via=VIA)
        self.assertEqual(killalot.blocked_by(self.conn, b), [])
        self.assertEqual({(l["role"], l["id"]) for l in killalot.item_links(self.conn, b)}, {("needed by", a), ("depends on", c)})
        killalot.unlink(self.conn, a, b, "depends_on", actor="me", via=VIA)
        self.assertEqual(killalot.blocked_by(self.conn, a), [])
        self.assertIn("unlink", [e["action"] for e in killalot.item_detail(self.conn, a)["events"]])

    def test_a_subtask_has_one_parent(self) -> None:
        epic, sub, other = self.add("Epic"), self.add("Sub"), self.add("Other")
        killalot.link(self.conn, epic, sub, "parent_of", actor="me", via=VIA)
        with self.assertRaisesRegex(killalot.KillalotError, "parent"):
            killalot.link(self.conn, other, sub, "parent_of", actor="me", via=VIA)

    def test_edit_only_touches_editable_fields(self) -> None:
        item_id = self.propose()
        for bad in ({"state": "done"}, {"approved_until": "2099-01-01"}, {"title=?, state": "x"}):
            with self.assertRaisesRegex(killalot.KillalotError, "cannot edit"):
                killalot.act(self.conn, item_id, "edit", actor="me", via=VIA, edits=bad)
        self.assertEqual(killalot.get_item(self.conn, item_id)["state"], "proposed")
        item = killalot.act(self.conn, item_id, "edit", actor="me", via=VIA, edits={"priority": "P1", "due_at": killalot.iso(killalot.utcnow())})
        self.assertEqual(item["priority"], 1)
        item = killalot.act(self.conn, item_id, "edit", actor="me", via=VIA, edits={"due_at": ""})
        self.assertIsNone(item["due_at"])

    def test_comments_are_events(self) -> None:
        item_id = self.propose()
        killalot.comment(self.conn, item_id, "check the seed first", actor="me", via=VIA)
        killalot.comment(self.conn, item_id, "seed was fine", actor="agent", via=None)
        detail = killalot.item_detail(self.conn, item_id)
        self.assertEqual([c["text"] for c in detail["comments"]], ["check the seed first", "seed was fine"])
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM events WHERE action='comment'")

    def test_cli_priority_links_comments(self) -> None:
        code, out = self.run_cli("--json", "add", "--project", str(self.ladder), "--title", "A", "--priority", "P1", "--via", VIA)
        a = json.loads(out)["id"]
        code, out = self.run_cli("--json", "add", "--project", str(self.ladder), "--title", "B", "--via", VIA)
        b = json.loads(out)["id"]
        self.assertEqual(self.run_cli("link", str(a), "depends_on", str(b), "--via", VIA)[0], 0)
        self.assertEqual(self.run_cli("comment", str(a), "--text", "note", "--via", VIA)[0], 0)
        self.assertEqual(self.run_cli("edit", str(b), "--priority", "P0", "--via", VIA)[0], 0)
        code, out = self.run_cli("show", str(a))
        self.assertIn("depends on #" + str(b), out)
        self.assertIn("note", out)
        self.assertIn("P1", out)
        code, out = self.run_cli("list", "--project", "ladder")
        self.assertIn(f"blocked by #{b}", out)


class OpsTests(Fixture):
    VIA_A = "assistant:web/web-d1"

    def test_registry_never_approves_or_starts(self) -> None:
        self.assertFalse({"approve", "start", "request_approval"} & set(killalot.OPS))
        item_id, _ = killalot.request_approval(self.conn, project=str(self.ladder), title="Prune", evidence_text="[dry-run] x\n", command=None)
        with self.assertRaisesRegex(killalot.KillalotError, "paired device"):
            killalot.run_op(self.conn, self.config, "accept", {"id": item_id}, via="mcp:1")

    def test_add_with_links_and_names(self) -> None:
        dep = killalot.add(self.conn, project=str(self.lottery), kind="task", title="Get GPUs", via=VIA)
        out = killalot.run_op(self.conn, self.config, "add_item", {"project": "ladder", "title": "Rerun ablation", "kind": "reminder",
                                                                   "due": "2d", "priority": "P1", "depends_on": [dep]}, via="mcp:1")
        new = out["added"]["id"]
        self.assertEqual(killalot.blocked_by(self.conn, new), [dep])
        self.assertEqual(killalot.get_item(self.conn, new)["priority"], 1)
        event = self.conn.execute("SELECT via FROM events WHERE item=? AND action='create'", (new,)).fetchone()
        self.assertEqual(event["via"], "mcp:1")
        with self.assertRaisesRegex(killalot.KillalotError, "no project"):
            killalot.run_op(self.conn, self.config, "add_item", {"project": "nope", "title": "x"}, via="mcp:1")
        with self.assertRaisesRegex(killalot.KillalotError, "kind"):
            killalot.run_op(self.conn, self.config, "add_item", {"project": "ladder", "title": "x", "kind": "approval"}, via="mcp:1")

    def test_staged_change_applies_once_and_only_on_confirm(self) -> None:
        staged = killalot.stage(self.conn, conv="web-d1", via=self.VIA_A, name="add_item", args={"project": "ladder", "title": "Email advisor"})
        self.assertIn("Email advisor", staged["summary"])
        self.assertEqual(killalot.list_items(self.conn), [])
        first = killalot.resolve_pending(self.conn, self.config, staged["change"], True)
        again = killalot.resolve_pending(self.conn, self.config, staged["change"], True)
        self.assertEqual((first["outcome"], again["outcome"]), ("applied", "applied"))
        self.assertEqual(len(killalot.list_items(self.conn)), 1)
        other = killalot.stage(self.conn, conv="web-d1", via=self.VIA_A, name="add_item", args={"project": "ladder", "title": "Second"})
        self.assertEqual(killalot.resolve_pending(self.conn, self.config, other["change"], False)["outcome"], "cancelled")
        late = killalot.stage(self.conn, conv="web-d1", via=self.VIA_A, name="add_item", args={"project": "ladder", "title": "Late"})
        self.conn.execute("UPDATE pending SET created_at=? WHERE id=?", (killalot.iso(killalot.utcnow() - timedelta(days=2)), late["change"]))
        self.assertEqual(killalot.resolve_pending(self.conn, self.config, late["change"], True)["outcome"], "expired")
        self.assertEqual(len(killalot.list_items(self.conn)), 1)
        with self.assertRaisesRegex(killalot.KillalotError, "not a change"):
            killalot.stage(self.conn, conv="web-d1", via=self.VIA_A, name="inbox", args={})

    def test_staged_items_can_link_to_each_other(self) -> None:
        first = killalot.stage(self.conn, conv="c", via=self.VIA_A, name="add_item", args={"project": "ladder", "title": "Rerun"})
        second = killalot.stage(self.conn, conv="c", via=self.VIA_A, name="add_item",
                                args={"project": "ladder", "title": "Write up", "depends_on": [f"change:{first['change']}"]})
        self.assertIn(f"depends on change {first['change']}", second["summary"])
        self.assertTrue(killalot.resolve_pending(self.conn, self.config, second["change"], True)["outcome"].startswith("failed: confirm change"))
        again = killalot.stage(self.conn, conv="c", via=self.VIA_A, name="add_item",
                               args={"project": "ladder", "title": "Write up", "depends_on": [f"change:{first['change']}"]})
        killalot.resolve_pending(self.conn, self.config, first["change"], True)
        self.assertEqual(killalot.resolve_pending(self.conn, self.config, again["change"], True)["outcome"], "applied")
        items = {i["title"]: i for i in killalot.list_items(self.conn)}
        self.assertEqual(items["Write up"]["blocked_by"], [items["Rerun"]["id"]])

    def test_rejection_needs_reason_even_through_ops(self) -> None:
        item_id = self.propose()
        with self.assertRaisesRegex(killalot.KillalotError, "reason"):
            killalot.run_op(self.conn, self.config, "reject", {"id": item_id}, via="mcp:1")

    def test_mcp_protocol(self) -> None:
        init = killalot.mcp_handle(self.config, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}, via="mcp:1")
        self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
        self.assertIsNone(killalot.mcp_handle(self.config, {"jsonrpc": "2.0", "method": "notifications/initialized"}, via="mcp:1"))
        tools = killalot.mcp_handle(self.config, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, via="mcp:1")["result"]["tools"]
        self.assertIn("add_item", [t["name"] for t in tools])
        call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "add_item", "arguments": {"project": "ladder", "title": "Staged"}}}
        staged = killalot.mcp_handle(self.config, call, via=self.VIA_A, stage_conv="web-d1")["result"]
        self.assertIn("staged", staged["content"][0]["text"])
        self.assertEqual(killalot.list_items(self.conn), [])
        direct = killalot.mcp_handle(self.config, call | {"id": 4}, via="mcp:1")["result"]
        self.assertFalse(direct["isError"])
        self.assertEqual(len(killalot.list_items(self.conn)), 1)
        dup = killalot.mcp_handle(self.config, call | {"id": 5}, via="mcp:1")["result"]
        self.assertTrue(dup["isError"])
        self.assertIn("already tracked", dup["content"][0]["text"])
        self.assertIn("error", killalot.mcp_handle(self.config, {"jsonrpc": "2.0", "id": 6, "method": "nope"}, via="mcp:1"))

    def test_mcp_stdio(self) -> None:
        lines = "\n".join(json.dumps(m) for m in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_projects", "arguments": {}}})) + "\n"
        out = io.StringIO()
        killalot.mcp_stdio(self.config, via=self.VIA_A, stage_conv="web-d1", stdin=io.StringIO(lines), stdout=out)
        replies = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2])
        self.assertIn("ladder", replies[1]["result"]["content"][0]["text"])


class AssistantTests(Fixture):
    def runner(self, reply: str = "Staged it.", session: str = "sess-1", stage_title: str | None = None):
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            if stage_title:  # what the model does through mcp-stdio --stage
                conn = killalot.connect(self.config)
                killalot.stage(conn, conv="cli-x", via="assistant:cli/cli-x", name="add_item", args={"project": "ladder", "title": stage_title})
                conn.close()
            out = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": reply, "session_id": session})
            return subprocess.CompletedProcess(argv, 0, out, "")
        return run, calls

    def test_turn_stages_and_resumes(self) -> None:
        run, calls = self.runner(stage_title="Rerun ablation")
        result = killalot.assistant_turn(self.config, channel="cli", conv="cli-x", text="remind me to rerun the ablation", runner=run)
        self.assertEqual(result["reply"], "Staged it.")
        self.assertEqual([c["summary"] for c in result["pending"]], ["Add task to ladder: “Rerun ablation” · P2"])
        argv, kw = calls[0]
        self.assertNotIn("--resume", argv)
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        mcp = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]["killalot"]
        self.assertIn("--stage", mcp["args"])
        self.assertIn("assistant:cli/cli-x", mcp["args"])
        self.assertIn("ladder", argv[argv.index("--append-system-prompt") + 1])
        self.assertEqual(kw["input"], "remind me to rerun the ablation")
        self.assertEqual(killalot.list_items(self.conn), [])  # nothing applied yet
        killalot.resolve_pending(self.conn, self.config, result["pending"][0]["id"], True)
        run2, calls2 = self.runner(reply="Done.")
        killalot.assistant_turn(self.config, channel="cli", conv="cli-x", text="thanks", runner=run2)
        argv2, kw2 = calls2[0]
        self.assertEqual(argv2[argv2.index("--resume") + 1], "sess-1")
        self.assertIn("applied", kw2["input"])  # the model learns what the owner tapped

    def test_failures_are_plain_errors(self) -> None:
        def missing(argv, **kw):
            raise FileNotFoundError(argv[0])
        with self.assertRaisesRegex(killalot.KillalotError, "not installed"):
            killalot.assistant_turn(self.config, channel="cli", conv="cli-y", text="hi", runner=missing)

        def broken(argv, **kw):
            return subprocess.CompletedProcess(argv, 0, json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True}), "")
        with self.assertRaisesRegex(killalot.KillalotError, "failed"):
            killalot.assistant_turn(self.config, channel="cli", conv="cli-y", text="hi", runner=broken)

    def test_codex_output(self) -> None:
        stdout = "\n".join(json.dumps(e) for e in ({"type": "thread.started", "thread_id": "t-9"},
                                                    {"type": "item.completed", "item": {"type": "agent_message", "text": "Which project?"}}))
        self.assertEqual(killalot.parse_backend_output("codex-jsonl", stdout), ("Which project?", "t-9"))


class FakeTelegram:
    """Records every Bot API call; answers like Telegram does."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.next_id = 100

    def opener(self, req, timeout):
        method = req.full_url.rsplit("/", 1)[1]
        params = json.loads(req.data.decode())
        self.calls.append((method, params))
        if method == "sendMessage":
            self.next_id += 1
        result = {"message_id": self.next_id} if method == "sendMessage" else ([] if method == "getUpdates" else True)

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return Response(json.dumps({"ok": True, "result": result}).encode())

    def sent(self) -> list[dict]:
        return [p for m, p in self.calls if m == "sendMessage"]


class TelegramTests(Fixture):
    CHAT = 4242

    def setUp(self) -> None:
        super().setUp()
        self.fake = FakeTelegram()
        self.tg = killalot.Telegram("123:abc", self.fake.opener)

    def msg(self, text: str, chat: int | None = None, reply_to: int | None = None) -> dict:
        message = {"message_id": 1, "chat": {"id": chat or self.CHAT}, "text": text}
        if reply_to:
            message["reply_to_message"] = {"message_id": reply_to}
        return {"update_id": 1, "message": message}

    def link(self) -> None:
        code = killalot.create_telegram_link(self.conn)
        killalot.telegram_handle(self.config, self.tg, self.msg(f"/start {code}"))
        self.assertEqual(killalot.telegram_chat(self.conn), self.CHAT)

    def test_binding_needs_the_code_and_other_chats_are_ignored(self) -> None:
        killalot.create_telegram_link(self.conn)
        killalot.telegram_handle(self.config, self.tg, self.msg("/start wrong"))
        self.assertIsNone(killalot.telegram_chat(self.conn))
        self.link()
        asked = []
        killalot.telegram_handle(self.config, self.tg, self.msg("add something", chat=999), ask=lambda *a: asked.append(a))
        killalot.telegram_handle(self.config, self.tg, self.msg("add something"), ask=lambda *a: asked.append(a))
        self.assertEqual([a[3] for a in asked], ["add something"])

    def test_notices_once_with_buttons_and_floods_collapse(self) -> None:
        self.propose("Already open")
        self.link()  # what was open before linking is not news
        self.assertEqual(killalot.telegram_notify(self.config, self.tg), 0)
        item_id = self.propose("New proposal")
        self.assertEqual(killalot.telegram_notify(self.config, self.tg), 1)
        self.assertEqual(killalot.telegram_notify(self.config, self.tg), 0)  # never re-sent
        notice = self.fake.sent()[-1]
        self.assertIn("New proposal", notice["text"])
        data = [b.get("callback_data") for row in notice["reply_markup"]["inline_keyboard"] for b in row]
        self.assertIn(f"a:{item_id}", data)
        approval, _ = killalot.request_approval(self.conn, project=str(self.ladder), title="Prune", evidence_text="[dry-run] x\n", command=None)
        killalot.telegram_notify(self.config, self.tg)
        buttons = [b for row in self.fake.sent()[-1]["reply_markup"]["inline_keyboard"] for b in row]
        self.assertTrue(all("url" in b for b in buttons))  # approvals only open the web page
        for i in range(killalot.TELEGRAM_FLOOD + 1):
            self.propose(f"Flood {i}")
        before = len(self.fake.sent())
        killalot.telegram_notify(self.config, self.tg)
        self.assertEqual(len(self.fake.sent()), before + 1)
        self.assertIn("/inbox", self.fake.sent()[-1]["text"])

    def test_buttons_act_as_the_owner(self) -> None:
        self.link()
        item_id = self.propose()
        cb = {"update_id": 2, "callback_query": {"id": "c1", "data": f"a:{item_id}", "message": {"message_id": 5, "chat": {"id": self.CHAT}, "text": "x"}}}
        killalot.telegram_handle(self.config, self.tg, cb)
        self.assertEqual(killalot.get_item(self.conn, item_id)["state"], "accepted")
        event = self.conn.execute("SELECT via FROM events WHERE item=? AND action='accept'", (item_id,)).fetchone()
        self.assertEqual(event["via"], f"telegram:{self.CHAT}")
        other = self.propose("Reject me")
        killalot.telegram_handle(self.config, self.tg, cb | {"callback_query": cb["callback_query"] | {"data": f"r:{other}"}})
        prompt_id = self.fake.next_id  # the ForceReply prompt just sent
        killalot.telegram_handle(self.config, self.tg, self.msg("not needed", reply_to=prompt_id))
        item = killalot.get_item(self.conn, other)
        self.assertEqual((item["state"], item["reject_reason"]), ("rejected", "not needed"))
        intruder = {"update_id": 3, "callback_query": {"id": "c2", "data": f"d:{item_id}", "message": {"message_id": 6, "chat": {"id": 1}, "text": "x"}}}
        killalot.telegram_handle(self.config, self.tg, intruder)
        self.assertEqual(killalot.get_item(self.conn, item_id)["state"], "accepted")

    def test_confirm_button_applies_a_staged_change(self) -> None:
        self.link()
        staged = killalot.stage(self.conn, conv=f"tg-{self.CHAT}", via=f"assistant:telegram/tg-{self.CHAT}", name="add_item",
                                args={"project": "ladder", "title": "From Telegram"})
        cb = {"update_id": 4, "callback_query": {"id": "c3", "data": f"pc:{staged['change']}", "message": {"message_id": 7, "chat": {"id": self.CHAT}, "text": "x"}}}
        killalot.telegram_handle(self.config, self.tg, cb)
        self.assertEqual([i["title"] for i in killalot.list_items(self.conn)], ["From Telegram"])

    def test_commands(self) -> None:
        self.link()
        self.propose()
        for command, expect in (("/inbox", "Proposals"), ("/projects", "ladder"), ("/digest", "Digest since")):
            killalot.telegram_handle(self.config, self.tg, self.msg(command))
            self.assertIn(expect, self.fake.sent()[-1]["text"], command)

    def test_ask_relays_reply_and_changes(self) -> None:
        self.link()
        with mock.patch.object(killalot, "assistant_turn", return_value={"reply": "Which project?", "pending": [{"id": 9, "summary": "Add task"}]}):
            killalot.telegram_ask(self.config, self.tg, self.CHAT, "add a task")
        texts = [m["text"] for m in self.fake.sent()]
        self.assertIn("Which project?", texts)
        self.assertEqual(self.fake.sent()[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "pc:9")


class ConnectorTests(PublicHelpers, Fixture):
    REDIRECT = "https://claude.ai/api/mcp/auth_callback"

    def setUp(self) -> None:
        super().setUp()
        self.pconfig = self.public_config()
        self.pconn = killalot.connect(self.pconfig)
        self.addCleanup(self.pconn.close)
        killalot.scan(self.pconn, self.pconfig)
        self.url = self.serve(self.pconfig)
        self.token = killalot.redeem_pairing(self.pconn, killalot.create_pairing(self.pconn, "mac"))[1]

    def call(self, path, body=None, form=False, headers=None, cookie=None):
        hdrs = {"X-Forwarded-For": "203.0.113.50", **(headers or {})}
        if cookie:
            hdrs["Cookie"] = f"{killalot.COOKIE_NAME}={cookie}"
        data = None
        if body is not None:
            if form:
                data = urllib.parse.urlencode(body).encode()
                hdrs["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                data = json.dumps(body).encode()
                hdrs["Content-Type"] = "application/json"

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        req = urllib.request.Request(self.url + path, data=data, headers=hdrs, method="POST" if body is not None else "GET")
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=5) as res:
                return res.status, res.read(), res.headers
        except urllib.error.HTTPError as err:
            return err.code, err.read(), err.headers

    def connect_app(self) -> dict:
        import base64
        import hashlib
        status, body, _ = self.call("/oauth/register", {"redirect_uris": [self.REDIRECT], "client_name": "Claude"})
        self.assertEqual(status, 201, body)
        client = json.loads(body)["client_id"]
        verifier = "v" * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        query = urllib.parse.urlencode({"response_type": "code", "client_id": client, "redirect_uri": self.REDIRECT, "code_challenge": challenge,
                                        "code_challenge_method": "S256", "state": "st"})
        status, body, _ = self.call("/oauth/authorize?" + query)
        self.assertEqual(status, 401)
        self.assertIn(b"Pair and continue", body)  # unpaired browser: offered a pairing code
        status, body, _ = self.call("/oauth/authorize?" + query, cookie=self.token)
        self.assertEqual(status, 200)
        nonce = body.decode().split('name="consent" value="')[1].split('"')[0]
        status, _, headers = self.call("/oauth/authorize", {"consent": nonce, "decision": "allow"}, form=True, cookie=self.token)
        self.assertEqual(status, 303)
        location = urllib.parse.urlparse(headers["Location"])
        params = urllib.parse.parse_qs(location.query)
        self.assertEqual(params["state"], ["st"])
        code = params["code"][0]
        bad = self.call("/oauth/token", {"grant_type": "authorization_code", "code": code, "client_id": client, "redirect_uri": self.REDIRECT,
                                         "code_verifier": "w" * 64}, form=True)
        self.assertEqual(bad[0], 400)  # wrong verifier, and the code is now spent
        return {"client": client, "verifier": verifier, "challenge": challenge, "query": query}

    def tokens(self, app) -> dict:
        query = app["query"]
        status, body, _ = self.call("/oauth/authorize?" + query, cookie=self.token)
        nonce = body.decode().split('name="consent" value="')[1].split('"')[0]
        _, _, headers = self.call("/oauth/authorize", {"consent": nonce, "decision": "allow"}, form=True, cookie=self.token)
        code = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)["code"][0]
        status, body, _ = self.call("/oauth/token", {"grant_type": "authorization_code", "code": code, "client_id": app["client"],
                                                     "redirect_uri": self.REDIRECT, "code_verifier": app["verifier"]}, form=True)
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def mcp(self, access: str, message: dict):
        return self.call("/mcp", message, headers={"Authorization": f"Bearer {access}", "Accept": "application/json, text/event-stream"})

    def test_metadata_and_registration_rules(self) -> None:
        status, body, _ = self.call("/.well-known/oauth-authorization-server")
        self.assertEqual(json.loads(body)["code_challenge_methods_supported"], ["S256"])
        status, body, _ = self.call("/.well-known/oauth-protected-resource/mcp")
        self.assertEqual(json.loads(body)["resource"], "https://example.duckdns.org:49147/mcp")
        status, body, _ = self.call("/oauth/register", {"redirect_uris": ["https://evil.example/cb"]})
        self.assertEqual(status, 400)
        status, _, headers = self.call("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(status, 401)
        self.assertIn("resource_metadata=", headers["WWW-Authenticate"])

    def test_full_flow_tools_refresh_and_revoke(self) -> None:
        app = self.connect_app()
        tok = self.tokens(app)
        status, body, _ = self.mcp(tok["access_token"], {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(status, 200)
        self.assertIn("add_item", [t["name"] for t in json.loads(body)["result"]["tools"]])
        call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "add_item", "arguments": {"project": "ladder", "title": "From Claude"}}}
        status, body, _ = self.mcp(tok["access_token"], call)
        self.assertFalse(json.loads(body)["result"]["isError"], body)
        row = self.pconn.execute("SELECT via FROM events WHERE action='create' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertTrue(row["via"].startswith("mcp:"))
        self.assertEqual(self.mcp(tok["access_token"], {"jsonrpc": "2.0", "method": "notifications/initialized"})[0], 202)
        status, body, _ = self.call("/oauth/token", {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": app["client"]}, form=True)
        fresh = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(self.mcp(tok["access_token"], {"jsonrpc": "2.0", "id": 3, "method": "ping"})[0], 401)  # rotated away
        self.assertEqual(self.mcp(fresh["access_token"], {"jsonrpc": "2.0", "id": 3, "method": "ping"})[0], 200)
        killalot.revoke_connection(self.pconn, killalot.connection_rows(self.pconn)[0]["id"], "terminal")
        self.assertEqual(self.mcp(fresh["access_token"], {"jsonrpc": "2.0", "id": 4, "method": "ping"})[0], 401)

    def test_revoking_the_device_cuts_its_connections(self) -> None:
        tok = self.tokens(self.connect_app())
        device = self.pconn.execute("SELECT id FROM devices WHERE name='mac'").fetchone()["id"]
        killalot.revoke_device(self.pconn, device, "terminal")
        self.assertEqual(self.mcp(tok["access_token"], {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0], 401)

    def test_cors_for_chat_app_pages_only(self) -> None:
        req = urllib.request.Request(self.url + "/mcp", method="OPTIONS", headers={"Origin": "https://claude.ai", "Access-Control-Request-Method": "POST"})
        with urllib.request.urlopen(req, timeout=5) as res:
            self.assertEqual(res.status, 204)
            self.assertEqual(res.headers["Access-Control-Allow-Origin"], "https://claude.ai")
            self.assertIn("Authorization", res.headers["Access-Control-Allow-Headers"])
            self.assertIsNone(res.headers["Access-Control-Allow-Credentials"])
        status, _, headers = self.call("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"Origin": "https://claude.ai"})
        self.assertEqual((status, headers["Access-Control-Allow-Origin"]), (401, "https://claude.ai"))
        self.assertIn("WWW-Authenticate", headers["Access-Control-Expose-Headers"])
        status, _, headers = self.call("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"Origin": "https://evil.example"})
        self.assertIsNone(headers["Access-Control-Allow-Origin"])
        req = urllib.request.Request(self.url + "/api/inbox", method="OPTIONS", headers={"Origin": "https://claude.ai"})
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(req, timeout=5)

    def test_discovery_without_a_token_never_locks_out(self) -> None:
        for _ in range(killalot.FAILED_AUTH_LIMIT + 2):
            self.assertEqual(self.call("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0], 401)
        self.assertEqual(self.call("/.well-known/oauth-authorization-server")[0], 200)  # not locked out
        for _ in range(killalot.FAILED_AUTH_LIMIT):
            self.call("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"Authorization": "Bearer forged"})
        self.assertEqual(self.call("/.well-known/oauth-authorization-server")[0], 429)

    def test_connector_can_live_on_its_own_origin(self) -> None:
        config = self.public_config('[mcp]\npublic_base = "https://hub.example.ts.net/"\n')
        self.assertEqual(config.connector_base, "https://hub.example.ts.net")
        resource, server = killalot.oauth_metadata(config)
        self.assertEqual((resource["resource"], server["token_endpoint"]), ("https://hub.example.ts.net/mcp", "https://hub.example.ts.net/oauth/token"))
        with self.assertRaisesRegex(killalot.KillalotError, "https origin"):
            self.public_config('[mcp]\npublic_base = "http://plain.example"\n')

    def test_off_in_tailscale_mode(self) -> None:
        base = self.serve(self.config)
        with self.assertRaises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + "/.well-known/oauth-authorization-server", timeout=5)
        self.assertEqual(err.exception.code, 404)


class CliTests(Fixture):
    def test_end_to_end_cli(self) -> None:
        code, out = self.run_cli("--json", "propose", "--project", str(self.ladder), "--title", "Update EXPERIMENTS.md", "--evidence", "JOURNAL.md")
        self.assertEqual(code, 0, out)
        item_id = json.loads(out)["id"]
        with self.assertRaises(SystemExit):  # --via is required
            self.run_cli("accept", str(item_id))
        self.assertEqual(self.run_cli("accept", str(item_id), "--via", VIA)[0], 0)
        self.assertEqual(self.run_cli("start", str(item_id))[0], 0)
        code, out = self.run_cli("done", str(item_id), "--evidence", "commit abc")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("show", str(item_id))
        self.assertIn("commit abc", out)
        for cmd in (("projects",), ("inbox",), ("list", "--project", "ladder"), ("digest", "--peek"), ("device", "list")):
            self.assertEqual(self.run_cli(*cmd)[0], 0, cmd)

    def test_missing_config_is_a_clean_error(self) -> None:
        code, out = killalot.main(["--config", str(self.base / "nope.toml"), "inbox"]), ""
        self.assertEqual(code, 2)

    def test_device_add_prints_public_link(self) -> None:
        code, out = self.run_cli("device", "add", "phone")
        self.assertEqual(code, 0)
        self.assertIn("https://hub.example.ts.net/pair?code=", out)


class DeployTests(Fixture):
    def test_deploy_copies_release_and_writes_wrapper_and_unit(self) -> None:
        home = self.base / "home"
        result = killalot.deploy(self.config, start_service=False, home=home)
        current = self.config.app_dir / "current"
        self.assertTrue((current / "scripts" / "killalot.py").is_file())
        self.assertTrue((current / "assets" / "app.html").is_file())
        self.assertEqual((current / "VERSION").read_text().strip(), result["version"])
        wrapper = (home / ".local/bin/killalot").read_text()
        self.assertIn(str(current / "scripts" / "killalot.py"), wrapper)
        self.assertIn(str(self.config_path), wrapper)
        unit = (home / ".config/systemd/user" / killalot.SERVICE_NAME).read_text()
        self.assertNotRegex(unit, r"__[A-Z_]+__")
        self.assertIn(f"PATH={home}/.local/bin:", unit)  # the assistant backend is found from the service
        self.assertIn(f"{current}/scripts/killalot.py serve", unit)
        # the deployed copy knows its version without the plugin manifest
        out = subprocess.run([sys.executable, "-c", f"import importlib.util,sys;s=importlib.util.spec_from_file_location('k','{current}/scripts/killalot.py');m=importlib.util.module_from_spec(s);sys.modules['k']=m;s.loader.exec_module(m);print(m.plugin_version())"],
                             capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), result["version"])

    def test_deploy_runs_systemctl_and_reports_health(self) -> None:
        calls = []
        with mock.patch.object(killalot.subprocess, "run", side_effect=lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", "")), \
             mock.patch.object(killalot, "wait_healthy", return_value=True):
            result = killalot.deploy(self.config, home=self.base / "home")
        self.assertTrue(result["healthy"])
        self.assertIn(["systemctl", "--user", "restart", killalot.SERVICE_NAME], calls)


if __name__ == "__main__":
    unittest.main()
