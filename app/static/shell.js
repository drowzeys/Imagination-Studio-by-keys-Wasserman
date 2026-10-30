"use strict";
// The studio's frame: a sidebar of two groups (Creative Engines, Studio Vaults) with the Agent between them, each room
// with its own tabs, Back and Home in the top bar, the render bay docked along the bottom, the ⌘K palette and the
// machine chip. Every view is still drawn by its own module; this file only decides where it sits. Settings → Studio
// layout hides and orders the rooms (settings.js roomsShown); a hidden room stays in ⌘K.
const GROUPS = [{ id: "apps", name: "", label: "Apps" }, { id: "engines", name: "Creative Engines" }, { id: "agent", name: "" }, { id: "vaults", name: "Studio Vaults" }];
const ROOMS = [
  // every tool as a one-click app (apps.js), above the engines
  { id: "apps", group: "apps", name: "Apps", key: "", tabs: ["Apps"], says: "every tool as a one-click app" },
  { id: "video", group: "engines", name: "Video", key: "1", tabs: ["Video", "Create"], says: "clips with sound, long takes, references" },
  { id: "image", group: "engines", name: "Image", key: "2", tabs: ["Image", "Create"], says: "stills, scouts, edit a picture" },
  { id: "sound", group: "engines", name: "Sound", key: "3", tabs: ["Sound", "Voice", "Music", "Foley"], says: "voices, score and sound effects" },
  { id: "story", group: "engines", name: "Story", key: "4", tabs: ["Story", "Production"], says: "the film: board, Wasserman Slate, First AD" },
  // a room of its own for a part that also sits inside a bigger page; `fold`: in the More sheet on a phone, not the bar
  { id: "cast", group: "engines", name: "Cast & Places", key: "", tabs: ["Cast & Places"], says: "a film's characters and locations, their pictures and LoRAs", fold: true },
  { id: "director", group: "engines", name: "Director", key: "", tabs: ["Director"], says: "a logline to a written, rendered, checked and cut film", fold: true },
  { id: "previs", group: "engines", name: "Previs", key: "5", tabs: ["Previs", "Motion Previs"], says: "motion from a clip, previs to guide a shot" },
  { id: "graphics", group: "engines", name: "Motion graphics", key: "", tabs: ["Motion graphics"], says: "titles, lower thirds, captions, slideshows and music videos", fold: true },
  { id: "edit", group: "engines", name: "Edit", key: "6", tabs: ["Edit", "Editor", "Graphics", "Finish"], says: "the timeline, motion graphics, the final mix and what goes to Resolve" },
  { id: "night", group: "engines", name: "Night shift", key: "", tabs: ["Night shift"], says: "a batch rendered overnight, dailies in the morning", fold: true },
  { id: "agent", group: "agent", name: "Agent", key: "", tabs: ["Agent"], says: "ask your agent; it makes it with the studio" },
  { id: "library", group: "vaults", name: "Library", key: "7", tabs: ["Library"], says: "every take" },
  { id: "looks", group: "vaults", name: "Looks", key: "8", tabs: ["Looks"], says: "LoRA styles for pictures and clips" },
  { id: "suite", group: "vaults", name: "Wasserman's Filmmaker Suite", short: "Filmmaker Suite", key: "9", tabs: ["Import"], says: "the entire Wasserman Filmmaker Suite of apps, built in" },
  { id: "nodes", group: "vaults", name: "Nodes", key: "", tabs: ["Nodes"], says: "a canvas of jobs, words to pictures to clips" },
  { id: "machine", group: "vaults", name: "Machine", key: "0", tabs: ["Machine", "Models", "Sparks", "Brains", "Updates"], says: "telemetry, Sparks, brains and updates" },
  { id: "learn", group: "vaults", name: "Learn", key: "", tabs: ["Tutorial"], says: "lessons, step by step" },
  { id: "settings", group: "vaults", name: "Settings", key: ",", tabs: ["Settings"], says: "looks, layout, presets and connections" },
];
const TAB_NAMES = { Home: "Home", Story: "Overview", Production: "Film", Image: "Overview", Video: "Overview", Create: "Create", Previs: "Overview", "Motion Previs": "Motion Previs",
  Agent: "Ask your agent", Sound: "Overview", Voice: "Voice", Music: "Music", Foley: "Sound effects", Edit: "Overview", Editor: "Timeline", Graphics: "Graphics", Finish: "Finish",
  Apps: "Apps", "Cast & Places": "Cast & Places", Director: "Director", "Motion graphics": "Motion graphics", "Night shift": "Night shift", Library: "Library", Looks: "Looks", Import: "Bring in & send out",
  Machine: "Telemetry", Models: "Models & weights", Sparks: "Sparks", Brains: "Brains & tools", Updates: "Updates", Tutorial: "Learn", Nodes: "Nodes", Settings: "Settings" };
