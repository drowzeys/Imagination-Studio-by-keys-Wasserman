"use strict";
/* Voice · Fish Audio Drama 3, a tab of its own (app/voice.py). Built with Fish Audio.
   Text to speech on this machine with TensorFold's port of Fish Audio Drama 3: speaker tags and [directions] in the
   text, an optional voice to clone (with the speaker's consent), one take per job in the render queue. A take can go to
   the create wizard as reference audio (<Audio N>) for MiniMax H3 Ref2VA. Nothing is sent to Fish Audio. */
const VO = { takes: null, text: "", ref: null, refText: "", consent: false, seed: 1, temperature: 1.0, top_p: 0.9, top_k: 30, job: null, busy: false, cutting: "", player: null, stream: true, view: "lines", names: {} };
// one turn a line: on one line Drama 3 drops later turns far more often (the app also breaks the lines itself, voice.py dialogue)
const VO_EXAMPLE = "<|speaker:0|> [whispering] Did you hear that noise downstairs?\n<|speaker:1|> [sighs] It's probably just the cat. Go back to sleep.\n<|speaker:0|> Cats don't knock on doors.";
const voOn = () => !!(S && S.voice && S.voice.available);
const voActive = () => VO.job && ["queued", "running"].includes(VO.job.status);
const voBounded = () => !!(S.voice?.qualification_required && S.voice?.qualified && S.voice?.verified_recipe);
// the CUDA engine's take plays while it is made (GET /api/voice/takes/{id}/live): one player a take, kept across redraws
function voLive(job) {
  if (!(S.voice && S.voice.live) || !job || job.status !== "running" || (job.params && job.params.live === false)) return null;
  if (!VO.player || VO.player.id !== job.id) VO.player = { id: job.id, el: el("audio", { src: "/api/voice/takes/" + encodeURIComponent(job.id) + "/live", controls: true, autoplay: true, preload: "auto", style: "width:100%;margin-top:8px" }) };
  return el("div", {}, el("div", { class: "note", style: "margin:8px 0 0" }, "Playing while it is made:"), VO.player.el); }
const voSpeed = st => st.backend === "cuda"
  ? "One take at a time in the queue, never beside a video render (Drama 3 takes about 42 GB). About 18 s to load the first time; it stays loaded for " + Math.round(((st.server || {}).idle_unload_s || 300) / 60) + " minutes after a take and is unloaded at once when a video or picture job starts. Then about 0.7 s for each second of speech, the first sound after about 0.3 s, played as it comes; Whisper checks the words after."
  : st.serves ? "One take at a time in the queue, never beside a video render (Drama 3 takes about 50 GB with its word check). About 5 s to load the first time; it stays loaded for " + Math.round(((st.server || {}).idle_unload_s || 300) / 60) + " minutes after a take and is unloaded at once when a video or picture job starts. Then about 0.35 s for each second of speech, the first sound after about 0.2 s, played as it comes. The engine checks its own words with Whisper as it goes: a runaway tail is cut, and a part with missing words is drawn again (a take that was short while playing is made once more)."
  : st.checks ? "One take at a time in the queue, never beside a video render (Drama 3 takes about 50 GB). About 4 s to load, then about 0.35 s for each second of speech, the first sound after about 0.25 s when it plays as it is made. The engine checks its own words with Whisper as it goes: a runaway tail is cut, and a part with missing words is drawn again (when it is not played as it is made) or reported."
  : "One take at a time in the queue, never beside a video render (Drama 3 takes about 50 GB). About 3 s to load, then roughly 0.4 s for each second of speech; Whisper checks the words after.";
// the MLX engine's own end-of-text check, part by part (take.verify): what it did and what it did not hear
function voCheck(t) {
  if (!t.verify || !t.verify.length) return null;
  const n = a => t.verify.filter(v => v.action === a).length, redrawn = t.verify.reduce((x, v) => x + Math.max(0, (v.attempts || 1) - 1), 0);
  const missing = t.verify.flatMap(v => v.missing || []), late = t.verify.filter(v => v.late_s).length;
  return (t.asr && t.asr.by === "whisper" ? "word check: " : "engine check: ") + [n("ok") ? n("ok") + " ok" : null, n("trim") ? n("trim") + " trimmed" : null, redrawn ? redrawn + " redrawn" : null,
    n("redraw") ? n("redraw") + " still short" : null, late ? late + " cut after playing" : null].filter(Boolean).join(", ") + (t.verify.length > 1 ? " of " + t.verify.length + " parts" : "")
    + (missing.length ? " · not heard: " + missing.slice(0, 8).join(", ") + (missing.length > 8 ? "…" : "") : ""); }
