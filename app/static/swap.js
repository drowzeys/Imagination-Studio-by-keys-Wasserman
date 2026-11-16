"use strict";
/* Character Swap (app/swap.py): a Library action on a clip. The person in a 5 to 15 second part of one continuous shot
   is replaced by the character in one picture; the scene, the performance and the clip's own sound stay. It opens in
   the Library's action panel (actions.js: actOpen, ACT.open, actQueue, actShow) and queues one job of kind "swap".
   Credit: the idea and its settings are Comfy Org's Openjutsu and Akatz Labs' Character Swap workflow (MIT); Akatz
   Labs' Character Swap LoRA and LightX2V's Turbo LoRA on MiniMax H3 Ref2VA, Meta's SAM 3.1 for the person.
   Several people in one job (f.swaps, at most st.most_swaps): the chain of Noncoder's MiniMax H3 Multi Swap workflow.
   "Number the people" queues a swap job with who: true (slot "swapwho"): everyone followed through the part, numbered. */
const swapOne = () => ({ image: null, who: "", direction: "" });
const SWAP = { state: null, lib: null, picking: false, length: null, for: null,
  f: { swaps: [swapOne()], start: 0, seconds: 5, quality: "480", anchor: true, refFull: null, transformer: "", seed: 904239 } };
// what a swap's "who" field says: a number of the numbered picture, a few words, or nothing (the largest person)
const swapWho = one => { const w = String(one.who || "").trim(); return /^[1-9]$/.test(w) ? { which: Number(w) } : w ? { person: w } : {}; };
// the job's settings: one person named by words or not at all is the swap as it always was (the whole frame); a
// person picked by number, or several people, go as swaps
function swapParams(path, f, st) { const all = f.swaps.map(one => ({ image: one.image, ...swapWho(one), ...(String(one.direction).trim() ? { direction: String(one.direction).trim() } : {}) }));
  const sizes = st.ref_sizes || [];  // offered only where full size may run; the first is this machine's default
  const base = { video: path, start: Number(f.start) || 0, seconds: Number(f.seconds), quality: Number(f.quality), anchor: !!f.anchor,
    ...(sizes.includes("max") && f.refFull != null ? { ref_size: f.refFull ? "max" : "match" } : {}),
    ...(f.transformer && (st.transformers || []).includes(f.transformer) ? { transformer: f.transformer } : {}), seed: Number(f.seed) || 0 };
  return all.length === 1 && !all[0].which ? { ...base, ...all[0] } : { ...base, swaps: all }; }
// why the job cannot be queued yet, or ""
function swapWhy(f) { if (f.swaps.some(one => !one.image)) return f.swaps.length > 1 ? "Choose a picture for every person" : "Choose the character’s picture first";
  const who = f.swaps.map(swapWho), numbers = who.map(w => w.which).filter(Boolean);
  if (new Set(numbers).size < numbers.length) return "Two swaps name the same person: each person is swapped once";
  if (f.swaps.length > 1 && who.filter(w => !w.which && !w.person).length > 1) return "Say who each swap is for: a number from the numbered picture, or a few words";
  return ""; }
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
// (st.estimates is one person's; each further person adds a whole swap less st.once, the finding: app/swap.py estimate)
const swapMinutes = (f, st) => { const q = (st.estimates || {})[f.quality] || {}, n = Math.min((st.seconds || [5, 15])[1], Math.max(5, Math.round(Number(f.seconds))));
  const s = q[String(n)]; if (!s) return ""; const one = s - (f.anchor ? 0 : ((st.anchor_seconds || {})[f.quality] || 0));
  const t = one + ((f.swaps || []).length - 1) * (one - (st.once || 0)); return "about " + Math.max(1, Math.round(t / 60)) + " min"; };
