"""Studio Vault → Looks (app/static/looks.js), run in node with the page's helpers stood in for:
python3 tests/test_looks_page.py. Skipped where node is not installed."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["shell.js", "engines.js", "home.js", "looks.js"]
HARNESS = r"""
const vm = require("vm"), fs = require("fs");
globalThis.S = { platform: "spark", platform_label: "DGX SPARK", long: {}, ref2va: true };
globalThis.TABS = ["Home", "Story", "Image", "Video", "Previs", "Sound", "Edit", "Create", "Production", "Import", "Motion Previs", "Agent", "Voice", "Music", "Editor", "Library", "Looks", "Machine", "Sparks", "Brains", "Tutorial"];
globalThis.tab = "Looks"; globalThis.renders = 0; globalThis.render = () => { renders++; };
globalThis.toasts = []; globalThis.toast = t => toasts.push(String(t));
globalThis.calls = []; globalThis.answers = {};
globalThis.api = async (path, body, method) => { calls.push([path, body, method || ""]); if (path in answers) { const a = answers[path]; if (a instanceof Error) throw a; return a; } return {}; };
globalThis.jobs = []; globalThis.poll = async () => {};
globalThis.window = { scrollTo() {} }; globalThis.history = { replaceState() {} }; globalThis.localStorage = { getItem() { return null; }, setItem() {} };
globalThis.document = { querySelector() { return null; }, querySelectorAll() { return []; }, body: { classList: { toggle() {} } }, activeElement: { matches() { return false; } } };
globalThis.$ = () => null; globalThis.location = {}; globalThis.fetch = async () => ({ ok: false, json: async () => ({}) });
globalThis.C = { step: 0, goes: "image", start: "own", loras: [] };
globalThis.SL = {}; globalThis.sync = () => {}; globalThis.changed = () => {};
globalThis.confirms = []; globalThis.confirm = m => { confirms.push(String(m)); return true; };
globalThis.LORAS = []; globalThis.LORA_NEEDS = { text: "Text or first frame", image: "A picture to edit", action: "A Library action" };
globalThis.HUB = { open: false, browse: false, tab: "all", q: "", mine: false, groups: [], timer: null };
globalThis.loads = 0; globalThis.loadLoras = async () => { loads++; }; globalThis.watched = 0; globalThis.hubWatch = () => { watched++; };
globalThis.edUsed = []; globalThis.edUse = r => edUsed.push(r.id); globalThis.sizeText = b => Math.round(b / 1e6) + " MB";
const text = node => node == null || node === false ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join(" ") : [node.attrs && node.attrs.placeholder, node.attrs && node.attrs.title, text(node.kids)].filter(Boolean).join(" ");
globalThis.text = text;
globalThis.el = (tag, attrs = {}, ...kids) => { const n = { tag, attrs, kids: kids.flat(), value: attrs.value || "", addEventListener() {}, classList: { add() {}, remove() {}, toggle() {} } };
  n.replaceChildren = (...k) => { n.kids = k.flat(); }; n.append = (...k) => n.kids.push(...k.flat()); n.replaceWith = () => {}; return n; };
globalThis.put = (node, ...kids) => node.kids.push(...kids.flat()); globalThis.field = (label, node) => el("div", {}, label, node);
const find = (node, test) => !node || typeof node !== "object" ? null : Array.isArray(node) ? node.map(n => find(n, test)).find(Boolean) || null : test(node) ? node : find(node.kids, test);
globalThis.find = find; globalThis.button = (root, words) => find(root, n => n.tag === "button" && text(n).includes(words));
globalThis.card = (root, id) => find(root, n => n.attrs && n.attrs["data-lora"] === id);
for (const f of process.argv.slice(3)) vm.runInThisContext(fs.readFileSync(f, "utf8"));
vm.runInThisContext(`(async () => { const out = {}; ${fs.readFileSync(process.argv[2], "utf8")}; console.log(JSON.stringify(out)); })().catch(e => { console.error(e); process.exit(1); });`);
"""

SETUP = """LORAS = [
  { id: "wasserman-80s-horror", name: "Wasserman 80s Horror", author: "A. Author", what: "VHS and 16mm horror", builtin: true, usable: true, installed: true, styles: { VHS: "shot on VHS.", "16mm": "on film." }, strength: 1, category: ["wasserman", "style"], licence_tag: "CC-BY" },
  { id: "camera-motion", name: "Camera Motion", author: "Someone", what: "Moves the camera", builtin: true, usable: false, installed: false, category: ["camera"], licence: "Its own licence text.", size: 300000000 },
  { id: "qwen-multiple-angles", name: "Multiple Angles", target: "image", needs: "image", builtin: true, usable: true, installed: true, category: ["camera"] },
  { id: "far-away", name: "Far Away", status: "unsupported", why: "Needs another base.", builtin: true, installed: false, category: ["fun"] }];
