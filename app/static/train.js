"use strict";
// Looks → Train a LoRA (app/train.py): clips and stills from the Library become a look or a character LoRA, trained on
// this DGX Spark as a job of the queue (spark/train_lora.py). trainPanel() is the panel the Looks page shows; a trained
// LoRA lands in Looks under My LoRAs, and trainTryButton(r) queues a clip with it.
const TR = { asked: false, info: null, lib: null, pick: new Set(), picking: false, busy: false, done: new Set(),
  form: { name: "", trigger: "", kind: "look", captions: "auto", steps: 1000, size: "standard" } };
const TRAIN_SETUP = "bash oneshot-setup-spark.sh --train";
const TRAIN_TRY = "A person walks slowly through a quiet street at dusk and stops to look back at the camera.";
async function trainLoad() { try { TR.info = await api("/api/train"); }
  catch (e) { TR.info = { ready: false, spark: true, missing: [e.message], setup: TRAIN_SETUP, datasets: [], runs: [] }; } render(); }
const trainLeast = () => ((TR.info || {}).limits || {}).items || 8;
const trainPerStep = size => ((((TR.info || {}).sizes || {})[size || TR.form.size]) || { per_step: 110 }).per_step;
const trainTime = (steps, size) => mmss(steps * trainPerStep(size));
const trainJobs = () => (typeof jobs !== "undefined" && Array.isArray(jobs) ? jobs : []).filter(j => j.kind === "train");
const trainActive = () => trainJobs().filter(j => ["queued", "running"].includes(j.status));

async function trainStart() { const f = TR.form, items = [...TR.pick];
  if (items.length < trainLeast()) return toast("Pick at least " + trainLeast() + " clips or stills (" + items.length + " picked)");
  if (!f.name.trim() || !f.trigger.trim()) return toast("Give the LoRA a name and a trigger word");
  if (typeof confirm === "function" && !confirm("Train “" + f.name.trim() + "” on " + items.length + " clips and stills?\n\nThe Spark is busy for about " + trainTime(f.steps) + ": ComfyUI is stopped and nothing else renders until it ends. You can cancel and resume later from the last checkpoint.")) return;
  TR.busy = true; render();
  try { const set = await api("/api/train/datasets", { name: f.name.trim(), trigger: f.trigger.trim(), kind: f.kind, captions: f.captions, items });
    await api("/api/train/start", { dataset: set.id, name: f.name.trim(), steps: Number(f.steps), size: f.size });
    TR.pick.clear(); TR.picking = false; toast("Training queued: " + f.name.trim()); if (typeof poll === "function") await poll(); await trainLoad(); }
  catch (e) { toast(e.message); }
  TR.busy = false; render(); }
async function trainResume(run) {
  try { await api("/api/train/start", { resume: run.lora }); toast("Resuming “" + run.name + "” from step " + run.saved); if (typeof poll === "function") await poll(); await trainLoad(); }
  catch (e) { toast(e.message); } }
// a short clip with the trained LoRA on: its trigger and look phrase are put at the start of the shot by the server
async function trainTry(r) {
  try { await api("/api/jobs", { kind: "video", title: "Try " + r.name, params: { prompt: TRAIN_TRY, loras: [{ id: r.id, strength: r.strength || 1, style: Object.keys(r.styles || {})[0] || "" }] } });
    toast("Queued a clip with " + r.name); if (typeof poll === "function") poll(); }
  catch (e) { toast(e.message); } }
const trainTryButton = r => el("button", { type: "button", class: "small", title: "Queues a short clip with this LoRA on", onclick: () => trainTry(r) }, "Try it");

function trainPicker() { if (!TR.lib) { TR.lib = []; api("/api/library").then(l => { TR.lib = (l || []).filter(m => m.kind === "video" || m.kind === "image"); render(); }).catch(() => {}); }
  const fromLibrary = typeof LIBPICK !== "undefined" ? [...LIBPICK].filter(p => TR.lib.some(m => m.path === p)) : [];
  return el("div", { class: "panel ag-pick" }, el("div", { class: "kv" }, el("b", { class: "ag-h" }, "Pick clips and stills from the Library (" + TR.pick.size + ")"),
      el("div", { class: "chips" }, fromLibrary.length ? el("button", { class: "small", title: "The ones ticked on the Library page", onclick: () => { for (const p of fromLibrary) TR.pick.add(p); render(); } }, "Add the " + fromLibrary.length + " selected in the Library") : null,
        TR.pick.size ? el("button", { class: "small ghost", onclick: () => { TR.pick.clear(); render(); } }, "Clear") : null,
        el("button", { class: "small", onclick: () => { TR.picking = false; render(); } }, "Done"))),
    TR.lib.length ? el("div", { class: "grid ag-grid" }, TR.lib.slice(0, 120).map(m => el("div", { class: "tile ag-tile" + (TR.pick.has(m.path) ? " on" : ""), "data-path": m.path, onclick: () => { if (TR.pick.has(m.path)) TR.pick.delete(m.path); else TR.pick.add(m.path); render(); } },
      media(m.path, { controls: false, muted: true }), el("div", { class: "cap" }, m.path.split("/").pop()))))
      : el("div", { class: "note" }, "No clips or stills in the Library yet.")); }

