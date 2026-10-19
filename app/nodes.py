"""Nodes: a small ComfyUI-style canvas over the studio's own jobs (app/static/nodes.js).

A graph is cards joined by typed edges. Running one checks it (no loops, every edge between ports of one type, every
required input joined), puts its cards in order and queues the studio's existing jobs one after another: a picture is
a scout job, an edit an edit job, a clip a video job, a line a Drama 3 take, a song a MiniMax-Music3 take, an export
the Editor's export. Each job goes in through the app's own route for it (POST /api/jobs, /api/voice/takes,
/api/music/takes), so it gets the same checks a job from any other tab gets, the memory guard's among them; what a job
made goes on to the next card as paths inside the studio folder. Nothing new runs on the GPU from here.

    GET    /api/nodes/graphs              saved graphs, the starter templates and the node types
    GET    /api/nodes/graphs/{id}         one saved graph
    POST   /api/nodes/graphs              save {id?, name, nodes, edges, view?}
    DELETE /api/nodes/graphs/{id}         to the trash folder (nodes/.trash), not erased
    POST   /api/nodes/check               {graph} -> {ok, problems: [{node, message}]}
    POST   /api/nodes/run                 {graph} or {id} -> the run (409 while another is starting or running)
    GET    /api/nodes/runs/{id}           the run: its status and each node's status, outputs and error
    POST   /api/nodes/runs/{id}/cancel    stops the job that is rendering and the rest of the run

Graphs are saved as $STUDIO_HOME/nodes/<id>.json. Runs are kept in memory, the last few, like the queue's jobs.
"""

from __future__ import annotations

import copy
import json
import math
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

import lengths  # beside this file: the frame counts the video model takes

ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
MOST_NODES, MOST_EDGES, MOST_TEXT = 64, 256, 8000
NODE_HOURS = 8           # the longest one card may wait for its job: the slowest long take is well inside it
POLL_S = 1.0             # how often a run looks at the job it waits on
KEPT_RUNS = 30
IMAGES = (".png", ".jpg", ".jpeg", ".webp")
VIDEOS = (".mp4", ".mov")
SOUNDS = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")
IMAGE_MODELS = ("qwen", "qwen-full", "flux3k-fast", "flux3k-full", "moss-image")
EDIT_MODELS = ("qwen", "flux3k-fast", "flux3k-full")
PRESETS = ("small", "draft", "p720", "p960", "native", "2k", "qhd", "sharp")
QUALITIES = ("fh8", "fh4", "fh20", "standard", "high", "full", "fast")

# Port types: text, image, images (a scout's set), video, audio, timeline, file. An input takes one edge unless it
# says "many"; "types" lists what it accepts. A Reference card's output is the kind of file it holds.
NODE_TYPES = {
    "text": {"label": "Text", "inputs": {}, "outputs": {"text": "text"}, "params": {"text": ""}},
    "picture": {"label": "Picture", "job": "scout", "inputs": {"prompt": {"types": ["text"], "required": True}},
                "outputs": {"image": "image", "images": "images"},
                "params": {"image_model": "qwen", "width": 1344, "height": 768, "seed": 1, "count": 1}},
    "pick": {"label": "Pick", "inputs": {"images": {"types": ["images"], "required": True}}, "outputs": {"image": "image"},
             "params": {"index": 1}},
    "edit": {"label": "Edit picture", "job": "edit",
             "inputs": {"image": {"types": ["image"], "required": True}, "prompt": {"types": ["text"], "required": True}},
             "outputs": {"image": "image"}, "params": {"image_model": "qwen", "seed": 1}},
    "clip": {"label": "Clip", "job": "video",
             "inputs": {"prompt": {"types": ["text"], "required": True}, "first_frame": {"types": ["image"]},
                        "refs": {"types": ["image", "video", "audio"], "many": True}},
             "outputs": {"video": "video"},
             "params": {"preset": "small", "quality": "fh8", "seconds": 5, "seed": 1, "look": ""}},
    "voice": {"label": "Voice", "job": "voice", "inputs": {"text": {"types": ["text"], "required": True}},
              "outputs": {"audio": "audio"}, "params": {"seed": 1}},
    "song": {"label": "Song", "job": "music", "inputs": {"style": {"types": ["text"], "required": True}, "lyrics": {"types": ["text"]}},
             "outputs": {"audio": "audio"}, "params": {"seconds": 30, "seed": 1}},
    "reference": {"label": "Reference", "inputs": {}, "outputs": {"file": "image"}, "params": {"path": "", "kind": "image"}},
    "timeline": {"label": "Timeline", "inputs": {"clips": {"types": ["video"], "many": True, "required": True}, "song": {"types": ["audio"]}},
                 "outputs": {"timeline": "timeline"}, "params": {"project": ""}},
    "export": {"label": "Export", "job": "export", "inputs": {"source": {"types": ["timeline", "video"], "required": True}},
               "outputs": {"file": "file"}, "params": {"size": "1920x1080", "music_db": -10}},
    "note": {"label": "Note", "inputs": {}, "outputs": {}, "params": {"text": ""}},
}
WORDS = {"image": "a picture", "video": "a video", "audio": "a sound"}
SIZES = ("1920x1080", "1280x720", "3840x2160", "1080x1920")


