"""Wasserman Slate AI Studio inside the Production tab.

Slate, the prompt studio for AI filmmaking, is by Sam Wasserman (wassermanproductions.com), Apache-2.0; its NOTICE
is kept at app/static/slate/NOTICE and its data files (coverage plans, setups, model profiles) beside it, unchanged.
This module is the server half of a port of its workflow: project files in Slate's own shape, its "brain" (your
Claude Code or Codex sign-in, or a local OpenAI-compatible server), and what Slate leaves to the user: a compile of
a shot to MiniMax H3's prompt form and the plan of the render that the Studio's queue then runs.
"""

import ipaddress
import json
import math
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
import wave
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

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


def attach(home: Path, durations: dict, plain, referenced, limit: int, parts, fits=None, within=None) -> APIRouter:
    """``within(path)``: the server's ``inside``, a path under the studio folder or 400."""

    global HOME, DURATIONS, compose, compose_references, LIMIT, ready, room, inside
    HOME, DURATIONS, compose, compose_references, LIMIT, ready = home, durations, plain, referenced, limit, parts
    room, inside = fits or room, within or inside
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
    stamp = time.time()  # to the millisecond: a save is refused when the film changed after it was loaded (save_project)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(stamp)) + f".{int(stamp * 1000) % 1000:03d}Z"


AUTO = "auto"  # a film's brain that follows the Studio's own (Brains & tools: NVIDIA Bolt when its server answers)


def blank(name: str) -> dict:
    return {"id": new_id(), "name": name, "logline": "", "world": "",
            "defaults": {"aspectRatio": "16:9", "fps": 24, "durationSec": 8, "targetModel": "minimax-h3", "brain": AUTO,
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
    """The film as the page has it. A body that carries the ``updatedAt`` it was loaded with, older than the file's
    (a render, the Director, the night shift or the suite wrote it since), is refused: 409, and the page loads it again."""

    file = project_file(project_id)
    if not file.is_file():
        raise HTTPException(404, "no such film")
    if body.get("updatedAt"):
        try:
            kept = json.loads(file.read_text()).get("updatedAt") or ""
        except ValueError:
            kept = ""
        if str(body["updatedAt"]) < str(kept):
            raise HTTPException(409, "the film changed on the server since it was loaded")
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
            remote.remember(base, body.get("key"))
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
            if any(part.startswith(".") for part in given.relative_to(HOME.resolve()).parts):
                raise HTTPException(400, "hidden files of the studio folder are not takes")  # /files never serves one;
            # copying it into a film's takes folder would launder it into something the page can download
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


# ---------- a voice kept from a take: the sound of a rendered shot as a character's cast voice ----------

VOICE_SECONDS = (2.0, 15.0)  # a reference audio clip for MiniMax's Ref2VA, as cast_voices takes it


def take_sound(source: Path, target: Path) -> float:
    """A take's sound as a mono 44.1 kHz 16-bit WAV of at most 15 s (the first 15 s of a longer one); its seconds."""

    ffmpeg = cli("ffmpeg")
    if not ffmpeg:
        raise HTTPException(400, "keeping a voice from a take needs ffmpeg on this machine")
    target.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le",
                           "-t", f"{VOICE_SECONDS[1]:g}", str(target)], capture_output=True, text=True, timeout=120)
    if done.returncode or not target.is_file():
        target.unlink(missing_ok=True)
        raise HTTPException(400, "this take has no sound to keep" if "does not contain any stream" in done.stderr or "matches no streams" in done.stderr
                            else "the take's sound could not be read: " + done.stderr.strip()[-200:])
    with wave.open(str(target)) as sound:
        return sound.getnframes() / float(sound.getframerate() or 1)


