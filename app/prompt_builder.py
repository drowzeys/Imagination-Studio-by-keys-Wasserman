"""Prompt Builder: a MiniMax H3 prompt from your own cast, a scenario, a place, a film look and an idea.

The page (app/static/builder.js) keeps a cast of the user's own characters (`cast.json` in the studio folder: name,
look, wardrobe, voice, era), offers drop-downs of scenarios, places, times, film looks and moods, and asks the Studio's
brain (slate.ask: Bolt or the brain set in Brains & tools) to write the prompt in the shot-list form the Create page's
importer reads (script_lines.py): timed shots with `Image:` and `Dialogue (Name, how): "..."`, then
`overall_soundscape:` and `non_diegetic_music:`. Without a brain the same form is written from the choices alone, with
empty lines to fill. *Send to 3. Words / Sound* (the page) puts the result into the Create wizard: scene, action,
camera and story beats into step 1, the lines into step 3, the length with the split choice when it is over 15 s.

The film looks and the starter cast are the Studio's own: styles of an era or a genre, and original characters. The
brain is told to use only the characters given.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request

import script_lines

router = APIRouter()
HOME: Path = Path(".")
CAST_LIMIT = 200  # characters kept in one cast
TEXT_LIMIT = 600  # characters a field of a character keeps
ENSEMBLE_WARN = 4  # MiniMax H3 keeps a few people apart in one shot; more than this blur together

SCENARIOS = ["A confrontation", "A confession", "A chase", "A reunion", "Planning a heist", "A standoff",
             "An interrogation", "A first meeting", "A farewell", "An argument", "A comic misunderstanding",
             "An ambush", "A celebration", "A rescue", "A bet between rivals", "A team assembles"]
LOCATIONS = ["a stone cave lit by torches", "a rooftop at night above a neon city", "a roadside diner",
             "an empty warehouse", "a desert highway", "a rain-soaked street with neon signs", "a moving train carriage",
             "a cramped police station", "a mountain temple courtyard", "a beach at dawn", "a starship corridor",
             "a sitcom living-room set", "a casino floor", "a forest clearing", "a jungle river bank",
             "a 1980s video arcade", "a smoky jazz bar", "a hotel penthouse"]
TIMES = ["dawn", "midday", "golden hour", "dusk", "night", "a thunderstorm"]
MOODS = ["tense", "comic", "romantic", "melancholic", "triumphant", "eerie", "over-the-top action"]
SHOT_PLANS = {"auto": "as the scene needs", "one": "one continuous shot", "few": "two or three shots",
              "many": "four or five shots with hard cuts"}
# a film look is an era's or a genre's way of shooting, never one film's
LOOKS = {
    "": "",
    "80s-action": "a 1980s action film: 35mm grain, smoke and hard backlight, warm practical lights, bold colours, slow-motion beats",
    "80s-vhs": "late-1980s home video: VHS softness, tape noise, blown highlights, on-camera flash",
    "90s-hk-action": "a 1990s Hong Kong action comedy: saturated colour, crash zooms, wire-assisted moves, punchy edits",
    "90s-buddy-cop": "a 1990s buddy-cop film: anamorphic flares, steel-blue night streets, sodium lamps, wisecracking energy",
    "90s-sitcom": "an early-1990s three-camera sitcom: flat bright studio lighting, a fixed set, a live-audience feel",
    "2000s-heist": "an early-2000s heist film: teal-and-orange grade, glossy night city, smooth dolly moves, split-second cuts",
    "2000s-handheld": "a mid-2000s gritty thriller: shaky handheld camera, desaturated greens, quick zooms",
    "70s-grindhouse": "a 1970s grindhouse film: scratched print, faded warm colour, crash zooms, gritty locations",
    "40s-noir": "a 1940s film noir: black and white, hard shadows through blinds, rain, cigarette haze",
    "60s-technicolor": "a 1960s Technicolor picture: rich saturated primaries, painted backdrops, smooth crane moves",
    "wuxia": "a wuxia fantasy: flowing robes, wind, bamboo and mist, graceful aerial moves, soft golden light",
    "modern-prestige": "a modern prestige drama: natural soft light, shallow depth of field, restrained camera",
    "mixed-eras": "a crossover of eras: each character keeps the wardrobe, grade and manner of their own decade",
}
STARTER_CAST = [
    {"name": "Rex Callahan", "era": "80s-action",
     "look": "a broad-shouldered man in his forties, sun-creased face, short grey-flecked hair, a scar through one eyebrow",
     "wardrobe": "a faded denim jacket over a grey vest, dog tags, scuffed combat boots",
     "voice": "a deep, gravelly voice with short clipped sentences"},
    {"name": "Dee Morales", "era": "90s-buddy-cop",
     "look": "a lean woman in her thirties, dark curly hair tied back, sharp eyes and a quick crooked grin",
     "wardrobe": "a brown leather jacket, white T-shirt, badge clipped to her belt",
     "voice": "a fast, wry voice with a teasing edge"},
    {"name": "Theo Lark", "era": "2000s-heist",
     "look": "a slim man in his thirties, slicked-back hair, a neat goatee, always relaxed",
     "wardrobe": "a charcoal slim suit, open collar, tinted glasses",
     "voice": "a smooth, low, unhurried voice"},
    {"name": "Mei Lan", "era": "wuxia",
     "look": "a young woman with long black hair pinned with a jade comb, calm watchful eyes",
     "wardrobe": "faded crimson travelling robes, a long sword across her back",
     "voice": "a calm, clear voice that rarely rises"},
    {"name": "Old Harlan", "era": "",
     "look": "an old man with a thick grey beard, ruddy cheeks and laughing eyes",
     "wardrobe": "a stained leather apron over a wool shirt, sleeves rolled up",
     "voice": "a warm, rough drawl"},
    {"name": "Pip Okafor", "era": "90s-sitcom",
     "look": "a gangly young man with big hopeful eyes and a nervous smile",
     "wardrobe": "an oversized checked coat, a bunch of slightly crushed flowers",
     "voice": "a nervous, slightly high voice that speeds up when excited"},
]

SYSTEM = """You write prompts for MiniMax H3, a video model that makes a clip with picture, voices and sound together.
Write in English. Use only the characters you are given, by the names given, with their looks, wardrobes and voices.
Do not add other named characters, real people, or characters from existing films, shows or games.
Describe only what is seen and heard; do not write "no X" or "without X" (the model tends to show what it is told
is absent): say what is there instead.

