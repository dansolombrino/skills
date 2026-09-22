#!/usr/bin/env python3
"""Read-only plot server: every project's plots/ over HTTP, one service per machine.

A plot is served at the URL path equal to its absolute file path, so the URL of any file is
known without asking the server: http://<host>:<port>/abs/path/to/<project>/plots/<...>.html.
Only files inside a project's plots/ tree under a configured root are served; everything else
answers 404. Configuration is the [plots] table of the rigsync registry; see
references/plot-server.md.

Subcommands:
  serve          run the server (the service runs this); / is the plot browser page
  url            print the URL of one plot file and whether the server serves it
  list           list the discovered projects and their HTML plots
  token          print a fresh access token
  sync           (root, sandboxed install) publish each project's plots/ into the sandbox
  system-config  (root, sandboxed install) write the system config from a user's [plots] table
"""

from __future__ import annotations

import argparse
import dataclasses
import hmac
import html
import ipaddress
import json
import mimetypes
import re
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import datetime
from email.utils import formatdate
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

DEFAULT_REGISTRY = "~/.config/rigsync/machines.toml"
SYSTEM_CONFIG = "/etc/plot-server/plot-server.toml"
SYSTEM_MANIFEST = "/etc/plot-server/projects.txt"
SYSTEM_DROPIN = "/etc/systemd/system/plot-server.service.d/plots.conf"
# Paths the sync job will write into a unit file: no spaces, quotes, colons or % specifiers.
UNIT_SAFE_PATH = re.compile(r"^/[A-Za-z0-9_./=+,@-]+$")
DEFAULT_PORT = 40975
DEFAULT_BIND = "127.0.0.1"
DEFAULT_DEPTH = 4
PLOTS_DIR = "plots"
PLOT_SUFFIXES = (".html", ".htm")
DISCOVERY_TTL_S = 30.0
COOKIE_NAME = "plot_server_token"
GZIP_MIN_BYTES = 64 * 1024
GZIP_TYPES = ("text/", "application/json", "application/javascript", "image/svg+xml")
BROWSER_PATH = Path(__file__).resolve().parents[1] / "assets" / "plot-browser.html"
DENIED = b"plot-server: access token required. Open any page once as ?token=<token>; a cookie then remembers it.\n"


class PlotServerError(Exception):
    pass


@dataclass(frozen=True)
class PlotsConfig:
    roots: tuple[Path, ...]
    port: int
    bind: str
    token: str | None
    depth: int
    public_url: str | None
    manifest: Path | None = None


def load_config(registry_path: Path) -> PlotsConfig:
    try:
        with registry_path.open("rb") as handle:
            registry = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise PlotServerError(f"missing registry: {registry_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PlotServerError(f"invalid TOML in {registry_path}: {exc}") from exc
    table = registry.get("plots")
    if not isinstance(table, dict):
        raise PlotServerError(f"{registry_path}: missing [plots] table (roots)")
    raw_roots = table.get("roots")
    if not isinstance(raw_roots, list) or not raw_roots or not all(isinstance(r, str) and r for r in raw_roots):
        raise PlotServerError(f"{registry_path}: plots.roots must be a non-empty list of absolute paths")
    roots = []
    for raw in raw_roots:
        root = Path(raw).expanduser()
        if not root.is_absolute():
            raise PlotServerError(f"{registry_path}: plots.roots entry {raw!r} is not absolute")
        roots.append(Path(os.path.normpath(root)))
    port = table.get("port", DEFAULT_PORT)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise PlotServerError(f"{registry_path}: plots.port must be an integer in 1..65535")
    bind = table.get("bind", DEFAULT_BIND)
    if not isinstance(bind, str) or not bind:
        raise PlotServerError(f"{registry_path}: plots.bind must be an address")
    token = table.get("token")
    if token is not None and (not isinstance(token, str) or len(token) < 16):
        raise PlotServerError(f"{registry_path}: plots.token must be a string of at least 16 characters")
    depth = table.get("depth", DEFAULT_DEPTH)
    if not isinstance(depth, int) or depth < 1:
        raise PlotServerError(f"{registry_path}: plots.depth must be a positive integer")
    public_url = table.get("public_url")
    if public_url is not None and (not isinstance(public_url, str) or not public_url.startswith(("http://", "https://"))):
        raise PlotServerError(f"{registry_path}: plots.public_url must start with http:// or https://")
    manifest = table.get("manifest")
    if manifest is not None and (not isinstance(manifest, str) or not Path(manifest).is_absolute()):
        raise PlotServerError(f"{registry_path}: plots.manifest must be an absolute path")
    return PlotsConfig(
        tuple(roots), port, bind, token, depth, public_url.rstrip("/") if public_url else None,
        Path(manifest) if manifest else None,
    )


def read_manifest(config: PlotsConfig) -> set[Path]:
    """Projects the sync job published into the sandbox (the sandbox hides .git, so no discovery)."""
    assert config.manifest is not None
    try:
        lines = config.manifest.read_text().splitlines()
    except OSError:
        return set()
    projects = set()
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            path = Path(os.path.normpath(line))
            if path.is_absolute() and any(path.is_relative_to(r) for r in config.roots):
                projects.add(path)
    return projects


def owns_plots(config: PlotsConfig, project: Path) -> bool:
    if config.manifest is not None:
        return project in read_manifest(config) and (project / PLOTS_DIR).is_dir()
    return is_project(project)


def is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).is_loopback
    except ValueError:
        return address == "localhost"