function trainRuns() { const runs = ((TR.info || {}).runs || []).filter(r => r.done || r.can_resume), active = trainActive().map(j => (j.params || {}).lora);
  if (!runs.length) return null;
  return el("div", { class: "stack" }, runs.map(r => { const hub = (typeof LORAS !== "undefined" ? LORAS : []).find(x => x.id === r.lora);
    return el("div", { class: "kv", "data-run": r.lora }, el("b", {}, r.name), el("span", {}, r.done ? "ready in Looks · trigger " + r.trigger : "stopped at step " + r.saved + " of " + r.steps),
      r.done ? (hub && hub.installed ? trainTryButton(hub) : null)
        : active.includes(r.lora) ? el("span", { class: "tag2 t" }, "in the queue")
        : el("button", { class: "small", title: "Goes on from the last checkpoint", onclick: () => trainResume(r) }, "Resume")); })); }

function trainPanel() { const info = TR.info, f = TR.form;
  if (!TR.asked) { TR.asked = true; trainLoad(); }
  // a run that has just finished: its LoRA is in the hub now
  for (const j of trainJobs()) if (j.status === "done" && !TR.done.has(j.id)) { TR.done.add(j.id); if (typeof loadLoras === "function") loadLoras().then(() => render()); trainLoad(); }
  const card = typeof cardFace === "function" && cardFace();
  const head = el("div", { class: "kv" }, el("b", {}, "Train a LoRA"), info && info.ready ? el("span", { class: "tag2 ok" }, card ? "Ready on this GPU." : "Ready on this Spark") : null);
  const line = el("p", { class: "note" }, "Your own clips or stills become a look or a character the video model can use: at least " + trainLeast() + ", each clip 5 seconds or longer. It trains on this " + (card ? "GPU" : "DGX Spark") + " and lands here in Looks.");
  if (!info) return el("div", { class: "panel train" }, head, line, el("div", { class: "note" }, "Loading…"));
  if (!info.spark) return el("div", { class: "panel train" }, head, line, el("div", { class: "note" }, "Training a LoRA runs on a DGX Spark. On the Spark: " + (info.setup || TRAIN_SETUP)));
  if (!info.ready) return el("div", { class: "panel train" }, head, line,
    el("div", { class: "note" }, card ? "Training is not set up on this GPU yet. Run once: " : "Training is not set up on this Spark yet. Run once: ", el("code", {}, info.setup || TRAIN_SETUP)),
    (info.missing || []).length ? el("details", { class: "fold" }, el("summary", {}, "What is missing (" + info.missing.length + ")"), el("div", { class: "note", style: "margin:0" }, info.missing.join("; "))) : null);
  const running = trainActive();
  const steps = el("input", { type: "range", min: 300, max: 3000, step: 100, value: f.steps, "aria-label": "Training steps, 300 to 3000",
    oninput: e => { f.steps = Number(e.target.value); said.textContent = f.steps + " steps · about " + trainTime(f.steps); } });
  const said = el("b", {}, f.steps + " steps · about " + trainTime(f.steps));
  const inp = (key, attrs) => el("input", { ...attrs, value: f[key], oninput: e => { f[key] = e.target.value; } });
  const sizes = Object.fromEntries(Object.entries(info.sizes || { standard: { label: "928x512, 124 frames" } }).map(([k, v]) => [k, [k === "standard" ? "Standard" : "Small", v.label]]));
  return el("div", { class: "panel train" }, head, line,
    running.length ? el("div", { class: "stack" }, running.map(j => jobCard(j))) : null,
    trainRuns(),
    el("div", { class: "row" }, field("Name", inp("name", { placeholder: "My 16mm noir" })), field("Trigger word", inp("trigger", { placeholder: "one word, e.g. wk_noir" }))),
    field("What it learns", choice(f, "kind", { look: ["A look", "a style: colour, grain, light, lenses"], character: ["A character", "one person or creature, the same in every shot"] }, render, true)),
    field("Captions", choice(f, "captions", { auto: ["Automatic", "each clip's prompt, else the Library's caption, else the trigger"], prompts: ["Prompts only", "each clip's prompt, else the trigger"], none: ["Trigger only", "every caption is the trigger word"] }, render, true)),
    field("Size", choice(f, "size", sizes, render, true)),
    el("div", {}, el("label", {}, "Length"), steps, el("div", { class: "note" }, said, " · " + (card ? "the GPU is busy" : "the Spark is busy") + " for that long: ComfyUI is stopped and nothing else renders until it ends. A checkpoint every 100 steps: a run you cancel resumes from it.")),
    el("div", { class: "chips" }, el("button", { type: "button", class: "small" + (TR.picking ? " on" : ""), onclick: () => { TR.picking = !TR.picking; render(); } }, TR.pick.size ? TR.pick.size + " picked: change" : "Pick from the Library"),
      el("span", { class: "note m-flat" }, TR.pick.size < trainLeast() ? "at least " + trainLeast() + " needed" : "ready to train")),
    TR.picking ? trainPicker() : null,
    el("div", { class: "lookacts" }, el("button", { type: "button", class: "primary", disabled: TR.busy || running.length > 0 || TR.pick.size < trainLeast(), title: running.length ? "One run at a time" : "", onclick: trainStart }, TR.busy ? "Starting…" : "Start training"))); }
