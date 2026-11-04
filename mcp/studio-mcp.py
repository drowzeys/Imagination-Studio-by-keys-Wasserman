#!/usr/bin/env python3
"""Imagination Studio MCP: drive a running Imagination Studio from an MCP client (stdio, no dependencies).

    STUDIO_URL=http://127.0.0.1:7870 python3 mcp/studio-mcp.py

Newline-delimited JSON-RPC 2.0 on stdin and stdout (MCP protocol 2024-11-05). Every tool is a call to the Studio's
own HTTP API, so the Studio must be running; paths are relative to the Studio's folder, as its Library shows them.
"""
import json
import copy
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
    {"name": "search_library", "description": "Search the Library by what is in each clip and picture (the Studio describes them): "
                                              "'a dog chasing a kite'. Returns the best matches with a caption and a path to use in other tools.",
     "inputSchema": text_schema({"query": {"type": "string", "description": "what to look for, in plain words"},
                                 "limit": {"type": "integer", "description": "at most this many (default 10)"}}, ("query",))},
    {"name": "queue_still", "description": "Queue still pictures from a prompt (a scout job: several candidates and a contact sheet). Honor studio_state.windows_stills limits on Windows: SD-Turbo, 512x512, at most six sequential drafts.",
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
    {"name": "queue_song", "description": "Queue a song: a style caption and lyrics with section tags ([Verse], [Chorus]); "
                                          "no lyrics makes an instrumental. engine yue2 (YuE2, where it is installed) or music3 "
                                          "(MiniMax-Music3); left out, the studio's default (studio_state's music.engine).",
     "inputSchema": text_schema({"caption": {"type": "string", "description": "genre, tempo, mood, vocals, instruments"},
                                 "lyrics": {"type": "string", "description": "lyrics with [Verse] / [Chorus] tags, or empty for an instrumental"},
                                 "seconds": {"type": "number", "description": "length in seconds (default 60)"},
                                 "engine": {"type": "string", "enum": ["yue2", "music3"], "description": "the song engine (default: the studio's)"},
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
    {"name": "restyle_clip", "description": "Make a Library clip again in a new look, in three steps: its first frame redrawn in the look, "
                                            "its motion read by Motion Previs, then the clip made from its depth layer and the new frame "
                                            "(Ref2VA), so its motion, camera and timing stay. At most 15 s (a longer clip is cut to its "
                                            "first 15). Returns the first job; list_jobs shows the last (its title ends \"3/3: the clip\").",
     "inputSchema": text_schema({"video": {"type": "string", "description": "Library path of the clip"},
                                 "prompt": {"type": "string", "description": "the new look (\"a watercolour painting\", \"16 mm film\")"},
                                 "keep_sound": {"type": "boolean", "description": "keep the clip's own sound (default true)"},
                                 "seconds": {"type": "number", "description": "only the first seconds, 2 to 15 (default the whole clip)"},
                                 "image_model": {"type": "string", "enum": ["qwen", "qwen-full", "flux3k-fast", "flux3k-full"]},
                                 "seed": {"type": "integer"}}, ("video", "prompt"))},
    {"name": "extend_clip", "description": "Carry a Library clip on from its last frame and join the two into one file "
                                           "(renders/<job>-extended.mp4, the job's first output). Over 15 s the new part is a long take.",
     "inputSchema": text_schema({"video": {"type": "string", "description": "Library path of the clip"},
                                 "seconds": {"type": "number", "description": "how much longer, 5 to 30 (default 5)"},
                                 "prompt": {"type": "string", "description": "what happens next (default the clip's own prompt)"},
                                 "join": {"type": "boolean", "description": "join the new part to the clip (default true)"},
                                 "seed": {"type": "integer"}}, ("video",))},
    {"name": "fix_clip", "description": "Change one thing in a Library clip from an instruction, in three steps: its first frame is "
                                        "redrawn by the picture model (the whole frame, no mask), its motion is read by Motion Previs, "
                                        "then the clip is made again from its depth layer and that frame. Returns the first job; the "
                                        "last is queued when the analysis finishes (list_jobs: its title ends \"3/3: the clip\").",
     "inputSchema": text_schema({"video": {"type": "string", "description": "Library path of the clip"},
                                 "instruction": {"type": "string", "description": "the change, e.g. change the sky to a stormy sunset"},
                                 "image_model": {"type": "string", "enum": ["qwen", "qwen-full", "flux3k-fast", "flux3k-full"]},
                                 "keep_sound": {"type": "boolean", "description": "keep the clip's own sound (default true)"},
                                 "seed": {"type": "integer"}}, ("video", "instruction"))},
    {"name": "swap_character", "description": "Character Swap: replace the person in a 5 to 15 s part of a Library clip (one "
                                              "continuous shot, the person in view in its first frame) with the character in a "
                                              "Library picture. The scene, the performance and the clip's own sound stay; with "
                                              "several people the largest is swapped. One job: its first output is the swapped part.",
     "inputSchema": text_schema({"clip": {"type": "string", "description": "Library path of the clip"},
                                 "picture": {"type": "string", "description": "Library path of the character's picture, outfit included"},
                                 "start": {"type": "number", "description": "where the part starts, in seconds (default 0)"},
                                 "seconds": {"type": "number", "description": "how long the part is, 5 to 15 (default 5)"},
                                 "quality": {"type": "integer", "enum": [480, 768], "description": "the short side, 480 (default, Fast) or 768 (High, a little over twice as long)"},
                                 "direction": {"type": "string", "description": "optional words for the model, e.g. she keeps her hands in her pockets"},
                                 "anchor": {"type": "boolean", "description": "put the character in the first frame first (default true)"},
                                 "seed": {"type": "integer"}, "title": {"type": "string"}}, ("clip", "picture"))},
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
    {"name": "music_beats", "description": "A song's tempo and beats, measured on this machine: bpm, beats and downbeats (seconds), "
                                           "bars, duration and confidence (0 to 1). The song is a Library path or a song's or voice line's id.",
     "inputSchema": text_schema({"path": {"type": "string", "description": "Library path of the song, or its take id"}}, ("path",))},
    {"name": "rough_cut", "description": "Cut a Slate film's best takes (or Library clips, for a trailer) to a song so every cut lands "
                                         "on the beat; the export is queued with the song under it, and FCPXML, EDL and a zip for "
                                         "DaVinci Resolve are written when it is done. Returns the project, the export job (or the "
                                         "foley jobs first) and the tempo. Follow it with rough_cut_status.",
     "inputSchema": text_schema({"film": {"type": "string", "description": "a Slate film's id (its best take of each shot, in order)"},
                                 "clips": {"type": "array", "items": {"type": "string"}, "description": "Library paths of clips, instead of a film"},
                                 "song": {"type": "string", "description": "Library path of the song, or a song's or voice line's id"},
                                 "cut": {"type": "string", "enum": ["beat", "bar", "two-bars", "natural"], "description": "how long each shot runs: natural (default: each shot near its own length, cut on a beat), bar, two-bars or beat"},
                                 "order": {"type": "string", "enum": ["given", "shuffle", "energy"], "description": "clip order (default given; energy is calm to busy)"},
                                 "foley": {"type": "boolean", "description": "make each clip's sound for its picture first (slower)"},
                                 "title": {"type": "string"}}, ("song",))},
    {"name": "rough_cut_status", "description": "Where a rough cut is: stage (foley, export, song, files, done, failed), its jobs, and "
                                                "when done its video and files (fcpxml, edl, zip).",
     "inputSchema": text_schema({"project": {"type": "string", "description": "the project id rough_cut returned"}}, ("project",))},
    {"name": "make_graphics", "description": "Queue a motion graphic rendered to .mp4 on this machine (HyperFrames): template title "
                                             "{title, subtitle?, seconds?, video?}, lower-third {video, name, role?, at?, seconds?}, kinetic "
                                             "{text, song?, beats? or bpm?}, captions {video} (its speech is transcribed here), slideshow "
                                             "{pictures[], voice?, song?, seconds_each?}, music-video {clips[], song, beats? or bpm?, every?, "
                                             "transition?}, explainer {sections[{heading, text, picture?}], voice?}. Paths are Library paths; "
                                             "a voice or song may be its take id. size: 1080p (default), 720p, vertical, square, or match (the clip's).",
     "inputSchema": text_schema({"template": {"type": "string", "enum": ["title", "lower-third", "kinetic", "captions", "slideshow", "music-video", "explainer"]},
                                 "data": {"type": "object", "description": "the template's settings, as listed for it (the words on screen go here, title included)"},
                                 "size": {"type": "string", "enum": ["1080p", "720p", "vertical", "square", "match"]},
                                 "title": {"type": "string", "description": "the job's name in the queue"}}, ("template", "data"))},
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
    {"name": "finish_cut", "description": "Finish a timeline (or clips) as the final cut and mix through Tesseract: each clip's own sound "
                                          "kept, the song under it lowered wherever someone speaks, an optional title scene. Makes an .mp4, "
                                          "its editable .tsrct project and FCPXML/EDL for DaVinci Resolve. Follow it with job_status; "
                                          "export_cut makes Premiere or After Effects projects of it.",
     "inputSchema": text_schema({"project": {"type": "string", "description": "a timeline's project id (default: the newest, unless clips are given)"},
                                 "clips": {"type": "array", "items": {"type": "string"}, "description": "Library paths of clips, in order, instead of a timeline"},
                                 "song": {"type": "string", "description": "a queue_song job's id or a Library sound path (default: the timeline's soundtrack); \"none\" for no music"},
                                 "voice": {"type": "string", "description": "Library path of a narration take, laid from the first shot"},
                                 "title": {"type": "string", "description": "a title card before the first shot"},
                                 "subtitle": {"type": "string", "description": "a line under the title"},
                                 "mix": {"type": "string", "enum": ["dialogue", "balanced", "music"], "description": "default dialogue"},
                                 "size": {"type": "string", "enum": ["720p", "1080p", "4k"], "description": "default 1080p"},
                                 "fps": {"type": "integer", "enum": [24, 25, 30, 60], "description": "default 24"},
                                 "captions": {"type": "boolean", "description": "captions of what is heard (needs speech-to-text on the Studio's computer)"}})},
    {"name": "export_cut", "description": "A finished cut (a finish_cut job's id) as a Premiere Pro or After Effects project with its media, "
                                          "zipped. Returns the zip's Library path. DaVinci Resolve needs no export: the finish job's .fcpxml opens there.",
     "inputSchema": text_schema({"id": {"type": "string", "description": "the finish_cut job's id"},
                                 "to": {"type": "string", "enum": ["premiere", "aftereffects"]}}, ("id", "to"))},
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
    {"name": "direct_film", "description": "The Director: from a logline to a checked, cut film. It writes what the film lacks (cast, "
                                          "places, scenes, shots with their lines), makes each character's reference picture and turnaround "
                                          "and each place's plate, renders every shot in order, checks each take with the QA gate and "
                                          "renders a failed one again, and cuts the best takes together. It runs on its own: follow it "
                                          "with director_status. A film that has a run stopped or cut off continues where it was.",
     "inputSchema": text_schema({"film": {"type": "string", "description": "the film's id or name (slate_new_film makes one)"},
                                 "logline": {"type": "string", "description": "the film in one sentence (default the film's own)"},
                                 "scenes": {"type": "integer", "description": "scenes to write when the film has none (default 3)"},
                                 "shotsPerScene": {"type": "integer", "description": "shots in each scene (default 3)"},
                                 "seconds": {"type": "integer", "description": "each shot's length, 1 to 15 (default 8)"},
                                 "cut": {"type": "boolean", "description": "cut the best takes together when done (default true)"}}, ("film",))},
    {"name": "director_status", "description": "Where the Director's run on a film stands: its stage (writing, casting, rendering, "
                                              "checking, retaking, cutting, done, failed, stopped), shots rendered and checked, retakes, "
                                              "the latest lines of its log and, when done, the cut's files.",
     "inputSchema": text_schema({"film": {"type": "string", "description": "the film's id or name"}}, ("film",))},
    {"name": "suite_imports", "description": "What was brought in from the filmmaker suite (Blockout previs, a screenplay, a board, a Motion Previs pack), "
                                             "newest first: id, title, app and shot count. With import, that one's shots with their ids and prompts.",
     "inputSchema": text_schema({"import": {"type": "string", "description": "an import's id or title, to list its shots"}})},
    {"name": "suite_attach", "description": "Send shots of a suite import into a Slate film: the still becomes the shot's first frame, a previs "
                                            "clip its motion reference, and the words fill an empty prompt. mapping is a list of {shot, to}: shot is "
                                            "the import's shot id, to a film shot's id or name, or \"new\" for a new shot (then scene names the scene). "
                                            "Returns what was changed.",
     "inputSchema": text_schema({"import": {"type": "string", "description": "the import's id or title"},
                                 "film": {"type": "string", "description": "the film's id or name"},
                                 "mapping": {"type": "array", "description": "which import shot goes to which film shot",
                                             "items": {"type": "object", "properties": {"shot": {"type": "string"}, "to": {"type": "string"}, "scene": {"type": "string"}}}}},
                                ("import", "film", "mapping"))},
    {"name": "train_lora", "description": "Train a LoRA on a DGX Spark from Library clips or stills (at least 8; each clip 5 s or "
                                          "longer): a look (a style) or a character. It runs for hours (about 110 s a step) and the "
                                          "Spark renders nothing else meanwhile, so do it only when the filmmaker asks. Returns the "
                                          "job; follow it with training_status. The finished LoRA is in Looks under its name.",
     "inputSchema": text_schema({"name": {"type": "string", "description": "the LoRA's name"},
                                 "trigger": {"type": "string", "description": "one word the prompts will use, e.g. wk_noir"},
                                 "kind": {"type": "string", "enum": ["look", "character"]},
                                 "items": {"type": "array", "items": {"type": "string"}, "description": "Library paths of the clips and stills"},
                                 "steps": {"type": "integer", "description": "300 to 3000 (default 1000)"},
                                 "captions": {"type": "string", "enum": ["auto", "prompts", "none"], "description": "default auto"}},
                                ("name", "trigger", "items"))},
    {"name": "training_status", "description": "A LoRA training run, by its job id or the LoRA's id: the job's status and step, the "
                                               "last checkpoint saved, and whether the LoRA is installed.",
     "inputSchema": text_schema({"id": {"type": "string"}}, ("id",))},
    {"name": "night_shift", "description": "Plan a night shift: Slate shots rendered one after another, overnight, then a reel, "
                                          "the dailies package and notes made, and a notification sent to the filmmaker's phone. With film "
                                          "and no shots, every shot of the film with a prompt and no good take; shots names some of them. "
                                          "start is when: idle (when the queue has been idle for 2 minutes, the default), now, or a time "
                                          "like 23:00.",
     "inputSchema": text_schema({"film": {"type": "string", "description": "the film's id or name"},
                                 "shots": {"type": "array", "items": {"type": "string"}, "description": "shot ids or names; all shots without a good take if left out"},
                                 "start": {"type": "string", "description": "idle, now, or HH:MM"},
                                 "name": {"type": "string", "description": "a name for the shift"}}, ("film",))},
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
        return {k: state.get(k) for k in ("presets", "qualities", "lengths", "image_models", "windows_stills", "capabilities", "platform", "platform_label", "turbo",
                                          "engine", "version", "home")}
    if name == "list_library":
        items = [i for i in call("GET", "/api/library") if not args.get("kind") or i.get("kind") == args["kind"]]
        return items[:int(args.get("limit") or 50)]
    if name == "search_library":
        found = call("GET", "/api/library/search?" + urllib.parse.urlencode({"q": args.get("query") or ""}))["items"]
        return [{k: i.get(k) for k in ("path", "kind", "caption", "tags", "score")} for i in found[:int(args.get("limit") or 10)]]
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
        body = {k: args[k] for k in ("caption", "lyrics", "seconds", "seed", "title", "engine") if args.get(k) not in (None, "")}
        return job_summary(call("POST", "/api/music/takes", body))
    if name == "queue_edit":
        params = {k: args[k] for k in ("image", "prompt", "image_model", "seed") if args.get(k) not in (None, "")}
        return job_summary(call("POST", "/api/jobs", {"kind": "edit", "title": args.get("title") or "agent: edit", "params": params}))
    if name == "queue_long":
        params = {k: args[k] for k in ("prompt", "total_seconds", "segment_seconds", "beats", "preset", "quality", "seed") if args.get(k) not in (None, "")}
        if args.get("first_frame"):
            params["image"] = args["first_frame"]
        return job_summary(call("POST", "/api/jobs", {"kind": "long", "title": args.get("title") or "agent: long take", "params": params}))
    if name in ("restyle_clip", "extend_clip", "fix_clip"):  # Restyle, Extend, Fix (app/revise.py): the first job and its chain
        keys = {"restyle_clip": ("video", "prompt", "keep_sound", "seconds", "image_model", "seed"), "extend_clip": ("video", "seconds", "prompt", "join", "seed"),
                "fix_clip": ("video", "instruction", "image_model", "keep_sound", "seed")}[name]
        made = call("POST", "/api/revise", {"kind": name.split("_")[0], "params": {k: args[k] for k in keys if args.get(k) not in (None, "")}})
        return {**job_summary(made), "revise": made.get("revise")}
    if name == "swap_character":  # Character Swap (app/swap.py): one job of kind swap
        params = {"video": args.get("clip"), "image": args.get("picture"),
                  **{k: args[k] for k in ("start", "seconds", "quality", "direction", "anchor", "seed") if args.get(k) not in (None, "")}}
        return job_summary(call("POST", "/api/jobs", {"kind": "swap", **({"title": args["title"]} if args.get("title") else {}), "params": params}))
    if name == "queue_sound":
        kind = args.get("kind")
        if kind not in ("sfx", "foley"):
            raise StudioError("kind is sfx (a sound effect from words) or foley (a clip's sound)")
        keys = ("prompt", "seconds", "seed") if kind == "sfx" else ("prompt", "video", "keep_sound", "seed")
        params = {k: args[k] for k in keys if args.get(k) not in (None, "")}
        return job_summary(call("POST", "/api/jobs", {"kind": kind, **({"title": args["title"]} if args.get("title") else {}), "params": params}))
    if name == "make_graphics":
        if not isinstance(args.get("data"), dict):
            raise StudioError("data is an object of the template's settings, e.g. {\"title\": \"The Long Night\"}")
        params = {**args["data"], "template": args.get("template"), **({"size": args["size"]} if args.get("size") else {})}
        return job_summary(call("POST", "/api/jobs", {"kind": "graphics", **({"title": args["title"]} if args.get("title") else {}), "params": params}))
    if name == "queue_soundtrack":
        body = {"take": str(args["song"]), "video": str(args["video"]), **({"music_db": args["music_db"]} if args.get("music_db") not in (None, "") else {})}
        return job_summary(call("POST", "/api/music/soundtrack", body))
    if name == "music_beats":
        return call("POST", "/api/music/beats", {"path": str(args["path"])})
    if name == "rough_cut":
        if bool(args.get("film")) == bool(args.get("clips")):
            raise StudioError("give film (a Slate film's id) or clips (Library paths), one of the two")
        body = {k: args[k] for k in ("film", "clips", "song", "cut", "order", "foley", "title") if args.get(k) not in (None, "", [])}
        made = call("POST", "/api/roughcut", body)
        return {k: made.get(k) for k in ("project", "job", "foley", "beats", "title", "dropped")} | {"cuts": len(made.get("timeline") or [])}
    if name == "rough_cut_status":
        said = call("GET", "/api/roughcut/" + urllib.request.quote(str(args["project"]), safe=""))
        return {k: said.get(k) for k in ("project", "title", "stage", "error", "job", "foley", "video", "files", "notes")}
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
    if name == "finish_cut":
        params = {k: args[k] for k in ("voice", "mix", "size", "fps", "captions") if args.get(k) not in (None, "")}
        if args.get("clips"):
            params["clips"] = [{"path": str(path)} for path in (args["clips"] if isinstance(args["clips"], list) else [args["clips"]])]
            name_ = f"{len(params['clips'])} clips"
        else:
            project = project_of(args.get("project"))
            if not project.get("timeline"):
                raise StudioError(f"the timeline of {project.get('name') or project['id']} is empty: add clips with add_to_timeline first")
            params["project"], name_ = project["id"], str(project.get("name") or project["id"])
        if args.get("song") not in (None, ""):
            params["song"] = None if str(args["song"]).lower() == "none" else str(args["song"])
        if args.get("title"):
            params["title"] = {"text": str(args["title"]), "subtitle": str(args.get("subtitle") or "")}
        return job_summary(call("POST", "/api/jobs", {"kind": "finish", "title": "Finish: " + name_, "params": params}))
    if name == "export_cut":
        if args.get("to") not in ("premiere", "aftereffects"):
            raise StudioError("to is premiere or aftereffects")
        return call("POST", "/api/finish/" + urllib.request.quote(str(args["id"]), safe="") + "/convert", {"to": args["to"]})
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
    if name == "night_shift":
        film = named(call("GET", "/api/slate/projects"), args["film"], "film")
        body = {"film": film["id"], "start": str(args.get("start") or "idle")}
        if args.get("shots"):
            body["shots"] = [str(one) for one in args["shots"]]
        if args.get("name"):
            body["name"] = str(args["name"])
        plan = call("POST", "/api/nightshift", body)
        return {"shift": plan["id"], "name": plan["name"], "state": plan["state"], "clips": plan["count"], "start": plan["start"],
                "note": "The shift renders on its own; the reel, dailies and notes land in the dailies folder when it is done."}
    if name == "direct_film":
        film = film_of(args["film"])
        body = {"project": film["id"], **{key: args[key] for key in ("logline", "scenes", "shotsPerScene", "seconds", "cut") if args.get(key) not in (None, "")}}
        run = call("POST", "/api/slate/direct", body)
        return {"film": film["id"], "state": run.get("state"), "note": "the Director is at work: follow it with director_status"}
    if name == "director_status":
        film = film_of(args["film"])
        run = call("GET", "/api/slate/direct/" + urllib.request.quote(film["id"], safe=""))
        return {"film": film["id"], "state": run.get("state"), "running": run.get("running"), "counts": run.get("counts"), "error": run.get("error") or None,
                "log": [line.get("text") for line in (run.get("log") or [])[-12:]], "cut": run.get("cut")}
    if name == "list_jobs":
        return [job_summary(job) for job in call("GET", "/api/jobs")[:int(args.get("limit") or 20)]]
    if name == "suite_imports":
        listed = [{**one, "name": one.get("title")} for one in call("GET", "/api/suite/imports")]
        if not args.get("import"):
            return [{key: one.get(key) for key in ("id", "title", "app", "shots")} for one in listed]
        found = call("GET", "/api/suite/imports/" + urllib.request.quote(named(listed, args["import"], "import")["id"], safe=""))
        return {"id": found["id"], "title": found.get("title"), "app": found.get("app"),
                "shots": [{key: one.get(key) for key in ("id", "title", "scene", "action", "camera", "seconds")} | {"still": bool(one.get("image")), "previs_clip": bool(one.get("reference_video"))}
                          for one in found.get("shots") or []]}
    if name == "suite_attach":
        listed = [{**one, "name": one.get("title")} for one in call("GET", "/api/suite/imports")]
        found = named(listed, args["import"], "import")
        film = film_of(args["film"])
        if not isinstance(args.get("mapping"), list) or not args["mapping"]:
            raise StudioError("mapping is a list of {shot, to}")
        flat = [shot for scene in film.get("scenes") or [] for shot in scene.get("shots") or []]
        rows = []
        for row in args["mapping"]:
            to = str(row.get("to") or "")
            if to != "new":
                to = named(flat, to, "shot")["id"]
            scene = named(film.get("scenes") or [], row["scene"], "scene")["id"] if to == "new" and row.get("scene") else None
            rows.append({"shot": str(row.get("shot") or ""), "to": to, **({"scene": scene} if scene else {})})
        made = call("POST", "/api/suite/imports/" + urllib.request.quote(found["id"], safe="") + "/attach", {"film": film["id"], "mapping": rows})
        return {"film": made["id"], "name": made.get("name"), "sent": len(rows),
                "shots": [{"id": s["id"], "name": s.get("name"), "first_frame": (s.get("tf") or {}).get("image"), "previs_clip": (s.get("tf") or {}).get("reference_video")}
                          for scene in made.get("scenes") or [] for s in scene.get("shots") or []]}
    if name == "train_lora":
        items = args["items"] if isinstance(args["items"], list) else [args["items"]]
        made = call("POST", "/api/train/datasets", {"name": str(args["name"]), "trigger": str(args["trigger"]), "kind": str(args.get("kind") or "look"),
                                                    "captions": str(args.get("captions") or "auto"), "items": [str(one) for one in items]})
        job = call("POST", "/api/train/start", {"dataset": made["id"], "name": str(args["name"]), **({"steps": int(args["steps"])} if args.get("steps") else {})})
        return {**job_summary(job), "dataset": made["id"], "lora": (job.get("params") or {}).get("lora"), "items": made.get("items")}
    if name == "training_status":
        return call("GET", "/api/train/status/" + urllib.request.quote(str(args["id"]), safe=""))
    if name == "upload_file":
        source = Path(str(args["path"])).expanduser()
        if not source.is_file():
            raise StudioError(f"no file at {source}")
        # the file is published to the Library, which every device of the studio can list and download: an agent
        # must not be talked into posting the person's private files from here. What may be offered is a file of
        # the studio folder itself (the app exports it to the tool servers it starts), and not a hidden one --
        # a take, a picture a tool made, an upload; not a keyring reached by a path
        home = Path(os.environ.get("STUDIO_HOME") or Path.home() / "ImaginationStudio").resolve()
        try:
            inside = source.resolve().relative_to(home)
        except ValueError:
            raise StudioError(f"{source} is not a file of the studio folder ({home}): make it one first, then upload")
        if any(part.startswith(".") for part in inside.parts):
            raise StudioError(f"{source} is hidden: hidden files of the studio folder are not published to the Library")
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
        try:
            state = call("GET", "/api/state")
        except Exception:  # noqa: BLE001  (the Studio not running yet, or an older one): every tool, as before
            state = {}
        if not isinstance(state, dict) or state.get("platform") != "windows":
            return {"tools": TOOLS}
        allowed = {"studio_state", "list_library", "search_library", "cancel_job", "list_projects", "add_to_timeline",
                   "job_status", "wait_for_jobs", "list_jobs", "slate_films", "slate_film", "slate_new_film",
                   "slate_add_scene", "slate_add_shot", "suite_imports", "suite_attach", "upload_file"}
        for name, kind in {"queue_still": "scout", "queue_sound": "sfx", "make_graphics": "graphics", "export_timeline": "export", "music_beats": "beats"}.items():
            if (state.get("capabilities", {}).get(kind) or {}).get("available"):
                allowed.add(name)
        tools = copy.deepcopy([tool for tool in TOOLS if tool["name"] in allowed])
        for tool in tools:
            props = tool["inputSchema"]["properties"]
            if tool["name"] == "queue_sound":
                tool["description"] = "Queue a five-second Stable Audio Open sound effect on this Windows Studio."
                props["kind"]["enum"] = ["sfx"]
                props["seconds"] = {"type": "number", "const": 5, "description": "Windows: five seconds only (the default)."}
            elif tool["name"] == "make_graphics":
                templates = state["capabilities"]["graphics"]["templates"]
                tool["description"] = "Queue a checked Windows graphic (" + ", ".join(templates) + "); at most 1280x720, 24 fps and ten seconds. No transcription."
                props["template"]["enum"] = templates
                props["size"]["enum"] = ["720p", "match"]
            elif tool["name"] == "music_beats":
                tool["description"] = "Measure tempo, beats and bars of existing local audio with NumPy/FFmpeg; Windows accepts up to ten minutes. No generation model."
            elif tool["name"] == "queue_still":
                props.pop("preset", None)
                props["width"] = {"type": "integer", "const": 512}
                props["height"] = {"type": "integer", "const": 512}
                props["image_model"] = {"type": "string", "const": "sd-turbo"}
                props["count"].update(minimum=1, maximum=6)
        return {"tools": tools}
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
