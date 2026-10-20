"""Sparks: one queue rendered on several DGX Sparks.

A job carries a number, ``sparks`` (1 to LIMIT): how many machines it may use, this one counted as one. The other
machines are worker Sparks, each running its own copy of this app on 127.0.0.1:7870, never opened to the network:
this app reaches a worker through an ssh tunnel it opens itself (key login as the user who runs the app) and closes
when the worker is removed or the app stops. A clip handed to a worker is rendered there through the worker's own job
API, the one its page uses: the pictures the clip reads are uploaded, the job is queued, its progress and a cancel are
relayed, and the clip is fetched into this studio folder, where the QA gate and the shot check then run as on a clip
made here. So every clip on a worker passes that worker's own memory guard (spark/memory_guard.py), twice: asked
before anything is sent, and again by its app when the job is queued.

What N means:
- several clips (queued jobs, a Production board, Slate shots): one clip per Spark, in parallel, on this Spark and the
  first N - 1 workers that are up and can render the clip;
- one clip: it renders on one Spark. Splitting one clip across Sparks is measured (docs/multi-spark.md) and is not
  in the app;
- a long video is a chain (each piece opens on the last frame of the one before): one Spark, this one;
- pictures, edits, Library actions and exports: this Spark.

A worker that stops answering while it renders is marked down and its clip goes back to the queue for another
machine; a worker is never waited on for more than LOST seconds. One job runs on a Spark at a time.

Kept in the studio folder, under sparks/:
  sparks.json    {"workers": [{"address", "name", "folder", "enabled", "via"?}], "sparks": the number new jobs start with}
  takes.jsonl    one line per finished clip: its path, seed, settings and the Spark that rendered it
  tunnels.json   the tunnels this app has open (closed by the next start if this one was killed)

This app may be on a DGX Spark or on a Mac. A Mac that cannot reach the workers' network goes through a machine that
can (a worker's ``via``: the Spark that leads them): the Mac logs in there, and that machine logs in to the worker
with its own key and forwards the worker's 127.0.0.1:7870 to a socket in a folder of its user's, which the Mac's
tunnel ends on (Relay). No key is added anywhere for it. On a Mac at the head only what a Spark renders with its own
settings is handed over: FastH3 clips at the sizes a Spark makes without the 2x decoder, never MiniMax H3 Turbo's
qualities or a text encoder file of the Mac's (capable).
"""

from __future__ import annotations

import atexit
import hashlib
import json
import math
import os
import re
import select
import shlex
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

import remote

LIMIT = 5        # the most Sparks a job may ask for: five, the Sparks there are and the most that have been measured
PORT = 7870      # a worker's app, on its own 127.0.0.1
POLL = float(os.environ.get("STUDIO_SPARK_POLL", "2"))     # seconds between looks at a worker's job
LOST = float(os.environ.get("STUDIO_SPARK_LOST", "60"))    # a worker silent this long while it renders is down
CALL = float(os.environ.get("STUDIO_SPARK_CALL", "10"))    # the longest one request to a worker may take
MOVES = 2        # a clip is taken back from a worker at most this often; after that it renders on this Spark
DISK_GB = 2      # a worker with less free disk than this is not given a clip
KINDS = ("video",)  # what is handed to a worker: single clips
PREFIX = "$HOME/.local/opt/tensorfold-studio"
THEIR_HOME = "$HOME/TensorFoldStudio"  # a worker's studio folder, and where its app's pid and log are kept
ADDRESS = re.compile(r"(?:[a-z_][a-z0-9_-]{0,31}@)?[A-Za-z0-9][A-Za-z0-9.-]{0,252}")
FOLDER = re.compile(r"[A-Za-z0-9_+][A-Za-z0-9_.+/-]{0,200}")
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3"]
RELAY = "$HOME/.cache/studio-sparks"  # on the machine a worker is reached through: the relays' sockets, the user's own
BEAT = 10        # seconds between the heartbeats a relay is sent; one silent for three of them ends itself
SOCKET = re.compile(r"/[A-Za-z0-9_.+/-]{1,80}")
# the files that are the app: a worker whose copy of any of them differs from this Spark's is updated before it is used
CODE = ("VERSION", "*.py", "app/*.py", "app/*.json", "app/static/*.js", "app/static/index.html", "spark/*.py", "spark/*.sh",
        "spark/tensorfold_h3/*.py", "spark/studio_mmaudio/*.py", "scripts/*.sh", "scripts/*.py")
# Eight 56-frame 864x480 FastH3 clips (8 passes, int8 engine) through this app's own API on 2026-10-13, this Spark and
# four workers on the 200G fabric, models loaded, one run each: 263.2, 129.8, 97.5, 69.0 and 65.0 s of wall time on
# 1 to 5 Sparks (docs/multi-spark.md). With n Sparks the clips go in ceil(8 / n) rounds, so 8 / rounds is the most
# there is to have: five was no better than four for eight clips, two rounds either way.
MEASURED_CLIPS = 8
MEASURED = {1: 1.0, 2: 2.03, 3: 2.70, 4: 3.81, 5: 4.05}
STATES = {"new": "not checked yet", "unreachable": "does not answer over ssh", "bare": "no Studio installed there",
          "stopped": "its Studio app is not running", "update": "its Studio code differs from this Spark's",
          "ready": "idle", "busy": "rendering", "down": "stopped answering"}
# presets that need the 2x decoder, which a DGX Spark does not have (spark/video.sh refuses them); said by name
DOUBLED = {"draft": "Draft", "2k": "2K", "qhd": "2K 2560x1440", "p960": "960p"}
ONE_CLIP = ("Splitting a single clip across Sparks is measured (1.40x on 2, 1.73x on 3, 1.77x on 4 for a 5 s 864x480 clip) "
            "and is not in the app yet.")


class Refused(Exception):
    """The worker's app answered and said no."""

    def __init__(self, status: int, said: str):
        super().__init__(said)
        self.status, self.said = status, said


class Moved(Exception):
    """The clip was taken back from its worker and is in the queue again."""


def call(base: str, path: str, body=None, method: str | None = None, timeout: float = CALL):
    """One JSON request to an app. Refused when it answers with an error, OSError when it does not answer."""

    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data, method=method or ("POST" if data is not None else "GET"),
                                     headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return json.loads(answer.read() or b"null")
    except urllib.error.HTTPError as error:
        text = error.read().decode("utf-8", "replace")
        try:
            said = json.loads(text).get("error") or text
        except (ValueError, AttributeError):
            said = text
        raise Refused(error.code, str(said)[:600]) from None
    except ValueError as error:  # an answer that is not JSON: not this app
        raise OSError(f"not a Studio app's answer: {error}") from None


