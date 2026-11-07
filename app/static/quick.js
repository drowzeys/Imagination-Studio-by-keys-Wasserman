"use strict";
/* Quick Edit (Creative Engines › Image): one box, like a chat. Paste a picture from the clipboard (or drop it, or
   press +), say what changes, press the arrow. It is the same `edit` job as Create's Edit a picture (app/picture_edit.py)
   with the model's own settings built in and nothing to set: the result keeps the picture's size, the seed is drawn
   anew for every turn, Qwen 2.1 Turbo runs its 8 steps, FLUX3-K Fast its 16 and FLUX3-K Full its 50. The picture
   model is the one choice on the bar; the models offered are those /api/edit/state lists as installed here. */
const QK = { state: null, image: null, prompt: "", model: "qwen", turns: [], busy: false, over: false, lib: null, picking: false };
const qkModels = () => (QK.state && QK.state.models) || [];
const qkModel = () => (qkModels().some(m => m.id === QK.model) ? QK.model : (qkModels()[0] || { id: "qwen" }).id);
const qkPicture = file => file && /^(image|video)\//.test(file.type || "") || /\.(png|jpe?g|webp|mp4|mov)$/i.test((file && file.name) || "");
async function qkLoad() { try { QK.state = await api("/api/edit/state"); } catch (e) { QK.state = { ready: false, why: e.message, models: [] }; } if (tab === "Quick Edit") render(); }
// a picture from the clipboard, a drop or the file picker: uploaded, and a clip's first frame taken for a video
async function qkTake(file) { if (!qkPicture(file)) return toast("Paste or drop a picture (png, jpg, webp) or a clip");
  const data = new FormData(); data.append("file", file, file.name || ("pasted." + ((file.type || "image/png").split("/")[1] || "png").replace("jpeg", "jpg")));
  try { const r = await fetch("/api/upload", { method: "POST", body: data }); const j = await r.json(); if (!j.path) throw new Error(j.error || "upload failed");
    QK.image = /\.(mp4|mov)$/i.test(j.path) ? (await api("/api/frame", { path: j.path, at: "first" })).path : j.path; QK.picking = false; render(); } catch (e) { toast(e.message); } }
// Edit on any picture in the studio: Quick Edit opens with it loaded, ready for the words
function qkOpen(path) { QK.image = path; QK.prompt = ""; QK.picking = false; if (typeof go === "function") go("Quick Edit"); else { tab = "Quick Edit"; render(); } }
// Edit this frame: a still of the frame a clip is showing (its player's time, else the first) opened in Quick Edit. The
// clip itself is not changed; the edited still can open a new clip (Make a clip of it)
async function qkFrame(path, player) { const t = player && Number.isFinite(player.currentTime) ? player.currentTime : 0, at = t > 0.05 ? Math.round(t * 100) / 100 : "first";
  try { const r = await api("/api/frame", { path, at }); qkOpen(r.path); toast("A still of " + (at === "first" ? "the first frame" : at + " s") + " to edit: the clip itself is not changed"); } catch (e) { toast(e.message); } }
const qkFrameButton = (path, player, cls) => el("button", { class: cls || "small", type: "button", title: "Edit a still of the frame the clip is showing (pause where you want it); the clip itself is not changed", onclick: () => qkFrame(path, typeof player === "function" ? player() : player) }, "Edit this frame");
// ⌘V / Ctrl+V anywhere on the page while Quick Edit is open: a picture on the clipboard becomes the picture to edit
function qkPaste(e) { if (tab !== "Quick Edit" || !e.clipboardData) return; const f = [...(e.clipboardData.files || [])].find(qkPicture); if (!f) return; e.preventDefault(); qkTake(f); }
if (typeof document !== "undefined" && typeof document.addEventListener === "function") document.addEventListener("paste", qkPaste);
function qkWants() { const out = []; if (!QK.state || !QK.state.ready) out.push("a picture model that edits, which is not set up here");
  if (!QK.image) out.push("a picture"); if (!QK.prompt.trim()) out.push("what changes"); return out; }
