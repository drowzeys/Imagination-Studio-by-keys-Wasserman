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
    // NVIDIA Nemotron 3.5 Bolt run by the Studio (app/bolt.py): what it holds of the memory, when loaded
    t.engines && t.engines.bolt && t.engines.bolt.managed ? row("Bolt brain", boltLine(t.engines.bolt) + " · " + (t.engines.bolt.backend || "")) : null,
    row("Workload governor", t.governor ? (t.governor.status || "answering") + (Array.isArray(t.governor.reservations) ? " · " + count(t.governor.reservations.length, "reservation") : "") + (t.governor.alert_temperature_c ? " · alert at " + t.governor.alert_temperature_c + "°C" : "") : "not on this machine", t.governor && t.governor.status && t.governor.status !== "ok" ? "warn" : ""),
    row("Processor", t.cpu ? "load " + (t.cpu.load1 ?? "–") + " on " + (t.cpu.cores || "?") + " cores" : "–"),
    row("Host", t.host || "–")].filter(Boolean); }
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
const BRAIN_MARK = { claude: "claude", codex: "codex", gemini: "gemini", grok: "grok", hermes: "hermes", openai: "openai", chatgpt: "openai" };
// the local brain wears NVIDIA's mark when it is the Studio's own Nemotron 3.5 Bolt engine
const isBolt = b => !!b && (!!b.engine || /nemotron|bolt/i.test(b.label || ""));
const brainMarkOf = (id, b) => id === "local" ? (isBolt(b || ((BR.data && BR.data.backends) || []).find(x => x.id === "local")) ? "nvidia" : null) : BRAIN_MARK[id] || null;
const brainLogo = (id, b) => el("span", { class: "logo" }, brainMarkOf(id, b) ? mark(brainMarkOf(id, b), "") : icon("machine"));
const toolLogo = id => el("span", { class: "logo" }, id === "studio" ? el("img", { src: "brand/aperture-mark-color.svg", alt: "Imagination Studio", width: 20, height: 20 }) : icon(id === "blockout" ? "library" : id === "blender" ? "previs" : "suite"));
async function brainsLoad() { try { BR.data = await api("/api/brains"); } catch (e) { BR.data = { error: e.message }; } try { BR.tools = await api("/api/agent/tools"); } catch { BR.tools = null; } if (tab === "Brains") render(); }
/* ---------- signing a brain in: the CLI's own login run by the studio (app/agents.py, the signin section) ----------
   BR.status[id] is the CLI's own word on its sign-in; BR.sign[id] follows a sign-in (starting, needs_browser, needs_code,
   device_code, verifying, done, failed), polled every second while it runs. Shown on Settings → Connections and Brains & tools. */
const SIGN_RUNNING = ["starting", "needs_browser", "needs_code", "device_code", "verifying"];
const SIGN_OUT = ["claude", "codex", "grok"], HERMES_PROVIDERS = [["openai-codex", "ChatGPT (Codex)"], ["anthropic", "Claude"], ["nous", "Nous Portal"]];
const signRedraw = () => { if (typeof CN !== "undefined" && CN.sheet) return cnSheetDraw(); if (tab === "Brains" || tab === "Settings") render(); };
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
// a CLI that is not there: install it here, or use it where it is over SSH; how it signs in once it is installed
const brainMissing = b => (b.where && b.where !== "here" ? "Not installed on " + b.where.replace(/^ssh:/, "") + ": install it there. " :
  "Not installed here: install it, or use it on another computer over SSH (Brains & tools → Use the sign-ins on another computer). ") + "Then: " + b.signin;
