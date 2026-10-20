#!/usr/bin/env python3
"""The agent benchmark: briefs a filmmaker would type into the Agent room, run one at a time against a test copy of the
Studio, each scored against a spec of what must exist afterwards.

    python3 scripts/agent-bench/bench.py --endpoint http://127.0.0.1:8091 --label bolt-default
    python3 scripts/agent-bench/bench.py --only pic-one,clip-test --env STUDIO_LOCAL_AGENT=baseline
    python3 scripts/agent-bench/bench.py --real --only pic-one        # renders for real (takes ~/agent-gpu.lock.d)
    python3 scripts/agent-bench/bench.py --summary runs/*/results.jsonl  # the table of runs already made

For each brief the Studio (dry_studio.py: the real app, jobs recorded and never rendered; or app/server.py with
--real) starts on --port with a fresh STUDIO_HOME, the brief's fixtures are made (Library files, a Slate film, a
finished song), the brief goes to the Agent room (POST /api/agent/threads with the Studio's own MCP tools and
--backend: local is the Studio's local harness on --endpoint, claude or codex their CLIs), and when the turn ends the
queue, Slate, the Editor and the turn's record are read and scored:

  complete  every job the spec asks for exists (its kind and count), and the films and timeline it asks for
  exact     complete, and every setting, name and count is right, and nothing else was queued or made
  honest    the final answer names no file that does not exist, and names what was made (a path or a job id)
  bad       malformed tool calls (no such tool, arguments that are not JSON, missing required arguments)
  seconds   wall time of the turn

Only the Python standard library is needed to run it (the Studio runs in its own interpreter, --python). Results go
to --out/<label>/results.jsonl, one line per brief, with the turn's stream.jsonl and messages beside them.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
LOCK = Path.home() / "agent-gpu.lock.d"
SCOUT_SIZES = {"draft": (1344, 768), "2k": (1024, 576), "qhd": (1280, 736), "small": (864, 480), "native": (1344, 768),
               "p720": (1280, 736), "p960": (864, 480), "sharp": (1376, 768)}
# what the Studio uses when a setting is not given (server.py, music.py, sound.py), so a spec may name the default
DEFAULTS = {"scout": {"count": 4, "image_model": "qwen", "preset": "2k"}, "edit": {"image_model": "qwen"},
            "video": {"quality": "fh4", "preset": "draft"}, "studio": {"quality": "fh4", "preset": "draft"},
            "long": {"quality": "fh4", "preset": "draft", "segment_seconds": 15}, "sfx": {"seconds": 6}, "music": {"seconds": 60}}
FILE = re.compile(r"(?<![\w/.-])(?:~|/)?[\w][\w./-]*\.(?:png|jpe?g|webp|mp4|mov|wav|mp3|zip)\b", re.I)
NEON_RAIN = {"name": "Neon Rain", "logline": "A courier with a stolen katana runs the city's rooftops in the rain.", "scenes": [
    {"name": "Alley", "synopsis": "The courier is cornered in a neon alley.", "shots": [
        {"name": "Shot 01", "prompt": "# Subject\nA wide view of a rain-soaked alley under neon signs, steam rising from vents.", "seconds": 5},
        {"name": "Shot 02", "prompt": "# Subject\nThe courier spins to face two pursuers, rain flying off her coat.", "seconds": 5}]}]}


# ---------- the Studio under test ----------

def http(base: str, method: str, path: str, body=None, timeout: float = 60):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return json.loads(answer.read() or b"null")


class Studio:
    def __init__(self, args, home: Path, extra_env: dict, port: int):
        self.base = f"http://127.0.0.1:{port}"
        self.home = home
        env = dict(os.environ, STUDIO_HOME=str(home), PORT=str(port), HOST="127.0.0.1", STUDIO_LOCAL_ENDPOINT=args.endpoint,
                   BENCH_APP_ROOT=str(Path(args.app).resolve()), **extra_env)
        env.pop("STUDIO_PIDFILE", None)
        script = [str(Path(args.app).resolve() / "app/server.py")] if args.real else [str(HERE / "dry_studio.py")]
        self.log = open(home.parent / f"{home.name}.studio.log", "w")
        self.process = subprocess.Popen([args.python, *script], env=env, stdout=self.log, stderr=subprocess.STDOUT,
                                        cwd=str(Path(args.app).resolve()), start_new_session=True)
        ends = time.time() + 90
        while time.time() < ends:
            try:
                http(self.base, "GET", "/api/state", timeout=5)
                return
            except (urllib.error.URLError, OSError, ValueError):
                if self.process.poll() is not None:
                    break
                time.sleep(0.5)
        self.close()
        raise RuntimeError(f"the Studio did not start: see {self.log.name}")

    def get(self, path: str):
        return http(self.base, "GET", path)

    def post(self, path: str, body: dict):
        return http(self.base, "POST", path, body)

    def close(self):
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.log.close()


def media(path: Path) -> None:
    """A small real file for a fixture: a grey picture, a three-second clip with a tone, a second of sound."""

    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    args = {".png": ["-f", "lavfi", "-i", "testsrc2=s=1024x576", "-frames:v", "1"],
            ".jpg": ["-f", "lavfi", "-i", "testsrc2=s=1024x576", "-frames:v", "1"],
            ".mp4": ["-f", "lavfi", "-i", "testsrc2=s=864x480:d=3:r=24", "-f", "lavfi", "-i", "sine=f=330:d=3",
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"],
            ".wav": ["-f", "lavfi", "-i", "sine=f=330:d=3"]}[suffix]
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(path)], check=True)


def wait_jobs(studio: Studio, ids: list[str], seconds: float = 120) -> None:
    ends = time.time() + seconds
    while time.time() < ends:
        if all(studio.get(f"/api/jobs/{i}")["status"] in ("done", "failed", "cancelled") for i in ids):
            return
        time.sleep(0.5)


def mcp_module(base: str):
    spec = importlib.util.spec_from_file_location("studio_mcp_bench", REPO / "mcp/studio-mcp.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.URL = base  # this brief's Studio (briefs may run side by side)
    return module


def fixtures(studio: Studio, brief: dict) -> dict:
    """The brief's fixtures, made before the agent starts: {songs: {job id: title}, films: [ids], jobs: [ids]}."""

    made: dict = {"songs": {}, "films": [], "jobs": []}
    wanted = brief.get("fixtures") or {}
    for path in wanted.get("library") or []:
        media(studio.home / path)
    for song in wanted.get("songs") or []:
        job = studio.post("/api/music/takes", song)
        made["songs"][job["id"]] = song["title"]
        made["jobs"].append(job["id"])
    if made["jobs"]:
        wait_jobs(studio, made["jobs"], 900)
    if wanted.get("film") == "neon-rain":
        tools = mcp_module(studio.base)
        film = tools.run("slate_new_film", {"name": NEON_RAIN["name"], "logline": NEON_RAIN["logline"]})
        for scene in NEON_RAIN["scenes"]:
            tools.run("slate_add_scene", {"film": film["film"], "name": scene["name"], "synopsis": scene["synopsis"]})
            for shot in scene["shots"]:
                tools.run("slate_add_shot", {"film": film["film"], "scene": scene["name"], **shot})
        made["films"].append(film["film"])
    return made


