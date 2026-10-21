#!/usr/bin/env python3
"""Imagination Studio MCP: drive a running Imagination Studio from an MCP client (stdio, no dependencies).

    STUDIO_URL=http://127.0.0.1:7870 python3 mcp/studio-mcp.py

Newline-delimited JSON-RPC 2.0 on stdin and stdout (MCP protocol 2024-11-05). Every tool is a call to the Studio's
own HTTP API, so the Studio must be running; paths are relative to the Studio's folder, as its Library shows them.
"""
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

URL = os.environ.get("STUDIO_URL", "http://127.0.0.1:7870").rstrip("/")


def studio_key() -> str:
    """The app's access key (app/access.py): STUDIO_KEY, else the studio folder's access-key file, read at each call
    (the app may have made it after this server started)."""

    if os.environ.get("STUDIO_KEY"):
        return os.environ["STUDIO_KEY"].strip()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
    try:
        from studio_home import studio_home
        return (studio_home() / "access-key").read_text().strip()
    except (ImportError, OSError):
        return ""
VERSION = "2024-11-05"
TIMEOUT = 60


def text_schema(properties: dict, required: tuple = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required)}


TOOLS = [
    {"name": "studio_state", "description": "Call first. What this Studio offers: presets (sizes), qualities, clip lengths, picture models, platform.",
     "inputSchema": text_schema({})},
    {"name": "list_library", "description": "Clips and stills in the Studio's Library, newest first. Each has a path to use in other tools.",
     "inputSchema": text_schema({"kind": {"type": "string", "enum": ["video", "image"], "description": "only videos or only images"},
                                 "limit": {"type": "integer", "description": "at most this many (default 50)"}})},
    {"name": "queue_still", "description": "Queue still pictures from a prompt (a scout job: several candidates and a contact sheet).",
     "inputSchema": text_schema({"prompt": {"type": "string"}, "count": {"type": "integer", "description": "how many pictures, 1 to 12 (default 1); several only when the filmmaker asks for options or a number"},
                                 "preset": {"type": "string", "description": "a preset from studio_state; sets the picture size (default 2k: 1024x576)"},
                                 "width": {"type": "integer", "description": "with height: a size of its own, multiples of 16, 256 to 2048"},
                                 "height": {"type": "integer"}, "seed": {"type": "integer"},
                                 "image_model": {"type": "string", "description": "an id from studio_state's image_models"}}, ("prompt",))},
    {"name": "queue_clip", "description": "Queue a video clip with sound. Give first_frame (a Library picture) to start from it, or "
                                          "image_prompt to make the first frame and the clip in one job, or neither for text to video.",
     "inputSchema": text_schema({"prompt": {"type": "string", "description": "what happens in the clip, its camera and sound"},
                                 "first_frame": {"type": "string", "description": "Library path of the opening picture: always given when the "
                                                                                 "clip is made from a picture (one made earlier, e.g. "
                                                                                 "scouts/<job>/scout_s1.png, or one the filmmaker names)"},
                                 "image_prompt": {"type": "string", "description": "the opening picture's prompt (text to image to video)"},
                                 "preset": {"type": "string", "description": "a preset from studio_state"},
                                 "quality": {"type": "string", "description": "a quality from studio_state"},
                                 "seconds": {"type": "number", "description": "length, brought to the nearest the model takes (up to 15)"},
                                 "references": {"type": "array", "items": {"type": "string"},
                                                "description": "Ref2VA: up to 9 Library pictures the clip takes its people, things and places "
                                                               "from; the prompt must name them <Picture 1>, <Picture 2>... in this "
                                                               "order (\"<Picture 1> walks beside <Picture 2>\"); no first_frame then"},
                                 "seed": {"type": "integer"}, "title": {"type": "string"}}, ("prompt",))},
    {"name": "queue_voice", "description": "Queue a spoken line with Fish Audio Drama 3 (a voice take). Describe the voice and delivery in "
                                           "brackets before the words, e.g. \"[a tired older man, quiet] We should go.\". No voice cloning here: "
                                           "a take from a reference recording needs the consent box on the Voice tab.",
     "inputSchema": text_schema({"text": {"type": "string", "description": "the line, with any [direction] in brackets"},
                                 "seed": {"type": "integer"}, "title": {"type": "string"}}, ("text",))},
    {"name": "queue_song", "description": "Queue a song with MiniMax-Music3: a style caption and lyrics with section tags ([Verse], [Chorus]); "
                                          "no lyrics makes an instrumental.",
     "inputSchema": text_schema({"caption": {"type": "string", "description": "genre, tempo, mood, vocals, instruments"},
                                 "lyrics": {"type": "string", "description": "lyrics with [Verse] / [Chorus] tags, or empty for an instrumental"},
                                 "seconds": {"type": "number", "description": "length in seconds (default 60)"},
                                 "seed": {"type": "integer"}, "title": {"type": "string"}}, ("caption",))},
    {"name": "queue_edit", "description": "Queue an edit of a Library picture by the image model: the prompt says what the picture "
                                          "should become (\"make it night, rain on the street\"). One edited picture.",
     "inputSchema": text_schema({"image": {"type": "string", "description": "Library path of the picture to edit"},
                                 "prompt": {"type": "string", "description": "the change, described as the result"},
                                 "image_model": {"type": "string", "description": "an id from studio_state's image_models"},
                                 "seed": {"type": "integer"}, "title": {"type": "string"}}, ("image", "prompt"))},
    {"name": "queue_long", "description": "Queue a long take: one video over 15 seconds, made as a chain of clips that each open on "
                                          "the last frame of the one before, then joined. A 'long take' is this one job, not Slate "
                                          "shots or several clips.",
     "inputSchema": text_schema({"prompt": {"type": "string", "description": "what happens, its camera and sound"},
                                 "total_seconds": {"type": "integer", "description": "the whole length, 16 to 1800 seconds"},
                                 "segment_seconds": {"type": "integer", "enum": [5, 8, 10, 15], "description": "each part's length (default 15)"},
                                 "beats": {"type": "array", "items": {"type": "string"}, "description": "what happens in each part, one line "
                                                                                               "per part in order (optional): the beats of one long take go here, in one call"},
                                 "first_frame": {"type": "string", "description": "Library path of the opening picture"},
                                 "preset": {"type": "string", "description": "a preset from studio_state"},
                                 "quality": {"type": "string", "description": "a quality from studio_state"},
                                 "seed": {"type": "integer"}, "title": {"type": "string"}}, ("prompt", "total_seconds"))},
    {"name": "queue_sound", "description": "Queue a sound: kind sfx is a sound effect from words (Stable Audio Open, 1 to 47 s, a .wav); "
                                           "kind foley makes a clip's sound for its picture (MMAudio: a .wav and the clip with it).",
     "inputSchema": text_schema({"kind": {"type": "string", "enum": ["sfx", "foley"]},
                                 "prompt": {"type": "string", "description": "the sound in words (needed for sfx)"},
                                 "seconds": {"type": "number", "description": "sfx only: length, 1 to 47 (default 6)"},
                                 "video": {"type": "string", "description": "foley only: Library path of the clip"},
                                 "keep_sound": {"type": "boolean", "description": "foley only: under the clip's own sound (default true) or in place of it"},
                                 "seed": {"type": "integer"}, "title": {"type": "string"}}, ("kind",))},
    {"name": "queue_soundtrack", "description": "Lay a finished song under a clip, ducked under its dialogue. Makes a new .mp4. The "
                                                "song is a queue_song job's id: one the filmmaker names is found by its title in list_jobs "
                                                "(kind music), not made again.",
     "inputSchema": text_schema({"song": {"type": "string", "description": "the song's job id"},
                                 "video": {"type": "string", "description": "Library path of the clip"},
                                 "music_db": {"type": "number", "description": "the song's level, -30 to 0 dB (default -10)"}}, ("song", "video"))},
    {"name": "check_clip", "description": "Run the QA gate on a finished clip: its sound, the people's identity across the clip and its "
                                          "sharpness. Returns a job; its qa holds the report (passed and each check).",
     "inputSchema": text_schema({"path": {"type": "string", "description": "Library path of the clip"},
                                 "prompt": {"type": "string", "description": "the prompt it was made from (its spoken lines are listened for)"}},
                                ("path",))},
    {"name": "motion_previs", "description": "Motion Previs: analyse a 2 to 15 s range of a clip into a production pack (depth, pose, "
                                             "camera, edges, masks). Returns a job; when done its pack is the pack's id.",
     "inputSchema": text_schema({"clip": {"type": "string", "description": "Library path of the clip"},
                                 "start": {"type": "number", "description": "seconds (default 0)"},
                                 "end": {"type": "number", "description": "seconds (default: the clip's end, at most 15 s after start)"},
                                 "mode": {"type": "string", "enum": ["full-scene", "camera-only", "actor-motion", "object-motion"]},
                                 "title": {"type": "string"}}, ("clip",))},
    {"name": "cancel_job", "description": "Cancel a queued or running job (one queued by mistake).",
     "inputSchema": text_schema({"id": {"type": "string"}}, ("id",))},
    {"name": "list_projects", "description": "The Editor's projects (timelines), newest first.",
     "inputSchema": text_schema({})},
    {"name": "add_to_timeline", "description": "Put a finished clip (a Library path) at the end of an Editor timeline. Without a project, "
                                               "the newest one is used, or a new one named 'Agent timeline' is made.",
     "inputSchema": text_schema({"path": {"type": "string", "description": "Library path of the clip"},
                                 "project": {"type": "string", "description": "a project id from list_projects"},
                                 "in": {"type": "number", "description": "start in the clip, seconds"},
                                 "out": {"type": "number", "description": "end in the clip, seconds (0 plays it to its end)"}}, ("path",))},
    {"name": "export_timeline", "description": "Queue the export of an Editor timeline as one .mp4 (needs ffmpeg). Follow it with job_status.",
     "inputSchema": text_schema({"project": {"type": "string", "description": "a project id (default: the newest)"},
                                 "size": {"type": "string", "enum": ["1920x1080", "2560x1440", "2048x1152", "1344x768"],
                                          "description": "default 1920x1080"}})},
    {"name": "job_status", "description": "One job: status (queued, running, done, failed, cancelled), progress, outputs, error.",
     "inputSchema": text_schema({"id": {"type": "string"}}, ("id",))},
    {"name": "wait_for_jobs", "description": "Wait until these jobs are done, failed or cancelled, for at most `seconds` (default 50, "
                                             "up to 600). Returns each job as job_status does. Use it instead of asking job_status again and again.",
     "inputSchema": text_schema({"ids": {"type": "array", "items": {"type": "string"}, "description": "job ids"},
                                 "seconds": {"type": "number", "description": "the longest to wait (default 50, up to 600)"}}, ("ids",))},
    {"name": "list_jobs", "description": "The Studio's queue and finished jobs, newest first.",
     "inputSchema": text_schema({"limit": {"type": "integer", "description": "at most this many (default 20)"}})},
    {"name": "slate_films", "description": "The films in Slate (the Production tab), newest first: id, name, scenes and shots.",
     "inputSchema": text_schema({})},
    {"name": "slate_film", "description": "One Slate film: its scenes and shots with their ids and prompts, and each shot's takes "
                                          "(clips) with their Library paths and job ids.",
     "inputSchema": text_schema({"film": {"type": "string", "description": "the film's id or name"}}, ("film",))},
    {"name": "slate_new_film", "description": "Make a new Slate film. Returns its id.",
     "inputSchema": text_schema({"name": {"type": "string"}, "logline": {"type": "string", "description": "the film in one sentence"}}, ("name",))},
    {"name": "slate_add_scene", "description": "Add a scene to a Slate film. Returns the scene's id.",
     "inputSchema": text_schema({"film": {"type": "string", "description": "the film's id or name"}, "name": {"type": "string"},
                                 "synopsis": {"type": "string", "description": "what happens in the scene"}}, ("film", "name"))},
    {"name": "slate_add_shot", "description": "Add a shot to a scene of a Slate film. The prompt describes what is seen: the subject and "
                                             "its action, the composition, the lighting, the camera, the style and the mood (it may use "
                                             "'# Subject', '# Composition', '# Lighting', '# Camera', '# Style', '# Mood' headings, each on a line "
                                             "of its own). No "
                                             "spoken words in the prompt. Returns the shot's id.",
     "inputSchema": text_schema({"film": {"type": "string", "description": "the film's id or name"},
                                 "scene": {"type": "string", "description": "the scene's id or name"},
                                 "name": {"type": "string", "description": "the shot's name: the one the filmmaker gives, else e.g. Shot 01"},
                                 "prompt": {"type": "string"},
                                 "intent": {"type": "string", "description": "a note for the crew: what the shot is for (never rendered)"},
                                 "seconds": {"type": "number", "description": "the clip's length, 1 to 15 (default the film's, 8)"},
                                 "size": {"type": "string", "enum": ["EWS", "WS", "MWS", "MS", "MCU", "CU", "ECU", "Insert"]},
                                 "angle": {"type": "string", "enum": ["eye level", "low", "high", "overhead", "dutch", "over-shoulder", "POV"]},
                                 "movement": {"type": "string", "enum": ["locked", "handheld", "push-in", "pull-back", "orbit", "pan", "tilt",
                                                                         "track", "crane", "drone", "steadicam"]},
                                 "soundscape": {"type": "string", "description": "ambience and physical sound"}},
                                ("film", "scene", "name", "prompt"))},
    {"name": "slate_render_shot", "description": "Queue a clip of a Slate shot: the shot is compiled to the video model's prompt and "
                                                "rendered at its own length; the take is logged in the shot. Give first_frame (a finished "
                                                "Library picture, e.g. a still made for this shot) to open the clip on it. Follow it with "
                                                "wait_for_jobs.",
     "inputSchema": text_schema({"film": {"type": "string", "description": "the film's id or name"},
                                 "shot": {"type": "string", "description": "the shot's id or name"},
                                 "first_frame": {"type": "string", "description": "Library path of the opening picture"}}, ("film", "shot"))},
    {"name": "upload_file", "description": "Copy a file from this machine into the Studio's Library (png, jpg, webp, mp4, mov, wav, mp3...). "
                                           "Returns its Library path.",
     "inputSchema": text_schema({"path": {"type": "string", "description": "absolute path of the file on this machine"}}, ("path",))},
]


