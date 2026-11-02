# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright>=1.45"]
# ///
"""Look at a rendered plot the way a reader would: open it in headless Chromium at several window
sizes, measure every piece of text Plotly drew, report what collides or is cut off, and save
screenshots to read.

    uv run scripts/plot_check.py plots/000_x/plot_loss/loss_curve.html [more.html ...]

One-time browser install (no root, ~100 MB under ~/.cache/ms-playwright):

    uv run --with playwright python -m playwright install chromium-headless-shell

Options:
    --widths 1440,1024,768   window widths to check (height 900)
    --state NAME=JS          also check after running JS in the page (repeatable), e.g.
                             --state 'one-run=document.querySelector("[data-plot-controls] .none").click()'
    --out DIR                where screenshots and report.json go (default: a temp dir, printed)
    --json                   print the report as JSON instead of text

Exit status: 0 no errors, 1 at least one error, 2 the page could not be checked.
Errors are geometric facts (text overlapping text, text cut off by the figure or the window, page
scrolling sideways, a JS exception); warnings need eyes (legend over the plot area, crowded tick
labels, text below 10 px). Geometry never proves a plot reads well: always look at the screenshots.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

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


def check(page, html: Path, widths, states, outdir: Path):
    report = {"file": str(html), "screenshots": [], "issues": []}
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    stem = html.stem
    for w in widths:
        page.set_viewport_size({"width": w, "height": 900})
        page.goto(html.resolve().as_uri(), wait_until="load")
        try:
            page.wait_for_function("() => document.querySelector('.js-plotly-plot .main-svg')", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(600)  # Plotly's automargin redraws once after the first paint
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
    for e in dict.fromkeys(errors):
        report["issues"].append({"level": "error", "kind": "js-error", "state": "-", "msg": e[:300]})
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("html", nargs="+", type=Path)
    ap.add_argument("--widths", default="1440,1024,768")
    ap.add_argument("--state", action="append", default=[], metavar="NAME=JS")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
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
        print("playwright missing: run with `uv run scripts/plot_check.py ...`", file=sys.stderr)
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
                print("  no geometric issues")
        print(f"screenshots and report.json: {outdir}")
        print("Geometry passing is not enough: read the screenshots before handing the plot over.")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