# ---------- one brief ----------

def run_brief(args, brief: dict, run_dir: Path, extra_env: dict, port: int) -> dict:
    home = run_dir / "homes" / brief["id"]
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True)
    env = dict(extra_env)
    if (brief.get("fixtures") or {}).get("job_seconds"):
        env["BENCH_JOB_SECONDS"] = str(brief["fixtures"]["job_seconds"])
    studio = Studio(args, home, env, port)
    try:
        fixed = fixtures(studio, brief)
        started = time.time()
        sent = studio.post("/api/agent/threads", {"text": brief["brief"], "tools": ["studio"], "backend": args.backend})
        thread_id, task_id = sent["thread"]["id"], sent["task"]["id"]
        while time.time() - started < args.timeout:
            time.sleep(1)
            thread = studio.get(f"/api/agent/threads/{thread_id}")
            if not thread.get("live") or thread["live"].get("status") not in ("queued", "running"):
                break
            if studio.get(f"/api/agent/tasks/{task_id}").get("status") not in ("queued", "running"):
                break  # the turn ended though its thread still shows it live
        else:
            studio.post(f"/api/agent/tasks/{task_id}/cancel", {})
        seconds = time.time() - started
        folder = home / "agent" / task_id
        task = json.loads((folder / "task.json").read_text()) if (folder / "task.json").is_file() else {}
        jobs = [j for j in studio.get("/api/jobs") if j["id"] not in fixed["jobs"]]
        for job in studio.get("/api/motion/jobs") or []:
            jobs.append({**job, "kind": "motion", "outputs": [f"motion/{job['pack']}.zip"] if job.get("pack") else []})
        films = [studio.get(f"/api/slate/projects/{f['id']}") for f in studio.get("/api/slate/projects")]
        projects = [studio.get(f"/api/projects/{p['id']}") for p in studio.get("/api/projects")]
        keep = run_dir / "turns" / brief["id"]
        keep.mkdir(parents=True, exist_ok=True)
        for name in ("stream.jsonl", "messages.json", "log.txt", "task.json"):
            if (folder / name).is_file():
                shutil.copyfile(folder / name, keep / name)
        reply = task.get("reply") or ""
        scored = score(brief["spec"], jobs, films, projects, fixed, reply, home)
        calls = task.get("calls") or {}
        return {"id": brief["id"], "feature": brief["feature"], "status": task.get("status"), "error": task.get("error"),
                "limit": task.get("limit"), "seconds": round(seconds, 1), "answers": calls.get("answers"), "calls": calls.get("made"),
                "bad": calls.get("bad"), "failed": calls.get("failed"), **scored, "reply": reply, "fixed": fixed,
                "jobs": [{k: j.get(k) for k in ("id", "kind", "status", "title", "outputs")} | {"params": slim(j.get("params") or {})} for j in jobs]}
    finally:
        studio.close()


