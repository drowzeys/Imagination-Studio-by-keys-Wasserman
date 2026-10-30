"use strict";
// Apps: every tool of the Studio as a one-click app, in one gallery. An app is a door, not a new engine: it opens the
// Studio's own form (Create, a Library action, a room) with the model, the LoRA or the start already chosen. A LoRA
// app that is not installed opens Looks on it; a Library action asks for the clip or the picture first, from the
// newest takes, then opens its panel (actions.js actOpen). What this machine cannot run says why instead of opening.
const AP = { cat: "all", q: "", pick: null, lib: null };
const AP_CATS = [
  { id: "cinema", name: "Cinema", line: "A film from a logline, a plan or one prompt" },
  { id: "people", name: "Characters", line: "The same face in every shot, swapped, cast or speaking" },
  { id: "camera", name: "Camera", line: "Moves, angles, POV and 360°" },
  { id: "vfx", name: "Effects", line: "Change a clip after it is made" },
  { id: "style", name: "Styles", line: "Looks and motion the base model misses" },
  { id: "sound", name: "Sound", line: "Voices, songs, effects and foley" },
  { id: "finish", name: "Finish", line: "Upscale, titles, captions, the cut and the mix" },
  { id: "suite", name: "Filmmaker Suite", line: "Wasserman's Filmmaker Suite of apps, built in" },
  { id: "studio", name: "Studio", line: "Overnight renders, nodes, your agent and the library" },
];
// a LoRA from the hub, put on the clip in Create (or Edit a picture); not installed, it opens Looks on it
const apLora = id => (typeof LORAS !== "undefined" ? LORAS : []).find(r => r.id === id) || null;
const apUseLora = r => r.target === "image" && typeof edUse === "function" ? edUse(r) : lookUseVideo(r);
function apGetLora(r) { if (typeof HUB !== "undefined") { HUB.q = r.name; HUB.tab = "all"; HUB.mine = false; }
  toast("Install “" + r.name + "” here, then open the app again"); go("Looks"); }
// a Library action wants a take of its kind first: the newest clips (or pictures) to pick from
function apPick(need, title, then) { AP.pick = { need, title, then }; if (!AP.lib) apLibrary(); render();
  setTimeout(() => { const n = document.querySelector("#appick"); if (n && n.scrollIntoView) n.scrollIntoView({ behavior: "smooth", block: "start" }); }, 60); }
async function apLibrary() { try { AP.lib = await api("/api/library"); if (typeof SHELL !== "undefined") SHELL.libCache = AP.lib; } catch { AP.lib = []; } render(); }
const apAct = (kind, need, title) => () => apPick(need, title, path => actOpen(kind, path));
const apArt = (slot, fallback) => typeof ART !== "undefined" && ART.ready.has(slot) ? slot : fallback;  // an app still of its own, once it is made
const apCreate = (set, step = 0) => () => startCreate(set, step);
const apActReady = kind => { if (typeof ACT === "undefined" || !ACT.state) return ""; const a = ACT.state[kind]; return a && !a.ready ? a.why || "Not ready on this machine yet" : ""; };
const apSwapReady = () => { if (typeof SWAP === "undefined") return "Character Swap is not in this build";
  if (!SWAP.state) { SWAP.state = { ready: false, why: "" }; swapLoad().then(() => { if (tab === "Apps") render(); }); return ""; }
  return SWAP.state.ready || !SWAP.state.why ? "" : SWAP.state.why; };

