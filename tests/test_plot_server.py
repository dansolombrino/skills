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
HASH_A = "rid-" + "a" * 32
HASH_B = "rid-" + "b" * 32
HASH_OLD = "rid-" + "0" * 32


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
        self.experiment = self.project / "evaluations/000_x"
        self.spec = self.project / "config/000_x"  # tracked identity files live in config/<exp>/
        self.write_run_id_files(self.experiment)
        (self.experiment / HASH_A).mkdir()
        (self.experiment / HASH_A / ".run_config.json").write_text('{"lr": 0.0001}')
        (self.project / "code.py").write_text("x = 1")
        self.outside = base / "outside.html"
        self.outside.write_text("<html>outside</html>")
        self.registry = base / "machines.toml"
        self.write_registry()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def spec_of(self, experiment: Path) -> Path:
        """config/<exp>/ for an evaluations/<exp>/ path."""
        return self.project / "config" / experiment.relative_to(self.project / "evaluations")

    def write_run_id_files(self, experiment: Path) -> None:
        """The derived map in evaluations/<exp>/, the identity files in config/<exp>/."""
        experiment.mkdir(parents=True, exist_ok=True)
        spec = self.spec_of(experiment)
        spec.mkdir(parents=True, exist_ok=True)
        run = {"params": {"lr": 0.0001, "seed": 0}, "version": 2, "wave_id": "w1", "state": "done", "rig": "r1"}
        (experiment / "RUN_ID_MAP.json").write_text(json.dumps({
            "layout": "hashed-v2", "version": 2,
            "by_hash": {HASH_A: run, HASH_B: {**run, "params": {"lr": 0.001, "seed": 0}}},
            "by_params": {}, "configs": {HASH_A: {"lr": 0.0001, "seed": 0}},
        }))
        (spec / "RUN_ID_VERSIONS.json").write_text(json.dumps([
            {"version": 1, "params": ["seed"], "date": "2026-01-01", "reason": "first"},
            {"version": 2, "params": ["lr", "seed"], "date": "2026-02-01", "reason": "sweep lr"},
        ]))
        (spec / "RUN_ID_HISTORY.json").write_text(json.dumps([
            {"from_version": 1, "to_version": 2, "date": "2026-02-01", "map": {HASH_OLD: HASH_A}, "backfill": {}},
        ]))

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

    def test_run_id_files_resolve_only_from_the_allowlist(self) -> None:
        resolve = plot_server.resolve_run_id_file
        self.assertEqual(resolve(self.config, self.project, "000_x", "RUN_ID_MAP.json"), self.experiment / "RUN_ID_MAP.json")
        for name in ("RUN_ID_VERSIONS.json", "RUN_ID_HISTORY.json"):
            self.assertEqual(resolve(self.config, self.project, "000_x", name), self.spec / name)
        # identity files are read from config/ only: a stray copy in evaluations/ is never served
        (self.spec / "RUN_ID_VERSIONS.json").rename(self.experiment / "RUN_ID_VERSIONS.json")
        self.assertIsNone(resolve(self.config, self.project, "000_x", "RUN_ID_VERSIONS.json"))
        (self.experiment / "RUN_ID_VERSIONS.json").rename(self.spec / "RUN_ID_VERSIONS.json")
        (self.experiment / "RUN_ID_OTHER.json").write_text("{}")
        (self.spec / "RUN_ID_OTHER.json").write_text("{}")
        (self.spec / "train.yaml").write_text("lr: 1")
        for experiment, name in (
            ("000_x", "RUN_ID_OTHER.json"),
            ("000_x", "secret.json"),
            ("000_x", "train.yaml"),
            ("000_x/" + HASH_A, ".run_config.json"),
            ("000_x/" + HASH_A, "RUN_ID_MAP.json"),
            ("", "RUN_ID_MAP.json"),
            ("000_x/..", "RUN_ID_MAP.json"),
            ("../evaluations/000_x", "RUN_ID_MAP.json"),
            ("/000_x", "RUN_ID_MAP.json"),
            ("000_x\x00", "RUN_ID_MAP.json"),
            ("a/b/c/d/e", "RUN_ID_MAP.json"),
            ("000_x", "../RUN_ID_MAP.json"),
        ):
            self.assertIsNone(resolve(self.config, self.project, experiment, name), (experiment, name))
        # The project must own a plots/ folder in a checkout under a root.
        self.assertIsNone(resolve(self.config, self.root, "000_x", "RUN_ID_MAP.json"))
        self.assertIsNone(resolve(self.config, Path(self.tmp.name), "000_x", "RUN_ID_MAP.json"))

    def test_run_id_file_symlinks_cannot_leave_evaluations(self) -> None:
        escape = self.project / "evaluations/001_escape"
        escape.mkdir()
        (escape / "RUN_ID_MAP.json").symlink_to(self.outside)
        self.assertIsNone(plot_server.resolve_run_id_file(self.config, self.project, "001_escape", "RUN_ID_MAP.json"))
        (self.project / "evaluations/002_dirlink").symlink_to(Path(self.tmp.name))
        (Path(self.tmp.name) / "RUN_ID_MAP.json").write_text("{}")
        self.assertIsNone(plot_server.resolve_run_id_file(self.config, self.project, "002_dirlink", "RUN_ID_MAP.json"))
        inside = self.project / "evaluations/003_inside"
        inside.mkdir()
        (inside / "RUN_ID_MAP.json").symlink_to(self.experiment / "RUN_ID_MAP.json")
        self.assertEqual(
            plot_server.resolve_run_id_file(self.config, self.project, "003_inside", "RUN_ID_MAP.json"),
            inside / "RUN_ID_MAP.json",
        )

    def test_config_symlinks_cannot_leave_config(self) -> None:
        escape = self.project / "config/001_escape"
        escape.mkdir(parents=True)
        (escape / "RUN_ID_VERSIONS.json").symlink_to(self.outside)
        self.assertIsNone(plot_server.resolve_run_id_file(self.config, self.project, "001_escape", "RUN_ID_VERSIONS.json"))
        (self.project / "config/002_dirlink").symlink_to(Path(self.tmp.name))
        (Path(self.tmp.name) / "RUN_ID_HISTORY.json").write_text("[]")
        self.assertIsNone(plot_server.resolve_run_id_file(self.config, self.project, "002_dirlink", "RUN_ID_HISTORY.json"))

    def test_experiments_are_discovered_nested_and_bounded(self) -> None:
        self.write_run_id_files(self.project / "evaluations/001_x/002_sub")
        (self.project / "evaluations/003_empty/" / HASH_A).mkdir(parents=True)
        self.write_run_id_files(self.project / "evaluations/.run_id_migrations/x")
        self.write_run_id_files(self.project / "evaluations/a/b/c/d/e")
        # the union: a config/ dir holding RUN_ID_VERSIONS.json is an experiment before any map exists
        (self.project / "config/004_new").mkdir(parents=True)
        (self.project / "config/004_new/RUN_ID_VERSIONS.json").write_text("[]")
        (self.project / "config/005_plain").mkdir(parents=True)
        (self.project / "config/005_plain/train.yaml").write_text("lr: 1")
        self.assertEqual(
            plot_server.discover_experiments(self.project), ["000_x", "001_x/002_sub", "004_new"]
        )

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


