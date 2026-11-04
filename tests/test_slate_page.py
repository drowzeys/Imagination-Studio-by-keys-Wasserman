"""Slate's page code (app/static/slate.js), run in node with the page's helpers stood in for:
python3 tests/test_slate_page.py. Skipped where node is not installed; no browser, no GPU, no network."""
import json
import subprocess
from pathlib import Path

from test_shell_page import need_node

ROOT = Path(__file__).resolve().parent.parent
HARNESS = r"""
const vm = require("vm"), fs = require("fs");
globalThis.vm = vm; globalThis.fs = fs;
globalThis.S = { qualities: { fh8: "FastH3" }, durations: { 5: 124, 8: 192, 10: 243, 15: 362 }, lengths: { fps: 24, step: 17, shortest: 22, longest: 362 } };
globalThis.render = () => {}; globalThis.toast = text => (globalThis.toasts = globalThis.toasts || []).push(text);
globalThis.calls = []; globalThis.answers = {};
globalThis.api = async (path, body) => { calls.push([path, body]); return answers[path] || {}; };
globalThis.stillParams = params => params;
globalThis.jobs = []; globalThis.LORAS = [{ id: "camera-motion", installed: true, name: "Camera Motion" }, { id: "gone", installed: false }];
globalThis.lorasOf = obj => (obj.loras || []).filter(p => LORAS.some(r => r.id === p.id && r.installed));
globalThis.poll = async () => {};
// the page's own builders, stood in for: a node is its tag, attributes and children, so a view can be drawn and read
const text = node => node == null || node === false ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join(" ") : [node.attrs && node.attrs.placeholder, node.attrs && node.attrs.value, text(node.kids)].filter(Boolean).join(" ");
globalThis.text = text;
globalThis.el = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids, addEventListener() {}, set value(v) { this.attrs.value = v; }, get value() { return this.attrs.value; } });
globalThis.put = (node, ...kids) => node.kids.push(...kids); globalThis.field = (label, node) => el("div", {}, label, node);
globalThis.bind = (obj, key, node) => { node.attrs.value = obj[key] ?? ""; return node; }; globalThis.select = (obj, key, options) => el("select", { value: obj[key] }, Object.values(options));
globalThis.choice = (obj, key, options) => el("div", {}, Object.values(options).flat()); globalThis.media = path => el("img", {}, path); globalThis.file = path => "/files/" + path;
globalThis.jobCard = job => el("div", {}, "job " + job.id); globalThis.loraHub = () => el("div", {}, "the LoRA hub"); globalThis.sharpButton = () => el("button", {}, "Sharp Spec");
globalThis.clearButton = () => el("button", {}, "Clear"); globalThis.addToTimeline = () => {}; globalThis.localStorage = { getItem: () => null, setItem() {} }; globalThis.confirm = () => true;
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
vm.runInThisContext(`
  const film = () => ({ id: "p-test", name: "Test", logline: "", world: "", defaults: { aspectRatio: "16:9", fps: 24, durationSec: 8, brain: "claude" }, scenes: [], characters: [], artDept: [], locations: [], lookbook: [], music: [], voices: [], copilot: [] });
  const out = {}; clearTimeout(slTimer);
  (async () => { ${fs.readFileSync(process.argv[3], "utf8")}; clearTimeout(slTimer); console.log(JSON.stringify(out)); })().catch(e => { console.error(e); process.exit(1); });
`);
"""


