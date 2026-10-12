// plot-download: the two download buttons every plot carries. Inline this file verbatim as the
// first <script> in the plot's <head>. It saves the plot as a self-contained HTML file named after
// its leaf, either whole ("all runs") or trimmed to the runs the current view shows ("selection").
//
// The page it is inlined into must:
//   - set <html data-plot-leaf="<leaf>.html">;
//   - keep its data in <script type="application/json" id="plot-data"> whose "runs" object is
//     keyed by run_id, with every "<" escaped as <;
//   - define window.plotSelectedRuns() returning the run_ids the current view shows;
//   - render only from a DOMContentLoaded listener or later, and never replace <body> wholesale.
(function () {
  "use strict";
  let pristine = null;
  const leaf = () => document.documentElement.getAttribute("data-plot-leaf") || "plot.html";

  // Taken before the page renders, so a download is the file as written, not the drawn DOM.
  document.addEventListener("DOMContentLoaded", () => {
    pristine = "<!DOCTYPE html>\n" + document.documentElement.outerHTML;
    addButtons();
  });
  window.addEventListener("load", addButtons);

  function freshDocument() {
    const doc = new DOMParser().parseFromString(pristine, "text/html");
    // A viewer may inject a <base> pointing at its server; the shared file must not carry it.
    doc.querySelectorAll("base[data-plot-browser]").forEach((el) => el.remove());
    return doc;
  }

  function save(doc) {
    const html = "<!DOCTYPE html>\n" + doc.documentElement.outerHTML;
    const a = document.createElement("a");
    a.download = leaf();
    a.href = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  }

  function downloadAll() {
    save(freshDocument());
  }

  function downloadSelection() {
    const selected = typeof window.plotSelectedRuns === "function" ? window.plotSelectedRuns() : [];
    const doc = freshDocument();
    const node = doc.getElementById("plot-data");
    if (!node || !selected || !selected.length) {
      alert("This plot exposes no current selection to download.");
      return;
    }
    const data = JSON.parse(node.textContent);
    const runs = data.runs || {};
    data.runs = Object.fromEntries(selected.filter((id) => id in runs).map((id) => [id, runs[id]]));
    node.textContent = JSON.stringify(data).replace(/</g, "\\u003c");
    save(doc);
  }

  function addButtons() {
    if (!document.body || document.getElementById("plot-download")) return;
    const bar = document.createElement("div");
    bar.id = "plot-download";
    bar.setAttribute("style", "position:fixed;right:12px;bottom:12px;z-index:2147483647;display:flex;gap:6px;" +
      "font:12px/1.2 system-ui,-apple-system,'Segoe UI',sans-serif");
    const button = (label, title, onClick) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.title = title;
      b.setAttribute("style", "font:inherit;color:#1f2328;background:#ffffff;border:1px solid #c8ccd1;" +
        "border-radius:6px;padding:5px 9px;cursor:pointer;box-shadow:0 1px 2px rgba(0,0,0,.08)");
      b.addEventListener("click", onClick);
      bar.appendChild(b);
    };
    button("⤓ All runs", "Download this plot as a self-contained HTML file with every run", downloadAll);
    button("⤓ Selection", "Download a self-contained HTML file with only the runs shown now", downloadSelection);
    const style = document.createElement("style");
    style.textContent = "@media print { #plot-download { display: none !important; } }";
    bar.appendChild(style);
    document.body.appendChild(bar);
  }
})();
