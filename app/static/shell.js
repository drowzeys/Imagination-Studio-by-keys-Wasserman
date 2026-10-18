"use strict";
// The studio's frame: a sidebar of two groups (Creative Engines, Studio Vaults), each room with its own tabs, the
// render bay docked along the bottom, the ⌘K palette and the machine chip. Every view is still drawn by its own
// module; this file only decides where it sits.
const GROUPS = [{ id: "engines", name: "Creative Engines" }, { id: "vaults", name: "Studio Vaults" }];
const ROOMS = [
  { id: "story", group: "engines", name: "Story", key: "1", tabs: ["Story", "Production"], says: "the film: board, Wasserman Slate, First AD" },
  { id: "image", group: "engines", name: "Image", key: "2", tabs: ["Image", "Create"], says: "stills, scouts, edit a picture" },
  { id: "video", group: "engines", name: "Video", key: "3", tabs: ["Video", "Create"], says: "clips with sound, long takes, references" },
  { id: "previs", group: "engines", name: "Previs", key: "4", tabs: ["Previs", "Motion Previs", "Agent"], says: "motion from a clip, sets built by your agent" },
  { id: "sound", group: "engines", name: "Sound", key: "5", tabs: ["Sound", "Voice", "Music"], says: "voices and score" },
  { id: "edit", group: "engines", name: "Edit", key: "6", tabs: ["Edit", "Editor"], says: "the timeline and what goes to Resolve" },
  { id: "library", group: "vaults", name: "Library", key: "7", tabs: ["Library"], says: "every take" },
  { id: "looks", group: "vaults", name: "Looks", key: "8", tabs: ["Looks"], says: "LoRA styles for pictures and clips" },
  { id: "suite", group: "vaults", name: "Suite", key: "9", tabs: ["Import"], says: "bring in from the filmmaker suite, send out" },
  { id: "machine", group: "vaults", name: "Machine", key: "0", tabs: ["Machine", "Sparks", "Brains", "Updates"], says: "telemetry, Sparks, brains and updates" },
  { id: "learn", group: "vaults", name: "Learn", key: "", tabs: ["Tutorial"], says: "lessons, step by step" },
];
const TAB_NAMES = { Home: "Home", Story: "Overview", Production: "Film", Image: "Overview", Video: "Overview", Create: "Create", Previs: "Overview", "Motion Previs": "Motion Previs",
  Agent: "Ask your agent", Sound: "Overview", Voice: "Voice", Music: "Music", Edit: "Overview", Editor: "Timeline", Library: "Library", Looks: "Looks", Import: "Bring in & send out",
  Machine: "Telemetry", Sparks: "Sparks", Brains: "Brains & tools", Updates: "Updates", Tutorial: "Learn" };
// Create is shared by Image and Video: it sits in the room of what the prompt goes to
const roomOf = t => t === "Create" ? ROOMS.find(r => r.id === (typeof C !== "undefined" && C.goes === "video" ? "video" : "image"))
  : ROOMS.find(r => r.tabs.includes(t)) || null;
