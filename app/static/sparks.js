"use strict";
/* Sparks: how many DGX Sparks a job may use, and the worker Sparks behind this queue (app/sparks.py).
   SPK.n is the number every job queued from this page carries ("sparks"); the chooser is the same control wherever
   it is drawn (the queue, the last step of Create, Production, Slate, the Sparks tab). */
const SPK = { data: null, n: 1, form: { address: "", folder: "", via: null }, plans: {}, takes: {}, ticks: 0 };
// what this machine is called on the page: a DGX Spark, or a Mac that leads the Sparks (app/sparks.py)
const spkHere = d => "this " + ((d && d.this && d.this.kind) || "Spark");
// what a machine is called on the page: sparks/labels.json in the studio folder (app/sparks.py, Hub.labels), else its own name.
// A job and a take record the machine's own name; every place that shows one asks here.
function spkLabel(name) { const names = SPK.data && SPK.data.names; return (names && names[name]) || name; }
// which Spark rendered a clip (sparks/takes.jsonl in the studio folder), for a take's line
const spkTake = path => { const t = SPK.takes[path]; return t && t.spark ? "on " + spkLabel(t.spark) : null; };
const spkLimit = () => (SPK.data && SPK.data.limit) || 5;
// A discrete card's banner starts with "CUDA ·". A GB10 starts with "DGX SPARK". Anything else keeps the original page.
const spkFleet = () => !!(SPK.data && SPK.data.supported && (SPK.data.workers || []).length);
// shell.js cardFace() is the one helper. This file is also loaded alone by its page test.
const onCard = () => typeof cardFace === "function" ? cardFace() : (typeof S !== "undefined" && !!S && S.platform === "spark" && (S.platform_label || "").startsWith("CUDA · "));
const spkQuiet = () => onCard() && !spkFleet();
// "5 of 8 available" when more are asked for than there are; else how many will be used.
// A count that includes a card does not call this machine a Spark.
const spkCount = (d, n) => {
  if (!d || !d.supported) return "this machine";
  if (n > d.available) return d.available + " of " + n + " available";
  if (n === 1) return spkHere(d) + " alone";
  if (onCard()) { const others = n - 1; return spkHere(d) + " and " + others + " Spark" + (others === 1 ? "" : "s"); }
  return n + " of " + d.available + " available Sparks";
};
const spkSig = d => !d ? "" : JSON.stringify([d.available, d.default, d.workers.map(w => [w.address, w.name, w.state, w.working, w.enabled, w.why, w.job, w.memory_gb, w.usable]), d.this && d.this.job]);
async function spkLoad(draw) { const was = spkSig(SPK.data); try { const first = !SPK.data; SPK.data = await api("/api/sparks");
    if (first) { let kept = null; try { kept = Number(localStorage.getItem("sparks")); } catch {} SPK.n = SPK.data.supported ? Math.max(1, Math.min(kept || SPK.data.default || 1, SPK.data.limit)) : 1; }
  } catch { return; }
  if (SPK.data.supported && !(SPK.ticks++ % 5)) api("/api/sparks/takes").then(t => { SPK.takes = t; }, () => {});
  spkMachinesDraw();  // each machine's own readings move every few seconds: drawn in place, not by redrawing the whole page
  if (spkSig(SPK.data) === was) return; SPK.plans = {}; spkQueue();
  if (draw && typeof tab !== "undefined" && !document.activeElement.matches("input,textarea,select")) render(); }
function spkSet(n) { SPK.n = SPK.data && SPK.data.supported ? Math.max(1, Math.min(Number(n) || 1, spkLimit())) : 1; SPK.plans = {};
  try { localStorage.setItem("sparks", String(SPK.n)); } catch {} api("/api/sparks", { sparks: SPK.n }).catch(() => {}); spkQueue(); render(); }
// What the number will do for this job, asked of the app (POST /api/sparks/plan) and said under the chooser
async function spkPlan(node, what) { const body = { sparks: SPK.n, kind: what.kind || "video", params: what.params || {}, clips: what.clips || 1, seconds: what.seconds || 0 }, key = JSON.stringify(body);
  let r = SPK.plans[key]; if (!r) { try { r = SPK.plans[key] = await api("/api/sparks/plan", body); } catch { return; } }
  node.replaceChildren(...spkSays(r)); }
