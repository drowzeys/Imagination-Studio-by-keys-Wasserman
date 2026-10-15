"""Wasserman Slate AI Studio inside the Production tab.

Slate, the prompt studio for AI filmmaking, is by Sam Wasserman (wassermanproductions.com), Apache-2.0; its NOTICE
is kept at app/static/slate/NOTICE and its data files (coverage plans, setups, model profiles) beside it, unchanged.
This module is the server half of a port of its workflow: project files in Slate's own shape, its "brain" (your
Claude Code or Codex sign-in, or a local OpenAI-compatible server), and what Slate leaves to the user: a compile of
a shot to MiniMax H3's prompt form and the plan of the render that the Studio's queue then runs.
"""

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

import lengths  # which frame counts the model takes, beside this file
import remote  # another machine's Studio app, read over its own API, beside this file
import negation  # what a prompt forbids by name, beside this file

router = APIRouter()
HOME: Path = Path()
DURATIONS: dict = {}
compose = compose_references = None  # the Studio's own prompt builders, set by attach()
ready = lambda: {}  # which optional parts this install has
room = lambda plan, lora: {"fits": True, "reason": ""}  # whether a render plan fits this machine's memory, set by attach()
LIMIT = 9
TIMEOUT = 420
CLI_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local/bin"), str(Path.home() / ".claude/local"))
TIER_MODEL = {"fast": "haiku", "standard": "sonnet", "top": None}  # top: the sign-in's own default model
# the output for a shot's aspect ratio, per resolution tier: a preset where one exists, else a custom size
SIZES = {"480p": {"16:9": "small", "9:16": (480, 864), "1:1": (640, 640), "4:3": (640, 480), "3:4": (480, 640), "21:9": (1120, 480)},
         "720p": {"16:9": "p720", "9:16": (704, 1280), "1:1": (960, 960), "4:3": (1088, 832), "3:4": (832, 1088), "21:9": (1536, 672)},
         "sharp": {"16:9": "sharp"}}  # the Wasserman Sharp Spec is one size
SIZE_WORDS = {"EWS": "Extreme wide shot", "WS": "Wide shot", "MWS": "Medium wide shot", "MS": "Medium shot", "MCU": "Medium close-up",
              "CU": "Close-up", "ECU": "Extreme close-up", "Insert": "Insert shot of a detail"}
ANGLE_WORDS = {"eye level": "at eye level", "low": "from a low angle", "high": "from a high angle", "overhead": "from directly overhead",
               "dutch": "with a tilted, dutch-angle horizon", "over-shoulder": "over the shoulder of the nearer person", "POV": "from the character's point of view"}
# Slate's movement words in the camera vocabulary of MiniMax's H3 prompt guide
MOVES = {"locked": "Static shot on a locked, still camera.", "handheld": "Handheld: the camera shakes slightly.",
         "push-in": "The camera pushes in with small amplitude at slow speed.", "pull-back": "The camera zooms out with small amplitude at slow speed.",
         "orbit": "Arc shot: the camera arcs around the subject at slow speed.", "pan": "The camera pans with small amplitude at slow speed.",
         "tilt": "The camera tilts with small amplitude at slow speed.", "track": "Tracking shot: the camera follows the subject at its pace.",
         "crane": "The camera pedestals up with large amplitude at slow speed.", "drone": "Aerial tracking shot at slow speed.",
         "steadicam": "Smooth tracking shot gliding with the subject."}
SECTIONS = ("subject", "composition", "lighting", "camera", "style", "mood")


def attach(home: Path, durations: dict, plain, referenced, limit: int, parts, fits=None) -> APIRouter:
    global HOME, DURATIONS, compose, compose_references, LIMIT, ready, room
    HOME, DURATIONS, compose, compose_references, LIMIT, ready = home, durations, plain, referenced, limit, parts
    room = fits or room
    (HOME / "slate").mkdir(parents=True, exist_ok=True)
    return router


# ---------- project files, in Slate's own shape ----------