def _node(nid, kind, x, y, title="", **params):
    return {"id": nid, "type": kind, "x": x, "y": y, "title": title, "params": {**NODE_TYPES[kind]["params"], **params}}


def _edge(source, out, target, port):
    return {"from": source, "out": out, "to": target, "in": port}


TEMPLATES = [
    {"id": "still-clip", "name": "Still → Clip", "line": "A picture from words, then a clip that opens on it.",
     "nodes": [_node("t1", "text", 40, 60, "The picture", text="A lighthouse keeper on a wet stone stair at dusk, lamp in hand, storm clouds behind"),
               _node("p1", "picture", 360, 40),
               _node("t2", "text", 40, 280, "What happens", text="The keeper climbs toward the lamp room as the beam sweeps across the sea. Rain and wind."),
               _node("c1", "clip", 700, 120)],
     "edges": [_edge("t1", "text", "p1", "prompt"), _edge("p1", "image", "c1", "first_frame"), _edge("t2", "text", "c1", "prompt")]},
    {"id": "scout-pick-clip", "name": "Scout → Pick → Clip", "line": "Four candidates, pick one, then the clip.",
     "nodes": [_node("t1", "text", 40, 60, "The picture", text="A night market under paper lanterns, steam rising from a noodle stall"),
               _node("p1", "picture", 360, 40, "Scouts", count=4, width=1280, height=736),
               _node("k1", "pick", 700, 40),
               _node("t2", "text", 700, 360, "What happens", text="The cook tosses noodles through the steam while the camera drifts past the stall."),
               _node("c1", "clip", 1020, 80)],
     "edges": [_edge("t1", "text", "p1", "prompt"), _edge("p1", "images", "k1", "images"), _edge("k1", "image", "c1", "first_frame"),
               _edge("t2", "text", "c1", "prompt")]},
    {"id": "line-voice-clip", "name": "Line → Voice → Clip", "line": "A spoken line in Drama 3, then a clip that speaks it (needs Ref2VA).",
     "nodes": [_node("t1", "text", 40, 40, "The line", text="We leave at first light. Bring only what you can carry."),
               _node("v1", "voice", 360, 40),
               _node("r1", "reference", 360, 270, "The person"),
               _node("t2", "text", 40, 420, "The shot", text="Use <Picture 1> as the person. The person looks into the lens and says the line in the voice of <Audio 1>."),
               _node("c1", "clip", 700, 120, quality="fh8")],
     "edges": [_edge("t1", "text", "v1", "text"), _edge("r1", "file", "c1", "refs"), _edge("v1", "audio", "c1", "refs"),
               _edge("t2", "text", "c1", "prompt")]},
]


def title_of(node: dict) -> str:
    return str(node.get("title") or "").strip()[:60] or NODE_TYPES.get(node.get("type"), {}).get("label", "a card")


