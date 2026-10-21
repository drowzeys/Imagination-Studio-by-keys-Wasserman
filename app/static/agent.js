"use strict";
// The Agent room: pick your own agent (Claude Code, Codex, Hermes, Gemini, Grok or a local model), say what you want, and
// it works through the Studio's tools (stills, clips with sound, voice, songs, the timeline) in a conversation that keeps
// its session from one turn to the next. It asks when a choice matters; what it made gathers in Delivered.
const AG = { prompt: "", backend: null, tools: new Set(["blender", "studio"]), attach: [], tasks: null, timer: null, picking: false, lib: null, open: {},
  threads: null, cur: null, thread: null, reply: "", jobs: {}, seen: 0, focus: null, earlier: false, toolsOpen: {} };
const AG_SUGGEST = [
  ["Garage, 35 mm", "In Blender, build a rain-soaked parking garage at night with sodium lights. Put a camera at eye level with a 35 mm lens looking down the row, and render a 1344×768 still."],
  ["Blockout push-in", "In Blockout, stage one actor walking to a car, stopping and turning. Add a slow push-in on a 35 mm lens over 8 seconds, then export the motion package."],
  ["Turntable", "In Blender, import the attached model, light it with a three-point setup and render a 24-frame turntable as stills."],
  ["Stills to clips", "Look at the attached stills, write a one-line action for each, and queue an 8-second clip from each one in Studio."],
  ["A teaser", "Make a 10-second teaser for a quiet horror film: one still to set the place, a clip from it with sound, and a short sung sting under it. Ask me anything you need first."],
];
// the agents the room offers, in this order; what each is comes from /api/brains (agents.detect)
const AG_AGENTS = [["claude", "Claude Code"], ["codex", "Codex"], ["hermes", "Hermes"], ["gemini", "Gemini CLI"], ["grok", "Grok CLI"], ["local", "Local model"]];
const AG_HARNESS = ["claude", "codex"];
// a local model whose server takes tool calls is a harness too: the Studio runs its loop with the Studio's tools
const agIsHarness = id => AG_HARNESS.includes(id) || !!((BR.data && BR.data.backends) || []).find(b => b.id === id && b.harness);
const AG_LIVE = ["queued", "running"];

// the list leaves out each task's log: the open and running ones are read in full
const agentLoad = async () => { try { const r = await api("/api/agent/tasks"); AG.tasks = Array.isArray(r) ? r : (r.tasks || []); } catch { AG.tasks = []; }
  AG.tasks = await Promise.all(AG.tasks.slice(0, 20).map(async t => (AG.open[t.id] ?? AG_LIVE.includes(t.status)) && !t.thread ? await api("/api/agent/tasks/" + t.id).catch(() => t) : t)); };
const agThreadsLoad = async () => { try { const r = await api("/api/agent/threads"); AG.threads = Array.isArray(r) ? r : (r.threads || []); } catch { AG.threads = []; } };
const agThreadLoad = async id => { if (!id) { AG.thread = null; return; } try { const t = await api("/api/agent/threads/" + encodeURIComponent(id)); if (t && t.id) AG.thread = t; } catch { AG.thread = null; AG.cur = null; } };
const agBusy = () => (AG.threads || []).some(t => AG_LIVE.includes(t.status)) || !!(AG.thread && AG.thread.live) || (AG.tasks || []).some(t => !t.thread && AG_LIVE.includes(t.status));
const agShape = () => JSON.stringify([(AG.threads || []).map(t => [t.id, t.status, t.turns, t.waiting, t.updated]), AG.thread && [AG.thread.id, (AG.thread.turns || []).length, (AG.thread.queued || []).length,
  AG.thread.live && [AG.thread.live.status, (AG.thread.live.steps || []).length, (AG.thread.live.outputs || []).length]], (AG.tasks || []).map(t => [t.id, t.status])]);
// a redraw that keeps the box being typed in, and where its caret was
function agRender() {
  const at = typeof document !== "undefined" && document.activeElement, key = at && at.getAttribute && at.getAttribute("data-ag");
  const sel = key && typeof at.selectionStart === "number" ? [at.selectionStart, at.selectionEnd] : null;
  render();
  if (key && typeof document.querySelector === "function") { const n = document.querySelector('[data-ag="' + key + '"]'); if (n && n.focus) { n.focus(); if (sel && n.setSelectionRange) n.setSelectionRange(sel[0], sel[1]); } }
}
function agIdleWatch() {
  if (!AG.idle) AG.idle = setInterval(async () => { if (tab !== "Agent") { clearInterval(AG.idle); AG.idle = null; return; } if (AG.timer) return;
    const before = agShape(); await agThreadsLoad(); if (AG.cur) await agThreadLoad(AG.cur); if (agShape() !== before) agRender(); if (agBusy()) agentWatch(); }, 10000);
  if (AG.idle && AG.idle.unref) AG.idle.unref();  // (node, in the tests: never keeps the process alive)
  if (!AG.focusHooked && typeof window !== "undefined" && window.addEventListener) { AG.focusHooked = true;
    window.addEventListener("focus", async () => { if (tab !== "Agent" || AG.timer) return; const before = agShape(); await agThreadsLoad(); if (AG.cur) await agThreadLoad(AG.cur);
      if (agShape() !== before) agRender(); if (agBusy()) agentWatch(); }); } }
function agentWatch() { if (AG.timer) return; AG.timer = setInterval(async () => { if (tab !== "Agent") { clearInterval(AG.timer); AG.timer = null; return; }
  const before = agShape(); await agThreadsLoad(); if (AG.cur) await agThreadLoad(AG.cur); if ((AG.tasks || []).some(t => !t.thread && AG_LIVE.includes(t.status))) await agentLoad();
  if (agShape() !== before) agRender();
  if (!agBusy()) { clearInterval(AG.timer); AG.timer = null; } }, 1500); }