function spkSays(r) { if (!r.supported) return [r.says];
  const e = r.estimate;
  return [el("b", {}, r.count + ". "), r.says,
    e && r.mode === "parallel" ? " Estimate: about " + mmss(e.seconds) + " for all of them, against " + mmss(e.one_spark) + " on one Spark (" + e.faster + "x; " + e.note + ")." : "",
    r.without.length ? el("div", {}, "Not used: " + r.without.map(w => w.name + " (" + w.why + ")").join("; ") + ".") : ""]; }
// The chooser: 1 to the limit. `what` says what is about to be queued: { kind, params, clips, seconds }.
function sparksPick(what = {}, label = "Sparks") { const d = SPK.data, says = el("div", { class: "note", style: "margin-top:4px" });
  if (!d) return null;
  if (!d.supported) return el("div", { class: "sparks" }, el("label", {}, label), el("div", { class: "row", style: "gap:5px;align-items:center" }, el("button", { class: "small on fit", disabled: true }, "1"), el("span", { class: "note" }, d.note)));
  if (spkQuiet()) return null;
  spkPlan(says, what);
  return el("div", { class: "sparks", "data-keep": "" }, el("label", { title: onCard() ? "How many machines this job may use, this GPU counted. More than there are is allowed: what is there is used." : "How many DGX Sparks this job may use, this one counted. More than there are is allowed: what is there is used." }, label),
    el("div", { class: "row", style: "gap:5px;align-items:center;flex-wrap:wrap" },
      Array.from({ length: d.limit }, (_, i) => el("button", { type: "button", class: "small fit" + (SPK.n === i + 1 ? " on" : "") + (i + 1 > d.available ? " far" : ""), "aria-pressed": String(SPK.n === i + 1),
        title: i + 1 > d.available ? "Only " + d.available + " available now: " + d.available + " will be used" : (onCard() ? (i ? "this GPU and " + i + " Spark" + (i === 1 ? "" : "s") : "this GPU") : (i + 1) + (i ? " Sparks" : " Spark")), onclick: () => spkSet(i + 1) }, String(i + 1))),
      el("button", { type: "button", class: "small fit", title: "The list of Sparks: add one, check them, update and start them", onclick: () => { tab = "Sparks"; history.replaceState(null, "", "#Sparks"); render(); } }, "Sparks…")), says); }
// The queue's own line, on every tab
function spkQueue() { const box = document.querySelector("#sparks"); if (!box || !SPK.data) return; const d = SPK.data;
  if (spkQuiet()) { if (SPK.n !== 1) SPK.n = 1; box.style.display = "none"; box.replaceChildren(); return; }
  if (onCard()) box.style.display = "";
  if (!d.supported) { box.replaceChildren(el("div", { class: "note" }, "Sparks: 1. " + d.note)); return; }
  box.replaceChildren(el("div", { class: "sparks" }, el("div", { class: "row", style: "gap:4px;align-items:center;flex-wrap:wrap" }, el("span", { class: "note fit", title: onCard() ? "How many machines a job queued now may use, this GPU counted" : "How many DGX Sparks a job queued now may use, this one counted" }, "Sparks"),
    Array.from({ length: d.limit }, (_, i) => el("button", { type: "button", class: "small fit" + (SPK.n === i + 1 ? " on" : "") + (i + 1 > d.available ? " far" : ""), onclick: () => spkSet(i + 1) }, String(i + 1)))),
    el("div", { class: "note" }, spkCount(d, SPK.n) + (SPK.n > 1 ? ": queued clips render one per Spark." : ".")))); }
const spkDo = async (path, body, method) => { try { SPK.data = await api(path, body, method); SPK.plans = {}; } catch (e) { toast(e.message, 6000); } spkQueue(); render(); };
const spkHas = can => !can ? "" : [can.fasth3 ? "FastH3 clips (text, first and last frame)" : "no FastH3", can.ref2va ? "Ref2VA" + (can.ref2va_turbo ? " with Turbo" : "") : "no Ref2VA", can.image ? "image model" : "no image model",
  can.loras ? can.loras + " LoRA" + (can.loras === 1 ? "" : "s") : "no LoRAs", can.ffmpeg ? "ffmpeg" : "no ffmpeg"].join(" · ");