def project_file(project_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", project_id):
        raise HTTPException(400, "bad project id")
    return HOME / "slate" / project_id / "project.json"


def new_id() -> str:
    return f"p-{int(time.time() * 1000):x}-{uuid.uuid4().hex[:6]}"


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def blank(name: str) -> dict:
    return {"id": new_id(), "name": name, "logline": "", "world": "",
            "defaults": {"aspectRatio": "16:9", "fps": 24, "durationSec": 8, "targetModel": "minimax-h3", "brain": "claude",
                         "localEndpoint": "", "localModel": ""},
            "scenes": [], "characters": [], "artDept": [], "locations": [], "lookbook": [], "references": [], "mySetups": [],
            "music": [], "voices": [], "copilot": [], "createdAt": now(), "updatedAt": now()}


def write(data: dict) -> dict:
    file = project_file(data["id"])
    file.parent.mkdir(parents=True, exist_ok=True)
    data["updatedAt"] = now()
    scratch = file.with_suffix(".tmp")
    scratch.write_text(json.dumps(data, indent=1))
    scratch.replace(file)
    return data


@router.get("/api/slate/projects")
def list_projects() -> list[dict]:
    out = []
    for file in sorted((HOME / "slate").glob("*/project.json"), key=lambda f: -f.stat().st_mtime):
        try:
            data = json.loads(file.read_text())
        except ValueError:
            continue
        scenes = data.get("scenes") or []
        out.append({"id": data.get("id", file.parent.name), "name": data.get("name", ""), "logline": data.get("logline", ""),
                    "updatedAt": data.get("updatedAt", ""), "sceneCount": len(scenes),
                    "shotCount": sum(len(scene.get("shots") or []) for scene in scenes)})
    return out


@router.post("/api/slate/projects")
def create_project(body: dict) -> dict:
    """A new film, or (with "project") a project.json made by Slate itself, taken in as it is."""

    given = body.get("project")
    if given is None:
        return write(blank((body.get("name") or "Untitled film").strip()))
    if not isinstance(given, dict) or not isinstance(given.get("scenes"), list):
        raise HTTPException(400, "this is not a Slate project.json")
    data = {**blank(given.get("name") or "Imported film"), **given}
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(data.get("id", ""))) or project_file(data["id"]).is_file():
        data["id"] = new_id()
    return write(data)


@router.get("/api/slate/projects/{project_id}")
def get_project(project_id: str) -> dict:
    file = project_file(project_id)
    if not file.is_file():
        raise HTTPException(404, "no such film")
    return json.loads(file.read_text())


@router.put("/api/slate/projects/{project_id}")
def save_project(project_id: str, body: dict) -> dict:
    if not project_file(project_id).is_file():
        raise HTTPException(404, "no such film")
    body["id"] = project_id
    return {"updatedAt": write(body)["updatedAt"]}


@router.delete("/api/slate/projects/{project_id}")
def remove_project(project_id: str) -> dict:
    """Moved to the studio's trash folder, not erased."""

    folder = project_file(project_id).parent
    if not folder.is_dir():
        raise HTTPException(404, "no such film")
    (HOME / "trash").mkdir(exist_ok=True)
    shutil.move(str(folder), str(HOME / "trash" / f"slate-{project_id}-{int(time.time())}"))
    return {"ok": True}


@router.post("/api/slate/exists")
def exists(body: dict) -> dict:
    """Which of these studio files are on disk: takes are logged when queued, their clips land later."""

    out = {}
    for path in (body.get("paths") or [])[:500]:
        target = (HOME / str(path)).resolve()
        out[path] = HOME.resolve() in target.parents and target.is_file()
    return out


# ---------- a take rendered somewhere else, logged into a shot ----------

SETTINGS = ("preset", "quality", "frames", "seconds", "width", "height", "steps", "loras", "references", "ref_turbo", "text_encoder")


