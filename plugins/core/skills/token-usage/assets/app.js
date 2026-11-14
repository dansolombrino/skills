"use strict";
// Token usage, charted like a stock: the running total as a price line (dotted: the previous
// period's close), or the burn rate per hour as candles with a moving average; volume bars below.
// Requests are relative, so the page works under any path prefix (service-hub). The DOM and SVG
// are built with createElement and textContent only: nothing from the API is parsed as markup.

const NS = "http://www.w3.org/2000/svg";
const KINDS = ["input", "output", "cache_read", "cache_write"];
const TOOLS = ["claude", "omp", "codex"];
const STATUS_BAR_TOOLS = ["claude", "omp"];  // the default: /stats plus omp's own count
const KIND_NAMES = { input: "input", output: "output", cache_read: "cache read", cache_write: "cache write" };
const TOOL_NAMES = { claude: "Claude Code", omp: "omp", codex: "Codex" };
const TOOL_SLOTS = { omp: 1, claude: 2, codex: 7 };
const PALETTE = [1, 2, 7, 4, 5, 3, 6, 8];
const RANGE_NAMES = { "1d": "past 24 hours", "1w": "past 7 days", "1m": "past 30 days", "3m": "past 3 months", ytd: "this year", all: "all time" };
const PERIOD_NAMES = { "1d": "the day before", "1w": "the week before", "1m": "the 30 days before", "3m": "the 3 months before", ytd: "the same span before" };
const INTERVALS = ["1m", "5m", "15m", "30m", "1h", "4h", "6h", "12h", "1D", "1W"];
const CHOICES = { range: Object.keys(RANGE_NAMES), interval: ["auto", ...INTERVALS], chart: ["line", "candles"], metric: ["tokens", "cost"], group: ["tool", "model", "project", "machine"] };
const MA = 5;

const state = { range: "1w", interval: "auto", chart: "line", metric: "tokens", group: "tool", kinds: [...KINDS], tools: [...STATUS_BAR_TOOLS] };
try {
  const saved = JSON.parse(localStorage.getItem("token-usage") || "{}");
  for (const [key, options] of Object.entries(CHOICES)) if (options.includes(saved[key])) state[key] = saved[key];
  if (Array.isArray(saved.kinds) && saved.kinds.length && saved.kinds.every((k) => KINDS.includes(k))) state.kinds = saved.kinds;
  if (Array.isArray(saved.tools) && saved.tools.length && saved.tools.every((k) => TOOLS.includes(k))) state.tools = saved.tools;
} catch (_) {}
const hidden = new Set();
let data = null;

const $ = (id) => document.getElementById(id);
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  if (text !== undefined) node.textContent = String(text);
  return node;
}