def kind_of_path(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    return "image" if suffix in IMAGES else "video" if suffix in VIDEOS else "audio" if suffix in SOUNDS else None


def out_type(node: dict, port: str) -> str | None:
    if node["type"] == "reference":
        return node["params"].get("kind") if port == "file" else None
    return NODE_TYPES[node["type"]]["outputs"].get(port)


def tidy(graph) -> dict:
    """A graph in its saved form, or 400: known node types, ids, positions and settings of the right kinds."""

    if not isinstance(graph, dict):
        raise HTTPException(400, "a graph is an object with nodes and edges")
    nodes, edges = graph.get("nodes") or [], graph.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise HTTPException(400, "a graph's nodes and edges are lists")
    if len(nodes) > MOST_NODES or len(edges) > MOST_EDGES:
        raise HTTPException(400, f"a graph holds at most {MOST_NODES} cards and {MOST_EDGES} links")
    out, seen = [], set()
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") not in NODE_TYPES:
            raise HTTPException(400, "unknown kind of card: " + str(node.get("type") if isinstance(node, dict) else node)[:40])
        nid = str(node.get("id") or "")
        if not ID.fullmatch(nid) or nid in seen:
            raise HTTPException(400, "every card has its own id of letters, digits, - and _")
        seen.add(nid)
        kind = NODE_TYPES[node["type"]]
        given = node.get("params") if isinstance(node.get("params"), dict) else {}
        params = {}
        for key, default in kind["params"].items():
            value = given.get(key, default)
            if isinstance(default, bool) or isinstance(default, str):
                value = "" if value is None else str(value)
                if len(value) > MOST_TEXT:
                    raise HTTPException(400, f"the text of {title_of(node)} is longer than {MOST_TEXT} characters")
            else:
                try:
                    if not math.isfinite(float(value)):
                        raise ValueError
                    value = type(default)(float(value))
                except (TypeError, ValueError, OverflowError):
                    raise HTTPException(400, f"{key} of {title_of(node)} is a number")
            params[key] = value
        try:
            x, y = float(node.get("x") or 0), float(node.get("y") or 0)
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(400, "a card's place is two numbers")
        out.append({"id": nid, "type": node["type"], "x": round(x, 1), "y": round(y, 1), "title": str(node.get("title") or "")[:60], "params": params})
    links = []
    for edge in edges:
        if not isinstance(edge, dict) or not all(isinstance(edge.get(k), str) for k in ("from", "out", "to", "in")):
            raise HTTPException(400, "a link is {from, out, to, in}: two cards and their ports")
        links.append({k: edge[k] for k in ("from", "out", "to", "in")})
    view = graph.get("view") if isinstance(graph.get("view"), dict) else {}
    try:
        view = {"x": float(view.get("x", 0)), "y": float(view.get("y", 0)), "k": min(max(float(view.get("k", 1)), 0.4), 2)}
        if not all(math.isfinite(v) for v in view.values()):
            raise ValueError
    except (TypeError, ValueError):
        view = {"x": 0.0, "y": 0.0, "k": 1.0}
    return {"name": str(graph.get("name") or "Untitled graph").strip()[:80] or "Untitled graph", "nodes": out, "edges": links, "view": view}


def problems(graph: dict, inside=None) -> list[dict]:
    """What stops a tidy graph from running, card by card: [{node, message}] (node None for the whole graph)."""

    found = []
    nodes = {n["id"]: n for n in graph["nodes"]}
    said = lambda node, message: found.append({"node": node, "message": message})
    into: dict[tuple[str, str], list[dict]] = {}
    for edge in graph["edges"]:
        source, target = nodes.get(edge["from"]), nodes.get(edge["to"])
        if source is None or target is None:
            said(None, "a link joins a card that is not in the graph")
            continue
        made = out_type(source, edge["out"])
        if made is None:
            said(source["id"], f"{title_of(source)} has no output called {edge['out']}")
            continue
        port = NODE_TYPES[target["type"]]["inputs"].get(edge["in"])
        if port is None:
            said(target["id"], f"{title_of(target)} has no input called {edge['in']}")
            continue
        if made not in port["types"]:
            said(target["id"], f"{title_of(source)} gives {made}, and the {edge['in'].replace('_', ' ')} of {title_of(target)} takes "
                               + " or ".join(port["types"]))
            continue
        into.setdefault((target["id"], edge["in"]), []).append(edge)
    for (nid, port), edges in into.items():
        if len(edges) > 1 and not NODE_TYPES[nodes[nid]["type"]]["inputs"][port].get("many"):
            said(nid, f"the {port.replace('_', ' ')} of {title_of(nodes[nid])} takes one link, and it has {len(edges)}")
    for node in graph["nodes"]:
        kind, params = NODE_TYPES[node["type"]], node["params"]
        for port, spec in kind["inputs"].items():
            if spec.get("required") and not into.get((node["id"], port)):
                need = " or ".join(spec["types"])
                said(node["id"], f"{title_of(node)} needs its {port.replace('_', ' ')}: join a card that gives {need}")
        if node["type"] == "text" and not params["text"].strip():
            said(node["id"], f"{title_of(node)} is empty: write the words it passes on")
        if node["type"] == "reference":
            if params["kind"] not in ("image", "video", "audio"):
                said(node["id"], f"{title_of(node)} holds a picture, a video or a sound")
            elif not params["path"]:
                said(node["id"], f"{title_of(node)} has no file: pick one from the Library")
            elif kind_of_path(params["path"]) != params["kind"]:
                said(node["id"], f"{title_of(node)} is set to {WORDS[params['kind']]}, and {Path(params['path']).name} is not one")
            elif inside is not None:
                try:
                    if not inside(params["path"]).is_file():
                        said(node["id"], f"{Path(params['path']).name} is not in the studio's folder")
                except HTTPException:
                    said(node["id"], f"{title_of(node)}'s file is outside the studio's folder")
        if node["type"] == "picture":
            if params["image_model"] not in IMAGE_MODELS:
                said(node["id"], f"{title_of(node)}: unknown picture model")
            if params["width"] % 16 or params["height"] % 16 or not (256 <= params["width"] <= 2048 and 256 <= params["height"] <= 2048):
                said(node["id"], f"{title_of(node)}: picture sides are multiples of 16 between 256 and 2048")
            if not 1 <= params["count"] <= 12:
                said(node["id"], f"{title_of(node)} makes 1 to 12 pictures")
        if node["type"] == "edit" and params["image_model"] not in EDIT_MODELS:
            said(node["id"], f"{title_of(node)}: unknown picture model for an edit")
        if node["type"] == "pick" and params["index"] < 1:
            said(node["id"], f"{title_of(node)} picks the 1st, 2nd, ... picture")
        if node["type"] == "clip":
            if params["preset"] not in PRESETS or params["quality"] not in QUALITIES:
                said(node["id"], f"{title_of(node)}: unknown size or quality")
            if not 1 <= params["seconds"] <= 15:
                said(node["id"], f"{title_of(node)} is 1 to 15 seconds: longer videos are the Video engine's long take")
            refs = [nodes[e["from"]] for e in into.get((node["id"], "refs"), [])]
            kinds = [out_type(r, e["out"]) for r, e in zip(refs, into.get((node["id"], "refs"), []))]
            if refs and into.get((node["id"], "first_frame")):
                said(node["id"], f"{title_of(node)}: references do not combine with a first frame (a reference is not a frame of the clip)")
            if "audio" in kinds and not ({"image", "video"} & set(kinds)):
                said(node["id"], f"{title_of(node)}: reference sound needs a reference picture or video beside it")
        if node["type"] == "timeline":
            for edge in into.get((node["id"], "song"), []):
                if nodes[edge["from"]]["type"] != "song":
                    said(node["id"], f"the song under {title_of(node)} comes from a Song card")
            if params["project"] and not ID.fullmatch(params["project"]):
                said(node["id"], f"{title_of(node)}: unknown project")
        if node["type"] == "export":
            if params["size"] not in SIZES:
                said(node["id"], f"{title_of(node)}: size is one of " + ", ".join(SIZES))
            if not -30 <= params["music_db"] <= 0:
                said(node["id"], f"{title_of(node)}: the song's level is between -30 and 0 dB")
    loop = cycle(graph)
    if loop:
        said(loop[0], "the graph loops: " + " → ".join(title_of(nodes[n]) for n in loop + [loop[0]]) + "; a card cannot wait on itself")
    return found


def cycle(graph: dict) -> list[str]:
    """One loop of the graph as card ids, or []."""

    after: dict[str, list[str]] = {n["id"]: [] for n in graph["nodes"]}
    for edge in graph["edges"]:
        if edge["from"] in after and edge["to"] in after:
            after[edge["from"]].append(edge["to"])
    state, path = {}, []

    def visit(nid):
        state[nid] = 1
        path.append(nid)
        for nxt in after[nid]:
            if state.get(nxt) == 1:
                return path[path.index(nxt):]
            if nxt not in state:
                found = visit(nxt)
                if found:
                    return found
        state[nid] = 2
        path.pop()
        return []

    for nid in after:
        if nid not in state:
            found = visit(nid)
            if found:
                return found
    return []


def order(graph: dict) -> list[str]:
    """The cards in the order they run: each after every card it takes from; otherwise left to right, top to bottom."""

    nodes = {n["id"]: n for n in graph["nodes"] if n["type"] != "note"}
    waits = {nid: set() for nid in nodes}
    for edge in graph["edges"]:
        if edge["from"] in nodes and edge["to"] in nodes:
            waits[edge["to"]].add(edge["from"])
    out = []
    place = lambda nid: (nodes[nid]["x"], nodes[nid]["y"], nid)
    ready = sorted((nid for nid, w in waits.items() if not w), key=place)
    while ready:
        nid = ready.pop(0)
        out.append(nid)
        for other, w in waits.items():
            if nid in w:
                w.discard(nid)
                if not w and other not in out and other not in ready:
                    ready.append(other)
                    ready.sort(key=place)
    if len(out) != len(nodes):
        raise HTTPException(400, "the graph loops: a card cannot wait on itself")
    return out


def checked(graph, inside=None) -> dict:
    """A graph that runs, or 400 with the first thing that stops it, said plainly."""

    graph = tidy(graph)
    found = problems(graph, inside)
    if found:
        raise HTTPException(400, found[0]["message"] + (f" (and {len(found) - 1} more)" if len(found) > 1 else ""))
    return graph


def attach(home: Path, enqueue, jobs: dict, lock, inside, relative) -> APIRouter:
    """``enqueue(kind, title, params, estimate)`` is server.enqueue, ``jobs`` the queue's jobs and ``lock`` its lock,
    ``inside`` / ``relative`` turn a studio path into a full one and back. A job goes in through the app's own route
    for its kind (the checks a job from any tab gets, the memory guard's among them); where the app has none the card
    fails rather than queue the job unchecked, so ``enqueue`` is kept for the signature only."""

    router = APIRouter()
    folder = home / "nodes"
    runs: dict[str, dict] = {}
    runs_lock = threading.Lock()
    starting = threading.Lock()  # held while a run is checked and started (start)

    def graph_file(graph_id: str) -> Path:
        if not ID.fullmatch(graph_id or ""):
            raise HTTPException(400, "bad graph id")
        return folder / f"{graph_id}.json"

    def saved() -> list[dict]:
        out = []
        for file in folder.glob("*.json"):
            try:
                data = json.loads(file.read_text())
                out.append({"id": file.stem, "name": data.get("name") or file.stem, "nodes": len(data.get("nodes") or []),
                            "modified": file.stat().st_mtime})
            except (OSError, ValueError, AttributeError):
                continue  # a file cut off while it was written must not take the page down
        return sorted(out, key=lambda g: -g["modified"])

    # ---------- graphs ----------

    @router.get("/api/nodes/graphs")
    def list_graphs() -> dict:
        return {"graphs": saved(), "templates": TEMPLATES, "types": NODE_TYPES}

    @router.get("/api/nodes/graphs/{graph_id}")
    def get_graph(graph_id: str) -> dict:
        file = graph_file(graph_id)
        if not file.is_file():
            raise HTTPException(404, "no such graph")
        return {**json.loads(file.read_text()), "id": graph_id}

    @router.post("/api/nodes/graphs")
    def save_graph(body: dict) -> dict:
        graph_id = str(body.get("id") or "") or time.strftime("g%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        file = graph_file(graph_id)
        graph = {**tidy(body), "id": graph_id, "saved": time.time()}
        folder.mkdir(parents=True, exist_ok=True)
        fresh = file.with_name(f"{file.name}.{uuid.uuid4().hex}.new")  # written beside it and swapped in: a reader never sees half a file
        fresh.write_text(json.dumps(graph, indent=1))
        fresh.replace(file)
        return graph

    @router.delete("/api/nodes/graphs/{graph_id}")
    def delete_graph(graph_id: str) -> dict:
        file = graph_file(graph_id)
        if not file.is_file():
            raise HTTPException(404, "no such graph")
        trash = folder / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        file.replace(trash / f"{graph_id}-{int(time.time())}.json")
        return {"deleted": graph_id}

    @router.post("/api/nodes/check")
    def check(body: dict) -> dict:
        graph = tidy(body.get("graph") if isinstance(body.get("graph"), dict) else body)
        found = problems(graph, inside)
        return {"ok": not found, "problems": found, "order": [] if found else order(graph)}

    # ---------- runs ----------

    def route(app, path: str):
        for one in getattr(app, "routes", []) or []:
            if getattr(one, "path", None) == path and "POST" in (getattr(one, "methods", None) or ()):
                return one.endpoint
        return None

    def public(run: dict) -> dict:
        with runs_lock:
            return copy.deepcopy({k: v for k, v in run.items() if not k.startswith("_")})

    def job_of(job_id: str) -> dict | None:
        with lock:
            job = jobs.get(job_id)
            return dict(job) if job is not None else None

    def wait(run: dict, state: dict, job_id: str) -> dict:
        deadline = time.time() + NODE_HOURS * 3600
        while True:
            job = job_of(job_id)
            if job is None:
                raise RuntimeError("its job left the queue before it finished")
            with runs_lock:
                state["job_status"] = job["status"]
                state["progress"] = job.get("progress")
            if job["status"] in ("done", "failed", "cancelled"):
                return job
            if run["_cancel"].is_set():
                stop(run, job_id)
                raise Cancelled()
            if time.time() > deadline:
                stop(run, job_id)
                raise RuntimeError(f"its job ran longer than {NODE_HOURS} hours and was stopped")
            run["_cancel"].wait(POLL_S)

    def stop(run: dict, job_id: str) -> None:
        cancel = route(run["_app"], "/api/jobs/{job_id}/cancel")
        if cancel is not None:
            try:
                cancel(job_id)
                return
            except HTTPException:
                pass
        with lock:  # without the app's route: a job that has not started leaves the queue
            job = jobs.get(job_id)
            if job is not None and job["status"] in ("queued", "running"):
                job["status"] = "cancelled"

    def submit(run: dict, path: str, kind: str, title: str, params: dict) -> dict:
        """The job queued through the app's own route for it, which checks it (the memory guard, the studio folder,
        the engine being installed). Without that route the card fails: a job is never queued unchecked."""

        endpoint = route(run["_app"], path)
        if endpoint is None:
            raise RuntimeError(f"this studio has no {path} to check and queue its job, so it was not queued")
        if path == "/api/jobs":
            return endpoint({"kind": kind, "title": title, "params": params})  # as many Sparks as the Sparks page says
        return endpoint({**params, "title": title})

    def media(job: dict, suffixes) -> list[str]:
        return [p for p in job.get("outputs") or [] if str(p).lower().endswith(suffixes)]

    def perform(run: dict, graph: dict, nid: str) -> None:
        nodes = {n["id"]: n for n in graph["nodes"]}
        node, state = nodes[nid], run["nodes"][nid]
        params, label = node["params"], title_of(node)
        feeds: dict[str, list] = {}
        for edge in graph["edges"]:
            if edge["to"] == nid:
                source = run["nodes"][edge["from"]]
                feeds.setdefault(edge["in"], []).append((source["outputs"][edge["out"]], out_type(nodes[edge["from"]], edge["out"]), source))
        one = lambda port: feeds[port][0][0] if feeds.get(port) else None
        outputs: dict = {}
        kind = node["type"]
        if kind == "text":
            outputs["text"] = params["text"].strip()
        elif kind == "reference":
            outputs["file"] = relative(inside(params["path"]))
        elif kind == "pick":
            images = one("images") or []
            if params["index"] > len(images):
                raise RuntimeError(f"there are {len(images)} pictures to pick from, not {params['index']}")
            outputs["image"] = images[params["index"] - 1]
        elif kind == "timeline":
            clips = [{"path": value, "in": 0, "out": 0, "duration": source.get("seconds") or 5.0} for value, _, source in feeds.get("clips", [])]
            song = feeds["song"][0][2].get("take") if feeds.get("song") else None
            outputs["timeline"] = {"clips": clips, "song": song}
            if params["project"]:
                state["project"] = add_to_project(params["project"], clips)
        else:
            if kind == "picture":
                width, height = params["width"], params["height"]
                job = run_job(run, state, "/api/jobs", "scout", f"Nodes · {label}",
                              {"prompt": one("prompt"), "count": params["count"], "seed": params["seed"], "width": width, "height": height,
                               "image_model": params["image_model"]})
                images = media(job, (".png",))
                if not images:
                    raise RuntimeError("the picture job finished without a picture")
                outputs.update(image=images[0], images=images)
            elif kind == "edit":
                job = run_job(run, state, "/api/jobs", "edit", f"Nodes · {label}",
                              {"image": one("image"), "prompt": one("prompt"), "seed": params["seed"], "image_model": params["image_model"]})
                images = media(job, IMAGES)
                if not images:
                    raise RuntimeError("the edit finished without a picture")
                outputs["image"] = images[0]
            elif kind == "clip":
                job_params = {"prompt": one("prompt"), "preset": params["preset"], "quality": params["quality"],
                              "seconds": params["seconds"], "seed": params["seed"]}
                if one("first_frame"):
                    job_params["image"] = one("first_frame")
                for value, made, _ in feeds.get("refs", []):
                    key = {"image": "references", "video": "reference_videos", "audio": "reference_audios"}[made]
                    job_params.setdefault(key, []).append(value)
                if params["look"]:
                    job_params["loras"] = [{"id": params["look"]}]
                job = run_job(run, state, "/api/jobs", "video", f"Nodes · {label}", job_params)
                clips = media(job, VIDEOS)
                if not clips:
                    raise RuntimeError("the clip job finished without a clip")
                outputs["video"] = clips[0]
                state["seconds"] = round(lengths.nearest(params["seconds"]) / lengths.FPS, 3)  # the exact length: 17n + 5 frames
            elif kind == "voice":
                job = run_job(run, state, "/api/voice/takes", "voice", f"Nodes · {label}", {"text": one("text"), "seed": params["seed"]})
                outputs["audio"] = (job.get("outputs") or [None])[0]
                state["take"] = (job.get("voice") or {}).get("id")
            elif kind == "song":
                job = run_job(run, state, "/api/music/takes", "music", f"Nodes · {label}",
                              {"caption": one("style"), "lyrics": one("lyrics") or "", "seconds": params["seconds"], "seed": params["seed"]})
                outputs["audio"] = (job.get("outputs") or [None])[0]
                state["take"] = (job.get("music") or {}).get("id")
            elif kind == "export":
                source = one("source")
                width, height = (int(v) for v in params["size"].split("x"))
                clips = source["clips"] if isinstance(source, dict) else [{"path": source}]
                job_params = {"clips": clips, "width": width, "height": height}
                if isinstance(source, dict) and source.get("song"):
                    job_params["soundtrack"] = {"take": source["song"], "music_db": params["music_db"]}
                job = run_job(run, state, "/api/jobs", "export", f"Nodes · {label}", job_params)
                outputs["file"] = (job.get("outputs") or [None])[0]
            if not all(outputs.values()):
                raise RuntimeError("the job finished without its file")
        with runs_lock:
            state["outputs"] = outputs
            shown = next((v for v in outputs.values() if isinstance(v, str) and kind_of_path(v)), None)
            state["thumb"] = shown

    def run_job(run: dict, state: dict, path: str, kind: str, title: str, params: dict) -> dict:
        queued = submit(run, path, kind, title, params)
        with runs_lock:
            state["job"] = queued["id"]
        job = wait(run, state, queued["id"])
        if job["status"] == "cancelled":
            raise Cancelled()
        if job["status"] == "failed":
            raise RuntimeError(job.get("error") or "its job failed")
        return job

    def add_to_project(project_id: str, clips: list[dict]) -> str:
        file = home / "projects" / project_id / "project.json"
        if not file.is_file():
            raise RuntimeError("the project for the timeline is gone")
        data = json.loads(file.read_text())
        data.setdefault("timeline", []).extend(clips)
        fresh = file.with_name(f"{file.name}.{uuid.uuid4().hex}.new")  # one per write: two writes never share it
        fresh.write_text(json.dumps(data, indent=1))
        fresh.replace(file)
        return data.get("name") or project_id

    def execute(run: dict, graph: dict) -> None:
        with runs_lock:
            run["status"], run["started"] = "running", time.time()
        failed = set()
        waits = {}
        for edge in graph["edges"]:
            waits.setdefault(edge["to"], set()).add(edge["from"])
        for nid in run["order"]:
            state = run["nodes"][nid]
            if run["_cancel"].is_set():
                with runs_lock:
                    state["status"] = "cancelled"
                continue
            behind = waits.get(nid, set()) & failed
            if behind:
                failed.add(nid)
                with runs_lock:
                    state["status"] = "skipped"
                    state["error"] = "waits on " + ", ".join(title_of(next(n for n in graph["nodes"] if n["id"] == b)) for b in sorted(behind)) + ", which did not finish"
                continue
            with runs_lock:
                state["status"], state["started"], run["current"] = "running", time.time(), nid
            try:
                perform(run, graph, nid)
                with runs_lock:
                    state["status"] = "done"
            except Cancelled:
                with runs_lock:
                    state["status"] = "cancelled"
                run["_cancel"].set()
            except Exception as error:  # noqa: BLE001 - one card that fails stops what waits on it, not the run
                failed.add(nid)
                with runs_lock:
                    state["status"], state["error"] = "failed", str(getattr(error, "detail", error))[-600:]
            finally:
                with runs_lock:
                    state["finished"] = time.time()
        with runs_lock:
            run["current"] = None
            run["finished"] = time.time()
            run["status"] = "cancelled" if run["_cancel"].is_set() else "failed" if failed else "done"

    @router.post("/api/nodes/run")
    def start(body: dict, request: Request) -> dict:
        given = body.get("graph")
        if given is None and body.get("id"):
            given = get_graph(str(body["id"]))
        graph = checked(given, inside)
        # one run at a time: the slot is taken here, under the lock, and held through the checks until the run is in
        # the list (two quick Runs would otherwise both pass the check before either is added)
        if not starting.acquire(blocking=False):
            raise HTTPException(409, "a graph is starting: wait for it, or cancel it once it runs")
        try:
            with runs_lock:
                if any(r["status"] in ("queued", "running") for r in runs.values()):
                    raise HTTPException(409, "a graph is running: cancel it or let it finish first")
            return begin(body, graph, request.app)
        finally:
            starting.release()

    def begin(body: dict, graph: dict, app) -> dict:
        steps = order(graph)
        estimate = route(app, "/api/estimate")
        clips = [node for node in graph["nodes"] if node["type"] == "clip"]
        if clips and estimate is None:  # never skipped: without the memory guard's answer no clip is queued
            raise HTTPException(503, "this studio has no /api/estimate to check the clips against the machine's memory")
        if estimate is not None:  # the memory guard's answer for every clip before anything is queued
            for node in graph["nodes"]:
                if node["type"] != "clip":
                    continue
                refs = [e for e in graph["edges"] if e["to"] == node["id"] and e["in"] == "refs"]
                said = estimate({"preset": node["params"]["preset"], "quality": node["params"]["quality"], "seconds": node["params"]["seconds"],
                                 "referenced": bool(refs), "references": len(refs), "with_lora": bool(node["params"]["look"])})
                room = (said or {}).get("memory") or {}
                if room.get("fits") is False:
                    raise HTTPException(400, f"{title_of(node)}: {room.get('reason') or 'it does not fit this machine’s memory'}")
        run_id = "r" + time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        run = {"id": run_id, "graph": str(body.get("id") or "") or None, "name": graph["name"], "status": "queued", "created": time.time(),
               "started": None, "finished": None, "current": None, "order": steps,
               "nodes": {n["id"]: {"status": "waiting" if n["id"] in steps else "idle", "job": None, "outputs": {}, "error": None, "thumb": None}
                         for n in graph["nodes"]},
               "_cancel": threading.Event(), "_app": app}
        with runs_lock:
            runs[run_id] = run
            for old in sorted(runs, key=lambda k: runs[k]["created"])[:-KEPT_RUNS]:
                if runs[old]["status"] not in ("queued", "running"):
                    runs.pop(old)
        threading.Thread(target=execute, args=(run, graph), daemon=True).start()
        return public(run)

    @router.get("/api/nodes/runs")
    def list_runs() -> list[dict]:
        with runs_lock:
            listed = sorted(runs.values(), key=lambda r: -r["created"])
        return [public(r) for r in listed]

    @router.get("/api/nodes/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(404, "no such run")
        return public(run)

    @router.post("/api/nodes/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> dict:
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(404, "no such run")
        if run["status"] in ("queued", "running"):
            run["_cancel"].set()
            current = run["nodes"].get(run.get("current") or "", {})
            if current.get("job"):
                stop(run, current["job"])
        return public(run)

    return router


class Cancelled(Exception):
    pass