HUB.groups = [{ id: "wasserman", name: "Wasserman" }, { id: "camera", name: "Camera" }, { id: "fun", name: "Fun" }, { id: "empty", name: "Empty" }];
"""


def run(code: str) -> dict:
    if not shutil.which("node"):
        return None
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "harness.js").write_text(HARNESS)
        (Path(scratch) / "test.js").write_text(SETUP + code)
        done = subprocess.run(["node", str(Path(scratch) / "harness.js"), str(Path(scratch) / "test.js"), *[str(ROOT / "app/static" / f) for f in FILES]],
                              capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_page_lists_every_look_and_the_horror_card_has_its_banner():
    out = run("""const page = viewLooks(); out.page = text(page);
      out.horror = !!find(card(page, "wasserman-80s-horror"), n => n.tag === "img" && n.attrs.src === "art/looks/80s-horror.webp");
      out.slot = !!find(card(page, "camera-motion"), n => n.attrs && n.attrs["data-art"] === "look-camera-motion");
      out.segs = text(find(page, n => n.attrs && n.attrs.class === "seg-ctl"));
      out.image = !!button(card(page, "qwen-multiple-angles"), "Use in Image"); out.install = !!button(card(page, "camera-motion"), "Install");
      out.deadAct = !!find(card(page, "far-away"), n => n.tag === "button");""")
    if out is None:
        return
    for words in ("Looks", "Wasserman 80s Horror", "Camera Motion", "Multiple Angles", "Far Away", "by A. Author", "Installed", "Needs another base.", "2 of 4 looks are usable", "own licence"):
        assert words in out["page"], words
    assert out["horror"] and out["slot"] and out["image"] and out["install"] and not out["deadAct"]
    assert "Camera" in out["segs"] and "Empty" not in out["segs"]


def test_install_asks_with_the_licence_then_installs():
    out = run("""const page = viewLooks(); await button(card(page, "camera-motion"), "Install").attrs.onclick();
      out.confirm = confirms[0]; out.calls = calls.map(c => c[0]); out.watched = watched;""")
    if out is None:
        return
    assert "Its own licence text." in out["confirm"] and "Camera Motion" in out["confirm"] and "300 MB" in out["confirm"]
    assert "/api/loras/camera-motion/install" in out["calls"] and out["watched"] >= 1


def test_use_in_video_puts_the_look_on_the_clip_and_opens_create():
    out = run("""let page = viewLooks(); button(card(page, "wasserman-80s-horror"), "Use in Video").attrs.onclick();
      out.loras = C.loras; out.goes = C.goes; out.start = C.start; out.tab = tab; out.toast = toasts.pop();
      page = viewLooks(); button(card(page, "wasserman-80s-horror"), "On in Video").attrs.onclick(); out.count = C.loras.length;
      button(card(page, "qwen-multiple-angles"), "Use in Image").attrs.onclick(); out.ed = edUsed;
      await button(card(page, "wasserman-80s-horror"), "Remove").attrs.onclick(); out.removed = calls.find(c => c[2] === "DELETE"); out.after = C.loras.length;""")
    if out is None:
        return
    assert out["loras"] == [{"id": "wasserman-80s-horror", "strength": 1, "style": "VHS"}]
    assert out["goes"] == "video" and out["start"] == "own" and out["tab"] == "Create" and out["toast"] == "Look on: Wasserman 80s Horror"
    assert out["count"] == 1 and out["ed"] == ["qwen-multiple-angles"]
    assert out["removed"][0] == "/api/loras/wasserman-80s-horror" and out["after"] == 0


def test_filters_and_the_add_form():
    out = run("""HUB.tab = "camera"; const grid = () => text(find(viewLooks(), n => n.attrs && n.attrs.class === "lookgrid"));
      out.camera = grid(); HUB.tab = "all"; HUB.q = "horror"; out.q = grid(); HUB.q = ""; HUB.mine = true; out.mine = grid(); HUB.mine = false;
      answers["/api/loras"] = { id: "mine-1", name: "Mine", url: "https://example.invalid/x.safetensors", installed: false };
      let page = viewLooks(); button(page, "+ Add your own").attrs.onclick(); page = viewLooks(); out.form = text(page);
      LK.form.name = "Mine"; LK.form.styles = "A = one.; B = two."; await button(page, "Add to Looks").attrs.onclick();
      const post = calls.find(c => c[0] === "/api/loras" && c[1]); out.post = post[1]; out.inst = calls.some(c => c[0] === "/api/loras/mine-1/install"); out.open = LK.open;""")
    if out is None:
        return
    assert "Camera Motion" in out["camera"] and "Wasserman 80s Horror" not in out["camera"]
    assert "Wasserman 80s Horror" in out["q"] and "Camera Motion" not in out["q"]
    assert "Camera Motion" not in out["mine"] and "Multiple Angles" in out["mine"]
    assert "Download address" in out["form"]
    assert out["post"]["name"] == "Mine" and out["post"]["styles"] == {"A": "one.", "B": "two."} and out["inst"] and out["open"] is False


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
