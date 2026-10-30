"use strict";
// Home: where the studio opens. One composer to generate straight away, then the ways in, recent takes, the machine
// and the suite.
const SUITE_SLUG = { ScriptBreak: "scriptbreak", "Cork Board": "cork-board", Blockout: "blockout", "Storyboard Reference": "storyboard-reference", "Master Canvas": "master-canvas", Slate: "slate", "Circle Take": "circle-take", "Stem Studio": "stem-studio" };
const SUITE_APPS = [
  ["ScriptBreak", "a screenplay into scenes, bibles and a shot list"], ["Cork Board", "index cards, acts and arcs"], ["Blockout", "3D previs: marks, lenses, camera moves"],
  ["Storyboard Reference", "reframed stills with a prompt each"], ["Master Canvas", "boards, references and shot order"], ["Slate", "the desktop film planner"],
  ["Circle Take", "dailies and a QC gate"], ["Stem Studio", "dialogue, music and effects stems"],
];
function timeOfDay() { const h = new Date().getHours(); const night = typeof cardFace === "function" && cardFace() ? "Queue it up and let the GPU work." : "Queue it up and let the Sparks work.";
  return h < 5 ? ["Night shoot", "Still rolling at this hour."] : h < 12 ? ["Morning call", "Fresh light. Good time to scout."]
  : h < 17 ? ["Afternoon shoot", "Plenty of day left for coverage."] : h < 21 ? ["Golden hour", "Make something that glows."] : ["Tonight's slate", night]; }
function timecode(d = new Date()) { const p = n => String(n).padStart(2, "0"); return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds()) + ":" + p(Math.floor(d.getMilliseconds() / 41.67)); }
// where Keys and Wasserman Productions publish (Home under the credit; Settings → About lists them with the rest)
const HOME_LINKS = [["Keys on GitHub", "https://github.com/drowzeys"], ["Wasserman Productions on GitHub", "https://github.com/wassermanproductions"]];
const startCreate = (set, step = 0) => { Object.assign(C, set); C.step = step; C.prompt = ""; sync(); go("Create"); };

// the machine, in one line: LED, name, GPU, memory against the guard's floor, disk
function machineStrip() {
  const t = SHELL.tel, gb = mb => (mb / 1024).toFixed(mb >= 102400 ? 0 : 1), running = jobs.find(j => j.status === "running");
  const card = t && typeof cardMem === "function" ? cardMem(t) : null;
  const memUsed = card ? pct(card.used_mb, card.total_mb) : (t && t.memory ? 100 - pct(t.memory.available_mb, t.memory.total_mb) : null);
  const m = (label, v, value, c, floor, cls) => el("div", { class: "m" + (cls ? " " + cls : "") }, el("div", { class: "kv" }, el("span", {}, label), el("b", { "data-k": label }, value)),
    el("div", { class: "gaugebar" }, el("i", { "data-b": label, style: "width:" + Math.max(1, Math.min(100, v || 0)) + "%;--c:" + c }), floor != null ? el("span", { class: "floor", style: "left:" + floor + "%" }) : null));
  return el("div", { class: "mstrip", id: "homemachine" },
    el("div", { class: "who" }, el("span", { class: "dotl" + (running ? " r" : t && (t.warnings || []).length ? " t" : "") }),
      el("div", { style: "min-width:0" }, el("b", {}, platName() || "This machine"), el("span", {}, running ? "Rolling: " + running.title : t && (t.warnings || []).length ? t.warnings[0] : "Ready for the next take"))),
    t && t.gpu ? m("GPU", t.gpu.util_pct, (t.gpu.util_pct ?? "–") + "%" + (t.gpu.temp_c != null ? " · " + t.gpu.temp_c + "°C" : ""), t.gpu.util_pct > 60 ? "var(--red)" : "var(--green)") : el("div", { class: "m" }, el("span", { class: "note", style: "margin:0" }, t ? "" : "Readouts appear once the machine reports them.")),
    t && t.memory ? m("Memory", memUsed, (card ? gb(card.used_mb) + " of " + gb(card.total_mb) : gb(t.memory.total_mb - t.memory.available_mb) + " of " + gb(t.memory.total_mb)) + " GB", memUsed > 85 ? "var(--red)" : "var(--amber)", card ? null : (t.memory.floor_mb ? 100 - pct(t.memory.floor_mb, t.memory.total_mb) : null)) : el("div", { class: "m" }),
    t && t.disk ? m("Disk", t.disk.used_pct, Math.round(t.disk.free_gb) + " GB free", t.disk.used_pct > 90 ? "var(--red)" : "var(--blue)", null, "disk") : el("div", { class: "m disk" }),
    el("button", { class: "small", onclick: () => go("Machine") }, "Telemetry"));
}
function homeLive() { const box = $("#homemachine"); if (box) box.replaceWith(machineStrip()); }

