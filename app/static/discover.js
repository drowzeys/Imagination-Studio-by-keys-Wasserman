"use strict";
/* Machine → Models & weights (app/discover.py): what the studio needs, whether it is where the studio looks, and
   where a scan of this machine found it otherwise. "Use it" links the found file or folder where the studio looks
   (nothing is moved, copied or downloaded), "Use everything found here" links each one proved; "Install" runs a
   missing one's own installer (app/installs.py); "Unlink" undoes a link the studio made. viewDiscover() draws it. */
(() => {
  if (typeof document === "undefined" || !document.head || document.getElementById("discover-style")) return;
  const style = document.createElement("style");
  style.id = "discover-style";
  style.textContent = `
.dsc-head{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.dsc-head .grow{flex:1}
.dsc-group{margin-top:14px}
.dsc-group h3{margin:0 0 6px;font:600 13px/1.3 var(--sans);color:var(--text)}
.dsc-row{border:1px solid var(--line);border-radius:var(--r2);background:var(--panel);padding:8px 10px;margin:6px 0}
.dsc-row .top{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.dsc-row .top b{font-weight:600;color:var(--text)}
.dsc-path{font:12px/1.4 var(--mono);color:var(--dim);word-break:break-all}
.dsc-cand{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:4px}
.dsc-state{font:600 11px/1 var(--sans);padding:3px 7px;border-radius:999px;border:1px solid var(--line2);color:var(--dim)}
.dsc-state.expected{color:var(--green);border-color:var(--green)}
.dsc-state.found{color:var(--blue);border-color:var(--blue)}
.dsc-state.needs-conversion{color:var(--amber);border-color:var(--amber)}
.dsc-state.missing{color:var(--red);border-color:var(--red)}
.dsc-state.scanning{color:var(--amber);border-color:var(--amber)}
.dsc-state.opt{color:var(--faint);border-color:var(--line)}
.dsc-cmd{font:12px/1.4 var(--mono);background:var(--bg2);border:1px solid var(--line);border-radius:var(--r1);padding:4px 6px;margin-top:4px;word-break:break-all;color:var(--text)}
`;
  document.head.appendChild(style);
})();

const DSC = { data: null, all: false, busy: "", timer: null };
const DSC_WORDS = { expected: "in place", found: "found elsewhere", "needs-conversion": "needs converting", missing: "not on this machine", scanning: "scanning…" };
const dscWhen = t => { if (!t) return "never"; const d = new Date(t * 1000); return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); };

async function dscLoad(node) {
  try { DSC.data = await api("/api/discover"); } catch (e) { DSC.data = DSC.data || { error: e.message }; }
  if (node) dscDraw(node);
  clearTimeout(DSC.timer);
  if (DSC.data && DSC.data.scanning) DSC.timer = setTimeout(() => dscLoad(node), 2000);
}

async function dscDo(node, path, body, busy) {
  DSC.busy = busy;
  if (node) dscDraw(node);
  try { DSC.data = await api(path, body); } catch (e) { toast(e.message, 6000); }
  DSC.busy = "";
  if (node) dscDraw(node);
  if (DSC.data && DSC.data.scanning) { clearTimeout(DSC.timer); DSC.timer = setTimeout(() => dscLoad(node), 2000); }
}

