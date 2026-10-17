"use strict";
// The stage: where Studio opens. Start something in one click, see what the machine is doing, pick up recent takes.
const SUITE_APPS = [
  ["ScriptBreak", "a screenplay into scenes, bibles and a shot list"], ["Cork Board", "index cards, acts and arcs"], ["Blockout", "3D previs: marks, lenses, camera moves"],
  ["Storyboard Reference", "reframed stills with a prompt each"], ["Master Canvas", "boards, references and shot order"], ["Slate", "the desktop film planner"],
  ["Circle Take", "dailies and a QC gate"], ["Stem Studio", "dialogue, music and effects stems"],
];
function timeOfDay() { const h = new Date().getHours(); return h < 5 ? ["Night shoot", "Still rolling at this hour."] : h < 12 ? ["Morning call", "Fresh light. Good time to scout."]
  : h < 17 ? ["Afternoon shoot", "Plenty of day left for coverage."] : h < 21 ? ["Golden hour", "Make something that glows."] : ["Tonight's slate", "Queue it up and let the Sparks work."]; }
function timecode(d = new Date()) { const p = n => String(n).padStart(2, "0"); return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds()) + ":" + p(Math.floor(d.getMilliseconds() / 41.67)); }
const startCreate = (set, step = 0) => { Object.assign(C, set); C.step = step; C.prompt = ""; sync(); go("Create"); };

function machineCard() {
  const t = SHELL.tel, gb = mb => (mb / 1024).toFixed(mb >= 102400 ? 0 : 1);
  const bar = (label, v, value, c, floor) => el("div", { class: "stack", style: "gap:7px" }, el("div", { class: "kv" }, el("span", {}, label), el("b", { "data-k": label }, value)),
    el("div", { class: "gaugebar" }, el("i", { "data-b": label, style: "width:" + Math.max(1, Math.min(100, v || 0)) + "%;--c:" + c }), floor != null ? el("span", { class: "floor", style: "left:" + floor + "%" }) : null));
  const running = jobs.find(j => j.status === "running");
  const memUsed = t && t.memory ? 100 - pct(t.memory.available_mb, t.memory.total_mb) : null;
  return el("div", { class: "machinecard", id: "homemachine" },
    el("div", { class: "kv" }, el("span", { class: "eyebrow" }, "Machine"), el("button", { class: "small", onclick: () => go("Machine") }, "Telemetry →")),
    el("div", { style: "display:flex;align-items:center;gap:12px" }, el("span", { class: "dotl" + (running ? " r" : "") }),
      el("div", {}, el("div", { style: "font:700 24px/1.1 var(--display);letter-spacing:-.02em" }, S.platform === "spark" ? "DGX Spark" : S.platform === "mac" ? "Mac" : "This machine"),
        el("div", { class: "note", style: "margin:2px 0 0;font:500 11px var(--mono);letter-spacing:.04em" }, S.platform_label || ""), el("div", { class: "note", style: "margin:4px 0 0" }, running ? "Rolling: " + running.title : "Ready for the next take"))),
    t && t.gpu ? bar("GPU", t.gpu.util_pct, (t.gpu.util_pct ?? "–") + "%" + (t.gpu.temp_c != null ? " · " + t.gpu.temp_c + "°C" : ""), t.gpu.util_pct > 60 ? "var(--onair)" : "var(--ok)") : null,
    t && t.memory ? bar("Memory", memUsed, gb(t.memory.total_mb - t.memory.available_mb) + " of " + gb(t.memory.total_mb) + " GB", memUsed > 85 ? "var(--onair)" : "var(--accent)", t.memory.floor_mb ? 100 - pct(t.memory.floor_mb, t.memory.total_mb) : null) : null,
    t && t.disk ? bar("Disk", t.disk.used_pct, Math.round(t.disk.free_gb) + " GB free", t.disk.used_pct > 90 ? "var(--onair)" : "var(--cue)") : null,
    !t ? el("div", { class: "note" }, "Live readouts appear here once the machine reports them.") : null,
    t && (t.warnings || []).length ? el("div", { class: "warnline" }, el("span", { class: "dotl r" }), el("div", {}, t.warnings[0])) : null);
}
function homeLive() { const box = $("#homemachine"); if (box) box.replaceWith(machineCard()); }

