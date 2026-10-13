"""Render nodes: more than one machine behind one queue.

A studio can hand a queued job to another machine that runs the same studio as a render node. The node does the whole
job through its own job API (the same one the page uses); this side sends the files the job reads, follows its
progress, relays a cancel, and brings back what it made. A job a node cannot take, or loses, goes to another node or is
rendered here. One job runs on a machine at a time, as before: N machines render N jobs at once.

Kept in the studio folder:
  nodes.json   {"local": {"render": true}, "nodes": [{"name", "url", "token", "shared", "drain"}]}
  node-token   the token another studio must present to use this one as a node (or STUDIO_TOKEN in the environment)
  jobs.json    the queue, so a restart keeps finished jobs' cards, queued jobs, and jobs running on a node

`shared: true` says the node's studio folder is this one (a network folder): nothing is copied either way.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

POLL = float(os.environ.get("STUDIO_NODE_POLL", "1.0"))       # seconds between looks at a node's job
LOST = float(os.environ.get("STUDIO_NODE_LOST", "30"))        # a node silent this long has lost its job
TRIES = 2                                                      # a job is moved to another machine at most this often
RELAYED = ("progress", "engine", "qa", "passes", "pace")
KEPT = ("id", "kind", "status", "title", "params", "source", "outputs", "progress", "error", "created", "started",
        "finished", "estimate", "engine", "qa", "passes", "pace", "node", "remote", "tries", "skip")
LOCAL = ("127.0.0.1", "::1", "localhost", "testclient")


def call(node: dict, path: str, body=None, method: str | None = None, timeout: float = 15, raw: bool = False):
    """One request to a node with its token. JSON in and out, or bytes out with ``raw``."""

    data = None if body is None else body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
    request = urllib.request.Request(node["url"].rstrip("/") + path, data, method=method or ("POST" if data is not None else "GET"))
    request.add_header("X-Studio-Token", node.get("token") or "")
    if data is not None:
        request.add_header("Content-Type", "application/octet-stream" if isinstance(body, (bytes, bytearray)) else "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return answer.read() if raw else json.loads(answer.read() or b"null")
    except urllib.error.HTTPError as error:
        text = error.read().decode("utf-8", "replace")
        try:
            said = json.loads(text).get("error") or text
        except (ValueError, AttributeError):
            said = text
        raise Refused(error.code, str(said)[:400]) from None


class Refused(Exception):
    """The node answered and said no (a 4xx or 5xx): not a lost connection."""

    def __init__(self, status: int, said: str):
        super().__init__(said)
        self.status, self.said = status, said


def read_token(home: Path) -> str:
    return (os.environ.get("STUDIO_TOKEN") or "").strip() or ((home / "node-token").read_text().strip() if (home / "node-token").is_file() else "")


def new_token(home: Path) -> str:
    """This studio's node token, made on first use."""

    token = read_token(home)
    if not token:
        token = secrets.token_urlsafe(24)
        target = home / "node-token"
        target.write_text(token + "\n")
        target.chmod(0o600)
    return token


