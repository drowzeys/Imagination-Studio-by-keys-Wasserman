"use strict";
/* Character Swap (app/swap.py): a Library action on a clip. The person in a 5 to 15 second part of one continuous shot
   is replaced by the character in one picture; the scene, the performance and the clip's own sound stay. It opens in
   the Library's action panel (actions.js: actOpen, ACT.open, actQueue, actShow) and queues one job of kind "swap".
   Credit: the idea and its settings are Comfy Org's Openjutsu and Akatz Labs' Character Swap workflow (MIT); Akatz
   Labs' Character Swap LoRA and LightX2V's Turbo LoRA on MiniMax H3 Ref2VA, Meta's SAM 3.1 for the person. */
const SWAP = { state: null, lib: null, picking: false, length: null, for: null,
  f: { image: null, start: 0, seconds: 5, quality: "480", anchor: true, direction: "", seed: 904239 } };
async function swapLoad() { try { SWAP.state = await api("/api/swap"); } catch { SWAP.state = { ready: false, why: "" }; } }
// the button a clip's Library tile gets
function swapButtons(m) { if (m.kind !== "video") return [];
  if (!SWAP.state) { SWAP.state = { ready: false, why: "" }; swapLoad().then(() => { if (tab === "Library") render(); }); }
  return [el("button", { class: "small", title: SWAP.state.ready ? "Replace the person in a part of this clip with the character in a picture: the scene, the performance and the sound stay"
    : SWAP.state.why || "Character Swap", onclick: () => actOpen("swap", m.path) }, "Swap the character")]; }
const swapAnchorBy = () => SWAP.state && SWAP.state.anchor_model === "qwen" ? "Qwen-Image-2.1" : "FLUX3-K Fast";
// the windows a part is made in (spark/swap_plan.py): 124 frames, then 106 more for each further window (each is at most
// 124 frames, the 18 it carries from the window before included)
const swapWindows = seconds => { const n = Math.round(Number(seconds) * 24); return n <= 124 ? 1 : 1 + Math.ceil((n - 124) / 106); };
// the time a swap takes on a DGX Spark, from the studio's measured stages (/api/swap estimates, app/swap.py)
const swapMinutes = (f, st) => { const q = (st.estimates || {})[f.quality] || {}, n = Math.min(15, Math.max(5, Math.round(Number(f.seconds))));
  const s = q[String(n)]; if (!s) return ""; const t = s - (f.anchor ? 0 : ((st.anchor_seconds || {})[f.quality] || 0)); return "about " + Math.max(1, Math.round(t / 60)) + " min"; };