@router.post("/api/slate/voice-from-take")
def voice_from_take(body: dict) -> dict:
    """A take's sound kept as a character's voice: {project, shot, take, character}. The take's audio (its first 15 s)
    is written to voice/<id>.wav with a .take.json as a Drama 3 take has (origin "take"), and the character's voice
    sheet (a new one named after the character when there is none) gets it as its cast voice (``drama``), so the
    shots after it in Drama 3 dialogue use it as the timbre reference. Returns the film."""

    file = project_file(str(body.get("project") or ""))
    if not file.is_file():
        raise HTTPException(404, "no such film")
    project = json.loads(file.read_text())
    scene, shot, _ = find_shot(project, str(body.get("shot") or ""))
    take = next((t for t in shot.get("takes") or [] if t.get("id") == body.get("take")), None)
    if not take or not take.get("mediaPath"):
        raise HTTPException(404, "no such take")
    source = (HOME / take["mediaPath"]).resolve()
    if HOME.resolve() not in source.parents or not source.is_file():
        raise HTTPException(400, "the take's clip is not on disk")
    who = next((c for c in project.get("characters") or [] if c.get("id") == body.get("character")), None)
    if not who:
        raise HTTPException(404, "no such character")
    take_id = time.strftime("%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    wav = HOME / "voice" / f"{take_id}.wav"
    seconds = take_sound(source, wav)
    if seconds < VOICE_SECONDS[0] - 0.05:
        wav.unlink(missing_ok=True)
        raise HTTPException(400, f"the take's sound is {seconds:.1f} s and a voice to keep is at least {VOICE_SECONDS[0]:g} s: keep a longer take")
    voices = project.setdefault("voices", [])
    sheet = next((v for v in voices if v.get("characterId") == who.get("id")), None)
    if sheet is None:
        sheet = {"id": f"voice-{int(time.time() * 1000):x}-{uuid.uuid4().hex[:5]}", "name": who.get("name") or "Voice", "characterId": who.get("id"),
                 "ageGender": "", "accent": "", "timbre": "", "pitch": "", "pacing": "", "energy": "", "texture": "", "emotionalRange": "",
                 "sampleLine": "", "notes": ""}
        voices.append(sheet)
    words = " ".join((line.get("text") or "").strip() for line in (shot.get("tf") or {}).get("lines") or []
                     if line.get("characterId") == who.get("id") and (line.get("text") or "").strip())
    path = str(wav.relative_to(HOME))
    record = {"id": take_id, "path": path, "title": f"Kept from a take: {who.get('name') or 'voice'}"[:80], "text": words, "seed": take.get("seed"),
              "backend": "take", "created": time.time(), "duration": round(seconds, 3), "origin": "take",
              "source": {"take": take.get("id"), "clip": take["mediaPath"], "shot": shot.get("id"), "scene": scene.get("id")},
              "reference": None, "slate": {"project": project.get("id"), "voice": sheet["id"]}}
    wav.with_suffix(".take.json").write_text(json.dumps(record, indent=1) + "\n")
    sheet["drama"] = {"path": path, "seconds": round(seconds, 3), "text": words, "seed": take.get("seed"), "take": take_id, "use": True, "origin": "take"}
    return write(project)


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
    import bolt  # NVIDIA Nemotron 3.5 Bolt as the Studio's own engine (bolt.py): its address is bolt.ENDPOINT

    if bolt.serves(given):
        return bolt.ENDPOINT, [bolt.SERVED]
    for endpoint in ([endpoint_of(given)] if given.strip() else [f"http://127.0.0.1:{port}/v1" for port in (11434, 1234, 8000, 8080)]):
        models = local_models(endpoint)
        if models is not None:
            return endpoint, models
    return None, []


def extract_json(text: str):
    """The first complete JSON value in a reply that may carry fences, a preamble or a model's thinking (Slate's
    extractJson). With a ``</think>`` in it, the text after the last one is read first: a reply with thinking off can
    still splice a think tag into its answer and start the answer again after it. A reply cut off inside its JSON has
    none, and says so: a value nested inside the broken one is never taken for the answer."""

    cleaned = re.sub(r"```(?:json)?", "", text)
    after = cleaned.rsplit("</think>", 1)[-1] if "</think>" in cleaned else None
    for part in ([after] if after is not None else []) + [cleaned]:
        found = first_json(part.strip())
        if found is not None:
            return found[0]
    raise ValueError("no valid JSON in the reply")


def first_json(cleaned: str):
    """(value,) of the first complete JSON object (else list) in ``cleaned``, or None."""

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
                        return (json.loads(cleaned[start:at + 1]),)
                    except ValueError:
                        break
    return None


def resolve_brain(backend: str, endpoint: str = "", model: str = "") -> tuple[str, str, str]:
    """(backend, endpoint, model) a film's brain asks: its own when it names one, else (AUTO, empty or unknown) the
    Studio's default brain with that brain's server and model."""
    import agents  # the brains (agents.py): Claude Code, Codex, Gemini CLI, Grok CLI here or over ssh, or a local server

    if agents.is_backend(backend):  # a local agent ("local:<id>") brings its own server and model (agents.as_local)
        return backend, endpoint, model
    now = agents.default_brain()
    return now["backend"], now["endpoint"] or "", now["model"] or ""


def ask(backend: str, system: str, prompt: str, tier: str, endpoint: str = "", model: str = "") -> str:
    import agents

    backend, endpoint, model = resolve_brain(backend, endpoint, model)
    return agents.ask(backend, system, prompt, tier, endpoint, model)


NEARBY = [ipaddress.ip_network(net) for net in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                                                 "100.64.0.0/10", "::1/128", "fc00::/7")]  # 100.64/10, fd7a:…: a tailnet