Answer with the prompt only, in exactly this layout:

<a one-line title>, <N> seconds, 16:9, 24fps

integrated_multimodal_description:
<one paragraph: the place, light, film look and atmosphere, and who is in the frame>

Character design:
<Name>: <look and wardrobe in one line>
(one line per character)

[Shot 1] 00:00-00:05
Shot size: <...>
Camera movement: <...>
Image: <what happens, concretely, in present tense>
Dialogue (<Name>, <how it is said>): "<the words>"

[Shot 2] 00:05-00:10
...

overall_soundscape:
<the sounds of the place, one paragraph>

non_diegetic_music:
<the score, or N/A>

Rules: the shots' times run from 00:00 to the length given, back to back. Each Dialogue line is one character's
line, short enough to say in the shot's time (about 2.5 words a second); a shot may have no dialogue. Give each
character at least one line when the scene allows it."""


# the Studio's queue, given by server.py: submit(body) queues a job as POST /api/jobs does; lookup(id) is its record
submit = None
lookup = None
LOCK = threading.Lock()  # cast.json is read and written by the page and by a reference sheet's thread
SHEET_POLL_S = 5
SHEET_SIZE = 1024  # a turnaround view's side: at 512 a full figure's face is about 20 pixels and comes out soft
PICTURE = re.compile(r"scouts/[A-Za-z0-9_-]{1,40}/[A-Za-z0-9_.-]{1,80}\.(?:png|jpg)")


def attach(home: Path, queue=None, find=None) -> APIRouter:
    global HOME, submit, lookup
    HOME, submit, lookup = home, queue, find
    return router


def cast_file() -> Path:
    return HOME / "builder" / "cast.json"


def clean_character(one: dict) -> dict:
    if not isinstance(one, dict):
        raise HTTPException(400, "a character is an object")
    name = str(one.get("name") or "").strip()[:80]
    if not name:
        raise HTTPException(400, "a character needs a name")
    out = {"id": str(one.get("id") or "") or uuid.uuid4().hex[:10], "name": name}
    for key in ("look", "wardrobe", "voice"):
        out[key] = str(one.get(key) or "").strip()[:TEXT_LIMIT]
    out["era"] = str(one.get("era") or "") if str(one.get("era") or "") in LOOKS else ""
    # a reference sheet made for the character (make_sheet): the still, the turnaround's sheet and views, and where the
    # making of it stands. Only pictures the Studio made (scouts/<job>/...) are kept
    sheet = one.get("sheet") if isinstance(one.get("sheet"), dict) else {}
    kept = {key: sheet[key] for key in ("still", "sheet") if isinstance(sheet.get(key), str) and PICTURE.fullmatch(sheet[key])}
    views = [v for v in sheet.get("views") or [] if isinstance(v, str) and PICTURE.fullmatch(v)][:8]
    state = sheet.get("state") if sheet.get("state") in ("still", "turn", "done", "failed") else ""
    if kept or views or state:
        out["sheet"] = {**kept, "views": views, "state": state, "error": str(sheet.get("error") or "")[:300],
                        "job": str(sheet.get("job") or "")[:40]}
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", out["id"]):
        out["id"] = uuid.uuid4().hex[:10]
    return out


def read_cast() -> list[dict]:
    try:
        saved = json.loads(cast_file().read_text())
        return [clean_character(one) for one in saved if isinstance(one, dict) and one.get("name")][:CAST_LIMIT]
    except (OSError, ValueError, HTTPException):
        return [clean_character({**one, "id": f"starter-{k}"}) for k, one in enumerate(STARTER_CAST)]


def write_cast(cast: list[dict]) -> list[dict]:
    cleaned, seen = [], set()
    for one in cast[:CAST_LIMIT]:
        character = clean_character(one)
        if character["id"] in seen:
            character["id"] = uuid.uuid4().hex[:10]
        seen.add(character["id"])
        cleaned.append(character)
    path = cast_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(cleaned, indent=1))
    temporary.replace(path)
    return cleaned


def sheet_prompt(one: dict) -> str:
    """The reference still: the whole figure, plain background, even light: what the Turnaround LoRA turns well
    (measured: a crop from a busy scene came back as five front views; a full figure on grey turned to profile)."""

    who = one["look"] or "a person"
    wearing = f", wearing {one['wardrobe']}" if one["wardrobe"] else ""
    return (f"Full-body photograph of {who}{wearing}, standing straight and facing the camera, arms relaxed at the "
            "sides. Plain light grey studio background, soft even lighting, the whole figure in frame from head to "
            "feet, sharp focus.")


def update_sheet(cid: str, **change) -> None:
    with LOCK:
        cast = read_cast()
        for one in cast:
            if one["id"] == cid:
                one["sheet"] = {**(one.get("sheet") or {}), **change}
        write_cast(cast)


def wait_for(job_id: str) -> dict | None:
    while True:
        job = lookup(job_id) if lookup else None
        if job is None or job.get("status") in ("done", "failed", "cancelled"):
            return job
        time.sleep(SHEET_POLL_S)


def sheet_run(cid: str, still_job: str) -> None:
    """The still's job, then the turnaround of its picture, recorded on the character as each lands."""

    try:
        job = wait_for(still_job)
        if not job or job.get("status") != "done":
            return update_sheet(cid, state="failed", error=(job or {}).get("error") or "the still was not made")
        still = next((p for p in job.get("outputs") or [] if not p.endswith("sheet.jpg")), "")
        name = next((one["name"] for one in read_cast() if one["id"] == cid), "")
        turn = submit({"kind": "turnaround", "title": "Reference sheet: " + name,
                       "params": {"image": still, "size": SHEET_SIZE, "strength": 1, "seed": 0}})
        update_sheet(cid, still=still, state="turn", job=turn["id"])
        job = wait_for(turn["id"])
        if not job or job.get("status") != "done":
            return update_sheet(cid, state="failed", error=(job or {}).get("error") or "the turnaround was not made")
        outputs = job.get("outputs") or []
        update_sheet(cid, state="done", error="", sheet=next((p for p in outputs if "sheet" in p.rsplit("/", 1)[-1]), ""),
                     views=[p for p in outputs if "view" in p.rsplit("/", 1)[-1]])
    except Exception as failure:  # the reason goes to the character's card
        update_sheet(cid, state="failed", error=str(getattr(failure, "detail", failure))[:300])


