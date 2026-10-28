"""The path checks and the trash of app/clear.py. Run: python3 -m pytest tests/test_clear.py (or python3 tests/test_clear.py)."""

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import clear  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def studio():
    home = Path(tempfile.mkdtemp()) / "TensorFoldStudio"
    for name in ("renders", "uploads", "qa", "projects/p1", "slate/f1", "loras", "scouts/s1"):
        (home / name).mkdir(parents=True)
    jobs, order, lock = {}, [], threading.Lock()
    router, movable = clear.attach(home, jobs, order, lock)
    app = FastAPI()
    app.include_router(router)
    return home, jobs, order, movable, TestClient(app)


def job(jobs, order, job_id, status, outputs, **params):
    jobs[job_id] = {"id": job_id, "status": status, "outputs": outputs, "params": params, "created": len(order)}
    order.append(job_id)


def write(home, name, text="x"):
    (home / name).write_text(text)
    return name


def refused(movable, path):
    try:
        movable(path)
    except ValueError as error:
        return str(error)
    return None


def test_paths():
    home, _, _, movable, _ = studio()
    outside = home.parent / "secret.mp4"
    outside.write_text("x")
    write(home, "renders/a.mp4"), write(home, "projects/p1/project.json"), write(home, "projects/p1/seg.mp4")
    write(home, "loras/style.safetensors"), write(home, "renders/notes.txt"), write(home, "top.mp4")
    os.symlink(outside, home / "renders/link.mp4")
    os.symlink(home.parent, home / "renders/up")
    assert movable("renders/a.mp4") == (home / "renders/a.mp4").resolve()
    assert movable(str(home / "projects/p1/seg.mp4"))
    for bad in ("../secret.mp4", str(outside), "renders/../../secret.mp4", "renders/up/secret.mp4", "renders/link.mp4",
                "projects/p1/project.json", "loras/style.safetensors", "renders/notes.txt", "top.mp4", "renders",
                "renders/missing.mp4", "", "/etc/passwd", "trash/x.mp4", "renders/a.mp4\x00.png"):
        assert refused(movable, bad), bad


def test_delete_restore_empty():
    home, jobs, order, _, web = studio()
    write(home, "renders/a.mp4", "aaaa"), write(home, "qa/j1-take1.jpg"), write(home, "uploads/j1_first.jpg")
    write(home, "uploads/own.png"), write(home, "uploads/shared.png"), write(home, "renders/b.mp4"), write(home, "renders/c.mp4")
    write(home, "slate/f1/take.mp4"), write(home, "slate/f1/project.json", json.dumps({"take": "slate/f1/take.mp4"}))
    job(jobs, order, "j1", "done", ["renders/a.mp4"], image="uploads/own.png", references=["uploads/shared.png"])
    job(jobs, order, "j2", "failed", [], references=["uploads/shared.png"])
    job(jobs, order, "j3", "running", ["renders/c.mp4"], image="uploads/shared.png")
    job(jobs, order, "j4", "done", ["slate/f1/take.mp4"])
    out = web.post("/api/clear", json={"jobs": ["j1", "j3", "nope"], "paths": ["renders/b.mp4", "../x.mp4", "renders/c.mp4"]}).json()
    assert out["jobs"] == 1 and out["files"] == 5, out  # a.mp4, the take sheet, the first frame, own.png, b.mp4
    why = {s["path"]: s["why"] for s in out["skipped"]}
    assert "uploads/shared.png" in why and "renders/c.mp4" in why and "j3" in why and "../x.mp4" in why, why
    assert order == ["j2", "j3", "j4"] and (home / "renders/c.mp4").is_file() and not (home / "renders/a.mp4").exists()
    state = web.get("/api/trash").json()
    assert state["files"] == 5 and state["last"] == {"when": state["last"]["when"], "files": 5, "jobs": 1}
    back = web.post("/api/trash/restore").json()
    assert back == {"files": 5, "jobs": 1, "missing": []} and order == ["j1", "j2", "j3", "j4"]
    assert (home / "renders/a.mp4").read_text() == "aaaa" and web.get("/api/trash").json()["files"] == 0
    assert web.post("/api/trash/restore").status_code == 400
    # every finished job: a clip that is in a film stays where it is, its card goes
    out = web.post("/api/clear", json={"finished": True, "failed": True}).json()
    assert out["jobs"] == 3 and order == ["j3"] and (home / "slate/f1/take.mp4").is_file(), out
    assert (home / "uploads/shared.png").is_file()  # the running job reads it
    assert (home / "slate/f1/project.json").is_file()
    assert web.delete("/api/trash").status_code == 400 and web.get("/api/trash").json()["files"] == 4
    assert web.delete("/api/trash?confirm=erase").json()["files"] == 4
    assert web.get("/api/trash").json() == {"files": 0, "bytes": 0, "folder": str(home / "trash"), "last": None}


def test_a_song_goes_to_the_trash_with_its_yue2_folder_and_comes_back():
    home, jobs, order, movable, web = studio()
    (home / "music" / "m1.yue2").mkdir(parents=True)
    write(home, "music/m1.wav"), write(home, "music/m1.yue2/score.abc"), write(home, "music/m1.yue2/settings.json")
    write(home, "music/notes.txt")
    assert refused(movable, "music/notes.txt") and refused(movable, "music/m1.yue2")
    jobs["m1"] = {"id": "m1", "kind": "music", "status": "done", "outputs": ["music/m1.wav"], "params": {}, "created": 0}
    order.append("m1")
    write(home, "music/m1.take.json", "{}")
    (home / "music/m1.yue2/more").mkdir()
    write(home, "music/m1.yue2/more/x.npy")
    out = web.post("/api/clear", json={"jobs": ["m1"]}).json()
    assert out["files"] == 5 and not (home / "music/m1.wav").exists() and not (home / "music/m1.yue2").exists(), out
    assert web.post("/api/trash/restore").json()["files"] == 5
    assert (home / "music/m1.wav").is_file() and (home / "music/m1.yue2/score.abc").is_file() and (home / "music/m1.take.json").is_file()
    # a song a film names by its take id (a soundtrack) stays, and so does its folder; so does a song being made
    write(home, "projects/p1/project.json", json.dumps({"soundtrack": {"take": "m1", "music_db": -12}}))
    out = web.post("/api/clear", json={"paths": ["music/m1.wav"]}).json()
    assert out["files"] == 0 and (home / "music/m1.wav").is_file() and (home / "music/m1.yue2/score.abc").is_file(), out
    (home / "projects/p1/project.json").unlink()
    jobs["m2"] = {"id": "m2", "kind": "music", "status": "running", "outputs": [], "params": {}, "created": 1}
    write(home, "music/m2.wav")
    out = web.post("/api/clear", json={"paths": ["music/m2.wav"]}).json()
    assert out["files"] == 0 and (home / "music/m2.wav").is_file() and "being made" in out["skipped"][0]["why"], out
    # an export queued with the song as its soundtrack keeps it too
    jobs["e1"] = {"id": "e1", "kind": "export", "status": "queued", "outputs": [], "params": {"soundtrack": {"take": "m1"}}, "created": 2}
    assert web.post("/api/clear", json={"paths": ["music/m1.wav"]}).json()["files"] == 0


if __name__ == "__main__":
    test_paths(), test_delete_restore_empty(), test_a_song_goes_to_the_trash_with_its_yue2_folder_and_comes_back()
    print("ok")
