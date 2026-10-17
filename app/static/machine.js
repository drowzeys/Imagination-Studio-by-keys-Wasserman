"use strict";
// Machine room: live telemetry of this machine (GPU, unified memory against the guard's floor, temperature, power),
// what holds the GPU, the renderer, and where the disk went. Read only: nothing here stops, frees or deletes anything.
const SVGNS = "http://www.w3.org/2000/svg";
const svg = (tag, attrs = {}, ...kids) => { const n = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); kids.forEach(k => n.append(k)); return n; };
const RING = 2 * Math.PI * 56;
function ring(id, label, color) {
  const val = svg("circle", { class: "val", cx: 66, cy: 66, r: 56, stroke: color, "stroke-dasharray": RING, "stroke-dashoffset": RING, style: "color:" + color });
  const box = el("div", { class: "ring", "data-ring": id }, svg("svg", { viewBox: "0 0 132 132" }, svg("circle", { class: "track", cx: 66, cy: 66, r: 56 }), val),
    el("div", { class: "lab" }, el("b", { "data-v": id }, "–"), el("span", {}, label)));
  return box;
}
function ringSet(id, fraction, text, color) { const r = document.querySelector('[data-ring="' + id + '"]'); if (!r) return;
  const v = r.querySelector(".val"); v.setAttribute("stroke-dashoffset", RING * (1 - Math.max(0, Math.min(1, fraction || 0)))); if (color) { v.setAttribute("stroke", color); v.style.color = color; }
  r.querySelector("[data-v]").textContent = text; }
function sparkPath(values, w = 600, h = 64, max = 100) { const pts = values.map((v, i) => [values.length < 2 ? 0 : i * w / (values.length - 1), h - 3 - (Math.max(0, Math.min(max, v || 0)) / max) * (h - 8)]);
  if (!pts.length) return ["", ""]; const line = "M" + pts.map(p => p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" L");
  return [line, line + " L" + w + " " + h + " L0 " + h + " Z"]; }
function sparkline(id, color) { return el("div", { class: "stack", style: "gap:6px" }, svg("svg", { class: "spark", viewBox: "0 0 600 64", preserveAspectRatio: "none", "data-spark": id },
  svg("defs", {}, svg("linearGradient", { id: "g-" + id, x1: 0, y1: 0, x2: 0, y2: 1 }, svg("stop", { offset: "0", "stop-color": color, "stop-opacity": ".35" }), svg("stop", { offset: "1", "stop-color": color, "stop-opacity": "0" }))),
  svg("path", { d: "", fill: "url(#g-" + id + ")" }), svg("path", { d: "", fill: "none", stroke: color, "stroke-width": "2", "vector-effect": "non-scaling-stroke" }))); }
