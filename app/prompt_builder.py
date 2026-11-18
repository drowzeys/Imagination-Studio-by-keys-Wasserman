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

import hashlib
import json
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request as URLRequest, urlopen

from fastapi import APIRouter, Body, HTTPException, Request

import script_lines
import refmod

router = APIRouter()
HOME: Path = Path(".")
CAST_LIMIT = 200  # characters kept in one cast
TEXT_LIMIT = 600  # characters a field of a character keeps
ENSEMBLE_WARN = 4  # MiniMax H3 keeps a few people apart in one shot; more than this blur together

# This is the complete source requested by the user: 503 good + 82 on-the-fence
# entries as of August 2026. We do not include INDEX_BAD.md.
KNOWN_INDEX_URL = (
    "https://huggingface.co/datasets/malcolmrey/various/raw/main/"
    "h3-center/known-characters/INDEX.md"
)
KNOWN_INDEX_PAGE = (
    "https://huggingface.co/datasets/malcolmrey/various/blob/main/"
    "h3-center/known-characters/INDEX.md"
)
KNOWN_CLIP_BASE = (
    "https://huggingface.co/datasets/malcolmrey/various/blob/main/"
    "h3-center/known-characters/"
)
KNOWN_REFRESH_AFTER = 7 * 24 * 60 * 60  # refresh existing cache at most weekly
KNOWN_RETRY_AFTER = 10 * 60  # don't stall the UI on each request when offline
KNOWN_TIMEOUT = 8  # seconds for first-time download
_known_retry_at: dict[str, float] = {}
_known_last_error: dict[str, str] = {}

SCENARIOS = ["A confrontation", "A confession", "A chase", "A reunion", "Planning a heist", "A standoff",
             "An interrogation", "A first meeting", "A farewell", "An argument", "A comic misunderstanding",
             "An ambush", "A celebration", "A rescue", "A bet between rivals", "A team assembles",
             "A double-cross", "A training montage", "A getaway", "A dance-off", "A job interview gone wrong",
             "A blind date", "A courtroom showdown", "A haunted night", "A last stand", "A road trip breakdown",
             "A rooftop duel", "A surprise party", "A breakup", "A negotiation", "A mistaken identity",
             "Hiding from someone", "A wedding toast", "An escape", "Breaking bad news", "A talent show"]
LOCATIONS = ["a stone cave lit by torches", "a rooftop at night above a neon city", "a roadside diner",
             "an empty warehouse", "a desert highway", "a rain-soaked street with neon signs", "a moving train carriage",
             "a cramped police station", "a mountain temple courtyard", "a beach at dawn", "a starship corridor",
             "a sitcom living-room set", "a casino floor", "a forest clearing", "a jungle river bank",
             "a 1980s video arcade", "a smoky jazz bar", "a hotel penthouse", "a high-school gym at a dance",
             "a busy hospital corridor", "a lighthouse in a storm", "a frozen lake", "a desert gas station",
             "a submarine control room", "a crowded night market", "a ballroom with chandeliers", "a scrapyard",
             "a recording studio", "a boxing ring", "a castle great hall", "a laundromat at 3 a.m.",
             "a mall food court", "a ski lodge", "a ferry deck", "a greenhouse", "a cramped office cubicle",
             "a parking garage", "a carnival at night", "a space station window", "a farmhouse kitchen"]
TIMES = ["dawn", "morning", "midday", "golden hour", "dusk", "night", "a thunderstorm", "heavy snow", "thick fog",
         "blue hour", "a heatwave afternoon"]
