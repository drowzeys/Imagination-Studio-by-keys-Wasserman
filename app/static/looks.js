"use strict";
// Studio Vault → Looks: the LoRA hub as a style library. Each LoRA is a square cover card with its author, one line,
// what it needs and its licence; Install, Use in Image or Video, Remove. The same /api/loras routes as the hub inside
// Create (loraHub in index.html): install asks with the LoRA's licence first, a running install is watched, and a
// recipe of your own is added with the same POST and install.
const LK = { asked: false, open: false, form: { name: "", url: "", file: "", trigger: "", strength: 1, styles: "", author: "", notes: "" } };
const LOOK_COVERS = { "wasserman-80s-horror": "art/looks/80s-horror.webp", "realism-people": "art/looks/realism-people.webp" };  // a look with its own banner; the rest wait for art slots
const lookFirstStyle = r => Object.keys(r.styles || {})[0] || "";
const lookBusy = r => !!(r.installing && r.installing.stage !== "failed");
const lookTerms = r => "“" + r.name + "”" + (r.author ? " by " + r.author : "") + (r.size ? " (" + sizeText(r.size) + ")" : "") + "\n" + (r.licence || "The file is the author's, under its own licence: read it on the LoRA's page first.") + (r.page ? "\n" + r.page : "");
/* ---------- a file its author gates on Hugging Face (app/hf_gate.py): the one step the person takes, the rest by itself ----------
   Install answers {gate} instead of starting: sign in to Hugging Face once (a read token, kept where Hugging Face's own
   tools keep it), accept the licence on the model's page (opened in a new tab), and the download starts as soon as the
   hub says access is granted. Hugging Face lets nobody accept a licence for someone else. */
const GATE = { r: null, g: null, node: null, timer: null, token: "", busy: false, said: "" };
async function loraInstallOne(r) { const out = await api("/api/loras/" + r.id + "/install", {}); if (out && out.gate) gateOpen(r, out.gate); return out; }
const gateInstall = r => typeof loraInstallOne === "function" ? loraInstallOne(r) : api("/api/loras/" + r.id + "/install", {});
// `then` (optional) is for a gate that is not a LoRA's: {check: async () => gate, done: () => …} (Models & weights)
function gateOpen(r, g, then) { GATE.r = r; GATE.g = g; GATE.then = then || null; GATE.said = ""; GATE.token = ""; gateDraw(); gateWatch(); }
function gateClose() { clearInterval(GATE.timer); GATE.timer = null; GATE.r = null; if (GATE.node) { GATE.node.remove(); GATE.node = null; } }
// while the person is on the licence page, the hub is asked every few seconds; access granted starts the download
function gateWatch() { clearInterval(GATE.timer); GATE.timer = null; if (!GATE.r || !["accept", "pending"].includes(GATE.g.access)) return;
  GATE.timer = setInterval(gateCheck, GATE.g.gated === "manual" ? 15000 : 4000); }
async function gateCheck() { const r = GATE.r; if (!r) return;
  if (GATE.then) { let g; try { g = await GATE.then.check(); } catch (e) { GATE.said = e.message; gateDraw(); return; }
    if (g && !["open", "granted"].includes(g.access)) { const was = GATE.g.access; GATE.g = { ...GATE.g, ...g }; if (was !== GATE.g.access) { gateWatch(); gateDraw(); } return; }
    const done = GATE.then.done; gateClose(); toast("Access granted: “" + r.name + "”"); if (done) done(); return; }
  let out; try { out = await api("/api/loras/" + r.id + "/install", {}); } catch (e) { GATE.said = e.message; gateDraw(); return; }
  if (out && out.gate) { const was = GATE.g.access; GATE.g = out.gate; if (was !== GATE.g.access) { gateWatch(); gateDraw(); } return; }
  gateClose(); toast("Access granted: “" + r.name + "” is downloading"); await loadLoras(); hubWatch(render); render(); }
