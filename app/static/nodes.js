"use strict";
/* Nodes · a small ComfyUI-style canvas over the studio's own jobs (app/nodes.py).
   Cards with typed ports, joined by curves: a Text card feeds a Picture, the Picture a Clip's first frame, and so on.
   Run checks the graph on the server, puts its cards in order and queues the studio's existing jobs one after another
   (a scout, an edit, a clip, a Drama 3 line, a MiniMax-Music3 song, an export), each card showing what it made.
   The canvas is drawn by hand: cards are HTML in one transformed layer, links an SVG beneath them. Pan by dragging the
   background (or space and drag), zoom with the wheel, link by dragging from an output to an input, Backspace deletes,
   right-click or double-click adds a card. ⌘S saves, ⌘Enter runs, Esc lets go. On a phone the graph is read-only. */

// The kinds of card, as app/nodes.py has them (tests/test_nodes_page.py holds the two to the same ports and settings)
const ND_TYPES = {
  text: { label: "Text", line: "Words: a prompt, a line, a style", inputs: {}, outputs: { text: "text" }, params: { text: "" } },
  picture: { label: "Picture", line: "Image Engine: Qwen 2.1 or FLUX3-K", inputs: { prompt: { types: ["text"], required: true } },
    outputs: { image: "image", images: "images" }, params: { image_model: "qwen", width: 1344, height: 768, seed: 1, count: 1 } },
  pick: { label: "Pick", line: "One picture of a set", inputs: { images: { types: ["images"], required: true } }, outputs: { image: "image" }, params: { index: 1 } },
  edit: { label: "Edit picture", line: "A picture re-drawn from words", inputs: { image: { types: ["image"], required: true }, prompt: { types: ["text"], required: true } },
    outputs: { image: "image" }, params: { image_model: "qwen", seed: 1 } },
  clip: { label: "Clip", line: "Video Engine: a clip with sound", inputs: { prompt: { types: ["text"], required: true }, first_frame: { types: ["image"] },
    refs: { types: ["image", "video", "audio"], many: true } }, outputs: { video: "video" }, params: { preset: "small", quality: "fh8", seconds: 5, seed: 1, look: "" } },
  voice: { label: "Voice", line: "Fish Audio Drama 3: a spoken line", inputs: { text: { types: ["text"], required: true } }, outputs: { audio: "audio" }, params: { seed: 1 } },
  song: { label: "Song", line: "MiniMax-Music3: a song from a style", inputs: { style: { types: ["text"], required: true }, lyrics: { types: ["text"] } },
    outputs: { audio: "audio" }, params: { seconds: 30, seed: 1 } },
  reference: { label: "Reference", line: "A file from the Library", inputs: {}, outputs: { file: "image" }, params: { path: "", kind: "image" } },
  timeline: { label: "Timeline", line: "Clips in order, a song under them", inputs: { clips: { types: ["video"], many: true, required: true }, song: { types: ["audio"] } },
    outputs: { timeline: "timeline" }, params: { project: "" } },
  export: { label: "Export", line: "One mp4 from a timeline or a clip", inputs: { source: { types: ["timeline", "video"], required: true } }, outputs: { file: "file" },
    params: { size: "1920x1080", music_db: -10 } },
  note: { label: "Note", line: "A sticky note; never runs", inputs: {}, outputs: {}, params: { text: "" } },
};
const ND_W = 248, ND_HEAD = 38, ND_ROW = 26, ND_EDGE = 1;  // a card's width, its head and one port row (nodes.css keeps these)
const ND_PRESETS = { small: "Small 864x480", draft: "Draft 1344x768", p720: "720p", p960: "960p", native: "Native 1344x768", "2k": "2K 2048x1152", qhd: "2K 2560x1440", sharp: "Sharp Spec 1376x768" };
const ND_QUALITIES = { fh8: "FastH3 8 passes", fh4: "FastH3 4 passes", fh20: "FastH3 20 passes", standard: "H3 Turbo", high: "H3 high", full: "H3 full 20 steps", fast: "H3 fast" };
const ND_KINDS = { image: "Picture", video: "Video", audio: "Sound" };
const ND_SIZES = { "1920x1080": "1920x1080", "1280x720": "1280x720", "3840x2160": "3840x2160", "1080x1920": "1080x1920 (9:16)" };
const ND_EDIT_MODELS = { qwen: "Qwen 2.1 Turbo", "flux3k-fast": "FLUX3-K Fast", "flux3k-full": "FLUX3-K Full" };
const ND_TEMPLATES = [];  // the server's starter graphs, once loaded
const ND = { graph: null, view: { x: 40, y: 30, k: 1 }, sel: null, seq: 0, dirty: false, saved: null, run: null, est: {}, problems: {},
  drag: null, pan: null, link: null, space: false, palette: null, root: null, stage: null, layer: null, svg: null, cards: {}, asked: false,
  library: null, projects: null, timer: null, bound: false };

const ndPhone = () => typeof matchMedia === "function" && matchMedia("(max-width: 760px)").matches;
const ndSay = s => String(s).replace(/_/g, " ");
const ndType = node => ND_TYPES[node.type];
const ndNode = id => ND.graph.nodes.find(n => n.id === id);
const ndOutType = (node, port) => node.type === "reference" ? (port === "file" ? node.params.kind : null) : (ndType(node).outputs[port] || null);
const ndTitle = node => (node.title || "").trim() || ndType(node).label;
const ndRows = node => Math.max(Object.keys(ndType(node).inputs).length, Object.keys(ndType(node).outputs).length);
const ndBusy = () => !!(ND.run && ["queued", "running"].includes(ND.run.status));
// where a port sits, in canvas units (the card's left or right edge, the middle of its row)
function ndPortAt(node, dir, port) {
  const keys = Object.keys(dir === "in" ? ndType(node).inputs : ndType(node).outputs), i = Math.max(0, keys.indexOf(port));
  return [node.x + (dir === "in" ? 0 : ND_W), node.y + ND_EDGE + ND_HEAD + ND_ROW * i + ND_ROW / 2]; }

