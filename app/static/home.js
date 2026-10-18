"use strict";
// Home: where the studio opens. Start something in one click, see what the machine is doing, pick up recent takes.
const SUITE_SLUG = { ScriptBreak: "scriptbreak", "Cork Board": "cork-board", Blockout: "blockout", "Storyboard Reference": "storyboard-reference", "Master Canvas": "master-canvas", Slate: "slate", "Circle Take": "circle-take", "Stem Studio": "stem-studio" };
const SUITE_APPS = [
  ["ScriptBreak", "a screenplay into scenes, bibles and a shot list"], ["Cork Board", "index cards, acts and arcs"], ["Blockout", "3D previs: marks, lenses, camera moves"],
  ["Storyboard Reference", "reframed stills with a prompt each"], ["Master Canvas", "boards, references and shot order"], ["Slate", "the desktop film planner"],
  ["Circle Take", "dailies and a QC gate"], ["Stem Studio", "dialogue, music and effects stems"],
];
function timeOfDay() { const h = new Date().getHours(); return h < 5 ? ["Night shoot", "Still rolling at this hour."] : h < 12 ? ["Morning call", "Fresh light. Good time to scout."]
  : h < 17 ? ["Afternoon shoot", "Plenty of day left for coverage."] : h < 21 ? ["Golden hour", "Make something that glows."] : ["Tonight's slate", "Queue it up and let the Sparks work."]; }
function timecode(d = new Date()) { const p = n => String(n).padStart(2, "0"); return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds()) + ":" + p(Math.floor(d.getMilliseconds() / 41.67)); }
const startCreate = (set, step = 0) => { Object.assign(C, set); C.step = step; C.prompt = ""; sync(); go("Create"); };

// the machine, in one line: LED, name, GPU, memory against the guard's floor, disk
function machineStrip() {
  const t = SHELL.tel, gb = mb => (mb / 1024).toFixed(mb >= 102400 ? 0 : 1), running = jobs.find(j => j.status === "running");
  const memUsed = t && t.memory ? 100 - pct(t.memory.available_mb, t.memory.total_mb) : null;
  const m = (label, v, value, c, floor, cls) => el("div", { class: "m" + (cls ? " " + cls : "") }, el("div", { class: "kv" }, el("span", {}, label), el("b", { "data-k": label }, value)),
    el("div", { class: "gaugebar" }, el("i", { "data-b": label, style: "width:" + Math.max(1, Math.min(100, v || 0)) + "%;--c:" + c }), floor != null ? el("span", { class: "floor", style: "left:" + floor + "%" }) : null));
  return el("div", { class: "mstrip", id: "homemachine" },
    el("div", { class: "who" }, el("span", { class: "dotl" + (running ? " r" : t && (t.warnings || []).length ? " t" : "") }),
      el("div", { style: "min-width:0" }, el("b", {}, platName() || "This machine"), el("span", {}, running ? "Rolling: " + running.title : t && (t.warnings || []).length ? t.warnings[0] : "Ready for the next take"))),
    t && t.gpu ? m("GPU", t.gpu.util_pct, (t.gpu.util_pct ?? "–") + "%" + (t.gpu.temp_c != null ? " · " + t.gpu.temp_c + "°C" : ""), t.gpu.util_pct > 60 ? "var(--red)" : "var(--green)") : el("div", { class: "m" }, el("span", { class: "note", style: "margin:0" }, t ? "" : "Readouts appear once the machine reports them.")),
    t && t.memory ? m("Memory", memUsed, gb(t.memory.total_mb - t.memory.available_mb) + " of " + gb(t.memory.total_mb) + " GB", memUsed > 85 ? "var(--red)" : "var(--amber)", t.memory.floor_mb ? 100 - pct(t.memory.floor_mb, t.memory.total_mb) : null) : el("div", { class: "m" }),
    t && t.disk ? m("Disk", t.disk.used_pct, Math.round(t.disk.free_gb) + " GB free", t.disk.used_pct > 90 ? "var(--red)" : "var(--blue)", null, "disk") : el("div", { class: "m disk" }),
    el("button", { class: "small", onclick: () => go("Machine") }, "Telemetry"));
}
function homeLive() { const box = $("#homemachine"); if (box) box.replaceWith(machineStrip()); }