def referenced(params, home: Path) -> list[str]:
    """Every file under the studio folder that a job's settings name, as paths relative to it."""

    found: list[str] = []
    root = home.resolve()

    def look(value) -> None:
        if isinstance(value, dict):
            for item in value.values():
                look(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                look(item)
        elif isinstance(value, str) and 2 < len(value) < 1024 and "\n" not in value:
            path = (root / value) if not os.path.isabs(value) else Path(value)
            try:
                path = path.resolve()
            except OSError:
                return
            if root not in path.parents:
                return
            if path.is_file():
                found.append(str(path.relative_to(root)))
            elif path.is_dir():  # a motion pack, a folder of stills
                found.extend(str(p.relative_to(root)) for p in sorted(path.rglob("*")) if p.is_file())

    look(params)
    return list(dict.fromkeys(found))


def guard(app, home: Path) -> None:
    """Who may call this studio. The node API always wants the token. With a token set, a caller from another machine
    wants it for everything (header X-Studio-Token, a Bearer header, or ?token= once, which sets a cookie)."""

    @app.middleware("http")
    async def check(request: Request, call_next):
        token = read_token(home)
        path = request.url.path
        given = (request.headers.get("x-studio-token") or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
                 or request.query_params.get("token") or request.cookies.get("studio_token") or "")
        good = bool(token) and secrets.compare_digest(given, token)
        if path.startswith("/api/node/"):
            if not token:
                return JSONResponse({"error": "this studio is not set up as a render node (no node token)"}, status_code=403)
            if not good:
                return JSONResponse({"error": "the node token is missing or wrong"}, status_code=401)
        elif token and not good and (request.client.host if request.client else "") not in LOCAL:
            return JSONResponse({"error": "this studio wants its token: open it once with ?token=…"}, status_code=401)
        response = await call_next(request)
        if good and request.query_params.get("token"):
            response.set_cookie("studio_token", token, httponly=True, samesite="lax")
        return response


def attach(home: Path, pack: Path, jobs: dict, order: list, lock, wake, running: dict, info, inside):
    """(router, ready, store) for the server: `ready(job)` says whether the local worker may take a job now."""

    book = home / "nodes.json"
    saved = home / "jobs.json"
    live: dict[str, dict] = {}       # node name -> what it last said about itself
    busy: dict[str, str] = {}        # node name -> the job of ours it is rendering
    router = APIRouter()
    version = (pack / "VERSION").read_text().strip()

    def config() -> dict:
        try:
            found = json.loads(book.read_text())
        except (OSError, ValueError):
            found = {}
        found.setdefault("local", {"render": True})
        found.setdefault("nodes", [])
        return found

    def keep(found: dict) -> None:
        part = book.with_suffix(".part")
        part.write_text(json.dumps(found, indent=1))
        part.chmod(0o600)  # it holds the nodes' tokens
        part.replace(book)

    def nodes() -> list[dict]:
        return config()["nodes"]

    # ---- this studio as a node --------------------------------------------------------------------------------

    @router.get("/api/node/info")
    def node_info() -> dict:
        active = sum(1 for j in jobs.values() if j["status"] in ("queued", "running"))
        free = None
        try:
            free = next(int(line.split()[1]) // 1048576 for line in open("/proc/meminfo") if line.startswith("MemAvailable"))
        except (OSError, StopIteration):
            pass
        return {"name": socket.gethostname(), "version": version, "active": active, "memory_gb": free, "home": str(home), **info()}

    @router.put("/api/node/file")
    async def node_file(path: str, request: Request) -> dict:
        """A file a job will read, written where the sender has it (inside the studio folder, never over its settings)."""

        target = inside(path)
        if target.name in ("nodes.json", "node-token", "jobs.json") or target == home.resolve():
            raise HTTPException(400, "not a file a job reads")
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        size = 0
        with open(part, "wb") as out:
            async for chunk in request.stream():
                out.write(chunk)
                size += len(chunk)
        part.replace(target)
        return {"path": path, "bytes": size}

    @router.get("/api/node/has")
    def node_has(path: str) -> dict:
        target = inside(path)
        return {"bytes": target.stat().st_size if target.is_file() else None}

    # ---- this studio as the one that hands jobs out -----------------------------------------------------------

    def look(node: dict) -> dict:
        try:
            said = call(node, "/api/node/info", timeout=4)
            said["ok"], said["why"] = True, ""
            if said.get("version") != version:
                said["ok"], said["why"] = False, f"it runs version {said.get('version')}, this studio {version}: upgrade one of them"
        except Refused as error:
            said = {"ok": False, "why": error.said}
        except Exception as error:  # noqa: BLE001 - unreachable, refused connection, timeout
            said = {"ok": False, "why": f"not reachable ({str(error)[-120:]})"}
        said["seen"] = time.time()
        live[node["name"]] = said
        return said

    def shown(node: dict) -> dict:
        said = live.get(node["name"]) or {}
        job = busy.get(node["name"])
        state = "draining" if node.get("drain") else "rendering" if job else "offline" if said and not said.get("ok") else "busy" if said.get("active") else "free" if said else "unknown"
        return {"name": node["name"], "url": node["url"], "shared": bool(node.get("shared")), "drain": bool(node.get("drain")),
                "state": state, "why": said.get("why", ""), "job": job, "version": said.get("version"),
                "platform": said.get("platform"), "memory_gb": said.get("memory_gb"), "engine": said.get("engine"), "host": said.get("name")}

    @router.get("/api/nodes")
    def list_nodes() -> dict:
        found = config()
        return {"local": {"render": bool(found["local"].get("render", True)), "name": socket.gethostname()},
                "nodes": [shown(n) for n in found["nodes"]], "token_set": bool(read_token(home))}

    @router.post("/api/nodes")
    def add_node(body: dict) -> dict:
        url = (body.get("url") or "").strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = "http://" + url if url else ""
        if not url:
            raise HTTPException(400, "a node needs its address, like http://10.0.0.2:7870")
        node = {"name": "", "url": url, "token": (body.get("token") or "").strip(), "shared": bool(body.get("shared")), "drain": False}
        said = look({**node, "name": "new"})
        live.pop("new", None)
        if not said.get("ok") and not said.get("version"):
            raise HTTPException(400, f"that address does not answer as a render node: {said.get('why')}")
        node["name"] = (body.get("name") or said.get("name") or urllib.parse.urlparse(url).hostname or "node").strip()[:40]
        with lock:
            found = config()
            if any(n["name"] == node["name"] or n["url"] == url for n in found["nodes"]):
                raise HTTPException(400, "that node is on the list already")
            found["nodes"].append(node)
            keep(found)
        look(node)
        return shown(node)

    @router.post("/api/nodes/{name}")
    def set_node(name: str, body: dict) -> dict:
        """`drain`: no new jobs for a node (the one it renders finishes). name "local": `render` on or off for this machine."""

        with lock:
            found = config()
            if name == "local":
                found["local"]["render"] = bool(body.get("render", True))
                keep(found)
                wake.set()
                return {"local": found["local"]}
            node = next((n for n in found["nodes"] if n["name"] == name), None)
            if node is None:
                raise HTTPException(404, "no such node")
            if "drain" in body:
                node["drain"] = bool(body["drain"])
            if "token" in body:
                node["token"] = str(body["token"]).strip()
            keep(found)
        return shown(node)

    @router.delete("/api/nodes/{name}")
    def remove_node(name: str) -> dict:
        with lock:
            if name in busy:
                raise HTTPException(400, "that node is rendering a job: drain it and wait, or cancel the job")
            found = config()
            found["nodes"] = [n for n in found["nodes"] if n["name"] != name]
            keep(found)
        live.pop(name, None)
        return {"removed": name}

    @router.post("/api/node-token")
    def make_token() -> dict:
        """This studio's own node token, shown to whoever sits at this machine (to paste into the studio that hands out jobs)."""

        return {"token": new_token(home)}

    # ---- which job goes where -----------------------------------------------------------------------------------

    def waiting_on(job: dict) -> bool:
        """A clip that opens or ends on a clip that is still being made waits for it."""

        params = job["params"]
        for key in ("first_frame_from", "last_frame_from"):
            if params.get(key) and not (home / params[key]).is_file():
                mine = order.index(job["id"]) if job["id"] in order else len(order)
                if any(jobs[i]["status"] in ("queued", "running") for i in order[:mine] if i in jobs):
                    return True
        return False

    def ready(job: dict) -> bool:
        """The local worker's question: may this machine render this queued job now?"""

        if waiting_on(job):
            return False
        if job["kind"] == "export":  # a join of files that are here
            return True
        found = config()
        if found["local"].get("render", True):
            return True
        # this machine only hands jobs out: it still takes what no node can
        return not any(usable(n, job) for n in found["nodes"])

    def usable(node: dict, job: dict) -> bool:
        said = live.get(node["name"]) or {}
        return bool(said.get("ok")) and not node.get("drain") and job["kind"] != "export" and node["name"] not in (job.get("skip") or [])

    def claim() -> tuple[dict, dict] | None:
        found = config()
        with lock:
            queued = [jobs[i] for i in order if i in jobs and jobs[i]["status"] == "queued" and not waiting_on(jobs[i])]
            if not queued:
                return None
            local_idle = found["local"].get("render", True) and running.get("id") is None and not any(
                j["status"] == "running" and not j.get("node") for j in jobs.values())
            for node in found["nodes"]:
                said = live.get(node["name"]) or {}
                if node["name"] in busy or not said.get("ok") or node.get("drain") or said.get("active"):
                    continue
                # the first job is this machine's when it is idle: a node takes the next
                for job in (queued[1:] if local_idle else queued):
                    if usable(node, job) and job["kind"] != "export":
                        job.update(status="running", started=time.time(), node=node["name"], remote=None,
                                   progress={"stage": f"sending to {node['name']}", "at": 0, "of": 1})
                        busy[node["name"]] = job["id"]
                        return node, job
        return None

    def back_to_queue(job: dict, node: dict, why: str, count: bool = True) -> None:
        with lock:
            busy.pop(node["name"], None)
            if job["status"] == "cancelled":
                return
            job["skip"] = sorted(set(job.get("skip") or []) | {node["name"]})
            job["tries"] = int(job.get("tries") or 0) + (1 if count else 0)
            if job["tries"] > TRIES:
                job.update(status="failed", error=f"{why} (tried on {job['tries']} machines)", finished=time.time())
                return
            job["log"] = (job.get("log", "") + f"\n[studio] {node['name']}: {why}; the job goes back to the queue\n")[-20000:]
            job.update(status="queued", started=None, node=None, remote=None, progress=None)
        wake.set()

    def send(node: dict, job: dict) -> None:
        if node.get("shared"):
            return
        files = referenced(job["params"], home)
        for index, path in enumerate(files):
            job["progress"] = {"stage": f"sending to {node['name']}", "at": index, "of": len(files)}
            source = home / path
            try:
                there = call(node, "/api/node/has?path=" + urllib.parse.quote(path))
            except Refused:
                there = {}
            if there.get("bytes") == source.stat().st_size:
                continue
            request = urllib.request.Request(node["url"].rstrip("/") + "/api/node/file?path=" + urllib.parse.quote(path),
                                             data=open(source, "rb"), method="PUT")
            request.add_header("X-Studio-Token", node.get("token") or "")
            request.add_header("Content-Length", str(source.stat().st_size))
            request.add_header("Content-Type", "application/octet-stream")
            with urllib.request.urlopen(request, timeout=600) as answer:
                answer.read()

    def fetch(node: dict, job: dict, outputs: list[str]) -> None:
        if node.get("shared"):
            return
        for index, path in enumerate(outputs):
            job["progress"] = {"stage": f"bringing back from {node['name']}", "at": index, "of": len(outputs)}
            target = inside(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + ".part")
            request = urllib.request.Request(node["url"].rstrip("/") + "/files/" + urllib.parse.quote(path))
            request.add_header("X-Studio-Token", node.get("token") or "")
            with urllib.request.urlopen(request, timeout=600) as answer, open(part, "wb") as out:
                while chunk := answer.read(1 << 20):
                    out.write(chunk)
            part.replace(target)

    def follow(node: dict, job: dict) -> None:
        """Send one claimed job to a node and stay with it to its end."""

        try:
            if not job.get("remote"):
                send(node, job)
                made = call(node, "/api/jobs", {"kind": job["kind"], "params": job["params"], "title": job.get("title")}, timeout=60)
                job["remote"] = made["id"]
            silent = None
            while True:
                if job["status"] == "cancelled":
                    try:
                        call(node, f"/api/jobs/{job['remote']}/cancel", {}, timeout=10)
                    except Exception:  # noqa: BLE001 - the node may be gone; the job is cancelled here either way
                        pass
                    break
                try:
                    there = call(node, f"/api/jobs/{job['remote']}", timeout=10)
                    silent = None
                except Refused as error:
                    if error.status == 404:  # the node was restarted and no longer knows the job
                        return back_to_queue(job, node, "the node no longer has the job")
                    raise
                except Exception:  # noqa: BLE001 - a dropped connection: wait for the node before giving the job up
                    silent = silent or time.time()
                    if time.time() - silent > LOST:
                        live[node["name"]] = {"ok": False, "why": "it stopped answering", "seen": time.time()}
                        return back_to_queue(job, node, f"the node stopped answering for {LOST:.0f} s")
                    time.sleep(POLL)
                    continue
                for key in RELAYED:
                    if there.get(key) is not None:
                        job[key] = there[key]
                if there.get("progress"):
                    job["progress"] = {**there["progress"], "stage": f"{node['name']} · {there['progress'].get('stage', '')}"}
                job["log"] = there.get("log") or job.get("log", "")
                if there["status"] == "done":
                    fetch(node, job, there.get("outputs") or [])
                    job.update(outputs=there.get("outputs") or [], status="done", progress=None, finished=time.time())
                    break
                if there["status"] in ("failed", "cancelled"):
                    said = there.get("error") or f"the job was {there['status']} on the node"
                    return back_to_queue(job, node, f"it failed there: {said}"[:500])
                time.sleep(POLL)
        except Refused as error:
            # the node will not take this job (a setting or a model it does not have): another machine may
            return back_to_queue(job, node, f"it will not take the job: {error.said}", count=False)
        except Exception as error:  # noqa: BLE001 - sending or fetching broke
            return back_to_queue(job, node, f"lost on the way ({str(error)[-160:]})")
        finally:
            job.setdefault("finished", None)
        with lock:
            busy.pop(node["name"], None)
            if job["status"] == "cancelled":
                job["finished"] = time.time()
        wake.set()

    def hand_out() -> None:
        last = 0.0
        while True:
            time.sleep(POLL)
            try:
                listed = nodes()
                if not listed:
                    continue
                if time.time() - last > 5 or wake.is_set():
                    for node in listed:
                        if node["name"] not in busy:
                            look(node)
                    last = time.time()
                    if any(j["status"] == "queued" for j in list(jobs.values())):
                        wake.set()  # a node that came or went changes what this machine should take
                while (found := claim()) is not None:
                    threading.Thread(target=follow, args=found, daemon=True).start()
            except Exception:  # noqa: BLE001 - the queue outlives a bad nodes.json or a node that answers nonsense
                time.sleep(2)

    # ---- the queue across a restart -----------------------------------------------------------------------------

    def store() -> None:
        """Load the saved queue, then keep it saved. Jobs that were on a node are picked up where they were."""

        try:
            for job in json.loads(saved.read_text()) if os.environ.get("STUDIO_JOB_STORE", "1") != "0" else []:
                if job.get("id") in jobs:
                    continue
                job.setdefault("log", "")
                if job["status"] == "running" and job.get("node") and job.get("remote"):
                    node = next((n for n in nodes() if n["name"] == job["node"]), None)
                    if node is not None:
                        busy[node["name"]] = job["id"]
                        threading.Thread(target=follow, args=(node, job), daemon=True).start()
                    else:
                        job.update(status="queued", node=None, remote=None, started=None)
                elif job["status"] == "running":
                    job.update(status="failed", error="the studio was restarted while this job was rendering", finished=time.time())
                jobs[job["id"]] = job
                order.append(job["id"])
            if any(j["status"] == "queued" for j in jobs.values()):
                wake.set()
        except (OSError, ValueError, KeyError, TypeError):
            pass

        def keep_saved() -> None:
            before = None
            while True:
                time.sleep(0.5)  # often: a job saved as queued after it finished would be rendered again
                try:
                    with lock:
                        now = json.dumps([{k: jobs[i].get(k) for k in KEPT} | {"log": (jobs[i].get("log") or "")[-3000:]}
                                          for i in order[-200:] if i in jobs])
                    if now != before:
                        part = saved.with_suffix(".part")
                        part.write_text(now)
                        part.replace(saved)
                        before = now
                except Exception:  # noqa: BLE001 - a full disk must not stop the queue
                    pass

        if os.environ.get("STUDIO_JOB_STORE", "1") != "0":
            threading.Thread(target=keep_saved, daemon=True).start()
        threading.Thread(target=hand_out, daemon=True).start()

    return router, ready, store