// ---------- the graph ----------
function ndBlank(name) { return { id: null, name: name || "Untitled graph", nodes: [], edges: [] }; }
function ndLoad(graph) {
  ND.graph = { id: graph.id || null, name: graph.name || "Untitled graph", nodes: [], edges: [] };
  for (const n of graph.nodes || []) if (ND_TYPES[n.type]) ND.graph.nodes.push({ id: String(n.id), type: n.type, x: Number(n.x) || 0, y: Number(n.y) || 0, title: n.title || "",
    params: { ...ND_TYPES[n.type].params, ...(n.params || {}) } });
  ND.graph.edges = (graph.edges || []).filter(e => ndNode(e.from) && ndNode(e.to)).map(e => ({ from: e.from, out: e.out, to: e.to, in: e.in }));
  ND.seq = Math.max(0, ...ND.graph.nodes.map(n => Number(String(n.id).replace(/\D/g, "")) || 0));
  if (graph.view) ND.view = { x: Number(graph.view.x) || 0, y: Number(graph.view.y) || 0, k: Math.min(2, Math.max(0.4, Number(graph.view.k) || 1)) };
  ND.sel = null; ND.problems = {}; ND.est = {}; ND.dirty = false; ND.saved = graph.id || null;
  for (const n of ND.graph.nodes) if (n.type === "clip") ndEstimate(n); }
function ndSerialize() {
  return { ...(ND.graph.id ? { id: ND.graph.id } : {}), name: ND.graph.name, view: { ...ND.view },
    nodes: ND.graph.nodes.map(n => ({ id: n.id, type: n.type, x: Math.round(n.x), y: Math.round(n.y), title: n.title || "", params: { ...n.params } })),
    edges: ND.graph.edges.map(e => ({ ...e })) }; }
function ndTouch() { ND.dirty = true; ND.problems = {}; }
function ndAdd(type, x, y) {
  if (!ND_TYPES[type]) return null;
  let id; do { id = "n" + (++ND.seq); } while (ndNode(id));
  const node = { id, type, x: Math.round(x), y: Math.round(y), title: "", params: JSON.parse(JSON.stringify(ND_TYPES[type].params)) };
  ND.graph.nodes.push(node); ND.sel = { node: id }; ndTouch(); if (type === "clip") ndEstimate(node); return node; }
// whether a card waits, through its links, on another: a link the other way would loop
function ndReaches(from, to) { const seen = new Set(), stack = [from];
  while (stack.length) { const id = stack.pop(); if (id === to) return true; if (seen.has(id)) continue; seen.add(id);
    for (const e of ND.graph.edges) if (e.from === id) stack.push(e.to); } return false; }
// why a link cannot be made ("" when it can)
function ndCanConnect(from, out, to, port) {
  const a = ndNode(from), b = ndNode(to); if (!a || !b) return "no such card";
  if (from === to) return "a card cannot feed itself";
  const made = ndOutType(a, out), spec = ndType(b).inputs[port];
  if (!made || !spec) return "no such port";
  if (!spec.types.includes(made)) return ndTitle(a) + " gives " + made + ", and the " + ndSay(port) + " of " + ndTitle(b) + " takes " + spec.types.join(" or ");
  if (ndReaches(to, from)) return "that link would make the graph loop";
  if (ND.graph.edges.some(e => e.from === from && e.out === out && e.to === to && e.in === port)) return "they are joined already";
  return ""; }
function ndConnect(from, out, to, port) {
  const why = ndCanConnect(from, out, to, port); if (why) return why;
  if (!ndType(ndNode(to)).inputs[port].many) ND.graph.edges = ND.graph.edges.filter(e => !(e.to === to && e.in === port));  // a single input takes the newest link
  ND.graph.edges.push({ from, out, to, in: port }); ndTouch(); return ""; }
function ndRemove() {
  if (!ND.sel) return;
  if (ND.sel.node) { const id = ND.sel.node; ND.graph.nodes = ND.graph.nodes.filter(n => n.id !== id); ND.graph.edges = ND.graph.edges.filter(e => e.from !== id && e.to !== id); delete ND.est[id]; }
  else if (ND.sel.edge != null) ND.graph.edges.splice(ND.sel.edge, 1);
  ND.sel = null; ndTouch(); ndPaint(); }
// a Reference card's kind changes what it gives: links that no longer fit go
function ndRetype(node) { ND.graph.edges = ND.graph.edges.filter(e => e.from !== node.id || ndType(ndNode(e.to)).inputs[e.in].types.includes(ndOutType(node, e.out))); }

// ---------- the server ----------
async function ndList() {
  try { const r = await api("/api/nodes/graphs"); ND.saved_list = r.graphs || []; ND_TEMPLATES.splice(0, ND_TEMPLATES.length, ...(r.templates || [])); }
  catch { ND.saved_list = ND.saved_list || []; }
  ndPaintBar(); if (!ND.graph.nodes.length) ndPaint(); }
