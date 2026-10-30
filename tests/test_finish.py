"""Finish (app/finish.py): the final cut and mix through Tesseract. python3 -m pytest tests/test_finish.py.
Tesseract itself is a stand-in here (a script that answers as tsrct does and writes down what it was told), so the
project it is given, the job, the remote path and the Resolve files are tested without it. One real render on this
computer's Tesseract, with clips made by ffmpeg, runs with STUDIO_FINISH_SMOKE=1."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "ImaginationStudio"))
sys.path.insert(0, str(ROOT / "app"))
import finish  # noqa: E402
import server  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
HOME = server.HOME

FAKE = r'''#!{python}
import json, os, shutil, sys
args = sys.argv[1:]
get = lambda k: args[args.index(k) + 1] if k in args else None
with open({log!r}, "a") as log:
    log.write(json.dumps({{"args": args, "cwd": os.getcwd()}}) + "\n")
if args[:1] == ["--version"]:
    print("tsrct 0.4.0 (stand-in)"); sys.exit(0)
command = args[1] if args[0] == "project" else args[0]
if command == "create":
    open(get("--project"), "w").write("{{}}")
elif command == "import-video":
    print(json.dumps({{"assetId": get("--asset-id"), "durationMs": 2041, "width": 640, "height": 360}}))
elif command == "import-asset":
    print(json.dumps({{"assetId": get("--asset-id"), "kind": "audio"}}))
elif command == "import-font":
    print(json.dumps({{"fontFamily": "Stand-in Serif", "fontStyle": "Regular", "assetId": "font-1"}}))
elif command == "checkout":
    json.dump({{"formatVersion": 1, "dimensions": {{"width": 1080, "height": 1920}}, "duration": 3.0,
               "composition": {{"id": "main", "layers": [], "name": "Main"}}}}, open(get("--output"), "w"))
elif command == "commit":
    shutil.copyfile(get("--file"), get("--project") + ".document.json")
elif command == "apply":
    shutil.copyfile(get("--actions"), get("--project") + ".actions.json")
elif command == "export":
    for done in (0, 60, 120):
        print(json.dumps({{"type": "progress", "completed": done, "total": 120, "unit": "frames"}}), file=sys.stderr)
    open(get("--output"), "wb").write(b"mp4")
    print(json.dumps({{"outputPath": get("--output")}}))
if command not in ("import-video", "import-asset", "import-font", "export"):
    print(json.dumps({{"project": get("--project")}}))
'''


def stand_in(folder: Path) -> tuple[Path, Path]:
    log = folder / "tsrct-calls.jsonl"
    tool = folder / "tsrct"
    tool.write_text(FAKE.format(python=sys.executable, log=str(log)))
    tool.chmod(0o755)
    return tool, log


def calls(log: Path) -> list[list[str]]:
    return [json.loads(line)["args"] for line in log.read_text().splitlines()] if log.is_file() else []


def seen(path):
    name = Path(path).name
    if name.startswith("song"):
        return {"seconds": 30.0, "audio": True, "video": False}
    if name.startswith("voice"):
        return {"seconds": 6.0, "audio": True, "video": False}
    return {"rate": (24, 1), "width": 1280, "height": 720, "seconds": 5.0, "audio": not name.startswith("mute"), "video": True}


@pytest.fixture
def studio(tmp_path, monkeypatch):
    """A timeline of three clips (one without sound), a song of the Music tab and a narration, in the studio folder."""

    for folder in ("renders", "music", "voice", "exports"):
        (HOME / folder).mkdir(parents=True, exist_ok=True)
    for name in ("shot_a.mp4", "shot_b.mp4", "mute_c.mp4"):
        (HOME / "renders" / name).write_bytes(b"clip")
    (HOME / "music" / "song_1.wav").write_bytes(b"song")
    (HOME / "music" / "s1.take.json").write_text(json.dumps({"id": "s1", "path": "music/song_1.wav"}))
    (HOME / "voice" / "voice_1.wav").write_bytes(b"voice")
    project = HOME / "projects" / "film1"
    project.mkdir(parents=True, exist_ok=True)
    (project / "project.json").write_text(json.dumps({"id": "film1", "name": "Night Drive", "soundtrack": {"take": "s1"},
        "timeline": [{"path": "renders/shot_a.mp4", "in": 1.0, "out": 3.5, "duration": 5.0},
                     {"path": "renders/shot_b.mp4", "in": 0, "out": 0, "duration": 5.0},
                     {"path": "renders/mute_c.mp4", "in": 2.0, "out": 4.0, "duration": 5.0}]}))
    monkeypatch.setattr(finish, "probe", seen)
    font = tmp_path / "TestSerif.ttf"
    font.write_bytes(b"stand-in font for the stand-in Tesseract")
    monkeypatch.setattr(finish, "font_files", lambda: {"title": str(font), "text": str(font)})
    tool, log = stand_in(tmp_path)
    monkeypatch.setattr(finish, "ready", lambda fresh=False: {"ok": True, "where": "here", "path": str(tool), "version": "0.4.0",
                                                              "converter": {"ok": False}, "linux": False, "ffmpeg": None})
    return {"tool": tool, "log": log}


def refused(call, *args):
    try:
        call(*args)
    except HTTPException as error:
        return error.detail
    return None


def test_a_timeline_becomes_the_project_tesseract_is_told(studio):
    plan = finish.plan_of({"project": "film1", "voice": "voice/voice_1.wav", "title": {"text": "Night Drive", "subtitle": "a short film"}})
    assert [c["start"] for c in plan["clips"]] == [1.0, 0.0, 2.0] and [c["length"] for c in plan["clips"]] == [2.5, 5.0, 2.0]
    assert plan["song"]["path"].endswith("music/song_1.wav")      # the timeline's own soundtrack
    assert plan["canvas"] == [1920, 1080] and plan["mix"] == "dialogue" and (plan["size"], plan["fps"]) == ("1080p", 24)
    durations = [5000, 5000, 5000]
    place = finish.timeline(plan, durations)
    assert place["lead"] == finish.TITLE_MS and [s["at"] for s in place["shots"]] == [3500, 6000, 11000] and place["total"] == 13000
    base = {"formatVersion": 1, "dimensions": {"width": 1080, "height": 1920}, "duration": 3.0, "composition": {"id": "main", "layers": [], "name": "Main"}}
    doc = finish.document(base, plan, durations)
    assert doc["dimensions"] == {"width": 1920, "height": 1080} and doc["duration"] == 13.0
    layers = {layer["id"]: layer for layer in doc["composition"]["layers"]}
    first = layers[finish.SHOT_ID]
    # a clip from source 1.0-3.5 s placed at 3.5-6.0 s: the window on the cut's clock, the source moment by its offset
    assert first["sourceRange"] == {"start": 1000, "duration": 2500} and first["playback"]["inputOffsetMs"] == 1000
    assert first["playback"]["inputRange"] == {"start": 3500, "duration": 2500} and first["volume"] == 1.0
    assert first["source"]["assetId"] == "clip-1" and first["sourceIntrinsicDuration"] == 5000
    assert "volume" not in layers[finish.SHOT_ID + 2]             # a clip without sound gets no audible layer
    song, voice = layers[finish.SONG_ID], layers[finish.VOICE_ID]
    assert song["playback"]["inputRange"] == {"start": 0, "duration": 13000} and abs(song["volume"] - 10 ** (-18 / 20)) < 1e-3
    assert voice["playback"]["inputRange"]["start"] == 3500 and voice["volume"] == 1.0   # narration from the first shot, at full level
    batch = finish.actions(plan, durations, {"title": ("Serif", "Regular"), "text": ("Sans", "Regular")}, [[4.0, 5.5]], [])
    kinds = [a["type"] for a in batch]
    assert kinds.count("createFxTextLayer") == 2 and "groupFxCompositionLayers" in kinds
    group = next(a for a in batch if a["type"] == "groupFxCompositionLayers")
    assert set(group["layerIds"]) == {10, 11, 12, 13}
    fade = next(a for a in batch if a["type"] == "setFxPropertyKeyframes" and a["property"]["layerId"] == group["groupLayerId"])
    assert [k["value"]["value"] for k in fade["keyframes"]] == [0, 100, 100, 0]
    duck = next(a for a in batch if a["type"] == "setFxPropertyAnimator")
    assert duck["property"] == {"layerId": finish.SONG_ID, "propertyType": "volume"}
    assert "[4.000,5.500]" in duck["animator"]["layerTimeJsCode"]


def test_the_song_dips_under_the_dialogue_by_the_mix():
    script = finish.duck_script([[2.0, 4.0]], "balanced", 10.0, 30.0)
    node = shutil.which("node")
    if not node:
        pytest.skip("node evaluates the envelope")
    probe = ("const f = new Function('input', " + json.dumps(script) + "); const at = t => f({time: {seconds: t}});"
             "console.log(JSON.stringify([at(0), at(1), at(3), at(5), at(9.99)]));")
    values = json.loads(subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True).stdout)
    bed = 10 ** (-10 / 20)
    assert values[0] == 0 and abs(values[1] - bed) < 1e-3        # faded in, then at the bed
    assert abs(values[2] - bed * 10 ** (-12 / 20)) < 1e-3          # 12 dB under the dialogue
    assert abs(values[3] - bed) < 1e-3 and values[4] < 0.01        # back up, and faded out at the end
    assert finish.merged([[0, 1], [1.3, 2], [5, 6]]) == [[0, 2], [5, 6]]


def test_the_job_command_and_what_is_refused(studio):
    command, env, outputs = finish.build({"project": "film1", "mix": "music", "size": "720p", "fps": 30}, "j1")
    assert command[0] == sys.executable and command[1].endswith("app/finish.py") and command[2] == "run" and env == {}
    plan = json.loads(Path(command[3]).read_text())
    assert (plan["mix"], plan["size"], plan["fps"], plan["host"]) == ("music", "720p", 30, "")
    assert [Path(p).name for p in outputs] == ["j1.mp4", "j1.tsrct", "j1.fcpxml", "j1.edl"]
    assert all(Path(p).parent == HOME / "exports" for p in outputs)
    finish.build({"project": "film1"}, "check")  # the queue's check of the settings writes nothing
    assert not (HOME / "exports" / ".finish" / "check").exists()
    assert "mix is" in refused(finish.build, {"project": "film1", "mix": "loud"}, "j2")
    assert "fps" in refused(finish.build, {"project": "film1", "fps": 23}, "j2")
    assert "give the clips" in refused(finish.build, {"clips": []}, "j2")
    assert "not a clip" in refused(finish.build, {"clips": [{"path": "music/song_1.wav"}]}, "j2")
    assert "outside" in refused(finish.build, {"clips": [{"path": "/etc/hosts"}]}, "j2")
    assert finish.plan_of({"project": "film1", "song": None})["song"] is None   # no song when the finish says none
    # the server's own dispatch, and its refusal when Tesseract is not ready
    command, _, _ = server.build("finish", {"clips": [{"path": "renders/shot_a.mp4", "in": 0.5}]}, "j3")
    assert command[2] == "run"
    finish.ready, keep = (lambda fresh=False: {"ok": False, "why": "Tesseract is not installed: run `bash scripts/setup-finish.sh`."}), finish.ready
    try:
        answer = client.post("/api/jobs", json={"kind": "finish", "params": {"project": "film1"}})
    finally:
        finish.ready = keep
    assert answer.status_code == 400 and "setup-finish.sh" in answer.json()["error"]


def test_a_finish_job_runs_tesseract_and_writes_every_file(studio):
    response = client.post("/api/jobs", json={"kind": "finish", "title": "Finish: Night Drive",
                                             "params": {"project": "film1", "title": {"text": "Night Drive"}}})
    assert response.status_code == 200, response.text
    job = response.json()
    ends = time.time() + 60
    while time.time() < ends:
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job
    assert sorted(Path(p).suffix for p in job["outputs"]) == [".edl", ".fcpxml", ".mp4", ".tsrct"]
    told = calls(studio["log"])
    names = [c[1] if c[0] == "project" else c[0] for c in told]
    assert names == ["create", "import-video", "import-video", "import-video", "import-asset", "import-font", "checkout", "commit", "apply", "export"]
    export = told[-1]
    assert export[export.index("--resolution") + 1] == "1080p" and export[export.index("--fps") + 1] == "24" and "--json" in export
    assert "[tensorfold] step 120/120" in job["log"]
    work = HOME / "exports" / ".finish" / job["id"]
    document = json.loads((work / "document.json").read_text())
    assert [l["sourceIntrinsicDuration"] for l in document["composition"]["layers"] if l["type"] == "Video"] == [2041] * 3  # as Tesseract imported them
    batch = json.loads((work / "actions.json").read_text())
    assert any(a.get("sourceText", {}).get("fontFamily") == "Stand-in Serif" for a in batch)   # the font as Tesseract named it
    fcpxml = (HOME / f"exports/{job['id']}.fcpxml").read_text()
    assert "shot_a" in fcpxml and 'audioRole="music"' in fcpxml
    edl = (HOME / f"exports/{job['id']}.edl").read_text()
    assert "TITLE: Night Drive" in edl and "shot_b.mp4" in edl
    assert (HOME / f"exports/{job['id']}.mp4").read_bytes() == b"mp4"
    library = [m["path"] for m in client.get("/api/library").json()]
    assert f"exports/{job['id']}.mp4" in library and not any(".finish" in p for p in library)


def test_on_a_spark_tesseract_runs_on_the_mac_over_ssh(tmp_path, monkeypatch):
    finish.seen.clear()
    monkeypatch.setattr(finish, "runs_here", lambda: False)
    monkeypatch.setattr(finish, "remote_host", lambda: "")
    state = finish.ready(fresh=True)
    assert not state["ok"] and "Linux on ARM" in state["why"] and "Settings > Connections" in state["why"]
    monkeypatch.setattr(finish, "remote_host", lambda: "me@example.com:2222")
    monkeypatch.setattr(finish, "probe_remote", lambda host: {"tsrct": "/Users/me/Library/Application Support/Tesseract/bin/tsrct",
        "version": "0.4.0", "conv": "/Users/me/.local/bin/tsrct-conv", "convversion": "0.3.0", "os": "Darwin arm64", "name": "Studio Mac"})
    state = finish.ready(fresh=True)
    assert state["ok"] and state["where"] == "ssh:me@example.com:2222" and state["machine"] == "Studio Mac"
    assert state["converter"]["where"] == "ssh:me@example.com:2222"
    assert finish.ssh_of("me@example.com:2222") == finish.SSH + ["-p", "2222", "me@example.com", "--"]
    # the runner over ssh: a folder of its own there, the inputs copied in, tsrct run inside it, the results copied back
    sent = []

    def fake(argv, **kw):
        sent.append(argv)
        out = "/Users/me/.imagination-studio/finish/j9.abc\n" if "mktemp" in argv[-1] else ""
        return subprocess.CompletedProcess(argv, 0, out, "")
    monkeypatch.setattr(finish.subprocess, "run", fake)
    plan = {"id": "j9", "work": str(tmp_path), "host": "me@example.com:2222", "tsrct": state["path"],
            "ssh": finish.ssh_of("me@example.com:2222"), "scp": finish.scp_of("me@example.com:2222")}
    host = finish.Host(plan, print)
    host.send([str(tmp_path / "a clip.mp4"), str(tmp_path / "song.wav")])
    assert host.media(str(tmp_path / "a clip.mp4")) == "media/000-a-clip.mp4"
    assert sent[1][:2] == ["scp", "-q"] and "-P" in sent[1] and sent[1][-1] == "me@example.com:/Users/me/.imagination-studio/finish/j9.abc/media/000-a-clip.mp4"
    host.tsrct("project", "create", "--project", "cut.tsrct")
    line = sent[-1][-1]
    assert sent[-1][:3] == ["ssh", "-o", "BatchMode=yes"] and "cd /Users/me/.imagination-studio/finish/j9.abc && " in line
    assert "'/Users/me/Library/Application Support/Tesseract/bin/tsrct' project create" in line
    host.clean()
    assert sent[-1][-1] == "rm -rf /Users/me/.imagination-studio/finish/j9.abc"
    finish.seen.clear()


def test_the_cut_for_resolve_names_each_clip_and_its_in_point(tmp_path):
    plan = {"name": "Night Drive", "fps": 24, "canvas": [1920, 1080], "title": None, "song": None,
            "clips": [{"path": str(tmp_path / "a.mp4"), "start": 1.0, "length": 2.0, "seconds": 5.0, "audio": True},
                      {"path": str(tmp_path / "b.mp4"), "start": 0.0, "length": 3.0, "seconds": 3.0, "audio": False}],
            "outputs": {"fcpxml": str(tmp_path / "cut.fcpxml"), "edl": str(tmp_path / "cut.edl")}}
    finish.resolve_files(plan, [5000, 3000])
    xml = (tmp_path / "cut.fcpxml").read_text()
    assert 'start="24/24s"' in xml and 'duration="48/24s"' in xml and 'duration="72/24s"' in xml
    edl = (tmp_path / "cut.edl").read_text()
    assert "001  A        AA/V" in edl and "002  B        V" in edl and "00:00:01:00 00:00:03:00 01:00:00:00 01:00:02:00" in edl


def test_premiere_and_after_effects_through_the_converter(studio, tmp_path, monkeypatch):
    (HOME / "exports" / "f1.tsrct").write_bytes(b"project")
    assert "finish the cut first" in refused(finish.convert, "f2", "premiere")
    assert "premiere or aftereffects" in refused(finish.convert, "f1", "resolve")
    assert "Converter is not installed" in refused(finish.convert, "f1", "premiere")
    conv = tmp_path / "tsrct-conv"
    conv.write_text(f"#!{sys.executable}\n" + r'''
import json, os, sys
args = sys.argv[1:]
out = args[args.index("--output") + 1]
os.makedirs(out + "/media")
open(out + "/project.prproj", "w").write(json.dumps(args))
open(out + "/media/000-clip.mp4", "w").write("clip")
print(json.dumps({"artifactStatus": "published", "diagnostics": [{"kind": "warning", "message": "warning: [PR-EXPORT]: a note"}]}))
''')
    conv.chmod(0o755)
    monkeypatch.setattr(finish, "ready", lambda fresh=False: {"ok": True, "where": "here", "converter": {"ok": True, "path": str(conv), "where": "here"}})
    answer = client.post("/api/finish/f1/convert", json={"to": "premiere"}).json()
    assert answer["path"] == "exports/f1-premiere.zip" and answer["notes"] == ["[PR-EXPORT]: a note"]
    with zipfile.ZipFile(HOME / answer["path"]) as bundle:
        names = bundle.namelist()
        told = json.loads(bundle.read("f1-premiere/project.prproj"))
    assert "f1-premiere/media/000-clip.mp4" in names and "f1-premiere/README.txt" in names
    assert told[told.index("--to") + 1] == "premiere" and told[told.index("--fps") + 1] == "24"
    assert client.post("/api/finish/f1/convert", json={"to": "premiere"}).json().get("kept")   # made once, then kept


def test_readiness_route_says_what_it_needs(monkeypatch):
    monkeypatch.setattr(finish, "ready", lambda fresh=False: {"ok": False, "where": "here", "why": "Tesseract is not installed: run `bash scripts/setup-finish.sh`."})
    state = client.get("/api/finish").json()
    assert not state["ok"] and "setup-finish.sh" in state["why"] and state["mixes"] == ["dialogue", "balanced", "music"]
    assert state["sizes"] == ["720p", "1080p", "4k"] and set(state["fonts"]) == {"title", "text"}


def test_the_converter_and_the_probe_over_ssh_answer_only_this_computer(monkeypatch):
    import agents
    asked = []

    def local(request, mutation=False):
        asked.append(mutation)
        raise HTTPException(403, "Set up brains and agents on this computer or through its Tailscale address.")

    monkeypatch.setattr(finish, "ready", lambda fresh=False: {"ok": True, "where": "here", "converter": {"ok": False}})
    monkeypatch.setattr(agents, "local", local)
    assert client.post("/api/finish/f1/convert", json={"to": "premiere"}).status_code == 403 and asked == [True]
    monkeypatch.setattr(finish, "remote_host", lambda: "")
    assert client.get("/api/finish").status_code == 200 and asked == [True]  # nothing runs over ssh: anyone may read it
    monkeypatch.setattr(finish, "remote_host", lambda: "me@example.com")
    assert client.get("/api/finish").status_code == 403 and asked == [True, False]


def test_heard_finds_sound_between_silences(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg measures the dialogue")
    sound = tmp_path / "talk.wav"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-f", "lavfi", "-i", "sine=f=300:d=1.5",
                    "-filter_complex", "[0]atrim=0:1[s];[1]volume=0.5[t];[0]atrim=0:1.5[e];[s][t][e]concat=n=3:v=0:a=1", "-t", "4", str(sound)], check=True)
    spans = finish.heard(str(sound), 0.0, 4.0, ffmpeg)
    assert len(spans) == 1 and abs(spans[0][0] - 1.0) < 0.1 and abs(spans[0][1] - 2.5) < 0.1


@pytest.mark.skipif(os.environ.get("STUDIO_FINISH_SMOKE") != "1", reason="a real Tesseract render: STUDIO_FINISH_SMOKE=1")
def test_a_real_finish_on_this_computer(tmp_path, monkeypatch):
    ffmpeg = shutil.which("ffmpeg")
    finish.seen.clear()
    state = finish.ready(fresh=True)
    if not ffmpeg or not state["ok"] or state["where"] != "here":
        pytest.skip("needs ffmpeg and Tesseract on this computer: " + state.get("why", ""))
    for folder in ("renders", "music", "exports"):
        (HOME / folder).mkdir(parents=True, exist_ok=True)
    make = lambda *args: subprocess.run([ffmpeg, "-v", "error", "-y", *args], check=True)
    tags = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    make("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=3", "-f", "lavfi", "-i", "sine=f=440:d=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", *tags, "-c:a", "aac", "-shortest", str(HOME / "renders/smoke_a.mp4"))
    make("-f", "lavfi", "-i", "smptehdbars=size=640x360:rate=24:duration=3", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", *tags, "-c:a", "aac", "-t", "3", str(HOME / "renders/smoke_b.mp4"))
    make("-f", "lavfi", "-i", "sine=f=220:d=12", "-ar", "48000", str(HOME / "music/smoke_song.wav"))
    params = {"clips": [{"path": "renders/smoke_a.mp4", "in": 0.5, "out": 2.5}, {"path": "renders/smoke_b.mp4"}],
              "song": "music/smoke_song.wav", "title": {"text": "Smoke Test", "subtitle": "a finish"}, "mix": "balanced",
              "size": "720p", "fps": 24}
    command, env, outputs = finish.build(params, "smoke1")
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=600)
    print(done.stdout[-3000:], done.stderr[-2000:])
    assert done.returncode == 0, done.stdout[-1500:] + done.stderr[-1500:]
    mp4 = Path(outputs[0])
    seen_ = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                                       "-of", "json", str(mp4)], capture_output=True, text=True).stdout)
    kinds = [s["codec_type"] for s in seen_["streams"]]
    assert "video" in kinds and "audio" in kinds
    assert abs(float(seen_["format"]["duration"]) - (finish.TITLE_MS / 1000 + 2.0 + 3.0)) < 0.3
    assert next(s for s in seen_["streams"] if s["codec_type"] == "video")["height"] == 720
    assert Path(outputs[1]).stat().st_size > 10000 and "smoke_a" in Path(outputs[2]).read_text()
    still = tmp_path / "title.png"
    make("-ss", "1.8", "-i", str(mp4), "-frames:v", "1", str(still))
    assert still.stat().st_size > 1000
    if (state.get("converter") or {}).get("ok"):
        made = finish.convert("smoke1", "aftereffects")
        with zipfile.ZipFile(HOME / made["path"]) as bundle:
            assert "smoke1-aftereffects/project.aep" in bundle.namelist()
        made = finish.convert("smoke1", "premiere")
        with zipfile.ZipFile(HOME / made["path"]) as bundle:
            assert "smoke1-premiere/project.prproj" in bundle.namelist()
    finish.seen.clear()