// the eight ways in, each a picture card (art/start-*.webp); Settings → Appearance picks which show (six fill a row)
const STARTS = () => [
  { id: "clip", art: "start-clip", sign: "video", k: "VIDEO", title: "A clip with sound", one: "Text to video, with lines and a soundscape", run: () => startCreate({ goes: "video", start: "text" }) },
  { id: "scout", art: "start-scout", sign: "video", k: "VIDEO", title: "Scout, then animate", one: "First frames in one load; pick one, then roll", run: () => startCreate({ goes: "video", start: "scout" }, 1) },
  { id: "still", art: "start-still", sign: "image", k: "IMAGE", title: "A still", one: "Qwen 2.1 or FLUX3-K, any size", run: () => startCreate({ goes: "image" }) },
  { id: "sharp", art: "start-sharp", sign: "video", k: "RECIPE", title: "Sharp Spec", one: "Sam's locked recipe: 1376 × 768, 8 passes", run: () => { if (typeof sharpCreate === "function") sharpCreate(); go("Create"); } },
  { id: "long", art: "start-long", sign: "video", k: "VIDEO", title: "A long take", one: "Chained clips, 30 seconds and up", run: () => { const k = S && S.long ? Object.keys(S.long)[0] : null; startCreate({ goes: "video", start: "text", ...(k ? { seconds: k } : {}) }); } },
  { id: "voice", art: "start-voice", sign: "sound", k: "SOUND", title: "A voice line", one: "Fish Audio Drama 3: cast or clone a voice", run: () => go("Voice") },
  { id: "song", art: "start-song", sign: "sound", k: "SOUND", title: "A song", one: "YuE2 or MiniMax-Music3: lyrics and a style", run: () => go("Music") },
  { id: "sfx", art: "start-sfx", sign: "sound", k: "SOUND", title: "Sound effects & foley", one: "Foley for a clip, or a sound from words", run: () => go("Foley") },
  { id: "looks", art: "vault-looks", sign: "looks", k: "LOOKS", title: "Looks", one: "LoRA styles for pictures and clips", run: () => go("Looks") },
  { id: "previs", art: "engine-previs", sign: "previs", k: "PREVIS", title: "Previs", one: "Motion, depth and camera from a clip", run: () => go("Previs") },
  { id: "nodes", art: "vault-nodes", sign: "nodes", k: "NODES", title: "Nodes", one: "Words to pictures to clips, wired in order", run: () => go("Nodes") },
  { id: "agent", art: "start-agent", sign: "previs", k: "AGENT", title: "Ask your agent", one: "Claude, Codex or Hermes make it with the studio", run: () => go("Agent") },
];
// 3.0's cards show unless switched off in Settings, even beside a start_cards list saved before they existed
const START_NEW = ["sfx", "looks", "previs", "nodes"];
const startShown = (look, id) => (look.start_cards || []).includes(id) || (START_NEW.includes(id) && !(look.start_off || []).includes(id));
const homePrefs = () => typeof prefs === "function" ? prefs() : { appearance: { start_cards: ["clip", "scout", "still", "sharp", "long", "voice"], poster: true }, presets: { recipe: "quick", mode: "clip", seconds: 5, seed: "fixed", qa_gate: true, image_model: "qwen" } };

/* ---------- the composer: one box, four kinds of take, one Generate ---------- */
const HC = { text: "", mode: null, recipe: null, seconds: null, est: {}, estInfo: {}, estAdmission: {}, estPending: {}, made: [], busy: false, image: null };
const hcSpan = sec => sec == null ? "" : sec < 90 ? Math.round(sec) + " s" : sec < 5400 ? Math.round(sec / 60) + " min" : Math.floor(sec / 3600) + " h " + Math.round(sec % 3600 / 60) + " min";
const HC_MODES = { clip: ["Clip with sound", "minimax", "MiniMax H3", "Describe the shot: who is there, what happens, what is heard."],
  picture: ["Picture", "qwen", "Qwen 2.1", "Describe the picture: the subject, the place, the light, the lens."],
  voice: ["Voice", "fish", "Drama 3", "Write the line as it is said. A [direction] in brackets sets the voice."],
  song: ["Song", "music3", "Music 3", "A style (genre, tempo, mood), or lyrics with [Verse] and [Chorus] tags."] };
