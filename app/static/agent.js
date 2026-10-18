"use strict";
// Ask your agent: a request in plain words goes to the user's own Claude Code or Codex, with Blender, Blockout and
// Studio itself as tools. Whatever it makes lands in the Library, ready to open a clip or guide one.
const AG = { prompt: "", backend: null, tools: new Set(["blender", "studio"]), attach: [], tasks: null, timer: null, picking: false, lib: null, open: {} };
const AG_SUGGEST = [
  ["Garage, 35 mm", "In Blender, build a rain-soaked parking garage at night with sodium lights. Put a camera at eye level with a 35 mm lens looking down the row, and render a 1344×768 still."],
  ["Blockout push-in", "In Blockout, stage one actor walking to a car, stopping and turning. Add a slow push-in on a 35 mm lens over 8 seconds, then export the motion package."],
  ["Turntable", "In Blender, import the attached model, light it with a three-point setup and render a 24-frame turntable as stills."],
  ["Stills to clips", "Look at the attached stills, write a one-line action for each, and queue an 8-second clip from each one in Studio."],
];
// the list leaves out each task's log: the open and running ones are read in full
const agentLoad = async () => { try { const r = await api("/api/agent/tasks"); AG.tasks = Array.isArray(r) ? r : (r.tasks || []); } catch { AG.tasks = []; }
  AG.tasks = await Promise.all(AG.tasks.slice(0, 20).map(async t => (AG.open[t.id] ?? ["queued", "running"].includes(t.status)) ? await api("/api/agent/tasks/" + t.id).catch(() => t) : t)); };
function agentWatch() { if (AG.timer) return; AG.timer = setInterval(async () => { if (tab !== "Agent") { clearInterval(AG.timer); AG.timer = null; return; }
  const before = JSON.stringify((AG.tasks || []).map(t => [t.id, t.status, (t.outputs || []).length, (t.log || "").length])); await agentLoad();
  if (JSON.stringify((AG.tasks || []).map(t => [t.id, t.status, (t.outputs || []).length, (t.log || "").length])) !== before && !document.activeElement.matches("textarea,input")) render();
  if (!(AG.tasks || []).some(t => ["queued", "running"].includes(t.status))) { clearInterval(AG.timer); AG.timer = null; } }, 1500); }
function useOutput(path, how) {
  if (how === "first") { Object.assign(C, { image: path, goes: "video", start: "own", step: 1, prompt: "" }); }
  else if (how === "last") { Object.assign(C, { lastImage: path, goes: "video", step: 1, prompt: "" }); if (C.start === "refs") C.start = "text"; }
  else if (how === "ref") { Object.assign(C, { goes: "video", start: "refs", step: 1, prompt: "" }); if (/\.(mp4|mov)$/i.test(path)) { if (!C.refVideos.includes(path)) { C.refVideos.push(path); C.video_subjects.push(""); } } else if (!C.refs.includes(path)) { C.refs.push(path); C.subjects.push(""); } }
  sync(); go("Create"); toast(how === "ref" ? "Added as a reference" : how === "first" ? "Set as the first frame" : "Set as the last frame"); }
