"""Apps (app/static/apps.js): every tool as a one-click app, run in node with the page's helpers stood in for (as
tests/test_shell_page.py): python3 -m pytest tests/test_apps_page.py. Skipped where node is not installed."""
from test_shell_page import FILES, run as shell_run
import test_shell_page

APPS_FILES = FILES + ["edit.js", "actions.js", "swap.js", "apps.js"]

SETUP = """
globalThis.ART = { ready: new Set(["start-clip", "look-camera-motion"]) };
globalThis.ICON = globalThis.ICON || {}; globalThis.mark = id => el("span", {}, id);
globalThis.LORAS = [{ id: "camera-motion", name: "Camera Motion", installed: true, status: "ready", strength: 0.9, styles: { "Push-in": "slow push-in" } },
  { id: "combat-base-v2", name: "Combat Base V2", installed: false, status: "ready" },
  { id: "equi360", name: "Equirectangular 360", installed: false, status: "unsupported", why: "DGX Spark only" }];
globalThis.HUB = { q: "", tab: "all", mine: false, groups: [] }; globalThis.loadLoras = async () => {};
ACT.state = { upscale: { ready: false, why: "Install the Video Upscaler LoRA first" }, remove: { ready: true }, turnaround: { ready: true } };
SWAP.state = { ready: true };
globalThis.document.querySelector = () => null;
const cards = root => { const out = []; const walk = n => { if (!n || typeof n !== "object") return; if (Array.isArray(n)) return n.forEach(walk);
  if (n.attrs && n.attrs["data-app"]) out.push(n); walk(n.kids); }; walk(root); return out; };
const card = (root, id) => cards(root).find(n => n.attrs["data-app"] === id);
"""


def run(code: str) -> dict:
    saved = test_shell_page.FILES
    test_shell_page.FILES = APPS_FILES
    try:
        return shell_run(SETUP + code)
    finally:
        test_shell_page.FILES = saved


def test_apps_is_a_room_of_its_own_above_the_engines():
    out = run("""tab = "Apps"; out.room = roomOf("Apps").id; out.first = GROUPS[0].id; out.name = TAB_NAMES.Apps;
      out.icon = !!ICON.apps;""")
    assert out == {"room": "apps", "first": "apps", "name": "Apps", "icon": True}


def test_every_category_has_apps_and_ids_are_unique():
    out = run("""const all = APPS(); out.ids = all.map(a => a.id); out.cats = [...new Set(all.map(a => a.cat))];
      out.known = AP_CATS.map(c => c.id);""")
    assert len(out["ids"]) == len(set(out["ids"]))
    assert set(out["cats"]) == set(out["known"])
    assert {"swap", "angles", "speech", "upscale", "captions", "director", "suite-blockout"} <= set(out["ids"])


def test_a_lora_app_uses_installs_or_says_why():
    out = run("""const v = viewApps();
      card(v, "camera").attrs.onclick(); out.used = [tab, C.goes, C.loras.map(l => l.id)];
      tab = "Apps"; card(viewApps(), "fight").attrs.onclick(); out.install = [tab, HUB.q, toasts.slice(-1)[0]];
      out.need = text(card(viewApps(), "fight")); out.vr = [card(viewApps(), "vr").attrs.disabled, text(card(viewApps(), "vr"))];""")
    assert out["used"] == ["Create", "video", ["camera-motion"]]
    assert out["install"][0] == "Looks" and out["install"][1] == "Combat Base V2" and "Install" in out["install"][2]
    assert "Install" in out["need"]
    assert out["vr"][0] is True and "DGX Spark only" in out["vr"][1]


def test_a_library_action_asks_for_the_take_first_then_opens_its_panel():
    out = run("""AP.lib = [{ path: "renders/a.mp4", kind: "video" }, { path: "scouts/b.png", kind: "image" }];
      card(viewApps(), "restyle").attrs.onclick(); const v = viewApps(); out.asks = text(find(v, n => n.attrs && n.attrs.id === "appick"));
      const tile = find(find(v, n => n.attrs && n.attrs.id === "appick"), n => n.attrs && n.attrs.title === "renders/a.mp4"); tile.attrs.onclick();
      out.opened = [ACT.open && ACT.open.kind, ACT.open && ACT.open.path, tab, AP.pick];
      out.upscale = [card(viewApps(), "upscale").attrs.disabled, text(card(viewApps(), "upscale"))];""")
    assert "pick the clip" in out["asks"] and "a.mp4" in out["asks"] and "b.png" not in out["asks"]
    assert out["opened"] == ["restyle", "renders/a.mp4", "Library", None]
    assert out["upscale"][0] is True and "Video Upscaler" in out["upscale"][1]


def test_search_and_categories_narrow_the_gallery():
    out = run("""AP.q = "orbit"; out.q = cards(viewApps()).map(n => n.attrs["data-app"]); AP.q = "";
      AP.cat = "sound"; out.cat = cards(viewApps()).map(n => n.attrs["data-app"]); AP.cat = "all";
      AP.q = "genjutsu"; out.gen = cards(viewApps()).map(n => n.attrs["data-app"]); AP.q = "zzzz"; out.none = text(viewApps()).includes("No app matches");""")
    assert "orbit" in out["q"] and "voice" not in out["q"]
    assert out["cat"] == ["voice", "song", "foley"]
    assert out["gen"] == ["swap"]
    assert out["none"] is True


def test_every_app_is_in_the_palette():
    out = run("""const a = (ico, label, hint, run, words) => ({ label, hint }); out.n = appsPalette(a).length; out.all = APPS().length;
      out.room = palActions().some(x => x.label === "Apps");""")
    assert out["n"] == out["all"] and out["room"] is True
