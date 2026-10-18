"use strict";
// The Creative Engines' landings: each room opens on a cover, a line, the marks of the models it runs, and picture
// cards for what it makes; a card opens the view that does it (Create already set, the film board, Voice, ...).
// Pictures come from art/<name>.webp once art/ART-MANIFEST.json marks the slot ready; until then a card shows a plain
// panel with the room's sign, so a missing picture is never a broken image or a failed request.
const ART = { ready: new Set(), loaded: false };
function artLoad() { if (typeof location === "undefined" || !location.protocol) return Promise.resolve();
  return fetch("art/ART-MANIFEST.json").then(r => r.ok ? r.json() : { slots: [] }).then(m => { for (const s of m.slots || []) if (s.ready) ART.ready.add(s.name);
    ART.loaded = true; if (ART.ready.size && S && typeof render === "function") render(); }).catch(() => {}); }
artLoad();
function artSlot(name, cls = "", sign = "image") { const box = el("div", { class: "art " + cls, "data-art": name }, icon(sign, "ph"));
  if (ART.ready.has(name)) box.append(el("img", { src: "art/" + name + ".webp", alt: "", loading: "lazy", decoding: "async" }));
  return box; }
// a picture card: the picture, one label, one line, a chevron
function pcard({ art, sign, title, one, run, k, off, src }) {
  const pic = src ? el("div", { class: "art" }, el("img", { src, alt: "", loading: "lazy" })) : artSlot(art, "", sign);
  return el("button", { class: "pcard", type: "button", onclick: run, disabled: !!off, title: off || one || "" }, pic, k ? el("span", { class: "k" }, k) : null,
    el("div", { class: "txt" }, el("div", {}, el("b", {}, title), one ? el("span", { class: "one" }, off || one) : null), icon("go", "go"))); }
function pageHead(eyebrow, title, line, right) {
  return el("div", { class: "pagehead" }, el("div", { class: "stack" }, el("span", { class: "eyebrow" }, eyebrow), el("h1", { class: "display" }, title), line ? el("p", { class: "sub" }, line) : null), right || null); }

