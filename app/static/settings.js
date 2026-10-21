"use strict";
// Settings: how the studio looks, which rooms the sidebar shows, the defaults a new take starts from, the brains and
// tools it connects to, and what it is. Kept by the server in the studio folder's settings.json (app/settings.py);
// the look is also kept in this browser so the next load paints with it before the server answers.
const SET_DEFAULTS = {
  appearance: { type_size: "comfortable", glow: true, poster: true, accent: "amber", motion: "system", start_cards: ["clip", "scout", "still", "sharp", "long", "voice"], start_off: [] },
  layout: { hidden: [], order: [] },
  presets: { recipe: "quick", image_model: "qwen", seconds: 5, qa_gate: true, audio_rate: "keep", seed: "fixed", mode: "clip" },
  hermes_profile: "", hermes_command: "" };
const SET = { data: null, loaded: false, section: "appearance", saving: false, local: { endpoint: null, found: null, finding: false, said: null, testing: false }, about: null };
const SET_SECTIONS = [["appearance", "Appearance", "type, glow, poster, accent"], ["layout", "Studio layout", "what the sidebar shows"],
  ["presets", "Presets & preferences", "what a new take starts from"], ["connections", "Connections", "subscriptions, local models, tools"], ["access", "Access key", "sign in your phone, a new key"],
  ["models", "Models & weights", "what the studio needs, found and linked"], ["sparks", "Sparks", "one to five DGX Sparks for a job"],
  ["brains", "Brains & tools", "your agents, another computer, MCP tools"], ["updates", "Updates", "the version and the branch it follows"], ["about", "About", "version, credits, licences"]];
// Machine's own pages, opened here too: each is the same view, and its data is drawn again when it arrives
const SET_VIEWS = { models: ["viewDiscover", () => typeof DSC !== "undefined" && DSC.data], sparks: ["viewSparks", () => typeof SPK !== "undefined" && SPK.data],
  brains: ["viewBrains", () => typeof BR !== "undefined" && [BR.data, BR.status, BR.sign]], updates: ["viewUpdates", () => typeof UPD !== "undefined" && [UPD.data, UPD.said]] };
const ALWAYS_ROOMS = ["machine", "settings"];
// Settings, merged over the defaults: what every other module reads
function prefs() { const d = SET.data || {}, out = {};
  for (const k of Object.keys(SET_DEFAULTS)) out[k] = typeof SET_DEFAULTS[k] === "object" && !Array.isArray(SET_DEFAULTS[k]) ? { ...SET_DEFAULTS[k], ...(d[k] || {}) } : (d[k] ?? SET_DEFAULTS[k]);
  return out; }
// the look on <html>: a type size class, glow and poster off, the accent, reduced motion
function prefsApply(look) { const a = { ...SET_DEFAULTS.appearance, ...(look || prefs().appearance) }, root = typeof document !== "undefined" && document.documentElement;
  if (!root || !root.classList) return a;
  for (const c of ["type-large", "type-largest", "no-glow", "no-poster", "accent-cobalt", "motion-reduce"]) root.classList.remove(c);
  if (a.type_size !== "comfortable") root.classList.add("type-" + a.type_size);
  if (!a.glow) root.classList.add("no-glow"); if (!a.poster) root.classList.add("no-poster");
  if (a.accent === "cobalt") root.classList.add("accent-cobalt"); if (a.motion === "reduce") root.classList.add("motion-reduce");
  return a; }
function prefsMirror() { try { localStorage.setItem("studio-look", JSON.stringify(prefs().appearance)); } catch {} }
// once, when the page opens: the saved settings, the look applied, and the Create wizard's defaults set from the presets
async function settingsLoad() { try { const r = await api("/api/settings"); SET.data = r.settings || null; } catch { SET.data = SET.data || null; }
  SET.loaded = true; prefsApply(); prefsMirror(); prefsToCreate(); return SET.data; }
async function settingsSave(patch, quiet) { SET.saving = true;
  try { const r = await api("/api/settings", patch); SET.data = r.settings; prefsApply(); prefsMirror(); if (!quiet) toast("Saved"); }
  catch (e) { toast(e.message); } SET.saving = false; render(); }
// the recipe a preset names, as the take sheet's recipes set it (shell.js takeRecipes)
function recipeParams(id) { const up = typeof UPSCALED !== "undefined" && UPSCALED["720p"] && S && S.presets && S.presets[UPSCALED["720p"][0]];
  if (id === "sharp" && S && S.sharp) return { engine: "fasth3", passes: "8", res: "sharp", up: false, preset: "sharp", quality: "fh8", seed: S.sharp.seed, seconds: S.sharp.seconds[0] };
  if (id === "final") return { engine: "fasth3", passes: "8", res: "720p", up: !!up, preset: up ? UPSCALED["720p"][0] : "p720", quality: "fh8" };
  return { engine: "fasth3", passes: "4", res: "480p", up: false, preset: "small", quality: "fh4" }; }
