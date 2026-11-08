"""Edit → Graphics, the motion graphics panel (app/static/graphics.js), and its two Library buttons (actions.js), run in
node with the page's helpers stood in for: python3 -m pytest tests/test_graphics_page.py. Skipped where node is not
installed; no browser, no render."""
import json
import subprocess
import tempfile
from pathlib import Path

from test_shell_page import need_node

ROOT = Path(__file__).resolve().parent.parent
HARNESS = r"""
const vm = require("vm"), fs = require("fs");
globalThis.tab = "Graphics"; globalThis.renders = 0; globalThis.render = () => { renders++; };
globalThis.toasts = []; globalThis.toast = t => toasts.push(String(t));
globalThis.calls = []; globalThis.answers = {};
globalThis.api = async (path, body) => { calls.push([path, body]); if (path in answers) { const a = answers[path]; if (a instanceof Error) throw a; return typeof a === "function" ? a(body) : a; } return {}; };
globalThis.jobs = []; globalThis.poll = async () => {}; globalThis.went = []; globalThis.go = t => { went.push(t); tab = t; };
const text = node => node == null || node === false ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join(" ") : [node.attrs && node.attrs.placeholder, node.attrs && node.attrs.title, text(node.kids)].filter(Boolean).join(" ");
globalThis.text = text;
globalThis.el = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids: kids.flat(Infinity).filter(k => k != null && k !== false), addEventListener() {} });
globalThis.field = (label, node) => el("div", {}, el("label", {}, label), node);
globalThis.bind = (obj, key, node) => { node.attrs.value = obj[key] ?? ""; node.obj = obj; node.key = key; return node; };
globalThis.select = (obj, key, options) => bind(obj, key, el("select", {}, Object.entries(options).map(([v, t]) => el("option", { value: v }, t))));
globalThis.choice = (obj, key, options, after) => el("div", { class: "choice", role: "radiogroup" }, Object.entries(options).map(([v, [title, sub]]) =>
  el("button", { type: "button", role: "radio", "aria-checked": String(obj[key]) === v ? "true" : "false", onclick: () => { obj[key] = v; if (after) after(); } }, el("b", {}, title), sub ? el("span", {}, sub) : null)));
globalThis.media = p => el("img", { src: p }); globalThis.file = p => "/files/" + p; globalThis.jobCard = job => el("div", { class: "job" }, "job " + job.id + " " + job.status);
globalThis.pageHead = (eyebrow, title, line) => el("div", { class: "pagehead" }, eyebrow, title, line); globalThis.copyText = () => {}; globalThis.addToTimeline = p => calls.push(["timeline", p]);
globalThis.ACT = { state: null }; globalThis.actOpen = () => {}; globalThis.actOf = () => ({ ready: true });
const find = (node, test) => !node || typeof node !== "object" ? null : Array.isArray(node) ? node.map(n => find(n, test)).find(Boolean) || null : test(node) ? node : find(node.kids, test);
const all = (node, test, out = []) => { if (!node || typeof node !== "object") return out; if (Array.isArray(node)) { node.forEach(n => all(n, test, out)); return out; } if (test(node)) out.push(node); all(node.kids, test, out); return out; };
globalThis.find = find; globalThis.all = all; globalThis.button = (root, words) => find(root, n => n.tag === "button" && text(n).includes(words));
globalThis.STATE = { ready: { ok: true, hyperframes: "0.8.186", why: null }, spark: false, fps: 24,
  templates: [["title", "Title", "A film title and a subtitle"], ["lower-third", "Lower third", "A name and a role laid over a clip"], ["kinetic", "Kinetic type", "A sentence word by word"],
    ["captions", "Captions", "A clip's speech burned in"], ["slideshow", "Slideshow", "Pictures with a slow push-in"], ["music-video", "Music video", "Clips cut on the beat"],
    ["explainer", "Explainer", "Sections timed to a voice-over"]].map(([id, title, one]) => ({ id, title, one })) };
globalThis.LIB = [{ path: "renders/a.mp4", kind: "video" }, { path: "renders/b.mp4", kind: "video" }, { path: "uploads/one.png", kind: "image" },
  { path: "voice/v1.wav", kind: "audio", sound: "voice", title: "Hello there" }, { path: "music/song.wav", kind: "audio", sound: "music", title: "Night drive" }];
for (const f of process.argv.slice(3)) vm.runInThisContext(fs.readFileSync(f, "utf8"));
vm.runInThisContext(`(async () => { const out = {}; ${fs.readFileSync(process.argv[2], "utf8")}; console.log(JSON.stringify(out)); })().catch(e => { console.error(e); process.exit(1); });`);
"""


