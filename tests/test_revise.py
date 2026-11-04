"""Restyle, Extend and Fix (app/revise.py): the jobs each queues, the 15 second cut, Extend's join, the three steps
of Restyle and Fix (first frame, motion analysis, the clip from the depth layer), and their panels in the Library (app/static/revise.js). The studio around them is stood in for: nothing is rendered.
Run: python3 -m pytest tests/test_revise.py. Needs ffmpeg for the clips (the tests that use one are skipped without it)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import revise  # noqa: E402
from fastapi import HTTPException  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LEAD = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
PRESETS = {"small": ("", (864, 480), {}), "native": ("", (1344, 768), {}), "p720": ("", (1280, 720), {}),
           "sharp": ("", (1376, 768), {}), "draft": ("", (1344, 768), {"X2": "1"})}
FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed")


def clip(path: Path, seconds: float, size="864x480", sound=True) -> Path:
    """A test clip: colour bars at 24 frames a second, with a tone under it or none."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tone = ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"] if sound else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=24:duration={seconds}", *tone,
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", *(["-c:a", "aac", "-shortest"] if sound else []),
                    str(path)], check=True)
    return path


def frames(path: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_frames", "-of", "json", str(path)], capture_output=True, text=True).stdout
    return int(json.loads(out)["streams"][0]["nb_read_frames"])


def server_module():
    os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
    import server
    return server


def studio(pace="", motion=True, able=None):
    home = Path(tempfile.mkdtemp()).resolve() / "home"
    for name in ("renders", "uploads", "sparks"):
        (home / name).mkdir(parents=True)
    queued, built = [], []
    # Motion Previs stood in for: its queue (motion.API) and its hand-off (motion.motion_reference)
    analyses, packs, submitted, handed = {}, {}, [], []

    def submit(body):
        submitted.append(body)
        one = f"m{len(submitted)}"
        analyses[one] = {"id": one, "status": "queued"}
        return dict(analyses[one])

    def get_pack(pack):
        if pack not in packs:
            raise HTTPException(404, "no such pack")
        return packs[pack]

    def motion_reference(pack, layer, mode, seconds, width, height, timing="stretch"):
        handed.append({"pack": pack, "layer": layer, "mode": mode, "seconds": seconds, "width": width, "height": height, "timing": timing})
        return {"clip": f"motion/{pack}/handoff/{layer}-{width}x{height}-{seconds:g}s-{timing}-cover.mp4", "sound": ns.handoff_sound,
                "camera": "The camera pushes in slowly.", "camera_reliable": True}

    api = {"submit": submit, "job": lambda one: analyses[one], "pack": get_pack,
           "able": lambda: able if able is not None else {"ffmpeg": True, "ai_depth": True, "pose": True, "camera": True}}
    real = server_module()

    def inside(path):
        resolved = (home / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        if home not in (resolved, *resolved.parents):
            raise HTTPException(400, "path is outside the studio folder")
        return resolved

    def queue_job(body):
        job = {"id": f"j{len(queued) + 1}", "kind": body["kind"], "title": body.get("title"), "params": body["params"],
               "status": "queued", "outputs": [], "log": ""}
        queued.append(body)
        ns.jobs[job["id"]] = job
        return dict(job)

    def frame_at(video, image, at):
        if FFMPEG:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(at), "-i", str(video), "-frames:v", "1", str(image)], check=True)
        else:
            image.write_bytes(b"png")

    ns = SimpleNamespace(HOME=home, inside=inside, relative=lambda p: str(Path(p).resolve().relative_to(home)), jobs={},
                         queue_job=queue_job, build=lambda kind, params, job_id: params["prompt"] and built.append((kind, params)) or ([], {}, []),  # the server's build needs a prompt,
                         frame_at=frame_at, environment=lambda extra: {**os.environ, **extra}, PRESETS=PRESETS,
                         CUSTOM_SIDES=(256, 1536), CUSTOM_PIXELS=1344 * 768, LONGEST=15, FPS=24, LEAD=LEAD,
                         VIDEO_SUFFIXES=(".mp4", ".mov"), hub=SimpleNamespace(takes=lambda: {}),
                         pace=SimpleNamespace(measure=lambda path: {"motion": "slow"}, choose=lambda asked, measured: pace),
                         queued=queued, built=built, SPARK=False, compose_references=real.compose_references,
                         motion_template=real.motion_template,
                         motion=SimpleNamespace(API=api, motion_reference=motion_reference) if motion else SimpleNamespace(),
                         analyses=analyses, packs=packs, submitted=submitted, handed=handed, handoff_sound=True)
    router, queue, then = revise.attach(ns)
    ns.router, ns.queue, ns.then = router, queue, then
    ns.chain = next(r.endpoint for r in router.routes if r.path == "/api/revise/{chain_id}")
    return ns


