// Create a RefMod (app/refmod.py, spark/spark_refmod.py): pictures and clips of one subject saved as a MiniMax H3
// reference in refmods/. Opened from "RefMod" under a picture or a clip, or "RefMod from selected" over the Library.
const REFMOD = { state: null, f: { files: [], starts: {}, name: "", concept: "identity", mode: "full", seconds: 5, description: "", replace: false } };
const RFM_CONCEPTS = { identity: ["A person or character", "a face, hair and outfit"], clothing: ["Clothing", "an outfit or costume"],
  pose_motion: ["A move", "a pose, dance, fight move or camera move"], style: ["A look", "the style of the pictures"],
  background: ["A place", "a set or background"], generic: ["Anything else", ""] };
const RFM_MODES = { full: ["Full Reference", "keeps faces and outfits; a larger file"], compressed: ["Compressed Reference", "a few tokens: a concept or a move, not a face"] };
async function rfmLoad() { try { REFMOD.state = await api("/api/refmod"); } catch { REFMOD.state = { ready: false, why: "", saved: [] }; } }
const rfmIsClip = p => /\.(mp4|mov)$/i.test(p);
// open the panel on one file, or on the files ticked in the Library
function rfmOpen(paths) { const f = REFMOD.f, list = paths.filter(p => !/\.(wav|mp3|m4a|aac|flac|ogg)$/i.test(p));
  if (!list.length) return toast("Tick pictures or clips of the subject first");
  f.files = list.slice(0, 24); f.starts = {}; f.replace = false;
  if (!f.name) f.name = list[0].split("/").pop().replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9 _.-]/g, "").slice(0, 60) || "my subject";
  f.concept = list.every(rfmIsClip) ? "pose_motion" : "identity";
  rfmLoad().then(() => { if (tab === "Library") render(); });
  ACT.open = { kind: "refmod", path: list[0] }; tab = "Library"; history.replaceState(null, "", "#Library"); render();
  requestAnimationFrame(() => document.getElementById("actpanel")?.scrollIntoView({ behavior: "smooth", block: "start" })); }
function rfmButtons(m) { if (m.kind === "audio") return [];
  return [el("button", { class: "small", title: "Save this " + (m.kind === "video" ? "clip" : "picture") + " as a MiniMax H3 reference (a RefMod) that clips can load again; tick more files and use “RefMod from selected” for several", onclick: () => rfmOpen([m.path]) }, "RefMod")]; }