const spkDot = state => el("span", { style: "display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px;background:" + ({ ready: "var(--ok)", busy: "var(--warn)", update: "var(--warn)", stopped: "var(--dim)", new: "var(--faint)" }[state] || "var(--bad)") });
function spkAdd(d, title = "Add a Spark") {
  const f = SPK.form, mac = d.head === "mac", here = spkHere(d);
  if (mac && f.via === null) f.via = d.via || "";
  return el("div", {}, el("h3", {}, title),
    el("div", { class: "note" }, "Its address as you would type it after ssh. It needs the Studio installed once (oneshot-setup-spark.sh, with the FastH3 models); “Update and start” then sends " + here + "'s Studio code and starts its app. No password is asked and nothing is installed as root: if ssh asks for a password, set up a key login first."
      + (mac ? " Through: a Spark this Mac reaches by ssh and that reaches the other Spark by its own key login (the Spark that leads them, e.g. user@spark-head); leave it empty when this Mac reaches the Spark itself." : "")),
    el("div", { class: "row" }, field("Address", bind(f, "address", el("input", { placeholder: "10.0.10.5  or  user@spark-5" }))), field("Studio folder there, under the home folder", bind(f, "folder", el("input", { placeholder: d.folder }))),
      mac ? field("Through", bind(f, "via", el("input", { placeholder: "user@the-spark-that-leads" }))) : null,
      el("button", { class: "primary fit", onclick: async () => { if (!f.address.trim()) return toast("Give the Spark's address"); await spkDo("/api/sparks/workers", { address: f.address.trim(), folder: f.folder.trim() || null, ...(mac && (f.via || "").trim() ? { via: f.via.trim() } : {}) }); f.address = ""; } }, "Add")));
}
// ---- each machine's own status: this Spark and every worker, side by side ------------------------------------------------------
// Every machine (this one and each worker) says what it is doing, its GPU, memory, disk, temperature and what it can render. The
// figures are the ones each Studio app reports about itself (telemetry.compact, on /api/spark/self); the head already looks at every
// connected worker every 20 s, so the panel only reads what /api/sparks holds: nothing here asks a worker anything.
const spkFree = gb => gb == null ? "–" : (gb >= 100 || Number.isInteger(gb) ? Math.round(gb) : gb.toFixed(1)) + " GB";
const spkGB = mb => mb == null ? "–" : spkFree(mb / 1000);
const spkAge = at => { if (!at) return ""; const s = Math.max(0, Math.round(Date.now() / 1000 - at)); return s < 90 ? s + " s ago" : Math.round(s / 60) + " min ago"; };
const spkTone = (pct, hot = 85, warm = 65) => pct == null ? "var(--faint)" : pct >= hot ? "var(--red)" : pct >= warm ? "var(--amber)" : "var(--green)";
function spkMeter(label, value, pct, tone) { return el("div", { class: "spkrow", style: "margin-top:7px" }, el("div", { class: "kv" }, el("span", {}, label), el("b", {}, value)),
  pct == null ? null : el("div", { class: "gaugebar", style: "margin-top:3px" }, el("i", { style: "width:" + Math.max(1, Math.min(100, pct)) + "%;--c:" + (tone || spkTone(pct)) }))); }
