"use strict";
/* Edit → Graphics (app/graphics.py): motion graphics made from the Library's clips, pictures, voice lines and songs,
   rendered to MP4 on this machine with HyperFrames. Seven templates: a title, a lower third, kinetic type, captions, a
   slideshow, a music video and an explainer. Each is one job of the queue (kind "graphics"); what it makes lands in
   renders/ and the Library. Two Library buttons open it on a clip: "Add a title" and "Caption this clip". */
const GFX = { state: null, lib: null, template: "title", size: "1080p", busy: false, job: null, beatsBusy: false,
  title: { title: "", subtitle: "", seconds: 6, video: "" },
  "lower-third": { video: "", name: "", role: "", at: 1, seconds: 5, side: "left" },
  kinetic: { text: "", song: "", bpm: 100, every: 1, beats: null },
  captions: { video: "", per_line: 5, position: "bottom" },
  slideshow: { pictures: [], seconds_each: 4, voice: "", song: "", title: "" },
  "music-video": { clips: [], song: "", bpm: 120, every: 2, seconds: 30, transition: "mixed", beats: null, title: "" },
  explainer: { title: "", voice: "", song: "", sections: [{ heading: "", text: "", picture: "" }] } };
const GFX_CLIP = ["lower-third", "captions"];  // made over a clip: the size can match it
async function gfxLoad() { try { GFX.state = await api("/api/graphics"); } catch (e) { GFX.state = { error: e.message }; }
  try { GFX.lib = await api("/api/library"); } catch { GFX.lib = []; }
  if (tab === "Graphics") render(); }
const gfxOf = kind => (GFX.lib || []).filter(m => kind === "voice" || kind === "music" ? m.kind === "audio" && m.sound === kind : m.kind === kind);
const gfxName = p => (p || "").split("/").slice(-2).join("/");
// open the panel on a clip from the Library: "Add a title" (a lower third over it) or "Caption this clip"
function gfxOpen(template, path) { GFX.template = template; if (GFX[template] && "video" in GFX[template]) GFX[template].video = path;
  if (GFX_CLIP.includes(template)) GFX.size = "match"; GFX.job = null; go("Graphics"); }
function gfxButtons(m) { if (m.kind !== "video") return [];
  return [el("button", { class: "small", title: "A name and a role over this clip, or a title", onclick: () => gfxOpen("lower-third", m.path) }, "Add a title"),
    el("button", { class: "small", title: "Burn this clip's speech in as captions, word by word", onclick: () => gfxOpen("captions", m.path) }, "Caption this clip")]; }
// one Library file from a list: a dropdown of its kind (as Sound effects picks its clip)
function gfxPick(obj, key, kind, label, empty) { const list = gfxOf(kind);
  return field(label, el("select", { "aria-label": label, onchange: e => { obj[key] = e.target.value; render(); } },
    el("option", { value: "" }, list.length ? (empty || "Choose from the Library…") : "Nothing of this kind in the Library yet"),
    list.slice(0, 300).map(m => el("option", { value: m.path, selected: obj[key] === m.path }, (m.title ? m.title.slice(0, 60) + " · " : "") + gfxName(m.path))))); }
