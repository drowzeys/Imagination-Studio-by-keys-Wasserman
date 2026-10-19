"use strict";
// Sound → Sound effects (app/sound.py): foley made for a clip's picture (MMAudio) or a sound effect from words (Stable
// Audio Open), each a job of the studio's queue. What is made lands in sound/ and the Library's Sound effects.
const SFX = { state: null, takes: null, mode: "foley", clips: null, video: "", prompt: "", negative: "", seconds: 6, keep: true, db: -6, seed: 1, busy: false, timer: null };
const SFX_MODES = { foley: ["Foley for a clip", "MMAudio watches the clip and makes its sound: steps on the footfalls, the door when it shuts."],
  sfx: ["A sound effect", "Stable Audio Open makes a sound from words: up to 47 seconds, 44.1 kHz stereo."] };
async function sfxLoad() { try { SFX.state = await api("/api/sound/state"); } catch (e) { SFX.state = { error: e.message }; }
  try { SFX.takes = await api("/api/sound/takes"); } catch { SFX.takes = []; }
  if (SFX.clips === null) { try { SFX.clips = (await api("/api/library")).filter(m => m.kind === "video"); } catch { SFX.clips = []; } }
  if (tab === "Foley") render(); }
const sfxLive = () => (typeof jobs !== "undefined" ? jobs : []).some(j => (j.kind === "sfx" || j.kind === "foley") && ["queued", "running"].includes(j.status));
function sfxWatch() { if (SFX.timer) return; SFX.timer = setInterval(async () => { if (tab !== "Foley") { clearInterval(SFX.timer); SFX.timer = null; return; }
  const before = JSON.stringify((SFX.takes || []).map(t => t.id)); try { SFX.takes = await api("/api/sound/takes"); } catch {}
  if (JSON.stringify((SFX.takes || []).map(t => t.id)) !== before) render(); if (!sfxLive()) { clearInterval(SFX.timer); SFX.timer = null; } }, 5000);
  if (SFX.timer && SFX.timer.unref) SFX.timer.unref(); }
async function sfxMake() { const m = SFX.mode;
  if (m === "foley" && !SFX.video) return toast("Pick the clip to make its sound");
  if (m === "sfx" && !SFX.prompt.trim()) return toast("Say what is heard first");
  const params = m === "foley" ? { video: SFX.video, prompt: SFX.prompt.trim(), negative: SFX.negative.trim(), keep_sound: SFX.keep, foley_db: Number(SFX.db), seed: Number(SFX.seed) || 1 }
    : { prompt: SFX.prompt.trim(), negative: SFX.negative.trim(), seconds: Number(SFX.seconds), seed: Number(SFX.seed) || 1 };
  SFX.busy = true; render();
  try { const job = await api("/api/jobs", { kind: m, params }); toast("Queued: " + (job.title || SFX_MODES[m][0])); if (typeof poll === "function") await poll(); sfxWatch(); }
  catch (e) { toast(e.message, 7000); }
  SFX.busy = false; render(); }
function sfxTake(t) { const clip = t.clip && t.kind === "foley";
  return el("div", { class: "tile lib-sound", "data-take": t.id },
    el("div", { class: "lib-sound-top" }, el("span", { class: "eyebrow" }, t.kind === "foley" ? "Foley" : "Sound effect"),
      el("b", { title: t.prompt || "" }, t.prompt || (t.source ? "For " + t.source.split("/").pop() : t.id))),
    clip ? media(t.clip, { controls: true, preload: "metadata" }) : media(t.path),
    el("div", { class: "cap" }, el("span", { class: "note m-flat" }, (t.seconds ? Number(t.seconds).toFixed(1) + " s · " : "") + "seed " + t.seed + (t.elapsed ? " · made in " + Math.round(t.elapsed) + " s" : ""))),
    el("div", { class: "cap", style: "padding-top:0;justify-content:flex-end;flex-wrap:wrap" },
      clip ? el("button", { class: "small", onclick: () => addToTimeline(t.clip) }, "+ timeline") : null,
      el("button", { class: "small", title: "Use this sound as a reference (<Audio N>) for a new clip", onclick: () => { C.goes = "video"; C.start = "refs"; C.step = 1;
        if (!C.refAudios.includes(t.path)) { C.refAudios.push(t.path); C.audio_notes.push(""); } go("Create"); toast("Added as reference sound"); } }, "reference sound"),
      el("button", { class: "small", onclick: () => { SFX.seed = (Number(t.seed) || 1) + 1; SFX.prompt = t.prompt || ""; SFX.mode = t.kind; if (t.kind === "foley" && t.source) SFX.video = t.source; render(); } }, "again"))); }
