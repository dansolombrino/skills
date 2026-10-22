#!/usr/bin/env python3
"""Telegram bridge on the hub: notices and conversations with the supervisor agents, and SSH certificate renewal.

Every wave gets its own thread (a forum topic) holding a pinned, live status card, closed when the
wave finishes; its notices land there, and whatever the user writes there (or in reply to one of its
messages) goes to that wave's supervisor agent through `sweep-supervisor tell`, which resumes the
session that wrote the message. A project with its own group (`[phone.projects]`) has its waves'
topics there; every other project shares the main chat.

A cluster such as CINECA Leonardo hands out a 12-hour SSH certificate only after a browser login
with a password and a one-time code. That login is the user's and stays the user's. This bridge
does everything around it: before the certificate expires it sends a login link (an OIDC
authorization request with PKCE whose secret half never leaves the hub); the user logs in on the
phone, the browser lands on the unreachable `http://127.0.0.1:<port>/?code=...` redirect, and the
user pastes that address back to the bot. The hub then redeems the code, has `step` sign the
existing public key, and confirms the new validity. A pasted code is useless to anyone else.

Only the configured chats are obeyed. Stdlib only; nothing listens on the network.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
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
import zlib
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
CARD_EVERY_S = 120  # how often each wave's pinned status card is refreshed (an edit, never a new message)
MESSAGES_KEPT = 2000  # sent messages remembered for routing replies
SUPERVISOR = Path(__file__).resolve().parents[2] / "sweep-supervisor" / "scripts" / "supervisor.py"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
TOPIC_COLORS = (0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F)  # the only icon colors Telegram accepts


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
    command: str  # the bot command that sends this rig's login link, without the slash


@dataclass(frozen=True)
class Config:
    registry: Path
    token_file: Path
    chat_id: int | None
    state_dir: Path
    logins: tuple[Login, ...]
    projects: dict[str, int]  # project (its root's folder name) -> the group its waves' topics live in

    @property
    def chats(self) -> set[int]:
        """Every chat the bot obeys: the main one and each project's group."""
        return {chat for chat in (self.chat_id, *self.projects.values()) if chat is not None}

    def chat_for(self, project: str | None) -> int | None:
        return self.projects.get(project or "", self.chat_id)


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
    projects = phone.get("projects") or {}
    if not isinstance(projects, dict) or not all(isinstance(v, int) and not isinstance(v, bool) for v in projects.values()):
        raise BridgeError(f"{registry}: [phone.projects] maps each project's folder name to its group's chat id (an integer)")
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
        command = login.get("command", "renew_" + re.sub(r"[^a-z0-9_]", "_", rig.lower()))
        if not isinstance(command, str) or not re.fullmatch(r"[a-z0-9_]{1,32}", command):
            raise BridgeError(f"{where}.command must be 1-32 lowercase letters, digits or underscores (Telegram's rule)")
        logins.append(Login(rig, _path(entry["ssh_cert"], f"{where}: ssh_cert"),
                            _path(login.get("public_key"), f"{where}.public_key"), identity, provisioner, command))
    return Config(
        registry=registry,
        token_file=_path(phone.get("token_file"), f"{registry}: phone.token_file"),
        chat_id=chat_id,
        state_dir=_path(phone.get("state_dir", "~/.local/state/phone-bridge"), f"{registry}: phone.state_dir"),
        logins=tuple(logins),
        projects=dict(projects),
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


def send(config: Config, text: str, token: str | None = None, thread: int | None = None, silent: bool = False,
         reply_to: int | None = None, chat: int | None = None) -> dict:
    """One message, to the main chat unless `chat` names a project's group."""
    chat = chat if chat is not None else config.chat_id
    if chat is None:
        raise BridgeError("phone.chat_id is not set yet: message the bot, then run `bridge.py chat-id`")
    reply = json.dumps({"message_id": reply_to, "allow_sending_without_reply": True}) if reply_to else None
    result = telegram(token or read_token(config), "sendMessage", chat_id=chat, text=text[:4000], message_thread_id=thread,
                      disable_web_page_preview="true", disable_notification="true" if silent else None, reply_parameters=reply)
    return result if isinstance(result, dict) else {}


# ───────────────────────────── waves: threads and conversations ─────────────────────────────


class ChatState:
    """Each chat's kind, each wave's thread and status card, and the wave and agent session behind every
    message sent, so a reply reaches the agent that wrote. Message ids are per chat, so messages are
    keyed `<chat>:<id>`. `send` runs as its own process per notice while `serve` runs, so every use
    holds a file lock; 0600 like the rest of the state."""

    def __init__(self, config: Config) -> None:
        self.dir = config.state_dir
        self.path = config.state_dir / "chat.json"
        self.allowed = {str(chat) for chat in config.chats}

    def __enter__(self) -> "ChatState":
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.handle = (self.dir / ".chat.lock").open("a+")
        fcntl.flock(self.handle, fcntl.LOCK_EX)
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        data = upgrade_state(data if isinstance(data, dict) else {})
        # a chat no longer configured (phone.chat_id or a project's group changed): its threads and messages do not exist here
        self.chats: dict[str, dict] = {chat: entry for chat, entry in data["chats"].items() if chat in self.allowed}
        self.topics: dict[str, dict] = {key: topic for key, topic in data["topics"].items() if str(topic.get("chat")) in self.allowed}
        self.messages: dict[str, dict] = {key: entry for key, entry in data["messages"].items() if key.split(":")[0] in self.allowed}
        # the user's message id on the hub -> the bot's status line under it, [chat, message id]
        self.receipts: dict[str, list] = {key: line for key, line in data["receipts"].items() if str(line[0]) in self.allowed}
        return self

    def __exit__(self, kind, *_exc) -> None:
        try:
            if kind is None:
                tmp = self.path.with_suffix(".tmp")
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as handle:
                    json.dump({"version": 2, "chats": self.chats, "topics": self.topics, "messages": self.messages,
                               "receipts": self.receipts}, handle)
                os.replace(tmp, self.path)
        finally:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()

    def remember(self, chat: int, message_id: object, context: dict) -> None:
        if message_id is None:
            return
        self.messages[f"{chat}:{message_id}"] = {key: context[key] for key in ("project", "root", "wave", "session", "question") if context.get(key)}
        for stale in list(self.messages)[:-MESSAGES_KEPT]:
            del self.messages[stale]

    def track(self, receipt: str, chat: int, message_id: object) -> None:
        if message_id is not None:
            self.receipts[receipt] = [chat, message_id]
        for stale in list(self.receipts)[:-MESSAGES_KEPT]:
            del self.receipts[stale]

    def topic_by_thread(self, chat: int, thread: int) -> dict | None:
        return next((topic for topic in self.topics.values() if topic.get("chat") == chat and topic.get("thread") == thread), None)


def upgrade_state(data: dict) -> dict:
    """The state as written since per-project groups; a one-chat state from before keeps its threads."""
    if data.get("version") == 2:
        return data
    chat = data.get("chat")
    if chat is None:
        return {"chats": {}, "topics": {}, "messages": {}, "receipts": {}}
    return {
        "chats": {str(chat): {"forum": data.get("forum")}},
        "topics": {key: {**topic, "chat": chat} for key, topic in (data.get("topics") or {}).items()},
        "messages": {f"{chat}:{key}": entry for key, entry in (data.get("messages") or {}).items()},
        "receipts": {key: [chat, line] for key, line in (data.get("receipts") or {}).items()},
    }


def wave_key(context: dict) -> str:
    return f"{context['root']}::{context['wave']}"


def project_of(context: dict) -> str:
    return context.get("project") or Path(context["root"]).name


def is_forum(config: Config, token: str, state: ChatState, chat: int) -> bool:
    """Whether the chat is a supergroup with topics; asked once per chat, then remembered."""
    entry = state.chats.setdefault(str(chat), {})
    if entry.get("forum") is None:
        found = telegram(token, "getChat", chat_id=chat)
        entry["forum"] = bool(isinstance(found, dict) and found.get("is_forum"))
    return entry["forum"]


def close_topic(token: str, topic: dict) -> None:
    """Close a finished wave's topic: it sinks under the live ones with a lock, history kept, and an admin
    (the user, the bot) can still write in it."""
    try:
        telegram(token, "closeForumTopic", chat_id=topic["chat"], message_thread_id=topic["thread"])
    except BridgeError as exc:
        if "NOT_MODIFIED" not in str(exc):  # already closed by hand
            print(f"phone-bridge: cannot close {topic.get('name')}: {exc}", file=sys.stderr)
    topic["closed"] = True


def wave_thread(config: Config, token: str, state: ChatState, context: dict) -> tuple[int, int | None]:
    """The chat and topic of the wave: its project's group if it has one, else the main chat. The topic
    is created on first use (and again in a group set up after the wave started, closing the old one);
    None in a chat without topics, where every wave shares the one thread and replies are routed by
    what they reply to."""
    project = project_of(context)
    chat = config.chat_for(project)
    if chat is None:
        raise BridgeError("phone.chat_id is not set yet: message the bot, then run `bridge.py chat-id`")
    if not is_forum(config, token, state, chat):
        return chat, None
    key = wave_key(context)
    topic = state.topics.get(key)
    if topic is None or topic.get("chat") != chat:
        if topic and not topic.get("closed"):
            close_topic(token, topic)
        # in the project's own group the wave alone names it; in the shared chat the project's color groups its waves
        name = (context["wave"] if chat != config.chat_id else f"{project} · {context['wave']}")[:128]
        created = telegram(token, "createForumTopic", chat_id=chat, name=name,
                           icon_color=TOPIC_COLORS[zlib.crc32(project.encode()) % len(TOPIC_COLORS)])
        topic = state.topics[key] = {"chat": chat, "thread": created["message_thread_id"], "name": name, "project": project,
                                     "root": context["root"], "wave": context["wave"], "card": None, "card_text": None,
                                     "done": False, "closed": False}
    return chat, topic["thread"]


def send_notice(config: Config, text: str, context: dict, token: str | None = None) -> None:
    """One notice: in its wave's thread (without the wave prefix) when it names a wave, remembered so
    a reply finds its agent; a notice that needs nothing from the user arrives silently."""
    token = token or read_token(config)
    with ChatState(config) as state:
        lines = [state.receipts[r] for r in context.get("receipts") or [] if r in state.receipts]
        if lines and context.get("body"):
            for chat, message_id in lines:  # a receipt edits the status line under the user's message
                try:
                    telegram(token, "editMessageText", chat_id=chat, message_id=message_id, text=context["body"][:4000])
                except BridgeError as exc:
                    if "not modified" not in str(exc):
                        raise
            return
        chat, thread = config.chat_id, None
        if context.get("wave") and context.get("root"):
            try:
                chat, thread = wave_thread(config, token, state, context)
            except BridgeError as exc:  # no topic rights: the main thread still gets it
                print(f"phone-bridge: no thread for {context['wave']}: {exc}", file=sys.stderr)
        try:
            message = send(config, (context.get("body") or text) if thread else text, token, thread, bool(context.get("silent")), chat=chat)
        except BridgeError as exc:
            if chat == config.chat_id:
                raise
            print(f"phone-bridge: {project_of(context)}'s group refused the notice, sent to the main chat: {exc}", file=sys.stderr)
            chat, message = config.chat_id, send(config, text, token, None, bool(context.get("silent")))
        if context.get("wave") and context.get("root"):
            state.remember(chat, message.get("message_id"), context)


def conversation(config: Config, message: dict) -> dict | None:
    """The wave (and agent session) a user's message is for: the message it replies to, else the topic
    it was written in; in a chat without topics, the wave that wrote there last. None in a general thread."""
    chat = (message.get("chat") or {}).get("id", config.chat_id)
    with ChatState(config) as state:
        replied = (message.get("reply_to_message") or {}).get("message_id")
        if replied is not None and f"{chat}:{replied}" in state.messages:
            return dict(state.messages[f"{chat}:{replied}"])
        if message.get("is_topic_message") and (topic := state.topic_by_thread(chat, message.get("message_thread_id"))):
            return {key: topic[key] for key in ("project", "root", "wave") if topic.get(key)}
        here = [entry for key, entry in state.messages.items() if key.split(":")[0] == str(chat)]
        if (state.chats.get(str(chat)) or {}).get("forum") or not here:
            return None
        return {key: here[-1][key] for key in ("project", "root", "wave") if here[-1].get(key)}


def supervisor(config: Config, root: str, *argv: str, timeout: float = 60) -> subprocess.CompletedProcess:
    if not SUPERVISOR.is_file():
        raise BridgeError(f"sweep-supervisor is not installed beside this skill ({SUPERVISOR})")
    try:
        return subprocess.run([sys.executable, str(SUPERVISOR), "--registry", str(config.registry), "--root", root, *argv],
                              capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BridgeError(f"cannot run the supervisor: {exc}") from None


def tell(config: Config, context: dict, text: str) -> str:
    """Hand the user's words to the wave's supervisor (its agent answers in the thread); returns the
    message's id on the hub, which every status line names."""
    argv = ["tell", "--wave", context["wave"], "--text", text]
    for flag, key in (("--session", "session"), ("--question", "question")):
        if context.get(key):
            argv += [flag, context[key]]
    result = supervisor(config, context["root"], *argv)
    if result.returncode != 0:
        raise BridgeError(f"the supervisor did not take it: {(result.stderr or result.stdout).strip()[-300:]}")
    match = re.search(r"\[tell\] (\S+):", result.stdout)
    return match.group(1) if match else "?"


@dataclass(frozen=True)
class Forwarded:
    """A message handed to a wave's agent, by its id on the hub."""

    receipt: str


def wave_card(config: Config, root: str, wave: str) -> tuple[str, bool] | None:
    """The wave's brief status as plain text and whether it has finished; None before its first cycle."""
    result = supervisor(config, root, "table", "--wave", wave)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    text = result.stdout.replace("**", "").replace("`", "").strip()
    return text, " — finished " in text.splitlines()[0]


def refresh_cards(config: Config, token: str) -> None:
    """Give every supervised wave a thread with a pinned status card, and keep the card current by
    editing it: the live state never costs a new message or a sound. A finished wave's topic is closed."""
    waves = supervisor(config, str(Path.home()), "waves")
    with ChatState(config) as state:
        for line in waves.stdout.splitlines() if waves.returncode == 0 else []:
            wave_id, _, root = line.partition("\t")
            topic = state.topics.get(f"{root}::{wave_id}")
            if root and not (topic and topic.get("done")):
                try:
                    wave_thread(config, token, state, {"root": root, "wave": wave_id, "project": Path(root).name})
                except BridgeError as exc:  # one group without topic rights leaves the others' cards current
                    print(f"phone-bridge: no thread for {wave_id}: {exc}", file=sys.stderr)
        for topic in state.topics.values():
            if topic.get("done"):
                if not topic.get("closed"):
                    close_topic(token, topic)
                continue
            card = wave_card(config, topic["root"], topic["wave"])
            if card is None:
                continue
            text, finished = card
            if topic.get("card") and text != topic.get("card_text"):
                try:
                    telegram(token, "editMessageText", chat_id=topic["chat"], message_id=topic["card"], text=text[:4000])
                except BridgeError as exc:
                    if "not modified" not in str(exc):
                        topic["card"] = None  # deleted or too old to edit: post a new one
            if not topic.get("card"):
                topic["card"] = send(config, text, token, topic["thread"], silent=True, chat=topic["chat"]).get("message_id")
                with contextlib.suppress(BridgeError):
                    telegram(token, "pinChatMessage", chat_id=topic["chat"], message_id=topic["card"], disable_notification="true")
                state.remember(topic["chat"], topic["card"], topic)
            topic.update(card_text=text, done=finished)
            if finished:
                close_topic(token, topic)


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
        raise BridgeError("that link is not the latest one (or was already used); ask for a new one")
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
                          f" (codes expire within about a minute; send /{login.command})")
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


def commands(config: Config) -> list[tuple[str, str]]:
    """The bot's command menu: one renewal command per rig, then /status."""
    return [(login.command, f"Login link to renew the {login.rig} SSH certificate") for login in config.logins] + [
        ("status", "This wave's status in its thread; elsewhere, when each SSH certificate expires")]


def help_text(config: Config) -> str:
    lines = [f"/{name} — {description}" for name, description in commands(config)]
    return "\n".join(lines + [
        "After logging in, paste the address the browser could not open (http://127.0.0.1:…).",
        "To talk to a wave's supervisor agent, write in the wave's thread or reply to one of its messages."])


def register_commands(config: Config, token: str) -> None:
    """Publish the menu, so typing / in the chat suggests the commands."""
    telegram(token, "setMyCommands", commands=json.dumps([{"command": c, "description": d} for c, d in commands(config)]))


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


def handle(config: Config, token: str, text: str, context: dict | None = None) -> str | Forwarded:
    """The reply to one message from the user, or `Forwarded` when it went to the wave's agent
    (`context`), which answers in the thread itself."""
    text = text.strip()
    word = text.split()[0].split("@")[0].lstrip("/") if text.startswith("/") else ""
    chosen = [login for login in config.logins if word == login.command or word == "renew"]  # plain /renew: every rig
    if chosen:
        return "\n\n".join(
            f"{login.rig}: log in here, then paste the address the browser cannot open:\n{start_renewal(config, login)}" for login in chosen)
    if word == "status":
        card = wave_card(config, context["root"], context["wave"]) if context else None
        return card[0] if card else "\n".join(status_lines(config))
    pasted = parse_redirect(text)
    if pasted:
        return finish_renewal(config, *pasted)
    if context and not word:
        return Forwarded(tell(config, context, text))
    return help_text(config)


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
    print(f"[serve] {Path(__file__).resolve()}: chat {config.chat_id}, {len(config.projects)} project group(s), "
          f"{len(config.logins)} certificate(s) watched", flush=True)
    try:
        register_commands(config, token)
    except BridgeError as exc:
        print(f"[serve] command menu: {exc}", file=sys.stderr, flush=True)
    offset, warned, last_check, last_cards = None, {}, 0.0, 0.0
    while True:
        maybe_self_update(config)
        if time.time() - last_check >= CERT_CHECK_EVERY_S:
            last_check = time.time()
            try:
                check_certs(config, token, warned)
            except BridgeError as exc:
                print(f"[serve] certificate check: {exc}", file=sys.stderr, flush=True)
        if time.time() - last_cards >= CARD_EVERY_S:
            last_cards = time.time()
            try:
                refresh_cards(config, token)
            except (BridgeError, OSError, KeyError) as exc:
                print(f"[serve] status cards: {exc}", file=sys.stderr, flush=True)
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
            if (message.get("chat") or {}).get("id") not in config.chats or not message.get("text") or (message.get("from") or {}).get("is_bot"):
                continue  # anyone else is ignored, silently
            thread = message.get("message_thread_id") if message.get("is_topic_message") else None
            try:
                respond(config, token, message, thread)
            except BridgeError as exc:
                print(f"[serve] reply: {exc}", file=sys.stderr, flush=True)


def respond(config: Config, token: str, message: dict, thread: int | None) -> None:
    """Answer one message in the thread it came from. A message passed to an agent gets a silent
    status line under it instead, which the supervisor edits as the agent takes it, answers or fails."""
    chat = (message.get("chat") or {}).get("id", config.chat_id)
    try:
        reply = handle(config, token, message["text"], conversation(config, message))
    except BridgeError as exc:
        reply = f"✗ {exc}"
    if isinstance(reply, str):
        send(config, reply, token, thread, reply_to=message.get("message_id"), chat=chat)
        return
    line = send(config, f"📨 received · waiting for the wave's agent · id {reply.receipt}", token, thread, silent=True,
                reply_to=message.get("message_id"), chat=chat)
    with ChatState(config) as state:
        state.track(reply.receipt, chat, line.get("message_id"))


def chat_ids(config: Config) -> int:
    """Print the chats that messaged the bot, so the user's own id can go into phone.chat_id."""
    for update in telegram(read_token(config), "getUpdates") or []:
        chat = (update.get("message") or {}).get("chat") or {}
        if chat:
            kind = chat.get("type", "") + (" with topics" if chat.get("is_forum") else "")
            print(f"{chat.get('id')}\t{kind}\t{chat.get('title') or chat.get('username') or chat.get('first_name') or ''}")
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
    send_p = sub.add_parser("send", help="push one notice to the phone (use as supervisor.notify_command)")
    send_p.add_argument("text")
    send_p.add_argument("--context", help="the supervisor's {context} JSON: puts the notice in its wave's thread")
    sub.add_parser("status", help="when each certificate expires")
    args = parser.parse_args(argv)
    if args.command == "send" and (newer := newer_installed_copy(Path(__file__))):
        # a notify_command written against an older install still runs the newest one
        os.execv(sys.executable, [sys.executable, str(newer), *(sys.argv[1:] if argv is None else argv)])
    registry = (args.registry or Path(os.environ.get("RIGSYNC_REGISTRY", "~/.config/rigsync/machines.toml")).expanduser()).resolve()
    try:
        config = load_config(registry)
        if args.command == "check":
            read_token(config)
            print(f"OK token file {config.token_file}")
            print(f"OK chat {config.chat_id}" if config.chat_id is not None else "TODO phone.chat_id: message the bot, then run chat-id")
            for project, chat in config.projects.items():
                print(f"OK {project}: its waves' topics in chat {chat}")
            for login in config.logins:
                print(f"OK {login.rig}: {login.identity} via {login.provisioner}, key {login.public_key}, cert {login.cert}")
            return 0 if config.chat_id is not None else 1
        if args.command == "serve":
            return serve(config)
        if args.command == "chat-id":
            return chat_ids(config)
        if args.command == "send":
            if args.context:
                try:
                    context = json.loads(args.context)
                except ValueError as exc:
                    raise BridgeError(f"--context is not JSON: {exc}") from None
                send_notice(config, args.text, context if isinstance(context, dict) else {})
            else:
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
