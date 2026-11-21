"use strict";
/* Library actions: Turnaround sheet (on a picture), Upscale and Remove a person (on a clip).
   Each is built on a LoRA from the hub that is not for making a clip but has a job of its own (app/actions.py).
   Credit: Turnaround LoRA and its contact-sheet nodes by matlod / MATLOWAI; Video Upscaler LoRA and the H3 Video
   Upsampler node by dntpi (Sandpies), on LBH-123-AI's latent upscaler; Person Remover LoRA and H3 Relay by Akatz Labs,
   with Meta's SAM 3.1 for the mask. Fix faces: Carasibana's H3 FaceRefine, PixelAI Labs' H3 Face Size Fade,
   drozbay's MaskVidExperiments and ylchen333's VOSR 2.0 nodes (CSWRY's VOSR 2.0), with SAM 3.1 for the mask. */
const ACT = { state: null, open: null, busy: false, jobs: {}, faceLib: null, facePicking: null, faceQuery: "",
  turn: { size: "1024", strength: "1", seed: 1, prompt: "" },
  up: { megapixels: "2", denoise: "0.1" },
  rm: { person: "", clean: "auto", clean_frame: null, window: "22", steps: 12, seed: 904234, silent: false, mask: null },
  face: { faces: [{ image: null, which: "largest" }], canvas: "512", steps: 8, denoise: "0.5", full_fix_below_px: 110,
    no_fix_above_px: 180, seed: 983546012546031, prompt: "", transformer: "", restore: "auto" } };
// Fix faces: which face each picture's pass follows (actions.FACE_PICKS); several people are taken left to right
const FACE_WHICH = { largest: "Largest face", smallest: "Smallest face", left: "Left-most", right: "Right-most", centre: "Nearest the centre" };
const FACE_ORDER = ["left", "right", "centre", "largest"];
const ACT_KEY = { turnaround: "turn", upscale: "up", remove: "rm", facefix: "face" };
async function actLoad() { try { ACT.state = await api("/api/actions"); } catch { ACT.state = {}; } }
const actOf = kind => (ACT.state || {})[kind] || { ready: false, why: "" };
function actOpen(kind, path) { ACT.open = { kind, path }; if (kind === "remove") ACT.rm.mask = null; if (kind === "facefix") { ACT.faceLib = null; ACT.facePicking = null; } tab = "Library"; history.replaceState(null, "", "#Library"); render();
  setTimeout(() => { const n = document.querySelector("#actpanel"); if (n) n.scrollIntoView({ behavior: "smooth", block: "start" }); }, 60); }
// the buttons a Library tile gets for its kind of file
function actButtons(m) { const gfx = typeof gfxButtons === "function" ? gfxButtons(m) : [];  // Add a title, Caption this clip (graphics.js)
  if (!ACT.state) return gfx;
  const one = (kind, label, title) => el("button", { class: "small", title: actOf(kind).ready ? title : actOf(kind).why, onclick: () => actOpen(kind, m.path) }, label);
  return (m.kind === "video" ? [one("upscale", "Upscale", "Make this clip larger and sharper: a latent upscale, then one light re-draw guided by the clip itself"),
                               one("remove", "Remove a person", "Take one person out of this clip and rebuild the background"),
                               one("facefix", "Fix faces", "Draw this clip's face again close up, from a picture of the face, and stitch it back")]
    : [one("turnaround", "Turnaround", "Five views of this picture's subject, from the front round to the side or back")])
    .concat(typeof revButtons === "function" ? revButtons(m) : [], typeof swapButtons === "function" ? swapButtons(m) : [], typeof rfmButtons === "function" ? rfmButtons(m) : [], gfx); }  // Restyle, Extend, Fix (revise.js), then Add a title, Caption this clip (graphics.js)
// a LoRA's card in the hub, when the LoRA belongs to an action: where it is used, in place of "Use on this clip"
function actHubNote(r) { const kind = r.action, a = actOf(kind);
  return el("div", { class: "note" }, "Used by the Library’s “" + (a.title || kind) + "” action: " + (kind === "turnaround" ? "press Turnaround under a picture." : "press " + (a.title || kind) + " under a clip.")
    + (ACT.state && !a.ready ? " " + a.why : "")); }
function actHubButton(r) { return el("button", { type: "button", class: "small", onclick: () => { HUB.browse = false; tab = "Library"; history.replaceState(null, "", "#Library"); render(); toast("Press “" + (r.action === "turnaround" ? "Turnaround” under a picture" : (actOf(r.action).title || r.action) + "” under a clip")); } }, "Open the Library"); }
async function actQueue(kind, params, slot) { if (ACT.busy) return; ACT.busy = true; render();   // the queue takes a moment to answer: a second click before it does must not make a second job
  try { const job = await api("/api/jobs", { kind, params }); ACT.jobs[slot || kind] = job.id; toast("Queued: " + job.title); poll(); }
  catch (e) { toast(e.message, 6000); }
  ACT.busy = false; render(); }
