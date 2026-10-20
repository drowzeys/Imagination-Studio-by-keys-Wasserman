#!/usr/bin/env python3
"""Every feature of the web app, driven through its own API the way the page drives it, on a Mac or a DGX Spark.

    python3 scripts/studio-check.py                    # everything: API, short renders, realistic clip and song, sound last
    python3 scripts/studio-check.py --tier api         # only what renders nothing (seconds)
    python3 scripts/studio-check.py --tier short       # API and the short renders
    python3 scripts/studio-check.py --only voice,music # checks whose name starts with one of these (and the setup ones)
    python3 scripts/studio-check.py --list             # the checks, in the order they run
    python3 scripts/studio-check.py --keep             # leave the renders, jobs and test films; --clean <report.json> later
    python3 scripts/studio-check.py --base http://mac:7870 --base http://spark:7870   # both at once, one table
    python3 scripts/studio-check.py --merge mac/report.json spark/report.json --out both # side by side from two runs

Run it on the machine of the app: the brains, agents and storage routes answer only from 127.0.0.1, and from another
machine they are n/a ("answers only on its own machine"). A request without an Origin passes the app's same-origin
test as the page's do. It needs the standard library, ffmpeg and ffprobe only. The platform is read from
``/api/state``; a feature that platform does not offer (``/api/state``, ``/api/*/state``, ``/api/actions``) is n/a
with the app's own reason, never PASS; a check whose input an earlier check failed to make is BLOCKED.

Each check queues the job the page queues, waits for it and checks what it wrote: ffprobe for the streams, length and
size, a mid frame for a flat or black picture, volumedetect for silence and clipping. Renders are the smallest the app
takes (22 frames at 864x480 with 4 passes, 512-pixel pictures, a 10 s song) except the realistic ones (a 5 s clip with
the Home page's Quick recipe, the Sharp Spec clip with its QA gate, a 30 s song with lyrics).

Output: ``<out>/report.json`` (machine-readable: every check's status, seconds, job seconds, detail, error, and what
the run made) and ``<out>/report.md``; default out /tmp/studio-check-<time>. At the end the run clears its own jobs,
moves their files to the trash and (on its own machine) erases exactly those, and deletes its films, projects and
Motion packs. The browser half (every tab, every button, writes blocked) is ``scripts/studio-check-ui.cjs``.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

os.environ["PATH"] = os.environ.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin"
JOB_TIMEOUT = 2400  # a job that takes longer than 40 minutes is a failure here
# reference timings of the same work on a DGX Spark (2026-10-19), for the report's last column: seconds
SPARK_REFERENCE = {"video-realistic": 81, "music-realistic": 78}


class Fail(Exception):
    pass


class NotHere(Exception):
    """The machine does not offer this feature (n/a): the reason is the app's own where it gives one."""


class Blocked(Exception):
    """An earlier check this one builds on failed or did not run."""


Skip = NotHere
LOCAL_ONLY = re.compile(r"on this computer|Tailscale address", re.I)


# ---------- HTTP ----------

