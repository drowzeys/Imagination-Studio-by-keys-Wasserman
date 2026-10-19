#!/usr/bin/env python3
"""Imagination Studio MCP: drive a running Imagination Studio from an MCP client (stdio, no dependencies).

    STUDIO_URL=http://127.0.0.1:7870 python3 mcp/studio-mcp.py

Newline-delimited JSON-RPC 2.0 on stdin and stdout (MCP protocol 2024-11-05). Every tool is a call to the Studio's
own HTTP API, so the Studio must be running; paths are relative to the Studio's folder, as its Library shows them.
"""
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

URL = os.environ.get("STUDIO_URL", "http://127.0.0.1:7870").rstrip("/")
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
     "inputSchema": text_schema({"prompt": {"type": "string"}, "count": {"type": "integer", "description": "1 to 12, default 4"},
                                 "preset": {"type": "string", "description": "a preset from studio_state; sets the picture size (default 2k: 1024x576)"},
                                 "width": {"type": "integer", "description": "with height: a size of its own, multiples of 16, 256 to 2048"},
                                 "height": {"type": "integer"}, "seed": {"type": "integer"},
                                 "image_model": {"type": "string", "description": "an id from studio_state's image_models"}}, ("prompt",))},
    {"name": "queue_clip", "description": "Queue a video clip with sound. Give first_frame (a Library picture) to start from it, or "
                                          "image_prompt to make the first frame and the clip in one job, or neither for text to video.",
     "inputSchema": text_schema({"prompt": {"type": "string", "description": "what happens in the clip, its camera and sound"},
                                 "first_frame": {"type": "string", "description": "Library path of the opening picture"},
                                 "image_prompt": {"type": "string", "description": "the opening picture's prompt (text to image to video)"},
                                 "preset": {"type": "string", "description": "a preset from studio_state"},
                                 "quality": {"type": "string", "description": "a quality from studio_state"},
                                 "seconds": {"type": "number", "description": "length, brought to the nearest the model takes (up to 15)"},
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
    {"name": "list_jobs", "description": "The Studio's queue and finished jobs, newest first.",
     "inputSchema": text_schema({"limit": {"type": "integer", "description": "at most this many (default 20)"}})},
    {"name": "upload_file", "description": "Copy a file from this machine into the Studio's Library (png, jpg, webp, mp4, mov, wav, mp3...). "
                                           "Returns its Library path.",
     "inputSchema": text_schema({"path": {"type": "string", "description": "absolute path of the file on this machine"}}, ("path",))},
]


class StudioError(Exception):
    pass


def call(method: str, path: str, body=None, raw: bytes | None = None, kind: str = "application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(URL + path, data=data, method=method, headers={"Content-Type": kind} if data is not None else {})
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
    return {k: job.get(k) for k in ("id", "kind", "status", "title", "progress", "outputs", "error", "elapsed", "estimate")}


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
        return job_summary(call("POST", "/api/jobs", {"kind": kind, "title": args.get("title") or "agent: clip", "params": params}))
    if name == "queue_voice":
        body = {k: args[k] for k in ("text", "seed", "title") if args.get(k) not in (None, "")}
        return job_summary(call("POST", "/api/voice/takes", body))
    if name == "queue_song":
        body = {k: args[k] for k in ("caption", "lyrics", "seconds", "seed", "title") if args.get(k) not in (None, "")}
        return job_summary(call("POST", "/api/music/takes", body))
    if name == "list_projects":
        return call("GET", "/api/projects")
    if name == "add_to_timeline":
        project, path = project_of(args.get("project")), str(args["path"])
        try:
            duration = call("GET", "/api/probe?path=" + urllib.request.quote(path, safe="")).get("duration") or 5
        except StudioError:
            duration = 5
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
        return job_summary(call("GET", "/api/jobs/" + urllib.request.quote(str(args["id"]), safe="")))
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
