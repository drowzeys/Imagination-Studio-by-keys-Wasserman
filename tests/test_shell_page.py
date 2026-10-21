"""The studio's frame and its rooms (app/static/shell.js, home.js, engines.js, machine.js, agent.js, suite.js, updates.js), run in node with
the page's helpers stood in for: python3 tests/test_shell_page.py. Skipped where node is not installed."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FILES = ["shell.js", "home.js", "engines.js", "machine.js", "agent.js", "suite.js", "updates.js", "settings.js", "sound.js"]
HARNESS = r"""
const vm = require("vm"), fs = require("fs");
globalThis.S = { platform: "spark", platform_label: "DGX SPARK · NVIDIA GB10 · H3-CUDA", presets: { small: { size: [864, 480] }, p720: { size: [1280, 720] }, sharp: { size: [1376, 768] } },
  sharp: { seed: 42, seconds: [8, 10], quality: "fh8" }, long: { 30: "30 s" }, ref2va: true, ref_media: true, reference_total: 12, sparks: { supported: true } };
globalThis.TABS = ["Home", "Story", "Image", "Video", "Previs", "Sound", "Edit", "Create", "Production", "Import", "Motion Previs", "Agent", "Voice", "Music", "Foley", "Editor", "Library", "Looks", "Machine", "Sparks", "Brains", "Updates", "Tutorial", "Nodes", "Settings"];
globalThis.tab = "Home"; globalThis.renders = 0; globalThis.render = () => { renders++; };
globalThis.toasts = []; globalThis.toast = t => toasts.push(String(t));
globalThis.calls = []; globalThis.answers = {};
globalThis.api = async (path, body) => { calls.push([path, body]); if (path in answers) { const a = answers[path]; if (a instanceof Error) throw a; return a; } return {}; };
globalThis.jobs = []; globalThis.poll = async () => {}; globalThis.PICK = new Set(); globalThis.project = { id: "p1" };
globalThis.window = { scrollTo() {} }; globalThis.pushed = []; globalThis.history = { replaceState() {}, pushState(a, b, u) { pushed.push(u); } }; globalThis.localStorage = { getItem() { return null; }, setItem() {} };
globalThis.document = { querySelector() { return null; }, querySelectorAll() { return []; }, getElementById() { return null; }, body: { classList: { toggle() {} } }, activeElement: { matches() { return false; } } };
globalThis.NODES = {}; globalThis.$ = sel => NODES[sel] || null; globalThis.location = { assign(u) { calls.push(["assign", u]); } };
globalThis.C = { step: 0, goes: "video", start: "text", engine: "fasth3", passes: "8", res: "480p", up: false, steps: "", seed: 1, refs: [], subjects: [], refVideos: [], video_subjects: [], dialogue: [], loras: [], imageSize: "1344x768" };
globalThis.SL = { mode: "board" }; globalThis.UPSCALED = { "480p": ["p960", "1728x960"], "720p": ["qhd", "2560x1440 (2K)"] };
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


def need_node():
    """The page tests run the app's JavaScript in node: skip without it, or fail where it must be there (CI)."""
    if shutil.which("node"):
        return
    why = "node is not installed: the page tests run the app's JavaScript in node"
    if os.environ.get("STUDIO_TESTS_NEED_NODE") == "1":
        pytest.fail(why + " (STUDIO_TESTS_NEED_NODE=1)")
    pytest.skip(why)