def require_safe_exposure(config: PlotsConfig, bind: str) -> None:
    """A server reachable beyond this machine must carry a token."""
    if not is_loopback(bind) and config.token is None:
        raise PlotServerError(
            f"refusing to listen on {bind} without plots.token; set one (plot_server.py token) or bind 127.0.0.1"
        )


def is_project(directory: Path) -> bool:
    return (directory / ".git").exists() and (directory / PLOTS_DIR).is_dir()


def resolve_plot(config: PlotsConfig, url_path: str) -> Path | None:
    """The file a URL path names, or None unless it lies inside a project's plots/ under a root."""
    decoded = unquote(url_path)
    if "\x00" in decoded or not decoded.startswith("/"):
        return None
    lexical = Path(os.path.normpath(decoded))
    if any(part == ".." for part in Path(decoded).parts):
        return None
    root = next((r for r in config.roots if lexical.is_relative_to(r)), None)
    if root is None:
        return None
    # The nearest ancestor named plots/ whose parent is a project owns the file.
    for ancestor in lexical.parents:
        if ancestor == root or not ancestor.is_relative_to(root):
            return None
        if ancestor.name == PLOTS_DIR and owns_plots(config, ancestor.parent):
            break
    else:
        return None
    if len(ancestor.parent.relative_to(root).parts) > config.depth:
        return None
    try:
        real = lexical.resolve(strict=True)
        real_plots = ancestor.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    # plots/ itself may be a symlink to another volume; the file must stay inside wherever it points.
    if not real.is_relative_to(real_plots) or not real.is_file():
        return None
    return lexical


def discover_projects(config: PlotsConfig) -> list[Path]:
    if config.manifest is not None:
        return sorted(p for p in read_manifest(config) if (p / PLOTS_DIR).is_dir())
    projects: list[Path] = []
    for root in config.roots:
        if not root.is_dir():
            continue
        for current, dirs, _files in os.walk(root):
            here = Path(current)
            level = len(here.relative_to(root).parts)
            if here != root and (here / ".git").exists():
                if (here / PLOTS_DIR).is_dir():
                    projects.append(here)
                dirs[:] = []  # a checkout's own subfolders are not separate projects
                continue
            if level >= config.depth:
                dirs[:] = []
                continue
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
    return sorted(set(projects))


def project_plots(project: Path) -> list[tuple[Path, os.stat_result]]:
    plots = project / PLOTS_DIR
    try:
        real_plots = plots.resolve(strict=True)
    except (OSError, RuntimeError):
        return []
    found = []
    for current, dirs, files in os.walk(real_plots):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.lower().endswith(PLOT_SUFFIXES):
                real = Path(current) / name
                try:
                    stat = real.stat()
                except OSError:
                    continue
                found.append((plots / real.relative_to(real_plots), stat))
    found.sort(key=lambda item: item[1].st_mtime, reverse=True)
    return found