const prefSeed = () => prefs().presets.seed === "random" ? 1 + Math.floor(Math.random() * 99999) : (typeof C !== "undefined" && Number(C.seed)) || 1;
function prefsToCreate() { if (typeof C === "undefined" || (typeof location !== "undefined" && location.search && location.search.length > 1)) return;
  const p = prefs().presets; C.imageModel = p.image_model; C.seconds = p.seconds;
  if (p.recipe !== "custom") { const r = recipeParams(p.recipe); Object.assign(C, { engine: r.engine, passes: r.passes, res: r.res, up: r.up, steps: "" }); if (r.seed) C.seed = r.seed; }
  if (p.seed === "random") C.seed = prefSeed(); if (typeof sync === "function") sync(); }
// the sidebar's rooms in the user's order, without the hidden ones (Machine and Settings always stay)
function roomsShown(list) { const lay = prefs().layout, order = lay.order || [];
  const rank = r => { const i = order.indexOf(r.id); return i < 0 ? 1000 + list.indexOf(r) : i; };
  return list.filter(r => ALWAYS_ROOMS.includes(r.id) || !(lay.hidden || []).includes(r.id)).sort((a, b) => rank(a) - rank(b)); }

/* ---------- the parts every section is made of ---------- */
const setSeg = (value, options, pick, label) => el("div", { class: "seg-ctl", role: "radiogroup", "aria-label": label || "" },
  Object.entries(options).map(([k, t]) => el("button", { type: "button", role: "radio", "aria-checked": value === k ? "true" : "false", class: value === k ? "on" : "", onclick: () => { if (value !== k) pick(k); } }, t)));
const setSwitch = (on, flip, label) => el("button", { type: "button", role: "switch", class: "st-switch" + (on ? " on" : ""), "aria-checked": on ? "true" : "false", "aria-label": label, onclick: () => flip(!on) }, el("i"));
const setRow = (title, one, control) => el("div", { class: "st-row" }, el("div", { class: "st-what" }, el("b", {}, title), one ? el("span", {}, one) : null), el("div", { class: "st-ctl" }, control));
const setPanel = (title, line, ...kids) => el("section", { class: "panel st-panel" }, el("div", { class: "st-ph" }, el("h2", {}, title), line ? el("p", { class: "note m-flat" }, line) : null), ...kids);

/* ---------- 1. Appearance ---------- */
const START_NAMES = { clip: "A clip with sound", scout: "Scout, then animate", still: "A still", sharp: "Sharp Spec", long: "A long take", voice: "A voice line", song: "A song", sfx: "Sound effects & foley", looks: "Looks", previs: "Previs", nodes: "Nodes", agent: "Ask your agent" };
function setAppearance() { const a = prefs().appearance, put1 = patch => settingsSave({ appearance: patch }, true);
  return [setPanel("Appearance", "Applied as you choose, on this screen and every other one that opens the studio.",
    setRow("Type size", "Text and controls in the work area", setSeg(a.type_size, { comfortable: "Comfortable", large: "Large", largest: "Largest" }, v => put1({ type_size: v }), "Type size")),
    setRow("Glowing pills", "The soft amber glow on the active place and the machine chip", setSwitch(a.glow, v => put1({ glow: v }), "Glowing pills")),
    setRow("Poster on Home", "The key art beside the greeting", setSwitch(a.poster, v => put1({ poster: v }), "Poster on Home")),
    setRow("Accent", "The colour of the one primary button and the active place",
      setSeg(a.accent, { amber: "Amber", cobalt: "Cobalt" }, v => put1({ accent: v }), "Accent")),
    setRow("Motion", "The breathing tally lights and hover moves", setSeg(a.motion, { system: "Follow the system", reduce: "Always reduce" }, v => put1({ motion: v }), "Motion"))),
  setPanel("Start cards on Home", "Which ways in sit under the composer. Six fill one row.",
    el("div", { class: "chips st-chips" }, Object.entries(START_NAMES).map(([id, name]) => { const off = a.start_off || [], on = typeof startShown === "function" ? startShown(a, id) : a.start_cards.includes(id);
      return el("button", { type: "button", class: "toggle" + (on ? " on" : ""), "aria-pressed": on ? "true" : "false",
        onclick: () => put1(on ? { start_cards: a.start_cards.filter(x => x !== id), start_off: [...off.filter(x => x !== id), id] }
          : { start_cards: [...a.start_cards.filter(x => x !== id), id], start_off: off.filter(x => x !== id) }) }, el("span", { class: "dotl" + (on ? "" : " o") }), name); })))]; }