function rfmPanel(o) { const f = REFMOD.f, st = REFMOD.state || {}, redraw = () => render();
  if (!REFMOD.state) rfmLoad().then(redraw);
  const box = el("div", { class: "seg", id: "actpanel", style: "margin-top:12px" },
    el("div", { class: "seghead" }, el("b", {}, "Create a RefMod"), el("span", { class: "note", style: "margin:0" }, f.files.length + (f.files.length === 1 ? " file" : " files")), el("span", { style: "flex:1" }),
      el("button", { class: "small", onclick: () => { ACT.open = null; render(); } }, "Close")));
  if (REFMOD.state && !st.ready) { put(box, el("div", { class: "note", style: "color:var(--warn)" }, st.why || "Not available on this machine.")); return box; }
  const clips = f.files.filter(rfmIsClip), pics = f.files.filter(p => !rfmIsClip(p));
  const most = { image: st.most_images || 16, video: st.most_videos || 8 };
  const over = pics.length > most.image || clips.length > most.video;
  const chosen = el("div", { class: "grid", style: "margin-top:8px;grid-template-columns:repeat(auto-fill,minmax(150px,1fr))" }, f.files.map(p => el("div", { class: "tile" },
    media(p, { style: "aspect-ratio:1;object-fit:contain;background:#000", ...(rfmIsClip(p) ? { muted: true } : {}) }),
    el("div", { class: "cap" }, el("span", { style: "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap", title: p }, p.split("/").pop()),
      el("button", { class: "small", title: "Leave this file out", "aria-label": "Leave " + p + " out", onclick: () => { f.files = f.files.filter(x => x !== p); if (!f.files.length) ACT.open = null; redraw(); } }, "✕")),
    rfmIsClip(p) ? el("div", { class: "cap" }, el("label", { style: "margin:0;font-size:12px" }, "from (s)"),
      el("input", { type: "number", min: 0, step: 0.1, value: f.starts[p] || 0, style: "width:80px", "aria-label": "Where the part of " + p + " starts, in seconds",
        oninput: e => { f.starts[p] = Math.max(0, Number(e.target.value) || 0); } })) : null)));
  const name = el("input", { value: f.name, maxlength: 80, placeholder: "leather jacket woman", oninput: e => { f.name = e.target.value; } });
  const saved = (st.saved || []);
  const taken = () => saved.some(s => s.name === f.name.trim().replace(/\s+/g, " "));
  put(box,
    el("div", { class: "note" }, "A RefMod saves pictures and clips of one subject as a MiniMax H3 reference that a clip can load again, without the pictures. Only the H3 video decoder runs: it takes under a minute. Several pictures of the same subject (a character sheet, a face, the outfit) make a stronger one."),
    chosen,
    el("div", { class: "row", style: "margin-top:10px;align-items:flex-end" },
      el("div", { style: "flex:1;min-width:220px" }, el("label", {}, "Name"), name),
      clips.length ? el("div", { class: "fit", style: "width:150px" }, el("label", {}, "Each clip's part (s)"), bind(f, "seconds", el("input", { type: "number", min: st.seconds?.[0] || 1, max: st.seconds?.[1] || 30, step: 0.5 }))) : null),
    el("label", {}, "What it is"), choice(f, "concept", RFM_CONCEPTS, redraw, true),
    el("label", {}, "How it is kept"), choice(f, "mode", RFM_MODES, redraw, true),
    f.mode === "compressed" && f.concept === "identity" ? el("div", { class: "note", style: "color:var(--warn)" }, "Compressed Reference pools the pictures to a small grid: a face does not survive it. Use Full Reference for a person.") : null,
    field("Describe it (optional, saved with it)", bind(f, "description", el("textarea", { rows: 2, maxlength: 400, placeholder: "young woman, black bob, brown leather jacket, black turtleneck" }))),
    over ? el("div", { class: "note", style: "color:var(--warn)" }, "At most " + most.image + " pictures and " + most.video + " clips in one RefMod.") : null,
    el("label", { style: "display:flex;gap:6px;align-items:center" }, el("input", { type: "checkbox", style: "width:auto", checked: f.replace, onchange: e => { f.replace = e.target.checked; } }), "Replace a saved RefMod of the same name"),
    el("div", { class: "row", style: "margin-top:10px" },
      el("button", { class: "hot fit", disabled: ACT.busy || over || !f.files.length, onclick: () => {
        if (!f.name.trim()) return toast("Name the RefMod first");
        if (taken() && !f.replace) return toast("A RefMod named " + f.name.trim() + " is already saved: choose another name or tick Replace");
        actQueue("refmod", { name: f.name.trim(), images: pics, videos: clips, starts: clips.map(p => f.starts[p] || 0), seconds: Number(f.seconds) || 5,
          mode: f.mode, concept: f.concept, description: f.description, replace: !!f.replace }, "refmod").then(() => rfmLoad().then(redraw)); } }, "Create RefMod"), el("span")),
    actShow("refmod", job => { if (!saved.some(s => s.path === job.outputs[0])) rfmLoad().then(redraw);
      return el("div", { class: "note" }, "Saved: ", el("b", {}, job.outputs[0] || ""), st.share ? " (and linked into " + st.share + ")" : ""); }),
    el("h3", { style: "margin-top:16px" }, "Saved RefMods" + (saved.length ? " · " + saved.length : "")),
    saved.length ? el("div", {}, saved.map(s => el("div", { class: "kv", style: "padding:6px 0;border-bottom:1px solid var(--line);gap:10px" },
      el("span", { style: "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap", title: s.description || s.path }, el("b", {}, s.name), s.description ? " · " + s.description : ""),
      el("span", { class: "note", style: "margin:0;white-space:nowrap" }, [s.concept, s.tokens ? s.tokens + " tokens" : "", sizeText(s.bytes), new Date(s.made * 1000).toLocaleDateString()].filter(Boolean).join(" · ")))))
      : el("div", { class: "note" }, "None yet."),
    el("div", { class: "note" }, "Saved in " + (st.folder || "refmods") + (st.share ? "; each one is also linked into " + st.share + ", so that ComfyUI's Load H3 RefMods lists it." : ".") + " " + (st.licence || "")));
  return box; }
