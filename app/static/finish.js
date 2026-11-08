"use strict";
/* Finish: the final cut and mix of a timeline, rendered by Tesseract (app/finish.py), with the same cut for DaVinci
   Resolve (FCPXML, EDL, the zip with media), Premiere Pro and After Effects (Tesseract Converter).
   finishPanel(project) is the panel for the Timeline; finishPanel({ clips }) finishes clips picked in the Library
   ("Finish these", finLibButton and finLibPanel). Tesseract by Mirage; Tesseract Converter (MIT). */
const FIN = { state: null, sounds: null, open: null, jobs: {}, converting: {},
  o: { mix: "dialogue", size: "1080p", fps: "24", title: "", subtitle: "", captions: false, voice: "", song: "" } };
async function finLoad(fresh) { try { FIN.state = await api("/api/finish" + (fresh ? "?fresh=true" : "")); } catch (e) { FIN.state = { ok: false, why: e.message }; }
  try { FIN.sounds = (await api("/api/library")).filter(m => m.kind === "audio"); } catch { FIN.sounds = []; } }
const FIN_MIXES = { dialogue: ["Dialogue first", "the music low, and lower still under speech"],
  balanced: ["Balanced", "music and dialogue side by side, the music dips under speech"],
  music: ["Music forward", "the music carries it, a light dip under speech"] };
const finKey = target => target && target.id ? "p:" + target.id : "lib";
// where the cut is finished, and what to run when it cannot be
function finReadyNote() { const s = FIN.state;
  if (!s) return el("div", { class: "note" }, "Looking for Tesseract…");
  if (!s.ok) return el("div", { class: "note", style: "color:var(--warn)" }, s.why || "Tesseract is not ready.", " ",
    el("button", { class: "small", onclick: async () => { await finLoad(true); render(); } }, "Look again"));
  const where = s.where && s.where.startsWith("ssh:") ? "Runs on " + (s.machine || s.where.slice(4)) + " over ssh: the clips are copied there and the finished cut comes back." : "Runs on this computer.";
  return el("div", { class: "note" }, "Tesseract " + (s.version || "") + ". " + where + (s.note ? " " + s.note : "")
    + (s.converter && s.converter.ok ? "" : " Premiere and After Effects need Tesseract Converter: " + (s.setup || "bash scripts/setup-finish.sh") + ".")
    + " Tesseract keeps usage counts by default (no file names, paths or contents); `tsrct telemetry disable` turns them off. The Studio never changes that setting.");
}
const finSegCtl = (key, options, label) => el("div", { class: "seg-ctl", role: "radiogroup", "aria-label": label },
  options.map(([v, t]) => el("button", { type: "button", role: "radio", "aria-checked": FIN.o[key] === v ? "true" : "false", class: FIN.o[key] === v ? "on" : "", onclick: () => { FIN.o[key] = v; render(); } }, t)));
function finParams(target) { const o = FIN.o;
  const params = { mix: o.mix, size: o.size, fps: Number(o.fps), captions: !!o.captions };
  if (target && target.id) params.project = target.id; else params.clips = (target.clips || []).map(p => typeof p === "string" ? { path: p } : p);
  if (o.title.trim()) params.title = { text: o.title.trim(), subtitle: o.subtitle.trim() };
  if (o.voice) params.voice = o.voice;
  if (o.song === "none") params.song = null; else if (o.song) params.song = o.song;
  return params; }
async function finQueue(target) { const name = target && target.id ? (target.name || "timeline") : (target.clips || []).length + " clips";
  try { const job = await api("/api/jobs", { kind: "finish", title: "Finish: " + name, params: finParams(target) }); FIN.jobs[finKey(target)] = job.id; toast("Queued: " + job.title); await poll(); render(); }
  catch (e) { toast(e.message, 7000); } }
async function finConvert(job, to) { const key = job.id + ":" + to; if (FIN.converting[key]) return; FIN.converting[key] = true; render();
  toast("Making the " + (to === "premiere" ? "Premiere Pro" : "After Effects") + " project…");
  try { const r = await api("/api/finish/" + job.id + "/convert", { to }); location.assign(file(r.path));
    if (r.notes && r.notes.length) toast("Made, with " + r.notes.length + " notes from the converter (in README.txt)", 6000); }
  catch (e) { toast(e.message, 8000); } finally { FIN.converting[key] = false; render(); } }