class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body=None, timeout: float = 120, raw: bool = False, headers=None):
        data, head = None, dict(headers or {})
        if body is not None:
            data, head["Content-Type"] = json.dumps(body).encode(), "application/json"
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=head)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as answer:
                payload = answer.read()
                status, kind = answer.status, answer.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            payload, status, kind = error.read(), error.code, error.headers.get("Content-Type", "")
        if raw:
            return status, payload
        try:
            parsed = json.loads(payload) if payload else None
        except ValueError:
            parsed = payload.decode(errors="replace")[:500] if "json" not in kind else None
        return status, parsed

    def ok(self, method: str, path: str, body=None, timeout: float = 120):
        status, data = self.call(method, path, body, timeout)
        if status >= 400:
            why = (data.get("error") or data.get("detail")) if isinstance(data, dict) else data
            if status == 403 and LOCAL_ONLY.search(str(why)):
                raise NotHere(f"{path} answers only on its own machine (127.0.0.1): run the check there")
            raise Fail(f"{method} {path} -> {status}: {str(why)[:400]}")
        return data

    def get(self, path, **kw):
        return self.ok("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.ok("POST", path, {} if body is None else body, **kw)

    def upload(self, file: Path, path: str = "/api/upload", fields: dict | None = None) -> dict:
        boundary = uuid.uuid4().hex
        parts = []
        for key, value in (fields or {}).items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        kind = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file.name}"\r\n'
                     f"Content-Type: {kind}\r\n\r\n".encode() + file.read_bytes() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        request = urllib.request.Request(self.base + path, data=b"".join(parts), method="POST",
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(request, timeout=300) as answer:
                return json.loads(answer.read())
        except urllib.error.HTTPError as error:
            raise Fail(f"upload {file.name} to {path} -> {error.code}: {error.read()[:300].decode(errors='replace')}")


# ---------- media checks ----------

def run(command: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def probe(file: Path) -> dict:
    done = run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,sample_rate,channels,nb_frames",
                "-of", "json", str(file)])
    if done.returncode:
        raise Fail(f"ffprobe cannot read {file.name}: {done.stderr.strip()[:200]}")
    data = json.loads(done.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    return {"duration": float(data.get("format", {}).get("duration") or 0), "video": video, "audio": audio}


def loudness(file: Path) -> tuple[float, float]:
    """(mean, max) dB of the sound."""

    done = run(["ffmpeg", "-v", "info", "-nostats", "-i", str(file), "-vn", "-af", "volumedetect", "-f", "null", "-"], 300)
    mean = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", done.stderr)
    peak = re.search(r"max_volume: (-?[\d.]+|-inf) dB", done.stderr)
    to = lambda m: -999.0 if not m or m.group(1) == "-inf" else float(m.group(1))
    return to(mean), to(peak)


def frame_stats(file: Path, at: float = 0.0) -> dict:
    """Luma min/avg/max and the frame-to-frame change of one frame: a flat or black picture shows here."""

    command = ["ffmpeg", "-v", "error", "-nostats"] + (["-ss", f"{at:.2f}"] if at else []) + [
        "-i", str(file), "-frames:v", "1", "-vf", "signalstats,metadata=print:file=-", "-f", "null", "-"]
    done = run(command)
    found = dict(re.findall(r"lavfi\.signalstats\.(YMIN|YAVG|YMAX)=([\d.]+)", done.stdout))
    return {k: float(v) for k, v in found.items()}


def picture(file: Path, size: tuple[int, int] | None = None) -> dict:
    info = probe(file)
    video = info["video"] or {}
    got = (video.get("width"), video.get("height"))
    if size and tuple(size) != got:
        raise Fail(f"{file.name} is {got[0]}x{got[1]}, asked {size[0]}x{size[1]}")
    stats = frame_stats(file)
    if stats and stats.get("YMAX", 255) - stats.get("YMIN", 0) < 12:
        raise Fail(f"{file.name} is a flat picture (luma {stats})")
    return {"size": f"{got[0]}x{got[1]}", "luma": stats.get("YAVG")}


def clip(file: Path, seconds: float | None = None, size: tuple[int, int] | None = None, sound: bool = True,
         tolerance: float = 0.35) -> dict:
    info = probe(file)
    video = info["video"]
    if not video:
        raise Fail(f"{file.name} has no picture stream")
    out = {"duration": round(info["duration"], 2), "size": f"{video.get('width')}x{video.get('height')}"}
    if size and (video.get("width"), video.get("height")) != tuple(size):
        raise Fail(f"{file.name} is {out['size']}, asked {size[0]}x{size[1]}")
    if seconds and abs(info["duration"] - seconds) > max(tolerance, seconds * 0.1):
        raise Fail(f"{file.name} runs {info['duration']:.2f} s, asked {seconds:.2f} s")
    middle = frame_stats(file, info["duration"] / 2)
    if middle and middle.get("YMAX", 255) - middle.get("YMIN", 0) < 12:
        raise Fail(f"{file.name}: the middle frame is flat (luma {middle})")
    out["luma"] = middle.get("YAVG")
    if sound:
        if not info["audio"]:
            raise Fail(f"{file.name} has no sound stream")
        mean, peak = loudness(file)
        out["sound_db"] = [mean, peak]
        if peak < -60:
            raise Fail(f"{file.name}: the sound is silent (peak {peak} dB)")
        if peak > 0.0:
            raise Fail(f"{file.name}: the sound clips (peak {peak} dB)")
    return out


def sound(file: Path, seconds: float | None = None, tolerance: float = 1.0) -> dict:
    info = probe(file)
    if not info["audio"]:
        raise Fail(f"{file.name} has no sound stream")
    if seconds and abs(info["duration"] - seconds) > max(tolerance, seconds * 0.15):
        raise Fail(f"{file.name} runs {info['duration']:.2f} s, asked about {seconds} s")
    mean, peak = loudness(file)
    if peak < -60:
        raise Fail(f"{file.name} is silent (peak {peak} dB)")
    return {"duration": round(info["duration"], 2), "rate": info["audio"].get("sample_rate"), "channels": info["audio"].get("channels"),
            "sound_db": [mean, peak]}


# ---------- the run ----------

class Run:
    def __init__(self, api: Api, out: Path, keep: bool):
        self.api, self.out, self.keep = api, out, keep
        self.results: list[dict] = []
        self.jobs: list[str] = []            # queue jobs this run made
        self.paths: list[str] = []           # studio files this run made outside the queue's (relative to home)
        self.slate: list[str] = []           # Slate films
        self.projects: list[str] = []        # Production projects
        self.packs: list[str] = []           # Motion Previs packs
        self.ctx: dict = {}
        self.state = api.get("/api/state")
        self.home = Path(self.state["home"])
        self.platform = self.state.get("platform") or "?"
        host = urllib.parse.urlsplit(api.base).hostname or ""
        # the run's files are read in place only when the app is on this machine; else through /files
        self.local = host in ("127.0.0.1", "localhost", "::1") and self.home.is_dir()
        self.fixtures = Path(tempfile.mkdtemp(prefix="studio-check-fixtures-"))

    def file(self, relative: str) -> Path:
        """A studio file to read: on this machine where it is, else fetched through the app's /files route."""

        if self.local:
            return self.home / relative
        target = self.fixtures / "fetched" / relative
        if not target.is_file():
            status, body = self.api.call("GET", "/files/" + urllib.request.quote(relative), raw=True, timeout=600)
            if status >= 400:
                raise Fail(f"/files/{relative} -> {status}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        return target

    def rel(self, path: Path) -> str:
        """The studio-relative path of a file handed back by file()."""

        root = self.home if self.local else self.fixtures / "fetched"
        return str(path.resolve().relative_to(root.resolve()))

    # a queue job from start to end; returns (job, seconds it ran)
    def job(self, body: dict, timeout: float = JOB_TIMEOUT, path: str = "/api/jobs") -> dict:
        body = {"sparks": 1, **body} if path == "/api/jobs" else body
        job = self.api.post(path, body)
        if not isinstance(job, dict) or "id" not in job:
            raise Fail(f"{path} answered without a job: {str(job)[:300]}")
        self.jobs.append(job["id"])
        return self.wait(job["id"], timeout)

    def wait(self, job_id: str, timeout: float = JOB_TIMEOUT) -> dict:
        start = time.time()
        while True:
            job = self.api.get(f"/api/jobs/{job_id}")
            if job["status"] in ("done", "failed", "cancelled"):
                break
            if time.time() - start > timeout:
                self.api.call("POST", f"/api/jobs/{job_id}/cancel", {})
                raise Fail(f"job {job_id} still {job['status']} after {timeout:.0f} s (cancelled); last log: {job.get('log', '')[-300:]}")
            time.sleep(2)
        if job["status"] != "done":
            log = (job.get("log") or "").strip().splitlines()
            raise Fail(f"job {job['status']}: {job.get('error') or ''} | log: {' / '.join(log[-4:])[-500:]}")
        return job

    def output(self, job: dict, pattern: str = r".") -> Path:
        for path in job.get("outputs") or []:
            if re.search(pattern, path):
                target = self.file(path)
                if target.stat().st_size == 0:
                    raise Fail(f"output {path} is listed but missing or empty")
                return target
        raise Fail(f"no output matching {pattern!r} in {job.get('outputs')}")

    def check(self, name: str, tier: str, needs: tuple = (), spark: float | None = None):
        def wrap(fn):
            fn.check = {"name": name, "tier": tier, "needs": needs, "spark": spark}
            return fn
        return wrap

    def record(self, name, tier, status, seconds, detail=None, error=None, job_seconds=None, spark=None):
        row = {"name": name, "tier": tier, "status": status, "seconds": round(seconds, 1),
               "job_seconds": None if job_seconds is None else round(job_seconds, 1), "spark_seconds": spark,
               "detail": detail, "error": error}
        self.results.append(row)
        flag = {"PASS": "ok  ", "FAIL": "FAIL", "N/A": "n/a ", "BLOCKED": "blkd"}[status]
        took = f"{seconds:7.1f} s" + (f" (job {job_seconds:.1f} s)" if job_seconds is not None else "")
        print(f"{flag} {name:28} {took:22} {error or json.dumps(detail, default=str)[:160] if detail else error or ''}", flush=True)

    # ---------- cleanup ----------

    def clean(self):
        if self.keep:
            print("--keep: renders, jobs and test projects left in place")
            return
        api = self.api
        for pack in self.packs:
            api.call("DELETE", f"/api/motion/packs/{pack}")
        for film in self.slate:
            api.call("DELETE", f"/api/slate/projects/{film}")
        mine = [j for j in self.jobs]
        status, answer = api.call("POST", "/api/clear", {"jobs": mine, "paths": self.paths, "films": True})
        erased = 0
        last = self.home / "trash" / "last-deletion.json"
        if not self.local:
            print(f"cleaned through the API: {len(mine)} jobs cleared, their files are in the app's trash ({answer if status >= 400 else 'ok'})")
            return
        if status < 400 and last.is_file():
            try:
                record = json.loads(last.read_text())
                for moved in record.get("files", []):
                    target = self.home / "trash" / moved["to"]
                    if target.is_file():
                        target.unlink()
                        erased += 1
            except (OSError, ValueError):
                pass
        # what the queue's clear does not move: songs, sound, Motion packs' folders, Slate takes, Production projects
        for relative in self.paths:
            target = self.file(relative)
            if target.is_file() and target.resolve().is_relative_to(self.home.resolve()):
                target.unlink()
                erased += 1
        for job in mine:
            for folder in ("music", "sound/sfx", "sound/foley", "voice"):
                for leftover in (self.home / folder).glob(f"{job}*"):
                    if leftover.is_file():
                        leftover.unlink()
                        erased += 1
        for pack in self.packs:  # a deleted Motion pack goes to the trash as <time>-motion__<id>[.zip]
            for found in (self.home / "trash").glob(f"*-motion__{pack}*"):
                if re.fullmatch(r"[\w-]{1,64}", pack):
                    shutil.rmtree(found) if found.is_dir() else found.unlink()
        for film in self.slate:  # a deleted film goes to the trash as slate-<id>-<time>
            for folder in (self.home / "trash").glob(f"slate-{film}-*"):
                if folder.is_dir() and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", film):
                    shutil.rmtree(folder)
        for project in self.projects:
            folder = self.home / "projects" / project
            if folder.is_dir() and re.fullmatch(r"p\d{4}-\d{6}", project):
                shutil.rmtree(folder)
        shutil.rmtree(self.fixtures, ignore_errors=True)
        print(f"cleaned: {len(mine)} jobs cleared, {erased} files erased, {len(self.slate)} films, {len(self.projects)} projects, "
              f"{len(self.packs)} packs deleted ({answer if status >= 400 else 'ok'})")


# ---------- fixtures ----------

def make_fixtures(r: Run):
    d = r.fixtures
    commands = {
        "clip.mp4": ["-f", "lavfi", "-i", "testsrc2=size=864x480:rate=24:duration=4", "-f", "lavfi", "-i",
                     "sine=frequency=330:duration=4:sample_rate=48000", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "128k"],
        "still.png": ["-f", "lavfi", "-i", "testsrc2=size=768x768", "-frames:v", "1"],
        "tone.wav": ["-f", "lavfi", "-i", "sine=frequency=220:duration=4:sample_rate=44100", "-af", "volume=0.3"],
    }
    for name, args in commands.items():
        done = run(["ffmpeg", "-v", "error", "-y"] + args + [str(d / name)])
        if done.returncode:
            raise Fail(f"ffmpeg could not make the fixture {name}: {done.stderr[:200]}")
    (d / "film.fountain").write_text("Title: Mac Check\n\nEXT. HARBOUR - NIGHT\n\nA lighthouse beam sweeps the fog.\n\n"
                                     "MARA\nWe should not be here.\n\nINT. CABIN - NIGHT\n\nRain on the window.\n")
    for name in ("clip.mp4", "still.png", "tone.wav"):
        r.ctx["up_" + name.split(".")[0]] = r.api.upload(d / name)["path"]
        r.paths.append(r.ctx["up_" + name.split(".")[0]])


# ---------- the checks ----------

CHECKS = []


def check(name: str, tier: str = "api", spark: float | None = None):
    def wrap(fn):
        CHECKS.append((name, tier, spark, fn))
        return fn
    return wrap


def need(r: Run, *keys):
    missing = [k for k in keys if k not in r.ctx]
    if missing:
        raise Blocked("needs the output of an earlier check: " + ", ".join(missing))
    return [r.ctx[k] for k in keys]


SMALL = {"preset": "small", "quality": "fh4", "seed": 1, "frames": 22, "seconds": 22 / 24}
SMALL_SIZE = (864, 480)


@check("state")
def c_state(r: Run):
    s = r.state
    if s.get("platform") not in ("mac", "spark"):
        raise Fail(f"platform is {s.get('platform')!r}, neither mac nor spark")
    return {"platform": s.get("platform"), "label": s.get("platform_label")} | {k: s.get(k) for k in ("version", "zig", "turbo", "x2", "ref2va", "ref2va_turbo", "ref_media")} | {
        "image_models": [m["id"] for m in s.get("image_models", [])]}


@check("fixtures")
def c_fixtures(r: Run):
    make_fixtures(r)
    return {k: v for k, v in r.ctx.items() if k.startswith("up_")}


@check("module-states")
def c_states(r: Run):
    out = {}
    for path in ("/api/voice/state", "/api/music/state", "/api/sound/state", "/api/motion/state", "/api/edit/state", "/api/actions",
                 "/api/loras", "/api/loras/groups", "/api/suite", "/api/discover", "/api/settings", "/api/telemetry",
                 "/api/telemetry/history", "/api/storage", "/api/library", "/api/trash", "/api/projects", "/api/slate/projects",
                 "/api/nodes/graphs", "/api/nodes/runs", "/api/motion/packs", "/api/motion/sources", "/api/sound/takes",
                 "/api/voice/takes", "/api/music/takes", "/api/suite/imports", "/api/hf", "/api/brains", "/api/agent/tools",
                 "/api/agent/threads", "/api/agent/tasks", "/api/export/callsheet-entry", "/api/updates", "/api/updates/health"):
        start = time.time()
        status, data = r.api.call("GET", path, timeout=60)
        took = time.time() - start
        out[path] = status if took < 3 else f"{status} ({took:.1f} s)"
        if status >= 400:
            raise Fail(f"GET {path} -> {status}: {str(data)[:200]}")
        if took > 10:
            raise Fail(f"GET {path} took {took:.1f} s")
    r.ctx["voice_state"], r.ctx["music_state"] = r.api.get("/api/voice/state"), r.api.get("/api/music/state")
    r.ctx["sound_state"], r.ctx["motion_state"] = r.api.get("/api/sound/state"), r.api.get("/api/motion/state")
    r.ctx["edit_state"], r.ctx["actions"] = r.api.get("/api/edit/state"), r.api.get("/api/actions")
    r.ctx["loras"] = {l["id"]: l for l in r.api.get("/api/loras")}
    slow = {k: v for k, v in out.items() if not isinstance(v, int)}
    return {"routes": len(out), "slow": slow}


@check("compose-check-estimate")
def c_compose(r: Run):
    composed = r.api.post("/api/compose", {"fields": {"scene": "A lighthouse on a cliff at dusk", "action": "the beam sweeps",
                                                      "camera": "", "dialogue": [], "soundscape": "", "music": "N/A"},
                                           "first_frame": False, "last_frame": False, "seconds": 5})
    if not composed.get("prompt"):
        raise Fail("compose gave no prompt")
    r.ctx["prompt"] = composed["prompt"]
    checked = r.api.post("/api/prompt/check", {"prompt": "a quiet street, no cars"})
    est = r.api.post("/api/estimate", {"preset": "small", "quality": "fh4", "seconds": 5, "text_only": True})
    if est.get("seconds") is None:
        raise Fail(f"no estimate for a 5 s clip at small/fh4 on this machine: {est}")
    return {"prompt_chars": len(composed["prompt"]), "negations": checked, "estimate_s": est.get("seconds")}


@check("probe-frame-shot-lineart")
def c_media_tools(r: Run):
    up_clip, up_tone, up_still = need(r, "up_clip", "up_tone", "up_still")
    p = r.api.get(f"/api/probe?path={up_clip}")
    if abs(float(p.get("duration") or 0) - 4) > 0.3 or not p.get("sound"):
        raise Fail(f"probe of a 4 s clip with sound: {p}")
    a = r.api.get(f"/api/probe?path={up_tone}")
    frame = r.api.post("/api/frame", {"path": up_clip, "at": "last"})
    r.paths.append(frame["path"])
    picture(r.file(frame["path"]), (864, 480))
    window = r.api.get(f"/api/shot/window?path={up_clip}")
    line = r.api.post("/api/lineart", {"path": up_still, "detail": "medium"})
    given = line["path"]
    relative = str(Path(given).relative_to(r.home)) if Path(given).is_absolute() else given
    picture(r.file(relative))
    r.paths.append(relative)
    return {"probe_audio": a, "frame": frame["path"], "window_keys": sorted(window)[:6], "lineart": line["path"]}


@check("production-project")
def c_project(r: Run):
    up_clip, = need(r, "up_clip")
    proj = r.api.post("/api/projects", {"name": "studio-check"})
    r.projects.append(proj["id"])
    proj["timeline"] = [{"path": up_clip, "in": 0, "out": 0, "duration": 4}, {"path": up_clip, "in": 1, "out": 3, "duration": 4}]
    r.api.ok("PUT", f"/api/projects/{proj['id']}", proj)
    back = r.api.get(f"/api/projects/{proj['id']}")
    if len(back.get("timeline") or []) != 2:
        raise Fail("the timeline did not stay saved")
    out = {}
    for ext in ("fcpxml", "edl", "zip"):
        status, body = r.api.call("GET", f"/api/export/timeline/{proj['id']}.{ext}", raw=True, timeout=120)
        if status >= 400:
            raise Fail(f"timeline .{ext} -> {status}: {body[:300].decode(errors='replace')}")
        out[ext] = len(body)
    r.ctx["project"] = proj["id"]
    return {"project": proj["id"], "preset": proj.get("preset"), "quality": proj.get("quality"), "bytes": out}


@check("slate-film")
def c_slate(r: Run):
    up_clip, = need(r, "up_clip")
    film = r.api.post("/api/slate/projects", {"name": "studio-check film"})
    r.slate.append(film["id"])
    film["scenes"] = [{"id": "sc1", "name": "Harbour", "shots": [{"id": "sh1", "name": "Beam", "prompt": "A lighthouse beam sweeps the fog over a dark harbour.",
                                                                    "spec": {"durationSec": 1}, "takes": []}]}]
    r.api.ok("PUT", f"/api/slate/projects/{film['id']}", film)
    compiled = r.api.post("/api/slate/compile", {"project": film, "shotId": "sh1"})
    if not compiled.get("prompt") or not compiled.get("plan"):
        raise Fail(f"compile gave {str(compiled)[:200]}")
    take = r.api.post("/api/slate/takes", {"projectId": film["id"], "shot": "sh1", "path": up_clip, "log": True})
    media = (take.get("take") or {}).get("mediaPath")
    there = r.api.post("/api/slate/exists", {"paths": [media]})
    if not there.get(media):
        raise Fail(f"an imported take is not on disk: {media}")
    r.paths.append(media)
    r.ctx["film"], r.ctx["film_plan"], r.ctx["film_prompt"] = film, compiled["plan"], compiled["prompt"]
    brain = r.api.get("/api/slate/brain")
    return {"film": film["id"], "plan": {k: compiled["plan"].get(k) for k in ("preset", "quality", "frames", "seconds")},
            "warnings": compiled.get("warnings"), "brain": str(brain)[:120]}


@check("suite-import")
def c_suite(r: Run):
    imported = r.api.upload(r.fixtures / "film.fountain", "/api/suite/import", {"app": "fountain"})
    to_slate = r.api.post(f"/api/suite/imports/{imported['id']}/slate", {})
    if to_slate.get("project"):
        r.slate.append(to_slate["project"])
    scenes = int(to_slate.get("scenes") or 0)
    if scenes < 1:
        raise Fail(f"the Fountain import made a film with no scenes: {str(to_slate)[:200]}")
    return {"import": imported.get("id"), "app": imported.get("app"), "scenes": scenes, "shots": to_slate.get("shots")}


@check("nodes-check")
def c_nodes_check(r: Run):
    graph = {"name": "studio-check", "nodes": [{"id": "t1", "type": "text", "x": 0, "y": 0, "params": {"text": "Hello from the Mac check."}},
                                            {"id": "v1", "type": "voice", "x": 300, "y": 0, "params": {}}],
             "edges": [{"from": "t1", "out": "text", "to": "v1", "in": "text"}]}
    answer = r.api.post("/api/nodes/check", {"graph": graph})
    if not answer.get("ok"):
        raise Fail(f"a text to voice graph does not check: {answer.get('problems')}")
    r.ctx["graph"] = graph
    return {"order": answer.get("order")}


@check("voice-cut")
def c_voice_cut(r: Run):
    up_tone, = need(r, "up_tone")
    cut = r.api.post("/api/voice/cut", {"path": up_tone, "seconds": 2})
    r.paths.append(cut["path"])
    return sound(r.file(cut["path"]), 2, 0.3)


@check("export-timeline", "short")
def c_export(r: Run):
    up_clip, = need(r, "up_clip")
    job = r.job({"kind": "export", "title": "studio-check export", "params": {"clips": [{"path": up_clip, "in": 0, "out": 0, "duration": 4},
                                                                                     {"path": up_clip, "in": 1, "out": 3, "duration": 4}],
                                                                           "width": 1344, "height": 768}})
    out = r.output(job, r"\.mp4$")
    return clip(out, 6, (1344, 768)) | {"_job": job}


@check("qa-gate", "short")
def c_qa(r: Run):
    up_clip, = need(r, "up_clip")
    job = r.job({"path": up_clip, "prompt": "A test pattern with a steady tone."}, path="/api/qa")
    qa = job.get("qa") or {}
    return {"passed": qa.get("passed"), "takes": len(qa.get("takes") or []), "note": str(qa.get("note") or qa.get("why") or "")[:160], "_job": job}


@check("brain-local", "short")
def c_brain(r: Run):
    answer = r.api.post("/api/brains/test", {"backend": "local"}, timeout=200)
    if not answer.get("ok"):
        raise Fail(f"the local brain did not answer: {answer.get('error')}")
    return {"text": str(answer.get("text"))[:60], "ms": answer.get("elapsedMs")}


@check("slate-first-ad", "short")
def c_first_ad(r: Run):
    """The Production tab's First AD: the film's brain (Automatic follows the Studio's own), asked for JSON."""
    answer = r.api.post("/api/slate/brain", {"prompt": 'Return JSON: {"reply": "ready"}', "system": "You are a film's First AD.",
                                              "tier": "standard", "expectJson": True}, timeout=200)
    if not answer.get("ok"):
        raise Fail(f"the First AD did not answer: {answer.get('error')}")
    return {"text": str(answer.get("text"))[:60], "json": answer.get("json"), "ms": answer.get("elapsedMs")}


@check("agent-thread", "short")
def c_agent(r: Run):
    made = r.api.post("/api/agent/threads", {"prompt": "Reply with the single word: ok. Do not use any tool.", "backend": "local"}, timeout=60)
    task, thread = made.get("task") or {}, made.get("thread") or {}
    start = time.time()
    while True:
        t = r.api.get(f"/api/agent/tasks/{task['id']}")
        if t.get("status") not in ("running", "queued"):
            break
        if time.time() - start > 300:
            r.api.call("POST", f"/api/agent/tasks/{task['id']}/cancel", {})
            raise Fail("the agent task ran over 300 s")
        time.sleep(2)
    r.api.call("DELETE", f"/api/agent/threads/{thread.get('id')}")
    if t.get("status") != "done":
        raise Fail(f"agent task {t.get('status')}: {str(t.get('error'))[:300]}")
    if not str(t.get("reply") or "").strip():
        raise Fail("the agent task finished with an empty reply")
    return {"answer": str(t.get("reply"))[:80], "seconds": round(time.time() - start, 1)}


# ----- pictures -----

def scout(r: Run, model: str, w: int, h: int, key: str | None = None):
    models = [m["id"] for m in r.state.get("image_models", [])]
    if model not in models:
        raise NotHere(f"{model} is not installed or not offered on a {r.platform}")
    job = r.job({"kind": "scout", "title": f"studio-check {model}", "params": {"prompt": "A red lighthouse on a green cliff above a stormy sea, golden hour",
                                                                           "count": 1, "seed": 1, "width": w, "height": h, "image_model": model}})
    out = r.output(job, r"scout_s\d+\.png$")
    if key:
        r.ctx[key] = r.rel(out)
    return picture(out, (w, h)) | {"_job": job}


@check("picture-qwen", "short")
def c_qwen(r: Run):
    return scout(r, "qwen", 1344, 768, "picture")


for _model in ("qwen-full", "flux3k-fast", "flux3k-full", "moss-image"):
    check(f"picture-{_model}", "short")(lambda r, m=_model: scout(r, m, 512, 512))


def picture_in(r: Run) -> str:
    return r.ctx.get("picture") or r.ctx["up_still"]


@check("edit-qwen", "short")
def c_edit(r: Run):
    job = r.job({"kind": "edit", "title": "studio-check edit", "params": {"image": picture_in(r), "seed": 0, "size": "model", "resolution": 512,
                                                                      "image_model": "qwen", "base": False, "loras": [], "prompt": "make it night, with stars"}})
    return picture(r.output(job, r"\.png$")) | {"_job": job}


@check("edit-angles", "short")
def c_angles(r: Run):
    state = r.ctx.get("edit_state") or {}
    lora = state.get("angle_lora") or "qwen-multiple-angles"
    if (r.ctx.get("loras", {}).get(lora) or {}).get("status") != "ready":
        raise Skip(f"the angle LoRA {lora} is not installed")
    job = r.job({"kind": "edit", "title": "studio-check angles", "params": {"image": picture_in(r), "seed": 0, "size": "model", "resolution": 512,
                                                                        "image_model": "qwen", "base": False, "loras": [{"id": lora}],
                                                                        "angles": [{"direction": "right", "height": "eye", "close": False}]}})
    return picture(r.output(job, r"\.png$")) | {"_job": job}


@check("edit-flux3k-fast", "short")
def c_edit_flux(r: Run):
    if not any(m["id"] == "flux3k-fast" for m in (r.ctx.get("edit_state") or {}).get("models", [])):
        raise Skip("FLUX3-K edits are not offered here")
    job = r.job({"kind": "edit", "title": "studio-check flux edit", "params": {"image": picture_in(r), "seed": 0, "size": "model", "resolution": 512,
                                                                           "image_model": "flux3k-fast", "base": False, "loras": [], "prompt": "make it snow"}})
    return picture(r.output(job, r"\.png$")) | {"_job": job}


@check("turnaround", "short")
def c_turn(r: Run):
    turn = (r.ctx.get("actions") or {}).get("turnaround") or {}
    if not turn.get("ready"):
        raise Skip(turn.get("why") or "not ready")
    job = r.job({"kind": "turnaround", "title": "studio-check turnaround", "params": {"image": picture_in(r), "size": 512, "strength": 1.0, "seed": 0, "prompt": ""}})
    return picture(r.output(job, r"turn_sheet\.png$")) | {"views": len(job.get("outputs") or []), "_job": job}


@check("actions-refused", "api")
def c_actions_refused(r: Run):
    """An action this machine does not offer (upscale, mask and remove where ComfyUI and their models are not installed) is refused when queued."""
    out = {}
    for kind in ("upscale", "mask", "remove"):
        if (r.ctx.get("actions") or {}).get(kind, {}).get("ready"):
            continue
        status, data = r.api.call("POST", "/api/jobs", {"kind": kind, "params": {"video": r.ctx.get("up_clip"), "image": r.ctx.get("up_still"), "person": "the person"}})
        if status < 400:
            r.jobs.append(data.get("id"))
            raise Fail(f"{kind} is not ready here but was queued")
        out[kind] = status
    if not out:
        raise NotHere("every action is offered here")
    return out


def action(r: Run, kind: str, params: dict, pattern: str):
    found = (r.ctx.get("actions") or {}).get(kind) or {}
    if not found.get("ready"):
        raise NotHere(found.get("why") or f"{kind} is not offered here")
    source, = need(r, "person_clip")
    job = r.job({"kind": kind, "title": f"studio-check {kind}", "params": {"video": source, **params}})
    out = r.output(job, pattern)
    return (clip(out, 22 / 24, sound=False) if out.suffix == ".mp4" else picture(out)) | {"outputs": len(job.get("outputs") or []), "_job": job}


check("action-upscale", "short")(lambda r: action(r, "upscale", {"megapixels": 1.0, "denoise": 0.1}, r"\.mp4$"))
check("action-mask", "short")(lambda r: action(r, "mask", {"person": "the person"}, r"person_mask\.mp4$"))
check("action-remove", "short")(lambda r: action(r, "remove", {"person": "the person", "window": 22, "steps": 4}, r"renders/.*\.mp4$"))


@check("sparks", "api")
def c_sparks(r: Run):
    if not (r.state.get("sparks") or {}).get("supported"):
        raise NotHere("this app has no Sparks setting (on a Mac, a Studio from before a Mac could lead Sparks)")
    me = r.api.get("/api/spark/self")
    hub = r.api.get("/api/sparks")
    plan = r.api.post("/api/sparks/plan", {"sparks": 1, "kind": "video", "params": {**SMALL, "prompt": "x"}})
    return {"self": str(me)[:120], "workers": len((hub or {}).get("workers") or []), "plan": str(plan)[:160]}


# ----- clips -----

@check("video-text-fh4", "short")
def c_video(r: Run):
    job = r.job({"kind": "video", "title": "studio-check text", "params": {"prompt": r.ctx.get("prompt") or "A lighthouse beam sweeps the fog.", "image": None, **SMALL}})
    out = r.output(job, r"\.mp4$")
    r.ctx["clip"] = r.rel(out)
    return clip(out, 22 / 24, SMALL_SIZE) | {"engine": job.get("engine"), "shot": (job.get("shot") or {}).get("passed"), "_job": job}


@check("video-2x-decoder", "short")
def c_video_x2(r: Run):
    if not r.state.get("x2"):
        raise NotHere("the 2x decoder is not installed here (a Mac install's)")
    job = r.job({"kind": "video", "title": "studio-check 2x", "params": {"prompt": "Leaves drift across a stone courtyard.", "image": None,
                                                                     **SMALL, "preset": "draft"}})
    size = tuple(r.state["presets"]["draft"]["size"])
    return clip(r.output(job, r"\.mp4$"), 22 / 24, size) | {"_job": job}


@check("video-standard-turbo", "short")
def c_video_std(r: Run):
    if not r.state.get("turbo"):
        raise NotHere("MiniMax H3 Turbo (quality standard) is not installed here")
    job = r.job({"kind": "video", "title": "studio-check standard", "params": {"prompt": "A paper boat drifts across a puddle in the rain.", "image": None,
                                                                           **SMALL, "quality": "standard"}})
    return clip(r.output(job, r"\.mp4$"), 22 / 24, SMALL_SIZE) | {"engine": job.get("engine"), "_job": job}


@check("video-from-picture", "short")
def c_video_image(r: Run):
    job = r.job({"kind": "video", "title": "studio-check from picture", "params": {"prompt": "The waves crash against the cliff below the lighthouse.",
                                                                               "image": picture_in(r), **SMALL}})
    return clip(r.output(job, r"\.mp4$"), 22 / 24) | {"engine": job.get("engine"), "_job": job}


@check("video-continuity", "short")
def c_video_cont(r: Run):
    source, = need(r, "clip")
    job = r.job({"kind": "video", "title": "studio-check continues", "params": {"prompt": "The beam keeps sweeping as the fog thickens.",
                                                                            "first_frame_from": source, **SMALL}})
    return clip(r.output(job, r"\.mp4$"), 22 / 24) | {"_job": job}


@check("video-first-last", "short")
def c_video_last(r: Run):
    job = r.job({"kind": "video", "title": "studio-check first+last", "params": {"prompt": "The camera slowly circles the lighthouse.",
                                                                             "image": picture_in(r), "last_same": True, **SMALL}})
    return clip(r.output(job, r"\.mp4$"), 22 / 24) | {"_job": job}


@check("video-lora", "short")
def c_video_lora(r: Run):
    if (r.ctx.get("loras", {}).get("wasserman-80s-horror") or {}).get("status") != "ready":
        raise Skip("the 80s Horror LoRA is not installed")
    job = r.job({"kind": "video", "title": "studio-check LoRA", "params": {"prompt": "A neon diner at night, a figure in the doorway, rain on the glass.",
                                                                       "image": None, **SMALL, "loras": [{"id": "wasserman-80s-horror", "style": "VHS"}]}})
    return clip(r.output(job, r"\.mp4$"), 22 / 24, SMALL_SIZE) | {"engine": job.get("engine"), "_job": job}


@check("video-slate-shot", "short")
def c_slate_render(r: Run):
    film, plan, prompt = need(r, "film", "film_plan", "film_prompt")
    params = {**plan, "prompt": prompt, "output": f"slate/{film['id']}/takes/sh1-mac.mp4",
              "slate": {"project": film["id"], "scene": "sc1", "shot": "sh1"}}
    asked = {k: plan.get(k) for k in ("preset", "quality", "frames")}
    params.update({"preset": "small", "quality": "fh4", "frames": 22, "seconds": 22 / 24})  # the plan's own size is for a film, not a check
    job = r.job({"kind": "video", "title": "studio-check slate shot", "params": params})
    out = r.output(job, r"\.mp4$")
    r.paths.append(r.rel(out))
    return clip(out, 22 / 24) | {"plan": asked, "_job": job}


@check("studio-text-image-video", "short")
def c_studio(r: Run):
    job = r.job({"kind": "studio", "title": "studio-check studio", "params": {"prompt": "The lantern flickers as the door creaks open.",
                                                                          "image_prompt": "An old wooden door with a lantern beside it, night",
                                                                          "preset": "small", "quality": "fh4", "frames": 22, "image_model": "qwen"}})
    return clip(r.output(job, r"\.mp4$"), 22 / 24) | {"_job": job}


@check("long-take-16s", "short")
def c_long(r: Run):
    job = r.job({"kind": "long", "title": "studio-check long", "params": {"prompt": "A train crosses a snowy valley at dawn.", "image": None, "preset": "small",
                                                                      "quality": "fh4", "seed": 1, "total_seconds": 16, "segment_seconds": 8, "beats": []}})
    return clip(r.output(job, r"renders/[^/]+\.mp4$"), 16, SMALL_SIZE, tolerance=1.0) | {"parts": len(job.get("outputs") or []) - 1, "_job": job}


@check("ref2va-picture", "short")
def c_ref2va(r: Run):
    if not r.state.get("ref2va"):
        raise Skip("Ref2VA is not installed")
    job = r.job({"kind": "video", "title": "studio-check ref2va", "params": {"prompt": "<Picture 1> stands in the rain as the lighthouse beam passes.",
                                                                         "references": [picture_in(r)], "ref_turbo": bool(r.state.get("ref2va_turbo")),
                                                                         "preset": "small", "frames": 22, "seconds": 22 / 24, "seed": 1}})
    out = r.output(job, r"\.mp4$")
    r.ctx["person_clip"] = r.rel(out)
    return clip(out, 22 / 24) | {"passes": job.get("passes"), "_job": job}


@check("ref2va-video-audio", "short")
def c_ref_media(r: Run):
    if not r.state.get("ref_media"):
        raise Skip("reference videos and audio are not available (engine without packing.reference_layout)")
    audio = r.ctx.get("voice_wav") or r.ctx["up_tone"]
    job = r.job({"kind": "video", "title": "studio-check ref media", "params": {
        "prompt": "<Picture 1> walks through the scene of <Video 1>, speaking in the voice of <Audio 1>.",
        "references": [picture_in(r)], "reference_videos": [r.ctx["up_clip"]], "reference_audios": [audio],
        "ref_turbo": bool(r.state.get("ref2va_turbo")), "preset": "small", "frames": 22, "seconds": 22 / 24, "seed": 1}})
    return clip(r.output(job, r"\.mp4$"), 22 / 24) | {"pace": (job.get("pace") or {}).get("verdict"), "_job": job}


# ----- voice, music, nodes, motion -----

@check("voice-take", "short")
def c_voice(r: Run):
    if not (r.ctx.get("voice_state") or {}).get("available"):
        raise Skip((r.ctx.get("voice_state") or {}).get("why") or "voice not available")
    job = r.job({"text": "The lighthouse keeper counted every ship that passed in the night.", "seed": 1, "live": False}, path="/api/voice/takes")
    out = r.output(job, r"\.wav$")
    r.ctx["voice_wav"] = r.rel(out)
    return sound(out) | {"score": (job.get("voice") or {}).get("score"), "_job": job}


@check("voice-dialogue", "short")
def c_dialogue(r: Run):
    if not (r.ctx.get("voice_state") or {}).get("available"):
        raise Skip("voice not available")
    job = r.job({"text": "<|speaker:0|> Did you see the light?\n<|speaker:1|> Only once, just before the storm.", "seed": 2, "live": False},
                path="/api/voice/takes")
    return sound(r.output(job, r"\.wav$")) | {"_job": job}


@check("voice-sheet", "short")
def c_sheet(r: Run):
    film, = need(r, "film")
    job = r.job({"sheet": {"id": "v1", "name": "Mara", "sampleLine": "We should not be here."}, "project": film["id"], "seed": 1},
                path="/api/voice/sheet")
    return sound(r.output(job, r"\.wav$")) | {"_job": job}


@check("music-10s", "short")
def c_music(r: Run):
    st = r.ctx.get("music_state") or {}
    if not st.get("available"):
        raise Skip(st.get("why") or "music not available")
    job = r.job({"caption": "a calm lo-fi piano loop", "lyrics": "", "seconds": 10, "steps": 8, "seed": 1, "live": False}, path="/api/music/takes")
    out = r.output(job, r"\.wav$")
    r.ctx["music_take"] = (job.get("music") or {}).get("id") or job["id"]
    return sound(out, 10, 2.0) | {"route": st.get("route"), "_job": job}


@check("music-soundtrack", "short")
def c_soundtrack(r: Run):
    take, video = need(r, "music_take", "clip")
    job = r.job({"take": take, "video": video, "music_db": -10}, path="/api/music/soundtrack")
    return clip(r.output(job, r"\.mp4$")) | {"_job": job}


@check("nodes-run", "short")
def c_nodes_run(r: Run):
    graph, = need(r, "graph")
    run_ = r.api.post("/api/nodes/run", {"graph": graph})
    start = time.time()
    while True:
        state = r.api.get(f"/api/nodes/runs/{run_['id']}")
        if state.get("status") in ("done", "failed", "cancelled"):
            break
        if time.time() - start > 900:
            r.api.call("POST", f"/api/nodes/runs/{run_['id']}/cancel", {})
            raise Fail("the node run ran over 900 s")
        time.sleep(2)
    nodes = state.get("nodes") or {}
    for node in (nodes.values() if isinstance(nodes, dict) else nodes):
        if node.get("job"):
            r.jobs.append(node["job"])
    if state.get("status") != "done":
        raise Fail(f"node run {state.get('status')}: {json.dumps(nodes, default=str)[:400]}")
    return {"seconds": round(time.time() - start, 1)}


@check("motion-analysis", "short")
def c_motion(r: Run):
    st = r.ctx.get("motion_state") or {}
    up_clip, = need(r, "up_clip")
    body = {"source": up_clip, "start": 0, "end": 3, "mode": "actor-motion", "layers": st.get("default_layers") or [], "fps": 12}
    job = r.api.post("/api/motion/jobs", body)
    start = time.time()
    while True:
        state = r.api.get(f"/api/motion/jobs/{job['id']}")
        if state.get("status") in ("done", "failed", "cancelled"):
            break
        if time.time() - start > 900:
            r.api.call("POST", f"/api/motion/jobs/{job['id']}/cancel", {})
            raise Fail("motion analysis ran over 900 s")
        time.sleep(2)
    if state.get("status") != "done":
        raise Fail(f"motion analysis {state.get('status')}: {state.get('error')}")
    pack = state.get("pack") or job["id"]
    r.packs.append(pack)
    r.ctx["pack"] = pack
    info = r.api.get(f"/api/motion/packs/{pack}")
    stages = {k: (v.get("seconds") if isinstance(v, dict) else v) for k, v in (state.get("stages") or {}).items()}
    files = json.dumps(info)
    missing = [layer for layer in body["layers"] if layer not in files]
    if missing:
        raise Fail(f"the pack lacks the layers {missing}: stages {stages}")
    return {"layers": body["layers"], "stages": stages, "seconds": round(time.time() - start, 1)}


@check("motion-reference", "short")
def c_motion_ref(r: Run):
    pack, = need(r, "pack")
    status, before = r.api.call("GET", "/api/motion/handoff")
    if status < 400 and before and before.get("clip"):
        raise Skip("a motion handoff is already set in the app: not overwritten by the check")
    made = r.api.post("/api/motion/reference", {"pack": pack, "layer": "depth", "mode": "camera-only", "seconds": 2, "width": 864, "height": 480})
    r.api.call("DELETE", "/api/motion/handoff")
    given = made["clip"]
    target = r.file(str(Path(given).relative_to(r.home)) if Path(given).is_absolute() else given)
    return clip(target, None, sound=False) | {"clip": made["clip"]}


# ----- realistic -----

@check("video-realistic-5s", "realistic", spark=SPARK_REFERENCE["video-realistic"])
def c_realistic(r: Run):
    """The Home page's Generate with its default Quick recipe: 5 s at 864x480, FastH3 4 passes."""
    composed = r.api.post("/api/compose", {"fields": {"scene": "A fisherman mends a net on a wooden pier at sunrise, gulls overhead, he hums to himself",
                                                      "action": "", "camera": "", "dialogue": [], "soundscape": "", "music": "N/A"},
                                           "first_frame": False, "last_frame": False, "seconds": 5})
    job = r.job({"kind": "video", "title": "studio-check realistic", "params": {"prompt": composed["prompt"], "image": None, "preset": "small",
                                                                            "quality": "fh4", "seed": 1, "seconds": 5}})
    out = r.output(job, r"\.mp4$")
    r.ctx["clip5"] = r.rel(out)
    return clip(out, 5.0, SMALL_SIZE) | {"engine": job.get("engine"), "shot": (job.get("shot") or {}).get("passed"), "_job": job}


@check("music-realistic-30s", "realistic", spark=SPARK_REFERENCE["music-realistic"])
def c_song(r: Run):
    st = r.ctx.get("music_state") or {}
    if not st.get("available"):
        raise Skip("music not available")
    job = r.job({"caption": "A warm, cinematic song with a clear lead vocal.", "lyrics": "[Verse]\nThe light is turning on the hill\n"
                 "The harbour sleeps, the sea is still\n[Chorus]\nCarry me home, carry me home\nOver the water, I'm never alone",
                 "seconds": 30, "seed": 1, "live": False}, path="/api/music/takes")
    return sound(r.output(job, r"\.wav$"), 30, 4.0) | {"_job": job}


@check("video-sharp-spec", "realistic")
def c_sharp(r: Run):
    """The Wasserman Sharp Spec: 1376x768, FastH3 8 passes, the QA gate after it (a failed gate retakes at the next seed)."""
    sharp = r.state.get("sharp")
    if not sharp:
        raise NotHere("the Sharp Spec preset is not offered here")
    seconds = min(sharp.get("seconds") or [8])
    job = r.job({"kind": "video", "title": "studio-check sharp", "params": {"prompt": "A woman in a red coat crosses a rainy street at night and looks back.",
                                                                        "image": None, "preset": "sharp", "quality": sharp.get("quality", "fh8"),
                                                                        "seed": sharp.get("seed", 42), "seconds": seconds}})
    qa = job.get("qa") or {}
    return clip(r.output(job, r"renders/[^/]+\.mp4$"), seconds, tuple(r.state["presets"]["sharp"]["size"]), tolerance=0.5) | {
        "qa": qa.get("passed"), "takes": len(qa.get("takes") or []), "_job": job}


# ----- sound last: its install may still be running -----

@check("foley", "sound")
def c_foley(r: Run):
    k = ((r.api.get("/api/sound/state").get("kinds") or {}).get("foley") or {})
    if not k.get("ready"):
        raise Skip(k.get("why") or "foley not ready")
    video = r.ctx.get("clip5") or r.ctx["up_clip"]  # foley takes 1 to 15 s of picture
    job = r.job({"kind": "foley", "title": "studio-check foley", "params": {"video": video, "steps": 10, "cfg": 4.5}})
    return clip(r.output(job, r"\.mp4$"), None) | {"wav": sound(r.output(job, r"\.wav$")), "_job": job}


@check("sfx", "sound")
def c_sfx(r: Run):
    k = ((r.api.get("/api/sound/state").get("kinds") or {}).get("sfx") or {})
    if not k.get("ready"):
        raise Skip(k.get("why") or "sfx not ready")
    job = r.job({"kind": "sfx", "title": "studio-check sfx", "params": {"prompt": "a heavy wooden door slams shut", "seconds": 2, "steps": 10, "cfg": 7}})
    return sound(r.output(job, r"\.wav$"), 2, 0.5) | {"_job": job}


def label_of(report: dict) -> str:
    host = urllib.parse.urlsplit(report.get("base", "")).hostname or "?"
    return f"{report.get('platform', '?')} {host}"


def merge(files: list[Path], out: Path) -> int:
    """One table of several machines' runs, a column each: PASS 12.3 s, FAIL, n/a (with the reasons below it)."""

    reports = [json.loads(f.read_text()) for f in files]
    labels = [label_of(r) for r in reports]
    names = list(dict.fromkeys(x["name"] for r in reports for x in r["results"]))
    by = [{x["name"]: x for x in r["results"]} for r in reports]
    tiers = {x["name"]: x["tier"] for r in reports for x in r["results"]}
    lines = ["| feature | tier | " + " | ".join(labels) + " |", "|---|---|" + "---|" * len(labels)]
    notes = []
    for name in names:
        cells = []
        for label, rows in zip(labels, by):
            x = rows.get(name)
            if x is None:
                cells.append("not run")
                continue
            took = x["job_seconds"] if x["job_seconds"] is not None else x["seconds"]
            cells.append(x["status"].lower() if x["status"] in ("N/A", "BLOCKED") else f"{x['status']} {took:g} s")
            if x["status"] != "PASS":
                notes.append(f"- **{name}** on {label}: {x['status']}: {str(x['error'])[:300]}")
        lines.append(f"| {name} | {tiers[name]} | " + " | ".join(cells) + " |")
    summary = ["| machine | version | pass | fail | n/a | blocked | minutes |", "|---|---|---|---|---|---|---|"] + [
        f"| {label} | {r.get('version')} | {r['counts'].get('PASS', 0)} | {r['counts'].get('FAIL', 0)} | {r['counts'].get('N/A', r['counts'].get('SKIP', 0))} | "
        f"{r['counts'].get('BLOCKED', 0)} | {r['seconds'] / 60:.1f} |" for label, r in zip(labels, reports)]
    text = "\n".join(summary + [""] + lines + ([""] + ["Not passed:"] + notes if notes else [])) + "\n"
    out.mkdir(parents=True, exist_ok=True)
    (out / "merged.md").write_text(text)
    (out / "merged.json").write_text(json.dumps({"machines": labels, "features": {n: {l: rows.get(n) for l, rows in zip(labels, by)} for n in names}},
                                                indent=1, default=str))
    print(text + f"\n{out / 'merged.md'}")
    return 1 if any(r["counts"].get("FAIL") for r in reports) else 0


def several(bases: list[str], args) -> int:
    """Each machine in its own process at once (each has its own queue), then the side-by-side table."""

    out = Path(args.out or f"/tmp/studio-check-{time.strftime('%m%d-%H%M%S')}")
    passed = ["--tier", args.tier] + (["--only", args.only] if args.only else []) + (["--skip", args.skip] if args.skip else []) + (
        ["--keep"] if args.keep else [])
    procs, files = [], []
    for base in bases:
        folder = out / re.sub(r"[^\w.-]+", "_", urllib.parse.urlsplit(base).netloc or base)
        folder.mkdir(parents=True, exist_ok=True)
        log = open(folder / "run.log", "w")
        procs.append((subprocess.Popen([sys.executable, __file__, "--base", base, "--out", str(folder)] + passed, stdout=log, stderr=subprocess.STDOUT), log))
        files.append(folder / "report.json")
        print(f"{base}: {folder / 'run.log'}")
    for proc, log in procs:
        proc.wait()
        log.close()
    return merge([f for f in files if f.is_file()], out)


TIERS = {"api": ("api",), "short": ("api", "short"), "all": ("api", "short", "realistic", "sound")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", action="append", help="the app's address (default http://127.0.0.1:7870); give it twice or more "
                        "to check several machines at once, each in its own process, and get one side-by-side table")
    parser.add_argument("--merge", nargs="+", metavar="REPORT", help="side-by-side table of earlier runs' report.json files, then stop")
    parser.add_argument("--tier", choices=TIERS, default="all")
    parser.add_argument("--only", default="", help="comma-separated name prefixes")
    parser.add_argument("--skip", default="", help="comma-separated name prefixes")
    parser.add_argument("--out", default="")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--clean", metavar="REPORT", help="clean up what an earlier --keep run made (its report.json), then stop")
    args = parser.parse_args()
    bases = args.base or [os.environ.get("STUDIO_URL", "http://127.0.0.1:7870")]
    if args.merge:
        return merge([Path(p) for p in args.merge], Path(args.out or "."))
    if len(bases) > 1:
        return several(bases, args)
    args.base = bases[0]
    if args.list:
        for name, tier, _, fn in CHECKS:
            print(f"{tier:9} {name}")
        return 0
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            print(f"{tool} is needed (brew install ffmpeg)")
            return 2
    if args.clean:
        made = json.loads(Path(args.clean).read_text()).get("made") or {}
        r = Run(Api(args.base), Path(tempfile.mkdtemp()), False)
        r.jobs, r.paths, r.slate = made.get("jobs", []), made.get("paths", []), made.get("slate", [])
        r.projects, r.packs = made.get("projects", []), made.get("packs", [])
        r.clean()
        return 0
    out = Path(args.out or f"/tmp/studio-check-{time.strftime('%m%d-%H%M%S')}")
    out.mkdir(parents=True, exist_ok=True)
    r = Run(Api(args.base), out, args.keep)
    only = [p for p in args.only.split(",") if p]
    skip = [p for p in args.skip.split(",") if p]
    started = time.time()
    print(f"{args.base}  version {r.state.get('version')}  platform {r.state.get('platform')}  home {r.home}\n", flush=True)
    try:
        for name, tier, spark, fn in CHECKS:
            if tier not in TIERS[args.tier]:
                continue
            # fixtures and the states feed every other check: they always run
            if name not in ("fixtures", "module-states", "state") and ((only and not any(name.startswith(p) for p in only)) or any(name.startswith(p) for p in skip)):
                continue
            t0 = time.time()
            try:
                detail = fn(r) or {}
                job = detail.pop("_job", None) if isinstance(detail, dict) else None
                r.record(name, tier, "PASS", time.time() - t0, detail, job_seconds=(job or {}).get("elapsed"), spark=spark)
            except NotHere as why:
                r.record(name, tier, "N/A", time.time() - t0, error=str(why))
            except Blocked as why:
                r.record(name, tier, "BLOCKED", time.time() - t0, error=str(why))
            except Fail as why:
                r.record(name, tier, "FAIL", time.time() - t0, error=str(why), spark=spark)
            except Exception as error:  # noqa: BLE001 - a broken check is a failure, the run goes on
                r.record(name, tier, "FAIL", time.time() - t0, error=f"{type(error).__name__}: {error} @ {traceback.format_exc().strip().splitlines()[-3][:160]}")
    finally:
        took = time.time() - started
        counts = {s: sum(1 for x in r.results if x["status"] == s) for s in ("PASS", "FAIL", "N/A", "BLOCKED")}
        (out / "report.json").write_text(json.dumps({"base": args.base, "version": r.state.get("version"), "platform": r.platform,
                                                     "label": r.state.get("platform_label"), "seconds": took,
                                                     "counts": counts, "results": r.results,
                                                     "made": {"jobs": r.jobs, "paths": r.paths, "slate": r.slate, "projects": r.projects,
                                                              "packs": r.packs}}, indent=1, default=str))
        lines = ["| check | tier | result | wall s | job s | Spark s | detail / error |", "|---|---|---|---|---|---|---|"]
        for x in r.results:
            text = (x["error"] or json.dumps(x["detail"], default=str))[:220].replace("|", "/")
            lines.append(f"| {x['name']} | {x['tier']} | {x['status']} | {x['seconds']} | {x['job_seconds'] or ''} | {x['spark_seconds'] or ''} | {text} |")
        (out / "report.md").write_text("\n".join(lines) + "\n")
        print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['N/A']} n/a, {counts['BLOCKED']} blocked in {took:.0f} s; {out}/report.md", flush=True)
        r.clean()
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
