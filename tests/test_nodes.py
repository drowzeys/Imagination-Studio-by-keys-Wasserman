"""Nodes (app/nodes.py): python3 -m pytest tests/test_nodes.py. No GPU: the queue is stood in for by a list the test
finishes by hand, and the app's routes by small stand-ins."""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
sys.path.insert(0, str(ROOT / "app"))
import nodes  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def node(nid, card, x=0, y=0, **params):
    return {"id": nid, "type": card, "x": x, "y": y, "params": params}


def edge(a, out, b, port):
    return {"from": a, "out": out, "to": b, "in": port}


class Studio:
    """A studio folder, a queue and an app with the nodes router, as server.py wires it."""

    def __init__(self, routes=True):
        self.home = Path(tempfile.mkdtemp())
        self.jobs, self.lock, self.queued, self.cancelled = {}, threading.Lock(), [], []
        self.app = FastAPI()

        def inside(path):
            resolved = (self.home / path).resolve()
            if self.home.resolve() not in (resolved, *resolved.parents):
                raise HTTPException(400, "path is outside the studio folder")
            return resolved

        relative = lambda path: str(Path(path).resolve().relative_to(self.home.resolve()))
        self.app.include_router(nodes.attach(self.home, self.enqueue, self.jobs, self.lock, inside, relative))
        self.room = {"fits": True}
        if routes:
            @self.app.post("/api/jobs")
            def submit(body: dict) -> dict:
                if body["kind"] == "video" and body["params"]["seconds"] == 13:
                    raise HTTPException(400, "MiniMax H3 Turbo is not installed here")
                return self.enqueue(body["kind"], body["title"], body["params"], None, via="route")

            @self.app.post("/api/voice/takes")
            def voice(body: dict) -> dict:
                return self.enqueue("voice", body["title"], {"text": body["text"], "seed": body["seed"]}, None, via="route")

            @self.app.post("/api/music/takes")
            def music(body: dict) -> dict:
                return self.enqueue("music", body["title"], {k: body[k] for k in ("caption", "lyrics", "seconds", "seed")}, None, via="route")

            @self.app.post("/api/jobs/{job_id}/cancel")
            def cancel(job_id: str) -> dict:
                self.cancelled.append(job_id)
                self.jobs[job_id]["status"] = "cancelled"
                return {"id": job_id}

            @self.app.post("/api/estimate")
            def estimate(body: dict) -> dict:
                return {"seconds": 60, "memory": self.room}
        self.client = TestClient(self.app)

    def enqueue(self, kind, title, params, estimate, via="enqueue"):
        job_id = f"j{len(self.queued) + 1}"
        with self.lock:
            self.jobs[job_id] = {"id": job_id, "kind": kind, "title": title, "params": params, "status": "queued", "outputs": [], "error": None}
        self.queued.append((kind, params, via))
        return {"id": job_id}

    def finish(self, outputs=None, error=None, timeout=10):
        """The next queued job ends: done with these files (written under the studio folder) or failed."""

        until = time.time() + timeout
        while time.time() < until:
            with self.lock:
                waiting = [j for j in self.jobs.values() if j["status"] == "queued"]
            if waiting:
                job = waiting[0]
                for path in outputs or []:
                    (self.home / path).parent.mkdir(parents=True, exist_ok=True)
                    (self.home / path).write_bytes(b"x")
                with self.lock:
                    job.update(status="failed" if error else "done", outputs=list(outputs or []), error=error,
                               **({"music": {"id": "song1"}} if job["kind"] == "music" else {}))
                return job
            time.sleep(0.02)
        raise AssertionError("no job was queued")

    def run(self, graph, until=("done", "failed", "cancelled"), timeout=10):
        run = self.client.post("/api/nodes/run", json={"graph": graph}).json()
        return run

    def settle(self, run_id, timeout=10):
        until = time.time() + timeout
        while time.time() < until:
            run = self.client.get(f"/api/nodes/runs/{run_id}").json()
            if run["status"] not in ("queued", "running"):
                return run
            time.sleep(0.02)
        raise AssertionError("the run did not finish")


nodes.POLL_S = 0.02


def still_clip():
    return {"name": "Still to clip", "nodes": [node("t1", "text", 0, 0, text="a lighthouse at dusk"), node("p1", "picture", 300, 0),
                                               node("t2", "text", 0, 300, text="the beam sweeps the sea"), node("c1", "clip", 600, 0, seconds=8)],
            "edges": [edge("t1", "text", "p1", "prompt"), edge("p1", "image", "c1", "first_frame"), edge("t2", "text", "c1", "prompt")]}