MOODS = ["tense", "comic", "romantic", "melancholic", "triumphant", "eerie", "over-the-top action", "bittersweet",
         "absurd", "suspenseful", "nostalgic", "cosy", "frantic", "deadpan", "hopeful", "menacing"]
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
    "20s-silent": "a 1920s silent film: black and white, iris shots, flickering exposure, theatrical gestures, intertitle feel",
    "30s-screwball": "a 1930s screwball comedy: crisp black and white, bright studio lighting, fast-talking two-shots",
    "50s-sci-fi": "a 1950s science-fiction B-movie: saturated Technicolor, matte-painted skies, dramatic zooms, theremin mood",
    "50s-western": "a 1950s western: wide desert vistas, dusty warm light, low-angle heroes, long squinting close-ups",
    "60s-spy": "a 1960s spy caper: bold primary sets, sleek gadgets, swinging zooms, split-screen style",
    "70s-disco": "a 1970s disco-era picture: warm amber grade, soft diffusion, mirror-ball sparkle, wide lapels",
    "70s-kung-fu": "a 1970s martial-arts film: snap zooms, punchy sound, saturated reds, courtyard fights",
    "80s-teen": "a 1980s teen comedy: suburban sunlight, pastel colours, montage energy, synth-pop feel",
    "80s-horror": "a 1980s horror film: deep blue moonlight, fog machines, red practical light, slow creeping dollies",
    "80s-sci-fi": "a 1980s science-fiction film: neon haze, rain, chrome and smoke, slow anamorphic pans",
    "90s-grunge": "a 1990s indie film: muted greens and browns, handheld 16mm, available light, awkward silences",
    "90s-music-video": "a 1990s music video: fisheye lens, high-contrast colour, fast cuts, wind machines",
    "90s-disaster": "a 1990s disaster blockbuster: golden sunsets, sweeping helicopter shots, debris and slow motion",
    "2000s-camcorder": "mid-2000s camcorder footage: interlaced softness, auto-exposure pumping, a date stamp feel",
    "2000s-fantasy-epic": "an early-2000s fantasy epic: misty landscapes, warm torchlight, sweeping crane shots, rich costumes",
    "2010s-superhero": "a 2010s action blockbuster: crisp digital image, teal skies, orbiting hero shots, big explosions",
    "2010s-indie-pastel": "a 2010s indie comedy: symmetrical pastel framing, flat whip pans, deadpan centred shots",
    "anime-90s": "a 1990s cel-animated look: hand-painted backgrounds, bold outlines, speed lines, dramatic stills",
    "claymation": "a stop-motion clay look: handmade textures, slightly jittery motion, miniature sets",
    "documentary": "a modern observational documentary: handheld, natural light, interviews framed off-centre",
    "found-footage": "found footage: a single handheld camera, night vision, tilted framing, sudden drops",
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
Do not introduce additional named characters beyond the selected cast; the selected cast may include original, fictional or real people.
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
LOCK = threading.RLock()  # cast.json is read and written by the page and by a reference sheet's thread
SHEET_POLL_S = 5
SHEET_SIZE = 1024  # a turnaround view's side: at 512 a full figure's face is about 20 pixels and comes out soft
PICTURE = re.compile(r"scouts/[A-Za-z0-9_-]{1,40}/[A-Za-z0-9_.-]{1,80}\.(?:png|jpg)")


def attach(home: Path, queue=None, find=None) -> APIRouter:
    global HOME, submit, lookup
    HOME, submit, lookup = home, queue, find
    return router


def cast_file() -> Path:
    return HOME / "builder" / "cast.json"


def known_file() -> Path:
    """The index cache is distinct from cast.json, so user edits remain untouched."""
    return HOME / "builder" / "known_characters.json"


def _stable_known_id(status: str, name: str, actor: str, franchise: str) -> str:
    # A character portrayed by two different actors must have two selectable IDs.
    signature = "\x1f".join((status, name, actor, franchise)).casefold().encode("utf-8")
    return "hf-" + hashlib.sha256(signature).hexdigest()[:20]


