"use strict";
// Machine room: live telemetry of this machine (GPU, unified memory against the guard's floor, temperature, power),
// what holds the GPU, the renderer, and where the disk went. Read only: nothing here stops, frees or deletes anything.
const SVGNS = "http://www.w3.org/2000/svg";
const svg = (tag, attrs = {}, ...kids) => { const n = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); kids.forEach(k => n.append(k)); return n; };
const RING = 2 * Math.PI * 56;
function ring(id, label, color) {
  const val = svg("circle", { class: "val", cx: 66, cy: 66, r: 56, "stroke-dasharray": RING, "stroke-dashoffset": RING, style: "stroke:" + color });
  const box = el("div", { class: "ring", "data-ring": id }, svg("svg", { viewBox: "0 0 132 132" }, svg("circle", { class: "track", cx: 66, cy: 66, r: 56 }), val),
    el("div", { class: "lab" }, el("b", { "data-v": id }, "–"), el("span", {}, label)));
  return box;
}
function ringSet(id, fraction, text, color) { const r = document.querySelector('[data-ring="' + id + '"]'); if (!r) return;
  const v = r.querySelector(".val"); v.setAttribute("stroke-dashoffset", RING * (1 - Math.max(0, Math.min(1, fraction || 0)))); if (color && v.style) v.style.stroke = color;
  r.querySelector("[data-v]").textContent = text; }
function sparkPath(values, w = 600, h = 64, max = 100) { const pts = values.map((v, i) => [values.length < 2 ? 0 : i * w / (values.length - 1), h - 3 - (Math.max(0, Math.min(max, v || 0)) / max) * (h - 8)]);
  if (!pts.length) return ["", ""]; const line = "M" + pts.map(p => p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" L");
  return [line, line + " L" + w + " " + h + " L0 " + h + " Z"]; }
// a flat line over a flat, faint area: the colour is a class (m-green, m-amber, m-blue), never a gradient
function sparkline(id, tone) { return el("div", { class: "stack m-sparkbox" }, svg("svg", { class: "spark m-" + tone, viewBox: "0 0 600 64", preserveAspectRatio: "none", "data-spark": id },
  svg("path", { d: "", class: "area" }), svg("path", { d: "", class: "line", "vector-effect": "non-scaling-stroke" }))); }
