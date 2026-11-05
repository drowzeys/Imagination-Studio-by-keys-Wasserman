"""Quick Edit's page code (app/static/quick.js), run in node with the page's helpers stood in for:
python3 -m pytest tests/test_quick_page.py. Skipped where node is not installed; no browser, no model."""
import json
import subprocess
import tempfile
from pathlib import Path

from test_shell_page import need_node

ROOT = Path(__file__).resolve().parent.parent
HARNESS = r"""
const vm = require("vm"), fs = require("fs");
globalThis.tab = "Quick Edit"; globalThis.renders = 0; globalThis.render = () => { renders++; };
globalThis.toasts = []; globalThis.toast = t => toasts.push(String(t));
globalThis.calls = []; globalThis.answers = {};
globalThis.api = async (path, body) => { calls.push([path, body]); const a = answers[path]; if (a instanceof Error) throw a; return a || {}; };
globalThis.jobs = []; globalThis.poll = async () => {}; globalThis.C = { step: 0, goes: "image", start: "text" };
globalThis.go = t => { tab = t; }; globalThis.changed = () => {}; globalThis.window = { scrollTo() {} };
globalThis.listeners = {}; globalThis.document = { addEventListener(k, f) { listeners[k] = f; }, querySelector() { return null; } };
// fetch is the upload: it answers with the path the server would give, by the file's name
globalThis.uploads = []; globalThis.fetch = async (path, opts) => { uploads.push([path, opts.body.name]); return { json: async () => ({ path: "uploads/" + opts.body.name }) }; };
globalThis.FormData = class { append(k, f, name) { this.name = name || f.name; } };
const text = node => node == null || node === false ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join(" ") : [node.attrs && node.attrs.placeholder, node.attrs && node.attrs.title, text(node.kids)].filter(Boolean).join(" ");
globalThis.text = text;
globalThis.el = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids: kids.flat(), classList: { add() {}, remove() {} }, click() {} });
globalThis.put = (node, ...kids) => node.kids.push(...kids.flat()); globalThis.bind = (obj, key, node) => { node.attrs.value = obj[key] ?? ""; return node; };
globalThis.file = p => "/files/" + p; globalThis.media = p => el("img", { src: p }); globalThis.jobCard = job => el("div", {}, "job " + job.id + " " + job.status);
globalThis.mark = (id, label) => el("span", { class: "mk" }, label === "" ? id : label || id);
globalThis.pageHead = (eyebrow, title, line) => el("div", { class: "pagehead" }, eyebrow, title, line);
const find = (node, test) => !node || typeof node !== "object" ? null : Array.isArray(node) ? node.map(n => find(n, test)).find(Boolean) || null : test(node) ? node : find(node.kids, test);
globalThis.find = find; globalThis.button = (root, words) => find(root, n => n.tag === "button" && text(n).includes(words));
globalThis.settle = () => new Promise(r => setTimeout(r, 0));
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
vm.runInThisContext(`(async () => { const out = {}; ${fs.readFileSync(process.argv[3], "utf8")}; console.log(JSON.stringify(out)); })().catch(e => { console.error(e); process.exit(1); });`);
"""
STATE = {"ready": True, "why": "", "models": [{"id": "qwen", "label": "Qwen 2.1 Turbo", "note": "8 steps"}, {"id": "flux3k-fast", "label": "FLUX3-K Fast", "note": "16 steps"},
                                                {"id": "flux3k-full", "label": "FLUX3-K Full", "note": "50 steps"}], "qwen": True, "base": True, "limit": 12, "pictures": 10}


