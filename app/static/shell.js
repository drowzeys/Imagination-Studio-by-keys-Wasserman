"use strict";
// The studio's frame: five rooms with their own tabs, the render bay docked along the bottom, the ⌘K palette and the
// machine pill. Every view is still drawn by its own module; this file only decides where it sits.
const ROOMS = [
  { id: "write", name: "Write", key: "1", tabs: ["Production", "Import"], says: "the film: scenes, cast, shots" },
  { id: "shoot", name: "Shoot", key: "2", tabs: ["Create", "Motion Previs", "Agent"], says: "one take at a time" },
  { id: "sound", name: "Sound", key: "3", tabs: ["Voice", "Music"], says: "voices and score" },
  { id: "cut", name: "Cut", key: "4", tabs: ["Library", "Editor"], says: "pick takes, put them in order" },
  { id: "machine", name: "Machine", key: "5", tabs: ["Machine", "Sparks", "Brains"], says: "the Sparks, models and connections" },
];
const TAB_NAMES = { Home: "Stage", Production: "Film", Import: "Bring in", Create: "Create", "Motion Previs": "Motion Previs", Agent: "Ask your agent", Voice: "Voice",
  Music: "Music", Library: "Library", Editor: "Timeline", Machine: "Telemetry", Sparks: "Sparks", Brains: "Brains & tools", Tutorial: "Learn" };
const roomOf = t => ROOMS.find(r => r.tabs.includes(t)) || null;
const LAST_TAB = (() => { try { return JSON.parse(localStorage.getItem("room-tabs") || "{}"); } catch { return {}; } })();
const ICON = {
  update: '<svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>',
  help: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.2a2.6 2.6 0 0 1 5 .9c0 1.8-2.5 2.2-2.5 3.9"/><path d="M12 17.2v.1"/></svg>',
  chev: '<svg viewBox="0 0 24 24"><path d="M6 15l6-6 6 6"/></svg>',
};
function go(t, opts = {}) { if (!TABS.includes(t)) return; tab = t; const r = roomOf(t); if (r) { LAST_TAB[r.id] = t; try { localStorage.setItem("room-tabs", JSON.stringify(LAST_TAB)); } catch {} }
  history.replaceState(null, "", "#" + t); if (!opts.keepScroll) window.scrollTo(0, 0); render(); }
function goRoom(id) { const r = ROOMS.find(x => x.id === id); if (r) go(LAST_TAB[r.id] && r.tabs.includes(LAST_TAB[r.id]) ? LAST_TAB[r.id] : r.tabs[0]); }

function renderShell() {
  const now = roomOf(tab), live = jobs.some(j => j.status === "running");
  $("#rooms").replaceChildren(...ROOMS.map(r => el("button", { class: now === r ? "on" : "", title: r.name + ": " + r.says + " (⌥" + r.key + ")", onclick: () => goRoom(r.id) },
    r.id === "shoot" && live && now !== r ? el("span", { class: "live", "aria-label": "rendering" }) : null, r.name, el("span", { class: "kbd" }, "⌥" + r.key))));
  $("#roomname").textContent = now ? now.name : tab === "Tutorial" ? "Learn" : "Home";
  const tabs = now ? now.tabs.filter(t => t !== "Sparks" || !S || (S.sparks && S.sparks.supported) || tab === "Sparks") : tab === "Tutorial" ? [tab] : [];
  $("#subtabs").replaceChildren(...tabs.map(t => el("button", { class: t === tab ? "on" : "", onclick: () => go(t) }, TAB_NAMES[t] || t)),
    ...(tabs.length ? [] : [el("span", { class: "note", style: "margin:0;font-size:12.5px" }, "Pick a room, or press ", el("kbd", {}, "⌘K"), " to search or run anything.")]));
  document.body.classList.toggle("quiet", SHELL.lastTab === tab); SHELL.lastTab = tab;
}
const SHELL = { lastTab: null, tel: null, history: [], es: null, telAt: 0, libCache: null };

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
  $("#baycount").textContent = [queued.length ? queued.length + " queued" : "", done ? done + " done" : "", failed ? failed + " failed" : ""].filter(Boolean).join(" · ");
  renderRoomsLive(running.length > 0); }
function renderRoomsLive(live) { const b = [...document.querySelectorAll("#rooms button")][1]; if (!b) return; const has = b.querySelector(".live");
  if (live && !has && !b.classList.contains("on")) b.prepend(el("span", { class: "live" })); else if ((!live || b.classList.contains("on")) && has) has.remove(); }