async function qkGo() { if (QK.busy) return; const wants = qkWants(); if (wants.length) return toast("Needs " + wants.join(" and "));
  QK.busy = true; render();  // the queue takes a moment to answer: a second press before it does must not make a second edit
  const prompt = QK.prompt.trim(), model = qkModel(), seed = Math.floor(Math.random() * 2147483647) || 1, image = QK.image;
  try { const j = await api("/api/jobs", { kind: "edit", title: "Quick edit: " + prompt.slice(0, 40), params: { image, prompt, seed, size: "source", image_model: model } });
    QK.turns.unshift({ job: j.id, prompt, model, seed, image }); QK.prompt = ""; QK.busy = false; await poll(); render(); } catch (e) { QK.busy = false; render(); toast(e.message); } }
// one turn of the chat: what was asked, and the picture that came back (or how far along it is)
function qkTurn(t) { const job = jobs.find(j => j.id === t.job), m = qkModels().find(m => m.id === t.model) || { label: t.model };
  const box = el("div", { class: "qturn", "data-job": t.job }, el("div", { class: "said" }, el("img", { src: file(t.image), alt: "", class: "mini" }), el("div", {}, el("b", {}, t.prompt), el("span", { class: "note m-flat" }, m.label + " · seed " + t.seed))));
  if (!job) put(box, el("div", { class: "note" }, "Queued…"));
  else if (job.status === "done" && job.outputs && job.outputs.length) put(box, el("div", { class: "qresult" }, job.outputs.map(p => el("div", {}, el("img", { src: file(p), alt: "The edited picture" }),
    el("div", { class: "row acts", style: "margin-top:8px" }, el("button", { class: "small fit", onclick: () => { QK.image = p; window.scrollTo(0, 0); render(); } }, "Edit this result"),
      el("a", { class: "btn small fit", href: file(p), download: "" }, "Download"),
      el("button", { class: "small fit", title: "Open a new clip on this picture", onclick: () => { C.image = p; C.goes = "video"; C.start = "own"; C.step = 1; changed(); go("Create"); } }, "Make a clip of it"),
      el("button", { class: "small fit", onclick: () => { QK.prompt = t.prompt; window.scrollTo(0, 0); render(); } }, "Ask again"), el("span"))))));
  else if (job.status === "failed" || job.status === "cancelled") put(box, el("div", { class: "note", style: "color:var(--warn)" }, el("b", {}, job.status === "failed" ? "It did not come back. " : "Cancelled. "), (job.error || "").slice(0, 300)),
    el("button", { class: "small", onclick: () => { QK.image = t.image; QK.prompt = t.prompt; window.scrollTo(0, 0); render(); } }, "Try again"));
  else put(box, typeof jobCard === "function" ? jobCard(job) : el("div", { class: "note" }, job.status));
  return box; }
