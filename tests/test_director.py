"""The Director (app/director.py): a logline to a written, cast, rendered, checked and cut film. A scripted brain, a
queue whose jobs finish at once and a stand-in QA gate: no GPU, no network, no model."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "ImaginationStudio"))
sys.path.insert(0, str(ROOT / "app"))
from fastapi.testclient import TestClient  # noqa: E402

import director  # noqa: E402
import server  # noqa: E402  (attaches Slate and the Director)
import slate  # noqa: E402

from test_slate_page import HARNESS, need_node  # noqa: E402

HOME = server.HOME
client = TestClient(server.app)

BIBLE = {"world": "A small coastal town in late autumn, grey light, wet stone.",
         "characters": [{"name": "Ada", "age": "34", "gender": "female", "hair": "short dark hair", "clothing": "a yellow raincoat",
                         "voice": {"ageGender": "a woman in her thirties", "timbre": "warm"}},
                        {"name": "Bo", "age": "60", "gender": "male", "hair": "grey beard", "clothing": "a fisherman's sweater"}],
         "locations": [{"name": "The Harbour", "interiorExterior": "exterior", "description": "A stone harbour with small boats", "timeOfDay": "dawn"}]}
SCENES = {"scenes": [{"name": "The Letter", "synopsis": "Ada finds a letter in a boat."}, {"name": "The Answer", "synopsis": "Bo reads it."}]}


def shots(bad: bool = False, later: bool = False) -> dict:
    """Two shots of a scene; the second scene's are Bo's."""

    who = "Bo" if later else "Ada"
    cast = [who, "Nobody"] if bad else [who]
    act = "reads the letter by the boats" if later else "kneels and lifts a letter from a boat"
    return {"shots": [{"name": "Wide", "intent": "Where we are", "prompt": f"# Subject\n{who} walks along the harbour wall.\n# Camera\nA slow track.",
                       "size": "WS", "movement": "track", "cast": cast, "location": "The Harbour", "lines": [], "soundscape": "Gulls and waves."},
                      {"name": "Close", "intent": "She sees it", "prompt": f"# Subject\n{who} {act}.",
                       "size": "CU", "cast": [who], "location": "The Harbour", "lines": [{"speaker": who, "text": "Who left this here?"}]}]}


class Brain:
    """Answers by what is asked; the first shots answer of a run can be one that does not hold up."""

    def __init__(self, bad_first_shots: bool = False):
        self.asked, self.bad = [], bad_first_shots

    def __call__(self, backend, system, prompt, tier, endpoint="", model=""):
        self.asked.append(prompt)
        if "Write the characters" in prompt or "Write the locations" in prompt:
            return "Here you go:\n```json\n" + json.dumps(BIBLE) + "\n```"
        if "Break this film into" in prompt:
            return json.dumps(SCENES)
        if "Write exactly" in prompt:
            bad, self.bad = self.bad, False
            return json.dumps(shots(bad, later="CURRENT SCENE: The Answer" in prompt))
        raise AssertionError("an unexpected question: " + prompt[:200])


class Queue:
    """The Studio's queue stood in for: a job is done when queued (its files written), unless ``hold`` says otherwise."""

    def __init__(self, fail_qa="", hold=False):
        self.jobs, self.bodies, self.fail_qa, self.hold, self.checked, self.made = {}, [], fail_qa, hold, [], []

    def submit(self, body):
        kind, params = body["kind"], body.get("params") or {}
        job_id = f"j{len(self.jobs) + 1}"
        if kind == "video":
            outputs = [params["output"]]
        elif kind == "scout":
            outputs = [f"scouts/{job_id}/scout_1.png", f"scouts/{job_id}/sheet.jpg"]
        elif kind == "turnaround":
            outputs = [f"turns/{job_id}/turn_sheet.png", f"turns/{job_id}/turn_view1.png", f"turns/{job_id}/other.txt"]
        elif kind == "export":
            outputs = [f"exports/{job_id}.mp4"]
        else:
            raise AssertionError(kind)
        for path in outputs:
            (HOME / path).parent.mkdir(parents=True, exist_ok=True)
            (HOME / path).write_bytes(b"x")
        job = {"id": job_id, "kind": kind, "status": "queued" if self.hold else "done", "params": params, "outputs": outputs, "title": body.get("title")}
        self.jobs[job_id] = job
        self.bodies.append(body)
        self.made.append(job)
        return job

    def lookup(self, job_id):
        return self.jobs.get(job_id)

    def cancel(self, job_id):
        if job_id in self.jobs:
            self.jobs[job_id]["status"] = "cancelled"

    def qa(self, body):
        """A QA job: the first clip whose prompt has the words of fail_qa fails."""

        self.checked.append(body["path"])
        job_id = f"q{len(self.jobs) + 1}"
        failed = bool(self.fail_qa) and self.fail_qa in body.get("prompt", "")
        if failed:
            self.fail_qa = ""
        self.jobs[job_id] = {"id": job_id, "kind": "qa", "status": "done", "qa": {"passed": not failed, "takes": [{"passed": not failed, "reason": "the face changes" if failed else ""}]}}
        return self.jobs[job_id]

    def videos(self):
        return [b for b in self.bodies if b["kind"] == "video"]


@pytest.fixture
def studio(monkeypatch):
    monkeypatch.setattr(director, "TICK", 0.01)
    monkeypatch.setattr(director, "ref2va", lambda: False)
    made = {}
    monkeypatch.setattr(director, "editor_new", lambda body: made.setdefault("edit", {"id": "pcut-1", "name": body["name"], "timeline": []}))
    monkeypatch.setattr(director, "editor_save", lambda pid, data: made.update(saved=json.loads(json.dumps(data))))
    director.runs.clear()

    def use(queue: Queue, brain: Brain):
        monkeypatch.setattr(director, "submit", queue.submit)
        monkeypatch.setattr(director, "job_of", queue.lookup)
        monkeypatch.setattr(director, "cancel", queue.cancel)
        monkeypatch.setattr(director, "qa_start", queue.qa)
        monkeypatch.setattr(slate, "ask", brain)
        return made
    return use


def new_film(logline="A harbour keeper finds a letter that was never sent.") -> str:
    film = client.post("/api/slate/projects", json={"name": "The Harbour"}).json()
    if logline:
        film["logline"] = logline
        client.put(f"/api/slate/projects/{film['id']}", json=film)
    return film["id"]