def send(base: str, path: Path, timeout: float = 300) -> str:
    """A file of this studio folder put into the worker's uploads (its /api/upload); the path it has there."""

    mark = uuid.uuid4().hex
    head = (f'--{mark}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n").encode()
    request = urllib.request.Request(base + "/api/upload", head + path.read_bytes() + f"\r\n--{mark}--\r\n".encode(),
                                     headers={"Content-Type": f"multipart/form-data; boundary={mark}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return json.loads(answer.read())["path"]
    except urllib.error.HTTPError as error:
        raise Refused(error.code, error.read().decode("utf-8", "replace")[:300]) from None
    except (ValueError, KeyError) as error:
        raise OSError(f"not a Studio app's answer: {error}") from None


def digest(pack: Path) -> dict:
    """The app's files (CODE) as {path: sha1}: what a worker's copy is held against."""

    found = {}
    for pattern in CODE:
        for path in sorted(pack.glob(pattern)):
            if path.is_file():
                found[str(path.relative_to(pack))] = hashlib.sha1(path.read_bytes()).hexdigest()
    return found


def differs(mine: dict, theirs: dict | None) -> list[str]:
    """The files of this Spark's code a worker has otherwise or not at all (what it has besides does not count)."""

    theirs = theirs or {}
    return sorted(name for name, value in mine.items() if theirs.get(name) != value)


def address_of(given: str) -> str:
    """``host`` or ``user@host`` as ssh takes it; ValueError for anything else (nothing that ssh could read as an option)."""

    text = (given or "").strip()
    if not ADDRESS.fullmatch(text):
        raise ValueError("a Spark's address is its host name or IP address, as you would type it after ssh (10.100.10.5 or user@spark-5)")
    return text


def folder_of(given: str) -> str:
    """Where the Studio is on a worker, under its home folder."""

    text = (given or "").strip().strip("/")
    if not FOLDER.fullmatch(text) or ".." in text.split("/"):
        raise ValueError("the Studio's folder on a Spark is a path under the home folder there, like keys-Mac-TensorFold-Studio")
    return text


def hop(via: str, address: str, command: str) -> list[str]:
    """``command`` run on ``address`` from ``via``: ssh to ``via``, and ssh from there with its own key. ``address``
    is checked by address_of; the command is quoted once for the shell there."""

    return [*SSH, via, " ".join([*SSH, address, shlex.quote(command)])]


def shell(address: str, command: str, timeout: float = 30, via: str | None = None) -> tuple[int, str]:
    """A command on a worker over ssh, as the user who runs this app: (status, what it printed). 255 with the reason
    when ssh itself cannot reach it or log in. With ``via`` it goes through that machine (hop)."""

    try:
        done = subprocess.run(hop(via, address, command) if via else [*SSH, address, command], capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 255, f"no answer over ssh in {timeout:g} s"
    except OSError as error:
        return 255, str(error)
    return done.returncode, (done.stdout + done.stderr).strip()


def copy(pack: Path, address: str, folder: str, timeout: float = 900, via: str | None = None) -> tuple[int, str]:
    """This Spark's Studio code sent to a worker with rsync over ssh. Models, the engine and the environment are not
    part of it (they are the worker's own install); what the worker's folder holds besides is left alone. With
    ``via`` the copy goes through that machine; macOS's own rsync (openrsync) takes the same words."""

    command = ["rsync", "-a", "--exclude", ".git", "--exclude", "samples", "--exclude", "outputs", "--exclude", "payload",
               "--exclude", "__pycache__", "--exclude", "*.pyc", "--exclude", ".DS_Store",
               "-e", " ".join([*SSH, via, *SSH] if via else SSH), f"{pack}/", f"{address}:{folder}/"]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 255, f"the copy did not finish in {timeout:g} s"
    except OSError as error:
        return 255, str(error)
    return done.returncode, (done.stdout + done.stderr).strip()[-400:]


class Tunnel:
    """127.0.0.1:<a free port here> to 127.0.0.1:PORT on a worker, through ssh. The worker's app stays closed to the
    network; the tunnel is this process's child and is closed with it."""

    def __init__(self, address: str):
        self.address, self.process, self.port, self.mark = address, None, None, None

    def free(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]

    def open(self, wait: float = 12) -> str:
        self.free()
        self.mark = f"127.0.0.1:{self.port}:127.0.0.1:{PORT}"
        self.process = subprocess.Popen([*SSH, "-o", "ExitOnForwardFailure=yes", "-N", "-L", self.mark, self.address],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        return self.reach(wait)

    def reach(self, wait: float) -> str:
        until = time.time() + wait
        while time.time() < until:
            if self.process.poll() is not None:
                raise OSError("no tunnel: " + ((self.process.stderr.read() or "").strip()[-200:] or "ssh stopped"))
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
                return f"http://127.0.0.1:{self.port}"
            except OSError:
                time.sleep(0.15)
        self.close()
        raise OSError(f"no tunnel to {self.address} in {wait:g} s")

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None


class Relay(Tunnel):
    """127.0.0.1:<a free port here> to 127.0.0.1:PORT on a worker that this machine cannot reach, through ``via``,
    which can: one ssh from here to ``via`` forwards the local port to a socket in a folder of the user's own there,
    and on ``via`` a second ssh, logged in to the worker with ``via``'s own key, forwards that socket to the worker's
    127.0.0.1:PORT. The second one lives only as long as the first: it is ended when the first's input closes (this
    app closes it, or stops, or its ssh dies) or when the heartbeat this app sends every BEAT seconds stops for three
    of them, and its socket is removed then. Nothing is opened to a network on either machine."""

    def __init__(self, address: str, via: str):
        super().__init__(address)
        self.via, self.beating = via, None

    def open(self, wait: float = 20) -> str:
        code, said = shell(self.via, f'mkdir -p "{RELAY}" && chmod 700 "{RELAY}" && cd "{RELAY}" && pwd -P', 15)
        folder = said.strip().splitlines()[-1] if code == 0 and said.strip() else ""
        if code or not SOCKET.fullmatch(folder):
            raise OSError(f"no relay through {self.via}: " + (said[-200:] or "ssh stopped"))
        self.free()
        place = f"{folder}/{uuid.uuid4().hex[:12]}.sock"
        self.mark = f"127.0.0.1:{self.port}:{place}"
        self.process = subprocess.Popen([*SSH, "-o", "ExitOnForwardFailure=yes", "-L", self.mark, self.via, "bash -c " + shlex.quote(self.script(place, wait))],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ready, until = False, time.time() + wait
        while not ready and time.time() < until and self.process.poll() is None:
            if select.select([self.process.stdout], [], [], max(0.0, until - time.time()))[0]:
                line = self.process.stdout.readline()
                if not line:
                    break
                ready = line.strip() == "ready"
        if not ready:
            said = (self.process.stderr.read() or "").strip()[-200:] if self.process.poll() is not None else f"it was not up in {wait:g} s"
            self.close()
            raise OSError(f"no relay to {self.address} through {self.via}: " + (said or "ssh stopped"))
        self.beating = threading.Thread(target=self.beat, args=(self.process,), daemon=True)
        self.beating.start()
        return self.reach(5)

    def script(self, place: str, wait: float = 20) -> str:
        """What runs on ``via``: its own ssh to the worker, forwarding the socket ``place`` to the worker's
        127.0.0.1:PORT; "ready" once the socket is there; then it lives while its input is open and beating."""

        return (f'S={place}; rm -f "$S"; {" ".join(SSH)} -o ExitOnForwardFailure=yes -N -L "$S:127.0.0.1:{PORT}" {self.address} < /dev/null & p=$!; '
                'i=0; while [ ! -S "$S" ]; do kill -0 $p 2>/dev/null || { wait $p; exit 1; }; i=$((i+1)); '
                f'[ $i -lt {int(wait * 10)} ] || {{ kill $p; exit 1; }}; sleep 0.1; done; echo ready; '
                f'while kill -0 $p 2>/dev/null && read -r -t {BEAT * 3:g} _; do :; done; kill $p 2>/dev/null; rm -f "$S"')

    def beat(self, process) -> None:
        while process.poll() is None:
            try:
                process.stdin.write("\n")
                process.stdin.flush()
            except (OSError, ValueError):
                return
            time.sleep(BEAT)

    def close(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.stdin.close()  # the relay there reads its end and stops its own ssh
                process.wait(timeout=5)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        super().close()


def capable(kind: str, params: dict, info: dict | None, spark: bool = True) -> str:
    """Why a worker cannot render this job ("" when it can), from what its app says it has (/api/spark/self).
    ``spark`` False: the job was queued on a Mac, whose own settings a Spark does not have."""

    info = info or {}
    if kind not in KINDS:
        return "only single clips are handed to another Spark"
    if not info.get("fasth3"):
        return "FastH3 is not installed there"
    preset = params.get("preset") or ("small" if spark else "draft")  # what each machine renders when none is named
    if info.get("presets") is not None and preset not in info["presets"]:
        return (f"the {DOUBLED.get(preset, preset)} preset is made with the 2x decoder, which is not on that machine" if preset in DOUBLED
                else f"the {preset} preset is not rendered there")
    if not spark and params.get("text_encoder") not in (None, "", "regular"):
        return "the text encoder file is one of this Mac's (an MLX file): a Spark reads only the files in its own ComfyUI folder"
    media = bool(params.get("reference_videos") or params.get("reference_audios"))
    if params.get("references") or media:
        if not info.get("ref2va"):
            return "MiniMax H3 Ref2VA (reference to video) is not installed there"
        if params.get("ref_turbo") and not info.get("ref2va_turbo"):
            return "the Ref2VA Turbo adapter is not installed there"
        if media and not info.get("ffmpeg"):
            return "reference videos and audio need ffmpeg, which is not installed there"
    elif info.get("qualities") is not None and params.get("quality") and params["quality"] not in info["qualities"]:
        return (f"the {params['quality']} quality is MiniMax H3 Turbo, which is not on that machine: a Spark renders FastH3 "
                "(fh4, fh8, fh20)")
    missing = [str(one.get("id")) for one in params.get("loras") or [] if isinstance(one, dict) and one.get("id") not in (info.get("loras") or [])]
    if missing:
        return "the LoRA " + ", ".join(missing) + " is not installed there"
    encoder = params.get("text_encoder")
    if encoder not in (None, "", "regular") and Path(str(encoder)).name not in (info.get("text_encoders") or []):
        return "the text encoder file is not on that Spark"
    return ""


def here_only(kind: str, params: dict) -> str:
    """Why a job renders on this Spark whatever the number says ("" for a single clip, which may go to a worker)."""

    if kind == "long":
        return "A long video is a chain: each piece opens on the last frame of the one before, so its pieces render one after another, on this Spark."
    if kind == "studio":
        return "A clip that starts from a new picture renders on this Spark: the picture model is here."
    if kind not in KINDS:
        return "Pictures, edits, Library actions and exports render on this Spark: only clips are handed to other Sparks."
    return ""


def speedup(used: int, clips: int) -> tuple[float, bool]:
    """(how many times faster than one Spark, whether that number of Sparks was measured) for ``clips`` clips of one
    length on ``used`` Sparks: the rounds the clips go in, times what the measured run kept of its own rounds."""

    used = max(1, min(used, clips))
    measured = used in MEASURED
    known = MEASURED.get(used) or MEASURED[max(MEASURED)]
    kept = min(1.0, known / (MEASURED_CLIPS / math.ceil(MEASURED_CLIPS / min(used, max(MEASURED)))))
    return max(1.0, clips / math.ceil(clips / used) * kept), measured


class Hub:
    """The workers of one studio and the clips on them. ``jobs``, ``order``, ``lock`` and ``wake`` are the server's
    queue; ``info`` is what this app says of itself to another (its /api/spark/self); ``inside`` resolves a path of
    the studio folder; ``work(job, worker)`` runs a claimed job to its end (the server's own job routine);
    ``timing(kind, params)`` is one clip's measured time on one Spark, or None."""

    def __init__(self, home: Path, pack: Path, spark: bool, jobs: dict, order: list, lock, wake, info, inside, work, timing=None):
        # ``spark``: this app is on a DGX Spark; False on a Mac, which leads workers the same way (capable says what
        # of a Mac's settings a Spark cannot render, and a worker it cannot reach is reached through its ``via``)
        self.home, self.pack, self.spark = home, pack, spark
        self.word = "Spark" if spark else "Mac"
        self.jobs, self.order, self.lock, self.wake = jobs, order, lock, wake
        self.info, self.inside, self.work, self.timing = info, inside, work, timing or (lambda kind, params: None)
        self.folder = home / "sparks"
        self.book = self.folder / "sparks.json"
        self.name = socket.gethostname()
        self.live: dict[str, dict] = {}   # address -> state, why, info, base, tunnel, job, working, checked
        self.mutex = threading.RLock()    # over self.live; taken after the queue's lock, never before it
        self.kick = threading.Event()
        self.mine: dict | None = None
        self.shell, self.copy, self.dial = shell, copy, lambda address: Relay(address, self.via(address)) if self.via(address) else Tunnel(address)  # stood in for by the tests
        # True while this Studio installs its own updates by itself (updates.Manager.automatic): a worker whose code
        # is behind is then brought level the same way, once per version of this Spark's code (catch_up)
        self.follow = lambda: False
        self.started = False

    # ---- the list ---------------------------------------------------------------------------------------------

    def config(self) -> dict:
        try:
            found = json.loads(self.book.read_text())
        except (OSError, ValueError):
            found = {}
        found.setdefault("workers", [])
        found.setdefault("sparks", 1)
        return found

    def keep(self, found: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        part = self.book.with_suffix(".part")
        part.write_text(json.dumps(found, indent=1))
        part.replace(self.book)

    def workers(self) -> list[dict]:
        return self.config()["workers"]

    def said(self, text: str) -> str:
        """Words for the page: "this Spark" is "this Mac" on a Mac at the head."""

        return text if self.spark or not text else text.replace("this Spark", "this Mac")

    def via(self, address: str) -> str | None:
        """The machine a worker is reached through, or None: it is reached directly."""

        return next((worker.get("via") for worker in self.config()["workers"] if worker["address"] == address), None) or None

    def on(self, worker: dict, command: str, timeout: float = 30) -> tuple[int, str]:
        """A command on a worker, through its ``via`` when it has one."""

        if worker.get("via"):
            return self.shell(worker["address"], command, timeout, via=worker["via"])
        return self.shell(worker["address"], command, timeout)

    def default_folder(self) -> str:
        """Where a worker's Studio is unless said otherwise: where this one is, under the home folder."""

        try:
            return str(self.pack.resolve().relative_to(Path.home().resolve()))
        except ValueError:
            return self.pack.name

    def state(self, address: str) -> dict:
        with self.mutex:
            return self.live.setdefault(address, {"state": "new", "why": "", "info": None, "base": None, "tunnel": None, "job": None,
                                                  "working": "", "checked": None, "fails": 0})

    def usable(self, worker: dict) -> str:
        """Why a worker is given no clip now ("" when it may be): switched off, not up, or rendering for someone else."""

        live = self.state(worker["address"])
        if not worker.get("enabled", True):
            return "switched off"
        if live["working"]:
            return live["working"]
        if live["state"] == "busy" and not live["job"]:
            return "rendering a job that is not this Spark's"
        if live["state"] not in ("ready", "busy"):
            return live["why"] or STATES.get(live["state"], live["state"])
        disk = (live["info"] or {}).get("disk_gb")
        if disk is not None and disk < DISK_GB:
            return f"its disk is nearly full ({disk:g} GB free)"
        return ""

    def shown(self, worker: dict) -> dict:
        live = self.state(worker["address"])
        info, why = live["info"] or {}, self.usable(worker)
        memory = info.get("memory") or {}
        return {"address": worker["address"], "name": worker.get("name") or worker["address"], "folder": worker.get("folder"),
                "enabled": worker.get("enabled", True), "state": live["state"], "says": self.said(STATES.get(live["state"], live["state"])),
                "why": self.said(live["why"]), "usable": not why, "unusable": self.said(why), "working": live["working"], "checked": live["checked"],
                "job": live["job"], "version": info.get("version") or live.get("version"), "via": worker.get("via"),
                "memory_gb": round(memory["available_mb"] / 1000, 1) if memory.get("available_mb") is not None else live.get("memory_gb"),
                "disk_gb": info.get("disk_gb"),
                "guard": bool(info.get("guard")),
                "can": {key: bool(info.get(key)) for key in ("fasth3", "image", "ref2va", "ref2va_turbo", "ffmpeg")} | {"loras": len(info.get("loras") or [])} if info else None}

    def overview(self) -> dict:
        found = self.config()
        rows = [self.shown(worker) for worker in found["workers"]]
        mine = self.info()
        with self.lock:
            local = next((job["id"] for job in self.jobs.values() if job["status"] == "running" and job.get("spark") == self.name), None)
        memory = (mine.get("memory") or {}).get("available_mb")
        return {"supported": True, "limit": LIMIT, "note": "", "available": 1 + sum(1 for row in rows if row["usable"]), "listed": 1 + len(rows),
                "default": max(1, min(int(found.get("sparks") or 1), LIMIT)), "workers": rows, "folder": self.default_folder(),
                "head": "spark" if self.spark else "mac", "via": next((w.get("via") for w in reversed(found["workers"]) if w.get("via")), None),
                "this": {"name": self.name, "kind": self.word, "version": mine.get("version"), "job": local, "state": "busy" if local else "ready",
                         "guard": bool(mine.get("guard")), "memory_gb": round(memory / 1000, 1) if memory is not None else None, "disk_gb": mine.get("disk_gb"),
                         "can": {key: bool(mine.get(key)) for key in ("fasth3", "image", "ref2va", "ref2va_turbo", "ffmpeg")} | {"loras": len(mine.get("loras") or [])}}}

    # ---- a worker: check, update and start, stop -----------------------------------------------------------------

    def code(self) -> dict:
        if self.mine is None:
            self.mine = digest(self.pack)
        return self.mine

    def connect(self, address: str) -> str:
        """The worker's app as a local address, through a tunnel opened now if none is open."""

        live = self.state(address)
        with self.mutex:
            tunnel = live["tunnel"]
            if tunnel is not None and tunnel.alive() and live["base"]:
                return live["base"]
            if tunnel is not None:
                tunnel.close()
            live["tunnel"], live["base"] = None, None
        tunnel = self.dial(address)
        base = tunnel.open()
        with self.mutex:
            live["tunnel"], live["base"] = tunnel, base
        self.note_tunnels()
        return base

    def hang_up(self, address: str) -> None:
        live = self.state(address)
        with self.mutex:
            tunnel, live["tunnel"], live["base"] = live["tunnel"], None, None
        if tunnel is not None:
            tunnel.close()
        self.note_tunnels()

    def note_tunnels(self) -> None:
        """The open tunnels' process ids, written down: a start after a kill closes what that run left behind."""

        with self.mutex:
            pids = [[live["tunnel"].process.pid, getattr(live["tunnel"], "mark", None)] for live in self.live.values()
                    if live["tunnel"] is not None and getattr(live["tunnel"], "process", None) is not None and live["tunnel"].alive()]
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / "tunnels.json").write_text(json.dumps(pids))
        except OSError:
            pass

    def close(self) -> None:
        """Every tunnel closed: when the app stops."""

        with self.mutex:
            addresses = list(self.live)
        for address in addresses:
            self.hang_up(address)

    def sweep(self) -> None:
        """Tunnels a killed run of this app left open: ssh processes it wrote down that still forward to a worker's app."""

        note = self.folder / "tunnels.json"
        try:
            pids = json.loads(note.read_text())
        except (OSError, ValueError):
            return
        for one in pids if isinstance(pids, list) else []:
            pid, mark = (one[0], one[1]) if isinstance(one, list) and len(one) == 2 else (one, None)
            try:
                words = command_of(int(pid))
                if words[:1] == ["ssh"] and (mark in words if mark else "-N" in words and any(word.endswith(f":127.0.0.1:{PORT}") for word in words)):
                    os.kill(int(pid), signal.SIGTERM)
            except (OSError, ValueError, TypeError):
                pass
        note.unlink(missing_ok=True)

    def probe(self, worker: dict) -> dict:
        """What ssh finds on a worker: its name, whether the Studio and its environment are there, its memory, and
        whether an app answers on its 127.0.0.1:PORT (and whether a head started that app)."""

        where = f'"$HOME/{worker.get("folder") or self.default_folder()}"'
        code, said = self.on(worker, (
            f'F={where}; P="${{PREFIX:-{PREFIX}}}"; H="{THEIR_HOME}"; echo "host $(hostname)"; echo "version $(cat "$F/VERSION" 2>/dev/null)"; '
            '[ -f "$F/app/server.py" ] && echo "pack yes"; [ -x "$P/venv/bin/python" ] && echo "install yes"; '
            'grep -E "^(MemTotal|MemAvailable)" /proc/meminfo; '
            f'(exec 3<>/dev/tcp/127.0.0.1/{PORT}) 2>/dev/null && echo "app yes"; '
            '[ -f "$H/app.pid" ] && kill -0 "$(cat "$H/app.pid")" 2>/dev/null && echo "ours yes"; true'), 25)
        found = {"reached": code == 0, "said": said}
        for line in said.splitlines() if code == 0 else []:
            key, _, value = line.partition(" ")
            if key in ("host", "version", "pack", "install", "app", "ours"):
                found[key] = value.strip()
            elif key.rstrip(":") in ("MemTotal", "MemAvailable"):
                found[key.rstrip(":")] = int(value.split()[0]) // 1024
        return found

    def check(self, worker: dict) -> dict:
        """Look at a worker and say what state it is in. Nothing on it is started, stopped or changed."""

        address, live = worker["address"], self.state(worker["address"])
        found = self.probe(worker)
        set_to = lambda state, why="", **more: live.update(state=state, why=why, checked=time.time(), fails=0, **more)
        if not found["reached"]:
            self.hang_up(address)
            set_to("unreachable", (f"ssh through {worker['via']} did not get in (key logins are needed from here to {worker['via']} and from there to it): "
                                   if worker.get("via") else "ssh as this user did not get in (a key login is needed): ") + found["said"][-160:], info=None)
            return live
        if found.get("host") and worker.get("name") in (None, "", address):
            self.rename(address, found["host"])
        live["version"] = found.get("version") or None
        live["memory_gb"] = round(found["MemAvailable"] / 1000, 1) if "MemAvailable" in found else None
        if not found.get("pack") or not found.get("install"):
            self.hang_up(address)
            set_to("bare", ("No Studio code in " + (worker.get("folder") or self.default_folder()) + " there. " if not found.get("pack") else "")
                   + ("" if found.get("install") else "No Studio environment there (models, ComfyUI, the engine): run oneshot-setup-spark.sh on that Spark once; "
                      "“Update and start” sends the code, not the models."), info=None)
            return live
        if not found.get("app"):
            self.hang_up(address)
            set_to("stopped", "Its Studio app is not running: “Update and start” starts it.", info=None)
            return live
        try:
            info = call(self.connect(address), "/api/spark/self", timeout=CALL)
        except Refused:
            set_to("update", f"Its app is Studio {found.get('version') or '?'}, from before the Sparks setting and the memory guard: “Update and start” "
                   "brings it to this Spark's code.", info=None)
            return live
        except OSError as error:
            self.hang_up(address)
            set_to("down", "Its app does not answer through the tunnel: " + str(error)[-160:], info=None)
            return live
        changed = differs(self.code(), info.get("code"))
        if changed or not info.get("guard"):
            set_to("update", f"Its Studio code differs from this Spark's in {len(changed)} file{'s' if len(changed) != 1 else ''} ({', '.join(changed[:3])}"
                   + (", …" if len(changed) > 3 else "") + "): “Update and start” brings it level.", info=info)
            return live
        if info.get("active") and not live["job"]:
            set_to("busy", "It is rendering a job that is not this Spark's.", info=info)
        else:
            set_to("busy" if live["job"] else "ready", "", info=info)
        return live

    def rename(self, address: str, name: str) -> None:
        found = self.config()
        taken = {worker.get("name") for worker in found["workers"] if worker["address"] != address} | {self.name}
        for worker in found["workers"]:
            if worker["address"] == address:
                worker["name"] = name if name not in taken else f"{name} ({address})"
        self.keep(found)

    def update(self, worker: dict) -> dict:
        """This Spark's code sent to a worker (rsync, as this user), its app started from it, and the worker checked
        again. An app that is rendering is left alone; an app no head started is not stopped here."""

        address, live = worker["address"], self.state(worker["address"])
        folder = worker.get("folder") or self.default_folder()
        found = self.probe(worker)
        if not found["reached"]:
            return self.check(worker)
        if found.get("app"):
            try:
                if any(job.get("status") in ("queued", "running") for job in call(self.connect(address), "/api/jobs", timeout=CALL)):
                    live.update(why="It is rendering: update it when its job is done.", checked=time.time())
                    return live
            except (OSError, Refused):
                pass
        code, said = (self.copy(self.pack, address, folder, via=worker["via"]) if worker.get("via") else self.copy(self.pack, address, folder))
        if code:
            live.update(state="unreachable" if code == 255 else live["state"], why="The code could not be sent (rsync): " + said[-200:], checked=time.time())
            return live
        self.hang_up(address)
        if found.get("app") and not found.get("ours"):
            live.update(state="update", why="The code is there. An app that this Studio did not start is running on that Spark: stop it there, "
                        "then press “Update and start” again.", checked=time.time(), info=None)
            return live
        code, said = self.on(worker, self.stop_command(folder, comfy=False) + "; " + self.start_command(folder), 40)
        if code:
            live.update(state="stopped", why="Its app could not be started: " + said[-200:], checked=time.time(), info=None)
            return live
        for _ in range(30):  # the app needs a few seconds to come up
            time.sleep(1)
            if self.probe(worker).get("app"):
                break
        return self.check(worker)

    @staticmethod
    def start_command(folder: str) -> str:
        # in a subshell of its own with every stream closed off: ssh returns at once and the app outlives it
        return (f'cd "$HOME/{folder}" && mkdir -p "{THEIR_HOME}" && (STUDIO_PIDFILE="{THEIR_HOME}/app.pid" HOST=127.0.0.1 PORT={PORT} '
                f'nohup setsid bash scripts/app.sh > "{THEIR_HOME}/app.log" 2>&1 < /dev/null &) && echo started')

    @staticmethod
    def stop_command(folder: str, comfy: bool = True) -> str:
        """Stop the app a head started on a worker (by the pid that app wrote down, checked to be a Studio app) and,
        with ``comfy``, the ComfyUI server behind it, which gives the models' memory back."""

        stop = (f'H="{THEIR_HOME}"; if [ -f "$H/app.pid" ] && grep -qa "app/server.py" "/proc/$(cat "$H/app.pid")/cmdline" 2>/dev/null; then '
                'kill "$(cat "$H/app.pid")"; for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$(cat "$H/app.pid")" 2>/dev/null || break; sleep 0.5; done; '
                'rm -f "$H/app.pid"; echo "app stopped"; else echo "no app of ours"; fi')
        return stop + (f'; cd "$HOME/{folder}" && bash spark/comfy.sh stop' if comfy else "")

    def stop(self, worker: dict) -> dict:
        """A worker's app (the one a head started) and its ComfyUI server stopped: its memory is free again."""

        address, live = worker["address"], self.state(worker["address"])
        if live["job"]:
            live.update(why="It is rendering one of this Spark's clips: cancel the job first.")
            return live
        found = self.probe(worker)
        if found["reached"] and found.get("app") and not found.get("ours"):
            live.update(why="The app on that Spark was not started by this Studio: it is left running.", checked=time.time())
            return live
        self.hang_up(address)
        self.on(worker, self.stop_command(worker.get("folder") or self.default_folder()), 40)
        return self.check(worker)

    def act(self, address: str, what: str) -> dict:
        """Check, update or stop a worker in the background; the page reads the result from the list."""

        worker = next((one for one in self.workers() if one["address"] == address), None)
        if worker is None:
            raise HTTPException(404, "no such Spark in the list")
        live = self.state(address)
        with self.mutex:
            if live["working"]:
                return self.shown(worker)
            live["working"] = {"check": "checking", "update": "updating and starting", "stop": "stopping"}[what]

        def go() -> None:
            try:
                {"check": self.check, "update": self.update, "stop": self.stop}[what](worker)
            except Exception as error:  # noqa: BLE001 - said beside the worker, never raised into a thread's void
                live.update(why=f"{what} failed: {str(error)[-200:]}", checked=time.time())
            finally:
                live["working"] = ""
                self.kick.set()
            if what == "check":
                self.catch_up(worker)

        threading.Thread(target=go, daemon=True).start()
        return self.shown(worker)

    def catch_up(self, worker: dict) -> None:
        """With automatic updates on, a worker that is behind only in its code gets this Spark's code (“Update and
        start”, as a person would press it), once per version of that code: one that then fails is left for a person,
        never tried in a loop. A worker that is down, unreachable or bare is never touched here."""

        live = self.state(worker["address"])
        if not self.follow() or not worker.get("enabled", True) or live["state"] != "update" or live["job"] or live["working"]:
            return
        version = hashlib.sha1(json.dumps(self.code(), sort_keys=True).encode()).hexdigest()
        if live.get("caught") == version:
            return
        live["caught"] = version
        self.act(worker["address"], "update")

    # ---- which Spark renders which clip -------------------------------------------------------------------------------

    def asked(self, job: dict) -> int:
        try:
            return max(1, min(int((job.get("sparks") or {}).get("chosen") or 1), LIMIT))
        except (TypeError, ValueError):
            return 1

    def team(self, job: dict) -> list[str]:
        """The workers a job may render on, in the list's order: the first N - 1 that are up and can render it."""

        sparks = job.get("sparks") or {}
        if self.asked(job) < 2 or here_only(job["kind"], job["params"]) or sparks.get("moves", 0) >= MOVES:
            return []
        found = []
        for worker in self.workers():
            if worker["address"] in (sparks.get("not") or {}) or self.usable(worker):
                continue
            if capable(job["kind"], job["params"], self.state(worker["address"])["info"], self.spark):
                continue
            found.append(worker["address"])
        return found[:self.asked(job) - 1]

    def waits(self, job: dict) -> bool:
        """Whether a clip opens or ends on a clip that is still to be made: it waits for that one."""

        wanted = {str(job["params"].get(key)) for key in ("first_frame_from", "last_frame_from") if job["params"].get(key)}
        if not wanted:
            return False
        for other in self.jobs.values():
            if other is not job and other["status"] in ("queued", "running") and other["kind"] in ("video", "studio", "long"):
                if (other["params"].get("output") or f"renders/{other['id']}.mp4") in wanted:
                    return True
        return False

    def claim(self, address: str | None = None) -> dict | None:
        """The next queued job for a machine (None: this Spark), marked as running on it. Called with the queue's lock
        held. This Spark takes first what no idle worker could take, so a clip a worker can render is left to it."""

        ready = [self.jobs[i] for i in self.order if self.jobs[i]["status"] == "queued" and not self.waits(self.jobs[i])]
        if address is None:
            idle = {worker["address"] for worker in self.workers() if not self.usable(worker) and not self.state(worker["address"])["job"]}
            job = next((job for job in ready if not idle.intersection(self.team(job))), None) or (ready[0] if ready else None)
        else:
            job = next((job for job in ready if address in self.team(job)), None)
        if job is None:
            return None
        job["status"], job["started"] = "running", time.time()
        job["remote"] = address
        job["spark"] = self.name if address is None else next((w.get("name") or address for w in self.workers() if w["address"] == address), address)
        if address is not None:
            self.state(address).update(job=job["id"], state="busy")
        return job

    def glance(self, worker: dict) -> bool:
        """A quick look at a connected worker before a clip is offered to it: is its app there and idle."""

        address, live = worker["address"], self.state(worker["address"])
        try:
            info = call(self.connect(address), "/api/spark/self", timeout=5)
        except (OSError, Refused) as error:
            live["fails"] += 1
            if live["fails"] >= 3:
                self.hang_up(address)
                live.update(state="down", why="It stopped answering: " + str(error)[-120:], checked=time.time(), info=None)
            return False
        live.update(info=info, fails=0, checked=time.time())
        if differs(self.code(), info.get("code")):
            live.update(state="update", why="Its Studio code no longer matches this Spark's: “Update and start” brings it level.")
            return False
        if info.get("active"):
            live.update(state="busy", why="It is rendering a job that is not this Spark's.")
            return False
        live.update(state="ready", why="")
        return True

    def loop(self) -> None:
        """Hands queued clips to idle workers, one each; looks at every connected worker now and then."""

        looked = 0.0
        while True:
            self.kick.wait(timeout=1.0)
            self.kick.clear()
            try:
                with self.lock:
                    waiting = any(job["status"] == "queued" and self.asked(job) > 1 for job in self.jobs.values())
                refresh = time.time() - looked > 20
                if refresh:
                    looked = time.time()
                for worker in self.workers():
                    live = self.state(worker["address"])
                    if live["job"] and refresh and live["base"]:  # rendering for us: only its memory and disk are read again
                        try:
                            live["info"] = call(live["base"], "/api/spark/self", timeout=5)
                        except (OSError, Refused):
                            pass  # the thread that follows its job decides when it is lost
                    if refresh and live["state"] == "update":
                        self.catch_up(worker)
                    if live["job"] or live["working"] or not worker.get("enabled", True) or live["state"] not in ("ready", "busy"):
                        continue
                    if not (waiting or refresh) or not self.glance(worker) or not waiting:
                        continue
                    with self.lock:
                        job = self.claim(worker["address"])
                    if job is not None:
                        threading.Thread(target=self.run, args=(job, worker), daemon=True).start()
            except Exception as error:  # noqa: BLE001 - the loop outlives a bad moment
                print(f"[sparks] {error}", flush=True)

    def run(self, job: dict, worker: dict) -> None:
        try:
            self.work(job, worker)
        finally:
            live = self.state(worker["address"])
            with self.mutex:
                if live["job"] == job["id"]:
                    live["job"] = None
                    if live["state"] == "busy":
                        live["state"] = "ready"
            self.kick.set()
            self.wake.set()

    def start(self) -> None:
        """At the app's start: last run's tunnels closed, the loop started, the listed workers looked at."""

        if self.started:
            return
        self.started = True
        self.sweep()
        atexit.register(self.close)
        threading.Thread(target=self.loop, daemon=True).start()
        for worker in self.workers():
            if worker.get("enabled", True):
                self.act(worker["address"], "check")

    # ---- one clip on a worker -----------------------------------------------------------------------------------------

    def move(self, job: dict, worker: dict, why: str, down: bool) -> None:
        """A clip taken back from a worker: in the queue again, never for that worker; the worker marked down when it
        is the worker that failed. Raises Moved (or nothing was moved: the job was cancelled meanwhile)."""

        address, name = worker["address"], worker.get("name") or worker["address"]
        live = self.state(address)
        if down:
            self.hang_up(address)
            live.update(state="down", why=why, checked=time.time(), info=None)
        with self.lock:
            if job["status"] != "running":
                return
            sparks = job.setdefault("sparks", {"chosen": 1})
            sparks.setdefault("not", {})[address] = why
            sparks["moves"] = sparks.get("moves", 0) + 1
            job.setdefault("moved", []).append({"from": name, "why": why, "at": time.time()})
            job["log"] = (job["log"] + f"[sparks] {name}: {why}. The clip is back in the queue.\n")[-20000:]
            job.update(status="queued", started=None, progress=None, spark=None, remote=None, their_job=None)
        raise Moved(why)

    def outbound(self, base: str, params: dict) -> dict:
        """A clip's settings as the worker takes them: the pictures and clips it reads uploaded to the worker and named
        by their paths there, and what this Spark does itself (the checks after the render, the take's place in a
        project) left out."""

        theirs = {key: value for key, value in params.items()
                  if key not in ("output", "first_frame_from", "last_frame_from", "retake", "silent", "slate", "brain")}
        for key in ("image", "last_image"):
            if theirs.get(key):
                theirs[key] = send(base, self.inside(theirs[key]))
        for key in ("references", "reference_videos", "reference_audios"):
            if theirs.get(key):
                theirs[key] = [send(base, self.inside(path)) for path in theirs[key] if path]
        theirs.update(qa=False, shot_check=False)  # both run here, on the fetched clip, where their tools are
        return theirs

    def render(self, job: dict, worker: dict, params: dict, target: Path, label: str = "") -> bool:
        """One take of ``job``'s clip rendered on ``worker`` and fetched to ``target``. False when the job was
        cancelled meanwhile; Moved when the worker refused the clip, failed it, or stopped answering."""

        address, name = worker["address"], worker.get("name") or worker["address"]
        say = lambda text: job.update(log=(job["log"] + f"[sparks] {self.said(text)}\n")[-20000:])
        try:
            base = self.connect(address)
            # the worker's own memory guard, asked before anything is sent (its app asks it again when the job is queued)
            room = (call(base, "/api/estimate", {**{key: params.get(key) for key in ("preset", "quality", "frames", "seconds", "width", "height", "steps", "text_encoder", "ref_turbo")},
                                                 "with_lora": bool(params.get("loras")), "referenced": bool(params.get("references") or params.get("reference_videos")),
                                                 "references": len(params.get("references") or [])}) or {}).get("memory") or {}
            if room.get("fits") is False:
                raise Refused(400, room.get("reason") or "its memory guard refused the clip")
            made = call(base, "/api/jobs", {"kind": job["kind"], "title": f"{job['title']} · for {self.name}", "params": self.outbound(base, params)}, timeout=60)
        except Refused as error:
            self.move(job, worker, f"it refused the clip: {error.said}", down=False)
            return False
        except OSError as error:
            self.move(job, worker, f"it stopped answering before the clip was queued ({str(error)[-120:]})", down=True)
            return False
        job["their_job"] = made["id"]
        say(f"{name} took the clip as its job {made['id']}" + (f"; its memory guard: about {room['need_mb'] / 1000:.0f} GB needed, {room.get('available_mb', 0) / 1000:.0f} GB there" if room.get("need_mb") else ""))
        silent, theirs = None, {}
        while True:
            if job["status"] != "running":  # cancelled here: cancelled there
                try:
                    call(base, f"/api/jobs/{made['id']}/cancel", {}, timeout=CALL)
                except (OSError, Refused):
                    pass
                return False
            try:
                theirs = call(base, f"/api/jobs/{made['id']}", timeout=CALL)
                silent = None
            except Refused as error:
                self.move(job, worker, "its app no longer has the job (it restarted)" if error.status == 404 else f"its app answered {error.status}", down=True)
                return False
            except OSError as error:
                silent = silent or time.time()
                if time.time() - silent > LOST:
                    self.move(job, worker, f"it stopped answering for {LOST:g} s while rendering ({str(error)[-100:]})", down=True)
                    return False
                time.sleep(POLL)
                continue
            if theirs.get("progress"):
                job["progress"] = {**theirs["progress"], "stage": f"{label}{name} · {theirs['progress'].get('stage', '')}"}
            if theirs.get("engine"):
                job["engine"] = theirs["engine"]
            if theirs.get("status") == "done":
                break
            if theirs.get("status") in ("failed", "cancelled"):
                self.move(job, worker, "its render failed: " + str(theirs.get("error") or "cancelled on that Spark")[-300:], down=False)
                return False
            time.sleep(POLL)
        try:
            if not theirs.get("outputs"):
                raise LookupError("its job finished without a clip")
            remote.fetch(base, theirs["outputs"][0], target, timeout=300)
        except (OSError, LookupError, ValueError) as error:
            self.move(job, worker, f"its clip could not be fetched ({str(error)[-160:]})", down=isinstance(error, OSError))
            return False
        said = [line for line in (theirs.get("log") or "").splitlines() if "[tensorfold]" in line or "[studio]" in line]
        job["log"] = (job["log"] + "\n".join(said[-12:]) + ("\n" if said else ""))[-20000:]
        say(f"{name} rendered it in {theirs.get('elapsed') or 0:.0f} s; the clip is in this Spark's folder")
        return job["status"] == "running"

    # ---- what a clip was rendered on ----------------------------------------------------------------------------------

    def record(self, job: dict) -> None:
        """A finished clip written into the takes list: its path, seed, settings and the Spark that rendered it."""

        if job["kind"] not in ("video", "studio", "long") or not job.get("outputs"):
            return
        params = job["params"]
        line = {"path": job["outputs"][0], "job": job["id"], "title": job.get("title"), "kind": job["kind"], "seed": params.get("seed"),
                "settings": {key: params[key] for key in ("preset", "quality", "frames", "seconds", "width", "height", "steps", "loras", "total_seconds")
                             if params.get(key) not in (None, "", [])},
                "spark": job.get("spark") or self.name, "address": job.get("remote"), "sparks": self.asked(job),
                "moved": job.get("moved") or None, "elapsed": round(time.time() - job["started"], 1) if job.get("started") else None,
                "finished": time.time()}
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with open(self.folder / "takes.jsonl", "a") as out:
                out.write(json.dumps(line) + "\n")
        except OSError:
            pass

    def takes(self) -> dict:
        """path -> the last line written for it."""

        found = {}
        try:
            with open(self.folder / "takes.jsonl") as lines:
                for line in lines:
                    try:
                        one = json.loads(line)
                        found[one["path"]] = one
                    except (ValueError, KeyError, TypeError):
                        continue
        except OSError:
            pass
        return found

    # ---- what a number means, said before a job is queued ---------------------------------------------------------------

    def plan(self, body: dict) -> dict:
        try:
            chosen = max(1, min(int(body.get("sparks") or 1), LIMIT))
            clips = max(1, int(body.get("clips") or 1))
        except (TypeError, ValueError):
            raise HTTPException(400, "sparks and clips are whole numbers")
        kind, params = str(body.get("kind") or "video"), body.get("params") if isinstance(body.get("params"), dict) else {}
        able, without = [], []
        for worker in self.workers():
            why = self.usable(worker) or capable(kind, params, self.state(worker["address"])["info"], self.spark)
            (without.append({"name": worker.get("name") or worker["address"], "why": self.said(why)}) if why else able.append(worker["address"]))
        listed, available = 1 + len(able) + len(without), 1 + len(able)
        here = here_only(kind, params)
        used = 1 if here else min(chosen, available)
        count = f"{available} of {chosen} available" if chosen > available else f"{used} Spark{'s' if used != 1 else ''}"
        if here:
            mode, says = "one", self.said(here)
        elif used < 2:
            mode, says = "one", (f"This {self.word} alone." if chosen < 2 else "No other Spark can take this job now, so it renders on this one.")
        elif clips > 1:
            mode, says = "parallel", f"One clip per Spark, {min(used, clips)} at a time: {clips} clips on {min(used, clips)} Sparks."
        else:
            mode, says = "one", f"This clip renders on one Spark, the first that is free of the {used}; other queued clips render beside it. " + ONE_CLIP
        estimate = None
        try:  # one clip's time on one Spark: given, or the app's own measured figure for these settings
            seconds = float(body.get("seconds") or self.timing(kind, params) or 0)
        except (TypeError, ValueError):
            seconds = 0
        if seconds > 0:
            faster, measured = speedup(used, clips) if mode == "parallel" else (1.0, True)
            mac = mode == "parallel" and not self.spark  # the run was measured with a Spark at the head, every clip on a Spark
            estimate = {"seconds": int(seconds * clips / faster), "one_spark": int(seconds * clips), "faster": round(faster, 2), "measured": measured and not mac,
                        "note": "an estimate from one measured run of 8 clips" + ("" if measured else f"; {used} Sparks were not measured (five exist)")
                        + ("; that run had a Spark at the head, not a Mac, and this time is the Mac's for one clip" if mac else "")}
        return {"supported": True, "limit": LIMIT, "chosen": chosen, "available": available, "listed": listed, "used": used, "mode": mode,
                "count": count, "says": says, "without": without, "estimate": estimate}

    # ---- the API ------------------------------------------------------------------------------------------------------

    def router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/api/spark/self")
        def about_this_spark() -> dict:
            """What this app is and has, for a Studio on another Spark that hands clips to it."""

            return self.info()

        @router.get("/api/sparks")
        def sparks() -> dict:
            return self.overview()

        @router.post("/api/sparks")
        def settings(body: dict) -> dict:
            """``sparks``: the number new jobs start with."""

            found = self.config()
            try:
                found["sparks"] = max(1, min(int(body.get("sparks") or 1), LIMIT))
            except (TypeError, ValueError):
                raise HTTPException(400, "sparks is a whole number")
            self.keep(found)
            return self.overview()

        @router.post("/api/sparks/workers")
        def add(body: dict) -> dict:
            try:
                address = address_of(str(body.get("address") or ""))
                folder = folder_of(str(body["folder"])) if body.get("folder") else None
                via = address_of(str(body["via"])) if body.get("via") else None
            except ValueError as error:
                raise HTTPException(400, str(error))
            found = self.config()
            if any(worker["address"] == address for worker in found["workers"]):
                raise HTTPException(400, f"{address} is already in the list")
            if len(found["workers"]) >= LIMIT - 1:
                raise HTTPException(400, f"at most {LIMIT} Sparks, this one included")
            found["workers"].append({"address": address, "name": str(body.get("name") or "").strip()[:60] or address, "folder": folder, "enabled": True,
                                     **({"via": via} if via else {})})
            self.keep(found)
            self.act(address, "check")
            return self.overview()

        @router.post("/api/sparks/workers/{address}")
        def change(address: str, body: dict) -> dict:
            found = self.config()
            worker = next((one for one in found["workers"] if one["address"] == address), None)
            if worker is None:
                raise HTTPException(404, "no such Spark in the list")
            if "enabled" in body:
                worker["enabled"] = bool(body["enabled"])
            if body.get("name"):
                worker["name"] = str(body["name"]).strip()[:60]
            if "folder" in body or "via" in body:
                try:
                    if "folder" in body:
                        worker["folder"] = folder_of(str(body["folder"])) if body["folder"] else None
                    if "via" in body:
                        via = address_of(str(body["via"])) if body["via"] else None
                        worker.pop("via", None)
                        worker.update({"via": via} if via else {})
                except ValueError as error:
                    raise HTTPException(400, str(error))
                if "via" in body:
                    self.hang_up(address)  # the next look goes the new way
            self.keep(found)
            self.kick.set()
            return self.overview()

        @router.delete("/api/sparks/workers/{address}")
        def remove(address: str) -> dict:
            found = self.config()
            if not any(one["address"] == address for one in found["workers"]):
                raise HTTPException(404, "no such Spark in the list")
            if self.state(address)["job"]:
                raise HTTPException(400, self.said("that Spark is rendering one of this Spark's clips: cancel the job first"))
            found["workers"] = [one for one in found["workers"] if one["address"] != address]
            self.keep(found)
            self.hang_up(address)  # its app, if a head started it, keeps running there: "Stop" first to free its memory
            with self.mutex:
                self.live.pop(address, None)
            return self.overview()

        @router.post("/api/sparks/workers/{address}/{what}")
        def action(address: str, what: str) -> dict:
            if what not in ("check", "update", "stop"):
                raise HTTPException(404, "check, update or stop")
            self.act(address, what)
            return self.overview()

        @router.post("/api/sparks/check")
        def check_all() -> dict:
            for worker in self.workers():
                self.act(worker["address"], "check")
            return self.overview()

        @router.post("/api/sparks/plan")
        def what_a_number_means(body: dict) -> dict:
            """What ``sparks`` will do for a job (``kind``, ``params``) or for ``clips`` jobs like it, before it is
            queued: how many Sparks can take it, which cannot and why, and the time at that number where one clip's
            time is measured for these settings (or given as ``seconds``)."""

            return self.plan(body)

        @router.get("/api/sparks/takes")
        def rendered_on(path: str = "") -> dict:
            found = self.takes()
            return found.get(path) or {} if path else found

        return router


def command_of(pid: int) -> list[str]:
    """A process's command line, as words: /proc on Linux, ps where there is no /proc (macOS)."""

    if Path("/proc/self").exists():
        return [word.decode("utf-8", "replace") for word in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if word]
    done = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    return done.stdout.split()


def about(home: Path, code: dict, state: dict, loras: list, active: int, memory: dict, models: Path, renders: dict | None = None) -> dict:
    """What an app says of itself on /api/spark/self. ``renders``: the presets and qualities it renders."""

    try:
        disk = round(shutil.disk_usage(home).free / 1e9, 1)
    except OSError:
        disk = None
    return {"name": socket.gethostname(), "version": state["version"], "platform": state["platform"], "engine": state["engine"],
            "guard": bool(memory), "code": code, "active": active, "memory": memory, "disk_gb": disk,
            "fasth3": any((models / "FastH3-Comfy/diffusion_models").glob("*fasth3*")),
            "image": any((models / "Qwen-Image-2.1-Comfy/diffusion_models").glob("qwen_image_2.1*.safetensors")),
            "ref2va": state["ref2va"], "ref2va_turbo": state["ref2va_turbo"], "ffmpeg": state["ffmpeg"],
            "loras": loras, "text_encoders": state["text_encoders"], **(renders or {})}