// the status line and the Sign in / Sign out buttons on a brain card
function signLine(b) { if (b.kind !== "cli") return null; const st = BR.status[b.id];
  if (b.found && !st) brainStatus(b.id);
  const on = st && st.state === "signed_in", text = !b.found ? null : !st || st.state === "asking" ? "Asking " + b.label + " whether it is signed in…" : on ? "Signed in" + (st.detail ? " · " + st.detail : "") : st.state === "signed_out" ? "Not signed in" : st.detail || "Sign-in unknown";
  if (st && st.state === "broken") return el("div", { class: "signin-state", "data-signin-state": "broken" }, el("div", { class: "note m-flat vt-bad signin-said" }, st.detail), st.hint ? el("div", { class: "note m-flat" }, st.hint) : null);
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
/* ---------- a QR code for a sign-in address (ISO/IEC 18004, byte mode, level M; drawn as inline SVG, no library) ---------- */
const QR_ECC = [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28];
const QR_BLOCKS = [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49];
const qrRaw = v => { let n = (16 * v + 128) * v + 64; if (v >= 2) { const a = Math.floor(v / 7) + 2; n -= (25 * a - 10) * a - 55; if (v >= 7) n -= 36; } return n; };
const qrData = v => Math.floor(qrRaw(v) / 8) - QR_ECC[v] * QR_BLOCKS[v];
const qrMul = (x, y) => { let z = 0; for (let i = 7; i >= 0; i--) { z = (z << 1) ^ ((z >>> 7) * 0x11D); z ^= ((y >>> i) & 1) * x; } return z; };
function qrDivisor(n) { const r = new Array(n).fill(0); r[n - 1] = 1; let root = 1;
  for (let i = 0; i < n; i++) { for (let j = 0; j < n; j++) { r[j] = qrMul(r[j], root); if (j + 1 < n) r[j] ^= r[j + 1]; } root = qrMul(root, 2); } return r; }
function qrRemainder(data, div) { const r = div.map(() => 0); for (const b of data) { const f = b ^ r.shift(); r.push(0); div.forEach((c, i) => { r[i] ^= qrMul(c, f); }); } return r; }
// the modules of the code for `text` (true is dark), or null when it is too long for one
function qrMatrix(text, forceMask) {
  const bytes = typeof TextEncoder !== "undefined" ? [...new TextEncoder().encode(text)] : [...unescape(encodeURIComponent(text))].map(c => c.charCodeAt(0));
  let v = 1; while (v <= 40 && 4 + (v < 10 ? 8 : 16) + 8 * bytes.length > qrData(v) * 8) v++;
  if (v > 40) return null;
  const bits = [], push = (val, len) => { for (let i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1); };
  push(4, 4); push(bytes.length, v < 10 ? 8 : 16); bytes.forEach(b => push(b, 8));
  const cap = qrData(v) * 8; push(0, Math.min(4, cap - bits.length)); push(0, (8 - bits.length % 8) % 8);
  for (let pad = 0xEC; bits.length < cap; pad ^= 0xEC ^ 0x11) push(pad, 8);
  const data = []; for (let i = 0; i < bits.length; i += 8) data.push(bits.slice(i, i + 8).reduce((a, b) => a * 2 + b, 0));
  // Reed-Solomon blocks, interleaved
  const nb = QR_BLOCKS[v], ecc = QR_ECC[v], raw = Math.floor(qrRaw(v) / 8), short = nb - raw % nb, slen = Math.floor(raw / nb), div = qrDivisor(ecc), blocks = [];
  for (let i = 0, k = 0; i < nb; i++) { const d = data.slice(k, k + slen - ecc + (i < short ? 0 : 1)); k += d.length; const e = qrRemainder(d, div); if (i < short) d.push(0); blocks.push(d.concat(e)); }
  const words = []; for (let i = 0; i < blocks[0].length; i++) blocks.forEach((b, j) => { if (i !== slen - ecc || j >= short) words.push(b[i]); });
  // the function patterns
  const size = v * 4 + 17, mod = [...Array(size)].map(() => new Array(size).fill(false)), fn = [...Array(size)].map(() => new Array(size).fill(false));
  const set = (x, y, dark) => { mod[y][x] = dark; fn[y][x] = true; };
  for (let i = 0; i < size; i++) { set(6, i, i % 2 === 0); set(i, 6, i % 2 === 0); }
  for (const [cx, cy] of [[3, 3], [size - 4, 3], [3, size - 4]]) for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) {
    const x = cx + dx, y = cy + dy, d = Math.max(Math.abs(dx), Math.abs(dy)); if (x >= 0 && x < size && y >= 0 && y < size) set(x, y, d !== 2 && d !== 4); }
  const align = v === 1 ? [] : (() => { const n = Math.floor(v / 7) + 2, step = Math.floor((v * 8 + n * 3 + 5) / (n * 4 - 4)) * 2, r = [6];
    for (let p = size - 7; r.length < n; p -= step) r.splice(1, 0, p); return r; })();
  align.forEach((ax, i) => align.forEach((ay, j) => { if ((i === 0 && j === 0) || (i === 0 && j === align.length - 1) || (i === align.length - 1 && j === 0)) return;
    for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) set(ax + dx, ay + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1); }));
  const format = mask => { const d = mask; let r = d; for (let i = 0; i < 10; i++) r = (r << 1) ^ ((r >>> 9) * 0x537); const b = ((d << 10) | r) ^ 0x5412, bit = i => ((b >>> i) & 1) === 1;  // level M: 00
    for (let i = 0; i <= 5; i++) set(8, i, bit(i)); set(8, 7, bit(6)); set(8, 8, bit(7)); set(7, 8, bit(8)); for (let i = 9; i < 15; i++) set(14 - i, 8, bit(i));
    for (let i = 0; i < 8; i++) set(size - 1 - i, 8, bit(i)); for (let i = 8; i < 15; i++) set(8, size - 15 + i, bit(i)); set(8, size - 8, true); };
  format(0);
  if (v >= 7) { let r = v; for (let i = 0; i < 12; i++) r = (r << 1) ^ ((r >>> 11) * 0x1F25); const b = (v << 12) | r;
    for (let i = 0; i < 18; i++) { const dark = ((b >>> i) & 1) === 1, a = size - 11 + i % 3, c = Math.floor(i / 3); set(a, c, dark); set(c, a, dark); } }
  // the data, in the zigzag
  let i = 0; for (let right = size - 1; right >= 1; right -= 2) { if (right === 6) right = 5;
    for (let vert = 0; vert < size; vert++) for (let j = 0; j < 2; j++) { const x = right - j, y = ((right + 1) & 2) === 0 ? size - 1 - vert : vert;
      if (!fn[y][x] && i < words.length * 8) { mod[y][x] = ((words[i >>> 3] >>> (7 - (i & 7))) & 1) === 1; i++; } } }
  const MASKS = [(x, y) => (x + y) % 2 === 0, (x, y) => y % 2 === 0, x => x % 3 === 0, (x, y) => (x + y) % 3 === 0, (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0,
    (x, y) => x * y % 2 + x * y % 3 === 0, (x, y) => (x * y % 2 + x * y % 3) % 2 === 0, (x, y) => ((x + y) % 2 + x * y % 3) % 2 === 0];
  const apply = m => { for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) if (!fn[y][x] && MASKS[m](x, y)) mod[y][x] = !mod[y][x]; };
  // the mask with the lowest penalty (runs, blocks, finder look-alikes, balance)
  const penalty = () => { let p = 0, dark = 0; const lines = [];
    for (let y = 0; y < size; y++) { lines.push(mod[y].map(b => b ? 1 : 0).join("")); lines.push(mod.map(r => r[y] ? 1 : 0).join("")); }
    for (const l of lines) { for (const run of l.match(/0{5,}|1{5,}/g) || []) p += run.length - 2; p += 40 * ((l.match(/(?=10111010000|00001011101)/g) || []).length); }
    for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) { if (mod[y][x]) dark++;
      if (x < size - 1 && y < size - 1 && mod[y][x] === mod[y][x + 1] && mod[y][x] === mod[y + 1][x] && mod[y][x] === mod[y + 1][x + 1]) p += 3; }
    return p + 10 * Math.floor(Math.abs(dark * 20 - size * size * 10) / (size * size)); };
  let best = forceMask, low = Infinity;
  if (best == null) for (let m = 0; m < 8; m++) { apply(m); format(m); const p = penalty(); if (p < low) { low = p; best = m; } apply(m); }
  apply(best); format(best);
  return mod;
}
// the code drawn as one SVG path, four modules of quiet light around it; a phone's camera opens the address
function qrSvg(text, px) { const m = qrMatrix(text); if (!m) return null; const n = m.length + 8; let d = "";
  m.forEach((row, y) => row.forEach((on, x) => { if (on) d += "M" + (x + 4) + " " + (y + 4) + "h1v1h-1z"; }));
  return svg("svg", { class: "cn-qr", viewBox: "0 0 " + n + " " + n, width: px || 168, height: px || 168, role: "img", "aria-label": "QR code of the sign-in page", "shape-rendering": "crispEdges" },
    svg("rect", { width: n, height: n, fill: "#fff" }), svg("path", { d, fill: "#000" })); }

/* ---------- Connect: one checklist per agent that the studio works through by itself (app/agents.py, the connect section) ----------
   Find it (install it when it is missing), sign in (the CLI's own flow: the page opens in a new tab, with the code, a QR
   code and Copy link), check it answers, make it the default. CN.data[id] is GET /api/brains/{id}/connect. */
const CN = { open: null, data: {}, plan: {}, install: {}, verifying: {}, tried: {}, timer: null, win: null, tick: 0, link: null, linkHost: null, linkSaid: null, linkBusy: false, linkOpen: false };
const CN_ORDER = ["claude", "codex", "hermes", "gemini", "grok"];
const cnRedraw = () => { if (typing()) return; if (CN.sheet) cnSheetDraw(); else if (["Brains", "Settings", "Home", "Agent"].includes(tab)) render(); };
const cnWhere = b => b && b.where && b.where !== "here" ? b.where.replace(/^ssh:/, "") : "";
const cnHere = () => typeof location === "undefined" || ["localhost", "127.0.0.1", "::1", "[::1]"].includes(location.hostname);
// the one word a card shows, and the next thing to do
function brainWord(b) { if (!b || b.kind !== "cli") return [b && b.found ? "Ready" : "Not answering", null];
  const st = BR.status[b.id], ok = CN.data[b.id] && CN.data[b.id].verify && CN.data[b.id].verify.ok, def = BR.data && BR.data.default === b.id;
  if (b.error) return ["Cannot start", "link", "Check the link"];
  if (!b.found) return ["Not installed", "connect", "Install"];
  if (!st || st.state === "asking") return ["Checking…", null];
  if (st.state === "broken" || st.state === "missing") return ["Cannot start", "connect", "Fix it"];
  if (st.state === "keychain") return ["Signed in elsewhere", "connect", "Allow over SSH"];
  if (st.state === "signed_in" || ok) return ["Ready", def ? null : "default", "Make default"];
  return ["Not signed in", "connect", "Connect"]; }
