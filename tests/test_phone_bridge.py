from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import stat
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
            self.assertIn("log in here", bridge.handle(self.config, "tok", "/renew leonardo"))
            self.assertIn("unknown rig", bridge.handle(self.config, "tok", "/renew behemoth"))
        with mock.patch.object(bridge, "cert_expiry", return_value=None):
            self.assertIn("no valid certificate", bridge.handle(self.config, "tok", "/status"))
        self.assertIn("/renew", bridge.handle(self.config, "tok", "hi"))

    def test_ipv4_is_tried_before_ipv6(self) -> None:
        v6 = (bridge.socket.AF_INET6, 1, 6, "", ("::1", 443, 0, 0))
        v4 = (bridge.socket.AF_INET, 1, 6, "", ("127.0.0.1", 443))
        with mock.patch.object(bridge, "_getaddrinfo", return_value=[v6, v4]):
            self.assertEqual(bridge.socket.getaddrinfo("api.telegram.org", 443), [v4, v6])

    def test_the_longest_duration_comes_from_the_ca(self) -> None:
        self.assertEqual(bridge.longest_duration(PROVISIONERS[1]), "48h")
        self.assertIsNone(bridge.longest_duration({"claims": {}}))


if __name__ == "__main__":
    unittest.main()