const HC_RECIPES = () => ({ quick: "Quick look", ...(S && S.sharp ? { sharp: "Sharp Spec" } : {}), final: "Final 2K" });
const hcMode = () => HC.mode || (HC_MODES[homePrefs().presets.mode] ? homePrefs().presets.mode : "clip");
const hcRecipe = () => { const r = HC.recipe || homePrefs().presets.recipe; return HC_RECIPES()[r] ? r : "quick"; };
const hcPictureModel = () => { const want = homePrefs().presets.image_model, have = S && S.image_models && S.image_models.length ? S.image_models.map(m => m.id) : ["qwen"]; return have.includes(want) ? want : "qwen"; };
const hcSeed = () => typeof prefSeed === "function" ? prefSeed() : 1;
const hcTitle = t => t.replace(/\s+/g, " ").trim().slice(0, 48);
// a clip's length: the one picked here, else the recipe's, else Settings → Presets; over 15 s it is a long take, a chain of 15 s clips
const hcLongs = () => (S && S.long) || {};
const hcSeconds = () => { const r = typeof recipeParams === "function" ? recipeParams(hcRecipe()) : {}; return Number(HC.seconds || r.seconds || homePrefs().presets.seconds || 5); };
function hcLength() { const now = hcSeconds(), shorts = Object.keys((S && S.durations) || { 5: 0 }).map(Number), longs = hcLongs();
  if (!shorts.includes(now) && !longs[now]) shorts.push(now);
  return el("label", { class: "cmp-length", title: "The model makes up to 15 s at a time; longer lengths render as 15 s clips chained on this machine, each opening on the last frame of the one before" },
    el("span", { class: "eyebrow" }, "Length"),
    el("select", { "aria-label": "Clip length", onchange: e => { HC.seconds = Number(e.target.value); render(); } },
      shorts.sort((a, b) => a - b).map(k => el("option", { value: k, selected: k === now }, k + " s")),
      Object.entries(longs).map(([k, label]) => el("option", { value: k, selected: Number(k) === now }, label + " · " + Math.ceil(k / 15) + " clips chained"))),
    hcEstimateNote()); }
// the expected time of this length with this recipe (server.estimate: a long take adds up its clips), asked once a choice
const hcEstimateKey = () => {
  const r = typeof recipeParams === "function" ? recipeParams(hcRecipe()) : { preset: "small", quality: "fh4" },
    placement = typeof SPK !== "undefined" && SPK.data && !hcLongs()[hcSeconds()] ? JSON.stringify(SPK.data.placement) : "";
  return [r.preset, r.quality, hcSeconds(), ...(placement ? [placement] : [])].join("|");
};
// Admission changes independently of a recipe's timing. Temperature, utilization and power do not affect it.
const hcAdmissionSig = () => {
  const d = typeof SPK !== "undefined" && SPK.data;
  if (!d) return "";
  const head = d.this || {};
  return JSON.stringify([d.placement, head.state, head.job, head.memory_gb, head.guard,
    (d.workers || []).map(w => [w.address, w.state, w.enabled, w.working, w.job, w.usable, w.guard, w.memory_gb]),
    d.local_gpus && d.local_gpus.devices.map(g => [g.id, g.free_mb, g.reason])]);
};
function hcEstimateNote() {
  const seconds = hcEstimate(), info = HC.estInfo[hcEstimateKey()], worker = estimateWorkerName(info);
  if (info && info.memory && info.memory.fits === false) return el("small", { class: "note m-flat", style: "color:var(--warn)" }, "This clip will be refused: " + (info.memory.reason || "not enough available memory") + (worker ? " on " + worker : ""));
  return seconds ? el("small", { class: "note m-flat" }, "about " + hcSpan(seconds) + (worker ? " on " + worker : "")) : null;
}
function hcEstimate() { const r = typeof recipeParams === "function" ? recipeParams(hcRecipe()) : { preset: "small", quality: "fh4" }, seconds = hcSeconds(), key = hcEstimateKey(), admission = hcAdmissionSig();
  if (!(key in HC.est) || HC.estAdmission[key] !== admission) {
    HC.est[key] = undefined; delete HC.estInfo[key];
    // One request per recipe at a time. A response to an obsolete fleet state is never displayed.
    if (!HC.estPending[key]) {
      HC.estPending[key] = true;
      api("/api/estimate", { kind: hcLongs()[seconds] ? "long" : "video", preset: r.preset, quality: r.quality, seconds, text_only: true }).then(d => {
        if (hcAdmissionSig() !== admission) { delete HC.est[key]; return; }
        const card = typeof cardFace === "function" && cardFace(); HC.estInfo[key] = d; HC.estAdmission[key] = admission;
        HC.est[key] = d && d.seconds != null && (d.worker || !card || d.where === "card") ? d.seconds : null;
      }, () => {
        HC.est[key] = null; HC.estInfo[key] = null; HC.estAdmission[key] = admission;
      }).finally(() => { delete HC.estPending[key]; render(); });
    }
  }
  return HC.est[key]; }
