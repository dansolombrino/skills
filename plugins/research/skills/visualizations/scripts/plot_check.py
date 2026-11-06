# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright>=1.45"]
# ///
"""Look at a rendered plot the way a reader would: open it in headless Chromium at several window
sizes, measure every piece of text Plotly drew, report what collides or is cut off, and save
screenshots to read.

    uv run <this skill's directory>/scripts/plot_check.py --link <absolute path of the plot> [more ...]

One-time browser install (no root, ~100 MB under ~/.cache/ms-playwright):

    uv run --with playwright python -m playwright install chromium-headless-shell

Options:
    --link                   when no file has an error, print each file's plot-server link
    --widths 1440,1024,768   window widths to check (height 900)
    --state NAME=JS          also check after running JS in the page (repeatable), e.g.
                             --state 'one-run=document.querySelector("[data-plot-controls] .none").click()'
    --out DIR                where screenshots and report.json go (default: a temp dir, printed)
    --json                   print the report as JSON instead of text (not with --link)

Exit status: 0 no errors, 1 at least one error (and no link), 2 the page could not be checked,
3 clean but `plot_server.py url` gave no working link (its message says why). A link from a server
running older code still counts as a link: `url` warns and this exits 0.
Errors are facts: text overlapping text, text cut off by the figure or the window, page scrolling
sideways, a JS exception, a request to anything but the file itself (blocked, so the check sees what
an offline reader sees), and a gap in the page contract (data-plot-leaf, plot-data, plotSelectedRuns,
data-plot-controls, data-plot-provenance, white backgrounds, both assets inlined verbatim). Warnings
need eyes: legend over the plot area, crowded tick labels, text below 10 px, an asset copy this skill
has since replaced. Geometry never proves a plot reads well: always look at the screenshots.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "assets"
OFFLINE_SCHEMES = ("file:", "data:", "blob:", "about:")
WHITE = {"#fff", "#ffffff", "white", "rgb(255,255,255)", "rgba(255,255,255,1)", "transparent", "rgba(0,0,0,0)"}
# Earlier released text of each asset (sha256 of the stripped text): such a copy is out of date, not
# edited, so it is a warning and the plot still passes. tests/test_plot_server.py keeps this complete
# against the git history: replacing an asset means adding its old hash here.
ASSET_HISTORY = {
    "plot-download.js": {"24be66717df7733253df6178d5c0ce344c12665039c03b13a0bb6e475bcd3ae2"},
    "plot-shell.js": set(),
}

CONTRACT_JS = r"""
() => {
  const de = document.documentElement;
  const node = document.getElementById("plot-data");
  let runs = null, dataError = null, unescaped = false;
  if (node) {
    unescaped = node.textContent.includes("<");
    try {
      const data = JSON.parse(node.textContent);
      if (data && data.runs && typeof data.runs === "object" && !Array.isArray(data.runs)) runs = Object.keys(data.runs);
    } catch (e) { dataError = String(e); }
  }
  let selected = null, selectedError = null;
  const hasSelected = typeof window.plotSelectedRuns === "function";
  if (hasSelected) { try { selected = window.plotSelectedRuns(); } catch (e) { selectedError = String(e); } }
  const headScripts = [...document.head.querySelectorAll("script")];
  const assets = [...document.scripts].filter((s) => !s.src).map((s) => {
    const m = /^\s*\/\/ (plot-download|plot-shell):/.exec(s.textContent.slice(0, 200));
    return m ? {name: m[1] + ".js", text: s.textContent, position: headScripts.indexOf(s)} : null;
  }).filter(Boolean);
  const external = [...document.querySelectorAll("script[src],link[href],img[src],iframe[src],embed[src],object[data],source[src],audio[src],video[src]")]
    .map((el) => el.getAttribute("src") || el.getAttribute("href") || el.getAttribute("data"))
    .filter((url) => url && !/^(data:|blob:|#)/i.test(url));
  return {
    leaf: de.getAttribute("data-plot-leaf"),
    hasData: !!node, dataError, unescaped, runs, hasSelected, selected, selectedError,
    controls: !!document.querySelector("[data-plot-controls]"),
    provenance: !!document.querySelector("[data-plot-provenance]"),
    assets, external,
  };
}
"""

BACKGROUNDS_JS = r"""
() => ({
  body: getComputedStyle(document.body).backgroundColor,
  figures: [...document.querySelectorAll(".js-plotly-plot")].map((gd) => {
    const full = gd._fullLayout || {};
    return {paper: full.paper_bgcolor || null, plot: full.plot_bgcolor || null};
  }),
})
"""

MEASURE_JS = r"""
() => {
  const R = (el) => { const r = el.getBoundingClientRect(); return {x: r.left, y: r.top, w: r.width, h: r.height}; };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 0.5 || r.height < 0.5) return false;
    const s = getComputedStyle(el);
    return s.visibility !== "hidden" && s.display !== "none" && parseFloat(s.opacity || "1") > 0.05;
  };
  // The text's own box, rotated as drawn: four screen points (tick labels are often angled).
  const quad = (el) => {
    try {
      const b = el.getBBox(), m = el.getScreenCTM();
      if (!m) return null;
      return [[b.x, b.y], [b.x + b.width, b.y], [b.x + b.width, b.y + b.height], [b.x, b.y + b.height]]
        .map(([x, y]) => [m.a * x + m.c * y + m.e, m.b * x + m.d * y + m.f]);
    } catch (e) { return null; }
  };
  const txt = (el) => (el.textContent || "").replace(/\s+/g, " ").trim();
  const fontPx = (el) => parseFloat(getComputedStyle(el).fontSize) || 0;
  const figures = [];
  document.querySelectorAll(".js-plotly-plot").forEach((gd, fi) => {
    const svg = gd.querySelector(".main-svg");
    if (!svg || !visible(gd)) return;
    const items = [];
    const add = (role, el, group) => {
      if (!visible(el)) return;
      const t = txt(el); if (!t) return;
      items.push({role, group: group || role, text: t.slice(0, 80), font: fontPx(el), box: R(el), quad: quad(el)});
    };
    gd.querySelectorAll(".infolayer .gtitle").forEach((el) => add("title", el));
    gd.querySelectorAll(".infolayer [class*='title'] text, .infolayer text[class*='title']").forEach((el) => {
      const c = el.getAttribute("class") || "";
      if (/^[xy]\d*title$/.test(c)) add("axis-title:" + c, el);
    });
    gd.querySelectorAll(".xtick text, .ytick text, .x2tick text, .y2tick text").forEach((el) => {
      const g = el.closest("g[class$='tick']") || el.parentNode;
      const layer = el.closest("g.xaxislayer-above, g.yaxislayer-above, g[class*='axislayer']");
      const axisId = (layer && layer.parentNode && layer.parentNode.getAttribute("class")) || (g.getAttribute("class") || "tick");
      add("tick", el, "tick:" + axisId + ":" + (g.getAttribute("class") || ""));
    });
    gd.querySelectorAll(".legend .traces text.legendtext").forEach((el) => add("legend-entry", el, "legend"));
    gd.querySelectorAll(".legend .legendtitletext").forEach((el) => add("legend-title", el, "legend"));
    gd.querySelectorAll(".annotation text, .annotation-text-g text").forEach((el) => add("annotation", el));
    gd.querySelectorAll(".colorbar text").forEach((el) => add("colorbar", el, "colorbar"));
    gd.querySelectorAll(".slider-group text, .updatemenu-container text").forEach((el) => add("widget", el, "widget"));
    const plotAreas = [...gd.querySelectorAll(".nsewdrag")].filter(visible).map(R);
    const legendBg = gd.querySelector(".legend .bg");
    // Data marks under the legend: bar/point shapes by box, line paths sampled every ~6 px.
    let hidden = 0;
    if (legendBg && visible(legendBg)) {
      const L = legendBg.getBoundingClientRect();
      const inL = (x, y) => x > L.left && x < L.right && y > L.top && y < L.bottom;
      gd.querySelectorAll(".cartesianlayer .trace .point, .cartesianlayer .trace .points path").forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.width || r.height) if (inL(r.left + r.width / 2, r.top + r.height / 2) || inL(r.left + r.width / 2, r.top)) hidden++;
      });
      gd.querySelectorAll(".cartesianlayer .trace path.js-line").forEach((el) => {
        try {
          const n = el.getTotalLength(), m = el.getScreenCTM(); if (!m) return;
          for (let d = 0; d <= n; d += Math.max(6, n / 3000)) {
            const p = el.getPointAtLength(d);
            if (inL(m.a * p.x + m.c * p.y + m.e, m.b * p.x + m.d * p.y + m.f)) { hidden++; break; }
          }
        } catch (e) {}
      });
    }
    figures.push({
      legendHides: hidden,
      index: fi, id: gd.id || null, box: R(gd), svg: R(svg), items, plotAreas,
      legend: legendBg && visible(legendBg) ? R(legendBg) : null,
      traces: (gd.data || []).length,
      title: txt(gd.querySelector(".gtitle") || document.createElement("i")),
    });
  });
  const de = document.documentElement;
  const htmlIssues = [];
  // HTML text clipped by its own box (ellipsis or overflow hidden).
  document.querySelectorAll("body *:not(svg *):not(script):not(style)").forEach((el) => {
    if (!visible(el) || el.closest(".js-plotly-plot")) return;
    const s = getComputedStyle(el);
    if ((s.overflowX === "hidden" || s.textOverflow === "ellipsis") && el.scrollWidth > el.clientWidth + 1 && txt(el))
      htmlIssues.push({kind: "html-text-clipped", text: txt(el).slice(0, 80), box: R(el)});
  });
  return {
    figures, htmlIssues,
    viewport: {w: innerWidth, h: innerHeight},
    page: {scrollWidth: de.scrollWidth, clientWidth: de.clientWidth, scrollHeight: de.scrollHeight},
  };
}
"""

PAD = 1.0  # px of tolerance before two boxes count as touching


def inter(a, b, pad=PAD):
    w = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]) - pad
    h = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]) - pad
    return max(w, 0) * max(h, 0)


def _quad(i):
    if i.get("quad"):
        return i["quad"]
    b = i["box"]
    return [[b["x"], b["y"]], [b["x"] + b["w"], b["y"]], [b["x"] + b["w"], b["y"] + b["h"]], [b["x"], b["y"] + b["h"]]]


def quads_overlap(a, b, pad=PAD):
    """Separating-axis test on two convex quads; touching within pad px does not count."""
    for poly in (a, b):
        for k in range(4):
            x1, y1 = poly[k]
            x2, y2 = poly[(k + 1) % 4]
            nx, ny = y1 - y2, x2 - x1
            norm = (nx * nx + ny * ny) ** 0.5 or 1.0
            pa = [(x * nx + y * ny) / norm for x, y in a]
            pb = [(x * nx + y * ny) / norm for x, y in b]
            if min(max(pa), max(pb)) - max(min(pa), min(pb)) <= pad:
                return False
    return True


def outside(box, frame, pad=PAD):
    return (box["x"] < frame["x"] - pad or box["y"] < frame["y"] - pad
            or box["x"] + box["w"] > frame["x"] + frame["w"] + pad
            or box["y"] + box["h"] > frame["y"] + frame["h"] + pad)


def analyse(m, label):
    issues = []
    err = lambda kind, msg, **kw: issues.append({"level": "error", "kind": kind, "state": label, "msg": msg, **kw})
    warn = lambda kind, msg, **kw: issues.append({"level": "warning", "kind": kind, "state": label, "msg": msg, **kw})
    vp, page = m["viewport"], m["page"]
    if page["scrollWidth"] > page["clientWidth"] + 1:
        err("page-scrolls-sideways", f"page is {page['scrollWidth']} px wide in a {page['clientWidth']} px window")
    for h in m["htmlIssues"]:
        err(h["kind"], f"HTML text cut off: {h['text']!r}", box=h["box"])
    if not m["figures"]:
        err("no-figure", "no visible Plotly figure on the page")
    window = {"x": 0, "y": -1e9, "w": vp["w"], "h": 2e9}
    for f in m["figures"]:
        fid = f"figure {f['index']}" + (f" (#{f['id']})" if f["id"] else "")
        items = f["items"]
        if not any(i["role"] == "title" for i in items):
            warn("no-title", f"{fid}: no title drawn")
        for i in items:
            if outside(i["box"], f["svg"]):
                err("text-clipped", f"{fid}: {i['role']} {i['text']!r} runs past the figure edge", box=i["box"])
            elif outside(i["box"], window):
                err("text-off-window", f"{fid}: {i['role']} {i['text']!r} runs past the window edge", box=i["box"])
            if 0 < i["font"] < 10:
                warn("small-text", f"{fid}: {i['role']} {i['text']!r} is {i['font']:.0f} px")
        # Text against text, as drawn (rotation included). Tick labels of one axis crowding each
        # other is one warning per axis (Plotly thins them on zoom); anything else is an error.
        crowded, pairs = {}, {}
        for a_i, a in enumerate(items):
            for b in items[a_i + 1:]:
                if a["group"] == b["group"] and a["group"] in ("legend", "colorbar", "widget"):
                    continue
                if not inter(a["box"], b["box"], 0) or not quads_overlap(_quad(a), _quad(b)):
                    continue
                if a["role"] == b["role"] == "tick" and a["group"].split(":")[1] == b["group"].split(":")[1]:
                    c = crowded.setdefault(a["group"].split(":")[1], [0, a["text"], b["text"]])
                    c[0] += 1
                    continue
                ra, rb = (a, b) if a["role"] <= b["role"] else (b, a)
                pairs.setdefault((ra["role"], rb["role"]), []).append((ra["text"], rb["text"]))
        for (ra, rb), hits in pairs.items():
            t1, t2 = hits[0]
            more = f" (+{len(hits) - 1} more {ra}/{rb} pairs)" if len(hits) > 1 else ""
            err("text-overlap", f"{fid}: {ra} {t1!r} overlaps {rb} {t2!r}{more}")
        for axis, (n, t1, t2) in crowded.items():
            warn("tick-crowding", f"{fid}: {n} pairs of {axis} tick labels overlap, e.g. {t1!r} and {t2!r}")
        # Non-tick text drawn over the data area.
        for i in items:
            if i["role"] in ("title", "legend-title") or i["role"].startswith("axis-title"):
                for pa in f["plotAreas"]:
                    if inter(i["box"], pa) > 0.25 * i["box"]["w"] * i["box"]["h"]:
                        err("text-over-data", f"{fid}: {i['role']} {i['text']!r} sits inside the plot area", box=i["box"])
                        break
        if f["legend"] and f["legendHides"]:
            err("legend-hides-data", f"{fid}: the legend is drawn over {f['legendHides']} data marks")
        elif f["legend"]:
            for pa in f["plotAreas"]:
                if inter(f["legend"], pa) > 0:
                    warn("legend-over-data", f"{fid}: the legend covers part of the plot area; check the screenshot that it hides no data")
                    break
            if outside(f["legend"], f["svg"]):
                err("legend-clipped", f"{fid}: the legend runs past the figure edge", box=f["legend"])
        for pa in f["plotAreas"]:
            if pa["h"] < 120 or pa["w"] < 160:
                warn("cramped-plot-area", f"{fid}: plot area is only {pa['w']:.0f}x{pa['h']:.0f} px; margins, legend or title are eating it")
                break
    # De-duplicate identical messages (same collision reported across many ticks).
    seen, out = set(), []
    for i in issues:
        key = (i["level"], i["msg"])
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


def asset_text(name: str) -> str:
    return (ASSETS / name).read_text().strip()


def asset_issues(found: list[dict]) -> list[dict]:
    """Both assets inline, first and second in <head>, and word for word this skill's (or an earlier release's)."""
    issues = []
    add = lambda level, kind, msg: issues.append({"level": level, "kind": kind, "state": "contract", "msg": msg})
    for position, name in enumerate(("plot-download.js", "plot-shell.js")):
        copies = [a for a in found if a["name"] == name]
        if not copies:
            add("error", "asset-missing", f"{name} is not inlined in the page")
            continue
        text = copies[0]["text"].strip()
        if text != asset_text(name):
            if hashlib.sha256(text.encode()).hexdigest() in ASSET_HISTORY[name]:
                add("warning", "asset-outdated", f"the inlined {name} is an earlier release; re-copy this skill's asset into the plotting script")
            else:
                add("error", "asset-not-verbatim", f"the inlined {name} differs from this skill's asset")
        if copies[0].get("position") != position:
            add("warning", "asset-order", f"{name} should be <script> number {position + 1} in <head>")
    return issues


def white_issues(backgrounds: dict, where: str) -> list[dict]:
    issues = []
    flat = lambda value: str(value).replace(" ", "").lower()
    add = lambda what, value: issues.append({"level": "error", "kind": "not-white", "state": "contract", "msg": f"{where}{what} is {value}, not white"})
    if flat(backgrounds.get("body")) not in WHITE:
        add("the page background", backgrounds.get("body"))
    for index, figure in enumerate(backgrounds.get("figures") or []):
        for key, label in (("paper", "paper_bgcolor"), ("plot", "plot_bgcolor")):
            if figure.get(key) is not None and flat(figure[key]) not in WHITE:
                add(f"figure {index}'s {label}", figure[key])
    return issues


def contract_issues(facts: dict) -> list[dict]:
    """The page contract in SKILL.md, from what CONTRACT_JS read off the rendered page."""
    issues = []
    err = lambda kind, msg: issues.append({"level": "error", "kind": kind, "state": "contract", "msg": msg})
    if not facts.get("leaf"):
        err("no-plot-leaf", "<html> has no data-plot-leaf")
    if not facts.get("hasData"):
        err("no-plot-data", 'no <script type="application/json" id="plot-data"> block')
    elif facts.get("dataError"):
        err("plot-data-invalid", f"the plot-data block does not parse: {facts['dataError'][:200]}")
    elif facts.get("runs") is None:
        err("plot-data-invalid", 'the plot-data block has no "runs" object keyed by run_id')
    elif facts.get("unescaped"):
        err("plot-data-unescaped", 'the plot-data block holds a raw "<": escape it as \\u003c')
    if not facts.get("hasSelected"):
        err("no-selected-runs", "window.plotSelectedRuns is not defined")
    elif facts.get("selectedError"):
        err("selected-runs-invalid", f"window.plotSelectedRuns() throws: {facts['selectedError'][:200]}")
    elif not isinstance(facts.get("selected"), list):
        err("selected-runs-invalid", "window.plotSelectedRuns() does not return a list of run_ids")
    elif facts.get("runs") is not None:
        unknown = [run for run in facts["selected"] if run not in facts["runs"]]
        if unknown:
            err("selected-runs-invalid", f"window.plotSelectedRuns() names runs not in plot-data: {unknown[:3]}")
    if not facts.get("controls"):
        err("no-controls-mark", "no element marked data-plot-controls")
    if not facts.get("provenance"):
        err("no-provenance-mark", "no element marked data-plot-provenance")
    for url in dict.fromkeys(facts.get("external") or []):
        err("external-reference", f"the page loads {url[:200]}: everything must be inline")
    return issues + asset_issues(facts.get("assets") or []) + white_issues(facts.get("backgrounds") or {}, "")


def check(page, html: Path, widths, states, outdir: Path):
    report = {"file": str(html), "screenshots": [], "issues": []}
    errors, blocked = [], []

    def offline_only(route):
        url = route.request.url
        if url.startswith(OFFLINE_SCHEMES):
            route.continue_()
        else:
            blocked.append(url)
            route.abort()

    page.route("**/*", offline_only)
    page.context.set_offline(True)  # what routes do not see, such as a WebSocket, fails as it would offline
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and "net::ERR_" not in m.text and errors.append(m.text))
    stem = html.stem
    for w_index, w in enumerate(widths):
        page.set_viewport_size({"width": w, "height": 900})
        page.goto(html.resolve().as_uri(), wait_until="load")
        try:
            page.wait_for_function("() => document.querySelector('.js-plotly-plot .main-svg')", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(600)  # Plotly's automargin redraws once after the first paint
        if w_index == 0:
            facts = page.evaluate(CONTRACT_JS)
            facts["backgrounds"] = page.evaluate(BACKGROUNDS_JS)
            report["issues"] += contract_issues(facts)
            page.emulate_media(color_scheme="dark")
            if not any(i["kind"] == "not-white" for i in report["issues"]):
                report["issues"] += white_issues(page.evaluate(BACKGROUNDS_JS), "in dark mode, ")
            page.emulate_media(color_scheme="no-preference")
        for name, js in [("initial", None)] + states:
            if js:
                page.evaluate(f"() => {{ {js} }}")
                page.wait_for_timeout(600)
            label = f"{w}px/{name}"
            report["issues"] += analyse(page.evaluate(MEASURE_JS), label)
            shot = outdir / f"{stem}.{w}.{name}.png"
            page.screenshot(path=str(shot), full_page=True)
            report["screenshots"].append(str(shot))
            for i, fig in enumerate(page.query_selector_all(".js-plotly-plot")):
                fshot = outdir / f"{stem}.{w}.{name}.fig{i}.png"
                try:
                    fig.screenshot(path=str(fshot))
                    report["screenshots"].append(str(fshot))
                except Exception:
                    pass
    for url in dict.fromkeys(blocked):
        report["issues"].append({"level": "error", "kind": "network-request", "state": "-", "msg": f"blocked a request to {url[:300]}: the file must render offline"})
    for e in dict.fromkeys(errors):
        report["issues"].append({"level": "error", "kind": "js-error", "state": "-", "msg": e[:300]})
    return report


def print_links(files) -> int:
    """Each file's plot-server link, through `plot_server.py url` so the two can't say different things."""
    python = shutil.which("python3") or sys.executable  # the server needs tomllib; this script's uv Python may be older
    worst = 0
    for html in files:
        code = subprocess.run([python, str(Path(__file__).with_name("plot_server.py")), "url", str(html.resolve())], check=False).returncode
        worst = worst if code in (0, 5) else 3  # 5: the link works, the server runs older code; it said so
    return worst


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("html", nargs="+", type=Path)
    ap.add_argument("--widths", default="1440,1024,768")
    ap.add_argument("--state", action="append", default=[], metavar="NAME=JS")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--link", action="store_true", help="when clean, print each file's plot-server link")
    a = ap.parse_args(argv)
    if a.json and a.link:
        ap.error("--link prints links as text; use one or the other")
    widths = [int(x) for x in a.widths.split(",") if x.strip()]
    states = []
    for s in a.state:
        name, _, js = s.partition("=")
        if not js:
            ap.error(f"--state needs NAME=JS, got {s!r}")
        states.append((name.strip().replace("/", "_"), js))
    outdir = a.out or Path(tempfile.mkdtemp(prefix="plot-check-"))
    outdir.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(f"playwright missing: run with `uv run {Path(__file__).resolve()} ...`", file=sys.stderr)
        return 2
    reports = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:
            print(f"cannot start Chromium ({e.__class__.__name__}). Install it once with:\n"
                  "  uv run --with playwright python -m playwright install chromium-headless-shell", file=sys.stderr)
            return 2
        for html in a.html:
            if not html.is_file():
                print(f"no such file: {html}", file=sys.stderr)
                return 2
            ctx = browser.new_context(device_scale_factor=2)
            page = ctx.new_page()
            reports.append(check(page, html, widths, states, outdir))
            ctx.close()
        browser.close()
    (outdir / "report.json").write_text(json.dumps(reports, indent=1))
    n_err = sum(i["level"] == "error" for r in reports for i in r["issues"])
    if a.json:
        print(json.dumps(reports, indent=1))
    else:
        for r in reports:
            print(f"== {r['file']}")
            for i in r["issues"]:
                print(f"  {i['level'].upper():7} [{i['state']}] {i['kind']}: {i['msg']}")
            if not r["issues"]:
                print("  no issues")
        print(f"screenshots and report.json: {outdir}")
        print("Geometry passing is not enough: read the screenshots before handing the plot over.")
    if n_err:
        if a.link:
            print("no links: fix the errors first", file=sys.stderr)
        return 1
    if a.link:
        sys.stdout.flush()
        return print_links(a.html)
    return 0


if __name__ == "__main__":
    sys.exit(main())