def refused(call, *args) -> str | None:
    try:
        call(*args)
    except HTTPException as error:
        return error.detail
    return None


def test_a_new_clip_keeps_the_size_of_the_one_it_comes_from():
    size = lambda w, h: revise.size_of(w, h, PRESETS, (256, 1536), 1344 * 768)  # noqa: E731
    assert size(864, 480) == {"preset": "small"} and size(1280, 720) == {"preset": "p720"} and size(1376, 768) == {"preset": "sharp"}
    big = size(1920, 1080)
    assert big["preset"] == "custom" and big["width"] % 32 == 0 and big["height"] % 32 == 0
    assert big["width"] * big["height"] <= 1344 * 768 and abs(big["width"] / big["height"] - 16 / 9) < 0.06
    tall = size(1080, 1920)
    assert tall["height"] > tall["width"] and tall["width"] * tall["height"] <= 1344 * 768
    assert size(640, 360)["preset"] == "custom" and size(640, 360)["width"] == 640  # small enough: its own size


def until(test, seconds=5.0):
    stop = time.time() + seconds
    while not test():
        if time.time() > stop:
            raise AssertionError("the chain did not move on")
        time.sleep(0.01)


def a_clip(s, monkeypatch, name="renders/a.mp4", seconds=3, sound=True):
    if FFMPEG:
        clip(s.HOME / name, seconds, sound=sound)
    else:
        (s.HOME / name).write_bytes(b"x")
        monkeypatch.setattr(revise, "probe", lambda path: {"width": 864, "height": 480, "duration": float(seconds), "sound": sound})


def finish(s, job_id, output):
    """A queued step's job finished, as server.perform would: outputs written, then ``then``."""

    (s.HOME / output).parent.mkdir(parents=True, exist_ok=True)
    (s.HOME / output).write_bytes(b"png")
    record = s.jobs[job_id]
    record.update(status="running", outputs=[output])
    s.then(record)
    record["status"] = "done"
    return record


