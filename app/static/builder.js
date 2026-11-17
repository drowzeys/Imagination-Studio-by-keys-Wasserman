"use strict";
// Prompt Builder (app/prompt_builder.py): a MiniMax H3 prompt from your own cast, a scenario, a place, a film look
// and an idea, written by the Studio's brain or laid out from the choices alone, then sent into Create: the scene,
// action, camera and story beats to step 1, the lines, voices, soundscape and music to step 3, and the length (over
// 15 s, the split into chained clips joined into one video).
const PB = { opts: null, cast: null, pick: [], scenario: "", location: "", time: "", mood: "", look: "", shots: "auto",
  seconds: 10, language: "English", idea: "", prompt: "", busy: false, by: "", edit: null, said: "" };

async function pbLoad() { [PB.opts, PB.cast] = await Promise.all([api("/api/builder/options"), api("/api/builder/cast")]);
  if (PB.cast.some(c => c.sheet && ["still", "turn"].includes(c.sheet.state))) pbWatch(); render(); }
const pbSave = async () => { try { PB.cast = await api("/api/builder/cast", PB.cast, "PUT"); } catch (e) { toast(e.message); } };
const pbLooks = () => ({ "": "No particular look", ...PB.opts.looks });
// a drop-down that also takes a typed value: the list's choices, or your own words
function pbPick(key, label, list, placeholder) {
  const id = "pb-" + key, input = bind(PB, key, el("input", { list: id, placeholder }));
  return field(label, el("div", {}, input, el("datalist", { id }, list.map(v => el("option", { value: v })))));
}

function pbCharacter(one, i) {
  const on = PB.pick.includes(one.id);
  if (PB.edit === one.id) {
    const done = async () => { PB.edit = null; await pbSave(); render(); };
    return el("div", { class: "seg" },
      el("div", { class: "row" }, field("Name", bind(one, "name", el("input"))), field("Their era's look (optional)", select(one, "era", pbLooks()))),
      field("Look", bind(one, "look", el("textarea", { style: "min-height:48px", placeholder: "age, build, face, hair: what the camera sees" }))),
      field("Wardrobe", bind(one, "wardrobe", el("input", { placeholder: "what they wear and carry" }))),
      field("Voice", bind(one, "voice", el("input", { placeholder: "a warm, rough drawl" }))),
      el("div", { class: "row" }, el("button", { class: "fit primary", onclick: done }, "Done"),
        el("button", { class: "fit", onclick: async () => { PB.cast.splice(i, 1); PB.pick = PB.pick.filter(p => p !== one.id); PB.edit = null; await pbSave(); render(); } }, "Delete"), el("span")));
  }
  return el("div", { class: "tile" + (on ? " on" : ""), style: "padding:10px;cursor:pointer", title: "Click to put them in the scene",
      onclick: () => { PB.pick = on ? PB.pick.filter(p => p !== one.id) : [...PB.pick, one.id]; render(); } },
    el("b", {}, (on ? "✓ " : "") + one.name), el("div", { class: "note" }, one.look),
    el("div", { class: "note" }, "Voice: " + (one.voice || "not set") + (one.era && PB.opts.looks[one.era] ? " · " + one.era : "")),
    pbSheet(one),
    el("div", { class: "row", style: "margin-top:6px" }, el("button", { class: "small fit", onclick: e => { e.stopPropagation(); PB.edit = one.id; render(); } }, "Edit"),
      el("button", { class: "small fit", disabled: ["still", "turn"].includes((one.sheet || {}).state), title: "A full-body still from their look and wardrobe (Qwen Image), then a turnaround of it (H3 Ref2VA with the Turnaround LoRA), both in the queue",
        onclick: e => { e.stopPropagation(); pbMakeSheet(one); } }, one.sheet && one.sheet.state === "done" ? "Make the sheet again" : "Make a reference sheet"), el("span")));
}

