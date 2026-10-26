from __future__ import annotations

import gzip
import importlib.util
import socket
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HUB = ROOT / "plugins/core/skills/service-hub/scripts/hub.py"
TOKEN = "t" * 24


def load_hub():
    spec = importlib.util.spec_from_file_location("service_hub", HUB)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


hub = load_hub()


class Backend(BaseHTTPRequestHandler):
    """Echoes what reached it, and answers a few paths the way real backends do."""

    seen: list[dict] = []

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        Backend.seen.append({"path": self.path, "headers": dict(self.headers)})
        if self.path == "/go":
            self.send_response(302)
            self.send_header("Location", "/landed")
            self.send_header("Set-Cookie", "app=1; Path=/; HttpOnly")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path in ("/page", "/page.gz"):
            body = b"<html><body><h1>app</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            if self.path.endswith(".gz"):
                body = gzip.compress(body)
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.headers.get("Upgrade"):
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.end_headers()
            self.wfile.flush()
            data = self.connection.recv(64)
            self.connection.sendall(b"echo:" + data)
            return
        body = f"path={self.path}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start(server: ThreadingHTTPServer) -> ThreadingHTTPServer:
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # type: ignore[override]
        return None


OPENER = urllib.request.build_opener(NoRedirect)


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "machines.toml"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def load(self, text: str):
        self.path.write_text(text)
        return hub.load_config(self.path)

    def test_services_keep_registry_order_and_defaults(self) -> None:
        config = self.load(
            '[hub]\ntitle = "rig"\n'
            '[hub.services.board]\nurl = "http://127.0.0.1:1/"\n'
            '[hub.services.ide]\nname = "IDE"\nlink = "https://example.test:6769"\n'
        )
        self.assertEqual([s.slug for s in config.services], ["board", "ide"])
        board, ide = config.services
        self.assertEqual((board.url, board.health, board.name), ("http://127.0.0.1:1", "/healthz", "board"))
        self.assertEqual((ide.url, ide.link, ide.health), (None, "https://example.test:6769", None))
        self.assertEqual((config.port, config.bind), (40970, "127.0.0.1"))

    def test_rejections(self) -> None:
        bad = {
            "network bind needs a token": '[hub]\nbind = "0.0.0.0"\n',
            "short token": '[hub]\ntoken = "short"\n',
            "reserved name": '[hub.services.api]\nurl = "http://127.0.0.1:1"\n',
            "upper case": '[hub.services.Board]\nurl = "http://127.0.0.1:1"\n',
            "both url and link": '[hub.services.x]\nurl = "http://127.0.0.1:1"\nlink = "http://a"\n',
            "neither": '[hub.services.x]\nname = "x"\n',
            "backend with a path": '[hub.services.x]\nurl = "http://127.0.0.1:1/sub"\n',
            "https backend": '[hub.services.x]\nurl = "https://127.0.0.1:1"\n',
            "relative health": '[hub.services.x]\nurl = "http://127.0.0.1:1"\nhealth = "healthz"\n',
        }
        for why, text in bad.items():
            with self.subTest(why), self.assertRaises(hub.HubError):
                self.load(text if text.startswith("[hub]") else "[hub]\n" + text)

    def test_icon_is_validated_and_served_without_the_token(self) -> None:
        with self.assertRaises(hub.HubError):
            self.load('[hub]\nicon = "/nope/icon.png"\n')
        with self.assertRaises(hub.HubError):
            self.load('[hub]\nicon = "/etc/hostname"\n')
        icon = Path(self.tmp.name) / "icon.png"
        icon.write_bytes(b"\x89PNG fake")
        config = self.load(f'[hub]\ntoken = "{TOKEN}"\nicon = "{icon}"\n')
        self.assertEqual(config.icon, icon)
        server = start(ThreadingHTTPServer(("127.0.0.1", 0), hub.make_handler(config, hub.Health(config))))
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with mock.patch.object(hub, "is_loopback", return_value=False):
            with OPENER.open(base + "/favicon.ico", timeout=5) as response:
                self.assertEqual((response.headers["Content-Type"], response.read()), ("image/png", b"\x89PNG fake"))
            request = urllib.request.Request(base + "/", headers={"Accept": "text/html"})
            with self.assertRaises(urllib.error.HTTPError) as denied:
                OPENER.open(request, timeout=5)
            self.assertIn(b'<link rel="icon" href="/favicon.ico?v=', denied.exception.read())
            page = urllib.request.Request(base + "/", headers={"Cookie": f"{hub.COOKIE_NAME}={TOKEN}"})
            with OPENER.open(page, timeout=5) as response:
                html = response.read()
            self.assertIn(b'rel="icon" href="favicon.ico?v=', html)  # in the HTML itself, not set later by script
            self.assertNotIn(b"__HUB_", html)

    def test_empty_health_disables_the_probe(self) -> None:
        config = self.load('[hub]\n[hub.services.x]\nurl = "http://127.0.0.1:1"\nhealth = ""\n')
        self.assertIsNone(config.services[0].health)
        self.assertEqual(hub.probe(config.services[0])["status"], "unknown")