const finOut = (job, suffix) => (job.outputs || []).find(p => p.endsWith(suffix));
// what a finished cut offers: play it, take the MP4, and the same cut for each editor
function finMade(job, target) { const mp4 = finOut(job, ".mp4"), busy = to => !!FIN.converting[job.id + ":" + to];
  return el("div", { style: "margin-top:8px" }, mp4 ? el("video", { class: "main", src: file(mp4), controls: true }) : null,
    el("div", { class: "row", style: "margin-top:8px" },
      mp4 ? el("a", { class: "fit tag2 t", href: file(mp4), download: "" }, "Download MP4") : null,
      target && target.id ? el("a", { class: "fit tag2", href: typeof timelineZip === "function" ? timelineZip(target.id) : "/api/export/timeline/" + target.id + ".zip", title: "The timeline and its clips for DaVinci Resolve: unzip, then File › Import › Timeline" }, "Open in Resolve")
        : finOut(job, ".fcpxml") ? el("a", { class: "fit tag2", href: file(finOut(job, ".fcpxml")), download: "", title: "The cut as FCPXML for DaVinci Resolve, naming the clips where they are on this machine" }, "Open in Resolve") : null,
      el("button", { class: "small", disabled: busy("premiere"), title: "The cut as a Premiere Pro project with its media (Tesseract Converter)", onclick: () => finConvert(job, "premiere") }, busy("premiere") ? "Premiere…" : "Premiere"),
      el("button", { class: "small", disabled: busy("aftereffects"), title: "The cut as an After Effects project with its media and fonts (Tesseract Converter; experimental)", onclick: () => finConvert(job, "aftereffects") }, busy("aftereffects") ? "After Effects…" : "After Effects"),
      finOut(job, ".tsrct") ? el("a", { class: "fit tag2", href: file(finOut(job, ".tsrct")), download: "", title: "The editable Tesseract project, its media packed inside" }, ".tsrct project") : null,
      finOut(job, ".edl") ? el("a", { class: "fit tag2", href: file(finOut(job, ".edl")), download: "" }, "Cut list · EDL") : null, el("span"))); }
// The Finish panel: for a timeline (project) or for clips picked in the Library ({ clips: [paths] })
function finishPanel(target) { if (!FIN.state) { finLoad().then(() => render()); }
  const o = FIN.o, redraw = () => render(), isProject = !!(target && target.id);
  const clips = isProject ? (target.timeline || []) : (target && target.clips) || [];
  const sounds = FIN.sounds || [], voices = sounds.filter(m => m.sound === "voice"), songs = sounds.filter(m => m.sound === "music");
  const named = m => (m.title || m.path.split("/").pop()).slice(0, 60);
  const box = el("div", { class: "seg", id: "finpanel", style: "margin-top:12px" },
    el("div", { class: "seghead" }, el("b", {}, "Finish"), el("span", { class: "note", style: "margin:0" }, "the final cut and mix, to Resolve, Premiere or After Effects"), el("span", { style: "flex:1" }),
      !isProject ? el("button", { class: "small", onclick: () => { FIN.open = null; render(); } }, "Close") : null),
    el("div", { class: "note", style: "margin-top:0" }, "Renders " + (isProject ? "this timeline" : clips.length + " picked clips") + " as one finished film: each cut as it is set, the clips' own sound kept at full level, narration laid from the first shot, the music under it all and lowered wherever someone speaks. A title opens it as a scene of its own. You get the MP4, its editable Tesseract project, and the same cut for DaVinci Resolve, Premiere Pro and After Effects."),
    finReadyNote(),
    field("Mix", choice(o, "mix", FIN_MIXES, redraw, true)),
    el("div", { class: "row" }, field("Size", finSegCtl("size", [["720p", "720p"], ["1080p", "1080p"], ["4k", "4K"]], "Size")),
      field("Frames per second", finSegCtl("fps", [["24", "24"], ["25", "25"], ["30", "30"], ["60", "60"]], "Frames per second"))),
    el("div", { class: "row" }, field("Title (optional)", bind(o, "title", el("input", { placeholder: "The name of the film, on a card before the first shot" }))),
      field("Under the title", bind(o, "subtitle", el("input", { placeholder: "a short film by…" })))),
    el("div", { class: "row" }, field("Music", select(o, "song", { "": isProject && target.soundtrack ? "The timeline’s soundtrack" : "None", ...(isProject && target.soundtrack ? { none: "None" } : {}), ...Object.fromEntries(songs.map(m => [m.path, named(m)])) })),
      field("Narration", select(o, "voice", { "": "None", ...Object.fromEntries(voices.map(m => [m.path, named(m)])) })),
      el("label", { style: "display:flex;gap:6px;align-items:center;flex:none", title: "Captions of the words a speech-to-text model on this computer hears; left out when there is none" },
        bind(o, "captions", el("input", { type: "checkbox", style: "width:auto" })), "Captions")),
    el("div", { class: "row", style: "margin-top:8px" }, el("button", { class: "primary fit", disabled: !clips.length || !(FIN.state && FIN.state.ok), onclick: () => finQueue(target) }, "Finish the cut"),
      !clips.length ? el("span", { class: "note", style: "margin:0" }, isProject ? "Add clips to the timeline first." : "Pick clips in the Library first.") : el("span")));
  const job = jobs.find(j => j.id === FIN.jobs[finKey(target)]);
  if (job) put(box, el("div", { style: "margin-top:12px" }, jobCard(job), job.status === "done" ? finMade(job, target) : null));
  return box; }
// the Library: "Finish these" for the picked clips, and its panel above the grid
const finPicked = () => [...(typeof LIBPICK !== "undefined" ? LIBPICK : [])].filter(p => /\.(mp4|mov|m4v)$/i.test(p));
function finLibButton() {
  return el("button", { class: "fit", title: "Finish the ticked clips as one cut, in the order they were ticked, through Tesseract", onclick: () => { const picked = finPicked();
    if (!picked.length) return toast("Tick the clips to finish first");
    FIN.open = { clips: picked }; render();
    setTimeout(() => { const n = document.querySelector("#finpanel"); if (n && n.scrollIntoView) n.scrollIntoView({ behavior: "smooth", block: "start" }); }, 60); } }, "Finish these"); }
const finLibPanel = () => FIN.open ? finishPanel(FIN.open) : null;
