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
function pcard({ art, sign, title, one, run, k, off, src, data }) {  // data: the card's data-* attributes
  const pic = src ? el("div", { class: "art" }, el("img", { src, alt: "", loading: "lazy" })) : artSlot(art, "", sign);
  return el("button", { class: "pcard", type: "button", onclick: run, disabled: !!off, title: off || one || "", ...(data || {}) }, pic, k ? el("span", { class: "k" }, k) : null,
    el("div", { class: "txt" }, el("div", {}, el("b", {}, title), one ? el("span", { class: "one" }, off || one) : null), icon("go", "go"))); }
// a vault page's head with its cover, like an engine's; without a ready cover it is the plain head
function vaultHead(id, title, line, right) {
  if (!ART.ready.has("vault-" + id)) return pageHead("Studio Vault", title, line, right);
  return el("div", { class: "enghead vault" }, artSlot("vault-" + id, "", id),
    el("div", { class: "stack" }, el("span", { class: "eyebrow" }, "Studio Vault"), el("h1", { class: "display" }, title), line ? el("p", { class: "sub" }, line) : null, right || null)); }
function pageHead(eyebrow, title, line, right) {
  return el("div", { class: "pagehead" }, el("div", { class: "stack" }, el("span", { class: "eyebrow" }, eyebrow), el("h1", { class: "display" }, title), line ? el("p", { class: "sub" }, line) : null), right || null); }