def run(code: str) -> dict:
    need_node()
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "harness.js").write_text(HARNESS)
        (Path(scratch) / "test.js").write_text(code)
        done = subprocess.run(["node", str(Path(scratch) / "harness.js"), str(Path(scratch) / "test.js"), *[str(ROOT / "app/static" / f) for f in FILES]],
                              capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_chip_names_the_card_and_a_spark_stays_a_spark():
    out = run("""out.spark = platName();
      S.platform_label = "CUDA · NVIDIA RTX PRO 6000 BLACKWELL MAX-Q WORKSTATION EDITION · H3-CUDA"; S.gpu_index = "1";
      out.rtx = platName(); delete S.gpu_index; out.rtxBare = platName();
      S.platform = "mac"; S.platform_label = "MAC · APPLE SILICON MX · H3-METAL"; out.mac = platName();""")
    assert out["spark"] == "DGX Spark"
    assert out["rtx"] == "RTX PRO 6000 · 1" and out["rtxBare"] == "RTX PRO 6000"
    assert out["mac"] == "Mac"


def test_every_tab_has_one_room_and_old_links_still_work():
    out = run("""out.rooms = ROOMS.map(r => r.name); out.groups = GROUPS.map(g => g.name + ":" + ROOMS.filter(r => r.group === g.id).map(r => r.name).join(","));
      out.homeless = TABS.filter(t => ROOMS.filter(r => r.tabs.includes(t)).length !== 1);
      go("Voice"); out.voice = tab; goRoom("edit"); out.edit = tab; goRoom("library"); out.lib = tab; go("Nowhere"); out.still = tab; out.names = TABS.map(t => TAB_NAMES[t] || "");
      C.goes = "video"; out.createVideo = roomOf("Create").id; C.goes = "edit"; out.createEdit = roomOf("Create").id; C.goes = "image"; out.createImage = roomOf("Create").id;
      out.keys = ROOMS.map(r => r.key);""")
    assert out["rooms"] == ["Story", "Image", "Video", "Previs", "Sound", "Edit", "Agent", "Library", "Looks", "Wasserman's Filmmaker Suite", "Nodes", "Machine", "Learn", "Settings"]
    assert out["groups"] == ["Creative Engines:Story,Image,Video,Previs,Sound,Edit", ":Agent", "Studio Vaults:Library,Looks,Wasserman's Filmmaker Suite,Nodes,Machine,Learn,Settings"]
    assert out["homeless"] == ["Home", "Create"]  # home sits outside the rooms; Create is in Image and in Video
    assert (out["createVideo"], out["createEdit"], out["createImage"]) == ("video", "image", "image")
    assert out["voice"] == "Voice" and out["edit"] == "Edit" and out["lib"] == "Library" and out["still"] == "Library"
    assert all(out["names"])
    assert out["keys"] == ["1", "2", "3", "4", "5", "6", "", "7", "8", "9", "", "0", "", ","]  # ⌥1 to ⌥9, ⌥0 and ⌥, for Settings


def test_the_sidebar_shows_both_groups_and_marks_the_room():
    out = run("""for (const k of ["#rooms", "#roomname", "#subtabs"]) NODES[k] = el("div", {});
      go("Music"); renderShell(); out.nav = text(NODES["#rooms"]); out.room = NODES["#roomname"].textContent; out.subs = text(NODES["#subtabs"]);
      out.on = NODES["#rooms"].kids.filter(n => n.attrs && n.attrs.class === "on").map(n => n.attrs["data-room"]);
      out.groups = NODES["#rooms"].kids.filter(n => n.attrs && /^grp/.test(n.attrs.class || "")).map(text);
      go("Library"); renderShell(); out.single = NODES["#subtabs"].kids.length;
      go("Home"); renderShell(); out.homeOn = NODES["#rooms"].kids.filter(n => n.attrs && /\bon\b/.test(n.attrs.class || "") && n.attrs["data-room"] !== "home").length; out.homeBtn = NODES["#rooms"].kids[0].attrs.class; out.homeName = NODES["#roomname"].textContent;""")
    assert out["groups"] == ["Creative Engines", "Studio Vaults"]
    assert out["nav"].index("Creative Engines") < out["nav"].index("Story") < out["nav"].index("Studio Vaults") < out["nav"].index("Library")
    assert out["on"] == ["sound"] and out["room"] == "Sound" and "Voice" in out["subs"] and "Music" in out["subs"]
    assert out["single"] == 0  # a room of one view shows no tabs
    assert out["homeOn"] == 0 and out["homeName"] == "Home" and out["homeBtn"] == "home on"  # Home is the first button, lit at Home


def test_each_engine_opens_on_a_landing_whose_cards_open_the_views():
    out = run("""const landings = { Story: viewStory, Image: viewImage, Video: viewVideo, Previs: viewPrevis, Sound: viewSound, Edit: viewEditHub };
      out.pages = Object.fromEntries(Object.entries(landings).map(([k, f]) => [k, text(f())]));
      const click = (f, words) => { const b = button(f(), words); b.attrs.onclick(); return [tab, C.goes, C.start]; };
      out.still = click(viewImage, "A still"); out.edit = click(viewImage, "Edit a picture"); out.clip = click(viewVideo, "A clip with sound");
      out.refs = click(viewVideo, "References"); out.voice = click(viewSound, "A voice line")[0]; out.song = click(viewSound, "A song")[0];
      out.motion = click(viewPrevis, "Motion Previs")[0]; out.timeline = click(viewEditHub, "Timeline")[0];
      button(viewStory(), "Wasserman Slate").attrs.onclick(); out.slate = [tab, SL.mode];
      button(viewStory(), "Film board").attrs.onclick(); out.board = [tab, SL.mode];
      for (const r of ROOMS.filter(r => r.group === "engines")) { goRoom(r.id); out["land_" + r.id] = tab; }
      out.art = find(viewVideo(), n => n.attrs && n.attrs["data-art"] === "engine-video") ? "slot" : "none";
      out.img = !!find(viewVideo(), n => n.tag === "img" && /^art[/]/.test(n.attrs.src || ""));""")
    assert "MiniMax H3" in out["pages"]["Video"] and "FLUX3-K" in out["pages"]["Image"] and "Qwen 2.1" in out["pages"]["Image"]
    assert "Fish Audio Drama 3" in out["pages"]["Sound"] and "MiniMax-Music3" in out["pages"]["Sound"]
    assert out["still"] == ["Create", "image", "text"] and out["edit"][:2] == ["Create", "edit"] and out["clip"] == ["Create", "video", "text"]
    assert out["refs"] == ["Create", "video", "refs"]
    assert (out["voice"], out["song"], out["motion"], out["timeline"]) == ("Voice", "Music", "Motion Previs", "Editor")
    assert out["slate"] == ["Production", "slate"] and out["board"] == ["Production", "board"]
    assert [out["land_" + k] for k in ("story", "image", "video", "previs", "sound", "edit")] == ["Story", "Image", "Video", "Previs", "Sound", "Edit"]
    assert out["art"] == "slot" and not out["img"]  # no art is ready in the manifest: a plain panel, never a broken image


def test_create_carries_what_the_prompt_goes_to_as_one_small_control():
    out = run("""C.goes = "image"; const bar = intentBar(); out.bar = text(bar);
      const seg = find(bar, n => n.attrs && n.attrs.role === "tablist"); out.on = seg.kids.filter(n => n.attrs.class === "on").map(text);
      button(bar, "Video with sound").attrs.onclick(); out.goes = [C.goes, C.step];""")
    assert "The prompt goes to" in out["bar"] and "Qwen 2.1" in out["bar"] and "FLUX3-K" in out["bar"]
    assert out["on"] == ["Image"] and out["goes"] == ["video", 0]


def test_the_updates_panel_shows_the_followed_branch_and_the_latest_release():
    out = run("""UPD.data = { message: "Studio is up to date.", source: "drowzeys/studio", branch: "development/wasserman-studio", following: "development/wasserman-studio",
        installed_version: "2.9.2", installed_commit: "189a988", available: false, managed: true, automatic: false, checking: false, updating: false, session: "s1",
        checked_at: Date.now() / 1000 - 120, latest_release: { tag: "v2.9.3", name: "2.9.3", published_at: "2026-10-16T12:00:00Z", url: "https://github.com/drowzeys/studio/releases/tag/v2.9.3" }, last_result: null };
      let page = viewUpdates(); out.page = text(page); out.install = !!button(page, "Update and restart").attrs.disabled; out.busy = updBusy();
      out.day = updDay("2026-10-16T12:00:00Z"); UPD.data = { ...UPD.data, available: true, latest_version: "2.9.4", target: "abcdef123" }; page = viewUpdates(); out.ready = text(page); out.install2 = !!button(page, "Update and restart").attrs.disabled;
      UPD.data = { message: "Local code changes were found.", source: "drowzeys/studio", following: "main", available: false, managed: false, session: "s1" };
      page = viewUpdates(); out.err = text(page);
      // after Update and restart: waiting until a new session answers, then a Reload button and no more polling
      UPD.session = "s1"; UPD.waiting = true; updTake({ updating: true, session: "s1", message: "" }); out.during = updBusy();
      updTake({ updating: false, session: "s2", message: "Studio is up to date." }); out.after = [UPD.back, updBusy()]; out.back = text(viewUpdates());""")
    assert "drowzeys/studio · development/wasserman-studio" in out["page"] and "Latest on GitHub: v2.9.3 · " + out["day"] in " ".join(out["page"].split()) and out["day"]
    assert "Latest on GitHub" not in out["err"]  # no release in the answer: nothing is said about one
    assert "2.9.2 · 189a988" in out["page"] and "2 min ago" in out["page"]
    assert out["install"] and not out["busy"]  # nothing to install; nothing is happening, so nothing polls
    assert "Ready to install" in out["ready"] and "2.9.4" in out["ready"] and not out["install2"]
    assert "Local code changes were found." in out["err"] and "drowzeys/studio · main" in out["err"] and "scripts/app.sh" in out["err"]
    assert out["during"] and out["after"] == [True, False] and "Reload Studio" in out["back"]


def test_the_palette_finds_actions_and_runs_them():
    out = run("""const acts = palActions(); out.labels = acts.map(a => a.label);
      acts.find(a => a.label === "Ask your agent").run(); out.agent = tab;
      acts.find(a => a.label === "New still or scouts").run(); out.still = [tab, C.goes];
      out.blender = acts.filter(a => a.words.includes("blender")).map(a => a.label);
      out.lessons = acts.find(a => a.label.startsWith("Learn")).hint; acts.find(a => a.label === "Check for updates").run(); out.updates = tab;""")
    for label in ("New clip with sound", "Ask your agent", "Wasserman's Filmmaker Suite", "Brains & tools", "Telemetry", "Check for updates", "Looks", "Motion Previs"):
        assert label in out["labels"], label
    assert out["lessons"] == "23 lessons"
    assert out["agent"] == "Agent" and out["still"] == ["Create", "image"]
    assert "Ask your agent" in out["blender"] and "Brains & tools" in out["blender"]
    assert out["updates"] == "Updates"


def test_the_stage_offers_every_recipe_and_the_take_sheet_sets_one():
    out = run("""answers["/api/library"] = [];
      const home = viewHome(); out.home = text(home);
      button(home, "A voice line").attrs.onclick(); out.voice = tab;
      const sheet = takeSheet(); out.sheet = text(sheet);
      button(sheet, "Quick look").attrs.onclick(); out.quick = [C.passes, C.res];""")
    for words in ("A clip with sound", "Scout, then animate", "A still", "Sharp Spec", "A long take", "A voice line", "Song", "Ask your agent", "ScriptBreak", "Blockout", "Start something", "Recent takes"):
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
      out.codexOff = find(page, n => n.attrs && n.attrs.role === "radio" && text(n).includes("Codex")).attrs["aria-disabled"] === "true";
      useOutput("agent/a1/garage.png", "first"); out.first = [tab, C.image, C.start];""")
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
      BR.data = { default: "claude", settings: { remote: { host: "", backends: [] } }, backends: [{ id: "claude", label: "Claude Code", found: true, where: "ssh:user@workstation" }, { id: "local", label: "Local model server", kind: "http", found: true, models: ["qwen3"] }] };
      BR.tools = { tools: [] }; const page = viewBrains(); out.brains = text(page);
      await button(page, "Make default").attrs.onclick(); out.saved = calls.find(c => c[0] === "/api/brains/settings")[1].default;""")
    assert out["create"] == ["Create", "A garage at night.", "Maya turns.", "Slow push-in.", "Who's there?", "uploads/suite/x/12c.png", "own"]
    assert "Make a Slate film" in out["importPage"] and "Timeline with media for Resolve (ZIP)" in out["importPage"] and "Night Shift" in out["importPage"]
    assert "on user@workstation" in out["brains"] and "1 models" in out["brains"]
    assert out["saved"] == "local"