def nearby_endpoint(given: str) -> str:
    """A local model server's address as typed, when it is on this computer, this network or the tailnet: http(s) to
    localhost, a loopback, private-LAN or Tailscale address (100.64.0.0/10, fd7a:…), a *.ts.net or *.local name or a
    one-word LAN name. Anything else (a public site, a cloud's metadata address at 169.254.x) is a 400: the server
    fetches what is named here, and the page that names it need not be the Studio's."""

    text = (given or "").strip()
    if not text:
        return ""
    if text.lower() == "studio:bolt":  # the Studio's own engine (bolt.ENDPOINT): nothing is fetched by name
        return text
    parts = urllib.parse.urlsplit(text if "://" in text else "http://" + text)
    try:
        name = (parts.hostname or "").rstrip(".").lower()
    except ValueError:
        name = ""
    if parts.scheme in ("http", "https") and name and not parts.username and not parts.password:
        if name == "localhost" or name.endswith((".ts.net", ".local", ".localhost")) or "." not in name and ":" not in name:
            # a name decides what this server fetches, so it is checked like an address: whatever it resolves to must
            # itself be nearby. A *.local or one-word name whose DNS answers with a public or metadata IP is not a
            # neighbour, and a name that resolves to nothing cannot be fetched from either.
            try:
                answers = {ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(name, parts.port or 80)}
            except (OSError, ValueError):
                answers = set()
            if answers and all(any(ip in net for net in NEARBY if net.version == ip.version) for ip in answers):
                return text
        try:
            address = ipaddress.ip_address(name)
        except ValueError:
            address = None
        if address is not None and any(address in net for net in NEARBY if net.version == address.version):
            return text
    raise HTTPException(400, "a local model server is on this computer, this network or the tailnet: http://host:port")


@router.get("/api/slate/brain")
def brain_status(endpoint: str = "") -> dict:
    import agents

    endpoint = nearby_endpoint(endpoint)
    found = {b["id"]: b for b in agents.detect(endpoint=endpoint)}
    local = found["local"]
    return {**{name: found[name]["found"] for name in agents.CLIS}, "local": local["found"], "localLabel": local["label"],
            "endpoint": local["endpoint"] if local["found"] else None, "models": local["models"],
            "localAgents": [{"id": b["id"], "label": b["label"], "found": b["found"]} for b in found.values() if b.get("local_agent")],
            "default": agents.default_brain().get("agent") or agents.default_brain()["backend"]}  # what a film on AUTO asks


