"""The Nodes page (app/static/nodes.js), run in node with the page's helpers stood in for: python3 -m pytest
tests/test_nodes_page.py. Skipped where node is not installed."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import nodes  # noqa: E402

HARNESS = r"""
const vm = require("vm"), fs = require("fs");
globalThis.S = { image_models: [{ id: "qwen", label: "Qwen 2.1 Turbo" }, { id: "flux3k-fast", label: "FLUX3-K Fast" }] };
globalThis.tab = "Nodes"; globalThis.render = () => {}; globalThis.toasts = []; globalThis.toast = t => toasts.push(String(t));
globalThis.calls = []; globalThis.answers = {};
globalThis.api = async (path, body, method) => { calls.push([path, body === undefined ? null : JSON.parse(JSON.stringify(body)), method || (body === undefined ? "GET" : "POST")]);
  const a = answers[path]; if (a instanceof Error) throw a; if (typeof a === "function") return a(body); return a === undefined ? (path.endsWith("s") ? [] : {}) : a; };
globalThis.mmss = s => Math.round(s) + " s"; globalThis.file = p => "/files/" + p; globalThis.poll = async () => {};
globalThis.matchMedia = q => ({ matches: !!globalThis.PHONE }); globalThis.confirm = () => true; globalThis.prompt = () => null;
globalThis.setTimeout = (f) => 0; globalThis.clearTimeout = () => {};
const text = node => node == null || node === false ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join(" ") : [node.attrs && node.attrs.placeholder, text(node.kids)].filter(Boolean).join(" ");
globalThis.text = text;
const classes = n => String(n.attrs.class || "").split(/\s+/).filter(Boolean);
const matches = (n, sel) => n && typeof n === "object" && n.attrs && sel.split(",").some(one => one.trim().split(".").filter(Boolean).every(c => classes(n).includes(c)));
const all = (n, sel, out = []) => { for (const k of (n.kids || [])) if (k && typeof k === "object" && k.attrs) { if (matches(k, sel)) out.push(k); all(k, sel, out); } return out; };
globalThis.el = (tag, attrs = {}, ...kids) => { attrs = { ...attrs }; const n = { tag, attrs, kids: kids.flat().filter(k => k != null && k !== false), style: {}, parentNode: null, isConnected: true, value: attrs.value || "" };
  for (const k of n.kids) if (k && typeof k === "object") k.parentNode = n;
  Object.defineProperty(n, "className", { get: () => n.attrs.class || "", set: v => { n.attrs.class = v; } });
  n.dataset = new Proxy({}, { get: (_, k) => n.attrs["data-" + String(k).replace(/[A-Z]/g, c => "-" + c.toLowerCase())], set: (_, k, v) => { n.attrs["data-" + k] = v; return true; } });
  n.classList = { add: c => { if (!classes(n).includes(c)) n.attrs.class = (n.attrs.class ? n.attrs.class + " " : "") + c; }, remove: c => { n.attrs.class = classes(n).filter(x => x !== c).join(" "); },
    toggle: (c, on) => { if (on === undefined ? !classes(n).includes(c) : on) n.classList.add(c); else n.classList.remove(c); }, contains: c => classes(n).includes(c) };
  n.replaceChildren = (...k) => { n.kids = k.flat().filter(x => x != null && x !== false); for (const x of n.kids) if (x && typeof x === "object") x.parentNode = n; };
  n.append = (...k) => { for (const x of k.flat()) if (x != null && x !== false) { n.kids.push(x); if (typeof x === "object") x.parentNode = n; } };
  n.remove = () => { if (n.parentNode) n.parentNode.kids = n.parentNode.kids.filter(x => x !== n); };
  n.querySelector = sel => all(n, sel)[0] || null; n.querySelectorAll = sel => all(n, sel);
  n.closest = sel => { let x = n; while (x) { if (matches(x, sel.replace(/\[[^\]]+\]/g, "")) || (sel.includes("[data-edge]") && x.attrs && x.attrs["data-edge"] != null) ||
    sel.split(",").some(s => x.tag === s.trim())) return x; x = x.parentNode; } return null; };
  n.contains = x => { while (x) { if (x === n) return true; x = x.parentNode; } return false; };
  n.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 700 }); n.focus = () => {};
  return n; };