def test_the_phone_bar_folds_the_vaults_search_and_updates_into_more():
    out = run("""for (const k of ["#rooms", "#roomname", "#subtabs"]) NODES[k] = el("div", {});
      let sheet = null; document.body.append = n => { sheet = n; }; globalThis.palOpen = () => { out.palette = true; };
      go("Looks"); renderShell(); const nav = NODES["#rooms"].kids, more = nav[nav.length - 1];
      out.more = [more.attrs["data-room"], more.attrs.class, text(more)];
      out.vaults = nav.filter(n => n.attrs && n.attrs["data-group"] === "vaults").map(n => n.attrs["data-room"]);
      more.attrs.onclick(); out.sheet = text(sheet);
      out.on = find(sheet, n => n.tag === "button" && /on/.test(n.attrs.class || "")) ? text(find(sheet, n => n.tag === "button" && /on/.test(n.attrs.class || ""))) : "";
      button(sheet, "Updates").attrs.onclick(); out.updates = tab; button(sheet, "Search").attrs.onclick();
      button(sheet, "Machine").attrs.onclick(); out.machine = tab;
      go("Story"); renderShell(); out.engineMore = NODES["#rooms"].kids.slice(-1)[0].attrs.class;""")
    assert out["more"][0] == "more" and out["more"][1] == "more on" and "More" in out["more"][2]  # a vault is open: More is the lit item
    assert out["vaults"] == ["library", "looks", "suite", "nodes", "machine", "learn", "settings", "about"]  # hidden from the phone bar by studio.css
    for words in ("Agent", "Studio Vaults", "Library", "Looks", "Filmmaker Suite", "Nodes", "Machine", "Learn", "Settings", "Search", "⌘K", "Updates"):
        assert words in out["sheet"], words
    assert "Looks" in out["on"]
    assert out["updates"] == "Updates" and out["palette"] and out["machine"] == "Machine" and out["engineMore"] == "more"


def test_a_lost_event_stream_polls_then_reconnects_with_backoff():
    out = run("""const streams = [], waits = [], timers = new Map(); let tid = 0;
      class FakeES { constructor(u) { this.url = u; this.closed = false; this.on = {}; streams.push(this); } addEventListener(k, f) { this.on[k] = f; } close() { this.closed = true; } }
      window.EventSource = FakeES; globalThis.EventSource = FakeES;
      globalThis.setTimeout = (f, ms) => { waits.push([ms, f]); return 0; };
      globalThis.setInterval = (f, ms) => { timers.set(++tid, ms); return tid; }; globalThis.clearInterval = id => timers.delete(id);
      globalThis.telApply = t => { out.applied = (out.applied || 0) + 1; }; answers["/api/telemetry"] = { memory: null };
      telStart(); out.opened = streams.length;
      streams[0].on.telemetry({ data: "{}" });
      const fail = i => { streams[i].onerror(); return [streams[i].closed, SHELL.telPoll != null, waits[waits.length - 1][0]]; };
      out.first = fail(0); waits[0][1](); out.second = fail(1); waits[1][1](); out.third = fail(2);
      const polls = () => [...timers.values()].filter(ms => ms === 3000).length;
      out.pollers = polls(); out.dog = [...timers.values()].filter(ms => ms === 5000).length; out.telCalls = calls.filter(c => c[0] === "/api/telemetry").length;
      for (let i = 0; i < 6; i++) { waits[waits.length - 1][1](); fail(streams.length - 1); } out.cap = waits[waits.length - 1][0];
      waits[waits.length - 1][1](); streams[streams.length - 1].onopen(); out.back = [SHELL.telPoll, polls(), SHELL.telWait, SHELL.es === streams[streams.length - 1]];""")
    assert out["opened"] == 1 and out["applied"] >= 1
    assert out["first"] == [True, True, 2000] and out["second"] == [True, True, 4000] and out["third"] == [True, True, 8000]
    assert out["pollers"] == 1 and out["telCalls"] == 1  # one 3 s poll while down, not one per failure
    assert out["dog"] == 1  # one watchdog, however often the stream is opened again
    assert out["cap"] == 30000
    assert out["back"] == [None, 0, 2000, True]  # the stream is back: the poll stops and the backoff starts over


def test_paths_are_shown_from_the_studio_folder_and_dropped_players_let_go():
    out = run("""S.home = "/data/studio"; out.rel = [relHome("/data/studio/renders/a.mp4"), relHome("/data/studio"), relHome("log: /data/studio/logs/comfy.log and /data/studio/x"),
        relHome("/data/studio-old/a"), relHome("/elsewhere/b"), relHome(null)];
      const seen = []; const player = n => ({ pause() { seen.push(n + ":pause"); }, removeAttribute(a) { seen.push(n + ":-" + a); }, load() { seen.push(n + ":load"); } });
      releaseMedia({ querySelectorAll: () => [player("v1"), player("a1")], matches: () => false }); out.seen = seen;""")
    assert out["rel"] == ["renders/a.mp4", "the studio folder", "log: logs/comfy.log and x", "/data/studio-old/a", "/elsewhere/b", None]
    assert out["seen"] == ["v1:pause", "v1:-src", "v1:load", "a1:pause", "a1:-src", "a1:load"]


