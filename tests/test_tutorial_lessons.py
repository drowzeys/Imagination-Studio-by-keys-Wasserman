"""The Tutorial tab's lessons for the Music tab's two song engines and for Character Swap (app/static/index.html,
lessons()), run in node: python3 -m pytest tests/test_tutorial_lessons.py. "Show me in the app" must point at a
control the tab really draws (app/static/music.js, app/static/swap.js). Skipped without node."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from test_shell_page import need_node

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "spark"))
import swap  # noqa: E402  (the panel's estimates: app/swap.py)

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
SWAP_JS = (ROOT / "app/static/swap.js").read_text(encoding="utf-8")
MUSIC_JS = (ROOT / "app/static/music.js").read_text(encoding="utf-8")


def lessons_source() -> str:
    start = HTML.index("function lessons()")
    depth, end = 0, None
    for i in range(HTML.index("{", start), len(HTML)):
        depth += 1 if HTML[i] == "{" else -1 if HTML[i] == "}" else 0
        if depth == 0:
            end = i + 1
            break
    assert end, "lessons() did not close"
    return HTML[HTML.index("const BAKER = "):start] + HTML[start:end]


def run(body: str):
    need_node()
    script = r"""
const vm = require("vm"), fs = require("fs");
globalThis.location = { port: "7870" };
globalThis.S = { platform: "spark", platform_label: "DGX SPARK · NVIDIA GB10 · H3-CUDA", turbo: false, x2: false, sharp: null,
  custom: { sides: [256, 1344], steps: [1, 40] }, home: "/studio", ref2va: false, voice: {} };
globalThis.cardFace = () => false;
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
const out = {};
const lesson = id => lessons().find(l => l.id === id);
""" + body + "\nconsole.log(JSON.stringify(out));\n"
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "lessons.js").write_text(lessons_source())
        (Path(scratch) / "harness.js").write_text(script)
        done = subprocess.run(["node", str(Path(scratch) / "harness.js"), str(Path(scratch) / "lessons.js")],
                              capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_music_lesson_names_both_engines_and_points_at_the_picker_where_it_is_drawn():
    out = run("""
      const pick = () => { const L = lesson("music"); const st = L.steps.find(s => s.t === "Choose the song engine");
        return { title: L.title, first: L.steps[0].d.join(" "), body: st.d.join(" "), tip: st.tip, go: st.go, all: JSON.stringify(L) }; };
      out.none = pick();
      S.music = { engine: "yue2", engines: { yue2: { installed: true, available: true }, music3: { available: true } } }; out.yue = pick();
      S.music = { engine: "music3", engines: { yue2: { installed: false, available: false }, music3: { available: true } } }; out.mac = pick();
    """)
    yue, mac, none = out["yue"], out["mac"], out["none"]
    assert yue["title"] == "A song with YuE2 or MiniMax-Music3"
    assert "YuE2" in yue["first"] and "MiniMax-Music3" in yue["first"] and "On a Mac it is MiniMax-Music3 alone" in yue["first"]
    assert yue["go"] == {"tab": "Music", "spot": "YuE2"} and "Song engine" in yue["body"]
    assert "CC BY-NC 4.0" in yue["tip"] and "company needs the authors' licence" in yue["tip"]
    assert "about 48 s with YuE2" in yue["all"] and "Download score" in yue["all"]
    for other in (mac, none):  # no picker: Show me goes to the tab's heading
        assert other["go"] == {"tab": "Music", "spot": "Music"} and "oneshot-setup-spark.sh --yue2" in other["body"]
    # the spot is the picker's own button, named as music.js names it
    assert 'yue2: "YuE2"' in MUSIC_JS and '"aria-label": "Song engine"' in MUSIC_JS


def test_the_character_swap_lesson_points_at_the_panel_s_real_controls():
    out = run("""
      const L = lesson("swap"); out.track = L.track; out.title = L.title; out.steps = L.steps.map(s => [s.t, s.go]);
      out.text = JSON.stringify(L); out.library = lessons().filter(l => l.track === "Library").map(l => l.id);
    """)
    assert out["track"] == "Library" and out["title"] == "Character Swap" and out["library"] == ["library", "actions", "swap"]
    spots = [go["spot"] for _, go in out["steps"]]
    assert all(go["tab"] == "Library" for _, go in out["steps"])
    assert spots == ["Swap the character", "The character", "Part starts at", "Quality",
                     "Put the character in the first frame first", "Swap the character"]
    # each spot starts the text of a label or button swap.js draws
    for spot in spots:
        assert ('"' + spot) in SWAP_JS, spot
    text = out["text"]
    fast, high = (round(swap.estimate(5, q) / 60) for q in (480, 768))  # the panel's own figure: swapMinutes
    for said in ("5 to 15 second", "Fast · 480p", "High · 768p", "hard cut", "opens on nobody", "On a Mac",
                 "not licensed for use in the EU, the UK, South Korea or the US", f"about {fast} minutes at Fast, about {high} at High",
                 "a little over twice as long", "one more for about every 4.4 seconds"):
        assert said in text, said


def test_the_swap_times_in_the_guides_and_the_tutorial_are_the_panels():
    import swap_plan as plan

    names = {1: "one", 2: "two", 3: "three", 4: "four"}
    rows = []
    for n in (5, 8, 10, 15):
        windows = len(plan.windows(plan.part_frames(n)))
        rows.append(f"| {n} s, {names[windows]} window{'s' if windows > 1 else ''} | about {round(swap.estimate(n, 480) / 60)} min | "
                    f"about {round(swap.estimate(n, 768) / 60)} min |")
    table = "\n".join(rows)
    guide, history, tutorial = ((ROOT / name).read_text(encoding="utf-8")
                                for name in ("docs/user-guide.md", "docs/changelog.md", "TUTORIAL.md"))
    assert table in guide and table in tutorial, table
    fast, high = (round(swap.estimate(5, q) / 60) for q in (480, 768))
    assert f"a 5 s part in\n  about **{fast} minutes** at Fast and about **{high} minutes** at High" in history
    assert "a little over twice as long" in tutorial and "a little over twice as long" in guide