def test_restyle_redraws_the_first_frame_reads_the_motion_and_renders_from_the_depth_layer(monkeypatch):
    monkeypatch.setattr(revise, "WATCH", 0.01)
    s = studio()
    a_clip(s, monkeypatch)
    job = s.queue({"kind": "restyle", "params": {"video": "renders/a.mp4", "prompt": "a watercolour painting", "seed": 7}})
    first = s.queued[-1]
    assert first["kind"] == "edit" and first["title"] == "Restyle: a.mp4 (1/3: the first frame)" and job["revise"]["kind"] == "restyle"
    p = first["params"]
    assert p["prompt"] == "Redraw this frame as: a watercolour painting; keep the composition"
    assert p["image"].startswith("uploads/a_first_") and p["size"] == "source" and p["image_model"] == "qwen" and p["seed"] == 7
    assert p["then"] == job["revise"]["id"]
    assert s.chain(job["revise"]["id"])["of"] == 3
    # 2/3: the analysis, queued on Motion Previs when the frame is done
    finish(s, job["id"], "scouts/j1/edit.png")
    asked = dict(s.submitted[0])
    assert abs(asked.pop("end") - 3) < 0.1 and len(s.submitted) == 1
    assert asked == {"source": "renders/a.mp4", "start": 0, "mode": "actor-motion", "layers": ["depth", "ai-depth", "pose", "camera"],
                     "title": "Restyle: a.mp4"}
    chain = s.chain(job["revise"]["id"])
    assert chain["steps"][1] == {"id": "m1", "kind": "analysis", "title": "Restyle: a.mp4 (2/3: the motion)", "status": "queued",
                                 "pack": None, "stage": None, "error": None} and chain["status"] == "running" and len(s.queued) == 1
    # nobody in the clip (the pose pass found no one): the main object's motion is kept
    s.packs["p1"] = {"layers": ["reference", "depth", "ai-depth", "openpose"], "pose": {"totalFrames": 72, "detectedFrames": 0}}
    s.analyses["m1"].update(status="done", pack="p1")
    until(lambda: len(s.queued) == 2)
    last = s.queued[-1]
    assert last["kind"] == "video" and last["title"] == "Restyle: a.mp4 (3/3: the clip)"
    q = last["params"]
    assert q["references"] == ["scouts/j1/edit.png"] and q["reference_videos"] == ["motion/p1/handoff/ai-depth-864x480-3s-trim-cover.mp4"]
    assert q["motion"] == {"pack": "p1", "layer": "ai-depth", "mode": "object-motion", "measured": None, "free": False}
    assert q["reference_video_sound"] is True and q["pace"] == "auto" and q["preset"] == "small" and q["seed"] == 7 and "then" not in q
    assert "image" not in q and "first_frame_from" not in q
    assert s.handed[-1] == {"pack": "p1", "layer": "ai-depth", "mode": "object-motion", "seconds": q["seconds"], "width": 864, "height": 480, "timing": "trim"}
    prompt = q["prompt"]
    assert "<Video 1> is the structural template of the target video" in prompt and "Legend of <Video 1>: a depth map" in prompt
    assert "<Picture 1> is the first-frame anchor" in prompt and "The look of the clip is that of <Picture 1>: a watercolour painting." in prompt
    assert "<Audio 1> is the synchronized audio track of <Video 1>." in prompt and "The camera pushes in slowly." in prompt
    assert "photorealistic" not in prompt and "] A video that follows the structure of <Video 1> frame by frame" in prompt
    assert "The picture keeps the look of <Picture 1> throughout: no skeleton lines" in prompt
    s.jobs["j2"]["status"] = "done"
    assert s.chain(job["revise"]["id"])["status"] == "done"
    # the same clip again: its pack is used again, the clip follows the frame at once
    job = s.queue({"kind": "restyle", "params": {"video": "renders/a.mp4", "prompt": "claymation", "keep_sound": False, "mode": "camera-only"}})
    finish(s, job["id"], "scouts/j3/edit.png")
    assert len(s.submitted) == 1 and len(s.queued) == 4
    chain = s.chain(job["revise"]["id"])
    assert chain["steps"][1]["cached"] is True and chain["steps"][1]["status"] == "done"
    q = s.queued[-1]["params"]
    assert q["reference_video_sound"] is False and q["motion"]["mode"] == "camera-only" and "<Audio 1>" not in q["prompt"]
    assert "look like" in refused(s.queue, {"kind": "restyle", "params": {"video": "renders/a.mp4", "prompt": " "}})
    assert "between 2 and 15" in refused(s.queue, {"kind": "restyle", "params": {"video": "renders/a.mp4", "prompt": "x", "seconds": 20}})
    assert "mode is one of" in refused(s.queue, {"kind": "restyle", "params": {"video": "renders/a.mp4", "prompt": "x", "mode": "all"}})
    assert "choose a clip" in refused(s.queue, {"kind": "restyle", "params": {"prompt": "x"}})
    assert "outside" in refused(s.queue, {"kind": "restyle", "params": {"video": "/etc/hosts", "prompt": "x"}})


def test_restyle_and_fix_say_when_motion_previs_or_ref2va_is_missing(monkeypatch):
    s = studio(motion=False)
    a_clip(s, monkeypatch)
    assert "Motion Previs" in refused(s.queue, {"kind": "restyle", "params": {"video": "renders/a.mp4", "prompt": "anime"}})
    s = studio(able={"ffmpeg": False})
    a_clip(s, monkeypatch)
    assert "oneshot-setup.sh --motion" in refused(s.queue, {"kind": "fix", "params": {"video": "renders/a.mp4", "instruction": "x"}})
    s = studio()
    a_clip(s, monkeypatch)

    def no_ref2va(kind, params, job_id):
        raise HTTPException(400, "MiniMax H3 Ref2VA is not installed here (oneshot-setup.sh --ref2va adds it)")

    s.build = no_ref2va
    assert "Ref2VA is not installed" in refused(s.queue, {"kind": "fix", "params": {"video": "renders/a.mp4", "instruction": "x"}})
    assert s.queued == []


