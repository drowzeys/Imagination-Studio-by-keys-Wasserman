"use strict";
// Bring in: what the other apps of the filmmaker suite made (a screenplay, a board, a previs package, a storyboard)
// becomes shots here. And out again: a timeline for Resolve, dailies for Circle Take.
const SU = { apps: null, imports: null, cur: null, busy: false, said: "" };
// the folder on the editing computer a timeline package is unzipped into, remembered in this browser
const CUT_FOLDER = () => { try { return localStorage.getItem("cut-folder") || ""; } catch { return ""; } };
let CUT_BOX = null;  // made on first use: this file loads before the page's helpers
const cutFolder = () => CUT_BOX || (CUT_BOX = el("input", { placeholder: "/Users/you/Movies/Imagination Studio", value: CUT_FOLDER(), "aria-label": "Folder on the editing computer" }));
const saveCutFolder = () => { try { localStorage.setItem("cut-folder", cutFolder().value.trim()); } catch {} };
const timelineZip = id => { const at = (CUT_BOX && CUT_BOX.value.trim()) || CUT_FOLDER(); return "/api/export/timeline/" + id + ".zip" + (at ? "?root=" + encodeURIComponent(at) : ""); };
const suLoad = async () => { try { SU.apps = await api("/api/suite"); } catch { SU.apps = []; } try { SU.imports = await api("/api/suite/imports"); } catch { SU.imports = []; } };
async function suiteUpload(files) { if (!files || !files.length) return; SU.busy = true; SU.said = ""; render();
  for (const one of files) { const data = new FormData(); data.append("file", one);
    try { const r = await fetch("/api/suite/import", { method: "POST", body: data }); const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || j.error || r.statusText); SU.cur = j; toast("Brought in " + (j.title || one.name) + ": " + count((j.shots || []).length, "shot")); }
    catch (e) { SU.said = one.name + ": " + e.message; } }
  SU.busy = false; try { SU.imports = await api("/api/suite/imports"); } catch {} render(); }
function shotToCreate(s) { const lines = (s.dialogue || []).filter(d => d.line).map(d => ({ voice: "", language: "English", text: d.line, sung: false, who: d.character }));
  Object.assign(C, { scene: s.scene || "", action: s.action || s.title || "", camera: s.camera || "", dialogue: lines, goes: "video", prompt: "", step: 0 });
  if (s.image) { C.image = s.image; C.start = "own"; } else C.start = "text";
  if (s.reference_video && S.ref_media) { C.start = "refs"; if (!C.refVideos.includes(s.reference_video)) { C.refVideos.push(s.reference_video); C.video_subjects.push(""); } }
  sync(); go("Create"); toast("Shot " + (s.title || s.id) + " is in Create"); }
async function stillsForAll(imp) { let n = 0;
  for (const s of imp.shots || []) { const prompt = [s.scene, s.action].filter(Boolean).join(" "); if (!prompt.trim()) continue;
    try { await api("/api/jobs", { kind: "scout", title: "Still: " + (s.title || s.id || "").slice(0, 36), params: { prompt, count: 1, seed: 1, width: 1344, height: 768, image_model: typeof imageModel === "function" ? imageModel() : "qwen" } }); n++; } catch (e) { toast(e.message); break; } }
  if (n) { await poll(); bayOpen(true); toast(count(n, "still") + " queued"); } }
