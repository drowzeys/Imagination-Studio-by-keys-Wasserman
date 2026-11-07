"use strict";
// Home: where the studio opens. One composer to generate straight away, then the ways in, recent takes, the machine
// and the suite.
const SUITE_SLUG = { ScriptBreak: "scriptbreak", "Cork Board": "cork-board", Blockout: "blockout", "Storyboard Reference": "storyboard-reference", "Master Canvas": "master-canvas", Slate: "slate", "Circle Take": "circle-take", "Stem Studio": "stem-studio" };
const SUITE_APPS = [
  ["ScriptBreak", "a screenplay into scenes, bibles and a shot list"], ["Cork Board", "index cards, acts and arcs"], ["Blockout", "3D previs: marks, lenses, camera moves"],
  ["Storyboard Reference", "reframed stills with a prompt each"], ["Master Canvas", "boards, references and shot order"], ["Slate", "the desktop film planner"],
  ["Circle Take", "dailies and a QC gate"], ["Stem Studio", "dialogue, music and effects stems"],
];
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
    t && t.gpu ? m(t.gpu.count > 1 ? t.gpu.count + " GPUs" : "GPU", t.gpu.util_pct, (t.gpu.util_pct ?? "–") + "%" + (t.gpu.temp_c != null ? " · " + t.gpu.temp_c + "°C" : ""), t.gpu.util_pct > 60 ? "var(--red)" : "var(--green)") : el("div", { class: "m" }, el("span", { class: "note", style: "margin:0" }, t ? "" : "Readouts appear once the machine reports them.")),
    t && t.memory ? m("Memory", memUsed, (card ? gb(card.used_mb) + " of " + gb(card.total_mb) : gb(t.memory.total_mb - t.memory.available_mb) + " of " + gb(t.memory.total_mb)) + " GB", memUsed > 85 ? "var(--red)" : "var(--amber)", card ? null : (t.memory.floor_mb ? 100 - pct(t.memory.floor_mb, t.memory.total_mb) : null)) : el("div", { class: "m" }),
    t && t.disk ? m("Disk", t.disk.used_pct, Math.round(t.disk.free_gb) + " GB free", t.disk.used_pct > 90 ? "var(--red)" : "var(--blue)", null, "disk") : el("div", { class: "m disk" }),
    el("button", { class: "small ghost", onclick: () => go("Machine") }, "Telemetry"));
}
function homeLive() { const box = $("#homenow"); if (box) box.replaceWith(homeNow()); }

