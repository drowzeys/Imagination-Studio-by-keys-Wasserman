"""The studio's frame and its new rooms (app/static/shell.js, home.js, machine.js, agent.js, suite.js), run in node with
the page's helpers stood in for: python3 tests/test_shell_page.py. Skipped where node is not installed."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["shell.js", "home.js", "machine.js", "agent.js", "suite.js"]
HARNESS = r"""
const vm = require("vm"), fs = require("fs");
globalThis.S = { platform: "spark", platform_label: "DGX SPARK · NVIDIA GB10 · H3-CUDA", presets: { small: { size: [864, 480] }, p720: { size: [1280, 720] }, sharp: { size: [1376, 768] } },
  sharp: { seed: 42, seconds: [8, 10], quality: "fh8" }, long: { 30: "30 s" }, ref2va: true, ref_media: true, reference_total: 12, sparks: { supported: true } };
globalThis.TABS = ["Home", "Create", "Production", "Import", "Motion Previs", "Agent", "Voice", "Music", "Editor", "Library", "Machine", "Sparks", "Brains", "Tutorial"];
globalThis.tab = "Home"; globalThis.renders = 0; globalThis.render = () => { renders++; };
globalThis.toasts = []; globalThis.toast = t => toasts.push(String(t));
globalThis.calls = []; globalThis.answers = {};
globalThis.api = async (path, body) => { calls.push([path, body]); if (path in answers) { const a = answers[path]; if (a instanceof Error) throw a; return a; } return {}; };
globalThis.jobs = []; globalThis.poll = async () => {}; globalThis.PICK = new Set(); globalThis.project = { id: "p1" };
globalThis.window = { scrollTo() {} }; globalThis.history = { replaceState() {} }; globalThis.localStorage = { getItem() { return null; }, setItem() {} };
globalThis.document = { querySelector() { return null; }, querySelectorAll() { return []; }, body: { classList: { toggle() {} } }, activeElement: { matches() { return false; } } };
globalThis.$ = () => null; globalThis.location = { assign(u) { calls.push(["assign", u]); } };
globalThis.C = { step: 0, goes: "video", start: "text", engine: "fasth3", passes: "8", res: "480p", up: false, steps: "", seed: 1, refs: [], subjects: [], refVideos: [], video_subjects: [], dialogue: [], loras: [], imageSize: "1344x768" };
globalThis.SL = {}; globalThis.UPSCALED = { "480p": ["p960", "1728x960"], "720p": ["qhd", "2560x1440 (2K)"] };
globalThis.sync = () => {}; globalThis.changed = () => {}; globalThis.isLong = () => false; globalThis.isRef = () => C.start === "refs"; globalThis.customOn = () => false;
globalThis.sharpOn = () => false; globalThis.sharpCreate = () => { C.res = "sharp"; }; globalThis.output = () => ({ size: [864, 480], label: "Small" });
globalThis.lorasOf = () => []; globalThis.refCount = () => 0; globalThis.framesOf = () => 124; globalThis.exact = f => f + " frames"; globalThis.showEstimate = () => {};
globalThis.mmss = s => Math.round(s) + " s"; globalThis.count = (n, w) => n + " " + w + (n === 1 ? "" : "s"); globalThis.file = p => "/files/" + p;
globalThis.media = p => el("img", { src: p }); globalThis.addToTimeline = () => {}; globalThis.ensureProject = async () => project; globalThis.imageModel = () => "qwen";
globalThis.bayOpenCalls = 0;
const text = node => node == null || node === false ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join(" ") : [node.attrs && node.attrs.placeholder, node.attrs && node.attrs.title, text(node.kids)].filter(Boolean).join(" ");
globalThis.text = text;
globalThis.el = (tag, attrs = {}, ...kids) => { const n = { tag, attrs, kids: kids.flat(), value: attrs.value || "", addEventListener() {}, classList: { add() {}, remove() {}, toggle() {} } };
  n.replaceChildren = (...k) => { n.kids = k.flat(); }; n.append = (...k) => n.kids.push(...k.flat()); n.replaceWith = () => {}; return n; };