function sparkSet(id, values, max) { const s = document.querySelector('[data-spark="' + id + '"]'); if (!s) return; const [line, area] = sparkPath(values, 600, 64, max); const p = s.querySelectorAll("path"); p[0].setAttribute("d", area); p[1].setAttribute("d", line); }
const M = { storage: null, loading: false, history: null };
function machineLive(t) {
  if (!t) return; const g = t.gpu || {}, mem = t.memory || {}, used = mem.total_mb ? 1 - mem.available_mb / mem.total_mb : 0;
  ringSet("gpu", (g.util_pct || 0) / 100, g.util_pct == null ? "–" : g.util_pct + "%", g.util_pct > 60 ? "var(--amber)" : "var(--green)");
  ringSet("mem", used, Math.round(used * 100) + "%", used > .85 ? "var(--red)" : used > .65 ? "var(--amber)" : "var(--blue)");
  ringSet("temp", (g.temp_c || 0) / 100, g.temp_c == null ? "–" : g.temp_c + "°", g.temp_c > 85 ? "var(--red)" : g.temp_c > 70 ? "var(--amber)" : "var(--green)");
  ringSet("power", (g.power_w || 0) / 240, g.power_w == null ? "–" : Math.round(g.power_w) + "W", "var(--blue)");
  const foot = (k, s) => { const n = document.querySelector('[data-foot="' + k + '"]'); if (n) n.textContent = s; };
  foot("gpu", (g.name || "GPU") + (g.clock_mhz ? " · " + g.clock_mhz + " MHz" : ""));
  foot("mem", mem.total_mb ? Math.round((mem.total_mb - mem.available_mb) / 1024) + " of " + Math.round(mem.total_mb / 1024) + " GB" + (mem.floor_mb ? " · guard floor " + Math.round(mem.floor_mb / 1024) + " GB" : "") : "–");
  const noGpu = !t.gpu; if (noGpu) foot("gpu", "no GPU readings on this machine");
  foot("temp", noGpu ? "reported on a DGX Spark" : g.temp_c > 85 ? "hot: renders may slow" : "GPU temperature"); foot("power", noGpu ? "reported on a DGX Spark" : "power draw");
  const hist = [...(M.history || []), ...SHELL.samples.map(s => ({ gpu: s.gpu, mem: s.mem }))].slice(-300);
  sparkSet("gpu", hist.map(s => s.gpu), 100); sparkSet("mem", hist.map(s => s.mem), 100);
  const st = document.querySelector("#machstatus"); if (st) st.replaceChildren(...statusRows(t));
  const w = document.querySelector("#machwarn"); if (w) w.replaceChildren(...(t.warnings || []).map(x => el("div", { class: "warnline" }, el("span", { class: "dotl r m-dot" }), el("div", {}, x))));
}
function statusRows(t) { const row = (k, v, cls) => el("div", { class: "kv m-row" }, el("span", {}, k), el("b", { class: cls || "" }, v));
  const cur = t.jobs && t.jobs.current;
  return [row("Rolling", cur ? cur.title : "nothing"), row("Queue", (t.jobs ? t.jobs.queued : 0) + " waiting"),
    row("GPU lock", t.gpu_lock ? (t.gpu_lock.held ? "held" + (t.gpu_lock.holder ? " by " + t.gpu_lock.holder : "") : "free") : "–"),
    row("Renderer", t.renderer && t.renderer.comfy ? (t.renderer.comfy.running ? "ComfyUI up on " + t.renderer.comfy.port : "idle, models unloaded") : "–"),
    row("Workload governor", t.governor ? (t.governor.status || "answering") + (Array.isArray(t.governor.reservations) ? " · " + count(t.governor.reservations.length, "reservation") : "") + (t.governor.alert_temperature_c ? " · alert at " + t.governor.alert_temperature_c + "°C" : "") : "not on this machine", t.governor && t.governor.status && t.governor.status !== "ok" ? "warn" : ""),
    row("Processor", t.cpu ? "load " + (t.cpu.load1 ?? "–") + " on " + (t.cpu.cores || "?") + " cores" : "–"),
    row("Host", t.host || "–")]; }
// eight muted colours (--c1 to --c8 in studio.css), one a folder, in order of size
const bucketTone = i => "c" + (i % 8 + 1);
function storagePanel() {
  const box = el("div", { class: "panel stack", id: "storage" });
  const draw = () => { const s = M.storage;
    if (!s) { box.replaceChildren(el("h2", {}, "Storage"), el("div", { class: "note" }, M.loading ? "Measuring folders…" : "Storage readouts need a newer Studio server.")); return; }
    const total = s.buckets.reduce((a, b) => a + (b.gb || 0), 0) || 1, sorted = [...s.buckets].filter(b => b.gb > 0.01).sort((a, b) => b.gb - a.gb);
    box.replaceChildren(el("div", { class: "kv" }, el("h2", { class: "m-flat" }, "Storage"), el("button", { class: "small", onclick: async () => { M.loading = true; draw(); try { await api("/api/storage/refresh", {}); } catch {} loadStorage(draw); } }, s.computing ? "Measuring…" : "Measure again")),
      el("div", { class: "m-free" }, el("span", { class: "big" }, Math.round(s.free_gb), el("small", {}, "GB free")), el("span", { class: "note m-flat" }, "of " + (s.total_gb >= 1000 ? (s.total_gb / 1000).toFixed(1) + " TB" : Math.round(s.total_gb) + " GB") + " · " + s.used_pct + "% used"),
        s.used_pct >= 90 ? el("span", { class: "tag2 bad" }, "almost full") : null),
      el("div", { class: "gaugebar m-tall" }, el("i", { style: "width:" + s.used_pct + "%;--c:" + (s.used_pct >= 90 ? "var(--red)" : "var(--blue)") })),
      el("h3", {}, "What Studio keeps"),
      el("div", { class: "buckets" }, sorted.map((b, i) => el("i", { class: bucketTone(i), title: b.label + " · " + b.gb.toFixed(1) + " GB", style: "flex:" + b.gb }))),
      el("div", { class: "legend" }, sorted.map((b, i) => el("div", { title: relHome(b.path) }, el("s", { class: bucketTone(i) }), b.label, el("b", {}, b.gb >= 100 ? Math.round(b.gb) + " GB" : b.gb.toFixed(1) + " GB")))),
      el("div", { class: "note" }, "Measured " + (s.computed_at ? new Date(s.computed_at * 1000).toLocaleTimeString() : "just now") + ". Nothing here is deleted for you: files leave only through the Library's trash.")); };
  draw(); loadStorage(draw); return box; }