// one need: its state, where the studio looks, and what the scan found
function dscRow(node, n) {
  const state = n.optional && n.state === "missing" ? "opt" : n.state;
  const said = n.optional && n.state === "missing" ? "not installed (optional)" : DSC_WORDS[n.state] || n.state;
  const use = c => el("button", { class: "small", disabled: !!DSC.busy || (n.unlisted && n.linkable === false),
    title: n.unlisted && n.linkable === false ? "Add it in the LoRA hub under My LoRAs" : "Link " + c.shown + " where the studio looks for it (nothing is moved or copied)",
    onclick: () => dscDo(node, "/api/discover/use", { need: n.id, path: c.path }, n.id) }, "Use it");
  return el("div", { class: "dsc-row", "data-need": n.id },
    el("div", { class: "top" }, el("b", {}, n.label), el("span", { class: "dsc-state " + state }, said),
      n.link ? el("button", { class: "small", disabled: !!DSC.busy, title: "Remove the link the studio made (the file itself stays where it is)",
        onclick: () => dscDo(node, "/api/discover/unlink", { need: n.id }, n.id) }, "Unlink") : null),
    n.expected ? el("div", { class: "dsc-path" }, (n.unlisted ? "would be linked at " : "expected at ") + relHome(n.expected) + (n.env ? " (" + n.env + ")" : "")) : null,
    n.link ? el("div", { class: "dsc-path" }, "linked to " + n.link.shown + (n.link.how === "record" ? " (recorded: that folder cannot be written, the studio reads the link itself)" : "")) : null,
    !n.link && n.state !== "expected" ? (n.candidates || []).map(c => el("div", { class: "dsc-cand" }, el("span", { class: "dsc-path" }, "found at " + c.shown),
      el("span", { class: "note" }, c.verified ? "proved by " + c.proof : c.proof), c.copies && c.copies.length ? el("span", { class: "note" }, "identical copies: " + c.copies.length) : null, use(c))) : null,
    !n.link && n.state !== "expected" ? (n.conversions || []).map(c => el("div", {}, el("div", { class: "dsc-path" }, "found " + c.shown + ", in another layout: " + (c.note || "")),
      c.convert ? el("div", { class: "dsc-cmd" }, c.convert) : null)) : null,
    n.state === "missing" ? dscInstall(node, n) : null);
}

// what Install does for a need that is not on this machine: the engine's own installer, the LoRA hub, or nothing to run
const dscRunning = () => DSC.data && DSC.data.installing && DSC.data.installing.state === "running";
function dscInstall(node, n) { const p = n.install || {}, job = DSC.data.installing || {}, mine = job.need === n.id;
  if (p.how === "first_use") return el("div", { class: "note" }, "Comes by itself the first time it is used.");
  if (!p.how) return n.download ? el("div", { class: "note" }, "Download: " + n.download) : null;  // a server without Install
  if (p.how === "manual") return n.download ? el("div", { class: "note" }, "Installed by hand: " + n.download) : null;
  if (p.how === "lora") { const r = (typeof LORAS !== "undefined" ? LORAS : []).find(x => x.id === p.lora) || { id: p.lora, name: n.label };
    return el("div", { class: "dsc-cand" }, el("button", { class: "small on", onclick: async () => { try { const out = typeof loraInstallOne === "function" ? await loraInstallOne(r) : await api("/api/loras/" + p.lora + "/install", {});
      if (!(out && out.gate)) toast("Installing " + (r.name || n.label)); setTimeout(() => dscLoad(node), 1500); } catch (e) { toast(e.message, 6000); } } }, "Install"), el("span", { class: "note" }, "from its author's page, through the LoRA hub")); }
  const size = p.size_gb ? " · about " + p.size_gb + " GB" : "";
  if (mine && job.state === "running") return el("div", {}, el("div", { class: "dsc-cand" }, el("span", { class: "dsc-state scanning" }, "installing…"),
      el("button", { class: "small", onclick: () => dscDo(node, "/api/discover/install/cancel", {}, n.id) }, "Cancel")),
    el("div", { class: "dsc-cmd" }, (job.lines || []).slice(-4).join("\n") || job.shown));
  return el("div", {},
    mine && job.state === "failed" ? el("div", { class: "note vt-bad" }, job.reason) : null,
    mine && job.state === "gated" ? el("div", { class: "note vt-bad" }, "A file it needs is gated on Hugging Face" + (job.gate && job.gate.repo ? " (" + job.gate.repo + ")" : "") + ": accept its licence, then Install again.") : null,
    el("div", { class: "dsc-cand" },
      el("button", { class: "small on", disabled: !!DSC.busy || dscRunning(), title: "Runs " + p.shown, onclick: () => dscDo(node, "/api/discover/install", { need: n.id }, n.id).then(() => dscFollow(node)) },
        mine && job.state === "done" ? "Installed" : "Install"),
      mine && job.state === "gated" && job.gate && job.gate.page && typeof gateOpen === "function" ? el("button", { class: "small", onclick: () => gateOpen({ id: n.id, name: n.label }, job.gate, {
        check: () => api("/api/hf/repo?repo=" + encodeURIComponent(job.gate.repo)),
        done: () => dscDo(node, "/api/discover/install", { need: n.id }, n.id).then(() => dscFollow(node)) }) }, "Get access") : null,
      el("span", { class: "note" }, p.shown + size)));
}
// while an installer runs, the page follows it
function dscFollow(node) { clearTimeout(DSC.follow); if (!dscRunning()) return; DSC.follow = setTimeout(async () => { await dscLoad(node); dscFollow(node); }, 2500); }