def parse_known_index(markdown: str) -> list[dict]:
    """Parse all actual character rows from the source Markdown; never scrape its HTML preview.

    The index has exactly two sections (good, onthefence). Each row has the
    character's name, actor, franchise, clip links and date. Preserve multiple
    actors for the same role and multiple test clips for the same character.
    """
    characters: list[dict] = []
    section = ""
    expected: dict[str, int] = {}
    seen: set[str] = set()
    for line in markdown.splitlines():
        heading = re.match(
            r"^##\s+Folder:\s*`?(good|onthefence)`?.*?[—-]\s*(\d+)\s+characters",
            line.strip(), re.I,
        )
        if heading:
            section = heading.group(1).lower()
            expected[section] = int(heading.group(2))
            continue
        if not section or not line.startswith("|"):
            continue
        pieces = [piece.strip() for piece in line.strip().strip("|").split("|")]
        if len(pieces) != 5:
            continue
        name, actor, franchise, clip_cell, date_added = pieces
        name = name.strip("* ")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_added):
            continue  # skip Markdown table headers and alignment separators
        if not name or not actor or not franchise:
            continue
        clip_paths = re.findall(r"\]\(((?:good|onthefence)/[^)]+\.mp4)\)", clip_cell)
        character_id = _stable_known_id(section, name, actor, franchise)
        if character_id in seen:
            continue
        seen.add(character_id)
        # The index does NOT describe appearance, clothing or voice. Keep these
        # editable fields empty rather than hallucinating details.
        characters.append({
            "id": character_id, "name": name, "look": "", "wardrobe": "", "voice": "", "era": "",
            "actor": actor, "franchise": franchise, "status": section,
            "date_added": date_added, "clips": [KNOWN_CLIP_BASE + path for path in clip_paths],
            "catalogue": True,
        })

    # Reject partial or malformed downloads; keep the previous complete cache.
    counts = {status: sum(c["status"] == status for c in characters) for status in expected}
    if set(expected) != {"good", "onthefence"} or counts != expected:
        raise ValueError(f"incomplete index: got {counts}, source expects {expected}")
    return characters


