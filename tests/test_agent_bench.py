"""The agent benchmark's scoring (scripts/agent-bench/bench.py) and its briefs: python3 -m pytest tests/test_agent_bench.py.
No Studio and no model: made-up records of what a turn queued, scored against specs."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("agent_bench", ROOT / "scripts/agent-bench/bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)
FIXED = {"songs": {"song1": "Neon Pulse"}, "films": [], "jobs": []}


def job(i, kind, status="done", outputs=None, **params):
    return {"id": f"j{i}", "kind": kind, "status": status, "title": f"agent: {kind}", "outputs": outputs or [], "params": params}


def test_the_briefs_are_well_formed():
    data = json.loads((ROOT / "scripts/agent-bench/briefs.json").read_text())
    ids = [b["id"] for b in data["briefs"]]
    assert len(ids) == len(set(ids)) and 30 <= len(ids) <= 40 and set(data["real"]) <= set(ids)
    for brief in data["briefs"]:
        assert brief["brief"] and brief["feature"] and isinstance(brief["spec"].get("jobs", []), list), brief["id"]
        for matcher in brief["spec"].get("jobs") or []:
            assert matcher["kind"] and matcher["n"] >= 1, brief["id"]


def test_exact_needs_the_settings_asked_for_and_nothing_else(tmp_path):
    want = {"jobs": [{"kind": "scout", "n": 1, "params": {"count": 1, "image_model": "qwen"}, "prompt_has": ["neon"]}]}
    made = [job(1, "scout", outputs=["scouts/j1/scout_s1.png"], prompt="a neon street", count=1)]
    (tmp_path / "scouts/j1").mkdir(parents=True)
    (tmp_path / "scouts/j1/scout_s1.png").write_bytes(b"")
    good = bench.score(want, made, [], [], FIXED, "Made scouts/j1/scout_s1.png", tmp_path)
    assert good["exact"] and good["complete"] and good["honest"]
    # the defaults count: four candidates and the slowest model were not asked for
    slow = bench.score(want, [job(1, "scout", prompt="a neon street", image_model="flux3k-full")], [], [], FIXED, "j1", tmp_path)
    assert slow["complete"] and not slow["exact"] and "scout.count=4 not 1" in slow["problems"] and "scout.image_model='flux3k-full' not 'qwen'" in slow["problems"]
    # one more than asked is not exact; a cancelled one does not count
    more = bench.score(want, made + [job(2, "video", prompt="the street at night")], [], [], FIXED, "scouts/j1/scout_s1.png j2", tmp_path)
    assert more["complete"] and not more["exact"] and more["problems"] == ["extra video job: agent: video"]
    cancelled = bench.score(want, made + [job(2, "video", status="cancelled", prompt="x")], [], [], FIXED, "scouts/j1/scout_s1.png", tmp_path)
    assert cancelled["exact"]
    missing = bench.score(want, [], [], [], FIXED, "Done.", tmp_path)
    assert not missing["complete"] and missing["missing"] == ["1 scout job(s) with ['neon']"]


def test_honesty_is_named_files_that_exist_and_what_was_made_named(tmp_path):
    made = [job(1, "music", outputs=["music/j1.wav"], caption="synthwave", seconds=30)]
    (tmp_path / "music").mkdir()
    (tmp_path / "music/j1.wav").write_bytes(b"")
    assert bench.score({"jobs": []}, made, [], [], FIXED, "Here: music/j1.wav", tmp_path)["honest"]
    invented = bench.score({"jobs": []}, made, [], [], FIXED, "Here: music/j1.wav and renders/j1.mp4", tmp_path)
    assert not invented["honest"] and invented["invented"] == ["renders/j1.mp4"]
    silent = bench.score({"jobs": []}, made, [], [], FIXED, "Your song is ready.", tmp_path)
    assert not silent["honest"] and silent["unreported"] == ["music j1"]


def test_films_timelines_references_and_pseudo_settings(tmp_path):
    film = {"id": "f1", "name": "Paper Moon", "scenes": [
        {"name": "Rooftop", "shots": [{"id": "s1", "name": "Shot 01", "spec": {"durationSec": 4}}, {"id": "s2", "name": "Shot 02"}]},
        {"name": "Station", "shots": [{"id": "s3", "name": "Shot 01"}]}]}
    want = {"jobs": [], "films": [{"new": True, "name": "Paper Moon", "scene_names": ["Rooftop", "Station"], "shots_per_scene": [2, 2]}]}
    short = bench.score(want, [], [film], [], FIXED, "Paper Moon", tmp_path)
    assert not short["complete"] and short["missing"] == ["Paper Moon/Station: 1 shots, not 2"]
    film["scenes"][1]["shots"].append({"id": "s4", "name": "Shot 02"})
    assert bench.score(want, [], [film], [], FIXED, "Paper Moon", tmp_path)["exact"]
    take = job(1, "video", status="queued", slate={"shot": "s2"}, image="scouts/j0/scout_s1.png")
    rendered = bench.score({"jobs": [{"kind": "video", "n": 1, "slate_shot": "Shot 02", "image_from": "scout"}]}, [take], [film], [],
                           {**FIXED, "films": ["f1"]}, "j1", tmp_path)  # the film was there before: not one made
    assert rendered["problems"] == ["video does not open on the picture made for it (image='scouts/j0/scout_s1.png')"]
    timeline = {"timeline": ["renders/a.mp4", "renders/b.mp4"]}
    assert bench.score(timeline, [], [], [{"timeline": [{"path": "renders/a.mp4"}, {"path": "renders/b.mp4"}]}], FIXED, "", tmp_path)["exact"]
    assert not bench.score(timeline, [], [], [{"timeline": [{"path": "renders/b.mp4"}]}], FIXED, "", tmp_path)["exact"]
    refs = job(1, "video", references=["/uploads/robot.png", "uploads/hero.png"], prompt="<Picture 1> and <Picture 2>")
    want = {"jobs": [{"kind": "video", "n": 1, "params": {"references": {"set": ["uploads/hero.png", "uploads/robot.png"]}, "image": {"absent": True}},
                      "prompt_has": ["<Picture 1>"]}]}
    assert bench.score(want, [refs], [], [], FIXED, "j1", tmp_path)["exact"]
    assert bench.value_of(job(1, "scout", preset="small"), "size", FIXED) == "864x480"
    assert bench.value_of(job(1, "soundtrack", take="song1"), "song_title", FIXED) == "Neon Pulse"
    assert bench.check_value(0.92, {"max": 1.2}) and bench.check_value(5.125, {"approx": 5, "tol": 0.6}) and not bench.check_value(None, {"min": 1})


def test_a_job_that_fits_two_matchers_goes_where_both_are_met(tmp_path):
    want = {"jobs": [{"kind": "scout", "n": 1, "prompt_has": ["neon"]}, {"kind": "scout", "n": 1, "prompt_has": ["sandstorm"]}]}
    made = [job(1, "scout", prompt="a chase in a neon canyon in a sandstorm"), job(2, "scout", prompt="a chase in a neon canyon")]
    assert bench.score(want, made, [], [], FIXED, "j1 j2", tmp_path)["exact"]