// Create is shared by Image and Video: it sits in the room of what the prompt goes to
const roomOf = t => t === "Create" ? ROOMS.find(r => r.id === (typeof C !== "undefined" && C.goes === "video" ? "video" : "image"))
  : ROOMS.find(r => r.tabs.includes(t)) || null;
const SVG = d => '<svg viewBox="0 0 24 24" aria-hidden="true">' + d + "</svg>";
// the brand kit's module icons (brand/module-*.svg: 56-unit box, 2.5 rounded stroke), drawn inline so they take the
// colour of where they sit; Looks, Suite and Learn are the Studio's own glyphs redrawn on the same box and stroke
const KIT = d => '<svg viewBox="0 0 56 56" aria-hidden="true" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" style="stroke-width:2.5"><path d="' + d + '"/></svg>';
const ICON = {
  home: SVG('<path d="M4 11l8-7 8 7v9H4z"/><path d="M10 20v-5h4v5"/>'),
  story: KIT("M12 8H40V48H12Z M18 18H34 M18 26H34 M18 34H30"),
  image: KIT("M8 12H48V44H8Z M8 38L20 26L30 36L38 28L48 38 M35 20H35.1"),
  video: KIT("M8 12H48V44H8Z M23 21L37 28L23 35Z"),
  previs: KIT("M28 6L48 17V39L28 50L8 39V17Z M8 17L28 28L48 17 M28 28V50"),
  sound: KIT("M8 24V32 M16 16V40 M24 8V48 M32 18V38 M40 12V44 M48 24V32"),
  edit: KIT("M8 14H48 M8 28H48 M8 42H48 M17 8V20 M37 22V34 M24 36V48"),
  library: KIT("M8 12H48V46H8Z M8 22H48 M22 32H34"),
  looks: KIT("M28 8L33.4 22.6L48 28L33.4 33.4L28 48L22.6 33.4L8 28L22.6 22.6Z"),
  suite: KIT("M18 8V40 M9 31L18 40L27 31 M38 48V16 M29 25L38 16L47 25"),
  machine: KIT("M14 14H42V42H14Z M21 21H35V35H21Z M20 6V14 M36 6V14 M20 42V50 M36 42V50 M6 20H14 M6 36H14 M42 20H50 M42 36H50"),
  agent: KIT("M10 12H46V36H26L16 44V36H10Z M19 24H19.1 M28 24H28.1 M37 24H37.1"),
  nodes: KIT("M8 10H22V22H8Z M34 34H48V46H34Z M34 8H48V20H34Z M22 16C30 16 26 40 34 40 M22 16H34 M15 22V40H34"),
  settings: KIT("M28 20A8 8 0 1 1 28 36A8 8 0 1 1 28 20Z M28 6V12 M28 44V50 M6 28H12 M44 28H50 M12.4 12.4L16.7 16.7 M39.3 39.3L43.6 43.6 M12.4 43.6L16.7 39.3 M39.3 16.7L43.6 12.4"),
  learn: KIT("M48 28A20 20 0 1 1 8 28A20 20 0 1 1 48 28Z M22.2 21.5A6.1 6.1 0 0 1 33.9 23.6C33.9 27.8 28.1 28.7 28.1 32.7 M28 40H28.1"),
  about: SVG('<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5"/><path d="M12 7.7v.1"/>'),
  doc: SVG('<path d="M6 3.5h8.5L18 7v13.5H6z"/><path d="M14.5 3.5V7H18"/><path d="M9 11h6M9 14h6M9 17h4"/>'),
  update: SVG('<path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v5h-5"/>'),
  search: SVG('<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/>'),
  queue: SVG('<path d="M4 6h16M4 12h16M4 18h10"/>'),
  chev: SVG('<path d="M6 15l6-6 6 6"/>'),
  go: SVG('<path d="M9 6l6 6-6 6"/>'),
  back: SVG('<path d="M15 6l-6 6 6 6"/>'),
  // the rooms of their own (rooms.js), on the kit's box and stroke: a person and a pin, a clapper, the titles' T with
  // its motion, a moon, and the sparkles of a LoRA being made
  cast: KIT("M26 18A6 6 0 1 1 14 18A6 6 0 1 1 26 18Z M8 44C8 34 13 29 20 29C27 29 32 34 32 44 M42 46C42 46 50 37 50 31A8 8 0 1 0 34 31C34 37 42 46 42 46Z M42 31H42.1"),
  director: KIT("M8 24H48V46H8Z M8 24V16L46 8L48 16Z M20 13.5L24 20.6 M32 11L36 18"),
  graphics: KIT("M18 12H46 M32 12V44 M8 22H20 M8 30H24 M8 38H20"),
  night: KIT("M44 33A18 18 0 1 1 23 12A14 14 0 0 0 44 33Z"),
  train: KIT("M24 8L28 20L40 24L28 28L24 40L20 28L8 24L20 20Z M42 34L44 40L50 42L44 44L42 50L40 44L34 42L40 40Z"),
  apps: KIT("M8 8H24V24H8Z M32 8H48V24H32Z M8 32H24V48H8Z M32 32H48V48H32Z"),
  more: SVG('<circle cx="5.5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18.5" cy="12" r="1.2"/>'),
};
const icon = (name, cls) => { const n = el("span", { class: cls || "ico" }); n.innerHTML = ICON[name] || ICON.home; return n; };
// the marks of the models and tools the Studio runs (app/static/brand, THIRD-PARTY-MARKS.md): identification only
const MARKS = { qwen: ["brand/qwen.svg", "Qwen"], flux: ["brand/bfl-white.svg", "FLUX3-K"], minimax: ["brand/minimax.svg", "MiniMax H3"], music3: ["brand/minimax.svg", "MiniMax-Music3"], yue: ["brand/yue.png", "YuE2"],
  fish: ["brand/fish-audio.svg", "Fish Audio Drama 3"], comfy: ["brand/comfyui.svg", "ComfyUI"], tensorfold: ["brand/tensorfold.webp", "TensorFold"],
  claude: ["brand/claude.svg", "Claude"], openai: ["brand/openai.svg", "ChatGPT"], codex: ["brand/codex.svg", "Codex"], gemini: ["brand/gemini.svg", "Gemini"], grok: ["brand/grok.svg", "Grok"],
  hermes: ["brand/hermes-agent.png", "Hermes Agent"], nvidia: ["brand/nvidia.svg", "NVIDIA"] };
