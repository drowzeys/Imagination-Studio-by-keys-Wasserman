"use strict";
/* Music · MiniMax Music 3, a tab of its own (app/music.py). MiniMax-Music3.
   A song from lyrics (section tags such as [Verse] and [Chorus]) and a style caption, made on this machine: up to five
   minutes with sung vocals, 44.1 kHz stereo, one song per job in the render queue. A song can be laid under a long
   video or the Editor's export as its soundtrack (ducked under the dialogue). On a Mac a song plays while it is made
   (GET /api/music/takes/{id}/live, a stereo WAV whose sound the app holds back until playing it will not stop). */
const MU = { takes: null, caption: "", lyrics: "", seconds: 60, seed: 1, job: null, asr: true, open: false, stream: true, player: null,
  style: { genre: "", bpm: "", key: "", mood: "", vocals: "", instruments: "" }, under: null, level: -10, lay: null, videos: null };
const MU_TAGS = ["Intro", "Verse", "Pre-Chorus", "Chorus", "Bridge", "Instrumental", "Outro"];
const MU_EXAMPLE = { caption: "Upbeat modern pop, 118 BPM, bright synths, punchy four-on-the-floor kick, clean electric guitar plucks, catchy sing-along chorus. Warm female lead vocal with layered harmonies.",
  lyrics: "[Verse]\nWindow down, the summer air is calling out my name\nEvery song on the radio is never quite the same\n\n[Pre-Chorus]\nAnd I can feel it coming like a wave\n\n[Chorus]\nTurn it up, turn it up, we're alive tonight\nDancing on the edge of the neon light\n(oh oh oh)\n\n[Outro]\nTurn it up" };
const muOn = () => !!(S && S.music && S.music.available);
const muActive = () => MU.job && ["queued", "running"].includes(MU.job.status);
// the structured helper: the style's parts in the order the model's own captions put them
function muCaption(style) {
  const v = k => String(style[k] || "").trim().replace(/[.\s]+$/, "");
  const head = [v("genre"), v("bpm") ? (/bpm/i.test(v("bpm")) ? v("bpm") : v("bpm") + " BPM") : "", v("key") ? (/^in /i.test(v("key")) ? v("key") : "in " + v("key")) : "", v("mood")].filter(Boolean).join(", ");
  return [head, v("instruments"), v("vocals")].filter(Boolean).map(s => s.charAt(0).toUpperCase() + s.slice(1) + ".").join(" "); }
// what the lyrics' tags say before the song is queued: no tags at all, a tag the model may not know, a tag with no lines
function muLyricsNotes(lyrics) {
  const text = String(lyrics || ""), found = [...text.matchAll(/\[([^\]\n]{1,40})\]/g)].map(m => m[1].trim());
  const notes = [];
  if (!text.trim()) return ["No lyrics: the song is an instrumental."];
  if (!found.length) notes.push("No section tags: put [Verse], [Chorus] … on lines of their own above the lines they hold, or the model chooses the shape itself.");
  const odd = found.filter(t => !S.music.sections.some(s => s.toLowerCase() === t.toLowerCase().replace(/\s+\d+$/, "")));
  if (odd.length) notes.push("Tags the model may read as words: " + [...new Set(odd)].slice(0, 5).map(t => "[" + t + "]").join(", ") + ".");
  return notes; }
// played while it is made (the Mac engine's stream; ComfyUI's route on a Spark cannot): on unless unticked
const muStreams = st => !!(st && st.available && st.stream);
// when a song of ``secs`` starts to play, in seconds from its request: music.py's start_s (the measured no-stall start
// along the line through the lengths measured, and the margin)
function muStart(secs, st) {
  const lead = (st && st.stream_lead) || {}, pts = Object.entries(lead.no_stall_s || { 60: 10.3, 300: 52.7 }).map(([k, v]) => [Number(k), Number(v)]).sort((a, b) => a[0] - b[0]);
  const [[a, ta], [b, tb]] = [pts[0], pts[pts.length - 1]];
  return Math.max(0, b > a ? ta + (tb - ta) * (secs - a) / (b - a) : ta) + (lead.margin_s != null ? lead.margin_s : 3); }
function muLive(job) {
  if (!muStreams(S.music) || !job || job.status !== "running" || (job.params && job.params.live === false)) return null;
  if (!MU.player || MU.player.id !== job.id) MU.player = { id: job.id, el: el("audio", { src: "/api/music/takes/" + encodeURIComponent(job.id) + "/live", controls: true, autoplay: true, preload: "auto", style: "width:100%;margin-top:8px" }) };
  return el("div", {}, el("div", { class: "note", style: "margin:8px 0 0" }, "Playing while it is made (MiniMax-Music3): it starts once enough of the song is made that it never stops."), MU.player.el); }