// the apps: [id, category, title, one line, art slot (or {src}), sign, what it opens]; `lora` apps are the hub's LoRAs
const APPS = () => [
  { id: "cinema-studio", cat: "cinema", title: "Cinema Studio", one: "Plan a film like a crew: script, cast, shot list, renders and the cut", art: "act-story-slate", sign: "story", run: () => go("Production") },
  { id: "director", cat: "cinema", title: "AI Director", one: "A logline in, a written, rendered, checked and cut film out", art: "engine-director", sign: "director", run: () => go("Director") },
  { id: "text-video", cat: "cinema", title: "Text to video", one: "A clip with dialogue, sound and score from one prompt", art: "start-clip", sign: "video", run: apCreate({ goes: "video", start: "text" }) },
  { id: "image-video", cat: "cinema", title: "Image to video", one: "Bring a still to life: your picture is the first frame", art: "act-video-own", sign: "video", run: apCreate({ goes: "video", start: "own" }, 1) },
  { id: "scout", cat: "cinema", title: "Scout, then animate", one: "First frames in one go; pick the best, then roll", art: "start-scout", sign: "video", run: apCreate({ goes: "video", start: "scout" }, 1) },
  { id: "long-take", cat: "cinema", title: "Long take", one: "30 seconds to 30 minutes, clip by clip, beat by beat", art: "start-long", sign: "video",
    run: () => { const k = S && S.long ? Object.keys(S.long)[0] : null; startCreate({ goes: "video", start: "text", ...(k ? { seconds: k } : {}) }); } },
  { id: "still", cat: "cinema", title: "Image generator", one: "Stills at any size with Qwen-Image 2.1 or FLUX3-K", art: "start-still", sign: "image", run: apCreate({ goes: "image" }) },
  { id: "sharp", cat: "cinema", title: "Sharp Spec", one: "Sam's locked recipe: 1376 × 768, eight passes", art: "start-sharp", sign: "video", run: () => { if (typeof sharpCreate === "function") sharpCreate(); go("Create"); } },
  { id: "horror", cat: "cinema", title: "80s Horror", one: "Wasserman's own LoRA: VHS neon or 16mm grindhouse", src: "art/looks/80s-horror.webp", sign: "looks", lora: "wasserman-80s-horror" },

  { id: "cast", cat: "people", title: "Consistent characters", one: "A sheet per character and place, the same in every shot", art: "engine-cast", sign: "cast", run: () => go("Cast & Places") },
  { id: "train", cat: "people", title: "Train a character", one: "A LoRA of your own from your clips and stills", art: "act-image-looks", sign: "train",
    off: () => typeof trainPanel === "function" ? "" : "Training is not in this build", run: () => { LK.train = true; go("Looks"); } },
  { id: "refs", cat: "people", title: "Reference to video", one: "Keep a face, an outfit, a motion or a voice in a new scene", art: "act-video-refs", sign: "video",
    off: () => S && S.ref2va ? "" : "Needs MiniMax H3 Ref2VA on this machine", run: apCreate({ goes: "video", start: "refs" }, 1) },
  { id: "swap", cat: "people", title: "Character Swap · Openjutsu", one: "Comfy's Openjutsu, fully local: one picture recasts a clip, performance and sound kept", art: "look-character-swap", sign: "cast", words: "genjutsu recast face swap",
    off: apSwapReady, run: () => apPick("video", "Character Swap", path => actOpen("swap", path)) },
  { id: "speech", cat: "people", title: "Talking faces", one: "Natural face muscles and clear speech for a person on camera", art: "look-natural-face-speech", sign: "cast", lora: "natural-face-speech" },
  { id: "realism", cat: "people", title: "Realistic people", one: "Faces that hold up in close-up, with real skin texture", src: "art/looks/realism-people.webp", sign: "cast", lora: "realism-people" },
  { id: "turnaround", cat: "people", title: "Turnaround sheet", one: "Five views of a character from one picture", art: "look-turnaround", sign: "image",
    off: () => apActReady("turnaround"), run: apAct("turnaround", "image", "Turnaround sheet") },

  { id: "camera", cat: "camera", title: "Camera moves", one: "Push-in, pull-back, orbit, crane, tilt and handheld, on cue", art: "look-camera-motion", sign: "video", lora: "camera-motion" },
  { id: "angles", cat: "camera", title: "Multiple angles", one: "The same subject from 12 directions, from one picture", art: "look-qwen-multiple-angles", sign: "image", lora: "qwen-multiple-angles" },
  { id: "orbit", cat: "camera", title: "360° bullet time", one: "One photo, a frozen-moment orbit all the way round", art: "look-orbit-360", sign: "video", lora: "orbit-360" },
  { id: "fpv", cat: "camera", title: "Head-mounted POV", one: "First person, with the bob of walking and running", art: "look-headmounted-fpv", sign: "video", lora: "headmounted-fpv" },
  { id: "vr", cat: "camera", title: "360° VR video", one: "A full-sphere panorama with sound, for VR players", art: "look-equi360", sign: "video", lora: "equi360" },
  { id: "previs", cat: "camera", title: "Motion previs", one: "Camera move, pose and depth from any clip, to guide a shot", art: "act-previs-motion", sign: "previs", run: () => go("Motion Previs") },

  { id: "edit-picture", cat: "vfx", title: "Edit a picture", one: "Change a still from a sentence: outfit, product, place, light", art: "act-image-edit", sign: "image", run: apCreate({ goes: "edit" }) },
  { id: "restyle", cat: "vfx", title: "Restyle a clip", one: "The same motion and camera in a whole new look", art: apArt("app-restyle", "look-anime-slider"), sign: "video", run: apAct("restyle", "video", "Restyle a clip") },
  { id: "fix", cat: "vfx", title: "Fix one thing", one: "Change one detail of a clip from a sentence", art: apArt("app-fix", "look-motion-continuity-repair"), sign: "video", run: apAct("fix", "video", "Fix one thing") },
  { id: "extend", cat: "vfx", title: "Extend a clip", one: "Carry a clip on from its last frame and join the two", art: apArt("app-extend", "start-long"), sign: "video", run: apAct("extend", "video", "Extend a clip") },
  { id: "remove", cat: "vfx", title: "Remove a person", one: "Take someone out of a clip and rebuild what was behind them", art: "look-person-remover", sign: "video",
    off: () => apActReady("remove"), run: apAct("remove", "video", "Remove a person") },
  { id: "morph", cat: "vfx", title: "Transformation", one: "Morph one picture into another, element by element", art: "look-transformation", sign: "video", lora: "transformation" },

  { id: "fight", cat: "style", title: "Fight scenes", one: "Faster, harder-hitting action and body movement", art: "look-combat-base-v2", sign: "video", lora: "combat-base-v2" },
  { id: "weapons", cat: "style", title: "Weapon combat", one: "Swords and staffs that hold together", art: "look-weapon-combat", sign: "video", lora: "weapon-combat" },
  { id: "wushu", cat: "style", title: "Kung fu", one: "Wushu kicks, spins and punches with weight behind them", art: "look-wushu-action", sign: "video", lora: "wushu-action" },
  { id: "physics", cat: "style", title: "Real physics", one: "Things that collide, stack and fall believably", art: "look-spatial-physics", sign: "video", lora: "spatial-physics" },
  { id: "anime", cat: "style", title: "Anime slider", one: "Dial from flat 2D anime to photoreal", art: "look-anime-slider", sign: "looks", lora: "anime-slider" },
  { id: "lineart", cat: "style", title: "Line art to anime", one: "Colour a line-art video into finished anime", art: "look-lineart-anime", sign: "looks", lora: "lineart-anime" },
  { id: "keyframes", cat: "style", title: "Hand-drawn animation", one: "Key drawings and inbetweens on held frames", art: "look-keyframe-animation", sign: "looks", lora: "keyframe-animation" },
  { id: "vhs", cat: "style", title: "VHS tape", one: "1980s home video, with a dial for the tape damage", art: "look-vh5tape-vhs", sign: "looks", lora: "vh5tape-vhs" },
  { id: "all-looks", cat: "style", title: "Every look", one: "The whole LoRA library, and your own", art: "vault-looks", sign: "looks", run: () => go("Looks") },

  { id: "voice", cat: "sound", title: "Voices & cloning", one: "Fish Audio Drama 3: cast a voice or clone your own", art: "start-voice", sign: "sound", run: () => go("Voice") },
  { id: "song", cat: "sound", title: "Songs & score", one: "YuE2 or MiniMax-Music3: lyrics and a style to a song", art: "start-song", sign: "sound", run: () => go("Music") },
  { id: "foley", cat: "sound", title: "Sound effects & foley", one: "Foley for a clip, or any sound from words", art: "start-sfx", sign: "sound", run: () => go("Foley") },

  { id: "upscale", cat: "finish", title: "Upscale", one: "A clip twice the size and sharper", art: "look-video-upscaler", sign: "video",
    off: () => apActReady("upscale"), run: apAct("upscale", "video", "Upscale") },
  { id: "captions", cat: "finish", title: "Captions", one: "A clip's speech burned in, word by word", art: apArt("app-captions", ""), sign: "graphics", run: () => apPick("video", "Captions", path => gfxOpen("captions", path)) },
  { id: "titles", cat: "finish", title: "Titles & motion graphics", one: "Lower thirds, title cards, slideshows and music videos", art: apArt("app-titles", ""), sign: "graphics", run: () => go("Motion graphics") },
  { id: "timeline", cat: "finish", title: "Timeline editor", one: "Cut your takes together, with sound", art: "act-edit-timeline", sign: "edit", run: () => go("Editor") },
  { id: "finish", cat: "finish", title: "Finish & export", one: "The final cut and mix, out to Resolve, Premiere or After Effects", art: "act-edit-resolve", sign: "edit", run: () => go("Finish") },

  { id: "night", cat: "studio", title: "Night shift", one: "Queue a batch tonight, wake up to dailies on your phone", art: "engine-night", sign: "night", run: () => go("Night shift") },
  { id: "nodes", cat: "studio", title: "Nodes", one: "Words to pictures to clips, wired in order", art: "vault-nodes", sign: "nodes", off: () => typeof viewNodes === "function" ? "" : "The node canvas is not in this install", run: () => go("Nodes") },
  { id: "agent", cat: "studio", title: "Ask your agent", one: "A local brain, or the agent you already use, drives the Studio", art: "start-agent", sign: "agent", run: () => go("Agent") },
  { id: "library", cat: "studio", title: "Library", one: "Every take, with its prompt and seed", art: "vault-library", sign: "library", run: () => go("Library") },
  { id: "suite", cat: "suite", title: "Filmmaker Suite", one: "Bring work in from the suite, send the cut out", art: "vault-suite", sign: "suite", run: () => go("Import") },
  ...apSuite(),
];
// each app of Wasserman's Filmmaker Suite (suite.js): its own icon, opening where the Studio takes its work
const AP_SUITE_GO = { previs: () => go("Motion Previs") };
function apSuite() { if (typeof suApps !== "function") return [];
  if (typeof SU !== "undefined" && SU.apps === null && typeof suLoad === "function" && !AP.suite) { AP.suite = true; suLoad().then(() => { if (tab === "Apps") render(); }).catch(() => {}); }
  const seen = new Set();
  return suApps().filter(a => a.id !== "editor" && SU_ICON[a.id] && !seen.has(SU_ICON[a.id]) && seen.add(SU_ICON[a.id])).map(a => ({ id: "suite-" + a.id, cat: "suite", title: a.name, one: a.what || "", src: "art/suite/" + SU_ICON[a.id] + ".webp", sign: "suite", icon: true,
    run: AP_SUITE_GO[SU_ROLE[a.id]] || (() => go("Import")) })); }