const mark = (id, label, big) => { const m = MARKS[id]; if (!m) return label || null; return el("span", { class: "mk" + (big ? " big" : "") }, el("img", { src: m[0], alt: "", width: big ? 40 : 18, height: big ? 40 : 18 }), label === "" ? null : label || m[1]); };
// every change of page goes on the app's own stack (Back) and the browser's (its Back and Forward); a page reached
// by the browser's Back (hashchange, opts.fromHash) is taken off the stack, not pushed again
const PAGES_KEPT = 50;
function go(t, opts = {}) { if (!TABS.includes(t)) return; moreClose(); const was = tab, h = SHELL.history;
  if (t !== was) { if (opts.fromHash && h[h.length - 1] === t) h.pop();
    else if (!opts.back && h[h.length - 1] !== was) { h.push(was); if (h.length > PAGES_KEPT) h.splice(0, h.length - PAGES_KEPT); } }
  tab = t;
  if (!opts.fromHash) { try { if (t !== was && history.pushState) history.pushState({ from: was }, "", "#" + t); else history.replaceState(history.state, "", "#" + t); } catch {} }
  if (!opts.keepScroll) window.scrollTo(0, 0); render(); }
// Back is the browser's Back when the page before this one in the browser is the app's last page (the hashchange
// then takes it off the stack): Back never adds a page the browser's Forward would have to walk through
function goBack() { const h = SHELL.history; while (h.length && (h[h.length - 1] === tab || !TABS.includes(h[h.length - 1]))) h.pop();
  const prev = h[h.length - 1]; if (!prev) return;
  let state = null; try { state = history.state; } catch {}
  if (state && state.from === prev && typeof history.back === "function") return history.back();
  h.pop(); go(prev, { back: true }); }