// a character's reference sheet (prompt_builder.make_sheet): where its making stands, then the sheet itself
function pbSheet(one) {
  const sh = one.sheet; if (!sh || !sh.state) return null;
  if (sh.state === "still") return el("div", { class: "note" }, "Reference sheet: making the full-body still…");
  if (sh.state === "turn") return el("div", { class: "note" }, "Reference sheet: turning the still round…");
  if (sh.state === "failed") return el("div", { class: "note", style: "color:var(--warn)" }, "Reference sheet failed: " + (sh.error || "no reason given"));
  return el("div", { style: "margin-top:6px" }, sh.sheet ? el("a", { href: file(sh.sheet), target: "_blank", onclick: e => e.stopPropagation() },
    el("img", { src: file(sh.sheet), alt: one.name + " reference sheet", style: "width:100%;border-radius:6px;background:#fff" })) : null,
    sh.still ? el("a", { class: "note", href: file(sh.still), target: "_blank", onclick: e => e.stopPropagation() }, "the full-body still") : null);
}
async function pbMakeSheet(one) {
  try { await pbSave(); const who = PB.cast.find(c => c.name === one.name) || one; await api("/api/builder/cast/" + encodeURIComponent(who.id) + "/sheet", {});
    toast("Queued: a full-body still of " + one.name + ", then its turnaround. They appear here as they land.");
    PB.cast = await api("/api/builder/cast"); render(); pbWatch(); } catch (e) { toast(e.message); } }
// while a sheet is being made, the cast is read again now and then so its card moves on by itself
let PB_WATCH = null;
function pbWatch() { if (PB_WATCH) return; PB_WATCH = setInterval(async () => {
  try { PB.cast = await api("/api/builder/cast"); } catch { return; }
  if (!PB.cast.some(c => c.sheet && ["still", "turn"].includes(c.sheet.state))) { clearInterval(PB_WATCH); PB_WATCH = null; }
  if (tab === "Prompt Builder" && !PB.edit) render(); }, 6000); }

function pbCastPanel() {
  const file = el("input", { type: "file", accept: ".json,application/json", onchange: async () => { const f = file.files[0]; file.value = ""; if (!f) return;
    try { const list = JSON.parse(await f.text()); if (!Array.isArray(list)) throw new Error("that file is not a cast list");
      const have = new Set(PB.cast.map(c => c.name.toLowerCase())), add = list.filter(c => c && c.name && !have.has(String(c.name).toLowerCase())).map(c => ({ ...c, id: "" }));
      PB.cast = PB.cast.concat(add); await pbSave(); render(); toast(add.length + " character" + (add.length === 1 ? "" : "s") + " added" + (list.length > add.length ? ", " + (list.length - add.length) + " already here" : ""));
    } catch (e) { toast(e.message); } } });
  const exportCast = () => { const a = el("a", { href: URL.createObjectURL(new Blob([JSON.stringify(PB.cast, null, 1)], { type: "application/json" })), download: "cast.json" }); a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000); };
  const many = PB.pick.length > PB.opts.ensemble_warn;
  return el("div", {}, el("h3", {}, "Cast · " + PB.pick.length + " in this scene"),
    el("div", { class: "note" }, "Your own characters, kept in the studio folder. Click one to put them in the scene; the scene keeps them in the order you click."),
    el("div", { class: "grid", style: "margin-top:8px" }, PB.cast.map(pbCharacter)),
    many ? el("div", { class: "note", style: "color:var(--warn)" }, "MiniMax H3 keeps a few people apart in one shot; with more than " + PB.opts.ensemble_warn + " they tend to blur together. Give each shot two or three of them.") : null,
    el("div", { class: "row", style: "margin-top:8px" },
      el("button", { class: "fit", onclick: async () => { const one = { id: "", name: "New character", look: "", wardrobe: "", voice: "", era: "" }; PB.cast.push(one); await pbSave(); PB.edit = PB.cast.at(-1).id; render(); } }, "+ New character"),
      el("button", { class: "fit", onclick: exportCast }, "Export cast"), field("Import a cast (.json)", file), el("span")));
}

async function pbWrite(brain) {
  if (PB.busy) return; PB.busy = true; PB.said = brain ? "The brain is writing the prompt…" : ""; render();
  try {
    const r = await api("/api/builder/write", { brain, cast: PB.pick, scenario: PB.scenario, location: PB.location, time: PB.time, mood: PB.mood,
      look: PB.look, shots: PB.shots, seconds: Number(PB.seconds), language: PB.language, idea: PB.idea });
    PB.prompt = r.prompt; PB.by = r.by;
    PB.said = r.error ? "The brain did not write it (" + r.error + "), so here is the layout from your choices to fill in. Set a brain under Machine → Brains & tools." : r.by === "brain" ? "Written by the brain in " + Math.round(r.elapsedMs / 1000) + " s. Edit it freely." : "The layout from your choices: write the lines and what happens.";
  } catch (e) { PB.said = e.message; }
  PB.busy = false; render();
}