@needs_ffmpeg
def test_the_clips_own_sound_is_laid_under_when_the_depth_layer_cannot_carry_it(monkeypatch):
    monkeypatch.setattr(revise, "WATCH", 0.01)
    s = studio()
    s.handoff_sound = False
    clip(s.HOME / "renders/a.mp4", 3)
    job = s.queue({"kind": "restyle", "params": {"video": "renders/a.mp4", "prompt": "anime"}})
    s.packs["p1"] = {"layers": ["depth"]}
    finish(s, job["id"], "scouts/j1/edit.png")
    s.analyses["m1"].update(status="done", pack="p1")
    until(lambda: len(s.queued) == 2)
    q = s.queued[-1]["params"]
    assert q["reference_video_sound"] is False and q["motion"]["layer"] == "depth" and q["motion"]["mode"] == "actor-motion"
    assert q["then"] == job["revise"]["id"] and "<Audio 1>" not in q["prompt"]
    clip(s.HOME / "renders/j2.mp4", 3, sound=False)
    record = s.jobs["j2"]
    record.update(status="running", outputs=["renders/j2.mp4"])
    s.then(record)
    assert record["outputs"] == ["renders/j2-sound.mp4", "renders/j2.mp4"] and revise.probe(s.HOME / "renders/j2-sound.mp4")["sound"]


def test_a_failed_analysis_is_said_on_the_chain(monkeypatch):
    monkeypatch.setattr(revise, "WATCH", 0.01)
    s = studio()
    a_clip(s, monkeypatch)
    job = s.queue({"kind": "fix", "params": {"video": "renders/a.mp4", "instruction": "make it snow"}})
    finish(s, job["id"], "scouts/j1/edit.png")
    s.analyses["m1"].update(status="failed", error="no video stream")
    until(lambda: s.chain(job["revise"]["id"])["error"])
    chain = s.chain(job["revise"]["id"])
    assert chain["status"] == "failed" and "no video stream" in chain["error"] and len(s.queued) == 1


@needs_ffmpeg
def test_a_clip_over_15_seconds_is_cut_to_its_first_15_with_a_note():
    s = studio()
    clip(s.HOME / "renders/long.mp4", 16.5, size="320x180")
    job = s.queue({"kind": "restyle", "params": {"video": "renders/long.mp4", "prompt": "film noir"}})
    ref = s.built[-1][1]["reference_videos"][0]  # what the last step was checked with
    assert ref.startswith("uploads/long_first15s_") and ref.endswith(".mp4")
    assert abs(revise.probe(s.HOME / ref)["duration"] - 15) < 0.15 and s.built[-1][1]["seconds"] == 15
    assert any("16.5 s long" in n and "first 15 s" in n for n in job["revise"]["notes"])


@needs_ffmpeg
def test_a_silent_clip_cannot_keep_its_sound():
    s = studio()
    clip(s.HOME / "renders/quiet.mp4", 3, sound=False)
    job = s.queue({"kind": "restyle", "params": {"video": "renders/quiet.mp4", "prompt": "anime"}})
    assert s.built[-1][1]["reference_video_sound"] is False and "no sound" in job["revise"]["notes"][0]


@needs_ffmpeg
def test_extend_opens_on_the_last_frame_with_the_clips_own_prompt():
    s = studio()
    clip(s.HOME / "renders/a.mp4", 2)
    s.jobs["old"] = {"id": "old", "kind": "video", "outputs": ["renders/a.mp4"], "params": {"prompt": f"{LEAD}\n\nA fox runs through snow."}}
    job = s.queue({"kind": "extend", "params": {"video": "renders/a.mp4", "seconds": 8, "seed": 3}})
    p = s.queued[-1]["params"]
    assert s.queued[-1]["kind"] == "video" and p["first_frame_from"] == "renders/a.mp4" and p["seconds"] == 8 and p["preset"] == "small"
    assert p["prompt"] == f"{LEAD}\n\nA fox runs through snow." and p["then"] == job["revise"]["id"]
    s.jobs.clear()
    s.queue({"kind": "extend", "params": {"video": "renders/a.mp4", "seconds": 20, "join": False}})
    body = s.queued[-1]
    assert body["kind"] == "long" and body["params"]["total_seconds"] == 20 and body["params"]["prompt"] == "continue the shot"
    assert "then" not in body["params"]  # not joined: nothing follows
    s.queue({"kind": "extend", "params": {"video": "renders/a.mp4", "prompt": "the fox stops and looks back"}})
    assert s.queued[-1]["params"]["prompt"].endswith("the fox stops and looks back") and s.queued[-1]["params"]["seconds"] == 5
    assert "5 to 30" in refused(s.queue, {"kind": "extend", "params": {"video": "renders/a.mp4", "seconds": 40}})