/* ---------- 2. Studio layout ---------- */
function setLayout() { const lay = prefs().layout, hidden = new Set(lay.hidden || []);
  const groups = typeof GROUPS !== "undefined" ? GROUPS : [];
  const full = () => { const all = typeof ROOMS !== "undefined" ? ROOMS : []; const ord = lay.order || [];
    return [...all].sort((a, b) => { const i = ord.indexOf(a.id), j = ord.indexOf(b.id); return (i < 0 ? 1000 + all.indexOf(a) : i) - (j < 0 ? 1000 + all.indexOf(b) : j); }); };
  const move = (id, by) => { const list = full().map(r => r.id), room = ROOMS.find(r => r.id === id), peers = full().filter(r => r.group === room.group).map(r => r.id);
    const at = peers.indexOf(id), to = at + by; if (to < 0 || to >= peers.length) return;
    const other = peers[to], i = list.indexOf(id), j = list.indexOf(other); list[i] = other; list[j] = id; settingsSave({ layout: { order: list } }, true); };
  const flip = id => { const next = new Set(hidden); next.has(id) ? next.delete(id) : next.add(id); settingsSave({ layout: { hidden: [...next] } }, true); };
  return [setPanel("Studio layout", "Show or hide each room in the sidebar, and put them in your order. A hidden room stays one ⌘K away; Home, Machine and Settings always stay.",
    groups.map(g => { const rooms = full().filter(r => r.group === g.id); if (!rooms.length) return null;
      return el("div", { class: "st-group" }, el("div", { class: "eyebrow" }, g.name || "Agent"),
        rooms.map((r, i) => { const always = ALWAYS_ROOMS.includes(r.id), shown = always || !hidden.has(r.id);
          return el("div", { class: "st-room" + (shown ? "" : " off"), "data-room": r.id }, icon(r.id, "ic"), el("div", { class: "st-what" }, el("b", {}, r.name), el("span", {}, always ? "always in the sidebar" : r.says)),
            el("div", { class: "st-ctl" }, el("button", { type: "button", class: "small iconish", disabled: i === 0, "aria-label": "Move " + r.name + " up", onclick: () => move(r.id, -1) }, "↑"),
              el("button", { type: "button", class: "small iconish", disabled: i === rooms.length - 1, "aria-label": "Move " + r.name + " down", onclick: () => move(r.id, 1) }, "↓"),
              always ? el("span", { class: "tag2" }, "stays") : setSwitch(shown, () => flip(r.id), "Show " + r.name))); })); }),
    el("div", {}, el("button", { type: "button", class: "small", onclick: () => settingsSave({ layout: { hidden: [], order: [] } }) }, "Show every room, in the studio's order")))]; }

/* ---------- 3. Presets & preferences ---------- */
function setPresets() { const p = prefs().presets, put1 = patch => settingsSave({ presets: patch }, true);
  const models = S && S.image_models && S.image_models.length ? S.image_models : [{ id: "qwen", label: "Qwen 2.1 Turbo" }];
  const lengths = Object.keys((S && S.durations) || { 3: 0, 5: 0, 8: 0, 10: 0 }).map(Number).filter(n => n >= 1 && n <= 15);
  const model = el("select", { "aria-label": "Default picture model", onchange: e => put1({ image_model: e.target.value }) }, models.map(m => el("option", { value: m.id, selected: m.id === p.image_model }, m.label)));
  const secs = el("select", { "aria-label": "Default clip length", onchange: e => put1({ seconds: Number(e.target.value) }) }, (lengths.length ? lengths : [5]).map(n => el("option", { value: n, selected: Number(p.seconds) === n }, n + " s")));
  return [setPanel("Presets & preferences", "What a new take starts from: the Home composer and the Create wizard open with these.",
    setRow("Render recipe", "For clips from Home and a new Create", setSeg(p.recipe, { quick: "Quick look", ...(S && S.sharp ? { sharp: "Sharp Spec" } : {}), final: "Final 2K", custom: "Custom" }, v => put1({ recipe: v }), "Render recipe")),
    setRow("Picture model", "For stills and scouts", model),
    setRow("Clip length", "A clip's seconds; the exact frames are shown before it is queued", secs),
    setRow("Home starts on", "The composer's first chip", setSeg(p.mode, { clip: "Clip", picture: "Picture", voice: "Voice", song: "Song" }, v => put1({ mode: v }), "Home starts on")),
    setRow("QA gate on each clip", "The transcript and contact-sheet check after a Sharp Spec clip", setSwitch(p.qa_gate, v => put1({ qa_gate: v }), "QA gate")),
    setRow("Delivery sound", "On a DGX Spark a clip's sound is 32 kHz; 48 kHz stereo re-encodes only the sound", setSeg(p.audio_rate, { keep: "Keep 32 kHz", "48000": "48 kHz stereo" }, v => put1({ audio_rate: v }), "Delivery sound")),
    setRow("Seed", "Fixed repeats a take; random gives each a new one", setSeg(p.seed, { fixed: "Fixed", random: "Random" }, v => put1({ seed: v }), "Seed")),
    setRow("Renders go to", "The studio folder, set when the studio starts (STUDIO_HOME)", el("code", { class: "st-path" }, (S && S.home) || "the studio folder")))]; }