function viewQuick() { const body = el("div", { class: "quick" }), st = QK.state;
  put(body, pageHead("Quick Edit", "What do you want to change?", "Paste a picture, say what is different, press the arrow. The result keeps the picture's size; each turn draws a new seed."));
  if (!st) { qkLoad(); put(body, el("div", { class: "note" }, "Looking at what is installed…")); return el("section", { class: "card" }, body); }
  if (!st.ready) { put(body, el("div", { class: "note", style: "color:var(--warn)" }, el("b", {}, "Quick Edit is not set up on this machine. "), st.why)); return el("section", { class: "card" }, body); }
  QK.model = qkModel();
  const up = el("input", { type: "file", accept: "image/*,video/mp4,video/quicktime", class: "hide", "aria-label": "Choose a picture", onchange: () => { if (up.files[0]) qkTake(up.files[0]); up.value = ""; } });
  const area = bind(QK, "prompt", el("textarea", { id: "quickprompt", rows: 2, placeholder: QK.image ? "Change the sky to a sunset, keep everything else…" : "Paste a picture first (⌘V / Ctrl+V), then say what changes…",
    onkeydown: e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); qkGo(); } } }));
  const steps = { qwen: "8 steps", "flux3k-fast": "16 steps", "flux3k-full": "50 steps" };
  const models = el("div", { class: "seg-ctl", role: "radiogroup", "aria-label": "Picture model", id: "quickmodel" }, qkModels().map(m => el("button", { type: "button", role: "radio", class: m.id === QK.model ? "on" : "", "aria-checked": m.id === QK.model ? "true" : "false",
    title: m.label + ": " + (steps[m.id] || "") + ", its own best settings", onclick: () => { QK.model = m.id; render(); } }, mark(/^flux/.test(m.id) ? "flux" : "qwen", ""), m.label)));
  const arrow = el("span", { html: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5"/><path d="M6 11l6-6 6 6"/></svg>' });
  const box = el("div", { class: "qbox" + (QK.over ? " over" : ""), id: "quickbox",
    ondragover: e => { e.preventDefault(); if (!QK.over) { QK.over = true; box.classList.add("over"); } }, ondragleave: () => { QK.over = false; box.classList.remove("over"); },
    ondrop: e => { e.preventDefault(); QK.over = false; box.classList.remove("over"); const f = [...((e.dataTransfer && e.dataTransfer.files) || [])].find(qkPicture); if (f) qkTake(f); else toast("Drop a picture (png, jpg, webp) or a clip"); } },
    el("div", { class: "qpic" }, QK.image ? el("div", { class: "thumb" }, el("img", { src: file(QK.image), alt: "The picture to edit" }), el("button", { type: "button", title: "Take the picture out", "aria-label": "Take the picture out", onclick: () => { QK.image = null; render(); } }, "✕"))
      : el("div", { class: "qdrop", role: "button", tabindex: 0, onclick: () => up.click(), onkeydown: e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); up.click(); } } }, el("span", {}, el("b", {}, "Paste a picture"), el("br"), "⌘V / Ctrl+V anywhere on this page, drop one here, or click to choose a file"))),
    area, up,
    el("div", { class: "qbar" }, el("button", { type: "button", class: "plus", title: "Choose a picture or a clip from this device", "aria-label": "Choose a picture", onclick: () => up.click() }, "+"),
      el("button", { type: "button", class: "small", onclick: () => { QK.picking = !QK.picking; if (QK.picking && !QK.lib) api("/api/library").then(list => { QK.lib = list; render(); }).catch(() => {}); render(); } }, QK.picking ? "Close the Library" : "Library"),
      qkModels().length > 1 ? models : el("span", { class: "note m-flat" }, (qkModels()[0] || {}).label || ""),
      el("button", { type: "button", class: "go hot", id: "quickgo", title: "Go (Enter)", "aria-label": "Go", disabled: QK.busy, onclick: qkGo }, arrow)));
  put(body, box);
  if (QK.picking) { const stills = (QK.lib || []).filter(m => m.kind === "image").slice(0, 24);
    put(body, !QK.lib ? el("div", { class: "note" }, "Looking in the Library…") : stills.length ? el("div", { class: "grid", style: "margin-top:12px" }, stills.map(m => el("div", { class: "tile" + (QK.image === m.path ? " on" : ""), style: "cursor:pointer", title: m.path, onclick: () => { QK.image = m.path; QK.picking = false; render(); } }, media(m.path))))
      : el("div", { class: "note" }, "No pictures in the Library yet.")); }
  const m = qkModels().find(x => x.id === QK.model) || {};
  put(body, el("div", { class: "note" }, "Describe the result, not the steps: what you do not mention is meant to stay. ", m.label ? m.label + " runs " + (steps[m.id] || "") + " with its own best settings; " : "", "there is nothing else to set. Results go to the Library. ",
    typeof edOpen === "function" ? el("button", { type: "button", class: "small ghost", id: "quickmore", title: "Create › Edit a picture, with this picture, these words and this model: for LoRAs, camera angles or several pictures",
      onclick: () => { Object.assign(ED, { prompt: QK.prompt, model: qkModel(), more: [], job: null, size: "source", base: false, loras: [], seed: Math.floor(Math.random() * 2147483647) || 1 }); edOpen(QK.image); } }, "More tools: LoRAs, angles, several pictures →") : "For LoRAs, camera angles or several pictures use Create › Edit a picture."),
    QK.turns.length ? el("div", { class: "qturns" }, el("h3", {}, "This session"), QK.turns.map(qkTurn)) : null);
  return el("section", { class: "card" }, body); }