class StudioError(Exception):
    pass


def call(method: str, path: str, body=None, raw: bytes | None = None, kind: str = "application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    head = {"Content-Type": kind} if data is not None else {}
    if key := studio_key():
        head["X-Studio-Key"] = key
    request = urllib.request.Request(URL + path, data=data, method=method, headers=head)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            return json.loads(answer.read() or b"null")
    except urllib.error.HTTPError as error:
        try:
            said = json.loads(error.read()).get("error")
        except (ValueError, AttributeError):
            said = None
        raise StudioError(f"the Studio said: {said or error.reason} ({error.code})")
    except (urllib.error.URLError, OSError) as error:
        raise StudioError(f"the Studio does not answer at {URL}: start it (bash scripts/app.sh) or set STUDIO_URL ({error})")


def job_summary(job: dict) -> dict:
    out = {k: job.get(k) for k in ("id", "kind", "status", "title", "progress", "outputs", "error", "elapsed", "estimate")}
    out.update({k: job[k] for k in ("qa", "pack") if job.get(k)})
    if "stages" in job and "kind" not in job:  # a Motion Previs analysis
        out["kind"] = "motion"
    return out


def job_of(job_id) -> dict:
    """A job of the render queue, else a Motion Previs analysis."""

    quoted = urllib.request.quote(str(job_id), safe="")
    try:
        return call("GET", "/api/jobs/" + quoted)
    except StudioError as error:
        if "(404)" not in str(error):
            raise
        try:
            return call("GET", "/api/motion/jobs/" + quoted)
        except StudioError:
            raise error from None


FINISHED = ("done", "failed", "cancelled")


def wait_for(ids: list, seconds: float) -> list:
    ends = time.time() + max(0.0, min(float(seconds), 600.0))
    while True:
        now = [job_summary(job_of(i)) for i in ids]
        if all(job.get("status") in FINISHED for job in now) or time.time() >= ends:
            return now
        time.sleep(min(3.0, max(0.1, ends - time.time())))


def slate_id(prefix: str) -> str:
    """An id in the shape the Slate page makes them (slId): prefix, the time in base 36, five random characters."""

    stamp, digits, out = int(time.time() * 1000), "0123456789abcdefghijklmnopqrstuvwxyz", ""
    while stamp:
        stamp, rest = divmod(stamp, 36)
        out = digits[rest] + out
    return f"{prefix}-{out}-{uuid.uuid4().hex[:5]}"


def slate_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def named(items: list, ref, what: str) -> dict:
    """The item whose id is ref, else the one whose name is ref (case aside); one only."""

    ref = str(ref or "").strip()
    hits = [i for i in items if i.get("id") == ref] or [i for i in items if (i.get("name") or "").strip().lower() == ref.lower()]
    if len(hits) != 1:
        raise StudioError(f"no {what} called {ref}" if not hits else f"{len(hits)} {what}s are called {ref}: give its id")
    return hits[0]


def film_of(ref) -> dict:
    meta = named(call("GET", "/api/slate/projects"), ref, "film")
    return call("GET", "/api/slate/projects/" + urllib.request.quote(meta["id"], safe=""))


def save_film(film: dict) -> None:
    call("PUT", "/api/slate/projects/" + urllib.request.quote(film["id"], safe=""), film)


def shot_of(film: dict, ref) -> tuple:
    pairs = [(scene, shot) for scene in film.get("scenes") or [] for shot in scene.get("shots") or []]
    shot = named([shot for _, shot in pairs], ref, "shot")
    return next(scene for scene, one in pairs if one is shot), shot


HEADING = re.compile(r"^[ \t]*(#+)[ \t]*(subject|composition|lighting|camera|style|mood)[ \t]*:[ \t]*(\S.*)$", re.I | re.M)


def sectioned(prompt: str) -> str:
    """A shot's prompt with each section heading on a line of its own, as Slate reads them ("# Subject: a keeper" is
    "# Subject" and "a keeper")."""

    return HEADING.sub(lambda m: f"{m.group(1)} {m.group(2).capitalize()}\n{m.group(3)}", prompt.strip())


def film_summary(film: dict) -> dict:
    return {"id": film["id"], "name": film.get("name"), "logline": film.get("logline"), "scenes": [
        {"id": scene.get("id"), "name": scene.get("name"), "synopsis": scene.get("synopsis"), "shots": [
            {"id": shot.get("id"), "name": shot.get("name"), "seconds": (shot.get("spec") or {}).get("durationSec"),
             "prompt": (shot.get("prompt") or "")[:300], "first_frame": (shot.get("tf") or {}).get("image"),
             "takes": [{"path": take.get("mediaPath"), "job": take.get("jobId"), "rating": take.get("rating")} for take in shot.get("takes") or []]}
            for shot in scene.get("shots") or []]} for scene in film.get("scenes") or []]}


def project_of(given) -> dict:
    """The project asked for, else the newest one; a first one is made when there is none."""

    if given:
        return call("GET", "/api/projects/" + urllib.request.quote(str(given), safe=""))
    listed = call("GET", "/api/projects")
    if listed:
        return call("GET", "/api/projects/" + urllib.request.quote(listed[0]["id"], safe=""))
    return call("POST", "/api/projects", {"name": "Agent timeline"})


def run(name: str, args: dict):
    if name == "studio_state":
        state = call("GET", "/api/state")
        return {k: state.get(k) for k in ("presets", "qualities", "lengths", "image_models", "platform", "platform_label", "turbo",
                                          "engine", "version", "home")}
    if name == "list_library":
        items = [i for i in call("GET", "/api/library") if not args.get("kind") or i.get("kind") == args["kind"]]
        return items[:int(args.get("limit") or 50)]
    if name == "queue_still":
        params = {k: args[k] for k in ("prompt", "count", "preset", "width", "height", "seed", "image_model") if args.get(k) not in (None, "")}
        params.setdefault("count", 1)  # a picture asked of an agent is one picture; the page's scout of four is for browsing
        return job_summary(call("POST", "/api/jobs", {"kind": "scout", "title": "agent: still", "params": params}))
    if name == "queue_clip":
        params = {k: args[k] for k in ("prompt", "preset", "quality", "seconds", "seed") if args.get(k) not in (None, "")}
        if "quality" not in params and not call("GET", "/api/state").get("turbo"):
            params["quality"] = "fh8"  # without MiniMax H3 Turbo the clip goes to FastH3
        if args.get("image_prompt"):
            kind, params["image_prompt"] = "studio", args["image_prompt"]
        else:
            kind = "video"
            if args.get("first_frame"):
                params["image"] = args["first_frame"]
        if args.get("references"):
            references = args["references"] if isinstance(args["references"], list) else [args["references"]]
            if kind != "video" or params.get("image"):
                raise StudioError("references make the clip from pictures: give them without first_frame or image_prompt")
            params["references"] = [str(r) for r in references]
            params.pop("quality", None)
        return job_summary(call("POST", "/api/jobs", {"kind": kind, "title": args.get("title") or "agent: clip", "params": params}))
    if name == "queue_voice":
        body = {k: args[k] for k in ("text", "seed", "title") if args.get(k) not in (None, "")}
        return job_summary(call("POST", "/api/voice/takes", body))
    if name == "queue_song":
        body = {k: args[k] for k in ("caption", "lyrics", "seconds", "seed", "title") if args.get(k) not in (None, "")}
        return job_summary(call("POST", "/api/music/takes", body))
    if name == "queue_edit":
        params = {k: args[k] for k in ("image", "prompt", "image_model", "seed") if args.get(k) not in (None, "")}
        return job_summary(call("POST", "/api/jobs", {"kind": "edit", "title": args.get("title") or "agent: edit", "params": params}))
    if name == "queue_long":
        params = {k: args[k] for k in ("prompt", "total_seconds", "segment_seconds", "beats", "preset", "quality", "seed") if args.get(k) not in (None, "")}
        if args.get("first_frame"):
            params["image"] = args["first_frame"]
        return job_summary(call("POST", "/api/jobs", {"kind": "long", "title": args.get("title") or "agent: long take", "params": params}))
    if name == "queue_sound":
        kind = args.get("kind")
        if kind not in ("sfx", "foley"):
            raise StudioError("kind is sfx (a sound effect from words) or foley (a clip's sound)")
        keys = ("prompt", "seconds", "seed") if kind == "sfx" else ("prompt", "video", "keep_sound", "seed")
        params = {k: args[k] for k in keys if args.get(k) not in (None, "")}
        return job_summary(call("POST", "/api/jobs", {"kind": kind, **({"title": args["title"]} if args.get("title") else {}), "params": params}))
    if name == "queue_soundtrack":
        body = {"take": str(args["song"]), "video": str(args["video"]), **({"music_db": args["music_db"]} if args.get("music_db") not in (None, "") else {})}
        return job_summary(call("POST", "/api/music/soundtrack", body))
    if name == "check_clip":
        body = {k: args[k] for k in ("path", "prompt") if args.get(k) not in (None, "")}
        return job_summary(call("POST", "/api/qa", body))
    if name == "motion_previs":
        clip = str(args["clip"])
        start = float(args.get("start") or 0)
        if args.get("end") not in (None, ""):
            end = float(args["end"])
        else:
            try:
                length = float(call("GET", "/api/probe?path=" + urllib.request.quote(clip, safe="")).get("duration") or 0)
            except StudioError:
                length = 0
            end = min(length, start + 15) if length else start + 5
        body = {"source": clip, "start": start, "end": end, **{k: args[k] for k in ("mode", "title") if args.get(k)}}
        return job_summary(call("POST", "/api/motion/jobs", body))
    if name == "cancel_job":
        quoted = urllib.request.quote(str(args["id"]), safe="")
        try:
            return job_summary(call("POST", f"/api/jobs/{quoted}/cancel"))
        except StudioError as error:
            if "(404)" not in str(error):
                raise
            return job_summary(call("POST", f"/api/motion/jobs/{quoted}/cancel"))
    if name == "list_projects":
        return call("GET", "/api/projects")
    if name == "add_to_timeline":
        project, path = project_of(args.get("project")), str(args["path"])
        try:
            probed = call("GET", "/api/probe?path=" + urllib.request.quote(path, safe=""))
        except StudioError:
            probed = {"duration": 5}  # no ffprobe here: the Editor measures it
        if not probed.get("duration"):
            raise StudioError(f"no finished clip at {path}: give its Library path as the Library or a job's outputs show it (renders/...)")
        duration = probed["duration"]
        project.setdefault("timeline", []).append({"path": path, "in": float(args.get("in") or 0), "out": float(args.get("out") or 0),
                                                   "duration": duration})
        saved = call("PUT", "/api/projects/" + urllib.request.quote(project["id"], safe=""), project)
        return {"project": saved["id"], "name": saved.get("name"), "clips": len(saved.get("timeline") or [])}
    if name == "export_timeline":
        project = project_of(args.get("project"))
        if not project.get("timeline"):
            raise StudioError(f"the timeline of {project.get('name') or project['id']} is empty: add clips with add_to_timeline first")
        width, height = (int(n) for n in str(args.get("size") or "1920x1080").split("x"))
        params = {"clips": project["timeline"], "width": width, "height": height, **({"soundtrack": project["soundtrack"]} if project.get("soundtrack") else {})}
        return job_summary(call("POST", "/api/jobs", {"kind": "export", "title": "Export " + str(project.get("name") or project["id"]), "params": params}))
    if name == "job_status":
        return job_summary(job_of(args["id"]))
    if name == "wait_for_jobs":
        ids = args["ids"] if isinstance(args["ids"], list) else [args["ids"]]
        if not ids or len(ids) > 20:
            raise StudioError("ids is a list of 1 to 20 job ids")
        return wait_for(ids, args.get("seconds") if args.get("seconds") not in (None, "") else 50)
    if name == "slate_films":
        return call("GET", "/api/slate/projects")
    if name == "slate_film":
        return film_summary(film_of(args["film"]))
    if name == "slate_new_film":
        made = call("POST", "/api/slate/projects", {"name": str(args["name"])})
        if args.get("logline"):
            made["logline"] = str(args["logline"])
            save_film(made)
        return {"film": made["id"], "name": made.get("name")}
    if name == "slate_add_scene":
        film = film_of(args["film"])
        scene = {"id": slate_id("scene"), "name": str(args["name"]).strip() or "Scene " + str(len(film["scenes"]) + 1),
                 "synopsis": str(args.get("synopsis") or ""), "shots": []}
        film.setdefault("scenes", []).append(scene)
        save_film(film)
        return {"film": film["id"], "scene": scene["id"], "name": scene["name"]}
    if name == "slate_add_shot":
        film = film_of(args["film"])
        scene, defaults = named(film.get("scenes") or [], args["scene"], "scene"), film.get("defaults") or {}
        seconds = float(args["seconds"]) if args.get("seconds") not in (None, "") else defaults.get("durationSec") or 8
        if not 1 <= seconds <= 15:
            raise StudioError("a shot is 1 to 15 seconds")
        shot = {"id": slate_id("shot"), "name": str(args.get("name") or "").strip() or "Shot " + str(len(scene["shots"]) + 1).zfill(2),
                "intent": str(args.get("intent") or ""),
                "spec": {"durationSec": seconds, "fps": defaults.get("fps") or 24, "aspectRatio": defaults.get("aspectRatio") or "16:9",
                         "lens": args.get("lens") or None, "movement": args.get("movement") or None, "size": args.get("size") or None,
                         "angle": args.get("angle") or None},
                "prompt": sectioned(str(args["prompt"])), "lockedLines": [], "mutedLines": [], "beatSheet": None, "targetModel": "minimax-h3",
                "maxChars": None, "variants": [], "history": [], "takes": [], "createdAt": slate_now(), "updatedAt": slate_now(),
                "tf": {"start": "text", "cast": [], "location": "", "lines": [], "soundscape": str(args.get("soundscape") or ""), "music": "N/A",
                       "cue": "", "seed": 1, "image": None, "h3": "", "h3Auto": True}}
        scene["shots"].append(shot)
        save_film(film)
        return {"film": film["id"], "scene": scene["id"], "shot": shot["id"], "name": shot["name"], "seconds": seconds,
                "rendered": False, "note": "saved in Slate, not rendered: slate_render_shot queues its clip, when a clip is asked for"}
    if name == "slate_render_shot":
        film = film_of(args["film"])
        scene, shot = shot_of(film, args["shot"])
        tf = shot.setdefault("tf", {})
        if args.get("first_frame"):
            tf.update(start="image", image=str(args["first_frame"]))
        save_film(film)  # the compile reads the shot from the film as it is now
        compiled = call("POST", "/api/slate/compile", {"projectId": film["id"], "shotId": shot["id"]})
        if args.get("first_frame") and compiled.get("mode") != "image":
            raise StudioError(f"{args['first_frame']} is not a finished picture in the Library yet: wait for its job, then render the shot")
        output = f"slate/{film['id']}/takes/{shot['id']}-{slate_id('t').split('-')[1]}.mp4"
        prompt = (tf.get("h3") or "").strip() if tf.get("h3Auto") is False and (tf.get("h3") or "").strip() else compiled["prompt"]
        job = call("POST", "/api/jobs", {"kind": "video", "title": f"{scene.get('name')} · {shot.get('name')}", "params": {
            **compiled["plan"], "prompt": prompt, "output": output, "slate": {"project": film["id"], "scene": scene["id"], "shot": shot["id"]}}})
        film = film_of(film["id"])  # logged into the film as it is after the job was queued
        _, shot = shot_of(film, shot["id"])
        shot.setdefault("takes", []).insert(0, {"id": slate_id("take"), "loggedAt": slate_now(), "model": "minimax-h3", "prompt": prompt,
                                               "rating": "good", "notes": "queued by the agent", "mediaPath": output, "jobId": job.get("id"),
                                               "seed": compiled["plan"].get("seed"), "settings": {k: (job.get("params") or compiled["plan"]).get(k)
                                               for k in ("preset", "quality", "frames", "seconds", "width", "height") if (job.get("params") or compiled["plan"]).get(k) is not None}})
        save_film(film)
        return {**job_summary(job), "take": output, "warnings": compiled.get("warnings") or []}
    if name == "list_jobs":
        return [job_summary(job) for job in call("GET", "/api/jobs")[:int(args.get("limit") or 20)]]
    if name == "upload_file":
        source = Path(str(args["path"])).expanduser()
        if not source.is_file():
            raise StudioError(f"no file at {source}")
        boundary = uuid.uuid4().hex
        kind = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{source.name}\"\r\n"
                f"Content-Type: {kind}\r\n\r\n").encode() + source.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        return call("POST", "/api/upload", raw=body, kind=f"multipart/form-data; boundary={boundary}")
    raise StudioError(f"no tool named {name}")


def answer(message: dict):
    method, params = message.get("method"), message.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": VERSION, "capabilities": {"tools": {}}, "serverInfo": {"name": "slate-studio", "version": "1.0.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        try:
            out = run(params.get("name"), params.get("arguments") or {})
            return {"content": [{"type": "text", "text": json.dumps(out, indent=1)}]}
        except (StudioError, KeyError, ValueError, TypeError) as error:
            return {"content": [{"type": "text", "text": str(error) or "missing argument"}], "isError": True}
    raise LookupError(method)


def main():
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict) or "id" not in message:
            continue  # a notification: nothing to answer
        reply = {"jsonrpc": "2.0", "id": message["id"]}
        try:
            reply["result"] = answer(message)
        except LookupError:
            reply["error"] = {"code": -32601, "message": f"method not found: {message.get('method')}"}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