async function loadStorage(draw) { M.loading = true; try { M.storage = await api("/api/storage"); } catch { M.storage = null; } M.loading = false; draw();
  if (M.storage && M.storage.computing) setTimeout(() => { if (document.querySelector("#storage")) loadStorage(draw); }, 2500); }
function viewMachine() {
  const wrap = el("div");
  const where = S.platform === "spark" ? "DGX Spark" : S.platform === "mac" ? "Mac" : "This machine";
  put(wrap, pageHead("Studio Vault", "Machine", where + ": live readings. Read only, nothing here changes the machine."),
    S.platform_label ? el("div", { class: "m-plat" }, S.platform_label) : null,
    el("div", { id: "machwarn", class: "stack m-warns" }),
    el("div", { class: "gauges" },
      el("div", { class: "gauge" }, ring("gpu", "GPU", "var(--green)"), el("div", { class: "foot", "data-foot": "gpu" }, "–")),
      el("div", { class: "gauge" }, ring("mem", "Memory", "var(--blue)"), el("div", { class: "foot", "data-foot": "mem" }, "–")),
      el("div", { class: "gauge" }, ring("temp", "Temp", "var(--green)"), el("div", { class: "foot", "data-foot": "temp" }, "–")),
      el("div", { class: "gauge" }, ring("power", "Power", "var(--blue)"), el("div", { class: "foot", "data-foot": "power" }, "–"))),
    el("div", { class: "split m-gap" },
      el("div", { class: "stack" },
        el("div", { class: "panel stack" }, el("div", { class: "kv" }, el("h2", { class: "m-flat" }, "GPU load"), el("span", {}, "last 10 minutes")), sparkline("gpu", "green"),
          el("div", { class: "kv" }, el("h2", { class: "m-flat" }, "Unified memory"), el("span", {}, "% used")), sparkline("mem", "blue")),
        storagePanel()),
      el("div", { class: "panel" }, el("h2", {}, "Right now"), el("div", { id: "machstatus" }, el("div", { class: "note" }, "Waiting for the first reading…")),
        el("details", { class: "fold" }, el("summary", {}, "The memory guard"), el("div", { class: "note m-flat" }, "The memory guard checks every clip before it is queued and again before a model loads, and stops the renderer if memory falls under its floor. These readouts show what it sees; they do not change it.")))));
  api("/api/telemetry/history").then(h => { M.history = (h.samples || []).map(s => ({ gpu: s.gpu, mem: s.mem })); if (SHELL.tel) machineLive(SHELL.tel); }).catch(() => {});
  setTimeout(() => { if (SHELL.tel) machineLive(SHELL.tel); }, 0);
  return wrap;
}

/* ---------- Brains & tools: the user's own subscriptions and the MCP tools an agent may use ---------- */
const BR = { data: null, tools: null, testing: {}, said: {}, checking: {}, checked: {}, status: {}, sign: {}, signOpen: null, signTimer: null, signSig: "", codeDraft: {} };
// each brain shows its own mark (brand/, THIRD-PARTY-MARKS.md); a local model server and the tools show a plain sign
const BRAIN_MARK = { claude: "claude", codex: "codex", gemini: "gemini", grok: "grok", openai: "openai", chatgpt: "openai" };
const brainLogo = id => el("span", { class: "logo" }, BRAIN_MARK[id] ? mark(BRAIN_MARK[id], "") : icon("machine"));
const toolLogo = id => el("span", { class: "logo" }, id === "studio" ? el("img", { src: "brand/aperture-mark-color.svg", alt: "Imagination Studio", width: 20, height: 20 }) : icon(id === "blockout" ? "library" : id === "blender" ? "previs" : "suite"));
async function brainsLoad() { try { BR.data = await api("/api/brains"); } catch (e) { BR.data = { error: e.message }; } try { BR.tools = await api("/api/agent/tools"); } catch { BR.tools = null; } if (tab === "Brains") render(); }
/* ---------- signing a brain in: the CLI's own login run by the studio (app/agents.py, the signin section) ----------
   BR.status[id] is the CLI's own word on its sign-in; BR.sign[id] follows a sign-in (starting, needs_browser, needs_code,
   device_code, verifying, done, failed), polled every second while it runs. Shown on Settings → Connections and Brains & tools. */
