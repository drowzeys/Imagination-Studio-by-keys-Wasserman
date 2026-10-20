#!/usr/bin/env python3
"""The Studio with a stand-in job runner, for the agent benchmark (scripts/agent-bench/bench.py).

    STUDIO_HOME=/tmp/bench-home PORT=7896 HOST=127.0.0.1 python3 scripts/agent-bench/dry_studio.py

Everything is the real app (its routes, its checks when a job is queued, Slate, the Editor, the Agent room and its
local harness) except the queue's runner: a job is never rendered. It is marked running, then done after
BENCH_JOB_SECONDS (default 2), with small placeholder files at the paths the real job would write (from the
server's own build(), else the kind's usual folder), so the Library lists them and a later step can use them.
Nothing touches the GPU.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("BENCH_APP_ROOT") or Path(__file__).resolve().parent.parent.parent).resolve()  # the app under test
sys.path.insert(0, str(ROOT / "app"))
os.environ.setdefault("MOTION_PYTHON", str(Path(__file__).resolve().with_name("fake_motion.py")))  # Motion Previs: no analysis
os.chdir(ROOT / "app")

import server  # noqa: E402  (the app; its queue thread calls server.perform, replaced below)

SECONDS = float(os.environ.get("BENCH_JOB_SECONDS", "2"))
SUFFIX = {"scout": ".png", "edit": ".png", "turnaround": ".png", "upscale": ".png", "mask": ".png", "remove": ".png",
          "video": ".mp4", "studio": ".mp4", "long": ".mp4", "export": ".mp4", "soundtrack": ".mp4",
          "voice": ".wav", "music": ".wav", "sfx": ".wav", "foley": ".wav"}
FOLDER = {"voice": "voice", "music": "music", "sfx": "sound/sfx", "foley": "sound/foley", "export": "exports", "soundtrack": "exports"}


def placeholder(path: Path) -> None:
    """A small file of the right type at path: a grey picture, a one-second clip with sound, or a second of silence."""

    path.parent.mkdir(parents=True, exist_ok=True)
    cache = server.HOME / ".bench" / ("x" + (path.suffix.lower() if path.suffix.lower() in (".png", ".jpg", ".mp4", ".wav") else ".bin"))
    if not cache.is_file():
        cache.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = shutil.which("ffmpeg")
        args = {".png": ["-f", "lavfi", "-i", "color=c=gray:s=320x180", "-frames:v", "1"],
                ".jpg": ["-f", "lavfi", "-i", "color=c=gray:s=320x180", "-frames:v", "1"],
                ".mp4": ["-f", "lavfi", "-i", "color=c=gray:s=320x180:d=1:r=24", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                         "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"],
                ".wav": ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1"]}.get(cache.suffix)
        if ffmpeg and args:
            subprocess.run([ffmpeg, "-v", "error", "-y", *args, str(cache)], check=False, capture_output=True)
        if not cache.is_file():
            cache.write_bytes(b"")
    shutil.copyfile(cache, path)


def outputs_of(job: dict) -> list[Path]:
    kind, params = job["kind"], job["params"]
    try:
        if kind not in ("export", "long", "voice", "music", "soundtrack"):
            _, _, outputs = server.build(kind, dict(params), job["id"])
            found = [Path(p) for p in outputs]
            if kind == "scout":  # the real scout writes count candidates and a contact sheet
                count = max(1, min(int(params.get("count", 4)), 12))
                folder = found[0].parent if found else server.HOME / "scouts" / job["id"]
                found = [folder / f"scout_s{i + 1}.png" for i in range(count)] + [folder / "sheet.jpg"]
            if found:
                return found
    except Exception:  # noqa: BLE001  (a kind whose engine is not installed: its usual folder instead)
        pass
    folder = server.HOME / FOLDER.get(kind, "renders")
    return [folder / f"{job['id']}{SUFFIX.get(kind, '.bin')}"]


def perform(job: dict, worker: dict | None = None) -> None:
    job["started"] = job.get("started") or time.time()
    job["status"], job["progress"] = "running", 0.5
    time.sleep(SECONDS)
    if job["status"] == "cancelled":
        job["finished"] = time.time()
        return
    paths = outputs_of(job)
    for path in paths:
        placeholder(path)
    job["outputs"] = [server.relative(p) for p in paths]
    if job["kind"] in ("voice", "music"):  # the take beside it, as the real job writes it (a soundtrack reads the song's)
        params = job["params"]
        take = {"id": job["id"], "path": job["outputs"][0], "title": params.get("title") or job.get("title"), "created": time.time(),
                "duration": 1.0, **{k: params.get(k) for k in ("caption", "lyrics", "seconds", "seed", "text")}}
        paths[0].with_suffix(".take.json").write_text(json.dumps(take, indent=1) + "\n")
        job[job["kind"]] = take
    if job["kind"] == "soundtrack":
        job["soundtrack"] = {"peak_dbfs": -1.0, "seconds": 1.0, "ducked": False, "music_db": job["params"].get("music_db")}
    job["log"] = (job.get("log") or "") + "[bench] dry run: not rendered\n"
    job["status"], job["finished"] = "done", time.time()


def qa_run(job: dict) -> None:
    time.sleep(SECONDS)
    clip = server.inside(job["params"].get("path") or "")
    if not clip.is_file():
        job["status"], job["error"] = "failed", "no such clip in the studio's folder"
    else:
        job["outputs"] = [server.relative(clip)]
        job["qa"] = {"passed": True, "takes": [{"passed": True, "audio": {"passed": True}, "identity": {"passed": True},
                                                 "sharpness": {"passed": True}, "take": 1}], "from": None}
        job["status"] = "done"
    job["finished"] = time.time()


server.perform = perform
server.qa_run = qa_run

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(server.app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "7896")), log_level="warning")