@router.post("/api/builder/cast/{cid}/sheet")
def make_sheet(cid: str) -> dict:
    """A reference sheet for one character: a full-body still from their look and wardrobe (Qwen Image), then a
    turnaround of it (MiniMax H3 Ref2VA with the Turnaround LoRA), both queued like any other job."""

    one = next((c for c in read_cast() if c["id"] == cid), None)
    if one is None:
        raise HTTPException(404, "no such character")
    if (one.get("sheet") or {}).get("state") in ("still", "turn"):
        job = lookup((one["sheet"] or {}).get("job") or "") if lookup else None
        if job and job.get("status") in ("queued", "running"):
            raise HTTPException(409, f"{one['name']}'s reference sheet is already being made")
    if submit is None:
        raise HTTPException(503, "the queue is not attached")
    with LOCK:  # a cast saved from the starter list gets its ids written before the thread looks them up
        write_cast(read_cast())
    job = submit({"kind": "scout", "title": "Reference still: " + one["name"],
                  "params": {"prompt": sheet_prompt(one), "count": 1, "seed": 7, "width": 768, "height": 1344}})
    update_sheet(cid, state="still", job=job["id"], error="", still="", sheet="", views=[])
    threading.Thread(target=sheet_run, args=(cid, job["id"]), daemon=True, name="builder-sheet").start()
    return {"job": job["id"]}


