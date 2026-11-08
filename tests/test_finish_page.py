"""The Finish panel's page code (app/static/finish.js), run in node with the page's helpers stood in for:
python3 -m pytest tests/test_finish_page.py. Skipped where node is not installed; no browser, no Tesseract."""
import json
import subprocess
import tempfile
from pathlib import Path

from test_shell_page import need_node

ROOT = Path(__file__).resolve().parent.parent
HARNESS = r"""
const vm = require("vm"), fs = require("fs");
globalThis.render = () => {}; globalThis.toasts = []; globalThis.toast = t => toasts.push(String(t));
globalThis.calls = []; globalThis.answers = {};
globalThis.api = async (path, body) => { calls.push([path, body]); const a = answers[path]; if (a instanceof Error) throw a; return a || {}; };
globalThis.jobs = []; globalThis.poll = async () => {}; globalThis.LIBPICK = new Set();
globalThis.location = { assign(u) { calls.push(["assign", u]); } }; globalThis.document = { querySelector() { return null; } };
const text = node => node == null || node === false ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join(" ") : [node.attrs && node.attrs.placeholder, node.attrs && node.attrs.title, text(node.kids)].filter(Boolean).join(" ");
globalThis.text = text;
globalThis.el = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids: kids.flat(), addEventListener() {} });
globalThis.put = (node, ...kids) => node.kids.push(...kids.flat()); globalThis.field = (label, node) => el("div", {}, label, node);
globalThis.bind = (obj, key, node) => { node.attrs.value = obj[key] ?? ""; return node; };
globalThis.select = (obj, key, options) => el("select", {}, Object.entries(options).map(([v, t]) => el("option", { value: v }, t)));
globalThis.choice = (obj, key, options) => el("div", { class: "choice" }, Object.entries(options).map(([v, [t, s]]) => el("button", { onclick: () => { obj[key] = v; } }, t, s)));
globalThis.file = p => "/files/" + p; globalThis.jobCard = job => el("div", {}, "job " + job.id + " " + job.status);
globalThis.timelineZip = id => "/api/export/timeline/" + id + ".zip";
const find = (node, test) => !node || typeof node !== "object" ? null : Array.isArray(node) ? node.map(n => find(n, test)).find(Boolean) || null : test(node) ? node : find(node.kids, test);
globalThis.find = find; globalThis.button = (root, words) => find(root, n => n.tag === "button" && text(n).includes(words));
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
vm.runInThisContext(`(async () => { const out = {}; ${fs.readFileSync(process.argv[3], "utf8")}; console.log(JSON.stringify(out)); })().catch(e => { console.error(e); process.exit(1); });`);
"""


def run(code: str) -> dict:
    need_node()
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "harness.js").write_text(HARNESS)
        (Path(scratch) / "test.js").write_text(code)
        done = subprocess.run(["node", str(Path(scratch) / "harness.js"), str(ROOT / "app/static/finish.js"), str(Path(scratch) / "test.js")],
                              capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_panel_says_where_it_runs_and_queues_the_finish():
    out = run("""FIN.state = { ok: true, where: "ssh:me@example.com", machine: "Studio Mac", version: "0.4.0", converter: { ok: true } };
      FIN.sounds = [{ path: "music/song.wav", kind: "audio", sound: "music", title: "Night theme" }, { path: "voice/v1.wav", kind: "audio", sound: "voice", title: "Opening line" }];
      const project = { id: "film1", name: "Night Drive", timeline: [{ path: "renders/a.mp4" }], soundtrack: { take: "s1" } };
      let panel = finishPanel(project); out.text = text(panel);
      button(panel, "Music forward").attrs.onclick(); FIN.o.title = "Night Drive"; FIN.o.voice = "voice/v1.wav"; FIN.o.fps = "30";
      answers["/api/jobs"] = { id: "j1", title: "Finish: Night Drive" };
      await button(finishPanel(project), "Finish the cut").attrs.onclick();
      out.sent = calls.find(c => c[0] === "/api/jobs")[1];
      jobs.push({ id: "j1", status: "done", outputs: ["exports/j1.mp4", "exports/j1.tsrct", "exports/j1.fcpxml", "exports/j1.edl"] });
      panel = finishPanel(project); out.done = text(panel);
      out.links = []; find(panel, n => { if (n.tag === "a") out.links.push(n.attrs.href); return false; });
      answers["/api/finish/j1/convert"] = { path: "exports/j1-premiere.zip", notes: ["a note"] };
      await button(panel, "Premiere").attrs.onclick(); out.assigned = calls.filter(c => c[0] === "assign").map(c => c[1]);
      out.convert = calls.find(c => c[0] === "/api/finish/j1/convert")[1];""")
    assert "Runs on Studio Mac over ssh" in out["text"] and "Dialogue first" in out["text"] and "Night theme" in out["text"]
    assert out["sent"]["kind"] == "finish" and out["sent"]["params"] == {"mix": "music", "size": "1080p", "fps": 30, "captions": False,
        "project": "film1", "title": {"text": "Night Drive", "subtitle": ""}, "voice": "voice/v1.wav"}
    assert "job j1 done" in out["done"] and "Download MP4" in out["done"] and "After Effects" in out["done"] and ".tsrct project" in out["done"]
    assert "/files/exports/j1.mp4" in out["links"] and "/api/export/timeline/film1.zip" in out["links"] and "/files/exports/j1.tsrct" in out["links"]
    assert out["convert"] == {"to": "premiere"} and out["assigned"] == ["/files/exports/j1-premiere.zip"]


def test_not_ready_says_the_setup_command_and_the_library_finishes_ticked_clips():
    out = run("""FIN.state = { ok: false, why: "Tesseract is not installed: run `bash scripts/setup-finish.sh`." }; FIN.sounds = [];
      let panel = finishPanel({ id: "film1", timeline: [{ path: "renders/a.mp4" }] }); out.text = text(panel);
      out.disabled = button(panel, "Finish the cut").attrs.disabled;
      button(finLibButton(), "Finish these").attrs.onclick(); out.empty = toasts.slice(-1)[0];
      LIBPICK.add("renders/b.mp4"); LIBPICK.add("scouts/s.png"); LIBPICK.add("renders/a.mp4");
      button(finLibButton(), "Finish these").attrs.onclick(); out.open = FIN.open;
      FIN.state = { ok: true, where: "here", version: "0.4.0", converter: { ok: false }, setup: "bash scripts/setup-finish.sh" };
      answers["/api/jobs"] = { id: "j2", title: "Finish: 2 clips" };
      panel = finLibPanel(); out.lib = text(panel); FIN.o.song = "none";
      await button(panel, "Finish the cut").attrs.onclick(); out.sent = calls.find(c => c[0] === "/api/jobs")[1];""")
    assert "setup-finish.sh" in out["text"] and out["disabled"] is True
    assert "Tick the clips" in out["empty"]
    assert out["open"] == {"clips": ["renders/b.mp4", "renders/a.mp4"]}     # clips only, in the order they were ticked
    assert "Runs on this computer" in out["lib"] and "need Tesseract Converter" in out["lib"] and "2 picked clips" in out["lib"]
    assert "keeps usage counts by default" in out["lib"] and "tsrct telemetry disable" in out["lib"] and "never changes" in out["lib"]
    assert out["sent"]["params"]["clips"] == [{"path": "renders/b.mp4"}, {"path": "renders/a.mp4"}] and out["sent"]["params"]["song"] is None