// the Connect button's click: a tab is opened now (a click may open one, a timer may not) and pointed at the sign-in page later
function cnPrepareTab(b) { const st = BR.status[b.id];
  if (b.id === "hermes" && !CN.hermesProvider) return;  // the tab opens on the provider's click, not on Connect's
  if (b.found && b.signin_flow === "pty" && !(st && st.state === "signed_in") && typeof window !== "undefined" && typeof window.open === "function") {
    try { const w = window.open("", "_blank"); if (w) { CN.win = w; try { w.document.title = "Signing in…"; w.document.body.innerHTML = "<p style='font:16px system-ui;padding:24px'>Opening the sign-in page…</p>"; } catch {} } } catch {} } }
function cnCloseTab() { if (CN.win) { try { CN.win.close(); } catch {} CN.win = null; } }
async function connectStart(b, fromClick) { if (fromClick !== false) cnPrepareTab(b);
  CN.open = b.id; CN.tried[b.id] = {}; BR.signOpen = null; await cnLoad(b.id); }
// from anywhere (Home, the Agent room, Slate's Bible): the same checklist over the page, nothing to go and find
function connectGo(b) { connectOpen(b.id || b); }
async function connectOpen(id) { if (!BR.data) await brainsLoad();
  const b = cnBrain(id) || { id, label: id, kind: "cli", found: false, where: "here", signin_flow: "pty" };
  cnPrepareTab(b); CN.sheet = id; cnSheetDraw(); await connectStart(b, false); cnSheetDraw(); }
function cnSheetDraw() { if (!CN.sheet || typeof document === "undefined" || !document.body || typeof document.body.append !== "function") return;
  const id = CN.sheet, b = cnBrain(id) || { id, label: id, kind: "cli", where: "here" };
  const panel = connectPanel(b) || el("div", { class: "signin connect" }, el("div", { class: "note m-flat" }, "Looking…"));
  const node = el("div", { class: "gate-wrap cn-sheet", "data-sheet": id, onclick: e => { if (e.target === node) cnClose(id); } },
    el("div", { class: "gate-sheet", role: "dialog", "aria-modal": "true", "aria-label": "Connect " + (b.label || id) }, panel));
  if (CN.sheetNode && CN.sheetNode.replaceWith) CN.sheetNode.replaceWith(node); else document.body.append(node);
  CN.sheetNode = node; }
async function cnLoad(id) { try { CN.data[id] = await api("/api/brains/" + id + "/connect"); } catch (e) { CN.data[id] = { error: e.message }; }
  const d = CN.data[id]; if (d && d.signin) BR.status[id] = { backend: id, ...d.signin }; if (d && d.signing && d.signing.state !== "idle") BR.sign[id] = d.signing;
  if (d && !d.error && !d.find.found && !CN.plan[id]) { try { CN.plan[id] = (await api("/api/brains/" + id + "/install?plan=1")).plan; } catch (e) { CN.plan[id] = { ok: false, reason: e.message }; } }
  cnAdvance(id); cnRedraw(); }
// the driver: what can run next runs, once each, until the step needs the person
function cnAdvance(id) { const d = CN.data[id], b = cnBrain(id), tried = CN.tried[id] || (CN.tried[id] = {}); if (!d || d.error || CN.open !== id) return cnWatch();
  const sign = BR.sign[id], st = d.signin || {};
  if (!d.find.found) { cnCloseTab(); return cnWatch(); }
  if (st.state !== "signed_in") {
    if (sign && SIGN_RUNNING.includes(sign.state)) return cnWatch();
    // Hermes signs in to a provider the person picks (cnSteps shows the choice): it is never started without one
    if (b && b.signin_flow === "pty" && id !== "hermes" && !["broken", "missing"].includes(st.state) && !tried.signin) { tried.signin = true; cnSignin(id); }
    return cnWatch(); }
  cnCloseTab();
  if (!d.verify && !tried.verify) { tried.verify = true; cnVerify(id); return; }
  if (d.verify && d.verify.ok && !d.default && !d.default_ready && !tried.default) { tried.default = true; cnDefault(id); }
  cnWatch(); }
const cnName = id => { const b = ((BR.data && BR.data.backends) || []).find(x => x.id === id); return b ? b.label : id || "Another brain"; };
const cnBrain = id => ((BR.data && BR.data.backends) || []).find(x => x.id === id) || (CN.data[id] ? { id, label: CN.data[id].label, kind: "cli", found: CN.data[id].find && CN.data[id].find.found, where: CN.data[id].where, signin_flow: "pty" } : null);
async function cnSignin(id, extra) { const b = cnBrain(id); if (!b) return;
  const body = { ...(extra || {}) }; if (id === "codex" && (!cnHere() || cnWhere(b))) body.device = true;  // a localhost callback only reaches a browser on this computer
  BR.sign[id] = { state: "starting", lines: [] }; cnRedraw();
  try { BR.sign[id] = await api("/api/brains/" + id + "/signin", body); } catch (e) { BR.sign[id] = { state: "failed", reason: e.message, lines: [] }; }
  cnPoint(id); cnWatch(); cnRedraw(); }
// the tab opened on the click goes to the sign-in page as soon as the CLI prints it
function cnPoint(id) { const s = BR.sign[id]; if (CN.win && s && s.url && ["needs_browser", "needs_code", "device_code"].includes(s.state)) { try { CN.win.location.href = s.url; CN.win.opener = null; } catch {} CN.win = null; } }
async function cnVerify(id) { CN.verifying[id] = true; cnRedraw();
  try { const r = await api("/api/brains/" + id + "/verify", {}); if (CN.data[id]) CN.data[id].verify = r; } catch (e) { if (CN.data[id]) CN.data[id].verify = { ok: false, error: e.message, fix: { action: "verify", label: "Check again" } }; }
  CN.verifying[id] = false; cnAdvance(id); cnRedraw(); }
async function cnDefault(id) { const d = CN.data[id];
  try { const r = await api("/api/brains/" + id + "/default", {}); if (BR.data) { BR.data.default = r.settings.default; BR.data.settings = r.settings; } if (d) { d.default = true; d.agent_default = r.settings.agent === id; } }
  catch (e) { toast(e.message); } cnRedraw(); }
async function cnInstall(id) { CN.install[id] = { state: "starting", lines: [] }; cnRedraw();
  try { CN.install[id] = await api("/api/brains/" + id + "/install", {}); } catch (e) { CN.install[id] = { state: "failed", reason: e.message, lines: [] }; }
  cnWatch(); cnRedraw(); }