const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 2 });
function fmt(x, metric = state.metric) {
  if (metric !== "cost") return compact.format(Math.round(x));
  if (x >= 10000) return "$" + compact.format(x);
  if (x > 0 && x < 0.01) return "<$0.01";
  return "$" + x.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
const label = (name) => (data && data.group === "tool" ? TOOL_NAMES[name] || name : name);
function color(name, index) {
  if (name === "other") return "var(--other)";
  if (data && data.group === "tool" && TOOL_SLOTS[name]) return `var(--series-${TOOL_SLOTS[name]})`;
  return `var(--series-${PALETTE[index % PALETTE.length]})`;
}
function when(ts, withTime = true, withSeconds = false) {
  const opts = { month: "short", day: "numeric" };
  if (withTime) Object.assign(opts, { hour: "2-digit", minute: "2-digit" });
  if (withSeconds) opts.second = "2-digit";
  return new Date(ts * 1000).toLocaleString(undefined, opts);
}
function duration(seconds) {
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  return `${Math.round(seconds / 3600)} h`;
}
// Whether an interval fits a range, as the server decides (tokens.py `fits`): 1 to max_candles candles.
function fits(range, interval) {
  if (interval === "auto" || !data || !data.spans) return true;
  const span = data.spans[range], step = data.intervals[interval];
  return step <= span && Math.ceil((span + 3600) / step) + 1 <= data.max_candles;
}
function niceStep(raw) {
  const p = 10 ** Math.floor(Math.log10(raw || 1));
  const f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}

// ---------------------------------------------------------------------------- model

function model() {
  const visible = data.series.map((s, i) => ({ ...s, index: i })).filter((s) => !hidden.has(s.name));
  const n = data.points, k = Math.max(1, Math.round(data.bucket / data.sub));
  const sums = new Array(n).fill(0);
  for (const s of visible) s.values.forEach((v, i) => { sums[i] += v; });
  const cumulative = [];
  sums.reduce((acc, v, i) => (cumulative[i] = acc + v), 0);
  // The "price" is the burn rate over the trailing candle-length window, so it moves smoothly and each
  // candle opens where the previous one closed; the lead-in gives the first candle its open.
  const lead = new Array(k).fill(0);
  for (const s of visible) (s.lead || []).forEach((v, i) => { lead[i] += v; });
  const full = lead.concat(sums), perHour = 3600 / (k * data.sub);
  const price = [];  // price[i + 1]: the rate at the end of sub i; price[0]: at the start of the range
  let windowSum = lead.reduce((a, b) => a + b, 0);
  price.push(windowSum * perHour);
  for (let i = 0; i < n; i++) { windowSum += full[k + i] - full[i]; price.push(Math.max(0, windowSum) * perHour); }
  // Candles start at the server's edges: local clock boundaries, so a day across a daylight saving
  // change spans 23 or 25 hours and a week starts on Monday; the first one may be partial.
  const edges = data.edges.concat([n]);
  const candles = [];
  for (let j = 0; j + 1 < edges.length; j++) {
    const lo = edges[j], hi = edges[j + 1];
    const path = price.slice(lo, hi + 1);
    const volume = visible.map((s) => s.values.slice(lo, hi).reduce((a, b) => a + b, 0));
    const total = volume.reduce((a, b) => a + b, 0);
    const t0 = data.start + lo * data.sub, t1 = Math.min(data.end, data.start + hi * data.sub);
    // The candle's slot on the time axis: its whole length, even where the range cuts it.
    const s1 = j + 2 < edges.length ? t1 : t0 + data.bucket;
    const s0 = j === 0 && edges.length > 2 ? s1 - data.bucket : t0;
    candles.push({ j, lo, t0, t1, s1, mid: (s0 + s1) / 2,
      o: path[0], c: path[path.length - 1], h: Math.max(...path), l: Math.min(...path),
      volume, total });
  }
  candles.forEach((c, j) => {
    const win = candles.slice(Math.max(0, j - MA + 1), j + 1);
    c.ma = win.reduce((a, b) => a + b.c, 0) / win.length;
  });
  const total = sums.reduce((a, b) => a + b, 0);
  const previous = data.previous_total === null ? null : visible.reduce((a, s) => a + (s.previous || 0), 0);
  // Like TradingView the axis runs to the end of the live candle, so it is drawn whole; time beyond now stays empty.
  const axisEnd = Math.max(data.end, candles.length ? candles[candles.length - 1].s1 : data.end);
  return { visible, sums, cumulative, candles, total, previous, n, k, axisEnd };
}

// ---------------------------------------------------------------------------- chart

function draw() {
  const box = $("chart");
  const W = box.clientWidth, H = box.clientHeight;
  if (!data || !W || !H) return;
  const mdl = model();
  const L = 6, R = 70, T = 10, B = 24, gap = 14;
  const plotW = W - L - R, mainH = Math.round((H - T - B - gap) * 0.74), volH = H - T - B - gap - mainH;
  const volTop = T + mainH + gap;
  const span = mdl.axisEnd - data.start;
  const x = (t) => L + ((t - data.start) / span) * plotW;
  // Every candle is as wide as a full interval (capped) and centred on its slot; the plot clips the
  // first one where the range start cuts it.
  const cw = Math.min(48, Math.max(1, (data.bucket / span) * plotW * 0.66));
  const cxOf = (c) => x(c.mid);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none" });
  const defs = svg("defs");
  const clip = svg("clipPath", { id: "plot" });
  clip.append(svg("rect", { x: L, y: 0, width: plotW, height: H }));
  defs.append(clip);
  root.append(defs);
  const plot = svg("g", { "clip-path": "url(#plot)" });

  const up = mdl.previous === null ? true : mdl.total >= mdl.previous;
  const trend = up ? "var(--up)" : "var(--down)";
  let yMax;
  if (state.chart === "line") yMax = Math.max(mdl.total, mdl.previous || 0);
  else yMax = Math.max(0, ...mdl.candles.map((c) => c.h));
  const step = niceStep((yMax || 1) / 4);
  yMax = Math.max(step, Math.ceil((yMax * 1.04) / step) * step);
  const y = (v) => T + mainH - (v / yMax) * mainH;

  for (let v = 0; v <= yMax + step / 2; v += step) {
    root.append(svg("line", { x1: L, x2: L + plotW, y1: y(v), y2: y(v), class: "grid" }));
    root.append(svg("text", { x: L + plotW + 8, y: y(v) + 4 }, fmt(v) + (state.chart === "candles" ? "/h" : "")));
  }
  xTicks(root, x, T, volTop + volH, mdl.axisEnd);

  if (!mdl.total) {
    root.append(svg("text", { x: L + plotW / 2, y: T + mainH / 2, "text-anchor": "middle", class: "empty" },
      data.index.calls ? "No usage in this range." : "Indexing the session logs…"));
  } else if (state.chart === "line") {
    const grad = svg("linearGradient", { id: "area", x1: 0, x2: 0, y1: 0, y2: 1 });
    grad.append(svg("stop", { offset: "0%", "stop-color": trend, "stop-opacity": 0.28 }),
      svg("stop", { offset: "100%", "stop-color": trend, "stop-opacity": 0.02 }));
    defs.append(grad);
    const pts = [[x(data.start), y(0)]];
    mdl.cumulative.forEach((v, i) => pts.push([x(Math.min(data.end, data.start + (i + 1) * data.sub)), y(v)]));
    const line = pts.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join("");
    root.append(svg("path", { d: `${line}L${pts[pts.length - 1][0].toFixed(1)},${y(0)}L${pts[0][0].toFixed(1)},${y(0)}Z`, fill: "url(#area)" }));
    root.append(svg("path", { d: line, fill: "none", stroke: trend, "stroke-width": 2, "stroke-linejoin": "round" }));
    if (mdl.previous) {
      root.append(svg("line", { x1: L, x2: L + plotW, y1: y(mdl.previous), y2: y(mdl.previous), class: "prev" }));
      root.append(svg("text", { x: L + 4, y: y(mdl.previous) - 5 }, `previous period ${fmt(mdl.previous)}`));
    }
  } else {
    for (const c of mdl.candles) {
      const cx = cxOf(c), col = c.c >= c.o ? "var(--up)" : "var(--down)";
      plot.append(svg("line", { x1: cx, x2: cx, y1: y(c.h), y2: y(c.l), stroke: col, "stroke-width": 1 }));
      const top = y(Math.max(c.o, c.c)), height = Math.max(1, Math.abs(y(c.o) - y(c.c)));
      plot.append(svg("rect", { x: cx - cw / 2, y: top, width: cw, height, fill: col }));
    }
    const ma = mdl.candles.map((c, i) => `${i ? "L" : "M"}${cxOf(c).toFixed(1)},${y(c.ma).toFixed(1)}`).join("");
    if (ma) plot.append(svg("path", { d: ma, fill: "none", stroke: "var(--accent)", "stroke-width": 1.5, opacity: 0.85 }));
  }

  // Volume: what each series spent per candle, stacked.
  const vMax = Math.max(0, ...mdl.candles.map((c) => c.total)) || 1;
  root.append(svg("line", { x1: L, x2: L + plotW, y1: volTop + volH, y2: volTop + volH, class: "grid" }));
  root.append(svg("text", { x: L + plotW + 8, y: volTop + 10 }, fmt(vMax)));
  for (const c of mdl.candles) {
    let base = volTop + volH;
    const cx = cxOf(c);
    c.volume.forEach((v, i) => {
      if (!v) return;
      const h = (v / vMax) * volH;
      base -= h;
      plot.append(svg("rect", { x: cx - cw / 2, y: base, width: cw, height: Math.max(h, 0.5), fill: color(mdl.visible[i].name, mdl.visible[i].index), opacity: 0.85 }));
    });
  }
  root.append(plot);

  const cross = svg("line", { y1: T, y2: volTop + volH, class: "cross", visibility: "hidden" });
  const dot = svg("circle", { r: 4, fill: trend, stroke: "var(--surface-1)", "stroke-width": 2, visibility: "hidden" });
  root.append(cross, dot);
  root.addEventListener("pointermove", (ev) => hover(ev, root, mdl, { x, y, L, plotW, span, cross, dot, W, cxOf }));
  root.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); $("tip").hidden = true; });
  box.replaceChildren(root);
}