const SVG = d => '<svg viewBox="0 0 24 24" aria-hidden="true">' + d + "</svg>";
const ICON = {
  home: SVG('<path d="M4 11l8-7 8 7v9H4z"/><path d="M10 20v-5h4v5"/>'),
  story: SVG('<path d="M6 3.5h9l3.5 3.5v13.5H6z"/><path d="M15 3.5V7h3.5"/><path d="M9 11.5h6.5M9 15h4.5"/>'),
  image: SVG('<rect x="3.5" y="5" width="17" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M4 17.5l5-5 4 4 2.5-2.5 4.5 4.5"/>'),
  video: SVG('<rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10.5l5-3v9l-5-3z"/>'),
  previs: SVG('<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/>'),
  sound: SVG('<path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 10.5v3"/>'),
  edit: SVG('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 12h18M9 5v7M15 12v7"/>'),
  library: SVG('<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>'),
  looks: SVG('<path d="M12 3.5l2.3 6.2 6.2 2.3-6.2 2.3L12 20.5l-2.3-6.2L3.5 12l6.2-2.3z"/>'),
  suite: SVG('<path d="M7.5 4v12M3.5 12l4 4 4-4"/><path d="M16.5 20V8M12.5 12l4-4 4 4"/>'),
  machine: SVG('<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9.5 2.5V6M14.5 2.5V6M9.5 18v3.5M14.5 18v3.5M2.5 9.5H6M2.5 14.5H6M18 9.5h3.5M18 14.5h3.5"/>'),
  learn: SVG('<circle cx="12" cy="12" r="9"/><path d="M9.5 9.2a2.6 2.6 0 0 1 5 .9c0 1.8-2.5 2.2-2.5 3.9"/><path d="M12 17.2v.1"/>'),
  doc: SVG('<path d="M6 3.5h8.5L18 7v13.5H6z"/><path d="M14.5 3.5V7H18"/><path d="M9 11h6M9 14h6M9 17h4"/>'),
  update: SVG('<path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v5h-5"/>'),
  search: SVG('<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/>'),
  queue: SVG('<path d="M4 6h16M4 12h16M4 18h10"/>'),
  chev: SVG('<path d="M6 15l6-6 6 6"/>'),
  go: SVG('<path d="M9 6l6 6-6 6"/>'),
  more: SVG('<circle cx="5.5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18.5" cy="12" r="1.2"/>'),
};
const icon = (name, cls) => { const n = el("span", { class: cls || "ico" }); n.innerHTML = ICON[name] || ICON.home; return n; };
// the marks of the models and tools the Studio runs (app/static/brand, THIRD-PARTY-MARKS.md): identification only
const MARKS = { qwen: ["brand/qwen.svg", "Qwen"], flux: ["brand/bfl-white.svg", "FLUX3-K"], minimax: ["brand/minimax.svg", "MiniMax H3"], music3: ["brand/minimax.svg", "MiniMax-Music3"],
  fish: ["brand/fish-audio.svg", "Fish Audio Drama 3"], comfy: ["brand/comfyui.svg", "ComfyUI"], tensorfold: ["brand/tensorfold.webp", "TensorFold"],
  claude: ["brand/claude.svg", "Claude"], openai: ["brand/openai.svg", "ChatGPT"], codex: ["brand/codex.svg", "Codex"], gemini: ["brand/gemini.svg", "Gemini"], grok: ["brand/grok.svg", "Grok"] };
const mark = (id, label, big) => { const m = MARKS[id]; if (!m) return label || null; return el("span", { class: "mk" + (big ? " big" : "") }, el("img", { src: m[0], alt: "", width: big ? 40 : 18, height: big ? 40 : 18 }), label === "" ? null : label || m[1]); };
function go(t, opts = {}) { if (!TABS.includes(t)) return; moreClose(); tab = t; history.replaceState(null, "", "#" + t); if (!opts.keepScroll) window.scrollTo(0, 0); render(); }
function goRoom(id) { const r = ROOMS.find(x => x.id === id); if (r) go(r.tabs[0]); }