function goHome() { if (tab !== "Home") go("Home"); }
function goRoom(id) { const r = ROOMS.find(x => x.id === id); if (r) go(r.tabs[0]); }

const shown = list => typeof roomsShown === "function" ? roomsShown(list) : list;
const aboutOn = () => tab === "Settings" && typeof SET !== "undefined" && SET.section === "about";
function renderShell() {
  const now = tab === "Home" ? null : roomOf(tab), live = jobs.some(j => j.status === "running");
  // Home first, under the brand and above the engines (phones keep Home in the top bar: studio.css)
  const nav = [el("button", { class: "home" + (tab === "Home" ? " on" : ""), "data-room": "home", type: "button", title: "Home: start something, see what is rolling",
    "aria-current": tab === "Home" ? "page" : false, onclick: () => go("Home") }, icon("home", "ic"), el("span", { class: "lbl" }, "Home"))];
  for (const g of GROUPS) { const rooms = shown(ROOMS.filter(x => x.group === g.id)); if (!rooms.length) continue;
    if (g.name) nav.push(el("div", { class: "grp g-" + g.id, role: "presentation" }, g.name));
    for (const r of rooms) { const b = el("button", { class: now === r && !(r.id === "settings" && aboutOn()) ? "on" : "", "data-room": r.id, "data-group": r.group, "data-fold": r.fold ? "more" : false, title: r.name + ": " + r.says + (r.key ? " (⌥" + r.key + ")" : ""), "aria-current": now === r ? "page" : false, onclick: () => goRoom(r.id) },
      icon(r.id, "ic"), el("span", { class: "lbl" + (r.name.length > 18 ? " long" : "") }, r.name), r.key ? el("span", { class: "kbd" }, "⌥" + r.key) : null);
      if (r.id === "video" && live && now !== r) b.prepend(el("span", { class: "live", "aria-label": "rendering" }));
      nav.push(b);
      // About has a button of its own under Settings: the credits, licences and links (Settings → About)
      if (r.id === "settings") nav.push(el("button", { class: aboutOn() ? "on" : "", "data-room": "about", "data-group": "vaults", type: "button", title: "About: credits, licences and links",
        "aria-current": aboutOn() ? "page" : false, onclick: () => { if (typeof goSettings === "function") goSettings("about"); else go("Settings"); } }, icon("about", "ic"), el("span", { class: "lbl" }, "About"))); } }
  // phones only (studio.css): the bar keeps the engines and folds the vaults, search and updates into More
  nav.push(el("button", { class: "more" + (now && (now.group !== "engines" || now.fold) ? " on" : ""), "data-room": "more", type: "button", title: "More: the Agent, the Studio Vaults, search and updates", "aria-haspopup": "dialog", onclick: moreOpen },
    icon("more", "ic"), el("span", { class: "lbl" }, "More")));
  $("#rooms").replaceChildren(...nav);
  $("#roomname").textContent = now ? now.name : "Home";
  const back = $("#backbtn"), home = $("#homebtn"), prev = SHELL.history.filter(t => t !== tab).slice(-1)[0];
  if (back) { back.disabled = !prev; back.setAttribute("aria-disabled", prev ? "false" : "true"); back.title = prev ? "Back to " + (TAB_NAMES[prev] || prev) + " (⌥←)" : "Nothing to go back to yet"; }
  if (home) home.classList.toggle("hide", tab === "Home");
  const tabs = now && now.tabs.length > 1 ? now.tabs : [];
  $("#subtabs").replaceChildren(...tabs.map(t => el("button", { class: t === tab ? "on" : "", "aria-current": t === tab ? "page" : false, onclick: () => go(t) }, TAB_NAMES[t] || t)));
  document.body.classList.toggle("quiet", SHELL.lastTab === tab); SHELL.lastTab = tab;
}
const SHELL = { lastTab: null, tel: null, history: [], samples: [], es: null, telAt: 0, libCache: null, telWait: 2000, telPoll: null, telDog: null };
// the More sheet on phones: the Studio Vaults, ⌘K search and Updates, which the bottom bar has no room for
function moreOpen() { if ($("#moresheet")) return moreClose(); const now = tab === "Home" ? null : roomOf(tab), fresh = typeof UPD !== "undefined" && UPD.data && UPD.data.available;
  const row = (ico, label, hint, run, on, kbd) => el("button", { class: "it" + (on ? " on" : ""), type: "button", "aria-current": on ? "page" : false, onclick: () => { moreClose(); run(); } },
    icon(ico, "ico"), el("span", { class: "t" }, el("b", {}, label, kbd ? el("kbd", {}, kbd) : null), el("small", {}, hint)));
  const sheet = el("div", { id: "moresheet", onclick: e => { if (e.target.id === "moresheet") moreClose(); } },
    el("div", { class: "box", role: "dialog", "aria-label": "More" },
      shown(ROOMS.filter(r => r.group === "apps" || r.group === "agent")).map(r => row(r.id, r.name, r.says, () => goRoom(r.id), now === r)),
      // the engines' rooms of their own, which the bar has no room for
      shown(ROOMS.filter(r => r.fold)).length ? el("div", { class: "grp" }, "Creative Engines") : null,
      shown(ROOMS.filter(r => r.fold)).map(r => row(r.id, r.name, r.says, () => goRoom(r.id), now === r)),
      el("div", { class: "grp" }, "Studio Vaults"),
      shown(ROOMS.filter(r => r.group === "vaults")).map(r => row(r.id, r.name, r.says, () => goRoom(r.id), now === r, r.key ? "⌥" + r.key : null)),
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
    return el("div", { class: "lane r", title: j.title }, el("span", { class: "onair" }, "ROLLING"), j.spark ? el("span", { class: "tcode" }, typeof spkLabel === "function" ? spkLabel(j.spark) : j.spark) : null, el("b", {}, j.title),
      el("span", { class: "mini" }, el("i", { style: "width:" + share + "%" })), el("span", { class: "tcode" }, left == null ? share + "%" : left > 5 ? mmss(left) : "almost")); });
  if (!lanes.length) lanes.push(el("div", { class: "lane" }, el("span", { class: "dotl" + (queued.length ? " t" : "") }), el("span", { class: "tcode" }, queued.length ? "Next up: " + queued[0].title : "Ready. Nothing rolling.")));
  sum.replaceChildren(...lanes);
  $("#baycount").replaceChildren(...[queued.length ? queued.length + " queued" : "", done ? done + " done" : "", failed ? failed + " failed" : ""].filter(Boolean).map(t => el("span", {}, t)));
  renderRoomsLive(running.length > 0); }