def _read_known_cache(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        if not all(isinstance(one, dict) and str(one.get("id", "")).startswith("hf-")
                   and one.get("status") in ("good", "onthefence") for one in data):
            return []
        return data
    except (OSError, ValueError, TypeError):
        return []


def known_characters(force: bool = False) -> list[dict]:
    """Get the full catalogue, using a persistent offline cache whenever possible.

    On first launch, fetch the current INDEX.md over HTTPS. Network failures
    never erase a successful cache or break the custom cast. The retry cooldown
    also prevents a slow HTTP timeout on each cast request while offline.
    """
    path = known_file()
    cached = _read_known_cache(path)
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        age = float("inf")
    if cached and not force and age < KNOWN_REFRESH_AFTER:
        return cached
    key = str(path.resolve())
    if not force and time.monotonic() < _known_retry_at.get(key, 0):
        return cached
    try:
        request = URLRequest(KNOWN_INDEX_URL, headers={
            "User-Agent": "MiniMaxH3-PromptBuilder/1.0", "Accept": "text/plain",
        })
        with urlopen(request, timeout=KNOWN_TIMEOUT) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("index larger than 1 MB")
        updated = parse_known_index(raw.decode("utf-8-sig"))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        _known_last_error.pop(key, None)
        _known_retry_at.pop(key, None)
        return updated
    except (OSError, URLError, HTTPError, UnicodeError, ValueError) as exc:
        _known_retry_at[key] = time.monotonic() + KNOWN_RETRY_AFTER
        _known_last_error[key] = str(exc)[:300]
        return cached




def details_file() -> Path:
    """Editable catalogue details are separate from both cast.json and the downloaded index."""
    return HOME / "builder" / "known_character_details.json"


def _read_details() -> dict:
    try:
        data = json.loads(details_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_details(data: dict) -> None:
    path = details_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def _sanitize_catalogue_detail(source: dict) -> dict:
    out = {}
    for key in ('look', 'wardrobe', 'voice'):
        if isinstance(source.get(key), str):
            out[key] = source[key].strip()[:TEXT_LIMIT]
    if isinstance(source.get('refmod'), str):
        out['refmod'] = source['refmod'].strip()[:240]
    try:
        strength = float(source.get('refmod_strength', 1))
        if 0.01 <= strength <= 1:
            out['refmod_strength'] = strength
    except (TypeError, ValueError):
        pass
    if isinstance(source.get('picture'), str) and PICTURE.fullmatch(source['picture']):
        out['picture'] = source['picture']
    sheet = source.get('sheet') if isinstance(source.get('sheet'), dict) else {}
    saved = {k: sheet[k] for k in ('still', 'sheet')
             if isinstance(sheet.get(k), str) and PICTURE.fullmatch(sheet[k])}
    views = [v for v in (sheet.get('views') or [])
             if isinstance(v, str) and PICTURE.fullmatch(v)][:8]
    state = sheet.get('state') if sheet.get('state') in ('still', 'turn', 'done', 'failed') else ''
    if saved or views or state:
        out['sheet'] = {**saved, 'views': views, 'state': state, 'error': str(sheet.get('error') or '')[:300],
                        'job': str(sheet.get('job') or '')[:40]}
    return out


def catalogue_with_details(entries: list[dict]) -> list[dict]:
    details = _read_details()
    return [{**one, **_sanitize_catalogue_detail(details.get(one['id']) or {})} for one in entries]


def update_catalogue_detail(cid: str, **changes) -> dict:
    if not cid.startswith('hf-') or not any(one['id'] == cid for one in known_characters()):
        raise HTTPException(404, 'catalogue character not found')
    with LOCK:
        data = _read_details()
        existing = data.get(cid) if isinstance(data.get(cid), dict) else {}
        clean = _sanitize_catalogue_detail({**existing, **changes})
        data[cid] = clean
        _write_details(data)
    return clean


@router.put('/api/builder/character-details')
def put_character_details(body: dict = Body(...)) -> dict:
    cid = str(body.get('id') or '')
    base = next((c for c in known_characters() if c['id'] == cid), None)
    if base is None:
        raise HTTPException(404, 'catalogue character not found')
    edits = {}
    for key in ('look', 'wardrobe', 'voice'):
        if key in body:
            if not isinstance(body[key], str):
                raise HTTPException(400, key + ' must be text')
            edits[key] = body[key]
    if 'refmod' in body:
        if not isinstance(body['refmod'], str):
            raise HTTPException(400, 'refmod must be text')
        edits['refmod'] = body['refmod'].strip()[:240]
    if 'refmod_strength' in body:
        try:
            strength = float(body['refmod_strength'])
        except (TypeError, ValueError):
            raise HTTPException(400, 'refmod_strength must be a number')
        if not 0.01 <= strength <= 1:
            raise HTTPException(400, 'refmod_strength must be between 0.01 and 1')
        edits['refmod_strength'] = strength
    if 'description' in body and 'look' not in edits:
        if not isinstance(body['description'], str):
            raise HTTPException(400, 'description must be text')
        edits['look'] = body['description']
    if 'picture' in body:
        # Only deletion is allowed here; imports must go through /frame, which validates the file.
        if body['picture'] != '':
            raise HTTPException(400, 'picture can only be cleared using this endpoint')
        edits['picture'] = ''
    if edits:
        update_catalogue_detail(cid, **edits)
    return {**base, **_sanitize_catalogue_detail(_read_details().get(cid) or {})}


@router.get('/api/builder/character-details/{cid}')
def get_character_details(cid: str) -> dict:
    base = next((c for c in known_characters() if c['id'] == cid), None)
    if base is None:
        raise HTTPException(404, 'catalogue character not found')
    return {**base, **_sanitize_catalogue_detail(_read_details().get(cid) or {})}


def clean_character(one: dict) -> dict:
    if not isinstance(one, dict):
        raise HTTPException(400, "a character is an object")
    name = str(one.get("name") or "").strip()[:80]
    if not name:
        raise HTTPException(400, "a character needs a name")
    out = {"id": str(one.get("id") or "") or uuid.uuid4().hex[:10], "name": name}
    for key in ("look", "wardrobe", "voice"):
        out[key] = str(one.get(key) or "").strip()[:TEXT_LIMIT]
    out['refmod'] = str(one.get('refmod') or '').strip()[:240]
    try:
        strength = float(one.get('refmod_strength', 1))
        out['refmod_strength'] = strength if 0.01 <= strength <= 1 else 1
    except (TypeError, ValueError):
        out['refmod_strength'] = 1
    out["era"] = str(one.get("era") or "") if str(one.get("era") or "") in LOOKS else ""
    # a reference sheet made for the character (make_sheet): the still, the turnaround's sheet and views, and where the
    # making of it stands. Only pictures the Studio made (scouts/<job>/...) are kept
    if isinstance(one.get("picture"), str) and PICTURE.fullmatch(one["picture"]):
        out["picture"] = one["picture"]
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


def _saved_custom_cast() -> list[dict]:
    try:
        saved = json.loads(cast_file().read_text(encoding='utf-8'))
        if not isinstance(saved, list):
            raise ValueError('cast is not a list')
        return [clean_character(x) for x in saved if isinstance(x, dict) and x.get('name')
                and not str(x.get('id') or '').startswith('hf-')][:CAST_LIMIT]
    except (OSError, ValueError, HTTPException, TypeError):
        return [clean_character({**x, 'id': f'starter-{k}'}) for k, x in enumerate(STARTER_CAST)]


def read_cast() -> list[dict]:
    """User-made cast plus the entire indexed catalogue, with saved sheets and descriptions."""
    custom = _saved_custom_cast()
    known_ids = {one['id'] for one in custom}
    return custom + [one for one in catalogue_with_details(known_characters()) if one['id'] not in known_ids]


def write_cast(cast: list[dict]) -> list[dict]:
    """Persist only user cast; catalogue and its generated media remain separately durable."""
    cleaned, seen = [], set()
    for one in cast:
        if not isinstance(one, dict) or str(one.get('id') or '').startswith('hf-'):
            continue
        if len(cleaned) >= CAST_LIMIT:
            break
        character = clean_character(one)
        if character['id'] in seen:
            character['id'] = uuid.uuid4().hex[:10]
        seen.add(character['id'])
        cleaned.append(character)
    path = cast_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)
    return read_cast()


# ---------- a character from a clip made here: who speaks in it, how they sound, and a frame as their picture ----------

FRAME_TIMEOUT = 60  # seconds ffmpeg may take for one frame (a request handler never runs a tool without a limit)
PARTS = {"full": None, "left": "iw/3:ih:0:0", "middle": "iw/3:ih:iw/3:0", "right": "iw/3:ih:2*iw/3:0",
         "left-half": "iw/2:ih:0:0", "right-half": "iw/2:ih:iw/2:0"}


def clip_job(job_id: str) -> dict:
    job = lookup(job_id) if lookup else None
    if not job or job.get("kind") not in ("video", "long") or job.get("status") != "done" or not job.get("outputs"):
        raise HTTPException(404, "pick a finished clip from the Library")
    return job


def from_prompt(prompt: str) -> list[dict]:
    """The speakers of a clip's prompt as characters: their name, their voice, and their look where the prompt
    gives one ("Name: a tall woman ...", the Prompt Builder's scene does)."""

    found = script_lines.extract(prompt)
    people: dict[str, dict] = {}
    for line in found["dialogue"]:
        name = re.sub(r"<Subject \d+>", "", line.get("speaker") or "").strip(" ,")
        if not name or name.lower() in ("the speaker", "an off-screen voice"):
            continue
        one = people.setdefault(name.lower(), {"name": name, "look": "", "wardrobe": "", "voice": ""})
        voice = re.sub(r"^a voice speaking\s+", "", line.get("voice") or "", flags=re.I).strip()
        if voice and not one["voice"]:
            one["voice"] = voice
    for key, one in people.items():
        look = re.search(re.escape(one["name"]) + r"\s*:\s*([^\n]+?)(?:\.\s|\.$|\n|$)", prompt)
        if look:
            text = look.group(1).strip()
            wearing = re.split(r";\s*|,\s*wearing\s+", text, maxsplit=1)
            one["look"], one["wardrobe"] = wearing[0].strip(), (wearing[1].strip() if len(wearing) > 1 else "")
    return list(people.values())


@router.get("/api/builder/from-clip/{job_id}")
def characters_of_clip(job_id: str) -> dict:
    """The people who speak in a clip made here, as characters to add to the cast, and the clip itself (for a frame)."""

    job = clip_job(job_id)
    params = job.get("params") or {}
    prompt = "\n".join(params.get("prompts") or [params.get("prompt") or ""])
    clip = next((o for o in job["outputs"] if o.endswith(".mp4")), job["outputs"][0])
    return {"characters": from_prompt(prompt), "clip": clip, "title": job.get("title") or ""}


@router.post("/api/builder/cast/{cid}/frame")
def picture_from_clip(cid: str, body: dict) -> dict:
    """A frame of a clip made here (`job`, `at` seconds, `part` of the width) as a character's own picture."""

    import server

    job = clip_job(str(body.get("job") or ""))
    clip = server.inside(next((o for o in job["outputs"] if o.endswith(".mp4")), job["outputs"][0]))
    try:
        at = max(0.0, float(body.get("at") or 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "at is a number of seconds")
    part = str(body.get("part") or "full")
    if part not in PARTS:
        raise HTTPException(400, f"part is one of {', '.join(PARTS)}")
    with LOCK:
        cast = read_cast()
        one = next((c for c in cast if c["id"] == cid), None)
    if one is None:
        raise HTTPException(404, "no such character")
    folder = HOME / "scouts" / f"cast-{uuid.uuid4().hex[:10]}"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "frame.png"
    filters = [f for f in (f"crop={PARTS[part]}" if PARTS[part] else "",) if f]
    try:
        done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.3f}", "-i", str(clip), "-frames:v", "1",
                               *(["-vf", ",".join(filters)] if filters else []), str(target)],
                              capture_output=True, text=True, timeout=FRAME_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as failure:
        raise HTTPException(500, f"ffmpeg did not take the frame: {failure}")
    if done.returncode or not target.is_file():
        raise HTTPException(400, "no frame there: " + (done.stderr.strip()[-200:] or "is the time inside the clip?"))
    picture = f"scouts/{folder.name}/frame.png"
    if cid.startswith('hf-'):
        update_catalogue_detail(cid, picture=picture)
    else:
        with LOCK:
            cast = _saved_custom_cast()
            for c in cast:
                if c["id"] == cid:
                    c["picture"] = picture
            write_cast(cast)
    return {"picture": picture}