def slim(params: dict) -> dict:
    return {k: (v[:4000] if isinstance(v, str) else v) for k, v in params.items() if k not in ("clips", "planning", "pose", "layers", "stages")}


# ---------- scoring ----------

def text_of(job: dict) -> str:
    params = job.get("params") or {}
    return " ".join(str(params.get(k) or "") for k in ("prompt", "text", "caption", "lyrics", "image_prompt", "beats")) + " " + str(job.get("title") or "")


def value_of(job: dict, key: str, fixed: dict):
    params, kind = job.get("params") or {}, job.get("kind")
    if key == "size" and kind == "scout":
        if params.get("width") and params.get("height"):
            return f"{params['width']}x{params['height']}"
        width, height = SCOUT_SIZES.get(params.get("preset") or "2k", (1024, 576))
        return f"{width}x{height}"
    if key == "seconds" and kind in ("video", "studio") and params.get("seconds") in (None, "") and params.get("frames"):
        return float(params["frames"]) / 24
    if key == "song_title":
        return fixed["songs"].get(params.get("take"))
    if key in ("image", "video", "source", "path") and isinstance(params.get(key), str):
        return params[key].lstrip("/")
    if params.get(key) in (None, ""):
        return (DEFAULTS.get(kind) or {}).get(key)
    return params[key]