globalThis.put = (node, ...kids) => node.kids.push(...kids.flat()); globalThis.field = (label, node) => el("div", {}, label, node);
const find = (node, test) => !node || typeof node !== "object" ? null : Array.isArray(node) ? node.map(n => find(n, test)).find(Boolean) || null : test(node) ? node : find(node.kids, test);
globalThis.find = find; globalThis.button = (root, words) => find(root, n => n.tag === "button" && text(n).includes(words));
for (const f of process.argv.slice(3)) vm.runInThisContext(fs.readFileSync(f, "utf8"));
vm.runInThisContext(`(async () => { const out = {}; ${fs.readFileSync(process.argv[2], "utf8")}; console.log(JSON.stringify(out)); })().catch(e => { console.error(e); process.exit(1); });`);
"""


def run(code: str) -> dict:
    if not shutil.which("node"):
        return None
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "harness.js").write_text(HARNESS)
        (Path(scratch) / "test.js").write_text(code)
        done = subprocess.run(["node", str(Path(scratch) / "harness.js"), str(Path(scratch) / "test.js"), *[str(ROOT / "app/static" / f) for f in FILES]],
                              capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_every_tab_has_one_room_and_old_links_still_work():
    out = run("""out.rooms = ROOMS.map(r => r.name); out.homeless = TABS.filter(t => ROOMS.filter(r => r.tabs.includes(t)).length !== 1);
      go("Voice"); out.voice = tab; goRoom("cut"); out.cut = tab; go("Nowhere"); out.still = tab; out.names = TABS.map(t => TAB_NAMES[t] || "");""")
    if out is None:
        return
    assert out["rooms"] == ["Write", "Shoot", "Sound", "Cut", "Machine"]
    assert out["homeless"] == ["Home", "Tutorial"]  # the stage and the lessons sit outside the rooms
    assert out["voice"] == "Voice" and out["cut"] == "Library" and out["still"] == "Library"
    assert all(out["names"])


def test_the_palette_finds_actions_and_runs_them():
    out = run("""const acts = palActions(); out.labels = acts.map(a => a.label);
      acts.find(a => a.label === "Ask your agent").run(); out.agent = tab;
      acts.find(a => a.label === "New still or scouts").run(); out.still = [tab, C.goes];
      out.blender = acts.filter(a => a.words.includes("blender")).map(a => a.label);""")
    if out is None:
        return
    for label in ("New clip with sound", "Ask your agent", "Bring in from the suite", "Brains & tools", "Telemetry", "Check for updates"):
        assert label in out["labels"], label
    assert out["agent"] == "Agent" and out["still"] == ["Create", "image"]
    assert "Ask your agent" in out["blender"] and "Brains & tools" in out["blender"]


def test_the_stage_offers_every_recipe_and_the_take_sheet_sets_one():
    out = run("""answers["/api/library"] = [];
      const home = viewHome(); out.home = text(home);
      button(home, "A voice line").attrs.onclick(); out.voice = tab;
      const sheet = takeSheet(); out.sheet = text(sheet);
      button(sheet, "Quick look").attrs.onclick(); out.quick = [C.passes, C.res];""")
    if out is None:
        return
    for words in ("A clip with sound", "Scout, then animate", "A still", "Sharp Spec", "A long take", "A voice line", "A song", "Ask your agent", "ScriptBreak", "Blockout"):
        assert words in out["home"], words
    assert out["voice"] == "Voice"
    assert "Take sheet" in out["sheet"] and "Final 720p" in out["sheet"] and "Go to Render" in out["sheet"]
    assert out["quick"] == ["4", "480p"]  # a Spark without the 2x decoder finishes at 720p


def test_the_agent_sends_the_request_with_the_chosen_tools():
    out = run("""BR.data = { default: "claude", backends: [{ id: "claude", label: "Claude Code", found: true }, { id: "codex", label: "Codex", found: false }], settings: {} };
      BR.tools = { tools: [{ id: "blender", label: "Blender" }, { id: "blockout", label: "Blockout" }, { id: "studio", label: "Studio" }] };
      AG.tasks = []; answers["/api/agent/tasks"] = [];
      let page = viewAgent(); out.page = text(page);
      button(page, "Blockout push-in").attrs.onclick(); page = viewAgent();
      await button(page, "Send").attrs.onclick();
      out.sent = calls.find(c => c[0] === "/api/agent/tasks" && c[1]);
      out.codexOff = !!button(page, "Codex").attrs.disabled;
      useOutput("agent/a1/garage.png", "first"); out.first = [tab, C.image, C.start];""")
    if out is None:
        return
    assert "Describe the shot" in out["page"] and "Blender" in out["page"]
    body = out["sent"][1]
    assert body["backend"] == "claude" and "blockout" in body["tools"] and "Blockout" in body["prompt"]
    assert out["codexOff"]
    assert out["first"] == ["Create", "agent/a1/garage.png", "own"]


def test_a_brought_in_shot_opens_in_create_and_brains_save_the_default():
    out = run("""shotToCreate({ id: "12C", title: "She turns", scene: "A garage at night.", action: "Maya turns.", camera: "Slow push-in.", dialogue: [{ character: "MAYA", line: "Who's there?" }], image: "uploads/suite/x/12c.png" });
      out.create = [tab, C.scene, C.action, C.camera, C.dialogue[0].text, C.image, C.start];
      SU.apps = []; SU.cur = { id: "i1", app: "ScriptBreak", title: "Night Shift", shots: [{ id: "1", title: "Wide", scene: "Garage", action: "Enters" }] };
      out.importPage = text(viewImport());
      BR.data = { default: "claude", settings: { remote: { host: "", backends: [] } }, backends: [{ id: "claude", label: "Claude Code", found: true, where: "ssh:sam@mac" }, { id: "local", label: "Local model server", kind: "http", found: true, models: ["qwen3"] }] };
      BR.tools = { tools: [] }; const page = viewBrains(); out.brains = text(page);
      await button(page, "Make default").attrs.onclick(); out.saved = calls.find(c => c[0] === "/api/brains/settings")[1].default;""")
    if out is None:
        return
    assert out["create"] == ["Create", "A garage at night.", "Maya turns.", "Slow push-in.", "Who's there?", "uploads/suite/x/12c.png", "own"]
    assert "Make a Slate film" in out["importPage"] and "Timeline to Resolve (FCPXML)" in out["importPage"] and "Night Shift" in out["importPage"]
    assert "signed in on sam@mac" in out["brains"] and "1 models" in out["brains"]
    assert out["saved"] == "local"


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
