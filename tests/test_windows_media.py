"""Windows FFmpeg discovery and bounded CPU media routes; no GPU or model."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from test_windows_ui import client, server  # noqa: F401
import windows_media


@pytest.mark.skipif(sys.platform != "win32", reason="the server module imported on Windows (WINDOWS at import time); the windows-arm64 CI job runs it")
def test_job_path_uses_windows_separators_and_keeps_system_path(client):
    env = server.environment({})
    assert "/opt/homebrew/bin:" not in env["PATH"]
    assert env["PATH"].endswith(os.environ.get("PATH", "")) or env["PATH"] == os.environ.get("PATH", "")


def test_media_path_requires_a_complete_pair(monkeypatch, tmp_path):
    monkeypatch.setattr(windows_media.sys, "platform", "win32")
    monkeypatch.setattr(windows_media, "bin_folder", lambda: tmp_path)
    (tmp_path / "ffmpeg.exe").touch()
    assert windows_media.search_path("existing") == "existing"
    (tmp_path / "ffprobe.exe").touch()
    assert windows_media.search_path("existing") == str(tmp_path) + os.pathsep + "existing"


@pytest.fixture
def media(client, monkeypatch):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("install FFmpeg/ffprobe for the media integration check")
    monkeypatch.setattr(server, "jobs", {})
    monkeypatch.setattr(server, "order", [])
    monkeypatch.setattr(server, "wake", SimpleNamespace(set=lambda: None))
    sources = []
    for name, audio in (("silent", False), ("sound", True)):
        target = server.HOME / "uploads" / f"windows-media-{name}.mp4"
        command = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=128x96:rate=24:duration=1"]
        if audio:
            command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac"]
        command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "1", str(target)]
        subprocess.run(command, check=True, capture_output=True, timeout=30)
        sources.append(target.relative_to(server.HOME).as_posix())
    return client, sources


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows owned process-tree media integration")
def test_probe_frame_and_trim_join_export_with_and_without_sound(media):
    client, sources = media
    for path in sources:
        info = client.get("/api/probe", params={"path": path}).json()
        assert info["width"] == 128 and info["height"] == 96
        assert .95 <= info["duration"] <= 1.1
        frame = client.post("/api/frame", json={"path": path, "at": "first"})
        assert frame.status_code == 200, frame.text
        assert client.get("/files/" + frame.json()["path"]).status_code == 200
    response = client.post("/api/jobs", json={"kind": "export", "params": {
        "clips": [{"path": path, "in": .25, "out": .75} for path in sources], "width": 128, "height": 96,
    }})
    assert response.status_code == 200, response.text
    job = server.jobs[response.json()["id"]]
    job["status"] = "running"
    server.export(job)
    output = job["outputs"][0]
    info = client.get("/api/probe", params={"path": output}).json()
    assert info["sound"] and .95 <= info["duration"] <= 1.15
    assert (info["width"], info["height"]) == (128, 96)
    assert client.post("/api/jobs", json={"kind": "video", "params": {"prompt": "test"}}).status_code == 501


def test_unreadable_media_is_not_a_success_shaped_probe(client, monkeypatch):
    monkeypatch.setattr(server, "media_tools", lambda: {"ffmpeg": True, "ffprobe": True})
    monkeypatch.setattr(server.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout="{}", stderr="invalid media",
    ))
    response = client.get("/api/probe", params={"path": "uploads/invalid.mp4"})
    assert response.status_code == 400


@pytest.mark.parametrize("change", [{"width": 0}, {"height": 513}, {"soundtrack": "unsupported"}])
def test_export_refuses_unqualified_settings(media, change):
    client, sources = media
    response = client.post("/api/jobs", json={"kind": "export", "params": {
        "clips": [{"path": sources[0]}], **change,
    }})
    assert response.status_code == 400


def test_rapid_timeline_additions_are_serialized_and_keep_both_clips():
    from test_shell_page import run
    root = Path(__file__).resolve().parents[1]
    code = (root / "app/static/index.html").read_text(encoding="utf-8").split("let timelineAdd = ")[1].split("function viewEditor()")[0]
    out = run("""
      %s
      S.ffmpeg = true; tab = "Editor"; let saved = { id: "p1", name: "Disposable", timeline: [] };
      globalThis.ensureProject = async () => ({ id: "p1" }); project = { ...saved };
      globalThis.api = async (path, body) => {
        await new Promise(resolve => setTimeout(resolve, 5));
        if (path.startsWith("/api/probe")) return { duration: 2 };
        if (body) saved = JSON.parse(JSON.stringify(body));
        return JSON.parse(JSON.stringify(saved));
      };
      await Promise.all([addToTimeline("uploads/first.mp4"), addToTimeline("uploads/second.mp4")]);
      out.paths = saved.timeline.map(c => c.path); out.durations = saved.timeline.map(c => c.duration);
    """ % ("let timelineAdd = " + code))
    assert out["paths"] == ["uploads/first.mp4", "uploads/second.mp4"]
    assert out["durations"] == [2, 2]


def test_a_failed_timeline_addition_reports_the_error_and_releases_the_next_one():
    from test_shell_page import run
    root = Path(__file__).resolve().parents[1]
    code = (root / "app/static/index.html").read_text(encoding="utf-8").split("let timelineAdd = ")[1].split("function viewEditor()")[0]
    out = run("""
      %s
      S.ffmpeg = true; let saved = { id: "p1", name: "Disposable", timeline: [] }, fail = true;
      globalThis.ensureProject = async () => ({ id: "p1" });
      globalThis.api = async (path, body) => {
        if (path.startsWith("/api/probe")) return { duration: 2 };
        if (fail) { fail = false; throw new Error("save source unavailable"); }
        if (body) saved = JSON.parse(JSON.stringify(body));
        return JSON.parse(JSON.stringify(saved));
      };
      try { await addToTimeline("uploads/bad.mp4"); } catch (e) { out.error = e.message; }
      await addToTimeline("uploads/good.mp4");
      out.paths = saved.timeline.map(c => c.path); out.notifications = [...toasts];
    """ % ("let timelineAdd = " + code))
    assert out["error"] == "save source unavailable"
    assert out["paths"] == ["uploads/good.mp4"]
    assert any("Could not add the clip" in note for note in out["notifications"])