/* ---------- the machine pill and telemetry ---------- */
const pct = (a, b) => b ? Math.round(100 * a / b) : 0;
function telApply(t) { SHELL.tel = t; SHELL.telAt = Date.now();
  SHELL.history.push({ t: Date.now(), gpu: t.gpu ? t.gpu.util_pct : null, mem: t.memory ? 100 - pct(t.memory.available_mb, t.memory.total_mb) : null, temp: t.gpu ? t.gpu.temp_c : null, power: t.gpu ? t.gpu.power_w : null });
  if (SHELL.history.length > 300) SHELL.history.shift();
  const used = t.memory ? 100 - pct(t.memory.available_mb, t.memory.total_mb) : 0, gpu = t.gpu && t.gpu.util_pct != null ? t.gpu.util_pct : null;
  const g = $("#mgpu"), m = $("#mmem"); if (g) { g.style.setProperty("--v", (gpu || 0) + "%"); g.style.setProperty("--c", gpu > 60 ? "var(--onair)" : "var(--ok)"); g.parentNode.title = "GPU " + (gpu == null ? "n/a" : gpu + "%") + " · memory " + used + "% used"; }
  if (m) { m.style.setProperty("--v", used + "%"); m.style.setProperty("--c", used > 85 ? "var(--onair)" : used > 65 ? "var(--warn)" : "var(--accent)"); }
  const busy = t.jobs && t.jobs.running, warn = (t.warnings || []).length;
  $("#mdot").className = "dotl" + (busy ? " r" : warn ? " t" : "");
  $("#mtxt").textContent = gpu != null ? gpu + "% GPU" : t.memory ? Math.round(t.memory.available_mb / 1024) + " GB free" : "";
  if (tab === "Machine" && typeof machineLive === "function") machineLive(t);
  if (tab === "Home" && typeof homeLive === "function") homeLive(t); }
function telStart() {
  const poll = async () => { try { telApply(await api("/api/telemetry")); } catch { SHELL.tel = null; } };
  if ("EventSource" in window) { try { const es = new EventSource("/api/events"); SHELL.es = es;
    es.addEventListener("telemetry", e => { try { telApply(JSON.parse(e.data)); } catch {} });
    es.onerror = () => { es.close(); SHELL.es = null; poll(); setInterval(() => { if (!SHELL.es && Date.now() - SHELL.telAt > 2500) poll(); }, 3000); };
    return; } catch {} }
  poll(); setInterval(poll, 3000); }

