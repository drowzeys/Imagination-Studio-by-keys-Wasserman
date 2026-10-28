"""The Director: a logline becomes a checked, cut film while you watch.

A run works through a Slate film (slate.py) in stages, each written to ``director.json`` in the film's folder so that a
restart picks it up where it stopped:

  writing    the film's brain writes what the film lacks: characters and places, scenes, then each scene's shots, all
             applied through the same actions the First AD uses (``apply`` mirrors slate.js ``slApply``)
  casting    a reference still for every character without a pinned picture, then a turnaround of it; a plate for
             every place without one
  rendering  every shot compiled (slate.compile_shot) and queued in order, one for each machine that renders (at most
  checking   three); a shot that opens on the shot before waits for it; each clip comes back as a take, is checked by
  retaking   the QA gate (the job's own, else POST /api/qa) and, when it fails, rendered again at the next seed, up to
             ``retakes`` times; a render the machine's workload limits stopped (heat, memory pressure) is not a take
             and is queued once more
  cutting    the take that passed of every shot (or the one circled) cut on the song's bars by the rough cut
             (roughcut.py), with its FCPXML, EDL and Resolve zip; without a song (or the rough cut) an Editor timeline
             and an export job. A take that failed its check stays in the Library and the shot's takes, out of the cut

Each shot ends with an outcome (``OUTCOMES``) and the run's last line counts them: what passed, what the queue refused,
what failed its check and what was left out of the cut.

The server hands in the queue and the job table (``attach``); tests stand in for them.
"""

import json
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

import slate  # the film files, the brain and the shot compiler, beside this file

router = APIRouter()
HOME: Path = Path()
STAGES = ("writing", "casting", "rendering", "cutting")
ACTIVE = ("writing", "casting", "rendering", "checking", "retaking", "cutting")
AT_ONCE = 3  # the most render jobs a run keeps in the queue (with as many machines to render them)
WORKLOAD_STOPS = 2  # a shot whose render the machine's workload limits stopped this often is left out (no third try)
WORKLOAD_STOP = "stopped by this machine's workload limits"  # spark_generate.py's words for a heat or memory stop
# how a shot ended: in the cut (passed, not checked, kept from before) or out of it, and why
OUTCOMES = {"passed": "passed the check", "unchecked": "not checked", "kept": "kept as it was", "failed": "failed the check",
            "refused": "refused by the queue", "broken": "the render failed", "stopped": "stopped by the workload limits", "removed": "removed"}
IN_CUT = ("passed", "unchecked", "kept")
TICK = 2.0  # seconds between looks at the queue
LOG = 200  # lines of the run's log kept

# what the server hands in (attach); each stands in for a route of server.py
submit = lambda body: (_ for _ in ()).throw(RuntimeError("the queue is not attached"))  # POST /api/jobs: the job
job_of = lambda job_id: None  # the queue's record of a job, or None when this app does not know it (a restart)
cancel = lambda job_id: None  # POST /api/jobs/{id}/cancel
qa_start = None  # POST /api/qa: a QA job, or an HTTPException where the gate cannot run
editor_new = editor_save = None  # POST /api/projects, PUT /api/projects/{id}
sizes: dict = {}  # preset -> (width, height)
APP = None  # the FastAPI app
ref2va = lambda: False  # whether shots can open on cast stills
machines = lambda: 1  # how many machines render the queue's clips (this one and its Sparks, sparks.py)
resident = lambda: ""  # what is loaded beside the queue now, in words (server.resident_words), for a refusal

runs: dict[str, dict] = {}  # project -> {"thread", "stop"}
guard = threading.RLock()  # one read-modify-write of a film file at a time
starting = threading.Lock()  # one look at ``runs`` and start() at a time: two fast POSTs never start two runs


class Stopped(Exception):
    pass


class Failed(Exception):
    pass


def attach(home: Path, *, queue, lookup, stop_job, check, new_project, save_project, presets: dict, app=None,
           references=None, renderers=None, loaded=None) -> APIRouter:
    global HOME, submit, job_of, cancel, qa_start, editor_new, editor_save, sizes, APP, ref2va, machines, resident
    HOME, submit, job_of, cancel, qa_start, editor_new, editor_save = home, queue, lookup, stop_job, check, new_project, save_project
    sizes, APP, ref2va = presets, app, references or ref2va
    machines, resident = renderers or machines, loaded or resident
    return router


def resume_later() -> None:
    """The runs a restart cut off, picked up once the queue's worker is up (server.py at start, never on import)."""

    later = threading.Timer(5.0, resume_all)
    later.daemon = True
    later.start()


# ---------- small helpers, in Slate's own shapes ----------

def sl_id(prefix: str) -> str:
    """An id as slate.js makes them (slId): prefix, the time in base 36, five random characters."""

    stamp, digits = int(time.time() * 1000), "0123456789abcdefghijklmnopqrstuvwxyz"
    base = ""
    while stamp:
        stamp, rest = divmod(stamp, 36)
        base = digits[rest] + base
    return f"{prefix}-{base}-{uuid.uuid4().hex[:5]}"


def text(value) -> str:
    return value if isinstance(value, str) else ""


def named(items: list, name) -> dict | None:
    want = str(name or "").strip().lower()
    return next((x for x in items if (x.get("name") or "").strip().lower() == want), None) if want else None


def tf_of(shot: dict) -> dict:
    if not isinstance(shot.get("tf"), dict):
        shot["tf"] = {"start": "text", "cast": [], "location": "", "lines": [], "soundscape": "", "music": "N/A", "cue": "",
                      "seed": 1, "image": None, "h3": "", "h3Auto": True}
    return shot["tf"]


def blank_shot(film: dict, name: str) -> dict:
    d = film.get("defaults") or {}
    return {"id": sl_id("shot"), "name": name, "intent": "",
            "spec": {"durationSec": d.get("durationSec", 8), "fps": d.get("fps", 24), "aspectRatio": d.get("aspectRatio", "16:9"),
                     "lens": None, "movement": None, "size": None, "angle": None},
            "prompt": "", "lockedLines": [], "mutedLines": [], "beatSheet": None, "targetModel": "minimax-h3", "maxChars": None,
            "variants": [], "history": [], "takes": [], "createdAt": slate.now(), "updatedAt": slate.now()}


# the bible's sheets as the First AD writes them (slate.js SL_BIBLE): the list, the reference key, id prefix and fields
BIBLE = {
    "add_character": ("characters", "character", "char", "character", lambda: {"scenario": "cinematic", "notes": "", "images": []},
                      ("age", "gender", "ethnicity", "faceFeatures", "hair", "clothing", "expression", "eyeDirection", "mood",
                       "environment", "keyLightSide", "lightingMood")),
    "add_location": ("locations", "location", "loc", "location", lambda: {"notes": "", "images": []},
                     ("interiorExterior", "description", "timeOfDay", "weather", "architecture", "textures", "practicalLights")),
    "add_art": ("artDept", "art", "art", "prop, wardrobe or vehicle", lambda: {"notes": ""},
                ("kind", "description", "materials", "condition", "era", "distinctive")),
    "add_music_cue": ("music", "cue", "cue", "music cue", lambda: {"lyrics": "", "notes": ""},
                      ("sceneRef", "intent", "genre", "mood", "tempo", "instrumentation", "era", "structure", "vocals", "lyricTheme", "durationSec")),
    "add_voice": ("voices", "voice", "voice", "voice", lambda: {"characterId": None, "notes": ""},
                  ("ageGender", "accent", "timbre", "pitch", "pacing", "energy", "texture", "emotionalRange", "sampleLine")),
}
CHANGE = {"update_character": "add_character", "update_location": "add_location", "update_art": "add_art",
          "update_music_cue": "add_music_cue", "update_voice": "add_voice"}
FIX = {"keyLightSide": lambda v: text(v) or "Key light from left", "lightingMood": lambda v: text(v) or "Natural soft light",
       "interiorExterior": lambda v: text(v) if text(v) in ("interior", "both") else "exterior",
       "kind": lambda v: text(v) if text(v) in ("wardrobe", "vehicle") else "prop",
       "vocals": lambda v: text(v) if text(v) in ("vocals", "either") else "instrumental",
       "durationSec": lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else None}


def sheet_fields(kind: str, action: dict, film: dict) -> tuple[dict, dict]:
    """(every field of the sheet with defaults, the fields this action sent), as slSheetRead reads them."""

    every, sent = {}, {}
    for key in BIBLE[kind][5]:
        value = FIX.get(key, text)(action.get(key))
        every[key] = value
        if action.get(key) is not None:
            sent[key] = value
    if kind == "add_voice" and "characterName" in action:
        who = named(film.get("characters") or [], action.get("characterName"))
        every["characterId"] = sent["characterId"] = who["id"] if who else None
    return every, sent