// what an app does on this machine now: {run, off, need}; a LoRA app follows the hub's own state of that LoRA
function apState(a) {
  if (!a.lora) { const off = a.off ? a.off() : ""; return { run: a.run, off }; }
  const r = apLora(a.lora);
  if (!r) return { off: "Not in this copy of the Looks hub" };
  if (r.status === "unsupported") return { off: r.why || "Not supported on this machine yet" };
  if (!r.installed) return { run: () => apGetLora(r), need: r.installing && r.installing.stage !== "failed" ? "Installing" : "Install" };
  return { run: () => apUseLora(r) }; }
function apCard(a) { const st = apState(a);
  const card = pcard({ art: a.art, src: a.src, sign: a.sign, title: a.title, one: a.one, k: (AP_CATS.find(c => c.id === a.cat) || {}).name.toUpperCase(), off: st.off, run: st.run, data: { "data-app": a.id } });
  if (a.icon) card.classList.add("ap-icon");  // an app's square icon, whole, on the card's ground
  if (st.need) card.append(el("span", { class: "ap-need" }, st.need));
  return card; }
const apHit = (a, words) => !words || [a.title, a.one, a.words, (AP_CATS.find(c => c.id === a.cat) || {}).name].join(" ").toLowerCase().includes(words);

// the take a Library action works on: the newest of the kind it needs
function apPicker() { const p = AP.pick; if (!p) return null;
  const list = AP.lib ? AP.lib.filter(m => m.kind === p.need).slice(0, 18) : null;
  return el("section", { class: "panel appick", id: "appick" },
    el("div", { class: "row", style: "align-items:center" }, el("h3", { style: "margin:0" }, p.title + ": pick the " + (p.need === "video" ? "clip" : "picture")), el("span", { class: "su-grow" }),
      el("button", { type: "button", class: "small ghost", onclick: () => { AP.pick = null; render(); } }, "Cancel")),
    list === null ? el("div", { class: "note" }, "Loading your takes…")
    : !list.length ? el("div", { class: "note" }, "No " + (p.need === "video" ? "clips" : "pictures") + " in the Library yet. Make one first, then open the app again.",
        el("div", { class: "row", style: "margin-top:10px" }, el("button", { type: "button", class: "small", onclick: () => startCreate({ goes: p.need === "video" ? "video" : "image", start: "text" }) }, p.need === "video" ? "Make a clip" : "Make a picture"), el("span")))
    : el("div", { class: "strip2" }, list.map(m => el("button", { type: "button", class: "tile appick-t", title: m.path, onclick: () => { const then = p.then; AP.pick = null; then(m.path); } },
        media(m.path, m.kind === "video" ? { controls: false, muted: true, playsinline: true, preload: "metadata" } : {}), el("div", { class: "cap" }, el("span", {}, m.path.split("/").pop()))))) ); }