function dscDraw(node) {
  const d = DSC.data, rows = [];
  if (!d) return node.replaceChildren(el("h2", {}, "Models & weights"), el("div", { class: "note" }, "Loading…"));
  if (d.error) return node.replaceChildren(el("h2", {}, "Models & weights"), el("div", { class: "note" }, "This needs a newer Studio server: " + d.error));
  const scan = d.last_scan, needs = d.needs || [];
  const shownNeeds = DSC.all ? needs : needs.filter(n => !(n.optional && n.state === "missing"));
  const groups = [];
  for (const n of shownNeeds) { let g = groups.find(x => x.name === n.engine); if (!g) groups.push(g = { name: n.engine, needs: [] }); g.needs.push(n); }
  const count = s => needs.filter(n => n.state === s && !(n.optional && s === "missing")).length;
  rows.push(el("div", { class: "dsc-head" }, el("h2", { class: "m-flat" }, "Models & weights"), el("span", { class: "grow" }),
    el("button", { class: "small", disabled: d.scanning || !!DSC.busy, title: "Search this machine again for everything the studio needs", onclick: () => dscDo(node, "/api/discover/scan", {}, "scan") },
      d.scanning ? "Scanning…" : "Scan again")));
  rows.push(el("div", { class: "note" }, d.scanning ? "Scanning this machine" + (d.progress && d.progress.dirs ? ": " + d.progress.dirs + " folders so far" : "") + "…"
    : scan ? "Last scan " + dscWhen(scan.at) + ", " + scan.dirs + " folders in " + scan.seconds + " s" + (scan.complete ? "." : " (stopped at its time limit).") : "Not scanned yet: a scan runs shortly after the Studio starts."));
  rows.push(el("div", { class: "note" }, d.note || "Nothing is moved or downloaded."));
  rows.push(el("div", { class: "note" }, count("expected") + " in place · " + count("found") + " found elsewhere · " + count("needs-conversion") + " to convert · " + count("missing") + " not on this machine"));
  const ready = needs.filter(n => n.state === "found" && !n.link && (n.candidates || []).filter(c => c.verified).length === 1);
  if (ready.length) rows.push(el("div", { class: "dsc-cand" }, el("button", { class: "primary", disabled: !!DSC.busy,
      title: "Link each one where the studio looks for it: nothing is moved, copied or downloaded",
      onclick: () => dscDo(node, "/api/discover/link-all", {}, "link-all").then(() => toast("Linked what was found on this machine")) }, "Use everything found here (" + ready.length + ")"),
    el("span", { class: "note" }, "Already on this machine: " + ready.map(n => n.label).slice(0, 4).join(", ") + (ready.length > 4 ? "…" : ""))));
  if (dscRunning()) { const j = DSC.data.installing; rows.push(el("div", { class: "note" }, "Installing " + j.label + "…")); dscFollow(node); }
  rows.push(el("label", { class: "note", style: "display:flex;gap:6px;align-items:center" }, el("input", { type: "checkbox", style: "width:auto", checked: DSC.all,
    onchange: e => { DSC.all = e.target.checked; dscDraw(node); } }), "Show optional models that are not installed"));
  for (const g of groups) rows.push(el("div", { class: "dsc-group" }, el("h3", {}, g.name), g.needs.map(n => dscRow(node, n))));
  if (scan && scan.skipped_mounts && scan.skipped_mounts.length) rows.push(el("div", { class: "note" }, "Network drives not searched: " + scan.skipped_mounts.join(", ") + " (STUDIO_SCAN_NETWORK=1 searches them)."));
  node.replaceChildren(...rows);
}

function viewDiscover() {
  const box = el("section", { class: "card", id: "discover" });
  dscDraw(box);
  dscLoad(box);
  return box;
}

if (typeof window !== "undefined") window.DISCOVER_READY = true;