/* ---------- ⌘K ---------- */
const PAL = { q: "", at: 0, items: [] };
function palActions() { const a = (ico, label, hint, run, words = "") => ({ ico, label, hint, run, words: (label + " " + hint + " " + words).toLowerCase() });
  const create = (set, step = 0) => () => { Object.assign(C, set); C.step = step; sync(); C.prompt = ""; go("Create"); };
  return [
    a("▶", "New clip with sound", "Shoot", create({ goes: "video", start: "text" }), "video h3 fasth3 text"),
    a("◐", "New still or scouts", "Shoot", create({ goes: "image" }), "image picture qwen flux"),
    a("✎", "Edit a picture", "Shoot", create({ goes: "edit" }), "re-draw"),
    a("∞", "Long take, up to 30 minutes", "Shoot", () => { C.goes = "video"; C.start = "text"; if (S && S.long) C.seconds = Object.keys(S.long)[0]; sync(); go("Create"); }, "long video chain"),
    a("◎", "Wasserman Sharp Spec", "Shoot", () => { if (typeof sharpCreate === "function") { sharpCreate(); go("Create"); } }, "sam locked recipe"),
    a("✦", "Ask your agent", "Blender · Blockout · Studio", () => go("Agent"), "claude codex blender blockout mcp"),
    a("◉", "Record a voice line", "Sound", () => go("Voice"), "fish drama speech"),
    a("♪", "Write a song", "Sound", () => go("Music"), "minimax music score"),
    a("▤", "Plan a film", "Write", () => go("Production"), "slate bible shots first ad"),
    a("⇣", "Bring in from the suite", "ScriptBreak · Cork Board · Blockout …", () => go("Import"), "import storyboard master canvas fountain"),
    a("▦", "Library", "Cut", () => go("Library"), "files renders"),
    a("▭", "Timeline", "Cut · Resolve export", () => go("Editor"), "editor fcpxml edl resolve"),
    a("≋", "Telemetry", "Machine", () => go("Machine"), "gpu memory temperature disk storage"),
    a("⌁", "Sparks", "Machine", () => go("Sparks"), "workers multi spark"),
    a("⚙", "Brains & tools", "subscriptions, MCP", () => go("Brains"), "claude codex gemini grok local llm blender blockout"),
    a("↻", "Check for updates", "Studio", () => $("#studio-updates-open").click(), "update"),
    a("☰", "Open the render bay", "Queue", () => bayOpen(true), "queue jobs"),
    a("?", "Learn Studio", "17 lessons", () => go("Tutorial"), "tutorial help"),
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
    const js = jobs.filter(j => q && hit((j.title + " " + j.kind + " " + j.status).toLowerCase())).slice(0, 5).map(j => ({ ico: "■", label: j.title, hint: j.status, run: () => bayOpen(true) }));
    PAL.items = [...acts, ...js, ...files]; PAL.at = Math.min(PAL.at, Math.max(0, PAL.items.length - 1));
    const row = (x, i) => el("button", { class: "it" + (i === PAL.at ? " on" : ""), role: "option", onmouseenter: () => { PAL.at = i; [...res.querySelectorAll(".it")].forEach((n, k) => n.classList.toggle("on", k === i)); }, onclick: () => { palClose(); x.run(); } },
      el("span", { class: "ico" }, typeof x.ico === "object" ? (x.ico.kind === "video" ? el("video", { src: file(x.ico.path) + "#t=0.5", muted: true, preload: "metadata" }) : x.ico.kind === "image" ? el("img", { src: file(x.ico.path), loading: "lazy" }) : "♪") : x.ico),
      el("span", {}, x.label), el("small", {}, x.hint));
    let i = 0; const out = [];
    if (acts.length) out.push(el("div", { class: "grp" }, q ? "Actions" : "Go to or start"), ...acts.map(x => row(x, i++)));
    if (js.length) out.push(el("div", { class: "grp" }, "Queue"), ...js.map(x => row(x, i++)));
    if (files.length) out.push(el("div", { class: "grp" }, "Library"), ...files.map(x => row(x, i++)));
    if (!out.length) out.push(el("div", { class: "note", style: "padding:14px" }, "Nothing matches “" + PAL.q + "”."));
    res.replaceChildren(...out); const on = res.querySelector(".it.on"); if (on) on.scrollIntoView({ block: "nearest" }); };
  input.addEventListener("input", () => { PAL.q = input.value; PAL.at = 0; draw(); });
  input.addEventListener("keydown", e => { if (e.key === "ArrowDown") { PAL.at = Math.min(PAL.items.length - 1, PAL.at + 1); draw(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { PAL.at = Math.max(0, PAL.at - 1); draw(); e.preventDefault(); }
    else if (e.key === "Enter") { const x = PAL.items[PAL.at]; if (x) { palClose(); x.run(); } } else if (e.key === "Escape") palClose(); });
  draw(); api("/api/library").then(list => { SHELL.libCache = list; if ($("#palette")) draw(); }).catch(() => {}); }
function palClose() { const p = $("#palette"); if (p) p.remove(); }

function shellStart() {
  $("#studio-updates-open").innerHTML = ICON.update; $("#helpbtn").innerHTML = ICON.help; $("#baychev").innerHTML = ICON.chev;
  $("#helpbtn").onclick = () => go("Tutorial"); $("#kbtn").onclick = palOpen; $("#mpill").onclick = () => go("Machine");
  $("#bayhead").onclick = e => { if (!e.target.closest("button:not(.chev)")) bayOpen(!$("#bay").classList.contains("open")); };
  try { if (localStorage.getItem("bay-open")) bayOpen(true); } catch {}
  document.addEventListener("keydown", e => { const typing = e.target.matches && e.target.matches("input,textarea,select,[contenteditable]");
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("#palette") ? palClose() : palOpen(); return; }
    if (e.altKey && !e.metaKey && !e.ctrlKey && /^Digit[1-5]$/.test(e.code)) { e.preventDefault(); goRoom(ROOMS[Number(e.code.slice(5)) - 1].id); return; }
    if (!typing && e.key === "/" && !$("#palette")) { e.preventDefault(); palOpen(); } });
  window.addEventListener("hashchange", () => { const t = decodeURIComponent(location.hash.slice(1)); if (TABS.includes(t) && t !== tab) go(t); });
  if (S) { $("#plat").textContent = S.platform === "spark" ? "DGX Spark" : S.platform === "mac" ? "Mac" : S.platform_label; $("#mpill").title = S.platform_label + " · open the Machine room"; }
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
function createLayout(node) { if (C.goes === "edit") return node; return el("div", { class: "split", style: "grid-template-columns:minmax(0,1fr) 330px" }, node, takeSheet()); }