def test_a_brain_that_is_not_set_up_is_greyed_and_links_to_brains_and_tools():
    out = run("""BR.data = { default: "claude", backends: [{ id: "claude", label: "Claude Code", found: false, where: "here" }, { id: "codex", label: "Codex", found: false, where: "ssh:mac" }], settings: {} };
      BR.tools = { tools: [] }; AG.tasks = []; AG.backend = "claude"; answers["/api/agent/tasks"] = [];
      let page = viewAgent(); out.backend = AG.backend; const agent = w => find(page, n => n.attrs && n.attrs.role === "radio" && text(n).includes(w)); out.claudeOff = agent("Claude Code").attrs["aria-disabled"] === "true"; out.codexOn = agent("Codex").attrs["aria-disabled"] !== "true";
      out.hint = text(find(page, n => /ag-hint/.test(n.attrs && n.attrs.class || "")));
      find(page, n => n.tag === "a" && n.attrs.class === "ag-setup").attrs.onclick({}); out.link = tab;
      BR.data.backends[1] = { id: "codex", label: "Codex", found: false, where: "here" }; AG.backend = null; page = viewAgent();
      out.sendOff = !!button(page, "Send").attrs.disabled; AG.prompt = "a room"; await button(page, "Send").attrs.onclick();
      out.sent = calls.some(c => c[0] === "/api/agent/tasks" && c[1]); out.toast = toasts.slice(-1)[0];
      S.home = "/data/studio"; const card = taskCard({ id: "t1", status: "failed", prompt: "a room", backend: "claude", error: "Claude Code is not installed on this machine: install it and sign in, or run it over ssh", log: "[claude here] wrote /data/studio/agent/t1/log.txt" });
      AG.open.t1 = true; const open = taskCard({ id: "t1", status: "failed", prompt: "a room", backend: "claude", error: "boom", log: "wrote /data/studio/agent/t1/out.png" });
      out.card = text(card); out.cardLink = !!find(card, n => n.tag === "a" && n.attrs.class === "ag-setup"); out.plain = !!find(open, n => n.tag === "a" && n.attrs.class === "ag-setup"); out.log = text(open);""")
    assert out["backend"] == "codex"  # Claude Code is missing here, Codex runs over SSH: the page picks Codex
    assert out["claudeOff"] and out["codexOn"]
    # the set-up link names Settings → Connections, or Brains & tools on an Agent room that predates Settings
    assert "Claude Code is not on this machine" in out["hint"] and "Set up" in out["hint"] and out["link"] in ("Settings", "Brains")
    assert out["sendOff"] and not out["sent"] and "not set up here" in out["toast"]
    assert "Set up" in out["card"] and out["cardLink"] and not out["plain"]
    assert "agent/t1/out.png" in out["log"] and "/data/studio" not in out["log"]


def test_the_tally_lights_breathe_in_css_and_stop_when_motion_is_reduced():
    css = (ROOT / "app" / "static" / "studio.css").read_text()
    for name in ("tally-glow", "tally-led", "tally-pill", "tally-sheen"):
        assert "@keyframes " + name + " {" in css, name
    assert "@media (prefers-reduced-motion:reduce) { *, *::before, *::after { animation:none !important;" in css



# ---------- Back and Home, Settings, the Home composer (2026-10-18) ----------

def test_back_returns_to_the_last_page_home_goes_home_and_the_stack_is_bounded():
    out = run("""for (const k of ["#rooms", "#roomname", "#subtabs"]) NODES[k] = el("div", {});
      const back = el("button", {}), home = el("button", {}); back.setAttribute = () => {}; home.classList = { on: null, toggle(c, v) { home.hidden = v; } };
      NODES["#backbtn"] = back; NODES["#homebtn"] = home;
      go("Home"); renderShell(); out.homeHidden = home.hidden; out.backOff = back.disabled;
      go("Voice"); go("Library"); go("Library"); renderShell(); out.homeShown = !home.hidden; out.backOn = !back.disabled; out.stack = [...SHELL.history]; out.pushed = [...pushed];
      goBack(); out.back1 = tab; goBack(); out.back2 = tab; goBack(); out.back3 = tab;
      go("Music"); goHome(); out.home = tab;
      for (let i = 0; i < 80; i++) go(i % 2 ? "Voice" : "Looks"); out.bounded = SHELL.history.length;
      // the browser's Back: the hash changes, go() runs with fromHash, takes the page off the stack and pushes nothing
      SHELL.history.length = 0; pushed.length = 0; go("Story"); go("Video"); const before = pushed.length;
      go("Story", { fromHash: true }); out.hash = [tab, SHELL.history.slice(-1)[0] || null, pushed.length - before];
      const acts = palActions(); out.pal = ["Back", "Home", "Settings", "Nodes", "Ask your agent"].map(l => !!acts.find(a => a.label === l));""")
    assert out["homeHidden"] and out["backOff"]  # on Home with nowhere to go back to
    assert out["homeShown"] and out["backOn"] and out["stack"] == ["Home", "Voice"]  # the same tab twice is one entry
    assert out["pushed"] == ["#Voice", "#Library"]  # the browser's history moves with the app
    assert (out["back1"], out["back2"], out["back3"]) == ("Voice", "Home", "Home")
    assert out["home"] == "Home" and out["bounded"] == 50
    assert out["hash"] == ["Story", "Voice", 0]  # back on Story; what was before it is next
    assert all(out["pal"])


def test_settings_shows_its_five_sections_and_the_look_saves():
    out = run("""answers["/api/settings"] = { settings: { appearance: { type_size: "large", glow: false, poster: true, accent: "amber", motion: "system", start_cards: ["clip"] } } };
      BR.data = { default: "claude", settings: { remote: { host: "", backends: [] }, local_endpoint: "" }, backends: [
        { id: "claude", label: "Claude Code", kind: "cli", found: true, where: "here", harness: true }, { id: "hermes", label: "Hermes", kind: "cli", found: false, where: "here", signin: "Run `hermes setup`" },
        { id: "gemini", label: "Gemini CLI", kind: "cli", found: true, where: "ssh:mac" }, { id: "local", label: "Local model server", kind: "http", found: true, endpoint: "http://127.0.0.1:11434/v1", models: ["qwen3"] }] };
      BR.tools = { tools: [{ id: "blender", label: "Blender" }] }; globalThis.updRequest = async () => ({ installed_commit: "abc1234" });
      await settingsLoad(); out.prefs = prefs().appearance;
      go("Settings"); SET.section = "about"; viewSettings(); await new Promise(r => setTimeout(r, 0)); const pages = {};
      for (const [id] of SET_SECTIONS) { SET.section = id; pages[id] = text(viewSettings()); } out.pages = pages;
      SET.section = "appearance"; let page = viewSettings();
      out.rail = find(page, n => n.attrs && n.attrs.class === "st-rail").kids.map(text);
      await button(page, "Largest").attrs.onclick(); out.sent = calls.filter(c => c[0] === "/api/settings" && c[1]).slice(-1)[0][1];
      SET.section = "connections"; page = viewSettings();
      await button(find(page, n => n.attrs && n.attrs["data-brain"] === "claude"), "Test").attrs.onclick(); out.test = calls.filter(c => c[0] === "/api/brains/test").slice(-1)[0][1];
      await button(find(page, n => n.attrs && n.attrs["data-tool"] === "blender"), "Test").attrs.onclick(); out.check = calls.some(c => c[0] === "/api/agent/tools/blender/check");
      out.brains = text(viewBrains());""")
    assert out["prefs"]["type_size"] == "large" and out["prefs"]["glow"] is False and out["prefs"]["start_cards"] == ["clip"]
    for words in ("Appearance", "Studio layout", "Presets & preferences", "Connections", "Models & weights", "Sparks", "Brains & tools", "Updates", "About"):
        assert any(words in r for r in out["rail"]), words
    assert "Type size" in out["pages"]["appearance"] and "Poster on Home" in out["pages"]["appearance"] and "Cobalt" in out["pages"]["appearance"]
    assert "Settings" in out["pages"]["layout"] and "stays" in out["pages"]["layout"] and "⌘K" in out["pages"]["layout"]
    assert "Render recipe" in out["pages"]["presets"] and "48 kHz stereo" in out["pages"]["presets"] and "QA gate" in out["pages"]["presets"]
    c = out["pages"]["connections"]
    assert "Any local model works here — nothing leaves your machine." in c and "Ollama" in c and "LM Studio" in c and "vLLM" in c and "llama.cpp" in c and "Hermes proxy" in c
    assert "Hermes" in c and "not found" in c and "hermes setup" in c and "Not installed" in c and "on mac" in c and "Blender" in c
    assert "Link another computer" in c
    a = out["pages"]["about"]
    assert "Fish Audio" in a and "MiniMax-Music3" in a and "abc1234" in a
    assert out["sent"] == {"appearance": {"type_size": "largest"}}
    assert out["test"] == {"backend": "claude"} and out["check"]
    assert "Any local model works here" in out["brains"] and "Settings → Connections" in out["brains"]