@router.get("/api/builder/options")
def options() -> dict:
    return {"scenarios": SCENARIOS, "locations": LOCATIONS, "times": TIMES, "moods": MOODS, "shots": SHOT_PLANS,
            "looks": {key: value for key, value in LOOKS.items() if key}, "starter": STARTER_CAST,
            "ensemble_warn": ENSEMBLE_WARN}


@router.get("/api/builder/cast")
def get_cast() -> list[dict]:
    return read_cast()


@router.put("/api/builder/cast")
def put_cast(body: list = Body(...)) -> list[dict]:
    if not isinstance(body, list):
        raise HTTPException(400, "the cast is a list of characters")
    with LOCK:
        # a sheet being made is the thread's to record: the page's copy may be from before it landed
        now = {one["id"]: one.get("sheet") for one in read_cast() if one.get("sheet")}
        body = [{**one, "sheet": now[one.get("id")]} if isinstance(one, dict) and one.get("id") in now else one for one in body]
        return write_cast(body)


def chosen(body: dict) -> list[dict]:
    """The characters of a request: the cast's, by id, in the order given (up to eight)."""

    cast = {one["id"]: one for one in read_cast()}
    picked = [cast[i] for i in body.get("cast") or [] if isinstance(i, str) and i in cast]
    return picked[:8]