function sparkSet(id, values, max) { const s = document.querySelector('[data-spark="' + id + '"]'); if (!s) return; const [line, area] = sparkPath(values, 600, 64, max); const p = s.querySelectorAll("path"); p[0].setAttribute("d", area); p[1].setAttribute("d", line); }
const M = { storage: null, loading: false, history: null };
function machineLive(t) {
  if (!t) return; const g = t.gpu || {}, mem = t.memory || {}, used = mem.total_mb ? 1 - mem.available_mb / mem.total_mb : 0;
  ringSet("gpu", (g.util_pct || 0) / 100, g.util_pct == null ? "–" : g.util_pct + "%", g.util_pct > 60 ? "var(--onair)" : "var(--ok)");
  ringSet("mem", used, Math.round(used * 100) + "%", used > .85 ? "var(--onair)" : used > .65 ? "var(--warn)" : "var(--accent)");
  ringSet("temp", (g.temp_c || 0) / 100, g.temp_c == null ? "–" : g.temp_c + "°", g.temp_c > 85 ? "var(--onair)" : g.temp_c > 70 ? "var(--warn)" : "var(--cue)");
  ringSet("power", (g.power_w || 0) / 240, g.power_w == null ? "–" : Math.round(g.power_w) + "W", "var(--accent2)");
  const foot = (k, s) => { const n = document.querySelector('[data-foot="' + k + '"]'); if (n) n.textContent = s; };
  foot("gpu", (g.name || "GPU") + (g.clock_mhz ? " · " + g.clock_mhz + " MHz" : ""));
  foot("mem", mem.total_mb ? Math.round((mem.total_mb - mem.available_mb) / 1024) + " of " + Math.round(mem.total_mb / 1024) + " GB" + (mem.floor_mb ? " · guard floor " + Math.round(mem.floor_mb / 1024) + " GB" : "") : "–");
  const noGpu = !t.gpu; if (noGpu) foot("gpu", "no GPU readings on this machine");
  foot("temp", noGpu ? "reported on a DGX Spark" : g.temp_c > 85 ? "hot: renders may slow" : "GPU temperature"); foot("power", noGpu ? "reported on a DGX Spark" : "power draw");
  const hist = [...(M.history || []), ...SHELL.history.map(s => ({ gpu: s.gpu, mem: s.mem }))].slice(-300);
  sparkSet("gpu", hist.map(s => s.gpu), 100); sparkSet("mem", hist.map(s => s.mem), 100);
  const st = document.querySelector("#machstatus"); if (st) st.replaceChildren(...statusRows(t));
  const w = document.querySelector("#machwarn"); if (w) w.replaceChildren(...(t.warnings || []).map(x => el("div", { class: "warnline" }, el("span", { class: "dotl r", style: "margin-top:5px" }), el("div", {}, x))));
}
function statusRows(t) { const row = (k, v, cls) => el("div", { class: "kv", style: "padding:10px 0;border-bottom:1px solid var(--line)" }, el("span", {}, k), el("b", { class: cls || "" }, v));
  const cur = t.jobs && t.jobs.current;
  return [row("Rolling", cur ? cur.title : "nothing"), row("Queue", (t.jobs ? t.jobs.queued : 0) + " waiting"),
    row("GPU lock", t.gpu_lock ? (t.gpu_lock.held ? "held" + (t.gpu_lock.holder ? " by " + t.gpu_lock.holder : "") : "free") : "–"),
    row("Renderer", t.renderer && t.renderer.comfy ? (t.renderer.comfy.running ? "ComfyUI up on " + t.renderer.comfy.port : "idle, models unloaded") : "–"),
    row("Workload governor", t.governor ? (t.governor.status || "answering") + (Array.isArray(t.governor.reservations) ? " · " + count(t.governor.reservations.length, "reservation") : "") + (t.governor.alert_temperature_c ? " · alert at " + t.governor.alert_temperature_c + "°C" : "") : "not on this machine", t.governor && t.governor.status && t.governor.status !== "ok" ? "warn" : ""),
    row("Processor", t.cpu ? "load " + (t.cpu.load1 ?? "–") + " on " + (t.cpu.cores || "?") + " cores" : "–"),
    row("Host", t.host || "–")]; }
const BUCKET_COLORS = ["#ffb457", "#ff7b3d", "#ff3d5c", "#b6abff", "#5ef2b2", "#8fd0ff", "#ffd98a", "#e58bff", "#9a97a8", "#5f5c6c", "#c4ff7a", "#ff9fb6"];
function storagePanel() {
  const box = el("div", { class: "panel stack", id: "storage" });
  const draw = () => { const s = M.storage;
    if (!s) { box.replaceChildren(el("h2", {}, "Storage"), el("div", { class: "note" }, M.loading ? "Measuring folders…" : "Storage readouts need a newer Studio server.")); return; }
    const total = s.buckets.reduce((a, b) => a + (b.gb || 0), 0) || 1, sorted = [...s.buckets].filter(b => b.gb > 0.01).sort((a, b) => b.gb - a.gb);
    box.replaceChildren(el("div", { class: "kv" }, el("h2", { style: "margin:0" }, "Storage"), el("button", { class: "small", onclick: async () => { M.loading = true; draw(); try { await api("/api/storage/refresh", {}); } catch {} loadStorage(draw); } }, s.computing ? "Measuring…" : "Measure again")),
      el("div", { style: "display:flex;align-items:baseline;gap:14px;flex-wrap:wrap" }, el("span", { class: "big" }, Math.round(s.free_gb), el("small", {}, "GB free")), el("span", { class: "note", style: "margin:0" }, "of " + (s.total_gb >= 1000 ? (s.total_gb / 1000).toFixed(1) + " TB" : Math.round(s.total_gb) + " GB") + " · " + s.used_pct + "% used"),
        s.used_pct >= 90 ? el("span", { class: "tag2 bad" }, "almost full") : null),
      el("div", { class: "gaugebar", style: "height:10px" }, el("i", { style: "width:" + s.used_pct + "%;--c:" + (s.used_pct >= 90 ? "var(--onair)" : "var(--cue)") })),
      el("h3", {}, "What Studio keeps"),
      el("div", { class: "buckets" }, sorted.map((b, i) => el("i", { title: b.label + " · " + b.gb.toFixed(1) + " GB", style: "flex:" + b.gb + ";background:" + BUCKET_COLORS[i % BUCKET_COLORS.length] }))),
      el("div", { class: "legend" }, sorted.map((b, i) => el("div", { title: b.path }, el("s", { style: "background:" + BUCKET_COLORS[i % BUCKET_COLORS.length] }), b.label, el("b", {}, b.gb >= 100 ? Math.round(b.gb) + " GB" : b.gb.toFixed(1) + " GB")))),
      el("div", { class: "note" }, "Measured " + (s.computed_at ? new Date(s.computed_at * 1000).toLocaleTimeString() : "just now") + ". Studio never deletes anything here on its own: the Library's trash is the only way files leave, and you empty it yourself.")); };
  draw(); loadStorage(draw); return box; }