function renderShell() {
  const now = tab === "Home" ? null : roomOf(tab), live = jobs.some(j => j.status === "running");
  const nav = [];
  for (const g of GROUPS) { nav.push(el("div", { class: "grp", role: "presentation" }, g.name));
    for (const r of ROOMS.filter(x => x.group === g.id)) { const b = el("button", { class: now === r ? "on" : "", "data-room": r.id, "data-group": r.group, title: r.name + ": " + r.says + (r.key ? " (⌥" + r.key + ")" : ""), "aria-current": now === r ? "page" : false, onclick: () => goRoom(r.id) },
      icon(r.id, "ic"), el("span", { class: "lbl" }, r.name), r.key ? el("span", { class: "kbd" }, "⌥" + r.key) : null);
      if (r.id === "video" && live && now !== r) b.prepend(el("span", { class: "live", "aria-label": "rendering" }));
      nav.push(b); } }
  // phones only (studio.css): the bar keeps the engines and folds the vaults, search and updates into More
  nav.push(el("button", { class: "more" + (now && now.group === "vaults" ? " on" : ""), "data-room": "more", type: "button", title: "More: the Studio Vaults, search and updates", "aria-haspopup": "dialog", onclick: moreOpen },
    icon("more", "ic"), el("span", { class: "lbl" }, "More")));
  $("#rooms").replaceChildren(...nav);
  $("#roomname").textContent = now ? now.name : "Home";
  const tabs = now && now.tabs.length > 1 ? now.tabs : [];
  $("#subtabs").replaceChildren(...tabs.map(t => el("button", { class: t === tab ? "on" : "", "aria-current": t === tab ? "page" : false, onclick: () => go(t) }, TAB_NAMES[t] || t)));
  document.body.classList.toggle("quiet", SHELL.lastTab === tab); SHELL.lastTab = tab;
}
const SHELL = { lastTab: null, tel: null, history: [], es: null, telAt: 0, libCache: null, telWait: 2000, telPoll: null };
// the More sheet on phones: the Studio Vaults, ⌘K search and Updates, which the bottom bar has no room for
function moreOpen() { if ($("#moresheet")) return moreClose(); const now = tab === "Home" ? null : roomOf(tab), fresh = typeof UPD !== "undefined" && UPD.data && UPD.data.available;
  const row = (ico, label, hint, run, on, kbd) => el("button", { class: "it" + (on ? " on" : ""), type: "button", "aria-current": on ? "page" : false, onclick: () => { moreClose(); run(); } },
    icon(ico, "ico"), el("span", { class: "t" }, el("b", {}, label, kbd ? el("kbd", {}, kbd) : null), el("small", {}, hint)));
  const sheet = el("div", { id: "moresheet", onclick: e => { if (e.target.id === "moresheet") moreClose(); } },
    el("div", { class: "box", role: "dialog", "aria-label": "More" }, el("div", { class: "grp" }, "Studio Vaults"),
      ROOMS.filter(r => r.group === "vaults").map(r => row(r.id, r.name, r.says, () => goRoom(r.id), now === r)),
      el("div", { class: "grp" }, "Studio"), row("search", "Search", "takes, settings and actions", palOpen, false, "⌘K"),
      row("update", "Updates", fresh ? "an update is ready to install" : (S && S.version ? "Studio " + S.version + " · " : "") + "check for a new version", () => go("Updates"), tab === "Updates")));
  document.body.append(sheet); const first = sheet.querySelector && sheet.querySelector("button"); if (first) first.focus(); return sheet; }
function moreClose() { const s = $("#moresheet"); if (s) s.remove(); }
// a path under the studio folder, said from that folder: the server keeps answering absolute paths (the suite and the
// Sparks need them); what the page shows of them, a readout, a log or a tooltip, drops the folder in front
const relHome = s => { const h = S && S.home ? String(S.home).replace(/\/+$/, "") : ""; if (!h || typeof s !== "string") return s; return s === h ? "the studio folder" : s.split(h + "/").join(""); };
// a view that is torn down lets go of its players: Chrome caps the media players a page may hold
function releaseMedia(root) { if (!root || !root.querySelectorAll) return;
  for (const m of [...(root.matches && root.matches("video,audio") ? [root] : []), ...root.querySelectorAll("video,audio")]) {
    try { m.pause(); m.removeAttribute("src"); m.load(); } catch {} } }

/* ---------- the render bay ---------- */
function bayOpen(open) { const bay = $("#bay"); if (!bay) return; bay.classList.toggle("open", open); try { localStorage.setItem("bay-open", open ? "1" : ""); } catch {} }
function jobShare(job) { const p = job.progress, elapsed = job.elapsed != null ? job.elapsed : (job.started ? ((job.finished || Date.now() / 1000) - job.started) : null);
  return { share: job.estimate && elapsed != null ? Math.min(99, Math.max(3, Math.round(100 * elapsed / job.estimate))) : (p ? Math.round(100 * p.at / p.of) : 3),
    left: job.estimate && elapsed != null ? Math.max(0, job.estimate - elapsed) : null }; }
