"""behemoth-gpu-niceness: parsing drops everything but users, the accounting, and the read-only web contract."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/core/skills/behemoth-gpu-niceness/scripts/niceness.py"


def load():
    spec = importlib.util.spec_from_file_location("niceness", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


n = load()

PROBE = """0, GPU-a, 100, 50000, 97887
1, GPU-b, 0, 40000, 97887
2, GPU-c, 50, 30000, 97887
3, GPU-d, 0, 0, 97887
4, GPU-e, 0, 20000, 97887
__APPS__
GPU-a, 11, 30000
GPU-a, 12, 10000
GPU-b, 21, 40000
GPU-c, 31, 15000
GPU-c, 32, [N/A]
GPU-zzz, 99, 5
__OWNERS__
     11 alice
     12 alice
     21 bob
     31 alice
     32 carol
"""


class ParseTests(unittest.TestCase):
    def test_only_users_and_numbers_survive(self) -> None:
        gpus = n.parse_probe(PROBE)
        self.assertEqual([g.index for g in gpus], [0, 1, 2, 3, 4])
        self.assertEqual(gpus[0].holders, {"alice": 40000})
        self.assertEqual(gpus[1].holders, {"bob": 40000})
        self.assertEqual(gpus[2].holders, {"alice": 15000, "carol": 0})
        self.assertEqual(gpus[3].holders, {})
        self.assertEqual(gpus[4].holders, {n.UNATTRIBUTED: 20000})  # memory with no visible process
        self.assertEqual(n.Gpu.__dataclass_fields__.keys(), {"index", "util", "mem_used", "mem_total", "holders"})

    def test_failures(self) -> None:
        for bad in ("__NVSMI_FAILED__\n__APPS__\n__OWNERS__\n", "garbage", "__APPS__\n__OWNERS__\n"):
            with self.subTest(bad), self.assertRaises(n.NicenessError):
                n.parse_probe(bad)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.registry = root / "machines.toml"
        self.registry.write_text(f'[machines.box]\nssh = "box"\n[niceness]\nmachine = "box"\nevery = 60\ndb = "{root}/s.sqlite"\n')
        self.config = n.load_config(self.registry)
        self.db = n.connect(self.config.db)
        self.addCleanup(self.db.close)

    def gpu(self, index, util, holders):
        return n.Gpu(index, util, sum(holders.values()), 1000, dict(holders))

    def test_ranking_by_gpu_time(self) -> None:
        t = 1_000_000_000
        for k in range(60):  # one hour, one sample a minute
            n.record(self.db, self.config, t + 60 * k, [
                self.gpu(0, 100, {"alice": 300, "bob": 100}),   # shared 3:1 by memory
                self.gpu(1, 0, {"bob": 500}),
                self.gpu(2, 0, {}),                              # unused
                self.gpu(3, 0, {n.UNATTRIBUTED: 2000}),
            ])
        data = n.stats(self.db, self.config, "24h", now=t + 3600)
        self.assertEqual([u["user"] for u in data["users"]], ["bob", "alice", n.UNATTRIBUTED])  # unattributed last
        users = {u["user"]: u for u in data["users"]}
        self.assertAlmostEqual(users["bob"]["gpu_h"], 1.25, places=2)
        self.assertAlmostEqual(users["alice"]["gpu_h"], 0.75, places=2)
        self.assertAlmostEqual(users["bob"]["share"], 1.25 / 4, places=3)
        self.assertEqual((data["gpus"], data["capacity_gpu_h"], data["used_gpu_h"], data["unused_gpu_h"]), (4, 4.0, 3.0, 1.0))
        self.assertEqual((users["alice"]["slot"], users["bob"]["slot"]), (0, 1))
        self.assertEqual(set(users["bob"]), {"user", "gpu_h", "share", "slot"})
        self.assertEqual(n.stats(self.db, self.config, "24h", now=t + 3 * 86400)["users"], [])

    def test_gaps_are_capped_and_failures_credit_nothing(self) -> None:
        t = 2_000_000_000
        n.record(self.db, self.config, t, [self.gpu(0, 100, {"a": 1})])
        n.record(self.db, self.config, t + 60, None, "ssh timed out")
        n.record(self.db, self.config, t + 3600, [self.gpu(0, 100, {"a": 1})])
        dts = [row[0] for row in self.db.execute("SELECT dt FROM probes ORDER BY ts")]
        self.assertEqual(dts, [60.0, 0.0, 180.0])
        self.assertEqual(n.stats(self.db, self.config, "24h", now=t + 3600)["sampled"]["hours"], 0.07)

    def test_retention(self) -> None:
        config = n.Config(**{**self.config.__dict__, "retention_days": 1})
        n.record(self.db, config, 100, [self.gpu(0, 1, {"a": 1})])
        n.record(self.db, config, 100 + 2 * 86400, [self.gpu(0, 1, {"a": 1})])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM holds").fetchone()[0], 1)

    def test_config_rejections(self) -> None:
        base = '[machines.box]\nssh = "box"\n'
        for why, text in {
            "missing table": base,
            "network bind": base + '[niceness]\nmachine = "box"\nbind = "0.0.0.0"\n',
            "unknown machine": base + '[niceness]\nmachine = "nope"\n',
            "too fast": base + '[niceness]\nmachine = "box"\nevery = 1\n',
        }.items():
            self.registry.write_text(text)
            with self.subTest(why), self.assertRaises(n.NicenessError):
                n.load_config(self.registry)


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        registry = root / "machines.toml"
        registry.write_text(f'[machines.box]\nssh = "box"\n[niceness]\nmachine = "box"\ndb = "{root}/s.sqlite"\n')
        cls.config = n.load_config(registry)
        db = n.connect(cls.config.db)
        n.record(db, cls.config, 1_700_000_000, [n.Gpu(0, 50, 10, 100, {"alice": 10})])
        db.close()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), n.make_handler(cls.config))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path: str, method: str = "GET"):
        request = urllib.request.Request(self.base + path, method=method, data=b"x" if method == "POST" else None)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as err:
            return err.code, dict(err.headers), err.read()

    def test_pages_and_api_carry_security_headers(self) -> None:
        for path in ("/", "/app.js", "/app.css", "/api/stats?window=all", "/healthz"):
            code, headers, _ = self.get(path)
            self.assertEqual(code, 200, path)
            self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
            self.assertIn("script-src 'self';", headers["Content-Security-Policy"])  # never 'unsafe-inline' for scripts
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        data = json.loads(self.get("/api/stats?window=all")[2])
        self.assertEqual(data["users"][0]["user"], "alice")

    def test_read_only_and_nothing_else_served(self) -> None:
        self.assertEqual(self.get("/api/stats", method="POST")[0], 405)
        self.assertEqual(self.get("/api/stats?window=1y")[0], 400)
        for path in ("/../scripts/niceness.py", "/index.html/..", "/s.sqlite", "/api/raw", "/api/now"):
            self.assertEqual(self.get(path)[0], 404, path)

    def test_page_uses_relative_urls_and_no_html_parsing(self) -> None:
        assets = SCRIPT.parents[1] / "assets"
        js = (assets / "app.js").read_text()
        html = (assets / "index.html").read_text()
        self.assertNotIn("innerHTML", js)
        self.assertNotIn('fetch("/', js)
        self.assertNotIn('src="/', html)
        self.assertNotIn('href="/', html)
        self.assertNotIn("http", html.replace("http-equiv", ""))


if __name__ == "__main__":
    unittest.main()