const slateMode = (mode, view) => { SL.mode = mode; try { localStorage.setItem("production-mode", mode); } catch {} if (view && SL.p) SL.view = view; go("Production"); };
const longFirst = () => S && S.long ? Object.keys(S.long)[0] : null;
const VIDEO_FACE = { open: false, clips: null, clip: "" };
async function videoFaceOpen(path = "") {
  VIDEO_FACE.open = true; VIDEO_FACE.clip = path; ACT.open = path ? { kind: "facefix", path } : null;
  go("Video");
  setTimeout(() => { const panel = document.querySelector("#video-facefix");
    if (panel && panel.scrollIntoView) panel.scrollIntoView({ behavior: "smooth", block: "start" }); }, 60);
  try { VIDEO_FACE.clips = (await api("/api/library")).filter(m => m.kind === "video");
    if (!VIDEO_FACE.clip && VIDEO_FACE.clips.length) VIDEO_FACE.clip = VIDEO_FACE.clips[0].path; }
  catch (e) { VIDEO_FACE.clips = []; toast(e.message); }
  if (tab === "Video" && VIDEO_FACE.open) render();
}
function videoFacePanel() {
  if (!VIDEO_FACE.open) return null;
  const clips = VIDEO_FACE.clips;
  const upload = el("input", { type: "file", accept: ".mp4,.mov", onchange: async e => {
    const picked = e.target.files && e.target.files[0]; if (!picked) return;
    const data = new FormData(); data.append("file", picked);
    try { const response = await fetch("/api/upload", { method: "POST", body: data }); const result = await response.json();
      if (!response.ok || !result.path) throw new Error(result.detail || result.error || "Upload failed");
      VIDEO_FACE.clip = result.path; ACT.open = { kind: "facefix", path: result.path }; render(); }
    catch (error) { toast(error.message); }
  } });
  return el("section", { class: "card", id: "video-facefix", style: "margin-top:16px" },
    el("div", { class: "row", style: "align-items:center" }, el("h2", { style: "margin:0" }, "Fix faces in a clip"), el("span", { style: "flex:1" }),
      el("button", { class: "small", onclick: () => { VIDEO_FACE.open = false; ACT.open = null; render(); } }, "Close")),
    ACT.open && ACT.open.kind === "facefix" && ACT.open.path === VIDEO_FACE.clip ? actPanel() :
      el("div", {}, el("div", { class: "note" }, "Choose a finished clip and a clear picture of the face. PixelAI Labs' fade keeps close-ups as they were."),
        clips === null ? el("div", { class: "note" }, "Loading clips…") :
          clips.length ? el("div", { class: "row", style: "align-items:flex-end;margin-top:8px" },
            field("Clip from the Library", el("select", { value: VIDEO_FACE.clip, onchange: e => { VIDEO_FACE.clip = e.target.value; } },
              clips.slice(0, 100).map(m => el("option", { value: m.path }, m.path.split("/").pop())))),
            el("button", { class: "fit", onclick: () => { const selected = document.querySelector("#video-facefix select");
              if (!selected) return; VIDEO_FACE.clip = selected.value; ACT.open = { kind: "facefix", path: VIDEO_FACE.clip }; render(); } }, "Use this clip")) :
            el("div", { class: "note" }, "No clips in the Library yet."),
        field("Or upload a clip", upload)));
}
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
      { art: "act-video-facefix", sign: "video", title: "Fix faces", one: "Repair distant faces in a finished clip and keep close-ups",
        words: "PixelAI Labs H3 Face Size Fade facefix ComfyUI-H3-FaceFix-PixelAILabs", run: () => videoFaceOpen() },
      { art: "start-long", sign: "video", title: "A long take", one: "Chained clips, 30 seconds and up", run: () => { const k = longFirst(); startCreate({ goes: "video", start: "text", ...(k ? { seconds: k } : {}) }); } },
      S && S.sharp ? { art: "start-sharp", sign: "video", title: "Sharp Spec", one: "Sam's locked recipe: 1376 × 768, 8 passes", run: () => { if (typeof sharpCreate === "function") sharpCreate(); go("Create"); } } : null].filter(Boolean) }),
  previs: () => ({ line: "Motion taken from a clip, and sets your own agent builds in Blender or Blockout.", marks: [mark("claude"), mark("codex")],
    primary: ["Ask your agent", () => go("Agent")], cards: [
      { art: "act-previs-motion", sign: "previs", title: "Motion Previs", one: "Pose, depth and camera from a clip", run: () => go("Motion Previs") },
      { art: "start-agent", sign: "previs", title: "Ask your agent", one: "Claude or Codex drive Blender or Blockout", run: () => go("Agent") }] }),
  sound: () => ({ line: "Voices, score and sound effects, made on this machine.", marks: [mark("fish"), mark("music3")],
    primary: ["Record a voice line", () => go("Voice")], cards: [
      { art: "start-voice", sign: "sound", title: "A voice line", one: "Fish Audio Drama 3: cast a voice, or clone one", run: () => go("Voice") },
      { art: "start-song", sign: "sound", title: "A song", one: "YuE2 or MiniMax-Music3: lyrics and a style into a track", run: () => go("Music") },
      { art: "start-sfx", sign: "sound", title: "Sound effects", one: "Foley for a clip, or a sound from words", run: () => go("Foley") }] }),
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
      el("div", { class: "stack" }, el("span", { class: "eyebrow engmark" }, el("img", { src: "brand/module-" + id + ".svg", alt: "", width: 22, height: 22 }), "Creative Engine"), el("h1", { class: "display" }, r.name), el("p", { class: "sub", style: "margin:0" }, e.line),
        e.marks.filter(Boolean).length ? el("div", { class: "marks" }, e.marks) : null,
        el("div", { class: "acts" }, el("button", { class: "primary", onclick: e.primary[1] }, e.primary[0])))),
    el("div", { class: "cards" + (e.cards.length >= 4 ? " c4" : e.cards.length === 3 ? " c3" : "") }, e.cards.map(pcard)),
    id === "video" ? videoFacePanel() : null); }
const viewStory = () => engineLanding("story"), viewImage = () => engineLanding("image"), viewVideo = () => engineLanding("video"),
  viewPrevis = () => engineLanding("previs"), viewSound = () => engineLanding("sound"), viewEditHub = () => engineLanding("edit");