def check_value(got, want) -> bool:
    """want: a value (strings case aside, numbers within 1e-6), or {in: [...]}, {min}, {max}, {approx, tol},
    {set: [...]} (the same items in any order), {len: n}, {has: [...]} (text containing each), {absent: true}."""

    if isinstance(want, dict):
        if "absent" in want:
            return got in (None, "", [])
        if "in" in want:
            return any(check_value(got, one) for one in want["in"])
        if "set" in want:
            return isinstance(got, list) and sorted(str(g).lstrip("/").lower() for g in got) == sorted(str(w).lower() for w in want["set"])
        if "len" in want:
            return isinstance(got, list) and len(got) == want["len"]
        if "has" in want:
            return isinstance(got, str) and all(w.lower() in got.lower() for w in want["has"])
        try:
            number = float(got)
        except (TypeError, ValueError):
            return False
        if "approx" in want:
            return abs(number - want["approx"]) <= want.get("tol", 0.5)
        return number >= want.get("min", float("-inf")) and number <= want.get("max", float("inf"))
    if want is None:
        return got in (None, "")
    if isinstance(want, (int, float)) and not isinstance(want, bool):
        try:
            return abs(float(got) - want) < 1e-6
        except (TypeError, ValueError):
            return False
    return str(got).strip().lower() == str(want).strip().lower()


def named_in(name: str, items: list) -> list:
    return [i for i in items if (i.get("name") or "").strip().lower() == name.strip().lower()]