async function gateSaveToken() { const value = (GATE.token || "").trim(); if (!value) return toast("Paste the token first");
  GATE.busy = true; GATE.said = ""; gateDraw();
  try { const who = await api("/api/hf/token", { token: value }); GATE.token = ""; toast("Signed in to Hugging Face as " + who.user); await gateCheck(); }
  catch (e) { GATE.said = e.message; } GATE.busy = false; if (GATE.r) gateDraw(); }
function gateOpenPage() { const g = GATE.g; try { window.open(g.page, "_blank", "noopener"); } catch {} GATE.opened = true; gateWatch(); gateDraw(); }
function gateDraw() { if (typeof document === "undefined" || !document.body) return; const r = GATE.r, g = GATE.g; if (!r) return;
  const signed = g.access !== "sign_in", granted = g.access === "granted";
  const step = (n, title, state, ...kids) => el("li", { class: "cn-step " + state, "data-step": "gate-" + n, "data-state": state },
    el("span", { class: "cn-tick", "aria-hidden": "true" }, state === "done" ? "✓" : ""), el("div", { class: "cn-body" }, el("b", {}, title), ...kids.flat()));
  const manual = g.gated === "manual";
  const field = el("input", { type: "password", value: GATE.token, placeholder: "hf_…", "aria-label": "Hugging Face read token", autocomplete: "off", spellcheck: "false",
    oninput: e => { GATE.token = e.target.value; }, onkeydown: e => { if (e.key === "Enter") gateSaveToken(); } });
  const box = el("div", { class: "gate-sheet", role: "dialog", "aria-modal": "true", "aria-label": "Get access to " + r.name },
    el("div", { class: "signin-head" }, el("b", {}, "“" + r.name + "” is gated by its author"), el("button", { type: "button", class: "small", onclick: gateClose }, "Close")),
    el("p", { class: "note m-flat" }, manual ? "Its author approves each download on Hugging Face. Ask once; the studio installs it by itself when they say yes."
      : "Accept its licence on Hugging Face once; the studio installs it by itself the moment you do."),
    el("ol", { class: "cn-steps" },
      step(1, "Sign in to Hugging Face", signed ? "done" : "wait", signed ? el("span", { class: "cn-said" }, "Signed in" + (g.user ? " as " + g.user : "") + ".")
        : [el("span", { class: "cn-said" }, g.detail || "Make a read token on Hugging Face (free account), copy it and paste it here. It is kept where Hugging Face's own tools keep it, so model installs use it too."),
          el("div", { class: "chips" }, el("button", { type: "button", class: "small", onclick: () => { try { window.open(g.token_page, "_blank", "noopener"); } catch {} } }, "Make a read token")),
          el("div", { class: "signin-paste" }, field, el("button", { type: "button", class: "primary", disabled: GATE.busy, onclick: gateSaveToken }, GATE.busy ? "Checking…" : "Sign in"))]),
      step(2, manual ? "Ask the author for access" : "Accept the licence", granted ? "done" : signed ? "wait" : "todo",
        el("span", { class: "cn-said" }, g.access === "pending" ? "Requested: waiting for the author to approve. You can close this; Install again later, or keep it open and it installs by itself."
          : manual ? "On the model's page, fill in the short form and press Submit. Read its licence there: it is the author's." : "On the model's page, read the licence and press “Agree and access repository”."),
        signed && !granted ? el("div", {}, el("button", { type: "button", class: "primary", onclick: gateOpenPage }, GATE.opened ? "Open the page again" : "Open " + (g.repo || "the page"))) : null,
        GATE.opened && g.access === "accept" ? el("span", { class: "note m-flat" }, "Checking every few seconds…") : null),
      step(3, "Download", granted ? "done" : "todo", el("span", { class: "cn-said" }, "Starts by itself."))),
    GATE.said ? el("div", { class: "note m-flat vt-bad" }, GATE.said) : null);
  const node = el("div", { class: "gate-wrap", onclick: e => { if (e.target === node) gateClose(); } }, box);
  if (GATE.node) GATE.node.replaceWith(node); else document.body.append(node); GATE.node = node;
  if (!signed && document.activeElement !== field && field.focus) setTimeout(() => field.focus(), 0); }