async function ndOpen(value) {
  if (!value) return;
  if (ND.dirty && ND.graph.nodes.length && !confirm("Leave " + ND.graph.name + " without saving it?")) return ndPaintBar();
  if (value.startsWith("t:")) { const t = ND_TEMPLATES.find(x => x.id === value.slice(2)); if (t) { ndLoad({ ...JSON.parse(JSON.stringify(t)), id: null, view: { x: 40, y: 30, k: 1 } }); ND.dirty = true; } }
  else { try { ndLoad(await api("/api/nodes/graphs/" + encodeURIComponent(value))); } catch (e) { return toast(e.message); } }
  ndPaint(); if (value.startsWith("t:")) ndFit(); }
async function ndSave() {
  if (ndPhone()) return;
  try { const r = await api("/api/nodes/graphs", ndSerialize()); ND.graph.id = r.id; ND.saved = r.id; ND.dirty = false; toast("Saved " + r.name); } catch (e) { return toast(e.message); }
  ndList(); }
async function ndDelete() {
  if (!ND.graph.id || !confirm("Delete " + ND.graph.name + "? It goes to the trash folder (nodes/.trash), not erased.")) return;
  try { await api("/api/nodes/graphs/" + encodeURIComponent(ND.graph.id), undefined, "DELETE"); } catch (e) { return toast(e.message); }
  ND.graph.id = null; ND.saved = null; ND.dirty = true; ndList(); ndPaintBar(); }
// the memory guard's answer and the expected time for a clip card, before anything is queued (/api/estimate)
async function ndEstimate(node) {
  const refs = ND.graph.edges.filter(e => e.to === node.id && e.in === "refs").length;
  try { ND.est[node.id] = await api("/api/estimate", { preset: node.params.preset, quality: node.params.quality, seconds: Number(node.params.seconds),
    referenced: refs > 0, references: refs, with_lora: !!node.params.look, loras: node.params.look ? [{ id: node.params.look }] : [] }); }
  catch { delete ND.est[node.id]; }
  ndPaintOut(node.id); }
async function ndRun() {
  if (ndPhone() || ndBusy()) return;
  if (!ND.graph.nodes.some(n => n.type !== "note")) return toast("Add a card first: right-click the canvas");
  let said; try { said = await api("/api/nodes/check", { graph: ndSerialize() }); } catch (e) { return toast(e.message); }
  ND.problems = {}; for (const p of said.problems) if (p.node && !ND.problems[p.node]) ND.problems[p.node] = p.message;
  if (!said.ok) { ndPaint(); return toast(said.problems[0].message + (said.problems.length > 1 ? " (and " + (said.problems.length - 1) + " more)" : "")); }
  const clips = ND.graph.nodes.filter(n => n.type === "clip"); await Promise.all(clips.map(ndEstimate));
  const full = clips.find(n => ND.est[n.id] && ND.est[n.id].memory && ND.est[n.id].memory.fits === false);
  if (full) { ND.sel = { node: full.id }; ndPaint(); return toast(ndTitle(full) + ": " + (ND.est[full.id].memory.reason || "it does not fit this machine's memory")); }
  try { ND.run = await api("/api/nodes/run", { graph: ndSerialize(), ...(ND.graph.id && !ND.dirty ? { id: ND.graph.id } : {}) }); } catch (e) { return toast(e.message); }
  toast("Running " + ND.graph.name + ": its jobs are in the queue"); ndPaintBar(); ndPaintStatus(); ndWatch(); }
async function ndCancel() {
  if (!ndBusy()) return;
  try { ND.run = await api("/api/nodes/runs/" + encodeURIComponent(ND.run.id) + "/cancel", {}); } catch (e) { return toast(e.message); }
  ndPaintBar(); ndPaintStatus(); }
// the run's state, every second and a half while it runs and the canvas is on the page
function ndWatch() {
  clearTimeout(ND.timer);
  if (!ND.run || !ndBusy()) return;
  ND.timer = setTimeout(async () => {
    if (!ND.root || !ND.root.isConnected) return;
    try { ND.run = await api("/api/nodes/runs/" + encodeURIComponent(ND.run.id)); } catch { }
    ndPaintBar(); ndPaintStatus();
    if (!ndBusy()) { toast(ND.run.name + ": " + ({ done: "finished", failed: "stopped at a card that failed", cancelled: "cancelled" }[ND.run.status] || ND.run.status)); if (typeof poll === "function") poll(); }
    ndWatch(); }, 1500); }
function ndMediaLists() {
  if (ND.library) return;
  ND.library = []; ND.projects = [];
  const list = path => api(path).then(x => Array.isArray(x) ? x.filter(t => t && t.path) : [], () => []);
  Promise.all([list("/api/library"), list("/api/voice/takes"), list("/api/music/takes")]).then(([lib, v, m]) => {
    ND.library = [...lib.map(x => ({ path: x.path, kind: x.kind })), ...[...v, ...m].map(t => ({ path: t.path, kind: "audio" }))]; if (ND.graph.nodes.some(n => n.type === "reference")) ndPaint(); });
  api("/api/projects").then(found => { ND.projects = Array.isArray(found) ? found : []; if (ND.graph.nodes.some(n => n.type === "timeline")) ndPaint(); }, () => {}); }

// ---------- drawing ----------
const ND_NS = "http://www.w3.org/2000/svg";
function ndSvg(tag, attrs) { const n = document.createElementNS(ND_NS, tag); for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v); return n; }
function ndCurve([x1, y1], [x2, y2]) { const dx = Math.max(48, Math.abs(x2 - x1) / 2);
  return "M" + x1 + " " + y1 + " C" + (x1 + dx) + " " + y1 + " " + (x2 - dx) + " " + y2 + " " + x2 + " " + y2; }