/* ---------- 4. Connections ---------- */
const LOCAL_PRESETS = [["Studio engine · Bolt", "studio:bolt"], ["Ollama", "http://127.0.0.1:11434/v1"], ["LM Studio", "http://127.0.0.1:1234/v1"], ["vLLM", "http://127.0.0.1:8000/v1"], ["llama.cpp", "http://127.0.0.1:8080/v1"], ["Hermes proxy", ""]];
// the Local models card: on Settings → Connections and at the top of Machine → Brains & tools
function localModelsCard() {
  const b = (typeof BR !== "undefined" && BR.data && BR.data.backends || []).find(x => x.id === "local"), set = typeof BR !== "undefined" && BR.data && BR.data.settings || {};
  const L = SET.local; if (L.endpoint === null) L.endpoint = set.local_endpoint || "";
  const found = L.found || (b ? { found: b.found, endpoint: b.endpoint, models: b.models || [] } : null);
  const field1 = el("input", { value: L.endpoint, placeholder: "http://127.0.0.1:11434/v1", "aria-label": "Local model server address", oninput: e => { L.endpoint = e.target.value; } });
  const find1 = async () => { L.finding = true; render(); try { const r = await api("/api/brains?endpoint=" + encodeURIComponent(L.endpoint.trim()));
      const one = (r.backends || []).find(x => x.id === "local") || {}; L.found = { found: !!one.found, endpoint: one.endpoint, models: one.models || [] }; } catch (e) { L.found = { found: false, error: e.message, models: [] }; } L.finding = false; render(); };
  const save1 = async patch => { try { await api("/api/brains/settings", patch); toast("Saved"); if (typeof BR !== "undefined") { BR.data = null; brainsLoad().then(() => render()); } } catch (e) { toast(e.message); } };
  const model = found && found.models.length ? el("select", { "aria-label": "Model", onchange: e => save1({ local_model: e.target.value }) },
    el("option", { value: "" }, "The server's first model"), found.models.map(m => el("option", { value: m, selected: m === set.local_model }, m))) : null;
  return el("section", { class: "panel st-local", "data-card": "local-models" },
    el("div", { class: "st-lh" }, el("span", { class: "logo" }, icon("machine")), el("div", { class: "m-min" }, el("h2", {}, "Local models welcome"),
      el("p", { class: "st-line" }, "Any local model works here — nothing leaves your machine.")),
      el("span", { class: "tag2 " + (found && found.found ? "ok" : "") }, found ? (found.found ? "answering" : "not answering") : "not looked yet")),
    el("div", { class: "note m-flat" }, "An OpenAI-compatible server on this computer or your network can be the brain for Slate, the QA look and the Agent room's conversation. Pick one to fill its usual address, or type your own."),
    el("div", { class: "chips" }, LOCAL_PRESETS.map(([name, url]) => el("button", { type: "button", class: "toggle" + (url && L.endpoint === url ? " on" : ""),
      title: url || "Run `hermes proxy start` in a terminal; `hermes proxy status` shows the address to type here",
      onclick: () => { if (!url) { toast("Run `hermes proxy start`, then type the address `hermes proxy status` shows", 6000); return; } L.endpoint = url; L.found = null; render(); } }, name))),
    el("div", { class: "st-lrow" }, field1, el("button", { type: "button", class: "small", disabled: L.finding, onclick: find1 }, L.finding ? "Looking…" : "Find"),
      el("button", { type: "button", class: "small", onclick: () => save1({ local_endpoint: L.endpoint.trim() }) }, "Save"),
      el("button", { type: "button", class: "small", disabled: L.testing || !(found && found.found), onclick: async () => { L.testing = true; render();
        try { L.said = await api("/api/brains/test", { backend: "local" }); } catch (e) { L.said = { ok: false, error: e.message }; } L.testing = false; render(); } }, L.testing ? "Asking…" : "Test")),
    found ? el("div", { class: "note m-flat " + (found.found ? "vt-ok" : "") }, found.found ? (b && b.model && b.endpoint === found.endpoint ? "Running " + b.model + " · " : "") + "Found at " + found.endpoint + " · " + found.models.length + " model" + (found.models.length === 1 ? "" : "s") : found.error || "Nothing answered there. Left empty, the studio looks on 11434, 1234, 8000 and 8080.") : null,
    model ? el("div", { class: "st-lrow" }, el("span", { class: "note m-flat" }, "Model"), model) : null,
    // a name set here that the server no longer runs: the studio asks for, and shows, the model it does run
    b && b.set_missing && b.endpoint === (found && found.endpoint) ? el("div", { class: "note m-flat" }, "Set to " + b.set_model + ", which this server no longer runs: the studio uses " + b.model + ". ",
      el("button", { type: "button", class: "small", onclick: () => save1({ local_model: "" }) }, "Follow what the server runs")) : null,
    L.said ? el("div", { class: "note m-flat " + (L.said.ok ? "vt-ok" : "vt-bad") }, L.said.ok ? "Answered “" + String(L.said.text || "").slice(0, 40) + "”" : L.said.error) : null); }