async function muLoad() { try { MU.takes = await api("/api/music/takes"); } catch { MU.takes = []; } if (tab === "Music") render(); }
const muLine = t => [t.duration != null ? Number(t.duration).toFixed(1) + " s" + (t.ended ? " (the model ended it)" : "") : null,
  t.total_s ? "made in " + mmss(t.total_s) : null, t.rtf ? t.rtf + "× its length" : null, t.load_s ? "models loaded in " + Math.round(t.load_s) + " s" : null,
  t.peak_memory_gb ? t.peak_memory_gb + " GB at the peak" : null,
  t.streamed ? "played while made" + (t.first_audio_s != null ? ": first sound after " + t.first_audio_s + " s" : "") + (t.play_s != null ? ", playing from " + t.play_s + " s" : "") : null,
  t.asr && t.asr.score != null ? "Whisper heard " + t.asr.score + " % of the words sung (" + t.asr.found + " of " + t.asr.words + ")" + (t.asr.reached != null && t.asr.reached < 100 ? ", the song got " + t.asr.reached + " % into the lyrics" : "") : t.asr && t.asr.skipped ? "no lyrics check: " + t.asr.skipped : null,
  "seed " + t.seed].filter(Boolean).join(" · ");
async function muRender() {
  if (!MU.caption.trim()) return toast("Describe the style first (the helper below writes one)");
  try { MU.job = await api("/api/music/takes", { caption: MU.caption, lyrics: MU.lyrics, seconds: Number(MU.seconds), seed: Number(MU.seed), asr: MU.asr,
    ...(muStreams(S.music) ? { live: !!MU.stream } : {}) }); }
  catch (e) { return toast(e.message); }
  await poll(); render(); }
async function muLay(t) {
  if (!MU.under) return toast("Pick the video first");
  try { MU.lay = await api("/api/music/soundtrack", { take: t.id, video: MU.under, music_db: Number(MU.level) }); toast("Queued: the song under " + MU.under.split("/").pop()); }
  catch (e) { return toast(e.message); }
  await poll(); render(); }
// "Use as soundtrack": a long video or an export (or any clip of the Library) under which the song is laid
function muUnder(t) {
  if (MU.videos === null) { MU.videos = []; api("/api/library").then(list => { MU.videos = list.filter(m => m.kind === "video"); if (tab === "Music") render(); }, () => {}); }
  const pick = el("select", { onchange: e => { MU.under = e.target.value || null; } }, el("option", { value: "" }, "Choose a video…"),
    MU.videos.map(m => el("option", { value: m.path, selected: MU.under === m.path }, (m.folder === "exports" ? "Export · " : "") + m.path.split("/").pop())));
  const lay = MU.lay && jobs.find(j => j.id === MU.lay.id);
  return el("div", { class: "group", style: "margin-top:8px" },
    el("div", { class: "note" }, "Lays this song under a video: a long video, the Editor's export or any clip of the Library. The song sits under the clip's own sound at the level below and is lowered further while someone speaks; no peak goes over -1 dBFS. The result is a new file in exports, beside the clip it came from."),
    el("div", { class: "row", style: "align-items:end;margin-top:6px" }, field("Video", pick),
      el("div", { class: "fit", style: "width:150px" }, el("label", { title: "The song's level against the clip's sound, in dB (0 is as made)" }, "Music level (dB)"), bind(MU, "level", el("input", { type: "number", min: -30, max: 0, step: 1 }))),
      el("button", { class: "fit primary", onclick: () => muLay(t) }, "Lay it under")),
    lay ? el("div", { style: "margin-top:8px" }, jobCard(lay), lay.status === "done" && lay.outputs[0] ? el("div", {}, el("video", { class: "main", src: file(lay.outputs[0]), controls: true }),
      el("div", { class: "row", style: "margin-top:6px" }, el("a", { class: "fit", href: file(lay.outputs[0]), download: "" }, "Download"), el("button", { class: "fit small", onclick: () => addToTimeline(lay.outputs[0]) }, "Add to the editor timeline"), el("span"))) : null) : null); }