def until(project: str, states=("done", "failed", "stopped"), seconds=20) -> dict:
    end = time.time() + seconds
    while time.time() < end:
        run = client.get(f"/api/slate/direct/{project}").json()
        if run.get("state") in states and not run.get("running"):
            return run
        time.sleep(0.02)
    raise AssertionError(f"the run did not reach {states}: {run}")


def test_a_logline_becomes_a_checked_cut_film(studio):
    queue, brain = Queue(fail_qa="lifts a letter"), Brain(bad_first_shots=True)
    made = studio(queue, brain)
    project = new_film()
    started = client.post("/api/slate/direct", json={"project": project, "scenes": 2, "shotsPerScene": 2, "seconds": 5, "retakes": 1, "cut": True})
    assert started.status_code == 200, started.text
    run = until(project)
    assert run["state"] == "done", run
    film = slate.get_project(project)

    # writing: the bible, the scenes and their shots, through the First AD's actions
    assert [c["name"] for c in film["characters"]] == ["Ada", "Bo"] and film["locations"][0]["name"] == "The Harbour"
    assert film["world"].startswith("A small coastal town") and film["voices"][0]["characterId"] == film["characters"][0]["id"]
    assert [s["name"] for s in film["scenes"]] == ["The Letter", "The Answer"] and all(len(s["shots"]) == 2 for s in film["scenes"])
    first, second = film["scenes"][0]["shots"]
    assert first["tf"]["start"] == "text" and second["tf"]["start"] == "previous"  # a cut, then one continuous move
    assert first["tf"]["cast"] == [film["characters"][0]["id"]] and first["tf"]["location"] == film["locations"][0]["id"]
    assert second["tf"]["lines"][0]["text"] == "Who left this here?" and second["tf"]["lines"][0]["characterId"] == film["characters"][0]["id"]
    assert first["spec"]["durationSec"] == 5 and first["spec"]["size"] == "WS"
    # the shots answer that cast someone not in the bible was asked for again, once, with the reason
    again = [p for p in brain.asked if "YOUR LAST ANSWER COULD NOT BE USED" in p]
    assert len(again) == 1 and "Nobody" in again[0]

    # casting: a reference still and its turnaround for each character, a plate for the place, all pinned
    kinds = [b["kind"] for b in queue.bodies]
    assert kinds.count("scout") == 3 and kinds.count("turnaround") == 2
    ada = film["characters"][0]
    assert ada["images"][0].endswith("scout_1.png") and any(p.endswith("turn_sheet.png") for p in ada["images"])
    assert not any(p.endswith(("sheet.jpg", "other.txt")) for p in ada["images"])
    assert len(film["locations"][0]["images"]) == 1
    turn = next(b for b in queue.bodies if b["kind"] == "turnaround")
    assert turn["params"]["image"].endswith("scout_1.png")

    # rendering: every shot once, and the take that failed its check once more at the next seed
    videos = queue.videos()
    assert len(videos) == 5 and all(v["params"]["slate"]["project"] == project for v in videos)
    of = lambda shot: [v for v in videos if v["params"]["slate"]["shot"] == shot["id"]]
    order = [s for sc in film["scenes"] for s in sc["shots"]]
    assert [len(of(s)) for s in order] == [1, 2, 1, 1]
    assert of(second)[1]["params"]["seed"] == of(second)[0]["params"]["seed"] + 1
    # the shot that opens on the shot before was queued once that one had its take, from its clip, and so was its retake
    assert {v["params"]["first_frame_from"] for v in of(second)} == {of(first)[0]["params"]["output"]}
    takes = second["takes"]
    assert len(takes) == 2 and [t["rating"] for t in takes] == ["good", "no-good"]  # newest first: the retake is the good one
    assert takes[0]["seed"] == takes[1]["seed"] + 1 and takes[0]["origin"].startswith("The Director: retake")
    assert all([t["rating"] for t in s["takes"]] == ["good"] for s in order if s["id"] != second["id"])
    assert run["counts"]["retakes"] == 1 and run["counts"]["settled"] == 4 and run["counts"]["shots"] == 4

    # cutting: the best take of each shot, in order, on an Editor timeline and exported
    timeline = made["saved"]["timeline"]
    assert [c["path"] for c in timeline] == [of(s)[-1]["params"]["output"] for s in order]
    export = next(b for b in queue.bodies if b["kind"] == "export")
    assert export["params"]["clips"] == timeline
    files = run["cut"]["files"]
    assert files["mp4"].startswith("exports/") and files["fcpxml"] == "/api/export/timeline/pcut-1.fcpxml" and files["zip"].endswith(".zip")
    assert any("Done:" in line["text"] for line in run["log"])


def test_a_restart_continues_and_a_stop_stops(studio):
    queue, brain = Queue(hold=True), Brain()
    studio(queue, brain)
    project = new_film()
    assert client.post("/api/slate/direct", json={"project": project, "scenes": 1, "shotsPerScene": 2, "seconds": 5, "cut": False}).status_code == 200
    end = time.time() + 10
    while time.time() < end and not any(b["kind"] == "scout" for b in queue.bodies):
        time.sleep(0.02)
    # the pictures are made (held in the queue until now)
    for job in list(queue.jobs.values()):
        job["status"] = "done"
    while time.time() < end and len(queue.videos()) < 1:
        for job in queue.jobs.values():
            if job["kind"] != "video":
                job["status"] = "done"
        time.sleep(0.02)
    assert client.post("/api/slate/direct", json={"project": project}).status_code == 409  # one run a film
    assert queue.videos()  # the first shot is queued; the next opens on the shot before and waits for it
    # stopped on request: the jobs still waiting are taken out of the queue
    assert client.post(f"/api/slate/direct/{project}/stop", json={}).json()["stopped"] is True
    run = until(project)
    assert run["state"] == "stopped" and [j["status"] for j in queue.made if j["kind"] == "video"] == ["cancelled"]
    asked = len(brain.asked)

    # a restart: the queue's memory is gone, the file says the run was rendering, and the first clip got made
    status = director.status_file(project)
    saved = json.loads(status.read_text())
    saved["state"] = "rendering"
    status.write_text(json.dumps(saved))
    first = queue.videos()[0]["params"]["output"]
    assert (HOME / first).is_file()
    director.runs.clear()
    fresh = Queue()
    studio(fresh, brain)
    director.resume_all()
    run = until(project)
    assert run["state"] == "done", run
    assert len(brain.asked) == asked  # nothing written twice
    assert not any(b["kind"] in ("scout", "turnaround") for b in fresh.bodies)  # nor cast twice
    film = slate.get_project(project)
    shots_ = film["scenes"][0]["shots"]
    # the clip made before the restart is kept as the first shot's take; only the other is rendered, from it
    assert len(queue.videos()) == 1 and shots_[0]["takes"][0]["mediaPath"] == first
    assert [v["params"]["slate"]["shot"] for v in fresh.videos()] == [shots_[1]["id"]] and fresh.videos()[0]["params"]["first_frame_from"] == first
    assert all(s["takes"][0]["rating"] == "good" for s in shots_)
    assert any("Picked up after a restart" in line["text"] for line in run["log"])