async function lookInstall(list) { if (!list.length) return toast("Nothing to download here");
  if (!confirm("Install on this machine?\n\n" + list.map(lookTerms).join("\n\n"))) return;
  for (const r of list) { try { await gateInstall(r); } catch (e) { toast(e.message); } }
  await loadLoras(); hubWatch(render); render(); }
async function lookRemove(r) { if (!confirm("Remove the file of “" + r.name + "” from this machine?")) return;
  try { await api("/api/loras/" + r.id, undefined, "DELETE"); C.loras = (C.loras || []).filter(x => x.id !== r.id); await loadLoras(); render(); } catch (e) { toast(e.message); } }
// a video look goes on the clip in Create; the clip's start stays as it was
function lookUseVideo(r) { if (!C.loras) C.loras = [];
  if (!C.loras.some(p => p.id === r.id)) C.loras.push({ id: r.id, strength: r.strength || 1, style: lookFirstStyle(r) });
  C.goes = "video"; sync(); go("Create"); toast("Look on: " + r.name); }
// how many of the hub's LoRAs can be used on this machine now, and why the others cannot (as loraHub's hubTally)
function lookTally() { const sets = LORAS.filter(r => r.builtin), ok = sets.filter(r => r.usable), not = sets.filter(r => !r.usable);
  const why = r => r.status === "unsupported" ? (r.platforms && !r.platforms.includes("mac") && S.platform !== "spark" ? "DGX Spark only" : "not on this machine") : r.status === "gated" ? "waits on its author's approval of your download" : "not installed";
  const head = ok.length + " of " + sets.length + " looks are usable on this " + (typeof cardFace === "function" && cardFace() ? "GPU" : S.platform === "spark" ? "DGX Spark" : "Mac");
  return not.length ? el("details", { class: "fold" }, el("summary", {}, head), el("div", { class: "note", style: "margin:0" }, "Not usable yet: " + not.map(r => r.name + " (" + why(r) + ")").join(", ") + ".")) : head + "."; }