def plain_prompt(one: dict) -> str:
    """The edit that turns a character's own picture into a turnaround's input: the person alone, full body, on grey
    (measured: a frame from a busy scene came back as five front views; the same person on grey turned to profile)."""

    return ("The same person, standing straight and facing the camera, full body from head to feet, arms relaxed at "
            "the sides, alone on a plain light grey studio background with soft even lighting. Keep their face, hair, "
            "clothes and colours exactly as they are. Photorealistic photograph.")


def sheet_prompt(one: dict) -> str:
    """The reference still: the whole figure, plain background, even light: what the Turnaround LoRA turns well
    (measured: a crop from a busy scene came back as five front views; a full figure on grey turned to profile)."""

    who = one["look"] or (f"{one['name']}, the character portrayed by {one['actor']} in {one['franchise']}"
                           if one.get('actor') else "a person")
    wearing = f", wearing {one['wardrobe']}" if one["wardrobe"] else ""
    return (f"Full-body photograph of {who}{wearing}, standing straight and facing the camera, arms relaxed at the "
            "sides. Plain light grey studio background, soft even lighting, the whole figure in frame from head to "
            "feet, sharp focus.")


def update_sheet(cid: str, **change) -> None:
    if cid.startswith('hf-'):
        with LOCK:
            existing = _read_details().get(cid) or {}
            sheet = {**(existing.get('sheet') or {}), **change}
            update_catalogue_detail(cid, sheet=sheet)
    else:
        with LOCK:
            cast = _saved_custom_cast()
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
def make_sheet(cid: str, body: dict = Body(default={})) -> dict:
    """Make a full-body still with Studio's still generator or requested supported image model,
    then turn it with MiniMax H3 Ref2VA's existing turnaround pipeline."""

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
    # 'studio' keeps the original job parameters unchanged. The Studio checks
    # whether a selected model is installed when the job is queued.
    image_model = str(body.get('image_model') or 'studio').strip().lower()
    # Backward compatibility with the earlier Prompt Builder picker; server.py
    # registers MiniMax H3-Image under the internal ID 'h3-image'.
    if image_model == 'minimax-h3-image':
        image_model = 'h3-image'
    if image_model not in ('studio', 'qwen', 'h3-image'):
        raise HTTPException(400, 'image_model must be studio, qwen or h3-image')
    if one.get("picture"):
        params = {"image": one["picture"], "prompt": plain_prompt(one),
                  "image_model": image_model if image_model != 'studio' else 'qwen', "seed": 11}
        job = submit({"kind": "edit", "title": "Reference still: " + one["name"], "params": params})
    else:
        params = {"prompt": sheet_prompt(one), "count": 1, "seed": 7, "width": 768, "height": 1344}
        if image_model != 'studio':
            params['image_model'] = image_model
        job = submit({"kind": "scout", "title": "Reference still: " + one["name"], "params": params})
    update_sheet(cid, state="still", job=job["id"], error="", still="", sheet="", views=[])
    threading.Thread(target=sheet_run, args=(cid, job["id"]), daemon=True, name="builder-sheet").start()
    return {"job": job["id"]}