class SandboxedInstallTest(Fixture):
    """sync (root, from a timer) and system-config, plus the manifest mode the sandboxed server runs in."""

    def sync(self, config) -> tuple[int, str, str, Path]:
        dropin = Path(self.tmp.name) / "plots.conf"
        out, err = io.StringIO(), io.StringIO()
        args = plot_server.argparse.Namespace(dropin=str(dropin), no_reload=True)
        with redirect_stdout(out), redirect_stderr(err):
            code = plot_server.cmd_sync(config, args)
        return code, out.getvalue(), err.getvalue(), dropin

    def manifest_config(self):
        manifest = Path(self.tmp.name) / "projects.txt"
        self.write_registry(f'manifest = "{manifest}"\n')
        return self.config, manifest

    def test_sync_publishes_plots_read_only_and_is_idempotent(self) -> None:
        config, manifest = self.manifest_config()
        code, out, _, dropin = self.sync(config)
        self.assertEqual(code, 0)
        self.assertIn("1 project(s) published; changed", out)
        text = dropin.read_text()
        self.assertIn(f"BindReadOnlyPaths=-{self.project / 'plots'}\n", text)
        self.assertNotIn("BindPaths=", text)
        self.assertEqual(manifest.read_text().splitlines()[-1], str(self.project))
        self.assertIn(f"BindReadOnlyPaths=-{self.experiment / 'RUN_ID_MAP.json'}\n", text)
        for name in ("RUN_ID_VERSIONS.json", "RUN_ID_HISTORY.json"):
            self.assertIn(f"BindReadOnlyPaths=-{self.spec / name}\n", text)
        self.assertIn("3 RUN_ID file(s)", out)
        self.assertNotIn(f"-{self.project / 'config'}\n", text)
        # Only the allowlisted files: never evaluations/ itself, a run's config or other files.
        self.assertNotIn(f"-{self.project / 'evaluations'}\n", text)
        self.assertNotIn(".run_config.json", text)
        self.assertNotIn("secret.json", text)
        self.assertIn("unchanged", self.sync(config)[1])
        # A RUN_ID file rewritten by rename has a new inode; the old bind would pin the stale copy.
        tmp = self.experiment / "RUN_ID_MAP.json.tmp"
        tmp.write_text((self.experiment / "RUN_ID_MAP.json").read_text())
        tmp.replace(self.experiment / "RUN_ID_MAP.json")
        self.assertIn("changed", self.sync(config)[1].replace("unchanged", ""))
        self.assertIn("unchanged", self.sync(config)[1])

    def test_sync_maps_symlinked_run_id_files_and_refuses_escapes(self) -> None:
        config, _ = self.manifest_config()
        inside = self.project / "evaluations/001_link"
        inside.mkdir()
        (inside / "RUN_ID_MAP.json").symlink_to(self.experiment / "RUN_ID_MAP.json")
        (self.project / "evaluations/002_out").symlink_to(Path(self.tmp.name))
        (Path(self.tmp.name) / "RUN_ID_MAP.json").write_text("{}")
        _, _, _, dropin = self.sync(config)
        text = dropin.read_text()
        self.assertIn(f"BindReadOnlyPaths=-{self.experiment / 'RUN_ID_MAP.json'}:{inside / 'RUN_ID_MAP.json'}\n", text)
        self.assertNotIn("002_out", text)

    def test_sync_maps_a_symlinked_plots_and_refuses_escapes_and_unsafe_paths(self) -> None:
        config, _ = self.manifest_config()
        elsewhere = self.root / "volume_plots"
        elsewhere.mkdir()
        linked = self.root / "area" / "linked" / "linked"
        (linked / ".git").mkdir(parents=True)
        (linked / "plots").symlink_to(elsewhere)
        escaping = self.root / "area" / "escape" / "escape"
        (escaping / ".git").mkdir(parents=True)
        (escaping / "plots").symlink_to(Path(self.tmp.name))  # outside every root
        spaced = self.root / "area" / "has space" / "p"
        (spaced / ".git").mkdir(parents=True)
        (spaced / "plots").mkdir()
        _, out, err, dropin = self.sync(config)
        text = dropin.read_text()
        self.assertIn(f"BindReadOnlyPaths=-{elsewhere}:{linked / 'plots'}\n", text)
        self.assertNotIn(str(escaping), text)
        self.assertIn("resolves outside every root", err)
        self.assertNotIn("has space", text)
        self.assertIn("cannot carry safely", err)
        self.assertIn("2 project(s) published", out)

    def test_manifest_mode_serves_only_published_projects(self) -> None:
        config, manifest = self.manifest_config()
        # Before sync nothing is published, even though the checkout is right there.
        self.assertIsNone(plot_server.resolve_plot(config, self.plot.as_posix()))
        self.assertEqual(plot_server.discover_projects(config), [])
        self.sync(config)
        self.assertEqual(plot_server.resolve_plot(config, self.plot.as_posix()), self.plot)
        self.assertEqual(plot_server.discover_projects(config), [self.project])
        # The sandbox hides .git: a published project needs no .git to be served.
        (self.project / ".git").rmdir()
        self.assertEqual(plot_server.resolve_plot(config, self.plot.as_posix()), self.plot)
        # A manifest line outside every root is ignored.
        manifest.write_text("/etc\n" + str(self.project) + "\n")
        self.assertEqual(plot_server.discover_projects(config), [self.project])

    def test_system_config_keeps_the_token_unless_rotated(self) -> None:
        self.write_registry(f'bind = "0.0.0.0"\ntoken = "{TOKEN}"\npublic_url = "http://example.test:40975"\n')
        out = Path(self.tmp.name) / "etc.toml"
        args = plot_server.argparse.Namespace(out=str(out), manifest="/etc/plot-server/projects.txt", new_token=False)
        with redirect_stdout(io.StringIO()):
            plot_server.cmd_system_config(self.config, args)
        first = plot_server.load_config(out)
        self.assertEqual((first.token, first.bind, first.public_url), (TOKEN, "0.0.0.0", "http://example.test:40975"))
        self.assertEqual(first.manifest, Path("/etc/plot-server/projects.txt"))
        self.assertEqual(out.stat().st_mode & 0o777, 0o640)
        args.new_token = True
        with redirect_stdout(io.StringIO()):
            plot_server.cmd_system_config(self.config, args)
        rotated = plot_server.load_config(out).token
        self.assertNotEqual(rotated, TOKEN)
        args.new_token = False
        with redirect_stdout(io.StringIO()):
            plot_server.cmd_system_config(self.config, args)
        self.assertEqual(plot_server.load_config(out).token, rotated)