function spkMachine(m) { const s = m.status, g = s && s.gpu, mem = s && s.memory, card = s && s.card, disk = s && s.disk, can = m.can || {}, doing = s && s.job;
  const says = m.working || m.says || "", busy = !!(doing || m.job || m.working || m.state === "busy"), live = !!s;
  const what = doing ? doing.title + (doing.progress ? " · " + doing.progress.stage + " " + doing.progress.at + "/" + doing.progress.of : "")
    : m.job ? "a clip of this Studio's queue (job " + m.job + ")" : m.state === "busy" ? "a job of its own" : m.state === "ready" ? "nothing, waiting for a clip" : "nothing (" + (m.says || m.state) + ")";
  const models = [can.fasth3 ? "FastH3" : null, can.image ? "Qwen picture models" : null, can.ref2va ? "Ref2VA" + (can.ref2va_turbo ? " + Turbo" : "") : null].filter(Boolean).join(", ");
  return el("div", { class: "seg spkm", style: "margin:0" },
    el("div", { class: "seghead" }, spkDot(busy ? "busy" : m.state), el("b", {}, m.label), m.head ? el("span", { class: "tag2" }, "head") : null, el("span", { style: "flex:1" }),
      el("span", { class: "pill " + (busy ? "running" : m.state === "ready" || m.head ? "done" : m.state === "new" || m.state === "stopped" ? "queued" : "failed") }, busy ? "rendering" : m.head ? "idle" : says)),
    el("div", { class: "note", style: "margin-top:2px" }, [m.host && m.host !== m.label ? m.host : null, m.version ? "Studio " + m.version : null].filter(Boolean).join(" · ")),
    el("div", { class: "kv", style: "margin-top:8px" }, el("span", {}, "Doing"), el("b", { style: "text-align:right;white-space:normal" }, what)),
    live && g ? spkMeter("GPU", (g.util_pct != null ? g.util_pct + "%" : "–") + (g.power_w != null ? " · " + Math.round(g.power_w) + " W" : ""), g.util_pct, spkTone(g.util_pct, 95, 60))
      : el("div", { class: "kv", style: "margin-top:7px" }, el("span", {}, "GPU"), el("b", {}, live ? "no GPU reading on this machine" : "not reported")),
    el("div", { class: "kv", style: "margin-top:7px" }, el("span", {}, "Temperature"), el("b", {}, g && g.temp_c != null ? Math.round(g.temp_c) + " °C" : "not reported")),
    live && mem && mem.total_mb ? spkMeter(mem.unified ? "Memory (the GPU shares it)" : "RAM", spkGB(mem.used_mb) + " used · " + spkGB(mem.free_mb) + " free of " + spkGB(mem.total_mb), mem.used_pct)
      : m.memory_gb != null ? el("div", { class: "kv", style: "margin-top:7px" }, el("span", {}, "Memory"), el("b", {}, m.memory_gb + " GB free")) : el("div", { class: "kv", style: "margin-top:7px" }, el("span", {}, "Memory"), el("b", {}, "not reported")),
    live && card && card.total_mb ? spkMeter("GPU memory", spkGB(card.used_mb) + " used · " + spkGB(card.free_mb) + " free of " + spkGB(card.total_mb), 100 * card.used_mb / card.total_mb) : null,
    live && disk && disk.free_gb != null ? spkMeter("Disk", spkFree(disk.free_gb) + " free of " + spkFree(disk.total_gb), disk.used_pct, spkTone(disk.used_pct, 90, 80))
      : m.disk_gb != null ? el("div", { class: "kv", style: "margin-top:7px" }, el("span", {}, "Disk"), el("b", {}, m.disk_gb + " GB free")) : null,
    live && s.cpu && s.cpu.load1 != null ? el("div", { class: "kv", style: "margin-top:7px" }, el("span", {}, "Processor"), el("b", {}, "load " + s.cpu.load1 + " on " + (s.cpu.cores || "?") + " cores")) : null,
    el("div", { class: "kv", style: "margin-top:7px" }, el("span", {}, "Models"), el("b", { style: "text-align:right;white-space:normal" }, m.can ? (can.loras || 0) + " LoRA" + (can.loras === 1 ? "" : "s") + (models ? " · " + models : "") : "not reported")),
    live && s.warnings && s.warnings.length ? el("div", { class: "note", style: "color:var(--warn);margin-top:6px" }, s.warnings[0]) : null,
    m.why ? el("div", { class: "note", style: "color:var(--warn);margin-top:6px" }, m.why) : null,
    !live && !m.head && m.can ? el("div", { class: "note", style: "margin-top:6px" }, "Live readings come with this Studio's newer code: “Update and start” on the Sparks tab.") : null,
    el("div", { class: "note", style: "margin-top:6px" }, live ? "Read " + spkAge(s.at) : m.checked ? "Last looked at " + spkAge(m.checked) : "")); }