const setWhere = b => b.found ? (b.where && b.where !== "here" ? "on " + b.where.replace(/^ssh:/, "") : "found here") + (b.version ? " · " + b.version : "") : b.error || (b.where && b.where !== "here" ? "not found on " + b.where.replace(/^ssh:/, "") : "not found");
function setBrainCard(b) { return typeof connectCard === "function" ? connectCard(b, b.id === "hermes" ? [setHermes()] : []) : el("div", { class: "brain", "data-brain": b.id }, b.label); }
function setHermes() { const p = prefs();
  const prof = el("input", { value: p.hermes_profile, placeholder: "profile (optional)", "aria-label": "Hermes profile" }), cmd = el("input", { value: p.hermes_command, placeholder: "full path to hermes (optional)", "aria-label": "Hermes command" });
  return el("div", { class: "st-hermes" }, prof, cmd, el("button", { type: "button", class: "small", onclick: () => settingsSave({ hermes_profile: prof.value.trim(), hermes_command: cmd.value.trim() }) }, "Save")); }
function setConnections() {
  if (typeof BR === "undefined") return [setPanel("Connections", "This studio has no brains module.")];
  if (!BR.data) { brainsLoad().then(() => { if (tab === "Settings") render(); }); return [localModelsCard(), setPanel("Your subscriptions", "Looking for your sign-ins…")]; }
  if (BR.data.error) return [localModelsCard(), setPanel("Your subscriptions", "Brains are not offered here: " + BR.data.error)];
  const clis = (BR.data.backends || []).filter(b => b.kind === "cli"), remote = (BR.data.settings || {}).remote || {};
  const tools = BR.tools ? (BR.tools.tools || BR.tools) : [];
  return [localModelsCard(),
    setPanel("Your agents", "Press Connect: the studio finds the agent (or installs it), runs its own sign-in, checks it answers and makes it your default. It keeps nothing of the sign-in; each answer is billed to your own subscription.",
      el("div", { class: "tri" }, clis.map(setBrainCard)),
      el("div", { class: "note m-flat" }, remote.host ? "On another computer: " + remote.host + " runs " + ((remote.backends || []).join(", ") || "nothing yet") + " over SSH." : "A DGX Spark can use the agents signed in on your Mac: link it below.")),
    typeof linkPanel === "function" ? linkPanel() : null,
    setPanel("Tools and the suite", "What an agent may drive, and how the filmmaker suite and Resolve connect (by files, nothing to sign in to).",
      el("div", { class: "tri" }, tools.map(t => { const said = BR.checked[t.id];
        return el("div", { class: "brain", "data-tool": t.id }, el("div", { class: "head" }, typeof toolLogo === "function" ? toolLogo(t.id) : null,
            el("div", { class: "m-min" }, el("b", {}, t.label || t.id), el("span", {}, (t.via === "ssh" ? "over SSH on " + (t.host || "another computer") : "on this machine") + (t.enabled === false ? " · off" : "")))),
          said ? el("div", { class: "note m-flat " + (said.ok ? "vt-ok" : "vt-bad") }, said.ok ? (said.tools || []).length + " tools answered" : said.error) : null,
          el("div", { class: "m-acts" }, el("button", { type: "button", class: "small", disabled: BR.checking[t.id], onclick: async () => { BR.checking[t.id] = true; render();
            try { BR.checked[t.id] = await api("/api/agent/tools/" + t.id + "/check", {}); } catch (e) { BR.checked[t.id] = { ok: false, error: e.message }; } BR.checking[t.id] = false; render(); } }, BR.checking[t.id] ? "Connecting…" : "Test"))); }),
        el("div", { class: "brain", "data-tool": "suite" }, el("div", { class: "head" }, el("span", { class: "logo" }, icon("suite")), el("div", { class: "m-min" }, el("b", {}, "Filmmaker suite & Resolve"), el("span", {}, "files in and out: Suite → Bring in & send out"))),
          el("div", { class: "m-acts" }, el("button", { type: "button", class: "small", onclick: async () => { try { const a = await api("/api/suite"); toast((Array.isArray(a) ? a.length : 0) + " suite apps and editors are known here"); } catch (e) { toast(e.message); } } }, "Test"),
            el("button", { type: "button", class: "small", onclick: () => go("Import") }, "Open"))))) ]; }

