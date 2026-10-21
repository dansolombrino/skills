#!/usr/bin/env python3
"""Telegram bridge on the hub: push notices to the user's phone and renew short-lived SSH certificates.

A cluster such as CINECA Leonardo hands out a 12-hour SSH certificate only after a browser login
with a password and a one-time code. That login is the user's and stays the user's. This bridge
does everything around it: before the certificate expires it sends a login link (an OIDC
authorization request with PKCE whose secret half never leaves the hub); the user logs in on the
phone, the browser lands on the unreachable `http://127.0.0.1:<port>/?code=...` redirect, and the
user pastes that address back to the bot. The hub then redeems the code, has `step` sign the
existing public key, and confirms the new validity. A pasted code is useless to anyone else.

Only the configured chat is obeyed. Stdlib only; nothing listens on the network.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

API = "https://api.telegram.org/bot{token}/{method}"
WARN_BEFORE_S = 3600  # the login link arrives this long before expiry
PENDING_TTL_S = 1800  # a link older than this is replaced on the next /renew
POLL_TIMEOUT_S = 50
CERT_CHECK_EVERY_S = 60
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


class BridgeError(RuntimeError):
    pass


# ───────────────────────────── configuration ─────────────────────────────


@dataclass(frozen=True)
class Login:
    """How one rig's certificate is renewed: `[machines.<rig>.ssh_login]` beside its `ssh_cert`."""

    rig: str
    cert: Path
    public_key: Path
    identity: str
    provisioner: str


@dataclass(frozen=True)
class Config:
    registry: Path
    token_file: Path
    chat_id: int | None
    state_dir: Path
    logins: tuple[Login, ...]


def _path(value: object, where: str) -> Path:
    if not isinstance(value, str) or not value:
        raise BridgeError(f"{where} must be a path")
    return Path(value).expanduser()


def load_config(registry: Path) -> Config:
    try:
        data = tomllib.loads(registry.read_text())
    except OSError as exc:
        raise BridgeError(f"missing registry: {registry}") from exc
    phone = data.get("phone")
    if not isinstance(phone, dict):
        raise BridgeError(f"{registry}: no [phone] table (token_file, chat_id); see the phone-bridge setup")
    chat_id = phone.get("chat_id")
    if chat_id is not None and (not isinstance(chat_id, int) or isinstance(chat_id, bool)):
        raise BridgeError(f"{registry}: phone.chat_id must be an integer")
    logins = []
    for rig, entry in (data.get("machines") or {}).items():
        login = entry.get("ssh_login") if isinstance(entry, dict) else None
        if login is None:
            continue
        where = f"{registry}: machines.{rig}.ssh_login"
        if not isinstance(login, dict) or "ssh_cert" not in entry:
            raise BridgeError(f"{where} needs machines.{rig}.ssh_cert beside it")
        identity = login.get("identity")
        if not isinstance(identity, str) or "@" not in identity:
            raise BridgeError(f"{where}.identity must be the login e-mail")
        provisioner = login.get("provisioner")
        if not isinstance(provisioner, str) or not provisioner:
            raise BridgeError(f"{where}.provisioner must name the CA's OIDC provisioner")
        logins.append(Login(rig, _path(entry["ssh_cert"], f"{where}: ssh_cert"),
                            _path(login.get("public_key"), f"{where}.public_key"), identity, provisioner))
    return Config(
        registry=registry,
        token_file=_path(phone.get("token_file"), f"{registry}: phone.token_file"),
        chat_id=chat_id,
        state_dir=_path(phone.get("state_dir", "~/.local/state/phone-bridge"), f"{registry}: phone.state_dir"),
        logins=tuple(logins),
    )


def read_token(config: Config) -> str:
    try:
        mode = config.token_file.stat().st_mode
        token = config.token_file.read_text().strip()
    except OSError as exc:
        raise BridgeError(f"cannot read the bot token file {config.token_file}") from exc
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise BridgeError(f"{config.token_file} must be readable by its owner only (chmod 600)")
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", token):
        raise BridgeError(f"{config.token_file} does not hold a bot token from @BotFather")
    return token


# ───────────────────────────── telegram ─────────────────────────────

_getaddrinfo = socket.getaddrinfo


def _ipv4_first(*args, **kwargs):
    """IPv4 addresses before IPv6: a hub whose IPv6 route silently drops packets otherwise spends
    the whole request timeout in SYN-SENT on every call before falling back."""
    return sorted(_getaddrinfo(*args, **kwargs), key=lambda info: info[0] != socket.AF_INET)