class Catalog:
    """Project discovery, refreshed at most every DISCOVERY_TTL_S seconds."""

    def __init__(self, config: PlotsConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._at = 0.0
        self._projects: list[Path] = []

    def projects(self) -> list[Path]:
        with self._lock:
            if time.monotonic() - self._at > DISCOVERY_TTL_S:
                self._projects = discover_projects(self.config)
                self._at = time.monotonic()
            return list(self._projects)


def human_size(size: int) -> str:
    return f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.0f} KB"


def index_page(config: PlotsConfig, projects: list[Path]) -> bytes:
    sections = []
    for project in projects:
        root = next(r for r in config.roots if project.is_relative_to(r))
        rows = []
        for path, stat in project_plots(project):
            when = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            rel = path.relative_to(project / PLOTS_DIR).as_posix()
            rows.append(
                f'<tr><td><a href="{html.escape(quote(path.as_posix()))}">{html.escape(rel)}</a></td>'
                f"<td>{when}</td><td class=n>{human_size(stat.st_size)}</td></tr>"
            )
        body = "".join(rows) or '<tr><td colspan=3 class=empty>no HTML plots yet</td></tr>'
        sections.append(
            f"<section><h2>{html.escape(project.relative_to(root).as_posix())}</h2>"
            f"<p class=path>{html.escape(project.as_posix())}</p>"
            f"<table><tr><th>plot</th><th>rendered</th><th class=n>size</th></tr>{body}</table></section>"
        )
    if not sections:
        roots = ", ".join(html.escape(r.as_posix()) for r in config.roots)
        sections.append(f"<p class=empty>No project with a plots/ folder under {roots}.</p>")
    page = (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'><title>Plots</title><style>"
        "body{background:#fff;color:#1a1a1a;font:14px/1.45 system-ui,sans-serif;margin:24px auto;max-width:1100px;padding:0 16px}"
        "h1{font-size:20px}h2{font-size:16px;margin:28px 0 2px}.path{color:#666;font-size:12px;margin:0 0 8px;word-break:break-all}"
        "table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:4px 8px;border-bottom:1px solid #e5e5e5;vertical-align:top}"
        "td:first-child{word-break:break-all}.n{text-align:right;white-space:nowrap}th{color:#555;font-weight:600}"
        "td:nth-child(2){white-space:nowrap;color:#444}a{color:#0b57d0;text-decoration:none}a:hover{text-decoration:underline}"
        ".empty{color:#777}</style></head><body>"
        f"<h1>Plots on {html.escape(socket.gethostname())}</h1>"
        "<p class=path>Newest first within each project. Read-only.</p>"
        + "".join(sections)
        + "</body></html>"
    )
    return page.encode()


def project_root(config: PlotsConfig, project: Path) -> Path:
    return next(r for r in config.roots if project.is_relative_to(r))


def api_projects(config: PlotsConfig, projects: list[Path]) -> dict:
    entries = []
    for project in projects:
        plots = project_plots(project)
        root = project_root(config, project)
        entries.append({
            "id": project.as_posix(),
            "name": project.relative_to(root).as_posix(),
            "root": root.as_posix(),
            "count": len(plots),
            "latest": max((stat.st_mtime for _, stat in plots), default=None),
        })
    return {"host": socket.gethostname(), "projects": entries}


def api_files(config: PlotsConfig, projects: list[Path], project_id: str) -> dict | None:
    project = next((p for p in projects if p.as_posix() == project_id), None)
    if project is None:
        return None
    files = [
        {
            "path": path.relative_to(project / PLOTS_DIR).as_posix(),
            "url": quote(path.as_posix()),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        }
        for path, stat in project_plots(project)
    ]
    return {"project": project.as_posix(), "files": files}


def browser_page(config: PlotsConfig, projects: list[Path]) -> bytes:
    try:
        return BROWSER_PATH.read_bytes()
    except OSError:
        return index_page(config, projects)


def presented_token(handler: BaseHTTPRequestHandler, query: str) -> tuple[str | None, bool]:
    """(token the client presented, whether it came from the query string)."""
    value = parse_qs(query).get("token")
    if value:
        return value[0], True
    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip(), False
    cookie = SimpleCookie(handler.headers.get("Cookie", ""))
    if COOKIE_NAME in cookie:
        return cookie[COOKIE_NAME].value, False
    return None, False