@router.post("/api/slate/takes")
def import_take(body: dict) -> dict:
    """A finished clip as a take of a shot: its file copied into the film's takes folder, with its seed and settings.

    The film is on one machine and its clips are often rendered on others, each by its own app. The clip is named by
    ``from`` (that app's address) with ``job`` (one of its finished jobs: the clip, prompt, seed and settings are
    read from it) or ``path`` (a file of its studio folder), or by ``path`` alone for a file already in this studio
    folder (an upload). ``seed``, ``prompt``, ``settings``, ``notes`` and ``rating`` say what the job does not.
    Returns {"take"}: the page adds it to the shot; with ``log`` true it is written into the project file here, for
    a script that drives the app."""

    file = project_file(str(body.get("projectId") or ""))
    if not file.is_file():
        raise HTTPException(404, "no such film")
    project = json.loads(file.read_text())
    wanted = str(body.get("shot") or "").strip()
    found = [(scene, shot) for scene in project.get("scenes") or [] for shot in scene.get("shots") or []
             if shot.get("id") == wanted or (shot.get("name") or "").strip().lower() == wanted.lower()]
    if len(found) != 1:
        raise HTTPException(404 if not found else 400, "no such shot" if not found else f"{len(found)} shots are called {wanted}: give the shot's id")
    scene, shot = found[0]
    path, seed, prompt = str(body.get("path") or "").strip(), body.get("seed"), body.get("prompt")
    settings, source, theirs = dict(body.get("settings") or {}), {}, {}
    try:
        if body.get("from"):
            base = remote.address(str(body["from"]))
            source = {"from": base}
            if body.get("job"):
                theirs = remote.job(base, str(body["job"]))
                if theirs.get("status") != "done" or not theirs.get("outputs"):
                    raise HTTPException(400, f"job {body['job']} on {base} is {theirs.get('status')}: there is no finished clip to import")
                asked = theirs.get("params") or {}
                path = path or theirs["outputs"][0]
                seed = seed if seed not in (None, "") else asked.get("seed")
                prompt = prompt or asked.get("prompt")
                settings = {**{key: asked[key] for key in SETTINGS if asked.get(key) not in (None, "", [])}, **settings}
                source.update(job=theirs.get("id"), engine=theirs.get("engine"), elapsed=theirs.get("elapsed"))
            if not path:
                raise HTTPException(400, "give the clip: one of that app's job ids, or the path of a file in its studio folder")
            suffix = Path(path).suffix.lower()
            target = file.parent / "takes" / f"{shot['id']}-{int(time.time() * 1000):x}{suffix}"
            remote.fetch(base, path, target)
            source["path"] = path
        else:
            given = (HOME / path).resolve() if path else None
            if given is None or HOME.resolve() not in given.parents or not given.is_file():
                raise HTTPException(400, "give the clip: another app's address with a job id or a path, or the path of a file in this studio folder")
            if given.suffix.lower() not in remote.SUFFIXES[:2]:
                raise HTTPException(400, "a take is a video file (.mp4 or .mov)")
            target = file.parent / "takes" / f"{shot['id']}-{int(time.time() * 1000):x}{given.suffix.lower()}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(given, target)
            source = {"path": path}
    except ValueError as error:
        raise HTTPException(400, str(error))
    except LookupError as error:
        raise HTTPException(404, str(error))
    except OSError as error:
        raise HTTPException(502, f"the other app did not answer: {str(error)[-200:]}")
    try:
        seed = int(seed) if seed not in (None, "") else None
    except (TypeError, ValueError):
        raise HTTPException(400, "a seed is a whole number")
    rating = body.get("rating") if body.get("rating") in ("circled", "good", "no-good") else "good"
    take = {"id": f"take-{int(time.time() * 1000):x}-{uuid.uuid4().hex[:5]}", "loggedAt": now(), "model": "minimax-h3",
            "prompt": prompt or (shot.get("tf") or {}).get("h3") or "", "rating": rating, "notes": str(body.get("notes") or ""),
            "mediaPath": str(target.resolve().relative_to(HOME.resolve())), "jobId": None, "seed": seed, "settings": settings,
            "source": source, "imported": True}
    for key in ("qa", "shot"):  # what that app's checks said of the clip, kept beside it
        if theirs.get(key):
            take[key] = theirs[key]
    if body.get("log"):
        shot.setdefault("takes", []).insert(0, take)
        write(project)
    return {"take": take, "scene": scene.get("id"), "shot": shot.get("id"), "logged": bool(body.get("log"))}


# ---------- the brain: Slate's three backends ----------

def cli(name: str) -> str | None:
    return shutil.which(name, path=os.pathsep.join([os.environ.get("PATH", ""), *CLI_DIRS]))


