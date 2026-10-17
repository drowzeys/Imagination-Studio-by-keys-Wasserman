"""The Music tab's page code (app/static/music.js), run in node with the page's helpers stood in for:
python3 tests/test_music_page.py. Skipped where node is not installed; no browser, no GPU, no network."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = r"""
const vm = require("vm"), fs = require("fs");
globalThis.S = { music: { available: true, why: "", route: "mlx", label: "TensorFold MLX · Apple Silicon", credit: "MiniMax-Music3",
  licence: "MiniMax-Music3 runs on this machine under the MiniMax-Music3 Community License.", seconds: [10, 300], rtf: 1.31,
  sections: ["Intro", "Verse", "Pre-Chorus", "Chorus", "Post-Chorus", "Bridge", "Hook", "Instrumental", "Solo", "Break", "Build", "Outro"],
  server: { idle_unload_s: 300 } } };
globalThis.tab = "Music"; globalThis.render = () => {}; globalThis.toast = text => (globalThis.toasts = globalThis.toasts || []).push(String(text));
globalThis.calls = []; globalThis.answers = {};
globalThis.api = async (path, body) => { calls.push([path, body]); return answers[path] || {}; };
globalThis.jobs = []; globalThis.poll = async () => {}; globalThis.window = { scrollTo() {} };
const text = node => node == null || node === false ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join(" ") : [node.attrs && node.attrs.placeholder, node.attrs && node.attrs.value, node.attrs && node.attrs.title, text(node.kids)].filter(Boolean).join(" ");
globalThis.text = text;
globalThis.el = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids: kids.flat(), addEventListener() {} });
globalThis.put = (node, ...kids) => node.kids.push(...kids); globalThis.field = (label, node) => el("div", {}, label, node);
globalThis.bind = (obj, key, node) => { node.attrs.value = obj[key] ?? ""; return node; };
globalThis.file = path => "/files/" + path; globalThis.jobCard = job => el("div", {}, "job " + job.id); globalThis.clearButton = () => [el("button", {}, "Clear")];
globalThis.addToTimeline = () => {}; globalThis.saveProject = () => {}; globalThis.mmss = s => Math.round(s) + " s";
const find = (node, test) => !node || typeof node !== "object" ? null : Array.isArray(node) ? node.map(n => find(n, test)).find(Boolean) || null : test(node) ? node : find(node.kids, test);
globalThis.find = find;
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
vm.runInThisContext(`(async () => { const out = {}; ${fs.readFileSync(process.argv[3], "utf8")}; console.log(JSON.stringify(out)); })().catch(e => { console.error(e); process.exit(1); });`);
"""


def run(code: str) -> dict:
    if not shutil.which("node"):
        return None
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "harness.js").write_text(HARNESS)
        (Path(scratch) / "test.js").write_text(code)
        done = subprocess.run(["node", str(Path(scratch) / "harness.js"), str(ROOT / "app/static/music.js"), str(Path(scratch) / "test.js")],
                              capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_tab_shows_the_credit_the_tags_and_the_helper():
    got = run("""
      MU.takes = [];
      const view = viewMusic(); out.text = text(view);
      out.title = text(view.kids[0]);
      out.tag_buttons = ["Intro", "Verse", "Pre-Chorus", "Chorus", "Bridge", "Instrumental", "Outro"].every(t => out.text.includes("[" + t + "]"));
      out.caption = muCaption({ genre: "upbeat modern pop", bpm: "118", key: "A minor", mood: "bright", vocals: "warm female lead vocal", instruments: "bright synths, clean guitar" });
      out.only_bpm = muCaption({ bpm: "92 bpm" });
      out.empty = muCaption({});
      out.notes_none = muLyricsNotes("just words, no tags");
      out.notes_odd = muLyricsNotes("[Verse]\\nla\\n[Kazoo Interlude]\\nla");
      out.notes_ok = muLyricsNotes("[Verse 2]\\nla\\n[chorus]\\nla");
      out.notes_empty = muLyricsNotes("");
      S.music.available = false; S.music.why = "No MiniMax Music 3 weights"; out.off = text(viewMusic()); S.music.available = true;
    """)
    if got is None:
        return
    assert got["title"] == "Music · MiniMax-Music3" and "MiniMax-Music3 Community License" in got["text"]
    assert got["tag_buttons"] and "Section tags go on lines of their own" in got["text"] and "backing vocals" in got["text"]
    assert "Style helper" in got["text"] and "Generate" in got["text"] and "Not played while it is made" in got["text"]
    assert got["caption"] == "Upbeat modern pop, 118 BPM, in A minor, bright. Bright synths, clean guitar. Warm female lead vocal."
    assert got["only_bpm"] == "92 bpm." and got["empty"] == ""
    assert "No section tags" in got["notes_none"][0] and "[Kazoo Interlude]" in got["notes_odd"][0]
    assert got["notes_ok"] == [] and "instrumental" in got["notes_empty"][0]
    assert "Not available here." in got["off"] and "No MiniMax Music 3 weights" in got["off"]


def test_generate_queues_a_song_and_a_take_can_go_under_a_video():
    got = run("""
      Object.assign(MU, { caption: "", lyrics: "" }); await muRender(); out.refused = toasts.slice(-1)[0]; out.calls_before = calls.length;
      Object.assign(MU, { caption: "Lo-fi hip-hop, 80 BPM", lyrics: "[Verse]\\nRain on the glass", seconds: 90, seed: 4 });
      answers["/api/music/takes"] = { id: "m1", status: "queued" };
      await muRender(); out.sent = calls.slice(-1)[0];
      const take = { id: "m1", path: "music/m1.wav", title: "Song", caption: "Lo-fi", lyrics: "[Verse]\\nRain", seconds: 90, seed: 4, duration: 88.2, ended: true,
                     total_s: 120, rtf: 1.36, created: 1, asr: { score: 71, found: 5, words: 7, reached: 40 } };
      out.line = muLine(take);
      MU.open = "m1"; MU.videos = [{ path: "renders/long.mp4", kind: "video", folder: "renders" }, { path: "exports/e.mp4", kind: "video", folder: "exports" }];
      const card = muTake(take); out.card = text(card);
      MU.under = null; await muLay(take); out.no_video = toasts.slice(-1)[0];
      MU.under = "renders/long.mp4"; MU.level = -12; answers["/api/music/soundtrack"] = { id: "s1" };
      await muLay(take); out.lay = calls.slice(-1)[0];
      MU.takes = [take]; const project = { soundtrack: null }; out.pick = text(muExportPick(project));
    """)
    if got is None:
        return
    assert "Describe the style" in got["refused"] and got["calls_before"] == 0
    assert got["sent"] == ["/api/music/takes", {"caption": "Lo-fi hip-hop, 80 BPM", "lyrics": "[Verse]\nRain on the glass", "seconds": 90, "seed": 4, "asr": True}]
    assert "88.2 s (the model ended it)" in got["line"] and "Whisper heard 71 % of the words sung (5 of 7), the song got 40 % into the lyrics" in got["line"]
    assert "Lay it under" in got["card"] and "long.mp4" in got["card"] and "Export · e.mp4" in got["card"] and "-1 dBFS" in got["card"]
    assert "Pick the video first" in got["no_video"]
    assert got["lay"] == ["/api/music/soundtrack", {"take": "m1", "video": "renders/long.mp4", "music_db": -12}]
    assert "Song under the export (MiniMax-Music3)" in got["pick"] and "None" in got["pick"]


def test_play_while_it_is_made_is_offered_where_the_engine_streams_and_on_by_default():
    got = run("""
      MU.takes = []; Object.assign(MU, { caption: "Lo-fi hip-hop", lyrics: "", seconds: 60, seed: 2 });
      out.spark = text(viewMusic()); S.music.route = "comfy"; out.comfy = text(viewMusic()); S.music.route = "mlx";
      answers["/api/music/takes"] = { id: "m0", status: "queued" }; await muRender(); out.sent_plain = calls.slice(-1)[0][1];
      Object.assign(S.music, { stream: true, stream_lead: { no_stall_s: { 60: 10.3, 300: 52.7 }, margin_s: 3, first_audio_s: 7.6 } });
      const view = viewMusic(); out.mac = text(view);
      const box = find(view, n => n.tag === "input" && n.attrs.type === "checkbox" && n.attrs.checked === true && String(n.attrs.onchange).includes("MU.stream"));
      out.checked = !!box;
      out.start60 = muStart(60, S.music); out.start300 = muStart(300, S.music); out.start30 = muStart(30, S.music);
      answers["/api/music/takes"] = { id: "m2", status: "running", params: { live: true } };
      await muRender(); out.sent = calls.slice(-1)[0][1];
      jobs = [MU.job]; const playing = viewMusic();
      const audio = find(playing, n => n.tag === "audio" && String(n.attrs.src).includes("/live"));
      out.src = audio && audio.attrs.src; out.autoplay = audio && audio.attrs.autoplay; out.playing = text(playing);
      box.attrs.onchange({ target: { checked: false } }); out.off = text(viewMusic());
      MU.job = { id: "m3", status: "queued" }; jobs = [MU.job]; await muRender(); out.sent_off = calls.slice(-1)[0][1];
      MU.job = { id: "m4", status: "running", params: { live: false } }; jobs = [MU.job]; out.no_player = !find(viewMusic(), n => n.tag === "audio" && String(n.attrs.src).includes("/live"));
      out.line = muLine({ seed: 1, streamed: true, first_audio_s: 7.6, play_s: 13.1 });
    """)
    if got is None:
        return
    # no stream from the engine (ComfyUI on a Spark, or an engine before 6844b413): no option, nothing asked
    assert "Play while it's made" not in got["spark"] and "Not played while it is made" in got["spark"]
    assert "ComfyUI's route on a DGX Spark does not stream" in got["comfy"] and "live" not in got["sent_plain"]
    assert "Play while it's made" in got["mac"] and got["checked"] and "first sound comes after about 8 s" in got["mac"] and "plays from about 13 s" in got["mac"]
    assert abs(got["start60"] - 13.3) < 1e-6 and abs(got["start300"] - 55.7) < 1e-6 and 7.9 < got["start30"] < 8.1
    assert got["sent"]["live"] is True and got["src"] == "/api/music/takes/m2/live" and got["autoplay"] is True
    assert "Playing while it is made" in got["playing"] and "MiniMax-Music3" in got["playing"]
    assert "Played when it is done." in got["off"] and got["sent_off"]["live"] is False and got["no_player"]
    assert "played while made: first sound after 7.6 s, playing from 13.1 s" in got["line"]


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