const SIGN_RUNNING = ["starting", "needs_browser", "needs_code", "device_code", "verifying"];
const SIGN_OUT = ["claude", "codex", "grok"], HERMES_PROVIDERS = [["openai-codex", "ChatGPT (Codex)"], ["anthropic", "Claude"], ["nous", "Nous Portal"]];
const signRedraw = () => { if (tab === "Brains" || tab === "Settings") render(); };
const typing = () => typeof document !== "undefined" && document.activeElement && document.activeElement.matches && document.activeElement.matches("input,textarea,select");
async function brainStatus(id, fresh) { if (!fresh && BR.status[id]) return; BR.status[id] = { state: "asking" };
  try { BR.status[id] = await api("/api/brains/" + id + "/status"); } catch (e) { BR.status[id] = { state: "unknown", detail: e.message }; } signRedraw(); }
function signFollow(id) { clearInterval(BR.signTimer); BR.signTimer = setInterval(async () => { let now;
  try { now = await api("/api/brains/" + id + "/signin"); } catch (e) { now = { state: "failed", reason: e.message, lines: [] }; }
  const sig = JSON.stringify(now), was = BR.signSig; BR.signSig = sig; BR.sign[id] = now;
  if (!SIGN_RUNNING.includes(now.state)) { clearInterval(BR.signTimer); BR.signTimer = null;
    if (now.state === "done") { toast("Signed in"); BR.status = {}; BR.data = null; brainsLoad().then(signRedraw); } }
  if (sig !== was && !(typing() && now.state === "needs_code")) signRedraw(); }, 1000); }
async function signStart(b, extra) { BR.signOpen = b.id; BR.codeDraft[b.id] = "";
  if (b.signin_flow !== "pty" || (b.id === "hermes" && !extra)) { BR.sign[b.id] = { state: b.signin_flow === "pty" ? "choose" : "terminal" }; return signRedraw(); }
  BR.sign[b.id] = { state: "starting", lines: [] }; signRedraw();
  try { BR.sign[b.id] = await api("/api/brains/" + b.id + "/signin", extra || {}); signFollow(b.id); } catch (e) { BR.sign[b.id] = { state: "failed", reason: e.message, lines: [] }; } signRedraw(); }
async function signCode(id) { const code = (BR.codeDraft[id] || "").trim(); if (!code) return toast("Paste the code from the sign-in page first");
  try { BR.sign[id] = await api("/api/brains/" + id + "/signin/code", { code }); BR.codeDraft[id] = ""; } catch (e) { toast(e.message); } signRedraw(); }
async function signCancel(id) { try { BR.sign[id] = await api("/api/brains/" + id + "/signin/cancel", {}); } catch (e) { toast(e.message); }
  clearInterval(BR.signTimer); BR.signTimer = null; signRedraw(); }
async function signOut(b) { if (typeof confirm === "function" && !confirm("Sign " + b.label + " out" + (b.where && b.where !== "here" ? " on " + b.where.replace(/^ssh:/, "") : "") + "? This runs its own sign-out; you can sign in again here.")) return;
  try { const r = await api("/api/brains/" + b.id + "/signout", {}); BR.status[b.id] = r; toast(r.ok ? "Signed out" : "The CLI did not say it signed out"); } catch (e) { toast(e.message); } signRedraw(); }
const copyText = async (value, what) => { try { await navigator.clipboard.writeText(value); toast(what + " copied"); } catch { toast("Select it and copy: " + value); } };
// the status line and the Sign in / Sign out buttons on a brain card
function signLine(b) { if (b.kind !== "cli") return null; const st = BR.status[b.id];
  if (b.found && !st) brainStatus(b.id);
  const on = st && st.state === "signed_in", text = !b.found ? null : !st || st.state === "asking" ? "Asking " + b.label + " whether it is signed in…" : on ? "Signed in" + (st.detail ? " · " + st.detail : "") : st.state === "signed_out" ? "Not signed in" : st.detail || "Sign-in unknown";
  return text ? el("div", { class: "note m-flat signin-state " + (on ? "vt-ok" : st && st.state === "signed_out" ? "vt-bad" : ""), "data-signin-state": st ? st.state : "asking" }, text) : null; }
