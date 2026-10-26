#!/usr/bin/env python3
"""service-hub: one port in front of every web service a machine hosts.

`/` is a landing page with one card per service (live up/down, latency). Each proxied service
answers under its own path prefix, http://<host>:<port>/<service>/..., forwarded to a backend
that listens on loopback only. Link-only services are cards that open their own URL. The hub
checks one access token (query once, then a cookie) before anything reaches a backend, so a
backend that trusts loopback clients is never exposed by the proxy. Configuration is the [hub]
table of the machines registry; see references/configuration.md.

Subcommands:
  serve   run the gateway (the service runs this)
  status  probe every service and print the table the landing page shows
  url     print the hub URL of a service, optionally with a path below it
  token   print a fresh access token
"""

from __future__ import annotations

import argparse
import hmac
import http.client
import ipaddress
import json
import os
import re
import secrets
import select
import socket
import sys
import threading
import time
import tomllib
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlparse

DEFAULT_REGISTRY = Path("~/.config/rigsync/machines.toml").expanduser()
DEFAULT_PORT = 40970
DEFAULT_BIND = "127.0.0.1"
DEFAULT_HEALTH = "/healthz"
COOKIE_NAME = "service_hub_token"
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
RESERVED = {"api", "healthz", "hub", "favicon.ico", "index.html", "login"}
HEALTH_TTL_S = 5.0
HEALTH_TIMEOUT_S = 2.0
CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 300.0
CHUNK = 64 * 1024
# Hop-by-hop headers are never forwarded (RFC 9110 §7.6.1).
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "transfer-encoding", "upgrade", "proxy-connection",
}
ICON_TYPES = {".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".webp": "image/webp"}
PAGE_PATH = Path(__file__).resolve().parents[1] / "assets" / "hub.html"
NAV_PATH = PAGE_PATH.with_name("hub-nav.js")
NAV_ROUTE = "/hub/nav.js"
# The back-to-hub button goes into proxied pages only up to this size: a page is buffered to insert it.
INJECT_MAX_BYTES = 4 * 1024 * 1024
BODY_END = re.compile(rb"</body\s*>", re.IGNORECASE)


def inject_nav(body: bytes, slug: str, icon: str = "") -> bytes:
    """Insert the hub's navigation script before the last </body> (or at the end).

    The service's icon rides along so the script can make it the tab icon without a round trip.
    """
    icon_attr = f' data-icon="{html_escape(icon)}"' if icon else ""
    tag = f'<script src="{NAV_ROUTE}" data-service="{slug}"{icon_attr} defer></script>'.encode()
    matches = list(BODY_END.finditer(body))
    at = matches[-1].start() if matches else len(body)
    return body[:at] + tag + body[at:]


def gunzip_bounded(data: bytes, limit: int) -> bytes | None:
    """Decompress a gzip body, or None when it is corrupt or larger than limit."""
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = inflater.decompress(data, limit)
    except zlib.error:
        return None
    return None if inflater.unconsumed_tail or not inflater.eof else out


class HubError(Exception):
    pass


@dataclass(frozen=True)
class Service:
    slug: str
    name: str
    description: str
    icon: str
    url: str | None      # backend base URL (proxied under /<slug>/)
    link: str | None     # external URL (link-only card)
    health: str | None   # path probed on the backend; None disables the probe
    nav: bool = True     # inject the back-to-hub button into this service's pages


@dataclass(frozen=True)
class HubConfig:
    port: int
    bind: str
    token: str | None
    title: str
    public_url: str | None
    services: tuple[Service, ...]
    icon: Path | None = None  # image file served as the favicon and the page mark

    def service(self, slug: str) -> Service | None:
        return next((s for s in self.services if s.slug == slug), None)


def registry_path(arg: str | None) -> Path:
    return Path(arg or os.environ.get("RIGSYNC_REGISTRY") or DEFAULT_REGISTRY).expanduser()


def load_config(path: Path) -> HubConfig:
    try:
        with path.open("rb") as handle:
            registry = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise HubError(f"missing registry: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise HubError(f"invalid TOML in {path}: {exc}") from exc
    table = registry.get("hub")
    if not isinstance(table, dict):
        raise HubError(f"{path}: missing [hub] table")
    port = table.get("port", DEFAULT_PORT)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise HubError(f"{path}: hub.port must be an integer in 1..65535")
    bind = table.get("bind", DEFAULT_BIND)
    if not isinstance(bind, str) or not bind:
        raise HubError(f"{path}: hub.bind must be an address")
    token = table.get("token")
    if token is not None and (not isinstance(token, str) or len(token) < 16):
        raise HubError(f"{path}: hub.token must be a string of at least 16 characters")
    title = table.get("title", socket.gethostname())
    if not isinstance(title, str) or not title:
        raise HubError(f"{path}: hub.title must be a non-empty string")
    public_url = table.get("public_url")
    if public_url is not None and (not isinstance(public_url, str) or not public_url.startswith(("http://", "https://"))):
        raise HubError(f"{path}: hub.public_url must start with http:// or https://")
    icon = table.get("icon")
    if icon is not None:
        if not isinstance(icon, str) or Path(icon).expanduser().suffix.lower() not in ICON_TYPES:
            raise HubError(f"{path}: hub.icon must be the path of a {'/'.join(ICON_TYPES)} file")
        icon = Path(icon).expanduser()
        if not icon.is_file():
            raise HubError(f"{path}: hub.icon {str(icon)!r} does not exist")
    raw = table.get("services", {})
    if not isinstance(raw, dict):
        raise HubError(f"{path}: hub.services must be a table of [hub.services.<name>] tables")
    services = []
    for slug, entry in raw.items():
        where = f"{path}: hub.services.{slug}"
        if not SLUG.match(slug) or slug in RESERVED:
            raise HubError(f"{where}: the name must be lowercase letters, digits and '-' (not {sorted(RESERVED)})")
        if not isinstance(entry, dict):
            raise HubError(f"{where} must be a table")
        url, link = entry.get("url"), entry.get("link")
        if (url is None) == (link is None):
            raise HubError(f"{where}: set exactly one of url (proxied backend) or link (external page)")
        if url is not None:
            parsed = urlparse(url) if isinstance(url, str) else None
            if parsed is None or parsed.scheme != "http" or not parsed.hostname or parsed.path not in ("", "/") or parsed.query:
                raise HubError(f"{where}: url must be a plain http://host:port backend, e.g. http://127.0.0.1:40971")
            url = url.rstrip("/")
        if link is not None and (not isinstance(link, str) or not link.startswith(("http://", "https://"))):
            raise HubError(f"{where}: link must start with http:// or https://")
        health = entry.get("health", DEFAULT_HEALTH if url else None)
        if health is not None and (not isinstance(health, str) or (health and not health.startswith("/"))):
            raise HubError(f"{where}: health must be a path starting with / (\"\" disables the probe)")
        texts = {k: entry.get(k, d) for k, d in (("name", slug), ("description", ""), ("icon", ""))}
        for key, value in texts.items():
            if not isinstance(value, str):
                raise HubError(f"{where}: {key} must be a string")
        nav = entry.get("nav", True)
        if not isinstance(nav, bool):
            raise HubError(f"{where}: nav must be true or false")
        services.append(Service(slug, texts["name"], texts["description"], texts["icon"], url, link, health or None, nav))
    if not is_loopback(bind) and token is None:
        raise HubError(f"{path}: refusing hub.bind = {bind!r} without hub.token; set one (hub.py token) or bind 127.0.0.1")
    return HubConfig(port, bind, token, title, public_url.rstrip("/") if public_url else None, tuple(services), icon)


def is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).is_loopback
    except ValueError:
        return address == "localhost"


# ---------------------------------------------------------------------------- health


def probe(service: Service) -> dict:
    """Up/down for one service. Any HTTP answer below 500 is up (401 means it is alive and gated)."""
    out: dict = {"status": "unknown", "latency_ms": None, "detail": None}
    start = time.monotonic()
    try:
        if service.url:
            if service.health is None:
                return out
            target = urlparse(service.url)
            conn = http.client.HTTPConnection(target.hostname, target.port or 80, timeout=HEALTH_TIMEOUT_S)
            try:
                conn.request("GET", service.health, headers={"User-Agent": "service-hub"})
                code = conn.getresponse().status
            finally:
                conn.close()
            out["status"] = "up" if code < 500 else "down"
            out["detail"] = f"HTTP {code}"
        else:
            target = urlparse(service.link)
            port = target.port or (443 if target.scheme == "https" else 80)
            with socket.create_connection((target.hostname, port), timeout=HEALTH_TIMEOUT_S):
                pass
            out["status"] = "up"
            out["detail"] = "port open"
    except (OSError, http.client.HTTPException) as exc:
        out["status"] = "down"
        out["detail"] = str(exc) or exc.__class__.__name__
        return out
    out["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
    return out


class Health:
    """Probes every service at most once per HEALTH_TTL_S, concurrently."""

    def __init__(self, config: HubConfig) -> None:
        self.config = config
        self.lock = threading.Lock()
        self.at = 0.0
        self.results: dict[str, dict] = {}

    def snapshot(self) -> dict[str, dict]:
        with self.lock:
            if time.monotonic() - self.at > HEALTH_TTL_S or not self.results:
                services = self.config.services
                with ThreadPoolExecutor(max_workers=max(1, min(16, len(services)))) as pool:
                    self.results = dict(zip((s.slug for s in services), pool.map(probe, services)))
                self.at = time.monotonic()
            return dict(self.results)


def services_json(config: HubConfig, health: Health) -> dict:
    results = health.snapshot()
    return {
        "api_version": 1,
        "title": config.title,
        "icon": config.icon is not None,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "services": [
            {
                "slug": s.slug, "name": s.name, "description": s.description, "icon": s.icon,
                "kind": "proxy" if s.url else "link",
                "href": f"{s.slug}/" if s.url else s.link,
                **results.get(s.slug, {}),
            }
            for s in config.services
        ],
    }


# ---------------------------------------------------------------------------- auth


def presented_token(handler: BaseHTTPRequestHandler, query: str) -> tuple[str | None, bool]:
    """(token the client presented, whether it came from the query string)."""
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key == "token":
            return value, True
    header = handler.headers.get("X-Hub-Token")
    if header:
        return header, False
    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip(), False
    cookie = SimpleCookie()
    try:
        cookie.load(handler.headers.get("Cookie", ""))
    except Exception:  # a malformed cookie header from some other app on this host
        return None, False
    if COOKIE_NAME in cookie:
        return cookie[COOKIE_NAME].value, False
    return None, False


def strip_token(query: str) -> str:
    return urlencode([(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k != "token"])


def strip_hub_cookie(raw: str) -> str:
    parts = [p.strip() for p in raw.split(";") if p.strip()]
    return "; ".join(p for p in parts if not p.startswith(COOKIE_NAME + "="))


LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · sign in</title>{icon_link}
<style>:root{{color-scheme:light dark;--bg:#f6f7f9;--card:#fff;--line:#e3e5e8;--text:#15171a;--muted:#6b7280;--accent:#2563eb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1115;--card:#171a20;--line:#2a2f37;--text:#e8eaed;--muted:#9aa1ab;--accent:#5b8dff}}}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding:16px;box-sizing:border-box}}
form{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:26px;width:100%;max-width:360px;box-shadow:0 8px 30px rgba(0,0,0,.06)}}
h1{{font-size:18px;margin:0 0 4px}}p{{margin:0 0 18px;color:var(--muted);font-size:13px}}
input{{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:transparent;color:inherit;font:inherit;margin-bottom:12px}}
button{{width:100%;padding:10px;border:0;border-radius:8px;background:var(--accent);color:#fff;font:inherit;font-weight:600;cursor:pointer}}
.err{{color:#dc2626;font-size:13px;margin:-6px 0 12px}}.logo{{width:48px;height:48px;display:block;margin-bottom:12px}}</style></head>
<body><form method="get" action="{action}">{icon_img}<h1>{title}</h1><p>Paste the hub access token. This browser remembers it for a year.</p>
{error}<input name="token" type="password" autocomplete="current-password" placeholder="access token" autofocus required>
<button type="submit">Open</button></form></body></html>"""


def icon_href(config: HubConfig, root: str = "") -> str | None:
    """The favicon URL with the file's mtime as a version, so a browser never keeps an old icon (or an old 404)."""
    if config.icon is None:
        return None
    try:
        version = int(config.icon.stat().st_mtime)
    except OSError:
        version = 0
    return f"{root}favicon.ico?v={version}"


def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ---------------------------------------------------------------------------- proxy


def make_handler(config: HubConfig, health: Health, access_log=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "service-hub"
        sys_version = ""
        auth = "-"

        def log_message(self, *args: object) -> None:
            pass

        def log_request(self, code: object = "-", size: object = "-") -> None:
            if access_log is None:
                return
            path = urlparse(self.path).path if isinstance(self.path, str) else "-"  # never the query: it can carry the token
            access_log(f"{self.client_address[0]} {self.command} {path} {getattr(code, 'value', code)} auth={self.auth}")

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch()

        do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_GET  # noqa: N815

        def _dispatch(self) -> None:
            url = urlparse(self.path)
            if url.path == "/healthz":
                self._send(200, "text/plain; charset=utf-8", b"ok\n")
                return
            if url.path == "/favicon.ico":  # public, so the sign-in page and the tab show it too
                self._icon()
                return
            if not self._authorized(url):
                return
            parts = url.path.split("/", 2)  # "", first segment, rest
            first = parts[1] if len(parts) > 1 else ""
            if url.path in ("/", "/index.html"):
                self._page()
            elif url.path == NAV_ROUTE:
                try:
                    self._send(200, "text/javascript; charset=utf-8", NAV_PATH.read_bytes(), cache="no-cache")
                except OSError:
                    self._send(404, "text/plain; charset=utf-8", b"not found\n")
            elif url.path == "/api/services":
                self._send(200, "application/json; charset=utf-8", json.dumps(services_json(config, health)).encode())
            elif (service := config.service(first)) is not None and service.url:
                if len(parts) == 2:  # /board -> /board/ so the page's relative URLs resolve under it
                    self._redirect(f"/{first}/" + (f"?{url.query}" if url.query else ""))
                else:
                    self._proxy(service, "/" + parts[2], url.query)
            elif service is not None:
                self._redirect(service.link)
            else:
                self._send(404, "text/plain; charset=utf-8", f"service-hub: no service named {first!r}\n".encode())

        def _authorized(self, url) -> bool:
            if is_loopback(self.client_address[0]):
                self.auth = "loopback"
                return True
            if config.token is None:
                self.auth = "open"
                return True
            token, from_query = presented_token(self, url.query)
            if token is None or not hmac.compare_digest(token.encode(), config.token.encode()):
                self.auth = "missing" if token is None else "REJECTED"
                wants_html = "text/html" in self.headers.get("Accept", "") and self.command in ("GET", "HEAD")
                if wants_html:
                    error = '<div class="err">That token is not valid.</div>' if token is not None else ""
                    href = icon_href(config, "/")
                    body = LOGIN_PAGE.format(
                        title=html_escape(config.title), action=html_escape(url.path or "/"), error=error,
                        icon_link=f'<link rel="icon" href="{href}">' if href else "",
                        icon_img=f'<img class="logo" src="{href}" alt="">' if href else "",
                    )
                    self._send(401, "text/html; charset=utf-8", body.encode())
                else:
                    self._send(401, "text/plain; charset=utf-8", b"service-hub: access token required (?token=, Authorization: Bearer, or the hub cookie)\n")
                return False
            self.auth = "token"
            if from_query and self.command in ("GET", "HEAD"):
                # Remember the token and take it out of the address bar, history and backend logs.
                rest = strip_token(url.query)
                self._redirect(url.path + (f"?{rest}" if rest else ""),
                               cookie=f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000")
                return False
            return True

        def _page(self) -> None:
            try:
                href = icon_href(config)
                body = (
                    PAGE_PATH.read_text(encoding="utf-8")
                    .replace("__HUB_TITLE__", html_escape(config.title))
                    .replace("__HUB_ICON__", href or "data:,")
                    .replace("__HUB_MARK_CLASS__", "mark img" if href else "mark")
                    .replace("__HUB_MARK__", f'<img src="{href}" alt="">' if href else "·")
                    .encode()
                )
            except OSError:
                body = b"service-hub: assets/hub.html is missing; /api/services still answers\n"
                self._send(200, "text/plain; charset=utf-8", body)
                return
            self._send(200, "text/html; charset=utf-8", body)

        def _forward_headers(self, service: Service, backend) -> list[tuple[str, str]]:
            connection_tokens = {t.strip().lower() for t in self.headers.get("Connection", "").split(",")}
            out = []
            for key, value in self.headers.items():
                low = key.lower()
                if low in HOP_BY_HOP or low in connection_tokens or low in ("host", "x-hub-token"):
                    continue
                if low == "authorization" and config.token and value == f"Bearer {config.token}":
                    continue
                if low == "cookie":
                    value = strip_hub_cookie(value)
                    if not value:
                        continue
                out.append((key, value))
            out += [
                ("Host", backend.netloc),
                ("X-Forwarded-For", self.client_address[0]),
                ("X-Forwarded-Host", self.headers.get("Host", "")),
                ("X-Forwarded-Proto", "http"),
                ("X-Forwarded-Prefix", f"/{service.slug}"),
            ]
            return out

        def _rewrite_location(self, service: Service, value: str) -> str:
            if value.startswith(service.url):
                value = value[len(service.url):] or "/"
            if value.startswith("/") and not value.startswith("//"):
                return f"/{service.slug}{value}"
            return value

        def _proxy(self, service: Service, path: str, query: str) -> None:
            backend = urlparse(service.url)
            target = quote(path, safe="/%:@!$&'()*+,;=~-._") + (f"?{query}" if query else "")
            headers = self._forward_headers(service, backend)
            if self.headers.get("Upgrade"):
                self._tunnel(service, backend, target, headers)
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length > 0 else None
            conn = http.client.HTTPConnection(backend.hostname, backend.port or 80, timeout=CONNECT_TIMEOUT_S)
            try:
                conn.connect()
                conn.sock.settimeout(READ_TIMEOUT_S)
                conn.putrequest(self.command, target, skip_host=True, skip_accept_encoding=True)
                for key, value in headers:
                    conn.putheader(key, value)
                if body is not None:
                    conn.putheader("Content-Length", str(len(body)))
                conn.endheaders(body)
                response = conn.getresponse()
            except (OSError, http.client.HTTPException) as exc:
                conn.close()
                self._send(502, "text/plain; charset=utf-8", f"service-hub: {service.name} is not answering ({exc})\n".encode())
                return
            try:
                if self._wants_nav(service) and self._send_with_nav(service, response):
                    return
                self.send_response(response.status, response.reason)
                for key, value in response.getheaders():
                    low = key.lower()
                    if low in HOP_BY_HOP or low == "content-length" and response.chunked:
                        continue
                    if low == "location":
                        value = self._rewrite_location(service, value)
                    elif low == "set-cookie":
                        value = re.sub(r"(?i)(;\s*path=)/(?=;|$)", rf"\1/{service.slug}/", value)
                    self.send_header(key, value)
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD":
                    while chunk := response.read(CHUNK):
                        self.wfile.write(chunk)
            except (OSError, http.client.HTTPException):
                pass  # the browser went away or the backend broke mid-stream
            finally:
                conn.close()
                self.close_connection = True

        def _wants_nav(self, service: Service) -> bool:
            """Only top-level page loads get the button, never a frame, a fetch or an asset."""
            if not service.nav or self.command != "GET":
                return False
            dest = self.headers.get("Sec-Fetch-Dest")
            return dest == "document" if dest else "text/html" in self.headers.get("Accept", "")

        def _send_with_nav(self, service: Service, response) -> bool:
            """Send an HTML page with the hub button added; False (nothing sent, body unread) when it does not apply."""
            ctype = response.getheader("Content-Type", "")
            encoding = (response.getheader("Content-Encoding") or "identity").lower()
            length = response.getheader("Content-Length")
            if response.status != 200 or not ctype.startswith("text/html") or encoding not in ("identity", "gzip"):
                return False
            if length is None or not length.isdigit() or int(length) > INJECT_MAX_BYTES:
                return False
            raw = response.read()
            page = raw if encoding == "identity" else gunzip_bounded(raw, INJECT_MAX_BYTES)
            body = inject_nav(page, service.slug, service.icon) if page is not None else raw
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                low = key.lower()
                if low in HOP_BY_HOP or low == "content-length" or (page is not None and low == "content-encoding"):
                    continue
                if low == "set-cookie":
                    value = re.sub(r"(?i)(;\s*path=)/(?=;|$)", rf"\1/{service.slug}/", value)
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            return True

        def _tunnel(self, service: Service, backend, target: str, headers: list[tuple[str, str]]) -> None:
            """WebSocket and other Upgrade requests: replay the handshake, then pipe bytes both ways."""
            try:
                upstream = socket.create_connection((backend.hostname, backend.port or 80), timeout=CONNECT_TIMEOUT_S)
            except OSError as exc:
                self._send(502, "text/plain; charset=utf-8", f"service-hub: {service.name} is not answering ({exc})\n".encode())
                return
            lines = [f"{self.command} {target} HTTP/1.1"] + [f"{k}: {v}" for k, v in headers]
            lines += ["Connection: Upgrade", f"Upgrade: {self.headers['Upgrade']}"]
            upstream.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
            client = self.connection
            upstream.settimeout(None)
            self.close_connection = True
            try:
                while True:
                    ready, _, _ = select.select([client, upstream], [], [], READ_TIMEOUT_S)
                    if not ready:
                        break
                    for sock in ready:
                        data = sock.recv(CHUNK)
                        if not data:
                            return
                        (upstream if sock is client else client).sendall(data)
            except OSError:
                pass
            finally:
                upstream.close()

        def _redirect(self, location: str, cookie: str | None = None) -> None:
            self.send_response(303 if cookie else 302)  # never 301: a browser would cache it past a registry change
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()

        def _icon(self) -> None:
            try:
                body = config.icon.read_bytes() if config.icon else None
            except OSError:
                body = None
            if body is None:
                self._send(404, "text/plain; charset=utf-8", b"no icon\n")
                return
            self._send(200, ICON_TYPES[config.icon.suffix.lower()], body, cache="public, max-age=3600")

        def _send(self, code: int, ctype: str, body: bytes, cache: str = "no-store") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

    return Handler


# ---------------------------------------------------------------------------- commands


def cmd_serve(config: HubConfig, args: argparse.Namespace) -> int:
    bind = args.bind or config.bind
    if not is_loopback(bind) and config.token is None:
        raise HubError(f"refusing to listen on {bind} without hub.token")
    health = Health(config)
    log = (lambda line: print(line, file=sys.stderr, flush=True)) if not args.quiet else None
    server = ThreadingHTTPServer((bind, args.port or config.port), make_handler(config, health, log))
    server.daemon_threads = True
    guard = "token required off this machine" if config.token else "OPEN (loopback only)"
    names = ", ".join(f"/{s.slug}/" if s.url else f"{s.slug} (link)" for s in config.services) or "no services yet"
    print(f"[serve] service-hub on http://{server.server_address[0]}:{server.server_address[1]}  ({guard})  {names}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_status(config: HubConfig, args: argparse.Namespace) -> int:
    data = services_json(config, Health(config))
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"{config.title}: {len(data['services'])} services")
    for s in data["services"]:
        where = f"/{s['slug']}/ -> {config.service(s['slug']).url}" if s["kind"] == "proxy" else s["href"]
        latency = f"{s['latency_ms']} ms" if s.get("latency_ms") is not None else ""
        print(f"  {s['status']:<7} {s['name']:<24} {where}  {latency}  {s.get('detail') or ''}".rstrip())
    return 0 if all(s["status"] != "down" for s in data["services"]) else 1


def cmd_url(config: HubConfig, args: argparse.Namespace) -> int:
    service = config.service(args.service)
    if service is None:
        raise HubError(f"no service named {args.service!r}; known: {', '.join(s.slug for s in config.services)}")
    if not service.url:
        print(service.link)
        return 0
    route = f"/{service.slug}/" + args.path.lstrip("/")
    print(f"local: http://127.0.0.1:{config.port}{route}")
    if config.public_url:
        print(f"public: {config.public_url}{route}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry", help="machines registry (default: $RIGSYNC_REGISTRY or ~/.config/rigsync/machines.toml)")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the gateway")
    serve.add_argument("--port", type=int)
    serve.add_argument("--bind")
    serve.add_argument("--quiet", action="store_true", help="no access log")
    status = sub.add_parser("status", help="probe every service")
    status.add_argument("--json", action="store_true")
    url = sub.add_parser("url", help="hub URL of a service")
    url.add_argument("service")
    url.add_argument("path", nargs="?", default="")
    sub.add_parser("token", help="print a fresh access token")
    args = parser.parse_args(argv)
    if args.command == "token":
        print(secrets.token_urlsafe(32))
        return 0
    try:
        config = load_config(registry_path(args.registry))
        return {"serve": cmd_serve, "status": cmd_status, "url": cmd_url}[args.command](config, args)
    except HubError as exc:
        print(f"service-hub: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