function ndTransform() { if (ND.layer) ND.layer.style.transform = "translate(" + ND.view.x + "px," + ND.view.y + "px) scale(" + ND.view.k + ")";
  if (ND.stage) { ND.stage.style.backgroundPosition = ND.view.x + "px " + ND.view.y + "px"; ND.stage.style.backgroundSize = 24 * ND.view.k + "px " + 24 * ND.view.k + "px"; } }
function ndPaintEdges() {
  if (!ND.svg) return;
  const kids = [];
  ND.graph.edges.forEach((e, i) => { const a = ndNode(e.from), b = ndNode(e.to); if (!a || !b) return;
    const d = ndCurve(ndPortAt(a, "out", e.out), ndPortAt(b, "in", e.in)), sel = ND.sel && ND.sel.edge === i;
    kids.push(ndSvg("path", { d, class: "nd-hit", "data-edge": i }), ndSvg("path", { d, class: "nd-wire t-" + ndOutType(a, e.out) + (sel ? " sel" : ""), "data-edge": i })); });
  if (ND.link) kids.push(ndSvg("path", { d: ndCurve(ND.link.start, ND.link.at), class: "nd-wire live t-" + ND.link.type }));
  ND.svg.replaceChildren(...kids); }
function ndField(node, key, kind, options) {
  const p = node.params, ro = ndPhone();
  const changed = (redraw) => { ndTouch(); if (node.type === "clip") ndEstimate(node); if (redraw) ndPaint(); else ndPaintBar(); };
  const opts = o => Object.entries(o).map(([v, t]) => el("option", { value: v, selected: String(p[key]) === v }, t));
  let input;
  if (kind === "area") input = el("textarea", { rows: node.type === "note" ? 4 : 3, placeholder: node.type === "note" ? "A note for whoever opens this graph" : "Words this card passes on", disabled: ro,
    oninput: e => { p[key] = e.target.value; ndTouch(); }, onchange: () => ndPaintBar() }, p[key]);
  else if (kind === "num") input = el("input", { type: "number", value: p[key], disabled: ro, step: key === "seconds" ? "0.5" : "1",
    onchange: e => { p[key] = Number(e.target.value); changed(false); } });
  else if (kind === "select") input = el("select", { disabled: ro, onchange: e => { p[key] = e.target.value; if (key === "kind") { p.path = ""; ndRetype(node); } changed(key === "kind"); } }, opts(options));
  else if (kind === "look") { const looks = typeof LORAS !== "undefined" && Array.isArray(LORAS) ? LORAS.filter(r => r.installed && (r.target || "video") === "video") : [];
    input = el("select", { disabled: ro, onchange: e => { p.look = e.target.value; changed(false); } }, el("option", { value: "" }, "No look"),
      looks.map(r => el("option", { value: r.id, selected: p.look === r.id }, r.name)), p.look && !looks.some(r => r.id === p.look) ? el("option", { value: p.look, selected: true }, p.look) : null); }
  else if (kind === "library") { const list = (ND.library || []).filter(x => x.kind === p.kind);
    input = el("select", { disabled: ro, onchange: e => { p.path = e.target.value; changed(true); } }, el("option", { value: "" }, list.length ? "Choose a file…" : "Nothing of this kind in the Library"),
      list.slice(0, 300).map(x => el("option", { value: x.path, selected: p.path === x.path }, x.path.split("/").slice(-2).join("/"))),
      p.path && !list.some(x => x.path === p.path) ? el("option", { value: p.path, selected: true }, p.path.split("/").pop()) : null); }
  else if (kind === "project") input = el("select", { disabled: ro, onchange: e => { p.project = e.target.value; changed(false); } }, el("option", { value: "" }, "Pass the clips on only"),
    (ND.projects || []).map(x => el("option", { value: x.id, selected: p.project === x.id }, "Add to " + x.name)));
  const words = { image_model: "model", music_db: "song dB", look: "look", path: "file", project: "editor", count: "how many", index: "which" }[key] || ndSay(key);
  return el("div", { class: "nd-f" + (kind === "area" || kind === "library" || kind === "look" || kind === "project" || key === "image_model" ? " wide" : "") },
    kind === "area" ? null : el("span", {}, words), input); }
function ndFields(node) {
  const models = typeof S !== "undefined" && S && S.image_models && S.image_models.length ? Object.fromEntries(S.image_models.map(m => [m.id, m.label]))
    : { qwen: "Qwen 2.1 Turbo", "qwen-full": "Qwen 2.1 Full", "flux3k-fast": "FLUX3-K Fast", "flux3k-full": "FLUX3-K Full" };
  const f = (key, kind, options) => ndField(node, key, kind, options);
  return {
    text: () => [f("text", "area")], note: () => [f("text", "area")],
    picture: () => [f("image_model", "select", models), f("width", "num"), f("height", "num"), f("seed", "num"), f("count", "num")],
    pick: () => [f("index", "num")],
    edit: () => [f("image_model", "select", ND_EDIT_MODELS), f("seed", "num")],
    clip: () => [f("preset", "select", ND_PRESETS), f("quality", "select", ND_QUALITIES), f("seconds", "num"), f("seed", "num"), f("look", "look")],
    voice: () => [f("seed", "num")], song: () => [f("seconds", "num"), f("seed", "num")],
    reference: () => [f("kind", "select", ND_KINDS), f("path", "library")],
    timeline: () => [f("project", "project")], export: () => [f("size", "select", ND_SIZES), f("music_db", "num")],
  }[node.type](); }