const swapEnd = f => Math.round((Number(f.start) + Number(f.seconds)) * 100) / 100;
function swapPick(path) { const one = SWAP.f.swaps[SWAP.picking === false ? 0 : SWAP.picking]; if (one) one.image = path; SWAP.picking = false; render(); }
function swapPanel(o) { const f = SWAP.f, st = SWAP.state || {}, redraw = () => render();
  if (SWAP.for !== o.path) { SWAP.for = o.path; SWAP.length = null; f.start = 0; delete ACT.jobs.swapwho; }  // another clip: its people are not numbered yet
  const box = el("div", { class: "seg", id: "actpanel", style: "margin-top:12px" },
    el("div", { class: "seghead" }, el("b", {}, "Character Swap"), el("span", { class: "note", style: "margin:0" }, o.path.split("/").pop()), el("span", { style: "flex:1" }),
      el("button", { class: "small", onclick: () => { ACT.open = null; render(); } }, "Close")));
  if (!st.ready) { put(box, el("div", { class: "note", style: "color:var(--warn)" }, st.why || "Not available on this machine.")); return box; }
  const clip = media(o.path, { style: "width:260px;border-radius:var(--r2)" });
  if (clip.addEventListener) clip.addEventListener("loadedmetadata", () => { if (!SWAP.length && clip.duration) { SWAP.length = clip.duration; render(); } });
  const several = f.swaps.length > 1, room = f.swaps.length < (st.most_swaps || 4);
  const stills = (SWAP.lib || []).filter(m => m.kind === "image").slice(0, 24);
  // one person of the job: their picture, who they are in the clip, and words for the model
  const person = (one, i) => { const up = el("input", { type: "file", accept: ".png,.jpg,.jpeg,.webp", "aria-label": "Upload the character’s picture" + (several ? " for person " + (i + 1) : ""), onchange: async () => { if (!up.files[0]) return; const data = new FormData(); data.append("file", up.files[0]);
      try { const r = await (await fetch("/api/upload", { method: "POST", body: data })).json(); if (!r.path) throw new Error(r.error || "upload failed"); one.image = r.path; SWAP.picking = false; render(); } catch (e) { toast(e.message); } } });
    const pick = el("button", { class: "fit", onclick: () => { SWAP.picking = SWAP.picking === i ? false : i; if (SWAP.picking !== false) api("/api/library").then(list => { SWAP.lib = list; render(); }).catch(() => {}); render(); } }, SWAP.picking === i ? "Close the Library" : "Pick from the Library");
    return el("div", { class: several ? "seg" : "", style: several ? "margin-top:8px" : "" },
      several ? el("div", { class: "seghead" }, el("b", {}, "Person " + (i + 1)), el("span", { style: "flex:1" }),
        el("button", { class: "small", "aria-label": "Take person " + (i + 1) + " out of this job", onclick: () => { f.swaps.splice(i, 1); SWAP.picking = false; render(); } }, "Remove")) : null,
      field("The character", el("div", { class: "row" }, el("div", { class: "fit" }, up), pick, el("span"))),
      one.image ? el("div", { class: "row", style: "align-items:center" }, media(one.image, { style: "max-width:160px;border-radius:var(--r2)" }), el("span", { class: "note", style: "margin:0" }, "The character, outfit included, as the result should show them."))
        : el("div", { class: "note" }, "A picture of the character, head to toe if you can: face, hair and the whole outfit come from it."),
      SWAP.picking === i ? (stills.length ? el("div", { class: "grid", style: "margin-top:10px" }, stills.map(m => el("div", { class: "tile" + (one.image === m.path ? " on" : ""), style: "cursor:pointer", title: m.path, onclick: () => swapPick(m.path) }, media(m.path))))
        : el("div", { class: "note" }, SWAP.lib ? "No pictures in the Library yet: upload one." : "Reading the Library…")) : null,
      el("div", { class: "row" }, field(several ? "Who in the clip" : "Who is swapped (optional)", bind(one, "who", el("input", { maxlength: (st.person_most || 200), "aria-label": "Who is swapped" + (several ? ", person " + (i + 1) : ""),
          placeholder: several ? "a number from the numbered picture · or a few words for someone in the first frame" : "the largest person in the first frame · a number from the numbered picture · or a few words" }))),
        field("Direction (optional)", bind(one, "direction", el("input", { maxlength: (st.direction_most || 500), "aria-label": "Direction" + (several ? " for person " + (i + 1) : ""), placeholder: "she keeps her hands in her coat pockets · the character’s cape moves in the wind" }))))); };
  const most = SWAP.length ? Math.max(0, Math.floor((SWAP.length - Number(f.seconds)) * 10) / 10) : null;
  const start = el("input", { type: "number", min: 0, step: 0.1, value: f.start, style: "max-width:110px", "aria-label": "Where the part starts, in seconds", ...(most != null ? { max: most } : {}),
    oninput: e => { f.start = Math.max(0, Number(e.target.value) || 0); said.textContent = swapSaid(); } });
  const length = el("input", { type: "range", min: 5, max: (st.seconds || [5, 15])[1], step: 1, value: f.seconds, "aria-label": "How long the part is, 5 to " + (st.seconds || [5, 15])[1] + " seconds",
    oninput: e => { f.seconds = Number(e.target.value); said.textContent = swapSaid(); took.textContent = time(); } });
  const swapSaid = () => f.seconds + " s, from " + Number(f.start).toFixed(1) + " s to " + swapEnd(f).toFixed(1) + " s" + (swapWindows(f.seconds) > 1 ? " · " + swapWindows(f.seconds) + " windows" : " · one window");
  const said = el("b", {}, swapSaid());
  const time = () => { const m = swapMinutes(f, st); return m ? m + " on a DGX Spark" : ""; };
  const anchor = el("input", { type: "checkbox", checked: !!f.anchor, onchange: e => { f.anchor = e.target.checked; took.textContent = time(); } });
  const took = el("span", { class: "note", style: "margin:0" }, time());
  const numbering = () => { if (SWAP.length && swapEnd(f) > SWAP.length + 0.05) return toast("The clip is " + SWAP.length.toFixed(1) + " s long: start the part earlier or make it shorter");
    actQueue("swap", { video: o.path, start: Number(f.start) || 0, seconds: Number(f.seconds), quality: Number(f.quality), who: true }, "swapwho"); };
  put(box, el("div", { class: "row", style: "align-items:flex-start" }, el("div", { style: "flex:0 0 260px" }, clip),
    el("div", {}, el("div", { class: "note", style: "margin-top:0" }, "Replaces a person in a part of this clip with the character in one picture, or up to " + (st.most_swaps || 4) + " people one after another, each with a picture of their own. The scene, the performance and the clip’s own sound stay. SAM 3.1 finds the person (the largest in the part’s first frame, the one you describe there, or the one whose number you give, wherever in the part they come into view) and follows them; only that person is drawn again, the rest of each frame is the clip’s own. Best on one continuous shot."),
      el("div", { class: "row" }, field("Part starts at (s)", start), el("div", {}, el("label", {}, "Part length"), length, said)),
      field("Quality", el("div", { class: "row", style: "align-items:center" }, el("div", { class: "seg-ctl", role: "radiogroup", "aria-label": "Quality" }, [["480", "Fast", "480p"], ["768", "High", "768p, a little over twice as long"]].map(([v, t, s]) =>
        el("button", { type: "button", role: "radio", "aria-checked": f.quality === v ? "true" : "false", class: f.quality === v ? "on" : "", title: s, onclick: () => { f.quality = v; redraw(); } }, t + " · " + v + "p"))), took)),
      el("div", { class: "row", style: "align-items:center;margin-top:8px" }, el("button", { class: "fit", disabled: ACT.busy, title: "A job of its own: SAM 3.1 follows everyone through the part, also people who come into view later, and shows a number on each", onclick: numbering }, "Number the people"),
        el("span", { class: "note", style: "margin:0" }, "Follows everyone through the part (up to " + (st.most_people || 9) + " people, also those who come into view later) and shows a number on each. The numbers hold for this start, length and quality: number them again after changing any.")),
      actShow("swapwho", job => job.outputs[0] ? el("div", { style: "margin-top:8px" }, el("label", {}, "The people of the part, numbered"), el("img", { src: file(job.outputs[0]), alt: "The frame of the part with the most people in view, every person tinted and numbered", style: "width:100%;max-width:520px;border-radius:var(--r2)" })) : null),
      f.swaps.map(person),
      room ? el("div", { class: "row", style: "align-items:center;margin-top:8px" }, el("button", { class: "fit", onclick: () => { f.swaps.push(swapOne()); took.textContent = time(); render(); } }, "Add another person"),
        el("span", { class: "note", style: "margin:0" }, "Each further person is one more whole swap, made on the result of the one before. A person picked by number is drawn in a crop around them and pasted back: everything else stays the clip’s own pixels.")) : null,
      el("label", { class: "switch", style: "margin-top:8px" }, anchor, el("span", {}, "Put the character in the first frame first",
        el("small", {}, swapAnchorBy() + " draws the character into the part’s first frame, and the swap starts from that frame. Off: the swap starts from the clip’s own first frame."))),
      (st.ref_sizes || []).includes("max") ? el("label", { class: "switch", style: "margin-top:8px" },
        el("input", { type: "checkbox", checked: f.refFull == null ? st.ref_sizes[0] === "max" : !!f.refFull, onchange: e => { f.refFull = e.target.checked; } }),
        el("span", {}, "Use the character’s picture at full size", el("small", {}, "The model reads the picture at up to 2048 pixels on its short side, so a large reference sheet keeps its face. Slower: the picture’s rows ride through every pass. Off: it is scaled down to the part’s size."))) : null,
      (st.transformers || []).length ? field("Video model", el("select", { "aria-label": "Video model", onchange: e => { f.transformer = e.target.value; } },
        [["", "Regular · MiniMax H3 Ref2VA, this install’s own file"], ...st.transformers.map(n => [n, n.replace(/\.safetensors$/, "") + " · another build on this machine, not measured"])].map(([v, t]) => el("option", { value: v, selected: f.transformer === v }, t)))) : null,
      el("div", { class: "row" }, field("Seed", bind(f, "seed", el("input", { type: "number", min: 0, style: "max-width:120px" }))), el("span")),
      el("div", { class: "note" }, st.licence || ""),
      el("button", { class: "hot", disabled: ACT.busy, onclick: () => { const why = swapWhy(f); if (why) return toast(why);
        if (SWAP.length && swapEnd(f) > SWAP.length + 0.05) return toast("The clip is " + SWAP.length.toFixed(1) + " s long: start the part earlier or make it shorter");
        actQueue("swap", swapParams(o.path, f, st)); } }, several ? "Swap " + f.swaps.length + " people" : "Swap the character"))),
    actShow("swap", job => el("div", { class: "row", style: "align-items:flex-start;margin-top:8px" },
      el("div", {}, el("label", {}, "Swapped"), el("video", { class: "main", src: file(job.outputs[0]), controls: true })),
      job.outputs[1] ? el("div", {}, el("label", {}, (job.params && (job.params.swaps || []).length > 1) ? "Who was swapped, each in their own colour" : "Who was swapped, in green"), el("video", { class: "main", src: file(job.outputs[1]), controls: true, loop: true })) : null,
      job.outputs[2] ? el("div", {}, el("label", {}, "The first frame it started from"), el("img", { src: file(job.outputs[2]), style: "width:100%;border-radius:var(--r2)" })) : null)));
  return box; }