// one timer follows whatever is running: the installer, the sign-in, a sign-in that finishes in another terminal
function cnWatch() { const id = CN.open, d = id && CN.data[id];
  const inst = id && CN.install[id], sign = id && BR.sign[id], b = id && cnBrain(id);
  const live = d && !d.error && ((inst && ["starting", "running"].includes(inst.state)) || (sign && SIGN_RUNNING.includes(sign.state)) ||
    (d.find.found && d.signin && d.signin.state !== "signed_in" && b && b.signin_flow !== "pty"));
  if (!live) { clearInterval(CN.timer); CN.timer = null; return; }
  if (CN.timer) return;
  CN.timer = setInterval(async () => { const id = CN.open; if (!id) { clearInterval(CN.timer); CN.timer = null; return; } CN.tick++;
    const inst = CN.install[id], sign = BR.sign[id];
    if (inst && ["starting", "running"].includes(inst.state)) { try { CN.install[id] = await api("/api/brains/" + id + "/install"); } catch {}
      if (["done", "failed"].includes(CN.install[id].state)) { clearInterval(CN.timer); CN.timer = null; BR.data = null; await brainsLoad(); if (CN.install[id].state === "done") { BR.status[id] = null; await cnLoad(id); } } cnRedraw(); return; }
    if (sign && SIGN_RUNNING.includes(sign.state)) { let now; try { now = await api("/api/brains/" + id + "/signin"); } catch (e) { now = { state: "failed", reason: e.message, lines: [] }; }
      const changed = JSON.stringify(now) !== JSON.stringify(sign); BR.sign[id] = now; cnPoint(id);
      if (!SIGN_RUNNING.includes(now.state)) { clearInterval(CN.timer); CN.timer = null; if (now.state === "done") { toast("Signed in"); BR.status[id] = null; await cnLoad(id); } }
      if (changed) cnRedraw(); return; }
    if (CN.tick % 4 === 0) { clearInterval(CN.timer); CN.timer = null; await cnLoad(id); } }, 1250);
  if (CN.timer && CN.timer.unref) CN.timer.unref(); }
function cnClose(id) { if (BR.sign[id] && SIGN_RUNNING.includes(BR.sign[id].state)) signCancel(id); CN.open = null; cnCloseTab(); clearInterval(CN.timer); CN.timer = null;
  if (CN.sheet) { CN.sheet = null; if (CN.sheetNode && CN.sheetNode.remove) CN.sheetNode.remove(); CN.sheetNode = null; BR.status = {}; BR.data = null; brainsLoad().then(() => render()); return; }
  render(); }
// the sign-in's own parts: the page (opened already, or a big button), the code, a QR code, Copy link
function cnSignBody(b, s) { const where = cnWhere(b) ? " on " + cnWhere(b) : "", out = [];
  const page = url => el("div", { class: "cn-page" }, el("div", { class: "stack" }, el("a", { class: "signin-open", href: url, target: "_blank", rel: "noopener noreferrer" }, "Open the sign-in page"),
      el("div", { class: "chips" }, el("button", { type: "button", class: "small", onclick: () => copyText(url, "Link") }, "Copy link")),
      el("div", { class: "note m-flat" }, "On your phone? Scan the code.")), qrSvg(url, 152));
  if (s.state === "starting") out.push(el("div", { class: "note m-flat" }, "Starting " + b.label + "’s sign-in" + where + "…"));
  else if (s.state === "needs_browser") out.push(page(s.url), el("div", { class: "note m-flat" }, "Sign in on that page. This ticks by itself when " + b.label + " hears back."));
  else if (s.state === "device_code") out.push(el("div", { class: "note m-flat" }, "Enter this code on the sign-in page:"),
    el("div", { class: "signin-code cn-code" }, el("span", {}, s.code), el("button", { type: "button", class: "small", onclick: () => copyText(s.code, "Code") }, "Copy code")), page(s.url),
    el("div", { class: "note m-flat" }, "This ticks by itself once the page accepts the code."));
  else if (s.state === "needs_code") { const field = el("input", { value: BR.codeDraft[b.id] || "", placeholder: "Paste the code the page shows", "aria-label": "Sign-in code", autocomplete: "off", spellcheck: "false",
      oninput: e => { BR.codeDraft[b.id] = e.target.value; }, onkeydown: e => { if (e.key === "Enter") signCode(b.id).then(cnWatch); } });
    out.push(page(s.url), el("div", { class: "note m-flat" }, "After you sign in, the page shows a code: paste it here and the studio hands it to " + b.label + "."),
      el("div", { class: "signin-paste" }, field, el("button", { type: "button", class: "primary", onclick: () => signCode(b.id).then(cnWatch) }, "Continue"))); }
  else if (s.state === "verifying") out.push(el("div", { class: "note m-flat" }, "Handing the code to " + b.label + "…"));
  else if (s.state === "failed") out.push(el("div", { class: "note m-flat vt-bad" }, s.reason || "The sign-in stopped."), el("div", {}, el("button", { type: "button", class: "small", onclick: () => cnSignin(b.id) }, "Try again")));
  if (s.token) out.push(el("div", { class: "note m-flat" }, s.token === "saved" ? "The sign-in token is kept on that Mac, readable only by you." : "The token this makes is kept only on that Mac (readable only by you), never in the studio."));
  if (b.id === "codex" && !where && cnHere() && SIGN_RUNNING.includes(s.state) && s.state !== "device_code")
    out.push(el("button", { type: "button", class: "small ghost", onclick: async () => { await signCancel(b.id); cnSignin(b.id, { device: true }); } }, "Use a one-time code instead"));
  if ((s.lines || []).length) out.push(el("details", { class: "fold signin-log", open: s.state === "failed" }, el("summary", {}, "What " + b.label + " says"), el("pre", {}, s.lines.join("\n"))));
  return out; }
