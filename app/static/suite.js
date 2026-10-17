"use strict";
// Bring in: what the other apps of the filmmaker suite made (a screenplay, a board, a previs package, a storyboard)
// becomes shots here. And out again: a timeline for Resolve, dailies for Circle Take.
const SU = { apps: null, imports: null, cur: null, busy: false, said: "" };
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
    el("div", { class: "kv", style: "align-items:flex-start" }, el("div", { class: "stack", style: "gap:4px" }, el("span", { class: "eyebrow" }, "From " + (imp.app || "a file")), el("h2", { style: "margin:0" }, imp.title || "Untitled"),
      el("span", { class: "note", style: "margin:0" }, [count(shots.length, "shot"), (imp.cast || []).length ? count(imp.cast.length, "character") : "", (imp.locations || []).length ? count(imp.locations.length, "location") : ""].filter(Boolean).join(" · "))),
      el("div", { class: "chips" },
        el("button", { class: "primary", disabled: !shots.length, onclick: async () => { try { const r = await api("/api/suite/imports/" + imp.id + "/slate", {}); SL.mode = "slate"; SL.p = await api("/api/slate/projects/" + r.project); SL.metas = null; SL.view = "shots"; go("Production"); toast("A Slate film is ready"); } catch (e) { toast(e.message); } } }, "Make a Slate film"),
        el("button", { disabled: !shots.length, onclick: () => stillsForAll(imp) }, "A still for every shot"),
        /blockout/i.test(imp.app || "") ? el("button", { onclick: async () => { try { const r = await api("/api/suite/imports/" + imp.id + "/motion", {}); toast("Motion pack " + (r.pack || "") + " is in Motion Previs"); go("Motion Previs"); } catch (e) { toast(e.message); } } }, "Use as motion reference") : null)),
    (imp.warnings || []).length ? el("div", { class: "note", style: "color:var(--warn);margin:0" }, imp.warnings.join(" · ")) : null,
    shots.length ? el("div", { class: "shots" }, shots.map((s, i) => el("div", { class: "shot" },
      el("div", { class: "thumb" }, s.image ? el("img", { src: file(s.image), loading: "lazy" }) : s.reference_video ? el("video", { src: file(s.reference_video) + "#t=0.5", muted: true, preload: "metadata" }) : "no picture yet", el("span", { class: "n" }, s.id || String(i + 1))),
      el("div", { class: "body" }, el("b", {}, s.title || "Shot " + (i + 1)), el("span", {}, [s.scene, s.action].filter(Boolean).join(" ") || "No description"),
        (s.dialogue || []).length ? el("span", { style: "color:#ffdcae" }, s.dialogue.map(d => (d.character ? d.character + ": " : "") + "“" + d.line + "”").join(" ")) : null,
        el("div", { style: "display:flex;gap:6px;margin-top:6px;align-items:center" }, s.seconds ? el("span", { class: "tag2" }, s.seconds + " s") : null, el("span", { style: "flex:1" }),
          el("button", { class: "small", onclick: () => shotToCreate(s) }, "Open in Create")))))) : el("div", { class: "note" }, "No shots were found in this file."),
    (imp.cast || []).length ? el("div", {}, el("h3", {}, "Cast"), el("div", { class: "chips" }, imp.cast.map(c => el("span", { class: "tag2", title: c.description || "" }, c.name)))) : null,
    (imp.locations || []).length ? el("div", {}, el("h3", {}, "Locations"), el("div", { class: "chips" }, imp.locations.map(c => el("span", { class: "tag2", title: c.description || "" }, c.name)))) : null);
}
function viewImport() {
  if (SU.apps === null) { suLoad().then(() => { if (tab === "Import") render(); }); }
  const input = el("input", { type: "file", multiple: true, style: "display:none", accept: ".json,.zip,.fountain,.fdx,.txt,.md,.scriptbreak", onchange: () => suiteUpload([...input.files]) });
  const zone = el("div", { class: "dropzone", tabindex: "0", role: "button", "aria-label": "Choose files to bring in", onclick: () => input.click(), onkeydown: e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } },
    ondragover: e => { e.preventDefault(); zone.classList.add("over"); }, ondragleave: () => zone.classList.remove("over"), ondrop: e => { e.preventDefault(); zone.classList.remove("over"); suiteUpload([...e.dataTransfer.files]); } },
    input, el("span", { class: "eyebrow" }, SU.busy ? "Reading…" : "Drop files here"), el("b", {}, SU.busy ? "Turning it into shots" : "A screenplay, a board, a previs package or a storyboard"),
    el("span", { class: "note", style: "margin:0;max-width:60ch" }, "Fountain or Final Draft scripts, a ScriptBreak project, Cork Board or Master Canvas JSON, a Master Canvas hand-off, a Storyboard Reference export or a Blockout package (.zip), or a Slate project.json."),
    SU.said ? el("span", { class: "note", style: "margin:0;color:var(--bad)" }, SU.said) : null);
  const apps = SU.apps && SU.apps.length ? SU.apps : SUITE_APPS.map(([name, what]) => ({ name, what }));
  ensureProject().catch(() => {});
  const pid = () => project && project.id;
  const picked = () => [...PICK].length ? [...PICK] : jobs.filter(j => j.status === "done").slice(0, 24).map(j => j.id);
  return el("div", {},
    el("div", { class: "sectionhead", style: "margin-top:0;align-items:flex-end" }, el("div", { class: "stack", style: "gap:8px" }, el("span", { class: "eyebrow" }, "The suite"),
      el("h2", { class: "display", style: "font-size:clamp(34px,4.4vw,58px)" }, "Everything you planned, ", el("em", {}, "ready to roll."))),
      el("p", { class: "sub", style: "margin:0;max-width:44ch" }, "Bring in what ScriptBreak, Cork Board, Blockout and the rest of the suite made. Each shot opens in Create with its words, frames and motion already in place.")),
    zone,
    el("div", { class: "split", style: "margin-top:18px" },
      el("div", { class: "stack" }, SU.cur ? importView(SU.cur) : el("div", { class: "panel" }, el("h2", {}, "Nothing open"), el("div", { class: "note" }, "Drop a file above, or open one you brought in before.")),
        (SU.imports || []).length ? el("div", { class: "panel" }, el("h2", {}, "Brought in before"), el("div", { class: "list" }, SU.imports.slice(0, 20).map(m => el("button", { class: SU.cur && SU.cur.id === m.id ? "on" : "",
          onclick: async () => { try { SU.cur = await api("/api/suite/imports/" + m.id); } catch { SU.cur = m; } render(); } }, el("div", {}, el("b", {}, m.title || m.id), el("span", {}, (m.app || "") + " · " + count(typeof m.shots === "number" ? m.shots : (m.shots || []).length, "shot"))))))) : null),
      el("div", { class: "stack" },
        el("div", { class: "panel stack" }, el("h2", {}, "Send out"),
          el("div", { class: "note", style: "margin:0" }, "Your timeline, cut list and dailies, in the formats the other tools read."),
          el("button", { class: "primary", onclick: () => pid() ? location.assign("/api/export/timeline/" + pid() + ".fcpxml") : toast("Open the Timeline once first") }, "Timeline to Resolve (FCPXML)"),
          el("button", { onclick: () => pid() ? location.assign("/api/export/timeline/" + pid() + ".edl") : toast("Open the Timeline once first") }, "Cut list (EDL)"),
          el("button", { onclick: () => { const ids = picked(); if (!ids.length) return toast("Finish a take first"); location.assign("/api/export/dailies.zip?jobs=" + ids.join(",")); } }, "Dailies for Circle Take" + (PICK.size ? " (" + PICK.size + " picked)" : "")),
          el("div", { class: "note", style: "margin:0" }, "Dailies are the takes ticked in the render bay, or the latest finished ones. Each carries its prompt, seed, model and LoRAs, so Circle Take can gate it and a reshoot comes back with the same settings.")),
        el("div", { class: "panel" }, el("h2", {}, "Apps Studio reads"), el("div", { class: "stack", style: "gap:8px" }, apps.map(a => el("div", { class: "kv", style: "padding:8px 0;border-bottom:1px solid var(--line);align-items:flex-start;gap:16px" },
          el("b", { style: "font:650 14px var(--display);white-space:nowrap" }, a.name), el("span", { style: "text-align:right;font-family:var(--sans);font-size:12.5px" }, a.what || ""))))))));
}