function recipe(art, title, text, run, k, extra) {
  return el("button", { class: "recipe", type: "button", onclick: run }, el("div", { class: "art " + art }, extra || null), k ? el("span", { class: "k" }, k) : null,
    el("div", { class: "txt" }, el("b", {}, title), el("span", {}, text)));
}
function viewHome() {
  const [hello, line] = timeOfDay(), wrap = el("div");
  const tc = el("span", { class: "tc" }, timecode()); const tick = setInterval(() => { if (!tc.isConnected) return clearInterval(tick); tc.textContent = timecode(); }, 1000 / 24);
  const wave = el("div", { class: "wave" }, Array.from({ length: 34 }, (_, i) => el("i", { style: "height:" + (18 + Math.round(70 * Math.abs(Math.sin(i * 1.7) * Math.cos(i * .45)))) + "%" })));
  put(wrap, el("div", { class: "stage" },
    el("div", { class: "hero" },
      el("div", { class: "stack", style: "gap:12px" }, el("div", { style: "display:flex;gap:14px;align-items:center" }, el("span", { class: "eyebrow" }, new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })), tc),
        el("h1", { class: "display" }, hello.split(" ").slice(0, -1).join(" ") + " ", el("em", {}, hello.split(" ").at(-1))), el("p", { class: "sub", style: "margin:0" }, line + " Picture, video with sound, voice and score, made on your own machine.")),
      el("div", { class: "acts" }, el("button", { class: "hot", onclick: () => startCreate({ goes: "video", start: "text" }) }, "Roll a new clip"),
        el("button", { onclick: () => go("Production") }, "Plan a film"), el("button", { onclick: () => go("Agent") }, "Ask your agent"),
        el("button", { class: "ghost", onclick: palOpen }, "Search or run ", el("kbd", {}, "⌘K")))),
    machineCard()));
  put(wrap, el("div", { class: "sectionhead" }, el("h2", {}, "Start something"), el("span", { class: "note", style: "margin:0" }, "Every recipe opens with sensible settings. Change anything after.")),
    el("div", { class: "recipes" },
      recipe("a1", "A clip with sound", "Text to video, with spoken lines and a soundscape", () => startCreate({ goes: "video", start: "text" }), "SHOOT"),
      recipe("a3", "Scout, then animate", "Several first frames in one load. Pick one, then roll", () => startCreate({ goes: "video", start: "scout" }, 1), "SHOOT"),
      recipe("a2", "A still", "Qwen or FLUX pictures at any size", () => startCreate({ goes: "image" }), "SHOOT"),
      recipe("a7", "Sharp Spec", "Sam's locked recipe: 1376 × 768, 8 passes, QA gate", () => { if (typeof sharpCreate === "function") sharpCreate(); go("Create"); }, "RECIPE"),
      recipe("a4", "A long take", "Chained clips from 30 seconds up to 30 minutes", () => { const k = S && S.long ? Object.keys(S.long)[0] : null; startCreate({ goes: "video", start: "text", ...(k ? { seconds: k } : {}) }); }, "SHOOT"),
      recipe("a5", "A voice line", "Fish Audio Drama 3: cast a voice, or clone one", () => go("Voice"), "SOUND", wave),
      recipe("a6", "A song", "Lyrics and a style into a full track", () => go("Music"), "SOUND"),
      recipe("a8", "Ask your agent", "Claude, Codex and others drive Blender or Blockout for you", () => go("Agent"), "AGENT")));
  const recent = el("div", { class: "strip2" }, el("div", { class: "note" }, "Loading recent takes…"));
  put(wrap, el("div", { class: "sectionhead" }, el("h2", {}, "Recent takes"), el("button", { class: "small", onclick: () => go("Library") }, "Open the Library →")), recent);
  api("/api/library").then(list => { SHELL.libCache = list; const pick = list.filter(m => m.kind !== "audio").slice(0, 12);
    recent.replaceChildren(...(pick.length ? pick.map(m => el("div", { class: "tile" }, media(m.path, m.kind === "video" ? { controls: false, muted: true, loop: true, playsinline: true, onmouseenter: e => e.target.play().catch(() => {}), onmouseleave: e => e.target.pause() } : {}),
      el("div", { class: "cap" }, el("span", {}, m.path.split("/").pop()), m.kind === "video" ? el("button", { class: "small", onclick: () => addToTimeline(m.path) }, "+ timeline")
        : el("button", { class: "small", onclick: () => startCreate({ image: m.path, goes: "video", start: "own" }, 1) }, "animate"))))
      : [el("div", { class: "note" }, "Nothing rendered yet. Your takes will line up here.")])); }).catch(() => recent.replaceChildren(el("div", { class: "note" }, "The Library could not be read.")));
  put(wrap, el("div", { class: "sectionhead" }, el("h2", {}, "The suite"), el("button", { class: "small", onclick: () => go("Import") }, "Bring something in →")),
    el("div", { class: "suiteRow" }, SUITE_APPS.map(([n, s]) => el("button", { class: "app", type: "button", onclick: () => go("Import") }, el("b", {}, n), el("span", {}, s)))));
  return wrap;
}