# ---------- checking a graph ----------

def messages(graph):
    return [p["message"] for p in nodes.problems(nodes.tidy(graph))]


def test_a_loop_is_refused_by_name():
    graph = {"nodes": [node("a", "edit"), node("b", "edit"), node("t", "text", text="x")],
             "edges": [edge("a", "image", "b", "image"), edge("b", "image", "a", "image"), edge("t", "text", "a", "prompt"), edge("t", "text", "b", "prompt")]}
    said = messages(graph)
    assert any("loops" in m and "Edit picture → Edit picture → Edit picture" in m for m in said), said
    assert nodes.cycle(nodes.tidy(graph))


def test_a_port_of_the_wrong_type_is_refused_plainly():
    graph = {"nodes": [node("t", "text", text="hello"), node("v", "voice"), node("c", "clip")],
             "edges": [edge("t", "text", "v", "text"), edge("v", "audio", "c", "prompt")]}
    said = messages(graph)
    assert "Voice gives audio, and the prompt of Clip takes text" in said


def test_missing_inputs_empty_text_and_unknown_ports():
    graph = {"nodes": [node("t", "text", text=" "), node("c", "clip"), node("x", "export")],
             "edges": [edge("t", "text", "c", "nonsense")]}
    said = messages(graph)
    assert "Clip needs its prompt: join a card that gives text" in said
    assert "Export needs its source: join a card that gives timeline or video" in said
    assert "Text is empty: write the words it passes on" in said
    assert "Clip has no input called nonsense" in said


def test_one_link_into_a_single_input_and_many_into_refs():
    graph = {"nodes": [node("t", "text", text="a"), node("u", "text", text="b"), node("c", "clip"),
                       node("r1", "reference", path="uploads/a.png"), node("r2", "reference", path="uploads/b.png")],
             "edges": [edge("t", "text", "c", "prompt"), edge("u", "text", "c", "prompt"), edge("r1", "file", "c", "refs"), edge("r2", "file", "c", "refs")]}
    said = messages(graph)
    assert said == ["the prompt of Clip takes one link, and it has 2"]


def test_references_never_go_with_a_first_frame_and_sound_needs_a_picture():
    graph = {"nodes": [node("t", "text", text="a"), node("c", "clip"), node("p", "reference", path="uploads/a.png"),
                       node("s", "reference", path="uploads/a.wav", kind="audio")],
             "edges": [edge("t", "text", "c", "prompt"), edge("p", "file", "c", "first_frame"), edge("s", "file", "c", "refs")]}
    said = messages(graph)
    assert any("references do not combine with a first frame" in m for m in said)
    assert any("reference sound needs a reference picture or video" in m for m in said)


def test_a_reference_is_the_kind_of_file_it_holds():
    graph = {"nodes": [node("r", "reference", path="renders/a.mp4", kind="image")], "edges": []}
    assert messages(graph) == ["Reference is set to a picture, and a.mp4 is not one"]
    graph = {"nodes": [node("r", "reference", path="renders/a.mp4", kind="video"), node("c", "clip"), node("t", "text", text="x")],
             "edges": [edge("r", "file", "c", "first_frame"), edge("t", "text", "c", "prompt")]}
    assert messages(graph) == ["Reference gives video, and the first frame of Clip takes image"]


def test_a_timeline_song_comes_from_a_song_card():
    graph = {"nodes": [node("t", "text", text="a"), node("v", "voice"), node("c", "clip"), node("l", "timeline")],
             "edges": [edge("t", "text", "v", "text"), edge("t", "text", "c", "prompt"), edge("c", "video", "l", "clips"), edge("v", "audio", "l", "song")]}
    assert messages(graph) == ["the song under Timeline comes from a Song card"]


def test_tidy_refuses_unknown_cards_and_keeps_settings_typed():
    for bad in ({"nodes": [{"id": "a", "type": "gpu"}]}, {"nodes": [node("a", "text"), node("a", "text")]}, {"nodes": "x"},
                {"nodes": [node("a", "picture", width="wide")]}, {"nodes": [], "edges": [{"from": "a"}]}):
        try:
            nodes.tidy(bad)
        except HTTPException as error:
            assert error.status_code == 400 and error.detail
        else:
            raise AssertionError(bad)
    tidy = nodes.tidy({"nodes": [node("p", "picture", width="1024", seed=3.0, extra="dropped")]})
    assert tidy["nodes"][0]["params"] == {"image_model": "qwen", "width": 1024, "height": 768, "seed": 3, "count": 1}