def test_the_director_asks_for_a_logline_and_keeps_rendered_shots(studio):
    queue, brain = Queue(), Brain()
    studio(queue, brain)
    bare = new_film(logline="")
    assert client.post("/api/slate/direct", json={"project": bare}).status_code == 400
    assert client.post("/api/slate/direct", json={"project": "nope"}).status_code == 404
    assert client.get(f"/api/slate/direct/{bare}").json()["state"] == "none"
    # a film the First AD already laid out: its scene and shot stay, its rendered take is kept as it is
    project = new_film()
    film = slate.get_project(project)
    for action in ({"type": "add_character", "name": "Ada"}, {"type": "add_location", "name": "The Harbour", "description": "stone"},
                   {"type": "create_scene", "name": "One"}, {"type": "create_shot", "scene": "One", "name": "Made", "prompt": "# Subject\nWaves."},
                   {"type": "create_shot", "scene": "One", "name": "New", "prompt": "# Subject\nGulls.", "opensOn": "previous"}):
        assert "✓" in director.apply(film, action)
    film["characters"][0]["images"] = ["uploads/ada.png"]
    film["locations"][0]["images"] = ["uploads/harbour.png"]
    (HOME / "uploads").mkdir(exist_ok=True)
    (HOME / "takes-made.mp4").write_bytes(b"x")
    film["scenes"][0]["shots"][0]["takes"] = [{"id": "take-old", "mediaPath": "takes-made.mp4", "rating": "circled"}]
    slate.write(film)
    assert client.post("/api/slate/direct", json={"project": project, "cut": False}).status_code == 200
    run = until(project)
    assert run["state"] == "done", run
    assert brain.asked == [] and queue.bodies and all(b["kind"] == "video" for b in queue.bodies)
    assert len(queue.videos()) == 1 and queue.videos()[0]["params"]["first_frame_from"] == "takes-made.mp4"
    film = slate.get_project(project)
    assert film["scenes"][0]["shots"][0]["takes"] == [{"id": "take-old", "mediaPath": "takes-made.mp4", "rating": "circled"}]


def test_two_fast_starts_start_one_run(studio, monkeypatch):
    import threading

    project, release = new_film(), threading.Event()
    reading = director.film_of
    monkeypatch.setattr(director, "film_of", lambda pid: time.sleep(0.2) or reading(pid))  # a slow read between the look and the start
    monkeypatch.setattr(director, "work", lambda run, stop: release.wait(10))
    answers = []
    posts = [threading.Thread(target=lambda: answers.append(client.post("/api/slate/direct", json={"project": project}).status_code)) for _ in range(2)]
    for one in posts:
        one.start()
    for one in posts:
        one.join(10)
    release.set()
    director.runs[project]["thread"].join(10)
    left = json.loads(director.status_file(project).read_text())  # the stand-in work leaves the film at work: a later
    left["state"] = "stopped"                                     # resume_all would pick it up with the real one
    director.status_file(project).write_text(json.dumps(left))
    assert sorted(answers) == [200, 409]


def test_nothing_starts_on_import():
    """The Director's pick-up after a restart and the captioner start with the studio (server.py's __main__), never
    when a script or a test imports it."""

    source = (ROOT / "app" / "server.py").read_text()
    main = source[source.index('if __name__ == "__main__":'):]
    assert "director.resume_later()" in main and 'caption_router.captioner["start"]()' in main
    assert source.count('caption_router.captioner["start"]()') == 1 and "STUDIO_PIDFILE" in main.split('captioner["start"]')[0]
    assert "Timer" not in (ROOT / "app" / "director.py").read_text().split("def resume_later")[0]


def test_apply_mirrors_the_first_ads_actions():
    film = {"id": "p-x", "defaults": {"durationSec": 8}, "scenes": [], "characters": [], "locations": []}
    assert "✓ Added character" in director.apply(film, {"type": "add_character", "name": "Ada", "hair": "short"})
    assert "Changed character" in director.apply(film, {"type": "update_character", "character": "ada", "clothing": "a raincoat"})
    assert film["characters"][0]["hair"] == "short" and film["characters"][0]["clothing"] == "a raincoat" and len(film["characters"]) == 1
    assert film["characters"][0]["keyLightSide"] == "Key light from left"
    assert "✗ Scene" in director.apply(film, {"type": "create_shot", "scene": "nowhere"})
    director.apply(film, {"type": "create_scene", "name": "A"})
    assert "already exists" in director.apply(film, {"type": "create_scene", "name": "a"})
    director.apply(film, {"type": "create_shot", "scene": "A", "cast": ["Ada"], "lines": [{"speaker": "Ada", "text": " Hello. "}], "opensOn": "previous", "bars": 2})
    shot = film["scenes"][0]["shots"][0]
    assert shot["name"] == "Shot 01" and shot["tf"]["start"] == "previous" and shot["tf"]["bars"] == 2
    assert shot["tf"]["lines"] == [{"characterId": film["characters"][0]["id"], "voiceId": None, "text": "Hello.", "language": "English", "sung": False}]
    assert "Updated" in director.apply(film, {"type": "update_shot", "shot": "Shot 01", "prompt": "# Subject\nNew."})
    assert "✗ Unknown action" in director.apply(film, {"type": "dance"})