// into Create: a new video clip from text, its boxes replaced by the prompt's
async function pbSend() {
  let r; try { r = await api("/api/builder/fields", { prompt: PB.prompt }); } catch (e) { return toast(e.message); }
  const f = r.fields, found = r.found;
  // a line spoken by someone in the cast keeps their saved voice, so H3 hears the same person in every line and clip;
  // the line's own note ("flat", "grinning") stays after it
  const byName = Object.fromEntries(PB.cast.map(c => [c.name.trim().toLowerCase(), c]));
  for (const d of found.dialogue) { const who = byName[(d.speaker || "").trim().toLowerCase()]; if (!who || !who.voice) continue;
    // only the words the note adds: "flat and gravelly" beside a gravelly voice is "flat"
    const has = new Set(who.voice.toLowerCase().match(/[a-z'-]+/g) || []), filler = new Set(["a", "an", "the", "and", "with", "voice", "in", "speaking", "of"]);
    const adds = ((d.voice || "").replace(/^a voice speaking\s+/i, "").toLowerCase().match(/[a-z'-]+/g) || []).filter(w => !filler.has(w) && !has.has(w));
    d.voice = who.voice + (adds.length ? ", " + adds.join(" and ") : ""); }
  Object.assign(C, { goes: "video", start: C.start === "refs" ? "text" : C.start, scene: f.scene || "", action: f.action || "", camera: f.camera || "",
    dialogue: found.dialogue, soundscape: found.soundscape || "", music: found.music || "N/A", prompt: "", parts: null, job: null });
  const want = found.seconds || Number(PB.seconds);
  C.seconds = fitLength(want); C.frames = null; sync();
  C.beats = isLong() ? (f.beats || []).join("\n") : "";
  C.step = 2; go("Create");
  toast(found.dialogue.length + " line" + (found.dialogue.length === 1 ? "" : "s") + " and the scene sent to Create · " + (S.long[C.seconds] || C.seconds + " s") + (isLong() ? ", " + Math.ceil(C.seconds / 15) + " clips joined into one video" : "") + ": check the voices, then step 4 and Render");
}

function viewBuilder() {
  const box = el("section", { class: "card" }, el("h2", {}, "Prompt Builder"),
    el("div", { class: "note" }, "A MiniMax H3 prompt from your own characters and a few choices, written by the Studio's brain, then sent into Create (step 3, Words / Sound) to render as one video. Over 15 s it renders as chained clips joined into one file."));
  if (!PB.opts || !PB.cast) { pbLoad().catch(e => toast(e.message)); return put(box, el("div", { class: "note" }, "Loading…")), box; }
  const lengths = Object.fromEntries([...Object.keys(S.durations).map(Number).filter(n => n >= 5), ...Object.keys(S.long || {}).map(Number).filter(n => n <= 120)].map(n => [n, S.long[n] || n + " s"]));
  const area = bind(PB, "prompt", el("textarea", { style: "min-height:320px;font-family:ui-monospace,monospace;font-size:13.5px", placeholder: "The prompt appears here. You can also paste or write one yourself." }));
  put(box, pbCastPanel(),
    el("h3", {}, "The scene"),
    el("div", { class: "row" }, pbPick("scenario", "Scenario", PB.opts.scenarios, "pick one or write your own"), pbPick("location", "Place", PB.opts.locations, "pick one or write your own")),
    el("div", { class: "row" }, pbPick("time", "Time of day", PB.opts.times, "night"), pbPick("mood", "Mood", PB.opts.moods, "tense"),
      field("Film look", select(PB, "look", pbLooks()))),
    el("div", { class: "row" }, field("Length", select(PB, "seconds", lengths)), field("Shots", select(PB, "shots", PB.opts.shots)),
      field("Language of the lines", bind(PB, "language", el("input", { placeholder: "English" })))),
    field("What happens (a sentence or two; the brain writes the shots and lines)", bind(PB, "idea", el("textarea", { style: "min-height:60px", placeholder: "They meet at the diner to split the money, and it goes wrong when one of them counts it twice." }))),
    el("div", { class: "row", style: "margin-top:8px" },
      el("button", { class: "hot fit", disabled: PB.busy, onclick: () => pbWrite(true) }, PB.busy ? "Writing…" : "Write with the brain"),
      el("button", { class: "fit", disabled: PB.busy, onclick: () => pbWrite(false) }, "Lay it out without a brain"), el("span")),
    PB.said ? el("div", { class: "note", style: "margin-top:6px" }, PB.said) : null,
    el("h3", {}, "The prompt"), area,
    el("div", { class: "row", style: "margin-top:8px" },
      el("button", { class: "primary fit", disabled: !PB.prompt.trim(), onclick: pbSend }, "Send to 3. Words / Sound"), el("span")));
  return box;
}
