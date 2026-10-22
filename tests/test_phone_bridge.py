from __future__ import annotations

import base64
import dataclasses
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/research/skills/phone-bridge/scripts/bridge.py"
SPEC = importlib.util.spec_from_file_location("bridge", SCRIPT)
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
sys.modules["bridge"] = bridge
SPEC.loader.exec_module(bridge)

PROVISIONERS = [
    {"type": "JWK", "name": "admin"},
    {
        "type": "OIDC", "name": "cineca-hpc", "clientID": "step-ca", "clientSecret": "published-secret",
        "configurationEndpoint": "https://sso.example/realms/X/.well-known/openid-configuration",
        "listenAddress": ":10000", "claims": {"maxUserSSHCertDuration": "48h3m0s"},
    },
]
DISCOVERY = {"authorization_endpoint": "https://sso.example/auth", "token_endpoint": "https://sso.example/token"}

FAKE_STEP = """#!/bin/sh
echo "$@" >> "$FAKE_STEP_LOG"
case "$1 $2" in
  "ca provisioner") cat "$FAKE_STEP_PROVISIONERS" ;;
  "ssh certificate") exit "${FAKE_STEP_RC:-0}" ;;
esac
"""


class PhoneBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.token = self.base / "telegram.token"
        self.token.write_text("123456:" + "A" * 35 + "\n")
        self.token.chmod(0o600)
        bin_dir = self.base / "bin"
        bin_dir.mkdir()
        (bin_dir / "step").write_text(FAKE_STEP)
        (bin_dir / "step").chmod(0o755)
        (self.base / "provisioners.json").write_text(json.dumps(PROVISIONERS))
        self.registry = self.base / "machines.toml"
        self.registry.write_text(f"""
[phone]
token_file = "{self.token}"
chat_id = 42
state_dir = "{self.base / 'state'}"

[machines.leonardo]
ssh = "leonardo"
ssh_cert = "{self.base / 'id_leonardo-cert.pub'}"

[machines.leonardo.ssh_login]
identity = "me@uni.it"
provisioner = "cineca-hpc"
command = "renew_cineca"
public_key = "{self.base / 'id_leonardo.pub'}"
""")
        patches = [
            mock.patch.dict(os.environ, {
                "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_STEP_LOG": str(self.base / "step.log"),
                "FAKE_STEP_PROVISIONERS": str(self.base / "provisioners.json"),
            }),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.config = bridge.load_config(self.registry)
        self.sent: list[str] = []
        self.http_calls: list[tuple[str, dict | None]] = []

    def fake_http(self, url: str, data: dict | None = None, timeout: float = 30) -> dict:
        self.http_calls.append((url, data))
        if url.endswith("openid-configuration"):
            return DISCOVERY
        if url == DISCOVERY["token_endpoint"]:
            return {"id_token": "header.payload.sig"}
        if "sendMessage" in url:
            self.sent.append(data["text"])
            return {"ok": True, "result": {}}
        raise AssertionError(url)

    def test_config_reads_the_login_and_refuses_a_readable_token(self) -> None:
        [login] = self.config.logins
        self.assertEqual((login.rig, login.identity, login.provisioner), ("leonardo", "me@uni.it", "cineca-hpc"))
        self.assertTrue(bridge.read_token(self.config).startswith("123456:"))
        self.token.chmod(0o644)
        with self.assertRaisesRegex(bridge.BridgeError, "chmod 600"):
            bridge.read_token(self.config)

    def test_the_link_uses_the_one_accepted_redirect_and_keeps_the_verifier_on_the_hub(self) -> None:
        with mock.patch.object(bridge, "http_json", side_effect=self.fake_http):
            link = bridge.start_renewal(self.config, self.config.logins[0])
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
        self.assertTrue(link.startswith(DISCOVERY["authorization_endpoint"] + "?"))
        self.assertEqual(query["redirect_uri"], ["http://127.0.0.1:10000"])
        self.assertEqual(query["client_id"], ["step-ca"])
        self.assertNotIn("published-secret", link)
        pending_path = self.base / "state" / "pending.json"
        pending = json.loads(pending_path.read_text())
        record = pending[query["state"][0]]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(record["verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertNotIn(record["verifier"], link)
        self.assertEqual(stat.S_IMODE(pending_path.stat().st_mode), 0o600)

    def test_a_pasted_redirect_is_redeemed_and_the_existing_key_signed_for_the_longest_validity(self) -> None:
        later = datetime.now().astimezone() + timedelta(hours=48)
        with mock.patch.object(bridge, "http_json", side_effect=self.fake_http):
            link = bridge.start_renewal(self.config, self.config.logins[0])
            state = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)["state"][0]
            pasted = f"here you go http://127.0.0.1:10000/?state={state}&session_state=x&code=THE-CODE"
            with mock.patch.object(bridge, "cert_expiry", return_value=later):
                reply = bridge.handle(self.config, "tok", pasted)
        self.assertIn("leonardo: renewed, valid until", reply)
        url, data = self.http_calls[-1]
        self.assertEqual(url, DISCOVERY["token_endpoint"])
        self.assertEqual((data["code"], data["redirect_uri"], data["client_secret"]), ("THE-CODE", "http://127.0.0.1:10000", "published-secret"))
        self.assertTrue(data["code_verifier"])
        signed = (self.base / "step.log").read_text().splitlines()[-1]
        self.assertIn(f"ssh certificate me@uni.it {self.base / 'id_leonardo.pub'} --sign --token header.payload.sig", signed)
        self.assertIn("--not-after 48h", signed)
        with mock.patch.object(bridge, "http_json", side_effect=self.fake_http), self.assertRaisesRegex(bridge.BridgeError, "already used"):
            bridge.handle(self.config, "tok", pasted)

    def test_a_refused_code_or_an_error_redirect_is_reported(self) -> None:
        with self.assertRaisesRegex(bridge.BridgeError, "access_denied"):
            bridge.parse_redirect("http://127.0.0.1:10000/?error=access_denied&error_description=nope&state=s")
        self.assertIsNone(bridge.parse_redirect("hello"))

        def refusing(url, data=None, timeout=30):
            return {"error": "invalid_grant", "error_description": "Code not valid"} if url == DISCOVERY["token_endpoint"] else self.fake_http(url, data)

        with mock.patch.object(bridge, "http_json", side_effect=refusing):
            link = bridge.start_renewal(self.config, self.config.logins[0])
            state = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)["state"][0]
            with self.assertRaisesRegex(bridge.BridgeError, "Code not valid.*renew"):
                bridge.finish_renewal(self.config, state, "late")

    def test_the_link_is_sent_once_before_expiry_and_once_after(self) -> None:
        soon = datetime.now().astimezone() + timedelta(minutes=30)
        warned: dict = {}
        with mock.patch.object(bridge, "http_json", side_effect=self.fake_http):
            with mock.patch.object(bridge, "cert_expiry", return_value=soon):
                bridge.check_certs(self.config, "tok", warned)
                bridge.check_certs(self.config, "tok", warned)
            self.assertEqual(len(self.sent), 1)
            self.assertIn("expires at", self.sent[0])
            self.assertIn("redirect_uri=http%3A%2F%2F127.0.0.1%3A10000", self.sent[0])
        past = datetime.now().astimezone() - timedelta(minutes=1)
        with mock.patch.object(bridge, "http_json", side_effect=self.fake_http), mock.patch.object(bridge, "cert_expiry", return_value=past):
            bridge.check_certs(self.config, "tok", {f"leonardo:{past.isoformat()}": "expiring"})
        self.assertIn("expired at", self.sent[-1])

    def test_commands_and_help(self) -> None:
        with mock.patch.object(bridge, "http_json", side_effect=self.fake_http):
            self.assertIn("log in here", bridge.handle(self.config, "tok", "/renew_cineca"))
            self.assertIn("log in here", bridge.handle(self.config, "tok", "/renew_cineca@dansolombrino_rig_bot"))
            self.assertIn("log in here", bridge.handle(self.config, "tok", "/renew"))
        self.assertEqual(bridge.commands(self.config)[0][0], "renew_cineca")
        self.assertIn("/renew_cineca", bridge.handle(self.config, "tok", "/renew_behemoth"))
        with mock.patch.object(bridge, "cert_expiry", return_value=None):
            self.assertIn("no valid certificate", bridge.handle(self.config, "tok", "/status"))
        self.assertIn("/renew_cineca", bridge.handle(self.config, "tok", "hi"))

    def test_ipv4_is_tried_before_ipv6(self) -> None:
        v6 = (bridge.socket.AF_INET6, 1, 6, "", ("::1", 443, 0, 0))
        v4 = (bridge.socket.AF_INET, 1, 6, "", ("127.0.0.1", 443))
        with mock.patch.object(bridge, "_getaddrinfo", return_value=[v6, v4]):
            self.assertEqual(bridge.socket.getaddrinfo("api.telegram.org", 443), [v4, v6])

    def test_send_from_an_older_install_runs_the_newest_one(self) -> None:
        newer = self.base / "research/9.9.9/skills/phone-bridge/scripts/bridge.py"
        argv = ["--registry", str(self.registry), "send", "hi", "--context", "{}"]
        with mock.patch.object(bridge, "newer_installed_copy", return_value=newer), \
                mock.patch.object(bridge.os, "execv", side_effect=SystemExit(0)) as execv, self.assertRaises(SystemExit):
            bridge.main(argv)
        self.assertEqual(execv.call_args.args[1][1:], [str(newer), *argv])

    def test_the_longest_duration_comes_from_the_ca(self) -> None:
        self.assertEqual(bridge.longest_duration(PROVISIONERS[1]), "48h")
        self.assertIsNone(bridge.longest_duration({"claims": {}}))


class WaveThreadTests(unittest.TestCase):
    """Each wave in its own topic; a reply reaches the agent session that wrote."""

    ROOT = "/home/u/proj"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        token = base / "telegram.token"
        token.write_text("123456:" + "A" * 35 + "\n")
        token.chmod(0o600)
        registry = base / "machines.toml"
        registry.write_text(f'[phone]\ntoken_file = "{token}"\nchat_id = -1001\nstate_dir = "{base / "state"}"\n')
        self.config = bridge.load_config(registry)
        self.forum = True
        self.calls: list[tuple[str, dict]] = []
        self.next_id = 100
        self.supervisor_calls: list[tuple[str, ...]] = []
        self.table = "Status written 2026-10-22 at 10:00 — ETA 12:00 (~2h) · 1/4 done\n"
        patches = [mock.patch.object(bridge, "http_json", side_effect=self.fake_http),
                   mock.patch.object(bridge, "supervisor", side_effect=self.fake_supervisor)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def fake_http(self, url: str, data: dict | None = None, timeout: float = 30) -> dict:
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, data or {}))
        self.next_id += 1
        if method == "getChat":
            return {"ok": True, "result": {"id": -1001, "type": "supergroup", "is_forum": self.forum}}
        if method == "createForumTopic":
            return {"ok": True, "result": {"message_thread_id": self.next_id, "name": data["name"]}}
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": self.next_id}}
        return {"ok": True, "result": True}

    def fake_supervisor(self, config, root, *argv, timeout=60):
        self.supervisor_calls.append((root, *argv))
        if argv[0] == "waves":
            return subprocess.CompletedProcess(argv, 0, f"20261022-090000\t{self.ROOT}\n", "")
        if argv[0] == "table":
            return subprocess.CompletedProcess(argv, 0, self.table, "")
        return subprocess.CompletedProcess(argv, 0, "[tell] 20261022-112747-000001: queued for wave A\n", "")

    def sent(self) -> list[dict]:
        return [data for method, data in self.calls if method == "sendMessage"]

    def notice(self, wave: str | None, **extra) -> dict:
        context = {"kind": "say", "silent": False, **extra}
        if wave:
            context.update(project="proj", root=self.ROOT, wave=wave)
        return context

    def test_each_wave_gets_its_own_thread_and_quiet_notices_make_no_sound(self) -> None:
        bridge.send_notice(self.config, "proj wave A: hi", self.notice("A", body="hi"), token="tok")
        bridge.send_notice(self.config, "proj wave B: yo", self.notice("B", body="yo", silent=True), token="tok")
        bridge.send_notice(self.config, "proj wave A: again", self.notice("A", body="again"), token="tok")
        bridge.send_notice(self.config, "leonardo: SSH certificate expires 10:00", self.notice(None, kind="cert-expiring"), token="tok")
        topics = [data["name"] for method, data in self.calls if method == "createForumTopic"]
        self.assertEqual(topics, ["proj · A", "proj · B"])
        a, b, again, cert = self.sent()
        self.assertEqual(a["message_thread_id"], again["message_thread_id"])
        self.assertNotEqual(a["message_thread_id"], b["message_thread_id"])
        self.assertEqual((a["text"], b.get("disable_notification")), ("hi", "true"), "a thread needs no wave prefix")
        self.assertNotIn("message_thread_id", cert, "fleet notices go to the general thread")
        self.assertEqual(stat.S_IMODE((self.config.state_dir / "chat.json").stat().st_mode), 0o600)

    def test_a_reply_reaches_the_session_that_wrote_and_a_thread_reaches_its_wave(self) -> None:
        bridge.send_notice(self.config, "q", self.notice("A", kind="ask", session="s-1", question="q2", body="❓ [q2] lend gpu?"), token="tok")
        asked = self.sent()[-1]
        thread = asked["message_thread_id"]
        question_id = self.next_id
        reply = {"message_id": 7, "text": "yes until Friday", "is_topic_message": True, "message_thread_id": thread,
                 "reply_to_message": {"message_id": question_id}}
        bridge.respond(self.config, "tok", reply, thread)
        self.assertEqual(self.supervisor_calls[-1], (self.ROOT, "tell", "--wave", "A", "--text", "yes until Friday", "--session", "s-1", "--question", "q2"))
        status = self.sent()[-1]
        self.assertEqual(status["text"], "📨 received · waiting for the wave's agent · id 20261022-112747-000001")
        self.assertEqual((status["disable_notification"], json.loads(status["reply_parameters"])["message_id"]), ("true", 7))
        line_id = self.next_id
        body = "✓ answered in 3m · id 20261022-112747-000001"
        bridge.send_notice(self.config, "proj wave A: " + body, self.notice("A", kind="receipt", body=body, receipts=["20261022-112747-000001"]), token="tok")
        self.assertEqual(self.calls[-1], ("editMessageText", {"chat_id": -1001, "message_id": line_id, "text": body}),
                         "the status line under the user's message is edited, never a new message")
        plain = {"message_id": 8, "text": "and pause the 3080", "is_topic_message": True, "message_thread_id": thread,
                 "reply_to_message": {"message_id": thread}}
        bridge.respond(self.config, "tok", plain, thread)
        self.assertEqual(self.supervisor_calls[-1], (self.ROOT, "tell", "--wave", "A", "--text", "and pause the 3080"))
        bridge.respond(self.config, "tok", {"message_id": 9, "text": "hello"}, None)
        self.assertIn("write in the wave's thread", self.sent()[-1]["text"], "the general thread belongs to no wave")
        bridge.respond(self.config, "tok", {"message_id": 10, "text": "/status", "is_topic_message": True, "message_thread_id": thread}, thread)
        self.assertEqual((self.sent()[-1]["text"], self.sent()[-1]["message_thread_id"]), (self.table.strip(), thread))

    def test_without_topics_plain_text_goes_to_the_wave_that_wrote_last(self) -> None:
        self.forum = False
        bridge.send_notice(self.config, "proj wave A: x", self.notice("A"), token="tok")
        bridge.send_notice(self.config, "proj wave B: y", self.notice("B"), token="tok")
        self.assertFalse([c for c in self.calls if c[0] == "createForumTopic"])
        self.assertEqual(self.sent()[-1]["text"], "proj wave B: y", "one shared thread keeps the wave prefix")
        bridge.respond(self.config, "tok", {"message_id": 3, "text": "go on"}, None)
        self.assertEqual(self.supervisor_calls[-1][:4], (self.ROOT, "tell", "--wave", "B"))

    def test_moving_to_another_chat_forgets_the_old_ones_threads(self) -> None:
        self.forum = False
        bridge.send_notice(self.config, "proj wave A: x", self.notice("A"), token="tok")
        self.forum = True
        self.config = dataclasses.replace(self.config, chat_id=-1002)
        bridge.send_notice(self.config, "proj wave A: y", self.notice("A", body="y"), token="tok")
        self.assertEqual([m for m, _ in self.calls].count("getChat"), 2, "the new chat is asked again")
        self.assertEqual(self.sent()[-1]["text"], "y", "and gets its own thread")

    def test_every_wave_has_a_pinned_card_kept_current_by_edits(self) -> None:
        bridge.refresh_cards(self.config, "tok")
        methods = [method for method, _data in self.calls]
        self.assertEqual(methods, ["getChat", "createForumTopic", "sendMessage", "pinChatMessage"])
        [card] = self.sent()
        self.assertEqual(card["disable_notification"], "true")
        self.calls.clear()
        bridge.refresh_cards(self.config, "tok")
        self.assertEqual([m for m, _ in self.calls], [], "an unchanged card costs nothing")
        self.table = "Status written 2026-10-22 at 12:01 — finished 12:01 · 4/4 done\n"
        bridge.refresh_cards(self.config, "tok")
        self.assertEqual([m for m, _ in self.calls], ["editMessageText", "closeForumTopic"], "a finished wave's topic is closed")
        self.calls.clear()
        bridge.refresh_cards(self.config, "tok")
        self.assertEqual(self.calls, [], "a finished wave's card stays as it ended")

    def test_a_project_with_its_own_group_gets_its_waves_topics_there(self) -> None:
        self.config = dataclasses.replace(self.config, projects={"proj": -2002})
        other = {"kind": "say", "project": "other", "root": "/home/u/other", "wave": "C", "body": "c"}
        bridge.send_notice(self.config, "proj wave A: hi", self.notice("A", body="hi"), token="tok")
        hi = self.next_id
        bridge.send_notice(self.config, "other wave C: c", other, token="tok")
        created = [data for method, data in self.calls if method == "createForumTopic"]
        self.assertEqual([(c["chat_id"], c["name"]) for c in created], [(-2002, "A"), (-1001, "other · C")],
                         "in its own group the wave alone names the topic")
        a, c = self.sent()
        self.assertEqual((a["chat_id"], c["chat_id"]), (-2002, -1001))
        self.assertIn(created[1]["icon_color"], bridge.TOPIC_COLORS)
        self.assertEqual(self.config.chats, {-1001, -2002})
        question = {"message_id": 5, "text": "how far?", "chat": {"id": -2002}, "reply_to_message": {"message_id": hi}}
        # message ids are per chat: the same id in the main chat is not this conversation
        self.assertIsNone(bridge.conversation(self.config, {**question, "chat": {"id": -1001}}))
        bridge.respond(self.config, "tok", question, None)
        self.assertEqual(self.supervisor_calls[-1][:4], (self.ROOT, "tell", "--wave", "A"))
        self.assertEqual(self.sent()[-1]["chat_id"], -2002, "the status line goes where the user wrote")

    def test_a_group_set_up_while_a_wave_runs_takes_it_over_and_closes_the_old_topic(self) -> None:
        bridge.send_notice(self.config, "proj wave A: hi", self.notice("A", body="hi"), token="tok")
        old_thread = self.sent()[-1]["message_thread_id"]
        self.config = dataclasses.replace(self.config, projects={"proj": -2002})
        bridge.send_notice(self.config, "proj wave A: again", self.notice("A", body="again"), token="tok")
        self.assertIn(("closeForumTopic", {"chat_id": -1001, "message_thread_id": old_thread}), self.calls)
        self.assertEqual(self.sent()[-1]["chat_id"], -2002)

    def test_finished_waves_from_before_are_closed_once_and_old_state_keeps_its_threads(self) -> None:
        self.config.state_dir.mkdir(parents=True)
        (self.config.state_dir / "chat.json").write_text(json.dumps({
            "chat": -1001, "forum": True, "messages": {"77": {"root": self.ROOT, "wave": "A"}}, "receipts": {"r1": 78},
            "topics": {f"{self.ROOT}::old": {"thread": 9, "name": "proj · old", "root": self.ROOT, "wave": "old", "done": True},
                       f"{self.ROOT}::20261022-090000": {"thread": 10, "name": "proj · 20261022-090000", "root": self.ROOT,
                                                         "wave": "20261022-090000", "card": 11, "card_text": self.table.strip(), "done": False}}}))
        bridge.refresh_cards(self.config, "tok")
        self.assertEqual(self.calls, [("closeForumTopic", {"chat_id": -1001, "message_thread_id": 9})],
                         "no new topic, no getChat: the old state's thread and chat kind carry over")
        self.calls.clear()
        bridge.refresh_cards(self.config, "tok")
        self.assertEqual(self.calls, [])
        reply = {"message_id": 80, "text": "go", "chat": {"id": -1001}, "reply_to_message": {"message_id": 77}}
        self.assertEqual(bridge.conversation(self.config, reply)["wave"], "A")
        bridge.send_notice(self.config, "x", self.notice("A", kind="receipt", body="✓ done", receipts=["r1"]), token="tok")
        self.assertEqual(self.calls[-1], ("editMessageText", {"chat_id": -1001, "message_id": 78, "text": "✓ done"}))

    def test_a_project_group_must_be_an_integer_chat_id(self) -> None:
        registry = self.config.registry
        registry.write_text(registry.read_text() + '\n[phone.projects]\nproj = "-100"\n')
        with self.assertRaisesRegex(bridge.BridgeError, "phone.projects"):
            bridge.load_config(registry)


if __name__ == "__main__":
    unittest.main()
