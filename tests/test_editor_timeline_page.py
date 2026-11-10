"""The Editor's timeline strip (app/static/index.html, viewEditor), run in node with the page's helpers stood in for:
python3 -m pytest tests/test_editor_timeline_page.py. A clip is dragged onto another to take its place, and the mouse
wheel scrolls a strip wider than its card sideways. Skipped without node."""
from pathlib import Path

from test_shell_page import run

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "app/static/index.html").read_text(encoding="utf-8")


def editor_source() -> str:
    start = HTML.index("function viewEditor()")
    depth, end = 0, None
    for i in range(HTML.index("{", start), len(HTML)):
        depth += 1 if HTML[i] == "{" else -1 if HTML[i] == "}" else 0
        if depth == 0:
            end = i + 1
            break
    assert end, "viewEditor() did not close"
    return HTML[start:end]


SETUP = editor_source() + r"""
  globalThis.saves = 0; globalThis.saveProject = async () => { saves++; };
  globalThis.finishPanel = undefined; globalThis.bind = (obj, key, node) => node; globalThis.muExportPick = () => null; globalThis.timelineZip = id => "/api/export/timeline/" + id + ".zip"; globalThis.jobCard = () => null;
  answers["/api/library"] = [];
  project = { id: "p1", name: "Cut", timeline: ["a", "b", "c"].map(n => ({ path: "renders/" + n + ".mp4", in: 0, out: 0, duration: 5 })) };
  const page = viewEditor(); await new Promise(r => setTimeout(r, 20));  // the timeline is drawn once the project and the Library are read
  const strip = find(page, n => n.attrs && n.attrs.class === "tl"), clips = strip.kids.filter(n => n.attrs && n.attrs.class === "clip");
  const order = () => project.timeline.map(c => c.path.split("/").pop());
  const event = (data, target) => ({ prevented: false, preventDefault() { this.prevented = true; }, target: target || { closest: () => null },
    currentTarget: { classList: { add() {}, remove() {} } }, dataTransfer: { types: ["text/x-timeline"], getData: () => data, setData(k, v) { this.set = [k, v]; } } });
"""


def test_a_clip_dragged_onto_another_takes_its_place():
    out = run(SETUP + r"""
      out.draggable = clips.map(n => n.attrs.draggable);
      const start = event(""); clips[0].attrs.ondragstart(start); out.carried = start.dataTransfer.set;
      const over = event(""); clips[2].attrs.ondragover(over); out.overAllowed = over.prevented;
      clips[2].attrs.ondrop(event("p1:0")); out.after = order(); out.saves = saves;
      clips[1].attrs.ondrop(event("p1:1")); out.same = order();
      clips[1].attrs.ondrop(event("p1:x")); clips[1].attrs.ondrop(event("x")); clips[1].attrs.ondrop(event("p1:")); out.junk = order();
      clips[0].attrs.ondrop(event("p2:2")); out.other = order(); out.savesAfter = saves;""")
    assert out["draggable"] == ["true", "true", "true"]
    assert out["carried"] == ["text/x-timeline", "p1:0"] and out["overAllowed"] is True
    assert out["after"] == ["b.mp4", "c.mp4", "a.mp4"] and out["saves"] == 1  # the first clip now in the third place
    assert out["same"] == out["after"] and out["junk"] == out["after"]  # onto itself, or not a clip: nothing moves
    assert out["other"] == out["after"] and out["savesAfter"] == 1  # a clip of another project's timeline: nothing moves


def test_a_drag_that_starts_in_a_field_is_not_a_clip_drag():
    out = run(SETUP + r"""
      const start = event("", { closest: sel => sel === "input" ? {} : null }); clips[0].attrs.ondragstart(start);
      out.prevented = start.prevented; out.carried = start.dataTransfer.set || null;""")
    assert out["prevented"] is True and out["carried"] is None  # In and Out stay text fields to select in


def test_the_wheel_scrolls_a_wide_strip_sideways_and_leaves_a_narrow_one():
    out = run(SETUP + r"""
      const wheel = (dy, dx, ctrlKey = false) => ({ deltaY: dy, deltaX: dx, ctrlKey, prevented: false, preventDefault() { this.prevented = true; } });
      Object.assign(strip, { scrollWidth: 1200, clientWidth: 400, scrollLeft: 0 });
      const down = wheel(120, 0); strip.attrs.onwheel(down); out.wide = [strip.scrollLeft, down.prevented];
      const sideways = wheel(0, 80); strip.attrs.onwheel(sideways); out.sideways = [strip.scrollLeft, sideways.prevented];
      const zoom = wheel(120, 0, true); strip.attrs.onwheel(zoom); out.zoom = [strip.scrollLeft, zoom.prevented];
      Object.assign(strip, { scrollWidth: 400, clientWidth: 400, scrollLeft: 0 });
      const narrow = wheel(120, 0); strip.attrs.onwheel(narrow); out.narrow = [strip.scrollLeft, narrow.prevented];""")
    assert out["wide"] == [120, True]
    assert out["sideways"] == [120, False]  # a trackpad's own sideways scroll is left to the browser
    assert out["zoom"] == [120, False]  # Ctrl and the wheel, or a pinch, is the browser's zoom
    assert out["narrow"] == [0, False]  # the page scrolls on as before
