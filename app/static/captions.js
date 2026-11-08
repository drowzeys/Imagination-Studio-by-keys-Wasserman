"use strict";
// Search the Library by what is in it: the Studio's local vision model describes each clip and picture in the
// background (app/captions.py); this draws the search box, the matching tiles and the "Describe now" button.
const CAP = { map: null, loading: false, busy: new Set() };
function capLoad() { if (CAP.map || CAP.loading) return Promise.resolve(); CAP.loading = true;
  return api("/api/library/captions").then(r => { CAP.map = r.captions || {}; }, () => { CAP.map = {}; }).finally(() => { CAP.loading = false; }); }
const capText = path => (CAP.map && CAP.map[path] && CAP.map[path].caption) || null;
// a tile that has no description yet offers to make one
function capButton(m) { if (m.kind !== "video" && m.kind !== "image") return null; if (!CAP.map || capText(m.path)) return null;
  return el("button", { class: "small", title: "Look at this " + (m.kind === "video" ? "clip" : "picture") + " with the local model so the Library can search by what is in it", disabled: CAP.busy.has(m.path),
    onclick: async () => { CAP.busy.add(m.path); render(); try { const r = await api("/api/library/captions/run", { paths: [m.path] });
        if (r.busy) toast("The local model is busy with a render. Try again when it is done."); else if (r.failed && r.failed.length) toast("The model could not describe this one."); } catch (e) { toast(e.message); }
      CAP.busy.delete(m.path); CAP.map = null; render(); } }, CAP.busy.has(m.path) ? "Describing…" : "Describe now"); }
// the search field and its results; `grid` is a function giving the normal listing, hidden while results show; each
// result has the Library's tick box (LIBPICK), and `picked` is called when one changes so the page's counts follow
function capSearch(grid, picked) { const out = el("div", { class: "grid", style: "margin-top:10px;display:none" }), line = el("div", { class: "note", style: "display:none" });
  const find = el("input", { type: "search", placeholder: "Search what is in your clips…", style: "max-width:320px", "aria-label": "Search what is in your clips", value: CAP.q || "" });
  let wait = null, seq = 0, found = [];
  const run = async () => { clearTimeout(wait); const q = find.value.trim(); CAP.q = q; const mine = ++seq, g = grid();
    if (!q) { found = []; out.style.display = "none"; line.style.display = "none"; if (g) g.style.display = ""; return; }
    try { const r = await api("/api/library/search?q=" + encodeURIComponent(q)); if (mine !== seq) return; const items = r.items || []; found = items.map(m => m.path);
      if (g) g.style.display = "none"; out.style.display = ""; line.style.display = "";
      line.textContent = items.length ? items.length + (items.length === 1 ? " match" : " matches") + " for what is in your clips." : "Nothing described matches that yet. New clips are described when nothing is rendering.";
      out.replaceChildren(...items.map(m => el("div", { class: "tile", "data-path": m.path, title: m.caption || m.path }, media(m.path),
        el("div", { class: "cap" }, el("input", { type: "checkbox", title: "Select " + m.path, "aria-label": "Select " + m.path, checked: LIBPICK.has(m.path), style: "width:auto;flex:none",
            onchange: e => { if (e.target.checked) LIBPICK.add(m.path); else LIBPICK.delete(m.path); if (picked) picked(); } }),
          el("span", { style: "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap", title: m.path }, m.path.split("/").pop())),
        el("div", { class: "note", style: "padding:0 10px 8px" }, m.caption || "")))); } catch (e) { toast(e.message); } };
  find.addEventListener("input", () => { clearTimeout(wait); wait = setTimeout(run, 250); }); find.addEventListener("change", run);
  find.addEventListener("keydown", e => { if (e.key === "Enter") run(); });
  if (CAP.q) setTimeout(run, 0);
  // the paths the search shows now (empty when it is not searching): Select all takes these while it is
  return { box: el("div", { class: "fit" }, find), results: el("div", {}, line, out), run, found: () => CAP.q ? found : [] }; }
