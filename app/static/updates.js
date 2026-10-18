"use strict";
// Machine → Updates: what is installed, which branch it follows, what is new, and one button to update and restart.
// No reload until the user chooses it: unsaved creative work stays on screen. The page asks the server only while
// something is happening (a check, an update, the restart after it), and once when it opens.
const UPD = { data: null, said: "", acting: false, waiting: false, back: false, session: null, timer: null };
async function updRequest(action, body = {}) {
  const r = await fetch("/api/updates" + (action ? "/" + action : ""), action ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || data.detail || "Could not reach Studio updates.");
  return data; }
function updTake(d) {
  if (UPD.waiting && !d.updating && d.session && UPD.session !== null && d.session !== UPD.session) { UPD.back = true; UPD.waiting = false; }
  else if (UPD.waiting && !d.updating && d.session === UPD.session && UPD.data && UPD.data.updating) UPD.waiting = false;  // it ended without a restart: the last result says why
  if (d.updating && !UPD.waiting) { UPD.waiting = true; if (UPD.session === null) UPD.session = d.session || ""; }
  UPD.data = d; UPD.said = ""; }
const updBusy = () => !!(UPD.data && (UPD.data.checking || UPD.data.updating)) || UPD.waiting;
function updPoll() { clearTimeout(UPD.timer); UPD.timer = null; if (updBusy()) UPD.timer = setTimeout(updRefresh, 2000); }
async function updRefresh() {
  try { updTake(await updRequest()); } catch (e) { UPD.said = UPD.waiting ? "Studio is restarting. Waiting to reconnect…" : e.message; }
  updShow(); updPoll(); }
async function updAct(name, body) { if (UPD.acting) return; UPD.acting = true; updShow();
  try { if (name === "install") { UPD.session = UPD.data ? UPD.data.session || "" : ""; UPD.waiting = true; }
    updTake(await updRequest(name, body)); }
  catch (e) { if (name === "install") UPD.waiting = false; UPD.said = e.message; }
  UPD.acting = false; updShow(); updPoll(); }
// the sidebar's Updates button shows when an update is ready, and the open panel redraws
function updShow() { const b = document.getElementById("studio-updates-open"); if (b && b.classList) b.classList.toggle("has", !!(UPD.data && UPD.data.available));
  if (tab === "Updates") render(); }
function updatesWatch() { updRefresh(); }
const updDay = iso => { const d = iso ? new Date(iso) : null; return d && !isNaN(d) ? d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : ""; };
const updWhen = t => { if (!t) return "never"; const s = Math.max(0, Date.now() / 1000 - t); return s < 60 ? "just now" : s < 3600 ? Math.round(s / 60) + " min ago" : s < 86400 ? Math.round(s / 3600) + " h ago" : new Date(t * 1000).toLocaleDateString(); };
function viewUpdates() {
  if (!UPD.data && !UPD.said && !UPD.timer) updRefresh();
  const d = UPD.data || {}, rel = d.latest_release, follow = d.following || d.branch;
  const row = (k, v) => v ? el("div", { class: "kv updrow" }, el("span", {}, k), el("b", {}, v)) : null;
  const status = UPD.back ? "Studio is back online. Reload when you have saved your work." : UPD.waiting ? "Updating. Studio restarts when the update is in place…" : UPD.said || d.message || "Loading update settings…";
  return el("div", {},
    pageHead("Studio Vault", "Updates", "Updates wait until your jobs and the render engine are idle. Projects, media and models stay in place."),
    el("div", { class: "split" },
      el("div", { class: "panel stack", id: "updpanel" },
        el("div", { class: "kv" }, el("span", { class: "eyebrow" }, "This installation"), el("span", { class: "dotl" + (d.available ? " t" : UPD.waiting ? " r" : "") })),
        el("div", {}, row("Installed", [d.installed_version, d.installed_commit].filter(Boolean).join(" · ") || "unknown"),
          row("Following", follow ? (d.source ? d.source + " · " : "") + follow : ""),
          row("Ready to install", d.available ? d.latest_version || (d.target || "").slice(0, 8) : ""),
          rel && rel.tag ? el("div", { class: "kv updrow" }, el("span", {}, "Latest on GitHub: ", rel.url ? el("a", { href: rel.url, target: "_blank", rel: "noopener" }, rel.tag) : el("b", {}, rel.tag),
            updDay(rel.published_at) ? " · " + updDay(rel.published_at) : "")) : null,
          row("Last checked", updWhen(d.checked_at))),
        el("p", { class: "updstatus", role: "status", "aria-live": "polite" }, status),
        UPD.back ? el("button", { class: "primary", onclick: () => location.reload() }, "Reload Studio") :
          el("div", { class: "chips" },
            el("button", { onclick: () => updAct("check"), disabled: UPD.acting || d.checking || d.updating || UPD.waiting }, d.checking ? "Checking…" : "Check for updates"),
            el("button", { class: "primary", onclick: () => updAct("install"), disabled: UPD.acting || !d.available || !d.managed || d.updating || d.checking || UPD.waiting }, "Update and restart")),
        d.managed === false ? el("div", { class: "note", style: "margin:0" }, "Start this installation with scripts/app.sh to install updates. Worker Sparks update through the Sparks tab.") : null),
      el("div", { class: "stack" },
        el("div", { class: "panel stack" },
          el("label", { class: "switch" }, el("input", { type: "checkbox", checked: !!d.automatic, disabled: UPD.acting || d.updating || (d.managed === false && !d.automatic), onchange: e => updAct("settings", { automatic: e.target.checked }) }),
            el("span", {}, "Automatic updates", el("small", {}, "Check every six hours while Studio is open. Install when idle."))),
          d.last_result ? el("div", { class: "note", style: "margin:0" }, el("b", {}, "Last result: "), d.last_result.message) : null),
        el("details", { class: "fold" }, el("summary", {}, "How updates work"),
          el("div", { class: "note", style: "margin:0" }, "Studio fast-forwards the branch it follows on GitHub; a release is shown for information and never installed by itself. Off by default. Local code changes pause updates. If an updated app cannot start, Studio restores the previous code and turns automatic updates off.")))));
}