def make_handler(config: PlotsConfig, catalog: Catalog | None = None, access_log=None):
    """access_log, when given, receives one line per request (the service sends it to the journal)."""
    catalog = catalog or Catalog(config)

    class Handler(BaseHTTPRequestHandler):
        server_version = "plot-server"
        sys_version = ""
        auth = "-"

        def log_message(self, *args: object) -> None:  # errors are reported through log_request
            pass

        def log_request(self, code: object = "-", size: object = "-") -> None:
            if access_log is None:
                return
            # Never log the query string: it can carry the token.
            path = urlparse(self.path).path if isinstance(self.path, str) else "-"
            status = getattr(code, "value", code)
            access_log(f"{self.client_address[0]} {self.command} {path} {status} auth={self.auth}")

        def do_HEAD(self) -> None:  # noqa: N802
            self._respond(head=True)

        def do_GET(self) -> None:  # noqa: N802
            self._respond(head=False)

        def _respond(self, head: bool) -> None:
            url = urlparse(self.path)
            if url.path == "/healthz":
                self._send(200, "text/plain; charset=utf-8", b"ok\n", head=head)
                return
            set_cookie = None
            self.auth = "loopback" if is_loopback(self.client_address[0]) else ("open" if config.token is None else "-")
            # Loopback clients (a shell on this machine, or an SSH tunnel ending here) are already
            # authenticated by the machine; anyone arriving over the network needs the token.
            if config.token is not None and not is_loopback(self.client_address[0]):
                token, from_query = presented_token(self, url.query)
                if token is None or not hmac.compare_digest(token.encode(), config.token.encode()):
                    self.auth = "missing" if token is None else "REJECTED"
                    self._send(401, "text/plain; charset=utf-8", DENIED, head=head)
                    return
                self.auth = "token"
                if from_query:
                    set_cookie = f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000"
            if url.path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", browser_page(config, catalog.projects()), set_cookie, head)
                return
            if url.path == "/plain":
                self._send(200, "text/html; charset=utf-8", index_page(config, catalog.projects()), set_cookie, head)
                return
            if url.path == "/api/projects":
                body = json.dumps(api_projects(config, catalog.projects())).encode()
                self._send(200, "application/json; charset=utf-8", body, set_cookie, head)
                return
            if url.path == "/api/files":
                project_id = parse_qs(url.query).get("project", [""])[0]
                data = api_files(config, catalog.projects(), project_id)
                if data is None:
                    self._send(404, "application/json; charset=utf-8", b'{"error": "unknown project"}', set_cookie, head)
                else:
                    self._send(200, "application/json; charset=utf-8", json.dumps(data).encode(), set_cookie, head)
                return
            path = resolve_plot(config, url.path)
            if path is None:
                self._send(404, "text/plain; charset=utf-8", b"not found\n", set_cookie, head)
                return
            self._send_file(path, set_cookie, head)

        def _headers(self, code: int, ctype: str, length: int, set_cookie: str | None, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            if set_cookie:
                self.send_header("Set-Cookie", set_cookie)
            self.end_headers()

        def _send(self, code: int, ctype: str, body: bytes, set_cookie: str | None = None, head: bool = False) -> None:
            self._headers(code, ctype, len(body), set_cookie)
            if not head:
                self.wfile.write(body)

        def _send_file(self, path: Path, set_cookie: str | None, head: bool) -> None:
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/json", "image/svg+xml"):
                ctype += "; charset=utf-8"
            try:
                handle = path.open("rb")
            except OSError:
                self._send(404, "text/plain; charset=utf-8", b"not found\n", set_cookie, head)
                return
            with handle:
                stat = os.fstat(handle.fileno())
                # Plot HTML is mostly inline JSON and compresses ~10x; gzip it on the way out.
                gzip = (
                    stat.st_size >= GZIP_MIN_BYTES
                    and ctype.startswith(GZIP_TYPES)
                    and "gzip" in self.headers.get("Accept-Encoding", "")
                )
                tag = f"{stat.st_mtime_ns:x}-{stat.st_size:x}"
                etag = f'"{tag}-gz"' if gzip else f'"{tag}"'
                validators = {
                    "ETag": etag,
                    "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
                    "Vary": "Accept-Encoding",
                    # The uncompressed size, so a client can show progress in real megabytes.
                    "X-Plot-Size": str(stat.st_size),
                }
                if etag in [t.strip() for t in self.headers.get("If-None-Match", "").split(",")]:
                    self.send_response(304)
                    for key, value in validators.items():
                        self.send_header(key, value)
                    self.send_header("Cache-Control", "no-cache")
                    if set_cookie:
                        self.send_header("Set-Cookie", set_cookie)
                    self.end_headers()
                    return
                if gzip:
                    # Streamed: no Content-Length; the HTTP/1.0 connection close ends the body.
                    self.close_connection = True
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Encoding", "gzip")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Referrer-Policy", "no-referrer")
                    for key, value in validators.items():
                        self.send_header(key, value)
                    if set_cookie:
                        self.send_header("Set-Cookie", set_cookie)
                    self.end_headers()
                    if head:
                        return
                    compressor = zlib.compressobj(6, zlib.DEFLATED, 31)
                    try:
                        while chunk := handle.read(1024 * 1024):
                            if out := compressor.compress(chunk):
                                self.wfile.write(out)
                        self.wfile.write(compressor.flush())
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                self._headers(200, ctype, stat.st_size, set_cookie, validators)
                if not head:
                    try:
                        shutil.copyfileobj(handle, self.wfile, 1024 * 1024)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

    return Handler