async function loadStorage(draw) { M.loading = true; try { M.storage = await api("/api/storage"); } catch { M.storage = null; } M.loading = false; draw();
  if (M.storage && M.storage.computing) setTimeout(() => { if (document.querySelector("#storage")) loadStorage(draw); }, 2500); }
function viewMachine() {
  const wrap = el("div");
  put(wrap, el("div", { class: "sectionhead", style: "margin-top:0" }, el("div", { class: "stack", style: "gap:6px" }, el("span", { class: "eyebrow" }, "Machine · live"), el("h2", { class: "display", style: "font-size:clamp(34px,4vw,52px)" }, S.platform === "spark" ? "DGX Spark" : S.platform === "mac" ? "Mac" : "This machine"),
      el("span", { class: "note", style: "margin:0;font:500 11.5px var(--mono);letter-spacing:.06em" }, S.platform_label || "")),
    el("div", { class: "chips" }, el("button", { onclick: () => go("Sparks") }, "Sparks"), el("button", { onclick: () => go("Brains") }, "Brains & tools"), el("button", { onclick: () => $("#studio-updates-open").click() }, "Updates"))),
    el("div", { id: "machwarn", class: "stack", style: "gap:8px;margin-bottom:14px" }),
    el("div", { class: "gauges" },
      el("div", { class: "gauge" }, ring("gpu", "GPU", "var(--ok)"), el("div", { class: "foot", "data-foot": "gpu" }, "–")),
      el("div", { class: "gauge" }, ring("mem", "Memory", "var(--accent)"), el("div", { class: "foot", "data-foot": "mem" }, "–")),
      el("div", { class: "gauge" }, ring("temp", "Temp", "var(--cue)"), el("div", { class: "foot", "data-foot": "temp" }, "–")),
      el("div", { class: "gauge" }, ring("power", "Power", "var(--accent2)"), el("div", { class: "foot", "data-foot": "power" }, "–"))),
    el("div", { class: "split", style: "margin-top:18px" },
      el("div", { class: "stack" },
        el("div", { class: "panel stack" }, el("div", { class: "kv" }, el("h2", { style: "margin:0" }, "GPU load"), el("span", {}, "last 10 minutes")), sparkline("gpu", "#5ef2b2"),
          el("div", { class: "kv" }, el("h2", { style: "margin:0;font-size:20px" }, "Unified memory"), el("span", {}, "% used")), sparkline("mem", "#ffb457")),
        storagePanel()),
      el("div", { class: "panel" }, el("h2", {}, "Right now"), el("div", { id: "machstatus" }, el("div", { class: "note" }, "Waiting for the first reading…")),
        el("div", { class: "note", style: "margin-top:14px" }, "The memory guard checks every clip before it is queued and again before a model loads, and stops the renderer if memory falls under its floor. These readouts show what it sees; they do not change it."))));
  api("/api/telemetry/history").then(h => { M.history = (h.samples || []).map(s => ({ gpu: s.gpu, mem: s.mem })); if (SHELL.tel) machineLive(SHELL.tel); }).catch(() => {});
  setTimeout(() => { if (SHELL.tel) machineLive(SHELL.tel); }, 0);
  return wrap;
}

/* ---------- Brains & tools: the user's own subscriptions and the MCP tools an agent may use ---------- */
const BR = { data: null, tools: null, testing: {}, said: {}, checking: {}, checked: {} };
const BRAIN_LOOK = { claude: ["C", "linear-gradient(135deg,#ffcf9e,#e5774a)"], codex: ["</>", "linear-gradient(135deg,#d6f5e8,#5ef2b2)"], gemini: ["G", "linear-gradient(135deg,#c9d8ff,#7c8cff)"],
  grok: ["X", "linear-gradient(135deg,#f0f0f0,#8f8f99)"], local: ["◎", "linear-gradient(135deg,#ffe7a8,#ffb457)"] };