// the eight ways in, each a picture card (art/start-*.webp); Settings → Appearance picks which show (six fill a row)
const STARTS = () => {
  const cards = [
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
  if (S && S.platform === "windows") {
    cards.find(c => c.id === "still").one = typeof imageModelName === "function"
      ? imageModelName() + (imageModel() === "sd-turbo" ? ": 512 × 512 storyboard drafts" : ": full-model pictures")
      : "SD-Turbo: 512 × 512 storyboard drafts";
    cards.find(c => c.id === "sfx").title = "Sound effects";
    cards.find(c => c.id === "sfx").one = "Stable Audio Open: each backend has its own recipe";
    cards.find(c => c.id === "sfx").run = () => { SFX.mode = "sfx"; go("Foley"); };
    cards.find(c => c.id === "agent").one = "Copilot CLI with This Studio tools";
    cards.push({ id: "film", sign: "story", title: "Plan a film", one: "Slate, the Bible and portable project handoff", run: () => go("Production") },
      { id: "script", sign: "suite", title: "Bring in a script", one: "Fountain or suite exports; no model required", run: () => go("Import") },
      { id: "graphics", sign: "edit", title: "Motion graphics", one: "Six templates from your own words, clips and audio", run: () => go("Graphics") });
  }
  const kinds = { clip: "video", scout: "video", still: typeof imageFeature === "function" ? imageFeature() : "scout", sharp: "video", long: "video", voice: "voice", song: "music", sfx: "sfx", looks: "looks", previs: "motion", nodes: "nodes", graphics: "graphics" };
  return cards.map(c => ({ ...c, feature: kinds[c.id], off: c.id === "still" && typeof imageModelWhy === "function"
    ? imageModelWhy() : (typeof capWhy === "function" ? capWhy : () => "")(kinds[c.id]) }));
};
// 3.0's cards show unless switched off in Settings, even beside a start_cards list saved before they existed
const START_NEW = ["sfx", "looks", "previs", "nodes", "film", "script", "graphics"];
const startShown = (look, id) => (look.start_cards || []).includes(id) || (START_NEW.includes(id) && !(look.start_off || []).includes(id));
const homePrefs = () => typeof prefs === "function" ? prefs() : { appearance: { start_cards: ["clip", "scout", "still", "sharp", "long", "voice"], poster: true }, presets: { recipe: "quick", mode: "clip", seconds: 5, seed: "fixed", qa_gate: true, image_model: "qwen" } };

/* ---------- the composer: one box, a chip per kind of take and engine, one Generate ---------- */
const HC = { text: "", mode: null, pictureModel: null, fluxModel: null, recipe: null, seconds: null, est: {}, estInfo: {}, estAdmission: {}, estPending: {}, made: [], busy: false, image: null };
const hcSpan = sec => sec == null ? "" : sec < 90 ? Math.round(sec) + " s" : sec < 5400 ? Math.round(sec / 60) + " min" : Math.floor(sec / 3600) + " h " + Math.round(sec % 3600 / 60) + " min";
const HC_MODES = { clip: ["Clip with sound", "minimax", "MiniMax H3", "Describe the shot: who is there, what happens, what is heard."],
  picture: ["Picture", "qwen", "Qwen 2.1", "Describe the picture: the subject, the place, the light, the lens."],
  flux: ["Picture", "flux", "FLUX3 Klein", "Describe the picture: the subject, the place, the light, the lens."],
  voice: ["Voice", "fish", "Drama 3", "Write the line as it is said. A [direction] in brackets sets the voice."],
  song: ["Song", "music3", "Music 3", "A style (genre, tempo, mood), or lyrics with [Verse] and [Chorus] tags."],
  yue: ["Song", "yue", "YuE2", "A style (genre, tempo, mood), or lyrics with [Verse] and [Chorus] tags."] };
const HC_RECIPES = () => ({ quick: "Quick look", ...(S && S.sharp ? { sharp: "Sharp Spec" } : {}), final: "Final 2K" });
const hcKind = mode => ({ clip: "video", picture: "scout", flux: "flux", voice: "voice", song: "music", yue: "music" })[mode];
// FLUX3 Klein and YuE2 have a chip only where they are installed; where YuE2 is the only song engine ready, Music 3 has none
const hcModels = () => typeof imageModels === "function" ? imageModels()
  : (S && S.image_models?.length ? S.image_models : [{ id: "qwen", label: "Qwen 2.1 Turbo" }]).map(m =>
    typeof m === "string" ? { id: m, label: m, available: true } : { available: true, ...m });
const hcFluxModel = () => { if (HC.fluxModel) return HC.fluxModel;
  const want = homePrefs().presets.image_model, have = hcModels().filter(m => m.available !== false && /^flux/.test(m.id)).map(m => m.id);
  return have.includes(want) ? want : have.includes("flux3k-fast") ? "flux3k-fast" : have[0] || null; };
const hcSongEngines = () => (S && S.music && S.music.engines) || null;
const hcYue = () => { const e = hcSongEngines(); return !!(e && e.yue2 && (e.yue2.installed || e.yue2.available)); };
const hcHas = id => id === "flux" ? !!hcFluxModel() : id === "yue" ? hcYue() : id !== "song" || !hcYue() || !hcSongEngines().music3 || !!hcSongEngines().music3.available;
// Settings says the kind of take; the picture model and song engine it presets choose that kind's chip
const hcMode = () => {
  if (S && S.platform === "windows") { if (["picture", "flux"].includes(HC.mode)) return HC.mode;
    const mode = HC.mode || (HC_MODES[homePrefs().presets.mode] ? homePrefs().presets.mode : "clip");
    return (typeof capWhy === "function" ? capWhy : () => "")(hcKind(mode)) && mode !== "picture" ? "picture" : mode; }
  if (HC.mode && hcHas(HC.mode)) return HC.mode;
  const p = homePrefs().presets, want = HC_MODES[p.mode] ? p.mode : "clip";
  if (want === "picture" && /^flux/.test(p.image_model || "") && hcHas("flux")) return "flux";
  if (want === "song" && S && S.music && S.music.engine === "yue2" && hcHas("yue")) return "yue";
  return hcHas(want) ? want : "clip"; };
const hcRecipe = () => { const r = HC.recipe || homePrefs().presets.recipe; return HC_RECIPES()[r] ? r : "quick"; };
const hcPictureModel = () => { if (HC.pictureModel) return HC.pictureModel;
  const want = homePrefs().presets.image_model, have = hcModels().filter(m => m.available !== false && !/^flux/.test(m.id)).map(m => m.id);
  return have.includes(want) ? want : have[0] || hcModels()[0].id; };
const hcModel = mode => mode === "flux" ? hcFluxModel() : hcPictureModel();
const hcWhy = (mode, model = hcModel(mode)) => ["picture", "flux"].includes(mode) && typeof imageModelWhy === "function"
  ? imageModelWhy(model) : (typeof capWhy === "function" ? capWhy : () => "")(hcKind(mode));
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
async function hcRequest(mode, text, seed, retake, model = hcModel(mode)) {
  const why = hcWhy(mode, model); if (why) throw new Error(why);
  const p = homePrefs().presets;
  if (mode === "clip") { const r = typeof recipeParams === "function" ? recipeParams(hcRecipe()) : { preset: "small", quality: "fh4" };
    const seconds = hcSeconds(), long = hcLongs()[seconds];
    const still = !long && HC.image || null;
    const composed = await api("/api/compose", { fields: { scene: text, action: "", camera: "", dialogue: [], soundscape: "", music: "N/A" }, first_frame: !!still, last_frame: false, seconds: long ? 15 : seconds });
    const params = { prompt: composed.prompt, image: still, preset: r.preset, quality: r.quality, seed: r.seed && !retake ? r.seed : seed, ...(p.qa_gate ? {} : { qa: false }) };
    return long ? ["/api/jobs", { kind: "long", title: long + ": " + hcTitle(text).slice(0, 40), params: { ...params, total_seconds: seconds, beats: [] } }]
      : ["/api/jobs", { kind: "video", title: hcTitle(text), params: { ...params, seconds } }]; }
  if (mode === "picture" || mode === "flux") return ["/api/jobs", { kind: "scout", title: "Still: " + hcTitle(text).slice(0, 40), params: stillParams({ prompt: text, count: 1, seed, preset: "2k", image_model: model }) }];
  if (mode === "voice") return ["/api/voice/takes", { text, seed }];
  const lyrics = /\[(intro|verse|pre-chorus|chorus|bridge|instrumental|outro)\]/i.test(text);
  const chosen = hcSongEngines() ? { engine: mode === "yue" ? "yue2" : "music3" } : {};
  return ["/api/music/takes", lyrics ? { caption: "A warm, cinematic song with a clear lead vocal.", lyrics: text, seconds: 60, seed, ...chosen } : { caption: text, lyrics: "", seconds: 60, seed, ...chosen }]; }
async function hcGenerate(again) {
  const mode = again ? again.mode : hcMode(), text = (again ? again.text : HC.text).trim();
  if (!text) return toast("Say what you want to make first");
  if (HC.busy) return; HC.busy = true; render();
  try { const [path, body] = await hcRequest(mode, text, again ? again.seed + 1 : hcSeed(), !!again, again?.image_model || hcModel(mode));
    const job = await api(path, body), seed = (body.params ? body.params.seed : body.seed) ?? 1;
    HC.made.unshift({ id: job.id, mode, text, seed, image_model: body.params?.image_model, title: job.title || hcTitle(text) }); HC.made = HC.made.slice(0, 12);
    HC.image = null; toast("Queued: " + (job.title || hcTitle(text))); if (typeof poll === "function") await poll(); }
  catch (e) { toast(e.message, 6000); }
  HC.busy = false; render(); }
// the full form for this kind of take, with the words carried over
function hcMore() { const mode = hcMode(), text = HC.text.trim();
  if (mode === "voice") { if (typeof VO !== "undefined") VO.text = text; return go("Voice"); }
  if (mode === "song" || mode === "yue") { if (typeof MU !== "undefined") { MU.engine = mode === "yue" ? "yue2" : "music3"; if (/\[[A-Za-z-]+\]/.test(text)) MU.lyrics = text; else MU.caption = text; } return go("Music"); }
  Object.assign(C, mode === "clip" ? { goes: "video", start: "text", ...(HC.seconds ? { seconds: HC.seconds } : {}) } : { goes: "image", imageModel: mode === "flux" ? hcFluxModel() : hcPictureModel() }); C.step = 0; C.scene = text; sync(); C.prompt = text; go("Create"); }
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
  return el("div", { class: "cmp-wrap" }, el("section", { class: "composer", id: "composer" },
    el("div", { class: "cmp-head" },
      el("div", { class: "cmp-modes", role: "tablist", "aria-label": "What to make" }, Object.entries(HC_MODES).filter(([id]) => hcHas(id)).map(([id, [label, mk, model]]) =>
        el("button", { type: "button", role: "tab", "data-mode": id, title: hcWhy(id) || label + " · " + model, disabled: !!hcWhy(id), "aria-selected": mode === id ? "true" : "false", class: "cmp-mode" + (mode === id ? " on" : ""), onclick: () => { HC.mode = id; render(); } },
          el("b", {}, label), S && S.platform === "windows" && ["picture", "flux"].includes(id)
            ? hcModels().find(m => m.id === hcModel(id))?.label || hcModel(id) : typeof mark === "function" ? mark(mk, model) : model)))),
    area,
    S && S.platform === "windows" && ["picture", "flux"].includes(mode) ? el("div", { class: "row" },
      el("label", {}, "Picture model", el("select", { "aria-label": "Home picture model", onchange: e => {
        HC[mode === "flux" ? "fluxModel" : "pictureModel"] = e.target.value; render();
      } }, hcModels().filter(m => mode !== "flux" || /^flux/.test(m.id)).map(m => el("option", { value: m.id, selected: m.id === hcModel(mode) },
        m.label + (m.id === "sd-turbo" ? " · storyboard draft · 512 × 512 · one step" : " · full model") + (m.available === false ? " · " + (m.why || "Unavailable; see Feature checks") : ""))))),
      typeof featureBadge === "function" && typeof imageFeature === "function" ? featureBadge(imageFeature(hcModel(mode))) : null,
      typeof openFeatureCheck === "function" && typeof imageFeature === "function" ? el("button", { class: "small", type: "button",
        onclick: () => openFeatureCheck(imageFeature(hcModel(mode))) }, "Feature checks") : null) : null,
    hcWhy(mode) ? el("div", { class: "note warn" }, hcWhy(mode)) : null,
    el("div", { class: "cmp-bar" },
      mode === "clip" ? el("button", { type: "button", class: "small cmp-recipe", title: "The render recipe: click for the next one (Settings → Presets sets the default)",
        onclick: () => { HC.recipe = ids[(ids.indexOf(rec) + 1) % ids.length]; render(); } }, recipes[rec]) : null,
      mode === "clip" ? hcLength() : null,
      mode === "clip" && HC.image ? el("span", { class: "tag2", title: HC.image }, "First frame: " + HC.image.split("/").pop(), el("button", { type: "button", class: "small ghost", "aria-label": "Take the first frame off", onclick: () => { HC.image = null; render(); } }, "Remove")) : null,
      el("span", { class: "su-grow" }),
      el("button", { type: "button", class: "primary cmp-go", title: hcWhy(mode), disabled: HC.busy || !!hcWhy(mode), onclick: () => hcGenerate() }, HC.busy ? "Queuing…" : "Generate", el("kbd", {}, "⌘↵"))),
    hcMade()),
    el("div", { class: "cmp-alt" }, el("span", { class: "cmp-hint" }, hint), el("span", { class: "su-grow" }),
      el("button", { type: "button", class: "ghost small cmp-agent", onclick: hcAgent }, "Ask your agent instead"),
      typeof suitePromptPicker === "function" ? el("button", { type: "button", class: "ghost small cmp-suite", onclick: () => suitePromptPicker(hcFromSuite) }, "From the suite") : null,
      el("a", { href: "#Create", class: "cmp-more", onclick: e => { if (e.preventDefault) e.preventDefault(); hcMore(); } }, "More options →"))); }

