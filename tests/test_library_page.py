"""The Library (app/static/index.html, viewLibrary): a page of tiles at a time, players that fetch their file only when
the tile comes near the screen, and a job poll that leaves the Library alone until a take is new or gone. Run in node
with the page's helpers stood in for (as tests/test_shell_page.py): python3 -m pytest tests/test_library_page.py.
Skipped where node is not installed."""
from pathlib import Path

from test_shell_page import FILES, run as shell_run
import test_shell_page

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "app/static/index.html").read_text(encoding="utf-8")


def block(start: str) -> str:
    """The source of one top-level function of index.html, from `start` to its closing brace."""
    at = HTML.index(start)
    depth = 0
    for i in range(HTML.index("{", at), len(HTML)):
        depth += 1 if HTML[i] == "{" else -1 if HTML[i] == "}" else 0
        if depth == 0:
            return HTML[at:i + 1]
    raise AssertionError(start + " did not close")


# the Library's own code: its state and helpers, then viewLibrary; and the job poll with the marks it keeps
LIBRARY = HTML[HTML.index("/* ---------- Library ---------- */"):HTML.index("function viewLibrary(")] + block("function viewLibrary(")
POLL = HTML[HTML.index("let lastSig"):].split("\n", 1)[0] + "\n" + block("async function poll(")

SETUP = r"""
// a node with what the Library reads off a real element: its style, its data-* attributes, its children, its listeners
const plain = el;
globalThis.el = (tag, attrs = {}, ...kids) => { const n = plain(tag, attrs, ...kids), style = {}, dataset = {}, on = {};
  for (const part of String(attrs.style || "").split(";")) { const [k, v] = part.split(":"); if (k && v) style[k.trim()] = v.trim(); }
  for (const [k, v] of Object.entries(attrs)) if (k.startsWith("data-")) dataset[k.slice(5).replace(/-(.)/g, (_, c) => c.toUpperCase())] = String(v);
  Object.assign(n, { style, dataset, on, addEventListener(type, fn) { on[type] = fn; } });
  Object.defineProperty(n, "children", { get: () => n.kids.filter(k => k && typeof k === "object") }); return n; };
// the watcher: the test says which players have come near the screen
globalThis.WATCHED = []; globalThis.IntersectionObserver = class { constructor(fn) { this.fn = fn; this.on = new Set(); WATCHED.push(this); }
  observe(n) { this.on.add(n); } unobserve(n) { this.on.delete(n); } disconnect() { this.on.clear(); this.gone = true; } };
globalThis.seen = nodes => { const io = WATCHED[WATCHED.length - 1]; io.fn(nodes.filter(n => io.on.has(n)).map(target => ({ target, isIntersecting: true }))); };
globalThis.LIBPICK = new Set(); globalThis.LORAS = []; globalThis.ACT = { state: {}, open: null }; globalThis.actButtons = () => [];
globalThis.actPanel = () => null; CAP.map = {};
// the page's own helpers the Library's side panel reads (the selected take's facts and buttons)
for (const [k, v] of Object.entries({ sizeText: b => b + " B", icon: () => el("span"), releaseMedia: () => {}, capButton: () => null, jobs: [] })) if (typeof globalThis[k] === "undefined") globalThis[k] = v;
// 150 takes, newest first: clips and stills in turn
globalThis.TAKES = Array.from({ length: 150 }, (_, i) => ({ path: (i % 2 ? "scouts/s" : "renders/r") + i + (i % 2 ? ".png" : ".mp4"), kind: i % 2 ? "image" : "video", modified: 1000 - i }));
answers["/api/library"] = TAKES; answers["/api/trash"] = { files: 0 };
const tick = () => new Promise(r => setTimeout(r, 20));
const draw = async () => { const box = viewLibrary(); await tick(); return box; };
// the Library's grid of tiles (the caption search keeps a grid of its own, hidden until it is searching)
const grid = box => find(box, n => n.attrs && n.attrs.class === "lib-grid");
const shown = box => grid(box).children.filter(t => t.style.display !== "none").map(t => t.dataset.path);
const more = box => find(box, n => n.tag === "button" && /^Show \d+ more/.test(n.textContent || ""));
const moreText = box => { const b = more(box); return b.style.display === "none" ? null : b.textContent; };
const search = box => find(box, n => n.attrs && n.attrs["aria-label"] === "Find in the Library");
// the grid's players (the side panel's preview of the selected take is one player, fetched at once)
const players = box => { const out = []; const walk = n => { if (!n || typeof n !== "object") return; if (Array.isArray(n)) return n.forEach(walk);
  if (n.tag === "video" || n.tag === "audio") out.push(n); walk(n.kids); }; walk(grid(box)); return out; };
"""


def run(code: str) -> dict:
    saved = test_shell_page.FILES
    test_shell_page.FILES = FILES
    try:
        return shell_run(SETUP + LIBRARY + "\n" + code)
    finally:
        test_shell_page.FILES = saved