function useOutput(path, how) {
  if (how === "first") { Object.assign(C, { image: path, goes: "video", start: "own", step: 1, prompt: "" }); }
  else if (how === "last") { Object.assign(C, { lastImage: path, goes: "video", step: 1, prompt: "" }); if (C.start === "refs") C.start = "text"; }
  else if (how === "ref") { Object.assign(C, { goes: "video", start: "refs", step: 1, prompt: "" }); if (/\.(mp4|mov)$/i.test(path)) { if (!C.refVideos.includes(path)) { C.refVideos.push(path); C.video_subjects.push(""); } } else if (!C.refs.includes(path)) { C.refs.push(path); C.subjects.push(""); } }
  sync(); go("Create"); toast(how === "ref" ? "Added as a reference" : how === "first" ? "Set as the first frame" : "Set as the last frame"); }
// a brain's mark beside its name, where it has one (shell.js MARKS); a letter where it has none
const agMark = id => { const k = typeof brainMarkOf === "function" ? brainMarkOf(id) || id : id;
  return typeof mark === "function" && typeof MARKS !== "undefined" && MARKS[k] ? mark(k, "") : null; };
const agBadge = id => agMark(id) || el("span", { class: "agr-mono", "aria-hidden": "true" }, id === "local" ? "◎" : (id || "?").slice(0, 1).toUpperCase());
// a brain a task can go to: found on this machine, or run over SSH on the computer named in Brains & tools, that machine
// reached, and not known to be signed out (the CLI's own word, as Settings → Connections shows it: BR.status, machine.js)
const AG_NOT_SIGNED = { signed_out: "Not signed in", broken: "Cannot start", missing: "Not found" };
const agSignState = b => { const st = b && typeof BR !== "undefined" && BR.status ? BR.status[b.id] : null; return st && AG_NOT_SIGNED[st.state] ? st : null; };
const agReady = b => !!b && !b.error && (!!b.found || /^ssh:./.test(b.where || "")) && !agSignState(b);
// each found CLI's sign-in, asked once (kept with Settings' own answers) and the room redrawn when it comes
function agAskSignins(found) { if (typeof brainStatus !== "function") return;
  for (const b of found) if (b.kind === "cli" && b.found && !b.error && !BR.status[b.id]) brainStatus(b.id).then(() => { if (tab === "Agent") agRender(); }).catch(() => {}); }
// Settings → Connections where the Settings page is there, else Machine → Brains & tools
const agSettings = () => typeof viewSettings === "function" && typeof TABS !== "undefined" && TABS.includes("Settings");
// the room's own way to fix it: Connect opens the checklist here; every connection lives in Settings → Connections too
const agSetup = () => { const want = ((BR.data && BR.data.backends) || []).find(b => AG_HARNESS.includes(b.id) && !agReady(b));
  return el("span", { class: "ag-fixes" }, want && typeof connectOpen === "function" ? el("button", { type: "button", class: "small on", onclick: () => connectOpen(want.id) }, "Connect " + (want.label || agName(want.id))) : null,
    el("a", { href: agSettings() ? "#Settings" : "#Brains", class: "ag-setup", onclick: e => { if (e && e.preventDefault) e.preventDefault(); if (agSettings() && typeof SET !== "undefined") SET.section = "connections"; go(agSettings() ? "Settings" : "Brains"); } },
      agSettings() ? "Set up in Settings → Connections" : "Set up on Machine → Brains & tools")); };
const agName = id => { const b = ((BR.data && BR.data.backends) || []).find(x => x.id === id); return (b && b.label) || (AG_AGENTS.find(a => a[0] === id) || [id, id])[1]; };
const agAgo = s => { if (!s) return ""; const d = Date.now() / 1000 - s; return d < 60 ? "just now" : d < 3600 ? Math.round(d / 60) + " min ago" : d < 86400 ? Math.round(d / 3600) + " h ago" : new Date(s * 1000).toLocaleDateString(); };
const agPic = p => /\.(png|jpe?g|webp|mp4|mov)$/i.test(p), agSound = p => /\.(wav|mp3|m4a|aac|flac|ogg)$/i.test(p);

function taskCard(t) {
  const outs = t.outputs || [], open = AG.open[t.id] ?? AG_LIVE.includes(t.status);
  const pics = outs.filter(p => /\.(png|jpe?g|webp|mp4|mov)$/i.test(p)), others = outs.filter(p => !pics.includes(p));
  return el("div", { class: "task" },
    el("div", { class: "seghead" }, el("span", { class: "pill " + t.status }, t.status), el("b", { class: "ag-prompt" }, t.prompt),
      el("span", { class: "tag2" }, agMark(t.backend), ({ claude: "Claude Code", codex: "Codex" })[t.backend] || t.backend), (t.tools || []).map(x => el("span", { class: "tag2" }, x)),
      AG_LIVE.includes(t.status) ? el("button", { class: "small", onclick: async () => { try { await api("/api/agent/tasks/" + t.id + "/cancel", {}); await agentLoad(); render(); } catch (e) { toast(e.message); } } }, "Stop") : null,
      el("button", { class: "small", onclick: async () => { AG.open[t.id] = !open; if (!open) { const full = await api("/api/agent/tasks/" + t.id).catch(() => null); if (full) Object.assign(t, full); } render(); } }, open ? "Hide log" : "Log")),
    t.reply ? el("div", { class: "ag-reply" }, t.reply) : null,
    t.error ? el("div", { class: "note vt-bad m-flat" }, relHome(t.error), /not installed on this machine/.test(t.error) ? [" · ", agSetup()] : null) : null,
    open && t.log ? el("div", { class: "log" }, relHome(t.log).slice(-6000)) : null,
    pics.length ? el("div", { class: "grid" }, pics.map(p => el("div", { class: "tile" }, media(p), el("div", { class: "cap ag-cap" },
      el("span", { class: "ag-name" }, p.split("/").pop()),
      el("button", { class: "small", onclick: () => useOutput(p, "first") }, "first frame"), /\.(png|jpe?g|webp)$/i.test(p) ? el("button", { class: "small", onclick: () => useOutput(p, "last") }, "last frame") : null,
      S.ref2va ? el("button", { class: "small", onclick: () => useOutput(p, "ref") }, "reference") : null,
      /\.(mp4|mov)$/i.test(p) ? el("button", { class: "small", onclick: () => addToTimeline(p) }, "+ timeline") : null)))) : null,
    others.length ? el("div", { class: "chips" }, others.map(p => el("a", { class: "tag2", href: file(p), download: "" }, p.split("/").pop()))) : null,
    el("div", { class: "kv" }, el("span", {}, t.started ? new Date(t.started * 1000).toLocaleTimeString() : "waiting"), el("span", {}, t.finished && t.started ? "took " + mmss(t.finished - t.started) : outs.length ? outs.length + " files" : "")));
}

