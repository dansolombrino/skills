// plot-shell: the reader controls every plot carries. Inline this file verbatim as the second
// <script> in the plot's <head>, right after plot-download.js and before Plotly. It adds a slim
// sticky toolbar over the page: show/hide the run selection, box zoom or pan, zoom in/out/reset,
// an axis lock, wheel zoom, hover mode, crosshair spikes, legend, log y, fit-to-window height,
// PNG/SVG export and fullscreen, with keyboard shortcuts (? lists them). Reader preferences stay in
// the browser; nothing it does changes the file or the data.
//
// The page it is inlined into should:
//   - mark the run-selection controls with data-plot-controls (one or more elements);
//   - mark the provenance block with data-plot-provenance;
//   - draw with Plotly.newPlot or Plotly.react (the shell wraps both to keep reader settings).
// Without the marks it guesses: form controls placed before the first figure, and #prov.
(function () {
  "use strict";
  if (window.__plotShell) return;
  window.__plotShell = 1;

  const store = {
    get(key, fallback) { try { const v = localStorage.getItem("plotshell." + key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
    set(key, value) { try { localStorage.setItem("plotshell." + key, JSON.stringify(value)); } catch {} },
  };
  // null means "as the page draws it".
  const state = {
    collapsed: store.get("collapsed", false),
    fit: store.get("fit", false),
    wheel: store.get("wheel", false),
    spikes: store.get("spikes", false),
    hover: store.get("hover", null),       // null | "closest" | "x unified"
    drag: store.get("drag", null),         // null | "zoom" | "pan"
    lock: store.get("lock", "xy"),         // "xy" | "x" | "y": the axes zoom may change
    keep: store.get("keep", false),        // keep zoom when the selection changes
    legend: null,                          // null | true | false
    logy: false,
  };
  const PERSIST = ["collapsed", "fit", "wheel", "spikes", "hover", "drag", "lock", "keep"];
  const save = () => PERSIST.forEach((k) => store.set(k, state[k]));
  // Inside the plot browser the frame carries <base data-plot-browser>; keys it owns go up to it.
  const embedded = () => !!document.querySelector("base[data-plot-browser]");

  // ── Plotly: wrap newPlot/react so every redraw keeps the reader's settings ─────────────────
  const pages = new WeakMap();  // graph div -> {layout, config} as the page last passed them
  let raw = null;               // the unwrapped Plotly.react
  const AXIS = /^[xy]axis\d*$/;
  const elOf = (gd) => typeof gd === "string" ? document.getElementById(gd) : gd;
  function axisNames(gd, layout) {
    const names = new Set(["xaxis", "yaxis"]);
    for (const k of Object.keys(layout || {})) if (AXIS.test(k)) names.add(k);
    const full = gd && gd._fullLayout;
    if (full) for (const k of Object.keys(full)) if (AXIS.test(k) && full[k] && full[k]._id) names.add(k);
    return [...names];
  }
  const get = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
  // Every layout attribute the shell owns, resolved to its final value; null = Plotly's default.
  function owned(gd, page) {
    const out = {};
    const keep = (path, mine) => { const v = mine !== undefined ? mine : get(page, path); out[path] = v === undefined ? null : v; };
    keep("dragmode", state.drag || undefined);
    keep("hovermode", state.hover || undefined);
    keep("showlegend", state.legend === null ? undefined : state.legend);
    keep("uirevision", state.keep ? (get(page, "uirevision") ?? "plot-shell") : undefined);
    if (state.fit) { out.height = fitHeight(gd); out.autosize = true; } else { keep("height"); keep("autosize"); }
    for (const ax of axisNames(gd, page)) {
      const y = ax[0] === "y";
      keep(ax + ".fixedrange", state.lock !== "xy" && state.lock !== ax[0] ? true : undefined);
      if (y) keep(ax + ".type", state.logy ? "log" : undefined);
      keep(ax + ".showspikes", state.spikes ? true : undefined);
      keep(ax + ".spikemode", state.spikes ? "across" : undefined);
      keep(ax + ".spikesnap", state.spikes ? "cursor" : undefined);
      keep(ax + ".spikethickness", state.spikes ? 1 : undefined);
      keep(ax + ".spikedash", state.spikes ? "dot" : undefined);
      keep(ax + ".spikecolor", state.spikes ? "#555555" : undefined);
    }
    return out;
  }
  function withOwned(gd, page) {
    const layout = Object.assign({}, page);
    for (const [path, v] of Object.entries(owned(gd, page))) {
      const keys = path.split(".");
      let o = layout;
      for (let i = 0; i < keys.length - 1; i++) { o[keys[i]] = Object.assign({}, o[keys[i]]); o = o[keys[i]]; }
      const last = keys[keys.length - 1];
      if (v === null) delete o[last]; else o[last] = v;
    }
    return layout;
  }
  function withConfig(config) {
    const leaf = (document.documentElement.getAttribute("data-plot-leaf") || "plot.html").replace(/\.html?$/i, "");
    return Object.assign({ displaylogo: false, responsive: true }, config, {
      scrollZoom: state.wheel,
      toImageButtonOptions: Object.assign({ filename: leaf, scale: 3 }, config && config.toImageButtonOptions),
    });
  }
  function wrap(P) {
    if (!P || P.__plotShell || typeof P.react !== "function") return P;
    raw = P.react;
    for (const name of ["newPlot", "react"]) {
      const orig = P[name];
      if (typeof orig !== "function") continue;
      P[name] = function (gd, data, layout, config) {
        const el = elOf(gd);
        try {
          const fig = data && !Array.isArray(data) && typeof data === "object" ? data : null;
          const page = { layout: (fig ? fig.layout : layout) || {}, config: (fig ? fig.config : config) || {} };
          if (el) pages.set(el, page);
          const args = fig
            ? [gd, Object.assign({}, fig, { layout: withOwned(el, page.layout), config: withConfig(page.config) })]
            : [gd, data, withOwned(el, page.layout), withConfig(page.config)];
          const done = orig.apply(this, args);
          if (done && done.then) done.then(() => afterPlot(el), () => {});
          return done;
        } catch (err) {
          return orig.call(this, gd, data, layout, config);
        }
      };
    }
    P.__plotShell = 1;
    return P;
  }
  if (window.Plotly) wrap(window.Plotly);
  else {
    let P;
    try { Object.defineProperty(window, "Plotly", { configurable: true, enumerable: true, get: () => P, set: (v) => { P = wrap(v); } }); } catch {}
  }

  const graphs = () => [...document.querySelectorAll(".js-plotly-plot")].filter((g) => g._fullLayout);
  let active = null;  // the figure zoom and export act on: the last one pointed at
  const target = () => (active && document.contains(active) ? active : graphs()[0]) || null;

  // Applies the shell's owned attributes to every figure in place, keeping the current zoom.
  function apply() {
    save();
    if (!window.Plotly) return;
    for (const g of graphs()) {
      const page = pages.get(g) || { layout: {}, config: {} };
      const update = owned(g, page.layout);
      if (state.fit) g.style.height = update.height + "px";
      else if (g.dataset.psHeight !== undefined) g.style.height = g.dataset.psHeight;
      try { window.Plotly.relayout(g, update); } catch {}
    }
    renderBar();
  }
  function setConfig() {
    save();
    for (const g of graphs()) {
      const page = pages.get(g) || { layout: {}, config: {} };
      try { raw.call(window.Plotly, g, g.data, g.layout, withConfig(page.config)); } catch {}
    }
    renderBar();
  }

  // ── zoom ─────────────────────────────────────────────────────────────────────────────────
  function zoomable(g) {
    const full = g._fullLayout;
    return Object.keys(full).filter((k) => AXIS.test(k) && full[k] && full[k].range && !full[k].fixedrange).map((k) => full[k]);
  }
  function zoom(factor) {
    const g = target();
    if (!g) return;
    const update = {};
    for (const ax of zoomable(g)) {
      const [a, b] = ax.range.map((v) => ax.r2l(v));
      const c = (a + b) / 2, h = ((b - a) / 2) * factor;
      update[ax._name + ".range"] = [ax.l2r(c - h), ax.l2r(c + h)];
    }
    window.Plotly.relayout(g, update);
  }
  function pan(axis, fraction) {
    const g = target();
    if (!g) return;
    const update = {};
    for (const ax of zoomable(g)) {
      if (ax._id[0] !== axis) continue;
      const [a, b] = ax.range.map((v) => ax.r2l(v));
      const d = (b - a) * fraction;
      update[ax._name + ".range"] = [ax.l2r(a + d), ax.l2r(b + d)];
    }
    window.Plotly.relayout(g, update);
  }
  function reset() {
    const g = target();
    if (!g) return;
    const update = {};
    const full = g._fullLayout;
    for (const k of Object.keys(full)) if (AXIS.test(k) && full[k] && full[k]._id) update[k + ".autorange"] = true;
    window.Plotly.relayout(g, update);
  }
  function exportImage(format) {
    const g = target();
    if (!g) return;
    const opts = withConfig((pages.get(g) || {}).config).toImageButtonOptions;
    window.Plotly.downloadImage(g, { format, filename: opts.filename, scale: format === "png" ? opts.scale : 1, width: g._fullLayout.width, height: g._fullLayout.height });
  }
  function series(on) {
    for (const g of graphs()) {
      const idx = g.data.map((t, i) => (t.visible === false || t.showlegend === false ? -1 : i)).filter((i) => i >= 0);
      if (idx.length) window.Plotly.restyle(g, { visible: on ? true : "legendonly" }, idx);
    }
  }
  function fullscreen() {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (document.documentElement.requestFullscreen) document.documentElement.requestFullscreen().catch(() => {});
  }

  // ── run selection: hide/show, with a one-line summary while hidden ──────────────────────
  const FIELD = "select, input:not([type=hidden]), textarea, [role=slider], [role=radiogroup]";
  function findControls() {
    const marked = [...document.querySelectorAll("[data-plot-controls]")];
    if (marked.length) return marked;
    const first = document.querySelector(".js-plotly-plot");
    const found = [];
    const skip = (el) => el.id === "plot-shell" || el.id === "plot-download" || /^(SCRIPT|STYLE|TEMPLATE)$/.test(el.tagName);
    for (let node = first; node && node !== document.body && node.parentElement; node = node.parentElement) {
      for (let s = node.previousElementSibling; s; s = s.previousElementSibling) {
        if (!skip(s) && !s.querySelector(".js-plotly-plot") && (s.matches(FIELD) || s.querySelector(FIELD))) found.unshift(s);
      }
    }
    return found;
  }
  function labelOf(field) {
    const named = field.getAttribute("aria-label") || field.title;
    const host = field.closest("label") || (field.id && document.querySelector('label[for="' + CSS.escape(field.id) + '"]'));
    if (host) {
      const copy = host.cloneNode(true);
      copy.querySelectorAll("select, option, input, textarea, button, [aria-hidden=true], .info, .i").forEach((n) => n.remove());
      const text = copy.textContent.replace(/\(i\)|ⓘ/g, "").replace(/\s+/g, " ").trim().replace(/[:=]\s*$/, "");
      if (text) return text;
    }
    return named || field.name || field.id || "";
  }
  function summary() {
    const parts = [];
    for (const box of controls) {
      for (const f of box.querySelectorAll("select, input")) {
        if (f.disabled || f.type === "hidden" || f.type === "button" || f.type === "submit") continue;
        let value;
        if (f.tagName === "SELECT") value = [...f.selectedOptions].map((o) => o.textContent.trim()).join(", ");
        else if (f.type === "checkbox" || f.type === "radio") { if (!f.checked) continue; value = f.type === "radio" ? (labelOf(f) || f.value) : "on"; }
        else value = f.value;
        const label = f.type === "radio" ? (f.name || "") : labelOf(f);
        if (value !== "") parts.push(label && f.type !== "radio" ? label + " = " + value : value);
      }
    }
    return parts.join(" · ");
  }
  let controls = [];
  function setCollapsed(on) {
    state.collapsed = on;
    for (const c of controls) c.classList.toggle("ps-collapsed", on);
    save();
    renderBar();
    resize();
  }

  // ── provenance: folded behind a summary line ─────────────────────────────────────────────
  function foldProvenance() {
    const el = document.querySelector("[data-plot-provenance]") || document.getElementById("prov") || document.getElementById("provenance");
    if (!el || el.closest("details.ps-prov")) return;
    const box = document.createElement("details");
    box.className = "ps-prov";
    const sum = document.createElement("summary");
    sum.textContent = "Provenance";
    box.appendChild(sum);
    el.parentNode.insertBefore(box, el);
    box.appendChild(el);
  }

  // ── fit to window ────────────────────────────────────────────────────────────────────────
  // The first figure fills the window below whatever sits above it; any later one fills the
  // window below the toolbar, so each can be scrolled to and seen whole.
  function fitHeight(g) {
    const first = document.querySelector(".js-plotly-plot");
    if (g && g === first) {
      const top = g.getBoundingClientRect().top + window.scrollY;
      return Math.max(320, Math.floor(window.innerHeight - top - 12));
    }
    const bar = document.getElementById("plot-shell");
    return Math.max(320, Math.floor(window.innerHeight - (bar ? bar.getBoundingClientRect().height : 0) - 24));
  }
  let resizeTimer;
  function resize() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (state.fit) apply();
      else for (const g of graphs()) { try { window.Plotly.Plots.resize(g); } catch {} }
    }, 60);
  }

  // ── toolbar ──────────────────────────────────────────────────────────────────────────────
  const CSS_TEXT = `
#plot-shell{position:sticky;top:0;z-index:2147483646;background:#ffffff;border-bottom:1px solid #e4e4e7;margin:0 0 8px;padding:4px 0;font:12px/1.2 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#18181b}
#plot-shell .ps-row{display:flex;flex-wrap:wrap;align-items:center;gap:4px}
#plot-shell button{font:inherit;color:inherit;background:#ffffff;border:1px solid #d4d4d8;border-radius:6px;height:26px;min-width:26px;padding:0 7px;cursor:pointer;display:inline-flex;align-items:center;gap:4px;white-space:nowrap}
#plot-shell button:hover{background:#f4f4f5}
#plot-shell button[aria-pressed=true]{background:#18181b;color:#ffffff;border-color:#18181b}
#plot-shell button:focus-visible{outline:2px solid #2563eb;outline-offset:1px}
#plot-shell .ps-grp{display:inline-flex;gap:2px;padding-left:6px;margin-left:2px;border-left:1px solid #e4e4e7}
#plot-shell .ps-sel{font-weight:600}
#plot-shell .ps-sum.on{flex-basis:140px}
#plot-shell .ps-sum{flex:1 1 0;min-width:0;color:#52525b;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:0 4px}
#plot-shell svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
#plot-shell .ps-help{margin-top:6px;padding:8px 10px;border:1px solid #e4e4e7;border-radius:8px;background:#fafafa;display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:3px 16px}
#plot-shell kbd{font:11px ui-monospace,Menlo,Consolas,monospace;border:1px solid #d4d4d8;border-bottom-width:2px;border-radius:4px;padding:0 4px;background:#ffffff}
.ps-collapsed{display:none!important}
details.ps-prov{margin-top:14px}
details.ps-prov>summary{cursor:pointer;font-size:12px;color:#52525b;width:max-content}
@media (max-width:640px){#plot-shell .ps-row{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:thin}#plot-shell .ps-row>*{flex:none}#plot-shell .ps-sum,#plot-shell .ps-sum.on{flex:none;max-width:60vw}}
@media print{#plot-shell{display:none!important}}`;
  const ICON = {
    box: '<path d="M4 4h6M4 4v6M20 20h-6M20 20v-6M14 4h6v6M4 14v6h6"/>',
    pan: '<path d="M12 3v18M3 12h18M12 3l-3 3M12 3l3 3M12 21l-3-3M12 21l3-3M3 12l3-3M3 12l3 3M21 12l-3-3M21 12l-3 3"/>',
    plus: '<circle cx="11" cy="11" r="7"/><path d="M11 8v6M8 11h6M20 20l-4-4"/>',
    minus: '<circle cx="11" cy="11" r="7"/><path d="M8 11h6M20 20l-4-4"/>',
    reset: '<path d="M4 12a8 8 0 1 0 2.3-5.7"/><path d="M4 4v5h5"/>',
    full: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  };
  const svg = (name) => '<svg viewBox="0 0 24 24" aria-hidden="true">' + ICON[name] + "</svg>";
  const KEYS = [
    ["s", "show / hide the run selection"], ["+ / −", "zoom in / out"], ["0", "reset zoom (or double-click the plot)"],
    ["Shift + arrows", "pan"], ["z / p", "box zoom / pan mode"], ["x", "zoom axes: both → x only → y only"],
    ["w", "mouse-wheel zoom on / off"], ["u", "hover: nearest point / all series at x"], ["c", "crosshair spikes"],
    ["l", "legend on / off"], ["g", "log / linear y"], ["h", "fit the figure to the window height"],
    ["f", "fullscreen"], ["?", "this help"],
  ];
  let bar, help = false;
  function button(act, label, title, pressed) {
    return '<button type="button" data-act="' + act + '" title="' + title + '"' + (pressed === undefined ? "" : ' aria-pressed="' + !!pressed + '"') + ">" + label + "</button>";
  }
  function renderBar() {
    if (!bar) return;
    const lockLabel = { xy: "XY", x: "X", y: "Y" }[state.lock];
    const full = (target() || {})._fullLayout || {};
    const drag = full.dragmode || "zoom";
    const unified = full.hovermode === "x unified";
    const sum = controls.length ? summary() : "";
    bar.innerHTML = '<div class="ps-row">' +
      (controls.length ? button("sel", (state.collapsed ? "▸" : "▾") + " Selection", "Show or hide the run selection  (s)") +
        (state.collapsed ? '<span class="ps-sum on"' : '<span class="ps-sum"') + ' title="' + sum.replace(/"/g, "&quot;") + '">' + (state.collapsed ? sum.replace(/</g, "&lt;") : "") + "</span>"
        : '<span class="ps-sum"></span>') +
      '<span class="ps-grp">' + button("box", svg("box"), "Box zoom: drag a rectangle  (z)", drag === "zoom") + button("pan", svg("pan"), "Pan: drag to move  (p)", drag === "pan") + "</span>" +
      '<span class="ps-grp">' + button("in", svg("plus"), "Zoom in  (+)") + button("out", svg("minus"), "Zoom out  (−)") + button("reset", svg("reset"), "Reset zoom  (0, or double-click the plot)") +
      button("lock", lockLabel, "Axes zoom and pan may change: both, x only, y only  (x)", state.lock !== "xy") +
      button("wheel", "Wheel", "Zoom with the mouse wheel  (w)", state.wheel) + "</span>" +
      '<span class="ps-grp">' + button("hover", "Hover x", "Hover lists every series at the cursor x instead of the nearest point  (u)", unified) +
      button("spikes", "Crosshair", "Crosshair spike lines to the axes  (c)", state.spikes) +
      button("legend", "Legend", "Show or hide the legend  (l)", full.showlegend !== false) +
      button("all", "All on", "Show every series") + button("none", "All off", "Hide every series (click a legend entry to bring one back)") +
      button("logy", "Log y", "Log or linear y axis  (g)", state.logy) + "</span>" +
      '<span class="ps-grp">' + button("fit", "Fit", "Fit the figure to the window height  (h)", state.fit) +
      button("png", "PNG", "Save the current view as a PNG image") + button("svg", "SVG", "Save the current view as an SVG image") +
      (document.fullscreenEnabled ? button("full", svg("full"), "Fullscreen  (f)") : "") + button("help", "?", "Keyboard shortcuts  (?)", help) + "</span></div>" +
      (help ? '<div class="ps-help">' + KEYS.map(([k, t]) => "<span><kbd>" + k + "</kbd> " + t + "</span>").join("") + "</div>" : "");
  }
  const ACTS = {
    sel: () => setCollapsed(!state.collapsed),
    box: () => { state.drag = "zoom"; apply(); },
    pan: () => { state.drag = "pan"; apply(); },
    in: () => zoom(1 / 1.5),
    out: () => zoom(1.5),
    reset,
    lock: () => { state.lock = { xy: "x", x: "y", y: "xy" }[state.lock]; apply(); },
    wheel: () => { state.wheel = !state.wheel; setConfig(); },
    hover: () => { state.hover = ((target() || {})._fullLayout || {}).hovermode === "x unified" ? "closest" : "x unified"; apply(); },
    spikes: () => { state.spikes = !state.spikes; apply(); },
    legend: () => { state.legend = ((target() || {})._fullLayout || {}).showlegend === false; apply(); },
    all: () => series(true),
    none: () => series(false),
    logy: () => { state.logy = !state.logy; apply(); reset(); },
    fit: () => {
      state.fit = !state.fit;
      if (state.fit) for (const g of graphs()) if (g.dataset.psHeight === undefined) g.dataset.psHeight = g.style.height;
      apply();
    },
    png: () => exportImage("png"),
    svg: () => exportImage("svg"),
    full: fullscreen,
    help: () => { help = !help; renderBar(); },
  };
  const KEYMAP = { s: "sel", z: "box", p: "pan", "+": "in", "=": "in", "-": "out", "0": "reset", x: "lock", w: "wheel", u: "hover", c: "spikes", l: "legend", g: "logy", h: "fit", f: "full", "?": "help" };
  const FORWARD = new Set(["j", "k", "ArrowUp", "ArrowDown", "b", "r", "d", "/", "f", "Escape"]);

  function onKey(e) {
    const t = e.target;
    if (e.ctrlKey || e.metaKey || e.altKey || (t && (t.isContentEditable || /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName)))) return;
    if (e.shiftKey && e.key.startsWith("Arrow")) {
      e.preventDefault();
      const step = { ArrowLeft: ["x", -0.2], ArrowRight: ["x", 0.2], ArrowUp: ["y", 0.2], ArrowDown: ["y", -0.2] }[e.key];
      pan(step[0], step[1]);
      return;
    }
    // The browser around an embedded plot owns plot navigation and its own focus mode.
    if (embedded() && FORWARD.has(e.key) && !e.shiftKey) {
      e.preventDefault();
      window.parent.postMessage({ type: "plot-shell-key", key: e.key }, "*");
      return;
    }
    const act = KEYMAP[e.key];
    if (act && ACTS[act]) { e.preventDefault(); ACTS[act](); }
  }

  function afterPlot(el) {
    if (el && state.fit && el.dataset.psHeight === undefined) el.dataset.psHeight = el.style.height;
    if (el && state.fit) el.style.height = fitHeight(el) + "px";
    if (bar) renderBar();
  }

  let started = false;
  function start() {
    if (started || !document.body) return;
    started = true;
    const style = document.createElement("style");
    style.textContent = CSS_TEXT;
    document.head.appendChild(style);
    bar = document.createElement("div");
    bar.id = "plot-shell";
    bar.setAttribute("role", "toolbar");
    bar.setAttribute("aria-label", "Plot controls");
    document.body.insertBefore(bar, document.body.firstChild);
    controls = findControls();
    if (state.collapsed) for (const c of controls) c.classList.add("ps-collapsed");
    foldProvenance();
    bar.addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]");
      if (b && ACTS[b.dataset.act]) ACTS[b.dataset.act]();
    });
    const changed = () => { if (state.collapsed) renderBar(); };
    document.addEventListener("change", changed, true);
    document.addEventListener("input", changed, true);
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", (e) => { const g = e.target.closest && e.target.closest(".js-plotly-plot"); if (g) active = g; }, true);
    window.addEventListener("resize", resize);
    renderBar();
    // A figure drawn before the wrap took hold: what it holds now is what the page asked for.
    const CONFIG_KEYS = ["responsive", "displaylogo", "displayModeBar", "modeBarButtonsToRemove", "modeBarButtonsToAdd", "editable", "locale", "toImageButtonOptions", "doubleClick", "showTips"];
    for (const g of graphs()) {
      if (pages.has(g)) continue;
      const config = {};
      for (const k of CONFIG_KEYS) if (g._context && g._context[k] !== undefined) config[k] = g._context[k];
      pages.set(g, { layout: JSON.parse(JSON.stringify(g.layout || {})), config });
    }
    if (window.Plotly && window.Plotly.__plotShell && graphs().length) {
      if (!raw) raw = window.Plotly.react;
      if (state.fit) for (const g of graphs()) if (g.dataset.psHeight === undefined) g.dataset.psHeight = g.style.height;
      // Redraw only when a remembered preference changes the page's own view.
      if (state.fit || state.spikes || state.hover || state.drag || state.lock !== "xy" || state.keep) apply();
      if (state.wheel) setConfig();
    }
  }
  // After the page's own DOMContentLoaded/load rendering, so its controls and figures exist.
  if (document.readyState === "complete") setTimeout(start, 0);
  else window.addEventListener("load", () => setTimeout(start, 0));
})();
