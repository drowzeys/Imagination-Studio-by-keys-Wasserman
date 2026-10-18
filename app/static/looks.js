"use strict";
// Studio Vault → Looks: the LoRA hub as a style library. Each LoRA is a square cover card with its author, one line,
// what it needs and its licence; Install, Use in Image or Video, Remove. The same /api/loras routes as the hub inside
// Create (loraHub in index.html): install asks with the LoRA's licence first, a running install is watched, and a
// recipe of your own is added with the same POST and install.
const LK = { asked: false, open: false, form: { name: "", url: "", file: "", trigger: "", strength: 1, styles: "", author: "", notes: "" } };
const LOOK_COVERS = { "wasserman-80s-horror": "art/looks/80s-horror.webp" };  // a look with its own banner; the rest wait for art slots
const lookFirstStyle = r => Object.keys(r.styles || {})[0] || "";
const lookBusy = r => !!(r.installing && r.installing.stage !== "failed");
const lookTerms = r => "“" + r.name + "”" + (r.author ? " by " + r.author : "") + (r.size ? " (" + sizeText(r.size) + ")" : "") + "\n" + (r.licence || "The file is the author's, under its own licence: read it on the LoRA's page first.") + (r.page ? "\n" + r.page : "");
async function lookInstall(list) { if (!list.length) return toast("Nothing to download here");
  if (!confirm("Install on this machine?\n\n" + list.map(lookTerms).join("\n\n"))) return;
  for (const r of list) { try { await api("/api/loras/" + r.id + "/install", {}); } catch (e) { toast(e.message); } }
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
  const head = ok.length + " of " + sets.length + " looks are usable on this " + (S.platform === "spark" ? "DGX Spark" : "Mac");
  return not.length ? el("details", { class: "fold" }, el("summary", {}, head), el("div", { class: "note", style: "margin:0" }, "Not usable yet: " + not.map(r => r.name + " (" + why(r) + ")").join(", ") + ".")) : head + "."; }

function lookCard(r) { const st = r.installing, busy = lookBusy(r), dead = r.status === "unsupported", gated = r.status === "gated";
  const on = (C.loras || []).some(p => p.id === r.id) && C.goes === "video";
  const cover = LOOK_COVERS[r.id] ? el("div", { class: "art ar1" }, el("img", { src: LOOK_COVERS[r.id], alt: "", loading: "lazy", decoding: "async" })) : artSlot("look-" + r.id, "ar1", "looks");
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
  return el("div", { class: "looks" },
    pageHead("Studio Vault", "Looks", "Styles for pictures and clips, one LoRA a card.",
      el("button", { type: "button", class: "small" + (LK.open ? " on" : ""), onclick: () => { LK.open = !LK.open; render(); } }, "+ Add your own")),
    el("div", { class: "note looktally" }, LORAS.length ? lookTally() : "Loading the hub…"),
    LK.open ? lookForm() : null,
    el("div", { class: "looksbar" },
      el("div", { class: "seg-ctl" }, tabs.map(g => el("button", { type: "button", class: HUB.tab === g.id ? "on" : "", onclick: () => { HUB.tab = g.id; render(); } }, g.name))),
      search,
      el("label", { class: "switch lookmine" }, el("input", { type: "checkbox", checked: HUB.mine, onchange: e => { HUB.mine = e.target.checked; render(); } }), "Installed only"),
      HUB.tab !== "all" && toInstall.length ? el("button", { type: "button", class: "small", onclick: () => lookInstall(toInstall) }, "Install all " + toInstall.length) : null),
    shown.length ? el("div", { class: "lookgrid" }, shown.map(lookCard)) : el("p", { class: "note" }, LORAS.length ? "Nothing here matches." : ""),
    el("p", { class: "note" }, "Each look is its author's work under its own licence; the page on its card says where it comes from and who to credit.")); }
