"use strict";
// Token usage, charted like a stock: the running total as a price line (dotted: the previous
// period's close), or the burn rate per hour as candles with a moving average; volume bars below.
// Zoom (wheel, pinch) and pan (drag) like TradingView: the page asks the server for the visible
// window at a resolution bounded by the plot's pixel width, and draws on canvas in a few batched
// paths, so a redraw stays cheap at any zoom. Requests are relative, so the page works under any
// path prefix (service-hub). Nothing from the API is parsed as markup: the DOM is built with
// createElement and textContent, the chart is drawn on canvas.

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
const HOUR = 3600, DAY = 86400;
const MIN_SPAN = 600;  // the deepest zoom: ten minutes
const PAD = { L: 6, R: 70, T: 10, B: 24, gap: 14 };

const state = { range: "1w", interval: "auto", chart: "line", metric: "tokens", group: "tool", kinds: [...KINDS], tools: [...STATUS_BAR_TOOLS] };
try {
  const saved = JSON.parse(localStorage.getItem("token-usage") || "{}");
  for (const [key, options] of Object.entries(CHOICES)) if (options.includes(saved[key])) state[key] = saved[key];
  if (Array.isArray(saved.kinds) && saved.kinds.length && saved.kinds.every((k) => KINDS.includes(k))) state.kinds = saved.kinds;
  if (Array.isArray(saved.tools) && saved.tools.length && saved.tools.every((k) => TOOLS.includes(k))) state.tools = saved.tools;
} catch (_) {}
const hidden = new Set();
let data = null;
// The zoomed window: null is the range itself; to === null keeps the right edge at now (live).
let view = null;
// What the canvas shows: [from, to] seconds. Set by zooming and panning before the data for that
// window arrives; null means the window of the data in hand.
let domain = null;
const chart = { main: null, over: null, W: 0, H: 0, dpr: 0, geom: null, mdl: null };

const $ = (id) => document.getElementById(id);
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
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
function niceStep(raw) {
  const p = 10 ** Math.floor(Math.log10(raw || 1));
  const f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}
const nowSec = () => Date.now() / 1000;
const plotWidth = () => Math.max(200, Math.round(($("chart").clientWidth || 1000) - PAD.L - PAD.R));
// Whether an interval fits a window, as the server decides (tokens.py `fits`): 1 to max_bars candles.
function fits(span, interval) {
  if (interval === "auto" || !data) return true;
  const step = data.intervals[interval];
  return step <= span && Math.ceil(span / step) + 1 <= data.max_bars;
}
function shownSpan() {
  const [a, b] = domain || (data ? [data.start, data.end] : [0, 0]);
  return b - a;
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
  const price = [];  // price[i + 1]: the rate at the end of sample i; price[0]: at the start
  let windowSum = lead.reduce((a, b) => a + b, 0);
  price.push(windowSum * perHour);
  for (let i = 0; i < n; i++) { windowSum += full[k + i] - full[i]; price.push(Math.max(0, windowSum) * perHour); }
  // Candles start at the server's edges: local clock boundaries, so a day across a daylight saving
  // change spans 23 or 25 hours and a week starts on Monday; the first one may be partial.
  const edges = data.edges.concat([n]);
  const candles = [];
  for (let j = 0; j + 1 < edges.length; j++) {
    const lo = edges[j], hi = edges[j + 1];
    let o = price[lo], c = price[hi], h = -Infinity, l = Infinity;
    for (let i = lo; i <= hi; i++) { if (price[i] > h) h = price[i]; if (price[i] < l) l = price[i]; }
    const volume = visible.map((s) => { let v = 0; for (let i = lo; i < hi; i++) v += s.values[i]; return v; });
    const total = volume.reduce((a, b) => a + b, 0);
    const t0 = data.start + lo * data.sub, t1 = Math.min(data.end, data.start + hi * data.sub);
    // The candle's slot on the time axis: its whole length, even where the window cuts it.
    const s1 = j + 2 < edges.length ? t1 : t0 + data.bucket;
    const s0 = j === 0 && edges.length > 2 ? s1 - data.bucket : t0;
    candles.push({ j, lo, t0, t1, s0, s1, mid: (s0 + s1) / 2, o, c, h, l, volume, total });
  }
  candles.forEach((c, j) => {
    const win = candles.slice(Math.max(0, j - MA + 1), j + 1);
    c.ma = win.reduce((a, b) => a + b.c, 0) / win.length;
  });
  const total = sums.reduce((a, b) => a + b, 0);
  const previous = data.previous_total === null ? null : visible.reduce((a, s) => a + (s.previous || 0), 0);
  // Live, the axis runs to the end of the current candle, as on TradingView, so it is drawn whole.
  const axisEnd = data.live && candles.length ? Math.max(data.end, candles[candles.length - 1].s1) : data.end;
  return { visible, sums, cumulative, candles, total, previous, n, k, axisEnd };
}