function viewFoley() {
  if (!SFX.state) sfxLoad(); if (sfxLive()) sfxWatch();
  const head = typeof pageHead === "function" ? pageHead("Sound", "Sound effects", "Foley for a clip, or a sound effect from words. Made on this machine.") : el("h1", { class: "display" }, "Sound effects");
  if (!SFX.state) return el("div", {}, head, el("div", { class: "note" }, "Looking…"));
  if (SFX.state.error) return el("div", {}, head, el("div", { class: "note" }, SFX.state.error));
  const k = SFX.state.kinds[SFX.mode], m = SFX.mode;
  const area = (key, rows, placeholder) => { const n = el("textarea", { rows, placeholder, "aria-label": placeholder }); n.value = SFX[key]; n.addEventListener("input", () => { SFX[key] = n.value; }); return n; };
  const num = (key, attrs) => el("input", { type: "number", value: SFX[key], ...attrs, oninput: e => { SFX[key] = e.target.value; } });
  const clips = SFX.clips || [];
  const form = m === "foley" ? [
      field("The clip", el("select", { "aria-label": "The clip", onchange: e => { SFX.video = e.target.value; render(); } }, el("option", { value: "" }, clips.length ? "Choose a clip from the Library…" : "No clips in the Library yet"),
        clips.slice(0, 200).map(c => el("option", { value: c.path, selected: SFX.video === c.path }, c.path.split("/").slice(-2).join("/"))))),
      SFX.video ? el("video", { class: "main", src: file(SFX.video), controls: true, preload: "metadata", style: "max-height:32vh" }) : null,
      field("What is heard (optional: MMAudio hears the picture by itself)", area("prompt", 2, "footsteps on wet gravel, a car door shuts")),
      el("div", { class: "chips" }, ...[[true, "Under the clip's own sound"], [false, "In place of it"]].map(([v, label]) => el("button", { type: "button", class: "small" + (SFX.keep === v ? " on" : ""), onclick: () => { SFX.keep = v; render(); } }, label))),
      SFX.keep ? field("Foley level under the clip's sound (dB)", num("db", { min: -30, max: 0, step: 1 })) : null]
    : [field("What is heard", area("prompt", 3, "a heavy wooden door slams shut in a stone hall, short reverb")),
      field("Seconds (1 to 47)", num("seconds", { min: 1, max: 47, step: 0.5 }))];
  return el("div", { class: "sfx" }, head,
    el("div", { class: "sfx-cards", role: "radiogroup", "aria-label": "What to make" }, Object.entries(SFX_MODES).map(([id, [name, one]]) => {
      const ready = SFX.state.kinds[id].ready;
      return el("button", { type: "button", class: "sfx-card" + (m === id ? " on" : ""), role: "radio", "aria-checked": m === id ? "true" : "false", onclick: () => { SFX.mode = id; render(); } },
        el("b", {}, name), el("span", { class: "sfx-state" }, el("span", { class: "dotl" + (ready ? (m === id ? " t" : "") : " o") }), ready ? "Ready" : "Not installed"),
        el("span", { class: "sfx-one" }, one)); })),
    el("section", { class: "panel stack", style: "margin-top:14px" },
      !k.ready ? el("div", { class: "note vt-bad" }, k.why) : null,
      !k.ready && !SFX.state.spark ? el("div", { class: "signin-cmd" }, el("code", {}, "bash scripts/setup-sound.sh"), el("button", { type: "button", class: "small", onclick: () => copyText("bash scripts/setup-sound.sh", "Command") }, "Copy")) : null,
      ...form,
      el("details", { class: "fold" }, el("summary", {}, "More: what is not heard, the seed"),
        field("What is not heard", area("negative", 2, m === "foley" ? "music, speech" : "low quality, distortion")), field("Seed", num("seed", { min: 1, step: 1 }))),
      el("div", { class: "row" }, el("button", { class: "primary fit", disabled: SFX.busy || !k.ready, onclick: sfxMake }, SFX.busy ? "Queuing…" : m === "foley" ? "Make the foley" : "Make the sound"),
        el("span", { class: "note m-flat" }, k.licence))),
    el("div", { class: "sectionhead" }, el("h2", {}, "Made here"), el("button", { class: "small", onclick: () => { if (typeof LIBF !== "undefined") LIBF.kind = "sfx"; go("Library"); } }, "In the Library")),
    !SFX.takes ? el("div", { class: "note" }, "Loading…") : !SFX.takes.length ? el("div", { class: "note" }, "Nothing yet: your sound effects and foley line up here.")
      : el("div", { class: "grid" }, SFX.takes.slice(0, 24).map(sfxTake)));
}