@needs_ffmpeg
def test_extend_joins_the_clip_and_its_extension_without_the_repeated_frame():
    s = studio()
    clip(s.HOME / "renders/a.mp4", 2)                     # 48 frames
    job = s.queue({"kind": "extend", "params": {"video": "renders/a.mp4", "seconds": 5}})
    clip(s.HOME / "renders/j1.mp4", 1, sound=False)        # 24 frames, the first a copy of the clip's last
    record = s.jobs[job["id"]]
    record.update(status="running", outputs=["renders/j1.mp4"])
    s.then(record)
    assert record["outputs"] == ["renders/j1-extended.mp4", "renders/j1.mp4"] and record["progress"] is None
    joined = s.HOME / "renders/j1-extended.mp4"
    assert frames(joined) == 48 + 23 and revise.probe(joined)["sound"] and revise.probe(joined)["width"] == 864
    record["status"] = "done"
    chain = s.chain(job["revise"]["id"])
    assert chain["status"] == "done" and chain["outputs"][0] == "renders/j1-extended.mp4" and chain["error"] is None


def test_fix_redraws_the_first_frame_reads_the_motion_then_makes_the_clip_from_its_depth(monkeypatch):
    monkeypatch.setattr(revise, "WATCH", 0.01)
    s = studio()
    a_clip(s, monkeypatch)
    job = s.queue({"kind": "fix", "params": {"video": "renders/a.mp4", "instruction": "change the sky to a stormy sunset",
                                             "image_model": "flux3k-fast", "seed": 5}})
    first = s.queued[-1]
    assert first["kind"] == "edit" and first["title"] == "Fix: a.mp4 (1/3: the first frame)"
    p = first["params"]
    assert p["image"].startswith("uploads/a_first_") and (s.HOME / p["image"]).is_file() and p["size"] == "source"
    assert p["prompt"] == "change the sky to a stormy sunset" and p["image_model"] == "flux3k-fast" and p["then"] == job["revise"]["id"]
    # the last step was checked when the first was queued, with the frame and the clip standing in
    kind, checked = s.built[-1]
    assert kind == "video" and checked["references"] == [p["image"]] and checked["reference_videos"] == ["renders/a.mp4"]
    chain = s.chain(job["revise"]["id"])
    assert chain["of"] == 3 and len(chain["steps"]) == 1 and chain["status"] == "running"
    finish(s, job["id"], "scouts/j1/edit.png")
    assert len(s.submitted) == 1 and s.submitted[0]["source"] == "renders/a.mp4" and s.submitted[0]["title"] == "Fix: a.mp4"
    s.analyses["m1"]["status"] = "running"
    assert s.chain(job["revise"]["id"])["steps"][1]["status"] == "running" and len(s.queued) == 1
    s.packs["p1"] = {"layers": ["reference", "depth", "ai-depth", "openpose"], "pose": {"totalFrames": 72, "detectedFrames": 70}}
    s.analyses["m1"].update(status="done", pack="p1")
    until(lambda: len(s.queued) == 2)
    second = s.queued[-1]
    assert second["kind"] == "video" and second["title"] == "Fix: a.mp4 (3/3: the clip)"
    q = second["params"]
    assert q["references"] == ["scouts/j1/edit.png"] and q["reference_videos"] == ["motion/p1/handoff/ai-depth-864x480-3s-trim-cover.mp4"]
    assert q["motion"] == {"pack": "p1", "layer": "ai-depth", "mode": "actor-motion", "measured": None, "free": False}
    assert q["reference_video_sound"] is True and q["preset"] == "small" and q["seed"] == 5 and "then" not in q
    fields = {"scene": "The look of the clip is that of <Picture 1>: change the sky to a stormy sunset.", "action": "", "camera": "",
              "dialogue": [], "soundscape": "", "music": "N/A", "subjects": [""], "video_subjects": [""], "audio_notes": [], "video_sound": True,
              "motion": {"layer": "ai-depth", "mode": "actor-motion", "camera": "The camera pushes in slowly.", "people": 1, "pace": "auto",
                         "measured": None, "free": False}}
    made = s.motion_template(fields["motion"], 1)
    fields["lead"] = made["lead"].replace("A photorealistic video", "A video")
    fields["avoid"] = made["avoid"].replace("The picture is photorealistic throughout, continuing the look of <Picture 1>",
                                            "The picture keeps the look of <Picture 1> throughout")
    assert q["prompt"] == s.compose_references(fields, 1)
    assert "the positions, silhouettes and body movement of its figure and the camera path" in q["prompt"]
    assert json.loads((s.HOME / "motion/revise.json").read_text()) and list(json.loads((s.HOME / "motion/revise.json").read_text()).values()) == ["p1"]
    chain = s.chain(job["revise"]["id"])
    assert [one["id"] for one in chain["steps"]] == ["j1", "m1", "j2"] and chain["status"] == "running"
    s.jobs["j2"]["status"] = "done"
    assert s.chain(job["revise"]["id"])["status"] == "done"
    s.then(s.jobs["j2"])  # a finished last step has nothing after it
    assert len(s.queued) == 2
    assert "say what to change" in refused(s.queue, {"kind": "fix", "params": {"video": "renders/a.mp4"}})
    assert "image_model" in refused(s.queue, {"kind": "fix", "params": {"video": "renders/a.mp4", "instruction": "x", "image_model": "paint"}})