// Ticks on local clock boundaries, found by walking the range: correct across daylight saving changes.
function xTicks(root, x, top, bottom, until) {
  const span = data.end - data.start;
  const H = 3600, D = 86400;
  const steps = [H, 3 * H, 6 * H, 12 * H, D, 2 * D, 7 * D, 14 * D, 30 * D, 91 * D];
  const step = steps.find((s) => span / s <= 7) || steps[steps.length - 1];
  const dayNumber = (d) => Math.round(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / (D * 1000));
  const tick = (d) => {
    if (d.getMinutes() !== 0) return false;
    if (step < D) return d.getHours() % (step / H) === 0;
    if (d.getHours() !== 0) return false;
    if (step === 2 * D) return dayNumber(d) % 2 === 0;
    if (step === 7 * D) return d.getDay() === 1;
    if (step === 14 * D) return d.getDay() === 1 && Math.floor(dayNumber(d) / 7) % 2 === 0;
    if (step === 30 * D) return d.getDate() === 1;
    if (step === 91 * D) return d.getDate() === 1 && d.getMonth() % 3 === 0;
    return true;
  };
  const walk = new Date(data.start * 1000).getTimezoneOffset() % 60 ? 900 : H;  // half-hour zones
  for (let t = Math.ceil(data.start / walk) * walk; t <= until; t += walk) {
    const d = new Date(t * 1000);
    if (!tick(d)) continue;
    const px = x(t);
    root.append(svg("line", { x1: px, x2: px, y1: top, y2: bottom, class: "grid" }));
    const text = step < D ? (d.getHours() === 0 ? when(t, false) : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })) : when(t, false);
    const anchor = px - x(data.start) < 24 ? "start" : "middle";  // a tick at the range start would be cut off
    root.append(svg("text", { x: px, y: bottom + 16, "text-anchor": anchor }, text));
  }
}