def serve(config: PlotsConfig, args: argparse.Namespace) -> int:
    bind = args.bind or config.bind
    port = args.port or config.port
    require_safe_exposure(config, bind)
    family = socket.AF_INET6 if ":" in bind else socket.AF_INET
    server_class = type("PlotHTTPServer", (ThreadingHTTPServer,), {"address_family": family, "daemon_threads": True})
    server = server_class((bind, port), make_handler(config, access_log=lambda line: print(line, flush=True)))
    access = "token required off this machine" if config.token else "loopback only"
    print(f"plot-server on http://{bind}:{port}/ ({access}); roots: {', '.join(map(str, config.roots))}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def local_base(config: PlotsConfig) -> str:
    return f"http://127.0.0.1:{config.port}"


def server_answers(config: PlotsConfig, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{local_base(config)}/healthz", timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def cmd_url(config: PlotsConfig, args: argparse.Namespace) -> int:
    path = Path(os.path.abspath(args.file))
    if resolve_plot(config, path.as_posix()) is None:
        print(
            f"not servable: {path} (must be a file inside <project>/plots/ of a git checkout under "
            f"{', '.join(map(str, config.roots))}, at most {config.depth} levels deep)",
            file=sys.stderr,
        )
        return 2
    route = quote(path.as_posix())
    print(f"local: {local_base(config)}{route}")
    if config.public_url:
        print(f"public: {config.public_url}{route}")
    if not server_answers(config):
        print(f"server: not answering on {local_base(config)}; see references/plot-server.md", file=sys.stderr)
        return 3
    try:
        request = urllib.request.Request(f"{local_base(config)}{route}", method="HEAD")
        with urllib.request.urlopen(request, timeout=5) as response:
            served = response.status == 200
    except (urllib.error.URLError, OSError):
        served = False
    if not served:
        print(
            "server: up, but not serving this file yet; a sandboxed server sees a new project once "
            "its sync job has run (every 2 minutes); see references/plot-server.md",
            file=sys.stderr,
        )
        return 4
    print("server: up")
    return 0


def write_if_changed(path: Path, text: str, mode: int) -> bool:
    try:
        if path.read_text() == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.chmod(mode)
    tmp.replace(path)
    return True


def cmd_sync(config: PlotsConfig, args: argparse.Namespace) -> int:
    """Publish each project's plots/ into the sandboxed service as a read-only bind mount.

    Runs as root from a timer. The service sees nothing of /mnt but these mounts, so this is
    the one place that decides what the server can read."""
    if config.manifest is None:
        raise PlotServerError("sync needs plots.manifest (the sandboxed install sets it)")
    real_roots = [r.resolve() for r in config.roots if r.is_dir()]
    binds, projects = [], []
    for project in discover_projects(dataclasses.replace(config, manifest=None)):
        plots = project / PLOTS_DIR
        try:
            real = plots.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if not any(real.is_relative_to(r) for r in real_roots):
            print(f"skip {project}: plots/ resolves outside every root ({real})", file=sys.stderr)
            continue
        if not (UNIT_SAFE_PATH.match(plots.as_posix()) and UNIT_SAFE_PATH.match(real.as_posix())):
            print(f"skip {project}: path has characters a unit file cannot carry safely", file=sys.stderr)
            continue
        projects.append(project.as_posix())
        binds.append(real.as_posix() if real == plots else f"{real.as_posix()}:{plots.as_posix()}")
    dropin = (
        "# Generated by plot_server.py sync; do not edit. Each line exposes one project's plots/\n"
        "# to the sandboxed plot-server, read-only.\n[Service]\n"
        + "".join(f"BindReadOnlyPaths=-{b}\n" for b in binds)
    )
    manifest = "# Generated by plot_server.py sync; do not edit.\n" + "".join(f"{p}\n" for p in projects)
    changed = write_if_changed(Path(args.dropin), dropin, 0o644)
    changed = write_if_changed(config.manifest, manifest, 0o644) or changed
    print(f"{len(projects)} project(s) published; {'changed' if changed else 'unchanged'}")
    if changed and not args.no_reload:
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "try-restart", "plot-server.service"], check=True)
    return 0