// ---------------------------------------------------------------------------- chart

function sizeCanvas() {
  const box = $("chart");
  const W = box.clientWidth, H = box.clientHeight, dpr = window.devicePixelRatio || 1;
  if (W === chart.W && H === chart.H && dpr === chart.dpr) return;
  for (const c of [chart.main, chart.over]) {
    c.width = Math.round(W * dpr); c.height = Math.round(H * dpr);
    c.style.width = `${W}px`; c.style.height = `${H}px`;
  }
  Object.assign(chart, { W, H, dpr });
}

function draw() {
  if (!data || !chart.main) return;
  sizeCanvas();
  const { W, H, dpr } = chart;
  if (!W || !H) return;
  const mdl = chart.mdl = model();
  const [x0, x1] = domain || [data.start, mdl.axisEnd];
  const { L, R, T, B, gap } = PAD;
  const plotW = W - L - R, mainH = Math.round((H - T - B - gap) * 0.74), volH = H - T - B - gap - mainH;
  const volTop = T + mainH + gap, span = x1 - x0;
  const x = (t) => L + ((t - x0) / span) * plotW;
  const css = getComputedStyle(document.documentElement);
  const paint = (c) => (c.startsWith("var(") ? css.getPropertyValue(c.slice(4, -1)).trim() : c);
  const ctx = chart.main.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.font = `11px ${getComputedStyle(document.body).fontFamily}`;
  ctx.textBaseline = "alphabetic";
  const muted = paint("var(--text-muted)"), grid = paint("var(--grid)");

  // Only candles in the window count for the scales and get drawn.
  const shown = mdl.candles.filter((c) => c.s1 >= x0 && c.s0 <= x1);
  const up = mdl.previous === null ? true : mdl.total >= mdl.previous;
  const trend = paint(up ? "var(--up)" : "var(--down)");
  let yMax = state.chart === "line" ? Math.max(mdl.total, mdl.previous || 0) : Math.max(0, ...shown.map((c) => c.h));
  const step = niceStep((yMax || 1) / 4);
  yMax = Math.max(step, Math.ceil((yMax * 1.04) / step) * step);
  const y = (v) => T + mainH - (v / yMax) * mainH;
  chart.geom = { x0, x1, span, x, y, L, plotW, T, bottom: volTop + volH, trend };

  ctx.strokeStyle = grid; ctx.lineWidth = 1; ctx.fillStyle = muted; ctx.textAlign = "left";
  ctx.beginPath();
  for (let v = 0; v <= yMax + step / 2; v += step) {
    const py = Math.round(y(v)) + 0.5;
    ctx.moveTo(L, py); ctx.lineTo(L + plotW, py);
    ctx.fillText(fmt(v) + (state.chart === "candles" ? "/h" : ""), L + plotW + 8, y(v) + 4);
  }
  const vBase = Math.round(volTop + volH) + 0.5;
  ctx.moveTo(L, vBase); ctx.lineTo(L + plotW, vBase);
  ctx.stroke();
  xTicks(ctx, x, x0, x1, T, volTop + volH, grid, muted);

  ctx.save();
  ctx.beginPath(); ctx.rect(L, 0, plotW, H); ctx.clip();  // a candle the window cuts is cut here
  const cw = Math.min(48, Math.max(1, (data.bucket / span) * plotW * 0.66));
  if (!mdl.total) {
    ctx.fillStyle = muted; ctx.textAlign = "center"; ctx.font = `14px ${getComputedStyle(document.body).fontFamily}`;
    ctx.fillText(data.index.calls ? "No usage in this window." : "Indexing the session logs…", L + plotW / 2, T + mainH / 2);
  } else if (state.chart === "line") {
    // The running total only rises, so a pixel column's first and last points draw it exactly (M4);
    // the server samples at least twice per column, and the rest are dropped here.
    const pts = [[x(data.start), y(0)]];
    let column = Math.floor(pts[0][0]), pending = null;
    for (let i = 0; i < mdl.n; i++) {
      const t = Math.min(data.end, data.start + (i + 1) * data.sub);
      if (t < x0 - data.sub) { pts[0] = [x(t), y(mdl.cumulative[i])]; column = Math.floor(pts[0][0]); continue; }
      const px = x(t), py = y(mdl.cumulative[i]), c = Math.floor(px);
      if (c !== column) { if (pending) pts.push(pending); pts.push([px, py]); column = c; pending = null; } else pending = [px, py];
      if (t > x1 + data.sub) break;
    }
    if (pending) pts.push(pending);
    const line = new Path2D();
    pts.forEach(([px, py], i) => (i ? line.lineTo(px, py) : line.moveTo(px, py)));
    const area = new Path2D(line);
    area.lineTo(pts[pts.length - 1][0], y(0)); area.lineTo(pts[0][0], y(0)); area.closePath();
    const grad = ctx.createLinearGradient(0, T, 0, T + mainH);
    grad.addColorStop(0, withAlpha(trend, 0.28)); grad.addColorStop(1, withAlpha(trend, 0.02));
    ctx.fillStyle = grad; ctx.fill(area);
    ctx.strokeStyle = trend; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.stroke(line);
    if (mdl.previous) {
      const py = Math.round(y(mdl.previous)) + 0.5;
      ctx.setLineDash([2, 4]); ctx.strokeStyle = muted; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(L, py); ctx.lineTo(L + plotW, py); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = muted; ctx.textAlign = "left";
      ctx.fillText(`previous period ${fmt(mdl.previous)}`, L + 4, py - 5);
    }
  } else {
    // One path per colour: a few fill and stroke calls, however many candles.
    const bodies = { up: new Path2D(), down: new Path2D() }, wicks = { up: new Path2D(), down: new Path2D() };
    for (const c of shown) {
      const cx = x(c.mid), side = c.c >= c.o ? "up" : "down", wx = Math.round(cx) + 0.5;
      wicks[side].moveTo(wx, y(c.h)); wicks[side].lineTo(wx, y(c.l));
      const top = y(Math.max(c.o, c.c));
      bodies[side].rect(cx - cw / 2, top, cw, Math.max(1, Math.abs(y(c.o) - y(c.c))));
    }
    ctx.lineWidth = 1;
    for (const side of ["up", "down"]) {
      ctx.strokeStyle = ctx.fillStyle = paint(`var(--${side})`);
      ctx.stroke(wicks[side]); ctx.fill(bodies[side]);
    }
    const ma = new Path2D();
    shown.forEach((c, i) => (i ? ma.lineTo(x(c.mid), y(c.ma)) : ma.moveTo(x(c.mid), y(c.ma))));
    ctx.strokeStyle = paint("var(--accent)"); ctx.globalAlpha = 0.85; ctx.lineWidth = 1.5; ctx.stroke(ma); ctx.globalAlpha = 1;
  }

  // Volume: what each series spent per candle, stacked; one path per series.
  const vMax = Math.max(0, ...shown.map((c) => c.total)) || 1;
  const paths = mdl.visible.map(() => new Path2D());
  for (const c of shown) {
    let base = volTop + volH;
    const left = x(c.mid) - cw / 2;
    c.volume.forEach((v, i) => {
      if (!v) return;
      const h = (v / vMax) * volH;
      base -= h;
      paths[i].rect(left, base, cw, Math.max(h, 0.5));
    });
  }
  ctx.globalAlpha = 0.85;
  paths.forEach((p, i) => { ctx.fillStyle = paint(color(mdl.visible[i].name, mdl.visible[i].index)); ctx.fill(p); });
  ctx.globalAlpha = 1;
  ctx.restore();
  ctx.fillStyle = muted; ctx.textAlign = "left";
  ctx.fillText(fmt(vMax), L + plotW + 8, volTop + 10);
  clearOverlay();
}