globalThis.document = { createElementNS: (ns, tag) => { const n = el(tag, {}); n.setAttribute = (k, v) => { n.attrs[k] = String(v); }; return n; }, elementFromPoint: () => globalThis.UNDER || null };
globalThis.window = { addEventListener() {} };
const find = (node, test) => !node || typeof node !== "object" ? null : Array.isArray(node) ? node.map(n => find(n, test)).find(Boolean) || null : test(node) ? node : find(node.kids, test);
globalThis.find = find; globalThis.button = (root, words) => find(root, n => n.tag === "button" && text(n).includes(words));
globalThis.settle = () => new Promise(r => setImmediate(r));
vm.runInThisContext(fs.readFileSync(process.argv[3], "utf8"));
vm.runInThisContext(`(async () => { const out = {}; ${fs.readFileSync(process.argv[2], "utf8")}; console.log(JSON.stringify(out)); })().catch(e => { console.error(e); process.exit(1); });`);
"""


def run(code: str) -> dict:
    if not shutil.which("node"):
        return None
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "harness.js").write_text(HARNESS)
        (Path(scratch) / "test.js").write_text(code)
        done = subprocess.run(["node", str(Path(scratch) / "harness.js"), str(Path(scratch) / "test.js"), str(ROOT / "app/static/nodes.js")],
                              capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_page_knows_the_same_cards_as_the_server():
    out = run("out.types = ND_TYPES; out.ready = window.NODES_READY;")
    if out is None:
        return
    assert out["ready"] is True
    page = {k: {"inputs": v["inputs"], "outputs": v["outputs"], "params": v["params"]} for k, v in out["types"].items()}
    server = {k: {"inputs": v["inputs"], "outputs": v["outputs"], "params": v["params"]} for k, v in nodes.NODE_TYPES.items()}
    assert page == json.loads(json.dumps(server))
    assert {k: v["label"] for k, v in out["types"].items()} == {k: v["label"] for k, v in nodes.NODE_TYPES.items()}


def test_add_connect_and_serialize():
    out = run("""ndLoad(ndBlank("Test"));
      const t = ndAdd("text", 10, 20), p = ndAdd("picture", 300, 20), c = ndAdd("clip", 600, 20), v = ndAdd("voice", 300, 300);
      t.params.text = "a lighthouse";
      out.ok = ndConnect(t.id, "text", p.id, "prompt"); out.ok2 = ndConnect(p.id, "image", c.id, "first_frame");
      out.wrong = ndConnect(v.id, "audio", c.id, "prompt");
      out.self = ndConnect(c.id, "video", c.id, "refs");
      const e = ndAdd("edit", 0, 0), f = ndAdd("edit", 0, 0); ndConnect(e.id, "image", f.id, "image"); out.loop = ndCanConnect(f.id, "image", e.id, "image");
      ndConnect(t.id, "text", c.id, "prompt"); ndConnect(t.id, "text", v.id, "text"); out.again = ndConnect(t.id, "text", c.id, "prompt");
      const u = ndAdd("text", 0, 100); ndConnect(u.id, "text", c.id, "prompt");  // a single input takes the newest link
      out.prompts = ND.graph.edges.filter(x => x.to === c.id && x.in === "prompt").map(x => x.from); out.u = u.id;
      ndConnect(v.id, "audio", c.id, "refs");
      const s = ndSerialize(); out.graph = s; out.ids = ND.graph.nodes.map(n => n.id);
      ND.sel = { node: p.id }; ndRemove(); out.afterRemove = ND.graph.edges.filter(x => x.from === p.id || x.to === p.id).length; out.dirty = ND.dirty;""")
    if out is None:
        return
    assert out["ok"] == "" and out["ok2"] == ""
    assert out["wrong"] == "Voice gives audio, and the prompt of Clip takes text"
    assert out["self"] == "a card cannot feed itself"
    assert out["loop"] == "that link would make the graph loop"
    assert out["again"] == "they are joined already"
    assert out["prompts"] == [out["u"]]
    assert len(set(out["ids"])) == len(out["ids"])
    graph = out["graph"]
    assert graph["name"] == "Test" and "id" not in graph and set(graph["view"]) == {"x", "y", "k"}
    assert {"from": out["ids"][1], "out": "image", "to": out["ids"][2], "in": "first_frame"} in graph["edges"]
    assert graph["nodes"][0] == {"id": out["ids"][0], "type": "text", "x": 10, "y": 20, "title": "", "params": {"text": "a lighthouse"}}
    # what the page sends is a graph the server reads as it is
    tidy = nodes.tidy(graph)
    assert [n["params"] for n in tidy["nodes"]] == [n["params"] for n in graph["nodes"]] and tidy["edges"] == graph["edges"]
    assert out["afterRemove"] == 0 and out["dirty"] is True


def test_a_reference_card_gives_the_kind_of_file_it_holds():
    out = run("""ndLoad(ndBlank()); const r = ndAdd("reference", 0, 0), c = ndAdd("clip", 300, 0), t = ndAdd("text", 0, 200);
      out.first = ndConnect(r.id, "file", c.id, "first_frame");
      r.params.kind = "audio"; ndRetype(r); out.left = ND.graph.edges.length;
      out.now = ndConnect(r.id, "file", c.id, "first_frame"); out.refs = ndConnect(r.id, "file", c.id, "refs");""")
    if out is None:
        return
    assert out["first"] == "" and out["left"] == 0
    assert out["now"] == "Reference gives audio, and the first frame of Clip takes image" and out["refs"] == ""


def test_the_view_draws_cards_ports_and_links_and_the_palette_filters():
    out = run("""answers["/api/nodes/graphs"] = { graphs: [{ id: "g1", name: "Mine", nodes: 2 }], templates: [{ id: "still-clip", name: "Still → Clip", line: "x", nodes: [], edges: [] }] };
      ndLoad(ndBlank()); const view = viewNodes(); await settle();
      out.empty = text(view).includes("An empty canvas") && text(view).includes("Still → Clip");
      const t = ndAdd("text", 0, 0), c = ndAdd("clip", 400, 0); ndConnect(t.id, "text", c.id, "prompt"); ndPaint();
      out.cards = ND.layer.querySelectorAll("nd-node").length; out.ports = ND.layer.querySelectorAll("nd-port").length;
      out.wires = ND.svg.kids.filter(k => String(k.attrs.class).includes("nd-wire")).length;
      out.wireClass = ND.svg.kids.find(k => String(k.attrs.class).includes("nd-wire")).attrs.class;
      out.d = ND.svg.kids[0].attrs.d; out.at = [ndPortAt(t, "out", "text"), ndPortAt(c, "in", "prompt")];
      out.bar = text(ND.root.querySelector("nd-bar")); out.open = !!find(view, n => n.tag === "optgroup" && n.attrs.label === "Saved");
      ND.palette = { screen: [0, 0], world: [0, 0], link: { from: t.id, out: "text", type: "text" }, q: "" }; out.forText = ndPaletteTypes().map(x => x[0]);
      ND.palette.link = { type: "audio" }; out.forAudio = ndPaletteTypes().map(x => x[0]);
      ND.palette = { screen: [0, 0], world: [500, 300], link: null, q: "music" }; out.search = ndPaletteTypes().map(x => x[0]);
      ndPick("song"); out.added = ND.graph.nodes[ND.graph.nodes.length - 1].type;""")
    if out is None:
        return
    assert out["empty"] and out["open"]
    assert out["cards"] == 2 and out["ports"] == 5  # the text's output; the clip's prompt, first frame and refs, and its video
    assert out["wires"] == 1 and "t-text" in out["wireClass"]
    (x1, y1), (x2, y2) = out["at"]
    assert (x1, y1) == (248, 1 + 38 + 13) and (x2, y2) == (400, 1 + 38 + 13)
    assert out["d"].startswith("M248 52 C") and out["d"].endswith("400 52")
    assert "Run" in out["bar"] and "Unsaved" in out["bar"]
    assert out["forText"] == ["picture", "edit", "clip", "voice", "song"]
    assert out["forAudio"] == ["clip", "timeline"]
    assert out["search"] == ["song"] and out["added"] == "song"


def test_run_checks_then_asks_the_memory_guard_then_queues():
    out = run("""ndLoad(ndBlank("Run me")); const t = ndAdd("text", 0, 0), c = ndAdd("clip", 300, 0); t.params.text = "x"; ndConnect(t.id, "text", c.id, "prompt");
      viewNodes(); calls.length = 0;
      answers["/api/nodes/check"] = { ok: true, problems: [], order: [t.id, c.id] };
      answers["/api/estimate"] = { seconds: 90, memory: { fits: false, reason: "this clip needs 96 GB and 70 are free" } };
      await ndRun(); out.refused = toasts.pop(); out.calls1 = calls.map(c => c[0]); out.sel = ND.sel;
      out.card = text(ND.cards[c.id]);
      answers["/api/estimate"] = { seconds: 90, memory: { fits: true } };
      answers["/api/nodes/run"] = b => ({ id: "r1", status: "running", order: [t.id, c.id], current: c.id, nodes: { [t.id]: { status: "done", outputs: { text: "x" } }, [c.id]: { status: "running", job: "j1", job_status: "running", progress: { stage: "picture", at: 3, of: 8 } } } });
      calls.length = 0; await ndRun(); out.calls2 = calls.map(c => c[0]); out.sent = calls.find(c => c[0] === "/api/nodes/run")[1];
      out.bar = text(ND.root.querySelector("nd-bar")); out.running = text(ND.cards[c.id]);
      answers["/api/nodes/runs/r1/cancel"] = { ...ND.run, status: "cancelled" }; await ndCancel(); out.cancel = calls.pop();
      answers["/api/nodes/check"] = { ok: false, problems: [{ node: c.id, message: "Clip needs its prompt: join a card that gives text" }] };
      calls.length = 0; await ndRun(); out.calls3 = calls.map(c => c[0]); out.problem = toasts.pop(); out.bad = ND.cards[c.id].attrs.class;""")
    if out is None:
        return
    assert out["calls1"] == ["/api/nodes/check", "/api/estimate"] and "this clip needs 96 GB" in out["refused"]
    assert "Does not fit" in out["card"]
    assert out["calls2"] == ["/api/nodes/check", "/api/estimate", "/api/nodes/run"]
    assert out["sent"]["graph"]["name"] == "Run me" and len(out["sent"]["graph"]["nodes"]) == 2
    assert "Running 2 of 2" in out["bar"] and "Cancel run" in out["bar"]
    assert "Rendering · picture 3/8" in out["running"]
    assert out["cancel"][0] == "/api/nodes/runs/r1/cancel"
    assert out["calls3"] == ["/api/nodes/check"] and out["problem"].startswith("Clip needs its prompt") and " bad" in out["bad"]


def test_save_open_and_a_template_load():
    out = run("""answers["/api/nodes/graphs"] = { graphs: [], templates: [{ id: "still-clip", name: "Still → Clip", line: "", nodes: [{ id: "t1", type: "text", x: 1, y: 2, params: { text: "a" } }, { id: "c1", type: "clip", x: 300, y: 0, params: {} }], edges: [{ from: "t1", out: "text", to: "c1", in: "prompt" }] }] };
      ndLoad(ndBlank()); viewNodes(); await settle();
      await ndOpen("t:still-clip"); out.loaded = ndSerialize(); out.dirty = ND.dirty;
      answers["/api/nodes/graphs"] = b => b ? { ...b, id: "g9", name: b.name } : { graphs: [], templates: [] };
      await ndSave(); out.id = ND.graph.id; out.saved = toasts.pop(); out.post = calls.find(c => c[0] === "/api/nodes/graphs" && c[1]);
      const n = ndAdd("note", 0, 0); out.next = n.id;
      answers["/api/nodes/graphs/g9"] = { id: "g9", name: "Back", nodes: [{ id: "n7", type: "song", x: 0, y: 0, params: { seconds: 20 } }], edges: [] };
      await ndOpen("g9"); out.reopened = ndSerialize(); out.seq = ndAdd("text", 0, 0).id;""")
    if out is None:
        return
    assert out["dirty"] is True and out["loaded"]["name"] == "Still → Clip" and "id" not in out["loaded"]
    assert out["loaded"]["nodes"][1]["params"] == nodes.NODE_TYPES["clip"]["params"]  # the card's defaults filled in
    assert out["id"] == "g9" and out["saved"] == "Saved Still → Clip" and out["post"][1]["edges"] == [{"from": "t1", "out": "text", "to": "c1", "in": "prompt"}]
    assert out["next"] == "n2"
    assert out["reopened"]["id"] == "g9" and out["reopened"]["nodes"][0]["params"] == {"seconds": 20, "seed": 1} and out["seq"] == "n8"


def test_a_phone_sees_the_graph_read_only():
    out = run("""globalThis.PHONE = true; ndLoad({ id: "g1", name: "Mine", nodes: [{ id: "t1", type: "text", x: 0, y: 0, params: { text: "a" } }], edges: [] });
      const view = viewNodes(); out.text = text(view); out.run = !!button(view, "Run"); out.save = !!button(view, "Save");
      out.disabled = find(view, n => n.tag === "textarea").attrs.disabled; calls.length = 0; await ndRun(); await ndSave(); out.calls = calls.length;""")
    if out is None:
        return
    assert "Best on a desktop" in out["text"] and not out["run"] and not out["save"]
    assert out["disabled"] is True and out["calls"] == 0
