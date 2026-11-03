"""Existing CPU beat/export routes on Windows; no model, GPU or browser."""
import io
import json
import math
import shutil
import wave
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import numpy as np
import pytest

from test_windows_ui import client, server  # noqa: F401
import beats
import suite


@pytest.fixture
def windows(client, monkeypatch):
    monkeypatch.setattr(beats, "WINDOWS", True)
    return client


def test_beat_route_is_enabled_without_opening_other_music_actions(windows, monkeypatch):
    path = server.HOME / "uploads" / "beat source.wav"
    path.write_bytes(b"fixture")
    calls = []
    monkeypatch.setattr(beats, "analyse", lambda source: calls.append(source) or {
        "bpm": 120, "beats": [0, .5, 1, 1.5], "downbeats": [0], "bars": 1, "duration": 2,
        "confidence": 1, "first": 0, "beatsPerBar": 4, "version": beats.VERSION})
    for _ in range(2):
        response = windows.post("/api/music/beats", json={"path": "uploads/beat source.wav"})
        assert response.status_code == 200, response.text
        assert response.json()["path"] == "uploads/beat source.wav" and response.json()["bpm"] == 120
    assert calls == [path.resolve()]
    assert windows.post("/api/music/takes", json={"caption": "Not enabled"}).status_code == 501
    assert windows.post("/api/roughcut", json={}).status_code == 501
    assert windows.post("/api/music/beats", json={"path": "../outside.wav"}).status_code == 400
    assert windows.post("/api/music/beats", json={"path": "uploads/missing.wav"}).status_code == 404


def test_windows_beat_decode_is_bounded_and_never_returns_a_truncated_success(monkeypatch):
    monkeypatch.setattr(beats, "WINDOWS", True)
    monkeypatch.setattr(beats, "ffmpeg", lambda: "ffmpeg")
    calls = []
    size = [beats.WINDOWS_MAX_SECONDS * 4]
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"\0" * size[0], stderr=b"")
    monkeypatch.setattr(beats.subprocess, "run", run)
    assert len(beats.decode(Path("fixture.wav"), rate=1)) == 600
    assert calls[-1][0][calls[-1][0].index("-t") + 1] == "601" and calls[-1][1]["timeout"] == 300
    size[0] += 4
    with pytest.raises(RuntimeError, match="at most ten minutes"):
        beats.decode(Path("fixture.wav"), rate=1)
    monkeypatch.setattr(beats, "WINDOWS", False)
    assert len(beats.decode(Path("fixture.wav"), rate=1)) == 601
    assert "-t" not in calls[-1][0]


@pytest.mark.parametrize("duration,expected", [
    (600, 200), (600.001, 400), (None, 400), (True, 400), ("600", 400),
    (float("nan"), 400), (float("inf"), 400), (-1, 400),
    pytest.param(10 ** 400, 400, id="oversized-integer"),
])
def test_cached_beats_honor_the_same_windows_duration_limit(windows, monkeypatch, duration, expected):
    source = server.HOME / "uploads" / "cached beats.wav"
    source.write_bytes(b"fixture")
    stat = source.stat()
    beats.sidecar(source).write_text(json.dumps({
        "source": {"size": stat.st_size, "mtime": round(stat.st_mtime, 3), "version": beats.VERSION},
        "duration": duration, "bpm": 120, "beats": [0, .5, 1, 1.5],
    }))
    monkeypatch.setattr(beats, "analyse", lambda *args: pytest.fail("A matching cache should not decode audio again"))
    response = windows.post("/api/music/beats", json={"path": "uploads/cached beats.wav"})
    assert response.status_code == expected, response.text
    if expected == 200:
        assert response.json()["duration"] == 600
    else:
        assert "duration" in response.json()["error"] or "ten minutes" in response.json()["error"]