async function voLoad() { try { VO.takes = await api("/api/voice/takes"); } catch { VO.takes = []; } if (tab === "Voice") render(); }
const voLine = t => [t.duration != null ? t.duration.toFixed(1) + " s" : null, t.frames_per_s ? t.frames_per_s + " frames/s" : null, t.rtf ? "made in " + t.rtf + "× its length" : null,
  t.first_audio_ms ? "first sound after " + (t.first_audio_ms / 1000).toFixed(2) + " s" : null, t.load_s ? "model loaded in " + Math.round(t.load_s) + " s" : null,
  t.asr && t.asr.score != null ? "Whisper" + (t.asr.by === "engine" ? " (engine)" : "") + " " + t.asr.score + " % (" + t.asr.found + " of " + t.asr.words + " words)" : t.asr && t.asr.skipped ? "no word check: " + t.asr.skipped : null,
  "seed " + t.seed, t.reference ? "cloned voice" : null, voCheck(t), t.retaken ? "made again without playing: a part was short while it played" : null].filter(Boolean).join(" · ");
// a take (or any sound file of the studio) as a reference audio clip of a MiniMax H3 Ref2VA clip: 2 to 15 s each
async function voUseAsRef(path, seconds) {
  if (!S.ref_media) return toast("Reference audio needs MiniMax H3 Ref2VA, which is not installed here (" + (S.platform === "spark" ? "oneshot-setup-spark.sh" : "oneshot-setup.sh") + " --ref2va).");
  let d = Number(seconds) || 0;
  if (d > 15.05) { if (!confirm("This take is " + d.toFixed(1) + " s and a reference audio clip is 2 to 15 seconds.\nUse its first 15 seconds?")) return;
    try { VO.cutting = path; render(); const r = await api("/api/voice/cut", { path, seconds: 15 }); path = r.path; d = r.duration || 15; } catch (e) { return toast(e.message); } finally { VO.cutting = ""; } }
  if (d && d < 1.95) return toast("This take is " + d.toFixed(1) + " s: a reference audio clip is at least 2 seconds.");
  const meta = await refMeta(path), why = refFull("audios", C.refAudios) || refWrong("audios", C.refAudios, meta);
  if (C.refAudios.includes(path)) return toast("It is a reference already");
  if (why) return toast("Not added: " + why);
  Object.assign(C, { goes: "video", start: "refs", step: 1, job: null }); C.refAudios.push(path); C.audio_notes.push(""); C.prompt = "";
  sync(); tab = "Create"; history.replaceState(null, "", "#Create"); render();
  toast("Added as " + audioName(C.refAudios.length - 1) + (C.refs.length || C.refVideos.length ? "" : ": add a picture or a video of who speaks, audio cannot be the only reference") + ". Describe the same voice in the line's Voice box.", 6000); }
async function voRender() {
  if (!VO.text.trim()) return toast("Write what is said first");
  if (VO.ref && !VO.consent) return toast("Tick the consent box to clone this voice, or take the clip out");
  // the queue takes a moment to answer: a second click before it does must not make a second take
  if (VO.busy) return; VO.busy = true; render();
  try { VO.job = await api("/api/voice/takes", { text: VO.text, seed: VO.seed, temperature: VO.temperature, top_p: VO.top_p, top_k: VO.top_k,
    live: voBounded() ? false : VO.stream, ...(voBounded() ? { asr: false } : {}),
    ...(VO.ref ? { ref_audio: VO.ref, ref_text: VO.refText, consent: VO.consent } : {}) }); } catch (e) { VO.busy = false; return toast(e.message); }
  VO.busy = false; await poll(); render(); }
