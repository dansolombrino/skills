// service-hub navigation: a small floating button the hub adds to every proxied page. Click it for
// a switcher listing every service (the current one marked) and the way back to the landing page.
// It lives in a shadow root, so the page's styles cannot reach it and it cannot restyle the page.
(() => {
  if (window.top !== window || document.getElementById("service-hub-nav")) return;
  const script = document.currentScript;
  const current = script && script.dataset.service;
  const emoji = script && script.dataset.icon;
  const store = { get(k, d) { try { return localStorage.getItem("service-hub:" + k) ?? d; } catch { return d; } }, set(k, v) { try { localStorage.setItem("service-hub:" + k, v); } catch {} } };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  const host = document.createElement("div");
  host.id = "service-hub-nav";
  host.style.cssText = "position:fixed;z-index:2147483646;bottom:16px;" + (store.get("side", "right") === "left" ? "left:16px" : "right:16px");
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>
    :host { all: initial; font: 14px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color-scheme: light dark;
      --bg: #ffffff; --line: #e3e5e8; --text: #15171a; --muted: #6b7280; --hover: #f1f3f6; --accent: #2563eb; --up: #16a34a; --down: #dc2626; }
    @media (prefers-color-scheme: dark) { :host { --bg: #171a20; --line: #2a2f37; --text: #e8eaed; --muted: #9aa1ab; --hover: #20242c; --accent: #6b9bff; --up: #34d399; --down: #f87171; } }
    button.fab { width: 40px; height: 40px; border-radius: 50%; border: 1px solid var(--line); background: var(--bg); color: var(--text);
      box-shadow: 0 4px 14px rgba(0,0,0,.18); cursor: pointer; display: grid; place-items: center; padding: 0; opacity: .78; transition: opacity .15s, transform .15s; }
    button.fab:hover, button.fab[aria-expanded="true"] { opacity: 1; transform: translateY(-1px); }
    button.fab:focus-visible, a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
    button.fab img { width: 24px; height: 24px; object-fit: contain; }
    button.fab svg { width: 20px; height: 20px; }
    .menu { position: absolute; bottom: 50px; min-width: 240px; max-width: min(320px, calc(100vw - 32px)); background: var(--bg); color: var(--text);
      border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 12px 34px rgba(0,0,0,.22); padding: 6px; display: none; }
    :host([data-side="right"]) .menu { right: 0; } :host([data-side="left"]) .menu { left: 0; }
    .menu.open { display: block; }
    a { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 8px; color: inherit; text-decoration: none; }
    a:hover { background: var(--hover); }
    a.home { font-weight: 600; }
    a.cur { background: var(--hover); }
    .ico { width: 22px; text-align: center; flex: none; }
    .nm { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .dot { width: 7px; height: 7px; border-radius: 50%; flex: none; background: var(--muted); }
    .dot.up { background: var(--up); } .dot.down { background: var(--down); }
    .tag { font-size: 11px; color: var(--muted); }
    hr { border: 0; border-top: 1px solid var(--line); margin: 5px 4px; }
    .foot { display: flex; justify-content: space-between; align-items: center; padding: 4px 10px 2px; font-size: 11px; color: var(--muted); }
    .foot button { all: unset; cursor: pointer; color: var(--muted); } .foot button:hover { color: var(--text); }
  </style>
  <div class="menu" role="menu"></div>
  <button class="fab" type="button" aria-haspopup="menu" aria-expanded="false" title="Service Hub: switch service">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>
  </button>`;
  host.dataset.side = store.get("side", "right");
  const fab = root.querySelector(".fab");
  const menu = root.querySelector(".menu");
  let data = null;

  const render = () => {
    const title = data ? data.title : "Service Hub";
    const items = (data ? data.services : []).map((s) => {
      const cur = s.slug === current;
      const href = s.kind === "proxy" ? "/" + s.slug + "/" : s.href;
      return `<a role="menuitem" href="${esc(href)}" class="${cur ? "cur" : ""}" ${s.kind === "proxy" ? "" : 'target="_blank" rel="noopener"'}>
        <span class="ico">${esc(s.icon || s.name.charAt(0).toUpperCase())}</span><span class="nm">${esc(s.name)}</span>
        ${cur ? '<span class="tag">here</span>' : ""}<span class="dot ${esc(s.status)}" title="${esc(s.status)}"></span></a>`;
    }).join("");
    menu.innerHTML = `<a role="menuitem" class="home" href="/"><span class="ico">⌂</span><span class="nm">${esc(title)}</span></a>` +
      (items ? "<hr>" + items : "") +
      `<div class="foot"><span>Esc closes</span><button type="button" data-move>Move to the ${host.dataset.side === "right" ? "left" : "right"}</button></div>`;
  };
  const load = async () => {
    try {
      const r = await fetch("/api/services", { credentials: "same-origin", cache: "no-store" });
      if (!r.ok) return;
      data = await r.json();
      if (data.icon && !fab.querySelector("img")) fab.innerHTML = '<img src="/favicon.ico" alt="">';
      if (menu.classList.contains("open")) render();
    } catch {}
  };
  const setOpen = (open) => {
    menu.classList.toggle("open", open);
    fab.setAttribute("aria-expanded", String(open));
    if (open) { render(); load(); }
  };

  fab.addEventListener("click", (e) => { e.stopPropagation(); setOpen(!menu.classList.contains("open")); });
  menu.addEventListener("click", (e) => {
    if (!e.target.closest("[data-move]")) return;
    e.stopPropagation();
    const side = host.dataset.side === "right" ? "left" : "right";
    store.set("side", side);
    host.dataset.side = side;
    host.style.left = side === "left" ? "16px" : "";
    host.style.right = side === "right" ? "16px" : "";
    render();
  });
  document.addEventListener("click", (e) => { if (!e.composedPath().includes(host)) setOpen(false); });
  // Capture phase, so the page's own Escape handler does not also fire while the menu is open.
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && menu.classList.contains("open")) { e.stopPropagation(); setOpen(false); fab.focus(); } }, true);

  // The service's emoji from the hub becomes the tab icon, the same one the switcher and the
  // landing page show. A page that rewrites its own icon later (the board does, every refresh)
  // is put back by the observer.
  if (emoji) {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><text x="32" y="54" font-size="54" text-anchor="middle">${esc(emoji)}</text></svg>`;
    const href = "data:image/svg+xml," + encodeURIComponent(svg);
    const apply = () => {
      const links = document.querySelectorAll('link[rel~="icon"]');
      if (!links.length) {
        const link = document.createElement("link");
        link.rel = "icon";
        document.head.appendChild(link);
        link.setAttribute("href", href);
        return;
      }
      links.forEach((link) => { if (link.getAttribute("href") !== href) link.setAttribute("href", href); });
    };
    apply();
    new MutationObserver(apply).observe(document.head, { subtree: true, childList: true, attributes: true, attributeFilter: ["href", "rel"] });
  }

  document.body.appendChild(host);
  load();
})();