def run(code: str) -> dict:
    """Run ``code`` (it fills ``out``) after slate.js; None where node is missing."""

    import tempfile

    need_node()
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "harness.js").write_text(HARNESS)
        (Path(scratch) / "test.js").write_text(code)
        done = subprocess.run(["node", str(Path(scratch) / "harness.js"), str(ROOT / "app/static/slate.js"), str(Path(scratch) / "test.js")],
                              capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_first_ad_changes_a_sheet_instead_of_adding_a_second_one():
    got = run("""
      SL.p = film();
      out.first = slApply([{ type: "add_character", name: "Leo", age: "21", ethnicity: "Southeast Asian", hair: "short black hair", clothing: "a rust-orange work jacket" },
                           { type: "add_location", name: "Campus Walkway", description: "a paved walkway under rain trees", timeOfDay: "late morning" },
                           { type: "create_scene", name: "Scene 1" }, { type: "create_shot", scene: "Scene 1", name: "1.1", cast: ["Leo"], location: "Campus Walkway", prompt: "# Subject\\nLeo walks." }]);
      const leo = SL.p.characters[0], id = leo.id; leo.images = ["uploads/leo.png"]; leo.notes = "keeps the jacket";
      SL.plan = { x: 1 };
      // a change: only the fields sent
      out.change = slApply([{ type: "update_character", character: "leo", ethnicity: "Scandinavian", hair: "short sandy-blond hair" }]);
      out.after_change = { ...SL.p.characters[0] }; out.count = SL.p.characters.length; out.same_id = SL.p.characters[0].id === id; out.plans_dropped = Object.keys(SL.plan).length === 0;
      // a rename, by id
      out.rename = slApply([{ type: "update_character", character: id, name: "Leon" }]);
      out.cast_still_linked = SL.p.scenes[0].shots[0].tf.cast[0] === id && SL.p.characters[0].name === "Leon";
      // the run's own case: an add with a name the bible has is a rewrite of that sheet
      out.readd = slApply([{ type: "add_character", name: "Leon", age: "22", ethnicity: "Western European" }]);
      out.after_readd = { ...SL.p.characters[0] }; out.count_after_readd = SL.p.characters.length;
      out.replace = slApply([{ type: "update_location", location: "Campus Walkway", replace: true, description: "a gravel path between birches" }]);
      out.place = { ...SL.p.locations[0] }; out.place_linked = SL.p.scenes[0].shots[0].tf.location === SL.p.locations[0].id;
      out.missing = slApply([{ type: "update_character", character: "Nobody Here", age: "30" }, { type: "update_voice", voice: "x" }]);
      out.others = slApply([{ type: "add_art", kind: "vehicle", name: "Bicycle", description: "an old bicycle" }, { type: "update_art", art: "Bicycle", condition: "rusted" },
                            { type: "add_music_cue", name: "Theme", genre: "pop" }, { type: "update_music_cue", cue: "Theme", tempo: "117 BPM" },
                            { type: "add_voice", name: "Leo's voice", characterName: "Leon", timbre: "warm" }, { type: "update_voice", voice: "Leo's voice", pitch: "low" }]);
      out.art = SL.p.artDept[0]; out.cue = SL.p.music[0]; out.voice = SL.p.voices[0]; out.voice_linked = SL.p.voices[0].characterId === id;
      out.prompt_names_them = SL_AD.includes("update_character") && SL_AD.includes("update_location") && SL_AD.includes("Never add a second sheet");
    """)
    if got is None:
        return
    assert got["first"][0] == '✓ Cast "Leo"' and got["first"][1] == '✓ Scouted "Campus Walkway"'
    assert got["count"] == 1 and got["same_id"] and got["plans_dropped"]
    changed = got["after_change"]
    assert changed["ethnicity"] == "Scandinavian" and changed["hair"] == "short sandy-blond hair"
    assert changed["age"] == "21" and changed["clothing"] == "a rust-orange work jacket"  # what was not sent stays
    assert changed["images"] == ["uploads/leo.png"] and changed["notes"] == "keeps the jacket"
    assert got["change"] == ['✓ Changed character "Leo": ethnicity, hair']
    assert got["rename"] == ['✓ Changed character "Leo" → "Leon"'] and got["cast_still_linked"]
    again = got["after_readd"]
    assert got["count_after_readd"] == 1 and got["readd"] == ['✓ Rewrote character "Leon"']
    assert again["age"] == "22" and again["ethnicity"] == "Western European" and again["hair"] == "" and again["images"] == ["uploads/leo.png"]
    assert got["place"]["description"] == "a gravel path between birches" and got["place"]["timeOfDay"] == "" and got["place_linked"]
    assert got["missing"] == ['✗ character "Nobody Here" not found — nothing changed', '✗ voice "x" not found — nothing changed']
    assert got["art"]["kind"] == "vehicle" and got["art"]["condition"] == "rusted" and got["art"]["description"] == "an old bicycle"
    assert got["cue"]["tempo"] == "117 BPM" and got["cue"]["genre"] == "pop" and got["cue"]["vocals"] == "instrumental"
    assert got["voice"]["pitch"] == "low" and got["voice"]["timbre"] == "warm" and got["voice_linked"]
    assert got["prompt_names_them"]


def test_an_imported_take_joins_the_shots_takes():
    got = run("""
      SL.p = film(); slApply([{ type: "create_scene", name: "Scene 1" }, { type: "create_shot", scene: "Scene 1", name: "1.4" }]);
      const shot = SL.p.scenes[0].shots[0];
      answers = { "/api/slate/takes": { take: { id: "take-1", loggedAt: "2026-10-13T17:00:00.000Z", mediaPath: "slate/p-test/takes/x.mp4", seed: 43, rating: "good", notes: "", imported: true,
                    settings: { preset: "sharp", frames: 124, quality: "fh8", loras: [{ id: "camera-motion" }] }, source: { from: "http://10.0.10.5:7870", job: "w2" } } } };
      const take = await slImportTake(shot, { from: "http://10.0.10.5:7870", job: "w2", seed: "", notes: "" });
      out.calls = calls.map(c => c[0]); out.sent = calls[1][1]; out.takes = shot.takes.map(t => t.id); out.have = SL.have[take.mediaPath];
      out.line = slTakeLine(take); out.best = slBest(shot);
    """)
    if got is None:
        return
    assert got["calls"] == ["/api/slate/projects/p-test", "/api/slate/takes"]  # the film is saved first: the server reads the shot from the file
    assert got["sent"] == {"projectId": "p-test", "shot": got["sent"]["shot"], "from": "http://10.0.10.5:7870", "job": "w2"}  # empty fields are not sent
    assert got["takes"] == ["take-1"] and got["have"] is True and got["best"] == "slate/p-test/takes/x.mp4"
    assert got["line"] == "seed 43 · sharp · 124 frames · fh8 · LoRA camera-motion · imported from 10.0.10.5:7870 · 2026-10-13 17:00"


def test_a_save_of_a_film_the_server_changed_reloads_it():
    got = run("""
      const mine = film(); mine.updatedAt = "2026-10-13T17:00:00.000Z"; SL.p = mine;
      const theirs = { ...film(), name: "Written by the Director", updatedAt: "2026-10-13T17:05:00.000Z" };
      globalThis.api = async (path, body, method) => { calls.push([path, method || (body ? "POST" : "GET")]);
        if (method === "PUT") { if (body.updatedAt < theirs.updatedAt) throw new Error("the film changed on the server since it was loaded"); return { updatedAt: "2026-10-13T17:06:00.000Z" }; }
        return theirs; };
      slSave(); await new Promise(done => setTimeout(done, 600));
      out.calls = calls.slice(); out.name = SL.p.name; out.toast = toasts.pop();
      SL.p.name = "Renamed"; await slPut(); out.after = SL.p.updatedAt;  // a save of the reloaded film goes through, and keeps the new stamp
    """)
    if got is None:
        return
    assert got["calls"] == [["/api/slate/projects/p-test", "PUT"], ["/api/slate/projects/p-test", "GET"]]
    assert got["name"] == "Written by the Director" and got["toast"] == "The film changed on the server (a render or the Director): reloaded."
    assert got["after"] == "2026-10-13T17:06:00.000Z"


def test_the_first_ad_sets_the_song_a_shots_bars_and_its_lora():
    got = run("""
      SL.p = film(); LORAS[0].styles = { Orbit: "orbit shot" }; LORAS[0].strength = 0.9; LORAS[0].needs = "text"; LORAS[0].status = "ready";
      out.song = slApply([{ type: "update_project", song: { name: "Hesitation", bpm: 117.45, firstBeat: 0.07 } }]);
      out.bar = slSong().bar; out.context = slContext().includes("SONG") && slContext().includes("117.45 BPM");
      out.made = slApply([{ type: "create_scene", name: "Scene 1" },
                          { type: "create_shot", scene: "Scene 1", name: "1.1", bars: 2, lora: { name: "camera motion", style: "Orbit" } },
                          { type: "create_shot", scene: "Scene 1", name: "1.2", bars: 4, lora: { name: "Not Installed" } }]);
      const [a, b] = SL.p.scenes[0].shots; out.a = JSON.parse(JSON.stringify(a.tf)); out.b = JSON.parse(JSON.stringify(b.tf)); out.spec = slSpec(a);
      slApply([{ type: "update_shot", shot: "1.1", lora: null, bars: 0 }]); out.cleared = JSON.parse(JSON.stringify(a.tf));
      out.offers = slShotLoras().map(r => r.id);
      // the render: a shot's own LoRA stands in for the film's, and one that is not installed is not sent
      SL.p.tf.loras = [{ id: "camera-motion", strength: 0.5 }];
      answers = { "/api/slate/compile": { prompt: "p", plan: { seconds: 4.458, frames: 107, seed: 1, quality: "fh8", preset: "small", loras: [{ id: "camera-motion", strength: 0.9, style: "Orbit" }, { id: "gone" }] }, warnings: [] }, "/api/jobs": { id: "j1", params: { preset: "small", frames: 107 } } };
      calls.length = 0; await slRender(SL.p.scenes[0], a); out.own = calls.find(c => c[0] === "/api/jobs")[1].params.loras;
      answers["/api/slate/compile"].plan.loras = undefined; calls.length = 0; await slRender(SL.p.scenes[0], a); out.films = calls.find(c => c[0] === "/api/jobs")[1].params.loras;
      out.prompt = SL_AD.includes('"bars"') && SL_AD.includes('"lora"') && SL_AD.includes('"song"');
    """)
    if got is None:
        return
    assert got["song"][0].startswith("✓ Song: 117.45 BPM, a bar is 2.04 s") and abs(got["bar"] - 2.0434) < 0.001 and got["context"]
    assert got["a"]["bars"] == 2 and got["a"]["loras"] == [{"id": "camera-motion", "strength": 0.9, "style": "Orbit"}] and got["spec"].startswith("2 bars")
    assert got["b"]["bars"] == 4 and not got["b"].get("loras") and any('No installed LoRA called "Not Installed"' in line for line in got["made"])
    assert got["cleared"]["loras"] == [] and "bars" not in got["cleared"]
    assert got["offers"] == ["camera-motion"]
    assert got["own"] == [{"id": "camera-motion", "strength": 0.9, "style": "Orbit"}] and got["films"] == [{"id": "camera-motion", "strength": 0.5}]
    assert got["prompt"]


def test_the_views_draw_with_a_song_a_shot_lora_and_an_imported_take():
    """Bible, Shots (workspace, roll camera, takes, import) and Deliver are drawn without the browser: what they say
    is read from the tree they build."""

    got = run("""
      SL.p = film(); SL.data = { categories: [{ id: "c", label: "Light", setups: [] }], plans: [], profiles: [] }; S.sharp = null; S.ref2va = false; S.presets = { small: { size: [864, 480] } };
      LORAS[0].styles = { Orbit: "orbit shot" }; LORAS[0].strength = 0.9; LORAS[0].needs = "text"; LORAS[0].status = "ready";
      slApply([{ type: "update_project", song: { name: "Hesitation", bpm: 117.45, firstBeat: 0.07 } }, { type: "create_scene", name: "Scene 1" },
               { type: "create_shot", scene: "Scene 1", name: "1.1", prompt: "# Subject\\nLeo walks.", bars: 2, lora: { name: "Camera Motion", style: "Orbit" } }]);
      const scene = SL.p.scenes[0], shot = scene.shots[0];
      shot.takes.push({ id: "take-1", loggedAt: "2026-10-13T17:00:00.000Z", mediaPath: "slate/p/takes/x.mp4", seed: 43, rating: "good", notes: "", imported: true, settings: { preset: "sharp", frames: 107 }, source: { from: "http://10.0.10.5:7870" } });
      SL.have["slate/p/takes/x.mp4"] = true;
      SL.plan[shot.id] = { prompt: "p", mode: "text", chars: 1, warnings: ["This shot will be refused when queued: a clip with a LoRA ..."], plan: { seconds: 4.458, frames: 107, quality: "fh8", preset: "small", loras: [{ id: "camera-motion" }], slot: { bars: 2, seconds: 4.087, frames: 98, start: 0.07, bar: 1, spare: 9 } } };
      SL.open.import = true;
      out.bible = text(slBible()); out.shots = text(slShots()); out.deliver = text(slDeliver());
      SL.p.tf.song.bpm = ""; SL.plan = {}; out.no_song = text(slBible()); out.shots_no_song = text(slWorkspace(scene, shot));
    """)
    if got is None:
        return
    assert "Song · for a music video" in got["bible"] and "One bar is 2.043 s" in got["bible"] and "add up to 2 bars" in got["bible"]
    assert "Slot, in bars" in got["shots"] and "LoRA for this shot" in got["shots"] and "Camera Motion" in got["shots"]
    assert "Slot: 2 bars = 4.09 s (98 frames), from bar 1 at 0:00.1 of the song. The clip is 107 frames (4.46 s): 9 to trim." in got["shots"]
    assert "This shot will be refused when queued" in got["shots"] and "2 bars · 107 frames" in got["shots"] and "LoRA · camera-motion" in got["shots"]
    assert "Import a take rendered on another machine" in got["shots"] and "The other app's address" in got["shots"]
    assert "seed 43 · sharp · 107 frames · imported from 10.0.10.5:7870" in got["shots"]
    assert "a note for people, kept out of the prompt" in got["shots"]
    assert "1 of 1 shots have a finished take" in got["deliver"] and "2 bars" in got["deliver"]
    assert "The tempo is yours to give" in got["no_song"] and "Slot, in bars" not in got["shots_no_song"]


def test_the_bible_tab_sheets_and_takes_offer_faces_places_and_voices():
    """The Bible tab's choices (the bible used automatically, who speaks the dialogue), a character sheet's turnaround,
    a location sheet's plate and pinned pictures, the LoRA on both, the shot's own dialogue choice, and a take's
    "Keep this voice" buttons, each drawn and clicked without the browser."""

    got = run("""
      const find = (node, test) => !node || typeof node !== "object" ? null : Array.isArray(node) ? node.map(n => find(n, test)).find(Boolean) || null : test(node) ? node : find(node.kids, test);
      const button = (root, words) => find(root, n => n.tag === "button" && text(n).includes(words));
      SL.data = { categories: [{ id: "c", label: "Light", setups: [] }], plans: [], profiles: [] }; S.sharp = null; S.ref2va = true; S.presets = { small: { size: [864, 480] } };
      // a film made before the settings: shown (and kept) as it renders; a new one gets the bible and the video model's dialogue
      const before = film(); before.voices = [{ id: "v0", drama: { path: "voice/o.wav", seconds: 3, use: true } }]; SL.p = before; slBible(); out.old = { bible: SL.p.tf.bible, dialogue: SL.p.tf.dialogue };
      answers["/api/slate/projects"] = film(); const home = slHome(); find(home, n => n.tag === "input").value = "New";
      await find(home, n => n.tag === "button" && text(n) === "Create").attrs.onclick(); answers["/api/slate/projects"] = undefined; out.made = SL.p !== before;
      SL.p.characters.push({ id: "char-leo", name: "Leo", images: ["casting/leo.png"] });
      SL.p.locations.push({ id: "loc-1", name: "Walkway", description: "a paved walkway under old trees", images: [] });
      out.bible = text(slBible()); out.film = { ...SL.p.tf };
      // a character sheet: its turnaround, queued from the reference still and pinned when done
      SL.sheet.cast = "char-leo"; const cast = slSheets("cast"); out.cast = text(cast);
      answers["/api/jobs"] = { id: "j-turn" }; await button(cast, "Make a turnaround").attrs.onclick();
      out.turnJob = calls.filter(c => c[0] === "/api/jobs").pop()[1];
      jobs.push({ id: "j-turn", status: "done", outputs: ["scouts/j-turn/turn_sheet.png", "scouts/j-turn/turn_view1.png", "scouts/j-turn/turn_view2.png"] });
      slSheets("cast"); slSheets("cast"); out.pinned = SL.p.characters[0].images;
      // a location sheet: a plate from its description, pinned when done; a LoRA with a strength
      SL.studio = "locations"; const place = slSheets("locations"); out.place = text(place);
      answers["/api/jobs"] = { id: "j-plate" }; await button(place, "Make a plate").attrs.onclick();
      out.plateJob = calls.filter(c => c[0] === "/api/jobs").pop()[1];
      jobs.push({ id: "j-plate", status: "done", outputs: ["scouts/j-plate/scout_s1.png", "scouts/j-plate/sheet.jpg"] });
      slSheets("locations"); out.plates = SL.p.locations[0].images;
      SL.p.locations[0].lora = { id: "camera-motion", strength: 0.7 }; out.placeLora = text(slSheets("locations"));
      // a shot with a line: the shot's own dialogue choice, and on its take a button per speaker
      slApply([{ type: "create_scene", name: "Scene 1" }, { type: "create_shot", scene: "Scene 1", name: "1.1", cast: ["Leo"], prompt: "# Subject\\nLeo waves." }]);
      const scene = SL.p.scenes[0], shot = scene.shots[0]; slTf(shot).lines = [{ characterId: "char-leo", voiceId: null, text: "Over here.", language: "English", sung: false }];
      shot.takes.push({ id: "take-1", loggedAt: "2026-10-13T17:00:00.000Z", mediaPath: "slate/p/takes/x.mp4", seed: 3, rating: "good", notes: "" }); SL.have["slate/p/takes/x.mp4"] = true;
      SL.plan[shot.id] = { prompt: "p", mode: "references", chars: 1, warnings: [], plan: { seconds: 5, frames: 124, quality: "fh8", preset: "small", references: ["casting/leo.png"] } };
      const roll = slRoll(scene, shot); out.roll = text(roll);
      const kept = film(); kept.voices = [{ id: "v1", characterId: "char-leo", drama: { path: "voice/k.wav", seconds: 4.2, use: true } }]; kept.characters = SL.p.characters; answers["/api/slate/voice-from-take"] = kept;
      await button(roll, "Keep this voice for Leo").attrs.onclick(); out.keep = calls.filter(c => c[0] === "/api/slate/voice-from-take").pop()[1]; out.kept = SL.p.voices[0].drama; out.toast = toasts.pop();
    """)
    if got is None:
        return
    assert "Faces, places and voices" in got["bible"] and "Used automatically" in got["bible"]
    assert "Dialogue spoken by the video model" in got["bible"] and "Dialogue voiced by Drama 3 and matched" in got["bible"]
    assert got["film"]["bible"] == "auto" and got["film"]["dialogue"] == "h3"
    assert got["old"] == {"bible": "off", "dialogue": "drama3"} and got["made"]  # a film made before the settings shows what it renders
    assert "Continuity stills" in got["cast"] and "Make a turnaround" in got["cast"] and "Make reference pictures from the sheet" in got["cast"]
    assert got["turnJob"] == {"kind": "turnaround", "title": "Turnaround: Leo", "params": {"image": "casting/leo.png", "size": 1024, "strength": 1, "seed": 0}}
    assert got["pinned"] == ["casting/leo.png", "scouts/j-turn/turn_sheet.png", "scouts/j-turn/turn_view1.png", "scouts/j-turn/turn_view2.png"]
    assert "Plates" in got["place"] and "Make a plate" in got["place"] and "LoRA" in got["place"] and "Camera Motion" in got["place"]
    assert got["plateJob"]["kind"] == "scout" and got["plateJob"]["params"]["count"] == 1 and got["plateJob"]["params"]["prompt"].startswith("Walkway: a paved walkway under old trees")
    assert got["plates"] == ["scouts/j-plate/scout_s1.png"]
    assert "Strength" in got["placeLora"]
    assert "Dialogue: the film's (spoken by the video model)" in got["roll"] and "Dialogue voiced by Drama 3 and matched" in got["roll"] and "BIBLE PICTURES" in got["roll"]
    assert got["keep"] == {"project": "p-test", "shot": got["keep"]["shot"], "take": "take-1", "character": "char-leo"}
    assert got["kept"]["path"] == "voice/k.wav" and got["toast"].startswith("Kept as the voice of Leo, 4.2 s")


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