// a brain's mark beside its name, where it has one (shell.js MARKS)
const agMark = id => typeof mark === "function" && typeof MARKS !== "undefined" && MARKS[id] ? mark(id, "") : null;
// a brain a task can go to: found on this machine, or run over SSH on the computer named in Brains & tools
const agReady = b => !!b && (!!b.found || /^ssh:./.test(b.where || ""));
const agSetup = () => el("a", { href: "#Brains", class: "ag-setup", onclick: e => { if (e.preventDefault) e.preventDefault(); go("Brains"); } }, "Set up on Machine → Brains & tools");
function taskCard(t) {
  const outs = t.outputs || [], open = AG.open[t.id] ?? ["queued", "running"].includes(t.status);
  const pics = outs.filter(p => /\.(png|jpe?g|webp|mp4|mov)$/i.test(p)), others = outs.filter(p => !pics.includes(p));
  return el("div", { class: "task" },
    el("div", { class: "seghead" }, el("span", { class: "pill " + t.status }, t.status), el("b", { class: "ag-prompt" }, t.prompt),
      el("span", { class: "tag2" }, agMark(t.backend), ({ claude: "Claude Code", codex: "Codex" })[t.backend] || t.backend), (t.tools || []).map(x => el("span", { class: "tag2" }, x)),
      ["queued", "running"].includes(t.status) ? el("button", { class: "small", onclick: async () => { try { await api("/api/agent/tasks/" + t.id + "/cancel", {}); await agentLoad(); render(); } catch (e) { toast(e.message); } } }, "Stop") : null,
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
function viewAgent() {
  if (!BR.data) brainsLoad().then(() => { if (tab === "Agent") render(); }); if (AG.tasks === null) { agentLoad().then(() => { if (tab === "Agent") render(); }); }
  if ((AG.tasks || []).some(t => ["queued", "running"].includes(t.status))) agentWatch();
  const harnesses = (BR.data && BR.data.backends || []).filter(b => ["claude", "codex"].includes(b.id));
  if (!AG.backend || (!agReady(harnesses.find(b => b.id === AG.backend)) && harnesses.some(agReady)))
    AG.backend = (harnesses.find(b => agReady(b) && b.id === (BR.data && BR.data.default)) || harnesses.find(agReady) || { id: "claude" }).id;
  const missing = harnesses.filter(b => !agReady(b));
  const toolList = BR.tools ? (BR.tools.tools || BR.tools) : [{ id: "blender", label: "Blender" }, { id: "blockout", label: "Blockout" }, { id: "studio", label: "Studio" }];
  const area = el("textarea", { placeholder: "Describe what you need. “Build a diner booth in Blender, warm practicals, a 50 mm two-shot from the aisle, render a still.”", "aria-label": "Your request" });
  area.value = AG.prompt; area.addEventListener("input", () => { AG.prompt = area.value; });
  area.addEventListener("keydown", e => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") run(); });
  const run = async () => { if (!AG.prompt.trim()) return toast("Say what you need first");
    const brain = harnesses.find(b => b.id === AG.backend); if (!agReady(brain)) return toast((brain ? brain.label : "No agent") + " is not set up here: Machine → Brains & tools");
    try { await api("/api/agent/tasks", { prompt: AG.prompt.trim(), backend: AG.backend, tools: [...AG.tools], attach: AG.attach }); AG.prompt = ""; AG.attach = []; await agentLoad(); agentWatch(); render(); toast("Sent to your agent"); }
    catch (e) { toast(e.message); } };
  const pickBox = AG.picking ? el("div", { class: "panel ag-pick" }, el("div", { class: "kv" }, el("b", { class: "ag-h" }, "Attach from the Library"), el("button", { class: "small", onclick: () => { AG.picking = false; render(); } }, "Done")),
    el("div", { class: "grid ag-grid" }, (AG.lib || []).slice(0, 36).map(m => el("div", { class: "tile ag-tile" + (AG.attach.includes(m.path) ? " on" : ""), onclick: () => { const i = AG.attach.indexOf(m.path); i < 0 ? AG.attach.push(m.path) : AG.attach.splice(i, 1); render(); } },
      m.kind === "audio" ? el("div", { class: "ag-audio" }, icon("sound")) : media(m.path, { controls: false, muted: true }), el("div", { class: "cap" }, m.path.split("/").pop()))))) : null;
  if (AG.picking && !AG.lib) api("/api/library").then(l => { AG.lib = l; render(); }).catch(() => {});
  const ready = harnesses.find(b => b.id === AG.backend);
  return el("div", {},
    el("div", { class: "pagehead" }, el("div", { class: "stack" }, el("span", { class: "eyebrow" }, "Previs · Ask your agent"),
      el("h1", { class: "display" }, "Describe the shot. ", el("em", {}, "It builds the previs.")),
      el("p", { class: "sub" }, "Your own Claude Code or Codex works in Blender, Blockout and Studio. What it makes lands in the Library."))),
    el("div", { class: "agentbox" }, area,
      AG.attach.length ? el("div", { class: "chips ag-attached" }, AG.attach.map(p => el("span", { class: "tag2 t" }, p.split("/").pop(), el("a", { href: "#", class: "ag-x", "aria-label": "Remove", onclick: e => { e.preventDefault(); AG.attach = AG.attach.filter(x => x !== p); render(); } }, "×")))) : null,
      el("div", { class: "agentbar" },
        el("div", { class: "chips" }, harnesses.length ? harnesses.map(b => el("button", { class: "toggle" + (AG.backend === b.id && agReady(b) ? " on" : "") + (agReady(b) ? "" : " off"), disabled: !agReady(b), "aria-disabled": agReady(b) ? false : "true",
          title: agReady(b) ? b.label : b.label + " is not found on this machine and no other computer is set up for it", onclick: () => { AG.backend = b.id; render(); } },
          el("span", { class: "dotl" + (agReady(b) ? (AG.backend === b.id ? " t" : "") : " o") }), agMark(b.id), b.label)) : el("span", { class: "note m-flat" }, "No agent found yet: set one up in Brains & tools.")),
        el("span", { class: "ag-sep" }),
        el("div", { class: "chips" }, toolList.map(t => el("button", { class: "toggle" + (AG.tools.has(t.id) ? " on" : ""), onclick: () => { AG.tools.has(t.id) ? AG.tools.delete(t.id) : AG.tools.add(t.id); render(); } },
          el("span", { class: "dotl" + (AG.tools.has(t.id) ? "" : " o") }), t.label || t.id))),
        el("span", { class: "su-grow" }),
        el("button", { class: "small", onclick: () => { AG.picking = !AG.picking; render(); } }, "Attach" + (AG.attach.length ? " (" + AG.attach.length + ")" : "")),
        el("button", { class: "primary", disabled: !agReady(ready), onclick: run }, "Send ", el("kbd", { class: "ag-kbd" }, "⌘⏎"))),
      missing.length ? el("div", { class: "note ag-hint" }, missing.map(b => b.label).join(" and ") + (missing.length > 1 ? " are" : " is") + " not on this machine. ", agSetup()) : null),
    pickBox,
    el("div", { class: "suggest" }, AG_SUGGEST.map(([k, text]) => el("button", { onclick: () => { AG.prompt = text; if (/Blockout/.test(text)) AG.tools.add("blockout"); if (/Blender/.test(text)) AG.tools.add("blender"); render(); } }, k))),
    el("div", { class: "sectionhead" }, el("h2", {}, "Tasks"), el("button", { class: "small", onclick: () => go("Brains") }, "Brains & tools →")),
    AG.tasks === null ? el("div", { class: "note" }, "Loading…") : AG.tasks.length ? el("div", { class: "stack" }, AG.tasks.slice(0, 20).map(taskCard))
      : el("div", { class: "panel ag-empty" }, el("div", { class: "ag-h" }, "No tasks yet"), el("div", { class: "note" }, "Pick a suggestion above, or describe a set, a camera and a lens. The agent saves what it makes into the Library.")));
}