def opens_on(film: dict, shot: dict, tf: dict, action: dict, receipts: list) -> None:
    """slOpensOn: text, previous and cast as they are; image on a Library path, else the location's first still."""

    path = text(action.get("image")).strip()
    if action.get("opensOn") in ("text", "previous", "cast"):
        tf["start"] = action["opensOn"]
    elif action.get("opensOn") == "image":
        place = next((l for l in film.get("locations") or [] if l.get("id") == tf.get("location")), None)
        still = path or ((place or {}).get("images") or [None])[0]
        if still:
            tf["start"], tf["image"] = "image", still
        else:
            receipts.append(f"⚠ {shot['name']}: \"image\" needs a Library path or a location with a still; its opening is unchanged")
    if action.get("opensOn") == "cast" and not ref2va():
        receipts.append(f"⚠ {shot['name']} opens on cast stills, which need the Ref2VA model: until it is installed it renders from text")


def gear(film: dict, shot: dict, action: dict, receipts: list) -> None:
    tf = tf_of(shot)
    if isinstance(action.get("cast"), list):
        tf["cast"] = [c["id"] for c in (named(film.get("characters") or [], n) for n in action["cast"]) if c]
    if "location" in action:
        tf["location"] = (named(film.get("locations") or [], action["location"]) or {}).get("id") or ""
    if isinstance(action.get("lines"), list):
        tf["lines"] = [{"characterId": (named(film.get("characters") or [], l.get("speaker")) or {}).get("id"), "voiceId": None,
                        "text": text(l.get("text")).strip(), "language": text(l.get("language")) or "English", "sung": bool(l.get("sung"))}
                       for l in action["lines"] if isinstance(l, dict) and text(l.get("text")).strip()]
    if "soundscape" in action:
        tf["soundscape"] = text(action["soundscape"])
    opens_on(film, shot, tf, action, receipts)
    tf["h3Auto"] = True
    if "bars" in action:
        try:
            bars = float(action["bars"])
        except (TypeError, ValueError):
            bars = 0
        if 0 < bars <= 64:
            tf["bars"] = bars
        else:
            tf.pop("bars", None)
    if "lora" in action:  # the page checks the hub's installed LoRAs; here a LoRA is taken off, never put on unchecked
        if not action["lora"]:
            tf["loras"] = []
        else:
            receipts.append(f"• A shot's LoRA is set on the page: \"{action['lora'].get('name') or action['lora'].get('id')}\" was not put on")


def spec_into(spec: dict, patch) -> None:
    for key in ("durationSec", "fps", "aspectRatio", "size", "angle", "lens", "movement"):
        if isinstance(patch, dict) and key in patch:
            spec[key] = patch[key]


def apply(film: dict, action: dict) -> str:
    """One First AD action applied to a film in place, as slate.js slApply does; its receipt."""

    receipts, kind = [], action.get("type")
    scenes = film.setdefault("scenes", [])
    scene_of = lambda ref: next((s for s in scenes if s.get("id") == ref), None) or named(scenes, ref)

    def shot_of(ref):
        for sc in scenes:
            hit = next((s for s in sc.get("shots") or [] if s.get("id") == ref), None) or named(sc.get("shots") or [], ref)
            if hit:
                return sc, hit
        return None

    try:
        if kind == "update_project":
            for key in ("logline", "world"):
                if key in action:
                    film[key] = action[key]
            if isinstance(action.get("defaults"), dict):
                film.setdefault("defaults", {}).update(action["defaults"], targetModel="minimax-h3")
            receipts.append("✓ Updated project bible")
        elif kind == "create_scene":
            if scene_of(action.get("name")):
                receipts.append(f"• Scene \"{action.get('name')}\" already exists — skipped")
            else:
                scenes.append({"id": sl_id("scene"), "name": text(action.get("name")) or f"Scene {len(scenes) + 1}",
                               "synopsis": text(action.get("synopsis")), "shots": []})
                receipts.append(f"✓ Created scene \"{scenes[-1]['name']}\"")
        elif kind == "update_scene":
            sc = scene_of(action.get("scene"))
            if not sc:
                receipts.append(f"✗ Scene \"{action.get('scene')}\" not found")
            else:
                if action.get("name"):
                    sc["name"] = action["name"]
                if "synopsis" in action:
                    sc["synopsis"] = action["synopsis"]
                receipts.append(f"✓ Updated scene \"{sc['name']}\"")
        elif kind == "create_shot":
            sc = scene_of(action.get("scene"))
            if not sc:
                receipts.append(f"✗ Scene \"{action.get('scene')}\" not found for new shot")
            else:
                shot = blank_shot(film, text(action.get("name")) or "Shot " + str(len(sc.setdefault("shots", [])) + 1).zfill(2))
                if action.get("intent"):
                    shot["intent"] = action["intent"]
                if action.get("prompt"):
                    shot["prompt"] = action["prompt"]
                spec_into(shot["spec"], action.get("spec"))
                if action.get("beatSheet"):
                    shot["beatSheet"] = action["beatSheet"]
                gear(film, shot, action, receipts)
                sc.setdefault("shots", []).append(shot)
                receipts.append(f"✓ Created \"{shot['name']}\" in \"{sc['name']}\"" + (" with prompt" if action.get("prompt") else ""))
        elif kind == "update_shot":
            hit = shot_of(action.get("shot"))
            if not hit:
                receipts.append(f"✗ Shot \"{action.get('shot')}\" not found")
            else:
                sc, shot = hit
                if "prompt" in action and action["prompt"] != shot.get("prompt"):
                    if (shot.get("prompt") or "").strip():
                        shot.setdefault("history", []).insert(0, {"id": sl_id("v"), "savedAt": slate.now(), "label": "before First AD change", "prompt": shot["prompt"]})
                        del shot["history"][50:]
                    shot["prompt"] = action["prompt"]
                if action.get("name"):
                    shot["name"] = action["name"]
                if "intent" in action:
                    shot["intent"] = action["intent"]
                spec_into(shot.setdefault("spec", {}), action.get("spec"))
                if "beatSheet" in action:
                    shot["beatSheet"] = action["beatSheet"]
                gear(film, shot, action, receipts)
                shot["updatedAt"] = slate.now()
                receipts.append(f"✓ Updated \"{shot['name']}\"")
        elif kind in BIBLE or kind in CHANGE:
            adding = kind in BIBLE
            sheet_kind = kind if adding else CHANGE[kind]
            key, ref_key, prefix, noun, fresh, _ = BIBLE[sheet_kind]
            items = film.setdefault(key, [])
            ref = action.get("name") if adding else action.get(ref_key)
            hit = next((x for x in items if x.get("id") == ref), None) or named(items, ref)
            every, sent = sheet_fields(sheet_kind, action, film)
            if not adding and not hit:
                receipts.append(f"✗ {noun} \"{ref}\" not found — nothing changed")
            elif not hit:
                items.append({"id": sl_id(prefix), **fresh(), **every, "name": text(action.get("name")) or "Unnamed"})
                receipts.append(f"✓ Added {noun} \"{items[-1]['name']}\"")
            else:
                whole, was = adding or action.get("replace") is True, hit.get("name")
                hit.update(every if whole else sent)
                if not adding and text(action.get("name")).strip():
                    hit["name"] = text(action["name"]).strip()
                receipts.append(f"✓ {'Rewrote' if whole else 'Changed'} {noun} \"{was}\"")
        elif kind == "select":
            receipts.append("• select is for the page")
        else:
            receipts.append(f"✗ Unknown action {kind or '?'}")
    except Exception as error:  # noqa: BLE001 - one action's failure is its receipt, as on the page
        receipts.append(f"✗ {kind} failed: {error}")
    return "\n".join(receipts)