socket.getaddrinfo = _ipv4_first


def http_json(url: str, data: dict | None = None, timeout: float = 30) -> dict:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except (OSError, ValueError):
            raise BridgeError(f"HTTP {exc.code} from {urllib.parse.urlsplit(url).netloc}") from None
    except (OSError, ValueError) as exc:
        raise BridgeError(f"cannot reach {urllib.parse.urlsplit(url).netloc}: {exc}") from None


def telegram(token: str, method: str, http_timeout: float = 30, **params: object) -> object:
    reply = http_json(API.format(token=token, method=method), {k: v for k, v in params.items() if v is not None}, http_timeout)
    if not reply.get("ok"):
        raise BridgeError(f"telegram {method}: {reply.get('description', 'failed')}")
    return reply.get("result")


def send(config: Config, text: str, token: str | None = None) -> None:
    if config.chat_id is None:
        raise BridgeError("phone.chat_id is not set yet: message the bot, then run `bridge.py chat-id`")
    telegram(token or read_token(config), "sendMessage", chat_id=config.chat_id, text=text[:4000],
             disable_web_page_preview="true")


# ───────────────────────────── certificates ─────────────────────────────


def cert_expiry(path: Path) -> datetime | None:
    try:
        result = subprocess.run(["ssh-keygen", "-L", "-f", str(path)], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"Valid: from \S+ to (\S+)", result.stdout)
    if result.returncode != 0 or not match:
        return None
    try:
        return datetime.fromisoformat(match.group(1)).astimezone()
    except ValueError:
        return None