function viewApps() {
  if (typeof LORAS !== "undefined" && !LORAS.length && !AP.loras) { AP.loras = true; loadLoras().then(() => render()).catch(() => {}); }
  const all = APPS(), words = AP.q.trim().toLowerCase();
  const cats = [{ id: "all", name: "All" }, ...AP_CATS];
  const search = el("input", { type: "search", class: "looksearch", placeholder: "Search apps: swap, orbit, captions…", value: AP.q, "aria-label": "Search apps",
    oninput: e => { AP.q = e.target.value; const at = e.target.selectionStart; render(); const again = document.querySelector(".apps input[type=search]"); if (again) { again.focus(); again.setSelectionRange(at, at); } } });
  const bar = el("div", { class: "looksbar" }, search,
    el("div", { class: "seg-ctl", role: "tablist", "aria-label": "Kinds of app" }, cats.map(c => el("button", { type: "button", role: "tab", "aria-selected": AP.cat === c.id ? "true" : "false", class: AP.cat === c.id ? "on" : "",
      onclick: () => { AP.cat = c.id; render(); } }, c.name))));
  const marks = el("div", { class: "ap-built" }, el("span", { class: "eyebrow" }, "Runs on"), ["comfy", "tensorfold", "minimax", "qwen", "flux", "fish", "yue"].map(id => mark(id)));
  const usable = all.filter(a => !apState(a).off).length;
  const head = pageHead("Imagination Studio", "Apps", "Every tool of a hosted AI studio, and more, on your own machine. Each app opens the Studio's own form with the model and settings chosen.",
    el("div", { class: "ap-count" }, [[all.length, "apps"], [usable, "ready here"], [0, "credits to spend"]].map(([n, what], i) => [i ? el("i") : null, el("span", { class: "ap-n" }, el("b", {}, String(n)), " ", el("span", {}, what))])));
  const sections = AP_CATS.filter(c => AP.cat === "all" || AP.cat === c.id).map(c => { const list = all.filter(a => a.cat === c.id && apHit(a, words)); if (!list.length) return null;
    return el("section", { class: "ap-sec", "data-cat": c.id }, el("div", { class: "sectionhead" }, el("h2", {}, c.name), el("span", { class: "note" }, c.line)), el("div", { class: "cards apps-grid" }, list.map(apCard))); }).filter(Boolean);
  return el("div", { class: "apps" }, head, marks, bar, apPicker(),
    sections.length ? sections : el("div", { class: "note", style: "margin-top:24px" }, "No app matches “" + AP.q + "”.")); }

// ⌘K: every app by name
function appsPalette(a) { return APPS().map(x => a(x.sign in ICON ? x.sign : "apps", x.title, "Apps · " + (AP_CATS.find(c => c.id === x.cat) || {}).name, () => { const st = apState(x); if (st.off) return toast(st.off); go("Apps"); st.run(); }, x.one + " " + (x.words || "") + " app")); }
