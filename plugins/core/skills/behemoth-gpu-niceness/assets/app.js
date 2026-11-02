"use strict";
// GPU-time ranking. Requests are relative, so the page works under any path prefix (service-hub).
// The DOM is built with textContent and createElement only: nothing from the API is parsed as HTML.

const WINDOWS = ["24h", "7d", "30d", "all"];
const WINDOW_NAMES = { "24h": "the last 24 hours", "7d": "the last 7 days", "30d": "the last 30 days", all: "all recorded time" };
const UNATTRIBUTED = "(unattributed)";
let windowName = "7d";
try { const saved = localStorage.getItem("niceness-window"); if (WINDOWS.includes(saved)) windowName = saved; } catch (_) {}

const $ = (id) => document.getElementById(id);
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
// Colour follows the user (first time seen on the machine), never their rank; slots past 8 fold to grey.
function color(slot, user) {
  if (user === UNATTRIBUTED || slot === null || slot === undefined || slot >= 8) return "var(--other)";
  return `var(--series-${slot + 1})`;
}
const fmt = (x, digits = 1) => Number(x).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
const when = (ts) => new Date(ts * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

function row(rank, user, slot, gpuH, share, maxShare, cls) {
  const tr = el("tr", cls);
  tr.append(el("td", "rank", rank));
  const who = el("td"); const w = el("span", "who"); const dot = el("span", "dot");
  dot.style.background = slot === "unused" ? "var(--track)" : color(slot, user);
  w.append(dot, el("span", null, user)); who.append(w); tr.append(who);
  tr.append(el("td", "num", fmt(gpuH)));
  const cell = el("td", "bar-col"); const wrap = el("div", "share");
  const bar = el("div", "bar"); const fill = el("span");
  fill.style.width = `${maxShare ? (100 * share) / maxShare : 0}%`;
  fill.style.background = slot === "unused" ? "var(--other)" : color(slot, user);
  bar.append(fill); wrap.append(bar, el("span", "pct", `${fmt(share * 100)}%`)); cell.append(wrap); tr.append(cell);
  return tr;
}

function render(data) {
  document.title = data.title;
  $("title").textContent = data.title;
  const body = $("board").querySelector("tbody");
  const foot = $("board").querySelector("tfoot");
  const since = data.sampled.from ? ` since ${when(data.sampled.from)}` : "";
  $("summary").textContent = data.capacity_gpu_h
    ? `${fmt(data.used_gpu_h)} of ${fmt(data.capacity_gpu_h)} GPU-hours used over ${WINDOW_NAMES[data.window]} (${data.gpus} GPUs, ${fmt(data.sampled.hours)} h sampled${since}).`
    : "No samples yet: the first one lands within a minute.";
  if (!data.users.length) {
    const tr = el("tr"); const td = el("td", "empty", "Nobody used a GPU in this window."); td.colSpan = 4; tr.append(td);
    body.replaceChildren(tr); foot.replaceChildren(); return;
  }
  const unused = data.capacity_gpu_h ? data.unused_gpu_h / data.capacity_gpu_h : 0;
  const maxShare = Math.max(unused, ...data.users.map((u) => u.share));
  let rank = 0;
  body.replaceChildren(...data.users.map((u) => row(
    u.user === UNATTRIBUTED ? "–" : ++rank, u.user, u.slot, u.gpu_h, u.share, maxShare, u.user === UNATTRIBUTED ? "muted" : "")));
  foot.replaceChildren(row("", "unused", "unused", data.unused_gpu_h, unused, maxShare, "muted"));
}

async function refresh() {
  try {
    const response = await fetch(`api/stats?window=${windowName}`, { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    render(await response.json());
  } catch (_) {
    $("summary").textContent = "Could not load the statistics.";
  }
}

function selectWindow(name) {
  windowName = name;
  try { localStorage.setItem("niceness-window", name); } catch (_) {}
  for (const b of document.querySelectorAll("[data-window]")) b.setAttribute("aria-pressed", String(b.dataset.window === name));
  refresh();
}

document.addEventListener("DOMContentLoaded", () => {
  for (const b of document.querySelectorAll("[data-window]")) b.addEventListener("click", () => selectWindow(b.dataset.window));
  selectWindow(windowName);
  setInterval(refresh, 5 * 60 * 1000);
});