def step(*argv: str, timeout: float = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["step", *argv], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BridgeError(f"cannot run step: {exc}") from None


def provisioner(login: Login) -> dict:
    """The CA's published OIDC provisioner: discovery endpoint, client id and secret, callback port."""
    result = step("ca", "provisioner", "list")
    if result.returncode != 0:
        raise BridgeError(f"step ca provisioner list failed: {result.stderr.strip()[-200:]}")
    try:
        entries = json.loads(result.stdout)
    except ValueError as exc:
        raise BridgeError("step ca provisioner list did not print JSON") from exc
    for entry in entries:
        if entry.get("name") == login.provisioner and entry.get("type") == "OIDC":
            return entry
    raise BridgeError(f"the CA has no OIDC provisioner {login.provisioner!r}")


def redirect_uri(entry: dict) -> str:
    """The one redirect the IdP accepts for this client: the provisioner's loopback listen address."""
    port = str(entry.get("listenAddress") or ":10000").rsplit(":", 1)[-1]
    return f"http://127.0.0.1:{port}"


def longest_duration(entry: dict) -> str | None:
    """The longest user certificate the CA signs, in whole hours (`48h3m0s` -> `48h`), or None."""
    claim = str((entry.get("claims") or {}).get("maxUserSSHCertDuration") or "")
    match = re.match(r"^(\d+)h", claim)
    return f"{match.group(1)}h" if match and int(match.group(1)) > 0 else None


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Pending:
    """Renewals waiting for a pasted redirect, by state. 0600: it holds the PKCE verifiers."""

    def __init__(self, config: Config) -> None:
        self.path = config.state_dir / "pending.json"

    def load(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle)
        os.replace(tmp, self.path)


def start_renewal(config: Config, login: Login) -> str:
    """A fresh login link for `login`; the hub keeps the PKCE verifier and waits for the paste."""
    entry = provisioner(login)
    discovery = http_json(entry["configurationEndpoint"])
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    params = {
        "client_id": entry["clientID"], "response_type": "code", "scope": "openid email",
        "redirect_uri": redirect_uri(entry), "state": state, "nonce": nonce,
        "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
        "login_hint": login.identity,
    }
    pending = Pending(config)
    data = {key: value for key, value in pending.load().items() if value.get("rig") != login.rig}
    data[state] = {"rig": login.rig, "verifier": verifier, "created": time.time()}
    pending.save(data)
    return discovery["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)


def parse_redirect(text: str) -> tuple[str, str] | None:
    """(state, code) from a pasted `http://127.0.0.1:<port>/?...` address, or None."""
    match = re.search(r"https?://127\.0\.0\.1:\d+\S*", text)
    if not match:
        return None
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(match.group(0)).query)
    if "error" in query:
        raise BridgeError(f"the login page returned {query['error'][0]}: {query.get('error_description', [''])[0]}")
    if "code" not in query or "state" not in query:
        return None
    return query["state"][0], query["code"][0]


def finish_renewal(config: Config, state: str, code: str) -> str:
    """Redeem the pasted code and sign the existing key; returns the confirmation line."""
    pending = Pending(config)
    data = pending.load()
    record = data.pop(state, None)
    pending.save(data)
    if record is None:
        raise BridgeError("that link is not the latest one (or was already used); send /renew for a new one")
    login = next((item for item in config.logins if item.rig == record["rig"]), None)
    if login is None:
        raise BridgeError(f"{record['rig']} is no longer configured")
    entry = provisioner(login)
    discovery = http_json(entry["configurationEndpoint"])
    tokens = http_json(discovery["token_endpoint"], {
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(entry),
        "client_id": entry["clientID"], "client_secret": entry.get("clientSecret", ""), "code_verifier": record["verifier"],
    })
    id_token = tokens.get("id_token")
    if not id_token:
        raise BridgeError(f"the identity provider refused the code: {tokens.get('error_description') or tokens.get('error') or 'no id_token'}"
                          " (codes expire within about a minute; send /renew)")
    # ask for the longest validity the CA allows: fewer logins, same 2FA each time
    longest = longest_duration(entry)
    lifetime = ["--not-after", longest] if longest else []
    result = step("ssh", "certificate", login.identity, str(login.public_key), "--sign", "--token", id_token, "--no-agent", "--force", *lifetime)
    if result.returncode != 0:
        raise BridgeError(f"step could not sign the key: {result.stderr.strip()[-300:]}")
    expires = cert_expiry(login.cert)
    if expires is None or expires <= datetime.now().astimezone():
        raise BridgeError(f"step succeeded but {login.cert} is not valid; check that it sits beside {login.public_key}")
    return f"{login.rig}: renewed, valid until {expires.strftime('%Y-%m-%d %H:%M')}"


# ───────────────────────────── the bot ─────────────────────────────


HELP = (
    "/renew [rig] — a fresh login link\n"
    "/status — when each certificate expires\n"
    "After logging in, paste the address the browser could not open (http://127.0.0.1:…)."
)


def status_lines(config: Config) -> list[str]:
    lines = []
    for login in config.logins:
        expires = cert_expiry(login.cert)
        if expires is None:
            lines.append(f"{login.rig}: no valid certificate at {login.cert}")
            continue
        left = (expires - datetime.now().astimezone()).total_seconds()
        when = expires.strftime("%m-%d %H:%M")
        lines.append(f"{login.rig}: expired {when}" if left <= 0 else f"{login.rig}: valid until {when} ({int(left // 3600)}h{int(left % 3600 // 60):02d}m)")
    return lines or ["no rig declares [machines.<rig>.ssh_login]"]


def handle(config: Config, token: str, text: str) -> str:
    text = text.strip()
    if text.startswith("/renew"):
        wanted = text.split()[1:] or [login.rig for login in config.logins]
        replies = []
        for login in config.logins:
            if login.rig in wanted:
                replies.append(f"{login.rig}: log in here, then paste the address the browser cannot open:\n{start_renewal(config, login)}")
        return "\n\n".join(replies) or f"unknown rig; configured: {', '.join(l.rig for l in config.logins) or 'none'}"
    if text.startswith("/status"):
        return "\n".join(status_lines(config))
    pasted = parse_redirect(text)
    if pasted:
        return finish_renewal(config, *pasted)
    return HELP


def check_certs(config: Config, token: str, warned: dict[str, str]) -> None:
    """Send the login link once per certificate when it is within an hour of expiry, again once expired."""
    now = datetime.now().astimezone()
    for login in config.logins:
        expires = cert_expiry(login.cert)
        if expires is None:
            continue
        left = (expires - now).total_seconds()
        phase = "expired" if left <= 0 else "expiring" if left <= WARN_BEFORE_S else None
        key = f"{login.rig}:{expires.isoformat()}"
        if phase is None or warned.get(key) == phase:
            continue
        warned[key] = phase
        head = (f"{login.rig}: SSH certificate expired at {expires.strftime('%H:%M')}" if phase == "expired"
                else f"{login.rig}: SSH certificate expires at {expires.strftime('%H:%M')}")
        try:
            link = start_renewal(config, login)
            send(config, f"{head}. Log in here, then paste the address the browser cannot open:\n{link}", token)
        except BridgeError as exc:
            send(config, f"{head}; I could not prepare the login link: {exc}", token)


def serve(config: Config) -> int:
    token = read_token(config)
    if config.chat_id is None:
        raise BridgeError("phone.chat_id is not set yet: message the bot, then run `bridge.py chat-id`")
    print(f"[serve] {Path(__file__).resolve()}: chat {config.chat_id}, {len(config.logins)} certificate(s) watched", flush=True)
    offset, warned, last_check = None, {}, 0.0
    while True:
        maybe_self_update(config)
        if time.time() - last_check >= CERT_CHECK_EVERY_S:
            last_check = time.time()
            try:
                check_certs(config, token, warned)
            except BridgeError as exc:
                print(f"[serve] certificate check: {exc}", file=sys.stderr, flush=True)
        try:
            updates = telegram(token, "getUpdates", http_timeout=POLL_TIMEOUT_S + 10, offset=offset,
                               timeout=POLL_TIMEOUT_S, allowed_updates='["message"]')
        except BridgeError as exc:
            print(f"[serve] {exc}", file=sys.stderr, flush=True)
            time.sleep(10)
            continue
        for update in updates or []:
            offset = update["update_id"] + 1
            message = update.get("message") or {}
            if (message.get("chat") or {}).get("id") != config.chat_id or not message.get("text"):
                continue  # anyone else is ignored, silently
            try:
                reply = handle(config, token, message["text"])
            except BridgeError as exc:
                reply = f"✗ {exc}"
            send(config, reply, token)


def chat_ids(config: Config) -> int:
    """Print the chats that messaged the bot, so the user's own id can go into phone.chat_id."""
    for update in telegram(read_token(config), "getUpdates") or []:
        chat = (update.get("message") or {}).get("chat") or {}
        if chat:
            print(f"{chat.get('id')}\t{chat.get('username') or chat.get('first_name') or ''}")
    return 0


# ───────────────────────────── releases ─────────────────────────────


def newer_installed_copy(script: Path) -> Path | None:
    script = script.resolve()
    if len(script.parents) < 5 or not VERSION_RE.fullmatch(script.parents[3].name):
        return None
    version_dir = script.parents[3]
    relative = script.relative_to(version_dir)

    def version(name: str) -> tuple[int, ...]:
        return tuple(int(part) for part in name.split("."))

    candidates = [
        (version(sibling.name), sibling / relative)
        for sibling in version_dir.parent.iterdir()
        if VERSION_RE.fullmatch(sibling.name) and version(sibling.name) > version(version_dir.name) and (sibling / relative).is_file()
    ]
    return max(candidates)[1] if candidates else None


_REJECTED: set[Path] = set()


def maybe_self_update(config: Config) -> None:
    candidate = newer_installed_copy(Path(__file__))
    if candidate is None or candidate in _REJECTED:
        return
    probe = subprocess.run([sys.executable, str(candidate), "--registry", str(config.registry), "check"], capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        _REJECTED.add(candidate)
        return
    print(f"[serve] updating to {candidate}", flush=True)
    os.execv(sys.executable, [sys.executable, str(candidate), "--registry", str(config.registry), "serve"])


# ───────────────────────────── cli ─────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="validate [phone], the token file and every ssh_login")
    sub.add_parser("serve", help="run the bot")
    sub.add_parser("chat-id", help="list chats that messaged the bot")
    send_p = sub.add_parser("send", help="push one line to the phone (use as supervisor.notify_command)")
    send_p.add_argument("text")
    sub.add_parser("status", help="when each certificate expires")
    args = parser.parse_args(argv)
    registry = (args.registry or Path(os.environ.get("RIGSYNC_REGISTRY", "~/.config/rigsync/machines.toml")).expanduser()).resolve()
    try:
        config = load_config(registry)
        if args.command == "check":
            read_token(config)
            print(f"OK token file {config.token_file}")
            print(f"OK chat {config.chat_id}" if config.chat_id is not None else "TODO phone.chat_id: message the bot, then run chat-id")
            for login in config.logins:
                print(f"OK {login.rig}: {login.identity} via {login.provisioner}, key {login.public_key}, cert {login.cert}")
            return 0 if config.chat_id is not None else 1
        if args.command == "serve":
            return serve(config)
        if args.command == "chat-id":
            return chat_ids(config)
        if args.command == "send":
            send(config, args.text)
            return 0
        if args.command == "status":
            print("\n".join(status_lines(config)))
            return 0
    except BridgeError as exc:
        print(f"phone-bridge: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