// each step: its state (todo, running, wait, done, failed, skip), a line and what to do
function cnSteps(b, d) { const where = cnWhere(b), on = where ? "on " + where : "on this computer", st = d.signin || {}, sign = BR.sign[b.id], inst = CN.install[b.id], plan = CN.plan[b.id];
  const steps = [];
  // a. find it
  if (d.find.found) steps.push(["find", "Find it", "done", d.find.chatgpt ? "Uses the ChatGPT app's own Codex: nothing to install." : "Found " + on + (d.find.version ? " · " + d.find.version : "")]);
  else if (d.find.error) steps.push(["find", "Find it", "failed", d.find.error, el("button", { type: "button", class: "small", onclick: () => { CN.linkOpen = true; render(); } }, "Check the link")]);
  else if (inst && ["starting", "running"].includes(inst.state)) steps.push(["find", "Install it", "running", "Installing with " + (inst.by || "its installer") + " " + on + "…",
    el("code", { class: "cn-cmd" }, inst.command || ""), (inst.lines || []).length ? el("pre", { class: "cn-log" }, inst.lines.slice(-6).join("\n")) : null]);
  else if (inst && inst.state === "failed") steps.push(["find", "Install it", "failed", inst.reason || "The installer stopped.", el("button", { type: "button", class: "small", onclick: () => cnInstall(b.id) }, "Try again"),
    (inst.lines || []).length ? el("details", { class: "fold" }, el("summary", {}, "What the installer said"), el("pre", {}, inst.lines.join("\n"))) : null]);
  else steps.push(["find", "Install it", "wait", "Not installed " + on + ".", !plan ? el("span", { class: "note m-flat" }, "Looking for npm, Homebrew or curl…")
    : plan.ok ? [el("div", { class: "note m-flat" }, "Install runs " + b.label + "’s own installer (" + plan.by + ") " + on + ", without administrator rights:"), el("code", { class: "cn-cmd" }, plan.command),
      el("div", {}, el("button", { type: "button", class: "primary", onclick: () => cnInstall(b.id) }, "Install " + b.label))]
      : el("div", { class: "note m-flat vt-bad" }, plan.reason)]);
  // b. sign in
  const found = d.find.found;
  if (!found) steps.push(["signin", "Sign in", "todo", "After it is installed."]);
  else if (st.state === "signed_in") steps.push(["signin", "Sign in", "done", "Signed in" + (st.detail ? " · " + st.detail : "") + (where ? " on " + where : "")]);
  else if (sign && sign.state !== "idle" && !(sign.state === "done")) steps.push(["signin", d.token_flow ? "Allow it over SSH" : "Sign in", sign.state === "failed" ? "failed" : "running", "", ...cnSignBody(b, sign)]);
  else if (st.state === "keychain") steps.push(["signin", "Allow it over SSH", "wait", st.detail, el("div", { class: "note m-flat" }, "macOS keeps Claude Code’s sign-in in that Mac’s Keychain, which an SSH session cannot open. Allow over SSH signs in once more and keeps a sign-in token only on that Mac, in your own folder, readable only by you."),
    el("div", {}, el("button", { type: "button", class: "primary", onclick: () => { cnPrepareTab(b); cnSignin(b.id); } }, "Allow over SSH"))]);
  else if (st.state === "broken") steps.push(["signin", "Sign in", "failed", st.detail, st.hint ? el("div", { class: "note m-flat" }, st.hint) : null, el("button", { type: "button", class: "small", onclick: () => cnInstall(b.id) }, "Update " + b.label)]);
  else if (b.signin_flow !== "pty") steps.push(["signin", "Sign in", "wait", b.label + " signs in inside its own terminal" + (where ? " on " + where : "") + ". Run this there; this ticks by itself when it is done:",
    el("div", { class: "signin-cmd" }, el("code", {}, b.signin_command || ""), el("button", { type: "button", class: "small", onclick: () => copyText(b.signin_command || "", "Command") }, "Copy"))]);
  else if (b.id === "hermes") steps.push(["signin", "Sign in", "wait", st.state === "asking" || !st.state ? "Asking whether it is signed in…" : "Which account should Hermes use? It signs in there, then picks a model that account has.",
    el("div", { class: "chips" }, HERMES_PROVIDERS.map(([provider, label]) => el("button", { type: "button", class: "small", onclick: () => { CN.hermesProvider = provider; cnPrepareTab(b); cnSignin(b.id, { provider }); } }, label)))]);
  else steps.push(["signin", "Sign in", "wait", st.state === "asking" || !st.state ? "Asking whether it is signed in…" : "Not signed in yet.", el("div", {}, el("button", { type: "button", class: "primary", onclick: () => { cnPrepareTab(b); cnSignin(b.id); } }, "Sign in"))]);
  // c. one real answer
  const v = d.verify, signed = st.state === "signed_in";
  if (CN.verifying[b.id]) steps.push(["verify", "Check it answers", "running", "Asking " + b.label + " to say ok (one short answer, a minute at most)…"]);
  else if (v && v.ok) steps.push(["verify", "Check it answers", "done", "Answered “" + v.text + "” in " + ((v.elapsedMs || 0) / 1000).toFixed(1) + " s"]);
  else if (v) { const fix = v.fix || { action: "verify", label: "Check again" }, act = { install: () => cnInstall(b.id), signin: () => { cnPrepareTab(b); cnSignin(b.id, b.id === "hermes" && CN.hermesProvider ? { provider: CN.hermesProvider } : undefined); }, link: () => { CN.linkOpen = true; render(); }, verify: () => cnVerify(b.id) }[fix.action];
    steps.push(["verify", "Check it answers", "failed", "", el("div", { class: "note m-flat vt-bad cn-error" }, v.error), act ? el("div", {}, el("button", { type: "button", class: "primary", onclick: act }, fix.label)) : el("div", { class: "note m-flat" }, fix.label)]); }
  else steps.push(["verify", "Check it answers", signed ? "wait" : "todo", signed ? "" : "After it is signed in.", signed ? el("button", { type: "button", class: "small", onclick: () => cnVerify(b.id) }, "Check") : null]);
  // d. the default
  const names = d.harness ? "your default brain and the Agent room's" : "your default brain";
  if (d.default) steps.push(["default", "Make it your default", "done", "It is " + (d.harness && !d.agent_default ? "your default brain" : names) + "."]);
  else if (v && v.ok) steps.push(["default", "Make it your default", "wait", cnName(BR.data && BR.data.default) + " stays your default.", el("button", { type: "button", class: "small", onclick: () => cnDefault(b.id) }, "Make " + b.label + " the default")]);
  else steps.push(["default", "Make it your default", "todo", "Once it answers."]);
  return steps; }
function connectPanel(b) { if (CN.open !== b.id) return null; const d = CN.data[b.id], where = cnWhere(b);
  const head = el("div", { class: "signin-head" }, el("b", {}, "Connect " + b.label + (where ? " on " + where : "")), el("button", { type: "button", class: "small", onclick: () => cnClose(b.id) }, "Close"));
  if (!d) return el("div", { class: "signin connect", "data-connect": b.id }, head, el("div", { class: "note m-flat" }, "Looking…"));
  if (d.error) return el("div", { class: "signin connect", "data-connect": b.id }, head, el("div", { class: "note m-flat vt-bad" }, d.error));
  const steps = cnSteps(b, d), all = steps.every(s => s[2] === "done") || (steps.slice(0, 3).every(s => s[2] === "done") && steps[3][2] === "wait");
  return el("div", { class: "signin connect", "data-connect": b.id, "data-done": all ? "yes" : "no" }, head,
    el("ol", { class: "cn-steps" }, steps.map(([key, name, state, line, ...more]) => el("li", { class: "cn-step " + state, "data-step": key, "data-state": state },
      el("span", { class: "cn-tick", "aria-hidden": "true" }, state === "done" ? "✓" : state === "failed" ? "!" : state === "running" ? "…" : ""),
      el("div", { class: "cn-body" }, el("b", {}, name), line ? el("span", { class: "cn-said" }, line) : null, ...more.flat())))),
    all ? el("div", { class: "note m-flat vt-ok" }, b.label + " is ready" + (d.harness ? ": the Agent room can use it with the Studio’s tools." : ".")) : null,
    el("div", { class: "note m-flat signin-fine" }, "Each step is " + b.label + "’s own: its installer, its sign-in, one short answer billed to your subscription. The studio keeps none of its sign-in.")); }