// Home, right of the composer: what is rendering now and what is next, then this machine in one card
function homeNow() {
  const running = jobs.filter(j => j.status === "running"), queued = jobs.filter(j => j.status === "queued");
  const now = running.map(j => { const { share, left } = jobShare(j), out = (j.inputs && j.inputs.image) || (j.params && j.params.image);
    return el("div", { class: "now-card" },
      el("div", { class: "now-top" }, el("span", { class: "now-tag" }, "Rendering"), j.spark ? el("span", { class: "now-where" }, typeof spkLabel === "function" ? spkLabel(j.spark) : j.spark) : null),
      el("b", {}, j.title), el("span", { class: "now-kind" }, (j.kind === "video" ? "Clip with sound" : j.kind === "long" ? "Long take" : j.kind === "scout" ? "Pictures" : j.kind) || ""),
      el("div", { class: "bar" }, el("i", { style: "width:" + share + "%" })),
      el("div", { class: "kv" }, el("span", {}, share + "%"), el("b", {}, left == null ? "" : left > 5 ? mmss(left) + " left" : "almost done"))); });
  return el("aside", { class: "home-now", id: "homenow", "aria-label": "Now" },
    now.length ? now : el("div", { class: "now-card idle" }, el("span", { class: "dotl" }), el("div", {}, el("b", {}, "Nothing rendering"), el("span", { class: "now-kind" }, "Generate something and it shows here."))),
    queued.length ? el("div", { class: "now-next" }, el("span", { class: "eyebrow" }, "Up next"),
      queued.slice(0, 4).map((j, i) => el("button", { type: "button", class: "now-row", onclick: () => bayOpen(true) }, el("span", { class: "n" }, String(i + 2 - (running.length ? 0 : 1))),
        el("span", { class: "t" }, el("b", {}, j.title), el("small", {}, j.kind))))) : null,
    machineStrip()); }
