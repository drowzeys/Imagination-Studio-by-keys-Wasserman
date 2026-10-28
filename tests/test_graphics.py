"""Motion graphics (app/graphics.py, app/graphics/<template>/): python3 -m pytest tests/test_graphics.py.
Nothing is rendered: HyperFrames, Node and the browser are stand-ins and every clip is probed by a stand-in, so each
template's job folder (its page, data.json, data.js and media) and its command are tested, with the settings it
refuses, the transcript cache, the runner's progress and the readiness report. STUDIO_GRAPHICS_SMOKE=1 also renders one
real title at 320x180 with what scripts/setup-graphics.sh installed."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "ImaginationStudio"))
sys.path.insert(0, str(ROOT / "app"))
import graphics  # noqa: E402
import server  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
HOME = server.HOME
PROBES = {".mp4": {"seconds": 8.0, "width": 1280, "height": 720, "audio": True},
          ".wav": {"seconds": 12.5, "width": 0, "height": 0, "audio": True},
          ".png": {"seconds": 0.0, "width": 1024, "height": 1024, "audio": False}}


def fake_install(root: Path) -> None:
    """What setup-graphics.sh leaves in $GRAPHICS_DIR, as stand-ins: Node 22, hyperframes, GSAP and a browser."""

    (root / "node/bin").mkdir(parents=True)
    node = root / "node/bin/node"
    node.write_text("#!/bin/sh\necho v22.23.4\n")
    node.chmod(0o755)
    (root / "node_modules/hyperframes/dist").mkdir(parents=True)
    (root / "node_modules/hyperframes/dist/cli.js").write_text("")
    (root / "node_modules/hyperframes/package.json").write_text(json.dumps({"version": graphics.HYPERFRAMES}))
    (root / "node_modules/gsap/dist").mkdir(parents=True)
    (root / "node_modules/gsap/dist/gsap.min.js").write_text("/* gsap */")
    chrome = root / "chrome"
    chrome.write_text("#!/bin/sh\n")
    chrome.chmod(0o755)
    (root / "browser-path").write_text(str(chrome) + "\n")


@pytest.fixture
def installed(tmp_path, monkeypatch):
    fake_install(tmp_path / "graphics")
    monkeypatch.setenv("GRAPHICS_DIR", str(tmp_path / "graphics"))
    monkeypatch.delenv("HYPERFRAMES_BROWSER_PATH", raising=False)
    monkeypatch.setattr(graphics.shutil, "which", lambda name, path=None: f"/usr/bin/{name}")
    monkeypatch.setattr(graphics, "probe", lambda path: dict(PROBES[Path(path).suffix.lower()]))
    for name in ("renders/a.mp4", "renders/b.mp4", "uploads/one.png", "uploads/two.png", "voice/v1.wav", "music/song.wav"):
        (HOME / name).parent.mkdir(parents=True, exist_ok=True)
        (HOME / name).write_bytes(b"media")
    router, build, title = graphics.attach(HOME, ROOT, False, server.inside, server.relative)
    monkeypatch.setattr(server, "graphics_build", build)
    return {"build": build, "title": title, "root": tmp_path / "graphics"}


def made(installed, params, job="0101-000000-abcdef"):
    command, env, outputs = installed["build"](params, job)
    folder = HOME / "graphics" / job
    return {"command": command, "env": env, "outputs": outputs, "folder": folder,
            "data": json.loads((folder / "data.json").read_text()), "page": (folder / "index.html").read_text()}


def test_each_template_writes_its_page_its_data_and_the_render_command(installed):
    cases = {
        "title": ({"title": "The Long Night", "subtitle": "A film in three parts", "seconds": 5}, 5.0, (1920, 1080)),
        "lower-third": ({"video": "renders/a.mp4", "name": "Ada Example", "role": "Director", "size": "match"}, 8.0, (1280, 720)),
        "kinetic": ({"text": "every light went out", "bpm": 120, "song": "music/song.wav"}, 1.5 + 1.8, (1920, 1080)),
        "captions": ({"video": "renders/a.mp4", "words": [{"word": "Hello", "start": 0.2, "end": 0.6}]}, 8.0, (1280, 720)),
        "slideshow": ({"pictures": ["uploads/one.png", "uploads/two.png"], "voice": "voice/v1.wav", "size": "vertical"}, 13.3, (1080, 1920)),
        "music-video": ({"clips": ["renders/a.mp4", "renders/b.mp4"], "song": "music/song.wav", "bpm": 120, "every": 2, "seconds": 6}, 6.0, (1920, 1080)),
        "explainer": ({"voice": "voice/v1.wav", "sections": [{"heading": "The idea", "text": "Small words first.", "picture": "uploads/one.png"},
                                                             {"heading": "The result", "text": "Then the rest."}]}, 13.5, (1920, 1080)),
    }
    for template, (params, seconds, (width, height)) in cases.items():
        job = f"0101-00000{len(template)}-{template[:6]}"
        got = made(installed, {"template": template, **params}, job)
        data, page, command = got["data"], got["page"], got["command"]
        assert data["template"] == template and data["width"] == width and data["height"] == height, template
        assert abs(data["duration"] - seconds) < 0.01, (template, data["duration"])
        assert f'data-width="{width}" data-height="{height}" data-fps="24"' in page and f'data-duration="{seconds:.3f}"' in page, template
        assert f"width={width}, height={height}" in page and "<!-- studio:media -->" not in page
        assert (got["folder"] / "data.js").read_text().startswith("window.STUDIO_DATA = {")
        assert (got["folder"] / "gsap.min.js").is_file() and (got["folder"] / "fonts/instrument-serif.woff2").is_file()
        assert (got["folder"] / "fonts/OFL-instrument-serif.txt").is_file()
        # the runner, then HyperFrames with the private Node, the job's folder, the delivery quality, 24 fps, renders/<job>.mp4
        assert command[:4] == [sys.executable, str(Path(graphics.__file__).resolve()), "run", str(got["folder"])]
        hf = command[command.index("--") + 1:]
        assert hf == [str(installed["root"] / "node/bin/node"), str(installed["root"] / "node_modules/hyperframes/dist/cli.js"), "render",
                      str(got["folder"]), "--quality", "delivery", "--fps", "24", "-o", str(HOME / "renders" / f"{job}.mp4")]
        assert got["outputs"] == [str(HOME / "renders" / f"{job}.mp4")]
        assert got["env"]["HYPERFRAMES_NO_TELEMETRY"] == "1" and got["env"]["HYPERFRAMES_BROWSER_PATH"].endswith("chrome")
        for name in [p for k in ("video", "song", "voice") for p in [params.get(k)] if p] + params.get("pictures", []) + params.get("clips", []):
            assert any((got["folder"] / "assets").glob("*" + Path(name).suffix)), (template, name)
    lower = made(installed, {"template": "lower-third", "video": "renders/a.mp4", "name": "Ada", "at": 2, "seconds": 3})
    assert lower["data"]["at"] == 2 and lower["data"]["hold"] == 3 and lower["data"]["side"] == "left"
    assert '<video id="clip" src="assets/video.mp4" playsinline data-has-audio="true"' in lower["page"]
    kinetic = made(installed, {"template": "kinetic", "text": "one two three four", "bpm": 120})
    assert kinetic["data"]["words"] == ["one", "two", "three", "four"] and kinetic["data"]["times"] == [0.0, 0.5, 1.0, 1.5]
    beat = made(installed, {"template": "kinetic", "text": "one two three", "beats": [0.4, 1.1, 1.9]})
    assert beat["data"]["times"] == [0.4, 1.1, 1.9]
    video = made(installed, {"template": "music-video", "clips": ["renders/a.mp4", "renders/b.mp4"], "song": "music/song.wav",
                             "beats": [0, 1, 2, 3, 4, 5], "every": 2, "seconds": 5, "transition": "whip"})
    assert [s["start"] for s in video["data"]["shots"]] == [0, 2, 4] and video["data"]["transition"] == "whip"
    assert 'id="cut1" src="assets/clip2.mp4" playsinline muted data-start="2.000" data-duration="2.000"' in video["page"]
    assert '<audio id="song" src="assets/song.wav" data-start="0.000" data-duration="5.000"' in video["page"]
    explainer = made(installed, {"template": "explainer", "voice": "voice/v1.wav", "sections": [{"heading": "A", "text": "one two three four five six seven eight"}, {"heading": "B", "text": ""}]})
    first, second = explainer["data"]["sections"]
    assert first["start"] == 0 and first["end"] == second["start"] and second["end"] == pytest.approx(13.5)
    assert first["end"] - first["start"] > second["end"] - second["start"]  # the longer section has more of the voice


def test_a_take_id_finds_the_voice_line_and_the_song(installed):
    (HOME / "music/song.take.json").write_text(json.dumps({"id": "song", "path": "music/song.wav"}))
    got = made(installed, {"template": "slideshow", "pictures": ["uploads/one.png"], "voice": "v1", "song": "song"})
    assert (got["folder"] / "assets/voice.wav").is_file() and (got["folder"] / "assets/song.wav").is_file()
    assert 'id="voice" src="assets/voice.wav"' in got["page"] and 'data-volume="0.35"' in got["page"]  # the song under the voice


def test_settings_it_cannot_make_are_refused_in_words(installed):
    def refused(params):
        with pytest.raises(HTTPException) as caught:
            installed["build"](params, "0101-000000-refuse")
        return caught.value.detail

    assert "template is one of title" in refused({"template": "poster"})
    assert refused({"template": "title"}) == "the title is needed"
    assert "at most 120 characters" in refused({"template": "title", "title": "x" * 121})
    assert "between 2 and 30" in refused({"template": "title", "title": "A", "seconds": 90})
    assert "size is one of" in refused({"template": "title", "title": "A", "size": "imax"})
    assert "choose the clip" in refused({"template": "lower-third", "name": "Ada"})
    assert "the name is needed" in refused({"template": "lower-third", "video": "renders/a.mp4"})
    assert "outside the studio folder" in refused({"template": "captions", "video": "/etc/hosts"})
    assert "is not a clip" in refused({"template": "captions", "video": "uploads/one.png"})
    assert "at most 80 words" in refused({"template": "kinetic", "text": " ".join(["a"] * 81)})
    assert "beats is a list" in refused({"template": "kinetic", "text": "a b", "beats": "1,2"})
    assert "between 40 and 240" in refused({"template": "kinetic", "text": "a b", "bpm": 900})
    assert "1 to 40 pictures" in refused({"template": "slideshow", "pictures": []})
    assert "choose the song first" in refused({"template": "music-video", "clips": ["renders/a.mp4"]})
    assert "mixed, flash, whip or cut" in refused({"template": "music-video", "clips": ["renders/a.mp4"], "song": "music/song.wav", "transition": "spin"})
    assert "1 to 12 sections" in refused({"template": "explainer", "sections": []})
    assert "section 1's heading is needed" in refused({"template": "explainer", "sections": [{"text": "words"}]})
    assert "a number" in refused({"template": "title", "title": "A", "seconds": True})


def test_captions_use_the_kept_words_or_transcribe_in_the_job(installed, monkeypatch):
    clip = HOME / "renders/a.mp4"
    graphics.words_path(clip).unlink(missing_ok=True)
    fresh = made(installed, {"template": "captions", "video": "renders/a.mp4"}, "0101-000000-words1")
    head = fresh["command"][:fresh["command"].index("--")]
    assert head[-2:] == ["--words", str(clip.resolve())] and fresh["data"]["words"] == []
    # the cache beside the clip: read by the next job, and by transcribe() without a speech-to-text tool
    words = [{"word": "Hello", "start": 0.1, "end": 0.5}, {"word": "there.", "start": 0.6, "end": 1.0}]
    graphics.words_path(clip).write_text(json.dumps({"clip": "a.mp4", "words": words}))
    kept = made(installed, {"template": "captions", "video": "renders/a.mp4"}, "0101-000000-words2")
    assert "--words" not in kept["command"] and kept["data"]["words"] == words
    monkeypatch.setattr(graphics.subprocess, "run", lambda *a, **k: pytest.fail("the cache is read, nothing runs"))
    assert graphics.transcribe(clip) == words
    graphics.words_path(clip).unlink()


def test_the_runner_fills_the_words_then_says_the_renders_progress(installed, tmp_path, monkeypatch):
    folder = tmp_path / "job"
    folder.mkdir()
    graphics.write_data(folder, {"template": "captions", "words": []})
    render = tmp_path / "render.py"
    render.write_text("print('@hf-progress {\"code\":\"capture\",\"pct\":40}')\nprint('[INFO] [Render:trace] {}')\n"
                      "print('  ███░░  40%  Capturing')\nprint('[initSession:screenshot] dedup')\nprint('@hf-progress {\"pct\":100}')\n"
                      "print('Render complete')\nprint('   Keep editing in the app → https://hyperframes.dev/studio/download')\n")
    monkeypatch.setattr(graphics, "transcribe", lambda clip: [{"word": "Hi", "start": 0.0, "end": 0.3}])
    lines = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: lines.append(" ".join(map(str, a))))
    code = graphics.main(["run", str(folder), "--words", str(tmp_path / "clip.mp4"), "--", sys.executable, str(render)])
    assert code == 0
    assert json.loads((folder / "data.json").read_text())["words"] == [{"word": "Hi", "start": 0.0, "end": 0.3}]
    assert "window.STUDIO_DATA" in (folder / "data.js").read_text()
    assert "[tensorfold] step 40/100" in lines and "[tensorfold] step 100/100" in lines and "Render complete" in lines
    assert not any("Render:trace" in line or "███" in line or "initSession" in line or "hyperframes.dev" in line for line in lines)
    assert server.STEP.search("[tensorfold] step 40/100")  # the queue reads it as the job's progress


def test_the_readiness_report_says_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPHICS_DIR", str(tmp_path / "none"))
    monkeypatch.delenv("HYPERFRAMES_BROWSER_PATH", raising=False)
    old = tmp_path / "bin"
    old.mkdir()
    (old / "node").write_text("#!/bin/sh\necho v20.11.1\n")
    (old / "node").chmod(0o755)
    monkeypatch.setattr(graphics, "DIRS", ())
    monkeypatch.setenv("PATH", str(old))
    said = graphics.ready()
    assert said["ok"] is False and said["node"] is None
    assert said["why"].startswith("HyperFrames needs Node 22 or newer with the latest security fixes (this machine has Node 20.11.1")
    assert "scripts/setup-graphics.sh" in said["why"]
    fake_install(tmp_path / "some")
    monkeypatch.setenv("GRAPHICS_DIR", str(tmp_path / "some"))
    (tmp_path / "some/node_modules/gsap/dist/gsap.min.js").unlink()
    assert "GSAP is not installed" in graphics.ready()["why"]
    (tmp_path / "some/node_modules/gsap/dist/gsap.min.js").write_text("")
    (tmp_path / "some/browser-path").write_text(str(tmp_path / "gone"))
    assert graphics.ready()["why"].startswith("no browser to render with")
    (tmp_path / "some/browser-path").unlink()
    monkeypatch.setattr(graphics.Path, "home", lambda: tmp_path)
    shell = tmp_path / ".cache/hyperframes/chrome/chrome-headless-shell/linux-150/chrome-headless-shell-linux64/chrome-headless-shell"
    shell.parent.mkdir(parents=True)
    shell.write_text("")
    (old / "ffmpeg").write_text("#!/bin/sh\n")
    (old / "ffmpeg").chmod(0o755)
    said = graphics.ready()
    assert said["ok"] is True and said["why"] is None and said["browser"] == str(shell)
    assert said["node"] == str(tmp_path / "some/node/bin/node") and said["hyperframes"] == graphics.HYPERFRAMES
    state = client.get("/api/graphics").json()
    assert [t["id"] for t in state["templates"]] == list(graphics.CATALOG) and state["fps"] == 24 and "ready" in state


def test_a_graphic_is_queued_with_its_title_and_made(installed, monkeypatch, spark_memory):
    def run(job, command, env, label=""):
        Path(command[-1]).write_bytes(b"mp4")
        job["log"] += " ".join(command)
        return True
    monkeypatch.setattr(server, "run", run)
    unloaded = []  # a graphic uses no GPU: Drama 3, Music 3 and Bolt stay loaded (for it: another test's job may still run)
    monkeypatch.setattr(server.voice, "release", lambda why: "graphics" in why and unloaded.append("voice"))
    monkeypatch.setattr(server.music, "release", lambda why: "graphics" in why and unloaded.append("music"))
    monkeypatch.setattr(server.bolt, "admit", lambda job, need: (job["kind"] == "graphics" and unloaded.append("bolt")) or "")
    job = client.post("/api/jobs", json={"kind": "graphics", "params": {"template": "title", "title": "The  Long Night"}}).json()
    assert job["kind"] == "graphics" and job["title"] == "Title: The Long Night", job
    ends = time.time() + 30
    while time.time() < ends:
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job
    assert job["outputs"] == [f"renders/{job['id']}.mp4"] and unloaded == []
    installed["build"]({"template": "title", "title": "A"}, "check")  # the queue's check of the settings writes nothing
    assert not (HOME / "graphics" / "check").exists() and not (HOME / "renders" / "check.mp4").exists()
    bad = client.post("/api/jobs", json={"kind": "graphics", "params": {"template": "title"}})
    assert bad.status_code == 400 and bad.json()["error"] == "the title is needed"
    named = client.post("/api/jobs", json={"kind": "graphics", "title": "Opening card", "params": {"template": "title", "title": "A"}}).json()
    assert named["title"] == "Opening card"
    client.post(f"/api/jobs/{named['id']}/cancel")


def test_without_an_install_the_job_is_refused_with_the_command(monkeypatch, tmp_path):
    monkeypatch.setenv("GRAPHICS_DIR", str(tmp_path / "none"))
    router, build, title = graphics.attach(HOME, ROOT, False, server.inside, server.relative)
    monkeypatch.setattr(server, "graphics_build", build)
    monkeypatch.setattr(graphics, "node_bin", lambda: ("/usr/bin/node", "22.0.0"))
    said = client.post("/api/jobs", json={"kind": "graphics", "params": {"template": "title", "title": "A"}})
    assert said.status_code == 400 and "HyperFrames is not installed" in said.json()["error"] and "setup-graphics.sh" in said.json()["error"]


def test_each_template_is_a_composition_hyperframes_reads():
    for template in graphics.CATALOG:
        page = (graphics.TEMPLATES / template / "index.html").read_text()
        assert 'data-composition-id="main"' in page and 'window.__timelines["main"] = tl' in page, template
        assert "<!-- studio:media -->" in page and '<script src="data.js"></script>' in page and '<script src="gsap.min.js"></script>' in page
        assert "crossorigin" not in page and "Math.random" not in page and "Date.now" not in page and "https://" not in page, template
        assert "@font-face" in page and "fonts/" in page


def test_the_agents_tool_queues_a_graphic():
    import importlib.util

    spec = importlib.util.spec_from_file_location("studio_mcp_graphics", ROOT / "mcp/studio-mcp.py")
    mcp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mcp)
    sent = []
    mcp.call = lambda method, path, body=None, **k: sent.append((method, path, body)) or {"id": "j1", "kind": "graphics", "status": "queued"}
    reply = mcp.answer({"method": "tools/call", "params": {"name": "make_graphics", "arguments": {
        "template": "lower-third", "data": {"video": "renders/a.mp4", "name": "Ada"}, "size": "match", "title": "Name card"}}})
    assert not reply.get("isError"), reply
    assert sent == [("POST", "/api/jobs", {"kind": "graphics", "title": "Name card",
                                           "params": {"video": "renders/a.mp4", "name": "Ada", "template": "lower-third", "size": "match"}})]
    bad = mcp.answer({"method": "tools/call", "params": {"name": "make_graphics", "arguments": {"template": "title", "data": "The End"}}})
    assert bad.get("isError")
    import agents
    assert "make_graphics" in agents.MAKERS and "make_graphics" in agents.STUDIO_TOOLS
    assert any(t["name"] == "make_graphics" for t in mcp.TOOLS)


@pytest.mark.skipif(os.environ.get("STUDIO_GRAPHICS_SMOKE") != "1", reason="a real render: STUDIO_GRAPHICS_SMOKE=1 with scripts/setup-graphics.sh run")
def test_a_real_title_renders_at_320x180(tmp_path):
    state = graphics.ready()
    assert state["ok"], state["why"]
    target = tmp_path / "title.mp4"
    done = subprocess.run([sys.executable, str(ROOT / "app/graphics.py"), "selftest", str(target)], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout[-1000:] + done.stderr[-1000:]
    info = graphics.probe(target)
    assert (info["width"], info["height"]) == (320, 180) and abs(info["seconds"] - 3.0) < 0.2


def test_a_node_without_the_latest_security_fixes_is_never_run():
    """Node's 2026-10-21 security release: 22.23.4, 24.22.1 and 26.12.1 are the oldest of their lines that are used."""
    for version, safe in (((22, 23, 3), False), ((22, 23, 4), True), ((24, 22, 0), False), ((24, 22, 1), True),
                          ((26, 12, 1), True), ((20, 20, 2), False), ((27, 0, 0), True), (None, False)):
        assert graphics.node_safe(version) is safe, version