// a brain's card: its mark, one word, the one next thing; the rest under Details
function connectCard(b, extra) { const [word, action, label] = brainWord(b), where = cnWhere(b), said = BR.said[b.id], def = BR.data && BR.data.default === b.id;
  if (b.kind === "cli" && b.found && !BR.status[b.id]) brainStatus(b.id);
  const next = action === "connect" || action === "link" ? el("button", { type: "button", class: "small on", onclick: () => action === "link" ? (CN.linkOpen = true, render()) : connectStart(b) }, label)
    : action === "default" ? el("button", { type: "button", class: "small", onclick: () => cnDefault(b.id) }, label) : null;
  const st = BR.status[b.id];
  return el("div", { class: "brain cn-card" + (def ? " on" : "") + (CN.open === b.id ? " cn-open" : ""), "data-brain": b.id, "data-word": word },
    el("div", { class: "head" }, brainLogo(b.id), el("div", { class: "m-min" }, el("b", {}, b.label), el("span", { class: "cn-word " + (word === "Ready" ? "ok" : word === "Checking…" ? "" : "bad") }, word, where && b.found ? " · on " + where : ""))),
    def ? el("div", { class: "m-tags" }, el("span", { class: "tag2 t" }, "default")) : null,
    CN.open === b.id ? null : el("div", { class: "m-acts" }, next, word === "Ready" ? el("button", { type: "button", class: "small", onclick: () => connectStart(b) }, "Check") : null),
    connectPanel(b),
    el("details", { class: "fold cn-details" }, el("summary", {}, "Details"),
      el("div", { class: "note m-flat" }, setWhere(b)), !b.found && !b.error && b.signin ? el("div", { class: "note m-flat" }, brainMissing(b)) : null, b.path ? el("div", { class: "note m-flat m-cmd" }, relHome(b.path)) : null,
      st && st.detail ? el("div", { class: "note m-flat" }, st.detail) : null, st && st.hint ? el("div", { class: "note m-flat" }, st.hint) : null,
      b.note ? el("div", { class: "note m-flat" }, b.note) : null, b.error ? el("div", { class: "note m-flat vt-bad" }, b.error) : null,
      b.signin_command ? el("div", { class: "note m-flat" }, "In a terminal instead: ", el("code", {}, b.signin_command)) : null,
      ...(extra || []),
      said ? el("div", { class: "note m-flat " + (said.ok ? "vt-ok" : "vt-bad") }, said.ok ? "Answered “" + (said.text || "").slice(0, 40) + "” in " + ((said.elapsedMs || 0) / 1000).toFixed(1) + " s" : said.error) : null,
      el("div", { class: "m-acts" }, el("button", { type: "button", class: "small", disabled: !b.found || BR.testing[b.id], onclick: async () => { BR.testing[b.id] = true; render();
        try { BR.said[b.id] = await api("/api/brains/test", { backend: b.id }); } catch (e) { BR.said[b.id] = { ok: false, error: e.message }; } BR.testing[b.id] = false; render(); } }, BR.testing[b.id] ? "Asking…" : "Test"),
        st && st.state === "signed_in" && SIGN_OUT.includes(b.id) ? el("button", { type: "button", class: "small", onclick: () => signOut(b) }, "Sign out") : null))); }

/* ---------- Link another computer: this studio's key, the one line to run there, a Test, then its agents ---------- */
async function linkLoad() { try { CN.link = await api("/api/brains/link"); } catch (e) { CN.link = { error: e.message }; } if (CN.linkHost === null) CN.linkHost = (CN.link && CN.link.host) || ""; cnRedraw(); }
async function linkDo(path, body) { CN.linkBusy = true; render();
  try { const r = await api(path, body); if (path.endsWith("/key")) CN.link = r; else if (path.endsWith("/test")) CN.linkSaid = r;
    else if (path.endsWith("/back")) { CN.linkBack = r; if (r.ok) { CN.link = { ...CN.link, studio_host: r.studio_host }; BR.data = null; await brainsLoad(); } }
    else { CN.link = r; CN.linkSaid = { ...CN.linkSaid, saved: r.found }; CN.linkBack = r.back || null; BR.data = null; BR.status = {}; await brainsLoad(); } }
  catch (e) { CN.linkSaid = { ok: false, state: "error", detail: e.message }; } CN.linkBusy = false; render(); }
function linkPanel() { if (!CN.link) { linkLoad(); return el("section", { class: "panel st-panel cn-link" }, el("h2", {}, "Link another computer"), el("div", { class: "note m-flat" }, "Looking for this studio's key…")); }
  const L = CN.link, said = CN.linkSaid, host = CN.linkHost || "", tested = said && said.ok && said.host === host;
  const field1 = el("input", { value: host, placeholder: "you@your-mac.local", "aria-label": "The other computer", autocomplete: "off", spellcheck: "false", oninput: e => { CN.linkHost = e.target.value.trim(); } });
  const step = (n, title, state, ...kids) => el("li", { class: "cn-step " + state, "data-step": "link-" + n, "data-state": state }, el("span", { class: "cn-tick", "aria-hidden": "true" }, state === "done" ? "✓" : state === "failed" ? "!" : ""), el("div", { class: "cn-body" }, el("b", {}, title), ...kids.flat()));
  const found = (said && said.saved) || null, agents = found ? found.map(id => cnBrain(id)).filter(Boolean) : [];
  return el("section", { class: "panel st-panel cn-link", "data-card": "link" },
    el("div", { class: "st-ph" }, el("h2", {}, "Link another computer"), el("p", { class: "note m-flat" }, "Use the agents signed in on another computer (a Mac beside a DGX Spark, say). The studio runs them there over SSH with its own key; no password is typed or kept.")),
    L.error ? el("div", { class: "note m-flat vt-bad" }, L.error) : el("ol", { class: "cn-steps" },
      step(1, "This studio's key", L.has_key ? "done" : "wait", L.has_key ? el("span", { class: "cn-said" }, "Ready (" + L.key_file + ")") : [el("span", { class: "cn-said" }, "This computer has no SSH key yet."),
        el("div", {}, el("button", { type: "button", class: "primary", disabled: CN.linkBusy, onclick: () => linkDo("/api/brains/link/key", {}) }, "Make a key"))]),
      step(2, "Let the other computer in", L.has_key ? (tested ? "done" : "wait") : "todo", el("span", { class: "cn-said" }, "On the other computer, open Terminal and run this one line. On a Mac, first turn on System Settings → General → Sharing → Remote Login."),
        L.command ? el("div", { class: "signin-cmd" }, el("code", { class: "cn-cmd" }, L.command), el("button", { type: "button", class: "small", onclick: () => copyText(L.command, "Command") }, "Copy")) : null),
      step(3, "Test the link", tested ? "done" : said && !said.ok ? "failed" : L.has_key ? "wait" : "todo", el("div", { class: "st-lrow" }, field1,
          el("button", { type: "button", class: "small on", disabled: CN.linkBusy || !L.has_key, onclick: () => linkDo("/api/brains/link/test", { host: CN.linkHost }) }, CN.linkBusy ? "Testing…" : "Test")),
        said && said.host === host ? el("div", { class: "note m-flat " + (said.ok ? "vt-ok" : "vt-bad") }, said.ok ? "Reached " + said.host + (said.os ? " (" + ({ Darwin: "macOS", Linux: "Linux" }[said.os] || said.os) + ")" : "") + "." : said.detail) : null,
        said && said.state === "unknown_host" && said.host === host ? [said.fingerprint ? el("div", { class: "note m-flat" }, "Its key fingerprint: ", el("code", {}, said.fingerprint), ". On that computer, `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` shows the same if it is the one you mean.") : null,
          el("button", { type: "button", class: "small", onclick: () => linkDo("/api/brains/link/test", { host: CN.linkHost, trust: true }) }, "Trust this computer and test")] : null),
      step(4, "Use its agents", found ? "done" : tested ? "wait" : "todo", tested && !found ? el("div", {}, el("button", { type: "button", class: "primary", disabled: CN.linkBusy, onclick: () => linkDo("/api/brains/link/save", { host: CN.linkHost }) }, "Use " + host + " for its agents")) : null,
        found ? [el("span", { class: "cn-said" }, found.length ? "Its agents now run there: connect each one, and the studio installs, signs in and checks it over SSH." : "No agent is installed there yet."),
          el("div", { class: "chips" }, agents.map(b => el("button", { type: "button", class: "small", onclick: () => connectStart(b) }, "Connect " + b.label)))] : null,
        L.host && !found ? el("span", { class: "note m-flat" }, "Now: " + L.host + " runs " + ((L.backends || []).join(", ") || "nothing") + ".") : null),
      linkBackStep(L, step))); }