/* ---------- the access key (app/access.py): sign another device in, show the key, make a new one ---------- */
const keyCopy = async (text, what) => { try { await navigator.clipboard.writeText(text); toast(what + " copied"); } catch { toast("Copy it by hand: " + text); } };
function setAccess() {
  if (!SET.key) { SET.key = { loading: true }; api("/api/key/share").then(d => { SET.key = d; if (tab === "Settings") render(); }, e => { SET.key = { error: e.message }; if (tab === "Settings") render(); }); }
  const k = SET.key, phone = (k.links || []).find(l => /Tailscale/.test(l.label));
  if (k.loading) return [setPanel("Access key", "Loading…")];
  if (k.error) return [setPanel("Access key", k.error)];
  const qr = phone && typeof qrSvg === "function" ? qrSvg(phone.url, 200) : null;
  return [setPanel("Access key", "The key is for reaching the studio from another device: a phone over Tailscale, another computer. Each signs in once and stays signed in.",
      el("div", { class: "key-steps" },
        el("div", { class: "key-step" }, el("b", {}, "Sign in your phone or another computer"),
          phone ? [el("p", { class: "note m-flat" }, "Scan this with the phone's camera. It opens the studio and signs the phone in; then add it to the home screen."),
                   qr ? el("div", { class: "key-qr" }, qr) : null,
                   el("div", { class: "m-acts" }, el("button", { type: "button", class: "small", onclick: () => keyCopy(phone.url, "The link") }, "Copy the link"))]
                : el("p", { class: "note m-flat" }, "A phone reaches the studio through Tailscale: install it on both and run ", el("code", {}, "tailscale serve --bg " + (location.port || "7870")),
                     " on this computer (Settings → Connections has the steps). This panel then shows a QR code for the phone.")),
        el("div", { class: "key-step" }, el("b", {}, "On this computer"),
          el("p", { class: "note m-flat" }, "Nothing to do: a browser on the computer running the studio (localhost) never needs the key, and neither do updates there.")),
        el("div", { class: "key-step" }, el("b", {}, "The key itself"),
          el("p", { class: "note m-flat" }, "For a tool, or to type on a device: kept in ", el("code", {}, k.file || "access-key"), " (only you can read it)."),
          el("div", { class: "m-acts" }, el("code", { class: "key-shown" }, SET.keyShown ? k.key : "•".repeat(12)),
            el("button", { type: "button", class: "small", onclick: () => { SET.keyShown = !SET.keyShown; render(); } }, SET.keyShown ? "Hide" : "Show"),
            el("button", { type: "button", class: "small", onclick: () => keyCopy(k.key, "The key") }, "Copy"))))),
    setPanel("Make a new key", "If the key got out, or a device you signed in is gone: every other browser and phone is signed out and asks again. This browser stays signed in.",
      el("div", {}, el("button", { type: "button", onclick: async () => { if (!confirm("Make a new key? Every other device is signed out.")) return;
        try { await api("/api/key/new", {}); SET.key = null; SET.keyShown = false; toast("New key made: sign your other devices in again"); render(); } catch (e) { toast(e.message); } } }, "Make a new key")))]; }

/* ---------- 5. About ---------- */
// Keys and Wasserman Productions: their sites and code
const ABOUT_LINKS = [["Keys", [["X @u1tra_instinct", "https://x.com/u1tra_instinct"], ["GitHub", "https://github.com/drowzeys"]]],
  ["Sam Wasserman / Wasserman Productions", [["wassermanproductions.com", "https://wassermanproductions.com"], ["X @SamJWasserman", "https://x.com/SamJWasserman"], ["GitHub", "https://github.com/wassermanproductions"]]]];
// CREDITS.md and THIRD_PARTY_NOTICES.md as the repo has them (/api/credits), drawn with el() only: headings, bullets,
// paragraphs, **bold** and `code`; nothing of the file becomes markup
function mdInline(text) { return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/).filter(Boolean).map(p =>
  p.startsWith("**") ? el("b", {}, p.slice(2, -2)) : p.startsWith("`") ? el("code", {}, p.slice(1, -1)) : p); }