def endpoint_of(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url if not url or url.endswith("/v1") else url + "/v1"


def local_models(endpoint: str) -> list[str] | None:
    try:
        with urllib.request.urlopen(endpoint + "/models", timeout=2) as answer:
            return [m.get("id") for m in json.loads(answer.read()).get("data", []) if m.get("id")]
    except Exception:
        return None


def find_local(given: str = "") -> tuple[str | None, list[str]]:
    for endpoint in ([endpoint_of(given)] if given.strip() else [f"http://127.0.0.1:{port}/v1" for port in (11434, 1234, 8000, 8080)]):
        models = local_models(endpoint)
        if models is not None:
            return endpoint, models
    return None, []


def extract_json(text: str):
    """The first complete JSON value in a reply that may carry fences or a preamble (Slate's extractJson)."""

    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        if start == -1:
            continue
        depth, quoted, escaped = 0, False, False
        for at in range(start, len(cleaned)):
            ch = cleaned[at]
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = not quoted
            elif not quoted and ch == opener:
                depth += 1
            elif not quoted and ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[start:at + 1])
                    except ValueError:
                        break
    raise ValueError("no valid JSON in the reply")


def ask(backend: str, system: str, prompt: str, tier: str, endpoint: str = "", model: str = "") -> str:
    if backend == "local":
        base, models = find_local(endpoint)
        if not base:
            raise RuntimeError("no local model server answers: start Ollama, LM Studio, vLLM or llama.cpp, or set its address in the Bible")
        body = json.dumps({"model": model or (models[0] if models else ""), "temperature": 0.7,
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}).encode()
        request = urllib.request.Request(base + "/chat/completions", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            return json.loads(answer.read())["choices"][0]["message"]["content"]
    tool = cli(backend if backend in ("claude", "codex") else "claude")
    if not tool:
        raise RuntimeError(f"{backend} is not installed on this machine: install it and sign in, or pick another brain in the Bible")
    env = {**os.environ, "PATH": os.pathsep.join([os.environ.get("PATH", ""), *CLI_DIRS])}
    with tempfile.TemporaryDirectory() as scratch:  # an empty folder: the brain writes text, it has no files to act on
        if backend == "codex":
            last = Path(scratch) / "last.txt"
            done = subprocess.run([tool, "exec", "--skip-git-repo-check", "--output-last-message", str(last), "-"],
                                  input=f"{system}\n\n---\n\n{prompt}", capture_output=True, text=True, timeout=TIMEOUT, cwd=scratch, env=env)
            if last.is_file() and last.read_text().strip():
                return last.read_text().strip()
            raise RuntimeError((done.stderr or done.stdout or "codex gave no answer").strip()[-400:])
        args = [tool, "-p", "--output-format", "json", "--tools", "", "--append-system-prompt", system]
        if TIER_MODEL.get(tier):
            args += ["--model", TIER_MODEL[tier]]
        done = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=TIMEOUT, cwd=scratch, env=env)
    raw = done.stdout.strip()
    try:
        parsed = json.loads(raw)
    except ValueError:
        if raw:
            return raw
        raise RuntimeError((done.stderr or "Claude Code gave no answer").strip()[-400:])
    result = parsed.get("result") if isinstance(parsed, dict) else None
    if isinstance(parsed, dict) and parsed.get("is_error"):
        message = result if isinstance(result, str) else "Claude Code returned an error"
        if re.search(r"authenticat|oauth|401|logged? ?in|revoked", message, re.I):
            message = "Claude Code's sign-in has expired: run `claude auth login` in a terminal on this machine, then retry. " + message
        raise RuntimeError(message)
    return result if isinstance(result, str) else raw


@router.get("/api/slate/brain")
def brain_status(endpoint: str = "") -> dict:
    base, models = find_local(endpoint)
    return {"claude": bool(cli("claude")), "codex": bool(cli("codex")), "local": bool(base), "endpoint": base, "models": models}


@router.post("/api/slate/brain")
def brain(body: dict) -> dict:
    started = time.time()
    try:
        text = ask(body.get("backend") or "claude", body.get("system") or "", body.get("prompt") or "", body.get("tier") or "top",
                   body.get("localEndpoint") or "", body.get("localModel") or "")
        out = {"ok": True, "text": text}
        if body.get("expectJson"):
            out["json"] = extract_json(text)
    except subprocess.TimeoutExpired:
        out = {"ok": False, "text": "", "error": f"the brain did not answer in {TIMEOUT // 60} minutes"}
    except Exception as error:  # the reason goes to the page as it is
        out = {"ok": False, "text": locals().get("text", ""), "error": str(error)[:600]}
    return {**out, "elapsedMs": int(1000 * (time.time() - started))}