function signButtons(b) { if (b.kind !== "cli") return []; const st = BR.status[b.id], on = st && st.state === "signed_in", running = BR.sign[b.id] && SIGN_RUNNING.includes(BR.sign[b.id].state);
  if (on && SIGN_OUT.includes(b.id)) return [el("button", { type: "button", class: "small", disabled: running, onclick: () => signOut(b) }, "Sign out")];
  return [el("button", { type: "button", class: "small" + (b.found && !on ? " on" : ""), disabled: !b.found || running, title: b.found ? "" : "Install " + b.label + " first", onclick: () => signStart(b) }, on ? "Sign in again" : "Sign in")]; }
// the panel under a card while it signs in
function signPanel(b) { const s = BR.sign[b.id]; if (b.kind !== "cli" || BR.signOpen !== b.id || !s) return null;
  const where = b.where && b.where !== "here" ? " on " + b.where.replace(/^ssh:/, "") : "", running = SIGN_RUNNING.includes(s.state);
  const open = url => [el("a", { class: "signin-open", href: url, target: "_blank", rel: "noopener noreferrer" }, "Open sign-in page"), el("div", { class: "signin-url" }, url)];
  const body = [];
  if (s.state === "choose") body.push(el("div", { class: "note m-flat" }, "Which provider should Hermes sign in to?"), el("div", { class: "chips" }, HERMES_PROVIDERS.map(([id, label]) => el("button", { type: "button", class: "small", onclick: () => signStart(b, { provider: id }) }, label))));
  else if (s.state === "terminal") body.push(el("div", { class: "note m-flat" }, b.label + " signs in inside its own terminal" + where + ". Run this there, follow its steps, then come back and press Check:"),
    el("div", { class: "signin-cmd" }, el("code", {}, b.signin_command || ""), el("button", { type: "button", class: "small", onclick: () => copyText(b.signin_command || "", "Command") }, "Copy")));
  else if (s.state === "starting") body.push(el("div", { class: "note m-flat" }, "Starting " + (s.command ? "“" + s.command + "”" : b.label + "’s sign-in") + where + "…"));
  else if (s.state === "needs_browser") body.push(...open(s.url), el("div", { class: "note m-flat" }, "Sign in on that page. This finishes by itself when " + b.label + " hears back."));
  else if (s.state === "device_code") body.push(el("div", { class: "note m-flat" }, "Open the page and enter this code:"), el("div", { class: "signin-code" }, el("span", {}, s.code), el("button", { type: "button", class: "small", onclick: () => copyText(s.code, "Code") }, "Copy")), ...open(s.url),
    el("div", { class: "note m-flat" }, "This finishes by itself once the page accepts the code."));
  else if (s.state === "needs_code") { const field = el("input", { value: BR.codeDraft[b.id] || "", placeholder: "Paste the code the page shows", "aria-label": "Sign-in code", autocomplete: "off", spellcheck: "false",
      oninput: e => { BR.codeDraft[b.id] = e.target.value; }, onkeydown: e => { if (e.key === "Enter") signCode(b.id); } });
    body.push(...open(s.url), el("div", { class: "note m-flat", title: s.prompt || "" }, "After you sign in, the page shows a code: paste it here."), el("div", { class: "signin-paste" }, field, el("button", { type: "button", class: "primary", onclick: () => signCode(b.id) }, "Continue"))); }
  else if (s.state === "verifying") body.push(el("div", { class: "note m-flat" }, "Checking the code with " + b.label + "…"));
  else if (s.state === "done") body.push(el("div", { class: "note m-flat vt-ok" }, "Signed in" + where + "."));
  else if (s.state === "failed") body.push(el("div", { class: "note m-flat vt-bad" }, s.reason || "The sign-in stopped."));
  const lines = s.lines || [];
  return el("div", { class: "signin", "data-signin": b.id, "data-state": s.state },
    el("div", { class: "signin-head" }, el("b", {}, "Sign in to " + b.label + where), el("span", { class: "tag2" }, s.state.replace("_", " "))),
    ...body,
    lines.length ? el("details", { class: "fold signin-log", open: s.state === "failed" }, el("summary", {}, "What " + b.label + " says"), el("pre", {}, lines.join("\n"))) : null,
    b.id === "codex" && !where && running && s.state !== "device_code" ? el("button", { type: "button", class: "small ghost", onclick: async () => { await signCancel(b.id); signStart(b, { device: true }); } }, "Use a one-time code instead") : null,
    el("div", { class: "note m-flat signin-fine" }, "This is " + b.label + "’s own sign-in: it keeps its credentials where it always does. The studio stores nothing of it."),
    el("div", { class: "m-acts" }, running ? el("button", { type: "button", class: "small", onclick: () => signCancel(b.id) }, "Cancel")
      : el("button", { type: "button", class: "small", onclick: () => { BR.signOpen = null; if (s.state === "terminal" || s.state === "failed") brainStatus(b.id, true); signRedraw(); } }, s.state === "terminal" ? "Check" : "Close"),
      s.state === "failed" ? el("button", { type: "button", class: "small", onclick: () => signStart(b) }, "Try again") : null)); }