def brief(body: dict, people: list[dict]) -> str:
    """What the brain is asked for: the user's choices, in words."""

    seconds = int(body.get("seconds") or 10)
    look = LOOKS.get(str(body.get("look") or ""), "")
    lines = [f"Length: {seconds} seconds.", f"Shots: {SHOT_PLANS.get(str(body.get('shots') or 'auto'), SHOT_PLANS['auto'])}."]
    for key, label in (("scenario", "Scenario"), ("location", "Place"), ("time", "Time of day"), ("mood", "Mood")):
        value = str(body.get(key) or "").strip()[:200]
        if value:
            lines.append(f"{label}: {value}.")
    if look:
        lines.append(f"Film look: {look}.")
    if seconds > 15:
        # a long video is rendered 15 s at a time, each part opening on the last frame of the one before
        lines.append(f"This is rendered as {-(-seconds // 15)} parts of 15 s, each starting from the last frame of the part "
                     "before. So the shot that ends each 15 s part (at 00:15, 00:30, ...) ends on a shot showing the "
                     "characters' faces, never an insert, a close-up of hands or an empty frame.")
    language = str(body.get("language") or "").strip()[:40]
    if language and language.lower() != "english":
        lines.append(f"The characters speak {language}: write the Dialogue lines in {language}, everything else in English.")
    if people:
        lines.append("Characters:")
        for one in people:
            era = LOOKS.get(one.get("era") or "", "")
            lines.append(f"- {one['name']}: {one['look']}; wears {one['wardrobe']}; voice: {one['voice']}"
                         + (f"; styled after {era}" if era else ""))
    idea = str(body.get("idea") or "").strip()[:2000]
    if idea:
        lines.append(f"What happens: {idea}")
    return "\n".join(lines)


def stamp(seconds: float) -> str:
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def template(body: dict, people: list[dict]) -> str:
    """The prompt's layout from the choices alone, for when no brain answers: lines to fill in by hand."""

    seconds = max(1, int(body.get("seconds") or 10))
    plan = {"one": 1, "few": 3, "many": 5}.get(str(body.get("shots") or "auto"), max(1, min(5, round(seconds / 5))))
    plan = max(1, min(plan, seconds))
    look = LOOKS.get(str(body.get("look") or ""), "")
    place = str(body.get("location") or "a place").strip()
    when = str(body.get("time") or "").strip()
    names = [one["name"] for one in people]
    who = (", ".join(names[:-1]) + " and " + names[-1]) if len(names) > 1 else (names[0] if names else "the characters")
    scene = (f"{place[:1].upper() + place[1:]}{' at ' + when if when else ''}. {(look[:1].upper() + look[1:] + '.') if look else ''} "
             f"{who} {'are' if len(names) != 1 else 'is'} in the frame. {str(body.get('scenario') or '').strip()}"
             f"{', ' + str(body.get('mood')).strip() if body.get('mood') else ''}.").replace(" .", ".").strip()
    out = [f"{str(body.get('scenario') or 'A scene').strip()}, {seconds} seconds, 16:9, 24fps", "",
           "integrated_multimodal_description:", scene, ""]
    if people:
        out.append("Character design:")
        out += [f"{one['name']}: {one['look']}, wearing {one['wardrobe']}." for one in people]
        out.append("")
    edges = [round(seconds * k / plan) for k in range(plan + 1)]
    for k in range(plan):
        speaker = people[k % len(people)] if people else None
        out += [f"[Shot {k + 1}] {stamp(edges[k])}-{stamp(edges[k + 1])}", "Shot size: Medium shot.",
                "Camera movement: Slow push-in." if k else "Camera movement: Fixed camera.",
                f"Image: {str(body.get('idea') or 'What happens in this shot.').strip() if not k else 'What happens next.'}"]
        if speaker:
            out.append(f"Dialogue ({speaker['name']}, in {speaker['voice'] or 'a natural voice'}): \"Write the line here.\"")
        out.append("")
    out += ["overall_soundscape:", f"The natural sound of {place}.", "", "non_diegetic_music:", "N/A"]
    return "\n".join(out)


SHOT = re.compile(r"^\s*\[Shot\s*\d+\]\s*(?P<a>\d{1,2}:\d{2})?\s*[-–—]?\s*(?P<b>\d{1,2}:\d{2})?", re.I | re.M)