def test_order_runs_each_card_after_what_it_takes_from():
    graph = nodes.tidy(still_clip())
    assert nodes.order(graph) == ["t1", "t2", "p1", "c1"]
    graph = nodes.tidy({"nodes": [node("c", "clip", 0, 0), node("t", "text", 500, 0, text="x"), node("n", "note")], "edges": [edge("t", "text", "c", "prompt")]})
    assert nodes.order(graph) == ["t", "c"]  # a note never runs


def test_the_templates_are_valid_graphs():
    for template in nodes.TEMPLATES:
        graph = nodes.tidy(template)
        said = [p["message"] for p in nodes.problems(graph)]
        if template["id"] == "line-voice-clip":
            assert said == ["The person has no file: pick one from the Library"]  # the face is the user's to pick
        else:
            assert said == [], (template["id"], said)
        nodes.order(graph)
    assert len(nodes.TEMPLATES) == 3


# ---------- graphs on disk ----------

# a card's height on the canvas once its run has drawn what it made (measured in the page at 1440 px, canvas units)
DRAWN = {"text": 150, "note": 150, "voice": 190, "song": 190, "pick": 285, "reference": 350, "picture": 445, "edit": 445, "clip": 470,
         "timeline": 200, "export": 300}


def test_a_templates_cards_do_not_cover_each_other_once_they_have_run():
    for template in nodes.TEMPLATES:
        cards = template["nodes"]
        for a in cards:
            for b in cards:
                if a is b or abs(a["x"] - b["x"]) >= 248 or a["y"] > b["y"]:
                    continue
                assert b["y"] - a["y"] >= DRAWN[a["type"]], (template["id"], a["id"], b["id"])


def test_graphs_are_saved_listed_and_moved_to_the_trash():
    studio = Studio()
    saved = studio.client.post("/api/nodes/graphs", json={**still_clip(), "id": "mine"}).json()
    assert saved["id"] == "mine" and len(saved["nodes"]) == 4
    listed = studio.client.get("/api/nodes/graphs").json()
    assert [g["id"] for g in listed["graphs"]] == ["mine"] and len(listed["templates"]) == 3 and "clip" in listed["types"]
    assert studio.client.get("/api/nodes/graphs/mine").json()["name"] == "Still to clip"
    assert studio.client.get("/api/nodes/graphs/..%2Fx").status_code in (400, 404)
    assert studio.client.post("/api/nodes/graphs", json={"id": "../up", "nodes": []}).status_code == 400
    assert studio.client.delete("/api/nodes/graphs/mine").json() == {"deleted": "mine"}
    assert studio.client.get("/api/nodes/graphs").json()["graphs"] == []
    assert list((studio.home / "nodes" / ".trash").glob("mine-*.json"))


def test_check_answers_with_problems_by_card():
    studio = Studio()
    said = studio.client.post("/api/nodes/check", json={"graph": {"nodes": [node("c", "clip")], "edges": []}}).json()
    assert said["ok"] is False and said["problems"][0]["node"] == "c"
    said = studio.client.post("/api/nodes/check", json={"graph": still_clip()}).json()
    assert said == {"ok": True, "problems": [], "order": ["t1", "t2", "p1", "c1"]}


# ---------- runs ----------

def test_a_run_queues_each_job_in_turn_and_passes_files_forward():
    studio = Studio()
    run = studio.run(still_clip())
    assert run["status"] in ("queued", "running") and run["order"] == ["t1", "t2", "p1", "c1"]
    studio.finish(["scouts/j1/scout_s1.png", "scouts/j1/sheet.jpg"])
    studio.finish(["renders/j2.mp4"])
    run = studio.settle(run["id"])
    assert run["status"] == "done", run
    (kind1, scout, via1), (kind2, clip, via2) = studio.queued
    assert (kind1, via1, kind2, via2) == ("scout", "route", "video", "route")
    assert scout == {"prompt": "a lighthouse at dusk", "count": 1, "seed": 1, "width": 1344, "height": 768, "image_model": "qwen"}
    assert clip["image"] == "scouts/j1/scout_s1.png" and clip["prompt"] == "the beam sweeps the sea" and clip["seconds"] == 8
    assert run["nodes"]["p1"]["outputs"]["images"] == ["scouts/j1/scout_s1.png"]
    assert run["nodes"]["c1"]["thumb"] == "renders/j2.mp4" and run["nodes"]["c1"]["job"] == "j2"
    assert run["nodes"]["t1"]["status"] == "done" and run["nodes"]["t1"]["outputs"] == {"text": "a lighthouse at dusk"}