function lookCard(r) { const st = r.installing, busy = lookBusy(r), dead = r.status === "unsupported", gated = r.status === "gated";
  const on = (C.loras || []).some(p => p.id === r.id) && C.goes === "video";
  const src = r.cover ? file(r.cover) : LOOK_COVERS[r.id];  // a cover kept beside the LoRA wins (loras/<id>.webp)
  const cover = src ? el("div", { class: "art ar1" }, el("img", { src, alt: "", loading: "lazy", decoding: "async" })) : artSlot("look-" + r.id, "ar1", "looks");
  const state = busy ? el("span", { class: "tag2 t" }, st.stage + (st.of ? " " + Math.round(100 * st.done / st.of) + "%" : "…"))
    : r.installed ? el("span", { class: "tag2 ok" }, "Installed") : null;
  const use = !r.installed || busy || dead ? null
    : r.status === "action" ? (typeof actHubButton === "function" ? actHubButton(r) : null)
    : r.target === "image" ? el("button", { type: "button", class: "small", title: "Opens Edit a picture with this look on", onclick: () => edUse(r) }, "Use in Image")
    : el("button", { type: "button", class: "small" + (on ? " on" : ""), title: "Puts this look on the clip in Create", onclick: () => lookUseVideo(r) }, on ? "On in Video" : "Use in Video");
  return el("div", { class: "lookcard" + (on ? " on" : "") + (dead || gated ? " off" : ""), "data-lora": r.id },
    cover,
    el("div", { class: "lookbody" },
      el("div", { class: "lookname" }, el("b", {}, r.name), r.author ? el("span", { class: "by", title: r.page || "" }, "by " + r.author) : null),
      r.what || r.notes ? el("p", { class: "lookone", title: r.what || r.notes }, r.what || r.notes) : null,
      el("div", { class: "looktags" }, state,
        el("span", { class: "tag2" + (dead ? " warn" : "") }, LORA_NEEDS[r.needs] || LORA_NEEDS.text),
        r.licence_tag ? el("span", { class: "tag2" + (/none|gated|custom/i.test(r.licence_tag) ? " warn" : ""), title: r.licence || "" }, r.licence_tag) : null,
        r.size ? el("span", { class: "tag2" }, sizeText(r.size)) : null),
      dead || gated ? el("p", { class: "lookwhy" }, (dead ? "Not supported in the Studio yet. " : "") + (r.why || "")) : null,
      r.status === "action" && r.installed ? el("p", { class: "lookwhy" }, "A Library action: used from a take's actions.") : null,
      st && st.stage === "failed" ? el("p", { class: "lookerr" }, st.error || "The download failed.") : null,
      el("div", { class: "lookacts" },
        dead ? null
        : busy ? null
        : !r.installed ? el("button", { type: "button", class: "small", title: r.page || "", onclick: () => lookInstall([r]) }, st && st.stage === "failed" ? "Try again" : "Install")
        : use,
        r.trained && r.installed && !busy && typeof trainTryButton === "function" ? trainTryButton(r) : null,  // a LoRA trained here (train.js)
        r.installed && !busy ? el("button", { type: "button", class: "small ghost", title: "Moves this LoRA's file to the trash folder" + (r.builtin ? "; the preset stays here" : " and takes it out of the hub"), onclick: () => lookRemove(r) }, "Remove") : null))); }

function lookForm() { const f = LK.form;
  const inp = (k, attrs = {}) => el("input", { ...attrs, value: f[k], oninput: e => { f[k] = attrs.type === "number" ? Number(e.target.value) : e.target.value; } });
  const box = (label, node, cls) => el("div", { class: "lookfield" + (cls ? " " + cls : "") }, el("label", {}, label), node);
  const up = el("input", { type: "file", accept: ".safetensors", onchange: async () => { if (!up.files[0]) return; const data = new FormData(); data.append("file", up.files[0]);
    try { const r = await (await fetch("/api/upload", { method: "POST", body: data })).json(); f.file = r.path; toast("Uploaded " + up.files[0].name); } catch (e) { toast(e.message); } } });
  const add = async () => { try { const styles = Object.fromEntries(String(f.styles).split(";").map(x => x.split("=").map(y => y.trim())).filter(x => x.length === 2 && x[0] && x[1]));
      const r = await api("/api/loras", { ...f, styles }); if (!r.installed && r.url) await api("/api/loras/" + r.id + "/install", {}); await loadLoras();
      LK.form = { name: "", url: "", file: "", trigger: "", strength: 1, styles: "", author: "", notes: "" }; LK.open = false; hubWatch(render); toast("Added: " + (r.name || f.name)); render(); } catch (e) { toast(e.message); } };
  return el("div", { class: "lookform" },
    el("p", { class: "note" }, "A recipe for a MiniMax H3 LoRA: where its file comes from and how it is used."),
    el("div", { class: "lookgrid2" }, box("Name", inp("name", { placeholder: "My 16mm western LoRA" })), box("By", inp("author", { placeholder: "its author" })),
      box("Download address of the .safetensors file", inp("url", { placeholder: "https://…/lora.safetensors" })), box("or upload the file", up),
      box("Trigger word", inp("trigger", { placeholder: "added at the start of the shot" })), box("Strength", inp("strength", { type: "number", step: 0.1, min: 0.1, max: 2 })),
      box("Looks (optional): name = phrase; name = phrase", inp("styles", { placeholder: "VHS = shot on VHS.; 16mm = shot on 16mm film." }), "wide"),
      box("Notes", inp("notes", { placeholder: "what it does, the sizes and steps it was made for" }), "wide")),
    el("div", { class: "lookacts" }, el("button", { type: "button", class: "primary", onclick: add }, "Add to Looks"),
      el("button", { type: "button", class: "ghost", onclick: () => { LK.open = false; render(); } }, "Cancel"))); }