@router.get("/api/builder/options")
def options() -> dict:
    catalog = known_characters()
    return {"scenarios": SCENARIOS, "locations": LOCATIONS, "times": TIMES, "moods": MOODS, "shots": SHOT_PLANS,
            "looks": {key: value for key, value in LOOKS.items() if key}, "starter": STARTER_CAST,
            "ensemble_warn": ENSEMBLE_WARN, "known_characters": {"count": len(catalog),
            "source": KNOWN_INDEX_PAGE}}


@router.get("/api/builder/refmods")
def get_refmods() -> dict:
    """List readable RefMods in the Studio library for Prompt Builder's Ref2VA picker."""

    folder = HOME / "refmods"
    entries = []
    for path in folder.rglob("*.safetensors") if folder.is_dir() else ():
        try:
            stat = path.stat()
        except OSError:
            continue
        meta = refmod.header_of(path)
        if meta.get("kind") not in ("image", "video", "audio"):
            continue  # Git LFS pointers, downloaded HTML pages and other files are not RefMods
        entries.append({"name": path.relative_to(folder).with_suffix("").as_posix(),
                        "kind": meta["kind"], "bytes": stat.st_size, "made": stat.st_mtime,
                        "description": meta.get("description", ""), "concept": meta.get("concept_type", "")})
    entries.sort(key=lambda item: (-item["made"], item["name"].casefold()))
    return {"items": entries, "limit": refmod.MOST_IN_CLIP, "folder": str(folder),
            "folder_entries": len(entries)}