function mdBlocks(text) { const out = []; let list = null, para = null;
  const end = () => { if (para) out.push(el("p", { class: "note m-flat" }, mdInline(para))); para = null; };
  for (const raw of (text || "").split("\n")) { const line = raw.trimEnd();
    if (!line.trim()) { end(); list = null; continue; }
    const head = /^(#{1,3}) (.*)$/.exec(line), item = /^\s*- (.*)$/.exec(line);
    if (head) { end(); list = null; out.push(el(head[1].length === 1 ? "h3" : "h4", { class: "st-md-head" }, mdInline(head[2]))); }
    else if (item) { end(); if (!list) out.push(list = el("ul", { class: "st-credits" })); list.appendChild(el("li", {}, mdInline(item[1]))); }
    else if (list && /^\s/.test(raw) && list.lastChild) list.lastChild.append(" ", ...mdInline(line.trim()));
    else { list = null; para = para ? para + " " + line.trim() : line.trim(); } }
  end(); return out; }
function setFullCredits() {
  if (!SET.credits) { SET.credits = { loading: true }; api("/api/credits").then(d => { SET.credits = d; if (tab === "Settings") render(); }, () => { SET.credits = { error: true }; }); }
  const c = SET.credits;
  return setPanel("Every credit", "Everyone whose work made the studio possible, as CREDITS.md and THIRD_PARTY_NOTICES.md in the repo say it.",
    c.loading ? el("p", { class: "note m-flat" }, "Loading…") : c.error ? el("p", { class: "note m-flat" }, "CREDITS.md could not be read here: it is in the repo, github.com/drowzeys.") :
    [el("details", { class: "st-md", open: true }, el("summary", {}, "CREDITS.md"), mdBlocks(c.credits)),
     el("details", { class: "st-md" }, el("summary", {}, "THIRD_PARTY_NOTICES.md"), mdBlocks(c.notices))]); }
function setAbout() { if (!SET.about && typeof updRequest === "function") { SET.about = { loading: true }; updRequest().then(d => { SET.about = d; if (tab === "Settings") render(); }, () => { SET.about = { error: true }; }); }
  const u = SET.about || {}, row = (k, v) => el("div", { class: "kv st-kv" }, el("span", {}, k), el("b", {}, v));
  return [setPanel("About", null,
    el("div", { class: "st-about" }, el("img", { src: "brand/ribbon-title-only-color.svg", alt: "Imagination Studio", width: 240, height: 83 }),
      el("img", { class: "st-credit", src: "brand/keys-wasserman-neon.svg", alt: "by keys+Wasserman", width: 320, height: 37 })),
    ABOUT_LINKS.map(([who, links]) => el("div", { class: "st-links" }, el("b", {}, who), links.map(([label, url]) => el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, label)))),
    el("p", { class: "note m-flat st-powered" }, "Powered by TensorFold (Ash Hart and the TensorFold contributors) · built with integrated Fish Audio Drama 3 · Built with Qwen · Built with Fish Audio · MiniMax-Music3"),
    row("Name", "Imagination Studio by keys+Wasserman"), row("Version", "Studio " + ((S && S.version) || "")), row("Commit", u.installed_commit || (u.loading ? "…" : "unknown")), row("Follows", [u.source, u.following || u.branch].filter(Boolean).join(" · ") || "—"),
    el("div", {}, el("button", { type: "button", onclick: () => go("Updates") }, u.available ? "An update is ready: Machine → Updates" : "Check for updates"))),
  setPanel("Credits", "The authors whose work the studio runs.",
    el("ul", { class: "st-credits" }, ["Keys (drowzeys): the studio itself, its TensorFold families and int8 kernels, and its Mac and DGX Spark engines",
      "Sam Wasserman and Wasserman Productions: helped design and build the studio in collaboration with Keys; Wasserman's Filmmaker Suite (ScriptBreak, Cork Board, Master Canvas, Storyboard Reference Studio, Blockout, Motion Previs Studio, Slate, Circle Take, Stem Studio, Call Sheet, DaVinci MCP), the Sharp Spec, the 80s Horror LoRA and the studio's art",
      "Qwen team: Qwen-Image-2.1", "Black Forest Labs: FLUX 3 Image Klein (FLUX3-K)", "OpenMOSS: MOSS-Image", "MiniMax: MiniMax H3 and MiniMax-Music3", "Built with Fish Audio: Fish Audio Drama 3",
      "Stability AI: Stable Audio Open · Ho Kei Cheng et al.: MMAudio · kijai: ComfyUI-MMAudio (sound effects and foley)",
      "Ash Hart: TensorFold",
      "NVIDIA: Nemotron 3.5 Bolt and its LiLiCorr drafter (the Studio's brain engine) · MiaAI-Lab: the Bolt TensorFold recipe for the DGX Spark",
      "antirez: h3.c · RobZombAI: H3MLX · mrbizarro: minimax-h3-mlx (Phosphene) · Filip Strand and the mflux contributors · felix-ab: flux3-mlx",
      "comfyanonymous, Comfy Org and the ComfyUI contributors: ComfyUI, and Comfy Org's MOSS-Image and MiniMax-Music-3 files · Apple: MLX · mlx-vlm · Qwen team: Qwen3-VL · VisualInference",
      "NVIDIA Research: Sol · FastVideo (Hao AI Lab): FastH3 · LightX2V (ModelTC) · larryvrh · TaoLiveAIGC · Viggle · speach1sdef178 · rookiestar28 · ddalcu · Kijai · DeepBeepMeep",
      "The Library's actions: MATLOWAI (H3 Contact Sheet) · dntpi / Sandpies (H3 Video Upsampler) · LBH-123-AI · bbaudio-2025 · Alissonerdx · Akatz Labs (H3 Relay, Person Remover) · Meta: SAM 3.1",
      "Motion Previs: Sam Wasserman's Motion Previs Studio · Google: MediaPipe · Lihe Yang et al.: Depth Anything (Xenova's ONNX conversion) · OpenCV · Microsoft: ONNX Runtime",
      "MMAudio's models: Apple DFN5B CLIP · Synchformer (Iashin et al.) · NVIDIA BigVGAN v2 · Google: T5 · the SGLang-Omni team and Descript: Fish's codec on a Spark",
      "The agents you connect: Claude Code (Anthropic), Codex (OpenAI), Gemini CLI (Google), Grok CLI (xAI), Hermes Agent (Nous Research); Ollama, LM Studio, vLLM, llama.cpp (Georgi Gerganov and the ggml contributors); Blender (Blender Foundation) and blender-mcp (Siddharth Ahuja)",
      "OpenAI Whisper, faster-whisper (SYSTRAN, Mobius Labs), yt-dlp, FFmpeg, PyTorch, Hugging Face diffusers, transformers and huggingface_hub, torchsde, FastAPI, Starlette and Uvicorn (Encode), xgrammar (MLC-AI), Zig, BusyBox, uv, NumPy, Pillow, PyAV",
      "Geist and Instrument Serif · LobeHub and Simple Icons: the agents' marks · @itxabdullaa: the Black Mirror test prompt",
      "The community LoRA authors, by name on each card in Looks and in CREDITS.md · every one of them, with what was used and its licence, under Every credit below"].map(t => el("li", {}, t)))),
  setFullCredits(),
  setPanel("Licences", "The studio's code is Apache-2.0. The models keep their own terms: do not redistribute their weights.",
    el("ul", { class: "st-credits" }, ["Attribution required: per the NOTICE file (Apache 2.0 §4(d)), any use, fork or redistribution keeps the NOTICE and credits Keys (github.com/drowzeys) and Sam Wasserman / Wasserman Productions (wassermanproductions.com) in its documentation and about/credits surface",
      "Qwen-Image-2.1: Qwen Research License (non-commercial)", "MiniMax H3: MiniMax H3 Community License (territory limits)", "MiniMax-Music3: its licence asks for its name in a commercial UI",
      "FLUX3-K: FLUX Kommunity License", "MOSS-Image: Apache 2.0 (commercial use allowed)", "Fish Audio Drama 3: Fish Audio Community License v1.0; a hosted voice service needs Fish's own licence",
      "NVIDIA Nemotron 3.5 Bolt: OpenMDW License 1.1 (commercial use allowed; a copy keeps the licence and NVIDIA's notices) · its DGX Spark engine: MiaAI-Lab's recipe, MIT, on TensorFold",
      "Stable Audio Open 1.0: Stability AI Community License (free under US$1 million of yearly revenue)", "MMAudio's weights: CC-BY-NC 4.0, non-commercial",
      "Wasserman's Filmmaker Suite: each app under its own licence, Apache-2.0 (Master Canvas: MIT); the studio ships none of their code but the Slate workflow and the Motion Previs port, each with its NOTICE"].map(t => el("li", {}, t))))]; }

