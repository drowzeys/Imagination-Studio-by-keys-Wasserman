"""Slate's page code (app/static/slate.js), run in node with the page's helpers stood in for:
python3 tests/test_slate_page.py. Skipped where node is not installed; no browser, no GPU, no network."""
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = r"""
const vm = require("vm"), fs = require("fs");
globalThis.S = { qualities: { fh8: "FastH3" }, durations: { 5: 124, 8: 192, 10: 243, 15: 362 }, lengths: { fps: 24, step: 17, shortest: 22, longest: 362 } };
globalThis.render = () => {}; globalThis.toast = text => (globalThis.toasts = globalThis.toasts || []).push(text);
globalThis.api = async (path, body) => (globalThis.calls = globalThis.calls || []).push([path, body]) && (globalThis.answers || {})[path] || {};
globalThis.jobs = []; globalThis.LORAS = [{ id: "camera-motion", installed: true, name: "Camera Motion" }, { id: "gone", installed: false }];
globalThis.lorasOf = obj => (obj.loras || []).filter(p => LORAS.some(r => r.id === p.id && r.installed));
globalThis.poll = async () => {};
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

    if not shutil.which("node"):
        return None
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
                    settings: { preset: "sharp", frames: 124, quality: "fh8", loras: [{ id: "camera-motion" }] }, source: { from: "http://10.100.10.5:7870", job: "w2" } } } };
      const take = await slImportTake(shot, { from: "http://10.100.10.5:7870", job: "w2", seed: "", notes: "" });
      out.calls = calls.map(c => c[0]); out.sent = calls[1][1]; out.takes = shot.takes.map(t => t.id); out.have = SL.have[take.mediaPath];
      out.line = slTakeLine(take); out.best = slBest(shot);
    """)
    if got is None:
        return
    assert got["calls"] == ["/api/slate/projects/p-test", "/api/slate/takes"]  # the film is saved first: the server reads the shot from the file
    assert got["sent"] == {"projectId": "p-test", "shot": got["sent"]["shot"], "from": "http://10.100.10.5:7870", "job": "w2"}  # empty fields are not sent
    assert got["takes"] == ["take-1"] and got["have"] is True and got["best"] == "slate/p-test/takes/x.mp4"
    assert got["line"] == "seed 43 · sharp · 124 frames · fh8 · LoRA camera-motion · imported from 10.100.10.5:7870 · 2026-10-13 17:00"


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
