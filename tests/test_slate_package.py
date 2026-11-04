"""Portable Slate archives round-trip across Studio folders without models or GPU work."""
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import threading
import wave
import zipfile

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import server
import slate
import slate_package as portable
import suite


@pytest.fixture
def film(tmp_path):
    home = tmp_path / "source"
    home.mkdir()
    for folder, color in (("uploads", "green"), ("scouts", "blue")):
        path = home / folder / "frame.png"
        path.parent.mkdir()
        Image.new("RGB", (48, 32), color).save(path)
    sound = home / "sound" / "reference.wav"
    sound.parent.mkdir()
    with wave.open(str(sound), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(44100)
        target.writeframes(b"\0\0" * 100)
    (home / "uploads" / "take.mp4").write_bytes(b"packaged, not decoded")
    source = home / "uploads" / "suite" / "script-1"
    source.mkdir(parents=True)
    screenplay = b"Title: Portable\n\nINT. HALL - DAY\n\nMARA waits beside a clock.\n"
    (source / "upload.fountain").write_bytes(screenplay)
    project = slate.blank('Portable "film", café')
    project["defaults"].update(brain="local:writer", localEndpoint="http://192.168.1.10:8080/v1", localModel="machine-only")
    project["characters"] = [{"id": "person-1", "name": "Mara", "images": ["uploads/frame.png"]}]
    project["locations"] = [{"id": "place-1", "name": "Hall", "images": ["scouts/frame.png"]}]
    project["voices"] = [{"id": "voice-1", "characterId": "person-1", "drama": {"path": "sound/reference.wav", "consent": True}}]
    project["references"] = [{"id": "ref-1", "mediaPath": "uploads/frame.png"}]
    project["importedFrom"] = {"app": "fountain", "import": "script-1"}
    project["scenes"] = [{"id": "scene-1", "name": "Hall", "synopsis": "A, B and \"C\".", "shots": [
        {"id": "shot-1", "name": "Wide", "intent": "Crew note stays distinct.", "prompt": "# Subject\nMara waits.",
         "spec": {"durationSec": 5, "fps": 24, "aspectRatio": "16:9"},
         "images": ["uploads/frame.png"], "takes": [{"id": "take-1", "mediaPath": "uploads/take.mp4", "jobId": "old-job",
             "source": {"from": "http://192.168.1.10:7870", "path": "deleted/original.mp4", "engine": "cuda"}}],
         "tf": {"start": "image", "image": "uploads\\frame.png", "cast": ["person-1"], "location": "place-1",
                "seed": 0, "lines": [{"characterId": "person-1", "voiceId": "voice-1", "text": "Now."}],
                "storyboardJob": "old-job", "storyboardBefore": "deleted/old-frame.png"}},
        {"id": "shot-2", "name": "Close", "prompt": "The clock.", "spec": {"durationSec": 5},
         "takes": [], "tf": {"image": "scouts/frame.png"}},
    ]}]
    return home, project, screenplay


def unpack_bytes(blob: bytes, home: Path, name="portable.zip"):
    home.mkdir(parents=True, exist_ok=True)
    folder = home / "uploads" / "suite" / "new-import"
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / "upload.zip"
    archive.write_bytes(blob)
    found = suite.read_upload(archive, name, "", folder, home)
    return found, json.loads((folder / "project.json").read_text(encoding="utf-8"))


def changed_zip(blob: bytes, change):
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    change(files)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return output.getvalue()


def test_portable_roundtrip_keeps_media_script_captions_and_links_after_source_removal(film, tmp_path):
    home, project, screenplay = film
    before = copy.deepcopy(project)
    result = portable.package(project, home)
    blob = (home / result["path"]).read_bytes()
    assert project == before
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        data = json.loads(archive.read("project.json"))
        assert manifest["schema"] == portable.SCHEMA
        assert archive.read(data["sourceScript"]) == screenplay
        assert "localEndpoint" not in data["defaults"] and data["defaults"]["brain"] == "auto"
        assert data["characters"][0]["images"][0] == data["scenes"][0]["shots"][0]["tf"]["image"]
        assert data["characters"][0]["images"][0] != data["locations"][0]["images"][0]
        assert "storyboardBefore" not in data["scenes"][0]["shots"][0]["tf"]
        assert data["scenes"][0]["shots"][0]["takes"][0]["source"] == {"engine": "cuda"}
        assert result["media_files"] == 4
        rows = list(csv.DictReader(io.StringIO(archive.read("shots.csv").decode("utf-8"))))
        assert len(rows) == 2 and rows[0]["intent"] == "Crew note stays distinct."
        assert rows[0]["prompt"] == "# Subject\nMara waits."
        with Image.open(io.BytesIO(archive.read("contact-sheet-001.jpg"))) as sheet:
            assert sheet.size == (768, 296)
        assert "media/" in archive.read("shots.md").decode("utf-8")
        for name, entry in manifest["files"].items():
            content = archive.read(name)
            assert entry["size"] == len(content) and entry["sha256"] == hashlib.sha256(content).hexdigest()
    shutil.rmtree(home)
    destination = tmp_path / "destination"
    found, imported = unpack_bytes(blob, destination)
    assert found["app"] == "slate" and found["shots"][0]["image"].startswith("uploads/suite/new-import/files/media/")
    shot = imported["scenes"][0]["shots"][0]
    assert shot["tf"]["cast"] == ["person-1"] and shot["tf"]["seed"] == 0
    assert shot["tf"]["lines"][0]["voiceId"] == imported["voices"][0]["id"]
    assert shot["prompt"] == before["scenes"][0]["shots"][0]["prompt"]
    def exists(value, script=False):
        assert (destination / value).is_file()
        return value
    portable.media_refs(imported, exists)
    again = portable.package(imported, destination)
    assert again["media_files"] == 4


@pytest.mark.parametrize("kind", ["missing", "outside", "hidden"])
def test_export_refuses_missing_outside_or_hidden_media_without_an_archive(film, tmp_path, kind):
    home, project, _ = film
    if kind == "missing":
        value = "uploads/missing.png"
    else:
        path = tmp_path / "outside.png" if kind == "outside" else home / ".private" / "frame.png"
        path.parent.mkdir(exist_ok=True)
        Image.new("RGB", (8, 8)).save(path)
        value = str(path)
    project["scenes"][0]["shots"][0]["tf"]["image"] = value
    with pytest.raises(HTTPException) as error:
        portable.package(project, home)
    assert error.value.status_code == 400
    assert not list((home / "exports").glob("*.zip"))


def test_import_checks_media_hashes_and_manifest_file_inventory(film, tmp_path):
    home, project, _ = film
    result = portable.package(project, home)
    blob = (home / result["path"]).read_bytes()
    def corrupt(files):
        name = next(name for name in files if name.startswith("media/"))
        files[name] = b"x" * len(files[name])
    with pytest.raises(HTTPException, match="SHA-256"):
        unpack_bytes(changed_zip(blob, corrupt), tmp_path / "corrupt")
    def missing(files):
        del files[next(name for name in files if name.startswith("media/"))]
    with pytest.raises(HTTPException, match="manifest"):
        unpack_bytes(changed_zip(blob, missing), tmp_path / "missing")
    def unlisted(files):
        files["unlisted.bin"] = b"not part of the portable manifest"
    with pytest.raises(HTTPException, match="manifest"):
        unpack_bytes(changed_zip(blob, unlisted), tmp_path / "unlisted")


def test_missing_optional_screenplay_is_reported_but_outside_script_is_refused(film, tmp_path):
    home, project, _ = film
    project["sourceScript"] = "uploads/lost.fountain"
    result = portable.package(project, home)
    assert any("No original screenplay" in note for note in result["notes"])
    with zipfile.ZipFile(home / result["path"]) as archive:
        assert "sourceScript" not in json.loads(archive.read("project.json"))
    outside = tmp_path / "outside.fountain"
    outside.write_text("Title: Outside")
    project["sourceScript"] = str(outside)
    with pytest.raises(HTTPException, match="inside this Studio"):
        portable.package(project, home)


def test_import_refuses_a_project_reference_outside_the_manifest_even_with_valid_json_hash(film, tmp_path):
    home, project, _ = film
    result = portable.package(project, home)
    blob = (home / result["path"]).read_bytes()
    def changed(files):
        data = json.loads(files["project.json"])
        data["scenes"][0]["shots"][0]["tf"]["image"] = "../outside.png"
        files["project.json"] = json.dumps(data).encode()
        manifest = json.loads(files["manifest.json"])
        manifest["files"]["project.json"] = {"size": len(files["project.json"]),
                                              "sha256": hashlib.sha256(files["project.json"]).hexdigest()}
        files["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(HTTPException, match="not included"):
        unpack_bytes(changed_zip(blob, changed), tmp_path / "outside-ref")


def test_unsupported_portable_schema_is_not_silently_imported_as_plain_json(film, tmp_path):
    home, project, _ = film
    result = portable.package(project, home)
    def changed(files):
        manifest = json.loads(files["manifest.json"])
        manifest["schema"] = "imagination-studio-slate-package-v99"
        files["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(HTTPException, match="unsupported portable"):
        unpack_bytes(changed_zip((home / result["path"]).read_bytes(), changed), tmp_path / "future")


def test_missing_manifest_is_not_imported_as_a_broken_plain_slate_project(film, tmp_path):
    home, project, _ = film
    result = portable.package(project, home)
    def remove_manifest(files):
        del files["manifest.json"]
    blob = changed_zip((home / result["path"]).read_bytes(), remove_manifest)
    with pytest.raises(HTTPException, match="manifest"):
        unpack_bytes(blob, tmp_path / "missing-manifest", name="renamed.zip")


def test_portable_api_roundtrip_uses_existing_import_and_slate_routes(film, tmp_path, monkeypatch):
    source, project, _ = film
    def client(home):
        home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(slate, "HOME", home)
        monkeypatch.setattr(server, "HOME", home)
        app = FastAPI()
        app.include_router(slate.router)
        app.include_router(suite.attach(home, {}, threading.Lock(), {}, app))
        app.add_api_route("/files/{path:path}", server.files)
        @app.exception_handler(HTTPException)
        def error(request, exception):
            return server.JSONResponse({"error": exception.detail}, status_code=exception.status_code)
        return TestClient(app, base_url="http://127.0.0.1")
    first = client(source)
    slate.write(project)
    response = first.post("/api/slate/package", json={"project": project["id"]})
    assert response.status_code == 200, response.text
    archive = first.get("/files/" + response.json()["path"]).content
    second = client(tmp_path / "clean")
    imported = second.post("/api/suite/import", files={"file": ("film.slate.zip", archive, "application/zip")})
    assert imported.status_code == 200, imported.text
    made = second.post(f"/api/suite/imports/{imported.json()['id']}/slate", json={})
    assert made.status_code == 200, made.text
    restored = second.get("/api/slate/projects/" + made.json()["project"]).json()
    assert second.get("/files/" + restored["scenes"][0]["shots"][0]["tf"]["image"]).status_code == 200
    assert second.get("/files/" + restored["voices"][0]["drama"]["path"]).status_code == 200
    before = set((tmp_path / "clean" / "uploads" / "suite").iterdir())
    def corrupt(files):
        del files[next(name for name in files if name.startswith("media/"))]
    invalid = second.post("/api/suite/import", files={"file": ("bad.slate.zip", changed_zip(archive, corrupt), "application/zip")})
    assert invalid.status_code == 400
    assert set((tmp_path / "clean" / "uploads" / "suite").iterdir()) == before
    again = second.post(f"/api/suite/imports/{imported.json()['id']}/slate", json={})
    assert again.json()["project"] != made.json()["project"]


def test_case_colliding_zip_entries_are_refused_before_any_file_is_written(tmp_path):
    archive = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("media/frame.png", b"first")
        bundle.writestr("media/FRAME.png", b"second")
    target = tmp_path / "extracted"
    with pytest.raises(HTTPException, match="duplicate"):
        suite.unpack(archive, target)
    assert not target.exists()


def test_portable_download_flushes_edits_and_exposes_a_file_link_not_an_in_memory_blob():
    from test_slate_page import run
    out = run("""
      SL.p = film();
      answers["/api/slate/projects/p-test"] = { updatedAt: "saved" };
      answers["/api/slate/package"] = { path: "exports/film.slate.zip", filename: "film.slate.zip",
        shots: 2, media_files: 3, notes: [] };
      await Promise.all([slPackage(), slPackage()]);
      out.calls = calls; out.link = slPackageLink().attrs; out.busy = SL.packaging;
      SL.p = { id: "another-film" }; out.other = slPackageLink();
    """)
    assert [call[0] for call in out["calls"]] == ["/api/slate/projects/p-test", "/api/slate/package"]
    assert out["calls"][0][1]["id"] == "p-test"
    assert out["link"]["href"] == "/files/exports/film.slate.zip"
    assert out["link"]["download"] == "film.slate.zip" and not out["busy"]
    assert out["other"] is None