def test_without_the_apps_route_a_card_fails_and_nothing_is_queued_unchecked():
    """A job only goes in through the app's own route, which checks it; there is no unchecked way round it."""
    studio = Studio(routes=False)
    run = studio.run({"nodes": [node("t", "text", text="We leave at first light."), node("v", "voice", seed=4)], "edges": [edge("t", "text", "v", "text")]})
    run = studio.settle(run["id"])
    assert run["status"] == "failed" and studio.queued == []
    assert "/api/voice/takes" in run["nodes"]["v"]["error"] and "not queued" in run["nodes"]["v"]["error"]


def test_a_clip_is_not_run_without_the_memory_guards_answer():
    studio = Studio(routes=False)
    answer = studio.client.post("/api/nodes/run", json={"graph": still_clip()})
    assert answer.status_code == 503 and "/api/estimate" in answer.json()["detail"]
    assert studio.queued == []


def test_two_runs_at_once_start_one_and_the_other_is_a_409():
    """A double click on Run: the check and the start are one step, so the second never passes the check."""
    studio = Studio()
    entered, release = threading.Event(), threading.Event()

    slow = next(r for r in studio.app.routes if getattr(r, "path", None) == "/api/estimate")
    quick = slow.endpoint

    def held(body: dict) -> dict:
        entered.set()
        release.wait(5)
        return quick(body)

    slow.endpoint = held
    answers = []
    first = threading.Thread(target=lambda: answers.append(studio.client.post("/api/nodes/run", json={"graph": still_clip()})))
    first.start()
    assert entered.wait(5)  # the first run is in its memory checks
    second = studio.client.post("/api/nodes/run", json={"graph": still_clip()})
    release.set()
    first.join(5)
    assert second.status_code == 409 and "starting" in second.json()["detail"]
    assert answers and answers[0].status_code == 200
    third = studio.client.post("/api/nodes/run", json={"graph": still_clip()})  # the first is still running
    assert third.status_code == 409 and "running" in third.json()["detail"]
    studio.client.post(f"/api/nodes/runs/{answers[0].json()['id']}/cancel")
    studio.settle(answers[0].json()["id"])
    assert len([r for r in studio.client.get("/api/nodes/runs").json()]) == 1


def test_numbers_that_are_not_finite_are_refused():
    for bad in ({"nodes": [{**node("t", "text", text="x"), "x": float("nan")}], "edges": []},
                {"nodes": [node("c", "clip", seconds=float("inf"))], "edges": []}):
        try:
            nodes.tidy(bad)
        except HTTPException as error:
            assert error.status_code == 400
        else:
            raise AssertionError(bad)
    assert nodes.tidy({"nodes": [], "edges": [], "view": {"x": float("nan"), "y": 0, "k": 1}})["view"] == {"x": 0.0, "y": 0.0, "k": 1.0}


def test_each_save_of_a_graph_writes_its_own_temp_file():
    studio = Studio()
    names = []
    real = Path.write_text

    def spy(self, *args, **kwargs):
        names.append(self.name)
        return real(self, *args, **kwargs)

    Path.write_text = spy
    try:
        for _ in range(2):
            assert studio.client.post("/api/nodes/graphs", json={"id": "g1", "name": "one", "nodes": [], "edges": []}).status_code == 200
    finally:
        Path.write_text = real
    temps = [n for n in names if n.startswith("g1.json.")]
    assert len(temps) == 2 and temps[0] != temps[1] and all(n.endswith(".new") for n in temps)
    assert not list((studio.home / "nodes").glob("*.new"))


def test_a_failed_card_skips_what_waits_on_it_and_says_why():
    studio = Studio()
    run = studio.run(still_clip())
    studio.finish(error="Qwen-Image-2.1-Turbo is not installed here")
    run = studio.settle(run["id"])
    assert run["status"] == "failed"
    assert run["nodes"]["p1"] == {**run["nodes"]["p1"], "status": "failed", "error": "Qwen-Image-2.1-Turbo is not installed here"}
    assert run["nodes"]["c1"]["status"] == "skipped" and "Picture" in run["nodes"]["c1"]["error"]
    assert run["nodes"]["t2"]["status"] == "done"
    assert len(studio.queued) == 1


def test_a_refused_job_fails_its_card_with_the_apps_reason():
    studio = Studio()
    graph = {"nodes": [node("t", "text", text="   x   "), node("c", "clip", seconds=13)], "edges": [edge("t", "text", "c", "prompt")]}
    run = studio.settle(studio.run(graph)["id"])
    assert run["status"] == "failed" and run["nodes"]["c"]["error"] == "MiniMax H3 Turbo is not installed here"
    assert studio.queued == []