function voTake(t) {
  return el("div", { class: "vo-take" },
    el("div", { class: "vo-take-top" }, el("b", {}, t.title || "Voice take"), el("span", { class: "vo-when" }, new Date(t.created * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })),
      t.asr && t.asr.score != null ? el("span", { class: "vo-score" + (t.asr.score >= 98 ? " ok" : ""), title: t.asr.heard ? "Whisper heard: " + t.asr.heard : "" }, t.asr.found + " of " + t.asr.words + " words") : null),
    el("audio", { src: file(t.path), controls: true, preload: "metadata" }),
    el("div", { class: "note m-flat vo-meta", title: t.asr && t.asr.heard ? "Whisper heard: " + t.asr.heard : "" }, voLine(t)),
    el("div", { class: "vo-text" }, t.text.length > 300 ? t.text.slice(0, 300) + "…" : t.text),
    el("div", { class: "chips" },
      el("a", { href: "/api/voice/takes/" + encodeURIComponent(t.id) + "/audio", download: "drama3-" + t.id + ".wav", class: "vo-dl" }, "Download WAV"),
      el("button", { class: "small", disabled: VO.cutting === t.path, title: "Opens Create with this take as a reference audio clip (2 to 15 s; a longer take is cut to its first 15 s)", onclick: () => voUseAsRef(t.path, t.duration) }, VO.cutting === t.path ? "Cutting…" : "Use as Ref2VA voice reference"),
      el("button", { class: "small", title: "Clone this take's voice for the next take: the same speaker across lines", disabled: !voOn() || (t.duration || 0) < 2, onclick: () => { VO.ref = t.path; VO.refText = t.reference ? "" : t.text.replace(/<\|speaker:\d+\|>|\[[^\]]*\]/g, " ").replace(/\s+/g, " ").trim(); VO.consent = false; render(); toast("Tick the consent box to use this voice"); } }, "Clone this voice"),
      el("button", { class: "small", title: "Put this take's text and settings back in the form", onclick: () => { Object.assign(VO, { text: t.text, seed: t.seed + 1, temperature: t.temperature, top_p: t.top_p, top_k: t.top_k }); window.scrollTo(0, 0); render(); } }, "Again, next seed"))); }
// the script as lines, one turn each, and back: "<|speaker:N|> words" is what Drama 3 reads (text without a tag is speaker 0)
function voParse(text) { const out = [];
  for (const raw of String(text || "").split("\n")) { if (!raw.trim()) continue; const m = raw.match(/^\s*<\|speaker:(\d+)\|>\s*(.*)$/);
    out.push(m ? { sp: Number(m[1]), text: m[2] } : { sp: out.length ? out[out.length - 1].sp : 0, text: raw.trim(), bare: true }); }
  return out; }
function voJoin(lines) { const many = lines.some(l => l.sp !== 0);
  return lines.filter(l => l.text.trim() || l.keep).map(l => (many || !l.bare ? "<|speaker:" + l.sp + "|> " : "") + l.text.trim()).join("\n"); }
const voName = sp => (VO.names[sp] || "").trim() || "Speaker " + (sp + 1);
const voWords = t => String(t || "").replace(/<\|speaker:\d+\|>|\[[^\]]*\]/g, " ").split(/\s+/).filter(Boolean).length;
// the line editor: a speaker per line (a select over the names given on the right) and its words
function voLines(st) { const lines = voParse(VO.text); if (!lines.length) lines.push({ sp: 0, text: "", keep: true });
  const save = () => { VO.text = voJoin(lines); if (typeof voCount === "function") voCount(); };
  const speakers = Math.max(2, ...lines.map(l => l.sp + 1)), opts = Array.from({ length: Math.min(16, speakers + 1) }, (_, k) => k);
  return el("div", { class: "vo-lines" }, lines.map((l, i) => el("div", { class: "vo-line" },
    el("select", { class: "vo-who vo-sp" + (l.sp % 4), "aria-label": "Who says line " + (i + 1), disabled: !st.available, onchange: e => { l.sp = Number(e.target.value); l.bare = false; save(); render(); } },
      opts.map(k => el("option", { value: k, selected: k === l.sp }, voName(k)))),
    (() => { const t = el("textarea", { rows: 1, class: "vo-said", "aria-label": "Line " + (i + 1), disabled: !st.available, placeholder: i ? "The next line" : "What is said. [whispering], [sighs] or a description in brackets steers the delivery." });
      t.value = l.text; t.addEventListener("input", () => { l.text = t.value; save(); }); return t; })(),
    el("button", { class: "small ghost vo-x", type: "button", "aria-label": "Remove line " + (i + 1), disabled: !st.available || lines.length < 2, onclick: () => { lines.splice(i, 1); save(); render(); } }, "×"))),
    el("div", { class: "vo-add" }, el("button", { class: "small", type: "button", disabled: !st.available, onclick: () => { const last = lines[lines.length - 1]; lines.push({ sp: lines.length > 1 ? lines[lines.length - 2].sp : (last.sp ? 0 : 1), text: "", keep: true, bare: false }); VO.text = voJoin(lines); render(); } }, "+ Add a line"),
      el("span", { class: "note m-flat" }, "One turn per line: on one line Drama 3 often drops the later turns."))); }