const swapEnd = f => Math.round((Number(f.start) + Number(f.seconds)) * 100) / 100;
function swapPick(path) { SWAP.f.image = path; SWAP.picking = false; render(); }
function swapPanel(o) { const f = SWAP.f, st = SWAP.state || {}, redraw = () => render();
  if (SWAP.for !== o.path) { SWAP.for = o.path; SWAP.length = null; f.start = 0; }
  const box = el("div", { class: "seg", id: "actpanel", style: "margin-top:12px" },
    el("div", { class: "seghead" }, el("b", {}, "Character Swap"), el("span", { class: "note", style: "margin:0" }, o.path.split("/").pop()), el("span", { style: "flex:1" }),
      el("button", { class: "small", onclick: () => { ACT.open = null; render(); } }, "Close")));
  if (!st.ready) { put(box, el("div", { class: "note", style: "color:var(--warn)" }, st.why || "Not available on this machine.")); return box; }
  const clip = media(o.path, { style: "width:260px;border-radius:var(--r2)" });
  if (clip.addEventListener) clip.addEventListener("loadedmetadata", () => { if (!SWAP.length && clip.duration) { SWAP.length = clip.duration; render(); } });
  const up = el("input", { type: "file", accept: ".png,.jpg,.jpeg,.webp", "aria-label": "Upload the character's picture", onchange: async () => { if (!up.files[0]) return; const data = new FormData(); data.append("file", up.files[0]);
    try { const r = await (await fetch("/api/upload", { method: "POST", body: data })).json(); if (!r.path) throw new Error(r.error || "upload failed"); swapPick(r.path); } catch (e) { toast(e.message); } } });
  const pick = el("button", { class: "fit", onclick: () => { SWAP.picking = !SWAP.picking; if (SWAP.picking) api("/api/library").then(list => { SWAP.lib = list; render(); }).catch(() => {}); render(); } }, SWAP.picking ? "Close the Library" : "Pick from the Library");
  const stills = (SWAP.lib || []).filter(m => m.kind === "image").slice(0, 24);
  const most = SWAP.length ? Math.max(0, Math.floor((SWAP.length - Number(f.seconds)) * 10) / 10) : null;
  const start = el("input", { type: "number", min: 0, step: 0.1, value: f.start, style: "max-width:110px", "aria-label": "Where the part starts, in seconds", ...(most != null ? { max: most } : {}),
    oninput: e => { f.start = Math.max(0, Number(e.target.value) || 0); said.textContent = swapSaid(); } });
  const length = el("input", { type: "range", min: 5, max: 15, step: 1, value: f.seconds, "aria-label": "How long the part is, 5 to 15 seconds",
    oninput: e => { f.seconds = Number(e.target.value); said.textContent = swapSaid(); took.textContent = time(); } });
  const swapSaid = () => f.seconds + " s, from " + Number(f.start).toFixed(1) + " s to " + swapEnd(f).toFixed(1) + " s" + (swapWindows(f.seconds) > 1 ? " · " + swapWindows(f.seconds) + " windows" : " · one window");
  const said = el("b", {}, swapSaid());
  const time = () => { const m = swapMinutes(f, st); return m ? m + " on a DGX Spark" : ""; };
  const anchor = el("input", { type: "checkbox", checked: !!f.anchor, onchange: e => { f.anchor = e.target.checked; took.textContent = time(); } });
  const took = el("span", { class: "note", style: "margin:0" }, time());
  put(box, el("div", { class: "row", style: "align-items:flex-start" }, el("div", { style: "flex:0 0 260px" }, clip),
    el("div", {}, el("div", { class: "note", style: "margin-top:0" }, "Replaces the person in a part of this clip with the character in one picture. The scene, the performance and the clip’s own sound stay. SAM 3.1 finds the largest person in the part’s first frame and follows them; only that person is drawn again, the rest of each frame is the clip’s own. Best on one continuous shot with the person in view from the first frame."),
      field("The character", el("div", { class: "row" }, el("div", { class: "fit" }, up), pick, el("span"))),
      f.image ? el("div", { class: "row", style: "align-items:center" }, media(f.image, { style: "max-width:160px;border-radius:var(--r2)" }), el("span", { class: "note", style: "margin:0" }, "The character, outfit included, as the result should show them."))
        : el("div", { class: "note" }, "A picture of the character, head to toe if you can: face, hair and the whole outfit come from it."),
      SWAP.picking ? (stills.length ? el("div", { class: "grid", style: "margin-top:10px" }, stills.map(m => el("div", { class: "tile" + (f.image === m.path ? " on" : ""), style: "cursor:pointer", title: m.path, onclick: () => swapPick(m.path) }, media(m.path))))
        : el("div", { class: "note" }, SWAP.lib ? "No pictures in the Library yet: upload one." : "Reading the Library…")) : null,
      el("div", { class: "row" }, field("Part starts at (s)", start), el("div", {}, el("label", {}, "Part length"), length, said)),
      field("Quality", el("div", { class: "row", style: "align-items:center" }, el("div", { class: "seg-ctl", role: "radiogroup", "aria-label": "Quality" }, [["480", "Fast", "480p"], ["768", "High", "768p, a little over twice as long"]].map(([v, t, s]) =>
        el("button", { type: "button", role: "radio", "aria-checked": f.quality === v ? "true" : "false", class: f.quality === v ? "on" : "", title: s, onclick: () => { f.quality = v; redraw(); } }, t + " · " + v + "p"))), took)),
      el("label", { class: "switch", style: "margin-top:8px" }, anchor, el("span", {}, "Put the character in the first frame first",
        el("small", {}, swapAnchorBy() + " draws the character into the part’s first frame, and the swap starts from that frame. Off: the swap starts from the clip’s own first frame."))),
      el("div", { class: "row" }, field("Direction (optional)", bind(f, "direction", el("input", { maxlength: (st.direction_most || 500), placeholder: "she keeps her hands in her coat pockets · the character’s cape moves in the wind" }))),
        field("Seed", bind(f, "seed", el("input", { type: "number", min: 0, style: "max-width:120px" })))),
      el("div", { class: "note" }, st.licence || ""),
      el("button", { class: "hot", disabled: ACT.busy, onclick: () => { if (!f.image) return toast("Choose the character’s picture first");
        if (SWAP.length && swapEnd(f) > SWAP.length + 0.05) return toast("The clip is " + SWAP.length.toFixed(1) + " s long: start the part earlier or make it shorter");
        actQueue("swap", { video: o.path, image: f.image, start: Number(f.start) || 0, seconds: Number(f.seconds), quality: Number(f.quality), anchor: !!f.anchor,
          ...(String(f.direction).trim() ? { direction: String(f.direction).trim() } : {}), seed: Number(f.seed) || 0 }); } }, "Swap the character"))),
    actShow("swap", job => el("div", { class: "row", style: "align-items:flex-start;margin-top:8px" },
      el("div", {}, el("label", {}, "Swapped"), el("video", { class: "main", src: file(job.outputs[0]), controls: true })),
      job.outputs[1] ? el("div", {}, el("label", {}, "Who was swapped, in green"), el("video", { class: "main", src: file(job.outputs[1]), controls: true, loop: true })) : null,
      job.outputs[2] ? el("div", {}, el("label", {}, "The first frame it started from"), el("img", { src: file(job.outputs[2]), style: "width:100%;border-radius:var(--r2)" })) : null)));
  return box; }