function withAlpha(colorText, alpha) {  // "#rrggbb" or "rgb(...)" with an alpha
  const hex = /^#([0-9a-f]{6})$/i.exec(colorText);
  if (hex) {
    const v = parseInt(hex[1], 16);
    return `rgba(${v >> 16}, ${(v >> 8) & 255}, ${v & 255}, ${alpha})`;
  }
  const rgb = /^rgba?\(([^)]+)\)$/.exec(colorText);
  return rgb ? `rgba(${rgb[1].split(",").slice(0, 3).join(",")}, ${alpha})` : colorText;
}

// Ticks on local clock boundaries, found by walking the window: correct across daylight saving changes.
function xTicks(ctx, x, x0, x1, top, bottom, grid, muted) {
  const span = x1 - x0, M = 60;
  const steps = [M, 5 * M, 15 * M, 30 * M, HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, DAY, 2 * DAY, 7 * DAY, 14 * DAY, 30 * DAY, 91 * DAY];
  const step = steps.find((s) => span / s <= 7) || steps[steps.length - 1];
  const dayNumber = (d) => Math.round(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / (DAY * 1000));
  const tick = (d) => {
    if (step < HOUR) return d.getMinutes() % (step / M) === 0;
    if (d.getMinutes() !== 0) return false;
    if (step < DAY) return d.getHours() % (step / HOUR) === 0;
    if (d.getHours() !== 0) return false;
    if (step === 2 * DAY) return dayNumber(d) % 2 === 0;
    if (step === 7 * DAY) return d.getDay() === 1;
    if (step === 14 * DAY) return d.getDay() === 1 && Math.floor(dayNumber(d) / 7) % 2 === 0;
    if (step === 30 * DAY) return d.getDate() === 1;
    if (step === 91 * DAY) return d.getDate() === 1 && d.getMonth() % 3 === 0;
    return true;
  };
  // Walk minutes for short steps, quarter hours otherwise (every zone's offset is whole quarters).
  const walk = step < HOUR ? M : 900;
  ctx.strokeStyle = grid; ctx.fillStyle = muted; ctx.beginPath();
  for (let t = Math.ceil(x0 / walk) * walk; t <= x1; t += walk) {
    const d = new Date(t * 1000);
    if (d.getSeconds() !== 0 || !tick(d)) continue;
    const px = Math.round(x(t)) + 0.5;
    ctx.moveTo(px, top); ctx.lineTo(px, bottom);
    const text = step < DAY ? (d.getHours() === 0 && d.getMinutes() === 0 ? when(t, false) : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })) : when(t, false);
    ctx.textAlign = px - x(x0) < 24 ? "left" : "center";  // a tick at the left edge would be cut off
    ctx.fillText(text, px, bottom + 16);
  }
  ctx.stroke();
}