function importView(imp) {
  const shots = imp.shots || [];
  return el("div", { class: "panel stack" },
    el("div", { class: "kv su-imphead" }, el("div", { class: "stack su-tight" }, el("span", { class: "eyebrow" }, "From " + (imp.app || "a file")), el("h2", { class: "su-title" }, imp.title || "Untitled"),
      el("span", { class: "note su-flat" }, [count(shots.length, "shot"), (imp.cast || []).length ? count(imp.cast.length, "character") : "", (imp.locations || []).length ? count(imp.locations.length, "location") : ""].filter(Boolean).join(" · "))),
      el("div", { class: "chips" },
        el("button", { class: "primary", disabled: !shots.length, onclick: async () => { try { const r = await api("/api/suite/imports/" + imp.id + "/slate", {}); SL.mode = "slate"; SL.p = await api("/api/slate/projects/" + r.project); SL.metas = null; SL.view = "shots"; go("Production"); toast("A Slate film is ready"); } catch (e) { toast(e.message); } } }, "Make a Slate film"),
        el("button", { disabled: !shots.length, onclick: () => stillsForAll(imp) }, "A still for every shot"),
        /blockout/i.test(imp.app || "") ? el("button", { onclick: async () => { try { const r = await api("/api/suite/imports/" + imp.id + "/motion", {}); toast("Motion pack " + (r.pack || "") + " is in Motion Previs"); go("Motion Previs"); } catch (e) { toast(e.message); } } }, "Use as motion reference") : null)),
    (imp.warnings || []).length ? el("div", { class: "note su-flat vt-warn" }, imp.warnings.join(" · ")) : null,
    shots.length ? el("div", { class: "shots" }, shots.map((s, i) => el("div", { class: "shot" },
      el("div", { class: "thumb" }, s.image ? el("img", { src: file(s.image), loading: "lazy" }) : s.reference_video ? el("video", { src: file(s.reference_video) + "#t=0.5", muted: true, preload: "metadata" }) : "no picture yet", el("span", { class: "n" }, s.id || String(i + 1))),
      el("div", { class: "body" }, el("b", {}, s.title || "Shot " + (i + 1)), el("span", {}, [s.scene, s.action].filter(Boolean).join(" ") || "No description"),
        (s.dialogue || []).length ? el("span", { class: "su-line" }, s.dialogue.map(d => (d.character ? d.character + ": " : "") + "“" + d.line + "”").join(" ")) : null,
        el("div", { class: "su-shotfoot" }, s.seconds ? el("span", { class: "tag2" }, s.seconds + " s") : null, el("span", { class: "su-grow" }),
          el("button", { class: "small", onclick: () => shotToCreate(s) }, "Open in Create")))))) : el("div", { class: "note" }, "No shots were found in this file."),
    (imp.cast || []).length ? el("div", {}, el("h3", {}, "Cast"), el("div", { class: "chips" }, imp.cast.map(c => el("span", { class: "tag2", title: c.description || "" }, c.name)))) : null,
    (imp.locations || []).length ? el("div", {}, el("h3", {}, "Locations"), el("div", { class: "chips" }, imp.locations.map(c => el("span", { class: "tag2", title: c.description || "" }, c.name)))) : null);
}
/* the suite's apps: each with its own icon (art/suite/<slug>.webp) and what it does here: brought in, sent out to, or
   working alongside. The server's list (/api/suite) is used when it answers, home.js's SUITE_APPS when it does not. */
const SU_ICON = { scriptbreak: "scriptbreak", "cork-board": "cork-board", blockout: "blockout", "storyboard-reference": "storyboard-reference", "master-canvas": "master-canvas",
  slate: "slate", "circle-take": "circle-take", "stem-studio": "stem-studio", "call-sheet": "call-sheet", "motion-previs-studio": "motion-previs-studio", "davinci-mcp": "davinci-mcp",
  editor: "davinci-mcp" };
const SU_ROLE = { scriptbreak: "in", "cork-board": "in", blockout: "in", "storyboard-reference": "in", "master-canvas": "in", slate: "in", fountain: "in",
  "circle-take": "dailies", editor: "timeline", "motion-previs-studio": "previs", "stem-studio": "info", "call-sheet": "info", "davinci-mcp": "info" };
const SU_BY = "Sam Wasserman · Wasserman Productions";
const SU_MORE = [{ id: "call-sheet", name: "Call Sheet", what: "The suite's launcher and dashboard.", by: SU_BY, licence: "Apache-2.0" }, { id: "motion-previs-studio", name: "Motion Previs Studio", what: "Pose, depth and camera from a clip.", by: SU_BY, licence: "Apache-2.0" },
  { id: "davinci-mcp", name: "DaVinci MCP", what: "Your agent drives Resolve.", by: "Sam Wasserman · Wasserman Productions", licence: "Apache-2.0" }];
