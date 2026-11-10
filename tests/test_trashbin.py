"""Whole films (Slate) in the studio's trash, listed and put back: python3 -m pytest tests/test_trashbin.py."""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

HOME = server.HOME
client = TestClient(server.app)


def film(name):
    answer = client.post("/api/slate/projects", json={"project": {"name": name, "scenes": []}})
    assert answer.status_code == 200, answer.text
    return answer.json()["id"]


def trashed():
    return {one["id"]: one for one in client.get("/api/slate/trash").json()}


def test_a_removed_film_is_listed_in_the_trash_and_put_back():
    made = film("Night bus (test)")
    assert client.delete(f"/api/slate/projects/{made}").status_code == 200
    assert made not in {one["id"] for one in client.get("/api/slate/projects").json()}
    gone = trashed()[made]
    assert gone["name"] == "Night bus (test)" and gone["folder"].startswith(f"slate-{made}-")
    assert client.post(f"/api/slate/trash/{gone['folder']}/restore").json() == {"id": made}
    assert made in {one["id"] for one in client.get("/api/slate/projects").json()}
    assert made not in trashed()
    assert client.post(f"/api/slate/trash/{gone['folder']}/restore").status_code == 404  # already back


def test_a_film_is_not_put_back_over_one_of_the_same_id():
    made = film("Twice")
    client.delete(f"/api/slate/projects/{made}")
    folder = trashed()[made]["folder"]
    (HOME / "slate" / made).mkdir(parents=True)
    try:
        assert client.post(f"/api/slate/trash/{folder}/restore").status_code == 409
    finally:
        shutil.rmtree(HOME / "slate" / made)


def test_only_a_removed_films_folder_is_put_back():
    (HOME / "trash").mkdir(exist_ok=True)
    (HOME / "trash" / "slate-x-1").write_text("a file, not a folder")
    for name in ("slate-x-1", "elsewhere-1", "slate-..-1"):
        assert client.post(f"/api/slate/trash/{name}/restore").status_code == 404, name
    assert "x" not in trashed()


def test_two_of_the_same_film_in_the_trash_never_nest():
    made = film("Two copies")
    client.delete(f"/api/slate/projects/{made}")
    first = trashed()[made]["folder"]
    second = f"slate-{made}-1"
    shutil.copytree(HOME / "trash" / first, HOME / "trash" / second)
    assert client.post(f"/api/slate/trash/{first}/restore").status_code == 200
    assert client.post(f"/api/slate/trash/{second}/restore").status_code == 409
    assert not (HOME / "slate" / made / second).exists() and (HOME / "trash" / second).is_dir()


def test_the_windows_preview_lets_a_film_be_put_back_as_it_lets_one_be_removed():
    assert server.WINDOWS_WRITES.fullmatch("/api/slate/projects/p-1")
    assert server.WINDOWS_WRITES.fullmatch("/api/slate/trash/slate-p-1-100/restore")


def test_a_film_taken_in_never_takes_the_id_of_one_being_put_back():
    """A film put back and one taken in with its id, at once: the taken one gets an id of its own."""
    (HOME / "slate" / "p-being-put-back").mkdir(parents=True)  # the folder is there, its project.json not yet
    try:
        answer = client.post("/api/slate/projects", json={"project": {"id": "p-being-put-back", "name": "Taken in", "scenes": []}})
        assert answer.status_code == 200 and answer.json()["id"] != "p-being-put-back"
        assert not (HOME / "slate" / "p-being-put-back" / "project.json").exists()
    finally:
        shutil.rmtree(HOME / "slate" / "p-being-put-back")


def test_a_slate_film_brought_in_from_the_suite_never_takes_the_id_of_one_being_put_back():
    """The suite's "Make a Slate film" keeps a Slate project.json's own id, unless a film of that id is in Production or
    its folder is being put back from the trash: then it is a film of its own, and the one put back is untouched."""
    import json
    from test_suite import SLATE  # a small Slate project.json, as the suite's own tests bring it in
    taken = "p-suite-taken"
    (HOME / "slate" / taken).mkdir(parents=True)  # the folder is there, its project.json not yet
    try:
        body = json.dumps({**SLATE, "id": taken, "name": "From the suite"}).encode()
        found = client.post("/api/suite/import", files={"file": ("project.json", body)})
        assert found.status_code == 200, found.text
        found = found.json()
        made = client.post(f"/api/suite/imports/{found['id']}/slate", json={}).json()
        assert made["project"] != taken and not (HOME / "slate" / taken / "project.json").exists()
    finally:
        shutil.rmtree(HOME / "slate" / taken)