def context(film: dict, scene: dict | None = None) -> str:
    """The bible as the brain reads it (slate.js slContext)."""

    out = ["PROJECT: " + (film.get("name") or "")]
    if film.get("logline"):
        out.append("Logline: " + film["logline"])
    if film.get("world"):
        out.append("World & tone: " + film["world"])
    if film.get("characters"):
        out.append("\nCHARACTERS (keep these descriptions verbatim-consistent in every prompt):")
        out += [f"- {c.get('name')}: " + "; ".join(text(c.get(k)) for k in ("age", "gender", "ethnicity", "faceFeatures", "hair", "clothing") if text(c.get(k)))
                for c in film["characters"]]
    if film.get("locations"):
        out.append("\nLOCATIONS:")
        out += [f"- {l.get('name')} ({l.get('interiorExterior') or 'exterior'}): {l.get('description') or ''}" + (f"; {l['timeOfDay']}" if l.get("timeOfDay") else "")
                for l in film["locations"]]
    if film.get("lookbook"):
        out.append("\nACTIVE STYLE PROFILES (apply their language where relevant):")
        out += [f"- {s.get('source')}: tone {s.get('tone')}; palette {s.get('palette')}; lighting {s.get('lighting')}" for s in film["lookbook"]]
    if film.get("voices"):
        out.append("\nVOICE CAST:")
        out += [f"- {v.get('name')}: " + ", ".join(text(v.get(k)) for k in ("ageGender", "accent", "timbre") if text(v.get(k))) for v in film["voices"]]
    if scene:
        out.append(f"\nCURRENT SCENE: {scene.get('name')}" + (f" — {scene['synopsis']}" if scene.get("synopsis") else ""))
    return "\n".join(out)


# clothing a head-and-shoulders crop would lose: a hat, a coat, a uniform (slate.js SL_COSTUME)
COSTUME = re.compile(r"\b(hat|hats|cap|caps|fedora|bowler|beret|helmet|hood|bonnet|turban|crown|veil|coat|overcoat|greatcoat|trench|"
                     r"jacket|blazer|cloak|cape|robe|gown|dress|uniform|armou?r|apron|overalls|suit|tuxedo|waistcoat|vest|kilt|"
                     r"scarf|shawl|boots|holster|belt|badge|costume)\b", re.I)


def sheet_picture(c: dict) -> str:
    """A character's reference portrait prompt (slate.js slSheetPicture). When the clothing names a hat, a coat or a
    uniform the portrait is from the knees up and shows all of it: later shots match the costume to this picture, and a
    head-and-shoulders crop with "no props" lost a period overcoat and a fedora."""

    said = slate.said  # a brain's "None" or "N/A" is left out, not written into the picture's prompt
    who = ", ".join(x for x in (slate.age_words(c.get("age")), said(c.get("gender")), said(c.get("ethnicity"))) if x)
    hair, clothing = said(c.get("hair")), said(c.get("clothing"))
    framing = ("three-quarter length from the knees up, facing the camera, the whole costume in frame as described, hat and coat included, "
               "plain seamless backdrop, generous headroom" if COSTUME.search(clothing) else
               "centered head-and-shoulders, facing the camera, plain seamless backdrop, generous headroom, no props")
    bits = [f"{c.get('name')}" + (f", {who}" if who else ""), said(c.get("faceFeatures")), hair and f"Hair: {hair}",
            clothing and f"Wearing {clothing}", said(c.get("expression")), f"Character reference portrait: {framing}",
            f"{said(c.get('keyLightSide')) or 'Key light from left'}, {said(c.get('lightingMood')) or 'natural soft light'}, clean catchlights, gentle fill",
            "Photoreal, neutral grade, true-to-skin color, crisp detail for identity reference."]
    words = ". ".join(bit.strip() for bit in bits if bit and bit.strip())
    return re.sub(r"\.\s*\.", ".", re.sub(r"\s+\.", ".", words))


def plate_picture(l: dict) -> str:
    """A location's plate prompt (slate.js slPlatePicture)."""

    bits = [f"{l.get('name')}: {l.get('description') or ''}", l.get("timeOfDay"), l.get("weather"), l.get("architecture"), l.get("textures"),
            l.get("practicalLights") and "lit by " + l["practicalLights"]]
    return ". ".join(b for b in bits if isinstance(b, str) and b.strip()) + \
        ". Location plate: a wide establishing still of the empty place at eye level, natural perspective, photographic, every surface in focus."


# ---------- the run's file ----------

def status_file(project: str) -> Path:
    return slate.project_file(project).parent / "director.json"


def read_status(project: str) -> dict | None:
    try:
        return json.loads(status_file(project).read_text())
    except (OSError, ValueError):
        return None


def save(run: dict) -> None:
    run["rev"] = int(run.get("rev") or 0) + 1
    target = status_file(run["project"])
    scratch = target.with_suffix(".tmp")
    scratch.write_text(json.dumps(run, indent=1))
    scratch.replace(target)


def say(run: dict, line: str, **current) -> None:
    run.setdefault("log", []).append({"at": slate.now(), "text": line})
    del run["log"][:-LOG]
    if current or line:
        run["current"] = {"text": line, **current}
    save(run)


def film_of(project: str) -> dict:
    return json.loads(slate.project_file(project).read_text())


def change(project: str, mutate) -> dict:
    """The film read fresh (the page may have saved it meanwhile), changed and written back."""

    with guard:
        film = film_of(project)
        mutate(film)
        return slate.write(film)


def outcome_of(rec: dict) -> str | None:
    """How a settled shot ended (OUTCOMES); for a run written before outcomes were kept, read from what it has."""

    if rec.get("outcome"):
        return rec["outcome"]
    if not rec.get("settled"):
        return None
    if rec.get("kept"):
        return "kept"
    if rec.get("best"):
        return "passed"
    attempts = rec.get("attempts") or []
    if not attempts:
        return "removed"
    return "failed" if any(a.get("status") == "done" for a in attempts) else "refused" if attempts[-1].get("job") is None else "broken"


def counts(run: dict, film: dict | None = None) -> dict:
    shots = run.get("shots") or {}
    attempts = [a for s in shots.values() for a in s.get("attempts") or []]
    mine = [shots[sid] for sid in run.get("order") or [] if sid in shots]
    ended = lambda *names: sum(1 for s in mine if s.get("settled") and outcome_of(s) in names)
    out = {"scenes": len((film or {}).get("scenes") or []) or run.get("counts", {}).get("scenes", 0),
           "shots": len(run.get("order") or []),
           "rendered": sum(1 for s in mine if any(a.get("status") == "done" for a in s.get("attempts") or []) or s.get("kept")),
           "settled": sum(1 for s in mine if s.get("settled")),
           "passed": ended("passed"), "kept": ended("kept"), "unchecked": ended("unchecked"), "inCut": ended(*IN_CUT),
           "failedCheck": ended("failed"), "refused": ended("refused"), "broken": ended("broken"), "stopped": ended("stopped"),
           "leftOut": sum(1 for s in mine if s.get("settled") and outcome_of(s) not in IN_CUT + ("removed",)),
           "failed": sum(1 for a in attempts if a.get("passed") is False),
           "retakes": sum(1 for a in attempts if a.get("retake") and not a.get("refused") and a.get("status") != "stopped"),
           "cast": len(run.get("casting") or {}),
           "castDone": sum(1 for c in (run.get("casting") or {}).values() if c.get("step") == "done")}
    run["counts"] = out
    return out


# ---------- the routes ----------