// what Generate sends for each kind of take: the same endpoints and bodies the tabs use
async function hcRequest(mode, text, seed, retake) {
  const p = homePrefs().presets;
  if (mode === "clip") { const r = typeof recipeParams === "function" ? recipeParams(hcRecipe()) : { preset: "small", quality: "fh4" };
    const seconds = hcSeconds(), long = hcLongs()[seconds];
    const still = !long && HC.image || null;
    const composed = await api("/api/compose", { fields: { scene: text, action: "", camera: "", dialogue: [], soundscape: "", music: "N/A" }, first_frame: !!still, last_frame: false, seconds: long ? 15 : seconds });
    const params = { prompt: composed.prompt, image: still, preset: r.preset, quality: r.quality, seed: r.seed && !retake ? r.seed : seed, ...(p.qa_gate ? {} : { qa: false }) };
    return long ? ["/api/jobs", { kind: "long", title: long + ": " + hcTitle(text).slice(0, 40), params: { ...params, total_seconds: seconds, beats: [] } }]
      : ["/api/jobs", { kind: "video", title: hcTitle(text), params: { ...params, seconds } }]; }
  if (mode === "picture") return ["/api/jobs", { kind: "scout", title: "Still: " + hcTitle(text).slice(0, 40), params: { prompt: text, count: 1, seed, preset: "2k", image_model: hcPictureModel() } }];
  if (mode === "voice") return ["/api/voice/takes", { text, seed }];
  const lyrics = /\[(intro|verse|pre-chorus|chorus|bridge|instrumental|outro)\]/i.test(text);
  return ["/api/music/takes", lyrics ? { caption: "A warm, cinematic song with a clear lead vocal.", lyrics: text, seconds: 60, seed } : { caption: text, lyrics: "", seconds: 60, seed }]; }
async function hcGenerate(again) {
  const mode = again ? again.mode : hcMode(), text = (again ? again.text : HC.text).trim();
  if (!text) return toast("Say what you want to make first");
  if (HC.busy) return; HC.busy = true; render();
  try { const [path, body] = await hcRequest(mode, text, again ? again.seed + 1 : hcSeed(), !!again);
    const job = await api(path, body), seed = (body.params ? body.params.seed : body.seed) || 1;
    HC.made.unshift({ id: job.id, mode, text, seed, title: job.title || hcTitle(text) }); HC.made = HC.made.slice(0, 12);
    HC.image = null; toast("Queued: " + (job.title || hcTitle(text))); if (typeof poll === "function") await poll(); }
  catch (e) { toast(e.message, 6000); }
  HC.busy = false; render(); }
// the full form for this kind of take, with the words carried over
function hcMore() { const mode = hcMode(), text = HC.text.trim();
  if (mode === "voice") { if (typeof VO !== "undefined") VO.text = text; return go("Voice"); }
  if (mode === "song") { if (typeof MU !== "undefined") { if (/\[[A-Za-z-]+\]/.test(text)) MU.lyrics = text; else MU.caption = text; } return go("Music"); }
  Object.assign(C, mode === "clip" ? { goes: "video", start: "text", ...(HC.seconds ? { seconds: HC.seconds } : {}) } : { goes: "image", imageModel: hcPictureModel() }); C.step = 0; C.scene = text; sync(); C.prompt = text; go("Create"); }