// several Library files in order: tiles to tick
function gfxMany(obj, key, kind, label) { const list = gfxOf(kind), chosen = obj[key];
  return el("div", {}, el("label", {}, label + (chosen.length ? " · " + chosen.length + " chosen, in the order ticked" : "")),
    !list.length ? el("div", { class: "note" }, "Nothing of this kind in the Library yet.")
      : el("div", { class: "grid" }, list.slice(0, 60).map(m => { const at = chosen.indexOf(m.path);
        return el("div", { class: "tile", "data-path": m.path }, media(m.path, kind === "video" ? { controls: false, muted: true } : {}),
          el("div", { class: "cap" }, el("input", { type: "checkbox", checked: at >= 0, style: "width:auto;flex:none", "aria-label": "Use " + m.path,
            onchange: e => { const i = chosen.indexOf(m.path); if (e.target.checked && i < 0) chosen.push(m.path); if (!e.target.checked && i >= 0) chosen.splice(i, 1); render(); } }),
            el("span", { class: "note m-flat", style: "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, at >= 0 ? (at + 1) + ". " + gfxName(m.path) : gfxName(m.path)))); }))); }
const gfxNum = (obj, key, attrs) => bind(obj, key, el("input", { type: "number", style: "max-width:120px", ...attrs }));
const gfxText = (obj, key, placeholder, rows) => bind(obj, key, rows ? el("textarea", { rows, placeholder, "aria-label": placeholder }) : el("input", { placeholder, "aria-label": placeholder }));
// a song's beats from the Music tab's beat finder when this Studio has one; otherwise the tempo makes an even grid
async function gfxBeats(f) { if (!f.song) return toast("Choose the song first");
  GFX.beatsBusy = true; render();
  try { const r = await api("/api/music/beats", { path: f.song }); const beats = (r.beats || r.times || []).map(Number).filter(n => n >= 0);
    if (beats.length < 2) throw new Error("no beats found"); f.beats = beats; if (r.bpm) f.bpm = Math.round(r.bpm); toast(beats.length + " beats found"); }
  catch (e) { f.beats = null; toast("The beats could not be found here (" + e.message + "): the tempo below sets them", 6000); }
  GFX.beatsBusy = false; render(); }
function gfxTempo(f) { return el("div", { class: "row" }, field("Tempo (beats a minute)", gfxNum(f, "bpm", { min: 40, max: 240 })),
  field("Beats per " + (GFX.template === "kinetic" ? "word" : "cut"), select(f, "every", { 1: "1", 2: "2", 4: "4", 8: "8" })),
  el("div", {}, el("label", {}, "The song's own beats"), el("div", { class: "row" }, el("button", { type: "button", class: "small", disabled: GFX.beatsBusy || !f.song, onclick: () => gfxBeats(f) }, GFX.beatsBusy ? "Listening…" : "Find the beats"),
    f.beats ? el("span", { class: "note m-flat" }, f.beats.length + " beats · ", el("button", { type: "button", class: "small ghost", onclick: () => { f.beats = null; render(); } }, "use the tempo")) : el("span", { class: "note m-flat" }, "or the tempo sets an even grid")))); }
function gfxForm(t) { const f = GFX[t];
  if (t === "title") return [el("div", { class: "row" }, field("Title", gfxText(f, "title", "The Long Night")), field("Subtitle (optional)", gfxText(f, "subtitle", "A film in three parts"))),
    el("div", { class: "row" }, field("Seconds on screen", gfxNum(f, "seconds", { min: 2, max: 30, step: 0.5 })), gfxPick(f, "video", "video", "Over a clip (optional)", "Over black"))];
  if (t === "lower-third") return [gfxPick(f, "video", "video", "The clip"), el("div", { class: "row" }, field("Name", gfxText(f, "name", "Ada Example")), field("Role (optional)", gfxText(f, "role", "Director of photography"))),
    el("div", { class: "row" }, field("Comes in at (seconds)", gfxNum(f, "at", { min: 0, step: 0.5 })), field("Stays for (seconds)", gfxNum(f, "seconds", { min: 1.5, max: 60, step: 0.5 })), field("Side", select(f, "side", { left: "Left", right: "Right" })))];
  if (t === "kinetic") return [field("The words, one at a time", gfxText(f, "text", "Every light in the city went out at once", 2)), gfxPick(f, "song", "music", "A song under it (optional)", "No song"), gfxTempo(f)];
  if (t === "captions") return [gfxPick(f, "video", "video", "The clip"), el("div", { class: "note" }, "Its speech is transcribed on this machine the first time (faster-whisper or whisper), and the words are kept beside the clip for next time."),
    el("div", { class: "row" }, field("Words on a line", gfxNum(f, "per_line", { min: 1, max: 12 })), field("Where", select(f, "position", { bottom: "Bottom", top: "Top" })))];
  if (t === "slideshow") return [gfxMany(f, "pictures", "image", "Pictures"), el("div", { class: "row" }, field("Seconds per picture", gfxNum(f, "seconds_each", { min: 1.5, max: 20, step: 0.5 })), field("Title (optional)", gfxText(f, "title", "Summer, 1987"))),
    el("div", { class: "row" }, gfxPick(f, "voice", "voice", "A voice line (optional: its length sets the slideshow's)", "No voice"), gfxPick(f, "song", "music", "A song (optional)", "No song"))];
  if (t === "music-video") return [gfxPick(f, "song", "music", "The song"), gfxMany(f, "clips", "video", "Clips, cut in turn"), gfxTempo(f),
    el("div", { class: "row" }, field("Longest (seconds)", gfxNum(f, "seconds", { min: 2, max: 600 })), field("Cuts", select(f, "transition", { mixed: "Flash, whip and straight cuts", flash: "Flash", whip: "Whip", cut: "Straight cuts" })), field("Title (optional)", gfxText(f, "title", "Night Drive")))];
  return [el("div", { class: "row" }, field("Title (optional)", gfxText(f, "title", "How the shot was made")), gfxPick(f, "voice", "voice", "The voice-over (its length sets the explainer's)", "No voice-over")),
    ...f.sections.map((s, i) => el("div", { class: "seg" }, el("div", { class: "seghead" }, el("b", {}, "Section " + (i + 1)), el("span", { style: "flex:1" }),
      f.sections.length > 1 ? el("button", { type: "button", class: "small", onclick: () => { f.sections.splice(i, 1); render(); } }, "Remove") : null),
      field("Heading", gfxText(s, "heading", "The idea")), field("Words", gfxText(s, "text", "What the voice says about it, in short", 2)), gfxPick(s, "picture", "image", "A picture (optional)", "No picture"))),
    f.sections.length < 12 ? el("button", { type: "button", class: "small fit", onclick: () => { f.sections.push({ heading: "", text: "", picture: "" }); render(); } }, "+ Add a section") : null,
    gfxPick(f, "song", "music", "A song under it (optional)", "No song")]; }
function gfxParams() { const t = GFX.template, f = GFX[t], p = { template: t, size: GFX.size };
  const need = (ok, say) => { if (!ok) throw new Error(say); };
  if (t === "title") { need(f.title.trim(), "Write the title first"); Object.assign(p, { title: f.title, subtitle: f.subtitle, seconds: Number(f.seconds) || 6 }); if (f.video) p.video = f.video; }
  else if (t === "lower-third") { need(f.video, "Choose the clip"); need(f.name.trim(), "Write the name"); Object.assign(p, { video: f.video, name: f.name, role: f.role, at: Number(f.at) || 0, seconds: Number(f.seconds) || 5, side: f.side }); }
  else if (t === "kinetic") { need(f.text.trim(), "Write the words first"); Object.assign(p, { text: f.text, bpm: Number(f.bpm) || 100, every: Number(f.every) || 1 }); if (f.song) p.song = f.song; if (f.beats) p.beats = f.beats; }
  else if (t === "captions") { need(f.video, "Choose the clip to caption"); Object.assign(p, { video: f.video, per_line: Number(f.per_line) || 5, position: f.position }); }
  else if (t === "slideshow") { need(f.pictures.length, "Tick the pictures first"); Object.assign(p, { pictures: f.pictures, seconds_each: Number(f.seconds_each) || 4, title: f.title }); if (f.voice) p.voice = f.voice; if (f.song) p.song = f.song; }
  else if (t === "music-video") { need(f.song, "Choose the song"); need(f.clips.length, "Tick the clips to cut"); Object.assign(p, { song: f.song, clips: f.clips, bpm: Number(f.bpm) || 120, every: Number(f.every) || 2, seconds: Number(f.seconds) || 30, transition: f.transition, title: f.title }); if (f.beats) p.beats = f.beats; }
  else { const sections = f.sections.filter(s => s.heading.trim() || s.text.trim()); need(sections.length, "Write at least one section");
    need(sections.every(s => s.heading.trim()), "Give every section a heading");
    Object.assign(p, { title: f.title, sections: sections.map(s => ({ heading: s.heading, text: s.text, ...(s.picture ? { picture: s.picture } : {}) })) }); if (f.voice) p.voice = f.voice; if (f.song) p.song = f.song; }
  if (p.size === "match" && !GFX_CLIP.includes(t) && !(t === "title" && p.video)) p.size = "1080p";
  return p; }
async function gfxMake() { let params; try { params = gfxParams(); } catch (e) { return toast(e.message); }
  GFX.busy = true; render();
  try { const job = await api("/api/jobs", { kind: "graphics", params }); GFX.job = job.id; toast("Queued: " + job.title); if (typeof poll === "function") await poll(); }
  catch (e) { toast(e.message, 7000); }
  GFX.busy = false; render(); }
function gfxResult(job) { if (job.status !== "done" || !(job.outputs || [])[0]) return null; const out = job.outputs[0];
  return el("div", { class: "tile", style: "margin-top:8px" }, el("video", { class: "main", src: file(out), controls: true, preload: "metadata" }),
    el("div", { class: "cap", style: "justify-content:flex-end;flex-wrap:wrap" }, el("span", { class: "note m-flat", style: "flex:1" }, gfxName(out)),
      el("button", { class: "small", onclick: () => addToTimeline(out) }, "+ timeline"), el("button", { class: "small", onclick: () => go("Library") }, "In the Library"))); }
function graphicsPanel() {
  if (!GFX.state) { gfxLoad(); return el("div", { class: "note" }, "Looking…"); }
  if (GFX.state.error) return el("div", { class: "note" }, GFX.state.error);
  const r = GFX.state.ready || {}, t = GFX.template, redraw = () => render();
  const cards = Object.fromEntries((GFX.state.templates || []).map(x => [x.id, [x.title, x.one]]));
  const sizes = { ...(GFX_CLIP.includes(t) || (t === "title" && GFX.title.video) ? { match: ["Match the clip", "its own size"] } : {}),
    "1080p": ["1080p", "1920 × 1080"], "720p": ["720p", "1280 × 720, quicker"], vertical: ["Vertical", "1080 × 1920"], square: ["Square", "1080 × 1080"] };
  if (!sizes[GFX.size]) GFX.size = "1080p";
  const mine = (typeof jobs !== "undefined" ? jobs : []).filter(j => j.kind === "graphics");
  const current = mine.find(j => j.id === GFX.job);
  return el("div", { class: "stack gfx" },
    el("section", { class: "panel stack" },
      !r.ok ? el("div", { class: "note vt-bad" }, r.why || "Motion graphics are not installed on this machine.") : null,
      !r.ok ? (() => { const cmd = GFX.state.spark ? "bash oneshot-setup-spark.sh --graphics" : "bash scripts/setup-graphics.sh";
        return el("div", { class: "signin-cmd" }, el("code", {}, cmd), el("button", { type: "button", class: "small", onclick: () => copyText(cmd, "Command") }, "Copy")); })() : null,
      field("What to make", choice(GFX, "template", cards, () => { GFX.job = null;  // over a clip: the clip's size first
        GFX.size = GFX_CLIP.includes(GFX.template) ? "match" : GFX.size === "match" ? "1080p" : GFX.size; redraw(); })),
      ...gfxForm(t),
      field("Size", choice(GFX, "size", sizes, redraw, true)),
      el("div", { class: "row" }, el("button", { class: "primary fit", disabled: GFX.busy || !r.ok, onclick: gfxMake }, GFX.busy ? "Queuing…" : "Make it"),
        el("span", { class: "note m-flat" }, "Rendered on this machine with HyperFrames at 24 frames a second" + (r.hyperframes ? " (" + r.hyperframes + ")" : "") + ".")),
      current ? el("div", {}, jobCard(current), gfxResult(current)) : null),
    mine.filter(j => j.id !== GFX.job && j.status === "done").length ? el("div", { class: "sectionhead" }, el("h2", {}, "Made here")) : null,
    el("div", { class: "grid" }, mine.filter(j => j.id !== GFX.job && j.status === "done").slice(0, 12).map(j => el("div", { class: "tile" }, media(j.outputs[0]),
      el("div", { class: "cap" }, el("span", { class: "note m-flat", style: "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, j.title),
        el("button", { class: "small", onclick: () => addToTimeline(j.outputs[0]) }, "+ timeline"))))));
}
function viewGraphics() {
  return el("div", {}, pageHead("Edit", "Motion graphics", "Titles, lower thirds, captions, slideshows and music videos, made from your takes and rendered on this machine."),
    graphicsPanel()); }