function setEmbedded(section) { const [name, data] = SET_VIEWS[section], view = typeof globalThis[name] === "function" ? globalThis[name] : null;
  if (!view) return [setPanel(SET_SECTIONS.find(x => x[0] === section)[1], "Not in this build.")];
  // a view that redraws only while its own tab is open: drawn again here when what it shows changes
  clearInterval(SET.embedTimer); let seen = JSON.stringify(data() || null);
  SET.embedTimer = setInterval(() => { if (tab !== "Settings" || SET.section !== section) { clearInterval(SET.embedTimer); SET.embedTimer = null; return; }
    const now = JSON.stringify(data() || null); if (now !== seen && !(document.activeElement && document.activeElement.matches && document.activeElement.matches("input,textarea,select"))) { seen = now; render(); } }, 1500);
  if (SET.embedTimer && SET.embedTimer.unref) SET.embedTimer.unref();
  return [el("div", { class: "st-embed", "data-embed": section }, view())]; }
function viewSettings() {
  const draw = { appearance: setAppearance, layout: setLayout, presets: setPresets, connections: setConnections, access: setAccess, about: setAbout,
    models: () => setEmbedded("models"), sparks: () => setEmbedded("sparks"), brains: () => setEmbedded("brains"), updates: () => setEmbedded("updates") }[SET.section] || setAppearance;
  return el("div", { class: "settings" },
    typeof pageHead === "function" ? pageHead("Studio Vault", "Settings", "How the studio looks, what it shows, and what it connects to.") : el("h1", { class: "display" }, "Settings"),
    el("div", { class: "st-wrap" },
      el("nav", { class: "st-rail", "aria-label": "Settings sections" }, SET_SECTIONS.map(([id, name, one]) => el("button", { type: "button", class: SET.section === id ? "on" : "", "aria-current": SET.section === id ? "page" : false, "data-section": id,
        onclick: () => { SET.section = id; render(); } }, el("b", {}, name), el("span", {}, one)))),
      el("div", { class: "st-body" }, draw()))); }
// open Settings at one section (⌘K, the Agent room's and Brains' links)
function goSettings(section) { if (section) SET.section = section; go("Settings"); }
// first paint: the look kept in this browser, before the server answers
try { const kept = JSON.parse(localStorage.getItem("studio-look") || "null"); if (kept) prefsApply(kept); } catch {}