// ---------- the room's own look (scoped to .agr-*, from the studio's tokens) ----------
const AG_CSS = `
.agr-cards { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:10px; }
.agr-card { display:flex; flex-direction:column; align-items:flex-start; gap:6px; text-align:left; padding:12px 12px 11px; border-radius:var(--r2); border:1px solid var(--line); background:var(--raise); box-shadow:var(--sh-card); color:var(--text); min-height:96px; cursor:pointer; font:inherit; }
.agr-card:hover { border-color:var(--line3); background:var(--raise2); }
.agr-card.on { border-color:var(--blue); box-shadow:inset 0 0 0 1px var(--blue),var(--sh-card); }
.agr-card[disabled] { cursor:default; opacity:.55; box-shadow:none; background:transparent; }
.agr-card .agr-top { display:flex; align-items:center; gap:8px; font-weight:600; font-size:14.5px; }
.agr-card .agr-top .mk img { width:20px; height:20px; }
.agr-card .agr-state { display:flex; align-items:center; gap:6px; font:500 11.5px var(--mono); letter-spacing:.04em; color:var(--dim); max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.agr-card .agr-can { font:500 11px var(--mono); letter-spacing:.06em; text-transform:uppercase; color:var(--faint); }
.agr-card .agr-can.t { color:var(--green); }
.agr-mono { display:inline-grid; place-items:center; width:20px; height:20px; border-radius:6px; background:var(--raise2); border:1px solid var(--line2); font:600 11.5px var(--mono); color:var(--text); flex:none; }
.agr-under { display:flex; flex-wrap:wrap; gap:8px 14px; align-items:center; margin:10px 2px 0; color:var(--dim); font-size:13.5px; }
.agr-under .toggle { padding:4px 11px 4px 9px; font-size:13px; }
.agr-room { display:grid; grid-template-columns:268px minmax(0,1fr); gap:16px; margin-top:20px; align-items:start; }
.agr-list { display:flex; flex-direction:column; gap:6px; position:sticky; top:72px; max-height:calc(100vh - 150px); overflow:auto; padding-right:2px; }
.agr-item { display:flex; flex-direction:column; gap:3px; text-align:left; padding:10px 11px; border-radius:var(--r2); border:1px solid transparent; background:transparent; color:var(--text); cursor:pointer; font:inherit; box-shadow:none; }
.agr-item:hover { background:var(--raise); border-color:var(--line); }
.agr-item.on { background:var(--raise); border-color:var(--blue); }
.agr-item .agr-t { display:flex; align-items:center; gap:7px; font-weight:600; font-size:13.5px; min-width:0; }
.agr-item .agr-t > span:last-child { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.agr-item .agr-p { color:var(--dim); font-size:12.5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.agr-item .agr-m { display:flex; gap:6px; align-items:center; font:500 11px var(--mono); color:var(--faint); letter-spacing:.04em; }
.agr-need { color:var(--amber); border:1px solid rgba(241,173,102,.45); border-radius:999px; padding:0 7px; font:600 10.5px var(--mono); letter-spacing:.06em; text-transform:uppercase; }
.agr-convo { min-width:0; display:flex; flex-direction:column; gap:12px; }
.agr-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.agr-head h2 { margin:0; font-size:19px; font-weight:600; min-width:0; overflow:hidden; text-overflow:ellipsis; }
.agr-turns { display:flex; flex-direction:column; gap:10px; }
.agr-user { align-self:flex-end; max-width:78%; background:var(--raise2); border:1px solid var(--line2); border-radius:14px 14px 4px 14px; padding:10px 14px; white-space:pre-wrap; overflow-wrap:anywhere; font-size:15px; }
.agr-agent { align-self:stretch; display:grid; grid-template-columns:28px minmax(0,1fr); gap:10px; }
.agr-agent .agr-body { background:var(--panel); border:1px solid var(--line); border-radius:4px 14px 14px 14px; padding:11px 14px; font-size:15px; line-height:1.55; overflow-wrap:anywhere; }
.agr-body p { margin:0 0 8px; } .agr-body p:last-child { margin-bottom:0; } .agr-body ul { margin:4px 0 8px 18px; padding:0; } .agr-body code { font:12.5px var(--mono); background:var(--raise2); padding:1px 5px; border-radius:4px; }
.agr-who { padding-top:6px; }
.agr-steps { margin-left:38px; display:flex; flex-direction:column; gap:2px; font:12px var(--mono); color:var(--dim); border-left:1px solid var(--line2); padding:2px 0 2px 12px; }
.agr-steps .agr-more { color:var(--faint); cursor:pointer; background:none; border:0; padding:0; font:inherit; text-align:left; box-shadow:none; }
.agr-working { display:flex; align-items:center; gap:8px; color:var(--text); font:500 12.5px var(--mono); letter-spacing:.04em; }
.agr-files { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
.agr-thumb { width:148px; border-radius:var(--r1); overflow:hidden; border:1px solid var(--line); background:#000; }
.agr-thumb img, .agr-thumb video { width:100%; aspect-ratio:16/9; object-fit:cover; display:block; }
.agr-thumb audio { width:100%; display:block; }
.agr-thumb .agr-fn { font:11px var(--mono); color:var(--dim); padding:4px 6px; background:var(--panel); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.agr-q { border:1px solid var(--blue); border-radius:var(--r3); background:var(--panel); padding:14px 16px; box-shadow:inset 0 0 0 1px rgba(6,102,255,.35),var(--sh-card); display:flex; flex-direction:column; gap:10px; }
.agr-q .eyebrow { color:var(--blue-text, var(--blue)); }
.agr-q .agr-qtext { font-size:16px; line-height:1.5; }
.agr-opts { display:flex; flex-wrap:wrap; gap:8px; }
.agr-box { border-radius:var(--r3); border:1px solid var(--line2); background:var(--bg2); padding:4px; }
.agr-box:focus-within { border-color:var(--blue); }
.agr-box textarea { border:0; background:transparent; box-shadow:none; min-height:64px; font-size:15.5px; padding:11px 12px 6px; width:100%; resize:vertical; }
.agr-box textarea:focus { background:transparent; }
.agr-bar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; padding:6px 8px 4px; border-top:1px solid var(--line); }
.agr-deliv { border:1px solid var(--line); border-radius:var(--r3); background:var(--panel); padding:10px 12px 12px; }
.agr-deliv .agr-strip { display:flex; gap:10px; overflow-x:auto; padding-top:8px; scroll-snap-type:x proximity; }
.agr-dtile { flex:none; width:196px; border-radius:var(--r2); overflow:hidden; border:1px solid var(--line); background:var(--raise); scroll-snap-align:start; }
.agr-dtile img, .agr-dtile video { width:100%; aspect-ratio:16/9; object-fit:cover; display:block; background:#000; }
.agr-dtile audio { width:100%; display:block; margin:8px 0 0; }
.agr-dtile .agr-dcap { padding:6px 8px 8px; display:flex; flex-wrap:wrap; gap:5px; align-items:center; }
.agr-dtile .agr-dcap > span { flex:1 0 100%; font:11.5px var(--mono); color:var(--dim); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.agr-dtile .agr-dcap button { padding:2px 8px; font-size:12px; }
.agr-err { color:var(--red); font-size:13.5px; }
.agr-queued { opacity:.7; border-style:dashed; }
.agr-end { height:8px; scroll-margin-bottom:96px; }
.agr-empty { padding:18px; color:var(--dim); font-size:14px; border:1px dashed var(--line2); border-radius:var(--r2); }
@media (max-width:1180px) { .agr-cards { grid-template-columns:repeat(3,minmax(0,1fr)); } }
@media (max-width:760px) {
  .agr-cards { display:flex; overflow-x:auto; scroll-snap-type:x proximity; padding-bottom:4px; }
  .agr-card { flex:none; width:158px; scroll-snap-align:start; min-height:88px; }
  .agr-room { grid-template-columns:minmax(0,1fr); gap:12px; }
  .agr-list { position:static; flex-direction:row; align-items:flex-start; overflow-x:auto; max-height:none; gap:6px; padding-bottom:4px; }
  .agr-item { flex:none; width:224px; border-color:var(--line); }
  .agr-item:first-child { width:auto; }
  .agr-item .agr-m { white-space:nowrap; }
  .agr-end { scroll-margin-bottom:190px; } .agr-q .kv .note { display:none; }
  .agr-user { max-width:92%; }
  .agr-agent { grid-template-columns:minmax(0,1fr); } .agr-who { display:none; } .agr-steps { margin-left:4px; }
  .agr-dtile { width:168px; }
}`;
function agStyle() { if (typeof document === "undefined" || !document.head || !document.createElement || document.getElementById("agr-css")) return;
  const s = document.createElement("style"); s.id = "agr-css"; s.textContent = AG_CSS; document.head.append(s); }