def toml_str(value: str) -> str:
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def cmd_system_config(config: PlotsConfig, args: argparse.Namespace) -> int:
    """Write the sandboxed service's config from a user's [plots] table, keeping or rotating the token."""
    out = Path(args.out)
    token = None
    if not args.new_token and out.exists():
        token = load_config(out).token
    if not args.new_token and token is None:
        token = config.token
    if token is None or args.new_token:
        token = secrets.token_urlsafe(32)
    lines = [
        "# plot-server system config, written by plot_server.py system-config. Readable by root and plotserver only.",
        "[plots]",
        "roots = [" + ", ".join(toml_str(r.as_posix()) for r in config.roots) + "]",
        f"port = {config.port}",
        f"bind = {toml_str(config.bind)}",
        f"depth = {config.depth}",
        f"token = {toml_str(token)}",
        f"manifest = {toml_str(args.manifest)}",
    ]
    if config.public_url:
        lines.append(f"public_url = {toml_str(config.public_url)}")
    write_if_changed(out, "\n".join(lines) + "\n", 0o640)
    print(f"wrote {out}{' with a new token' if args.new_token else ''}")
    return 0


def cmd_list(config: PlotsConfig) -> int:
    for project in discover_projects(config):
        plots = project_plots(project)
        print(f"{project}  ({len(plots)} HTML plot{'s' if len(plots) != 1 else ''})")
        for path, stat in plots:
            print(f"  {path.relative_to(project).as_posix()}  {human_size(stat.st_size)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", default=DEFAULT_REGISTRY, help="rigsync registry holding the [plots] table")
    sub = parser.add_subparsers(dest="command", required=True)
    serve_parser = sub.add_parser("serve", help="run the server")
    serve_parser.add_argument("--bind", help="override plots.bind")
    serve_parser.add_argument("--port", type=int, help="override plots.port")
    url_parser = sub.add_parser("url", help="print a plot's URL and whether the server answers")
    url_parser.add_argument("file")
    sub.add_parser("list", help="list discovered projects and their HTML plots")
    sub.add_parser("token", help="print a fresh access token")
    sync_parser = sub.add_parser("sync", help="(root) publish each project's plots/ into the sandboxed service")
    sync_parser.add_argument("--dropin", default=SYSTEM_DROPIN)
    sync_parser.add_argument("--no-reload", action="store_true", help="write files only; do not reload systemd")
    cfg_parser = sub.add_parser("system-config", help="(root) write the sandboxed service's config")
    cfg_parser.add_argument("--out", default=SYSTEM_CONFIG)
    cfg_parser.add_argument("--manifest", default=SYSTEM_MANIFEST)
    cfg_parser.add_argument("--new-token", action="store_true", help="rotate the token")
    args = parser.parse_args(argv)
    if args.command == "token":
        print(secrets.token_urlsafe(32))
        return 0
    try:
        config = load_config(Path(args.registry).expanduser())
        if args.command == "serve":
            return serve(config, args)
        if args.command == "url":
            return cmd_url(config, args)
        if args.command == "sync":
            return cmd_sync(config, args)
        if args.command == "system-config":
            return cmd_system_config(config, args)
        return cmd_list(config)
    except PlotServerError as exc:
        print(f"plot-server: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