function renderRoomsLive(live) { const b = document.querySelector('#rooms button[data-room="video"]'); if (!b) return; const has = b.querySelector(".live");
  if (live && !has && !b.classList.contains("on")) b.prepend(el("span", { class: "live" })); else if ((!live || b.classList.contains("on")) && has) has.remove(); }

/* ---------- the machine chip and telemetry ---------- */
const pct = (a, b) => b ? Math.round(100 * a / b) : 0;
// The chip names this machine. A DGX Spark stays "DGX Spark". A CUDA workstation is its card
// (platform_label is "CUDA · NVIDIA RTX PRO 6000 BLACKWELL … · H3-CUDA"), plus the nvidia-smi index.
const platName = () => {
  if (!S) return "";
  if (S.platform === "mac") return "Mac";
  if (S.platform !== "spark") return S.platform_label || "";
  const label = S.platform_label || "";
  if (!label || label.startsWith("DGX SPARK")) return "DGX Spark";
  const raw = (label.split(" · ")[1] || "").replace(/^NVIDIA\s+/, "");
  const card = raw.split(/\s+(?:BLACKWELL|ADA|AMPERE|MAX-Q|WORKSTATION)\b/)[0].trim();
  if (!card) return "DGX Spark";
  return S.gpu_index ? card + " · " + S.gpu_index : card;
};
// Unified vs card, from the banner machine() already wrote. Unified starts with "DGX SPARK"
// (a GB10, and the nvidia-smi failure banner). A card is the CUDA build and starts with "CUDA · ".
function cardFace() {
  return !!(S && S.platform === "spark" && (S.platform_label || "").startsWith("CUDA · "));
}
// A video time on a card is a warm sample of that canvas (where === "card"). Anything else is no number and no suffix.
// A GB10 keeps "on a DGX Spark, models loaded". A Mac keeps "on an M5 Ultra".
function estimateWhere(r) {
  const seconds = r && r.seconds;
  if (!seconds) return "";
  if (cardFace()) return r.where === "card" ? "≈ " + mmss(seconds) + " on this card" : "";
  if (S && S.platform === "spark") return "≈ " + mmss(seconds) + " on a DGX Spark, models loaded";
  return "≈ " + mmss(seconds) + " on an M5 Ultra";
}
// Voice, Music, and Bolt keep the GB10 identity in the data. On a card the label says where that was measured.
function measuredLabel(label) {
  if (!cardFace()) return label || "";
  if (label === "TensorFold CUDA · DGX Spark") return "TensorFold CUDA · measured on a DGX Spark";
  if (label === "ComfyUI · DGX Spark (CUDA)") return "ComfyUI · measured on a DGX Spark (CUDA)";
  if (label === "TensorFold CUDA · DGX Spark (Mia's Bolt recipe)") return "TensorFold CUDA · measured on a DGX Spark (Mia's Bolt recipe)";
  return label || "";
}
// The pool a card's meters lead with. A GB10 has no memory.card, so the RAM reading stays.
function cardMem(t) {
  const c = t && t.memory && t.memory.card;
  return cardFace() && c && c.total_mb ? c : null;
}
function telApply(t) { SHELL.tel = t; SHELL.telAt = Date.now();
  const card = cardMem(t);
  const memPct = card ? pct(card.used_mb, card.total_mb) : (t.memory ? 100 - pct(t.memory.available_mb, t.memory.total_mb) : null);
  SHELL.samples.push({ t: Date.now(), gpu: t.gpu ? t.gpu.util_pct : null, mem: memPct, temp: t.gpu ? t.gpu.temp_c : null, power: t.gpu ? t.gpu.power_w : null });
  if (SHELL.samples.length > 300) SHELL.samples.shift();
  const used = memPct == null ? 0 : memPct, gpu = t.gpu && t.gpu.util_pct != null ? t.gpu.util_pct : null;
  const busy = t.jobs && t.jobs.running, warn = (t.warnings || []).length;
  // the chip is green when the machine is ready and red while it rolls; a warning (a full disk) is a small amber mark
  // beside it and in the title, never the chip's own colour
  $("#mdot").className = "dotl" + (busy ? " r" : ""); $("#mpill").classList.toggle("warn", !busy && warn > 0);
  $("#mtxt").textContent = gpu != null ? gpu + "% GPU" : t.memory ? Math.round(t.memory.available_mb / 1024) + " GB free" : "";
  const gb = mb => (mb / 1024).toFixed(mb >= 102400 ? 0 : 1);
  const say = [(S && S.platform_label) || platName(), busy ? "rolling" : "ready", "GPU " + (gpu == null ? "n/a" : gpu + "%" + (t.gpu && t.gpu.temp_c != null ? " at " + t.gpu.temp_c + "°C" : "")),
    card ? "memory " + gb(card.used_mb) + " of " + gb(card.total_mb) + " GB (" + used + "%)" : (t.memory ? "memory " + gb(t.memory.total_mb - t.memory.available_mb) + " of " + gb(t.memory.total_mb) + " GB (" + used + "%)" : ""),
    // a card's memory is the memory clause above; this second clause is only where the page still leads with RAM
    !card && t.memory && t.memory.card ? "card " + gb(t.memory.card.used_mb) + " of " + gb(t.memory.card.total_mb) + " GB" : "",
    // the Studio's Bolt brain (app/bolt.py) while it holds memory
    t.engines && t.engines.bolt && t.engines.bolt.state === "loaded" ? "Bolt brain " + (t.engines.bolt.memory_gb != null ? t.engines.bolt.memory_gb + " GB" : "loaded") : "",
    t.disk ? Math.round(t.disk.free_gb) + " GB disk free" : "",
    warn ? t.warnings[0] : ""].filter(Boolean).join(" · ");
  $("#mpill").title = say + " · open the Machine room"; $("#mpill").setAttribute("aria-label", say);
  if (tab === "Machine" && typeof machineLive === "function") machineLive(t);
  if (tab === "Home" && typeof homeLive === "function") homeLive(t); }