# ---------- a shot, compiled to MiniMax H3's prompt form and to a render ----------

def sections(shot: dict) -> dict:
    """The sectioned prompt as {section: text}, muted lines left out; text before the first heading is Subject."""

    muted, out, at = set(shot.get("mutedLines") or []), {name: [] for name in SECTIONS}, "subject"
    for number, line in enumerate((shot.get("prompt") or "").split("\n"), start=1):
        heading = re.fullmatch(r"#+\s*(\w+)\s*:?\s*", line.strip())
        if heading and heading.group(1).lower() in SECTIONS:
            at = heading.group(1).lower()
        elif line.strip() and number not in muted:
            out[at].append(line.strip())
    return {name: sentence(" ".join(lines)) for name, lines in out.items()}


def sentence(text: str) -> str:
    text = " ".join((text or "").split())
    return text if not text or text[-1] in ".!?\"”" else text + "."


def person(sheet: dict) -> str:
    bits = [f"{sheet.get('age')} years old" if str(sheet.get("age") or "").strip().isdigit() else sheet.get("age"), sheet.get("gender"),
            sheet.get("ethnicity"), sheet.get("faceFeatures"), sheet.get("hair") and "hair: " + sheet["hair"],
            sheet.get("clothing") and "wearing " + sheet["clothing"]]
    return ", ".join(bit.strip().rstrip(".") for bit in bits if bit and bit.strip())


def voice_of(sheet: dict | None, fallback: str = "") -> str:
    if not sheet:
        return fallback.strip() or "a natural voice"
    sound = ", ".join(bit.strip().rstrip(".") for bit in (sheet.get(key, "") for key in ("accent", "timbre", "pitch", "pacing", "texture")) if bit and bit.strip())
    who = (sheet.get("ageGender") or "").strip().rstrip(".")
    if who:
        return f"the voice of {who}" + (f" ({sound})" if sound else "")
    return f"a voice that is {sound}" if sound else "a natural voice"