// what a card made, or why it did not, under its settings
function ndOut(node) {
  const st = ND.run && ND.run.nodes && ND.run.nodes[node.id], out = [];
  const est = ND.est[node.id];
  if (est && est.memory && est.memory.fits === false) out.push(el("div", { class: "nd-err" }, "Does not fit: " + (est.memory.reason || "this machine's memory")));
  else if (est && est.seconds && !st) out.push(el("div", { class: "nd-meta" }, "About " + mmss(est.seconds)));
  if (ND.problems[node.id]) out.push(el("div", { class: "nd-err" }, ND.problems[node.id]));
  if (!st) return out;
  if (st.status === "running") { const pr = st.progress;
    out.push(el("div", { class: "nd-meta live" }, st.job ? (st.job_status === "queued" ? "In the queue" : "Rendering") + (pr && pr.of ? " · " + (pr.stage || "step") + " " + pr.at + "/" + pr.of : "") : "Working")); }
  if (st.status === "waiting") out.push(el("div", { class: "nd-meta" }, "Waits its turn"));
  const images = st.outputs && Array.isArray(st.outputs.images) && st.outputs.images.length > 1 ? st.outputs.images : null;
  if (images) out.push(el("div", { class: "nd-strip" }, images.slice(0, 12).map((p, i) => el("img", { src: file(p), alt: "", title: "Picture " + (i + 1), loading: "lazy" }))));
  else if (st.thumb) out.push(/\.(mp4|mov)$/i.test(st.thumb) ? el("video", { class: "nd-thumb", src: file(st.thumb) + "#t=0.1", muted: true, playsinline: true, preload: "metadata", controls: true })
    : /\.(wav|mp3|m4a|aac|flac|ogg)$/i.test(st.thumb) ? el("audio", { src: file(st.thumb), controls: true, preload: "metadata" })
    : el("img", { class: "nd-thumb", src: file(st.thumb), alt: "", loading: "lazy" }));
  if (st.outputs && st.outputs.text) out.push(el("div", { class: "nd-meta" }, "Passes on " + st.outputs.text.length + " characters"));
  if (st.project) out.push(el("div", { class: "nd-meta" }, "Added to " + st.project + " in the Editor"));
  if (st.error) out.push(el("div", { class: "nd-err" }, st.error));
  return out; }
function ndCard(node) {
  const t = ndType(node), st = ND.run && ND.run.nodes && ND.run.nodes[node.id], ins = Object.entries(t.inputs), outs = Object.keys(t.outputs);
  const rows = [];
  for (let i = 0; i < ndRows(node); i++) {
    const [port, spec] = ins[i] || [], out = outs[i];
    const joined = port && ND.graph.edges.some(e => e.to === node.id && e.in === port);
    rows.push(el("div", { class: "nd-row" },
      port ? el("span", { class: "nd-in" }, el("i", { class: "nd-port in t-" + spec.types[0] + (joined ? " on" : "") + (spec.required ? " req" : ""), "data-node": node.id, "data-port": port, "data-dir": "in",
        title: ndSay(port) + ": takes " + spec.types.join(" or ") + (spec.many ? ", several" : "") + (spec.required ? "" : " (optional)") }), ndSay(port)) : el("span"),
      out ? el("span", { class: "nd-out" }, ndSay(out), el("i", { class: "nd-port out t-" + ndOutType(node, out), "data-node": node.id, "data-port": out, "data-dir": "out",
        title: ndSay(out) + ": gives " + ndOutType(node, out) + ". Drag to an input." })) : el("span"))); }
  const card = el("div", { class: "nd-node k-" + node.type + (ND.sel && ND.sel.node === node.id ? " sel" : "") + (st ? " s-" + st.status : "") + (ND.problems[node.id] ? " bad" : ""),
    "data-node": node.id, style: "left:" + node.x + "px;top:" + node.y + "px;width:" + ND_W + "px" },
    el("div", { class: "nd-head", title: "Drag to move · double-click to rename" }, el("i", { class: "nd-dot" }), el("b", {}, ndTitle(node)), el("span", { class: "nd-kind" }, t.label)),
    rows.length ? el("div", { class: "nd-ports" }, rows) : null,
    el("div", { class: "nd-body" }, ndFields(node)),
    el("div", { class: "nd-res" }, ndOut(node)));
  ND.cards[node.id] = card; return card; }
function ndPaintOut(id) { const card = ND.cards[id], node = ND.graph && ndNode(id); if (!card || !node || !card.querySelector) return;
  const res = card.querySelector(".nd-res"); if (res) { if (typeof releaseMedia === "function") releaseMedia(res); res.replaceChildren(...ndOut(node)); } }
// a run's news without rebuilding the cards (a field being typed in keeps its cursor)
function ndPaintStatus() {
  for (const node of ND.graph.nodes) { const card = ND.cards[node.id]; if (!card || !card.className) continue;
    const st = ND.run && ND.run.nodes && ND.run.nodes[node.id], was = card.dataset ? card.dataset.status : null, now = st ? st.status : "";
    card.className = card.className.replace(/\s?s-\w+/g, "") + (now ? " s-" + now : "");
    if (was !== now || (st && (st.status === "running" || st.thumb || st.error))) { if (card.dataset) card.dataset.status = now; ndPaintOut(node.id); } } }
function ndPaintBar() {
  const bar = ND.root && ND.root.querySelector && ND.root.querySelector(".nd-bar"); if (!bar) return;
  bar.replaceChildren(...ndBar().filter(Boolean)); }