function muTake(t) {
  return el("div", { class: "seg", style: "margin-top:10px" }, el("div", { class: "seghead" }, el("b", {}, t.title || "Song"), el("span", { style: "flex:1" }), el("span", { class: "note", style: "margin:0" }, new Date(t.created * 1000).toLocaleString())),
    el("audio", { src: file(t.path), controls: true, preload: "metadata", style: "width:100%;margin-top:6px" }),
    el("div", { class: "note", title: t.asr && t.asr.heard ? "Whisper heard: " + t.asr.heard : "" }, muLine(t)),
    el("div", { class: "note" }, "Style: " + (t.caption.length > 200 ? t.caption.slice(0, 200) + "…" : t.caption)),
    el("div", { class: "row", style: "margin-top:8px;flex-wrap:wrap;gap:6px" },
      el("a", { class: "fit", href: "/api/music/takes/" + encodeURIComponent(t.id) + "/audio", download: "minimax-music3-" + t.id + ".wav" }, el("button", { class: "small", type: "button" }, "Download WAV")),
      el("button", { class: "small fit" + (MU.open === t.id ? " on" : ""), onclick: () => { MU.open = MU.open === t.id ? false : t.id; render(); } }, "Use as soundtrack"),
      el("button", { class: "small fit", title: "Put this song's lyrics, style and length back in the form, at the next seed", onclick: () => { Object.assign(MU, { caption: t.caption, lyrics: t.lyrics === "[Instrumental]" ? "" : t.lyrics, seconds: t.seconds, seed: t.seed + 1 }); window.scrollTo(0, 0); render(); } }, "Again, next seed")),
    MU.open === t.id ? muUnder(t) : null); }
function muHelper() {
  const box = (key, label, placeholder) => el("div", {}, el("label", {}, label), bind(MU.style, key, el("input", { placeholder })));
  return el("div", { class: "group" }, el("h3", {}, "Style helper"),
    el("div", { class: "note" }, "Fill what you know; “Write the style” turns it into the caption above, which you can still edit."),
    el("div", { class: "row" }, box("genre", "Genre", "upbeat modern pop"), box("bpm", "Tempo (BPM)", "118"), box("key", "Key", "A minor")),
    el("div", { class: "row" }, box("mood", "Mood", "bright, hopeful"), box("vocals", "Vocals", "warm female lead vocal with layered harmonies")),
    el("div", { class: "row" }, box("instruments", "Instruments", "bright synths, four-on-the-floor kick, clean guitar plucks"),
      el("div", { class: "fit", style: "align-self:end" }, el("button", { onclick: () => { const c = muCaption(MU.style); if (!c) return toast("Fill at least one box"); MU.caption = c; render(); } }, "Write the style")))); }