def score(spec: dict, jobs: list, films: list, projects: list, fixed: dict, reply: str, home: Path) -> dict:
    problems, missing = [], []
    live = [j for j in jobs if j.get("status") != "cancelled"]
    shots = {shot["id"]: shot.get("name") for film in films for scene in film.get("scenes") or [] for shot in scene.get("shots") or []}
    scout_outputs = {o for j in jobs if j.get("kind") == "scout" for o in j.get("outputs") or []}
    used: set = set()
    for matcher in spec.get("jobs") or []:
        kinds = matcher["kind"] if isinstance(matcher["kind"], list) else [matcher["kind"]]
        wanted_status = matcher.get("status")
        pool = [j for j in (jobs if wanted_status else live) if j.get("kind") in kinds and j["id"] not in used
                and (not wanted_status or j.get("status") == wanted_status)]
        loose = [j for j in pool if all(w.lower() in text_of(j).lower() for w in matcher.get("prompt_has") or [])] or pool

        def faults(job: dict) -> list[str]:
            out = [f"{job['kind']}.{key}={value_of(job, key, fixed)!r} not {want!r}" for key, want in (matcher.get("params") or {}).items()
                   if not check_value(value_of(job, key, fixed), want)]
            out += [f"{job['kind']} prompt lacks {w!r}" for w in matcher.get("prompt_has") or [] if w.lower() not in text_of(job).lower()]
            params = job.get("params") or {}
            if matcher.get("image_from") and str(params.get("image") or "").lstrip("/") not in scout_outputs:
                out.append(f"{job['kind']} does not open on the picture made for it (image={params.get('image')!r})")
            if matcher.get("slate_shot"):
                shot_id = (params.get("slate") or {}).get("shot")
                if shot_id not in shots or (matcher["slate_shot"] != "*" and (shots[shot_id] or "").lower() != matcher["slate_shot"].lower()):
                    out.append(f"{job['kind']} is not a take of Slate shot {matcher['slate_shot']}")
            return out
        ranked = sorted(loose, key=lambda j: len(faults(j)))[:matcher["n"]]
        if len(ranked) < matcher["n"]:
            missing.append(f"{matcher['n'] - len(ranked)} {'/'.join(kinds)} job(s)" + (f" with {matcher['prompt_has']}" if matcher.get("prompt_has") else ""))
        for job in ranked:
            used.add(job["id"])
            problems += faults(job)
    extra = [j for j in live if j["id"] not in used]
    problems += [f"extra {j['kind']} job: {j.get('title')}" for j in extra]

    fixture_films = set(fixed["films"])
    new_films = [f for f in films if f["id"] not in fixture_films]
    wanted_new = [f for f in spec.get("films") or [] if f.get("new")]
    if len(new_films) > len(wanted_new):
        problems.append(f"{len(new_films) - len(wanted_new)} extra Slate film(s)")
    taken: set = set()
    for want in spec.get("films") or []:
        pool = [f for f in (new_films if want.get("new") else films) if f["id"] not in taken]
        if want.get("name"):
            pool = named_in(want["name"], pool) or ([] if not want.get("new") else [p for p in pool if want["name"].lower() in (p.get("name") or "").lower()])
            if want.get("new") and pool and not named_in(want["name"], pool):
                problems.append(f"the film is not named {want['name']!r}")
        if not pool:
            missing.append(f"Slate film {want.get('name') or '(new)'}")
            continue
        film = pool[0]
        taken.add(film["id"])
        scenes = film.get("scenes") or []
        if "scenes" in want and len(scenes) != want["scenes"]:
            (missing if len(scenes) < want["scenes"] else problems).append(f"{film.get('name')}: {len(scenes)} scenes, not {want['scenes']}")
        count = sum(len(s.get("shots") or []) for s in scenes)
        if "shots" in want and count != want["shots"]:
            (missing if count < want["shots"] else problems).append(f"{film.get('name')}: {count} shots, not {want['shots']}")
        if want.get("scene_names"):
            names = sorted((s.get("name") or "").strip().lower() for s in scenes)
            if names != sorted(n.lower() for n in want["scene_names"]):
                problems.append(f"{film.get('name')}: scenes {names}, not {want['scene_names']}")
                if len(scenes) < len(want["scene_names"]):
                    missing.append(f"{film.get('name')}: scenes {want['scene_names']}")
            for name, n in zip(want["scene_names"], want.get("shots_per_scene") or []):
                scene = named_in(name, scenes)
                got = len(scene[0].get("shots") or []) if scene else 0
                if got != n:
                    (missing if got < n else problems).append(f"{film.get('name')}/{name}: {got} shots, not {n}")
        if want.get("shot"):
            found = [shot for s in scenes for shot in s.get("shots") or [] if (shot.get("name") or "").strip().lower() == want["shot"]["name"].lower()]
            if not found:
                missing.append(f"shot {want['shot']['name']!r}")
            elif "seconds" in want["shot"] and not check_value((found[0].get("spec") or {}).get("durationSec"), want["shot"]["seconds"]):
                problems.append(f"shot {want['shot']['name']!r} is {(found[0].get('spec') or {}).get('durationSec')} s")
    if spec.get("timeline"):
        lines = [[str(c.get("path") or "").lstrip("/") for c in p.get("timeline") or []] for p in projects]
        if spec["timeline"] not in lines:
            (problems if any(lines) else missing).append(f"no timeline holds {spec['timeline']} (timelines: {lines})")
    for words in spec.get("reply_has") or []:
        if words.lower() not in reply.lower():
            problems.append(f"the answer does not say {words!r}")

    # honesty: every file the answer names exists; what was made is named (a path, a file name or the job's id)
    invented, names = [], None
    for found in FILE.findall(reply):
        name = found.replace(str(home) + "/", "").lstrip("~/")
        if "/" not in name:  # a bare file name ("each has a sheet.jpg"): one of that name somewhere in the studio folder
            names = names if names is not None else {p.name for p in home.rglob("*") if p.is_file()}
            if not any(n == name or n.endswith("_" + name) for n in names):  # "scout_s1.png through s4.png"
                invented.append(found)
        elif not ((home / name).exists() or Path(os.path.expanduser(found)).exists()):
            invented.append(found)
    unreported = []
    for job in live:
        if job.get("kind") == "qa":
            continue  # its output is the clip that was named in the brief
        marks = [job["id"], *(job.get("outputs") or []), *[Path(o).name for o in job.get("outputs") or []],
                 *{str(Path(o).parent) for o in job.get("outputs") or [] if Path(o).parent.name == job["id"]}]
        if not any(m and m.lower() in reply.lower() for m in marks):
            unreported.append(f"{job['kind']} {job['id']}")
    for film in new_films:
        if (film.get("name") or "").lower() not in reply.lower() and film["id"] not in reply:
            unreported.append(f"film {film.get('name')}")
    complete = not missing
    return {"complete": complete, "exact": complete and not problems, "honest": not invented and not unreported,
            "missing": missing, "problems": problems, "invented": invented, "unreported": unreported}


# ---------- runs and the table ----------

DOWN = re.compile(r"no local model server answers|the local model server did not answer|said 50[234]")