function viewVoice() {
  if (VO.takes === null) { VO.takes = []; voLoad(); }
  const st = S.voice || { available: false, why: "This app does not know Drama 3.", defaults: {} };
  const bounded = voBounded();  // a verified recipe only (short plain speech, one speaker): no lines, cast or cloning
  if (VO.job) { const fresh = jobs.find(j => j.id === VO.job.id); if (fresh) { if (fresh.status === "done" && VO.job.status !== "done") voLoad(); VO.job = fresh; } }
  const count = el("span", { class: "note m-flat" });
  globalThis.voCount = () => { const w = voWords(VO.text), n = voParse(VO.text).filter(l => l.text.trim()).length; count.textContent = n ? n + (n === 1 ? " line · " : " lines · ") + w + " words · about " + Math.max(1, Math.round(w / 2.6)) + " s of speech" : "Nothing written yet"; };
  voCount();
  const raw = el("textarea", { class: "vo-raw", disabled: !st.available, maxlength: bounded ? 120 : st.text_limit, placeholder: bounded ? "A short plain sentence, one speaker." : "What is said. <|speaker:0|> … <|speaker:15|> switch voices; [whispering], [sighs], [laughs] or a description in brackets steer the delivery." });
  bind(VO, "text", raw, () => voCount());
  const script = el("section", { class: "vo-script", "data-clear": "voice" },
    el("div", { class: "vo-scripthead" }, el("b", {}, "Script"),
      bounded ? null : el("div", { class: "seg-ctl", role: "group", "aria-label": "Edit as" }, [["lines", "Lines"], ["text", "Text"]].map(([k, t]) => el("button", { type: "button", class: VO.view === k ? "on" : "", "aria-pressed": VO.view === k ? "true" : "false", onclick: () => { VO.view = k; render(); } }, t))),
      el("span", { class: "su-grow" }),
      bounded ? null : el("a", { href: "#", onclick: e => { e.preventDefault(); VO.text = VO_EXAMPLE; render(); } }, "Two-speaker example"), ...clearButton("voice")),
    bounded ? [raw, el("div", { class: "note" }, "Verified: short plain speech, up to 20 words / 120 characters. Cloning, multiple speakers, streaming and transcription are not checked.")]
    : VO.view === "text" ? [raw, el("div", { class: "note" }, "Speaker tags ", el("code", {}, "<|speaker:0|>"), " to ", el("code", {}, "<|speaker:15|>"), " switch voices (text without one is speaker 0). Long text is split at sentence ends and keeps one voice.")] : voLines(st));
  const go = el("div", { class: "vo-go" }, count, el("span", { class: "su-grow" }),
    st.available && st.checks && !bounded ? el("label", { class: "check m-flat", title: "Sent audio cannot be taken back: played as it is made, a part with missing words is reported, not drawn again" },
      el("input", { type: "checkbox", checked: VO.stream, onchange: e => { VO.stream = e.target.checked; } }), el("span", {}, "Play while it is made")) : null,
    el("button", { class: "hot", disabled: !st.available || voActive() || VO.busy || (VO.ref && !VO.consent), onclick: voRender }, VO.busy || voActive() ? "Speaking…" : "Speak it"));
  const lines = voParse(VO.text), used = [...new Set([0, ...lines.map(l => l.sp)])].sort((a, b) => a - b);
  const num = (key, attrs) => bind(VO, key, el("input", { type: "number", disabled: !st.available, ...attrs }));
  const side = el("aside", { class: "vo-side", "aria-label": "Voices" },
    bounded ? null : el("div", { class: "vo-card" }, el("div", { class: "vo-cardhead" }, el("b", {}, "Cast")),
      used.map(sp => el("div", { class: "vo-cast" }, el("span", { class: "vo-dot vo-sp" + (sp % 4), "aria-hidden": "true" }, voName(sp).slice(0, 1).toUpperCase()),
        (() => { const n = el("input", { value: VO.names[sp] || "", placeholder: "Speaker " + (sp + 1), "aria-label": "Name of speaker " + (sp + 1) }); n.addEventListener("change", () => { VO.names[sp] = n.value; render(); }); return n; })(),
        el("span", { class: "vo-tag" }, "<|speaker:" + sp + "|>"))),
      el("div", { class: "note m-flat vo-pad" }, "Names are for you; the model hears the speaker number. Describe a voice in a [direction] on its first line, or clone one below.")),
    bounded ? null : el("div", { class: "vo-card" }, el("div", { class: "vo-cardhead" }, el("b", {}, "Clone a voice"), el("span", { class: "note m-flat" }, "optional")), el("div", { class: "vo-pad" }, voClone())),
    el("details", { class: "vo-card vo-fold" }, el("summary", {}, el("b", {}, "Sampling"), el("span", { class: "note m-flat" }, "seed " + VO.seed + " · temperature " + VO.temperature)),
      el("div", { class: "vo-pad vo-nums" }, el("label", {}, "Seed", num("seed", { min: 0, step: 1 })), el("label", { title: "Fish's default 1.0" }, "Temperature", num("temperature", { min: 0.1, max: 2, step: 0.05 })),
        el("label", { title: "Fish's default 0.9" }, "Top-p", num("top_p", { min: 0.1, max: 1, step: 0.05 })), el("label", { title: "Fish's default 30" }, "Top-k", num("top_k", { min: 1, max: 200, step: 1 })),
        el("button", { class: "small", disabled: !st.available, title: "Fish's sampling: temperature 1.0, top-p 0.9, top-k 30", onclick: () => { Object.assign(VO, { temperature: 1.0, top_p: 0.9, top_k: 30 }); render(); } }, "Fish defaults"))),
    el("div", { class: "vo-credit" }, typeof mark === "function" ? mark("fish", "") : null, el("span", {}, el("b", {}, "Built with Fish Audio."), " " + (st.licence || "") + " Nothing is sent to Fish Audio: " + (typeof measuredLabel === "function" ? measuredLabel(st.label || "TensorFold") : (st.label || "TensorFold")) + ".")),
    st.available ? el("div", { class: "note m-flat vo-speed" }, voSpeed(st)) : null);
  const main = el("div", { class: "vo-main" },
    el("div", { class: "vo-head" }, el("h1", {}, "Voice"), el("span", { class: "note m-flat" }, bounded ? "Fish Audio Drama 3 · on this machine · short plain speech" : "Fish Audio Drama 3 · on this machine · several speakers, directions, a cloned voice")),
    st.available ? null : el("div", { class: "tip" }, el("b", {}, "Not available here. "), st.why || ""),
    script, go,
    VO.job && VO.job.status === "failed" ? el("div", { class: "tip" }, el("b", {}, "The take failed. "), VO.job.error || "") : VO.job && voActive() ? jobCard(VO.job) : null,
    VO.job ? voLive(VO.job) : null,
    el("div", { class: "sectionhead" }, el("h2", {}, "Takes"), el("span", { class: "note" }, VO.takes.length ? VO.takes.length + (VO.takes.length === 1 ? " take" : " takes") : "")),
    VO.takes.length ? el("div", { class: "vo-takes" }, VO.takes.slice(0, 40).map(voTake)) : el("div", { class: "note" }, "No takes yet."));
  return el("div", { class: "vo" }, main, side); }