function clearOverlay() {
  if (!chart.over) return;
  const ctx = chart.over.getContext("2d");
  ctx.setTransform(chart.dpr, 0, 0, chart.dpr, 0, 0);
  ctx.clearRect(0, 0, chart.W, chart.H);
  $("tip").hidden = true;
}

function hover(ev) {
  const g = chart.geom, mdl = chart.mdl;
  if (!g || !mdl || !data) return;
  const rect = chart.over.getBoundingClientRect();
  const px = ev.clientX - rect.left;
  const t = g.x0 + ((px - g.L) / g.plotW) * g.span;
  clearOverlay();
  if (t < data.start || t > data.end || !mdl.total) return;
  const ctx = chart.over.getContext("2d");
  const tip = $("tip");
  const rows = [];
  let cx, head, sub, items, dotY = null;
  if (state.chart === "line") {
    const i = Math.min(mdl.n - 1, Math.max(0, Math.floor((t - data.start) / data.sub)));
    const t1 = Math.min(data.end, data.start + (i + 1) * data.sub);
    cx = g.x(t1); dotY = g.y(mdl.cumulative[i]);
    head = when(t1, true, data.sub < 60);
    rows.push(["Running total", fmt(mdl.cumulative[i])], [`In these ${duration(data.sub)}`, fmt(mdl.sums[i])]);
    items = mdl.visible.map((s) => { let v = 0; for (let j = 0; j <= i; j++) v += s.values[j]; return [s, v]; });
    sub = "Running total by series";
  } else {
    const i = Math.floor((t - data.start) / data.sub);
    let lo = 0, hi = mdl.candles.length - 1;  // the last candle starting at or before sample i
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (mdl.candles[mid].lo <= i) lo = mid; else hi = mid - 1; }
    const c = mdl.candles[lo];
    cx = g.x(c.mid);
    head = `${when(c.t0)} – ${when(c.t1)}`;
    rows.push(["Open", fmt(c.o) + "/h"], ["High", fmt(c.h) + "/h"], ["Low", fmt(c.l) + "/h"], ["Close", fmt(c.c) + "/h"],
      [`Moving average (${MA})`, fmt(c.ma) + "/h"], ["Volume", fmt(c.total)]);
    items = mdl.visible.map((s, i) => [s, c.volume[i]]);
    sub = "Volume by series";
  }
  const css = getComputedStyle(document.documentElement);
  ctx.strokeStyle = css.getPropertyValue("--text-muted").trim(); ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.moveTo(Math.round(cx) + 0.5, g.T); ctx.lineTo(Math.round(cx) + 0.5, g.bottom); ctx.stroke(); ctx.setLineDash([]);
  if (dotY !== null) {
    ctx.beginPath(); ctx.arc(cx, dotY, 4, 0, 2 * Math.PI);
    ctx.fillStyle = g.trend; ctx.fill(); ctx.lineWidth = 2; ctx.strokeStyle = css.getPropertyValue("--surface-1").trim(); ctx.stroke();
  }
  const nodes = [el("div", "when", head)];
  for (const [k, v] of rows) { const r = el("div", "row"); r.append(el("span", "k", k), el("span", null, v)); nodes.push(r); }
  const listed = items.filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  if (listed.length > 1) {
    nodes.push(el("div", "when", sub));
    for (const [s, v] of listed) {
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

// ---------------------------------------------------------------------------- zoom and pan

// Limits: no deeper than MIN_SPAN (or one candle of a chosen interval), no wider than the history,
// nor than a chosen interval's max_bars (TradingView stops zooming out there too); never past now.
function bounds() {
  const now = nowSec(), first = Math.min(data.index.first ?? data.start, data.start);
  const step = state.interval === "auto" ? 0 : data.intervals[state.interval];
  const maxSpan = Math.max(DAY, now - first);
  // Live, the right edge may run to the end of the current candle, as the drawn axis does.
  const edge = data.live && chart.mdl ? Math.max(now, chart.mdl.axisEnd) : now;
  return { now, edge, first, minSpan: Math.max(MIN_SPAN, step), maxSpan: step ? Math.min(maxSpan, step * (data.max_bars - 2)) : maxSpan };
}

function setDomain(from, to) {
  const b = bounds();
  const span = Math.min(Math.max(to - from, b.minSpan), b.maxSpan);
  if (span !== to - from) { const mid = (from + to) / 2; from = mid - span / 2; to = mid + span / 2; }
  if (to > b.edge) { from -= to - b.edge; to = b.edge; }
  if (from < b.first) { to = Math.min(b.edge, to + (b.first - from)); from = b.first; }
  const live = to >= b.now - 1;
  domain = [from, to];
  view = { from: Math.floor(from), to: live ? null : Math.ceil(to) };
  draw();
  scheduleFetch();
}

function zoomAt(t, factor) {
  const [a, b] = domain || [chart.geom.x0, chart.geom.x1];
  const span = b - a, bnd = bounds();
  const next = Math.min(Math.max(span * factor, bnd.minSpan), bnd.maxSpan);
  const k = next / span;
  setDomain(t - (t - a) * k, t + (b - t) * k);
}

function panBy(seconds) {
  const [a, b] = domain || [chart.geom.x0, chart.geom.x1];
  setDomain(a + seconds, b + seconds);
}

let fetchTimer = 0;
function scheduleFetch() {
  clearTimeout(fetchTimer);
  fetchTimer = setTimeout(refresh, 140);  // after the gesture settles; the canvas already shows it
}

function resetZoom() {
  view = null; domain = null;
  refresh();
}

function timeAt(clientX) {
  const g = chart.geom, rect = chart.over.getBoundingClientRect();
  return g.x0 + ((clientX - rect.left - g.L) / g.plotW) * g.span;
}

function attachGestures(canvas) {
  const pointers = new Map();
  let drag = null, pinch = null, lastTap = null;
  canvas.addEventListener("wheel", (ev) => {
    if (!data || !chart.geom) return;
    ev.preventDefault();
    const px = ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? chart.W : 1;  // lines, pages
    const dx = ev.deltaX * px, dy = ev.deltaY * px;
    if (!ev.ctrlKey && Math.abs(dx) > Math.abs(dy)) { panBy((dx / chart.geom.plotW) * chart.geom.span); return; }
    if (ev.shiftKey) { panBy((dy / chart.geom.plotW) * chart.geom.span); return; }
    // A trackpad pinch arrives as ctrl+wheel with small deltas: scaled up to feel the same.
    zoomAt(timeAt(ev.clientX), Math.exp(dy * (ev.ctrlKey ? 0.01 : 0.0015)));
  }, { passive: false });
  canvas.addEventListener("pointerdown", (ev) => {
    if (!data || !chart.geom || (ev.pointerType === "mouse" && ev.button !== 0)) return;
    canvas.setPointerCapture(ev.pointerId);
    pointers.set(ev.pointerId, ev.clientX);
    if (pointers.size === 1) drag = { x: ev.clientX, domain: domain || [chart.geom.x0, chart.geom.x1], moved: false };
    else if (pointers.size === 2) { const [a, b] = [...pointers.values()]; pinch = { dist: Math.abs(a - b) || 1 }; drag = null; }
    $("chart").classList.add("dragging");
  });
  canvas.addEventListener("pointermove", (ev) => {
    if (!pointers.has(ev.pointerId)) { hover(ev); return; }
    pointers.set(ev.pointerId, ev.clientX);
    if (pinch && pointers.size === 2) {
      const [a, b] = [...pointers.values()], dist = Math.abs(a - b) || 1;
      zoomAt(timeAt((a + b) / 2), pinch.dist / dist);
      pinch.dist = dist;
    } else if (drag) {
      if (!drag.moved && Math.abs(drag.x - ev.clientX) < 3) return;  // a click, not a drag
      drag.moved = true;
      const [a, b] = drag.domain, dt = ((drag.x - ev.clientX) / chart.geom.plotW) * (b - a);
      setDomain(a + dt, b + dt);
    }
  });
  const end = (ev) => {
    // A double click or double tap resets the zoom; found here, as dblclick does not reach a canvas
    // holding pointer capture.
    if (ev.type === "pointerup" && drag && !drag.moved && pointers.size === 1) {
      if (lastTap && ev.timeStamp - lastTap.t < 350 && Math.abs(ev.clientX - lastTap.x) < 6) { lastTap = null; resetZoom(); }
      else lastTap = { t: ev.timeStamp, x: ev.clientX };
    }
    pointers.delete(ev.pointerId);
    if (pointers.size < 2) pinch = null;
    if (!pointers.size) { drag = null; $("chart").classList.remove("dragging"); }
  };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("pointerleave", (ev) => { if (!pointers.has(ev.pointerId)) clearOverlay(); });
}

// ---------------------------------------------------------------------------- header and legend

function render() {
  if (!data) return;
  const mdl = model();
  document.title = `${fmt(mdl.total)} · ${data.title}`;
  const where = data.view ? `${when(data.start)} – ${data.live ? "now" : when(data.end)}` : RANGE_NAMES[data.range];
  $("eyebrow").textContent = `${data.metric === "cost" ? "Estimated cost" : "Tokens"} · ${where} · ${data.interval} bars`;
  $("total").textContent = fmt(mdl.total);
  const change = $("change");
  if (mdl.previous) {
    const diff = mdl.total - mdl.previous, pct = (diff / mdl.previous) * 100;
    change.className = `chg ${diff >= 0 ? "up" : "down"}`;
    change.textContent = `${diff >= 0 ? "▲" : "▼"} ${fmt(Math.abs(diff))} (${Math.abs(pct).toFixed(1)}%) vs ${data.view ? "the span before" : PERIOD_NAMES[data.range]}`;
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
  $("reset").hidden = !data.view;

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
    const status = m.error ? `failed (${m.error}), last good ${m.ok ? when(m.ok) : "never"}` : m.ok ? `updated ${when(m.ok)}` : "not reached yet";
    return `${m.name} ${status}`;
  }).join("; ") + ".";
  foot += " Claude Code is counted like /stats: every transcript line, plus the totals /stats kept for days whose transcripts are gone (\"(Claude Code history)\").";
  if (data.bucket < DAY) foot += " Those history totals are one row per day at noon UTC, so bars under a day show them as a spike at that hour.";
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
  clearTimeout(fetchTimer);
  const query = new URLSearchParams({ range: state.range, interval: state.interval, group: state.group, metric: state.metric,
    kinds: state.kinds.join(","), tools: state.tools.join(","), width: String(plotWidth()) });
  if (view) { query.set("from", String(view.from)); if (view.to !== null) query.set("to", String(view.to)); }
  const asked = JSON.stringify(view);
  const seq = ++requests;  // a slower answer to an older choice never overwrites a newer one
  try {
    const response = await fetch(`api/series?${query}`, { credentials: "same-origin", cache: "no-store" });
    if (seq !== requests) return;
    if (response.status === 400 && state.interval !== "auto") {  // the interval no longer fits the window
      state.interval = "auto"; sync();
      return refresh();
    }
    if (!response.ok) throw new Error(String(response.status));
    const answer = await response.json();
    if (seq !== requests) return;
    data = answer;
    if (JSON.stringify(view) === asked) domain = null;  // else a gesture moved on: keep showing where it is
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
  const span = shownSpan();
  for (const b of document.querySelectorAll("[data-interval]")) {
    const ok = !data || fits(span, b.dataset.interval);
    b.disabled = !ok;
    b.title = ok ? "" : `Too ${data.intervals[b.dataset.interval] > span ? "long for this window: zoom out" : "short for this window: zoom in"}`;
  }
  $("auto-interval").textContent = state.interval === "auto" && data ? `Auto · ${data.interval}` : "Auto";
  for (const b of document.querySelectorAll("[data-kind]")) b.setAttribute("aria-pressed", String(state.kinds.includes(b.dataset.kind)));
  for (const b of document.querySelectorAll("[data-tool]")) b.setAttribute("aria-pressed", String(state.tools.includes(b.dataset.tool)));
}

document.addEventListener("DOMContentLoaded", () => {
  chart.main = document.createElement("canvas");
  chart.over = document.createElement("canvas");
  chart.over.className = "over";
  $("chart").replaceChildren(chart.main, chart.over);
  attachGestures(chart.over);
  for (const key of ["range", "interval", "metric", "group"]) {
    for (const b of document.querySelectorAll(`[data-${key}]`)) {
      b.addEventListener("click", () => {
        if (key === "group" && state.group !== b.dataset.group) hidden.clear();
        if (key === "range") { view = null; domain = null; }  // a range button resets the zoom
        state[key] = b.dataset[key];
        sync(); refresh();
      });
    }
  }
  $("reset").addEventListener("click", resetZoom);
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
  let width = 0;
  new ResizeObserver(() => {
    draw();
    if (data && Math.abs(plotWidth() - width) > 40) { width = plotWidth(); scheduleFetch(); }  // resolution follows width
  }).observe($("chart"));
  setInterval(refresh, 60 * 1000);
});