@router.post("/api/slate/brain")
def brain(body: dict, request: Request) -> dict:
    import agents

    agents.local(request, True)  # runs the user's CLI sign-ins: the same check as the brains and agents routes
    started = time.time()
    try:
        text = ask(body.get("backend") or AUTO, body.get("system") or "", body.get("prompt") or "", body.get("tier") or "top",
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


NOTHING = {"none", "n/a", "na", "null", "nil", "unknown", "not specified", "unspecified", "-", "--"}


def said(value) -> str:
    """A sheet's field as words for a prompt: "" for anything but text, and for a brain's "None", "N/A" or "unknown"."""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = f"{value:g}"
    words = value.strip() if isinstance(value, str) else ""
    return "" if words.rstrip(".").strip().lower() in NOTHING or words in ("—", "–") else words


def age_words(value) -> str:
    """An age for a prompt: "40 years old" for a number, the words as given otherwise ("an adult"), "" for none."""

    age = said(value)
    return f"{age} years old" if age.isdigit() else age


def person(sheet: dict) -> str:
    hair, clothing = said(sheet.get("hair")), said(sheet.get("clothing"))
    bits = [age_words(sheet.get("age")), said(sheet.get("gender")), said(sheet.get("ethnicity")), said(sheet.get("faceFeatures")),
            hair and "hair: " + hair, clothing and "wearing " + clothing]
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
        whose = f"the off-screen voice of {line.get('speaker') or 'the speaker'}" if line.get("offscreen") else f"<Subject {int(line.get('subject') or 1)}>'s voice"
        notes.append(f"the voice timbre reference for {whose}, containing {line['voice']}")
    return audios, notes


DIALOGUE = ("h3", "drama3")  # who speaks a shot's lines: the video model from the words, or Drama 3's cast voices


def film_dialogue(project: dict) -> str:
    """The film's dialogue setting. A film made before it (no "dialogue" key) keeps what it had: its cast voices
    (a voice sheet's Drama 3 take in use) voice the lines of shots on reference pictures, so it reads as drama3."""

    film = project.get("tf") or {}
    if "dialogue" not in film:
        return "drama3" if any(((v or {}).get("drama") or {}).get("use") for v in project.get("voices") or []) else "h3"
    return film["dialogue"] if film["dialogue"] in DIALOGUE else "h3"


def stills_of(sheet: dict) -> list[str]:
    """A sheet's pinned pictures that are on disk, the reference first."""

    return [p for p in sheet.get("images") or [] if isinstance(p, str) and (HOME / p).is_file()]


def bible_pictures(cast: list[dict], place: dict | None, each: int) -> tuple[list[str], list[str], list[str], list[str]]:
    """(pictures, subjects, pictured ids, definitions) of a shot on reference pictures. The cast come first: each
    pictured person's reference still is a ``<Subject N>`` in ``<Picture N>``; then up to ``each`` - 1 more of their
    pinned pictures ("the look of" them), then up to 2 of the place's ("the place:"), while the 9 pictures last."""

    pictures, subjects, pictured, more = [], [], [], []
    for sheet in cast:
        stills = stills_of(sheet)
        if stills and len(pictures) < LIMIT:
            pictures.append(stills[0])
            subjects.append(f"{sheet.get('name', 'the person')}, {person(sheet)}".rstrip(", "))
            pictured.append(sheet.get("id"))
            more += [(path, f"another view of <Subject {len(subjects)}>: the look of {sheet.get('name') or 'this person'}") for path in stills[1:each]]
    if place and pictures:  # a place only beside someone pictured: a picture alone is the shot's subject to the model
        more += [(path, f"the place: {place.get('name') or 'the location'}") for path in stills_of(place)[:2]]
    definitions = []
    for path, note in more:
        if len(pictures) >= LIMIT or path in pictures:
            continue
        pictures.append(path)
        number = len(pictures)
        keep = "the setting, architecture and light" if note.startswith("the place") else "the identity, face, hair, clothing and colours"
        definitions.append(f"<Picture {number}> is {note}; {keep} shown in <Picture {number}> are kept.")
    return pictures, subjects, pictured, definitions


def lora_list(given) -> list[dict]:
    """LoRAs as a job carries them, {id, strength?, style?}, from a list (or one sheet's) with the empty ones left out."""

    return [{"id": str(one["id"]), **({"strength": one["strength"]} if one.get("strength") not in (None, "") else {}),
             **({"style": one["style"]} if one.get("style") else {})} for one in given or [] if isinstance(one, dict) and one.get("id")]


def inside(path: str | Path) -> Path:
    """A path under the studio folder, or 400 (replaced by the server's own in attach)."""

    resolved = (HOME / path).resolve()
    if HOME.resolve() not in (resolved, *resolved.parents):
        raise HTTPException(400, "path is outside the studio folder")
    return resolved


def on_disk(path: str) -> bool:
    """Whether a path the film names is a file in the studio folder (never one outside it, nor a hidden one)."""

    try:
        return inside(path).is_file()
    except HTTPException:
        return False


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

    references, subjects, pictured = [], [], []  # pictured: the cast ids of the references, <Subject 1> first
    definitions = []  # the bible's further pictures: a second look of someone pictured, the place
    # the bible, used automatically (bible_pictures); a film made before the setting (no "bible" key) has it off, so
    # it renders as it did: only a shot that opens on cast stills takes their reference stills
    bible = film.get("bible", "off") != "off" and start in ("text", "cast")
    if start == "cast" or (bible and parts.get("ref2va")):
        references, subjects, pictured, definitions = bible_pictures(cast, place if bible else None, 2 if bible else 1)
        if not parts.get("ref2va"):
            warnings.append("Reference pictures need the Ref2VA model (oneshot-setup.sh --ref2va); rendering from text.")
            references, subjects, pictured, definitions = [], [], [], []
        elif not references and start == "cast":
            warnings.append("No one cast in this shot has a still pinned in Casting; rendering from text.")
        if [s for s in cast if not stills_of(s)] and references:
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

    clip = extra.get("reference_video") if extra.get("reference_video") and on_disk(extra["reference_video"]) else None  # a previs clip brought in from the suite
    if clip and not parts.get("ref2va"):
        warnings.append("A previs reference clip needs the Ref2VA model (oneshot-setup.sh --ref2va); rendering without it.")
        clip = None
    elif clip and (image or previous):
        warnings.append("A previs reference clip does not combine with a first frame: the clip leads and the frame is left out.")
        image = previous = None
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
    voices, lines, sheets = by_id("voices"), [], []
    for line in extra.get("lines") or []:
        if not (line.get("text") or "").strip():
            continue
        who = by_id("characters").get(line.get("characterId"))
        sheet = voices.get(line.get("voiceId")) or next((v for v in voices.values() if who and v.get("characterId") == who.get("id")), None)
        # a speaker who is not one of the reference pictures is heard off screen: given to <Subject 1>, the line was
        # spoken by whoever is pictured first (issue #56). A line with no speaker named stays with <Subject 1>.
        away = bool(references and who and who.get("id") not in pictured)
        if away:
            warnings.append(f"{who.get('name') or 'A speaker'} speaks but is not pictured in this shot: the line is written as an off-screen voice.")
        lines.append({"voice": voice_of(sheet, line.get("voice") or ""), "text": line["text"].strip(), "language": line.get("language") or "English",
                      "sung": bool(line.get("sung")), "speaker": who.get("name") if who else "", **({"offscreen": True} if away else {}),
                      "subject": 0 if away else pictured.index(who["id"]) + 1 if who and who.get("id") in pictured else 1})
        sheets.append(sheet)
    # the dialogue: spoken by the video model from the words alone (h3), or with each speaker's Drama 3 voice as the
    # timbre reference (drama3), which only a shot on reference pictures can take
    dialogue = extra.get("dialogue") if extra.get("dialogue") in DIALOGUE else film_dialogue(project)
    audios, notes = cast_voices(lines, sheets, warnings) if references and dialogue == "drama3" else ([], [])
    if lines and dialogue == "drama3" and not references:
        warnings.append("Dialogue voiced by Drama 3 needs a shot on reference pictures: here the video model speaks the lines from the words.")
    words = sum(len(line["text"].split()) for line in lines)
    if lines and words > seconds * 3.4:
        warnings.append(f"{words} words is a lot for {seconds} s (about three words a second): the line may be rushed or cut off.")
    cue = by_id("music").get(extra.get("cue"))
    music = sentence(", ".join(bit for bit in (cue.get("genre"), cue.get("mood"), cue.get("tempo"), cue.get("instrumentation")) if bit)) if cue else (extra.get("music") or "N/A")
    fields = {"scene": picture, "camera": camera, "action": action, "dialogue": lines, "soundscape": extra.get("soundscape") or "",
              "music": music, "subjects": subjects, "people": bool(cast) or None,  # None: the builder reads the words
              **({"audio_notes": notes} if audios else {}), **({"definitions": definitions} if definitions else {})}
    prompt = compose_references(fields, len(subjects)) if references else compose(fields, bool(image or previous), frames=frames)
    for one in negation.check_fields(fields)["negations"]:  # said, never rewritten (negation.py)
        warnings.append(f"“{one['phrase']}” ({one['field'].lower()}): the video model has no negative prompt, and what a prompt names tends to appear. Say what is there instead.")
    limit = 7000
    if len(prompt) > limit:
        warnings.append(f"The prompt is {len(prompt)} characters; MiniMax H3's guide suggests at most {limit}. Tighten the shot.")
    if not (shot.get("prompt") or "").strip():
        warnings.append("The shot has no prompt yet.")
    # a character named only in the prompt's words: not cast, so their pictures are not attached and no line is theirs
    uncast = [c.get("name") for c in project.get("characters") or [] if c.get("name") and c.get("id") not in (extra.get("cast") or [])
              and re.search(r"(?<!\w)" + re.escape(c["name"].strip().lower()) + r"(?!\w)", (shot.get("prompt") or "").lower())]
    if uncast:
        warnings.append(", ".join(uncast) + (" is" if len(uncast) == 1 else " are") + " named in the prompt but not cast in this shot: "
                        "cast them so their pictures and lines go with the shot.")
    # a LoRA for this shot alone (the hub's, as a job carries them): it stands in for the film's on this shot. The
    # bible's LoRAs (a character's, the place's) join it in every mode: the shot's own first, at most 4, none twice
    own = lora_list(extra.get("loras"))
    entities = lora_list([sheet.get("lora") for sheet in [*cast, place or {}]])
    if entities and not own and not references:
        own = lora_list(film.get("loras"))  # the film's would stand in for none: they stay, and the bible's join them
    loras = []
    for one in own + entities:
        if one["id"] not in {kept["id"] for kept in loras}:
            loras.append(one)
    if len(loras) > 4:
        warnings.append("A clip takes at most 4 LoRAs: " + ", ".join(one["id"] for one in loras[4:]) + " left out.")
        loras = loras[:4]
    plan = {"seconds": seconds, "frames": frames, "seed": int(extra.get("seed") or 1), "quality": film.get("quality") or "fh8",
            **({"slot": slot} if slot else {}), **({"loras": loras} if loras else {}),
            **({"preset": size} if isinstance(size, str) else {"preset": "custom", "width": size[0], "height": size[1]}),
            **({"references": references, "ref_turbo": film.get("refPasses") == "4"} if references else {}),
            **({"reference_audios": audios} if audios else {}),
            **({"image": image} if image else {}), **({"first_frame_from": previous} if previous else {}),
            **({"reference_videos": [clip]} if clip else {})}
    mode = "references" if references or clip else "image" if image else "previous" if previous else "text"
    # the machine's memory (a DGX Spark's guard): said here, before the shot is queued, with what fits
    try:
        memory = room(plan, bool(plan.get("loras") or (film.get("loras") and not references)))
    except Exception:  # noqa: BLE001 - a plan the queue will refuse for another reason
        memory = {"fits": True, "reason": ""}
    if not memory.get("fits", True):
        warnings.append("This shot will be refused when queued: " + memory["reason"])
    return {"prompt": prompt, "plan": plan, "mode": mode, "dialogue": dialogue, "warnings": warnings, "chars": len(prompt), "memory": {"fits": memory.get("fits", True), "reason": memory.get("reason", ""), "need_mb": memory.get("need_mb"), "nearest": memory.get("nearest")}}


@router.post("/api/slate/compile")
def compile_endpoint(body: dict) -> dict:
    project = body.get("project") or get_project(body.get("projectId", ""))
    return compile_shot(project, body.get("shotId", ""), body.get("chain"))