@router.get('/api/builder/known-characters')
def get_known_characters(q: str = '', status: str = 'all', offset: int = 0, limit: int = 1000) -> dict:
    if status not in ('all', 'good', 'onthefence'):
        raise HTTPException(400, 'status must be all, good or onthefence')
    if offset < 0 or limit < 1 or limit > 1000:
        raise HTTPException(400, 'offset must be >= 0, limit must be 1..1000')
    entries = catalogue_with_details(known_characters())
    query = q.strip().casefold()[:100]
    filtered = [c for c in entries if (status == 'all' or c['status'] == status)
                and (not query or any(query in str(c.get(key) or '').casefold()
                                      for key in ('name', 'actor', 'franchise')))]
    return {'items': filtered[offset:offset+limit], 'total': len(filtered), 'offset': offset,
            'limit': limit, 'source': KNOWN_INDEX_PAGE,
            'error': _known_last_error.get(str(known_file().resolve()), '') if not entries else ''}


@router.post('/api/builder/known-characters/refresh')
def refresh_known_characters() -> dict:
    characters = known_characters(force=True)
    error = _known_last_error.get(str(known_file().resolve()), '')
    return {'count': len(characters), 'error': error, 'cached': bool(error), 'source': KNOWN_INDEX_PAGE}


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
            casting = (f"the character played by {one['actor']} in {one['franchise']}" if one.get('actor') else '')
            face = one['look'] or casting or 'appearance unspecified'
            lines.append(f"- {one['name']}: {face}; wears {one['wardrobe'] or 'wardrobe unspecified'}; "
                         f"voice: {one['voice'] or 'automatic natural MiniMax H3 voice'}"
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
        out += [f"{one['name']}: {one['look'] or (('portrayed by ' + one['actor'] + ' in ' + one['franchise']) if one.get('actor') else 'appearance unspecified')}, wearing {one['wardrobe'] or 'costume appropriate to the scene'}." for one in people]
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