// a shot from the suite fills the box: its words, its length (the nearest the model makes) and its still as the first frame
function hcFromSuite(p) { HC.text = p.prompt || ""; HC.mode = "clip"; HC.image = p.image || null;
  if (p.seconds) { const have = Object.keys((S && S.durations) || {}).map(Number); HC.seconds = have.length ? have.reduce((a, b) => Math.abs(b - p.seconds) < Math.abs(a - p.seconds) ? b : a) : Math.round(p.seconds); }
  render(); }
function hcAgent() { const text = HC.text.trim(); if (typeof AG !== "undefined") { AG.prompt = text; AG.draft = text; } if (typeof window !== "undefined") window.AGENT_DRAFT = text; go("Agent"); }
function hcMade() { if (!HC.made.length) return null;
  return el("div", { class: "cmp-made" }, el("span", { class: "eyebrow" }, "Just made"), el("div", { class: "strip2" }, HC.made.map(m => { const j = jobs.find(x => x.id === m.id) || { status: "queued" };
    const out = (j.outputs || []).find(o => !/sheet\.jpg$/.test(o));
    return el("div", { class: "tile", "data-made": m.id }, out ? media(out, /\.(mp4|mov)$/i.test(out) ? { controls: true, muted: true, playsinline: true } : {}) : el("div", { class: "cmp-wait" }, el("span", { class: "pill " + j.status }, j.status)),
      el("div", { class: "cap" }, el("span", {}, m.title), out ? el("button", { class: "small", onclick: () => go("Library") }, "Open") : null,
        el("button", { class: "small", disabled: !["done", "failed", "cancelled"].includes(j.status), onclick: () => hcGenerate(m) }, "Retake"))); }))); }
function homeComposer() {
  const mode = hcMode(), [, , , hint] = HC_MODES[mode];
  const area = el("textarea", { rows: 3, class: "cmp-text", placeholder: "What do you want to make?", "aria-label": "What do you want to make?" });
  area.value = HC.text;
  const grow = () => { if (area.style && area.scrollHeight) { area.style.height = "auto"; area.style.height = Math.min(360, area.scrollHeight + 2) + "px"; } };
  area.addEventListener("input", () => { HC.text = area.value; grow(); });
  area.addEventListener("keydown", e => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); hcGenerate(); } });
  const recipes = HC_RECIPES(), ids = Object.keys(recipes), rec = hcRecipe();
  return el("section", { class: "composer", id: "composer" },
    el("div", { class: "cmp-modes", role: "tablist", "aria-label": "What to make" }, Object.entries(HC_MODES).map(([id, [label, mk, model]]) =>
      el("button", { type: "button", role: "tab", "data-mode": id, "aria-selected": mode === id ? "true" : "false", class: "cmp-mode" + (mode === id ? " on" : ""), onclick: () => { HC.mode = id; render(); } },
        el("b", {}, label), typeof mark === "function" ? mark(id === "picture" && /^flux/.test(hcPictureModel()) ? "flux" : mk, id === "picture" && /^flux/.test(hcPictureModel()) ? "FLUX3-K" : model) : model))),
    area, el("div", { class: "cmp-hint" }, hint),
    el("div", { class: "cmp-bar" },
      el("button", { type: "button", class: "primary cmp-go", disabled: HC.busy, onclick: () => hcGenerate() }, HC.busy ? "Queuing…" : "Generate", el("kbd", {}, "⌘↵")),
      mode === "clip" ? el("button", { type: "button", class: "small cmp-recipe", title: "The render recipe: click for the next one (Settings → Presets sets the default)",
        onclick: () => { HC.recipe = ids[(ids.indexOf(rec) + 1) % ids.length]; render(); } }, el("span", { class: "eyebrow" }, "Recipe"), recipes[rec]) : null,
      mode === "clip" ? hcLength() : null,
      el("button", { type: "button", class: "ghost cmp-agent", onclick: hcAgent }, "Ask your agent instead"),
      typeof suitePromptPicker === "function" ? el("button", { type: "button", class: "ghost cmp-suite", onclick: () => suitePromptPicker(hcFromSuite) }, "From the suite") : null,
      mode === "clip" && HC.image ? el("span", { class: "tag2", title: HC.image }, "First frame: " + HC.image.split("/").pop(), el("button", { type: "button", class: "small ghost", "aria-label": "Take the first frame off", onclick: () => { HC.image = null; render(); } }, "Remove")) : null,
      el("span", { class: "su-grow" }),
      el("a", { href: "#Create", class: "cmp-more", onclick: e => { if (e.preventDefault) e.preventDefault(); hcMore(); } }, "More options →")),
    hcMade()); }