function hover(ev, root, mdl, g) {
  const rect = root.getBoundingClientRect();
  const px = ((ev.clientX - rect.left) / rect.width) * g.W;
  const t = data.start + ((px - g.L) / g.plotW) * g.span;
  if (t < data.start || t > data.end || !mdl.total) return;
  const tip = $("tip");
  const rows = [];
  let cx, head, sub, items;
  if (state.chart === "line") {
    const i = Math.min(mdl.n - 1, Math.max(0, Math.floor((t - data.start) / data.sub)));
    const t1 = Math.min(data.end, data.start + (i + 1) * data.sub);
    cx = g.x(t1);
    g.dot.setAttribute("cx", cx); g.dot.setAttribute("cy", g.y(mdl.cumulative[i])); g.dot.setAttribute("visibility", "visible");
    head = when(t1, true, data.sub < 60);
    rows.push(["Running total", fmt(mdl.cumulative[i])], [`In these ${duration(data.sub)}`, fmt(mdl.sums[i])]);
    items = mdl.visible.map((s) => [s, s.values.slice(0, i + 1).reduce((a, b) => a + b, 0)]);
    sub = "Running total by series";
  } else {
    const i = Math.floor((t - data.start) / data.sub);
    let lo = 0, hi = mdl.candles.length - 1;  // the last candle starting at or before sample i
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (mdl.candles[mid].lo <= i) lo = mid; else hi = mid - 1; }
    const c = mdl.candles[lo];
    cx = g.cxOf(c);
    g.dot.setAttribute("visibility", "hidden");
    head = `${when(c.t0)} – ${when(c.t1)}`;
    rows.push(["Open", fmt(c.o) + "/h"], ["High", fmt(c.h) + "/h"], ["Low", fmt(c.l) + "/h"], ["Close", fmt(c.c) + "/h"],
      [`Moving average (${MA})`, fmt(c.ma) + "/h"], ["Volume", fmt(c.total)]);
    items = mdl.visible.map((s, i) => [s, c.volume[i]]);
    sub = "Volume by series";
  }
  g.cross.setAttribute("x1", cx); g.cross.setAttribute("x2", cx); g.cross.setAttribute("visibility", "visible");
  const nodes = [el("div", "when", head)];
  for (const [k, v] of rows) { const r = el("div", "row"); r.append(el("span", "k", k), el("span", null, v)); nodes.push(r); }
  const shown = items.filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  if (shown.length > 1) {
    nodes.push(el("div", "when", sub));
    for (const [s, v] of shown) {
      const r = el("div", "row"), k = el("span", "k"), d = el("span", "dot");
      d.style.background = color(s.name, s.index);
      k.append(d, el("span", null, label(s.name)));
      r.append(k, el("span", null, fmt(v)));
      nodes.push(r);
    }
  }
  tip.replaceChildren(...nodes);
  tip.hidden = false;
  const card = tip.parentElement.getBoundingClientRect();
  const left = ev.clientX - card.left;
  tip.style.left = `${left + 16 + tip.offsetWidth > card.width ? left - 16 - tip.offsetWidth : left + 16}px`;
  tip.style.top = "14px";
}