function bayUpdate() { const sum = $("#baysum"); if (!sum) return;
  const running = jobs.filter(j => j.status === "running"), queued = jobs.filter(j => j.status === "queued"), done = jobs.filter(j => j.status === "done").length, failed = jobs.filter(j => j.status === "failed").length;
  const lanes = running.map(j => { const { share, left } = jobShare(j);
    return el("div", { class: "lane r", title: j.title }, el("span", { class: "onair" }, "ROLLING"), j.spark ? el("span", { class: "tcode" }, j.spark) : null, el("b", {}, j.title),
      el("span", { class: "mini" }, el("i", { style: "width:" + share + "%" })), el("span", { class: "tcode" }, left == null ? share + "%" : left > 5 ? mmss(left) : "almost")); });
  if (!lanes.length) lanes.push(el("div", { class: "lane" }, el("span", { class: "dotl" + (queued.length ? " t" : "") }), el("span", { class: "tcode" }, queued.length ? "Next up: " + queued[0].title : "Ready. Nothing rolling.")));
  sum.replaceChildren(...lanes);
  $("#baycount").replaceChildren(...[queued.length ? queued.length + " queued" : "", done ? done + " done" : "", failed ? failed + " failed" : ""].filter(Boolean).map(t => el("span", {}, t)));
  renderRoomsLive(running.length > 0); }
function renderRoomsLive(live) { const b = document.querySelector('#rooms button[data-room="video"]'); if (!b) return; const has = b.querySelector(".live");
  if (live && !has && !b.classList.contains("on")) b.prepend(el("span", { class: "live" })); else if ((!live || b.classList.contains("on")) && has) has.remove(); }

/* ---------- the machine chip and telemetry ---------- */
const pct = (a, b) => b ? Math.round(100 * a / b) : 0;
const platName = () => !S ? "" : S.platform === "spark" ? "DGX Spark" : S.platform === "mac" ? "Mac" : S.platform_label;
function telApply(t) { SHELL.tel = t; SHELL.telAt = Date.now();
  SHELL.history.push({ t: Date.now(), gpu: t.gpu ? t.gpu.util_pct : null, mem: t.memory ? 100 - pct(t.memory.available_mb, t.memory.total_mb) : null, temp: t.gpu ? t.gpu.temp_c : null, power: t.gpu ? t.gpu.power_w : null });
  if (SHELL.history.length > 300) SHELL.history.shift();
  const used = t.memory ? 100 - pct(t.memory.available_mb, t.memory.total_mb) : 0, gpu = t.gpu && t.gpu.util_pct != null ? t.gpu.util_pct : null;
  const busy = t.jobs && t.jobs.running, warn = (t.warnings || []).length;
  $("#mdot").className = "dotl" + (busy ? " r" : warn ? " t" : "");
  $("#mtxt").textContent = gpu != null ? gpu + "% GPU" : t.memory ? Math.round(t.memory.available_mb / 1024) + " GB free" : "";
  const gb = mb => (mb / 1024).toFixed(mb >= 102400 ? 0 : 1);
  const say = [(S && S.platform_label) || platName(), busy ? "rolling" : "ready", "GPU " + (gpu == null ? "n/a" : gpu + "%" + (t.gpu && t.gpu.temp_c != null ? " at " + t.gpu.temp_c + "°C" : "")),
    t.memory ? "memory " + gb(t.memory.total_mb - t.memory.available_mb) + " of " + gb(t.memory.total_mb) + " GB (" + used + "%)" : "", t.disk ? Math.round(t.disk.free_gb) + " GB disk free" : "",
    warn ? t.warnings[0] : ""].filter(Boolean).join(" · ");
  $("#mpill").title = say + " · open the Machine room"; $("#mpill").setAttribute("aria-label", say);
  if (tab === "Machine" && typeof machineLive === "function") machineLive(t);
  if (tab === "Home" && typeof homeLive === "function") homeLive(t); }
