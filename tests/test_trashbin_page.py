"""Films moved to the trash and put back (app/static/trashbin.js, rooms.js), in node with the shell's stand-ins:
python3 -m pytest tests/test_trashbin_page.py. Skipped where node is not installed."""
import json
import subprocess
import tempfile
from pathlib import Path

from test_shell_page import FILES, HARNESS, ROOM_STANDINS, ROOT, need_node

FILES = [*FILES, "trashbin.js"]


def run(code: str) -> dict:
    need_node()
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "harness.js").write_text(HARNESS)
        (Path(scratch) / "test.js").write_text(code)
        done = subprocess.run(["node", str(Path(scratch) / "harness.js"), str(Path(scratch) / "test.js"), *[str(ROOT / "app/static" / f) for f in FILES]],
                              capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


ALL = """const all = (root, test, acc = []) => { if (!root || typeof root !== "object") return acc; if (Array.isArray(root)) { root.forEach(n => all(n, test, acc)); return acc; } if (test(root)) acc.push(root); all(root.kids, test, acc); return acc; };
const settle = () => new Promise(r => setTimeout(r, 20));
globalThis.confirm = () => true;
"""


def test_a_film_goes_to_the_trash_from_a_rooms_film_picker():
    out = run(ROOM_STANDINS.replace("const settle", "const settleRoom") + ALL.replace("globalThis.confirm = () => true;", "") + """globalThis.confirm = () => true;
      go("Cast & Places"); viewCast(); await settle(); viewCast(); await settle(); await settle(); let page = viewCast();
      const trash = find(page, n => n.tag === "button" && n.attrs && n.attrs["data-film-trash"]);
      const film = SL.p && SL.p.id;
      out.offered = [!!trash, text(trash)];
      const asked = [], plain = globalThis.api; globalThis.api = async (path, body, method) => { asked.push([path, method || (body === undefined ? "GET" : "POST")]); return plain(path, body); };
      await trash.attrs.onclick(); out.methods = asked;
      out.film = film; out.sent = calls.some(c => c[0] === "/api/slate/projects/" + film && c[1] === undefined && calls.indexOf(c) > 0); out.open = SL.p; out.asked = RM.asked;""")
    assert out["offered"][0] is True and out["offered"][1].endswith("Move to the trash")
    assert out["film"] == "f-new" and out["open"] is None and out["asked"] is False  # the film list is read again
    assert ["/api/slate/projects/f-new", "DELETE"] in out["methods"]


def test_the_librarys_trash_lists_the_films_and_puts_one_back():
    out = run(ALL + """answers["/api/slate/trash"] = [{ folder: "slate-f2-200", id: "f2", name: "Red umbrella", removed: 200 }, { folder: "slate-f1-100", id: "f1", name: "Night bus", removed: 100 }];
      tbPanel(); await settle(); let panel = tbPanel();
      out.closed = text(panel);
      find(panel, n => n.attrs && n.attrs["data-trashbin-open"]).attrs.onclick(); panel = tbPanel();
      out.rows = all(panel, n => n.attrs && n.attrs["data-trashed"]).map(text);
      await find(panel, n => n.attrs && n.attrs["data-put-back"] === "slate-f1-100").attrs.onclick();
      out.restored = calls.some(c => c[0] === "/api/slate/trash/slate-f1-100/restore");""")
    assert "Films in the trash (2)" in out["closed"]
    assert "Red umbrella" in out["rows"][0] and "Night bus" in out["rows"][1]  # newest first, as the server lists them
    assert out["restored"] is True


def test_an_empty_trash_shows_nothing():
    out = run(ALL + """answers["/api/slate/trash"] = [];
      tbPanel(); await settle(); out.panel = tbPanel();""")
    assert out["panel"] is None



def test_the_library_page_carries_the_trash_panel():
    page = (ROOT / "app/static/index.html").read_text()
    assert '<script src="trashbin.js"></script>' in page and 'typeof tbPanel === "function" ? tbPanel() : null' in page


def test_productions_remove_this_film_has_the_librarys_trash_read_again():
    source = (ROOT / "app/static/slate.js").read_text()
    remove = source[:source.index('"Remove this film")')].rsplit("el(\"button\"", 1)[1]
    assert 'tbForget' in remove and remove.index("DELETE") < remove.index("tbForget")


def test_an_older_answer_does_not_overwrite_the_trash_read_after_a_film_was_removed():
    out = run(ALL + """const held = []; const plain = globalThis.api;
      globalThis.api = (path, body) => path === "/api/slate/trash" ? new Promise(r => held.push(r)) : plain(path, body);
      tbPanel();  // the Library asks; its answer is slow
      tbForget(); tbPanel();  // a film is removed in Production, the Library is opened again and asks again
      held[1]([{ folder: "slate-f9-300", id: "f9", name: "Night bus", removed: 300 }]); await settle();
      held[0]([]); await settle();  // the older answer, an empty trash, comes last
      TB.open = true; out.rows = (TB.films || []).map(f => f.id); out.asks = held.length;""")
    assert out["asks"] == 2 and out["rows"] == ["f9"]


def test_an_older_answer_does_not_put_a_removed_film_back_in_the_rooms_picker():
    out = run(ALL + """const held = []; const plain = globalThis.api;
      globalThis.api = (path, body, method) => path === "/api/slate/projects" && !method ? new Promise(r => held.push(r)) : plain(path, body);
      rmPicker();  // a room reads the films; the answer is slow
      tbForget(); rmPicker();  // a film is moved to the trash, the room reads them again
      held[1]([]); await settle();
      held[0]([{ id: "gone", name: "Night bus" }]); await settle();  // the older answer, with the film, comes last
      out.films = RM.films.map(f => f.id); out.asks = held.length;""")
    assert out["asks"] == 2 and out["films"] == []