// ---------------------------------------------------------------------------- header and legend

function render() {
  if (!data) return;
  const mdl = model();
  document.title = `${fmt(mdl.total)} · ${data.title}`;
  $("eyebrow").textContent = `${data.metric === "cost" ? "Estimated cost" : "Tokens"} · ${RANGE_NAMES[data.range]} · ${data.interval} bars`;
  $("total").textContent = fmt(mdl.total);
  const change = $("change");
  if (mdl.previous) {
    const diff = mdl.total - mdl.previous, pct = (diff / mdl.previous) * 100;
    change.className = `chg ${diff >= 0 ? "up" : "down"}`;
    change.textContent = `${diff >= 0 ? "▲" : "▼"} ${fmt(Math.abs(diff))} (${Math.abs(pct).toFixed(1)}%) vs ${PERIOD_NAMES[data.range]}`;
  } else {
    change.className = "chg";
    change.textContent = "";
  }
  const kinds = data.kinds.length === KINDS.length ? "all tokens" : data.kinds.map((k) => KIND_NAMES[k]).join(" + ");
  const agents = data.tools.map((t) => TOOL_NAMES[t] || t);
  const agentText = agents.length > 1 ? `${agents.slice(0, -1).join(", ")} and ${agents[agents.length - 1]}` : agents[0];
  const nm = data.index.machines.length;
  const same = data.tools.length === STATUS_BAR_TOOLS.length && STATUS_BAR_TOOLS.every((t) => data.tools.includes(t)) && data.kinds.length === KINDS.length;
  $("summary").textContent = `${agentText} on ${nm} machine${nm === 1 ? "" : "s"} · counting ${kinds}${same ? ", as /stats + omp count" : ""} · ${state.chart === "line" ? "running total" : "burn rate per hour over the trailing candle, MA " + MA}`;

  const body = $("legend").querySelector("tbody");
  const all = data.series.reduce((a, s) => a + (hidden.has(s.name) ? 0 : s.total), 0) || 1;
  const maxShare = Math.max(0, ...data.series.map((s) => (hidden.has(s.name) ? 0 : s.total / all)));
  body.replaceChildren(...data.series.map((s, i) => {
    const off = hidden.has(s.name);
    const tr = el("tr", off ? "off" : "");
    tr.title = off ? "Show this series" : "Hide this series";
    const who = el("td"), w = el("span", "who"), dot = el("span", "dot");
    dot.style.background = color(s.name, i);
    w.append(dot, el("span", null, label(s.name))); who.append(w);
    const share = off ? 0 : s.total / all;
    const cell = el("td", "bar-col"), wrap = el("div", "share"), bar = el("div", "bar"), fill = el("span");
    fill.style.width = `${maxShare ? (100 * share) / maxShare : 0}%`;
    fill.style.background = color(s.name, i);
    bar.append(fill); wrap.append(bar, el("span", "pct", `${(share * 100).toFixed(1)}%`)); cell.append(wrap);
    let vs = "–", cls = "num";
    if (s.previous) {
      const pct = ((s.total - s.previous) / s.previous) * 100;
      vs = `${pct >= 0 ? "▲" : "▼"} ${Math.abs(pct).toFixed(1)}%`;
      cls += pct >= 0 ? " up" : " down";
    } else if (s.previous === 0 && s.total) {
      vs = "new";
    }
    tr.append(who, el("td", "num", fmt(s.total)), cell, el("td", cls, vs));
    tr.addEventListener("click", () => { if (off) hidden.delete(s.name); else hidden.add(s.name); render(); });
    return tr;
  }));

  const idx = data.index;
  let foot = idx.calls ? `${idx.calls.toLocaleString()} rows since ${when(idx.first, false)}. ` : "Nothing indexed yet. ";
  foot += "Machines: " + idx.machines.map((m) => {
    const state = m.error ? `failed (${m.error}), last good ${m.ok ? when(m.ok) : "never"}` : m.ok ? `updated ${when(m.ok)}` : "not reached yet";
    return `${m.name} ${state}`;
  }).join("; ") + ".";
  foot += " Claude Code is counted like /stats: every transcript line, plus the totals /stats kept for days whose transcripts are gone (\"(Claude Code history)\").";
  if (data.bucket < 86400) foot += " Those history totals are one row per day at noon UTC, so bars under a day show them as a spike at that hour.";
  foot += " Only timestamps, models, working directories and token counts are kept.";
  if (data.metric === "cost") {
    foot += " Cost is an estimate: the per-token prices omp records for each model, applied to every agent's calls of that model.";
    if (data.uncosted_tokens) foot += ` ${fmt(data.uncosted_tokens, "tokens")} tokens of models with no known price are left out.`;
  }
  $("foot").textContent = foot;
  draw();
}