const slateMode = (mode, view) => { SL.mode = mode; try { localStorage.setItem("production-mode", mode); } catch {} if (view && SL.p) SL.view = view; go("Production"); };
const longFirst = () => S && S.long ? Object.keys(S.long)[0] : null;
// each engine: its cover, one line, the marks it shows, its one amber action, and its cards
const ENGINE_PAGES = {
  story: () => ({ line: "Plan the film: the board, the bible, the shots. Wasserman Slate and the First AD lay it out with you.", marks: [],
    primary: ["Open the film", () => go("Production")], cards: [
      { art: "act-story-board", sign: "story", title: "Film board", one: "Segments in order, one prompt each", run: () => slateMode("board") },
      { art: "act-story-slate", sign: "story", title: "Wasserman Slate", one: "Bible, studios and shots for a whole film", run: () => slateMode("slate") },
      { art: "act-story-firstad", sign: "story", title: "First AD", one: "Say what you are making; it lays out scenes and shots", run: () => slateMode("slate", "ad") },
      { art: "act-story-import", sign: "suite", title: "Bring in a script", one: "ScriptBreak, Fountain or Final Draft", run: () => go("Import") }] }),
  image: () => ({ line: "Stills, scouts and picture edits on Qwen 2.1 or FLUX3-K, at any size the models take.", marks: [mark("qwen", "Qwen 2.1"), mark("flux")],
    primary: ["Make a still", () => startCreate({ goes: "image" })], cards: [
      { art: "start-still", sign: "image", title: "A still", one: "One picture or several, any size", run: () => startCreate({ goes: "image" }) },
      { art: "act-image-scouts", sign: "image", title: "Scouts", one: "Six candidates in one load; pick one", run: () => startCreate({ goes: "image", count: 6 }) },
      { art: "act-image-edit", sign: "image", title: "Edit a picture", one: "Change it in words, or move the camera", run: () => startCreate({ goes: "edit" }) },
      { art: "act-image-looks", sign: "looks", title: "Looks", one: "LoRA styles for pictures and clips", run: () => go("Looks") }] }),
  video: () => ({ line: "Clips with sound on MiniMax H3 and FastH3, up to 15 seconds; longer ones as chained takes.",
    marks: [mark("minimax"), mark("tensorfold"), S && S.platform === "spark" ? mark("comfy") : null],
    primary: ["Roll a new clip", () => startCreate({ goes: "video", start: "text" })], cards: [
      { art: "start-clip", sign: "video", title: "A clip with sound", one: "Text to video, with spoken lines and a soundscape", run: () => startCreate({ goes: "video", start: "text" }) },
      { art: "start-scout", sign: "video", title: "Scout, then animate", one: "First frames in one load; pick one, then roll", run: () => startCreate({ goes: "video", start: "scout" }, 1) },
      { art: "act-video-own", sign: "video", title: "From your picture", one: "Your own first frame", run: () => startCreate({ goes: "video", start: "own" }, 1) },
      { art: "act-video-refs", sign: "video", title: "References", one: "Keep a face, a motion or a voice", run: () => startCreate({ goes: "video", start: "refs" }, 1),
        off: S && S.ref2va ? "" : "Needs Ref2VA: oneshot-setup.sh --ref2va" },
      { art: "start-long", sign: "video", title: "A long take", one: "Chained clips, 30 seconds and up", run: () => { const k = longFirst(); startCreate({ goes: "video", start: "text", ...(k ? { seconds: k } : {}) }); } },
      S && S.sharp ? { art: "start-sharp", sign: "video", title: "Sharp Spec", one: "Sam's locked recipe: 1376 × 768, 8 passes", run: () => { if (typeof sharpCreate === "function") sharpCreate(); go("Create"); } } : null].filter(Boolean) }),
  previs: () => ({ line: "Motion taken from a clip, and sets your own agent builds in Blender or Blockout.", marks: [mark("claude"), mark("codex")],
    primary: ["Ask your agent", () => go("Agent")], cards: [
      { art: "act-previs-motion", sign: "previs", title: "Motion Previs", one: "Pose, depth and camera from a clip", run: () => go("Motion Previs") },
      { art: "start-agent", sign: "previs", title: "Ask your agent", one: "Claude or Codex drive Blender or Blockout", run: () => go("Agent") }] }),
  sound: () => ({ line: "Voices and score, made on this machine.", marks: [mark("fish"), mark("music3")],
    primary: ["Record a voice line", () => go("Voice")], cards: [
      { art: "start-voice", sign: "sound", title: "A voice line", one: "Fish Audio Drama 3: cast a voice, or clone one", run: () => go("Voice") },
      { art: "start-song", sign: "sound", title: "A song", one: "MiniMax-Music3: lyrics and a style into a track", run: () => go("Music") }] }),
  edit: () => ({ line: "Put the takes in order and send the cut to Resolve, with its media.", marks: [],
    primary: ["Open the timeline", () => go("Editor")], cards: [
      { art: "act-edit-timeline", sign: "edit", title: "Timeline", one: "Clips in order, trimmed, with sound", run: () => go("Editor") },
      { art: "act-edit-resolve", sign: "suite", title: "To Resolve", one: "A ZIP with media, FCPXML or an EDL", run: () => go("Import") },
      { art: "act-edit-dailies", sign: "suite", title: "Dailies", one: "Picked takes for Circle Take", run: () => go("Import") },
      { art: "act-edit-library", sign: "library", title: "Library", one: "Every take on this machine", run: () => go("Library") }] }),
};
function engineLanding(id) { const r = ROOMS.find(x => x.id === id), e = ENGINE_PAGES[id]();
  return el("div", {},
    el("div", { class: "enghead" }, artSlot("engine-" + id, "", id),
      el("div", { class: "stack" }, el("span", { class: "eyebrow" }, "Creative Engine"), el("h1", { class: "display" }, r.name), el("p", { class: "sub", style: "margin:0" }, e.line),
        e.marks.filter(Boolean).length ? el("div", { class: "marks" }, e.marks) : null,
        el("div", { class: "acts" }, el("button", { class: "primary", onclick: e.primary[1] }, e.primary[0])))),
    el("div", { class: "cards" + (e.cards.length >= 4 ? " c4" : e.cards.length === 3 ? " c3" : "") }, e.cards.map(pcard))); }
const viewStory = () => engineLanding("story"), viewImage = () => engineLanding("image"), viewVideo = () => engineLanding("video"),
  viewPrevis = () => engineLanding("previs"), viewSound = () => engineLanding("sound"), viewEditHub = () => engineLanding("edit");