def serving(endpoint: str, seconds: float = 1800) -> None:
    """Wait (up to half an hour) until the model server lists its models."""

    base = endpoint.rstrip("/")
    base = base if base.endswith("/v1") else base + "/v1"
    ends = time.time() + seconds
    while time.time() < ends:
        try:
            if http(base, "GET", "/models", timeout=10):
                return
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(15)


def gpu_lock(name: str) -> None:
    while True:
        try:
            LOCK.mkdir()
            (LOCK / "owner").write_text(f"{name} pid {os.getpid()} {time.ctime()}\n")
            return
        except FileExistsError:
            time.sleep(20)


def gpu_unlock() -> None:
    try:
        if f"pid {os.getpid()} " in (LOCK / "owner").read_text():
            shutil.rmtree(LOCK, ignore_errors=True)
    except OSError:
        pass


def summarize(rows: list[dict]) -> dict:
    n = len(rows) or 1
    seconds = [r["seconds"] for r in rows if r.get("seconds") is not None]
    return {"briefs": len(rows), "exact": sum(bool(r.get("exact")) for r in rows), "complete": sum(bool(r.get("complete")) for r in rows),
            "honest": sum(bool(r.get("honest")) for r in rows), "bad_calls": sum(r.get("bad") or 0 for r in rows),
            "tool_calls": sum(r.get("calls") or 0 for r in rows), "errors": sum(1 for r in rows if r.get("status") not in ("done",)),
            "mean_s": round(sum(seconds) / len(seconds), 1) if seconds else None,
            "median_s": round(statistics.median(seconds), 1) if seconds else None, "exact_pct": round(100 * sum(bool(r.get("exact")) for r in rows) / n)}