// the eight ways in, each a picture card (art/start-*.webp)
const STARTS = () => [
  { art: "start-clip", sign: "video", k: "VIDEO", title: "A clip with sound", one: "Text to video, with lines and a soundscape", run: () => startCreate({ goes: "video", start: "text" }) },
  { art: "start-scout", sign: "video", k: "VIDEO", title: "Scout, then animate", one: "First frames in one load; pick one, then roll", run: () => startCreate({ goes: "video", start: "scout" }, 1) },
  { art: "start-still", sign: "image", k: "IMAGE", title: "A still", one: "Qwen 2.1 or FLUX3-K, any size", run: () => startCreate({ goes: "image" }) },
  { art: "start-sharp", sign: "video", k: "RECIPE", title: "Sharp Spec", one: "Sam's locked recipe: 1376 × 768, 8 passes", run: () => { if (typeof sharpCreate === "function") sharpCreate(); go("Create"); } },
  { art: "start-long", sign: "video", k: "VIDEO", title: "A long take", one: "Chained clips, 30 seconds and up", run: () => { const k = S && S.long ? Object.keys(S.long)[0] : null; startCreate({ goes: "video", start: "text", ...(k ? { seconds: k } : {}) }); } },
  { art: "start-voice", sign: "sound", k: "SOUND", title: "A voice line", one: "Fish Audio Drama 3: cast or clone a voice", run: () => go("Voice") },
  { art: "start-song", sign: "sound", k: "SOUND", title: "A song", one: "MiniMax-Music3: lyrics and a style", run: () => go("Music") },
  { art: "start-agent", sign: "previs", k: "PREVIS", title: "Ask your agent", one: "Claude or Codex build it in Blender or Blockout", run: () => go("Agent") },
];
function viewHome() {
  const [hello, line] = timeOfDay(), wrap = el("div");
  const tc = el("span", { class: "tc" }, timecode()); const tick = setInterval(() => { if (!tc.isConnected) return clearInterval(tick); tc.textContent = timecode(); }, 1000 / 24);
  const hero = el("div", { class: "homehero" + (ART.ready.has("home-hero") ? " has-art" : "") }, artSlot("home-hero", "ar21", "home"),
    el("div", { class: "over" },
      el("div", { class: "stack" }, el("div", { style: "display:flex;gap:14px;align-items:center;flex-wrap:wrap" }, el("span", { class: "eyebrow" }, new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })), tc),
        el("h1", { class: "display" }, hello), el("p", { class: "sub", style: "margin:0" }, line + " Pictures, clips with sound, voices and score, made on your own machine.")),
      el("div", { class: "acts" }, el("button", { class: "primary", onclick: () => startCreate({ goes: "video", start: "text" }) }, "Roll a new clip"),
        el("button", { onclick: () => go("Production") }, "Plan a film"), el("button", { class: "ghost", onclick: palOpen }, "Search ", el("kbd", {}, "⌘K")))));
  put(wrap, hero);
  put(wrap, el("div", { class: "sectionhead" }, el("h2", {}, "Start something"), el("span", { class: "note" }, "Each opens with sensible settings.")),
    el("div", { class: "cards c4 two-up" }, STARTS().map(pcard)));
  const recent = el("div", { class: "strip2" }, el("div", { class: "note" }, "Loading recent takes…"));
  put(wrap, el("div", { class: "sectionhead" }, el("h2", {}, "Recent takes"), el("button", { class: "small", onclick: () => go("Library") }, "Library")), recent);
  api("/api/library").then(list => { SHELL.libCache = list; const pick = list.filter(m => m.kind !== "audio").slice(0, 12);
    recent.replaceChildren(...(pick.length ? pick.map(m => el("div", { class: "tile" }, media(m.path, m.kind === "video" ? { controls: false, muted: true, loop: true, playsinline: true, onmouseenter: e => e.target.play().catch(() => {}), onmouseleave: e => e.target.pause() } : {}),
      el("div", { class: "cap" }, el("span", {}, m.path.split("/").pop()), m.kind === "video" ? el("button", { class: "small", onclick: () => addToTimeline(m.path) }, "+ timeline")
        : el("button", { class: "small", onclick: () => startCreate({ image: m.path, goes: "video", start: "own" }, 1) }, "animate"))))
      : [el("div", { class: "note" }, "Nothing rendered yet. Your takes will line up here.")])); }).catch(() => recent.replaceChildren(el("div", { class: "note" }, "The Library could not be read.")));
  put(wrap, el("div", { class: "sectionhead" }, el("h2", {}, "Machine")), machineStrip());
  put(wrap, el("div", { class: "sectionhead" }, el("h2", {}, "The suite"), el("button", { class: "small", onclick: () => go("Import") }, "Suite")),
    el("div", { class: "suiteRow" }, SUITE_APPS.map(([n, s]) => el("button", { class: "app", type: "button", title: s, onclick: () => go("Import") },
      el("img", { src: "art/suite/" + SUITE_SLUG[n] + ".webp", alt: "", width: 32, height: 32, loading: "lazy" }), el("b", {}, n)))));
  return wrap;
}