function viewBrains() {
  const wrap = el("div");
  const head = () => pageHead("Studio Vault", "Brains & tools", "Your own AI sign-ins, and the tools an agent may use.");
  if (!BR.data) { brainsLoad(); return el("div", {}, head(), el("div", { class: "note" }, "Looking for your sign-ins…")); }
  if (BR.data.error) return el("div", {}, head(), el("div", { class: "note" }, "This Studio server does not offer brains yet (" + BR.data.error + ")."));
  const d = BR.data, set = d.settings || {}, remote = set.remote || {};
  const host = el("input", { value: remote.host || "", placeholder: "you@your-mac" }), studioHost = el("input", { value: remote.studio_host || "", placeholder: "you@this-machine (optional)" });
  const save = async patch => { try { await api("/api/brains/settings", { ...set, ...patch }); toast("Saved"); BR.data = null; brainsLoad(); } catch (e) { toast(e.message); } };
  put(wrap, head(), typeof localModelsCard === "function" ? localModelsCard() : null,
    el("p", { class: "note m-flat m-setlink" }, "Sign-in help, the Hermes profile and every connection in one place: ", el("a", { href: "#Settings", onclick: e => { e.preventDefault(); typeof goSettings === "function" ? goSettings("connections") : go("Settings"); } }, "Settings → Connections"), "."),
    el("details", { class: "fold m-fold" }, el("summary", {}, "What is kept, and who pays"),
    el("div", { class: "note m-flat" }, "Studio uses the AI subscriptions you already pay for. Nothing is stored but which one you prefer: each answer is billed to your own sign-in, and a local model never leaves your network.")));
  put(wrap, el("div", { class: "tri" }, (d.backends || []).map(b => { const on = d.default === b.id;
    return el("div", { class: "brain" + (on ? " on" : "") },
      el("div", { class: "head" }, brainLogo(b.id), el("div", { class: "m-min" }, el("b", {}, b.label), el("span", {}, b.found ? (b.where && b.where !== "here" ? "signed in on " + b.where.replace(/^ssh:/, "") : "found on this machine") + (b.version ? " · " + b.version : "") : "not found"))),
      el("div", { class: "m-tags" }, el("span", { class: "tag2 " + (b.found ? "ok" : "") }, b.found ? "ready" : "not set up"), on ? el("span", { class: "tag2 t" }, "default") : null,
        b.kind === "http" && b.models && b.models.length ? el("span", { class: "tag2" }, b.models.length + " models") : null),
      !b.found && b.signin ? el("div", { class: "note m-flat" }, b.signin) : null,
      signLine(b),
      BR.said[b.id] ? el("div", { class: "note m-flat " + (BR.said[b.id].ok ? "vt-ok" : "vt-bad") }, BR.said[b.id].ok ? "Answered “" + (BR.said[b.id].text || "").slice(0, 40) + "” in " + (BR.said[b.id].elapsedMs / 1000).toFixed(1) + " s" : BR.said[b.id].error) : null,
      el("div", { class: "m-acts" }, el("button", { class: "small", disabled: !b.found || BR.testing[b.id], onclick: async () => { BR.testing[b.id] = true; render();
          try { BR.said[b.id] = await api("/api/brains/test", { backend: b.id }); } catch (e) { BR.said[b.id] = { ok: false, error: e.message }; } BR.testing[b.id] = false; render(); } }, BR.testing[b.id] ? "Asking…" : "Test"),
        ...signButtons(b),
        on ? null : el("button", { class: "small", disabled: !b.found, onclick: () => save({ default: b.id }) }, "Make default")),
      signPanel(b)); })));
  put(wrap, el("div", { class: "m-gap" },
    el("div", { class: "panel stack" }, el("h2", {}, "Use the sign-ins on another computer"),
      el("div", { class: "note m-flat" }, "Studio runs the CLI there over SSH, with key login only."),
      el("details", { class: "fold" }, el("summary", {}, "When to use this"), el("div", { class: "note m-flat" }, "A DGX Spark usually has no Claude or Codex sign-in of its own. Name the Mac (or any machine) where you are signed in, and Studio runs the CLI there over SSH. The Spark needs key login to it; no password is ever typed or kept here.")),
      field("SSH host", host), field("How that computer reaches this one (for Blender and Studio tools)", studioHost), el("div", { class: "chips" }, ["claude", "codex", "gemini", "grok", "hermes"].filter(id => id !== "hermes" || (d.backends || []).some(b => b.id === "hermes")).map(id => el("label", { class: "toggle m-flat" + ((remote.backends || []).includes(id) ? " on" : "") },
        el("input", { type: "checkbox", checked: (remote.backends || []).includes(id), class: "hide", onchange: e => { const list = new Set(remote.backends || []); e.target.checked ? list.add(id) : list.delete(id); remote.backends = [...list]; e.target.parentNode.classList.toggle("on", e.target.checked); } }),
        el("span", { class: "dotl" + ((remote.backends || []).includes(id) ? "" : " o") }), MARKS[id] ? mark(id, "") : null, ({ claude: "Claude Code", codex: "Codex", gemini: "Gemini CLI", grok: "Grok CLI", hermes: "Hermes" })[id]))),
      el("div", {}, el("button", { class: "primary", onclick: () => save({ remote: { host: host.value.trim(), backends: remote.backends || [], studio_host: studioHost.value.trim() } }) }, "Save")))));
  const tools = BR.tools;
  put(wrap, el("div", { class: "sectionhead" }, el("h2", {}, "Tools your agent can use"), el("button", { class: "small", onclick: () => go("Agent") }, "Ask your agent →")),
    !tools ? el("div", { class: "note" }, "This Studio server does not list agent tools yet.") : el("div", { class: "tri" }, (tools.tools || tools || []).map(t => {
      const said = BR.checked[t.id];
      return el("div", { class: "brain" }, el("div", { class: "head" }, toolLogo(t.id),
          el("div", { class: "m-min" }, el("b", {}, t.label || t.id), el("span", {}, (t.via === "ssh" ? "over SSH on " + (t.host || "your Mac") : "on this machine") + (t.enabled === false ? " · off" : "")))),
        el("div", { class: "note m-flat m-cmd" }, (t.command || []).map(a => /\/.+\//.test(a) && a.split("/").length > 3 ? "…/" + a.split("/").slice(-2).join("/") : a).join(" ")),
        said ? el("div", { class: "note m-flat " + (said.ok ? "vt-ok" : "vt-bad") }, said.ok ? (said.tools || []).length + " tools: " + (said.tools || []).slice(0, 6).join(", ") + ((said.tools || []).length > 6 ? "…" : "") : said.error) : null,
        el("div", { class: "m-acts" }, el("button", { class: "small", disabled: BR.checking[t.id], onclick: async () => { BR.checking[t.id] = true; render();
            try { BR.checked[t.id] = await api("/api/agent/tools/" + t.id + "/check", {}); } catch (e) { BR.checked[t.id] = { ok: false, error: e.message }; } BR.checking[t.id] = false; render(); } }, BR.checking[t.id] ? "Connecting…" : "Check"),
          t.id === "blender" ? el("button", { class: "small", onclick: async () => { try { const r = await api("/api/agent/tools/blender/start", {}); toast(r.message || (r.running ? "Blender is listening" : "Blender is starting")); } catch (e) { toast(e.message); } } }, "Start Blender") : null)); })));
  return wrap;
}