// a voice to clone: a clip of one speaker and what it says, used only with the speaker's consent (keep the box)
function voClone() {
  const up = el("input", { type: "file", accept: "audio/*,.wav,.mp3,.m4a,.aac,.flac,.ogg", disabled: !voOn(), "aria-label": "A recording of the voice to clone", onchange: async () => { if (!up.files[0]) return; const data = new FormData(); data.append("file", up.files[0]);
    try { const j = await (await fetch("/api/upload", { method: "POST", body: data })).json(); if (!j.path) throw new Error(j.error || "upload failed");
      const m = await refMeta(j.path), lim = S.voice.reference_seconds;
      if (m.duration && (m.duration < lim[0] - 0.05 || m.duration > lim[1] + 0.05)) return toast("A voice to clone is " + lim[0] + " to " + lim[1] + " seconds of speech, and this clip is " + m.duration.toFixed(1) + " s");
      VO.ref = j.path; VO.consent = false; render(); } catch (e) { toast(e.message); } } });
  return el("div", { class: "vo-clone" }, el("div", { class: "note m-flat" }, "A clean recording of one speaker, about 10 to 30 seconds. The take sounds like it; it is not an exact copy."),
    VO.ref ? [el("audio", { src: file(VO.ref), controls: true, preload: "metadata" }),
      field("What the clip says (left empty, Whisper writes it)", bind(VO, "refText", el("textarea", { rows: 2, placeholder: "the exact words of the recording" }))),
      el("button", { class: "small", onclick: () => { VO.ref = null; VO.refText = ""; VO.consent = false; render(); } }, "Take it out")] : up,
    el("label", { class: "check" }, el("input", { type: "checkbox", checked: VO.consent, disabled: !VO.ref, onchange: e => { VO.consent = e.target.checked; render(); } }), el("span", {}, "I have the speaker's consent to clone this voice"))); }

