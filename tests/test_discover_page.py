"""Machine → Models & weights (app/static/discover.js), run in node with the page's helpers stood in for:
python3 -m pytest tests/test_discover_page.py. Skipped where node is not installed."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = r"""
const vm = require("vm"), fs = require("fs");
globalThis.window = {}; globalThis.document = { head: null, getElementById() { return null; } };
globalThis.S = { home: "/u/TensorFoldStudio" };
globalThis.relHome = s => typeof s === "string" ? s.split(S.home + "/").join("") : s;
globalThis.toasts = []; globalThis.toast = t => toasts.push(String(t));
globalThis.calls = []; globalThis.answers = {};
globalThis.api = async (path, body) => { calls.push([path, body]); const a = answers[path]; if (a instanceof Error) throw a; return a || {}; };
const text = node => node == null || node === false ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join(" ") : [node.attrs && node.attrs.title, text(node.kids)].filter(Boolean).join(" ");
globalThis.text = text;
globalThis.el = (tag, attrs = {}, ...kids) => { const n = { tag, attrs, kids: kids.flat(Infinity) }; n.replaceChildren = (...k) => { n.kids = k.flat(Infinity); }; return n; };
const find = (node, test) => !node || typeof node !== "object" ? null : Array.isArray(node) ? node.map(n => find(n, test)).find(Boolean) || null : test(node) ? node : find(node.kids, test);
const all = (node, test, out = []) => { if (!node || typeof node !== "object") return out; if (Array.isArray(node)) { node.forEach(n => all(n, test, out)); return out; } if (test(node)) out.push(node); all(node.kids, test, out); return out; };
globalThis.find = find; globalThis.all = all; globalThis.button = (root, words) => find(root, n => n.tag === "button" && text(n).includes(words));
globalThis.tick = () => new Promise(r => setTimeout(r, 0));
vm.runInThisContext(fs.readFileSync(process.argv[3], "utf8"));
vm.runInThisContext(`(async () => { const out = {}; ${fs.readFileSync(process.argv[2], "utf8")}; console.log(JSON.stringify(out)); })().catch(e => { console.error(e); process.exit(1); });`);
"""

STATE = {"scanning": False, "platform": "spark", "note": "Nothing is moved, copied or downloaded: a file found elsewhere is linked.",
         "last_scan": {"at": 1760000000, "seconds": 41.2, "complete": True, "dirs": 5120, "skipped_mounts": ["/mnt/backup"]},
         "needs": [
             {"id": "drama3-checkpoint", "label": "Fish Audio Drama 3 checkpoint", "engine": "Voice (Drama 3)", "optional": False, "state": "found",
              "expected": "~/models/drama-3", "env": "DRAMA3_CUDA_MODEL", "download": "hf download fishaudio/drama-3", "link": None,
              "candidates": [{"path": "/u/models/fishaudio/drama-3", "shown": "~/models/fishaudio/drama-3", "verified": True, "proof": "folder members"}],
              "conversions": []},
             {"id": "drama3-codec", "label": "Drama 3 codec", "engine": "Voice (Drama 3)", "optional": False, "state": "needs-conversion",
              "expected": "~/.local/opt/x/drama3/codec-v1.safetensors", "env": "DRAMA3_CUDA_CODEC", "download": "", "link": None, "candidates": [],
              "conversions": [{"path": "/u/models/fishaudio/drama-3/codec.pth", "shown": "~/models/fishaudio/drama-3/codec.pth",
                               "convert": "python3 codec_convert.py A B", "note": "the checkpoint's own codec.pth"}]},
             {"id": "fasth3", "label": "FastH3 8-step transformer (bf16)", "engine": "Video (FastH3)", "optional": False, "state": "expected",
              "expected": "~/spark-studio-models/FastH3-Comfy/x.safetensors", "env": None, "download": "", "candidates": [], "conversions": [],
              "link": {"path": "/u/hf/x.safetensors", "shown": "~/hf/x.safetensors", "how": "symlink", "link_shown": "~/spark-studio-models/FastH3-Comfy/x.safetensors"}},
             {"id": "flux3k-full", "label": "FLUX3-K Full (50 steps)", "engine": "Picture (FLUX3-K)", "optional": True, "state": "missing",
              "expected": "~/spark-studio-models/FLUX3-K-Comfy/y.safetensors", "env": None, "download": "oneshot-setup-spark.sh --flux3",
              "link": None, "candidates": [], "conversions": []},
         ]}


def run(code: str) -> dict:
    if not shutil.which("node"):
        return None
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "harness.js").write_text(HARNESS)
        (Path(scratch) / "test.js").write_text(code)
        done = subprocess.run(["node", str(Path(scratch) / "harness.js"), str(Path(scratch) / "test.js"), str(ROOT / "app/static/discover.js")],
                              capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_panel_groups_needs_says_their_state_and_links_on_use_it():
    out = run(f"""answers["/api/discover"] = {json.dumps(STATE)};
      out.ready = window.DISCOVER_READY;
      const box = viewDiscover(); await tick(); await tick();
      out.text = text(box);
      out.groups = all(box, n => n.tag === "h3").map(text);
      out.rows = all(box, n => n.attrs && n.attrs["data-need"]).map(n => n.attrs["data-need"]);
      answers["/api/discover/use"] = {{ ...{json.dumps(STATE)}, needs: [] }};
      button(box, "Use it").attrs.onclick(); await tick(); await tick();
      out.used = calls.find(c => c[0] === "/api/discover/use");
      button(box, "Scan again").attrs.onclick(); await tick();
      out.scan = calls.some(c => c[0] === "/api/discover/scan");""")
    if out is None:
        return
    assert out["ready"] is True
    assert out["groups"] == ["Voice (Drama 3)", "Video (FastH3)"]
    assert out["rows"] == ["drama3-checkpoint", "drama3-codec", "fasth3"]  # an optional model not installed is hidden
    t = out["text"]
    assert "found elsewhere" in t and "found at ~/models/fishaudio/drama-3" in t and "expected at ~/models/drama-3 (DRAMA3_CUDA_MODEL)" in t
    assert "needs converting" in t and "python3 codec_convert.py A B" in t
    assert "linked to ~/hf/x.safetensors" in t and "Unlink" in t and "in place" in t
    assert "Nothing is moved" in t and "Last scan" in t and "5120 folders" in t and "/mnt/backup" in t
    assert out["used"] == ["/api/discover/use", {"need": "drama3-checkpoint", "path": "/u/models/fishaudio/drama-3"}]
    assert out["scan"] is True


def test_unlink_and_the_optional_toggle():
    out = run(f"""answers["/api/discover"] = {json.dumps(STATE)};
      const box = viewDiscover(); await tick(); await tick();
      button(box, "Unlink").attrs.onclick(); await tick();
      out.unlinked = calls.find(c => c[0] === "/api/discover/unlink");
      const box2 = viewDiscover(); await tick(); await tick();
      find(box2, n => n.tag === "input").attrs.onchange({{ target: {{ checked: true }} }});
      out.rows = all(box2, n => n.attrs && n.attrs["data-need"]).map(n => n.attrs["data-need"]);
      out.text = text(box2);
      answers["/api/discover"] = new Error("Not Found");
      DSC.data = null; const box3 = viewDiscover(); await tick(); await tick(); out.old = text(box3);""")
    if out is None:
        return
    assert out["unlinked"] == ["/api/discover/unlink", {"need": "fasth3"}]
    assert "flux3k-full" in out["rows"] and "not installed (optional)" in out["text"] and "Download: oneshot-setup-spark.sh --flux3" in out["text"]
    assert "newer Studio server" in out["old"]
