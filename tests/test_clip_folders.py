"""Where a new clip goes (issue #257): python3 -m pytest tests/test_clip_folders.py. No GPU, no network."""
import os
import sys
import tempfile
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
import sparks  # noqa: E402
import take_names  # noqa: E402

PRESET = "small" if server.SPARK else "draft"
PLAIN = {"prompt": "A train crosses a snowy bridge at dusk, steam rising.", "preset": PRESET, "quality": "fh8", "frames": 124}


@pytest.mark.usefixtures("spark_memory")
def test_a_clip_gets_its_own_readable_folder_and_a_named_place_is_kept(monkeypatch):
    job = "1109-194432-adc366"
    monkeypatch.setitem(server.jobs, job, {"id": job, "title": "Train through snow", "params": PLAIN})
    _, _, outputs = server.build("video", PLAIN, job)
    made = Path(outputs[0]).relative_to(server.HOME)
    assert made.parts[0] == "renders" and len(made.parts) == 4 and made.parts[1].endswith("-11")
    assert made.parts[2].endswith("-11-09_194432_train-through-snow_adc366") and made.name == made.parts[2] + ".mp4"
    assert Path(outputs[0]).parent.is_dir()  # made when the clip is, ready for the render
    # the same place every time it is built, and what the queue says another clip may wait for
    assert server.build("video", PLAIN, job)[2] == outputs and server.clip_target(job, PLAIN) == Path(outputs[0])
    # no usable title: the first words of the prompt
    monkeypatch.setitem(server.jobs, job, {"id": job, "title": "", "params": PLAIN})
    assert server.clip_target(job, PLAIN).parent.name.endswith("_a-train-crosses-a-snowy-bridge_adc366")
    # a place named for it (a film's segment) is kept as it is
    _, _, named = server.build("video", {**PLAIN, "output": "projects/film/seg01.mp4"}, job)
    assert Path(named[0]) == server.HOME / "projects/film/seg01.mp4"
    # a job looked over before it is in the queue (its title is not known yet) leaves no folder of another name behind
    before = sorted(p.name for p in (server.HOME / "renders").rglob("*") if p.is_dir())
    assert Path(server.build("video", PLAIN, "1109-194433-eeeeee")[2][0]) == server.HOME / "renders/1109-194433-eeeeee.mp4"
    assert sorted(p.name for p in (server.HOME / "renders").rglob("*") if p.is_dir()) == before
    # a command built only to be checked leaves no take folder behind
    before = sorted(p.name for p in (server.HOME / "renders").rglob("*") if p.is_dir())
    _, _, checked = server.build("video", PLAIN, "check")
    assert Path(checked[0]) == server.HOME / "renders/check.mp4"
    assert sorted(p.name for p in (server.HOME / "renders").rglob("*") if p.is_dir()) == before


def test_a_clip_that_opens_on_one_still_to_be_made_waits_for_it_in_either_layout(tmp_path):
    jobs = {}
    hub = sparks.Hub(tmp_path, ROOT, True, jobs, [], threading.Lock(), threading.Event(), lambda: {}, lambda path: tmp_path / path,
                     lambda job, worker: None, lambda kind, params: 60)
    first = {"id": "1109-194432-adc366", "kind": "video", "status": "queued", "title": "Train through snow", "params": dict(PLAIN)}
    place = take_names.clip(tmp_path, first["id"], first["title"], PLAIN["prompt"]).relative_to(tmp_path).as_posix()
    assert place.startswith("renders/") and place.endswith("_train-through-snow_adc366.mp4")
    for wanted in (place, "renders/1109-194432-adc366.mp4"):  # its new place, or the flat one an older page named
        second = {"id": "1109-194500-bbbbbb", "kind": "video", "status": "queued", "title": "next", "params": {**PLAIN, "first_frame_from": wanted}}
        jobs.clear()
        jobs.update({first["id"]: first, second["id"]: second})
        assert hub.waits(second) and not hub.waits(first)
    second["params"]["first_frame_from"] = "renders/another.mp4"
    assert not hub.waits(second)
    first["params"]["output"] = "projects/film/seg01.mp4"  # a named place is the one waited for
    second["params"]["first_frame_from"] = "projects/film/seg01.mp4"
    assert hub.waits(second)


def test_a_long_video_and_its_parts_share_the_takes_folder(monkeypatch, tmp_path):
    job = {"id": "1109-200000-cccccc", "kind": "long", "title": "1 min: harbour at dawn", "status": "running", "log": "", "outputs": [],
           "params": {**PLAIN, "total_seconds": 20, "segment_seconds": 10}}
    monkeypatch.setitem(server.jobs, job["id"], job)
    built = []

    def build(kind, piece, job_id):
        built.append(piece)
        return ["true"], {}, [str(server.HOME / piece["output"])]

    def run(job, command, env, label=""):
        (server.HOME / built[-1]["output"]).write_bytes(b"clip")
        return True
    monkeypatch.setattr(server, "build", build)
    monkeypatch.setattr(server, "run", run)
    monkeypatch.setattr(server, "last_frame", lambda video, image: Path(image).write_bytes(b"jpg"))
    monkeypatch.setattr(server.chain, "join", lambda clips, final, total, fps, folder, env=None: Path(final).write_bytes(b"mp4"))
    assert server.long_take(job)
    final = Path(job["outputs"][0])
    assert final.parts[0] == "renders" and final.parent.name.endswith("_1-min-harbour-at-dawn_cccccc") and final.name == final.parent.name + ".mp4"
    assert [Path(p).name for p in job["outputs"][1:]] == ["seg001.mp4", "seg002.mp4"] and {Path(p).parent for p in job["outputs"]} == {final.parent}
    assert built[1]["image"] == (final.parent / "seg001_first.jpg").as_posix()