def clamp(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


@router.post("/api/slate/direct")
def direct(body: dict, request: Request) -> dict:
    """Start (or pick up) the Director on a film: {project, logline?, scenes, shotsPerScene, seconds, quality, retakes,
    cut, song?}. A film whose run stopped, failed or was cut off by a restart continues: what is written, cast and
    rendered is kept. Returns the run."""

    import agents

    agents.local(request, True)  # the brain is the user's own sign-in: the same check as /api/slate/brain
    project = str(body.get("project") or "")
    if not slate.project_file(project).is_file():
        raise HTTPException(404, "no such film")
    with starting:
        if project in runs and runs[project]["thread"].is_alive():
            raise HTTPException(409, "the Director is already at work on this film")
        film = film_of(project)
        logline = text(body.get("logline")).strip()
        if logline and logline != film.get("logline"):
            film = change(project, lambda f: apply(f, {"type": "update_project", "logline": logline}))
        if not (film.get("logline") or "").strip() and not film.get("scenes"):
            raise HTTPException(400, "give the film a logline: one sentence of who wants what")
        asked = {"scenes": clamp(body.get("scenes"), 1, 12, 3), "shotsPerScene": clamp(body.get("shotsPerScene"), 1, 12, 3),
                 "seconds": clamp(body.get("seconds"), 1, 15, 8), "retakes": clamp(body.get("retakes"), 0, 3, 1),
                 "quality": text(body.get("quality")) or (film.get("tf") or {}).get("quality") or "", "cut": body.get("cut") is not False,
                 "song": text(body.get("song")).strip()}
        before = read_status(project) or {}
        run = {"project": project, "state": "writing", "asked": asked, "started": slate.now(), "finished": None, "error": "",
               "done": [], "log": before.get("log", [])[-40:], "current": {}, "counts": {}, "cut": None, "rev": before.get("rev", 0),
               # what a run that did not finish had in flight is picked up: its jobs, its takes, its casting
               "shots": before.get("shots", {}) if before.get("state") != "done" else {},
               "casting": before.get("casting", {}) if before.get("state") != "done" else {}, "order": []}
        say(run, "The Director starts: " + (film.get("logline") or film.get("name") or "the film"))
        return {**start(run), "started": True}


def start(run: dict) -> dict:
    stop = threading.Event()
    thread = threading.Thread(target=work, args=(run, stop), daemon=True, name="director-" + run["project"])
    runs[run["project"]] = {"thread": thread, "stop": stop, "run": run}
    thread.start()
    return run


@router.get("/api/slate/direct/{project}")
def direct_status(project: str) -> dict:
    slate.project_file(project)  # a bad id is a 400
    live = runs.get(project)
    run = read_status(project)  # written after every change: the thread's own copy is being changed
    if not run:
        return {"project": project, "state": "none"}
    try:  # when the film file last changed: the page reads the film again when the run has changed it
        changed = film_of(project).get("updatedAt")
    except (OSError, ValueError):
        changed = None
    return {**run, "running": bool(live and live["thread"].is_alive()), "filmUpdatedAt": changed}


@router.post("/api/slate/direct/{project}/stop")
def direct_stop(project: str, request: Request) -> dict:
    import agents

    agents.local(request, True)
    slate.project_file(project)
    live = runs.get(project)
    if not live or not live["thread"].is_alive():
        run = read_status(project)
        if run and run.get("state") in ACTIVE:  # cut off by a restart and not picked up: marked stopped
            run["state"], run["finished"] = "stopped", slate.now()
            say(run, "Stopped.")
        return {"stopped": False, "state": (run or {}).get("state", "none")}
    live["stop"].set()
    return {"stopped": True}


def resume_all() -> None:
    """Runs a restart cut off: each continues from its last finished stage."""

    for file in (HOME / "slate").glob("*/director.json"):
        try:
            run = json.loads(file.read_text())
        except (OSError, ValueError):
            continue
        project = run.get("project") or file.parent.name
        with starting:
            if run.get("state") in ACTIVE and project not in runs and slate.project_file(project).is_file():
                run["project"] = project
                say(run, "Picked up after a restart.")
                start(run)


# ---------- the run ----------

def work(run: dict, stop: threading.Event) -> None:
    try:
        for stage, step in (("writing", write_stage), ("casting", cast_stage), ("rendering", render_stage), ("cutting", cut_stage)):
            if stage in run["done"]:
                continue
            if stop.is_set():
                raise Stopped()
            run["state"] = stage
            save(run)
            step(run, stop)
            run["done"].append(stage)
            save(run)
        run["state"], run["finished"] = "done", slate.now()
        counts(run)
        say(run, "Done: " + summary(run))
    except Stopped:
        for shot in (run.get("shots") or {}).values():  # what waits in the queue is taken out; a clip being made finishes
            for one in shot.get("attempts") or []:
                job = job_of(one.get("job") or "") if one.get("status") == "queued" else None
                if job and job.get("status") == "queued":
                    cancel(one["job"])
        run["state"], run["finished"] = "stopped", slate.now()
        say(run, "Stopped. Direct it again to carry on from here: what is written, cast and rendered is kept.")
    except Exception as error:  # noqa: BLE001 - the reason goes to the page
        run["state"], run["finished"], run["error"] = "failed", slate.now(), str(getattr(error, "detail", error))[:600]
        say(run, "Failed: " + run["error"])


def summary(run: dict) -> str:
    """The run's last line: what passed, what did not and why, by name; never a shot the queue refused as rendered."""

    c, shots = counts(run), run.get("shots") or {}
    plural = lambda n, word: f"{n} {word}" + ("" if n == 1 else "s")
    who = lambda outcome: ", ".join(shots[sid].get("name") or "?" for sid in run.get("order") or [] if outcome_of(shots.get(sid) or {}) == outcome)
    bits = [f"{c['passed']} of {plural(c['shots'], 'shot')} passed the check"]
    if c["kept"]:
        bits.append(f"{c['kept']} kept as rendered before")
    if c["unchecked"]:
        bits.append(f"{c['unchecked']} not checked (in the cut)")
    for key, words in (("failedCheck", "failed the check (in the Library, out of the cut)"), ("refused", "refused by the queue"),
                       ("broken", "failed to render"), ("stopped", "stopped by the workload limits")):
        if c[key]:
            bits.append(f"{c[key]} {words}: {who({'failedCheck': 'failed'}.get(key, key))}")
    bits.append(plural(c["retakes"], "retake"))
    if run.get("cut"):
        bits.append("cut without " + plural(c["leftOut"], "shot") if c["leftOut"] else "cut")
    return ", ".join(bits)


def wait(stop: threading.Event, seconds: float = None) -> None:
    if stop.wait(TICK if seconds is None else seconds):
        raise Stopped()


# ---------- writing ----------

SYSTEM = ("You are the director's brain inside Slate, a prompt studio for AI filmmaking: an expert screenwriter, director and "
          "cinematographer. You write vivid, concrete, production-ready visual language: physical, photographable descriptions, never "
          "vague adjectives or filler like \"high quality, 8k, masterpiece\". Respect every continuity fact you are given. Answer with "
          "JSON only, no commentary.")
PROMPT_FORMAT = ("Every \"prompt\" is sectioned, each heading on a line of its own, in this order (only sections with content): "
                 "# Subject (who/what, the action, wardrobe), # Composition, # Lighting, # Camera, # Style, # Mood. Never write spoken "
                 "words inside a prompt: speech goes in \"lines\". Never write what must not be seen (\"no X\"): say what is there. "
                 "Repeat the bible's character and location descriptions in every prompt.")
SIZES_WORDS = "size EWS|WS|MWS|MS|MCU|CU|ECU|Insert; angle eye level|low|high|overhead|dutch|over-shoulder|POV; movement locked|handheld|push-in|pull-back|orbit|pan|tilt|track|crane|drone|steadicam; lens 24mm|35mm|50mm|85mm"


def brain(film: dict, task: str, prompt: str, check) -> dict:
    """The film's brain asked for JSON, and asked once more when the answer does not hold up."""

    d = film.get("defaults") or {}
    problem, asked = "", prompt
    for again in range(2):
        try:
            answer = slate.ask(d.get("brain") or slate.AUTO, SYSTEM, asked, "top", d.get("localEndpoint") or "", d.get("localModel") or "")
            data = slate.extract_json(answer)
            problem = check(data) if isinstance(data, dict) else "the answer is not a JSON object"
        except ValueError as error:
            problem = str(error)
        except Exception as error:  # noqa: BLE001 - a brain that does not answer
            raise Failed(f"the brain did not answer ({task}): {str(error)[:300]}")
        if not problem or (again and isinstance(problem, Soft)):  # asked once to fix a soft problem; the second answer stands
            return data
        asked = prompt + f"\n\nYOUR LAST ANSWER COULD NOT BE USED: {problem}. Answer again, with the JSON only."
    raise Failed(f"the brain's {task} could not be used twice: {problem}")


def check_bible(need_cast: bool, need_places: bool):
    def check(data: dict) -> str:
        if need_cast and not [c for c in data.get("characters") or [] if isinstance(c, dict) and text(c.get("name")).strip()]:
            return "\"characters\" is a list of characters, each with a name"
        if need_places and not [l for l in data.get("locations") or [] if isinstance(l, dict) and text(l.get("name")).strip()]:
            return "\"locations\" is a list of locations, each with a name"
        return ""
    return check


def check_scenes(data: dict) -> str:
    good = [s for s in data.get("scenes") or [] if isinstance(s, dict) and text(s.get("name")).strip()]
    return "" if good else "\"scenes\" is a list of scenes, each with a name and a synopsis"


class Soft(str):
    """A problem the brain is asked to fix once; a second answer with only these is taken (a cast or a line guessed
    from the words is worse than the shot as written, and two refusals would stop the whole run)."""


SPEAKING = (r"says?|said|asks?|asked|tells?|told|answers?|answered|repl(?:y|ies|ied)|shouts?|shouted|yells?|yelled|whispers?|"
            r"whispered|mutters?|muttered|murmurs?|murmured|cries|cried|screams?|screamed|speaks?|spoke")


def spoken_in(prompt: str, names: list[str]) -> list[str]:
    """Speech written into a prompt: one of the bible's characters (or he, she, they) speaking, then a quotation within a
    few words. A sign that says something, a diner called something or a radio announcer is not the cast speaking."""

    who = "|".join([re.escape(n) for n in names if n] + ["[Hh]e", "[Ss]he", "[Tt]hey"])
    return re.findall(r"(?<!\w)(?:" + who + r")(?![\w'’])[^\"“.]{0,30}?\b(?:" + SPEAKING + r")\b[^\"“]{0,40}?[\"“]([^\"”]+)[\"”]", prompt)


def named_in(prompt: str, names: list[str], places: list[str] = ()) -> list[str]:
    """The bible's characters a prompt names, by whole name as written (case and all), places' names left out first."""

    for place in sorted((p for p in places if p), key=len, reverse=True):  # "Marlowe House" is a place, not Marlowe
        prompt = re.sub(re.escape(place), " ", prompt, flags=re.I)
    # "Ada's boat" is about Ada, not of her: a possessive alone does not put her in the shot
    return sorted(n for n in names if n and re.search(r"(?<!\w)" + re.escape(n) + r"(?![\w'’])", prompt))


def check_shots(film: dict, seconds: int):
    as_written = [(c.get("name") or "").strip() for c in film.get("characters") or []]
    names = {n.lower() for n in as_written}
    place_names = [(l.get("name") or "").strip() for l in film.get("locations") or []]
    places = {n.lower() for n in place_names}

    def check(data: dict) -> str:
        shots, soft = data.get("shots"), ""
        if not isinstance(shots, list) or not shots:
            return "\"shots\" is a list of shots"
        for i, shot in enumerate(shots, 1):
            if not isinstance(shot, dict) or "# subject" not in text(shot.get("prompt")).lower():
                return f"shot {i} needs a sectioned \"prompt\" starting with # Subject"
            cast = shot.get("cast") if isinstance(shot.get("cast"), list) else []
            for who in cast:
                if str(who).strip().lower() not in names:
                    return f"shot {i} casts \"{who}\", who is not in the bible: use these names only: " + ", ".join(sorted(names))
            # a shot whose people are only in the prompt's words renders from text: their pictures are never attached
            seen = named_in(text(shot.get("prompt")), as_written, place_names)
            if seen and not cast and not soft:
                soft = Soft(f"shot {i} names {', '.join(seen)} in its prompt and has an empty \"cast\": list in \"cast\" the characters seen in the shot")
            spoken = [l for l in shot.get("lines") or [] if isinstance(l, dict) and text(l.get("text")).strip()]
            if not spoken and not soft and any(len(q.split()) >= 3 for q in spoken_in(text(shot.get("prompt")), as_written)):
                soft = Soft(f"shot {i} has speech inside its prompt and no \"lines\": the exact words go in \"lines\", each with its speaker")
            if shot.get("location") and str(shot["location"]).strip().lower() not in places:
                return f"shot {i} is set at \"{shot['location']}\", which is not in the bible: use these only: " + ", ".join(sorted(places))
            lines = [l for l in shot.get("lines") or [] if isinstance(l, dict) and text(l.get("text")).strip()]
            words = sum(len(text(l.get("text")).split()) for l in lines)
            if words > seconds * 3:
                return f"shot {i} has {words} spoken words for {seconds} s: at most {seconds * 3}, short and speakable"
            for line in lines:
                if text(line.get("speaker")).strip().lower() not in names:
                    return f"a line of shot {i} has no speaker from the bible"
        return soft
    return check


def write_stage(run: dict, stop: threading.Event) -> None:
    project, asked = run["project"], run["asked"]
    film = film_of(project)
    need_cast, need_places = not film.get("characters"), not film.get("locations")
    if need_cast or need_places:
        say(run, "Writing the cast and the places")
        data = brain(film, "cast and places", f"""{context(film)}

Write the {'characters' if need_cast else ''}{' and ' if need_cast and need_places else ''}{'locations' if need_places else ''} this film needs: {'2 to 4 characters' if need_cast else ''}{', ' if need_cast and need_places else ''}{'1 to ' + str(min(asked['scenes'], 4)) + ' locations' if need_places else ''}, specific and photographable, no clichés.{' Also give "world": two sentences of where and when it is and how it looks.' if not film.get('world') else ''}
Return JSON: {{"world"?:"...", "characters":[{{"name","age","gender","ethnicity","faceFeatures","hair","clothing","voice":{{"ageGender","accent","timbre","pitch","pacing"}}}}], "locations":[{{"name","interiorExterior":"interior"|"exterior","description","timeOfDay","weather","architecture","textures","practicalLights"}}]}}
Each field a compact phrase list like a casting or location sheet, not sentences (a location's description: one or two sentences).""",
                     check_bible(need_cast, need_places))
        wait(stop, 0)

        def put(f):
            if data.get("world") and not f.get("world"):
                apply(f, {"type": "update_project", "world": text(data["world"])})
            for c in (data.get("characters") or []) if need_cast else []:
                if isinstance(c, dict) and text(c.get("name")).strip():
                    apply(f, {"type": "add_character", **{k: v for k, v in c.items() if k != "voice"}})
                    voice = c.get("voice") if isinstance(c.get("voice"), dict) else None
                    if voice:
                        apply(f, {"type": "add_voice", **voice, "name": f"{c['name']}'s voice", "characterName": c["name"]})
            for l in (data.get("locations") or []) if need_places else []:
                if isinstance(l, dict) and text(l.get("name")).strip():
                    apply(f, {"type": "add_location", **l})
        film = change(project, put)
        say(run, f"Cast {len(film.get('characters') or [])} and found {len(film.get('locations') or [])} place{'s' if len(film.get('locations') or []) != 1 else ''}")
    if not film.get("scenes"):
        say(run, f"Writing {asked['scenes']} scenes")
        data = brain(film, "scenes", f"""{context(film)}

Break this film into exactly {asked['scenes']} scenes, in order, a story with a beginning, a middle and an end. Each is {asked['shotsPerScene']} shots of {asked['seconds']} s.
Return JSON: {{"scenes":[{{"name":"<short scene name>","synopsis":"<what happens, in one or two sentences>"}}]}}""", check_scenes)
        wait(stop, 0)
        film = change(project, lambda f: [apply(f, {"type": "create_scene", "name": s["name"], "synopsis": text(s.get("synopsis"))})
                                         for s in data["scenes"][:asked["scenes"]] if isinstance(s, dict) and text(s.get("name")).strip()])
    for index, scene in enumerate(film.get("scenes") or []):
        if scene.get("shots"):
            continue
        wait(stop, 0)
        say(run, f"Writing the shots of \"{scene['name']}\"")
        data = brain(film, "shots", f"""{context(film, scene)}

Write exactly {asked['shotsPerScene']} shots for this scene, in order, each {asked['seconds']} seconds: one or two actions a shot. Vary size, angle and movement like a seasoned director. A new size or angle is a cut to a new shot. "continues": true only for a shot that carries the shot before on in the same size and angle, starting where it ends; a new angle is false.
{PROMPT_FORMAT}
Spoken lines: short and speakable, at most {asked['seconds'] * 3} words a shot in all, each with its speaker's name from the bible; a shot without lines is silent.
"cast" names every character seen in the shot and "location" the place, by their names in the bible: a character in the prompt is in "cast", and what anyone says is in "lines", never only in the prompt.
Return JSON: {{"shots":[{{"name":"<short>","intent":"<one line for the crew: what the shot is for>","prompt":"<sectioned prompt>","size","angle","movement","lens","continues":false,"cast":["<names>"],"location":"<name>","lines":[{{"speaker":"<name>","text":"<the exact words>"}}],"soundscape":"<ambience and physical sound>"}}]}}
Spec vocabulary: {SIZES_WORDS}.""", check_shots(film, asked["seconds"]))

        def put(f, scene_id=scene["id"], shots=data["shots"][:asked["shotsPerScene"]]):
            for n, s in enumerate(shots):
                apply(f, {"type": "create_shot", "scene": scene_id, "name": text(s.get("name")) or f"Shot {n + 1:02d}",
                          "intent": text(s.get("intent")), "prompt": text(s.get("prompt")),
                          "spec": {"durationSec": asked["seconds"], **{k: s.get(k) or None for k in ("size", "angle", "movement", "lens")}},
                          "cast": [str(x) for x in s.get("cast") or []], "location": s.get("location") or "",
                          "lines": [l for l in s.get("lines") or [] if isinstance(l, dict)], "soundscape": text(s.get("soundscape")),
                          "opensOn": opening(s, shots[n - 1] if n else None)})
        film = change(project, put)
        say(run, f"Wrote {len(film['scenes'][index].get('shots') or [])} shots of \"{scene['name']}\"")
    run["counts"]["scenes"] = len(film.get("scenes") or [])


def opening(shot: dict, before: dict | None) -> str:
    """What a written shot opens on. A beat carried on in the same size and angle opens on the shot before (a join:
    the camera does not snap back). A new angle is a cut: on the cast's stills, the same pictures in the same order
    and no first frame, where the shot has cast and Ref2VA is here; a join carries no reference pictures, and a new
    angle written as one is how a face slips. Without Ref2VA a new angle still opens on the shot before, the only
    continuity a text clip has; the first shot of a scene opens on text then."""

    same = lambda key: (text(shot.get(key)) or "").strip().lower() == (text((before or {}).get(key)) or "").strip().lower()
    carries = before is not None and shot.get("continues") in (True, "true") and same("size") and same("angle")
    if carries:
        return "previous"
    if shot.get("cast") and ref2va():
        return "cast"
    return "previous" if before is not None else "text"


# ---------- casting ----------

def queue(body: dict) -> dict | None:
    try:
        return submit(body)
    except HTTPException as error:
        raise Failed(str(error.detail)) from None


def cast_stage(run: dict, stop: threading.Event) -> None:
    """A reference still and its turnaround for each character without a picture, a plate for each place without one:
    those a shot of the run has in its cast or as its place (a portrait of someone no shot shows is never used)."""

    project, casting = run["project"], run.setdefault("casting", {})
    film = film_of(project)
    used = [tf_of(s) for sc in film.get("scenes") or [] for s in sc.get("shots") or [] if (s.get("prompt") or "").strip()]
    seen = {i for tf in used for i in tf.get("cast") or []} | {tf.get("location") for tf in used if tf.get("location")}
    for c in film.get("characters") or []:
        if not (c.get("images") or []) and c["id"] not in casting and c["id"] in seen:
            casting[c["id"]] = {"kind": "cast", "name": c.get("name"), "step": "scout", "job": None}
    for l in film.get("locations") or []:
        if not (l.get("images") or []) and (l.get("description") or "").strip() and l["id"] not in casting and l["id"] in seen:
            casting[l["id"]] = {"kind": "place", "name": l.get("name"), "step": "scout", "job": None}
    save(run)
    while True:
        open_ = [(sid, one) for sid, one in casting.items() if one["step"] != "done"]
        if not open_:
            break
        for sid, one in open_:
            job = job_of(one["job"]) if one.get("job") else None
            if job is None or job.get("status") == "cancelled":  # not queued yet, or lost to a restart: queued (again)
                cast_queue(run, sid, one)
            elif job.get("status") == "done":
                cast_pin(run, sid, one, job)
            elif job.get("status") == "failed":
                say(run, f"{one['name']}: the {'turnaround' if one['step'] == 'turn' else 'still'} failed ({job.get('error') or 'no reason'}); carrying on without it")
                one["step"] = "done"
        counts(run)
        save(run)
        if any(one["step"] != "done" for one in casting.values()):
            wait(stop)


def cast_queue(run: dict, sid: str, one: dict) -> None:
    film = film_of(run["project"])
    sheet = next((x for x in (film.get("characters") or []) + (film.get("locations") or []) if x.get("id") == sid), None)
    if sheet is None:
        one["step"] = "done"
        return
    if one["step"] == "turn":
        body = {"kind": "turnaround", "title": "Turnaround: " + sheet.get("name", ""), "params": {"image": (sheet.get("images") or [None])[0], "size": 1024, "strength": 1, "seed": 0}}
    elif one["kind"] == "cast":
        body = {"kind": "scout", "title": "Reference: " + sheet.get("name", ""), "params": {"prompt": sheet_picture(sheet), "count": 1, "seed": 1, "width": 1024, "height": 1024}}
    else:
        body = {"kind": "scout", "title": "Plate: " + sheet.get("name", ""), "params": {"prompt": plate_picture(sheet), "count": 1, "seed": 1, "width": 1344, "height": 768}}
    try:
        job = queue(body)
    except Failed as error:
        say(run, f"{one['name']}: {'no turnaround' if one['step'] == 'turn' else 'no still'} ({error}); carrying on without it")
        one["step"] = "done"
        return
    one["job"] = job["id"]
    say(run, body["title"], job=job["id"])


def cast_pin(run: dict, sid: str, one: dict, job: dict) -> None:
    outputs = job.get("outputs") or []
    keep = [p for p in outputs if re.search(r"turn_(sheet|view\d)\.png$", p)] if one["step"] == "turn" else [p for p in outputs if not p.endswith("sheet.jpg")][:1]
    key = "characters" if one["kind"] == "cast" else "locations"

    def pin(film):
        sheet = next((x for x in film.get(key) or [] if x.get("id") == sid), None)
        if sheet is not None:
            sheet.setdefault("images", []).extend(p for p in keep if p not in sheet["images"])
    change(run["project"], pin)
    say(run, f"Pinned {len(keep)} picture{'s' if len(keep) != 1 else ''} to {one['name']}")
    one["step"], one["job"] = ("turn", None) if one["kind"] == "cast" and one["step"] == "scout" and keep else ("done", None)


# ---------- rendering, checking, retakes ----------

def on_disk(path: str | None) -> bool:
    if not path:
        return False
    target = (HOME / path).resolve()
    return HOME.resolve() in target.parents and target.is_file()


def render_stage(run: dict, stop: threading.Event) -> None:
    project, shots = run["project"], run.setdefault("shots", {})
    film = film_of(project)
    run["order"] = [s["id"] for sc in film.get("scenes") or [] for s in sc.get("shots") or [] if (s.get("prompt") or "").strip()]
    for sc in film.get("scenes") or []:
        for s in sc.get("shots") or []:
            if s["id"] in run["order"] and s["id"] not in shots:
                kept = kept_take(s)
                # a shot rendered before the Director came to it is kept as it is
                there = bool(kept and on_disk(kept))
                shots[s["id"]] = {"scene": sc["id"], "name": s.get("name"), "attempts": [], "settled": there,
                                  "best": kept if there else None, "kept": there, **({"outcome": "kept"} if there else {})}
    save(run)
    while True:
        for sid in run["order"]:
            follow(run, sid)
        fill(run)
        counts(run)
        busy = [a for s in shots.values() for a in s.get("attempts") or [] if a.get("status") == "queued"]
        run["state"] = "retaking" if any(a.get("retake") for a in busy) else "rendering" if busy or any(
            not shots[sid]["attempts"] and not shots[sid]["settled"] for sid in run["order"]) else "checking"
        save(run)
        if all(shots[sid]["settled"] for sid in run["order"]):
            break
        wait(stop)


def failed_check(take: dict) -> bool:
    """Whether a take's own QA gate or shot check failed it (Slate rates a take good when it queues it, before that)."""

    return any(isinstance(take.get(key), dict) and take[key].get("passed") is False for key in ("qa", "shot"))


def kept_take(shot: dict) -> str | None:
    """The clip a shot rendered before the Director came to it stands on: its circled take, else its newest take on
    disk that is not rated no-good and did not fail its own check; None when it has none."""

    takes = [t for t in shot.get("takes") or [] if isinstance(t, dict) and on_disk(t.get("mediaPath"))]
    circled = next((t["mediaPath"] for t in takes if t.get("rating") == "circled"), None)
    return circled or next((t["mediaPath"] for t in takes if t.get("rating") != "no-good" and not failed_check(t)), None)


def at_once() -> int:
    """Render jobs a run keeps in the queue: one for each machine that renders, at most AT_ONCE. One machine renders
    one clip at a time; more queued at once only sit there, and a retake then waits behind the next shots."""

    try:
        return max(1, min(AT_ONCE, int(machines())))
    except (TypeError, ValueError):
        return 1


def fill(run: dict) -> None:
    """Shots without a take queued in order while fewer than ``at_once()`` render; one that opens on the shot before
    waits for it."""

    shots = run["shots"]
    busy = sum(1 for s in shots.values() for a in s.get("attempts") or [] if a.get("status") == "queued")
    film = film_of(run["project"])
    for sid in run["order"]:
        if busy >= at_once():
            return
        rec = shots[sid]
        if rec["settled"] or rec["attempts"]:
            continue
        try:
            scene, shot, index = slate.find_shot(film, sid)
        except HTTPException:
            rec.update(settled=True, outcome="removed")  # the shot was removed on the page meanwhile
            continue
        before = scene["shots"][index - 1] if index else None
        if tf_of(shot).get("start") == "previous" and before and before["id"] in shots and not shots[before["id"]]["settled"]:
            continue
        render(run, film, sid, seed=None)
        busy += 1 if (rec["attempts"] or [{}])[-1].get("status") == "queued" else 0  # a refusal holds no place in the queue


def render(run: dict, film: dict, sid: str, seed: int | None, retake: bool | None = None) -> None:
    """One take of a shot queued as slate.js slRender queues it; at ``seed`` for a retake (or, ``retake`` False, for
    the same take again after the workload limits stopped it)."""

    retake = seed is not None if retake is None else retake
    rec = run["shots"][sid]
    try:
        scene, shot, index = slate.find_shot(film, sid)
    except HTTPException:  # removed on the page while it was retaken or queued again: the run goes on without it
        rec.update(settled=True, outcome="removed")
        return
    tf = tf_of(shot)
    before = scene["shots"][index - 1] if index else None
    chain = (run["shots"].get(before["id"]) or {}).get("best") if before and tf.get("start") == "previous" else None
    made = slate.compile_shot(film, sid, chain)
    plan = dict(made["plan"])
    if seed is not None:
        plan["seed"] = seed
    if run["asked"].get("quality") and not plan.get("references"):
        plan["quality"] = run["asked"]["quality"]
    prompt = tf["h3"].strip() if tf.get("h3Auto") is False and (tf.get("h3") or "").strip() else made["prompt"]
    output = f"slate/{film['id']}/takes/{sid}-{int(time.time() * 1000):x}.mp4"
    own = (film.get("tf") or {}).get("loras") or []
    d = film.get("defaults") or {}
    params = {**plan, "prompt": prompt, "output": output,
              **({"loras": plan["loras"]} if plan.get("loras") else {"loras": own} if own and not plan.get("references") else {}),
              "brain": {"backend": d.get("brain"), "endpoint": d.get("localEndpoint"), "model": d.get("localModel")},
              "slate": {"project": film["id"], "scene": scene["id"], "shot": sid}}
    try:
        job = queue({"kind": "video", "title": f"{scene.get('name')} · {shot.get('name')}", "params": params})
    except Failed as error:
        # refused before it started (the memory guard, a missing model): no clip, no take, never counted as rendered
        beside = resident()
        rec["attempts"].append({"job": None, "output": output, "seed": plan["seed"], "prompt": prompt, "status": "failed", "refused": True,
                                "passed": False, "reason": str(error), "retake": retake, "checked": True})
        say(run, f"{shot.get('name')}: refused by the queue ({error})" + (f" Loaded beside the queue now: {beside}." if beside else ""), shot=sid)
        return
    rec["attempts"].append({"job": job["id"], "output": output, "seed": plan["seed"], "prompt": prompt, "status": "queued",
                            "passed": None, "reason": "", "retake": retake, "checked": False,
                            "settings": {k: (job.get("params") or params).get(k) for k in ("preset", "quality", "frames", "seconds", "width", "height", "loras") if (job.get("params") or params).get(k) is not None}})
    say(run, f"{'Retake' if retake else 'Rendering' if seed is None else 'Rendering again'}: {scene.get('name')} · {shot.get('name')}" + (f" at seed {plan['seed']}" if seed is not None else ""), job=job["id"], shot=sid)


def follow(run: dict, sid: str) -> None:
    rec = run["shots"][sid]
    if rec["settled"] or not rec["attempts"]:
        return
    one = rec["attempts"][-1]
    if one["status"] == "queued":
        job = job_of(one["job"])
        if job is None:  # this app restarted: the clip is there or the take is queued again
            if on_disk(one["output"]):
                one["status"] = "done"
                record(run, sid, one, {})
            else:
                rec["attempts"].pop()
                return
        elif job.get("status") == "cancelled":
            rec["attempts"].pop()  # taken out of the queue: queued again, not a retake
            return
        elif job.get("status") == "failed" and WORKLOAD_STOP in (job.get("error") or ""):
            # heat or memory pressure stopped it: not a take and not a retake (below: queued once more, then left out)
            one.update(status="stopped", passed=None, checked=True, reason=job.get("error") or "")
            say(run, f"{rec['name']}: {one['reason'][:200]}", shot=sid)
        elif job.get("status") == "failed":
            one.update(status="failed", passed=False, checked=True, reason=job.get("error") or "the render failed")
            say(run, f"{rec['name']}: the render failed ({one['reason'][:160]})")
        elif job.get("status") == "done":
            one["status"] = "done"
            record(run, sid, one, job)
        else:
            return
    if one["status"] == "stopped":
        # the same take queued once more at its seed (the queue's own gate waits for the machine to cool before it
        # starts it), counted from the end: a stop of an earlier take does not use this take's try. Here too when the
        # one queued again was lost to a restart or a cancel, so it is queued again rather than left out.
        stops = 0
        for a in reversed(rec["attempts"]):
            if a.get("status") != "stopped" or a.get("seed") != one.get("seed"):
                break
            stops += 1
        if stops < WORKLOAD_STOPS:
            say(run, f"{rec['name']}: queued once more at seed {one.get('seed')}", shot=sid)
            render(run, film_of(run["project"]), sid, one["seed"], retake=one.get("retake", False))
            return
        return settle(run, sid)
    if one["status"] == "done" and not one["checked"]:
        check(run, sid, one)
        if not one["checked"]:
            return
    # checked: a pass (or a clip the gate could not check) settles the shot, a failure is taken again while it may be;
    # a refusal is not (the next seed needs the same memory), nor are the renders the workload limits stopped
    tries = sum(1 for a in rec["attempts"] if a.get("status") != "stopped")
    if one["passed"] is False and not one.get("refused") and tries <= run["asked"]["retakes"]:
        seeds = {a["seed"] for a in rec["attempts"]}
        seed = int(one["seed"] or 0) + 1
        while seed in seeds:
            seed += 1
        render(run, film_of(run["project"]), sid, seed)
        return
    settle(run, sid)


def record(run: dict, sid: str, one: dict, job: dict) -> None:
    """The clip as a take of the shot, as slRender logs it (here when the job is done)."""

    path = (job.get("outputs") or [one["output"]])[0]
    one["output"] = path
    take = {"id": sl_id("take"), "loggedAt": slate.now(), "model": "minimax-h3", "prompt": one.get("prompt") or "", "rating": "", "notes": "",
            "mediaPath": path, "jobId": one.get("job"), "seed": one.get("seed"), "settings": one.get("settings") or {},
            "origin": "The Director" + (f": retake at seed {one.get('seed')}" if one.get("retake") else "")}
    for key in ("qa", "shot"):
        if job.get(key):
            take[key] = job[key]
    one["take"] = take["id"]

    def put(film):
        try:
            _, shot, _ = slate.find_shot(film, sid)
        except HTTPException:
            return
        shot.setdefault("takes", []).insert(0, take)
    change(run["project"], put)
    say(run, f"{run['shots'][sid]['name']}: take in", shot=sid)


def check(run: dict, sid: str, one: dict) -> None:
    """The QA gate's word on a take: the job's own report, else a QA job of its own; where the gate cannot run, the
    shot check's word, else the take counts as unchecked (it is not failed for it)."""

    job = job_of(one["job"]) if one.get("job") else None
    report = (job or {}).get("qa") or {}
    if report.get("passed") is not None:
        return verdict(run, sid, one, report)
    if one.get("qaJob"):
        gate = job_of(one["qaJob"])
        if gate is None or gate.get("status") in ("failed", "cancelled"):
            return verdict(run, sid, one, {}, (gate or {}).get("error") or "the QA gate stopped")
        if gate.get("status") == "done":
            return verdict(run, sid, one, gate.get("qa") or {})
        return
    if qa_start is None:
        return verdict(run, sid, one, {}, "the QA gate is not part of this app")
    try:
        gate = qa_start({"path": one["output"], "prompt": one.get("prompt") or "", "seed": one.get("seed"), "title": "QA gate: " + (run["shots"][sid]["name"] or "")})
    except HTTPException as error:
        return verdict(run, sid, one, {}, str(error.detail))
    one["qaJob"] = gate["id"]
    run["state"] = "checking"
    say(run, f"Checking {run['shots'][sid]['name']}", job=gate["id"], shot=sid)


def reason_of(take: dict) -> str:
    """Why one checked take failed: the shot check's reason, else the QA gate's notes on the gates that failed (the
    line heard, the look held)."""

    said = [str(take.get("reason") or take.get("why") or "")]
    said += [f"{gate}: {take[gate]['note']}" for gate in ("audio", "identity")
             if isinstance(take.get(gate), dict) and take[gate].get("result") == "fail" and take[gate].get("note")]
    return "; ".join(one for one in said if one)


def verdict(run: dict, sid: str, one: dict, report: dict, unchecked: str = "") -> None:
    key = "qa"
    if report.get("passed") is None:
        shot_check = (job_of(one["job"]) or {}).get("shot") or {} if one.get("job") else {}
        if shot_check.get("passed") is False:
            report, key = {"passed": False, "takes": shot_check.get("takes") or [], "why": "the shot check"}, "shot"
    takes = report.get("takes") or []
    reason = "; ".join(filter(None, (reason_of(t) for t in takes if isinstance(t, dict) and t.get("passed") is False)))
    one.update(checked=True, passed=report.get("passed"), reason=reason or report.get("why") or unchecked)
    if report.get("passed") is not None and one.get("take"):  # a QA job of its own: its report on the take, as a job's is
        def put(film):
            try:
                _, shot, _ = slate.find_shot(film, sid)
            except HTTPException:
                return
            for take in shot.get("takes") or []:
                if take.get("id") == one["take"] and not take.get(key):
                    take[key] = report
        change(run["project"], put)
    name = run["shots"][sid]["name"]
    say(run, f"{name}: " + ("passed the check" if one["passed"] else "failed the check" + (f" ({one['reason'][:160]})" if one["reason"] else "")
                           if one["passed"] is False else "not checked" + (f" ({unchecked[:160]})" if unchecked else "")), shot=sid)


def settle(run: dict, sid: str) -> None:
    """The shot's takes rated: the first that passed (or could not be checked) good, a failed one no-good."""

    rec = run["shots"][sid]
    best = next((a for a in rec["attempts"] if a["status"] == "done" and a["passed"] is not False), None)
    ratings = {a["take"]: "good" if a is best else "no-good" if a["passed"] is False else "" for a in rec["attempts"] if a.get("take")}

    def rate(film):
        try:
            _, shot, _ = slate.find_shot(film, sid)
        except HTTPException:
            return
        for take in shot.get("takes") or []:
            if take.get("id") in ratings and take.get("rating") != "circled":
                take["rating"] = ratings[take["id"]]
    if ratings:
        change(run["project"], rate)
    last = next((a for a in reversed(rec["attempts"]) if a["status"] != "stopped"), None) or (rec["attempts"] or [{}])[-1]
    # a take that failed its check is the shot's no-good take, in the Library and among its takes, never in the cut
    # unless someone circles it; a refusal or a failed render made no clip at all
    outcome = ("passed" if best["passed"] else "unchecked") if best else \
        "failed" if any(a["status"] == "done" for a in rec["attempts"]) else \
        "stopped" if rec["attempts"][-1].get("status") == "stopped" else "refused" if last.get("refused") else "broken"
    why = rec["attempts"][-1].get("reason") if outcome == "stopped" else last.get("reason")
    rec.update(settled=True, best=(best or {}).get("output"), outcome=outcome, why="" if best else (why or "")[:600])
    rec.pop("fallback", None)
    if not best:
        say(run, f"{rec['name']}: {OUTCOMES[outcome]}, left out of the cut" +
            (": its take is in the Library and among the shot's takes, rated no-good (circle it to cut it in)" if outcome == "failed" else ""), shot=sid)


# ---------- cutting ----------

def rough_make(body: dict) -> dict:
    """POST /api/roughcut in this process (roughcut.py): the cut on the song's beats, its project and export job."""

    import roughcut

    if roughcut.queue is None:
        raise LookupError("the rough cut is not part of this app")
    return roughcut.make(body)


def rough_status(project: str) -> dict:
    """GET /api/roughcut/{project}: foley, export, song, files, done or failed."""

    import roughcut

    return roughcut.status(project)


def cut_stage(run: dict, stop: threading.Event) -> None:
    if not run["asked"].get("cut"):
        return
    film = film_of(run["project"])
    clips, takes, left, gone = [], [], [], []
    for sid in run["order"]:
        rec = run["shots"].get(sid) or {}
        path = rec.get("best")  # a take that failed its check is not cut in unless it is circled
        try:
            _, shot, _ = slate.find_shot(film, sid)
        except HTTPException:
            continue
        circled = next((t["mediaPath"] for t in shot.get("takes") or [] if t.get("rating") == "circled" and on_disk(t.get("mediaPath"))), None)
        path = circled or path
        if path and on_disk(path):
            take = next((t for t in shot.get("takes") or [] if t.get("mediaPath") == path), {})
            frames = (take.get("settings") or {}).get("frames")
            clips.append({"path": path, "in": 0, "out": 0, "duration": round(frames / 24, 3) if frames else float((shot.get("spec") or {}).get("durationSec") or 5)})
            takes.append(take)
        else:
            left.append(f"{shot.get('name')} ({OUTCOMES.get(outcome_of(rec) or '', 'no take')})")
            if outcome_of(rec) in IN_CUT:  # its outcome would have cut it in: its file is what is missing
                gone.append(shot.get("name") or "?")
    if not clips:  # the run ends on its outcomes, as one that cut does
        raise Failed(summary(run) + ", nothing to cut" + (f" (the take of {', '.join(gone)} is no longer on disk)" if gone else ""))
    if left:
        say(run, f"Left out of the cut: {', '.join(left)}")
    settings = (takes[0] if takes else {}).get("settings") or {}
    width, height = (settings.get("width"), settings.get("height")) if settings.get("width") else sizes.get(settings.get("preset"), (1920, 1080))
    title = film.get("name") or "Film"
    song = run["asked"].get("song") or ""
    if song:  # the rough cut: every cut on the song's bar lines, the song under it, FCPXML, EDL and a Resolve zip
        say(run, f"Cutting {len(clips)} shots on the song's bars")
        try:
            made = rough_make({"clips": [c["path"] for c in clips], "song": song, "cut": "natural", "foley": False, "order": "given",
                               "title": title, "width": int(width), "height": int(height)})
        except LookupError as error:
            say(run, f"{error}: cutting in the Editor instead")
        except HTTPException as error:
            say(run, f"The rough cut was refused ({str(error.detail)[:200]}): cutting in the Editor instead, the song left out")
        else:
            run["cut"] = {"project": made["project"], "job": made.get("job"), "files": {}, "beats": made.get("beats")}
            say(run, "Exporting the cut", job=made.get("job"))
            while True:
                state = rough_status(made["project"])
                if state.get("stage") == "done":
                    break
                if state.get("stage") == "failed":
                    raise Failed("the rough cut failed: " + str(state.get("error") or "no reason given"))
                if state.get("job") and state["job"] != run["cut"]["job"]:
                    run["cut"]["job"] = state["job"]
                    run["current"]["job"] = state["job"]
                    save(run)
                wait(stop)
            files = state.get("files") or {}
            run["cut"]["files"] = {"mp4": state.get("video"), **{k: files.get(k) for k in ("fcpxml", "edl", "zip")}}
            say(run, "The cut is ready: " + str(run["cut"]["files"]["mp4"]))
            return
    if editor_new is None or editor_save is None:
        raise Failed("this app has no Editor to cut in")
    say(run, f"Cutting {len(clips)} shots in the Editor")
    edit = editor_new({"name": title + " · the Director's cut"})
    edit["timeline"] = clips
    editor_save(edit["id"], edit)
    job = queue({"kind": "export", "title": "The Director's cut: " + title, "params": {"clips": clips, "width": int(width), "height": int(height)}})
    run["cut"] = {"project": edit["id"], "job": job["id"], "files": {}}
    say(run, "Exporting the cut", job=job["id"])
    done = finish(run, job["id"], stop)
    base = f"/api/export/timeline/{edit['id']}"
    run["cut"]["files"] = {"mp4": (done.get("outputs") or [None])[0], "fcpxml": base + ".fcpxml", "edl": base + ".edl", "zip": base + ".zip"}
    say(run, "The cut is ready: " + str(run["cut"]["files"]["mp4"]))


def finish(run: dict, job_id: str, stop: threading.Event) -> dict:
    while True:
        job = job_of(job_id)
        if job is None:
            raise Failed("the cut's job is gone (the app restarted): direct it again to cut")
        if job.get("status") == "done":
            return job
        if job.get("status") in ("failed", "cancelled"):
            raise Failed("the cut failed: " + str(job.get("error") or job.get("status")))
        wait(stop)