def table(paths: list[str]) -> str:
    lines = ["| run | briefs | exact | complete | honest | bad calls | tool calls | failed turns | mean s | median s |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for path in paths:
        rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
        s = summarize(rows)
        lines.append(f"| {Path(path).parent.name} | {s['briefs']} | {s['exact']} ({s['exact_pct']}%) | {s['complete']} | {s['honest']} | "
                     f"{s['bad_calls']} | {s['tool_calls']} | {s['errors']} | {s['mean_s']} | {s['median_s']} |")
    return "\n".join(lines)


def rescore(run_dir: Path, briefs: dict) -> None:
    """Score a run again from what it kept (each row's jobs, and the films and timelines in its studio folders), after
    a change to the scoring: results.jsonl is rewritten, the old one kept as results.jsonl.old."""

    results = run_dir / "results.jsonl"
    rows = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
    for row in rows:
        home = run_dir / "homes" / row["id"]
        if row["id"] not in briefs or "jobs" not in row or not home.is_dir():
            continue
        films = [json.loads(f.read_text()) for f in sorted(home.glob("slate/*/project.json"))]
        projects = [json.loads(f.read_text()) for f in sorted(home.glob("projects/*/project.json"))]
        fixed = row.get("fixed") or {"jobs": [], "films": [f["id"] for f in films if f.get("name") == NEON_RAIN["name"]
                                                            and (briefs[row["id"]].get("fixtures") or {}).get("film")],
                                     "songs": {json.loads(t.read_text())["id"]: json.loads(t.read_text()).get("title")
                                               for t in home.glob("music/*.take.json")}}
        row.update(score(briefs[row["id"]]["spec"], row["jobs"], films, projects, fixed, row.get("reply") or "", home))
    shutil.copyfile(results, run_dir / "results.jsonl.old")
    results.write_text("".join(json.dumps(row) + "\n" for row in rows))
    print(table([str(results)]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--endpoint", default=os.environ.get("STUDIO_LOCAL_ENDPOINT", "http://127.0.0.1:8091"))
    parser.add_argument("--backend", default="local", help="local (the Studio's local harness), claude or codex")
    parser.add_argument("--app", default=str(REPO), help="the Studio checkout under test (default: this one)")
    parser.add_argument("--python", default=str(Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio")) / "venv/bin/python"))
    parser.add_argument("--port", type=int, default=7896, help="the first port; --jobs N uses N from here")
    parser.add_argument("--jobs", type=int, default=1, help="briefs at once, for a model server with that many slots (wall times then overlap)")
    parser.add_argument("--label", default=time.strftime("run-%m%d-%H%M%S"))
    parser.add_argument("--out", default=str(Path.home() / "agent-bench-runs"))
    parser.add_argument("--briefs", default=str(HERE / "briefs.json"))
    parser.add_argument("--only", default="", help="brief ids, comma separated")
    parser.add_argument("--env", action="append", default=[], help="KEY=VALUE for the Studio under test (e.g. STUDIO_LOCAL_AGENT=...)")
    parser.add_argument("--real", action="store_true", help="render for real (the 'real' briefs unless --only), under the GPU lock")
    parser.add_argument("--timeout", type=float, default=1800, help="seconds one brief may take")
    parser.add_argument("--summary", nargs="*", help="print the table of these results.jsonl files and stop")
    parser.add_argument("--rescore", nargs="*", help="score these run folders again from what they kept, and stop")
    args = parser.parse_args()
    if args.summary is not None:
        print(table(args.summary))
        return
    if args.rescore is not None:
        briefs_ = {b["id"]: b for b in json.loads(Path(args.briefs).read_text())["briefs"]}
        for folder in args.rescore:
            rescore(Path(folder).expanduser(), briefs_)
        return
    if not Path(args.python).is_file():
        args.python = sys.executable
    data = json.loads(Path(args.briefs).read_text())
    chosen = [i for i in args.only.split(",") if i] or (data.get("real") if args.real else [])
    briefs = [b for b in data["briefs"] if not chosen or b["id"] in chosen]
    extra_env = dict(item.split("=", 1) for item in args.env)
    run_dir = Path(args.out).expanduser() / args.label
    run_dir.mkdir(parents=True, exist_ok=True)
    results = run_dir / "results.jsonl"
    done = {json.loads(line)["id"] for line in results.read_text().splitlines() if line.strip()} if results.is_file() else set()
    (run_dir / "run.json").write_text(json.dumps({"endpoint": args.endpoint, "backend": args.backend, "app": args.app, "env": extra_env,
                                                  "real": args.real, "started": time.ctime()}, indent=1))
    import queue
    import threading
    ports, writing = queue.Queue(), threading.Lock()
    for i in range(max(1, args.jobs)):
        ports.put(args.port + i)

    def one(brief: dict) -> None:
        port = ports.get()
        try:
            for attempt in range(3):  # a model server that was down or restarting is waited for, and the brief run again
                serving(args.endpoint)
                if args.real:
                    gpu_lock(f"agent-bench {args.label}")
                try:
                    row = run_brief(args, brief, run_dir, extra_env, port)
                except Exception as error:  # noqa: BLE001  (one brief's trouble is its own row)
                    row = {"id": brief["id"], "feature": brief["feature"], "status": "bench-error", "error": str(error)[:500], "complete": False,
                           "exact": False, "honest": False, "seconds": None}
                finally:
                    if args.real:
                        gpu_unlock()
                if not DOWN.search(str(row.get("error") or "")):
                    break
                row["retried"] = attempt + 1
        finally:
            ports.put(port)
        row["endpoint"] = args.endpoint
        with writing:
            with results.open("a") as out:
                out.write(json.dumps(row) + "\n")
            mark = "EXACT" if row.get("exact") else "complete" if row.get("complete") else "FAIL"
            print(f"{brief['id']:<20} {mark:<8} honest={row.get('honest')} bad={row.get('bad')} {row.get('seconds')}s "
                  f"{'; '.join((row.get('missing') or []) + (row.get('problems') or []))[:300]}", flush=True)

    waiting = [b for b in briefs if b["id"] not in done]  # a run that was stopped carries on where it was
    if args.jobs > 1 and not args.real:  # a server with several slots: briefs side by side, each Studio on its own port
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(args.jobs) as pool:
            list(pool.map(one, waiting))
    else:
        for brief in waiting:
            one(brief)
    print(table([str(results)]))


if __name__ == "__main__":
    main()