def run(code: str) -> dict:
    need_node()
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "harness.js").write_text(HARNESS)
        (Path(scratch) / "test.js").write_text(code)
        done = subprocess.run(["node", str(Path(scratch) / "harness.js"), str(Path(scratch) / "test.js"),
                               str(ROOT / "app/static/actions.js"), str(ROOT / "app/static/graphics.js")], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_panel_offers_the_seven_templates_a_size_and_make_it():
    out = run("""
      out.looking = text(graphicsPanel());
      GFX.state = STATE; GFX.lib = LIB;
      const view = viewGraphics(); out.text = text(view);
      out.cards = all(find(view, n => n.attrs && n.attrs.role === "radiogroup"), n => n.tag === "b").map(text);
      out.make = !!button(view, "Make it") && !button(view, "Make it").attrs.disabled;
      out.sizes = all(view, n => n.attrs && n.attrs.role === "radiogroup").map(g => all(g, n => n.tag === "b").map(text)).slice(-1)[0];
      GFX.template = "captions"; out.captionSizes = all(graphicsPanel(), n => n.attrs && n.attrs.role === "radiogroup").map(g => all(g, n => n.tag === "b").map(text)).slice(-1)[0];
      out.clipChoices = all(graphicsPanel(), n => n.tag === "option").map(n => n.attrs.value);
      GFX.state = { ...STATE, ready: { ok: false, why: "HyperFrames is not installed: run `bash scripts/setup-graphics.sh`." }, spark: true };
      const off = graphicsPanel(); out.off = text(off); out.offMake = button(off, "Make it").attrs.disabled;
    """)
    assert out["looking"] == "Looking…"
    assert out["cards"] == ["Title", "Lower third", "Kinetic type", "Captions", "Slideshow", "Music video", "Explainer"]
    assert "A name and a role laid over a clip" in out["text"] and "Motion graphics" in out["text"]
    assert out["make"] is True
    assert out["sizes"] == ["1080p", "720p", "Vertical", "Square"]
    assert out["captionSizes"][0] == "Match the clip"
    assert "renders/a.mp4" in out["clipChoices"] and "uploads/one.png" not in out["clipChoices"]
    assert "HyperFrames is not installed" in out["off"] and "bash oneshot-setup-spark.sh --graphics" in out["off"] and out["offMake"] is True


def test_each_template_queues_a_graphics_job_with_its_settings():
    out = run("""
      GFX.state = STATE; GFX.lib = LIB; answers["/api/jobs"] = body => ({ id: "g1", title: "Title: x", kind: body.kind, status: "queued" });
      await gfxMake(); out.empty = toasts.pop();
      Object.assign(GFX.title, { title: "The Long Night", subtitle: "Part one", seconds: 5 }); await gfxMake();
      out.title = calls.filter(c => c[0] === "/api/jobs").pop()[1]; out.job = GFX.job;
      GFX.template = "slideshow"; GFX.slideshow.pictures = ["uploads/one.png"]; GFX.slideshow.voice = "voice/v1.wav"; GFX.size = "vertical"; await gfxMake();
      out.slides = calls.filter(c => c[0] === "/api/jobs").pop()[1].params;
      GFX.template = "explainer"; GFX.size = "1080p"; GFX.explainer.sections = [{ heading: "The idea", text: "Small words.", picture: "uploads/one.png" }, { heading: "", text: "", picture: "" }];
      await gfxMake(); out.explainer = calls.filter(c => c[0] === "/api/jobs").pop()[1].params;
      GFX.explainer.sections = [{ heading: "", text: "no heading", picture: "" }]; await gfxMake(); out.noHeading = toasts.pop();
      jobs = [{ id: "g1", kind: "graphics", status: "done", title: "Title: The Long Night", outputs: ["renders/g1.mp4"] }];
      GFX.template = "title"; const panel = graphicsPanel(); out.result = !!find(panel, n => n.tag === "video" && n.attrs.src === "/files/renders/g1.mp4");
      button(panel, "+ timeline").attrs.onclick(); out.timeline = calls.pop();
    """)
    assert out["empty"] == "Write the title first"
    assert out["title"] == {"kind": "graphics", "params": {"template": "title", "size": "1080p", "title": "The Long Night", "subtitle": "Part one", "seconds": 5}}
    assert out["job"] == "g1"
    assert out["slides"] == {"template": "slideshow", "size": "vertical", "pictures": ["uploads/one.png"], "seconds_each": 4, "title": "", "voice": "voice/v1.wav"}
    assert out["explainer"]["sections"] == [{"heading": "The idea", "text": "Small words.", "picture": "uploads/one.png"}]
    assert out["noHeading"] == "Give every section a heading"
    assert out["result"] is True and out["timeline"] == ["timeline", "renders/g1.mp4"]


def test_beats_come_from_the_beat_finder_or_the_tempo():
    out = run("""
      GFX.state = STATE; GFX.lib = LIB; GFX.template = "music-video"; const f = GFX["music-video"];
      f.song = "music/song.wav"; f.clips = ["renders/a.mp4", "renders/b.mp4"];
      answers["/api/music/beats"] = { beats: [0.5, 1.0, 1.5, 2.0], bpm: 120.4 }; await gfxBeats(f);
      out.found = [f.beats, f.bpm, toasts.pop()];
      await gfxMake(); out.withBeats = calls.filter(c => c[0] === "/api/jobs").pop()[1].params;
      answers["/api/music/beats"] = new Error("not found (404)"); await gfxBeats(f); out.fallback = [f.beats, toasts.pop()];
      await gfxMake(); out.withTempo = calls.filter(c => c[0] === "/api/jobs").pop()[1].params;
    """)
    assert out["found"][0] == [0.5, 1.0, 1.5, 2.0] and out["found"][1] == 120 and out["found"][2] == "4 beats found"
    assert out["withBeats"]["beats"] == [0.5, 1.0, 1.5, 2.0] and out["withBeats"]["clips"] == ["renders/a.mp4", "renders/b.mp4"]
    assert out["fallback"][0] is None and "the tempo below sets them" in out["fallback"][1]
    assert "beats" not in out["withTempo"] and out["withTempo"]["bpm"] == 120 and out["withTempo"]["every"] == 2


def test_the_library_offers_add_a_title_and_caption_this_clip_on_a_clip():
    out = run("""
      out.clip = actButtons({ kind: "video", path: "renders/a.mp4" }).map(n => n.kids[0]);
      out.picture = actButtons({ kind: "image", path: "uploads/one.png" }).map(text);
      ACT.state = {}; out.withActions = actButtons({ kind: "video", path: "renders/a.mp4" }).map(n => n.kids[0]);
      const caption = actButtons({ kind: "video", path: "renders/b.mp4" }).find(n => text(n).includes("Caption this clip")); caption.attrs.onclick();
      out.opened = [GFX.template, GFX.captions.video, GFX.size, went.pop()];
      gfxButtons({ kind: "video", path: "renders/a.mp4" })[0].attrs.onclick(); out.titled = [GFX.template, GFX["lower-third"].video, went.pop()];
    """)
    assert out["clip"] == ["Add a title", "Caption this clip"]
    assert out["picture"] == []
    assert out["withActions"][:2] == ["Upscale", "Remove a person"] and out["withActions"][-2:] == ["Add a title", "Caption this clip"]  # the actions first, then revise (when loaded), then graphics
    assert out["opened"] == ["captions", "renders/b.mp4", "match", "Graphics"]
    assert out["titled"] == ["lower-third", "renders/a.mp4", "Graphics"]