// the event stream, and while it is down a poll every 3 s; a lost stream is opened again after 2, 4, 8 … 30 s. A
// stream that goes quiet without an error (closed, or a half-open tunnel: no telemetry for 15 s, where it comes every
// 2 s) is treated as lost too, by a watchdog every 5 s
const TEL_QUIET = 15000;
function telStart() {
  const poll = async () => { try { telApply(await api("/api/telemetry")); } catch { SHELL.tel = null; } };
  const polling = on => { if (on && !SHELL.telPoll) { poll(); SHELL.telPoll = setInterval(poll, 3000); } else if (!on && SHELL.telPoll) { clearInterval(SHELL.telPoll); SHELL.telPoll = null; } };
  if (!("EventSource" in window)) return polling(true);
  const open = () => { let es; try { es = new EventSource("/api/events"); } catch { return polling(true); } SHELL.es = es;
    es.addEventListener("telemetry", e => { try { telApply(JSON.parse(e.data)); } catch {} });
    es.onopen = () => { SHELL.telWait = 2000; polling(false); };
    es.onerror = () => lost(es); };
  const lost = es => { es.close(); if (SHELL.es === es) SHELL.es = null; polling(true);
    setTimeout(open, SHELL.telWait); SHELL.telWait = Math.min(30000, SHELL.telWait * 2); };
  open();
  if (!SHELL.telDog) SHELL.telDog = setInterval(() => { const es = SHELL.es;
    if (es && (es.readyState === 2 || (es.readyState === 1 && SHELL.telAt && Date.now() - SHELL.telAt > TEL_QUIET))) lost(es); }, 5000); }

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
    a("agent", "Ask your agent", "Agent · Claude, Codex, Hermes", () => go("Agent"), "claude codex hermes blender blockout mcp chat"),
    a("back", "Back", "the last page · ⌥←", goBack, "previous return"),
    a("home", "Home", "the main screen · ⌥H", goHome, "start generate composer"),
    a("settings", "Settings", "looks, layout, presets · ⌥,", () => typeof goSettings === "function" ? goSettings("appearance") : go("Settings"), "preferences appearance type size glow poster accent"),
    a("settings", "Studio layout", "Settings · show, hide, order rooms", () => typeof goSettings === "function" ? goSettings("layout") : go("Settings"), "sidebar rooms hide show order"),
    a("settings", "Connections", "Settings · subscriptions, local models", () => typeof goSettings === "function" ? goSettings("connections") : go("Settings"), "local model ollama lm studio vllm llama hermes oauth sign in"),
    a("apps", "Apps", "every tool as a one-click app", () => go("Apps"), "apps gallery tools swap angles lipsync upscale captions"),
    ...(typeof appsPalette === "function" ? appsPalette(a) : []),
    a("nodes", "Nodes", "a canvas of jobs", () => go("Nodes"), "graph comfyui canvas pipeline"),
    a("previs", "Motion Previs", "Previs", () => go("Motion Previs"), "pose depth skeleton motion reference"),
    a("sound", "Record a voice line", "Sound · Fish Audio Drama 3", () => go("Voice"), "fish drama speech"),
    a("sound", "Write a song", "Sound · YuE2 or MiniMax-Music3", () => go("Music"), "yue2 minimax music score"),
    a("story", "Plan a film", "Story", () => go("Production"), "slate bible shots first ad"),
    a("cast", "Cast & Places", "Creative Engines · characters and locations", () => go("Cast & Places"), "bible characters cast locations places faces sheets dialogue"),
    a("director", "Director", "Creative Engines · a logline to a cut film", () => go("Director"), "direct logline whole film automatic"),
    a("night", "Night shift", "Creative Engines · render overnight", () => go("Night shift"), "overnight tonight batch dailies sleep render the rest"),
    a("graphics", "Motion graphics", "Creative Engines · titles, captions, slideshows", () => go("Motion graphics"), "titles lower third captions slideshow music video kinetic type hyperframes"),
    a("edit", "Finish", "Edit · the final cut and mix", () => go("Finish"), "final mix tesseract premiere after effects resolve"),
    a("story", "Rough cut", "Production · a film cut to a song", () => { if (typeof slateMode === "function") slateMode("slate", "rough"); else go("Production"); }, "rough cut beat song trailer resolve"),
    a("train", "Train a LoRA", "Looks · from your own clips", () => { if (typeof LK !== "undefined") LK.train = true; go("Looks"); }, "train lora training character look"),
    a("library", "Restyle, extend or fix a clip", "Library · under every clip", () => go("Library"), "restyle extend fix revise"),
    a("suite", "Wasserman's Filmmaker Suite", "ScriptBreak · Cork Board · Blockout … built in", () => go("Import"), "import storyboard master canvas fountain suite send out"),
    a("library", "Library", "Studio Vaults", () => go("Library"), "files renders takes"),
    a("looks", "Looks", "LoRA styles", () => go("Looks"), "lora hub style horror"),
    a("edit", "Timeline", "Edit · Resolve export", () => go("Editor"), "editor fcpxml edl resolve"),
    a("machine", "Telemetry", "Machine", () => go("Machine"), "gpu memory temperature disk storage"),
    a("machine", "Sparks", "Machine", () => go("Sparks"), "workers multi spark"),
    a("machine", "Brains & tools", "subscriptions, MCP", () => go("Brains"), "claude codex gemini grok local llm blender blockout"),
    a("update", "Check for updates", "Machine", () => go("Updates"), "update version release"),
    a("queue", "Open the render bay", "Queue", () => bayOpen(true), "queue jobs"),
    a("learn", "Learn Imagination Studio", lessonCount() + " lessons", () => go("Tutorial"), "tutorial help"),
    // a room hidden from the sidebar (Settings → Studio layout) is still here
    ...ROOMS.filter(r => !shown([r]).length).map(r => a(r.id, r.name, "hidden from the sidebar", () => goRoom(r.id), r.says)),
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
  $("#helpbtn").onclick = () => go("Tutorial");
  const back = $("#backbtn"), home = $("#homebtn");
  if (back) { back.replaceChildren(icon("back", "ic"), el("span", { class: "t" }, "Back")); back.onclick = goBack; }
  if (home) { home.replaceChildren(icon("home", "ic"), el("span", { class: "t" }, "Home")); home.onclick = goHome; } $("#kbtn").onclick = palOpen; $("#mpill").onclick = () => go("Machine");
  $("#bayhead").onclick = e => { if (!e.target.closest("button:not(.chev)")) bayOpen(!$("#bay").classList.contains("open")); };
  try { if (localStorage.getItem("bay-open")) bayOpen(true); } catch {}
  document.addEventListener("keydown", e => { const typing = e.target.matches && e.target.matches("input,textarea,select,[contenteditable]");
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("#palette") ? palClose() : palOpen(); return; }
    if (e.altKey && !e.metaKey && !e.ctrlKey && /^Digit[0-9]$/.test(e.code)) { const r = roomForKey(e.code.slice(5)); if (r) { e.preventDefault(); goRoom(r.id); } return; }
    if (e.altKey && !e.metaKey && !e.ctrlKey && e.code === "Comma") { e.preventDefault(); go("Settings"); return; }
    if (e.altKey && !e.metaKey && !e.ctrlKey && !typing && e.code === "ArrowLeft") { e.preventDefault(); goBack(); return; }
    if (e.altKey && !e.metaKey && !e.ctrlKey && e.code === "KeyH") { e.preventDefault(); goHome(); return; }
    if (e.key === "Escape" && $("#moresheet")) { moreClose(); return; }
    if (!typing && e.key === "/" && !$("#palette")) { e.preventDefault(); palOpen(); } });
  // a player that leaves the page any other way (a list redrawn in place) is let go of too
  if (typeof MutationObserver === "function") new MutationObserver(list => { for (const m of list) for (const n of m.removedNodes) if (n.nodeType === 1 && !n.isConnected) releaseMedia(n); })
    .observe($("#view"), { childList: true, subtree: true });
  window.addEventListener("hashchange", () => { palClose(); const t = decodeURIComponent(location.hash.slice(1)); if (TABS.includes(t) && t !== tab) go(t, { fromHash: true }); });
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
    el("div", { class: "note", style: "margin:0;font-size:13.5px" }, "The full controls stay on the left. A recipe only sets engine, passes and size."));
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