function viewHome() {
  const wrap = el("div", { class: "home" }), look = homePrefs().appearance;
  const recent = el("div", { class: "home-takes" }, el("div", { class: "note" }, "Loading recent takes…"));
  const main = el("div", { class: "home-main" },
    el("h1", { class: "home-ask" }, "What are we making?"),
    typeof connectNudge === "function" ? connectNudge("home") : null, homeComposer());
  const starts = STARTS().filter(s => startShown(look, s.id));
  if (starts.length) put(main, el("div", { class: "sectionhead" }, el("h2", {}, "Start something"), el("span", { class: "note" }, "Each opens the full form with sensible settings.")),
    el("div", { class: "starts" }, starts.map(s => el("button", { type: "button", class: "start", "data-start": s.id, title: s.one, onclick: s.run }, icon(s.sign || "video", "ic"), el("span", { class: "t" }, el("b", {}, s.title), el("small", {}, s.one))))));
  put(main, el("div", { class: "sectionhead" }, el("h2", {}, "Recent takes"), el("a", { href: "#Library", onclick: e => { if (e.preventDefault) e.preventDefault(); go("Library"); } }, "Open Library →")), recent);
  api("/api/library").then(list => { SHELL.libCache = list; const pick = list.filter(m => m.kind !== "audio").slice(0, 8);
    recent.replaceChildren(...(pick.length ? pick.map(m => el("div", { class: "take" }, el("div", { class: "pic" }, media(m.path, m.kind === "video" ? { controls: false, muted: true, loop: true, playsinline: true, preload: "metadata", src: file(m.path) + "#t=0.5", onmouseenter: e => e.target.play().catch(() => {}), onmouseleave: e => e.target.pause() } : {})),
      el("div", { class: "cap" }, el("span", {}, m.path.split("/").pop()), m.kind === "video" ? el("button", { class: "small ghost", onclick: () => addToTimeline(m.path) }, "+ timeline")
        : el("button", { class: "small ghost", onclick: () => startCreate({ image: m.path, goes: "video", start: "own" }, 1) }, "Animate"))))
      : [el("div", { class: "note" }, "Nothing rendered yet. Your takes will line up here.")])); }).catch(() => recent.replaceChildren(el("div", { class: "note" }, "The Library could not be read.")));
  if (typeof nightPanel === "function") put(main, el("div", { class: "sectionhead" }, el("h2", {}, "Night shift"), el("span", { class: "note" }, "A batch rendered while you sleep, with dailies in the morning.")), nightPanel());
  put(main, el("div", { class: "sectionhead" }, el("h2", {}, "Wasserman's Filmmaker Suite"), el("a", { href: "#Import", onclick: e => { if (e.preventDefault) e.preventDefault(); go("Import"); } }, "The whole suite, built in →")),
    el("div", { class: "suiteRow" }, SUITE_APPS.map(([n, s]) => el("button", { class: "app", type: "button", title: s, onclick: () => go("Import") },
      el("img", { src: "art/suite/" + SUITE_SLUG[n] + ".webp", alt: "", width: 32, height: 32, loading: "lazy" }), el("b", {}, n)))));
  put(main, el("p", { class: "home-foot" }, "Imagination Studio by keys+Wasserman · ", el("span", { class: "home-links" }, HOME_LINKS.map(([label, url]) => el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, label)))));
  put(wrap, main, homeNow());
  return wrap;
}