def test_layout_hides_and_orders_rooms_and_a_hidden_room_stays_in_the_palette():
    out = run("""for (const k of ["#rooms", "#roomname", "#subtabs"]) NODES[k] = el("div", {});
      SET.data = { layout: { hidden: ["looks", "learn"], order: [] } };
      renderShell(); out.nav = NODES["#rooms"].kids.filter(n => n.attrs && n.attrs["data-room"]).map(n => n.attrs["data-room"]);
      out.pal = palActions().filter(a => a.hint === "hidden from the sidebar").map(a => a.label);
      SET.section = "layout"; const page = viewSettings();
      await find(page, n => n.attrs && n.attrs["aria-label"] === "Move Sound up").attrs.onclick(); out.order = calls.filter(c => c[0] === "/api/settings" && c[1]).slice(-1)[0][1].layout.order;
      out.machineSwitch = !!find(find(page, n => n.attrs && n.attrs["data-room"] === "machine"), n => n.attrs && n.attrs.role === "switch");
      SET.data = { layout: { hidden: [], order: ["sound", "story"] } }; renderShell();
      out.reordered = NODES["#rooms"].kids.filter(n => n.attrs && n.attrs["data-group"] === "engines").map(n => n.attrs["data-room"]);""")
    assert "looks" not in out["nav"] and "learn" not in out["nav"] and "machine" in out["nav"] and "settings" in out["nav"] and "agent" in out["nav"]
    assert out["pal"] == ["Looks", "Learn"]
    assert out["order"].index("sound") < out["order"].index("previs")
    assert not out["machineSwitch"]  # Machine always stays: no switch to hide it
    assert out["reordered"][:2] == ["sound", "story"]


def test_the_home_composer_queues_each_kind_and_carries_the_words():
    out = run("""answers["/api/library"] = []; answers["/api/compose"] = { prompt: "integrated_multimodal_description: [Shot 1] A lighthouse." };
      answers["/api/jobs"] = { id: "j1", title: "A lighthouse" }; answers["/api/voice/takes"] = { id: "v1" }; answers["/api/music/takes"] = { id: "m1" };
      let page = viewHome(); out.page = text(page);
      out.primaries = (function count(n) { return !n || typeof n !== "object" ? 0 : Array.isArray(n) ? n.reduce((a, k) => a + count(k), 0) : (/\\bprimary\\b/.test(n.attrs && n.attrs.class || "") ? 1 : 0) + count(n.kids); })(page);
      HC.text = "A lighthouse at dusk"; await button(page, "Generate").attrs.onclick();
      out.clip = calls.filter(c => c[0] === "/api/jobs").slice(-1)[0][1];
      HC.mode = "picture"; await hcGenerate(); out.picture = calls.filter(c => c[0] === "/api/jobs").slice(-1)[0][1];
      HC.mode = "voice"; await hcGenerate(); out.voice = calls.filter(c => c[0] === "/api/voice/takes").slice(-1)[0][1];
      HC.mode = "song"; HC.text = "[Verse] light on the water"; await hcGenerate(); out.song = calls.filter(c => c[0] === "/api/music/takes").slice(-1)[0][1];
      out.made = HC.made.map(m => m.mode);
      // ⌘Enter in the box generates, the chips switch the kind
      HC.mode = null; HC.text = "A gull"; page = viewHome(); const box = find(page, n => n.tag === "textarea");
      const keys = []; box.addEventListener = (k, f) => keys.push([k, f]); page = viewHome();
      const area = find(page, n => n.tag === "textarea"); out.placeholder = area.attrs.placeholder;
      const chip = find(page, n => n.attrs && n.attrs["data-mode"] === "voice"); chip.attrs.onclick(); out.chip = hcMode();
      HC.mode = "clip"; HC.text = "A lighthouse at dusk"; hcMore(); out.more = [tab, C.prompt, C.goes];
      HC.text = "Plan a short"; hcAgent(); out.agent = [tab, AG.prompt];""")
    assert "What do you want to make?" in out["page"] and "Clip with sound" in out["page"] and "MiniMax H3" in out["page"] and "Drama 3" in out["page"] and "Music 3" in out["page"]
    assert "Ask your agent instead" in out["page"] and "More options" in out["page"] and "Quick look" in out["page"]
    assert out["primaries"] == 1  # Generate is the one primary on Home
    assert out["clip"]["kind"] == "video" and out["clip"]["params"]["preset"] == "small" and out["clip"]["params"]["quality"] == "fh4" and out["clip"]["params"]["prompt"].startswith("integrated")
    assert out["picture"]["kind"] == "scout" and out["picture"]["params"]["count"] == 1 and out["picture"]["params"]["preset"] == "2k"
    assert out["voice"]["text"] == "A lighthouse at dusk"
    assert out["song"]["lyrics"] == "[Verse] light on the water" and out["song"]["caption"]
    assert out["made"] == ["song", "voice", "picture", "clip"]
    assert out["placeholder"] == "What do you want to make?" and out["chip"] == "voice"
    assert out["more"] == ["Create", "A lighthouse at dusk", "video"]
    assert out["agent"] == ["Agent", "Plan a short"]


def test_each_card_shows_one_word_and_one_next_action():
    out = run("""const brains = [{ id: "claude", label: "Claude Code", kind: "cli", found: true, where: "ssh:mac", signin_flow: "pty" },
        { id: "codex", label: "Codex", kind: "cli", found: true, where: "here", signin_flow: "pty" },
        { id: "gemini", label: "Gemini CLI", kind: "cli", found: false, where: "here", signin_flow: "terminal", signin: "Run `gemini`" },
        { id: "grok", label: "Grok CLI", kind: "cli", found: true, where: "here", signin_flow: "pty" },
        { id: "hermes", label: "Hermes", kind: "cli", found: true, where: "ssh:mac", error: "mac did not answer" },
        { id: "local", label: "Local model server", kind: "http", found: false, models: [] }];
      BR.data = { default: "codex", settings: { remote: { host: "mac", backends: ["claude", "hermes"] } }, backends: brains }; BR.tools = { tools: [] };
      BR.status = { claude: { state: "keychain", detail: "Signed in on that Mac, but macOS keeps the sign-in in its Keychain" }, codex: { state: "signed_in", detail: "ChatGPT" },
        grok: { state: "broken", detail: "Error loading config" } };
      tab = "Settings"; SET.section = "connections"; const page = viewSettings();
      const card = id => find(page, n => n.attrs && n.attrs["data-brain"] === id);
      out.words = Object.fromEntries(["claude", "codex", "gemini", "grok", "hermes"].map(id => [id, card(id).attrs["data-word"]]));
      out.actions = Object.fromEntries(["claude", "codex", "gemini", "grok", "hermes"].map(id => [id, (find(card(id), n => n.attrs && n.attrs.class === "m-acts") || { kids: [] }).kids.filter(Boolean).map(text)]));
      out.hidden = text(find(card("claude"), n => n.tag === "details"));
      out.top = text(card("claude")).replace(out.hidden, "");""")
    assert out["words"] == {"claude": "Signed in elsewhere", "codex": "Ready", "gemini": "Not installed", "grok": "Cannot start", "hermes": "Cannot start"}
    assert out["actions"]["claude"] == ["Allow over SSH"] and out["actions"]["gemini"] == ["Install"] and out["actions"]["grok"] == ["Fix it"]
    assert out["actions"]["hermes"] == ["Check the link"] and out["actions"]["codex"] == ["Check"]  # the default: nothing to make
    assert "Keychain" in out["hidden"] and "Keychain" not in out["top"]  # the technical part only under Details