// 5: the way back, so an agent there can start the tools that run here (Blender, the Studio's own)
function linkBackStep(L, step) { const host = L.host || CN.linkHost, back = CN.linkBack, set = L.studio_host || (back && back.ok && back.studio_host);
  if (!host) return step(5, "Let it reach this studio", "todo", el("span", { class: "cn-said" }, "After it is linked."));
  if (set) return step(5, "Let it reach this studio", "done", el("span", { class: "cn-said" }, host + " reaches this computer as " + set + ": its agents can use Blender and the Studio's tools."));
  return step(5, "Let it reach this studio", back && !back.ok ? (back.state === "allow" || back.state === "no_sshd" ? "wait" : "failed") : "wait",
    el("span", { class: "cn-said" }, back && !back.ok ? back.detail : "An agent on " + host + " starts Blender and the Studio's tools over SSH back to this computer. The studio finds the way."),
    el("div", { class: "chips" },
      el("button", { type: "button", class: back && back.state === "allow" ? "small" : "small on", disabled: CN.linkBusy, onclick: () => linkDo("/api/brains/link/back", { host }) }, CN.linkBusy ? "Looking…" : back ? "Check again" : "Find the way back"),
      back && back.state === "allow" ? el("button", { type: "button", class: "primary", disabled: CN.linkBusy, title: "Adds " + host + "'s public key to this computer's ~/.ssh/authorized_keys and has it trust this computer's host key once",
        onclick: () => linkDo("/api/brains/link/back", { host, allow: true }) }, "Allow " + host) : null)); }

/* ---------- "Connect your agent": on Home and in the Agent room while no agent is signed in ---------- */
const CN_DISMISS = "studio-connect-dismissed";
const cnDismissed = () => { try { return localStorage.getItem(CN_DISMISS) === "1"; } catch { return false; } };
function connectNudge(where) { if (typeof BR === "undefined") return null;
  if (!BR.data) { if (!BR.loadingNudge) { BR.loadingNudge = true; brainsLoad().then(() => { BR.loadingNudge = false; cnRedraw(); }); } return null; }
  if (BR.data.error || (where === "home" && cnDismissed())) return null;
  const clis = (BR.data.backends || []).filter(b => b.kind === "cli");
  for (const b of clis) if (b.found && !b.error && !BR.status[b.id]) brainStatus(b.id).then(cnRedraw, () => {});
  if (clis.some(b => b.found && (!BR.status[b.id] || BR.status[b.id].state === "asking"))) return null;  // not yet known: nothing flashes up
  if (clis.some(b => BR.status[b.id] && BR.status[b.id].state === "signed_in")) return null;
  const order = CN_ORDER.map(id => clis.find(b => b.id === id)).filter(Boolean), pick = order.find(b => b.found) || order[0];
  const locals = (BR.data.backends || []).filter(b => (b.id === "local" || b.local_agent) && b.found && !b.error);  // the local brain and each local agent added
  if (!pick) return null;
  return el("section", { class: "panel cn-nudge", "data-card": "connect", "data-where": where },
    el("div", { class: "cn-nudge-h" }, el("div", { class: "m-min" }, el("span", { class: "eyebrow" }, "One click"), el("h2", {}, "Connect your agent"),
      el("p", { class: "note m-flat" }, "Claude Code, Codex, Hermes, Gemini or Grok, with the subscription you already have. The studio finds it (or installs it), signs you in, checks it answers and makes it your default.")),
      where === "home" ? el("button", { type: "button", class: "small ghost", "aria-label": "Hide this card", onclick: () => { try { localStorage.setItem(CN_DISMISS, "1"); } catch {} render(); } }, "Not now") : null),
    el("div", { class: "chips" }, el("button", { type: "button", class: "primary", onclick: () => connectGo(pick) }, brainLogo(pick.id), "Connect " + pick.label),
      order.length > 1 || locals.length ? el("span", { class: "cn-or" }, "or") : null,
      order.filter(b => b !== pick).map(b => el("button", { type: "button", class: "small cn-alt", onclick: () => connectGo(b) }, brainMarkOf(b.id, b) ? mark(brainMarkOf(b.id, b), "") : null, b.label)),
      locals.map(b => el("button", { type: "button", class: "small cn-alt" + (BR.data.default === b.id ? " on" : ""), "aria-pressed": BR.data.default === b.id ? "true" : "false", onclick: () => cnUseLocal(b) },
        brainMarkOf(b.id, b) ? mark(brainMarkOf(b.id, b), "") : null, localName(b) + (BR.data.default === b.id ? " · in use" : ""))))); }
// the local brain needs no sign-in: choosing it makes it the default brain (and the Agent room's, when it drives the Studio)
const localName = b => (b.label || "Local model").replace(/ \(Studio engine\)$/, "") + " (local)";
async function cnUseLocal(b) { try { const r = await api("/api/brains/" + encodeURIComponent(b.id) + "/default", {});
    if (BR.data) { BR.data.default = r.settings.default; BR.data.settings = r.settings; }
    toast(localName(b) + " is your default brain" + (b.harness ? ", and the Agent room's" : ""));
  } catch (e) { toast(e.message); } cnRedraw(); }