function viewMusic() {
  const box = el("section", { class: "card" }, el("h2", {}, "Music · MiniMax-Music3"));
  if (MU.takes === null) { MU.takes = []; muLoad(); }
  const st = S.music || { available: false, why: "This app does not know MiniMax Music 3.", seconds: [10, 300], sections: MU_TAGS, rtf: 1 };
  if (MU.job) { const fresh = jobs.find(j => j.id === MU.job.id); if (fresh) { if (fresh.status === "done" && MU.job.status !== "done") muLoad(); MU.job = fresh; } }
  put(box, el("div", { class: "note", style: "margin-top:-4px" }, "A whole song from your lyrics and a style, on this machine: sung vocals, up to five minutes, 44.1 kHz stereo. A song can go under a long video or the Editor's export (", el("b", {}, "Use as soundtrack"), ")."),
    el("div", { class: "note", style: "color:var(--text)" }, el("b", {}, "MiniMax-Music3."), " " + (st.licence || "") + " " + (st.label ? "Runs on " + st.label + "." : "")));
  if (!st.available) put(box, el("div", { class: "tip" }, el("b", {}, "Not available here. "), st.why || ""));
  const lyrics = bind(MU, "lyrics", el("textarea", { style: "min-height:170px", disabled: !st.available, placeholder: "[Verse]\nThe lines that are sung…\n\n[Chorus]\nThe hook…" }));
  const tagButton = name => el("button", { class: "small fit", type: "button", disabled: !st.available, title: "Add [" + name + "] on a line of its own", onclick: () => { MU.lyrics = (MU.lyrics.replace(/\s+$/, "") + (MU.lyrics.trim() ? "\n\n" : "") + "[" + name + "]\n"); render(); } }, "[" + name + "]");
  const notes = st.sections ? muLyricsNotes(MU.lyrics) : [];
  put(box, el("div", { class: "group", "data-clear": "music" }, el("h3", {}, "Lyrics"), lyrics,
    el("div", { class: "row", style: "margin-top:6px;flex-wrap:wrap;gap:6px" }, MU_TAGS.map(tagButton), el("span")),
    el("div", { class: "note" }, "Section tags go on lines of their own, above the lines they hold: ", el("code", {}, "[Intro]"), ", ", el("code", {}, "[Verse]"), ", ", el("code", {}, "[Pre-Chorus]"), ", ", el("code", {}, "[Chorus]"), ", ", el("code", {}, "[Bridge]"), ", ", el("code", {}, "[Outro]"),
      ". ", el("code", {}, "[Instrumental]"), " is a passage without singing; lines in (parentheses) are backing vocals. Leave the lyrics empty for an instrumental. ",
      el("a", { href: "#", onclick: e => { e.preventDefault(); Object.assign(MU, MU_EXAMPLE); render(); } }, "Pop example")),
    notes.length ? el("div", { class: "note", style: "color:var(--warn)" }, notes.join(" ")) : null,
    el("h3", {}, "Style"), bind(MU, "caption", el("textarea", { style: "min-height:70px", disabled: !st.available, placeholder: "Genre, tempo, key, mood, the voice that sings and the instruments: “Hip-hop anthem, 92 BPM, boom-bap drums and deep 808 bass. Confident male rap vocal.”" })),
    el("div", { class: "row", style: "margin-top:8px" },
      el("div", { class: "fit", style: "width:140px" }, el("label", { title: "The longest the song may be: the model may end it sooner" }, "Length (s)"), bind(MU, "seconds", el("input", { type: "number", min: st.seconds[0], max: st.seconds[1], step: 5, disabled: !st.available }))),
      el("div", { class: "fit", style: "width:120px" }, el("label", {}, "Seed"), bind(MU, "seed", el("input", { type: "number", min: 0, step: 1, disabled: !st.available }))), ...clearButton("music")),
    muHelper()));
  const secs = Math.min(Math.max(Number(MU.seconds) || 60, st.seconds[0]), st.seconds[1]);
  put(box, el("div", { class: "row", style: "margin-top:16px;align-items:center" }, el("div", { class: "fit" }, el("button", { class: "hot", disabled: !st.available || muActive(), onclick: muRender }, muActive() ? "Making the song…" : "Generate")),
    el("div", { class: "note", style: "margin:0" }, st.available ? "One song at a time in the queue, never beside a video render or a voice take. About " + mmss(secs * (st.rtf || 1)) + " for " + secs + " s once the models are loaded (" + (st.route === "comfy" ? "they stay loaded in ComfyUI" : "the engine stays loaded") + " for " + Math.round(((st.server || {}).idle_unload_s || 300) / 60) + " minutes after a song, and is unloaded at once when another job starts). "
      + (muStreams(st) ? (MU.stream ? "Played while it is made: the engine's first sound comes after about 8 s, and it plays from about " + Math.round(muStart(secs, st)) + " s, once enough of the song is made that it never stops." : "Played when it is done.") : "Not played while it is made" + (st.route === "comfy" ? " (ComfyUI's route on a DGX Spark does not stream)." : st.route === "metal" ? " (TensorFold's Metal engine does not stream yet; MUSIC3_BACKEND=mlx runs the MLX engine, which does)." : ".")) : "")),
    muStreams(st) ? el("label", { class: "check", title: "The song plays from the app as the engine makes it; the kept song is the engine's finished WAV either way" },
      el("input", { type: "checkbox", checked: MU.stream, onchange: e => { MU.stream = e.target.checked; render(); } }), el("span", {}, "Play while it's made")) : null,
    el("label", { class: "check" }, el("input", { type: "checkbox", checked: MU.asr, onchange: e => { MU.asr = e.target.checked; } }), el("span", {}, "Have Whisper listen for the lyrics after (a rough score: sung words are harder to hear than spoken ones)")));
  if (MU.job) put(box, MU.job.status === "failed" ? el("div", { class: "tip" }, el("b", {}, "The song failed. "), MU.job.error || "") : muActive() ? jobCard(MU.job) : null);
  if (MU.job) put(box, muLive(MU.job));
  put(box, el("h3", { style: "margin-top:22px" }, "Songs"), MU.takes.length ? MU.takes.slice(0, 40).map(muTake) : el("div", { class: "note" }, "No songs yet."));
  return box; }
// the Editor: a song of this tab under the whole export (POST /api/jobs, kind export, params.soundtrack)
function muExportPick(project) {
  if (MU.takes === null) { MU.takes = []; muLoad().then(() => { if (tab === "Editor") render(); }); }
  if (!MU.takes.length) return null;
  const now = project.soundtrack || null;
  return el("div", { class: "row", style: "margin:4px 0 8px;align-items:end" },
    field("Song under the export (MiniMax-Music3)", el("select", { onchange: e => { project.soundtrack = e.target.value ? { take: e.target.value, music_db: now ? now.music_db : -10 } : null; saveProject(); render(); } },
      el("option", { value: "" }, "None"), MU.takes.map(t => el("option", { value: t.id, selected: !!(now && now.take === t.id) }, (t.title || "Song") + " · " + (t.duration != null ? Number(t.duration).toFixed(0) + " s" : ""))))),
    now ? el("div", { class: "fit", style: "width:150px" }, el("label", {}, "Music level (dB)"), bind(now, "music_db", el("input", { type: "number", min: -30, max: 0, step: 1 }), saveProject)) : el("span")); }