CONNECT = """const ticks = []; globalThis.setInterval = fn => { ticks.push(fn); return ticks.length; }; globalThis.clearInterval = () => {};
  const tabs = []; globalThis.window.open = () => { const w = { location: {}, document: { body: {} }, closed: false, close() { this.closed = true; } }; tabs.push(w); return w; };
  globalThis.location.hostname = "127.0.0.1";
  globalThis.document.createElementNS = (ns, tag) => { const n = el(tag, {}); n.setAttribute = (k, v) => { n.attrs[k] = v; }; n.append = (...k) => n.kids.push(...k); return n; };
  const tick = async () => { await ticks[ticks.length - 1](); await new Promise(r => setTimeout(r, 0)); };
  const settle = () => new Promise(r => setTimeout(r, 0));
  const steps = id => { const p = find(viewSettings(), n => n.attrs && n.attrs["data-connect"] === id); return p ? Object.fromEntries(p.kids.find(k => k && k.tag === "ol").kids.map(li => [li.attrs["data-step"], li.attrs["data-state"]])) : null; };
  const panel = id => find(viewSettings(), n => n.attrs && n.attrs["data-connect"] === id);
  const base = (id, more) => ({ backend: id, label: id, where: "here", find: { found: true, version: "1.0" }, install: { state: "idle" }, signin: { state: "signed_out" }, signing: { state: "idle" },
    verify: null, default: false, agent_default: false, harness: true, default_ready: false, token_flow: false, ...more });
  tab = "Settings"; SET.section = "connections";"""


def test_connect_ticks_find_signin_verify_and_default_by_itself():
    out = run(CONNECT + """
      const b = { id: "claude", label: "Claude Code", kind: "cli", found: true, where: "here", signin_flow: "pty", signin_command: "claude auth login" };
      BR.data = { default: "local", settings: { remote: { host: "", backends: [] } }, backends: [b] }; BR.tools = { tools: [] }; BR.status = { claude: { state: "signed_out" } };
      answers["/api/brains/claude/connect"] = base("claude");
      answers["/api/brains/claude/signin"] = { backend: "claude", state: "needs_code", url: "https://claude.com/cai/oauth/authorize?code=true&state=x", prompt: "Paste code here", lines: [] };
      await connectStart(b); await settle();
      out.opened = tabs.length; out.pointed = tabs[0].location.href;
      out.s1 = steps("claude"); const p = panel("claude"); out.panel = text(p);
      out.qr = !!find(p, n => n.tag === "svg" && n.attrs.class === "cn-qr"); out.qrPath = (find(p, n => n.tag === "path") || { attrs: {} }).attrs.d.length > 200;
      out.copy = !!button(p, "Copy link"); out.open = find(p, n => n.tag === "a" && n.attrs.class === "signin-open").attrs.href;
      find(p, n => n.tag === "input").attrs.oninput({ target: { value: "pasted#code" } });
      answers["/api/brains/claude/signin/code"] = { backend: "claude", state: "verifying", lines: [] };
      await button(p, "Continue").attrs.onclick(); out.code = calls.filter(c => c[0] === "/api/brains/claude/signin/code").slice(-1)[0][1];
      answers["/api/brains/claude/signin"] = { backend: "claude", state: "done", lines: [] };
      answers["/api/brains/claude/connect"] = base("claude", { signin: { state: "signed_in", detail: "Claude subscription" } });
      answers["/api/brains/claude/verify"] = { ok: true, text: "ok", elapsedMs: 2100 };
      answers["/api/brains/claude/default"] = { settings: { default: "claude", agent: "claude", remote: { host: "", backends: [] } } };
      await tick(); await settle(); await settle();
      out.verified = calls.some(c => c[0] === "/api/brains/claude/verify"); out.madeDefault = calls.some(c => c[0] === "/api/brains/claude/default");
      out.s2 = steps("claude"); out.done = text(panel("claude")); out.default = BR.data.default;""")
    assert out["opened"] == 1 and out["pointed"].startswith("https://claude.com/cai/oauth/authorize")  # the tab opened on the click goes to the page
    assert out["s1"] == {"find": "done", "signin": "running", "verify": "todo", "default": "todo"}
    assert "Open the sign-in page" in out["panel"] and "Scan the code" in out["panel"] and out["copy"] and out["open"].startswith("https://claude.com/")
    assert out["qr"] and out["qrPath"]
    assert out["code"] == {"code": "pasted#code"}
    assert out["verified"] and out["madeDefault"] and out["default"] == "claude"
    assert out["s2"] == {"find": "done", "signin": "done", "verify": "done", "default": "done"} and "Answered “ok” in 2.1 s" in out["done"] and "is ready" in out["done"]


def test_connect_installs_a_missing_cli_shows_a_device_code_and_the_fix_for_a_failed_answer():
    out = run(CONNECT + """
      const b = { id: "codex", label: "Codex", kind: "cli", found: false, where: "ssh:mac", signin_flow: "pty" };
      BR.data = { default: "claude", settings: { remote: { host: "mac", backends: ["codex"] } }, backends: [b] }; BR.tools = { tools: [] }; BR.status = {};
      answers["/api/brains/codex/connect"] = base("codex", { where: "ssh:mac", find: { found: false }, signin: { state: "missing" } });
      answers["/api/brains/codex/install?plan=1"] = { state: "idle", plan: { ok: true, command: "npm install -g @openai/codex@latest", by: "npm", where: "ssh:mac" } };
      await connectStart(b); await settle(); out.tabs = tabs.length; out.s1 = steps("codex"); out.plan = text(panel("codex"));
      answers["/api/brains/codex/install"] = { backend: "codex", state: "running", command: "npm install -g @openai/codex@latest", by: "npm", lines: ["added 1 package"] };
      await button(panel("codex"), "Install Codex").attrs.onclick(); out.running = text(panel("codex")); out.s2 = steps("codex");
      // installed: the brains are read again, the sign-in starts with a one-time code (the CLI is on another computer)
      answers["/api/brains/codex/install"] = { backend: "codex", state: "done", lines: [] };
      BR.data.backends[0].found = true; answers["/api/brains"] = BR.data; answers["/api/agent/tools"] = { tools: [] };
      answers["/api/brains/codex/connect"] = base("codex", { where: "ssh:mac" });
      answers["/api/brains/codex/signin"] = { backend: "codex", state: "device_code", url: "https://auth.openai.com/codex/device", code: "ABCD-12345", lines: [] };
      await tick(); await settle(); await settle();
      out.device = calls.filter(c => c[0] === "/api/brains/codex/signin" && c[1]).slice(-1)[0][1];
      const p = panel("codex"); out.code = text(find(p, n => n.attrs && /signin-code/.test(n.attrs.class || ""))); out.qr = !!find(p, n => n.tag === "svg");
      // signed in, but the answer fails: the exact error and the one action that fixes it
      answers["/api/brains/codex/signin"] = { backend: "codex", state: "done", lines: [] };
      answers["/api/brains/codex/connect"] = base("codex", { where: "ssh:mac", signin: { state: "signed_in" } });
      answers["/api/brains/codex/verify"] = { ok: false, error: "This Codex CLI on mac is older than your ChatGPT account's models: update it", fix: { action: "install", label: "Update Codex" } };
      await tick(); await settle(); await settle();
      out.s3 = steps("codex"); out.failed = text(panel("codex")); out.fix = !!button(panel("codex"), "Update Codex"); out.noDefault = !calls.some(c => c[0] === "/api/brains/codex/default");""")
    assert out["tabs"] == 0  # nothing to sign in to yet: no tab is opened
    assert out["s1"] == {"find": "wait", "signin": "todo", "verify": "todo", "default": "todo"}
    assert "npm install -g @openai/codex@latest" in out["plan"] and "on mac" in out["plan"] and "without administrator rights" in out["plan"]
    assert out["s2"]["find"] == "running" and "added 1 package" in out["running"]
    assert out["device"] == {"device": True} and "ABCD-12345" in out["code"] and "Copy code" in out["code"] and out["qr"]
    assert out["s3"] == {"find": "done", "signin": "done", "verify": "failed", "default": "todo"}
    assert "older than your ChatGPT account" in out["failed"] and out["fix"] and out["noDefault"]