def test_then_leaves_other_jobs_alone_and_says_what_failed():
    s = studio()
    stray = {"id": "x", "kind": "video", "params": {"then": "nope"}, "outputs": ["renders/x.mp4"], "log": ""}
    s.then(stray)
    assert stray["log"] == "" and s.queued == []
    assert "unknown action" in refused(s.queue, {"kind": "paint", "params": {}})


def test_the_studio_takes_the_actions_on_its_jobs_route_and_runs_the_next_step():
    """server.py's hooks: POST /api/jobs hands Restyle, Extend and Fix to revise.py, and a finished job whose params
    name a chain is passed to it."""

    os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
    import server
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    said = client.post("/api/jobs", json={"kind": "restyle", "params": {"prompt": "a watercolour painting"}})
    assert said.status_code == 400 and "choose a clip" in said.json()["error"]
    assert client.post("/api/revise", json={"kind": "fix", "params": {"video": "renders/none.mp4", "instruction": "x"}}).status_code == 400
    assert client.get("/api/revise").json()["kinds"][0] == {"kind": "restyle", "title": "Restyle"}
    assert client.get("/api/revise/nope").status_code == 404
    assert set(server.motion.API) == {"submit", "job", "pack", "able"}  # Motion Previs' queue, for Restyle and Fix
    assert server.compose_references and server.motion_template and server.motion.motion_reference
    source = (ROOT / "app/server.py").read_text()
    assert 'if params.get("then") and job["status"] != "cancelled":' in source and "revise_then(job)" in source