// the event stream, and while it is down a poll every 3 s; a lost stream is opened again after 2, 4, 8 … 30 s
function telStart() {
  const poll = async () => { try { telApply(await api("/api/telemetry")); } catch { SHELL.tel = null; } };
  const polling = on => { if (on && !SHELL.telPoll) { poll(); SHELL.telPoll = setInterval(poll, 3000); } else if (!on && SHELL.telPoll) { clearInterval(SHELL.telPoll); SHELL.telPoll = null; } };
  if (!("EventSource" in window)) return polling(true);
  const open = () => { let es; try { es = new EventSource("/api/events"); } catch { return polling(true); } SHELL.es = es;
    es.addEventListener("telemetry", e => { try { telApply(JSON.parse(e.data)); } catch {} });
    es.onopen = () => { SHELL.telWait = 2000; polling(false); };
    es.onerror = () => { es.close(); if (SHELL.es === es) SHELL.es = null; polling(true);
      setTimeout(open, SHELL.telWait); SHELL.telWait = Math.min(30000, SHELL.telWait * 2); }; };
  open(); }

/* ---------- ⌘K ---------- */
const PAL = { q: "", at: 0, items: [] };
const lessonCount = () => { try { return lessons().length; } catch { return 23; } };
function palActions() { const a = (ico, label, hint, run, words = "") => ({ ico, label, hint, run, words: (label + " " + hint + " " + words).toLowerCase() });
  const create = (set, step = 0) => () => { Object.assign(C, set); C.step = step; sync(); C.prompt = ""; go("Create"); };
  return [
    a("video", "New clip with sound", "Video", create({ goes: "video", start: "text" }), "video h3 fasth3 text"),
    a("image", "New still or scouts", "Image", create({ goes: "image" }), "image picture qwen flux"),
    a("image", "Edit a picture", "Image", create({ goes: "edit" }), "re-draw"),
    a("video", "Long take, up to 30 minutes", "Video", () => { C.goes = "video"; C.start = "text"; if (S && S.long) C.seconds = Object.keys(S.long)[0]; sync(); go("Create"); }, "long video chain"),
    a("video", "Wasserman Sharp Spec", "Video", () => { if (typeof sharpCreate === "function") { sharpCreate(); go("Create"); } }, "sam locked recipe"),
    a("previs", "Ask your agent", "Previs · Blender · Blockout", () => go("Agent"), "claude codex blender blockout mcp"),
    a("previs", "Motion Previs", "Previs", () => go("Motion Previs"), "pose depth skeleton motion reference"),
    a("sound", "Record a voice line", "Sound · Fish Audio Drama 3", () => go("Voice"), "fish drama speech"),
    a("sound", "Write a song", "Sound · MiniMax-Music3", () => go("Music"), "minimax music score"),
    a("story", "Plan a film", "Story", () => go("Production"), "slate bible shots first ad"),
    a("suite", "Bring in from the suite", "ScriptBreak · Cork Board · Blockout …", () => go("Import"), "import storyboard master canvas fountain suite send out"),
    a("library", "Library", "Studio Vaults", () => go("Library"), "files renders takes"),
    a("looks", "Looks", "LoRA styles", () => go("Looks"), "lora hub style horror"),
    a("edit", "Timeline", "Edit · Resolve export", () => go("Editor"), "editor fcpxml edl resolve"),
    a("machine", "Telemetry", "Machine", () => go("Machine"), "gpu memory temperature disk storage"),
    a("machine", "Sparks", "Machine", () => go("Sparks"), "workers multi spark"),
    a("machine", "Brains & tools", "subscriptions, MCP", () => go("Brains"), "claude codex gemini grok local llm blender blockout"),
    a("update", "Check for updates", "Machine", () => go("Updates"), "update version release"),
    a("queue", "Open the render bay", "Queue", () => bayOpen(true), "queue jobs"),
    a("learn", "Learn Imagination Studio", lessonCount() + " lessons", () => go("Tutorial"), "tutorial help"),
  ]; }