const actJob = slot => jobs.find(j => j.id === ACT.jobs[slot]);
const actShow = (slot, made) => { const job = actJob(slot); return job ? el("div", { style: "margin-top:12px" }, jobCard(job), job.status === "done" ? made(job) : null) : null; };
const actUse = (path, how) => how === "first" ? () => { C.image = path; C.goes = "video"; C.start = "own"; C.step = 1; tab = "Create"; render(); }
  : () => { C.goes = "video"; C.start = "refs"; sync(); if (!isRef()) return toast("Reference pictures need MiniMax H3 Ref2VA on this machine"); if (!C.refs.includes(path)) { C.refs.push(path); C.subjects.push(""); } C.step = 1; tab = "Create"; render(); };
function actPanel() { const o = ACT.open; if (!o) return null; if (typeof REV_KINDS !== "undefined" && REV_KINDS.includes(o.kind)) return revPanel(o);  // revise.js
  if (o.kind === "swap" && typeof swapPanel === "function") return swapPanel(o);  // swap.js
  if (o.kind === "refmod" && typeof rfmPanel === "function") return rfmPanel(o);  // refmod.js
  const a = actOf(o.kind), f = ACT[ACT_KEY[o.kind]], redraw = () => render();
  const box = el("div", { class: "seg", id: "actpanel", style: "margin-top:12px" },
    el("div", { class: "seghead" }, el("b", {}, a.title || o.kind), el("span", { class: "note", style: "margin:0" }, o.path.split("/").pop()), el("span", { style: "flex:1" }),
      el("button", { class: "small", onclick: () => { ACT.open = null; render(); } }, "Close")));
  if (!a.ready) { put(box, el("div", { class: "note", style: "color:var(--warn)" }, a.why || "Not available on this machine.")); return box; }
  const by = o.kind === "facefix" ? el("div", { class: "note" }, "With H3 FaceRefine by Carasibana, H3 Face Size Fade by PixelAI Labs, MaskVidExperiments by drozbay and VOSR 2.0 (CSWRY, nodes by ylchen333), on MiniMax H3 Ref2VA.")
    : el("div", { class: "note" }, "With " + a.lora_name + " by " + a.author + ".");
  if (o.kind === "turnaround") {
    put(box, el("div", { class: "row", style: "align-items:flex-start" }, el("div", { style: "flex:0 0 220px" }, media(o.path, { style: "width:220px;border-radius:8px" })),
      el("div", {}, el("div", { class: "note", style: "margin-top:0" }, "Five views of the subject of this picture, turning from where it faces now, made together so they agree. Each is a picture of its own in the Library: use one as a first frame, or several as reference pictures of the same character."),
        field("Size of each view", choice(f, "size", { 512: ["512", "fastest, what the LoRA was trained at"], 1024: ["1024", "the sweet spot"], 2048: ["2048", "slow; the turn comes late"] }, redraw, true)),
        field("What matters more", choice(f, "strength", { 1: ["The turn", "strength 1.0"], 0.7: ["The look", "strength 0.7, turns less"] }, redraw, true)),
        el("div", { class: "row" }, field("Seed", bind(f, "seed", el("input", { type: "number", min: 0, style: "max-width:110px" }))),
          field("Instruction (optional)", bind(f, "prompt", el("input", { placeholder: "the camera orbits horizontally around the subject of <Picture 1> at eye level, ninety degrees clockwise, the subject stays upright on the ground" })))),
        by, el("button", { class: "hot", disabled: ACT.busy, onclick: () => actQueue("turnaround", { image: o.path, size: Number(f.size), strength: Number(f.strength), seed: Number(f.seed) || 0, prompt: f.prompt }) }, "Make the turnaround sheet"))),
      actShow("turnaround", job => el("div", {}, el("img", { src: file(job.outputs[0]), style: "width:100%;border-radius:8px;margin-top:8px", alt: "the five views side by side" }),
        el("div", { class: "grid", style: "margin-top:8px" }, job.outputs.slice(1).map((p, i) => el("div", { class: "tile" }, media(p, { style: "aspect-ratio:1" }), el("div", { class: "cap", style: "justify-content:flex-end" }, el("span", { style: "flex:1" }, "view " + (i + 1)),
          el("button", { class: "small", title: "Start a clip on this picture", onclick: actUse(p, "first") }, "first frame"), el("button", { class: "small", title: "Add it to a reference clip’s pictures", onclick: actUse(p, "ref") }, "reference"))))))));
  } else if (o.kind === "upscale") {
    put(box, el("div", { class: "note", style: "margin-top:0" }, "Makes a finished clip larger: the model’s latent upscaler enlarges it, then one light re-draw in tiles puts detail back, guided by the clip itself so it restores what is there. Mouths, motion and the clip’s own sound stay."),
      field("Size", choice(f, "megapixels", { 1: ["1 megapixel", "about 1344 × 768"], 2: ["2 megapixels", "about 1920 × 1080"], 3: ["3 megapixels", "about 2304 × 1280"], 4: ["4 megapixels", "about 2688 × 1504, slowest"] }, redraw, true)),
      field("Re-draw", choice(f, "denoise", { 0: ["None", "only enlarge"], 0.1: ["Light", "0.1, the tested setting"], 0.2: ["Stronger", "0.2, adds more of its own"] }, redraw, true)),
      by, el("button", { class: "hot", disabled: ACT.busy, onclick: () => actQueue("upscale", { video: o.path, megapixels: Number(f.megapixels), denoise: Number(f.denoise) }) }, "Upscale this clip"),
      actShow("upscale", job => el("video", { class: "main", src: file(job.outputs[0]), controls: true, style: "margin-top:8px" })));
  } else if (o.kind === "facefix") {
    const most = a.faces || 4;
    const loader = one => { const up = el("input", { type: "file", accept: ".png,.jpg,.jpeg,.webp", onchange: async () => { if (!up.files[0]) return; const data = new FormData(); data.append("file", up.files[0]);
      try { const r = await (await fetch("/api/upload", { method: "POST", body: data })).json(); if (!r.path) throw new Error(r.error || "upload failed"); one.image = r.path; render(); } catch (e) { toast(e.message); } } }); return up; };
    const pick = i => { ACT.facePicking = ACT.facePicking === i ? null : i; if (ACT.facePicking !== null && !ACT.faceLib)
      api("/api/library").then(list => { ACT.faceLib = list.filter(m => m.kind === "image"); if (ACT.open && ACT.open.kind === "facefix") render(); }).catch(e => toast(e.message)); render(); };
    const library = one => { if (!ACT.faceLib) return el("div", { class: "note" }, "Reading pictures from the Library…");
      const grid = el("div", { class: "grid", style: "max-height:320px;overflow-y:auto;margin-top:10px" });
      const countNote = el("div", { class: "note", style: "margin-top:6px" });
      const show = query => { const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
        const matches = ACT.faceLib.filter(m => words.every(w => m.path.toLowerCase().includes(w)));
        countNote.textContent = matches.length > 80 ? "Showing 80 of " + matches.length + " pictures. Search by name or folder to find an older one." : "";
        grid.replaceChildren(...(matches.length ? matches.slice(0, 80).map(m => el("button", { type: "button", class: "tile" + (one.image === m.path ? " on" : ""), title: m.path,
          style: "padding:0;text-align:left;overflow:hidden", onclick: () => { one.image = m.path; ACT.facePicking = null; render(); } },
          media(m.path), el("span", { class: "cap" }, m.path.split("/").pop()))) : [el("div", { class: "note" }, ACT.faceLib.length ? "No pictures match." : "No pictures in the Library yet. Upload one above.")])); };
      const search = el("input", { type: "search", placeholder: "Find a picture by name or folder", "aria-label": "Find a face picture in the Library", value: ACT.faceQuery,
        oninput: e => { ACT.faceQuery = e.target.value; show(ACT.faceQuery); } });
      show(ACT.faceQuery);
      return el("div", { style: "margin:8px 0 14px" }, search, countNote, grid); };
    // a second face: the two are taken left to right, and the first one's "largest" becomes "left"
    const add = () => { if (f.faces.length === 1 && f.faces[0].which === "largest") f.faces[0].which = "left";
      const used = f.faces.map(x => x.which); f.faces.push({ image: null, which: FACE_ORDER.find(w => !used.includes(w)) || "largest" }); render(); };
    const drop = i => { f.faces.splice(i, 1); ACT.facePicking = null; if (f.faces.length === 1) f.faces[0].which = "largest"; render(); };
    const rows = f.faces.map((one, i) => el("div", {}, el("div", { class: "row", style: "align-items:center;margin-top:6px" },
      el("b", { style: "flex:none;min-width:64px" }, "Person " + (i + 1)), loader(one),
      el("button", { class: "fit small", type: "button", onclick: () => pick(i) }, ACT.facePicking === i ? "Close Library" : "Pick from Library"),
      one.image ? el("img", { src: file(one.image), style: "max-width:110px;border-radius:8px" }) : el("span", { class: "note", style: "margin:0" }, "a close-up of this person’s face"),
      field("Which face in the clip", select(one, "which", FACE_WHICH, redraw)),
      f.faces.length > 1 ? el("button", { class: "small", onclick: () => drop(i) }, "Remove") : null),
      ACT.facePicking === i ? library(one) : null));
    put(box, el("div", { class: "note", style: "margin-top:0" }, "Draws the faces of a finished clip again, close up. Each face is found and followed on every frame and cropped so it fills a square; MiniMax H3 draws that crop again from your picture of that person, at half strength and only inside the face. VOSR 2.0 restores the crop when memory allows, then H3 FaceRefine stitches it back with a soft, colour-matched edge. Several people are done one after another in the same job, each on the result of the one before. The rest of the picture and the clip’s own sound stay."),
      el("div", { class: "note" }, "Which face each picture goes to is chosen once, where the faces first appear, then followed: say which one by its place in the frame. With two people side by side, Left-most and Right-most."),
      ...rows,
      f.faces.length < most ? el("button", { class: "small", style: "margin-top:6px", onclick: add }, "+ Add another face") : null,
      (a.models || []).length ? field("Video model", select(f, "transformer", { "": "Regular · MiniMax H3 Ref2VA", ...Object.fromEntries(a.models.map(n => [n, n.replace(/\.safetensors$/i, "")])) }, redraw)) : null,
      field("Crop restoration", select(f, "restore", { auto: "Automatic · use VOSR 2.0 when memory allows", on: "VOSR 2.0 · may exceed memory on this machine", off: "H3 redraw only · stitch directly" }, redraw)),
      el("div", { class: "note" }, "On machines with less than 48 GB RAM, Automatic stitches the H3-redrawn face directly. Loading VOSR 2.0 beside H3 stopped a two-face render on this machine's 32 GB RAM."),
      field("Face crop", choice(f, "canvas", { 512: ["512", "safer on 32 GB"], 640: ["640", ""], 768: ["768", "the author’s setting; needs more memory"] }, redraw, true)),
      field("Re-draw", choice(f, "denoise", { 0.35: ["Light", "0.35, keeps more of the clip’s face"], 0.5: ["Half", "0.5, the author’s setting"], 0.65: ["Stronger", "0.65, closer to the picture"] }, redraw, true)),
      el("div", { class: "note" }, "PixelAI Labs' face size fade keeps the original face in close-ups. The pixel sizes scale with video height (set here for 1080p)."),
      el("div", { class: "row" }, field("Full fix below (px)", bind(f, "full_fix_below_px", el("input", { type: "number", min: 8, max: 4095, step: 2, style: "max-width:110px" }))),
        field("No fix above (px)", bind(f, "no_fix_above_px", el("input", { type: "number", min: 9, max: 4096, step: 2, style: "max-width:110px" })))),
      el("div", { class: "row" }, field("Passes", bind(f, "steps", el("input", { type: "number", min: 2, max: 40, style: "max-width:90px" }))),
        field("Seed", bind(f, "seed", el("input", { type: "number", min: 0, style: "max-width:170px" }))),
        field("Prompt for the face pass (optional)", bind(f, "prompt", el("input", { placeholder: "empty, as the author has it: the picture alone guides the face" })))),
      by, el("button", { class: "hot", disabled: ACT.busy, onclick: () => {
        if (f.faces.some(x => !x.image)) return toast("Load a picture for every face");
        if (new Set(f.faces.map(x => x.which)).size !== f.faces.length) return toast("Give each picture a different face to follow");
        const full = Number(f.full_fix_below_px), none = Number(f.no_fix_above_px);
        if (!Number.isInteger(full) || !Number.isInteger(none) || full < 8 || full >= none || none > 4096) return toast("Set full fix below a smaller face size than no fix above");
        actQueue("facefix", { video: o.path, faces: f.faces.map(x => ({ image: x.image, which: x.which })), canvas: Number(f.canvas), denoise: Number(f.denoise),
          full_fix_below_px: full, no_fix_above_px: none, steps: Number(f.steps), seed: Number(f.seed), prompt: f.prompt, transformer: f.transformer, restore: f.restore }); } },
        f.faces.length > 1 ? "Fix the " + f.faces.length + " faces" : "Fix the face"),
      actShow("facefix", job => el("div", {}, el("div", { class: "row", style: "align-items:flex-start;margin-top:8px" }, el("div", {}, el("label", {}, "Faces fixed"), el("video", { class: "main", src: file(job.outputs[0]), controls: true })),
        el("div", {}, el("label", {}, "The clip as it was"), el("video", { class: "main", src: file(o.path), controls: true }))),
        ...job.outputs.slice(1).map((p, i) => el("div", {}, el("label", {}, "Person " + (i + 1) + ": the face crops, before and after"), el("video", { src: file(p), controls: true, loop: true, style: "width:100%;border-radius:8px" }))))));
  } else {
    const up = el("input", { type: "file", accept: ".png,.jpg,.jpeg,.webp", onchange: async () => { if (!up.files[0]) return; const data = new FormData(); data.append("file", up.files[0]);
      try { const r = await (await fetch("/api/upload", { method: "POST", body: data })).json(); if (!r.path) throw new Error(r.error || "upload failed"); f.clean_frame = r.path; render(); } catch (e) { toast(e.message); } } });
    const need = () => { if (!f.person.trim()) { toast("Say who to remove in a few words"); return false; } return true; };
    put(box, el("div", { class: "note", style: "margin-top:0" }, "Takes one person out of a clip and draws the background again. The person is found from a few words and followed through the clip; the removal then runs in overlapping windows of 22 frames. The whole picture is drawn again, so small details outside the person can change too. Best on a short continuous shot."),
      field("Who to remove", bind(f, "person", el("input", { placeholder: "man in a gray shirt · woman in a red coat · person on the left" }))),
      el("div", { class: "row" }, el("button", { class: "fit", disabled: ACT.busy, onclick: () => { if (need()) actQueue("mask", { video: o.path, person: f.person }, "mask"); } }, "1. Find the person"),
        el("span", { class: "note", style: "margin:0" }, "A quick look before the long render: the person painted green. If hands, hair or a shadow are left out, or someone else is green, describe the person more exactly.")),
      actShow("mask", job => el("div", { class: "row", style: "align-items:flex-start;margin-top:8px" }, el("div", {}, el("label", {}, "The person, in green"), el("video", { src: file(job.outputs[0]), controls: true, loop: true, style: "width:100%;border-radius:8px" })),
        job.outputs[2] ? el("div", {}, el("label", {}, "A first frame without the person, built from the clip"), el("img", { src: file(job.outputs[2]), style: "width:100%;border-radius:8px" })) : null)),
      field("The clean first frame", choice(f, "clean", { auto: ["Build it for me", "from the clip’s other frames, then drawn sharp by the remover"], own: ["My own picture", "the first frame with the person edited out, same size"] }, redraw, true)),
      f.clean === "own" ? el("div", { class: "row" }, up, f.clean_frame ? el("img", { src: file(f.clean_frame), style: "max-width:220px;border-radius:8px" }) : el("span", { class: "note", style: "margin:0" }, "Edit the person out of the clip’s first frame in any picture editor and load it here.")) : null,
      el("div", { class: "row" }, field("Window", select(f, "window", { 22: "22 frames (proven)", 39: "39 frames", 56: "56 frames" })), field("Passes", bind(f, "steps", el("input", { type: "number", min: 4, max: 30, style: "max-width:90px" }))),
        field("Seed", bind(f, "seed", el("input", { type: "number", min: 0, style: "max-width:120px" }))),
        el("label", { style: "display:flex;gap:6px;align-items:center;flex:none", title: "The clip’s own sound is put back under the result. It still holds the removed person’s voice: tick this to leave the sound out." }, bind(f, "silent", el("input", { type: "checkbox", style: "width:auto" })), "No sound")),
      by, el("button", { class: "hot", disabled: ACT.busy, onclick: () => { if (!need()) return; if (f.clean === "own" && !f.clean_frame) return toast("Load the clean first frame, or choose “Build it for me”");
        actQueue("remove", { video: o.path, person: f.person, window: Number(f.window), steps: Number(f.steps), seed: Number(f.seed), silent: !!f.silent, ...(f.clean === "own" ? { clean_frame: f.clean_frame } : {}) }); } }, "2. Remove the person"),
      actShow("remove", job => el("div", {}, (job.log || "").split("\n").filter(l => l.includes("] check: ")).map(l => el("div", { class: "note", style: l.includes("still show green") ? "color:var(--warn)" : "" }, l.split("] check: ")[1])),
        el("div", { class: "row", style: "align-items:flex-start;margin-top:8px" }, el("div", {}, el("label", {}, "Without the person"), el("video", { class: "main", src: file(job.outputs[0]), controls: true })),
        el("div", {}, el("label", {}, "The clip as it was"), el("video", { class: "main", src: file(o.path), controls: true }))))));
  }
  return box; }