function ndBar() {
  const ro = ndPhone(), busy = ndBusy(), run = ND.run;
  const open = el("select", { class: "nd-open", "aria-label": "Open a graph", onchange: e => ndOpen(e.target.value) }, el("option", { value: "" }, "Open…"),
    (ND.saved_list || []).length ? el("optgroup", { label: "Saved" }, ND.saved_list.map(g => el("option", { value: g.id }, g.name))) : null,
    ND_TEMPLATES.length ? el("optgroup", { label: "Start from" }, ND_TEMPLATES.map(t => el("option", { value: "t:" + t.id }, t.name))) : null);
  const name = el("input", { class: "nd-name", value: ND.graph.name, "aria-label": "Graph name", disabled: ro, oninput: e => { ND.graph.name = e.target.value; ND.dirty = true; } });
  const done = run ? Object.values(run.nodes).filter(s => s.status === "done").length : 0, total = run ? run.order.length : 0;
  const cur = run && run.current && ndNode(run.current);
  const status = run ? el("span", { class: "nd-status s-" + run.status }, el("i", { class: "nd-dot" }),
    busy ? "Running " + Math.min(done + 1, total) + " of " + total + (cur ? " · " + ndTitle(cur) : "") : { done: "Finished", failed: "Stopped", cancelled: "Cancelled" }[run.status] + " · " + done + " of " + total + " made") : null;
  return [open, name, ND.dirty && !ro ? el("span", { class: "nd-chip" }, "Unsaved") : null, status, el("span", { class: "fill" }),
    el("button", { class: "small ghost", title: "Fit the whole graph in view", onclick: ndFit }, "Fit"),
    ro ? null : el("button", { class: "small", title: "Add a card (right-click or double-click the canvas)", onclick: () => ndPalette(null) }, "Add card"),
    ro ? null : el("button", { class: "small", title: "A new, empty graph", onclick: () => { if (ND.dirty && ND.graph.nodes.length && !confirm("Leave " + ND.graph.name + " without saving it?")) return; ndLoad(ndBlank()); ndPaint(); } }, "New"),
    ro || !ND.graph.id ? null : el("button", { class: "small ghost", onclick: ndDelete }, "Delete"),
    ro ? null : el("button", { class: "small", title: "Save (⌘S)", onclick: ndSave }, "Save"),
    ro ? null : busy ? el("button", { class: "small", title: "Stop the job that renders and the cards after it (Esc stops a link, not the run)", onclick: ndCancel }, "Cancel run")
      : el("button", { class: "primary", title: "Run (⌘Enter): the cards' jobs go into the queue one after another", onclick: ndRun }, "Run")]; }
function ndEmpty() {
  if (ND.graph.nodes.length) return null;
  return el("div", { class: "nd-empty" }, el("b", {}, "An empty canvas"), el("p", {}, ndPhone() ? "Open a saved graph above." : "Right-click or double-click to add a card, or start from one of these:"),
    el("div", { class: "nd-temps" }, ND_TEMPLATES.map(t => el("button", { class: "nd-temp", disabled: ndPhone(), onclick: () => ndOpen("t:" + t.id) }, el("b", {}, t.name), el("span", {}, t.line))))); }
function ndPaint() {
  if (!ND.layer) return;
  ND.cards = {};
  if (typeof releaseMedia === "function") releaseMedia(ND.layer);
  ND.svg = ndSvg("svg", { class: "nd-wires", width: 1, height: 1 });
  ND.layer.replaceChildren(ND.svg, ...ND.graph.nodes.map(ndCard));
  const empty = ND.stage.querySelector && ND.stage.querySelector(".nd-empty"); if (empty) empty.remove();
  const blank = ndEmpty(); if (blank) ND.stage.append(blank);
  ndPaintEdges(); ndTransform(); ndPaintBar(); }

// the whole graph in view, at no more than its own size
function ndFit() {
  if (!ND.stage || !ND.stage.getBoundingClientRect || !ND.graph.nodes.length) return;
  const r = ND.stage.getBoundingClientRect(); if (!r.width) return;
  const box = ND.graph.nodes.reduce((b, n) => { const c = ND.cards[n.id], h = c && c.offsetHeight ? c.offsetHeight : ND_HEAD + ND_ROW * ndRows(n) + 120;
    return [Math.min(b[0], n.x), Math.min(b[1], n.y), Math.max(b[2], n.x + ND_W), Math.max(b[3], n.y + h)]; }, [Infinity, Infinity, -Infinity, -Infinity]);
  const k = Math.min(1, Math.max(0.4, Math.min((r.width - 64) / (box[2] - box[0]), (r.height - 64) / (box[3] - box[1]))));
  ND.view = { k, x: (r.width - (box[2] - box[0]) * k) / 2 - box[0] * k, y: (r.height - (box[3] - box[1]) * k) / 2 - box[1] * k }; ndTransform(); }

// ---------- the palette ----------
function ndPalette(at, link) {
  const r = ND.stage.getBoundingClientRect ? ND.stage.getBoundingClientRect() : { left: 0, top: 0, width: 800, height: 600 };
  const screen = at || [r.width / 2 - 130, 80];
  const world = [(screen[0] - ND.view.x) / ND.view.k, (screen[1] - ND.view.y) / ND.view.k];
  ND.palette = { screen, world, link: link || null, q: "" };
  ndPaintPalette(); }
function ndPaletteTypes() {
  const p = ND.palette, words = p.q.trim().toLowerCase();
  return Object.entries(ND_TYPES).filter(([id, t]) => (!words || (t.label + " " + t.line).toLowerCase().includes(words))
    && (!p.link || Object.values(t.inputs).some(s => s.types.includes(p.link.type)))); }