def run(code: str) -> dict:
    need_node()
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "harness.js").write_text(HARNESS)
        (Path(scratch) / "test.js").write_text(code)
        done = subprocess.run(["node", str(Path(scratch) / "harness.js"), str(ROOT / "app/static/quick.js"), str(Path(scratch) / "test.js")],
                              capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_page_loads_the_state_and_offers_the_three_models():
    out = run(f"""answers["/api/edit/state"] = {json.dumps(STATE)};
      const first = viewQuick(); out.looking = text(first); await settle(); await settle();
      const page = viewQuick(); out.page = text(page);
      out.models = find(page, n => n.attrs && n.attrs.id === "quickmodel").kids.map(b => [text(b), b.attrs.class]);
      button(page, "FLUX3-K Full").attrs.onclick(); out.picked = qkModel();
      out.wants = qkWants(); out.calls = calls.map(c => c[0]);""")
    assert "Looking at what is installed" in out["looking"] and out["calls"] == ["/api/edit/state"]
    assert "Paste a picture" in out["page"] and "What do you want to change" in out["page"]
    labels = ["Qwen 2.1 Turbo", "FLUX3-K Fast", "FLUX3-K Full"]
    assert [[w for w in labels if w in m[0]] for m in out["models"]] == [[w] for w in labels]  # the three, in the server's order
    assert [("flux" in m[0], "qwen" in m[0]) for m in out["models"]] == [(False, True), (True, False), (True, False)]  # each with its maker's mark
    assert all("steps" in m[0] for m in out["models"])
    assert [m[1] for m in out["models"]] == ["on", "", ""]  # Qwen, the first offered, is the default
    assert out["picked"] == "flux3k-full"
    assert out["wants"] == ["a picture", "what changes"]


def test_a_pasted_picture_is_uploaded_and_a_pasted_clip_gives_its_first_frame():
    out = run(f"""answers["/api/edit/state"] = {json.dumps(STATE)}; answers["/api/frame"] = {{ path: "uploads/clip_first.png" }};
      QK.state = {json.dumps(STATE)};
      // text on the clipboard is left to the textarea; a picture is taken, even while another tab's paste would not be
      let stopped = 0; const paste = files => ({{ clipboardData: {{ files }}, preventDefault() {{ stopped++; }} }});
      listeners.paste(paste([])); out.textLeft = [stopped, QK.image];
      tab = "Create"; listeners.paste(paste([{{ type: "image/png", name: "shot.png" }}])); out.otherTab = [stopped, QK.image]; tab = "Quick Edit";
      listeners.paste(paste([{{ type: "image/png", name: "" }}])); await settle(); await settle(); out.pasted = [stopped, QK.image, uploads[0]];
      listeners.paste(paste([{{ type: "video/mp4", name: "take.mp4" }}])); await settle(); await settle(); await settle();
      out.clip = [QK.image, calls.find(c => c[0] === "/api/frame")[1]];
      const bad = {{ type: "text/plain", name: "notes.txt" }}; await qkTake(bad); out.refused = toasts[toasts.length - 1];""")
    assert out["textLeft"] == [0, None] and out["otherTab"] == [0, None]
    assert out["pasted"] == [1, "uploads/pasted.png", ["/api/upload", "pasted.png"]]  # a nameless clipboard picture gets a name
    assert out["clip"] == ["uploads/clip_first.png", {"path": "uploads/take.mp4", "at": "first"}]
    assert "Paste or drop a picture" in out["refused"]


def test_go_queues_one_edit_job_with_the_models_own_settings_and_a_new_seed_each_turn():
    out = run(f"""answers["/api/edit/state"] = {json.dumps(STATE)}; QK.state = {json.dumps(STATE)}; QK.image = "uploads/a.png";
      let n = 0; answers["/api/jobs"] = {{ id: "j1" }};
      await qkGo(); out.noWords = toasts[toasts.length - 1];
      QK.prompt = "  make the sky a sunset  "; QK.model = "flux3k-fast";
      const first = qkGo(), second = qkGo(); await first; await second;  // two presses before the queue answers: one job
      const sent = calls.filter(c => c[0] === "/api/jobs"); out.sent = sent.length; out.body = sent[0][1];
      out.after = [QK.prompt, QK.busy, QK.turns.length, QK.image];
      answers["/api/jobs"] = {{ id: "j2" }}; QK.prompt = "now at night"; await qkGo();
      out.seeds = QK.turns.map(t => t.seed); out.order = QK.turns.map(t => t.job);
      jobs = [{{ id: "j1", status: "done", outputs: ["scouts/j1/edit.png"] }}, {{ id: "j2", status: "running" }}];
      const page = viewQuick(); out.page = text(page);
      button(page, "Edit this result").attrs.onclick(); out.chained = QK.image;
      jobs = [{{ id: "j1", status: "failed", error: "ComfyUI said no" }}]; out.failed = text(viewQuick());""")
    assert out["noWords"] == "Needs what changes"
    assert out["sent"] == 1
    body = out["body"]
    assert body["kind"] == "edit" and body["title"] == "Quick edit: make the sky a sunset"
    assert body["params"]["image"] == "uploads/a.png" and body["params"]["prompt"] == "make the sky a sunset"
    assert body["params"]["image_model"] == "flux3k-fast" and body["params"]["size"] == "source"
    assert set(body["params"]) == {"image", "prompt", "seed", "size", "image_model"}  # nothing else to set: the model's own settings
    assert 1 <= body["params"]["seed"] < 2 ** 31
    assert out["after"] == ["", False, 1, "uploads/a.png"]  # the box empties, the picture stays for the next turn
    assert len(set(out["seeds"])) == 2 and out["order"] == ["j2", "j1"]  # the newest turn first
    assert "job j2 running" in out["page"] and "Edit this result" in out["page"] and "Download" in out["page"]
    assert out["chained"] == "scouts/j1/edit.png"
    assert "It did not come back" in out["failed"] and "ComfyUI said no" in out["failed"]


def test_without_an_editing_model_the_page_says_so_and_queues_nothing():
    out = run("""answers["/api/edit/state"] = { ready: false, why: "the installed mflux has no edit pipeline", models: [] };
      viewQuick(); await settle(); await settle(); out.page = text(viewQuick());
      QK.image = "uploads/a.png"; QK.prompt = "brighter"; await qkGo(); out.said = toasts[toasts.length - 1]; out.jobs = calls.filter(c => c[0] === "/api/jobs").length;""")
    assert "not set up on this machine" in out["page"] and "no edit pipeline" in out["page"]
    assert out["said"].startswith("Needs a picture model that edits") and out["jobs"] == 0