def test_the_panel_in_the_first_ad_tab():
    need_node()
    import subprocess

    code = r"""
    SL.p = film(); SL.p.logline = "A keeper finds a letter."; SL.view = "ad";
    answers["/api/music/takes"] = [{ id: "song-1", title: "Low Tide" }];
    DR.songs = [{ id: "song-1", title: "Low Tide" }];
    DR.st[SL.p.id] = { state: "none" };
    out.idle = text(slADView());
    out.button = !!(function find(n) { return n && typeof n === "object" && (n.tag === "button" && text(n).includes("Direct it") || (n.kids || []).flat(9).some(find)); })(slADView());
    globalThis.jobs = [{ id: "j7", status: "running" }];
    DR.st[SL.p.id] = { state: "rendering", running: true, done: ["writing", "casting"], asked: { cut: true }, counts: { shots: 9, rendered: 7, settled: 6, retakes: 2, cast: 3, castDone: 3 },
      current: { text: "Rendering: One · Close", job: "j7" }, log: [{ at: "2026-01-01T10:00:00.000Z", text: "The Director starts" }] };
    out.running = text(directorPanel(SL.p));
    DR.st[SL.p.id] = { state: "done", running: false, done: ["writing", "casting", "rendering", "cutting"], asked: { cut: true }, counts: { shots: 9, rendered: 9, settled: 9, retakes: 2 },
      cut: { files: { mp4: "exports/x.mp4", fcpxml: "/api/export/timeline/p1.fcpxml", edl: "/api/export/timeline/p1.edl", zip: "/api/export/timeline/p1.zip" } }, log: [] };
    out.done = text(directorPanel(SL.p));
    DR.st[SL.p.id] = { state: "done", running: false, done: ["writing", "casting", "rendering", "cutting"], asked: { cut: true },
      counts: { shots: 3, rendered: 2, settled: 3, passed: 1, failedCheck: 1, refused: 1, retakes: 0 }, order: ["a", "b", "c"],
      shots: { a: { name: "Wide", settled: true, outcome: "passed" }, b: { name: "Pier Fall", settled: true, outcome: "refused", why: "needs about 77 GB" },
               c: { name: "Night Ferry", settled: true, outcome: "failed", why: "the face changes" } }, log: [] };
    out.left = text(directorPanel(SL.p));
    DR.st[SL.p.id] = { state: "none" }; out.form = text(directorPanel(SL.p));
    out.sheet = slSheetPicture({ name: "Wren", age: "40", clothing: "a long wool overcoat and a felt hat" });
    out.plain = slSheetPicture({ name: "Ada", clothing: "a grey sweater" });
    out.blank = slSheetPicture(BLANK);
    calls.length = 0; DR.st[SL.p.id] = { state: "none" }; await drStart(SL.p);
    out.posted = calls.find(c => c[0] === "/api/slate/direct");
    """
    with tempfile.TemporaryDirectory() as scratch:
        load = 'vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));\n'
        assert HARNESS.count(load) == 1
        (Path(scratch) / "harness.js").write_text(HARNESS.replace(load, load + 'vm.runInThisContext(fs.readFileSync(process.argv[4], "utf8"));\n'))
        (Path(scratch) / "test.js").write_text(code.replace("BLANK", json.dumps(BLANK_SHEET)))
        done = subprocess.run(["node", str(Path(scratch) / "harness.js"), str(ROOT / "app/static/slate.js"), str(Path(scratch) / "test.js"),
                               str(ROOT / "app/static/director.js")], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert "Direct the whole film" in out["idle"] and "A keeper finds a letter." in out["idle"] and out["button"]
    assert "Cut it when done" in out["idle"] and "One retake" in out["idle"] and "Low Tide" in out["idle"]
    assert "7 of 9 shots rendered" in out["running"] and "2 retakes" in out["running"] and "job j7" in out["running"]
    assert "Stop" in out["running"] and "Rendering: One · Close" in out["running"] and "The log · 1 lines" in out["running"]
    assert "Direct it" not in out["running"]  # the form is put away while it runs
    assert "Watch the cut" in out["done"] and "FCPXML" in out["done"] and "Review takes" in out["done"]
    assert "1 passed the check" in out["left"] and "1 failed the check" in out["left"] and "1 refused by the queue" in out["left"]
    assert "Left out of the cut · 2" in out["left"] and "Pier Fall: refused by the queue: no clip was made (needs about 77 GB)" in out["left"]
    assert "Night Ferry: failed the check: its take is in the Library" in out["left"] and "Wide:" not in out["left"]
    assert "No retake" in out["form"] and "Keep it" not in out["form"]
    # the page's portrait prompt frames a costume as the Director's does
    assert "knees up" in out["sheet"] and "no props" not in out["sheet"] and "no props" in out["plain"]
    assert out["blank"] == director.sheet_picture(BLANK_SHEET)  # the page's portrait and the Director's say the same
    posted = out["posted"][1]
    assert posted["project"] == "p-test" and posted["logline"] == "A keeper finds a letter." and posted["scenes"] == 3 and posted["cut"] is True and posted["retakes"] == 1


def test_with_a_song_the_cut_is_the_rough_cut_on_the_beat(studio, monkeypatch):
    queue, brain = Queue(), Brain()
    studio(queue, brain)
    asked, stages = [], iter(["export", "export", "files", "done"])
    files = {"fcpxml": "exports/The-Harbour-p1.fcpxml", "edl": "exports/The-Harbour-p1.edl", "zip": "exports/The-Harbour-p1-resolve.zip"}
    monkeypatch.setattr(director, "rough_make", lambda body: asked.append(body) or {"project": "p1", "job": "e1", "beats": {"bpm": 120}})
    monkeypatch.setattr(director, "rough_status", lambda pid: (lambda stage: {"project": pid, "stage": stage, "job": "e1",
                                                                                **({"video": "exports/e1.mp4", "files": files} if stage == "done" else {})})(next(stages)))
    project = new_film()
    assert client.post("/api/slate/direct", json={"project": project, "scenes": 1, "shotsPerScene": 2, "seconds": 5, "song": "song-1"}).status_code == 200
    run = until(project)
    assert run["state"] == "done", run
    film = slate.get_project(project)
    best = [s["takes"][0]["mediaPath"] for s in film["scenes"][0]["shots"]]
    assert len(asked) == 1 and asked[0]["clips"] == best and asked[0]["song"] == "song-1"
    assert asked[0]["cut"] == "natural" and asked[0]["foley"] is False and asked[0]["order"] == "given"
    assert not any(b["kind"] == "export" for b in queue.bodies)  # the rough cut exports; the Editor's way is not taken
    assert run["cut"]["project"] == "p1" and run["cut"]["files"] == {"mp4": "exports/e1.mp4", **files}

    # a rough cut that fails says why; one this app cannot make falls back to the Editor
    monkeypatch.setattr(director, "rough_status", lambda pid: {"stage": "failed", "error": "the export failed"})
    again = new_film()
    client.post("/api/slate/direct", json={"project": again, "scenes": 1, "shotsPerScene": 1, "seconds": 5, "song": "song-1"})
    run = until(again)
    assert run["state"] == "failed" and "the export failed" in run["error"]

    def refuse(body):
        raise director.HTTPException(400, "no steady beat was found in that song")
    monkeypatch.setattr(director, "rough_make", refuse)
    third = new_film()
    client.post("/api/slate/direct", json={"project": third, "scenes": 1, "shotsPerScene": 1, "seconds": 5, "song": "song-1"})
    run = until(third)
    assert run["state"] == "done" and run["cut"]["files"]["fcpxml"].endswith(".fcpxml") and any("no steady beat" in l["text"] for l in run["log"])


class Picky(Queue):
    """A queue that refuses the clips whose prompt has ``refuse`` (the memory guard's refusal, before the job starts)
    and stops the ones whose prompt has ``stop`` (the workload limits, mid-render), ``stops`` times."""

    def __init__(self, refuse="", stop="", stops=0, **kw):
        super().__init__(**kw)
        self.refuse, self.stop, self.stops = refuse, stop, stops

    def submit(self, body):
        prompt = (body.get("params") or {}).get("prompt") or ""
        if body["kind"] == "video" and self.refuse and self.refuse in prompt:
            raise director.HTTPException(400, "a clip from references needs about 77 GB at its peak; this machine has 89 GB for it "
                                              "and keeps 12 GB free as its floor, so it is refused")
        job = super().submit(body)
        if body["kind"] == "video" and self.stop and self.stop in prompt and self.stops:
            self.stops -= 1
            (HOME / job["outputs"][0]).unlink()
            job.update(status="failed", outputs=[], error="the render was stopped by this machine's workload limits: GPU temperature ceiling or thermal slowdown")
        return job


def test_a_refused_shot_is_never_called_rendered_and_a_failed_take_stays_out_of_the_cut_but_in_the_library(studio):
    # Bo's wide is refused by the queue; Ada's close fails its check with no retakes asked
    queue, brain = Picky(refuse="Bo walks", fail_qa="lifts a letter"), Brain()
    made = studio(queue, brain)
    project = new_film()
    assert client.post("/api/slate/direct", json={"project": project, "scenes": 2, "shotsPerScene": 2, "seconds": 5, "retakes": 0, "cut": True}).status_code == 200
    run = until(project)
    assert run["state"] == "done", run
    film = slate.get_project(project)
    (wide, close), (bo_wide, bo_close) = [sc["shots"] for sc in film["scenes"]]
    c = run["counts"]
    assert c["shots"] == 4 and c["passed"] == 2 and c["refused"] == 1 and c["failedCheck"] == 1 and c["leftOut"] == 2 and c["retakes"] == 0
    assert c["rendered"] == 3  # the refused shot made no clip and is not counted as one
    assert run["shots"][bo_wide["id"]]["outcome"] == "refused" and "77 GB" in run["shots"][bo_wide["id"]]["why"]
    assert run["shots"][close["id"]]["outcome"] == "failed"
    assert len([v for v in queue.videos() if v["params"]["slate"]["shot"] == bo_wide["id"]]) == 0  # never queued again at another seed
    done = next(line["text"] for line in run["log"] if line["text"].startswith("Done:"))
    assert "rendered and checked" not in done and "2 of 4 shots passed the check" in done
    assert f"1 refused by the queue: {bo_wide['name']}" in done and "1 failed the check (in the Library, out of the cut)" in done
    assert done.endswith("cut without 2 shots")
    assert any(line["text"].startswith("Left out of the cut:") and "refused by the queue" in line["text"] for line in run["log"])
    # the cut: only the takes that passed; the failed take is rated no-good, among the shot's takes and in the Library
    failed_take = close["takes"][0]
    assert failed_take["rating"] == "no-good"
    assert [c_["path"] for c_ in made["saved"]["timeline"]] == [wide["takes"][0]["mediaPath"], bo_close["takes"][0]["mediaPath"]]
    listed = {m["path"]: m for m in client.get("/api/library").json()}
    assert listed[failed_take["mediaPath"]].get("failed") is True and not listed[wide["takes"][0]["mediaPath"]].get("failed")
    # circled, the failed take is cut in when the film is directed again
    film["scenes"][0]["shots"][1]["takes"][0]["rating"] = "circled"
    slate.write(film)
    client.post("/api/slate/direct", json={"project": project, "retakes": 0, "cut": True})
    until(project)
    assert failed_take["mediaPath"] in [c_["path"] for c_ in made["saved"]["timeline"]]


class Gate(Queue):
    """A queue whose QA jobs answer as the QA gate does (app/qa.py check): the reason of a failed take is the note of
    the gate that failed it, under ``audio`` or ``identity``; every clip fails."""

    def qa(self, body):
        self.checked.append(body["path"])
        job_id = f"q{len(self.jobs) + 1}"
        take = {"audio": {"result": "fail", "heard": "who left", "note": "2 of the line's 4 words heard"},
                "identity": {"result": "pass", "note": "claude: the same woman in all three frames"},
                "sharpness": {}, "times": [0.5, 2.5, 4.5], "passed": False, "take": 1, "seed": 7, "sheet": "qa/x.jpg"}
        self.jobs[job_id] = {"id": job_id, "kind": "qa", "status": "done", "qa": {"passed": False, "takes": [take], "from": None}}
        return self.jobs[job_id]


def test_the_qa_gate_s_own_words_reach_the_outcome_the_log_and_the_take_and_a_run_with_nothing_to_cut_still_counts(studio):
    queue, brain = Gate(), Brain()
    studio(queue, brain)
    project = new_film()
    client.post("/api/slate/direct", json={"project": project, "scenes": 1, "shotsPerScene": 2, "seconds": 5, "retakes": 0, "cut": True})
    run = until(project)
    assert run["state"] == "failed", run
    wide, close = slate.get_project(project)["scenes"][0]["shots"]
    for shot in (wide, close):
        rec = run["shots"][shot["id"]]
        assert rec["outcome"] == "failed" and rec["why"] == "audio: 2 of the line's 4 words heard"
        assert any(line["text"] == f"{shot['name']}: failed the check (audio: 2 of the line's 4 words heard)" for line in run["log"])
        take = shot["takes"][0]  # the gate's report is kept on the take, as on a clip the queue checked itself
        assert take["qa"]["passed"] is False and take["qa"]["takes"][0]["audio"]["note"] == "2 of the line's 4 words heard"
        assert take["rating"] == "no-good"
    last = run["log"][-1]["text"]
    assert last.startswith("Failed: no shot has a take to cut: ")
    assert "0 of 2 shots passed the check" in last and f"2 failed the check (in the Library, out of the cut): {wide['name']}, {close['name']}" in last
    assert last.endswith("0 retakes") and run["counts"]["failedCheck"] == 2


def test_a_take_failed_for_its_identity_says_so_and_a_take_with_reasons_keeps_them():
    take = {"audio": {"result": "skipped", "note": "no speech-to-text tool"}, "identity": {"result": "fail", "note": "claude: the face changes"}}
    assert director.failed_because(take) == "identity: claude: the face changes"
    assert director.failed_because({**take, "audio": {"result": "fail", "note": "0 of 3 words heard"}}) == \
        "audio: 0 of 3 words heard; identity: claude: the face changes"
    assert director.failed_because({"reason": "the face changes", **take}) == "the face changes"
    assert director.failed_because({"why": "the shot check"}) == "the shot check" and director.failed_because({"passed": False}) == ""


def test_a_render_the_workload_limits_stopped_is_queued_once_more_and_is_not_a_retake(studio):
    queue, brain = Picky(stop="Ada walks", stops=1), Brain()
    studio(queue, brain)
    project = new_film()
    client.post("/api/slate/direct", json={"project": project, "scenes": 1, "shotsPerScene": 2, "seconds": 5, "retakes": 0, "cut": False})
    run = until(project)
    film = slate.get_project(project)
    wide = film["scenes"][0]["shots"][0]
    tries = [v for v in queue.videos() if v["params"]["slate"]["shot"] == wide["id"]]
    assert len(tries) == 2 and tries[0]["params"]["seed"] == tries[1]["params"]["seed"]  # the same take again
    assert run["shots"][wide["id"]]["outcome"] == "passed" and run["counts"]["retakes"] == 0
    # stopped twice: left out with the reason, no third try
    queue, brain = Picky(stop="Ada walks", stops=5), Brain()
    studio(queue, brain)
    again = new_film()
    client.post("/api/slate/direct", json={"project": again, "scenes": 1, "shotsPerScene": 2, "seconds": 5, "retakes": 2, "cut": False})
    run = until(again)
    wide = slate.get_project(again)["scenes"][0]["shots"][0]
    assert len([v for v in queue.videos() if v["params"]["slate"]["shot"] == wide["id"]]) == director.WORKLOAD_STOPS
    assert run["shots"][wide["id"]]["outcome"] == "stopped" and run["counts"]["stopped"] == 1 and "workload limits" in run["shots"][wide["id"]]["why"]


def test_a_heat_stop_in_the_governor_s_own_words_is_queued_once_more_at_its_seed_and_not_a_third_time(studio, tmp_path, monkeypatch):
    """The whole chain of a heat stop: the governor's telemetry finds the GPU too hot (spark/workload.py reasons), the
    managed server says so in its log as it stops (spark/managed_comfy.py), the render fails with spark_generate's
    reading of that line, and the Director queues the same take once more at its seed; stopped again, it is left out."""

    sys.path.insert(0, str(ROOT / "spark"))
    import spark_generate as sg
    import workload

    hot = {"memory": {"available_bytes": 100 * 2 ** 30}, "gpus": [{"temperature_c": 95.0, "thermal_flags": {}}],
           "psi": {"some_avg10": 0.0, "full_avg10": 0.0}}
    why = workload.reasons(hot, [{"zone": "z0", "temp_c": 41.0, "ceiling_c": 95.0}], 1, 0, 2 ** 30)
    assert why == ["GPU temperature ceiling or thermal slowdown"]
    assert 'print("[studio] stopping the managed server: " + why, flush=True)' in (ROOT / "spark/managed_comfy.py").read_text()
    log = tmp_path / "comfy.log"
    log.write_text("[INFO] got prompt\n[studio] stopping the managed server: " + "; ".join(why) + "\n")
    monkeypatch.setattr(sg, "side", lambda ext: log)
    said = sg.stopped()
    assert director.WORKLOAD_STOP in said and "GPU temperature ceiling" in said
    # met as the server started: it ended before any render (managed_comfy.py's own words), the same stop
    log.write_text("[studio] the managed server stopped: GPU temperature ceiling or thermal slowdown\n")
    assert director.WORKLOAD_STOP in sg.stopped() and "GPU temperature ceiling" in sg.stopped()
    assert '"[studio] the managed server stopped: {error}"' in (ROOT / "spark/managed_comfy.py").read_text()
    log.write_text("[studio] the managed server stopped: the workload governor did not admit the renderer: {}\n")
    assert sg.stopped() == ""  # not a limit: no stop is claimed

    class Hot(Picky):
        def submit(self, body):
            job = super().submit(body)
            if job["status"] == "failed":
                job["error"] = said  # as server.run reports a script that exited with words of its own
            return job

    for stops, tries, outcome in ((1, 2, "passed"), (2, director.WORKLOAD_STOPS, "stopped")):
        queue, brain = Hot(stop="Ada walks", stops=stops), Brain()
        studio(queue, brain)
        project = new_film()
        client.post("/api/slate/direct", json={"project": project, "scenes": 1, "shotsPerScene": 2, "seconds": 5, "retakes": 1, "cut": False})
        run = until(project)
        wide = slate.get_project(project)["scenes"][0]["shots"][0]
        made = [v["params"]["seed"] for v in queue.videos() if v["params"]["slate"]["shot"] == wide["id"]]
        assert len(made) == tries and len(set(made)) == 1, made  # the same take at its seed, never a retake's next seed
        assert run["shots"][wide["id"]]["outcome"] == outcome and run["counts"]["retakes"] == 0
        assert sum(1 for line in run["log"] if f"queued once more at seed {made[0]}" in line["text"]) == 1


def test_a_stopped_run_takes_its_waiting_stills_out_of_the_queue(studio):
    queue, brain = Queue(hold=True), Brain()
    studio(queue, brain)
    project = new_film()
    client.post("/api/slate/direct", json={"project": project, "scenes": 1, "shotsPerScene": 2, "seconds": 5, "cut": False})
    end = time.time() + 10
    while time.time() < end and len([b for b in queue.bodies if b["kind"] == "scout"]) < 2:  # Ada and the harbour
        time.sleep(0.02)
    stills = [j for j in queue.made if j["kind"] == "scout"]
    assert len(stills) == 2 and all(j["status"] == "queued" for j in stills)
    assert client.post(f"/api/slate/direct/{project}/stop", json={}).json()["stopped"] is True
    until(project)
    assert all(j["status"] == "cancelled" for j in stills)


def test_one_render_is_queued_for_each_machine(monkeypatch):
    monkeypatch.setattr(director, "machines", lambda: 1)
    assert director.at_once() == 1
    monkeypatch.setattr(director, "machines", lambda: 2)
    assert director.at_once() == 2
    monkeypatch.setattr(director, "machines", lambda: 9)
    assert director.at_once() == director.AT_ONCE
    monkeypatch.setattr(director, "machines", lambda: "?")
    assert director.at_once() == 1


def test_the_shot_check_wants_the_cast_and_the_lines_in_their_fields():
    film = {"characters": [{"name": "Ada"}, {"name": "Bo"}], "locations": [{"name": "The Harbour"}]}
    check = director.check_shots(film, 5)
    good = {"prompt": "# Subject\nAda walks the wall.", "cast": ["Ada"], "lines": []}
    assert check({"shots": [good]}) == ""
    # the names only in the prompt: the pictures would never be attached
    said = check({"shots": [{**good, "cast": []}]})
    assert "names ada" in said.lower() and "\"cast\"" in said
    # speech written into the prompt with no lines
    said = check({"shots": [{**good, "prompt": "# Subject\nAda turns and says, \"Who left this here?\""}]})
    assert "speech inside its prompt" in said
    # a sign's words are not speech, and a shot of the empty place needs no cast
    assert check({"shots": [{**good, "prompt": "# Subject\nAda passes a sign that reads \"NO FISHING HERE TODAY\"."}]}) == ""
    assert check({"shots": [{"prompt": "# Subject\nWaves break on the harbour wall.", "cast": [], "lines": []}]}) == ""
    assert isinstance(said, director.Soft)  # asked to fix once; a second answer with only this is taken
    # not the cast speaking: a sign that says, a diner called, a radio announcer
    for words in ("A neon sign says \"OPEN ALL NIGHT\" above Ada.", "Ada sits in a diner called \"The Last Drop\".",
                  "Ada listens. The radio announcer says \"storm warning tonight\"."):
        assert check({"shots": [{**good, "prompt": "# Subject\n" + words}]}) == "", words
    # a name as a common word, or inside a place's name, is not that character
    roses = director.check_shots({"characters": [{"name": "Rose"}, {"name": "Will"}], "locations": [{"name": "Rose House"}]}, 5)
    assert roses({"shots": [{"prompt": "# Subject\nPetals of a rose drift past Rose House. The rain will not stop.", "cast": [], "lines": []}]}) == ""
    # a possessive is about someone, not of them, and a name inside a longer word is not that character
    assert check({"shots": [{"prompt": "# Subject\nAda's boat rocks at the quay.", "cast": [], "lines": []}]}) == ""
    assert check({"shots": [{"prompt": "# Subject\nA boat bobs at the quay.", "cast": [], "lines": []}]}) == ""


def test_a_new_angle_opens_on_the_cast_and_a_carried_on_beat_on_the_shot_before(monkeypatch):
    wide = {"size": "WS", "angle": "eye level", "cast": ["Ada"]}
    on = lambda shot, before: director.opening(shot, before)
    monkeypatch.setattr(director, "ref2va", lambda: True)
    assert on(wide, None) == "cast"
    assert on({"size": "CU", "angle": "eye level", "cast": ["Ada"], "continues": True}, wide) == "cast"  # a new size is a cut
    assert on({"size": "CU", "angle": "eye level", "cast": ["Ada"]}, wide) == "cast"
    assert on({"size": "WS", "angle": "eye level", "cast": ["Ada"], "continues": True}, wide) == "previous"
    assert on({"size": "WS", "angle": "eye level", "cast": [], "continues": False}, wide) == "previous"  # no one to pin
    monkeypatch.setattr(director, "ref2va", lambda: False)
    assert on({"size": "CU", "cast": ["Ada"]}, wide) == "previous" and on(wide, None) == "text"


# a sheet as a brain fills the fields it has nothing for
BLANK_SHEET = {"name": "Bo", "age": "late fifties", "gender": "male", "ethnicity": "N/A", "faceFeatures": "unknown", "hair": "None",
               "clothing": "none.", "expression": "", "keyLightSide": None}


def test_a_brain_s_none_never_reaches_a_prompt_and_a_written_age_is_not_years_old():
    said = director.sheet_picture(BLANK_SHEET)
    assert said.startswith("Bo, late fifties, male. Character reference portrait: centered head-and-shoulders")
    assert not any(word in said for word in ("None", "N/A", "unknown", "years old", "Hair:", "Wearing", ". .", "..")), said
    assert director.sheet_picture({"name": "Ada", "age": "34", "hair": "short dark hair"}).startswith("Ada, 34 years old. Hair: short dark hair.")
    assert director.sheet_picture({"name": "Ada", "age": 34}).startswith("Ada, 34 years old.")
    assert slate.person(BLANK_SHEET) == "late fifties, male" and slate.person({"age": "34", "hair": "N/A", "clothing": "a coat"}) == "34 years old, wearing a coat"
    assert slate.voice_of({"ageGender": "None", "timbre": "warm", "pitch": "n/a"}) == "a voice that is warm"
    assert director.plate_picture({"name": "The Pier", "description": "Old planks", "weather": "None", "timeOfDay": "N/A"}).startswith(
        "The Pier: Old planks. Location plate:")


def test_stills_are_made_with_a_picture_model_this_machine_has(monkeypatch, tmp_path):
    import settings

    monkeypatch.setattr(settings, "HOME", tmp_path)
    monkeypatch.setattr(director, "pictures", lambda: ["flux3k-fast", "moss-image"])  # a Spark with FLUX3-K and no Qwen
    assert director.picture_model() == "flux3k-fast"
    settings.save({"presets": {"image_model": "moss-image"}})
    assert director.picture_model() == "moss-image"  # Settings' default where it is installed
    settings.save({"presets": {"image_model": "qwen"}})
    assert director.picture_model() == "flux3k-fast"
    monkeypatch.setattr(director, "pictures", lambda: ["qwen", "flux3k-fast"])
    assert director.picture_model() == "qwen"
    monkeypatch.setattr(director, "pictures", lambda: [])
    assert director.picture_model() is None  # the queue then says what to install


def test_only_the_cast_the_shots_show_get_portraits_and_stills_name_their_picture_model(studio, monkeypatch):
    class Extra(Brain):  # the bible has a third character no shot casts
        def __call__(self, backend, system, prompt, tier, endpoint="", model=""):
            said = super().__call__(backend, system, prompt, tier, endpoint, model)
            if "Write the characters" in prompt or "Write the locations" in prompt:
                bible = json.loads(said.split("```json\n")[1].split("\n```")[0])
                bible["characters"].append({"name": "Cy", "age": "None", "gender": "male", "hair": "N/A", "clothing": "a hat and a coat"})
                return "```json\n" + json.dumps(bible) + "\n```"
            return said

    monkeypatch.setattr(director, "pictures", lambda: ["flux3k-fast"])
    queue, brain = Queue(), Extra()
    studio(queue, brain)
    project = new_film()
    client.post("/api/slate/direct", json={"project": project, "scenes": 2, "shotsPerScene": 2, "seconds": 5, "retakes": 0, "cut": False})
    run = until(project)
    assert run["state"] == "done", run
    stills = [b for b in queue.bodies if b["kind"] == "scout"]
    assert sorted(b["title"] for b in stills) == ["Plate: The Harbour", "Reference: Ada", "Reference: Bo"]  # no portrait of Cy
    assert all(b["params"]["image_model"] == "flux3k-fast" for b in stills)
    assert not slate.get_project(project)["characters"][2].get("images")


def test_a_costume_portrait_shows_the_costume():
    period = director.sheet_picture({"name": "Wren", "age": "40", "clothing": "a long wool overcoat and a felt hat"})
    assert "knees up" in period and "hat and coat included" in period and "no props" not in period
    plain = director.sheet_picture({"name": "Ada", "clothing": "a grey sweater"})
    assert "head-and-shoulders" in plain and "no props" in plain


def test_a_run_written_before_outcomes_were_kept_still_counts_true():
    run = {"order": ["a", "b", "c", "d"], "shots": {
        "a": {"settled": True, "best": "x.mp4", "attempts": [{"status": "done", "passed": True}]},
        "b": {"settled": True, "best": None, "fallback": "y.mp4", "attempts": [{"status": "done", "passed": False}]},
        "c": {"settled": True, "best": None, "attempts": [{"job": None, "status": "failed", "passed": False}]},
        "d": {"settled": True, "best": "z.mp4", "kept": True, "attempts": []}}}
    c = director.counts(run)
    assert (c["passed"], c["kept"], c["failedCheck"], c["refused"], c["leftOut"]) == (1, 1, 1, 1, 2)


def test_a_second_answer_with_only_a_soft_problem_is_taken(studio):
    """Asked once to cast the people its prompt names, a brain that answers the same again is taken at its word:
    two refusals would stop the whole run."""

    class Stubborn(Brain):
        def __call__(self, backend, system, prompt, tier, endpoint="", model=""):
            if "Write exactly" in prompt:
                self.asked.append(prompt)
                return json.dumps({"shots": [{"name": "Wide", "prompt": "# Subject\nAda walks the harbour wall.", "cast": [], "location": "The Harbour", "lines": []}]})
            return super().__call__(backend, system, prompt, tier, endpoint, model)

    queue, brain = Queue(), Stubborn()
    studio(queue, brain)
    project = new_film()
    client.post("/api/slate/direct", json={"project": project, "scenes": 1, "shotsPerScene": 1, "seconds": 5, "cut": False})
    run = until(project)
    assert run["state"] == "done", run
    assert len([p for p in brain.asked if "Write exactly" in p]) == 2 and "empty \"cast\"" in brain.asked[-1]


def test_a_heat_stop_queued_again_survives_a_restart_and_a_kept_take_that_failed_its_check_is_not_kept(studio):
    queue, brain = Picky(stop="Ada walks", stops=1, hold=True), Brain()
    studio(queue, brain)
    project = new_film()
    client.post("/api/slate/direct", json={"project": project, "scenes": 1, "shotsPerScene": 1, "seconds": 5, "retakes": 0, "cut": False})
    end = time.time() + 10
    while time.time() < end and len(queue.videos()) < 2:  # the pictures are made; the clip is stopped and queued again
        for job in queue.jobs.values():
            if job["kind"] != "video":
                job["status"] = "done"
        time.sleep(0.02)
    assert len(queue.videos()) == 2
    assert client.post(f"/api/slate/direct/{project}/stop", json={}).json()["stopped"] is True
    until(project)
    # a restart: the queue knows none of it; the take queued again is lost, and is queued once more, not left out
    director.runs.clear()
    (HOME / queue.videos()[1]["params"]["output"]).unlink()  # the clip queued again was never made
    fresh = Queue()
    studio(fresh, brain)
    saved = json.loads(director.status_file(project).read_text())
    saved["state"] = "rendering"
    director.status_file(project).write_text(json.dumps(saved))
    director.resume_all()
    run = until(project)
    wide = slate.get_project(project)["scenes"][0]["shots"][0]
    mine = [v for v in fresh.videos() if v["params"]["slate"]["project"] == project]  # a restart picks up every film left at work
    assert run["shots"][wide["id"]]["outcome"] == "passed" and len(mine) == 1, run["shots"][wide["id"]]
    assert mine[0]["params"]["seed"] == queue.videos()[0]["params"]["seed"]

    # a take Slate rated good when it queued it, which its own QA gate then failed, is not kept as the shot's clip
    (HOME / "slate-old.mp4").write_bytes(b"x")
    shot = {"takes": [{"mediaPath": "slate-old.mp4", "rating": "good", "qa": {"passed": False}}]}
    assert director.kept_take(shot) is None
    shot["takes"][0]["rating"] = "circled"
    assert director.kept_take(shot) == "slate-old.mp4"