async function palOpen() { if ($("#palette")) return; PAL.q = ""; PAL.at = 0;
  const input = el("input", { placeholder: "Search takes, settings and actions…", "aria-label": "Search", autocomplete: "off", spellcheck: "false" });
  const res = el("div", { class: "res", role: "listbox" });
  const box = el("div", { id: "palette", onclick: e => { if (e.target.id === "palette") palClose(); } }, el("div", { class: "box", role: "dialog", "aria-label": "Command palette" }, input, res,
    el("div", { class: "foot" }, el("span", {}, "↑↓ move"), el("span", {}, "⏎ open"), el("span", {}, "esc close"))));
  document.body.append(box); input.focus();
  const draw = () => { const q = PAL.q.trim().toLowerCase(), words = q.split(/\s+/).filter(Boolean), hit = s => words.every(w => s.includes(w));
    const acts = palActions().filter(x => !q || hit(x.words));
    const files = (SHELL.libCache || []).filter(m => q && hit(m.path.toLowerCase())).slice(0, 6).map(m => ({ ico: m, label: m.path.split("/").pop(), hint: m.folder || m.kind,
      run: () => { go("Library"); setTimeout(() => toast("Opened the Library: " + m.path.split("/").pop()), 50); } }));
    const js = jobs.filter(j => q && hit((j.title + " " + j.kind + " " + j.status).toLowerCase())).slice(0, 5).map(j => ({ ico: "queue", label: j.title, hint: j.status, run: () => bayOpen(true) }));
    PAL.items = [...acts, ...js, ...files]; PAL.at = Math.min(PAL.at, Math.max(0, PAL.items.length - 1));
    const row = (x, i) => el("button", { class: "it" + (i === PAL.at ? " on" : ""), role: "option", onmouseenter: () => { PAL.at = i; [...res.querySelectorAll(".it")].forEach((n, k) => n.classList.toggle("on", k === i)); }, onclick: () => { palClose(); x.run(); } },
      typeof x.ico === "object" ? el("span", { class: "ico" }, x.ico.kind === "video" ? el("video", { src: file(x.ico.path) + "#t=0.5", muted: true, preload: "metadata" }) : x.ico.kind === "image" ? el("img", { src: file(x.ico.path), loading: "lazy" }) : icon("sound", "ic")) : icon(x.ico, "ico"),
      el("span", {}, x.label), el("small", {}, x.hint));
    let i = 0; const out = [];
    if (acts.length) out.push(el("div", { class: "grp" }, q ? "Actions" : "Go to or start"), ...acts.map(x => row(x, i++)));
    if (js.length) out.push(el("div", { class: "grp" }, "Queue"), ...js.map(x => row(x, i++)));
    if (files.length) out.push(el("div", { class: "grp" }, "Library"), ...files.map(x => row(x, i++)));
    if (!out.length) out.push(el("div", { class: "note", style: "padding:14px" }, "Nothing matches “" + PAL.q + "”."));
    releaseMedia(res); res.replaceChildren(...out); const on = res.querySelector(".it.on"); if (on) on.scrollIntoView({ block: "nearest" }); };
  input.addEventListener("input", () => { PAL.q = input.value; PAL.at = 0; draw(); });
  input.addEventListener("keydown", e => { if (e.key === "ArrowDown") { PAL.at = Math.min(PAL.items.length - 1, PAL.at + 1); draw(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { PAL.at = Math.max(0, PAL.at - 1); draw(); e.preventDefault(); }
    else if (e.key === "Enter") { const x = PAL.items[PAL.at]; if (x) { palClose(); x.run(); } } else if (e.key === "Escape") palClose(); });
  draw(); api("/api/library").then(list => { SHELL.libCache = list; if ($("#palette")) draw(); }).catch(() => {}); }
function palClose() { const p = $("#palette"); if (p) { p.remove(); releaseMedia(p); } }

// ⌥1 to ⌥9 and ⌥0: the rooms that carry a key, in the sidebar's order
const roomForKey = d => ROOMS.find(r => r.key === d) || null;
function shellStart() {
  const up = $("#studio-updates-open"); up.replaceChildren(icon("update", "ic"), el("span", { class: "t" }, "Updates")); up.onclick = () => go("Updates");
  $("#helpbtn").innerHTML = ICON.learn; $("#baychev").innerHTML = ICON.chev;
  $("#helpbtn").onclick = () => go("Tutorial"); $("#kbtn").onclick = palOpen; $("#mpill").onclick = () => go("Machine");
  $("#bayhead").onclick = e => { if (!e.target.closest("button:not(.chev)")) bayOpen(!$("#bay").classList.contains("open")); };
  try { if (localStorage.getItem("bay-open")) bayOpen(true); } catch {}
  document.addEventListener("keydown", e => { const typing = e.target.matches && e.target.matches("input,textarea,select,[contenteditable]");
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("#palette") ? palClose() : palOpen(); return; }
    if (e.altKey && !e.metaKey && !e.ctrlKey && /^Digit[0-9]$/.test(e.code)) { const r = roomForKey(e.code.slice(5)); if (r) { e.preventDefault(); goRoom(r.id); } return; }
    if (e.key === "Escape" && $("#moresheet")) { moreClose(); return; }
    if (!typing && e.key === "/" && !$("#palette")) { e.preventDefault(); palOpen(); } });
  // a player that leaves the page any other way (a list redrawn in place) is let go of too
  if (typeof MutationObserver === "function") new MutationObserver(list => { for (const m of list) for (const n of m.removedNodes) if (n.nodeType === 1 && !n.isConnected) releaseMedia(n); })
    .observe($("#view"), { childList: true, subtree: true });
  window.addEventListener("hashchange", () => { palClose(); const t = decodeURIComponent(location.hash.slice(1)); if (TABS.includes(t) && t !== tab) go(t); });
  if (S) { $("#plat").textContent = platName(); $("#mpill").title = (S.platform_label || platName()) + " · open the Machine room"; }
  if (typeof updatesWatch === "function") updatesWatch();
  telStart(); }

/* ---------- the take sheet beside Create: recipes in plain words, the setup at a glance, one Roll button ---------- */
const STEP_NAMES = ["Intent", "First frame", "Words and sound", "Prompt", "Render"];
function takeRecipes() { const final = UPSCALED["720p"] && S.presets[UPSCALED["720p"][0]] ? { res: "720p", up: true, label: "Final " + UPSCALED["720p"][1].replace(/^\d+x\d+ ?/, "").replace(/[()]/g, "") || "Final" } : { res: "720p", up: false, label: "Final 720p" };
  return [
    { id: "quick", title: "Quick look", says: "4 passes · 864 × 480", on: () => C.engine === "fasth3" && C.passes === "4" && C.res === "480p" && !C.up && !customOn(), set: () => { Object.assign(C, { engine: "fasth3", passes: "4", res: "480p", up: false, steps: "" }); changed(); } },
    S.sharp ? { id: "sharp", title: "Sharp Spec", says: "Sam's locked recipe · 1376 × 768", on: sharpOn, set: sharpCreate } : null,
    { id: "final", title: final.label, says: "8 passes · " + (final.up ? UPSCALED["720p"][1] : "1280 × 720"), on: () => C.engine === "fasth3" && C.passes === "8" && C.res === "720p" && !!C.up === final.up && !customOn(), set: () => { Object.assign(C, { engine: "fasth3", passes: "8", res: "720p", up: final.up, steps: "" }); changed(); } },
  ].filter(Boolean); }
function takeSheet() {
  const video = C.goes === "video", est = el("div", { class: "note", style: "margin:0" });
  const row = (k, v) => el("div", { class: "kv", style: "padding:9px 0;border-bottom:1px solid var(--line)" }, el("span", {}, k), el("b", { style: "text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:62%" }, v));
  const out = output() || { label: "" }, size = out.size ? out.size.join(" × ") : "";
  const startWord = { text: "text only", scout: "a scout image", own: "your picture", refs: "references" }[C.start];
  const roll = () => { if (C.step !== 4) { C.step = 4; render(); window.scrollTo({ top: 0, behavior: "smooth" }); return; } const b = $("#view section.card button.hot"); if (b && !b.disabled) b.click(); else toast("Finish the steps marked on the left first"); };
  if (video && !isLong()) showEstimate(C, est);
  return el("div", { class: "panel stack", style: "position:sticky;top:calc(var(--head) + var(--sub) + 18px);gap:16px" },
    el("div", { class: "kv" }, el("span", { class: "eyebrow" }, "Take sheet"), el("span", {}, "step " + (C.step + 1) + " of 5 · " + STEP_NAMES[C.step])),
    video && !isRef() ? el("div", { class: "stack", style: "gap:8px" }, takeRecipes().map(r => el("button", { class: "recipe-row" + (r.on() ? " on" : ""), onclick: r.set }, el("b", {}, r.title), el("span", {}, r.says)))) : null,
    el("div", {}, row("Making", video ? (isLong() ? "a long take" : "a clip with sound") : C.goes === "image" ? "stills" : "a picture edit"),
      video ? row("Starts from", startWord) : null, video ? row("Engine", (C.engine === "fasth3" ? "FastH3 · " + (customOn() ? C.steps : C.passes) + " passes" : "MiniMax H3")) : null,
      video ? row("Size", size || out.label) : row("Size", C.imageSize.replace("x", " × ")), video ? row("Length", isLong() ? (S.long[C.seconds] || C.seconds + " s") : exact(framesOf(C))) : null,
      row("Seed", String(C.seed)), lorasOf(C).length ? row("Looks", lorasOf(C).length + " LoRA" + (lorasOf(C).length > 1 ? "s" : "")) : null,
      isRef() ? row("References", refCount() + " of " + (S.reference_total || 12)) : null),
    est,
    video ? el("button", { class: "hot", style: "width:100%", onclick: roll }, C.step === 4 ? "Roll this take" : "Go to Render →") : null,
    el("div", { class: "note", style: "margin:0;font-size:12px" }, "The full controls stay on the left. A recipe only sets engine, passes and size."));
}
// what the prompt goes to, as one small control over Create: the Image and Video rooms open it already set
function intentBar() { const opts = { video: "Video with sound", image: "Image", edit: "Edit a picture" };
  const marks = C.goes === "video" ? [mark("minimax"), S && S.platform === "spark" ? mark("comfy") : null, mark("tensorfold")] : [mark("qwen", "Qwen 2.1"), mark("flux")];
  return el("div", { class: "createbar" }, el("span", { class: "eyebrow" }, "The prompt goes to"),
    el("div", { class: "seg-ctl", role: "tablist", "aria-label": "The prompt goes to" }, Object.entries(opts).map(([k, label]) => el("button", { type: "button", role: "tab", "aria-selected": C.goes === k ? "true" : "false", class: C.goes === k ? "on" : "",
      onclick: () => { if (C.goes === k) return; C.goes = k; C.step = 0; changed(); } }, label))),
    el("div", { class: "marks" }, marks)); }
function createLayout(node) { const bar = intentBar(); if (C.goes === "edit") return el("div", {}, bar, node);
  return el("div", {}, bar, el("div", { class: "split", style: "grid-template-columns:minmax(0,1fr) 330px" }, node, takeSheet())); }
