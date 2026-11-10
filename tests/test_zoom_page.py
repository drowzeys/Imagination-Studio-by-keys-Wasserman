"""A picture shown larger (app/static/zoom.js), run in node with the document stood in for:
python3 -m pytest tests/test_zoom_page.py. Skipped without node."""
import json
import subprocess
import tempfile
from pathlib import Path

from test_shell_page import need_node

ROOT = Path(__file__).resolve().parent.parent
STAND_IN = """
const listeners = {}, made = [];
const node = tag => ({ tag, kids: [], attrs: {}, handlers: {}, append(...k) { this.kids.push(...k); }, setAttribute(k, v) { this.attrs[k] = v; },
  addEventListener(k, f) { this.handlers[k] = f; }, remove() { this.removed = true; } });
globalThis.document = { addEventListener: (k, f) => { listeners[k] = f; }, createElement: tag => { const n = node(tag); made.push(n); return n; },
  body: { kids: [], append(n) { this.kids.push(n); } } };
"""


def run(code: str) -> dict:
    need_node()
    script = STAND_IN + (ROOT / "app/static/zoom.js").read_text() + code + "\nconsole.log(JSON.stringify(out));"
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "zoom.js").write_text(script)
        done = subprocess.run(["node", str(Path(scratch) / "zoom.js")], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr[-1500:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_a_picture_is_shown_larger_and_closed_again():
    out = run("""
const shown = { src: "/files/a.png", alt: "a", currentSrc: "", closest(s) { return s === "img" ? this : null; }, matches: () => true };
let prevented = false;
listeners.click({ target: shown, preventDefault() { prevented = true; } });
const layer = document.body.kids[0];
const out = { opened: !!layer && layer.attrs.role === "dialog", src: layer && layer.kids[0].src, prevented };
listeners.keydown({ key: "Escape" }); out.closedByEscape = layer.removed === true;
const inButton = { src: "/files/b.png", closest(s) { return s === "img" ? this : s.includes("button") ? {} : null; }, matches: () => true };
listeners.click({ target: inButton, preventDefault() {} }); out.inButton = document.body.kids.length;
const other = { src: "/files/c.png", closest(s) { return s === "img" ? this : null; }, matches: () => false };
listeners.click({ target: other, preventDefault() {} }); out.other = document.body.kids.length;
""")
    assert out["opened"] is True and out["src"] == "/files/a.png" and out["prevented"] is True
    assert out["closedByEscape"] is True
    assert out["inButton"] == 1 and out["other"] == 1  # a picture in a button, or anywhere else, is left alone



def test_the_library_grid_and_cast_pictures_are_the_ones_it_opens():
    page = (ROOT / "app/static/index.html").read_text()
    assert 'el("div", { class: "tile", "data-path": m.path' in page
    assert 'media(m.path, { "data-zoom": true })' in page  # the Library's grid marks its pictures
    assert '<script src="zoom.js"></script>' in page
    selector = (ROOT / "app/static/zoom.js").read_text().split("const ZOOM_IN = ", 1)[1].split(";", 1)[0]
    assert selector == "'[data-room-view=\"cast\"] img, img[data-zoom]'"
    assert '"data-room-view": "cast"' in (ROOT / "app/static/rooms.js").read_text()


def test_the_lora_training_picker_tiles_are_left_to_pick():
    # LoRA training's picker (train.js) is a grid of .tile[data-path] too: a click there ticks the picture, no more
    train = (ROOT / "app/static/train.js").read_text()
    assert 'class: "tile ag-tile"' in train and "data-zoom" not in train