HARNESS = r"""
const vm = require("vm"), fs = require("fs");
globalThis.tab = "Library"; globalThis.render = () => {}; globalThis.toasts = []; globalThis.toast = text => toasts.push(String(text));
globalThis.calls = []; globalThis.answers = {}; globalThis.history = { replaceState() {} }; globalThis.document = { querySelector: () => null };
globalThis.api = async (path, body) => { calls.push([path, body]); return answers[path] || {}; };
globalThis.jobs = []; globalThis.poll = async () => {}; globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};
globalThis.S = { image_models: [{ id: "qwen", label: "Qwen 2.1 Turbo" }, { id: "flux3k-fast", label: "FLUX3-K Fast" }, { id: "moss-image", label: "MOSS-Image" }] };
const text = node => node == null || node === false ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join(" ") : [node.attrs && node.attrs.placeholder, node.attrs && node.attrs.title, text(node.kids)].filter(Boolean).join(" ");
globalThis.text = text;
globalThis.el = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids: kids.flat(), addEventListener() {} });
globalThis.put = (node, ...kids) => node.kids.push(...kids); globalThis.field = (label, node) => el("div", {}, label, node);
globalThis.bind = (obj, key, node) => { node.attrs.value = obj[key] ?? ""; return node; };
globalThis.select = (obj, key, options) => el("select", {}, Object.entries(options).map(([v, t]) => el("option", { value: v }, t)));
globalThis.choice = (obj, key, options) => el("div", { class: "choice" }, Object.entries(options).map(([v, [t, s]]) => el("button", {}, t, s)));
globalThis.media = path => el("video", { src: "/files/" + path }); globalThis.file = path => "/files/" + path;
globalThis.jobCard = job => el("div", {}, "job " + job.id);
const find = (node, test) => !node || typeof node !== "object" ? null : Array.isArray(node) ? node.map(n => find(n, test)).find(Boolean) || null : test(node) ? node : find(node.kids, test);
globalThis.find = find;
for (const f of process.argv.slice(2, -1)) vm.runInThisContext(fs.readFileSync(f, "utf8"));
vm.runInThisContext(`(async () => { const out = {}; ${fs.readFileSync(process.argv[process.argv.length - 1], "utf8")}; console.log(JSON.stringify(out)); })().catch(e => { console.error(e); process.exit(1); });`);
"""