// ---------- pieces ----------
// an agent's words as paragraphs and lists, built as nodes (never as HTML)
function agText(text) {
  const inline = line => String(line).split(/(\*\*[^*]+\*\*|`[^`]+`)/).filter(Boolean).map(part =>
    /^\*\*[^*]+\*\*$/.test(part) ? el("b", {}, part.slice(2, -2)) : /^`[^`]+`$/.test(part) ? el("code", {}, part.slice(1, -1)) : part);
  return String(text || "").split(/\n{2,}/).map(block => { const lines = block.split("\n").filter(l => l.trim());
    if (lines.length && lines.every(l => /^\s*(?:[-*•]|\d{1,2}[.)])\s+/.test(l))) return el("ul", {}, lines.map(l => el("li", {}, ...inline(l.replace(/^\s*(?:[-*•]|\d{1,2}[.)])\s+/, "")))));
    // the page's el() flattens one level: every child goes in as its own argument
    return el("p", {}, ...lines.flatMap((l, i) => [i ? el("br") : null, ...inline(l.replace(/^#{1,4}\s+/, ""))])); });
}
function agThumb(p) { return el("a", { class: "agr-thumb", href: file(p), target: "_blank", rel: "noopener", title: p.split("/").pop() },
  agSound(p) ? media(p) : media(p, { controls: false, muted: true }), el("div", { class: "agr-fn" }, p.split("/").pop())); }
// what a turn's Studio jobs made: from the render bay's list, else asked once
function agJobOutputs(ids) {
  const out = [];
  for (const id of ids || []) { const j = (typeof jobs !== "undefined" && Array.isArray(jobs) && jobs.find(x => x.id === id)) || AG.jobs[id];
    if (j && j.outputs) out.push(...j.outputs);
    if ((!j || AG_LIVE.includes(j.status)) && !AG.jobs["?" + id]) { AG.jobs["?" + id] = true;
      api("/api/jobs/" + encodeURIComponent(id)).then(r => { AG.jobs[id] = r; if (AG_LIVE.includes(r.status)) setTimeout(() => { delete AG.jobs["?" + id]; }, 4000); if (tab === "Agent") agRender(); }).catch(() => {}); } }
  return out;
}
function agDelivered(thread) {
  const seen = new Set(), files = [];
  for (const t of thread.turns || []) if (t.role === "agent") for (const p of [...(t.attachments || []), ...agJobOutputs(t.jobs)]) if (!seen.has(p) && (agPic(p) || agSound(p) || /\.(glb|blend|json|zip|fcpxml|edl)$/i.test(p))) { seen.add(p); files.push(p); }
  if (thread.live) for (const p of [...(thread.live.outputs || []), ...agJobOutputs(thread.live.jobs)]) if (!seen.has(p)) { seen.add(p); files.push(p); }
  if (!files.length) return null;
  const toLibrary = p => { if (typeof LIBF !== "undefined") LIBF.q = p.split("/").pop(); go("Library"); };
  return el("div", { class: "agr-deliv" }, el("div", { class: "kv" }, el("span", { class: "eyebrow" }, "Delivered · " + files.length), files.some(agPic) ? el("span", { class: "note m-flat" }, files.every(agPic) ? "Everything here is in the Library too" : "The stills and clips here are in the Library too") : null),
    el("div", { class: "agr-strip" }, files.map(p => el("div", { class: "agr-dtile" }, agSound(p) ? el("div", { class: "agr-audio" }, media(p)) : agPic(p) ? media(p, { controls: /\.(mp4|mov)$/i.test(p), preload: "metadata" }) : null,
      el("div", { class: "agr-dcap" }, el("span", {}, p.split("/").pop()),
        agPic(p) || agSound(p) ? el("button", { class: "small", onclick: () => toLibrary(p) }, "Open in Library") : el("a", { class: "tag2", href: file(p), download: "" }, "Download"),
        /\.(mp4|mov)$/i.test(p) ? el("button", { class: "small", onclick: () => addToTimeline(p) }, "+ timeline") : null,
        /\.(png|jpe?g|webp)$/i.test(p) ? el("button", { class: "small", onclick: () => useOutput(p, "first") }, "Animate") : null)))));
}
function agSteps(lines, key, live) {
  if (!lines.length) return null;
  const all = AG.toolsOpen[key], shown = all || live ? lines.slice(live ? -6 : 0) : lines.slice(-3);
  return el("div", { class: "agr-steps" }, !all && lines.length > shown.length ? el("button", { class: "agr-more", onclick: () => { AG.toolsOpen[key] = true; agRender(); } }, "… " + (lines.length - shown.length) + " earlier steps") : null,
    shown.map(s => el("div", {}, s)));
}
function agTurns(thread) {
  const out = [], turns = thread.turns || [];
  for (let i = 0; i < turns.length; i++) { const t = turns[i];
    if (t.role === "user") out.push(el("div", { class: "agr-user" }, t.text, (t.attachments || []).length ? el("div", { class: "agr-files" }, t.attachments.map(agThumb)) : null));
    else if (t.role === "tool") { const run = []; while (i < turns.length && turns[i].role === "tool") run.push(turns[i++].text); i--; out.push(agSteps(run, t.id, false)); }
    else { const last = i === turns.length - 1, asking = last && t.question && !thread.live;
      out.push(el("div", { class: "agr-agent" }, el("div", { class: "agr-who" }, agBadge(t.backend || thread.backend)),
        el("div", { class: "agr-body" }, t.status === "cancelled" ? el("span", { class: "note m-flat" }, "Stopped.") : null,
          asking ? el("p", { class: "note m-flat" }, "Asked below.") : t.text ? agText(t.text) : null,
          t.error ? el("div", { class: "agr-err" }, relHome(t.error), /not installed|signed in|sign-in|setup|ssh|Settings → Connections/.test(t.error) ? [" · ", agSetup()] : null) : null,
          (t.attachments || []).filter(p => agPic(p) || agSound(p)).length ? el("div", { class: "agr-files" }, t.attachments.filter(p => agPic(p) || agSound(p)).slice(0, 8).map(agThumb)) : null))); } }
  return out;
}
function agLive(thread) {
  const l = thread.live; if (!l) return null;
  const made = [...(l.outputs || []), ...agJobOutputs(l.jobs)].filter(p => agPic(p) || agSound(p));
  return el("div", { class: "agr-agent agr-livebox" }, el("div", { class: "agr-who" }, agBadge(l.backend)),
    el("div", { class: "agr-body" }, el("div", { class: "agr-working" }, el("span", { class: "dotl" + (l.status === "running" ? " r" : " t") }),
      l.status === "queued" ? "Waiting its turn in the agent queue…" : agName(l.backend) + " is working…", el("span", { class: "su-grow" }),
      el("button", { class: "small", onclick: () => agStop(thread.id) }, "Stop")),
      (l.steps || []).length ? el("div", { class: "agr-steps", style: "margin:8px 0 0" }, (l.steps || []).slice(-6).map(s => el("div", {}, s.text))) : null,
      made.length ? el("div", { class: "agr-files" }, made.slice(-8).map(agThumb)) : null));
}
async function agStop(id) { try { await api("/api/agent/threads/" + encodeURIComponent(id) + "/cancel", {}); await agThreadLoad(id); await agThreadsLoad(); agRender(); } catch (e) { toast(e.message); } }

// ---------- sending ----------
const agBrain = id => ((BR.data && BR.data.backends) || []).find(b => b.id === id);
function agChosen() { return agBrain(AG.backend); }
// the agent a message goes to: an open conversation's own, else the card chosen above
const agTarget = how => how !== "start" && AG.cur && AG.thread ? AG.thread.backend : AG.backend;
async function agSend(text, how) {
  text = String(text || "").trim(); if (!text) return toast(how === "reply" ? "Write your answer first" : "Say what you need first");
  const id = agTarget(how), brain = agBrain(id), st = agSignState(brain);
  if (!agReady(brain)) return toast((brain ? brain.label : agName(id) || "No agent") + (st ? " is " + st.state.replace("_", " ") : " is not set up here") + ": " + (agSettings() ? "Settings → Connections" : "Machine → Brains & tools"));
  try {
    if (how !== "start" && AG.cur && AG.thread) { const r = await api("/api/agent/threads/" + encodeURIComponent(AG.cur) + "/messages", { text, backend: id, attach: AG.attach }); if (r && r.thread) AG.thread = r.thread; AG.reply = ""; }
    else { const r = await api("/api/agent/tasks", { prompt: text, backend: id, tools: agIsHarness(id) ? [...AG.tools] : [], attach: AG.attach }); AG.prompt = ""; if (r && r.thread) { AG.cur = r.thread; await agThreadLoad(AG.cur); } }
    AG.attach = []; AG.picking = false; AG.seen = -1; await agThreadsLoad(); agentWatch(); render(); toast((AG.thread && AG.thread.live && how !== "start" && (AG.thread.queued || []).length ? "Queued for " : "Sent to ") + agName(id));
  } catch (e) { toast(e.message); }
}
function agComposer(key, value, set, placeholder, how, disabled) {
  const area = el("textarea", { placeholder, "aria-label": how === "reply" ? "Your answer" : "Your message", "data-ag": key, rows: how === "start" ? 4 : 2 });
  area.value = value; area.addEventListener("input", () => set(area.value));
  area.addEventListener("keydown", e => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") agSend(area.value, how); });
  const id = agTarget(how), brain = agBrain(id), ready = agReady(brain), st = agSignState(brain);
  return el("div", { class: "agr-box" + (how === "start" ? " agentbox" : "") }, area,
    !ready && how !== "start" && brain ? el("div", { class: "note m-flat agr-err" }, agName(id) + (st ? ": " + (AG_NOT_SIGNED[st.state] || st.state) : brain.error ? ": " + relHome(brain.error) : " is not set up here") + " · ", agSetup()) : null,
    AG.attach.length ? el("div", { class: "chips ag-attached" }, AG.attach.map(p => el("span", { class: "tag2 t" }, p.split("/").pop(), el("a", { href: "#", class: "ag-x", "aria-label": "Remove", onclick: e => { e.preventDefault(); AG.attach = AG.attach.filter(x => x !== p); render(); } }, "×")))) : null,
    el("div", { class: "agr-bar" }, el("span", { class: "tag2" }, agBadge(id), agName(id)),
      how === "start" ? (agIsHarness(id) && AG.tools.size ? el("span", { class: "note m-flat" }, "with " + [...AG.tools].join(", ")) : null)
        : AG.thread && (AG.thread.tools || []).length ? el("span", { class: "note m-flat" }, "with " + AG.thread.tools.join(", ")) : null,
      el("span", { class: "su-grow" }),
      el("button", { class: "small", onclick: () => { AG.picking = !AG.picking; render(); } }, "Attach" + (AG.attach.length ? " (" + AG.attach.length + ")" : "")),
      el("button", { class: "primary", disabled: !ready || !!disabled, onclick: () => agSend(area.value || (how === "start" ? AG.prompt : AG.reply), how) }, how === "reply" ? "Send answer " : "Send ", el("kbd", { class: "ag-kbd" }, "⌘⏎"))));
}
function agQuestion(thread, t) {
  return el("div", { class: "agr-q", role: "group", "aria-label": "Your agent asks" },
    el("div", { class: "kv" }, el("span", { class: "eyebrow" }, agName(t.backend || thread.backend) + " asks"), el("span", { class: "note m-flat" }, "Answer and it carries on")),
    el("div", { class: "agr-qtext" }, agText(t.text)),
    (t.options || []).length ? el("div", { class: "agr-opts" }, t.options.map(o => el("button", { class: "toggle", onclick: () => agSend(o, "reply") }, o))) : null,
    agComposer("reply", AG.reply, v => { AG.reply = v; }, "Your answer…", "reply"));
}

// ---------- the room ----------
function agChooser(found) {
  return el("div", { class: "agr-cards", role: "radiogroup", "aria-label": "Choose your agent" }, AG_AGENTS.map(([id, name]) => {
    const b = found.find(x => x.id === id), ready = agReady(b), on = ready && AG.backend === id, remote = b && /^ssh:./.test(b.where || ""), st = agSignState(b);
    const state = !b ? "Not found" : b.error ? "Not reached" : st ? AG_NOT_SIGNED[st.state] : remote ? "On " + b.where.slice(4).replace(/^[^@]*@/, "") : b.found ? "Ready" + (b.version ? " · " + String(b.version).replace(/^[^0-9]*/, "").split(/[\s(]/)[0] : "") : "Not found";
    const can = agIsHarness(id) ? "Uses the Studio" : "Conversation";
    // an agent that is not ready is one click from ready: its card opens the Connect checklist over the room
    const fix = id !== "local" && typeof connectOpen === "function";
    return el("button", { class: "agr-card" + (on ? " on" : "") + (ready ? "" : " off"), role: "radio", "aria-checked": on ? "true" : "false", disabled: !ready && !fix, "aria-disabled": ready ? false : "true",
      title: ready ? (b.label || name) + (b && b.path ? " · " + relHome(b.path) : "") : ((b && b.error) || (st && [st.detail, st.hint].filter(Boolean).join(" ")) || (b && b.signin) || (name + " is not found on this machine and no other computer is set up for it")) +
        (fix ? " · Click to connect it: the studio installs it if needed, signs you in and checks it answers." : ""),
      onclick: () => { if (!ready) { if (fix) connectOpen(id); else if (typeof goSettings === "function") goSettings("connections"); return; } AG.backend = id; render(); } },
      el("span", { class: "agr-top" }, agBadge(id), (b && b.label && (!["codex", "local"].includes(id) || b.named) ? b.label : name)),
      el("span", { class: "agr-state" }, el("span", { class: "dotl" + (ready ? (on ? " t" : "") : " o") }), state),
      !ready && fix ? el("span", { class: "agr-can agr-fix" }, "Click to connect") : el("span", { class: "agr-can" + (ready && agIsHarness(id) ? " t" : "") }, can)); }));
}
function agPicker() {
  if (!AG.picking) return null; if (!AG.lib) api("/api/library").then(l => { AG.lib = l; render(); }).catch(() => {});
  return el("div", { class: "panel ag-pick" }, el("div", { class: "kv" }, el("b", { class: "ag-h" }, "Attach from the Library"), el("button", { class: "small", onclick: () => { AG.picking = false; render(); } }, "Done")),
    el("div", { class: "grid ag-grid" }, (AG.lib || []).slice(0, 36).map(m => el("div", { class: "tile ag-tile" + (AG.attach.includes(m.path) ? " on" : ""), onclick: () => { const i = AG.attach.indexOf(m.path); i < 0 ? AG.attach.push(m.path) : AG.attach.splice(i, 1); render(); } },
      m.kind === "audio" ? el("div", { class: "ag-audio" }, icon("sound")) : media(m.path, { controls: false, muted: true }), el("div", { class: "cap" }, m.path.split("/").pop())))));
}
function agList() {
  const list = AG.threads || [];
  return el("nav", { class: "agr-list", "aria-label": "Conversations" },
    el("button", { class: "agr-item" + (!AG.cur ? " on" : ""), onclick: () => { AG.cur = null; AG.thread = null; render(); } }, el("span", { class: "agr-t" }, el("span", { class: "agr-mono" }, "+"), el("span", {}, "New conversation"))),
    AG.threads === null ? el("div", { class: "note" }, "Loading…") : list.map(t => el("button", { class: "agr-item" + (AG.cur === t.id ? " on" : ""), "data-thread": t.id, onclick: async () => { AG.cur = t.id; AG.seen = -1; await agThreadLoad(t.id);
        const b = ((BR.data && BR.data.backends) || []).find(x => x.id === t.backend); if (agReady(b)) AG.backend = t.backend; if (agBusy()) agentWatch(); render(); } },
      el("span", { class: "agr-t" }, agBadge(t.backend), el("span", {}, t.title)),
      t.last ? el("span", { class: "agr-p" }, t.last) : null,
      el("span", { class: "agr-m" }, AG_LIVE.includes(t.status) ? [el("span", { class: "dotl r" }), "working"] : t.waiting ? el("span", { class: "agr-need" }, "Needs you") : null,
        el("span", {}, agAgo(t.updated)), t.delivered ? el("span", {}, "· " + t.delivered + " files") : null))));
}
function agConversation(thread) {
  const turns = thread.turns || [], last = turns[turns.length - 1];
  const asking = last && last.role === "agent" && last.question && !thread.live;
  return el("section", { class: "agr-convo", "aria-label": "Conversation" },
    el("div", { class: "agr-head" }, el("h2", {}, thread.title), el("span", { class: "tag2" }, agBadge(thread.backend), agName(thread.backend)),
      thread.session ? el("span", { class: "tag2 ok", title: "The next message continues the agent's own session" }, "session kept") : null,
      el("span", { class: "su-grow" }),
      !thread.live ? el("button", { class: "small", onclick: async () => { if (typeof confirm === "function" && !confirm("Forget this conversation? What it made stays in the Library.")) return;
        try { await api("/api/agent/threads/" + encodeURIComponent(thread.id), undefined, "DELETE"); AG.cur = null; AG.thread = null; await agThreadsLoad(); render(); } catch (e) { toast(e.message); } } }, "Forget") : null),
    agDelivered(thread),
    el("div", { class: "agr-turns" }, agTurns(thread), agLive(thread),
      (thread.queued || []).map(q => el("div", { class: "agr-user agr-queued", title: "Sent while the agent works: it goes when this turn ends" }, el("span", { class: "note m-flat" }, "Queued · "), q.text))),
    asking ? agQuestion(thread, last) : agComposer("next", AG.reply, v => { AG.reply = v; }, thread.live ? "Your agent is working. Write the next thing; it goes when this turn ends." : "Reply, or ask for the next thing…", "next", (thread.queued || []).length >= 3),
    agPicker(), el("div", { class: "agr-end" }));
}
// each harness that cannot start a conversation, said once and as it is: installed but signed out (or failing to start)
// is not "not on this machine", which only one that was not found is
function agWhyNot(missing) {
  const said = {};
  missing.forEach(b => { const st = agSignState(b), why = st ? (AG_NOT_SIGNED[st.state] || st.state).toLowerCase()
    : b.error ? "not reached" : b.found || /^ssh:./.test(b.where || "") ? "not ready" : "not on this machine"; (said[why] = said[why] || []).push(b.label); });
  return Object.entries(said).map(([why, names]) => names.join(" and ") + (names.length > 1 ? " are " : " is ") + why + ".").join(" "); }
function agStart(missing) {
  return el("section", { class: "agr-convo", "aria-label": "New conversation" },
    agComposer("start", AG.prompt, v => { AG.prompt = v; }, "Describe the shot, the scene or the film. “Make a 10-second clip of a diner at night with rain on the window, then a sung sting under it.”", "start"),
    missing.length ? el("div", { class: "note ag-hint" }, agWhyNot(missing), " ", agSetup()) : null,
    agPicker(),
    el("div", { class: "suggest" }, AG_SUGGEST.map(([k, text]) => el("button", { onclick: () => { AG.prompt = text; if (/Blockout/.test(text)) AG.tools.add("blockout"); if (/Blender/.test(text)) AG.tools.add("blender"); if (/Studio|clip|still|sting/.test(text)) AG.tools.add("studio"); render(); } }, k))),
    el("div", { class: "agr-empty" }, "Your agent works through the Studio's own tools: stills, clips with sound, voice lines, songs and the timeline. When something matters (the length, the look, a voice, which take) it asks you here first."));
}
function viewAgent() {
  agStyle();
  if (!BR.data) brainsLoad().then(() => { if (tab === "Agent") render(); });
  if (AG.tasks === null && !AG.loading) { AG.loading = true; agentLoad().then(() => { AG.loading = false; if (tab === "Agent") render(); }); }
  if (AG.threads === null && !AG.loadingThreads) { AG.loadingThreads = true;
    agThreadsLoad().then(async () => { AG.loadingThreads = false; if (AG.cur) await agThreadLoad(AG.cur); if (tab === "Agent") { render(); if (agBusy()) agentWatch(); } }); }
  if (agBusy()) agentWatch();
  const found = (BR.data && BR.data.backends) || [];
  agAskSignins(found); agIdleWatch();
  const harnesses = found.filter(b => AG_HARNESS.includes(b.id));
  if (!agReady(found.find(b => b.id === AG.backend))) {
    const chosen0 = BR.data && BR.data.settings && BR.data.settings.agent;  // the Agent room's own default (Connect sets it)
    const def = BR.data && (found.find(b => b.id === chosen0 && agReady(b)) || found.find(b => b.id === BR.data.default && agReady(b)));
    // nobody chose a brain or an agent, and the set local server answers: it is the room's first choice
    const auto = BR.data && BR.data.automatic && !chosen0 && BR.data.default === "local" ? found.find(b => b.id === "local" && agReady(b)) : null;
    AG.backend = (auto || ((def && (agIsHarness(def.id) || !harnesses.some(agReady))) ? def : null) || harnesses.find(agReady) || found.find(b => agReady(b) && AG_AGENTS.some(a => a[0] === b.id)) || { id: AG.backend || "claude" }).id;
  }
  const missing = harnesses.filter(b => !agReady(b));
  const toolList = BR.tools ? (BR.tools.tools || BR.tools) : [{ id: "blender", label: "Blender" }, { id: "blockout", label: "Blockout" }, { id: "studio", label: "Studio" }];
  const harness = agIsHarness(AG.backend), chosen = agChosen();
  // a tool that runs on this computer cannot be started by an agent on another one until that one can reach back here
  const far = !!(chosen && /^ssh:./.test(chosen.where || "")), unreach = (BR.tools && BR.tools.unreachable) || {};
  AG.dropped = AG.dropped || new Set();
  for (const id of [...AG.tools]) if (far && unreach[id]) { AG.tools.delete(id); AG.dropped.add(id); }
  for (const id of [...AG.dropped]) if (!(far && unreach[id])) { AG.tools.add(id); AG.dropped.delete(id); }  // reachable again: back as it was
  const thread = AG.cur && AG.thread && AG.thread.id === AG.cur ? AG.thread : null;
  const earlier = (AG.tasks || []).filter(t => !t.thread);
  // after a send or a new turn, the newest turn is brought into view
  const count = thread ? (thread.turns || []).length + (thread.live ? 1 : 0) : 0;
  if (thread && count !== AG.seen && typeof requestAnimationFrame === "function") { AG.seen = count; requestAnimationFrame(() => { const end = document.querySelector(".agr-end"); if (end && end.scrollIntoView) end.scrollIntoView({ block: "end", behavior: "smooth" }); }); }
  return el("div", {},
    el("div", { class: "pagehead" + (typeof ART !== "undefined" && ART.ready.has("vault-agent") ? " enghead vault" : "") },
      typeof ART !== "undefined" && ART.ready.has("vault-agent") ? artSlot("vault-agent", "", "agent") : null,
      el("div", { class: "stack" }, el("span", { class: "eyebrow" }, "Agent · your own subscription"),
      el("h1", { class: "display" }, "Ask your agent. ", el("em", {}, "It makes it in the Studio.")),
      el("p", { class: "sub" }, "Pick the agent you already use. Say what you want; it works through the Studio and asks when a choice matters. Describe the shot, the scene or the whole film."))),
    typeof connectNudge === "function" ? connectNudge("agent") : null,
    el("div", { class: "sectionhead" }, el("h2", {}, "Choose your agent"), agSetup()),
    agChooser(found),
    el("div", { class: "agr-under" },
      harness ? [el("span", {}, "It may use"), el("div", { class: "chips" }, toolList.map(t => { const why = far && unreach[t.id], on = AG.tools.has(t.id) && !why;
        return el("button", { class: "toggle" + (on ? " on" : "") + (why ? " off" : ""), "aria-pressed": on ? "true" : "false", "aria-disabled": why ? "true" : false, title: why || "",
          onclick: () => { if (why) return toast(why, 7000); AG.tools.has(t.id) ? AG.tools.delete(t.id) : AG.tools.add(t.id); render(); } },
        el("span", { class: "dotl" + (on ? "" : " o") }), t.label || t.id); })),
        far && Object.keys(unreach).length ? el("span", { class: "note m-flat ag-back" }, "Some tools need " + chosen.where.slice(4) + " to reach this computer: ",
          el("a", { href: "#Settings", onclick: e => { if (e && e.preventDefault) e.preventDefault(); if (typeof SET !== "undefined") SET.section = "connections"; if (typeof CN !== "undefined") CN.linkOpen = true; go("Settings"); } }, "let it reach this studio"), ".") : null]
        : el("span", {}, AG.backend === "hermes" ? "Hermes is a conversation here: it reads its MCP servers from its own config, not from the Studio, so it plans and answers but does not drive the Studio's tools. Pick Claude Code or Codex for that."
          : (chosen ? chosen.label : agName(AG.backend)) + (AG.backend === "local" ? " is a conversation here: its server does not take tool calls (llama.cpp needs --jinja), so it plans, writes prompts and answers; Claude Code or Codex make things with the Studio's tools." : " is a conversation here: it plans, writes prompts and answers; Claude Code or Codex make things with the Studio's tools.")),
      chosen && chosen.where && /^ssh:/.test(chosen.where) ? el("span", { class: "note m-flat" }, "Runs on " + chosen.where.slice(4) + " over SSH") : null),
    el("div", { class: "agr-room" }, agList(), thread ? agConversation(thread) : agStart(missing)),
    earlier.length ? el("div", { class: "sectionhead" }, el("h2", {}, "Earlier one-shot tasks"), el("button", { class: "small", onclick: () => { AG.earlier = !AG.earlier; render(); } }, AG.earlier ? "Hide" : "Show " + earlier.length)) : null,
    earlier.length && AG.earlier ? el("div", { class: "stack" }, earlier.slice(0, 20).map(taskCard)) : null);
}
