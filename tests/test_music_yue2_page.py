"""The Music tab's song engine picker (app/static/music.js) where YuE2 is installed, run in node with the page's helpers
stood in for (tests/test_music_page.py's harness): python3 -m pytest tests/test_music_yue2_page.py. Skipped without node."""
from test_music_page import run

ENGINES = """
  const yue = { available: true, why: "", route: "yue2", label: "PyTorch CUDA · NVIDIA", credit: "YuE2", installed: true,
    licence: "YuE2 runs on this machine. Its code is Apache 2.0; its weights are CC BY-NC 4.0 with the authors' creator permission.",
    seconds: [10, 300], rtf: 0.7, stream: false, server: { idle_unload_s: 300 }, sections: ["Intro", "Verse", "Pre-Chorus", "Chorus", "Bridge", "Interlude", "Outro", "Instrumental"] };
  const m3 = Object.assign({}, S.music, { route: "cuda", label: "TensorFold CUDA · DGX Spark" });
  S.music = Object.assign({}, yue, { engine: "yue2", engine_names: { yue2: "YuE2", music3: "MiniMax Music 3" }, engines: { yue2: yue, music3: m3 } });
"""


def test_the_picker_starts_on_the_default_and_each_engine_shows_its_licence():
    got = run(ENGINES + """
      MU.takes = []; MU.engine = null;
      let view = viewMusic(); out.title = text(view.kids[0]); out.text = text(view);
      const picker = find(view, n => n.attrs && n.attrs.class && String(n.attrs.class).includes("seg-ctl") && n.attrs["aria-label"] === "Song engine");
      out.buttons = picker ? picker.kids.map(b => [text(b).trim(), b.attrs.class, b.attrs["aria-checked"]]) : null;
      picker.kids[1].attrs.onclick(); view = viewMusic(); out.title3 = text(view.kids[0]); out.text3 = text(view);
      Object.assign(MU, { caption: "Synthwave, 110 BPM", lyrics: "[Verse]\\nNeon", seconds: 60, seed: 4 });
      answers["/api/music/takes"] = { id: "m1", status: "queued" }; await muRender(); out.sent3 = calls.slice(-1)[0][1];
      MU.engine = "yue2"; await muRender(); out.sent = calls.slice(-1)[0][1];
      S.music.engines.yue2 = Object.assign({}, yue, { available: false, why: "No YuE2 weights here" }); out.off = text(viewMusic());
    """)
    if got is None:
        return
    assert got["title"] == "Music · YuE2" and "CC BY-NC 4.0" in got["text"] and "writes the song's score" in got["text"]
    assert "[Interlude]" in got["text"] and "The studio's default here" in got["text"]
    assert got["buttons"] == [["YuE2", "on", "true"], ["MiniMax Music 3", "", "false"]]
    assert got["title3"] == "Music · MiniMax-Music3" and "MiniMax-Music3 Community License" in got["text3"] and "This song only" in got["text3"]
    assert got["sent3"]["engine"] == "music3" and got["sent"]["engine"] == "yue2" and "live" not in got["sent"]
    assert "Not available here." in got["off"] and "No YuE2 weights here" in got["off"] and "YuE2 (not ready)" in got["off"]


def test_a_yue2_take_shows_its_score_and_its_name():
    got = run(ENGINES + """
      const take = { id: "y1", engine: "yue2", path: "music/y1.wav", score: "music/y1.yue2/score.abc", title: "Song", caption: "pop", lyrics: "[Verse]\\nla",
                     seconds: 60, seed: 7, duration: 60, ended: false, plan_s: 5.2, total_s: 34, rtf: 0.57, created: 1, instrumental: false };
      const card = muTake(take); out.card = text(card); out.line = muLine(take);
      const links = []; (function walk(n) { if (!n || typeof n !== "object") return; if (Array.isArray(n)) return n.forEach(walk); if (n.tag === "a") links.push(n.attrs.download); walk(n.kids); })(card);
      out.links = links;
      MU.takes = [take, { id: "m1", engine: "music3", title: "Old", duration: 30 }]; out.pick = text(muExportPick({ soundtrack: null }));
      MU.engine = "music3"; (function walk(n) { if (!n || typeof n !== "object") return; if (Array.isArray(n)) return n.forEach(walk);
        if (n.tag === "button" && text(n.kids) === "Again, next seed") n.attrs.onclick(); else walk(n.kids); })(card);
      out.engine_after = MU.engine; out.seed_after = MU.seed;
    """)
    if got is None:
        return
    assert "Download score" in got["card"] and got["links"] == ["yue2-y1.wav", "yue2-y1.abc"]
    assert "YuE2: the score in 5 s" in got["line"]
    assert "Song under the export (YuE2, MiniMax-Music3)" in got["pick"]
    assert got["engine_after"] == "yue2" and got["seed_after"] == 8


def test_without_yue2_installed_there_is_no_picker():
    got = run("""
      MU.takes = []; S.music.engines = { music3: S.music, yue2: { available: false, installed: false, why: "YuE2 runs on Linux" } }; S.music.engine = "music3";
      out.text = text(viewMusic()); out.picker = !!find(viewMusic(), n => n.attrs && n.attrs["aria-label"] === "Song engine");
    """)
    if got is None:
        return
    assert got["picker"] is False and "Music" in got["text"] and "MiniMax-Music3" in got["text"]


def test_where_yue2_is_not_offered_the_tab_says_why_in_the_picker_s_place():
    got = run(ENGINES + """
      MU.takes = []; MU.engine = null;
      const mac = Object.assign({}, yue, { available: false, installed: false, why: "YuE2 has no Mac build: on a Mac the Music tab makes songs with MiniMax Music 3." });
      S.music = Object.assign({}, S.music.engines.music3, { engine: "music3", engine_names: { yue2: "YuE2", music3: "MiniMax Music 3" }, engines: { yue2: mac, music3: S.music.engines.music3 } });
      const view = viewMusic(); out.text = text(view);
      out.picker = !!find(view, n => n.attrs && n.attrs["aria-label"] === "Song engine");
      out.note = !!find(view, n => n.attrs && n.attrs.class && String(n.attrs.class).includes("mu-yue2-why"));
    """)
    if got is None:
        return
    assert not got["picker"] and got["note"] and "YuE2 has no Mac build" in got["text"]