def seconds_of(stamp_text: str | None) -> float | None:
    if not stamp_text:
        return None
    minutes, seconds = stamp_text.split(":")
    return int(minutes) * 60 + int(seconds)


def fields(prompt: str) -> dict:
    """The Create wizard's step-1 boxes from a written prompt: `scene` (the description paragraph), `action` (the
    shots' Image lines in order), `camera` (the first shot's camera movement) and `beats` (what happens in each 15 s
    clip of a long video, from the shots' times)."""

    text = script_lines.unfold(prompt.replace("\r\n", "\n"))
    scene = script_lines.section(text, (r"integrated_multimodal_description", r"scene|setting|description"))
    # every clip of a long video reads the scene: the characters' looks go with it, so a clip that opens on a frame
    # without faces (an insert, a close-up of hands) still draws the same people
    cast = re.search(r"^\s*(?:character design|characters|cast)\s*:\s*\n((?:[^\n\[]+\n?)+)", text, re.I | re.M)
    if cast:
        looks = " ".join(line.strip().rstrip(".") + "." for line in cast.group(1).splitlines() if ":" in line)
        if looks:
            scene = (scene + " " + looks).strip()
    shots = []
    marks = list(SHOT.finditer(text))
    for k, mark in enumerate(marks):
        body = text[mark.end():marks[k + 1].start() if k + 1 < len(marks) else len(text)]
        image = re.search(r"^\s*(?:image|action|visual)\s*:\s*(.+)$", body, re.I | re.M)
        camera = re.search(r"^\s*camera(?: movement)?\s*:\s*(.+)$", body, re.I | re.M)
        shots.append({"start": seconds_of(mark.group("a")), "image": image.group(1).strip() if image else "",
                      "camera": camera.group(1).strip() if camera else ""})
    images = [one["image"].rstrip(".") + "." for one in shots if one["image"]]
    beats: list[str] = []
    if shots and all(one["start"] is not None for one in shots):
        windows: dict[int, list[str]] = {}
        for one in shots:
            if one["image"]:
                windows.setdefault(int(one["start"] // 15), []).append(one["image"].rstrip(".") + ".")
        if windows:
            beats = [" ".join(windows.get(k, [])) for k in range(max(windows) + 1)]
            for k in range(1, len(beats)):  # a clip with no shot of its own goes on with the one before
                beats[k] = beats[k] or beats[k - 1]
    return {"scene": scene, "action": " Then ".join(images), "camera": next((one["camera"] for one in shots if one["camera"]), ""),
            "beats": beats}


@router.post("/api/builder/write")
def write(body: dict, request: Request) -> dict:
    """The prompt for the choices: written by the Studio's brain (`brain: true`), else the layout from the choices."""

    people = chosen(body)
    started = time.time()
    error, by = "", "template"
    if body.get("brain"):
        import agents
        import slate

        agents.local(request, True)  # the brain runs with the user's own sign-ins: as /api/slate/brain
        try:
            prompt = slate.ask(slate.AUTO, SYSTEM, brief(body, people), "top").strip()
            prompt = re.sub(r"^```[a-z]*\n|\n```\s*$", "", prompt).strip()
            by = "brain"
        except subprocess.TimeoutExpired:
            error, prompt = f"the brain did not answer in {slate.TIMEOUT // 60} minutes", ""
        except Exception as failure:  # the reason goes to the page as it is
            error, prompt = str(failure)[:600], ""
        if not prompt:
            prompt, by = template(body, people), "template"
    else:
        prompt = template(body, people)
    return {"prompt": prompt, "by": by, "error": error, "found": script_lines.extract(prompt), "fields": fields(prompt),
            "elapsedMs": int(1000 * (time.time() - started))}


@router.post("/api/builder/fields")
def read_fields(body: dict) -> dict:
    """What a prompt edited by hand on the page gives the Create wizard: its lines, sound and length, and its boxes."""

    prompt = str(body.get("prompt") or "")[:script_lines.LIMIT]
    return {"found": script_lines.extract(prompt), "fields": fields(prompt)}