// NVIDIA Nemotron 3.5 Bolt run by the Studio itself (app/bolt.py): its state and backend, what it holds, Load and Unload
const BOLT_WORD = { loaded: "Loaded", unloaded: "Unloaded", loading: "Loading…", held: "Unloaded for a render", external: "Not managed here" };
const gbOf = x => x == null ? "" : (Math.round(x * 10) / 10) + " GB";
function boltLine(e) { return BOLT_WORD[e.state] + (e.state === "loaded" && e.memory_gb != null ? " · " + gbOf(e.memory_gb) : "") + (e.state === "held" && e.held_for ? " (" + e.held_for + ")" : ""); }
function boltPanel(e) {
  const act = async (what) => { BR.boltBusy = true; render();
    try { const r = await api("/api/bolt/" + what, {}); toast(what === "load" ? (r.state === "loaded" ? "Bolt is loaded" : "Loading Bolt (about " + Math.round(r.load_s || 40) + " s)") : "Bolt unloaded: its memory is back");
      BR.data = null; await brainsLoad(); } catch (err) { toast(err.message, 6000); } BR.boltBusy = false; render(); };
  if (e.state === "loading" && !BR.boltTimer) BR.boltTimer = setTimeout(() => { BR.boltTimer = null; if (tab === "Brains") { BR.data = null; brainsLoad(); } }, 3000);
  return el("div", { class: "stack m-bolt", "data-bolt": e.state },
    el("div", { class: "kv m-row" }, el("span", {}, "Studio engine"), el("b", { class: e.state === "loaded" ? "vt-ok" : e.error ? "warn" : "" }, boltLine(e))),
    el("div", { class: "kv m-row" }, el("span", {}, "Backend"), el("b", {}, e.backend_label || e.backend)),
    el("div", { class: "note m-flat" }, e.available === false && e.why ? e.why : "Loaded on the first question (about " + Math.round(e.load_s || 40) + " s), unloaded " + (e.idle_unload_s ? "after " + Math.round(e.idle_unload_s / 60) + " idle minutes" : "only by hand") + " and before a render that needs its memory, never while it answers. Needs about " + Math.round(e.need_gb || 40) + " GB free to load; holds about " + Math.round(e.resident_gb || 28) + " GB."),
    e.error && e.state !== "loaded" ? el("div", { class: "note m-flat vt-bad" }, e.error) : null,
    el("div", { class: "m-acts" },
      el("button", { class: "small", disabled: BR.boltBusy || e.state === "loaded" || e.state === "loading" || e.state === "held" || e.available === false, onclick: () => act("load") }, e.state === "loading" ? "Loading…" : "Load"),
      el("button", { class: "small", disabled: BR.boltBusy || e.state !== "loaded" || e.in_flight > 0, title: e.in_flight > 0 ? "It is answering now" : "", onclick: () => act("unload") }, "Unload"))); }
function viewBrains() {
  const wrap = el("div");
  const head = () => pageHead("Studio Vault", "Brains & tools", "Your own AI sign-ins, and the tools an agent may use.");
  if (!BR.data) { brainsLoad(); return el("div", {}, head(), el("div", { class: "note" }, "Looking for your sign-ins…")); }
  if (BR.data.error) return el("div", {}, head(), el("div", { class: "note" }, "This Studio server does not offer brains yet (" + BR.data.error + ")."));
  const d = BR.data, set = d.settings || {}, remote = set.remote || {};
  const host = el("input", { value: remote.host || "", placeholder: "you@your-mac" }), studioHost = el("input", { value: remote.studio_host || "", placeholder: "you@this-machine (optional)" });
  const save = async patch => { try { await api("/api/brains/settings", { ...set, ...patch }); toast("Saved"); BR.data = null; brainsLoad(); } catch (e) { toast(e.message); } };
  put(wrap, head(), typeof localModelsCard === "function" ? localModelsCard() : null, typeof localAgentsCard === "function" ? localAgentsCard() : null,
    el("p", { class: "note m-flat m-setlink" }, "Sign-in help, the Hermes profile and every connection in one place: ", el("a", { href: "#Settings", onclick: e => { e.preventDefault(); typeof goSettings === "function" ? goSettings("connections") : go("Settings"); } }, "Settings → Connections"), "."),
    el("details", { class: "fold m-fold" }, el("summary", {}, "What is kept, and who pays"),
    el("div", { class: "note m-flat" }, "Studio uses the AI subscriptions you already pay for. Nothing is stored but which one you prefer: each answer is billed to your own sign-in, and a local model never leaves your network.")));
  const clis = (d.backends || []).filter(b => b.kind !== "http" && b.id !== "local").map(b => ({ kind: "cli", ...b })), localB = (d.backends || []).find(b => b.id === "local");
  put(wrap, el("div", { class: "sectionhead" }, el("h2", {}, "Your agents"), el("span", { class: "note" }, "Connect one: the studio installs it, signs you in and checks it.")),
    el("div", { class: "tri" }, clis.map(b => connectCard(b)),
      localB ? el("div", { class: "brain" + (d.default === "local" ? " on" : ""), "data-brain": "local" }, el("div", { class: "head" }, brainLogo("local", localB), el("div", { class: "m-min" }, el("b", {}, localB.label),
          el("span", { class: "cn-word " + (localB.found ? "ok" : "bad") }, localB.found ? "Ready" : "Not answering", localB.found && localB.model ? " · running " + localB.model : localB.models && localB.models.length ? " · " + localB.models.length + " models" : ""))),
        localB.engine ? boltPanel(localB.engine) : null,
        el("div", { class: "m-acts" }, d.default === "local" ? el("span", { class: "tag2 t", title: d.automatic ? "Nobody chose a default brain, and this local server answers: it leads until you make another brain the default" : "" }, d.automatic ? "default · automatic" : "default") : el("button", { class: "small", disabled: !localB.found, onclick: () => save({ default: "local" }) }, "Make default"))) : null));
  put(wrap, el("div", { class: "m-gap" }, linkPanel(),
    el("details", { class: "fold m-fold", open: CN.linkOpen || false }, el("summary", {}, "Set the other computer by hand"),
      el("div", { class: "panel stack" },
      el("div", { class: "note m-flat" }, "Studio runs the CLI there over SSH, with key login only. A DGX Spark usually has no Claude or Codex sign-in of its own: name the Mac (or any machine) where you are signed in."),
      field("SSH host", host), field("How that computer reaches this one (for Blender and Studio tools)", studioHost), el("div", { class: "chips" }, ["claude", "codex", "gemini", "grok", "hermes"].filter(id => id !== "hermes" || (d.backends || []).some(b => b.id === "hermes")).map(id => el("label", { class: "toggle m-flat" + ((remote.backends || []).includes(id) ? " on" : "") },
        el("input", { type: "checkbox", checked: (remote.backends || []).includes(id), class: "hide", onchange: e => { const list = new Set(remote.backends || []); e.target.checked ? list.add(id) : list.delete(id); remote.backends = [...list]; e.target.parentNode.classList.toggle("on", e.target.checked); } }),
        el("span", { class: "dotl" + ((remote.backends || []).includes(id) ? "" : " o") }), MARKS[id] ? mark(id, "") : null, ({ claude: "Claude Code", codex: "Codex", gemini: "Gemini CLI", grok: "Grok CLI", hermes: "Hermes" })[id]))),
      el("div", {}, el("button", { class: "primary", onclick: () => save({ remote: { host: host.value.trim(), backends: remote.backends || [], studio_host: studioHost.value.trim() } }) }, "Save"))))));
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