async function brainsLoad() { try { BR.data = await api("/api/brains"); } catch (e) { BR.data = { error: e.message }; } try { BR.tools = await api("/api/agent/tools"); } catch { BR.tools = null; } if (tab === "Brains") render(); }
function viewBrains() {
  const wrap = el("div");
  if (!BR.data) { brainsLoad(); return el("section", { class: "card" }, el("h2", {}, "Brains & tools"), el("div", { class: "note" }, "Looking for your sign-ins…")); }
  if (BR.data.error) return el("section", { class: "card" }, el("h2", {}, "Brains & tools"), el("div", { class: "note" }, "This Studio server does not offer brains yet (" + BR.data.error + ")."));
  const d = BR.data, set = d.settings || {}, remote = set.remote || {};
  const host = el("input", { value: remote.host || "", placeholder: "you@your-mac.tailnet.ts.net" }), studioHost = el("input", { value: remote.studio_host || "", placeholder: "sam@dgx-spark-2 (optional)" }), endpoint = el("input", { value: set.local_endpoint || "", placeholder: "http://127.0.0.1:11434 (Ollama), :1234 (LM Studio), :8000 (vLLM)" });
  const save = async patch => { try { await api("/api/brains/settings", { ...set, ...patch }); toast("Saved"); BR.data = null; brainsLoad(); } catch (e) { toast(e.message); } };
  put(wrap, el("div", { class: "sectionhead", style: "margin-top:0" }, el("div", { class: "stack", style: "gap:6px" }, el("span", { class: "eyebrow" }, "Connections"), el("h2", { class: "display", style: "font-size:clamp(34px,4vw,52px)" }, "Your ", el("em", {}, "brains"), " and tools")),
    el("p", { class: "sub", style: "margin:0;max-width:46ch" }, "Studio uses the AI subscriptions you already pay for. Nothing is stored but which one you prefer: each answer is billed to your own sign-in, and a local model never leaves your network.")));
  put(wrap, el("div", { class: "tri" }, (d.backends || []).map(b => { const [glyph, bg] = BRAIN_LOOK[b.id] || ["•", "var(--grad)"], on = d.default === b.id;
    return el("div", { class: "brain" + (on ? " on" : "") },
      el("div", { class: "head" }, el("span", { class: "logo", style: "background:" + bg }, glyph), el("div", { style: "min-width:0" }, el("b", {}, b.label), el("span", {}, b.found ? (b.where && b.where !== "here" ? "signed in on " + b.where.replace(/^ssh:/, "") : "found on this machine") + (b.version ? " · " + b.version : "") : "not found"))),
      el("div", { style: "display:flex;gap:6px;flex-wrap:wrap" }, el("span", { class: "tag2 " + (b.found ? "ok" : "") }, b.found ? "ready" : "not set up"), on ? el("span", { class: "tag2 t" }, "default") : null,
        b.kind === "http" && b.models && b.models.length ? el("span", { class: "tag2" }, b.models.length + " models") : null),
      !b.found && b.signin ? el("div", { class: "note", style: "margin:0" }, b.signin) : null,
      BR.said[b.id] ? el("div", { class: "note", style: "margin:0;color:" + (BR.said[b.id].ok ? "var(--ok)" : "var(--bad)") }, BR.said[b.id].ok ? "Answered “" + (BR.said[b.id].text || "").slice(0, 40) + "” in " + (BR.said[b.id].elapsedMs / 1000).toFixed(1) + " s" : BR.said[b.id].error) : null,
      el("div", { style: "display:flex;gap:8px;margin-top:auto" }, el("button", { class: "small", disabled: !b.found || BR.testing[b.id], onclick: async () => { BR.testing[b.id] = true; render();
          try { BR.said[b.id] = await api("/api/brains/test", { backend: b.id }); } catch (e) { BR.said[b.id] = { ok: false, error: e.message }; } BR.testing[b.id] = false; render(); } }, BR.testing[b.id] ? "Asking…" : "Test"),
        on ? null : el("button", { class: "small", disabled: !b.found, onclick: () => save({ default: b.id }) }, "Make default"))); })));
  put(wrap, el("div", { class: "split", style: "margin-top:18px" },
    el("div", { class: "panel stack" }, el("h2", {}, "Use the sign-ins on another computer"),
      el("div", { class: "note", style: "margin:0" }, "A DGX Spark usually has no Claude or Codex sign-in of its own. Name the Mac (or any machine) where you are signed in, and Studio runs the CLI there over SSH. The Spark needs key login to it; no password is ever typed or kept here."),
      field("SSH host", host), field("How that computer reaches this one (for Blender and Studio tools)", studioHost), el("div", { class: "chips" }, ["claude", "codex", "gemini", "grok"].map(id => el("label", { class: "toggle" + ((remote.backends || []).includes(id) ? " on" : ""), style: "margin:0" },
        el("input", { type: "checkbox", checked: (remote.backends || []).includes(id), style: "display:none", onchange: e => { const list = new Set(remote.backends || []); e.target.checked ? list.add(id) : list.delete(id); remote.backends = [...list]; e.target.parentNode.classList.toggle("on", e.target.checked); } }),
        el("span", { class: "dotl" + ((remote.backends || []).includes(id) ? "" : " o") }), ({ claude: "Claude Code", codex: "Codex", gemini: "Gemini CLI", grok: "Grok CLI" })[id]))),
      el("div", {}, el("button", { class: "primary", onclick: () => save({ remote: { host: host.value.trim(), backends: remote.backends || [], studio_host: studioHost.value.trim() } }) }, "Save"))),
    el("div", { class: "panel stack" }, el("h2", {}, "A local model"), el("div", { class: "note", style: "margin:0" }, "Any OpenAI-compatible server: Ollama, LM Studio, vLLM or llama.cpp. Left empty, Studio looks on the usual ports."),
      field("Address", endpoint), el("div", {}, el("button", { onclick: () => save({ local_endpoint: endpoint.value.trim() }) }, "Save")))));
  const tools = BR.tools;
  put(wrap, el("div", { class: "sectionhead" }, el("h2", {}, "Tools your agent can use"), el("button", { class: "small", onclick: () => go("Agent") }, "Ask your agent →")),
    !tools ? el("div", { class: "note" }, "This Studio server does not list agent tools yet.") : el("div", { class: "tri" }, (tools.tools || tools || []).map(t => {
      const said = BR.checked[t.id];
      return el("div", { class: "brain" }, el("div", { class: "head" }, el("span", { class: "logo", style: "background:" + ({ blender: "linear-gradient(135deg,#ffd0a0,#ff7b3d)", blockout: "linear-gradient(135deg,#e0e0e8,#9a97a8)", studio: "var(--grad)" }[t.id] || "var(--grad-cue)") }, ({ blender: "B", blockout: "▣", studio: "S" }[t.id] || "◇")),
          el("div", { style: "min-width:0" }, el("b", {}, t.label || t.id), el("span", {}, (t.via === "ssh" ? "over SSH on " + (t.host || "your Mac") : "on this machine") + (t.enabled === false ? " · off" : "")))),
        el("div", { class: "note", style: "margin:0;font:11.5px/1.5 var(--mono);overflow-wrap:anywhere" }, (t.command || []).map(a => /\/.+\//.test(a) && a.split("/").length > 3 ? "…/" + a.split("/").slice(-2).join("/") : a).join(" ")),
        said ? el("div", { class: "note", style: "margin:0;color:" + (said.ok ? "var(--ok)" : "var(--bad)") }, said.ok ? (said.tools || []).length + " tools: " + (said.tools || []).slice(0, 6).join(", ") + ((said.tools || []).length > 6 ? "…" : "") : said.error) : null,
        el("div", { style: "display:flex;gap:8px;flex-wrap:wrap;margin-top:auto" }, el("button", { class: "small", disabled: BR.checking[t.id], onclick: async () => { BR.checking[t.id] = true; render();
            try { BR.checked[t.id] = await api("/api/agent/tools/" + t.id + "/check", {}); } catch (e) { BR.checked[t.id] = { ok: false, error: e.message }; } BR.checking[t.id] = false; render(); } }, BR.checking[t.id] ? "Connecting…" : "Check"),
          t.id === "blender" ? el("button", { class: "small", onclick: async () => { try { const r = await api("/api/agent/tools/blender/start", {}); toast(r.message || (r.running ? "Blender is listening" : "Blender is starting")); } catch (e) { toast(e.message); } } }, "Start Blender") : null)); })));
  return wrap;
}