def test_a_mac_over_ssh_offers_allow_over_ssh_and_the_link_wizard_tests_and_saves():
    out = run(CONNECT + """
      const b = { id: "claude", label: "Claude Code", kind: "cli", found: true, where: "ssh:me@mac", signin_flow: "pty" };
      BR.data = { default: "codex", settings: { remote: { host: "me@mac", backends: ["claude"] } }, backends: [b] }; BR.tools = { tools: [] };
      BR.status = { claude: { state: "keychain", detail: "Signed in on that Mac, but macOS keeps the sign-in in its Keychain, which SSH cannot open." } };
      answers["/api/brains/claude/connect"] = base("claude", { where: "ssh:me@mac", token_flow: true, signin: BR.status.claude, default_ready: true });
      answers["/api/brains/link"] = { has_key: false, host: "", backends: [] };
      CN.tried.claude = { signin: true }; CN.open = "claude"; CN.data.claude = answers["/api/brains/claude/connect"];
      out.s = steps("claude"); out.keychain = text(panel("claude")); out.allow = !!button(panel("claude"), "Allow over SSH");
      answers["/api/brains/claude/signin"] = { backend: "claude", state: "needs_code", url: "https://claude.com/cai/oauth/authorize?x=1", token: "waiting", lines: [] };
      await button(panel("claude"), "Allow over SSH").attrs.onclick(); await settle(); out.tokenNote = text(panel("claude"));
      // the wizard: make a key, the line to run there, test (a first meeting asks to trust), save
      CN.link = null; let page = viewSettings(); await settle(); page = viewSettings();
      const wiz = () => find(viewSettings(), n => n.attrs && n.attrs["data-card"] === "link");
      out.w1 = text(wiz());
      answers["/api/brains/link/key"] = { has_key: true, public_key: "ssh-ed25519 AAAAC3 imagination-studio", key_file: "~/.ssh/id_ed25519.pub", command: "mkdir -p ~/.ssh && echo 'ssh-ed25519 AAAAC3 imagination-studio' >> ~/.ssh/authorized_keys", host: "", backends: [] };
      await button(wiz(), "Make a key").attrs.onclick(); out.w2 = text(wiz());
      find(wiz(), n => n.tag === "input").attrs.oninput({ target: { value: "me@mac" } });
      answers["/api/brains/link/test"] = { ok: false, host: "me@mac", state: "unknown_host", fingerprint: "SHA256:abc (ED25519)", detail: "This computer has not met that one before." };
      await button(wiz(), "Test").attrs.onclick(); out.w3 = text(wiz());
      answers["/api/brains/link/test"] = { ok: true, host: "me@mac", state: "ok", os: "Darwin" };
      await button(wiz(), "Trust this computer and test").attrs.onclick(); out.trust = calls.filter(c => c[0] === "/api/brains/link/test").slice(-1)[0][1];
      out.w4 = text(wiz());
      answers["/api/brains/link/save"] = { settings: {}, found: ["claude"], has_key: true, host: "me@mac", backends: ["claude"] };
      answers["/api/brains"] = BR.data; answers["/api/agent/tools"] = { tools: [] };
      await button(wiz(), "Use me@mac for its agents").attrs.onclick(); out.w5 = text(wiz()); out.connectOne = !!button(wiz(), "Connect Claude Code");""")
    assert out["s"]["signin"] == "wait" and "Keychain" in out["keychain"] and out["allow"]
    assert "kept only on that Mac" in out["tokenNote"]
    assert "Make a key" in out["w1"]
    assert "authorized_keys" in out["w2"] and "Remote Login" in out["w2"]
    assert "SHA256:abc" in out["w3"] and "Trust this computer and test" in out["w3"]
    assert out["trust"] == {"host": "me@mac", "trust": True} and "Reached me@mac (macOS)" in out["w4"]
    assert "Its agents now run there" in out["w5"] and out["connectOne"]


def test_home_and_the_agent_room_offer_connect_until_an_agent_is_signed_in():
    out = run(CONNECT + """
      const store = {}; globalThis.localStorage = { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };
      globalThis.ART = { ready: new Set() }; globalThis.artSlot = () => null; globalThis.palOpen = () => {}; globalThis.platName = () => "Mac"; globalThis.SHELL = { tel: null, samples: [] };
      BR.data = { default: "claude", settings: { remote: { host: "", backends: [] } }, backends: [{ id: "claude", label: "Claude Code", kind: "cli", found: false, where: "here" },
        { id: "codex", label: "Codex", kind: "cli", found: true, where: "here", signin_flow: "pty" }] }; BR.tools = { tools: [] };
      BR.status = { codex: { state: "signed_out" } };
      tab = "Home"; let home = viewHome(); const card = n => find(n, x => x.attrs && x.attrs["data-card"] === "connect");
      out.home = text(card(home)); out.primary = text(find(card(home), n => n.tag === "button" && /primary/.test(n.attrs.class || "")));
      answers["/api/brains/codex/connect"] = base("codex", { signin: { state: "signed_out" } });
      await find(card(home), n => n.tag === "button" && /primary/.test(n.attrs.class || "")).attrs.onclick(); out.went = [tab, CN.sheet, CN.open, tabs.length];
      CN.sheet = null;
      tab = "Agent"; AG.tasks = []; AG.threads = []; out.room = !!card(viewAgent());
      tab = "Home"; await button(card(viewHome()), "Not now").attrs.onclick(); out.dismissed = !card(viewHome());
      out.roomStill = !!card(viewAgent());
      BR.status.codex = { state: "signed_in" }; out.gone = !card(viewAgent());""")
    assert "Connect your agent" in out["home"] and out["primary"].endswith("Connect Codex")  # the one that is installed first
    assert out["went"][:3] == ["Home", "codex", "codex"] and out["went"][3] == 1  # Connect opens over the page: nothing to go and find
    assert out["room"] and out["dismissed"] and out["roomStill"] and out["gone"]


