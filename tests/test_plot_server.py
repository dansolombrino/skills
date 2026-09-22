from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "plugins/research/skills/visualizations/scripts/plot_server.py"
SPEC = importlib.util.spec_from_file_location("plot_server", SCRIPT)
assert SPEC and SPEC.loader
plot_server = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = plot_server
SPEC.loader.exec_module(plot_server)

TOKEN = "t" * 32


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "Projects"
        self.project = self.root / "area" / "proj" / "proj"
        (self.project / ".git").mkdir(parents=True)
        self.plot = self.project / "plots/000_x/plot_loss/loss.html"
        self.plot.parent.mkdir(parents=True)
        self.plot.write_text("<html>loss</html>")
        (self.project / "evaluations").mkdir()
        (self.project / "evaluations/secret.json").write_text("{}")
        (self.project / "code.py").write_text("x = 1")
        self.outside = base / "outside.html"
        self.outside.write_text("<html>outside</html>")
        self.registry = base / "machines.toml"
        self.write_registry()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_registry(self, extra: str = "") -> None:
        self.registry.write_text(f'[plots]\nroots = ["{self.root}"]\n{extra}')
        self.config = plot_server.load_config(self.registry)

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = plot_server.main(["--registry", str(self.registry), *args])
        return code, out.getvalue(), err.getvalue()


class PlotServerTest(Fixture):
    def test_defaults_are_loopback_on_40975(self) -> None:
        self.assertEqual(self.config.port, 40975)
        self.assertEqual(self.config.bind, "127.0.0.1")
        self.assertIsNone(self.config.token)

    def test_resolve_serves_only_files_inside_a_projects_plots(self) -> None:
        self.assertEqual(plot_server.resolve_plot(self.config, self.plot.as_posix()), self.plot)
        for path in (
            self.project / "code.py",
            self.project / "evaluations/secret.json",
            self.outside,
            self.project / "plots/000_x/plot_loss/../../../code.py",
            self.project / "plots/000_x/plot_loss/missing.html",
            self.project / "plots/000_x",
        ):
            self.assertIsNone(plot_server.resolve_plot(self.config, path.as_posix()), path)

    def test_resolve_refuses_a_plots_folder_outside_a_checkout(self) -> None:
        loose = self.root / "loose/plots/x.html"
        loose.parent.mkdir(parents=True)
        loose.write_text("x")
        self.assertIsNone(plot_server.resolve_plot(self.config, loose.as_posix()))

    def test_symlink_inside_plots_cannot_escape_but_plots_may_live_elsewhere(self) -> None:
        escape = self.plot.parent / "escape.html"
        escape.symlink_to(self.outside)
        self.assertIsNone(plot_server.resolve_plot(self.config, escape.as_posix()))
        other = self.root / "area" / "other" / "other"
        (other / ".git").mkdir(parents=True)
        volume = Path(self.tmp.name) / "volume_plots"
        (volume / "a").mkdir(parents=True)
        (volume / "a/fig.html").write_text("fig")
        (other / "plots").symlink_to(volume)
        target = other / "plots/a/fig.html"
        self.assertEqual(plot_server.resolve_plot(self.config, target.as_posix()), target)
        self.assertIn(other, plot_server.discover_projects(self.config))

    def test_depth_limits_resolution_and_discovery(self) -> None:
        self.write_registry("depth = 2\n")
        self.assertIsNone(plot_server.resolve_plot(self.config, self.plot.as_posix()))
        self.assertEqual(plot_server.discover_projects(self.config), [])

    def test_public_bind_without_token_is_refused(self) -> None:
        self.write_registry('bind = "0.0.0.0"\n')
        with self.assertRaises(plot_server.PlotServerError):
            plot_server.require_safe_exposure(self.config, self.config.bind)
        plot_server.require_safe_exposure(self.config, "127.0.0.1")
        self.write_registry(f'bind = "0.0.0.0"\ntoken = "{TOKEN}"\n')
        plot_server.require_safe_exposure(self.config, self.config.bind)

    def test_url_reports_servable_paths_and_a_missing_server(self) -> None:
        self.write_registry(f'port = 1\npublic_url = "http://example.test:40975/"\ntoken = "{TOKEN}"\n')
        code, out, err = self.run_cli("url", str(self.plot))
        self.assertEqual(code, 3)
        self.assertIn(f"local: http://127.0.0.1:1{self.plot}", out)
        self.assertIn(f"public: http://example.test:40975{self.plot}", out)
        self.assertNotIn(TOKEN, out + err)
        self.assertIn("not answering", err)
        code, _, err = self.run_cli("url", str(self.outside))
        self.assertEqual(code, 2)
        self.assertIn("not servable", err)

    def test_list_shows_projects_and_plots(self) -> None:
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn(str(self.project), out)
        self.assertIn("plots/000_x/plot_loss/loss.html", out)

    def test_token_command_prints_a_fresh_secret(self) -> None:
        code, out, _ = self.run_cli("token")
        self.assertEqual(code, 0)
        self.assertGreaterEqual(len(out.strip()), 32)