class LiveHubTests(unittest.TestCase):
    """Real round trips through the hub to a real backend; a network client is simulated by patching is_loopback."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = start(ThreadingHTTPServer(("127.0.0.1", 0), Backend))
        dead = free_port()
        cls.config = hub.HubConfig(
            port=0, bind="127.0.0.1", token=TOKEN, title="test-rig", public_url="http://pub.test:40970",
            services=(
                hub.Service("board", "Board", "lanes", "", f"http://127.0.0.1:{cls.backend.server_address[1]}", None, "/healthz"),
                hub.Service("dead", "Dead", "", "", f"http://127.0.0.1:{dead}", None, "/healthz"),
                hub.Service("raw", "Raw", "", "", f"http://127.0.0.1:{cls.backend.server_address[1]}", None, None, nav=False),
                hub.Service("ide", "IDE", "", "", None, "http://127.0.0.1:9", None),
            ),
        )
        cls.server = start(ThreadingHTTPServer(("127.0.0.1", 0), hub.make_handler(cls.config, hub.Health(cls.config))))
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        for server in (cls.server, cls.backend):
            server.shutdown()
            server.server_close()

    def setUp(self) -> None:
        Backend.seen.clear()
        patcher = mock.patch.object(hub, "is_loopback", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, path: str, headers: dict[str, str] | None = None, method: str = "GET", data: bytes | None = None):
        request = urllib.request.Request(self.base + path, headers=headers or {}, method=method, data=data)
        try:
            with OPENER.open(request, timeout=5) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as err:
            return err.code, dict(err.headers), err.read()

    def authed(self, path: str, **kwargs):
        headers = {"Cookie": f"{hub.COOKIE_NAME}={TOKEN}", **kwargs.pop("headers", {})}
        return self.get(path, headers=headers, **kwargs)

    def test_network_clients_need_the_token_and_never_reach_a_backend_without_it(self) -> None:
        for path in ("/", "/api/services", "/board/anything"):
            code, _, body = self.get(path)
            self.assertEqual(code, 401, path)
        code, headers, body = self.get("/board/", headers={"Accept": "text/html"})
        self.assertEqual(code, 401)
        self.assertIn(b'name="token"', body)  # sign-in form, not a bare error
        self.assertEqual(self.get("/", headers={"Authorization": "Bearer wrong" + TOKEN})[0], 401)
        self.assertEqual(Backend.seen, [])
        self.assertEqual(self.get("/healthz")[0], 200)

    def test_token_in_the_query_becomes_a_cookie_and_leaves_the_url(self) -> None:
        code, headers, _ = self.get(f"/board/x?token={TOKEN}&keep=1")
        self.assertEqual(code, 303)
        self.assertEqual(headers["Location"], "/board/x?keep=1")
        self.assertIn(f"{hub.COOKIE_NAME}={TOKEN}; Path=/; HttpOnly", headers["Set-Cookie"])
        self.assertEqual(Backend.seen, [])

    def test_proxy_strips_the_prefix_and_hub_credentials(self) -> None:
        code, _, body = self.get(
            "/board/api/board?x=1",
            headers={"Cookie": f"other=1; {hub.COOKIE_NAME}={TOKEN}", "Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual((code, body), (200, b"path=/api/board?x=1"))
        seen = Backend.seen[-1]["headers"]
        self.assertEqual(seen.get("Cookie"), "other=1")
        self.assertNotIn("Authorization", seen)
        self.assertEqual(seen["X-Forwarded-Prefix"], "/board")
        self.assertEqual(self.authed("/board/mnt/a%20b/plot.html")[2], b"path=/mnt/a%20b/plot.html")

    def test_slug_without_slash_redirects_and_location_and_cookies_are_rewritten(self) -> None:
        code, headers, _ = self.authed("/board?x=1")
        self.assertEqual((code, headers["Location"]), (302, "/board/?x=1"))
        code, headers, _ = self.authed("/board/go")
        self.assertEqual((code, headers["Location"]), (302, "/board/landed"))
        self.assertIn("Path=/board/", headers["Set-Cookie"])

    def test_page_loads_get_the_back_to_hub_button(self) -> None:
        tag = b'<script src="/hub/nav.js" data-service="board" defer></script></body>'
        for path in ("/board/page", "/board/page.gz"):
            code, headers, body = self.authed(path, headers={"Sec-Fetch-Dest": "document", "Accept-Encoding": "gzip"})
            self.assertEqual(code, 200, path)
            self.assertIn(tag, body, path)
            self.assertNotIn("Content-Encoding", headers)
            self.assertEqual(int(headers["Content-Length"]), len(body))
        # Without fetch metadata, an HTML Accept marks a page load.
        self.assertIn(tag, self.authed("/board/page", headers={"Accept": "text/html"})[2])
        # Frames, fetches, opted-out services and non-HTML stay byte-for-byte.
        for path, headers in (
            ("/board/page", {"Sec-Fetch-Dest": "iframe", "Accept": "text/html"}),
            ("/board/page", {"Sec-Fetch-Dest": "empty"}),
            ("/raw/page", {"Sec-Fetch-Dest": "document"}),
            ("/board/x", {"Sec-Fetch-Dest": "document"}),
        ):
            self.assertNotIn(b"nav.js", self.authed(path, headers=headers)[2], (path, headers))
        code, _, gz = self.authed("/board/page.gz", headers={"Sec-Fetch-Dest": "empty"})
        self.assertEqual(gzip.decompress(gz), b"<html><body><h1>app</h1></body></html>")
        code, headers, script = self.authed("/hub/nav.js")
        self.assertEqual(code, 200)
        self.assertIn(b"service-hub-nav", script)

    def test_nav_injection_bounds(self) -> None:
        self.assertEqual(hub.inject_nav(b"no body tag", "x"), b'no body tag<script src="/hub/nav.js" data-service="x" defer></script>')
        self.assertIn('data-icon="&quot;📈"'.encode(), hub.inject_nav(b"<body></body>", "x", '"📈'))
        self.assertIsNone(hub.gunzip_bounded(gzip.compress(b"a" * 1000), 100))
        self.assertIsNone(hub.gunzip_bounded(b"not gzip", 100))

    def test_post_body_is_forwarded(self) -> None:
        self.assertEqual(self.authed("/board/echo", method="POST", data=b"hello")[2], b"hello")

    def test_dead_backend_is_a_502_and_unknown_service_a_404(self) -> None:
        code, _, body = self.authed("/dead/")
        self.assertEqual(code, 502)
        self.assertIn(b"Dead is not answering", body)
        self.assertEqual(self.authed("/nope/")[0], 404)
        self.assertEqual(self.authed("/ide")[1]["Location"], "http://127.0.0.1:9")

    def test_services_api_reports_health(self) -> None:
        import json

        data = json.loads(self.authed("/api/services")[2])
        status = {s["slug"]: (s["kind"], s["status"], s["href"]) for s in data["services"]}
        self.assertEqual(status["board"], ("proxy", "up", "board/"))
        self.assertEqual(status["dead"][1], "down")
        self.assertEqual(status["ide"][:2], ("link", "down"))
        page = self.authed("/")[2]
        self.assertIn(b"test-rig", page)
        self.assertIn(b"api/services", page)

    def test_upgrade_requests_are_tunnelled(self) -> None:
        port = self.server.server_address[1]
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(
                (f"GET /board/ws HTTP/1.1\r\nHost: x\r\nCookie: {hub.COOKIE_NAME}={TOKEN}\r\n"
                 "Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n").encode()
            )
            head = b""
            while b"\r\n\r\n" not in head:
                head += sock.recv(1024)
            self.assertIn(b" 101 ", head.split(b"\r\n", 1)[0])
            sock.sendall(b"ping")
            self.assertEqual(sock.recv(64), b"echo:ping")
        self.assertEqual(Backend.seen[-1]["path"], "/ws")


class LoopbackTests(unittest.TestCase):
    def test_loopback_detection(self) -> None:
        self.assertTrue(hub.is_loopback("127.0.0.1"))
        self.assertTrue(hub.is_loopback("::1"))
        self.assertFalse(hub.is_loopback("192.168.1.10"))

    def test_url_command(self) -> None:
        import io
        from contextlib import redirect_stdout

        config = hub.HubConfig(40970, "0.0.0.0", TOKEN, "r", "http://pub.test:40970",
                               (hub.Service("plots", "Plots", "", "", "http://127.0.0.1:40975", None, "/healthz"),))
        out = io.StringIO()
        with redirect_stdout(out):
            hub.cmd_url(config, hub.argparse.Namespace(service="plots", path="/mnt/p/plots/a.html"))
        self.assertIn("public: http://pub.test:40970/plots/mnt/p/plots/a.html", out.getvalue())


if __name__ == "__main__":
    unittest.main()