def test_the_library_shows_a_page_of_tiles_and_show_more_adds_the_next():
    out = run("""LIBF.limit = LIB_PAGE; const box = await draw();
      out.tiles = grid(box).children.length; out.first = shown(box); out.button = moreText(box);
      more(box).attrs.onclick(); out.second = shown(box).length; out.button2 = moreText(box);
      more(box).attrs.onclick(); out.third = shown(box).length; out.button3 = moreText(box);
      out.kept = LIBF.limit; const again = await draw(); out.redrawn = shown(again).length;""")
    assert out["tiles"] == 150  # every take has its tile; the page decides which are shown
    assert out["first"] == [f"{'scouts/s' if i % 2 else 'renders/r'}{i}{'.png' if i % 2 else '.mp4'}" for i in range(60)]
    assert out["button"] == "Show 60 more (90 not shown)"
    assert out["second"] == 120 and out["button2"] == "Show 30 more (30 not shown)"
    assert out["third"] == 150 and out["button3"] is None  # nothing left: the button goes
    assert out["kept"] == 180 and out["redrawn"] == 150  # a redraw (a new take, say) keeps the pages already open


def test_a_search_or_a_kind_starts_again_at_the_first_page():
    out = run("""LIBF.limit = LIB_PAGE; let box = await draw(); more(box).attrs.onclick(); more(box).attrs.onclick();
      const words = search(box); words.value = "renders"; words.on.change();
      out.search = [LIBF.limit, shown(box).length, shown(box).every(p => p.startsWith("renders/")), moreText(box)];
      words.value = ""; words.on.change(); LIBF.limit = 120;
      button(box, "Pictures").attrs.onclick();
      out.kind = [LIBF.kind, LIBF.limit]; box = await draw(); out.stills = [grid(box).children.length, shown(box).length, moreText(box)];""")
    assert out["search"] == [60, 60, True, "Show 15 more (15 not shown)"]  # 75 clips match, the first 60 shown
    assert out["kind"] == ["image", 60]
    assert out["stills"] == [75, 60, "Show 15 more (15 not shown)"]


def test_select_all_takes_only_the_tiles_on_screen():
    out = run("""LIBF.limit = LIB_PAGE; LIBF.select = true; const box = await draw();
      button(box, "Select all").attrs.onclick(); out.first = [...LIBPICK];
      LIBPICK.clear(); more(box).attrs.onclick(); button(box, "Select all").attrs.onclick(); out.second = LIBPICK.size;""")
    assert len(out["first"]) == 60 and "renders/r60.mp4" not in out["first"]  # Delete selected never reaches unseen files
    assert out["second"] == 120


def test_a_player_fetches_its_file_only_once_its_tile_comes_near_the_screen():
    out = run("""LIBF.limit = LIB_PAGE; const box = await draw(); const all = players(box);
      out.players = all.length; out.withSrc = all.filter(p => p.src).length; out.waiting = all.filter(p => p.dataset.src).length;
      out.preload = all[0].attrs.preload;
      seen(all.slice(0, 3)); out.after = all.filter(p => p.src).map(p => p.src); out.left = all.filter(p => p.dataset.src).length;
      seen(all.slice(0, 3)); out.again = all.filter(p => p.src).length;
      const first = WATCHED[WATCHED.length - 1]; await draw(); out.oldLetGo = first.gone === true && WATCHED.length === 2;""")
    assert out["players"] == 75 and out["withSrc"] == 0 and out["waiting"] == 75  # no clip is fetched on a draw
    assert out["preload"] == "metadata"
    assert out["after"] == ["/files/renders/r0.mp4#t=0.5", "/files/renders/r2.mp4#t=0.5", "/files/renders/r4.mp4#t=0.5"]  # a tile shows the clip at 0.5 s
    assert out["left"] == 72 and out["again"] == 3
    assert out["oldLetGo"]  # each draw has its own watcher; the last one's tiles are not kept


def test_the_job_poll_redraws_the_library_only_for_a_take_that_is_new_or_gone():
    out = run(POLL + """
      globalThis.HELD = false; globalThis.jobTools = () => {}; globalThis.queueDraw = () => {}; globalThis.bayUpdate = () => {};
      globalThis.busyHere = () => false; NODES["#jobs"] = el("div");
      const step = async list => { answers["/api/jobs"] = list; renders = 0; await poll(); return renders; };
      tab = "Library";
      out.first = await step([{ id: "a", status: "running" }]);
      out.queued = await step([{ id: "b", status: "queued" }, { id: "a", status: "running" }]);
      out.started = await step([{ id: "b", status: "running" }, { id: "a", status: "running" }]);
      out.cancelled = await step([{ id: "b", status: "cancelled" }, { id: "a", status: "running" }]);
      out.done = await step([{ id: "b", status: "cancelled" }, { id: "a", status: "done" }]);
      out.same = await step([{ id: "b", status: "cancelled" }, { id: "a", status: "done" }]);
      out.deleted = await step([{ id: "b", status: "cancelled" }]);
      tab = "Video";
      out.otherTab = await step([{ id: "c", status: "queued" }, { id: "b", status: "cancelled" }]);""")
    assert out["first"] == 0  # the first answer only sets the marks
    assert out["queued"] == 0 and out["started"] == 0 and out["cancelled"] == 0  # no file changed
    assert out["done"] == 1 and out["same"] == 0 and out["deleted"] == 1
    assert out["otherTab"] == 1  # other tabs redraw on any change, as before