class LiveServerTest(Fixture):
    """Real HTTP round trips; the remote-client case is simulated by patching is_loopback."""

    def start(self) -> str:
        server = plot_server.ThreadingHTTPServer(("127.0.0.1", 0), plot_server.make_handler(self.config))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def get(self, url: str, headers: dict | None = None) -> tuple[int, bytes, dict]:
        request = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def test_loopback_client_needs_no_token_and_gets_the_file(self) -> None:
        self.write_registry(f'token = "{TOKEN}"\n')
        base = self.start()
        code, body, headers = self.get(base + self.plot.as_posix())
        self.assertEqual(code, 200)
        self.assertEqual(body, b"<html>loss</html>")
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertEqual(headers["Cache-Control"], "no-cache")
        code, body, _ = self.get(base + "/plain")
        self.assertEqual(code, 200)
        self.assertIn(b"000_x/plot_loss/loss.html", body)
        self.assertEqual(self.get(base + (self.project / "code.py").as_posix())[0], 404)

    def test_remote_client_needs_the_token_then_a_cookie_suffices(self) -> None:
        self.write_registry(f'token = "{TOKEN}"\n')
        base = self.start()
        with mock.patch.object(plot_server, "is_loopback", return_value=False):
            self.assertEqual(self.get(base + self.plot.as_posix())[0], 401)
            self.assertEqual(self.get(base + self.plot.as_posix() + "?token=wrong")[0], 401)
            self.assertEqual(self.get(base + "/healthz")[0], 200)
            code, _, headers = self.get(base + f"/?token={TOKEN}")
            self.assertEqual(code, 200)
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            self.assertIn("HttpOnly", headers["Set-Cookie"])
            self.assertEqual(self.get(base + self.plot.as_posix(), {"Cookie": cookie})[0], 200)
            self.assertEqual(self.get(base + self.plot.as_posix(), {"Authorization": f"Bearer {TOKEN}"})[0], 200)

    def test_browser_page_and_json_api(self) -> None:
        base = self.start()
        code, body, _ = self.get(base + "/")
        self.assertEqual(code, 200)
        self.assertEqual(body, plot_server.BROWSER_PATH.read_bytes())
        self.assertIn(b"/api/files", body)
        self.assertIn(b"000_x/plot_loss/loss.html", self.get(base + "/plain")[1])
        code, body, _ = self.get(base + "/api/projects")
        projects = json.loads(body)["projects"]
        self.assertEqual([p["id"] for p in projects], [self.project.as_posix()])
        self.assertEqual((projects[0]["name"], projects[0]["count"]), ("area/proj/proj", 1))
        code, body, _ = self.get(base + "/api/files?project=" + urllib.parse.quote(self.project.as_posix()))
        files = json.loads(body)["files"]
        self.assertEqual(files[0]["path"], "000_x/plot_loss/loss.html")
        self.assertEqual(self.get(base + files[0]["url"])[0], 200)
        self.assertEqual(self.get(base + "/api/files?project=/etc")[0], 404)
        with mock.patch.object(plot_server, "BROWSER_PATH", plot_server.BROWSER_PATH.with_name("missing.html")):
            self.assertIn(b"000_x/plot_loss/loss.html", self.get(base + "/")[1])

    def test_large_text_is_gzipped_and_revalidates_with_304(self) -> None:
        import gzip
        page = self.project / "plots/page.html"
        payload = ("<html><body>" + "0.123456789, " * 20000 + "</body></html>").encode()
        page.write_bytes(payload)
        base = self.start()
        code, body, headers = self.get(base + page.as_posix(), {"Accept-Encoding": "gzip"})
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Encoding"], "gzip")
        self.assertLess(len(body), len(payload) // 5)
        self.assertEqual(gzip.decompress(body), payload)
        self.assertEqual(int(headers["X-Plot-Size"]), len(payload))
        code, body, plain = self.get(base + page.as_posix())
        self.assertNotIn("Content-Encoding", plain)
        self.assertEqual(body, payload)
        self.assertNotEqual(headers["ETag"], plain["ETag"])
        self.assertEqual(self.get(base + page.as_posix(), {"If-None-Match": plain["ETag"]})[0], 304)
        self.assertEqual(
            self.get(base + page.as_posix(), {"If-None-Match": headers["ETag"], "Accept-Encoding": "gzip"})[0], 304
        )
        # Small files go out as-is.
        self.assertNotIn("Content-Encoding", self.get(base + self.plot.as_posix(), {"Accept-Encoding": "gzip"})[2])

    def test_large_files_stream_whole(self) -> None:
        big = self.project / "plots/big.html"
        payload = os.urandom(3 * 1024 * 1024 + 7)
        big.write_bytes(payload)
        base = self.start()
        code, body, headers = self.get(base + big.as_posix())
        self.assertEqual(code, 200)
        self.assertEqual(body, payload)
        self.assertEqual(int(headers["Content-Length"]), len(payload))


if __name__ == "__main__":
    unittest.main()