function viewHome() {
  const [hello, line] = timeOfDay(), wrap = el("div", { class: "home" }), look = homePrefs().appearance;
  const tc = el("span", { class: "tc" }, timecode()); const tick = setInterval(() => { if (!tc.isConnected) return clearInterval(tick); tc.textContent = timecode(); }, 1000 / 24);
  // the greeting beside the poster (smaller now: the composer under it leads the screen); Settings can take the poster off
  const poster = look.poster !== false;
  const hero = el("div", { class: "homehero slim" + (poster && ART.ready.has("poster") ? " has-art" : "") + (poster ? "" : " no-art") }, poster ? artSlot("poster", "", "home") : null,
    el("div", { class: "over" },
      el("div", { class: "stack" }, el("div", { style: "display:flex;gap:14px;align-items:center;flex-wrap:wrap" }, el("span", { class: "eyebrow" }, new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })), tc),
        el("h1", { class: "display" }, hello), el("p", { class: "tagline" }, "Local Creative ", el("em", {}, "All in One"), " Studio"),
        el("p", { class: "creator" }, el("span", { class: "by" }, "by"), el("img", { src: "brand/keys-wasserman-neon.svg", alt: "Keys + Wasserman", width: 320, height: 37 })),
        el("p", { class: "home-links" }, HOME_LINKS.map(([label, url]) => el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, label))),
        el("p", { class: "sub", style: "margin:0" }, line + " Pictures, clips with sound, voices and score, made on your own machine.")),
      el("div", { class: "acts" }, el("button", { onclick: () => go("Production") }, "Plan a film"), el("button", { class: "ghost", onclick: palOpen }, "Search ", el("kbd", {}, "⌘K")))));
  put(wrap, hero, typeof connectNudge === "function" ? connectNudge("home") : null, homeComposer());
  const starts = STARTS().filter(s => startShown(look, s.id));
  if (starts.length) put(wrap, el("div", { class: "sectionhead" }, el("h2", {}, "Start something"), el("span", { class: "note" }, "Each opens the full form with sensible settings.")),
    el("div", { class: "cards startrow" }, starts.map(pcard)));
  const recent = el("div", { class: "strip2" }, el("div", { class: "note" }, "Loading recent takes…"));
  put(wrap, el("div", { class: "sectionhead" }, el("h2", {}, "Recent takes"), el("button", { class: "small", onclick: () => go("Library") }, "Library")), recent);
  api("/api/library").then(list => { SHELL.libCache = list; const pick = list.filter(m => m.kind !== "audio").slice(0, 12);
    recent.replaceChildren(...(pick.length ? pick.map(m => el("div", { class: "tile" }, media(m.path, m.kind === "video" ? { controls: false, muted: true, loop: true, playsinline: true, onmouseenter: e => e.target.play().catch(() => {}), onmouseleave: e => e.target.pause() } : {}),
      el("div", { class: "cap" }, el("span", {}, m.path.split("/").pop()), m.kind === "video" ? el("button", { class: "small", onclick: () => addToTimeline(m.path) }, "+ timeline")
        : el("button", { class: "small", onclick: () => startCreate({ image: m.path, goes: "video", start: "own" }, 1) }, "animate"))))
      : [el("div", { class: "note" }, "Nothing rendered yet. Your takes will line up here.")])); }).catch(() => recent.replaceChildren(el("div", { class: "note" }, "The Library could not be read.")));
  if (typeof nightPanel === "function") put(wrap, el("div", { class: "sectionhead" }, el("h2", {}, "Night shift"), el("span", { class: "note" }, "A batch rendered while you sleep, with dailies in the morning.")), nightPanel());
  put(wrap, el("div", { class: "sectionhead" }, el("h2", {}, "Machine")), machineStrip());
  put(wrap, el("div", { class: "sectionhead" }, el("h2", {}, "Wasserman's Filmmaker Suite"), el("button", { class: "small", onclick: () => go("Import") }, "The whole suite, built in →")),
    el("div", { class: "suiteRow" }, SUITE_APPS.map(([n, s]) => el("button", { class: "app", type: "button", title: s, onclick: () => go("Import") },
      el("img", { src: "art/suite/" + SUITE_SLUG[n] + ".webp", alt: "", width: 32, height: 32, loading: "lazy" }), el("b", {}, n)))));
  return wrap;
}