def test_the_trash_removes_a_clips_emptied_folder():
    import clear  # noqa: F401
    from fastapi.testclient import TestClient
    client = TestClient(server.app)
    stem = "2026-11-09_194432_train-through-snow_ffffff"
    folder = server.HOME / "renders/2026-11" / stem
    folder.mkdir(parents=True)
    (folder / f"{stem}.mp4").write_bytes(b"clip")
    done = client.post("/api/clear", json={"paths": [f"renders/2026-11/{stem}/{stem}.mp4"], "films": True}).json()
    assert done["files"] == 1 and not done["skipped"] and not folder.exists() and (server.HOME / "renders/2026-11").is_dir()
    assert client.post("/api/trash/restore").json()["files"] == 1 and (folder / f"{stem}.mp4").is_file()


def test_pictures_get_their_own_folder_and_readable_names():
    job = "1109-193812-a9fb3a"
    params = {"prompt": "Snowy train station at night, sodium lamps", "count": 2, "seed": 7}
    folder = take_names.pictures(server.HOME, job, params)  # where server.build sends the scout script
    outputs = [str(folder / name) for name in ("scout_s7.png", "scout_s8.png", "sheet.jpg")]
    assert folder.parents[1] == server.HOME / "scouts" and "take_names.pictures(HOME, job_id, params)" in (ROOT / "app/server.py").read_text()
    assert folder.name.endswith("-11-09_193812_snowy-train-station-at-night-sodium_a9fb3a")
    assert [Path(o).name for o in outputs] == ["scout_s7.png", "scout_s8.png", "sheet.jpg"]  # what the render scripts write
    # when the job is done its pictures carry the take's name; the sheet keeps its own
    folder.mkdir(parents=True, exist_ok=True)
    for path in outputs:
        Path(path).write_bytes(b"x")
    named = take_names.named(outputs)
    assert [Path(o).name for o in named] == [f"{folder.name}_s7.png", f"{folder.name}_s8.png", "sheet.jpg"]
    assert all(Path(o).is_file() for o in named) and not Path(outputs[0]).exists()
    assert take_names.named(named) == named  # nothing to do twice
    # an older flat folder and a clip are left alone
    old = server.HOME / "scouts/oldjob"
    old.mkdir(parents=True, exist_ok=True)
    (old / "scout_s1.png").write_bytes(b"x")
    assert take_names.named([str(old / "scout_s1.png")]) == [str(old / "scout_s1.png")] and (old / "scout_s1.png").is_file()
    assert take_names.pictures(server.HOME, "check", params) == server.HOME / "scouts/check"
    assert take_names.pictures(server.HOME, job, {"prompts": ["A red door", "A blue door"]}).name.endswith("_a-red-door_a9fb3a")


def test_organize_through_the_app_moves_older_takes_keeps_old_paths_opening_and_undoes():
    from fastapi.testclient import TestClient
    client = TestClient(server.app)
    old = server.HOME / "voice"
    old.mkdir(parents=True, exist_ok=True)
    (old / "0102-030405-0a0b0c.wav").write_bytes(b"RIFF")
    (old / "0102-030405-0a0b0c.take.json").write_text('{"id": "0102-030405-0a0b0c", "path": "voice/0102-030405-0a0b0c.wav", "text": "Good morning."}')
    seen = client.get("/api/organize").json()
    assert seen["takes"] >= 1 and seen["kinds"]["voice line"] >= 1
    done = client.post("/api/organize", json={}).json()
    assert done["takes"] >= 1 and not done["failed"] and not (old / "0102-030405-0a0b0c.wav").exists()
    moved = server.organized()["voice/0102-030405-0a0b0c.wav"]
    assert moved.endswith("_good-morning_0a0b0c.wav") and (server.HOME / moved).is_file()
    assert server.inside("voice/0102-030405-0a0b0c.wav") == (server.HOME / moved).resolve()  # the old path still opens
    assert client.get("/api/voice/takes/0102-030405-0a0b0c").json()["path"] == moved  # and the take is found by its id
    assert client.get("/api/organize").json()["undo"] >= 2
    back = client.post("/api/organize", json={"undo": True}).json()
    assert back["files"] >= 2 and (old / "0102-030405-0a0b0c.wav").is_file() and "voice/0102-030405-0a0b0c.wav" not in server.organized()
    # refused while a job waits: it may be reading these files
    server.jobs["busy-organize"] = {"id": "busy-organize", "status": "queued", "kind": "video", "params": {}, "outputs": []}
    try:
        assert client.post("/api/organize", json={}).status_code == 409
    finally:
        server.jobs.pop("busy-organize")