def test_the_connect_card_offers_the_local_brain_and_hermes_asks_which_account():
    """Your own local model (Nemotron 3.5 Bolt here) is one of the card's choices and is made the default in one
    click; Hermes signs in only to the account the person picks, never to ChatGPT by itself."""
    out = run(CONNECT + """
      const store = {}; globalThis.localStorage = { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };
      globalThis.ART = { ready: new Set() }; globalThis.artSlot = () => null; globalThis.palOpen = () => {}; globalThis.platName = () => "DGX Spark"; globalThis.SHELL = { tel: null, samples: [] };
      const hermes = { id: "hermes", label: "Hermes", kind: "cli", found: true, where: "here", signin_flow: "pty" };
      BR.data = { default: "claude", settings: { remote: { host: "", backends: [] } }, backends: [hermes,
        { id: "local", label: "NVIDIA Nemotron 3.5 Bolt (Studio engine)", kind: "http", found: true, harness: true, engine: { state: "unloaded", available: true } }] }; BR.tools = { tools: [] };
      BR.status = { hermes: { state: "signed_out" } };
      tab = "Home"; const card = n => find(n, x => x.attrs && x.attrs["data-card"] === "connect");
      let home = viewHome(); out.home = text(card(home));
      answers["/api/brains/local/default"] = { settings: { default: "local", agent: "local", remote: { host: "", backends: [] } } };
      await button(card(home), "Nemotron 3.5 Bolt (local)").attrs.onclick(); out.defaulted = [calls.some(c => c[0] === "/api/brains/local/default"), BR.data.default];
      out.inUse = text(card(viewHome())).includes("(local) · in use");
      answers["/api/brains/hermes/connect"] = base("hermes", { signin: { state: "signed_out" } });
      await connectStart(hermes); await settle();
      out.started = calls.filter(c => c[0] === "/api/brains/hermes/signin").length; out.tabsBefore = tabs.length;
      const p = panel("hermes"); out.panel = text(p);
      answers["/api/brains/hermes/signin"] = { backend: "hermes", state: "device_code", url: "https://claude.ai/oauth", code: "AB-12", lines: [] };
      await button(p, "Claude").attrs.onclick(); out.sent = calls.filter(c => c[0] === "/api/brains/hermes/signin").slice(-1)[0][1]; out.tabsAfter = tabs.length;""")
    assert "Nemotron 3.5 Bolt (local)" in out["home"] and out["defaulted"] == [True, "local"] and out["inUse"]
    assert out["started"] == 0 and out["tabsBefore"] == 0  # no ChatGPT sign-in on its own, no blank tab
    assert "Which account should Hermes use?" in out["panel"] and "ChatGPT (Codex)" in out["panel"] and "Nous Portal" in out["panel"]
    assert out["sent"] == {"provider": "anthropic"} and out["tabsAfter"] == 1


def test_the_qr_code_has_its_finders_and_size_for_the_address():
    out = run("""const m = qrMatrix("https://auth.openai.com/codex/device"), big = qrMatrix("https://claude.com/" + "a".repeat(420));
      const finder = (m, x, y) => [0, 1, 2, 3, 4, 5, 6].every(i => m[y][x + i] && m[y + 6][x + i] && m[y + i][x] && m[y + i][x + 6]) && m[y + 3][x + 3] && !m[y + 1][x + 1];
      out.size = m.length; out.bigSize = big.length; out.finders = [finder(m, 0, 0), finder(m, m.length - 7, 0), finder(m, 0, m.length - 7)];
      out.timing = [...Array(m.length - 16)].map((_, i) => m[6][8 + i] === (i % 2 === 0)).every(Boolean); out.dark = m[m.length - 8][8];
      out.tooLong = qrMatrix("x".repeat(3000)) === null;""")
    assert out["size"] == 29 and out["bigSize"] == 81  # version 3 and version 16 at level M
    assert out["finders"] == [True, True, True] and out["timing"] and out["dark"] and out["tooLong"]


def test_a_stream_that_goes_quiet_without_an_error_is_opened_again():
    """A half-open tunnel: the EventSource neither errors nor delivers. The watchdog treats it as lost."""
    out = run("""const streams = [], waits = [], dogs = []; let now = 1000000;
      class FakeES { constructor(u) { this.readyState = 1; this.on = {}; streams.push(this); } addEventListener(k, f) { this.on[k] = f; } close() { this.readyState = 2; } }
      window.EventSource = FakeES; globalThis.EventSource = FakeES; Date.now = () => now;
      globalThis.setTimeout = (f, ms) => { waits.push([ms, f]); return 0; };
      globalThis.setInterval = (f, ms) => { if (ms === 5000) dogs.push(f); return dogs.length; }; globalThis.clearInterval = () => {};
      globalThis.telApply = t => { SHELL.telAt = now; }; answers["/api/telemetry"] = {};
      SHELL.telDog = null; telStart(); streams[0].on.telemetry({ data: "{}" });
      now += 4000; dogs[0](); out.fresh = [streams.length, streams[0].readyState, waits.length];
      now += 16000; dogs[0](); out.quiet = [streams[0].readyState, SHELL.es, SHELL.telPoll != null, waits.length];
      waits[0][1](); out.reopened = [streams.length, SHELL.es === streams[1]];
      streams[1].readyState = 2; dogs[0](); out.closed = [SHELL.es, waits.length];
      telStart(); out.oneDog = dogs.length;""")
    assert out["fresh"] == [1, 1, 0]  # telemetry 4 s ago: left alone
    assert out["quiet"] == [2, None, True, 1]  # 20 s without telemetry: closed, polled, and opened again after a wait
    assert out["reopened"] == [2, True]
    assert out["closed"] == [None, 2]  # closed without an error: the same
    assert out["oneDog"] == 1


def test_the_back_pill_takes_the_browsers_back_and_adds_no_page():
    """Nodes → Looks → Back: the browser's own Back, so its Forward leads to Looks and its Back does not return to Looks."""
    out = run("""const stack = [{ state: null, hash: "#Home" }]; let at = 0, backs = 0;
      globalThis.history = { get state() { return stack[at].state; }, pushState(s, t, u) { stack.splice(at + 1); stack.push({ state: s, hash: u }); at++; },
        replaceState(s, t, u) { stack[at] = { state: s, hash: u }; }, back() { backs++; at--; go(stack[at].hash.slice(1), { fromHash: true }); } };
      go("Nodes"); go("Looks"); goBack(); out.after = [tab, at, stack.length, backs, [...SHELL.history]];
      go("Voice"); history.replaceState(null, "", "#Voice"); goBack(); out.fallback = [tab, backs];""")
    assert out["after"] == ["Nodes", 1, 3, 1, ["Home"]]  # back on Nodes, one entry back, nothing pushed; Forward still holds Looks
    assert out["fallback"] == ["Nodes", 1]  # where the browser's entry is not the app's last page, Back still goes there


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)



def test_tools_a_remote_agent_cannot_start_yet_are_set_aside_with_the_reason_and_come_back():
    out = run("""BR.data = { default: "codex", settings: { remote: { host: "mac", backends: ["codex"] } }, backends: [{ id: "codex", label: "Codex", kind: "cli", found: true, where: "ssh:mac" }] };
      BR.status = { codex: { state: "signed_in" } }; AG.tasks = []; AG.threads = []; AG.backend = "codex"; AG.tools = new Set(["blender", "studio"]);
      BR.tools = { tools: [{ id: "blender", label: "Blender" }, { id: "studio", label: "This Studio" }], unreachable: { blender: "Blender runs on this computer: mac needs a way back to it." } };
      let page = viewAgent(); out.tools = [...AG.tools]; out.off = button(page, "Blender").attrs["aria-disabled"]; out.note = text(find(page, n => n.attrs && n.attrs.class === "note m-flat ag-back"));
      button(page, "Blender").attrs.onclick(); out.toast = toasts.slice(-1)[0];
      BR.tools.unreachable = {}; viewAgent(); out.back = [...AG.tools].sort();""")
    assert out["tools"] == ["studio"] and out["off"] == "true" and "reach this studio" in out["note"]
    assert "needs a way back" in out["toast"] and out["back"] == ["blender", "studio"]