def test_non_windows_cached_beats_keep_the_existing_duration_behavior(tmp_path, monkeypatch):
    monkeypatch.setattr(beats, "WINDOWS", False)
    source = tmp_path / "long.wav"
    source.write_bytes(b"fixture")
    stat = source.stat()
    beats.sidecar(source).write_text(json.dumps({
        "source": {"size": stat.st_size, "mtime": round(stat.st_mtime, 3), "version": beats.VERSION},
        "duration": 1200, "beats": [0, .5, 1, 1.5],
    }))
    monkeypatch.setattr(beats, "analyse", lambda *args: pytest.fail("Should read the matching cache"))
    assert beats.cached(source)["duration"] == 1200


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg needed for the real CPU beat-finding check")
def test_existing_beat_finder_measures_a_real_click_track_on_windows(windows):
    rate, seconds = 44100, 8
    sound = np.zeros(rate * seconds, np.float32)
    pulse = np.sin(2 * np.pi * 1000 * np.arange(1323) / rate) * np.hanning(2646)[1323:]
    for index in range(seconds * 2):
        start = index * rate // 2
        sound[start:start + len(pulse)] += pulse * (.8 if index % 4 == 0 else .5)
    path = server.HOME / "uploads" / "known 120 bpm.wav"
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes((sound * 32767).astype("<i2").tobytes())
    response = windows.post("/api/music/beats", json={"path": path.relative_to(server.HOME).as_posix()})
    assert response.status_code == 200, response.text
    found = response.json()
    assert abs(found["bpm"] - 120) <= 2 and abs(found["duration"] - seconds) < .01
    assert len(found["beats"]) >= 12 and found["beats"] == sorted(set(found["beats"]))
    assert all(math.isfinite(b) for b in found["beats"]) and 0 <= found["confidence"] <= 1
    assert found["downbeats"] and "\\" not in found["path"]


def test_timeline_handoff_handles_windows_drive_urls_and_case_colliding_names(windows, monkeypatch):
    clips = []
    for folder, name in (("first", "Shot & A.mp4"), ("second", "shot & a.mp4")):
        path = server.HOME / "uploads" / "handoff-check" / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(folder.encode())
        clips.append({"path": path.relative_to(server.HOME).as_posix(), "in": .25, "out": .75, "duration": 1})
    monkeypatch.setattr(suite, "probe", lambda path: {"seconds": 1, "rate": (24, 1), "width": 128, "height": 96, "audio": True, "video": True})
    project = windows.post("/api/projects", json={"name": "Portable cut"}).json()
    project["timeline"] = clips
    assert windows.put("/api/projects/" + project["id"], json=project).status_code == 200
    url = "/api/export/timeline/" + project["id"]
    response = windows.get(url + ".fcpxml")
    assert response.status_code == 200, response.text
    root = ET.fromstring(response.content)
    sources = [rep.get("src") for rep in root.iter("media-rep")]
    assert sources == [(server.HOME / clip["path"]).resolve().as_uri() for clip in clips]
    sequence = root.find(".//sequence")
    assert sequence.get("duration") == "24/24s"
    spans = root.findall(".//spine/asset-clip")
    assert [s.get("start") for s in spans] == ["6/24s", "6/24s"]
    assert [s.get("duration") for s in spans] == ["12/24s", "12/24s"]
    response = windows.get(url + ".edl")
    assert response.status_code == 200 and "00:00:00:06" in response.text and "00:00:00:18" in response.text
    for destination in ("", r"D:\Cuts & Reviews"):
        response = windows.get(url + ".zip", params={"root": destination})
        assert response.status_code == 200, response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            media = [name for name in bundle.namelist() if "/media/" in name]
            assert len(media) == len({name.casefold() for name in media}) == 2
            assert {bundle.read(name) for name in media} == {b"first", b"second"}
            xml = ET.fromstring(bundle.read(project["id"] + "/" + project["id"] + ".fcpxml"))
            refs = [rep.get("src") for rep in xml.iter("media-rep")]
            if destination:
                assert all(ref.startswith("file:///D:/Cuts%20%26%20Reviews/") for ref in refs)
            else:
                assert all(project["id"] + "/" + unquote(ref) in bundle.namelist() for ref in refs)
            assert all("\\" not in ref and " " not in ref and "&" not in ref for ref in refs)