def page(code: str) -> dict:
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "harness.js").write_text(HARNESS)
        (Path(scratch) / "test.js").write_text(code)
        done = subprocess.run(["node", str(Path(scratch) / "harness.js"), str(ROOT / "app/static/actions.js"),
                               str(ROOT / "app/static/revise.js"), str(Path(scratch) / "test.js")], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_a_clip_gets_restyle_extend_and_fix_and_each_opens_its_panel():
    got = page("""
      ACT.state = { upscale: { ready: true }, remove: { ready: true }, turnaround: { ready: true } };
      out.video = actButtons({ kind: "video", path: "renders/a.mp4" }).map(b => text(b.kids));
      out.image = actButtons({ kind: "image", path: "uploads/a.png" }).map(b => text(b.kids));
      const hot = node => find(node, n => n.attrs && n.attrs.class === "hot");
      actOpen("restyle", "renders/a.mp4"); let panel = actPanel(); out.restyle = text(panel); out.id = panel.attrs.id;
      hot(panel).attrs.onclick(); out.refused = toasts.slice(-1)[0];
      REV.restyle.prompt = "a watercolour painting"; answers["/api/revise"] = { id: "j1", title: "Restyle: a.mp4", revise: { id: "c1", status: "running", steps: [{ id: "j1" }], of: 1, notes: ["The clip is 16.5 s long; first 15 s"] } };
      await hot(actPanel()).attrs.onclick(); out.sent = calls.slice(-1)[0]; out.note = toasts.slice(-1)[0];
      actOpen("extend", "renders/a.mp4"); panel = actPanel(); out.extend = text(panel);
      await hot(panel).attrs.onclick(); out.extend_sent = calls.slice(-1)[0];
      actOpen("fix", "renders/a.mp4"); panel = actPanel(); out.fix = text(panel);
      out.models = find(panel, n => n.tag === "select").kids.map(o => o.attrs.value);
      REV.fix.instruction = "change the sky to a stormy sunset";
      answers["/api/revise"] = { id: "j2", title: "Fix: a.mp4", revise: { id: "c2", status: "running", steps: [{ id: "j2" }], of: 3, notes: [] } };
      await hot(panel).attrs.onclick(); out.fix_sent = calls.slice(-1)[0];
      jobs = [{ id: "j2", status: "done", outputs: ["scouts/j2/edit.png"] }];
      out.fix_steps = text(actPanel());
      REV.chains.fix = { id: "c2", status: "running", steps: [{ id: "j2" }, { id: "m1", kind: "analysis", title: "Fix: a.mp4 (2/3: the motion)", status: "running", stage: "depth" }], of: 3, notes: [] };
      out.fix_reading = text(actPanel());
      REV.chains.fix = { id: "c2", status: "done", steps: [{ id: "j2" }, { id: "pack-p1", kind: "analysis", status: "done", cached: true }, { id: "j3" }], of: 3, notes: [] };
      jobs.push({ id: "j3", status: "done", outputs: ["renders/j3.mp4"] }); out.fix_done = text(actPanel());
      actOpen("upscale", "renders/a.mp4"); out.upscale = text(actPanel());
    """)
    assert got["video"] == ["Upscale", "keys-HDR LORA", "Remove a person", "Restyle", "Extend", "Fix"] and got["image"] == ["Turnaround"]
    assert got["id"] == "actpanel" and "Restyle this clip" in got["restyle"] and "three steps" in got["restyle"]
    assert "the motion, camera and timing come from the clip’s depth" in got["restyle"] and "The whole frame is redrawn" in got["restyle"]
    assert got["refused"] == "Say what the clip should look like now"
    assert got["sent"] == ["/api/revise", {"kind": "restyle", "params": {"video": "renders/a.mp4", "prompt": "a watercolour painting", "image_model": "qwen", "keep_sound": True, "seed": 1}}]
    assert "first 15 s" in got["note"]
    assert "Extend this clip" in got["extend"] and "30 s" in got["extend"] and "Join to the clip" in got["extend"]
    assert got["extend_sent"][1] == {"kind": "extend", "params": {"video": "renders/a.mp4", "seconds": 5, "seed": 1, "join": True}}
    assert "the whole frame is redrawn from the instruction" in got["fix"] and "the motion and camera are the clip’s" in got["fix"]
    assert "Motion Previs reads the clip’s motion as a depth layer" in got["fix"]
    assert got["models"] == ["qwen", "flux3k-fast"]
    assert got["fix_sent"][1]["kind"] == "fix" and got["fix_sent"][1]["params"]["instruction"] == "change the sky to a stormy sunset"
    assert "Step 1 of 3: the first frame" in got["fix_steps"] and "Step 2 of 3: the motion is queued when the one before finishes." in got["fix_steps"]
    assert "Step 2 of 3: the motion" in got["fix_reading"] and "Motion Previs is making the depth layer." in got["fix_reading"]
    assert "Step 3 of 3: the clip is queued" in got["fix_reading"]
    assert "Step 3 of 3: the clip" in got["fix_done"] and "Read before" in got["fix_done"] and "Fixed" in got["fix_done"] and "The clip as it was" in got["fix_done"]
    assert "Upscale this clip" in got["upscale"]


def test_agents_restyle_extend_and_fix_through_the_studio_tools(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("studio_mcp_revise", ROOT / "mcp/studio-mcp.py")
    mcp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mcp)
    sent = []
    monkeypatch.setattr(mcp, "call", lambda method, path, body=None, **_: sent.append((method, path, body)) or
                        {"id": "j1", "kind": "video", "status": "queued", "outputs": [], "revise": {"id": "c1", "of": 2}})
    listed = {t["name"]: t for t in mcp.answer({"method": "tools/list"})["tools"]}
    assert listed["restyle_clip"]["inputSchema"]["required"] == ["video", "prompt"] and listed["fix_clip"]["inputSchema"]["required"] == ["video", "instruction"]
    for name, args in (("restyle_clip", {"video": "renders/a.mp4", "prompt": "anime", "keep_sound": False}),
                       ("extend_clip", {"video": "renders/a.mp4", "seconds": 20}),
                       ("fix_clip", {"video": "renders/a.mp4", "instruction": "make it snow", "image_model": ""})):
        reply = mcp.answer({"method": "tools/call", "params": {"name": name, "arguments": args}})
        assert not reply.get("isError"), reply
        assert json.loads(reply["content"][0]["text"])["revise"] == {"id": "c1", "of": 2}
    sent = [one for one in sent if one[:2] != ("GET", "/api/state")]  # tools/list reads the platform first (Windows lists fewer tools)
    assert sent[0] == ("POST", "/api/revise", {"kind": "restyle", "params": {"video": "renders/a.mp4", "prompt": "anime", "keep_sound": False}})
    assert sent[1][2] == {"kind": "extend", "params": {"video": "renders/a.mp4", "seconds": 20}}
    assert sent[2][2] == {"kind": "fix", "params": {"video": "renders/a.mp4", "instruction": "make it snow"}}
    import agents
    assert all(name in agents.MAKERS and name in agents.STUDIO_TOOLS for name in ("restyle_clip", "extend_clip", "fix_clip"))