def test_cancel_stops_the_job_that_renders_and_the_rest():
    studio = Studio()
    run = studio.run(still_clip())
    until = time.time() + 5
    while not studio.queued and time.time() < until:
        time.sleep(0.02)
    assert studio.client.post("/api/nodes/run", json={"graph": still_clip()}).status_code == 409  # one graph at a time
    studio.client.post(f"/api/nodes/runs/{run['id']}/cancel")
    run = studio.settle(run["id"])
    assert run["status"] == "cancelled" and studio.cancelled == ["j1"]
    assert run["nodes"]["p1"]["status"] == "cancelled" and run["nodes"]["c1"]["status"] == "cancelled"
    assert len(studio.queued) == 1


def test_the_memory_guard_refuses_a_clip_before_anything_is_queued():
    studio = Studio()
    studio.room = {"fits": False, "reason": "this clip needs 96 GB and 70 are free"}
    answer = studio.client.post("/api/nodes/run", json={"graph": still_clip()})
    assert answer.status_code == 400 and "this clip needs 96 GB" in answer.json()["detail"]
    assert studio.queued == []


def test_an_invalid_graph_is_a_400_with_a_plain_message():
    studio = Studio()
    answer = studio.client.post("/api/nodes/run", json={"graph": {"nodes": [node("c", "clip")], "edges": []}})
    assert answer.status_code == 400 and answer.json()["detail"] == "Clip needs its prompt: join a card that gives text"
    assert studio.client.get("/api/nodes/runs/nope").status_code == 404


def test_scout_pick_song_timeline_export():
    studio = Studio()
    (studio.home / "projects/p1").mkdir(parents=True)
    (studio.home / "projects/p1/project.json").write_text('{"id": "p1", "name": "Film", "timeline": []}')
    graph = {"nodes": [node("t", "text", text="a market"), node("p", "picture", count=3), node("k", "pick", index=2),
                       node("a", "text", 0, 300, text="the cook tosses noodles"), node("c", "clip", seconds=10),
                       node("s", "text", 0, 600, text="warm lo-fi"), node("m", "song", seconds=20),
                       node("l", "timeline", project="p1"), node("x", "export", music_db=-12)],
             "edges": [edge("t", "text", "p", "prompt"), edge("p", "images", "k", "images"), edge("k", "image", "c", "first_frame"),
                       edge("a", "text", "c", "prompt"), edge("s", "text", "m", "style"), edge("c", "video", "l", "clips"),
                       edge("m", "audio", "l", "song"), edge("l", "timeline", "x", "source")]}
    run = studio.run(graph)
    studio.finish([f"scouts/j1/scout_s{i}.png" for i in (1, 2, 3)] + ["scouts/j1/sheet.jpg"])
    studio.finish(["renders/j2.mp4"])
    studio.finish(["music/song1.wav"])
    studio.finish(["exports/j4.mp4"])
    run = studio.settle(run["id"])
    assert run["status"] == "done", run
    kinds = [q[0] for q in studio.queued]
    assert kinds == ["scout", "video", "music", "export"]
    assert studio.queued[1][1]["image"] == "scouts/j1/scout_s2.png"
    assert studio.queued[2][1] == {"caption": "warm lo-fi", "lyrics": "", "seconds": 20, "seed": 1}
    export = studio.queued[3][1]
    assert export["clips"] == [{"path": "renders/j2.mp4", "in": 0, "out": 0, "duration": 10.125}]
    assert export["soundtrack"] == {"take": "song1", "music_db": -12} and (export["width"], export["height"]) == (1920, 1080)
    assert '"renders/j2.mp4"' in (studio.home / "projects/p1/project.json").read_text()
    assert run["nodes"]["x"]["outputs"] == {"file": "exports/j4.mp4"}


def test_the_server_has_the_routes_a_run_goes_through():
    """The paths nodes.py looks up on the app are the ones server.py, voice.py and music.py serve."""

    source = (ROOT / "app/server.py").read_text() + (ROOT / "app/voice.py").read_text() + (ROOT / "app/music.py").read_text()
    for path in ('@app.post("/api/jobs")', '@app.post("/api/jobs/{job_id}/cancel")', '@app.post("/api/estimate")',
                 '@router.post("/api/voice/takes")', '@router.post("/api/music/takes")'):
        assert path in source, path