def cast_voices(lines: list[dict], sheets: list[dict | None], warnings: list[str]) -> tuple[list[str], list[str]]:
    """The voices cast with Fish Audio Drama 3 on the Sound tab (a sheet's ``drama`` take, voice.py) that go into a
    shot opening on cast stills as reference audio, ``<Audio N>``: the voice timbre of that speaker's lines. MiniMax's
    limits hold: each clip 2 to 15 s, 15 s and 3 clips in all; what does not fit is said and left out."""

    audios, notes, total = [], [], 0.0
    for line, sheet in zip(lines, sheets):
        take = (sheet or {}).get("drama") or {}
        path, name = take.get("path"), (sheet or {}).get("name") or "a voice"
        if not path or take.get("use") is False or path in audios:
            continue
        if not (HOME / path).is_file():
            warnings.append(f"The cast voice of {name} is no longer on disk: cast it again on the Sound tab.")
            continue
        try:
            seconds = float(take.get("seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0.0
        if not 1.95 <= seconds <= 15.05:
            warnings.append(f"The cast voice of {name} is {seconds:.1f} s and a reference audio clip is 2 to 15 s: cut it on the Sound tab. Left out.")
            continue
        if len(audios) >= 3 or total + seconds > 15.05:
            warnings.append(f"The cast voice of {name} is left out: the reference audio clips are at most 3 and 15 s in all.")
            continue
        audios.append(path)
        total += seconds
        notes.append(f"the voice timbre reference for <Subject {int(line.get('subject') or 1)}>'s voice, containing {line['voice']}")
    return audios, notes


def find_shot(project: dict, shot_id: str) -> tuple[dict, dict, int]:
    for scene in project.get("scenes") or []:
        for index, shot in enumerate(scene.get("shots") or []):
            if shot.get("id") == shot_id:
                return scene, shot, index
    raise HTTPException(404, "no such shot")


def best_take(shot: dict) -> str | None:
    """The clip that stands for a shot: its circled take, else its newest one that is not marked no-good."""

    takes = [take for take in shot.get("takes") or [] if take.get("mediaPath")]
    for take in takes:
        if take.get("rating") == "circled":
            return take["mediaPath"]
    return next((take["mediaPath"] for take in takes if take.get("rating") != "no-good"), None)


# ---------- a song under the film: tempo, bars, and each shot's slot ----------

def song_of(project: dict) -> dict | None:
    """The film's song as the bar grid needs it, or None when it has none: ``bpm``, ``beats`` in a bar, ``first``
    (seconds to the first beat of bar 1) and ``bar`` (seconds). Kept under the project's "tf": {"song": {"name",
    "bpm", "beatsPerBar", "firstBeat"}}; the tempo is the user's (from a beat tracker or the score), not measured here."""

    song = (project.get("tf") or {}).get("song") or {}
    try:
        bpm, beats, first = float(song.get("bpm") or 0), int(song.get("beatsPerBar") or 4), float(song.get("firstBeat") or 0)
    except (TypeError, ValueError):
        return None
    if not 20 <= bpm <= 400 or not 1 <= beats <= 16:
        return None
    return {"name": str(song.get("name") or ""), "bpm": bpm, "beats": beats, "first": first, "bar": beats * 60.0 / bpm}


def shot_frames(project: dict, shot: dict, song: dict | None) -> tuple[int, float | None]:
    """(frames a shot renders, its slot in seconds or None): with a song and a slot in bars, the shortest length the
    model takes that covers the slot (any 17n + 5 count, lengths.py), so the cut can sit on the bar line; else the
    shot's own seconds brought to the nearest count."""

    try:
        bars = float((shot.get("tf") or {}).get("bars") or 0)
    except (TypeError, ValueError):
        bars = 0
    if song and bars > 0:
        slot = bars * song["bar"]
        need = math.ceil(slot * lengths.FPS - 1e-6)
        return next((count for count in lengths.counts() if count >= need), lengths.LONGEST), slot
    asked = float((shot.get("spec") or {}).get("durationSec") or project.get("defaults", {}).get("durationSec") or 8)
    return lengths.nearest(asked), None


def slot_of(project: dict, shot_id: str) -> dict | None:
    """Where a shot sits in the song: {"bars", "seconds", "frames" (the slot's own, at 24 a second), "start" (seconds
    into the song), "bar" (the bar it starts on, counted from 1), "spare" (frames the clip has beyond its slot, to trim)}.
    The shots are laid end to end in the film's order; one without a slot takes its clip's length."""

    song = song_of(project)
    if not song:
        return None
    at = song["first"]
    for scene in project.get("scenes") or []:
        for shot in scene.get("shots") or []:
            frames, slot = shot_frames(project, shot, song)
            if shot.get("id") == shot_id:
                if slot is None:
                    return None
                own = round(slot * lengths.FPS)
                return {"bars": float(shot["tf"]["bars"]), "seconds": round(slot, 3), "frames": own, "start": round(at, 3),
                        "bar": round((at - song["first"]) / song["bar"] + 1, 2), "spare": frames - own}
            at += slot if slot is not None else frames / lengths.FPS
    return None


def compile_shot(project: dict, shot_id: str, chain: str | None = None) -> dict:
    scene, shot, index = find_shot(project, shot_id)
    extra, film, parts = shot.get("tf") or {}, project.get("tf") or {}, ready()
    spec, part, warnings = shot.get("spec") or {}, sections(shot), []
    by_id = lambda items: {item.get("id"): item for item in project.get(items) or []}
    cast = [by_id("characters")[i] for i in extra.get("cast") or [] if i in by_id("characters")]
    place = by_id("locations").get(extra.get("location"))
    start = extra.get("start") or "text"

    # any length the model takes (lengths.py): the shot's own seconds, brought to the nearest count of 17n + 5 frames
    asked = float(spec.get("durationSec") or project.get("defaults", {}).get("durationSec") or 8)
    song = song_of(project)
    frames, slot_seconds = shot_frames(project, shot, song)
    slot = slot_of(project, shot_id) if slot_seconds is not None else None
    seconds = next((named for named, count in DURATIONS.items() if count == frames), round(frames / lengths.FPS, 3))
    if extra.get("bars") and not song:
        warnings.append("This shot has a slot in bars and the film has no song: set the tempo in the Bible, or the shot renders at its seconds.")
    if slot and slot["spare"] < 0:
        warnings.append(f"A slot of {slot['bars']:g} bars is {slot['seconds']:g} s, longer than a clip ({lengths.LONGEST / lengths.FPS:.1f} s): the clip ends {-slot['spare']} frames early. Split the slot.")
    if not slot and spec.get("durationSec") and abs(frames / lengths.FPS - asked) > 0.5:
        warnings.append(f"A clip is {lengths.SHORTEST / lengths.FPS:.1f} to {lengths.LONGEST / lengths.FPS:.0f} s long: the shot is {asked:g} s and renders as {lengths.says(frames)}.")
    if spec.get("fps") and int(spec["fps"]) != 24:
        warnings.append(f"MiniMax H3 renders at 24 fps; the shot says {spec['fps']}.")
    tier = film.get("res") if film.get("res") in SIZES else "480p"
    ratio = spec.get("aspectRatio") or "16:9"
    if ratio not in SIZES[tier]:
        warnings.append(f"Aspect ratio {ratio} is not one this engine has a size for; rendering 16:9.")
        ratio = "16:9"
    size = SIZES[tier][ratio]

    references, subjects = [], []
    if start == "cast":
        for sheet in cast:
            stills = [p for p in sheet.get("images") or [] if (HOME / p).is_file()]
            if stills and len(references) < LIMIT:
                references.append(stills[0])
                subjects.append(f"{sheet.get('name', 'the person')}, {person(sheet)}".rstrip(", "))
        if not parts.get("ref2va"):
            warnings.append("Reference pictures need the Ref2VA model (oneshot-setup.sh --ref2va); rendering from text.")
            references, subjects = [], []
        elif not references:
            warnings.append("No one cast in this shot has a still pinned in Casting; rendering from text.")
        if [s for s in cast if not [p for p in s.get("images") or [] if (HOME / p).is_file()]] and references:
            warnings.append("Cast without a pinned still are described in words only.")
    image = previous = None
    if start == "image":
        image = extra.get("image") if extra.get("image") and (HOME / extra["image"]).is_file() else None
        if not image:
            warnings.append("No first-frame image is set; rendering from text.")
    if start == "previous":
        before = scene["shots"][index - 1] if index else None
        previous = before and (best_take(before) or chain)  # chain: the clip the shot before is queued to write
        if not before:
            warnings.append("The first shot of a scene has nothing before it; rendering from text.")
        elif not previous:
            warnings.append(f"“{before.get('name')}” has no take yet: render it first, or use “Render the scene” to queue them in order.")

    # the picture: who is in it and where (Slate repeats bible descriptions in every prompt), then the sections
    named = [f"{sheet.get('name')} is {person(sheet)}." for sheet in cast if sheet.get("name") and person(sheet) and not references]
    where = place and sentence(", ".join(bit for bit in (
        f"{place.get('name')}: {place.get('description') or ''}".strip(": "), place.get("timeOfDay"), place.get("weather"),
        place.get("architecture"), place.get("textures"), place.get("practicalLights") and "lit by " + place["practicalLights"]) if bit))
    look = part["style"] or next((sentence("; ".join(bit for bit in (s.get("tone"), s.get("palette"), s.get("lighting")) if bit))
                                  for s in project.get("lookbook") or [] if s.get("tone") or s.get("palette")), "")
    picture = " ".join(bit for bit in (look, *named, where or "", part["subject"], part["composition"], part["lighting"], part["mood"]) if bit)
    framing = " ".join(bit for bit in (SIZE_WORDS.get(spec.get("size") or ""), ANGLE_WORDS.get(spec.get("angle") or ""),
                                        spec.get("lens") and f"on a {spec['lens']} lens") if bit)
    camera = " ".join(bit for bit in (sentence(framing), MOVES.get(spec.get("movement") or "", ""), part["camera"]) if bit)
    beats = [b for b in shot.get("beatSheet") or [] if (b.get("text") or "").strip()]
    lead = ["First,", "Then,", "After that,", "Next,", "Finally,"]
    # What happens is the Subject section's, or the beat sheet's when there is one. The shot's intent is a note for
    # people (what the shot is for, where it sits in the song): it used to stand in as the action when there were no
    # beats, and a production run's timecodes and "the audience sees the collision coming" went to the model.
    action = " ".join(f"{lead[min(i, 3)] if i < len(beats) - 1 or i == 0 else lead[4]} {sentence(b['text'][0].lower() + b['text'][1:])}"
                      for i, b in enumerate(beats))
    voices, order, lines, sheets = by_id("voices"), [sheet.get("id") for sheet in cast], [], []
    for line in extra.get("lines") or []:
        if not (line.get("text") or "").strip():
            continue
        who = by_id("characters").get(line.get("characterId"))
        sheet = voices.get(line.get("voiceId")) or next((v for v in voices.values() if who and v.get("characterId") == who.get("id")), None)
        lines.append({"voice": voice_of(sheet, line.get("voice") or ""), "text": line["text"].strip(), "language": line.get("language") or "English",
                      "sung": bool(line.get("sung")), "speaker": who.get("name") if who else "",
                      "subject": order.index(who["id"]) + 1 if who and references and who.get("id") in order[:len(references)] else 1})
        sheets.append(sheet)
    audios, notes = cast_voices(lines, sheets, warnings) if references else ([], [])
    words = sum(len(line["text"].split()) for line in lines)
    if lines and words > seconds * 3.4:
        warnings.append(f"{words} words is a lot for {seconds} s (about three words a second): the line may be rushed or cut off.")
    cue = by_id("music").get(extra.get("cue"))
    music = sentence(", ".join(bit for bit in (cue.get("genre"), cue.get("mood"), cue.get("tempo"), cue.get("instrumentation")) if bit)) if cue else (extra.get("music") or "N/A")
    fields = {"scene": picture, "camera": camera, "action": action, "dialogue": lines, "soundscape": extra.get("soundscape") or "",
              "music": music, "subjects": subjects, "people": bool(cast) or None,  # None: the builder reads the words
              **({"audio_notes": notes} if audios else {})}
    prompt = compose_references(fields, len(references)) if references else compose(fields, bool(image or previous), frames=frames)
    for one in negation.check_fields(fields)["negations"]:  # said, never rewritten (negation.py)
        warnings.append(f"“{one['phrase']}” ({one['field'].lower()}): the video model has no negative prompt, and what a prompt names tends to appear. Say what is there instead.")
    limit = 7000
    if len(prompt) > limit:
        warnings.append(f"The prompt is {len(prompt)} characters; MiniMax H3's guide suggests at most {limit}. Tighten the shot.")
    if not (shot.get("prompt") or "").strip():
        warnings.append("The shot has no prompt yet.")
    # a LoRA for this shot alone (the hub's, as a job carries them): it stands in for the film's on this shot
    own = [{"id": str(one["id"]), **({"strength": one["strength"]} if one.get("strength") not in (None, "") else {}),
            **({"style": one["style"]} if one.get("style") else {})} for one in extra.get("loras") or [] if isinstance(one, dict) and one.get("id")]
    plan = {"seconds": seconds, "frames": frames, "seed": int(extra.get("seed") or 1), "quality": film.get("quality") or "fh8",
            **({"slot": slot} if slot else {}), **({"loras": own} if own and not references else {}),
            **({"preset": size} if isinstance(size, str) else {"preset": "custom", "width": size[0], "height": size[1]}),
            **({"references": references, "ref_turbo": film.get("refPasses") == "4"} if references else {}),
            **({"reference_audios": audios} if audios else {}),
            **({"image": image} if image else {}), **({"first_frame_from": previous} if previous else {})}
    mode = "references" if references else "image" if image else "previous" if previous else "text"
    if own and references:
        warnings.append("A shot that opens on cast stills renders on the reference model: its own LoRA is left out.")
    # the machine's memory (a DGX Spark's guard): said here, before the shot is queued, with what fits
    try:
        memory = room(plan, bool(plan.get("loras") or (film.get("loras") and not references)))
    except Exception:  # noqa: BLE001 - a plan the queue will refuse for another reason
        memory = {"fits": True, "reason": ""}
    if not memory.get("fits", True):
        warnings.append("This shot will be refused when queued: " + memory["reason"])
    return {"prompt": prompt, "plan": plan, "mode": mode, "warnings": warnings, "chars": len(prompt), "memory": {"fits": memory.get("fits", True), "reason": memory.get("reason", ""), "need_mb": memory.get("need_mb"), "nearest": memory.get("nearest")}}


@router.post("/api/slate/compile")
def compile_endpoint(body: dict) -> dict:
    project = body.get("project") or get_project(body.get("projectId", ""))
    return compile_shot(project, body.get("shotId", ""), body.get("chain"))