function spkMachineCards() { const d = SPK.data;
  if (!d) return [el("div", { class: "note" }, "Loading…")];
  if (!d.supported) return [el("div", { class: "note" }, d.note)];
  const t = d.this, all = [{ head: true, label: t.label || t.name, host: t.name, state: t.job ? "busy" : "ready", says: t.job ? "rendering" : "idle", job: t.job, status: t.status, can: t.can, version: t.version, memory_gb: t.memory_gb, disk_gb: t.disk_gb },
    ...d.workers.map(w => ({ ...w, label: w.label || w.name, host: w.address }))];
  return all.map(spkMachine); }
function spkMachinesDraw() { if (typeof document === "undefined" || !document.querySelectorAll) return; for (const box of document.querySelectorAll("[data-spk-machines]")) box.replaceChildren(...spkMachineCards()); }
function spkMachinesPanel() { return el("div", { class: "stack", style: "margin:14px 0" }, el("h3", { style: "margin:0" }, "Each machine"),
  el("div", { class: "note" }, "What each one is doing and how it is holding up, as it reports it. Read only."),
  el("div", { "data-spk-machines": "", style: "display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px" }, ...spkMachineCards())); }
function viewSparks() { const box = el("section", { class: "card" }, el("h2", {}, "Sparks")), d = SPK.data;
  if (!d) { spkLoad(true).then(() => { if (SPK.data && tab === "Sparks") render(); }); return put(box, el("div", { class: "note" }, "Loading…")), box; }
  if (!d.supported) return put(box, el("div", { class: "note" }, d.note), sparksPick()), box;
  if (spkQuiet()) return put(box,
    el("p", { class: "note m-flat" }, "This GPU renders every clip. Add a DGX Spark when you have another one. Clips that do not have to wait on each other then run one per machine, and the number shows up on the queue."),
    spkAdd(d, "Add a DGX Spark")), box;
  const f = SPK.form, a = encodeURIComponent, mac = d.head === "mac", here = spkHere(d);
  if (mac && f.via === null) f.via = d.via || "";
  const row = w => el("div", { class: "seg" }, el("div", { class: "seghead" }, spkDot(w.working ? "busy" : w.state), el("b", {}, w.label || w.name), el("span", { class: "note" }, w.address + (w.via ? " through " + w.via : "")),
      el("span", { class: "pill " + (w.state === "ready" ? "done" : w.state === "busy" || w.working ? "running" : w.state === "new" || w.state === "stopped" ? "queued" : "failed") }, w.working || w.says), el("span", { style: "flex:1" }),
      el("label", { class: "note fit", style: "display:flex;gap:5px;align-items:center;margin:0", title: "Unticked, this Spark is given no clips; it stays in the list" }, el("input", { type: "checkbox", style: "width:auto", checked: w.enabled, onchange: e => spkDo("/api/sparks/workers/" + a(w.address), { enabled: e.target.checked }) }), "use"),
      el("button", { class: "small", disabled: !!w.working, title: "Look at this Spark: reachable, its Studio's version, what it can render, free memory, busy or idle. Nothing on it is changed.", onclick: () => spkDo("/api/sparks/workers/" + a(w.address) + "/check", {}) }, "Check"),
      el("button", { class: "small", disabled: !!w.working, title: "Send " + here + "'s Studio code to it (rsync over ssh, as you), start its app on its own 127.0.0.1, and check it again. Models are not sent.", onclick: () => spkDo("/api/sparks/workers/" + a(w.address) + "/update", {}) }, "Update and start"),
      el("button", { class: "small", disabled: !!w.working, title: "Stop its Studio app and its ComfyUI server: its memory is free again", onclick: () => confirm("Stop the Studio app on " + (w.label || w.name) + " and free its memory?") && spkDo("/api/sparks/workers/" + a(w.address) + "/stop", {}) }, "Stop"),
      el("button", { class: "small", title: "Take this Spark off the list (what runs on it is left as it is)", onclick: () => confirm("Remove " + (w.label || w.name) + " from the list?") && spkDo("/api/sparks/workers/" + a(w.address), undefined, "DELETE") }, "Remove")),
    el("div", { class: "note" }, [w.version ? "Studio " + w.version : null, w.guard ? "memory guard" : w.state === "ready" || w.state === "busy" ? null : w.version ? "memory guard not confirmed" : null, w.memory_gb != null ? w.memory_gb + " GB of memory available" : null,
      w.disk_gb != null ? w.disk_gb + " GB of disk free" : null, w.job ? "rendering job " + w.job : null].filter(Boolean).join(" · ")),
    w.can ? el("div", { class: "note" }, "Renders: " + spkHas(w.can)) : null,
    w.why || (w.unusable && w.state === "ready") ? el("div", { class: "note", style: "color:var(--warn)" }, w.why || w.unusable) : null);
  put(box, el("div", { class: "note" }, (onCard() ? "A job may use this GPU and the DGX Sparks you add. Choose how many here, in the queue, or where you queue a job: " + here + " counts as one. Several clips render one per machine, side by side; one clip renders on one machine. " : "A job may use more than one DGX Spark. Choose how many here, in the queue, or where you queue a job: " + here + " counts as one. Several clips render one per Spark, side by side; one clip renders on one Spark. ") + "Each other Spark runs its own copy of this Studio, closed to the network: this app reaches it through an ssh tunnel it opens itself, so you need a key login to it as the user who runs this app."
      + (mac ? " From a Mac only single FastH3 clips a Spark makes with its own settings go to a Spark (no MiniMax H3 Turbo quality, no 2x decoder preset, no text encoder file of this Mac's); the rest renders on this Mac, and the page says why. A Spark this Mac cannot reach goes through one that can (“Through”): that Spark logs in to it with its own key." : "")),
    sparksPick({ clips: 8 }, "Sparks for new jobs"),
    spkMachinesPanel(),
    el("h3", {}, here[0].toUpperCase() + here.slice(1) + " and " + (d.listed - 1) + " other" + (d.listed === 2 ? "" : "s") + " · " + d.available + " available"),
    el("div", { class: "seg" }, el("div", { class: "seghead" }, spkDot(d.this.state), el("b", {}, d.this.label || d.this.name), el("span", { class: "note" }, here + (d.this.label && d.this.label !== d.this.name ? " · " + d.this.name : "")), el("span", { class: "pill " + (d.this.job ? "running" : "done") }, d.this.job ? "rendering" : "idle")),
      el("div", { class: "note" }, ["Studio " + d.this.version, d.this.guard !== false ? "memory guard" : null, d.this.memory_gb != null ? d.this.memory_gb + " GB of memory available" : null, d.this.disk_gb != null ? d.this.disk_gb + " GB of disk free" : null].filter(Boolean).join(" · ")), el("div", { class: "note" }, "Renders: " + spkHas(d.this.can))),
    d.workers.map(row),
    el("div", { class: "row", style: "margin-top:10px" }, el("button", { class: "fit", disabled: !d.workers.length, onclick: () => spkDo("/api/sparks/check", {}) }, "Check all"), el("span")),
    el("h3", {}, "Add a Spark"),
    el("div", { class: "note" }, "Its address as you would type it after ssh. It needs the Studio installed once (oneshot-setup-spark.sh, with the FastH3 models); “Update and start” then sends " + here + "'s Studio code and starts its app. No password is asked and nothing is installed as root: if ssh asks for a password, set up a key login first."
      + (mac ? " Through: a Spark this Mac reaches by ssh and that reaches the other Spark by its own key login (the Spark that leads them, e.g. user@spark-head); leave it empty when this Mac reaches the Spark itself." : "")),
    el("div", { class: "row" }, field("Address", bind(f, "address", el("input", { placeholder: "10.0.10.5  or  user@spark-5" }))), field("Studio folder there, under the home folder", bind(f, "folder", el("input", { placeholder: d.folder }))),
      mac ? field("Through", bind(f, "via", el("input", { placeholder: "user@the-spark-that-leads" }))) : null,
      el("button", { class: "primary fit", onclick: async () => { if (!f.address.trim()) return toast("Give the Spark's address"); await spkDo("/api/sparks/workers", { address: f.address.trim(), folder: f.folder.trim() || null, ...(mac && (f.via || "").trim() ? { via: f.via.trim() } : {}) }); f.address = ""; } }, "Add")));
  return box; }
if (typeof window !== "undefined" && window.addEventListener) { window.addEventListener("load", () => setTimeout(() => spkLoad(true), 400)); setInterval(() => spkLoad(true), 4000); }