const suSlug = a => a.id || String(a.name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
function suApps() { const list = (SU.apps && SU.apps.length ? SU.apps : SUITE_APPS.map(([name, what]) => ({ name, what }))).map(a => ({ ...a, id: suSlug(a) }));
  for (const m of SU_MORE) if (!list.some(a => a.id === m.id)) list.push(m);
  return list; }
const suSendout = () => { const n = typeof document !== "undefined" && document.getElementById && document.getElementById("su-sendout"); if (n && n.scrollIntoView) n.scrollIntoView({ behavior: "smooth", block: "start" }); };
function suAppCard(a, act) { const role = SU_ROLE[a.id] || "info", slug = SU_ICON[a.id];
  const [tag, cls, run] = role === "in" ? ["Bring in", "t", act.bring] : role === "dailies" ? ["Send out · dailies", "", act.dailies] : role === "timeline" ? ["Send out · timeline", "", suSendout]
    : role === "previs" ? ["Open in Previs", "", () => go("Motion Previs")] : ["Works alongside", "", null];
  const pic = slug ? el("img", { class: "su-icon", src: "art/suite/" + slug + ".webp", alt: "", loading: "lazy", decoding: "async" }) : el("span", { class: "su-icon blank" }, icon(a.id === "fountain" ? "doc" : "suite"));
  const kids = [pic, el("div", { class: "su-txt" }, el("b", {}, a.name), el("span", {}, a.what || ""),
    a.by ? el("small", { class: "su-by" }, "by " + a.by + (a.licence ? " · " + a.licence : "")) : null), el("span", { class: "tag2 " + cls }, tag)];
  return run ? el("button", { class: "su-app", type: "button", title: tag + ": " + a.name, onclick: run }, ...kids) : el("div", { class: "su-app still" }, ...kids); }
function viewImport() {
  if (SU.apps === null) { suLoad().then(() => { if (tab === "Import") render(); }); }
  const input = el("input", { type: "file", multiple: true, class: "hide", accept: ".json,.zip,.fountain,.fdx,.txt,.md,.scriptbreak", onchange: () => suiteUpload([...input.files]) });
  const zone = el("div", { class: "dropzone", tabindex: "0", role: "button", "aria-label": "Choose files to bring in", onclick: () => input.click(), onkeydown: e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } },
    ondragover: e => { e.preventDefault(); zone.classList.add("over"); }, ondragleave: () => zone.classList.remove("over"), ondrop: e => { e.preventDefault(); zone.classList.remove("over"); suiteUpload([...e.dataTransfer.files]); } },
    input, el("span", { class: "eyebrow" }, SU.busy ? "Reading…" : "Drop files here"), el("b", {}, SU.busy ? "Turning it into shots" : "A screenplay, a board, a previs package or a storyboard"),
    el("span", { class: "note su-flat" }, "Each shot opens in Create with its words, frames and motion in place."),
    SU.said ? el("span", { class: "note su-flat vt-bad" }, SU.said) : null);
  ensureProject().catch(() => {});
  const pid = () => project && project.id;
  const picked = () => [...PICK].length ? [...PICK] : jobs.filter(j => j.status === "done").slice(0, 24).map(j => j.id);
  const dailies = () => { const ids = picked(); if (!ids.length) return toast("Finish a take first"); location.assign("/api/export/dailies.zip?jobs=" + ids.join(",")); };
  const act = { bring: () => input.click(), dailies };
  return el("div", {},
    vaultHead("suite", "Wasserman's Filmmaker Suite", "The entire Wasserman Filmmaker Suite of apps, built in: bring work in, send the cut out."),
    el("div", { class: "su-apps" }, suApps().map(a => suAppCard(a, act))),
    el("div", { class: "su-gap" }, zone),
    el("details", { class: "fold" }, el("summary", {}, "What it reads"),
      el("div", { class: "note su-flat" }, "Fountain or Final Draft scripts, a ScriptBreak project, Cork Board or Master Canvas JSON, a Master Canvas hand-off, a Storyboard Reference export or a Blockout package (.zip), or a Slate project.json.")),
    el("div", { class: "split su-gap" },
      el("div", { class: "stack" }, SU.cur ? importView(SU.cur) : el("div", { class: "panel" }, el("h2", {}, "Nothing open"), el("div", { class: "note" }, "Drop a file above, or open one you brought in before.")),
        (SU.imports || []).length ? el("div", { class: "panel" }, el("h2", {}, "Brought in before"), el("div", { class: "list" }, SU.imports.slice(0, 20).map(m => el("button", { class: SU.cur && SU.cur.id === m.id ? "on" : "",
          onclick: async () => { try { SU.cur = await api("/api/suite/imports/" + m.id); } catch { SU.cur = m; } render(); } }, el("div", {}, el("b", {}, m.title || m.id), el("span", {}, (m.app || "") + " · " + count(typeof m.shots === "number" ? m.shots : (m.shots || []).length, "shot"))))))) : null),
      el("div", { class: "stack" },
        el("div", { class: "panel stack", id: "su-sendout" }, el("h2", {}, "Send out"),
          el("div", { class: "note su-flat" }, "The timeline, a cut list and dailies, in the formats the other tools read."),
          field("Where you will unzip it, on the computer you edit on", cutFolder()),
          el("button", { onclick: () => { if (!pid()) return toast("Open the Timeline once first"); saveCutFolder(); location.assign(timelineZip(pid())); } }, "Timeline with media for Resolve (ZIP)"),
          el("button", { onclick: () => pid() ? location.assign("/api/export/timeline/" + pid() + ".fcpxml") : toast("Open the Timeline once first") }, "FCPXML only, for this machine's paths"),
          el("button", { onclick: () => pid() ? location.assign("/api/export/timeline/" + pid() + ".edl") : toast("Open the Timeline once first") }, "Cut list (EDL)"),
          el("button", { onclick: dailies }, "Dailies for Circle Take" + (PICK.size ? " (" + PICK.size + " picked)" : "")),
          el("details", { class: "fold" }, el("summary", {}, "How these work"),
            el("div", { class: "note su-flat" }, "Unzip the timeline in that folder and import the .fcpxml: every clip opens online. Leave the folder empty to relink by hand."),
            el("div", { class: "note" }, "Dailies are the takes ticked in the render bay, or the latest finished ones. Each carries its prompt, seed, model and LoRAs, so Circle Take can gate it and a reshoot comes back with the same settings."))))));
}