class LiveServerTest(Fixture):
    """Real HTTP round trips; the remote-client case is simulated by patching is_loopback."""

    def start(self) -> str:
        self.log: list[str] = []
        server = plot_server.ThreadingHTTPServer(
            ("127.0.0.1", 0), plot_server.make_handler(self.config, access_log=self.log.append)
        )
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
        joined = "\n".join(self.log)
        self.assertNotIn(TOKEN, joined)  # the query string never reaches the log
        self.assertRegex(joined, r"127\.0\.0\.1 GET \S+/loss\.html 401 auth=missing")
        self.assertRegex(joined, r"GET \S+/loss\.html 401 auth=REJECTED")
        self.assertRegex(joined, r"GET / 200 auth=token")

    def test_browser_page_and_json_api(self) -> None:
        base = self.start()
        code, body, _ = self.get(base + "/")
        self.assertEqual(code, 200)
        self.assertEqual(body, plot_server.BROWSER_PATH.read_bytes())
        self.assertIn(b"api/files", body)
        self.assertIn(b"/api/runs", body)
        self.assertIn(b"api/experiments", body)
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

    def test_plot_shell_is_served_for_the_browser_to_inject(self) -> None:
        base = self.start()
        code, body, headers = self.get(base + "/plot-shell.js")
        self.assertEqual(code, 200)
        self.assertEqual(body, plot_server.SHELL_PATH.read_bytes())
        self.assertTrue(headers["Content-Type"].startswith("text/javascript"))
        with mock.patch.object(plot_server, "SHELL_PATH", plot_server.SHELL_PATH.with_name("missing.js")):
            self.assertEqual(self.get(base + "/plot-shell.js")[0], 404)

    def runs_url(self, base: str, experiment: str = "000_x", file: str = "MAP", project: Path | None = None) -> str:
        query = urllib.parse.urlencode({"project": (project or self.project).as_posix(), "experiment": experiment, "file": file})
        return f"{base}/api/runs?{query}"

    def test_runs_api_serves_the_run_identity_files(self) -> None:
        base = self.start()
        code, body, headers = self.get(base + "/api/experiments?project=" + urllib.parse.quote(self.project.as_posix()))
        self.assertEqual(code, 200)
        experiments = json.loads(body)["experiments"]
        self.assertEqual([e["path"] for e in experiments], ["000_x"])
        self.assertEqual(sorted(experiments[0]["files"]), ["HISTORY", "MAP", "VERSIONS"])
        self.assertEqual(experiments[0]["files"]["MAP"]["size"], (self.experiment / "RUN_ID_MAP.json").stat().st_size)
        self.assertEqual(self.get(base + "/api/experiments?project=/etc")[0], 404)
        for kind in ("MAP", "VERSIONS", "HISTORY"):
            code, body, headers = self.get(self.runs_url(base, file=kind))
            self.assertEqual(code, 200, kind)
            folder = self.experiment if kind == "MAP" else self.spec
            self.assertEqual(body, (folder / f"RUN_ID_{kind}.json").read_bytes())
            self.assertTrue(headers["Content-Type"].startswith("application/json"))
            self.assertEqual(self.get(self.runs_url(base, file=kind), {"If-None-Match": headers["ETag"]})[0], 304)
        self.assertIn(HASH_A, json.loads(self.get(self.runs_url(base))[1])["by_hash"])
        for url in (
            self.runs_url(base, file="OTHER"),
            self.runs_url(base, file="secret.json"),
            self.runs_url(base, file="../secret"),
            self.runs_url(base, experiment="000_x/" + HASH_A, file=".run_config.json"),
            self.runs_url(base, experiment="000_x/.."),
            self.runs_url(base, experiment="../../plots"),
            self.runs_url(base, experiment="missing"),
            self.runs_url(base, project=Path("/etc")),
            self.runs_url(base, project=self.root),
            base + "/api/runs",
        ):
            self.assertEqual(self.get(url)[0], 404, url)
        # Nothing under evaluations/ or config/ is reachable by its path either.
        for path in (
            self.experiment / "RUN_ID_MAP.json",
            self.spec / "RUN_ID_VERSIONS.json",
            self.experiment / HASH_A / ".run_config.json",
            self.project / "evaluations/secret.json",
        ):
            self.assertEqual(self.get(base + path.as_posix())[0], 404, path)

    def test_runs_api_refuses_a_symlink_escape(self) -> None:
        escape = self.project / "evaluations/001_escape"
        escape.mkdir()
        secret = Path(self.tmp.name) / "RUN_ID_MAP.json"
        secret.write_text('{"secret": true}')
        (escape / "RUN_ID_MAP.json").symlink_to(secret)
        base = self.start()
        self.assertEqual(self.get(self.runs_url(base, experiment="001_escape"))[0], 404)
        body = self.get(base + "/api/experiments?project=" + urllib.parse.quote(self.project.as_posix()))[1]
        self.assertEqual([e["path"] for e in json.loads(body)["experiments"]], ["000_x"])

    def test_page_falls_back_to_the_maps_embedded_identity_copies(self) -> None:
        (self.spec / "RUN_ID_VERSIONS.json").unlink()
        (self.spec / "RUN_ID_HISTORY.json").unlink()
        base = self.start()
        self.assertEqual(self.get(self.runs_url(base, file="VERSIONS"))[0], 404)
        self.assertEqual(self.get(self.runs_url(base, file="HISTORY"))[0], 404)
        self.assertEqual(self.get(self.runs_url(base))[0], 200)
        page = plot_server.BROWSER_PATH.read_text()
        self.assertIn("r.status === 404) return undefined", page)
        self.assertIn('embedded("versions")', page)
        self.assertIn('embedded("history")', page)

    def test_runs_api_needs_the_token_off_this_machine(self) -> None:
        self.write_registry(f'token = "{TOKEN}"\n')
        base = self.start()
        experiments = base + "/api/experiments?project=" + urllib.parse.quote(self.project.as_posix())
        with mock.patch.object(plot_server, "is_loopback", return_value=False):
            self.assertEqual(self.get(self.runs_url(base))[0], 401)
            self.assertEqual(self.get(experiments)[0], 401)
            self.assertEqual(self.get(self.runs_url(base), {"Authorization": f"Bearer {TOKEN}"})[0], 200)
            self.assertEqual(self.get(experiments, {"Authorization": f"Bearer {TOKEN}"})[0], 200)

    def test_a_large_run_id_map_is_gzipped(self) -> None:
        import gzip
        runs = {f"rid-{i:032x}": {"params": {"lr": 0.0001, "seed": i}, "version": 1} for i in range(2000)}
        payload = json.dumps({"layout": "hashed-v2", "version": 1, "by_hash": runs}).encode()
        self.assertGreater(len(payload), plot_server.GZIP_MIN_BYTES)
        (self.experiment / "RUN_ID_MAP.json").write_bytes(payload)
        base = self.start()
        code, body, headers = self.get(self.runs_url(base), {"Accept-Encoding": "gzip"})
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Encoding"], "gzip")
        self.assertEqual(gzip.decompress(body), payload)
        self.assertEqual(
            self.get(self.runs_url(base), {"If-None-Match": headers["ETag"], "Accept-Encoding": "gzip"})[0], 304
        )

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