// ---------------------------------------------------------------------------- state

let requests = 0;
async function refresh() {
  const query = new URLSearchParams({ range: state.range, interval: state.interval, group: state.group, metric: state.metric, kinds: state.kinds.join(","), tools: state.tools.join(",") });
  const seq = ++requests;  // a slower answer to an older choice never overwrites a newer one
  try {
    const response = await fetch(`api/series?${query}`, { credentials: "same-origin", cache: "no-store" });
    if (seq !== requests) return;
    if (response.status === 400 && state.interval !== "auto") {  // the interval no longer fits (say, All grew)
      state.interval = "auto"; sync();
      return refresh();
    }
    if (!response.ok) throw new Error(String(response.status));
    data = await response.json();
    if (seq !== requests) return;
    render();
    sync();
  } catch (_) {
    if (seq === requests) $("summary").textContent = "Could not load the usage.";
  }
}

function sync() {
  try { localStorage.setItem("token-usage", JSON.stringify(state)); } catch (_) {}
  for (const key of ["range", "interval", "chart", "metric", "group"]) {
    for (const b of document.querySelectorAll(`[data-${key}]`)) b.setAttribute("aria-pressed", String(b.dataset[key] === state[key]));
  }
  for (const b of document.querySelectorAll("[data-interval]")) {
    const ok = fits(state.range, b.dataset.interval);
    b.disabled = !ok;
    b.title = ok ? "" : `Too ${data && data.intervals[b.dataset.interval] > data.spans[state.range] ? "long" : "short"} for this range`;
  }
  $("auto-interval").textContent = state.interval === "auto" && data && data.range === state.range ? `Auto · ${data.interval}` : "Auto";
  for (const b of document.querySelectorAll("[data-kind]")) b.setAttribute("aria-pressed", String(state.kinds.includes(b.dataset.kind)));
  for (const b of document.querySelectorAll("[data-tool]")) b.setAttribute("aria-pressed", String(state.tools.includes(b.dataset.tool)));
}

document.addEventListener("DOMContentLoaded", () => {
  for (const key of ["range", "interval", "metric", "group"]) {
    for (const b of document.querySelectorAll(`[data-${key}]`)) {
      b.addEventListener("click", () => {
        if (key === "group" && state.group !== b.dataset.group) hidden.clear();
        state[key] = b.dataset[key];
        if (key === "range" && !fits(state.range, state.interval)) state.interval = "auto";  // as TradingView does
        sync(); refresh();
      });
    }
  }
  for (const b of document.querySelectorAll("[data-chart]")) b.addEventListener("click", () => { state.chart = b.dataset.chart; sync(); render(); });
  for (const b of document.querySelectorAll("[data-kind]")) {
    b.addEventListener("click", () => {
      const kinds = state.kinds.includes(b.dataset.kind) ? state.kinds.filter((k) => k !== b.dataset.kind) : [...state.kinds, b.dataset.kind];
      if (!kinds.length) return;  // at least one kind is always counted
      state.kinds = KINDS.filter((k) => kinds.includes(k)); sync(); refresh();
    });
  }
  for (const b of document.querySelectorAll("[data-tool]")) {
    b.addEventListener("click", () => {
      const tools = state.tools.includes(b.dataset.tool) ? state.tools.filter((k) => k !== b.dataset.tool) : [...state.tools, b.dataset.tool];
      if (!tools.length) return;  // at least one agent is always counted
      state.tools = TOOLS.filter((k) => tools.includes(k)); sync(); refresh();
    });
  }
  sync();
  refresh();
  new ResizeObserver(() => draw()).observe($("chart"));
  setInterval(refresh, 60 * 1000);
});