/* Slate, Sound tab: a voice sheet's cast voice (POST /api/voice/sheet). The sheet's description goes in as a [direction]
   ahead of its sample line; the take is kept on the sheet (sheet.drama) and, ticked, goes into shots that open on cast
   stills as that speaker's reference audio (slate.py, cast_voices). */
function slCastVoice(sheet) {
  const d = sheet.drama || null, job = d && d.job && jobs.find(j => j.id === d.job), active = job && ["queued", "running"].includes(job.status);
  if (d && d.job && job && job.status === "done" && job.voice) { sheet.drama = { path: job.voice.path, seconds: job.voice.duration, text: job.voice.text, seed: job.voice.seed, take: job.voice.id, asr: job.voice.asr ? job.voice.asr.score : null, use: d.use !== false }; slSave(); }
  const now = sheet.drama && sheet.drama.path ? sheet.drama : (sheet.drama && sheet.drama.was) || null, on = voOn(), long = now && now.seconds > 15.05;
  const cast = async seed => { try { const j = await api("/api/voice/sheet", { sheet, project: SL.p.id, seed }); sheet.drama = { job: j.id, use: now ? now.use !== false : true, was: now }; await poll(); slDraw(); } catch (e) { toast(e.message); } };
  return el("div", { class: "group" }, el("h3", {}, "Cast voice · Fish Audio Drama 3"),
    el("div", { class: "note" }, "Speaks the sample line in this voice with Fish Audio Drama 3 on this machine, the sheet's description given as the direction. Ticked, the take goes into shots that open on cast stills as this speaker's reference audio (<Audio N>, 2 to 15 s). Built with Fish Audio."),
    !on ? el("div", { class: "tip" }, el("b", {}, "Not available here. "), (S.voice && S.voice.why) || "") : null,
    el("div", { class: "row", style: "margin-top:8px;align-items:center" }, el("button", { class: "fit primary", disabled: !on || active, onclick: () => cast(now ? (Number(now.seed) || 0) + 1 : 1) }, active ? "Casting…" : now ? "Cast again (next seed)" : "Cast voice"),
      job && job.status === "failed" ? el("div", { class: "note", style: "color:var(--bad);margin:0" }, job.error || "failed") : el("span")),
    active ? jobCard(job) : null,
    now ? el("div", { style: "margin-top:10px" }, el("audio", { src: file(now.path), controls: true, preload: "metadata", style: "width:100%" }),
      el("div", { class: "note" }, [now.seconds != null ? Number(now.seconds).toFixed(1) + " s" : null, now.asr != null ? "Whisper " + now.asr + " %" : null, "seed " + now.seed].filter(Boolean).join(" · ")),
      long ? el("div", { class: "row", style: "align-items:center" }, el("div", { class: "note", style: "color:var(--warn);margin:0" }, "Longer than 15 s: a reference audio clip is 2 to 15 seconds."),
        el("button", { class: "small fit", onclick: async () => { try { const r = await api("/api/voice/cut", { path: now.path, seconds: 15 }); Object.assign(now, { path: r.path, seconds: r.duration || 15 }); slDraw(); } catch (e) { toast(e.message); } } }, "Cut to 15 s")) : null,
      el("label", { class: "check" }, el("input", { type: "checkbox", checked: now.use !== false, onchange: e => { now.use = e.target.checked; SL.plan = {}; slDraw(); } }), el("span", {}, "Use as this voice's Ref2VA reference in shots that open on cast stills")),
      el("div", { class: "row", style: "margin-top:6px" }, el("button", { class: "small fit", onclick: () => voUseAsRef(now.path, now.seconds) }, "Use in the create wizard"), el("button", { class: "small fit", onclick: () => { sheet.drama = null; SL.plan = {}; slDraw(); } }, "Remove the take"), el("span"))) : null); }