function ndPick(type) {
  const p = ND.palette; ndClosePalette();
  const node = ndAdd(type, p.world[0], p.world[1] - ND_HEAD); if (!node) return;
  if (p.link) { const port = Object.entries(ndType(node).inputs).find(([, s]) => s.types.includes(p.link.type)); if (port) ndConnect(p.link.from, p.link.out, node.id, port[0]); }
  ndPaint(); }
function ndClosePalette() { ND.palette = null; const old = ND.stage && ND.stage.querySelector && ND.stage.querySelector(".nd-palette"); if (old) old.remove(); }
function ndPaintPalette() {
  const old = ND.stage.querySelector && ND.stage.querySelector(".nd-palette"); if (old) old.remove();
  const p = ND.palette; if (!p) return;
  const list = el("div", { class: "nd-plist" });
  const fillList = () => list.replaceChildren(...ndPaletteTypes().map(([id, t]) => el("button", { class: "nd-pitem k-" + id, onclick: () => ndPick(id) },
    el("i", { class: "nd-sw" }), el("b", {}, t.label), el("span", {}, t.line))));
  const search = el("input", { type: "search", placeholder: p.link ? "Cards that take " + p.link.type + "…" : "Add a card…", "aria-label": "Search cards",
    oninput: e => { p.q = e.target.value; fillList(); },
    onkeydown: e => { if (e.key === "Enter") { const first = ndPaletteTypes()[0]; if (first) ndPick(first[0]); } if (e.key === "Escape") { e.stopPropagation(); ndClosePalette(); } } });
  fillList();
  const r = ND.stage.getBoundingClientRect ? ND.stage.getBoundingClientRect() : { width: 1000, height: 700 };  // kept inside the stage
  const box = el("div", { class: "nd-palette", style: "left:" + Math.max(8, Math.min(p.screen[0], r.width - 296)) + "px;top:" + Math.max(8, Math.min(p.screen[1], r.height - 380)) + "px" }, search, list);
  ND.stage.append(box); if (search.focus) setTimeout(() => search.focus(), 0); }

// ---------- pointer and keys ----------
const ndWorld = e => { const r = ND.stage.getBoundingClientRect(); return [(e.clientX - r.left - ND.view.x) / ND.view.k, (e.clientY - r.top - ND.view.y) / ND.view.k]; };
function ndDown(e) {
  if (e.button === 2) return;
  const t = e.target, ro = ndPhone();
  if (t.closest(".nd-palette")) return;
  if (ND.palette) ndClosePalette();
  const port = t.closest(".nd-port"), card = t.closest(".nd-node"), wire = t.closest("[data-edge]");
  if (!ND.space && !ro && port && port.dataset.dir === "out") {
    const node = ndNode(port.dataset.node), start = ndPortAt(node, "out", port.dataset.port);
    ND.link = { from: node.id, out: port.dataset.port, type: ndOutType(node, port.dataset.port), start, at: start };
    ND.stage.classList.add("linking"); ND.stage.dataset.link = ND.link.type; ndMarkPorts(); e.preventDefault(); return; }
  if (!ND.space && !ro && port && port.dataset.dir === "in") {  // pull a link off an input to move it elsewhere
    const edge = ND.graph.edges.findIndex(x => x.to === port.dataset.node && x.in === port.dataset.port);
    if (edge >= 0) { const x = ND.graph.edges[edge], a = ndNode(x.from), start = ndPortAt(a, "out", x.out); ND.graph.edges.splice(edge, 1); ndTouch();
      ND.link = { from: x.from, out: x.out, type: ndOutType(a, x.out), start, at: ndWorld(e) }; ND.stage.classList.add("linking"); ND.stage.dataset.link = ND.link.type;
      ndPaint(); ndMarkPorts(); e.preventDefault(); return; } }
  if (!ND.space && t.closest("input,textarea,select,button,a,video,audio,option")) return;
  if (!ND.space && !ro && card) { const node = ndNode(card.dataset.node), [wx, wy] = ndWorld(e);
    ND.sel = { node: node.id }; ND.drag = { id: node.id, dx: wx - node.x, dy: wy - node.y, moved: false };
    for (const c of Object.values(ND.cards)) c.classList.toggle("sel", c === card); ndPaintEdges(); e.preventDefault(); return; }
  if (!ND.space && !ro && wire) { ND.sel = { edge: Number(wire.dataset.edge) }; for (const c of Object.values(ND.cards)) c.classList.remove("sel"); ndPaintEdges(); return; }
  ND.pan = { x: e.clientX, y: e.clientY, vx: ND.view.x, vy: ND.view.y };
  if (ND.sel) { ND.sel = null; for (const c of Object.values(ND.cards)) c.classList.remove("sel"); ndPaintEdges(); }
  ND.stage.classList.add("panning"); }
function ndMarkPorts() { if (!ND.stage.querySelectorAll) return;
  for (const p of ND.stage.querySelectorAll(".nd-port.in")) p.classList.toggle("ok", !!ND.link && !ndCanConnect(ND.link.from, ND.link.out, p.dataset.node, p.dataset.port)); }
function ndMove(e) {
  if (!ND.stage) return;
  if (ND.link) { ND.link.at = ndWorld(e); ndPaintEdges(); return; }
  if (ND.drag) { const node = ndNode(ND.drag.id), [wx, wy] = ndWorld(e); if (!node) return;
    node.x = Math.round(wx - ND.drag.dx); node.y = Math.round(wy - ND.drag.dy); ND.drag.moved = true;
    const card = ND.cards[node.id]; if (card) { card.style.left = node.x + "px"; card.style.top = node.y + "px"; } ndPaintEdges(); return; }
  if (ND.pan) { ND.view.x = ND.pan.vx + e.clientX - ND.pan.x; ND.view.y = ND.pan.vy + e.clientY - ND.pan.y; ndTransform(); } }