function viewLooks() {
  if (!LK.asked) { LK.asked = true; if (!LORAS.length || !HUB.groups.length) loadLoras().then(() => render()); }
  if (LORAS.some(lookBusy)) hubWatch(render);
  const cats = r => r.category || ["mine"], count = id => LORAS.filter(r => id === "all" || cats(r).includes(id)).length;
  const tabs = [{ id: "all", name: "All" }, ...HUB.groups].filter(g => g.id === "all" || count(g.id));
  if (!tabs.some(g => g.id === HUB.tab)) HUB.tab = "all";
  const words = HUB.q.trim().toLowerCase(), hit = r => !words || [r.name, r.author, r.what, r.notes, r.trigger].join(" ").toLowerCase().includes(words);
  const shown = LORAS.filter(r => (HUB.tab === "all" || cats(r).includes(HUB.tab)) && hit(r) && (!HUB.mine || r.installed));
  const toInstall = shown.filter(r => !r.installed && r.status !== "unsupported" && !lookBusy(r));
  const search = el("input", { type: "search", class: "looksearch", placeholder: "Search looks: horror, anime, orbit…", value: HUB.q, "aria-label": "Search looks",
    oninput: e => { HUB.q = e.target.value; const at = e.target.selectionStart; render(); const again = document.querySelector(".looks input[type=search]"); if (again) { again.focus(); again.setSelectionRange(at, at); } } });
  // two segments: the hub's looks, and Train a LoRA from Library clips and stills (train.js)
  const training = !!LK.train && typeof trainPanel === "function";
  const segs = typeof trainPanel === "function" ? el("div", { class: "seg-ctl", role: "tablist", "aria-label": "Looks or train a LoRA", style: "margin-bottom:14px" },
    [[false, "Looks"], [true, "Train a LoRA"]].map(([on, label]) => el("button", { type: "button", role: "tab", "aria-selected": training === on ? "true" : "false", class: training === on ? "on" : "",
      onclick: () => { if (training === on) return; LK.train = on; render(); } }, label))) : null;
  const head = vaultHead("looks", "Looks", "Styles for pictures and clips, one LoRA a card.",
    training ? null : el("span", { class: "chips" }, el("button", { type: "button", class: "small" + (LK.open ? " on" : ""), onclick: () => { LK.open = !LK.open; render(); } }, "+ Add your own")));
  if (training) return el("div", { class: "looks" }, head, segs, trainPanel());
  return el("div", { class: "looks" },
    head, segs,
    el("div", { class: "note looktally" }, LORAS.length ? lookTally() : "Loading the hub…"),
    LK.open ? lookForm() : null,
    el("div", { class: "looksbar" },
      el("div", { class: "seg-ctl" }, tabs.map(g => el("button", { type: "button", class: HUB.tab === g.id ? "on" : "", onclick: () => { HUB.tab = g.id; render(); } }, g.name))),
      search,
      el("label", { class: "switch lookmine" }, el("input", { type: "checkbox", checked: HUB.mine, onchange: e => { HUB.mine = e.target.checked; render(); } }), "Installed only"),
      HUB.tab !== "all" && toInstall.length ? el("button", { type: "button", class: "small", onclick: () => lookInstall(toInstall) }, "Install all " + toInstall.length) : null),
    shown.length ? el("div", { class: "lookgrid" }, shown.map(lookCard)) : el("p", { class: "note" }, LORAS.length ? "Nothing here matches." : ""),
    el("p", { class: "note" }, "Each look is its author's work under its own licence; the page on its card says where it comes from and who to credit.")); }