function ndUp(e) {
  if (!ND.stage) return;
  if (ND.link) { const link = ND.link; ND.link = null; ND.stage.classList.remove("linking");
    const hit = document.elementFromPoint ? document.elementFromPoint(e.clientX, e.clientY) : null, port = hit && hit.closest && hit.closest(".nd-port.in");
    if (port) { const why = ndConnect(link.from, link.out, port.dataset.node, port.dataset.port); if (why) toast(why); ndPaint(); }
    else if (hit && ND.stage.contains(hit) && !hit.closest(".nd-node")) { const r = ND.stage.getBoundingClientRect(); ndPaintEdges(); ndPalette([e.clientX - r.left, e.clientY - r.top], link); }
    else ndPaintEdges();
    return; }
  if (ND.drag) { if (ND.drag.moved) { ndTouch(); ndPaintBar(); } ND.drag = null; return; }
  if (ND.pan) { ND.pan = null; ND.stage.classList.remove("panning"); } }
function ndWheel(e) {
  e.preventDefault();
  const r = ND.stage.getBoundingClientRect(), cx = e.clientX - r.left, cy = e.clientY - r.top;
  const k = Math.min(2, Math.max(0.4, ND.view.k * Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015))));
  ND.view.x = cx - (cx - ND.view.x) * k / ND.view.k; ND.view.y = cy - (cy - ND.view.y) * k / ND.view.k; ND.view.k = k; ndTransform(); }
function ndKey(e) {
  if (!ND.root || !ND.root.isConnected) return;
  const typing = e.target && e.target.matches && e.target.matches("input,textarea,select"), cmd = e.metaKey || e.ctrlKey;
  if (cmd && e.key.toLowerCase() === "s") { e.preventDefault(); ndSave(); return; }
  if (cmd && e.key === "Enter") { e.preventDefault(); ndRun(); return; }
  if (e.key === "Escape") { if (ND.palette) { ndClosePalette(); return; } if (ND.link) { ND.link = null; ND.stage.classList.remove("linking"); ndPaintEdges(); return; }
    if (ND.sel && !typing) { ND.sel = null; ndPaint(); } return; }
  if (typing) return;
  if ((e.key === "Backspace" || e.key === "Delete") && ND.sel && !ndPhone()) { e.preventDefault(); ndRemove(); return; }
  if (e.key === " " && !ND.space) { ND.space = true; if (ND.stage) ND.stage.classList.add("grab"); e.preventDefault(); } }
function ndKeyUp(e) { if (e.key === " ") { ND.space = false; if (ND.stage) ND.stage.classList.remove("grab"); } }
function ndBind() {
  if (ND.bound || typeof window === "undefined" || !window.addEventListener) return; ND.bound = true;
  window.addEventListener("pointermove", ndMove); window.addEventListener("pointerup", ndUp); window.addEventListener("pointercancel", ndUp);
  window.addEventListener("keydown", ndKey); window.addEventListener("keyup", ndKeyUp); }

// ---------- the view ----------
function viewNodes() {
  if (!ND.graph) ndLoad(ndBlank());
  if (!ND.asked) { ND.asked = true; ndList(); api("/api/nodes/runs").then(list => { if (!ND.run && Array.isArray(list) && list.length) { ND.run = list[0]; ndPaintBar(); ndPaintStatus(); ndWatch(); } }, () => {}); }
  ndMediaLists(); ndBind();
  const ro = ndPhone();
  ND.stage = el("div", { class: "nd-stage" + (ro ? " ro" : ""), tabindex: "0", "aria-label": "Node canvas",
    onpointerdown: ndDown, onwheel: ndWheel,
    oncontextmenu: e => { if (ro || e.target.closest(".nd-node,.nd-palette")) return; e.preventDefault(); const r = ND.stage.getBoundingClientRect(); ndPalette([e.clientX - r.left, e.clientY - r.top]); },
    ondblclick: e => { if (ro) return; const head = e.target.closest(".nd-head");
      if (head) { const node = ndNode(head.parentNode.dataset.node), name = prompt("Name this card", ndTitle(node)); if (name != null) { node.title = name.trim().slice(0, 60); ndTouch(); ndPaint(); } return; }
      if (e.target.closest(".nd-node,.nd-palette")) return; const r = ND.stage.getBoundingClientRect(); ndPalette([e.clientX - r.left, e.clientY - r.top]); } });
  ND.layer = el("div", { class: "nd-layer" }); ND.stage.append(ND.layer);
  const head = typeof pageHead === "function" ? pageHead("Studio Vault", "Nodes", "A graph of the studio's own jobs: words to pictures to clips, run in order.")
    : el("div", { class: "pagehead" }, el("h1", { class: "display" }, "Nodes"));
  ND.root = el("div", { class: "nodes" + (ro ? " ro" : "") }, head,
    ro ? el("div", { class: "note nd-phone" }, "Best on a desktop: on a phone the graph is read-only. Open one to see what it made.") : null,
    el("div", { class: "nd-bar" }), ND.stage,
    ro ? null : el("div", { class: "nd-help" }, "Drag the background to pan · wheel to zoom · drag from an output to an input to link · Backspace deletes · right-click adds a card · ⌘S saves · ⌘Enter runs"));
  ndPaint(); if (ND.run) ndPaintStatus(); ndWatch();
  if (ro) setTimeout(ndFit, 0);  // a phone sees the whole graph, and pans it with a finger
  return ND.root; }

if (typeof window !== "undefined") window.NODES_READY = true;
