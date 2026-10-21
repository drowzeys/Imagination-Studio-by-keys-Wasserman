#!/usr/bin/env python3
"""TensorFold Studio web app: a create wizard, a production board and a clip editor over the pack's scripts.

    bash scripts/app.sh            # http://127.0.0.1:7870
    HOST=0.0.0.0 bash scripts/app.sh   # reachable from other machines on the network (behind the access key, app/access.py: trusted LANs only)

One job runs at a time, in the order queued; every job is one of this pack's scripts with environment variables.
Projects, uploads and renders live under $STUDIO_HOME (default ~/ImaginationStudio; app/studio_home.py).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
PACK = HERE.parent
from studio_home import studio_home  # noqa: E402  (~/ImaginationStudio, an older ~/TensorFoldStudio moved there once)
HOME = studio_home(move=__name__ == "__main__")  # moved only when the studio starts, never by a script that imports this
os.environ["STUDIO_HOME"] = str(HOME)  # the scripts and engines it starts keep everything in the same folder
for name in ("projects", "uploads", "renders", "scouts", "exports"):
    (HOME / name).mkdir(parents=True, exist_ok=True)
import access  # noqa: E402  (the studio's access key, beside this file)
ACCESS_KEY = access.ensure(HOME)  # <studio folder>/access-key, 0600, made once and kept
APP_PORT = os.environ.get("PORT", "7870")

import lengths  # noqa: E402  (which frame counts the model takes, beside this file)
sys.path.insert(0, str(PACK / "spark"))
import memory_guard  # noqa: E402  (a DGX Spark's memory: what a clip needs and whether this machine has it, in spark/)
import settings  # noqa: E402  (Settings: looks, layout, presets, settings.json, beside this file)

FPS = 24
# seconds -> frames (17n + 5): the four lengths the app has always offered. A job may ask for any count the model takes
# (lengths.py): by "frames", or by "seconds", which is brought to the nearest count.
DURATIONS = lengths.CLASSIC
# longer videos are chains of clips: each one opens on the last frame of the one before, and they are joined at the end
LONG = {30: "30 s", 60: "1 min", 120: "2 min", 300: "5 min", 600: "10 min", 900: "15 min", 1800: "30 min"}
LONGEST = max(DURATIONS)
# name -> (label, final size, environment). Generation happens at half size where X2 is set.
PRESETS = {
    "draft": ("Draft 1344x768 (generated at 672x384, 2x decoder)", (1344, 768), {"X2": "1"}),
    "2k": ("2K 2048x1152 (generated at 1024x576, 2x decoder)", (2048, 1152), {"TWOK": "1"}),
    "qhd": ("2K 2560x1440 (generated at 1280x736, 2x decoder)", (2560, 1440), {"QHD": "1"}),
    "small": ("Small 864x480 (native)", (864, 480), {"WIDTH": "864", "HEIGHT": "480"}),
    "native": ("Native 1344x768 (no 2x decoder)", (1344, 768), {"WIDTH": "1344", "HEIGHT": "768"}),
    "p720": ("720p 1280x720 (native)", (1280, 720), {"WIDTH": "1280", "HEIGHT": "736", "CROP": "1280x720"}),
    "p960": ("960p 1728x960 (generated at 480p, 2x decoder)", (1728, 960), {"X2": "1", "WIDTH": "1728", "HEIGHT": "960"}),
    "sharp": ("Wasserman Sharp Spec 1376x768 (native, one pass, no upscale)", (1376, 768), {"WIDTH": "1376", "HEIGHT": "768"}),
}
# Sam Wasserman's locked recipe for a sharp clip: this size with FastH3 at 8 passes (sparse attention 0.8), seed 42 and
# a retake at the next seed, 8 or 10 seconds, one generation pass with no upscale and no crop.
SHARP = {"preset": "sharp", "quality": "fh8", "seed": 42, "seconds": [8, 10]}
QA_TAKES = 2  # his QA gate (qa.py) follows each such clip: one that fails is rendered again at the next seed
QUALITIES = {
    "standard": ("Standard: Turbo 5 passes + base-model sound", {}),
    "high": ("High: 20 steps, no adapter, fast recipe (about 1.5x slower)", {"QUALITY": "high"}),
    "full": ("Full: plain 20 steps, no adapter (about 3x slower)", {"QUALITY": "full"}),
    "fast": ("Fast: Turbo 3 passes, adapter sound", {"POINTS": "4", "REVOICE": "0"}),
    "fh8": ("FastH3: 8 passes, sparse attention", {"ENGINE": "fasth3", "STEPS": "8"}),
    "fh4": ("FastH3: 4 passes, fastest and softer", {"ENGINE": "fasth3", "STEPS": "4"}),
    "fh20": ("FastH3: 20 passes, for prompts with several actions", {"ENGINE": "fasth3", "STEPS": "20"}),
}
API_QUALITY = "fh4"  # a clip asked for over the API without a quality: FastH3 at 4 passes, the page's default (settings.js recipeParams)
SCOUT_SIZES = {"draft": (1344, 768), "2k": (1024, 576), "qhd": (1280, 736), "small": (864, 480),
               "native": (1344, 768), "p720": (1280, 736), "p960": (864, 480), "sharp": (1376, 768)}
# measured on a Mac Studio M5 Ultra, 8 second clips, standard quality, image step included (README)
MEASURED_8S = {"draft": 108, "2k": 299, "qhd": 635, "small": 174, "native": 709}
# FastH3 on the native engine (TensorFold 1.0's Zig + Metal runtime), 5 second text-to-video clips, same machine.
ZIG_ENGINE = Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio")) / "zig-engine"
# Measured through this app: 480p at 4, 8 and 20 passes (43, 66, 129 s) and 720p at 8 passes (199 to 217 s). The
# other cells are those plus the measured pass time (5.3 s at 480p, 20 s at 720p) and about 10 s for the 2x decoder.
ZIG_FASTH3_5S = {"fh4": {"small": 43, "p960": 53, "p720": 125, "qhd": 137, "draft": 31, "2k": 60, "native": 104},
                 "fh8": {"small": 66, "p960": 76, "p720": 205, "qhd": 217, "draft": 44, "2k": 93, "native": 175},
                 "fh20": {"small": 129, "p960": 139, "p720": 445, "qhd": 457, "draft": 83, "2k": 192, "native": 388}}
# Draft, 2K and Native at 8 passes are measured through this app too (44, 93 and 175 s; a pass is 3.2, 8.2 and 17.8 s,
# from which the 4 and 20 pass cells follow).
# How the time grows with length: a 10 second 720p clip took 388 s against about 205 s for 5 seconds, a 15 second 480p
# clip 231 s against 66 s (a pass is 22 s at 44.6K rows against 5.3 s at 15.4K).
ZIG_LENGTH_POWER = {"small": 1.15, "p960": 1.15, "draft": 1.15, "2k": 1.15, "p720": 0.95, "qhd": 0.95, "native": 0.95}
# A clip that starts from an image carries the image's rows and its vision tokens through every pass.
ZIG_FROM_IMAGE = 1.13  # measured through this app: 72 s from an image against 64 s from text, 480p, 8 passes
# FastH3 on the MLX engine (machines without the native engine):
# 5 second text-to-video clips, measured on the same machine: quality -> preset -> seconds
FASTH3_5S = {"fh4": {"small": 51, "p960": 61, "p720": 112, "qhd": 123},
             "fh8": {"small": 78, "p960": 88, "p720": 192, "qhd": 203},
             "fh20": {"small": 174, "p960": 184, "p720": 434, "qhd": 445}}
# Reference to video (MiniMax H3 Ref2VA, oneshot-setup.sh --ref2va): plain 20 passes with the pictures' rows carried
# through each, or LightX2V's 4-step Ref2VA adapter and the sound made again without it. Measured through
# scripts/video.sh on an M5 Ultra with one portrait reference, 5 seconds: preset -> seconds.
REF2VA_5S = {"small": 223}
REF2VA_TURBO_5S = {"small": 88}
# the same on a DGX Spark (oneshot-setup-spark.sh --ref2va), through ComfyUI's own blocks
SPARK_REF2VA = Path(os.environ.get("SPARK_MODELS", Path.home() / "spark-studio-models")) / "MiniMax-H3-Comfy"
SPARK_REF2VA_5S = {"small": 321}
SPARK_REF2VA_TURBO_5S = {"small": 107}
REF2VA_ADAPTER = "lightx2v_ref2v_4step_v0.1_ourlayout.safetensors"
REFERENCE_LIMIT = 9
# MiniMax's limits for the other two kinds of reference: 3 videos and 3 audio clips, each 2 to 15 seconds and 15
# seconds in all per kind, and 12 files of all kinds in one request (the Mac engine and, on a Spark, ComfyUI's node)
REFERENCE_VIDEO_LIMIT, REFERENCE_AUDIO_LIMIT, REFERENCE_TOTAL = 3, 3, 12
REFERENCE_CLIP_SECONDS, REFERENCE_KIND_SECONDS = (2.0, 15.0), 15.0
VIDEO_SUFFIXES, AUDIO_SUFFIXES = (".mp4", ".mov"), (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")
# a size typed in by hand: multiples of 32, and no more pixels than the largest size the model is run at here
CUSTOM_SIDES, CUSTOM_PIXELS, CUSTOM_STEPS = (256, 1536), 1344 * 768, (1, 50)
LEAD = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."


def lead(first: bool, last: bool, seconds: float = 5, frames: int | None = None) -> str:
    """The line that opens a keyframe prompt: which picture stands at which moment of the clip. MiniMax's own form
    for a first frame is LEAD; an end frame is written the same way at the clip's last moment (Picture 2 beside a
    first frame, Picture 1 alone), which is the clip's frame count over 24. The pictures themselves are held as
    keyframes by the model whatever this says."""

    end = f"{(frames or lengths.nearest(float(seconds))) / FPS:.2f}"
    if first and last:
        return LEAD[:-1] + f"; at {end} seconds into the target video, <Picture 2> (from [Shot 1]) is fully referenced."
    if last:
        return f"For the target video, at {end} seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
    return LEAD if first else ""

# On a DGX Spark (Linux, CUDA) the scripts hand over to spark/: FastH3 and Qwen-Image-2.1 through ComfyUI. There is no
# 2x decoder and no MiniMax H3 Turbo there, so only the native sizes and the FastH3 qualities are offered.
SPARK = sys.platform.startswith("linux")
if SPARK:
    PRESETS = {k: v for k, v in PRESETS.items() if "X2" not in v[2] and k not in ("2k", "qhd")}
    QUALITIES = {k: v for k, v in QUALITIES.items() if k.startswith("fh")}
# FastH3 on a DGX Spark (GB10), 5 second text-to-video clips with the models already loaded, measured through the
# scripts on TensorFold's CUDA engine with the 8-bit video decoder at 8 passes: 864x480 in 73 s (a pass 7.1 s, the
# decode 14 s), 1280x736 in 183 s (18 s, 28 s) and 1344x768 in about 195 s (20.5 s, 29 s). The 4 and 20 pass cells
# follow from the pass time.
SPARK_ENGINE_LIB = Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio")) / "tf-h3" / "libtf_h3.so"
SPARK_FASTH3_5S = {"fh4": {"small": 45, "p720": 111, "native": 113},
                   "fh8": {"small": 73, "p720": 183, "native": 195, "sharp": 202},  # sharp: Sam Wasserman's 330 to 380 s at 8 s
                   "fh20": {"small": 158, "p720": 399, "native": 441}}
# On ComfyUI's own blocks in bf16 (no engine built): 864x480 at 8 passes 118 s (a pass 10.5 s) and 1344x768 289 s
# (a pass 29.4 s with its sparse attention); 720p is scaled from 1344x768 by its rows.
SPARK_COMFY_5S = {"fh4": {"small": 76, "p720": 155, "native": 172},
                  "fh8": {"small": 118, "p720": 260, "native": 289},
                  "fh20": {"small": 244, "p720": 575, "native": 642}}


GPU_NAME: list[str] = []  # asked of the driver once: see machine()


def machine() -> tuple[str, str]:
    """(platform, label) for the header: which build this is and what it runs on.

    The GPU's name is asked of nvidia-smi once and kept. /api/state used to run nvidia-smi twice on every call, with
    no time limit, in the request itself: 0.35 to 0.42 s each time a job started on a Spark (measured), and for as
    long as the driver takes when it is busy handing out memory, which is when a worker's API was seen to stop
    answering. A page that polls the state then holds one of the server's threads per call."""

    if SPARK:
        if not GPU_NAME:
            try:
                GPU_NAME.append(subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True,
                                               text=True, timeout=5).stdout.strip().splitlines()[0])
            except (OSError, IndexError, subprocess.TimeoutExpired):
                return "spark", "DGX SPARK · CUDA · H3-CUDA"  # not kept: asked again on a later call
        # the same build on a CUDA workstation (an RTX Pro 6000): named by its card, not as a DGX Spark
        return "spark", (f"DGX SPARK · {GPU_NAME[0].upper()} · H3-CUDA" if "GB10" in GPU_NAME[0].upper()
                         else f"CUDA · {GPU_NAME[0].upper()} · H3-CUDA")
    # no chip name: the same recipe runs from an M1 with 64 GB up
    return "mac", "MAC · APPLE SILICON MX · H3-METAL"


app = FastAPI(title="Imagination Studio by keys+Wasserman, powered by TensorFold, built with Fish Audio Drama 3")
jobs: dict[str, dict] = {}
order: list[str] = []
lock = threading.Lock()
wake = threading.Event()
running: dict = {"process": None, "id": None}
update_writes = 0


@app.middleware("http")
async def update_maintenance(request, call_next):
    global update_writes
    writes = request.method not in ("GET", "HEAD", "OPTIONS") and not request.url.path.startswith("/api/updates")
    with lock:
        if writes and updater.maintenance.is_set():
            return JSONResponse({"error": "Studio is restarting for an update. This page will reconnect shortly."}, status_code=503)
        if writes:
            update_writes += 1
    try:
        return await call_next(request)
    finally:
        if writes:
            with lock:
                update_writes -= 1


# ---------- requests that change something: from the Studio's own page, and not too large ----------

WRITES = ("POST", "PUT", "PATCH", "DELETE")
JSON_LIMIT = int(float(os.environ.get("STUDIO_JSON_MB", "1")) * (1 << 20))       # any body but a file upload
UPLOAD_LIMIT = int(float(os.environ.get("STUDIO_UPLOAD_MB", "512")) * (1 << 20))  # one file sent to /api/upload
# a production pack or a suite file brought in (a .zip of clips): the suite's own cap per file was 2 GB already
ARCHIVE_LIMIT = int(float(os.environ.get("STUDIO_ARCHIVE_MB", "2048")) * (1 << 20))
QUEUE_MAX = int(os.environ.get("STUDIO_QUEUE_MAX", "200"))  # jobs waiting or running at once
# the routes that take a file (multipart), each with its own limit; every other write is held to JSON_LIMIT, whatever its type
UPLOAD_ROUTES = {"/api/upload": "UPLOAD_LIMIT", "/api/motion/packs/import": "ARCHIVE_LIMIT", "/api/suite/import": "ARCHIVE_LIMIT"}
TAILSCALE_APP = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
# this machine's own names (learn_own_names), learned once in the background: None until then
OWN: dict = {"labels": None, "tailnet": None}


def learn_own_names() -> dict:
    """This computer's names, for trusted_host (OWN): its host name, its Bonjour name (macOS) and its Tailscale name. Each
    tool is asked once, with a short time limit; one that is missing or does not answer is left out."""

    labels = {socket.gethostname().split(".")[0].lower()}
    tailnet = None
    asks = [["scutil", "--get", "LocalHostName"]] if sys.platform == "darwin" else []
    tailscale = shutil.which("tailscale") or (TAILSCALE_APP if os.access(TAILSCALE_APP, os.X_OK) else None)
    for ask in asks:
        try:
            said = subprocess.run(ask, capture_output=True, text=True, timeout=2).stdout.strip().lower()
            if said:
                labels.add(said)
        except (OSError, subprocess.SubprocessError):
            pass
    if tailscale:
        try:
            said = subprocess.run([tailscale, "status", "--json", "--peers=false"], capture_output=True, text=True, timeout=3)
            me = (json.loads(said.stdout or "{}").get("Self") or {}) if said.returncode == 0 else {}
            dns = str(me.get("DNSName") or "").rstrip(".").lower()
            if dns.endswith(".ts.net") and dns.count(".") >= 3:
                labels.add(dns.split(".")[0])
                tailnet = dns.split(".", 1)[1]  # e.g. tail1234.ts.net: the names of this tailnet
            if me.get("HostName"):
                labels.add(str(me["HostName"]).lower())
        except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
            pass
    return {"labels": {one for one in labels if one}, "tailnet": tailnet}


threading.Thread(target=lambda into=OWN: into.update(learn_own_names()), daemon=True, name="own-names").start()


def trusted_host(host: str) -> bool:
    """Whether a request's Host is one a page from elsewhere cannot point at this app (DNS rebinding): localhost, an
    address written as numbers (the app is opened to a LAN with HOST=0.0.0.0, so a LAN or tailnet address stays
    allowed), this computer's own names (one word, or .local), a Tailscale name, or one named in STUDIO_ALLOWED_HOSTS
    (comma-separated). A public name that could resolve to this machine is refused.

    A .local name must be this computer's own once its names are learned (learn_own_names, a few seconds after start):
    anyone on the LAN can announce one. A one-word name must be too, where Tailscale answered with this computer's
    name on the tailnet (its MagicDNS short name); without Tailscale to ask, a one-word name is taken as before. Any
    *.ts.net name is taken, as by the agents' and the updater's own checks: those names are given out by Tailscale
    and resolve to tailnet addresses only, so a page cannot point one at this machine."""

    try:
        name = (urlsplit("//" + host).hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    if not name:
        return False
    if name in ("localhost", "testserver") or name.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    if name in {one.strip().lower() for one in os.environ.get("STUDIO_ALLOWED_HOSTS", "").split(",") if one.strip()}:
        return True
    labels, tailnet = OWN["labels"], OWN["tailnet"]
    if name.endswith(".local"):
        return labels is None or name[:-len(".local")] in labels
    if "." not in name:
        return labels is None or tailnet is None or name in labels
    return name.endswith(".ts.net")


def refused(request_headers: dict) -> str:
    """Why a request that changes something is refused ("" when it is not): sent by a page of another site."""

    host = request_headers.get("host", "")
    origin = request_headers.get("origin")
    if not trusted_host(host):
        return "Open the Studio by this computer's name, its address or its Tailscale name (STUDIO_ALLOWED_HOSTS adds another)."
    try:
        elsewhere = origin is not None and urlsplit(origin).netloc.lower() != host.lower()
    except ValueError:  # an Origin that is not a URL
        elsewhere = True
    if request_headers.get("sec-fetch-site") == "cross-site" or elsewhere:
        return "Do this from the Studio page."
    return ""


class TooLarge(Exception):
    pass


class WriteGuard:
    """Every request, reads included: refused (403) when its Host is not one of this computer's (trusted_host: a page
    that rebinds its own name to this machine could otherwise read the Library, the jobs' prompts and the settings).
    Every POST, PUT, PATCH and DELETE under /api/ besides: refused (403) when a page of another site sent it, and
    refused (413) when its body is over the limit, whatever its Content-Type: the routes that take a file have their
    own (UPLOAD_ROUTES), every other is held to JSON_LIMIT. A request without an Origin (curl, scripts, the workers' own
    calls) passes the Origin test; /api/updates keeps its own, stricter one."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope["headers"]}
        if not trusted_host(headers.get("host", "")):
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return await JSONResponse({"error": "Open the Studio by this computer's name, its address or its Tailscale name (STUDIO_ALLOWED_HOSTS adds another)."},
                                      status_code=403)(scope, receive, send)
        if access.enabled():
            if access.needs_key(scope["path"]) and not access.allowed(headers, APP_PORT, ACCESS_KEY):
                if scope["type"] == "websocket":
                    return await send({"type": "websocket.close", "code": 4401})
                return await JSONResponse({"error": "This studio asks for its access key: open the address scripts/studio-key.sh "
                                                    "prints, or type the key on the page.", "access": "key"},
                                          status_code=401)(scope, receive, send)
            if scope["type"] == "http" and scope["method"] == "GET" and b"key=" in scope.get("query_string", b""):
                return await self.take_key(scope, receive, send, headers)
        if scope["type"] != "http" or scope["method"] not in WRITES or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        why = refused(headers)
        if why:
            return await JSONResponse({"error": why}, status_code=403)(scope, receive, send)
        limit = globals()[UPLOAD_ROUTES.get(scope["path"].rstrip("/"), "JSON_LIMIT")]  # read here: a test may change one
        too_large = JSONResponse({"error": f"this is over the {limit / (1 << 20):g} MB a request may carry here"}, status_code=413)
        try:
            if int(headers.get("content-length") or 0) > limit:
                return await too_large(scope, receive, send)
        except ValueError:
            return await JSONResponse({"error": "Content-Length is a number"}, status_code=400)(scope, receive, send)
        seen, over, started = 0, False, False

        async def counted():  # a body sent without a length (chunked) is counted as it comes
            nonlocal seen, over
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    over = True
                    raise TooLarge()
            return message

        async def guarded(message):
            nonlocal started
            if over:
                return  # whatever the route made of a cut-off body, the answer is 413 (below)
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted, guarded)
        except TooLarge:
            pass
        if over and not started:
            await too_large(scope, receive, send)


    async def take_key(self, scope, receive, send, headers):
        """``/?key=<key>`` (the launcher's first page): the key kept as this app's cookie and the page opened again
        without it in the address bar. A wrong key is ignored: the page opens and asks for it."""

        query = [(name, value) for name, value in parse_qsl(scope["query_string"].decode("latin-1"), keep_blank_values=True)]
        offered = next((value for name, value in query if name == "key"), "")
        if not access.matches(offered, ACCESS_KEY):
            return await self.app(scope, receive, send)
        rest = urlencode([(name, value) for name, value in query if name != "key"])
        answer = Response(status_code=303, headers={"Location": scope["path"] + ("?" + rest if rest else ""), "Cache-Control": "no-store"})
        answer.headers.append("Set-Cookie", access.cookie(APP_PORT, ACCESS_KEY, secure_request(scope, headers)))
        return await answer(scope, receive, send)


def secure_request(scope, headers) -> bool:
    """Whether the page was opened over HTTPS (directly, or through `tailscale serve`): its cookie is then Secure."""

    return scope.get("scheme") == "https" or headers.get("x-forwarded-proto", "").lower() == "https"


app.add_middleware(WriteGuard)


@app.post("/api/key")
async def take_typed_key(request: Request):
    """The key typed on the page's prompt (a phone, a browser the launcher did not open): kept as this app's cookie.
    A wrong key waits a second before it is refused."""

    try:
        offered = str((await request.json()).get("key") or "").strip()
    except (ValueError, AttributeError):
        offered = ""
    if not access.matches(offered, ACCESS_KEY):
        await asyncio.sleep(1)
        return JSONResponse({"error": "That is not this studio's access key.", "access": "key"}, status_code=401)
    answer = JSONResponse({"ok": True})
    answer.headers.append("Set-Cookie", access.cookie(APP_PORT, ACCESS_KEY, secure_request(request.scope, {k.lower(): v for k, v in request.headers.items()})))
    return answer


class SecurityHeaders:
    """Headers on every answer: this is a local app, so it needs no remote code, no framing and no sniffing."""

    CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
           "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; font-src 'self' data:; "
           "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def headed(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                for key, value in ((b"content-security-policy", self.CSP.encode()),
                                   (b"x-content-type-options", b"nosniff"),
                                   (b"x-frame-options", b"DENY"),
                                   (b"referrer-policy", b"no-referrer"),
                                   (b"permissions-policy", b"camera=(), microphone=(), geolocation=()")):
                    headers.append([key, value])
            await send(message)

        await self.app(scope, receive, headed)


app.add_middleware(SecurityHeaders)


def accepting_jobs():
    """Called under the queue lock, shared with the updater's idle check."""
    if updater.maintenance.is_set():
        raise HTTPException(503, "Studio is restarting for an update. Try again after it reconnects.")


def inside(path: str | Path) -> Path:
    """A path under the studio folder, or 400: never one with a null byte, and never in or of a hidden folder or file
    (a part that starts with "."): .studio-agents holds the agents' tools and threads, .studio-updates the updater's."""

    if "\0" in str(path):
        raise HTTPException(400, "a path has no null byte")
    try:
        resolved = (HOME / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    except (OSError, ValueError, RuntimeError):
        raise HTTPException(400, "not a path")
    home = HOME.resolve()
    if home not in (resolved, *resolved.parents):
        raise HTTPException(400, "path is outside the studio folder")
    if hidden(resolved.relative_to(home)):
        raise HTTPException(400, "hidden files of the studio folder are not served or used")
    return resolved


def hidden(relative_path: Path | str) -> bool:
    """Whether a path under the studio folder is in or of a hidden folder or file."""

    return any(part.startswith(".") for part in Path(relative_path).parts)


def relative(path: Path) -> str:
    return str(Path(path).resolve().relative_to(HOME.resolve()))


# What a Motion Previs layer shows, for the model: a control layer is a diagram of the shot, not footage of it.
MOTION_LEGENDS = {
    "openpose": "an OpenPose skeleton on a black background, where the coloured lines are the joints and limbs of the subject's body and the black area is everything else",
    "pose": "a pose skeleton on a black background, where the coloured lines are the joints and limbs of the subject's body and the black area is everything else",
    "ai-depth": "a depth map in shades of grey, where brighter is nearer to the camera and darker is farther away",
    "depth": "a depth map in shades of grey, where brighter is nearer to the camera and darker is farther away",
    "edges": "an edge map, where the bright lines on black are the outlines of the people and objects",
    "lineart": "a line drawing, where the lines are the outlines of the people and objects",
    "masks": "a motion mask, where the bright areas are what moves and the black area is what stays still",
    "normals": "a surface-direction map, where each colour stands for the way a surface faces",
}
MOTION_MODES = ("camera-only", "actor-motion", "object-motion", "full-scene")
# what each mode keeps of a control layer, and of the source clip itself
MOTION_KEEPS = {"camera-only": "the camera path and the scale and layout it gives the scene",
                "actor-motion": "the positions, silhouettes and body movement of its figure and the camera path",
                "object-motion": "the position and motion path of its main object and the camera path",
                "full-scene": "the layout, silhouettes, positions, movement and camera path"}
MOTION_SOURCE = {
    "camera-only": ("the camera reference for the target video: only its camera movement and timing are kept",
                    "the camera movement and timing of <Video 1> are followed; its people, objects and setting are not rendered.",
                    "The camera moves as the camera of <Video 1> does."),
    "actor-motion": ("the motion reference for the target video: the body motion of its person and its camera movement are kept, and who it is and where it is are replaced",
                     "the position, stance, body motion and timing of its person and the camera movement of <Video 1> are followed frame by frame; its person and its setting are not rendered.",
                     "{who} moves as the person in <Video 1> does, move for move, and the camera follows the camera of <Video 1>."),
    "object-motion": ("the motion reference for the target video: the path of its main object and its camera movement are kept, and the object's design and the setting are replaced",
                      "the motion path and timing of the main object and the camera movement of <Video 1> are followed frame by frame; its design, surface and setting are not rendered.",
                      "The main object moves along the path of the object in <Video 1>, and the camera follows the camera of <Video 1>."),
    "full-scene": ("the source video: its setting, its camera movement and the motion in it are kept",
                   "the setting, light, camera path and the position, motion and timing of what moves in <Video 1> are followed frame by frame.",
                   "The scene, the motion and the camera follow <Video 1>."),
}


# the same sentences for a clip with more than one person in it: longest phrases first
MOTION_PLURAL = (
    ("the body motion of its person", "the body motion of its people"), ("its person is replaced by", "its people are replaced by"),
    ("with its person replaced by", "with its people replaced by"), ("the person shown in", "the people shown in"),
    ("the person of <Video 1> is not rendered", "the people of <Video 1> are not rendered"),
    ("its person and its setting are not rendered", "its people and its setting are not rendered"),
    ("<Subject 1> takes the place of the person in <Video 1> and moves as that person does",
     "<Subject 1> take the places of the people in <Video 1>, one for one, and move as those people do"),
    ("moves as the person in <Video 1> does", "move as the people in <Video 1> do"),
    ("timing of its person", "timing of its people"), ("of its figure", "of its figures"),
    ("are those of the figure in <Video 1>", "are those of the figures in <Video 1>, one for one"),
)


def motion_template(motion: dict, pictures: int) -> dict:
    """``motion_template_one`` for the number of people the user says are in ``<Video 1>`` (``motion["people"]``,
    1 when not given): with two or more the same sentences speak of its people and figures, one for one. The count is
    the user's: the pose pass follows one person even when asked for more (two fighters at arm's length came back
    as one in every frame)."""

    made = motion_template_one(motion, pictures)
    try:
        people = int(motion.get("people") or 1)
    except (TypeError, ValueError):
        people = 1
    if people < 2:
        return made

    def plural(text: str) -> str:
        for one, many in MOTION_PLURAL:
            text = text.replace(one, many)
        return text

    return {key: [plural(line) for line in value] if isinstance(value, list) else plural(value) for key, value in made.items()}


def motion_template_one(motion: dict, pictures: int) -> dict:
    """What a motion reference adds to the Ref2VA prompt: how ``<Video 1>`` is declared, what is kept of it, and what
    the clip must not look like. ``motion`` is ``{"layer", "mode"}``: the layer of a Motion Previs pack that goes in
    as ``<Video 1>`` ("reference" is the source clip itself) and what to carry over.

    The source clip is declared as footage (the form tested on both builds). A control layer (skeleton, depth, edges)
    is declared in the control-video form: ``<Video 1>`` the structural template, ``<Picture 1>`` the first-frame
    anchor, a legend of the layer, and its colours named as labels, not as the look of the clip.
    """

    layer = motion.get("layer") or "reference"
    mode = motion.get("mode") if motion.get("mode") in MOTION_MODES else "full-scene"
    who = "<Subject 1>" if pictures else "The subject"
    if layer not in MOTION_LEGENDS:  # the source clip
        role, keep, follow = MOTION_SOURCE[mode]
        if mode == "full-scene" and pictures:
            # the form that held best in the tests: the clip is <Video 1> with its person replaced. Calling the picture
            # a first-frame anchor here made the picture's layout override the video.
            lead = "The target video is <Video 1> with its person replaced by <Subject 1>, the person shown in <Picture 1>."
            role += ", and its person is replaced by <Subject 1>. Only the appearance of <Subject 1> comes from <Picture 1>"
            keep = keep[:-1] + "; the person of <Video 1> is not rendered."
            follow = "<Subject 1> takes the place of the person in <Video 1> and moves as that person does, move for move; the setting and the camera are those of <Video 1>."
        return {"video_roles": [role], "video_keeps": [keep], "video_follows": [follow.format(who=who)],
                **({"lead": lead} if mode == "full-scene" and pictures else {})}
    kept = MOTION_KEEPS[mode]
    definitions = []
    if pictures:
        definitions.append("<Picture 1> is the first-frame anchor: the opening frame of the target video, with the subject, "
                           "setting and light of the target. The appearance of <Subject 1> comes from <Picture 1>"
                           + ("." if mode == "camera-only" else ", the position and motion from <Video 1>."))
    definitions.append(f"Legend of <Video 1>: {MOTION_LEGENDS[layer]}. These colours are labels only, not the look of the target video.")
    follow = ("The camera follows the path of <Video 1> frame by frame." if mode == "camera-only"
              else "The main object is where the main object of <Video 1> is in every frame and moves along the same path, and the camera "
                   "follows the path of <Video 1>." if mode == "object-motion" and not pictures
              else f"The position, scale and movement of {who.replace('The', 'the')} in every frame are those of the "
                   f"{'main object' if mode == 'object-motion' else 'figure'} in <Video 1>, and the camera follows the path of <Video 1>.")
    look = ", continuing the look of <Picture 1>" if pictures else ""
    return {"lead": "A photorealistic video that follows the structure of <Video 1> frame by frame"
                    + (" and has the look of <Picture 1>, with <Subject 1>." if pictures else "."),
            "video_roles": [f"the structural template of the target video: it supplies {kept} frame by frame"],
            "video_keeps": [f"fully_preserved structure - {kept} of <Video 1> are followed frame by frame; its look is not rendered."],
            "video_follows": [follow], "definitions": definitions,
            "avoid": f"The picture is photorealistic throughout{look}: no skeleton lines, no flat-coloured, depth-map or segmentation-map look."}


def freed(asked, motion: dict | None, pictures: int) -> bool:
    """Whether a clip leaves its reference video out: the user chose Impacts, said so (``motion["free"]``), there is a
    picture to carry who is in it, and the reference was measured and is not fast. The model follows a reference
    video's movement whatever the prompt says about speed (measured: a slow sticky-hands duel stayed at 15 to 19
    percent of the frame width a second under four wordings, the camera-only one among them, and reached 42 with the
    video left out), so hits over a slow reference are only true when the reference is not sent."""

    measured = (motion or {}).get("measured")
    return bool(motion and motion.get("free") and pictures > 0 and isinstance(measured, dict)
                and measured.get("motion") in ("slow", "moderate") and pace.choose(asked or motion.get("pace"), measured) == "impacts")


def paced_fields(fields: dict, motion: dict | None, has_video: bool, pictures: int) -> dict:
    """The sound and the motion of a reference clip made to agree (pace.py). ``pace`` is the user's choice, in
    ``fields["pace"]`` or ``motion["pace"]``: "impacts" (fast strikes and blocks, with their sound), "flow" (slow,
    continuous movement, silent or with music) or "auto". Automatic reads ``motion["measured"]`` (what the analysis
    measured of the reference: ``pace.measure``) and changes one case only: a soundscape that names hits over a
    reference that moves slowly becomes Flow. Everything else is left as it was written.

    Flow takes the hits out of the soundscape, says that the movement is slow and that no strike lands, and does not
    reuse a video's own sound when that sound holds hits. Impacts says that the arms attack and block at full speed
    and that each strike is heard as it lands. Returns ``{"fields", "pace"}``.
    """

    measured = (motion or {}).get("measured") if isinstance((motion or {}).get("measured"), dict) else None
    asked = fields.get("pace") or (motion or {}).get("pace")
    asked = asked if asked in pace.PACES else "auto"
    chosen = pace.choose(asked, measured)
    if asked == "auto" and not (chosen == "flow" and has_video and measured.get("motion") == "slow"
                                and pace.has_impacts(fields.get("soundscape") or "")):
        return {"fields": fields, "pace": ""}  # Automatic steps in only where hits were asked for over a slow reference
    if not (has_video or asked != "auto"):
        return {"fields": fields, "pace": ""}
    fields = dict(fields)
    try:
        many = int((motion or {}).get("people") or 1) > 1
    except (TypeError, ValueError):
        many = False
    action, soundscape = (fields.get("action") or "").strip(), (fields.get("soundscape") or "").strip()
    if chosen == "flow":
        fields["action"] = (action + " " + pace.SMOOTH).strip()
        # Without music the clip is asked for near silence in so many words: left at "cloth rustle", or told "No
        # music", the model laid music of its own over the shot (measured on the Mac, twice)
        quiet = pace.calm(soundscape)
        named = (fields.get("music") or "N/A").strip().upper() != "N/A"
        fields["soundscape"] = quiet if named or quiet == pace.QUIET else "Near silence: " + quiet[0].lower() + quiet[1:]
        if asked == "flow" and measured and measured.get("sound") == "impacts":
            fields["video_sound"] = False  # Flow by choice: the reference's own hits are not reused
    else:
        who = "<Subject 1>" if pictures else ("The two" if many else "The subject")
        fields["action"] = (action + " " + (pace.VIGOROUS if many else pace.VIGOROUS_ONE).format(who=who)).strip()
        if not fields.get("video_sound") and not pace.has_impacts(soundscape):
            fields["soundscape"] = ("Sharp slaps and knocks of hands and forearms, one on every strike that lands. " + soundscape).strip()
    return {"fields": fields, "pace": chosen}


# What a clip without spoken lines is told. The Studio used to write "Nobody speaks." in the shot and "No voices, no
# speech." in the soundscape; the video model has no negative prompt on any route (negation.py), so the same is now
# said by what is there: closed mouths where the shot has people, and what the sound track holds.
QUIET_PICTURE = "Mouths stay closed."
QUIET_SOUND = "Only ambient sound is heard."
QUIET_AFTER = "After the spoken lines only ambient sound is heard."
PEOPLE = re.compile(r"\b(man|men|woman|women|person|people|boy|boys|girl|girls|child|children|kid|kids|baby|he|she|they|him|her|his|them|their|"
                    r"someone|somebody|everyone|crowd|couple|friends?|students?|dancers?|singers?|actors?|actress|workers?|soldiers?|"
                    r"mother|father|son|daughter|brother|sister|husband|wife|face|faces|\w+ (?:is|are) \d+ years old)\b", re.IGNORECASE)


def quiet(fields: dict, *texts: str) -> str:
    """The sentence a shot without spoken lines ends on: QUIET_PICTURE where the shot has people (``people`` in the
    fields says so, else the words do), nothing for a shot without them."""

    people = fields.get("people")
    if people is None:
        people = bool(PEOPLE.search(" ".join(text or "" for text in texts)))
    return " " + QUIET_PICTURE if people else ""


def said_quiet(sound: str) -> bool:
    """Whether a soundscape already says that there is no speech, in the old words or the new."""

    low = sound.lower()
    return "no speech" in low or "only ambient sound" in low


STOPS = ".!?…:;,\"”’')"


def joined(*parts) -> str:
    """The wizard's parts as one shot: each but the last ends on a full stop where it has no stop of its own, so the
    scene and the action read as two sentences ("…from a window. The lamp turns…", not "…from a window The lamp…")."""

    parts = [part.strip() for part in parts if part and part.strip()]
    return " ".join([part if part[-1] in STOPS else part + "." for part in parts[:-1]] + parts[-1:])


def compose_references(fields: dict, count: int) -> str:
    """The Ref2VA model's own prompt form: who each picture is, what is kept of it, then the shot.

    ``fields`` may also describe reference videos (``video_subjects``: one entry per video, who or what it shows,
    empty for a video that only gives the motion) and audio clips (``audio_notes``: one per clip, what it is).
    With ``video_sound`` each video's own soundtrack is a reference too, and the model numbers those sounds first:
    ``<Audio 1>`` is then the sound of ``<Video 1>`` and the audio clips follow.

    A caller with its own template for a reference video (a control layer such as a depth or segmentation pass
    rather than footage, say) can replace what is written about it: ``video_roles`` (one per video) is the
    sentence after ``<Video k> is``, ``video_keeps`` (one per video) the sentence after ``<Video k>: reference -``,
    ``lead`` opens the summary in place of the usual sentence, ``definitions`` are further lines for the subject definitions (a legend of the control colours, or what a
    picture is in relation to a video), and ``avoid`` is closing text for the shot (what the clip must not look
    like), and ``video_follows`` (one per video) the sentence the shot ends on. The create wizard sets them through
    ``motion`` (``{"layer", "mode", "camera"}``, a Motion Previs hand-off for ``<Video 1>``: see ``motion_template``),
    whose ``camera`` sentence stands in for an empty Camera field.
    """

    motion = fields.get("motion") if isinstance(fields.get("motion"), dict) else None
    free = len(fields.get("video_subjects") or []) <= 1 and freed(fields.get("pace"), motion, int(count))
    if free:  # the fight is the prompt's: no video goes in, and its camera move stays as words
        fields = {**fields, "video_subjects": [], "video_sound": False}
    if motion and (fields.get("video_subjects") or motion.get("layer") in (None, "", "none") or free):
        pictures = max(0, min(int(count), REFERENCE_LIMIT)) if fields.get("video_subjects") else 0
        made = motion_template(motion, pictures) if fields.get("video_subjects") and motion.get("layer") not in (None, "", "none") else {}
        fields = {**fields, **{key: value for key, value in made.items() if not fields.get(key)}}
        if not (fields.get("camera") or "").strip() and (motion.get("camera") or "").strip():
            fields["camera"] = motion["camera"].strip()

    videos = [(text or "").strip() for text in (fields.get("video_subjects") or [])][:REFERENCE_VIDEO_LIMIT]
    audios = [(text or "").strip() for text in (fields.get("audio_notes") or [])][:REFERENCE_AUDIO_LIMIT]
    count = max(0 if videos else 1, min(int(count), REFERENCE_LIMIT))
    paced = paced_fields(fields, motion, bool(videos) or free, max(0, min(int(count), REFERENCE_LIMIT)))
    fields, pacing = paced["fields"], paced["pace"]
    names = [(text or "").strip() for text in (fields.get("subjects") or [])][:count]
    names += [""] * (count - len(names))
    subjects = [f"<Subject {i}> is {name or 'the main subject'} shown in <Picture {i}>." for i, name in enumerate(names, start=1)]
    kept = [f"<Subject {i}> (appears in [Shot 1]): fully_preserved - the identity, face, hair, clothing and colours "
            f"shown in <Picture {i}> are kept." for i in range(1, count + 1)]
    people, sounds, follow, tracks = count, 0, [], []
    # how the speaker sounds, in the user's words: written beside an audio reference too, because the model follows a
    # voice reference when the prompt describes the same voice and may ignore it when the prompt is silent about it
    described = next(((line.get("voice") or "").strip() for line in fields.get("dialogue") or []
                      if (line.get("text") or "").strip() and (line.get("voice") or "").strip()), "")
    roles = [(text or "").strip() for text in (fields.get("video_roles") or [])]
    keeps = [(text or "").strip() for text in (fields.get("video_keeps") or [])]
    follows = [(text or "").strip() for text in (fields.get("video_follows") or [])]
    for k, shown in enumerate(videos, start=1):
        role, keep = (roles[k - 1] if k <= len(roles) else ""), (keeps[k - 1] if k <= len(keeps) else "")
        if role:  # the caller's own account of this video
            subjects.append(f"<Video {k}> is {role.rstrip('.')}.")
            kept.append(f"<Video {k}>: " + ("" if keep.startswith("fully_preserved") else "reference - ")
                        + (keep or f"the structure and movement of <Video {k}> are followed."))
            follow.append((follows[k - 1] if k <= len(follows) else "") or f"The layout, movement and camera path follow <Video {k}>.")
        elif shown or not people:  # a video that shows someone: a subject of its own, as the model card writes it
            people += 1
            subjects.append(f"<Subject {people}> is {shown or 'the main subject'} in <Video {k}>.")
            subjects.append(f"<Video {k}> is the reference video for the appearance and the motion of <Subject {people}>.")
            kept.append(f"<Subject {people}> (appears in [Shot 1]): fully_preserved - the identity, face, hair, "
                        f"clothing and colours shown in <Video {k}> are kept.")
            kept.append(f"<Video {k}>: reference - the motion and timing of <Subject {people}> in <Video {k}> are followed.")
            follow.append(f"<Subject {people}> moves as in <Video {k}>.")
        else:
            subjects.append(f"<Video {k}> is the motion reference for the target video.")
            kept.append(f"<Video {k}>: reference - the motion, timing and camera movement of <Video {k}> are followed.")
            follow.append(f"The motion and camera movement follow <Video {k}>.")
        if fields.get("video_sound"):
            sounds += 1
            subjects.append(f"<Audio {sounds}> is the synchronized audio track of <Video {k}>.")
            kept.append(f"<Audio {sounds}>: reference - the sound of <Audio {sounds}> is reused in the target video.")
            tracks.append(f"<Audio {sounds}>")
    subjects += [line.strip() for line in (fields.get("definitions") or []) if line and line.strip()]
    voices = []
    for note in audios:
        sounds += 1
        subjects.append(f"<Audio {sounds}> is " + (note.rstrip(".") if note else "the voice timbre reference for <Subject 1>'s voice"
                                                   + (f", containing {described}" if described else "")) + ".")
        kept.append(f"<Audio {sounds}>: reference - the target audio references the voice timbre from <Audio {sounds}>"
                    + " for the spoken lines." if described or not note
                    else f"<Audio {sounds}>: reference - the target audio references the sound of <Audio {sounds}>.")
        voices.append(f"<Audio {sounds}>")
    scene = (fields.get("scene") or "").strip()
    shot = joined(scene, fields.get("camera", ""), fields.get("action", ""), *follow)
    spoken = []
    for index, line in enumerate(fields.get("dialogue") or [], start=1):
        text = (line.get("text") or "").strip()
        if not text:
            continue
        voice = (line.get("voice") or "a natural voice").strip()
        verb = "sings" if line.get("sung") else "speaks"
        language = (line.get("language") or "English").strip()
        spoken.append(f"<Subject {int(line.get('subject') or 1)}> (S{index}), in {voice}, {verb}: <d>[{language}] {text}</d>")
    if spoken:
        if voices:
            shot += " The speaker's voice timbre references the delivery from " + " and ".join(voices) + "."
        # a clip longer than its lines: without this the model may keep the mouth moving and invent words
        shot += (" " + " ".join(spoken) + " Mouth movements follow the words. After the last word the speaker stops"
                 " talking, closes the mouth and stays silent until the end of the clip.")
    elif tracks:  # a video's own sound is a reference: it may hold speech, so nothing here says that nobody speaks
        shot += " The sound of " + " and ".join(tracks) + " plays through the clip."
    else:
        # a reference picture or a video with a subject is almost always a person: said unless the fields say otherwise
        shot += quiet({"people": True if fields.get("people") is None and people else fields.get("people")}, scene, fields.get("action", ""))
    if (fields.get("avoid") or "").strip():
        shot += " " + fields["avoid"].strip()
    sound = (fields.get("soundscape") or ("The sound of " + " and ".join(tracks) + ", reused." if tracks
                                          else "Natural ambient sound for the scene.")).strip()
    sound += " " + QUIET_AFTER if spoken else "" if tracks else " " + QUIET_SOUND
    if voices:
        sound += (" The spoken lines reference the voice timbre of " if spoken else " The sound references ") + " and ".join(voices) + "."
    music = (fields.get("music") or "N/A").strip()
    if music.upper() != "N/A":
        sound += f" Music: {music}."
    who = ", ".join(f"<Subject {i}>" for i in range(1, people + 1))
    kinds = " + ".join(kind for kind, used in (("subject reference", count), ("video reference", videos),
                                               ("audio reference", sounds)) if used)
    return ("subject_definitions:\n" + "\n".join(subjects)
            + f"\n\nsummary:\n[{kinds}] "
            + ((fields.get("lead") or "").strip() + " " if (fields.get("lead") or "").strip()
               else f"A new video with {who}, keeping how each looks in its " + ("reference. " if videos else "picture. ") if who
               else "A new video that follows its video reference. ")
            + (scene or "A new scene.")
            + "\n\nretention_analysis:\n" + "\n".join(kept)
            # the sound under its own heading, as in the text-to-video form: written on the end of the shot, the model
            # read it as something to say (measured on a DGX Spark: a voice in 41 % and 63 % of the frames of a
            # clip that asks for no speech, on two seeds, and in none with the heading)
            + f"\n\ndetailed_description:\n[Shot 1] {shot.strip()}\n\noverall_soundscape:\n{sound}")


def compose(fields: dict, first_frame: bool, last_frame: bool = False, seconds: float = 5, frames: int | None = None) -> str:
    """MiniMax H3's three-field prompt from the wizard's parts."""

    shot = joined(fields.get("scene", ""), fields.get("camera", ""), fields.get("action", ""))
    spoken = []
    for index, line in enumerate(fields.get("dialogue") or [], start=1):
        text = (line.get("text") or "").strip()
        if not text:
            continue
        voice = (line.get("voice") or "a natural voice").strip()
        verb = "sings" if line.get("sung") else "says"
        language = (line.get("language") or "English").strip()
        spoken.append(f"{(line.get('speaker') or '').strip() or 'The speaker'}, in {voice} (S{index}), {verb}: <d>[{language}] {text}</d>")
    if spoken:
        shot += (" " + " ".join(spoken) + " Mouth movements follow the words. After the last word the speaker stops"
                 " talking, closes the mouth and stays silent until the end of the clip.")
    else:
        shot += quiet(fields, fields.get("scene", ""), fields.get("action", ""))
    sound = (fields.get("soundscape") or "Natural ambient sound for the scene.").strip()
    if not said_quiet(sound):
        sound += " " + QUIET_AFTER if spoken else " " + QUIET_SOUND
    music = (fields.get("music") or "N/A").strip()
    body = (f"integrated_multimodal_description: [Shot 1] {shot.strip()}\n\n"
            f"overall_soundscape: {sound}\n\nnon_diegetic_music: {music}")
    opening = lead(first_frame, last_frame, seconds, frames)
    return f"{opening}\n\n{body}" if opening else body


def plan(total: int, piece: int = LONGEST) -> list[int]:
    """Clip lengths, in seconds, that add up to at least ``total``: whole pieces, then the shortest clip that covers the rest."""

    if piece not in DURATIONS:
        raise HTTPException(400, f"a segment is one of {sorted(DURATIONS)} seconds")
    pieces = [piece] * (total // piece)
    rest = total - piece * len(pieces)
    if rest > 0:
        pieces.append(min(d for d in DURATIONS if d >= rest))
    return pieces


def zig_ready() -> bool:
    """Whether FastH3 will run on the native engine here (scripts/video.sh decides the same way)."""

    if not (ZIG_ENGINE / "tf-h3-dit").is_file() or os.environ.get("FASTH3_ENGINE") == "mlx":
        return False
    try:
        chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return re.search(r"Apple M([5-9]|[1-9][0-9])", chip) is not None


try:
    M5 = re.search(r"Apple M([5-9]|[1-9][0-9])", subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                                               capture_output=True, text=True).stdout) is not None
except OSError:
    M5 = False


def turbo_ready() -> bool:
    """Whether MiniMax H3's own transformer and its Turbo adapter are installed (oneshot-setup.sh --turbo).

    The default install leaves them out: FastH3 needs the MiniMax H3 text encoder and decoders, not its transformer.
    """

    model = Path(os.environ.get("H3_MODEL_DIR", Path.home() / "h3-models/MiniMax-H3"))
    return any((model / "FL2VA" / "transformer").glob("*.safetensors")) and any((ZIG_ENGINE.parent / "adapters").glob("lightx2v*"))


# The picture models a person picks from (`image_model` on a scout or studio job, IMAGE_MODEL to scripts/image.sh).
# Qwen-Image-2.1-Turbo is every install's; FLUX3-K (Black Forest Labs' FLUX 3 Image Klein, oneshot-setup.sh --flux3) adds
# Fast, the distilled checkpoint at 16 steps, and Full, the base one at 50. Seconds per 1344x768 picture, measured with
# the model loaded, bf16: Qwen 9.7 on an M5 Ultra; FLUX3-K Fast 10.5 on a DGX Spark (ComfyUI) and 14.9 on an M5 Ultra
# (flux3-mlx), Full 57.5 on a Spark and 53.6 on an M5 Ultra; BFL's prompt upsampler adds about 18 s on a Spark (8-bit,
# ComfyUI) and 5 s on an M5 Ultra (8-bit MLX), once per prompt. MOSS-Image (OpenMOSS, Apache 2.0, oneshot-setup-spark.sh
# --moss / oneshot-setup.sh --moss) runs on ComfyUI v0.42.0's own support on both machines, the Mac's ComfyUI on
# PyTorch's Metal device (mac/comfy.sh): 45 s for 1344x768 on a Spark with the model loaded.
IMAGE_MODELS = {"qwen": ("Qwen 2.1 Turbo", "8 steps, the quickest draft; Qwen Research License (non-commercial)"),
                "qwen-full": ("Qwen 2.1 Full", "the 40-step base model at CFG 4: more detail than Turbo, about 8x slower"),
                "flux3k-fast": ("FLUX3-K Fast", "FLUX 3 Image Klein, 16 steps: sharper text, whole figures, better prompt following"),
                "flux3k-full": ("FLUX3-K Full", "FLUX 3 Image Klein base, 50 steps: the most detail, about 5x slower than Fast"),
                "moss-image": ("MOSS-Image", "25 steps: exact lettering for posters, titles and signs; Apache 2.0, commercial use allowed")}
# BFL's file names, the present one first (renamed 2026-10-17, the same files; spark_generate.FLUX3_OLD)
FLUX3_FILES = {"flux3k-fast": ("flux3-image-klein-bf16.safetensors", "flux3_image_klein_sd_bf16.safetensors"),
               "flux3k-full": ("flux3-image-klein-base-bf16.safetensors", "flux3_image_klein_base_bf16.safetensors")}


def image_models() -> list[str]:
    """The picture models installed here, Qwen first: FLUX3-K's ComfyUI files on a Spark, its own venv and BFL's
    checkpoints on a Mac."""

    found = (["qwen"] if qwen_ready() else []) + (["qwen-full"] if qwen_base_ready() else [])
    for name, unets in FLUX3_FILES.items():
        if SPARK:
            folder = Path(os.environ.get("SPARK_MODELS", Path.home() / "spark-studio-models")) / "FLUX3-K-Comfy"
            ready = (any((folder / "diffusion_models" / unet).is_file() for unet in unets)
                     and (folder / "system_prompts.json").is_file())
        else:
            folder = Path(os.environ.get("FLUX3_MODEL_DIR", Path.home() / "flux3-models"))
            ready = ((ZIG_ENGINE.parent / "flux3-venv" / "bin" / "python").exists()
                     and (folder / ("FLUX3-K-Fast" if name == "flux3k-fast" else "FLUX3-K-Full") / "model.safetensors").is_file())
        if ready:
            found.append(name)
    if moss_ready():
        found.append("moss-image")
    return found


def moss_ready() -> bool:
    """MOSS-Image's ComfyUI files, and a ComfyUI that knows the model (v0.42.0 and later): a Spark's, or on a Mac the
    ComfyUI of scripts/setup-comfy.sh in its own environment."""

    prefix = Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio"))
    if SPARK:
        models = Path(os.environ.get("SPARK_MODELS", Path.home() / "spark-studio-models")) / "MOSS-Image-Comfy"
    else:
        models = Path(os.environ.get("MAC_COMFY_MODELS") or prefix / "comfy-models")
        if not (prefix / "comfy-venv/bin/python").exists():
            return False
    return ((models / "diffusion_models/moss_image_sft_bf16.safetensors").is_file()
            and (models / "text_encoders/qwen3.5_9b_bf16.safetensors").is_file()
            and (models / "vae/flux2-vae_small.safetensors").is_file()
            and (prefix / "ComfyUI/comfy_extras/nodes_moss_image.py").is_file())


def qwen_ready() -> bool:
    """Qwen-Image-2.1-Turbo, every install's picture model: on a Spark its ComfyUI files (a worker set up without image
    weights has none, and a picture there used to fail inside ComfyUI with a bare 400); a Mac's setup always has it."""

    if not SPARK:
        return True
    models = Path(os.environ.get("SPARK_MODELS", Path.home() / "spark-studio-models")) / "Qwen-Image-2.1-Comfy"
    return all((models / name).is_file() for name in ("diffusion_models/qwen_image_2.1_turbo_bf16.safetensors",
                                                     "text_encoders/qwen3vl_8b_int8_convrot.safetensors",
                                                     "vae/qwen_image_2.1_vae_bf16.safetensors"))


def qwen_base_ready() -> bool:
    """Qwen-Image-2.1's 40-step base model, the same files Edit a picture's 40-step choice uses."""

    if SPARK:
        models = Path(os.environ.get("SPARK_MODELS", Path.home() / "spark-studio-models")) / "Qwen-Image-2.1-Comfy"
        return (models / "diffusion_models/qwen_image_2.1_bf16.safetensors").is_file()
    turbo = Path(os.environ.get("QWEN_MODEL_DIR", Path.home() / "qwen-models/Qwen-Image-2.1-Turbo"))
    base = Path(os.environ.get("QWEN_BASE_DIR", turbo.parent / "Qwen-Image-2.1"))
    return (base / "transformer").is_dir() and (base / "processor").is_dir()


def image_model_env(params: dict) -> dict:
    """IMAGE_MODEL for a job's picture, refused when that model is not installed here."""

    name = params.get("image_model") or "qwen"
    if name not in IMAGE_MODELS:
        raise HTTPException(400, f"unknown picture model {name}: one of {', '.join(IMAGE_MODELS)}")
    if name not in image_models():
        how = {"qwen": "", "qwen-full": " --image-edit", "moss-image": " --moss"}.get(name, " --flux3")
        raise HTTPException(400, f"{IMAGE_MODELS[name][0]} is not installed here: oneshot-setup.sh{how} adds it"
                                 + (" (Machine → Models & weights shows what is missing)" if name == "qwen" else ""))
    return {"IMAGE_MODEL": name}


def text_encoders() -> dict[str, str]:
    """Other MiniMax H3 text encoder files found on this machine: file name -> what TEXT_ENCODER is set to for it.
    A file put in the text_encoders folder beside the model (on a Spark, ComfyUI's own folder) is offered by name."""

    if SPARK:
        folder = SPARK_REF2VA.parent / "FastH3-Comfy/text_encoders"
        own = {"qwen3vl_32b_minimax_h3_int8_convrot.safetensors"}
        return {f.name: f.name for f in sorted(folder.glob("*minimax_h3*.safetensors")) if f.name not in own}
    folder = Path(os.environ.get("H3_MODEL_DIR", Path.home() / "h3-models/MiniMax-H3")).parent / "text_encoders"
    return {f.name: str(f) for f in sorted(folder.glob("*.safetensors"))}



def own_text_encoder(asked: str) -> str | None:
    """A text encoder file of the user's own choosing: what TEXT_ENCODER is set to for it, or None when there is no
    such file. On a Mac a path; on a Spark the name of a file in ComfyUI's text_encoders folder (a path's last part)."""
    if not asked.endswith(".safetensors"):
        return None
    if SPARK:
        name = Path(asked).name
        return name if (SPARK_REF2VA.parent / "FastH3-Comfy/text_encoders" / name).is_file() else None
    path = Path(asked).expanduser()
    return str(path) if path.is_file() else None

def ref2va_ready() -> bool:
    """Whether reference to video can run here: MiniMax H3's Ref2VA transformer (oneshot-setup.sh --ref2va) beside
    the text encoder and decoders every install has. A Mac feature: the passes run on the MLX engine."""

    if SPARK:
        return any(SPARK_REF2VA.glob("diffusion_models/minimax_h3_ref2va_pruned_*.safetensors"))
    model = Path(os.environ.get("H3_MODEL_DIR", Path.home() / "h3-models/MiniMax-H3"))
    return any((model / "Ref2VA" / "transformer").glob("*.safetensors"))


def custom(params: dict) -> tuple[dict, bool]:
    """(environment, whether anything was set) for a size or a number of passes chosen by hand."""

    env = {}
    if params.get("preset") == "custom":
        try:
            width, height = int(params["width"]), int(params["height"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "a custom size needs a width and a height")
        low, high = CUSTOM_SIDES
        if width % 32 or height % 32 or not (low <= width <= high and low <= height <= high):
            raise HTTPException(400, f"width and height are multiples of 32 between {low} and {high}")
        if width * height > CUSTOM_PIXELS:
            raise HTTPException(400, f"at most {CUSTOM_PIXELS} pixels (1344x768), got {width}x{height}")
        env.update(WIDTH=str(width), HEIGHT=str(height))
    if params.get("steps") not in (None, ""):
        try:
            steps = int(params["steps"])
        except (TypeError, ValueError):
            raise HTTPException(400, "passes is a whole number")
        if not CUSTOM_STEPS[0] <= steps <= CUSTOM_STEPS[1]:
            raise HTTPException(400, f"passes is between {CUSTOM_STEPS[0]} and {CUSTOM_STEPS[1]}")
        env["STEPS"] = str(steps)
    if params.get("text_encoder") not in (None, "", "regular"):
        asked = str(params["text_encoder"]).strip()
        found = text_encoders().get(asked) or own_text_encoder(asked)
        if not found:
            raise HTTPException(400, f"no text encoder file at {asked} on this machine (a .safetensors file"
                                     + (" in ComfyUI's text_encoders folder)" if SPARK else ")"))
        env["TEXT_ENCODER"] = found
    return env, bool(env)


def ref2va_turbo_ready() -> bool:
    if SPARK:
        return ((SPARK_REF2VA / "diffusion_models/minimax_h3_ref2va_pruned_bf16.safetensors").is_file()
                and (SPARK_REF2VA / "loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors").is_file())
    return ref2va_ready() and (ZIG_ENGINE.parent / "adapters" / REF2VA_ADAPTER).is_file()


def media_seconds(path: str) -> tuple[float | None, bool | None]:
    """(length in seconds, whether it has sound) of a media file, or (None, None) without ffprobe: the renderer
    measures again and refuses what is out of range."""

    tool = shutil.which("ffprobe") or shutil.which("ffprobe", path="/opt/homebrew/bin:/usr/local/bin")
    if tool is None:
        return None, None
    try:
        done = subprocess.run([tool, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json",
                               path], capture_output=True, text=True, timeout=30)  # in a request: never without a limit
    except subprocess.TimeoutExpired:
        return None, None
    try:
        data = json.loads(done.stdout)
        return float(data["format"]["duration"]), any(one.get("codec_type") == "audio" for one in data["streams"])
    except (KeyError, TypeError, ValueError):
        return None, None


def references(params: dict) -> dict:
    """The job's references as paths under the studio folder: {} when it has none, else ``pictures``, ``videos``
    and ``audios`` (each a list) and ``video_sound`` (the videos' own soundtracks are references too)."""

    found = {kind: [str(inside(path)) for path in (params.get(key) or []) if path]
             for kind, key in (("pictures", "references"), ("videos", "reference_videos"), ("audios", "reference_audios"))}
    if len(found["videos"]) == 1 and freed(params.get("pace"), params.get("motion") if isinstance(params.get("motion"), dict) else None, len(found["pictures"])):
        found["videos"] = []  # Impacts over a slow reference: the prompt carries the fight (see freed)
    pictures, videos, audios = found["pictures"], found["videos"], found["audios"]
    if not (pictures or videos or audios):
        return {}
    for kind, limit, files in (("reference pictures", REFERENCE_LIMIT, pictures), ("reference videos", REFERENCE_VIDEO_LIMIT, videos),
                               ("reference audio clips", REFERENCE_AUDIO_LIMIT, audios)):
        if len(files) > limit:
            raise HTTPException(400, f"at most {limit} {kind}")
    if len(pictures) + len(videos) + len(audios) > REFERENCE_TOTAL:
        raise HTTPException(400, f"at most {REFERENCE_TOTAL} references in all")
    if audios and not (pictures or videos):
        raise HTTPException(400, "reference audio needs a reference picture or video beside it: the model does not take sound alone")
    if params.get("image") or params.get("first_frame_from"):
        raise HTTPException(400, "references do not combine with a first frame")
    if not ref2va_ready():
        raise HTTPException(400, "MiniMax H3 Ref2VA is not installed here (oneshot-setup.sh --ref2va adds it)")
    if params.get("ref_turbo") and not ref2va_turbo_ready():
        raise HTTPException(400, "the Ref2VA Turbo adapter is not installed here (oneshot-setup.sh --ref2va adds it)")
    missing = [path for path in pictures + videos + audios if not Path(path).is_file()]
    if missing:
        raise HTTPException(400, f"no reference file at {relative(Path(missing[0]))}")
    wrong = [path for path in videos if Path(path).suffix.lower() not in VIDEO_SUFFIXES]
    if wrong:
        raise HTTPException(400, f"a reference video is an mp4 or mov file, and {relative(Path(wrong[0]))} is not")
    wrong = [path for path in audios if Path(path).suffix.lower() not in AUDIO_SUFFIXES + VIDEO_SUFFIXES]
    if wrong:
        raise HTTPException(400, f"a reference audio clip is a sound file, and {relative(Path(wrong[0]))} is not")
    video_sound = bool(videos and params.get("reference_video_sound"))
    measured = (params.get("motion") or {}).get("measured") if isinstance(params.get("motion"), dict) else None
    if video_sound and isinstance(measured, dict) and measured.get("sound") == "impacts" and params.get("pace") == "flow":
        video_sound = False  # Flow by choice: the reference's own hits are not reused (as the prompt says)
    low, high = REFERENCE_CLIP_SECONDS
    for kind, files in (("video", videos), ("audio clip", audios)):
        total = 0.0
        for path in files:
            seconds, sound = media_seconds(path)
            if seconds is None:
                continue
            if not low - 0.05 <= seconds <= high + 0.05:
                raise HTTPException(400, f"a reference {kind} is {low:g} to {high:g} seconds long, and "
                                         f"{relative(Path(path))} is {seconds:.1f} s")
            if not sound and (kind == "audio clip" or video_sound):
                raise HTTPException(400, f"{relative(Path(path))} has no sound" + (": untick the videos' own sound"
                                                                                  if kind == "video" else ""))
            total += seconds
        if total > REFERENCE_KIND_SECONDS + 0.05:
            raise HTTPException(400, f"the reference {kind}s are at most {REFERENCE_KIND_SECONDS:g} seconds in all, got {total:.1f} s")
    return {**found, "video_sound": video_sound}


def estimate(preset: str, quality: str, seconds: float, text_only: bool = False, referenced: bool = False,
             ref_turbo: bool = False, ref_media: bool = False) -> int | None:
    if referenced and ref_media:
        return None  # a reference video or audio clip adds its own rows to every pass: the time depends on its length
    if referenced:
        # the same MLX engine as the 20-step qualities, whose time grows with the length to the power 1.6
        if SPARK:
            base = (SPARK_REF2VA_TURBO_5S if ref_turbo else SPARK_REF2VA_5S).get(preset)
        else:
            base = (REF2VA_TURBO_5S if ref_turbo else REF2VA_5S).get(preset)
        return None if base is None or seconds > LONGEST else int(base * (seconds / 5.0) ** 1.6)
    if seconds > LONGEST:
        # every part after the first opens on a frame. On a DGX Spark a chained part takes the time of a clip from text:
        # Keys measured a 30 s take at 864x480, 4 passes (two 15 s parts) in 333 s, where the from-a-picture factor
        # said 508 s, and a 30 minute chain in about 5.5 h (2026-10-19, one Spark)
        parts = [estimate(preset, quality, piece, text_only=SPARK) for piece in plan(seconds)]
        return None if None in parts else int(sum(parts) + 2 * len(parts))
    if SPARK:
        base = (SPARK_FASTH3_5S if SPARK_ENGINE_LIB.exists() else SPARK_COMFY_5S).get(quality, {}).get(preset)
        # the pass time grows a little faster than the length: attention over all rows is part of every pass
        return None if base is None else int(base * (seconds / 5.0) ** 1.2 * (1.0 if text_only else 1.5))
    if zig_ready() and preset in ZIG_FASTH3_5S.get(quality, {}):
        return int(ZIG_FASTH3_5S[quality][preset] * (seconds / 5.0) ** ZIG_LENGTH_POWER[preset] * (1.0 if text_only else ZIG_FROM_IMAGE))
    if quality.startswith("fh") and not M5:
        return None  # FastH3 on an earlier chip runs with 8-bit weights on the MLX engine; no time is measured there
    if quality in FASTH3_5S and preset in FASTH3_5S[quality]:
        return int(FASTH3_5S[quality][preset] * (seconds / 5.0) ** 1.3)
    if quality.startswith("fh"):
        return None
    base = MEASURED_8S.get(preset)
    if base is None:
        return None
    scale = {"standard": 1.0, "high": 1.45, "full": 3.0, "fast": 0.58}[quality] * (seconds / 8.0) ** 1.6
    return int(base * scale)


def environment(extra: dict) -> dict:
    env = dict(os.environ)
    for key, value in settings.job_env().items():  # Settings → Presets (the delivery sound rate); the environment's own wins
        env.setdefault(key, value)
    env.update({key: str(value) for key, value in extra.items() if value not in (None, "")})
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    return env


# The longest clip, in frames, by the pixels of the canvas it is generated at. On a DGX Spark the GPU and the system
# share one pool of memory, so this is a measurement (see lengths.py and the README); the Mac keeps the model's own 15 s.
SPARK_LONGEST = ((1376 * 768, lengths.LONGEST),)


def longest_frames(env: dict) -> int:
    """The longest clip this machine renders at the size in ``env``."""

    if not SPARK:
        return lengths.LONGEST
    try:
        pixels = int(env.get("WIDTH") or 864) * int(env.get("HEIGHT") or 480)
    except (TypeError, ValueError):
        return lengths.LONGEST
    return next((frames for most, frames in SPARK_LONGEST if pixels <= most), SPARK_LONGEST[-1][1])


def clip_frames(params: dict, env: dict | None = None) -> int:
    """The frame count of a single clip, or 400 with the reason."""

    try:
        return lengths.of(params, longest_frames(env or {}))
    except ValueError as error:
        raise HTTPException(400, str(error))


def clip_seconds(params: dict) -> float:
    """The length of a job for the time estimate: a long video's total, else the clip's own seconds (at most LONGEST,
    over which the estimate counts a chain)."""

    if params.get("total_seconds"):
        return int(params["total_seconds"])
    try:
        frames = lengths.of(params)
    except ValueError:
        return 5.0
    # the measured times are for the four classic lengths by their round names: 124 frames is "5 seconds"
    return {count: named for named, count in DURATIONS.items()}.get(frames) or min(frames / FPS * 120 / 124, float(LONGEST))


def held_room() -> int | None:
    """What this machine has for a render while Drama 3, MiniMax Music 3's CUDA engine or NVIDIA Nemotron 3.5 Bolt is
    loaded (each is unloaded before a job that needs its memory starts, so their memory counts as that job's, like the
    ComfyUI server's own); None otherwise."""

    held = [one for one in (voice.room_mb(), music.room_mb(), bolt.room_mb()) if one]
    return max([memory_guard.room_mb() or 0] + held) if held else None


def bolt_room_mb(resident_gb: float) -> int | None:
    """What this machine has for a job with Bolt resident beside it (MB): the memory guard's room (what is available,
    or what ComfyUI's guard noted before its server loaded) less what Bolt holds, or would hold once loaded."""

    room = memory_guard.room_mb()
    if room is None:
        return None
    one = bolt.engine()
    if not one.loaded():
        return int(room - resident_gb * 1024)
    if memory_guard.discrete():  # the card's room already counts what Bolt holds on it
        return int(room)
    now = memory_guard.meminfo().get("MemAvailable") or room
    try:
        noted = Path(memory_guard.state_file()).stat().st_mtime
    except OSError:
        noted = None
    if room > now and noted is not None and noted < one.loaded_at:  # ComfyUI's note was made before Bolt loaded
        room -= bolt.held_mb()
    return int(room)


def bolt_need(job: dict, resident_gb: float) -> tuple[bool, str] | None:
    """Whether a job of this machine's queue fits with NVIDIA Nemotron 3.5 Bolt resident beside it (``resident_gb``
    held by it), from the measured peaks: (fits, the figures said in the job's log), or None when the job's kind has no
    measured peak here (Bolt then goes). On a DGX Spark the memory guard's estimate (clips, songs, sound; Drama 3's
    NEED_GB); on a Mac the peaks measured on an M5 Ultra (bolt.MAC_PEAK_GB) against what vm_stat counts as available."""

    kind, params = job["kind"], job["params"]
    if not SPARK:
        free = voice.mem_available_gb()
        if free < 0:
            return None
        one = bolt.engine()
        # loaded, the available memory already lacks what it took, but not its mapped weights (counted as available)
        have = free - getattr(one, "uncounted_gb", 0.0) if one.loaded() else free - resident_gb
        turbo = params.get("quality") == "turbo" or params.get("engine") == "turbo" or bool(params.get("references"))
        peak = bolt.MAC_PEAK_GB["turbo"] if turbo and kind in ("video", "studio", "long") else bolt.MAC_PEAK_GB.get(kind, bolt.MAC_PEAK_UNKNOWN_GB)
        left, floor = have - peak, one.guard_gb
        return left >= floor, (f"{have:.0f} GB available with Bolt resident, the job's measured peak about {peak:.0f} GB, "
                               f"{left:.0f} GB left against a floor of {floor:g}")
    room = bolt_room_mb(resident_gb)
    if room is None:
        return None
    floor = memory_guard.floor_mb()
    try:
        if kind in ("video", "studio", "long"):
            chosen, _ = custom(params) if params.get("preset") == "custom" else ({}, False)
            sized = {**(PRESETS[params["preset"]][2] if params.get("preset") in PRESETS else {}), **chosen}
            frames = lengths.of({"frames": params.get("frames"), "seconds": min(float(params.get("seconds") or 5), LONGEST)})
            pictures, videos = params.get("references") or [], params.get("reference_videos") or []
            found = {"pictures": pictures, "videos": [str(inside(v)) for v in videos]} if pictures or videos else None
            said = memory_of(params, sized, frames, bool(params.get("loras")), found, available_mb=room)
        elif kind == "music":
            said = memory_guard.music_check(float(params.get("seconds") or 60), available_mb=room,
                                            route=getattr(music.engine(), "guard_route", "music"))
        elif kind in sound.KINDS:
            said = memory_guard.sound_check(kind, float(params.get("seconds") or 10), available_mb=room)
            if said.get("need_mb") is None:
                return None
        elif kind == "voice":
            one = voice.backend()
            need = 0 if one.loaded() else int(float(getattr(one, "NEED_GB", 50)) * 1024)
            said = {"fits": room - need >= floor, "need_mb": need, "available_mb": room}
        else:
            return None
    except (HTTPException, ValueError, KeyError, TypeError):
        return None
    if said.get("route") is None and said.get("need_mb") is None:
        return None
    need, have = said.get("need_mb") or 0, said.get("available_mb") or room
    return bool(said.get("fits")), (f"{have / 1000:.0f} GB for the job with Bolt resident, the job's estimated peak about "
                                    f"{need / 1000:.0f} GB, the floor {floor / 1000:.0f} GB")


def memory_of(params: dict, env: dict, frames: int, lora: bool, found: dict | None = None, available_mb: int | None = None) -> dict:
    """Whether a clip fits this machine's memory (spark/memory_guard.py; a DGX Spark only, everything fits on a Mac):
    the size it is generated at, its frames, whether references put it on ComfyUI's own bfloat16 blocks and whether a
    LoRA is merged into TensorFold's engine (or, with an engine build that cannot take one, goes to those blocks too)."""

    if not SPARK:
        return {"fits": True, "reason": ""}
    try:
        width, height = int(env.get("WIDTH") or 864), int(env.get("HEIGHT") or 480)
    except (TypeError, ValueError):
        width, height = 864, 480
    found = found or {}
    videos = found.get("videos") or []
    lora = lora or bool(found and params.get("ref_turbo"))
    engine = SPARK_ENGINE_LIB.exists() and (not lora or memory_guard.engine_takes_lora(SPARK_ENGINE_LIB))
    # a Drama 3 server kept loaded after a voice take is unloaded before this job starts (voice.release): its memory
    # counts as this job's, like the ComfyUI server's own; so does MiniMax Music 3's CUDA engine (music.release), and
    # NVIDIA Nemotron 3.5 Bolt's (bolt.admit unloads it when the job does not fit beside it)
    room = held_room() if available_mb is None else available_mb
    return memory_guard.check(width, height, frames, lora=lora, references=bool(found), available_mb=room,
                              base=env.get("SPARK_BASE") == "h3", engine=engine,
                              pictures=len(found.get("pictures") or []),
                              reference_frames=sum(min(frames, int(round((media_seconds(path)[0] or frames / FPS) * FPS))) for path in videos))


def media_tools() -> dict:
    """Whether ffmpeg and ffprobe are on this machine, looked for where the jobs will look."""

    path = environment({})["PATH"]
    return {name: bool(shutil.which(name, path=path)) for name in ("ffmpeg", "ffprobe")}


FFMPEG_HOW = "sudo apt install ffmpeg" if SPARK else "brew install ffmpeg"
FFMPEG_NOTE = ("ffmpeg is not installed on this machine (" + FFMPEG_HOW + "). Without it: no long videos, no timeline export, "
               "no frame taken from a clip (continue from a clip, end on a clip), no reference videos or audio, no silenced "
               "sound track" + (", and a clip keeps ComfyUI's own sound track instead of the limited 256 kb/s one. Single clips, "
                                "the 720p crop included, and pictures render without it." if SPARK else "."))


def without_ffmpeg(kind: str, params: dict) -> str:
    """Why this job cannot run on a machine without ffmpeg ("" when it can, or when ffmpeg is there): said before the
    job is queued, not after its render."""

    if media_tools()["ffmpeg"]:
        return ""
    what = ("joining the clips of a timeline" if kind == "export" else "laying a song under a clip" if kind == "soundtrack" else "joining the clips of a long video" if kind == "long"
            else "taking the last frame of the clip this one continues from" if params.get("first_frame_from")
            else "taking the first frame of the clip this one leads into" if params.get("last_frame_from")
            else "reading reference videos and audio" if params.get("reference_videos") or params.get("reference_audios")
            else "silencing the clip's sound track" if params.get("silent") else "")
    return f"{what} needs ffmpeg, which is not installed on this machine: {FFMPEG_HOW}" if what else ""


# A Mac's ComfyUI jobs (mac/comfy.sh: Upscale, Find and Remove a person, MOSS-Image): their peaks, measured on the
# M5 Ultra (GB, the ComfyUI server's own memory at its highest, models loaded from nothing). Remove a person and Upscale
# hold MiniMax H3's bf16 Ref2VA transformer and text encoder (62 GB each) at once.
MAC_COMFY_PEAK_GB = {"upscale": 125.0, "mask": 12.0, "remove": 135.0, "moss-image": 40.0}  # measured 118, SAM only, 127, 36
MAC_COMFY_FLOOR_GB = 16.0  # what stays available beside it: mac/comfy.sh's kill-guard stops the server under this


def mac_heaviest(limit: int = 3, least_gb: float = 4.0) -> str:
    """The processes holding the most memory on this Mac other than the Studio's ComfyUI, for a refusal's reason:
    "ollama 114 GB, tensorfold-native 34 GB"."""

    try:
        said = subprocess.run(["ps", "-axo", "rss=,comm="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    rows = []
    for line in said.splitlines():
        size, _, name = line.strip().partition(" ")
        if size.isdigit() and int(size) / 1024 ** 2 >= least_gb and "comfy-venv" not in name:
            rows.append((int(size) / 1024 ** 2, Path(name.strip()).name))
    return ", ".join(f"{name} {gb:.0f} GB" for gb, name in sorted(rows, reverse=True)[:limit])


def mac_comfy_room(what: str) -> None:
    """On a Mac, refuses a ComfyUI job whose measured peak does not fit: what is available now (voice.mem_available_gb:
    what macOS gets back without compressing) and what the Studio's ComfyUI server already holds, less the floor. A job
    that did not fit stalled the window server for 125 s and the Mac restarted (2026-10-20: ollama held 114 GB)."""

    peak = MAC_COMFY_PEAK_GB.get(what)
    if SPARK or not peak:
        return
    free = voice.mem_available_gb()
    if free < 0:
        return
    held = 0.0
    try:
        said = subprocess.run(["pgrep", "-f", "[m]ain.py --listen 127.0.0.1 --port " + os.environ.get("MAC_COMFY_PORT", "8191")],
                              capture_output=True, text=True, timeout=5).stdout.split()
        if said:
            rss = subprocess.run(["ps", "-o", "rss=", "-p", said[0]], capture_output=True, text=True, timeout=5).stdout.strip()
            held = int(rss) / 1024 ** 2 if rss.isdigit() else 0.0
    except (OSError, subprocess.TimeoutExpired):
        pass
    have = free + held
    if have - peak < MAC_COMFY_FLOOR_GB:
        others = mac_heaviest()
        raise HTTPException(400, f"this needs about {peak:.0f} GB and this Mac has {have:.0f} GB for it (keeping "
                                 f"{MAC_COMFY_FLOOR_GB:.0f} GB free), so it is not started"
                            + (f": the most memory is held by {others}. Quit what you do not need and try again" if others else ""))


def build(kind: str, params: dict, job_id: str, room: bool = True) -> tuple[list[str], dict, list[str]]:
    """(command, environment, output paths) for one job. ``room`` False leaves this machine's memory out of it: the
    clip renders on another Spark, whose own guard is asked (sparks.py)."""

    scripts = PACK / "scripts"
    if kind in actions.KINDS:  # a Library action (actions.py): turnaround, upscale, mask, remove
        built = action_build(kind, params, job_id)
        if room:
            mac_comfy_room(kind)
        return built
    if kind in sound.KINDS:  # a sound effect or a clip's foley (sound.py)
        return sound_build(kind, params, job_id)
    seed = str(int(params.get("seed", 0)))
    if kind == "scout":
        folder = HOME / "scouts" / job_id
        prompt = params["prompt"]
        count = max(1, min(int(params.get("count", 4)), 12))
        # a scout is made at the size the video model will start from (the draft keeps a full 1344x768 still)
        width, height = SCOUT_SIZES.get(params.get("preset", "2k"), (1024, 576))
        if params.get("width") and params.get("height"):  # an image for its own sake, at the size asked for
            width, height = int(params["width"]), int(params["height"])
            if width % 16 or height % 16 or not (256 <= width <= 2048 and 256 <= height <= 2048):
                raise HTTPException(400, "image sides are multiples of 16 between 256 and 2048")
        env = {"SEED": seed, "WIDTH": width, "HEIGHT": height, **image_model_env(params)}
        if room:
            mac_comfy_room(params.get("image_model") or "")
        first = int(seed)
        outputs = [str(folder / f"scout_s{first + i}.png") for i in range(count)] + [str(folder / "sheet.jpg")]
        return ["bash", str(scripts / "scout.sh"), prompt, str(count), str(folder)], env, outputs
    if kind == "export":
        return ["python3", "-c", "pass"], {}, []
    if kind == "edit":  # a picture re-drawn by the image model (picture_edit.py)
        return edit_build(params, job_id)
    preset = params.get("preset", "small" if SPARK else "draft")
    quality = params.get("quality", "fh8" if SPARK else "standard")  # a Spark has no "standard": an API call or an agent may leave it out
    chosen, _ = custom(params)
    if (preset not in PRESETS and preset != "custom") or quality not in QUALITIES:
        raise HTTPException(400, "unknown preset or quality")
    sized = {**(PRESETS[preset][2] if preset in PRESETS else {}), **chosen}
    # any length the model takes: "frames" (17n + 5), or "seconds" brought to the nearest such count
    frames = clip_frames(params, sized)
    target = Path(params["output"]) if params.get("output") else HOME / "renders" / f"{job_id}.mp4"
    target = inside(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    env = {"SEED": seed, "FRAMES": frames, **(PRESETS[preset][2] if preset in PRESETS else {}), **QUALITIES[quality][1]}
    fast_h3 = SPARK or env.get("ENGINE") == "fasth3"
    prompt = params["prompt"]
    image = params.get("image")
    styles = lora_resolve(params.get("loras"))  # the LoRA hub (loras.py): files to merge, and their trigger words
    if styles:
        # merged at load into the weights the clip runs on: FastH3's or MiniMax H3's for a clip from text or a first
        # frame, the Ref2VA transformer's for a clip from references (bfloat16 on a Spark, see spark_generate.py)
        env["LORA"] = "\n".join(f"{one['path']}:{one['strength']:g}" for one in styles) + "\n"
        prompt = lora_lead(prompt, styles)
    # the end keyframe (MiniMax H3's first-and-last-frame mode): a picture of its own, or the first frame again
    ending = params.get("last_image") or (image if params.get("last_same") else None)
    if ending:
        if kind not in ("video", "studio"):
            raise HTTPException(400, "a last frame is for single clips")
        if kind == "video" and references(params):
            raise HTTPException(400, "references do not combine with a last frame: a reference is not a frame of the clip")
        if not inside(ending).is_file():
            raise HTTPException(400, "the last frame's picture is not in the studio's folder")
        env["LAST_FRAME"] = str(inside(ending))
    if kind == "video":  # text to video, or from an image the user already has
        pictures = references(params)
        if styles:
            # a LoRA made for one job on the reference model says what the clip must hold (lora_flows.py)
            missing = lora_flows.fits(params.get("loras"), len(pictures["pictures"]) if pictures else 0,
                                      len(pictures["videos"]) if pictures else 0, bool(pictures and params.get("ref_turbo")))
            if missing:
                raise HTTPException(400, missing)
        if pictures:
            if styles and lora_flows.plain(params.get("loras")):
                env["REF_PLAIN"] = "1"  # the prompt as written: no `Use <Video 1> as the subject.` ahead of it
            # Ref2VA takes neither the Turbo adapter nor FastH3: the quality setting does not apply
            env = {k: v for k, v in env.items() if k not in QUALITIES[quality][1]}
            # one per line, so a path may hold spaces
            for name, kind in (("REF_IMAGES", "pictures"), ("REF_VIDEOS", "videos"), ("REF_AUDIOS", "audios")):
                if pictures[kind]:
                    env[name] = "\n".join(pictures[kind]) + "\n"
            if pictures["video_sound"]:
                env["REF_VIDEO_SOUND"] = "1"
            if params.get("ref_turbo"):
                env["REF_TURBO"] = "1"
        env.update(passes(chosen, SPARK or (fast_h3 and not pictures)))  # Ref2VA runs on MiniMax H3
        fits = memory_of(params, env, frames, bool(styles), pictures) if room else {"fits": True}
        if not fits["fits"]:
            raise HTTPException(400, fits["reason"])
        if image:
            return (["bash", str(scripts / "studio.sh"), "", prompt, str(target)],
                    {**env, "IMAGE_FILE": str(inside(image)), "RAW_PROMPT": "1"}, [str(target)])
        size = PRESETS[preset][1] if preset in PRESETS else (int(env["WIDTH"]), int(env["HEIGHT"]))
        plain = {k: v for k, v in env.items() if k not in ("TWOK", "QHD")}
        if "TWOK" in env:
            plain.update(X2="1", WIDTH="2048", HEIGHT="1152")
        if "QHD" in env:
            plain.update(X2="1", WIDTH="2560", HEIGHT="1472", CROP="2560x1440")
        if "X2" in env and "WIDTH" not in plain:
            plain.update(WIDTH=str(size[0]), HEIGHT=str(size[1]))
        return ["bash", str(scripts / "video.sh"), prompt, str(target)], plain, [str(target)]
    if kind == "studio":  # text to image to video in one go
        env.update(passes(chosen, fast_h3))
        fits = memory_of(params, env, frames, bool(styles)) if room else {"fits": True}
        if not fits["fits"]:
            raise HTTPException(400, fits["reason"])
        return (["bash", str(scripts / "studio.sh"), params["image_prompt"], prompt, str(target)],
                {**env, **image_model_env(params), "RAW_PROMPT": "1"}, [str(target), str(target.with_suffix(".png"))])
    raise HTTPException(400, f"unknown job kind {kind}")


def passes(chosen: dict, steps: bool) -> dict:
    """A hand-set number of passes in the form the engine takes: STEPS for FastH3 and on a Spark, sigma points (one
    more than the passes) for MiniMax H3 on the Mac."""

    out = dict(chosen)
    if "STEPS" in out and not steps:
        out["POINTS"] = str(int(out.pop("STEPS")) + 1)
    return out


def last_frame(video: Path, image: Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.2", "-i", str(video), "-vsync", "0", "-update", "1",
                    "-q:v", "1", str(image)], check=True, env=environment({}), timeout=120)


def frame_at(video: Path, image: Path, at: float) -> None:
    """One frame of a clip at its own resolution: the frame shown ``at`` seconds in."""

    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(at, 0):.3f}", "-i", str(video), "-frames:v", "1",
                    "-q:v", "1", str(image)], check=True, env=environment({}), timeout=120)


def export(job: dict) -> None:
    """Cut each timeline clip to its in and out points and join them, with a short audio fade at each join."""

    params = job["params"]
    clips = params["clips"]
    if not clips:
        raise RuntimeError("the timeline is empty")
    width, height = int(params.get("width", 1920)), int(params.get("height", 1080))
    target = HOME / "exports" / f"{job['id']}.mp4"
    inputs, filters, labels = [], [], []
    for index, clip in enumerate(clips):
        source = inside(clip["path"])
        start = max(0.0, float(clip.get("in", 0.0)))
        end = float(clip.get("out", 0.0))
        inputs += ["-i", str(source)]
        span = f"trim=start={start}:end={end},setpts=PTS-STARTPTS," if end > start else ""
        aspan = f"atrim=start={start}:end={end},asetpts=PTS-STARTPTS," if end > start else ""
        length = (end - start) if end > start else float(clip.get("duration", 5.0))
        fade = min(0.08, length / 4)
        filters.append(f"[{index}:v]{span}scale={width}:{height}:force_original_aspect_ratio=decrease,"
                       f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS}[v{index}]")
        filters.append(f"[{index}:a]{aspan}afade=t=in:d={fade},afade=t=out:st={max(length - fade, 0)}:d={fade},"
                       f"aresample=48000[a{index}]")
        labels.append(f"[v{index}][a{index}]")
    filters.append(f"{''.join(labels)}concat=n={len(clips)}:v=1:a=1[v][a]")
    command = ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(filters), "-map", "[v]", "-map",
               "[a]", "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-b:a", "256k", str(target)]
    done = subprocess.run(command, capture_output=True, text=True, env=environment({}))
    job["log"] += done.stderr
    if done.returncode:
        raise RuntimeError("ffmpeg failed")
    job["outputs"] = [relative(target)]
    if params.get("soundtrack"):  # a song of the Music tab laid under the whole export (music.lay)
        said = params["soundtrack"]
        job["progress"] = {"stage": "laying the song under the export", "at": 0, "of": 1}
        mixed = target.with_suffix(".music.mp4")
        laid = music.lay(target, inside(said["song"]), mixed, float(said["music_db"]), log=lambda line: job.update(log=job["log"] + line))
        mixed.replace(target)
        job["soundtrack"] = laid
        job["log"] += f"[tensorfold] soundtrack: {json.dumps(laid)}\n"
        job["progress"] = None


STEP = re.compile(r"\[tensorfold\] (step|voice|ar|dit) (\d+)/(\d+)")
STEP_WORDS = {"step": "picture", "voice": "sound", "ar": "the model writes the song", "dit": "the sound"}  # MiniMax Music 3: ar, dit
RAN_ON = re.compile(r"\[tensorfold\] engine: (\w+)")


def run(job: dict, command: list[str], env: dict, label: str = "") -> bool:
    """Run one script for ``job``, following its progress; False when the job was cancelled meanwhile."""

    process = subprocess.Popen(command, cwd=PACK, env=environment(env), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, start_new_session=True)
    running.update(process=process, id=job["id"])
    for line in process.stdout:
        job["log"] = (job["log"] + line)[-20000:]
        engine = RAN_ON.search(line)
        if engine:
            job["engine"] = engine.group(1)
        found = STEP.search(line)
        if found:
            kind, at, total = found.group(1), int(found.group(2)), int(found.group(3))
            job["progress"] = {"stage": label + STEP_WORDS[kind], "at": at, "of": total}
    code = process.wait()
    running.update(process=None, id=None)
    if job["status"] == "cancelled":
        return False
    if code:
        # the script's own last words say why: a file out of range, a model not installed, memory
        said = [line.strip() for line in job["log"].splitlines() if line.strip() and not STEP.search(line)]
        raise RuntimeError((said[-1][-400:] if said else "the script stopped") + f" (status {code})")
    return True


def take(job: dict, params: dict, label: str = "") -> tuple[bool, dict, list[str]]:
    """One render of a job's clip, on this machine or on the Spark the job was handed to (sparks.py), which leaves the
    clip at the same path: (False when the job was cancelled meanwhile, the environment, the output paths)."""

    worker = job.get("remote") and next((one for one in hub.workers() if one["address"] == job["remote"]), {"address": job["remote"]})
    if worker:
        _, env, outputs = build(job["kind"], params, job["id"], room=False)
        return hub.render(job, worker, params, Path(outputs[0]), label), env, outputs
    command, env, outputs = build(job["kind"], params, job["id"])
    return run(job, command, env, label), env, outputs


def hush(clip: Path) -> None:
    """The clip with its sound track silenced (``silent`` in a job's settings: a Flow clip without music). Asking the
    model for near silence is not enough: on the Mac it laid music of its own over two of three such clips."""

    quiet = clip.with_name(clip.stem + ".quiet" + clip.suffix)
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-map", "0:v:0", "-map", "0:a?", "-c:v", "copy", "-af", "volume=0",
                           "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(quiet)], capture_output=True, text=True, env=environment({}))
    if done.returncode or not quiet.is_file():
        quiet.unlink(missing_ok=True)
        raise RuntimeError("the clip's sound could not be silenced: " + (done.stderr or "").strip()[-200:])
    quiet.replace(clip)


def gated(job: dict, clip: Path) -> bool:
    """Sam Wasserman's QA gate on a clip made to his Sharp Spec; False when the job was cancelled meanwhile."""

    params, takes = job["params"], []
    can = qa.ready()
    if not can["runs"]:
        # said in the job's result, not passed over: a worker without ffmpeg makes the clip, and a machine that has
        # the tools runs the gate on it (POST /api/qa with this app's address and the clip's path)
        job["qa"] = qa.not_run("The QA gate did not run: this machine has no " + " and no ".join(can["missing"]) + ". The clip was made and is not "
                               "checked. A machine that has them can run the gate on it: POST /api/qa {\"from\": this app's address, "
                               f"\"path\": \"{relative(clip)}\", \"prompt\": the prompt}}, or import the take into its Slate shot.")
        return True
    for number in range(1, QA_TAKES + 1):
        if number > 1:
            shutil.copy(clip, HOME / "qa" / f"{job['id']}-take{number - 1}.mp4")  # the failed take is kept
            params["seed"] = int(params.get("seed", 0)) + 1
            if not take(job, params, f"take {number} · ")[0]:
                return False
        job["progress"] = {"stage": "QA gate, take", "at": number, "of": QA_TAKES}
        picture = HOME / "qa" / f"{job['id']}-take{number}.jpg"
        try:
            report = qa.check(clip, params["prompt"], picture, params.get("brain"))
        except Exception as error:  # noqa: BLE001 - the clip is made; a gate that cannot run does not lose it
            job["qa"] = {"passed": None, "takes": takes, "error": str(error)[-300:]}
            break
        takes.append({**report, "take": number, "seed": int(params.get("seed", 0)), "sheet": relative(picture)})
        job["qa"] = {"passed": report["passed"], "takes": takes}
        if report["passed"] or job["status"] == "cancelled":
            break
    return job["status"] != "cancelled"


SHOT_TAKES = 2  # like the QA gate: a clip that fails the shot check is rendered once more at the next seed, when asked


def canvas_of(env: dict, preset: str) -> tuple[int, int]:
    """The size a clip's keyframes were fitted to: the generation canvas, or one of its shape (shot.py only needs
    the shape; the 2x decoder and a crop change the clip's size, not the canvas's proportions)."""

    if env.get("QHD") or env.get("CROP") == "2560x1440":
        return 2560, 1472
    if env.get("TWOK"):
        return 2048, 1152
    if env.get("WIDTH") and env.get("HEIGHT"):
        return int(env["WIDTH"]), int(env["HEIGHT"])
    return PRESETS[preset][1] if preset in PRESETS else (1344, 768)


def shot_checked(job: dict, clip: Path, env: dict) -> bool:
    """The shot check on a single clip (shot.py): were its first and last frame held, and is there a cut inside a
    clip asked for as one shot. The verdict goes beside the clip; with ``retake`` in the job's settings a clip that
    fails is rendered once more at the next seed, and the failed take is kept. False when the job was cancelled."""

    params, takes = job["params"], []
    # the QA gate has its own retake: a clip it rendered twice, or will judge, is not rendered a third time here
    may = bool(params.get("retake")) and "qa" not in job
    for number in range(1, SHOT_TAKES + 1):
        if number > 1:
            kept = HOME / "qa" / f"{job['id']}-shot-take{number - 1}.mp4"  # the failed take is kept
            kept.parent.mkdir(exist_ok=True)
            shutil.copy(clip, kept)
            takes[-1]["kept"] = relative(kept)
            params["seed"] = int(params.get("seed", 0)) + 1
            done, env, _ = take(job, params, f"take {number} · ")
            if not done:
                return False
        job["progress"] = {"stage": "shot check, take", "at": number, "of": SHOT_TAKES if may else 1}
        first = env.get("IMAGE_FILE") or (str(clip.with_suffix(".png")) if job["kind"] == "studio" and clip.with_suffix(".png").is_file() else None)
        try:
            report = shot.check(clip, first, env.get("LAST_FRAME"), canvas_of(env, params.get("preset", "")), shot.one_shot(params.get("prompt") or ""), not SPARK)
        except Exception as error:  # noqa: BLE001 - the clip is made; a check that cannot run does not lose it
            job["shot"] = {"passed": None, "takes": takes, "error": str(error)[-300:]}
            break
        takes.append({**report, "take": number, "seed": int(params.get("seed", 0))})
        job["shot"] = {"passed": report["passed"], "takes": takes}
        if report["passed"] or job["status"] == "cancelled":
            break
        if not may:  # said beside the clip, with the seed a retake would use: the page offers it as a button
            job["shot"]["offer"] = int(params.get("seed", 0)) + 1
            break
    return job["status"] != "cancelled"


def long_take(job: dict) -> bool:
    """A video longer than one clip: render it piece by piece, each opening on the last frame of the one before, and join."""

    params = job["params"]
    total = int(params["total_seconds"])
    pieces = plan(total, int(params.get("segment_seconds", LONGEST)))
    beats = [line.strip() for line in params.get("beats") or [] if line.strip()]
    folder = HOME / "renders" / job["id"]
    folder.mkdir(parents=True, exist_ok=True)
    image, clips = params.get("image"), []
    for index, seconds in enumerate(pieces):
        prompt = params["prompt"]
        if beats:  # one line per piece says what happens in it; the last line holds for the rest
            beat = f"In this part of the video: {beats[min(index, len(beats) - 1)].rstrip('.')}."
            prompt = prompt.replace("[Shot 1] ", f"[Shot 1] {beat} ", 1) if "[Shot 1] " in prompt else f"{beat} {prompt}"
        if index:
            still = folder / f"seg{index:03d}_first.jpg"
            last_frame(clips[-1], still)
            image = relative(still)
        if image and not prompt.startswith("For the target video"):
            prompt = f"{LEAD}\n\n{prompt}"
        target = folder / f"seg{index + 1:03d}.mp4"
        piece = {**params, "prompt": prompt, "image": image, "seconds": seconds, "output": relative(target),
                 "seed": int(params.get("seed", 0)) + index}
        command, env, _ = build("video", piece, job["id"])
        if not run(job, command, env, f"part {index + 1}/{len(pieces)} · "):
            return False
        if not target.is_file():
            raise RuntimeError(f"part {index + 1} wrote no clip")
        clips.append(target)
        job["outputs"] = [relative(c) for c in clips]  # finished parts can be watched while the rest renders
    # join: every part after the first repeats the frame it started from, so that frame is dropped
    job["progress"] = {"stage": "joining", "at": len(clips), "of": len(clips)}
    listing = folder / "parts.txt"
    trimmed = []
    for index, clip in enumerate(clips):
        part = folder / f"join{index + 1:03d}.ts"
        cut = ["-vf", "trim=start_frame=1,setpts=PTS-STARTPTS", "-af", f"atrim=start={1 / FPS},asetpts=PTS-STARTPTS"] if index else []
        done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), *cut, "-c:v", "libx264", "-crf", "17",
                               "-preset", "medium", "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a",
                               "256k", "-ar", "48000", "-f", "mpegts", str(part)], capture_output=True, text=True,
                              env=environment({}))
        if done.returncode:
            job["log"] += done.stderr
            raise RuntimeError("ffmpeg failed while preparing a part")
        trimmed.append(part)
    listing.write_text("".join(f"file '{part}'\n" for part in trimmed))
    final = HOME / "renders" / f"{job['id']}.mp4"
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-t",
                           str(total), "-c", "copy", "-movflags", "+faststart", str(final)], capture_output=True,
                          text=True, env=environment({}))
    for part in trimmed:
        part.unlink(missing_ok=True)
    if done.returncode:
        job["log"] += done.stderr
        raise RuntimeError("ffmpeg failed while joining the parts")
    job["outputs"] = [relative(final)] + [relative(c) for c in clips]
    return True


def perform(job: dict, worker: dict | None = None) -> None:
    """One claimed job to its end: on this machine, or its clip on the Spark it was handed to (``worker``, sparks.py)
    with everything else done here: the frame it opens on is cut here, the clip comes back to its own path, and the
    QA gate, the shot check and the sound follow as on a clip made here."""

    params = job["params"]
    asked = dict(params)
    try:
        # a Drama 3 server kept loaded after a voice take (voice.py) never stands beside a render here, nor MiniMax Music 3
        # kept loaded after a song (music.py): each is unloaded before any other job of this machine's queue
        if job["kind"] not in ("voice", "export", "soundtrack") and worker is None and not job.get("remote"):
            if voice.release(f"a {job['kind']} job needs the GPU"):
                job["log"] += "[tensorfold] Drama 3 was unloaded to give this job its memory\n"
        if job["kind"] not in ("music", "export", "soundtrack") and worker is None and not job.get("remote"):
            if music.release(f"a {job['kind']} job needs the GPU"):
                job["log"] += "[tensorfold] MiniMax Music 3 was unloaded to give this job its memory\n"
        # NVIDIA Nemotron 3.5 Bolt (bolt.py), the Studio's brain engine: unloaded before this job when the job's measured
        # peak does not fit beside it (bolt_need), after the answers in flight; held until the queue is empty
        if job["kind"] not in ("export", "soundtrack") and worker is None and not job.get("remote"):
            job["log"] += bolt.admit(job, lambda resident: bolt_need(job, resident))
        if params.get("first_frame_from"):  # continuity: open on the last frame of an earlier clip
            source = inside(params["first_frame_from"])
            if not source.is_file():
                raise RuntimeError("the clip this one continues from has not been rendered")
            still = HOME / "uploads" / f"{job['id']}_first.jpg"
            last_frame(source, still)
            params["image"] = relative(still)
        if params.get("last_frame_from"):  # end on the first frame of another clip, so the two join
            source = inside(params["last_frame_from"])
            if not source.is_file():
                raise RuntimeError("the clip this one leads into has not been rendered")
            still = HOME / "uploads" / f"{job['id']}_last.png"
            frame_at(source, still, 0)
            params["last_image"] = relative(still)
        if job["kind"] == "export":
            export(job)
        elif job["kind"] == "long":
            if not long_take(job):
                return
        elif job["kind"] == "voice":  # Fish Audio Drama 3 text to speech (voice.py), under the GPU lock
            if not voice_perform(job):
                return
        elif job["kind"] == "music":  # MiniMax Music 3, a song from lyrics and a style (music.py), under the GPU lock
            if not music_perform(job):
                return
        elif job["kind"] == "soundtrack":  # a song laid under a clip (music.lay): ffmpeg only
            if not music_soundtrack(job):
                return
        else:
            done, env, outputs = take(job, params)
            if not done:
                return
            job["outputs"] = [relative(Path(p)) for p in outputs if Path(p).is_file()]
            if not job["outputs"]:
                raise RuntimeError("the script finished without writing its output")
            if job["kind"] == "video" and params.get("preset") == "sharp" and params.get("qa", True):
                if not gated(job, Path(outputs[0])):
                    return
            elif job["kind"] == "video" and params.get("preset") == "sharp":
                job["qa"] = qa.not_run("The QA gate was switched off for this job (qa: false): the clip is not checked.")
            if job["kind"] in ("video", "studio") and params.get("shot_check", True):
                if not shot_checked(job, Path(outputs[0]), env):
                    return
            if job["kind"] == "video" and params.get("silent"):
                hush(Path(outputs[0]))
            if job["kind"] == "video" and (params.get("reference_videos") or params.get("pace") in ("impacts", "flow")):
                try:  # reported beside the clip like the QA gate; it never fails a clip that was made
                    job["pace"] = {**pace.measure(Path(outputs[0])), "asked": params.get("pace") or "auto"}
                except Exception as error:  # noqa: BLE001
                    job["pace"] = {"verdict": "not measured", "note": str(error)[-200:]}
        hub.record(job)  # the clip, its seed and settings, and the Spark that rendered it (sparks/takes.jsonl)
        if job["status"] != "cancelled":
            job["status"] = "done"
    except sparks.Moved:  # the Spark it was on refused it, failed it or stopped answering: it is in the queue again
        params.clear()  # as it was queued: a retake's seed and the frames cut for it are made again
        params.update(asked)
    except Exception as error:  # noqa: BLE001 - a failed job must not stop the queue
        if job["status"] != "cancelled":
            job["status"], job["error"] = "failed", str(getattr(error, "detail", error))
    finally:
        if job["status"] != "queued":
            job["finished"] = time.time()


def work() -> None:
    while True:
        wake.wait()
        with lock:
            job = hub.claim()  # the next job for this machine; on a DGX Spark a worker may have taken others (sparks.py)
            if job is None:
                wake.clear()
        if job is None:
            bolt.queue_empty()  # nothing for this machine: a Bolt unloaded for the queue's jobs may load again
            continue
        perform(job)
        hub.kick.set()


threading.Thread(target=work, daemon=True).start()


def public(job: dict) -> dict:
    out = {k: job.get(k) for k in ("id", "kind", "status", "title", "outputs", "progress", "error", "created",
                                    "started", "finished", "estimate", "engine", "qa", "passes", "pace", "source", "shot",
                                    "negations", "sparks", "spark", "moved", "voice", "music", "soundtrack")}
    # by this machine's clock: the page may be open on another one
    out["elapsed"] = (job.get("finished") or time.time()) - job["started"] if job.get("started") else None
    out["log"] = job["log"][-3000:]
    out["params"] = {k: v for k, v in job["params"].items() if k != "clips"}
    return out


@app.get("/api/state")
def state() -> dict:
    return {"presets": {k: {"label": v[0], "size": v[1]} for k, v in PRESETS.items()},
            "qualities": {k: v[0] for k, v in QUALITIES.items()}, "durations": DURATIONS, "long": LONG,
            "home": str(HOME), "pack": str(PACK), "version": (PACK / "VERSION").read_text().strip(),
            "zig": zig_ready(), "turbo": turbo_ready() and not SPARK, "x2": not SPARK,
            "ref2va": ref2va_ready(), "ref2va_turbo": ref2va_turbo_ready(), "reference_limit": REFERENCE_LIMIT,
            # reference videos and audio: wherever Ref2VA is installed
            "ref_media": ref2va_ready(), "reference_video_limit": REFERENCE_VIDEO_LIMIT,
            "reference_audio_limit": REFERENCE_AUDIO_LIMIT, "reference_total": REFERENCE_TOTAL,
            "text_encoders": sorted(text_encoders()),
            # the picture models installed here, in the order the page offers them (IMAGE_MODELS)
            "image_models": [{"id": k, "label": IMAGE_MODELS[k][0], "note": IMAGE_MODELS[k][1]} for k in image_models()], "sharp": SHARP if "sharp" in PRESETS and "fh8" in QUALITIES else None, "custom": {"sides": CUSTOM_SIDES, "pixels": CUSTOM_PIXELS, "steps": CUSTOM_STEPS},
            "platform": machine()[0], "platform_label": machine()[1], "last_frame": True,
            # every clip length the model takes (lengths.py), and the longest this machine renders at each size
            "lengths": {"fps": FPS, "step": lengths.STEP, "shortest": lengths.SHORTEST, "longest": lengths.LONGEST,
                        "presets": {k: longest_frames(v[2]) for k, v in PRESETS.items()}, "custom": longest_frames({"WIDTH": 1344, "HEIGHT": 768})},
            "ffmpeg": media_tools()["ffmpeg"], "ffmpeg_note": "" if media_tools()["ffmpeg"] else FFMPEG_NOTE,
            "shot_check": True, "negation_reason": negation.REASON,
            # how many DGX Sparks a job may use (sparks.py), this machine counted: a Spark or a Mac at the head
            "sparks": {"supported": True, "limit": sparks.LIMIT},
            # Sam Wasserman's QA gate: whether it can run here, and what is missing when it cannot (qa.py)
            "qa": qa.ready(),
            # Voice: Fish Audio Drama 3 text to speech on this machine (voice.py): MLX on a Mac, CUDA on a DGX Spark
            "voice": voice.state(),
            # Music: MiniMax Music 3 (MiniMax-Music3) on this machine (music.py): MLX on a Mac, ComfyUI on a DGX Spark
            "music": music.state(),
            # NVIDIA Nemotron 3.5 Bolt, the brains' Studio-managed engine (bolt.py): its state, backend and memory
            "bolt": bolt.summary(),
            "engine": ("CUDA · TensorFold int8" if SPARK_ENGINE_LIB.exists() else "CUDA · ComfyUI") if SPARK else ("native Zig engine" if zig_ready() else "MLX engine")}


def numbers(body: dict, keys: tuple) -> dict:
    """The body with each of ``keys`` that is given read as a number (references as a whole one): 400 with the name of
    the first that is not, never a 500."""

    out = dict(body)
    for key in keys:
        value = body.get(key)
        if value in (None, ""):
            continue
        try:
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ValueError
            number = float(value)
            if not math.isfinite(number) or (key == "references" and (number < 0 or number != int(number))):
                raise ValueError
        except ValueError:
            raise HTTPException(400, f"{key} is {'a whole number' if key == 'references' else 'a number'}")
        out[key] = int(number) if key == "references" else number
    return out


def plain_fields(body: dict) -> dict:
    """The composer's form, checked before it is read: fields is an object, the counts are numbers."""

    if not isinstance(body.get("fields", {}), dict):
        raise HTTPException(400, "fields is an object of the shot's words")
    return numbers(body, ("references", "seconds"))


@app.post("/api/compose")
def compose_prompt(body: dict) -> dict:
    body = plain_fields(body)
    if body.get("references") or body.get("referenced"):
        # a picked LoRA with its own caption style (Character Swap, Lineart Anime, Keyframe Animation) writes the prompt
        own = lora_flows.compose(body.get("loras") or (body.get("fields") or {}).get("loras"), body.get("fields", {}))
        if own:
            return {"prompt": own, "flow": True}
        return {"prompt": compose_references(body.get("fields", {}), int(body.get("references") or 0)),
                **negation.check_fields(body.get("fields", {}))}
    try:
        frames = lengths.of({"frames": body.get("frames"), "seconds": body.get("seconds") or 5})
    except ValueError:
        frames = lengths.nearest(5)
    # the prompt is written as the fields say; what they forbid is listed beside it, never taken out (negation.py)
    return {"prompt": compose(body.get("fields", {}), bool(body.get("first_frame")), bool(body.get("last_frame")), frames=frames),
            **negation.check_fields(body.get("fields", {}))}


@app.post("/api/prompt/check")
def check_prompt(body: dict) -> dict:
    """What a finished video prompt forbids by name (negation.py): for a prompt typed or edited by hand, before it is
    queued. Nothing is changed."""

    return negation.check(str(body.get("prompt") or ""))


@app.post("/api/estimate")
def estimate_time(body: dict) -> dict:
    """The time a clip is expected to take (None where none was measured) and, on a DGX Spark, whether it fits the
    machine's memory: {"fits", "reason", "need_mb", ...} from spark/memory_guard.py, shown before the job is queued."""

    body = numbers(body, ("seconds", "references"))
    if body.get("frames") not in (None, ""):
        try:
            lengths.of({"frames": body["frames"]})
        except ValueError as error:
            raise HTTPException(400, str(error))
    room = None
    if SPARK and float(body.get("seconds") or 5) <= LONGEST + 1:
        try:
            chosen, _ = custom(body)
            sized = {**(PRESETS[body["preset"]][2] if body.get("preset") in PRESETS else {}), **chosen}
            count = int(body.get("references") or 0)
            room = memory_of(body, sized, lengths.of({"frames": body.get("frames"), "seconds": body.get("seconds") or 5}), bool(body.get("loras") or body.get("with_lora")),
                             {"pictures": [None] * count, "videos": []} if body.get("referenced") else None)
            room = {key: room.get(key) for key in ("fits", "reason", "need_mb", "error_mb", "left_mb", "floor_mb", "available_mb", "nearest")}
        except (HTTPException, ValueError, KeyError):
            room = None  # the settings are refused for another reason when the job is queued
    if body.get("preset") == "custom" or body.get("steps") not in (None, "") or body.get("loras"):
        return {"seconds": None, "memory": room}  # a LoRA changes which engine renders the clip: no time is measured for it
    return {"seconds": estimate(body.get("preset", "draft"), body.get("quality", "standard"),
                                clip_seconds({"frames": body.get("frames"), "seconds": body.get("seconds", 5)})
                                if float(body.get("seconds") or 5) <= LONGEST + 1 or body.get("frames") else int(body["seconds"]),
                                bool(body.get("text_only")), bool(body.get("referenced")),
                                bool(body.get("ref_turbo")), bool(body.get("ref_media"))), "memory": room}


def clip_estimate(kind: str, params: dict, referenced: bool = False, ref_media: bool = False) -> int | None:
    """The time a job is expected to take on one machine (None where none was measured: a custom size or passes, a LoRA)."""

    if kind not in ("video", "studio", "long") or params.get("preset") == "custom" or params.get("steps") not in (None, "") or params.get("loras"):
        return None
    return estimate(params.get("preset", ""), params.get("quality", "fh8" if SPARK else "standard"), clip_seconds(params),
                    kind == "video" and not params.get("image") and not params.get("first_frame_from"), referenced,
                    bool(params.get("ref_turbo")), ref_media)


WRONG_TYPE = "a setting of this job has the wrong kind of value: a number is a number, a list is a list"


def export_clips(params: dict) -> None:
    """An export's clips, checked when it is queued: a list of {path, in?, out?, duration?} inside the studio folder."""

    clips = params.get("clips")
    if not isinstance(clips, list) or not all(isinstance(clip, dict) and isinstance(clip.get("path"), str) for clip in clips):
        raise HTTPException(400, "an export's clips are a list of clips, each with the path of a file in the studio's folder")
    for clip in clips:
        inside(clip["path"])
        for key in ("in", "out", "duration"):
            if clip.get(key) is not None and (isinstance(clip[key], bool) or not isinstance(clip[key], (int, float))):
                raise HTTPException(400, f"a clip's {key} is a number of seconds")


@app.post("/api/jobs")
def submit(body: dict) -> dict:
    try:
        return queue_job(body)
    except (TypeError, ValueError, AttributeError, KeyError) as error:
        # a setting sent as the wrong type (text for a number, a list for an object): said plainly, not a 500
        print(f"[studio] a job was refused: {type(error).__name__}: {error}", flush=True)
        raise HTTPException(400, WRONG_TYPE) from None


def queue_job(body: dict) -> dict:
    kind = body.get("kind")
    if body.get("params") is not None and not isinstance(body.get("params"), dict):
        raise HTTPException(400, "params is an object of the job's settings")
    if body.get("title") is not None and not isinstance(body.get("title"), str):
        raise HTTPException(400, "title is text")
    params = dict(body.get("params") or {})
    if kind not in ("scout", "video", "studio", "export", "long", "edit") + actions.KINDS + sound.KINDS:
        raise HTTPException(400, "unknown job kind")
    if kind == "long" and (params.get("last_image") or params.get("last_same") or params.get("last_frame_from")):
        raise HTTPException(400, "a last frame is for single clips: a long video is a chain of clips")
    if kind == "long":
        total = int(params.get("total_seconds", 0))
        if not LONGEST < total <= max(LONG):
            raise HTTPException(400, f"a long video is over {LONGEST} seconds and at most {max(LONG) // 60} minutes")
        pieces = plan(total, int(params.get("segment_seconds", LONGEST)))
        build("video", {**params, "seconds": pieces[0], "output": None}, "check")
    if kind not in ("export", "edit", "foley") + actions.KINDS and not (params.get("prompt") or "").strip():
        raise HTTPException(400, "a prompt is needed")
    if kind != "video" and params.get("references"):
        raise HTTPException(400, "reference pictures are for single clips")
    if kind != "video" and (params.get("reference_videos") or params.get("reference_audios")):
        raise HTTPException(400, "reference videos and audio are for single clips")
    if kind == "export":
        export_clips(params)
    missing = without_ffmpeg(kind, params)
    if missing:
        raise HTTPException(400, missing)
    if kind == "export" and params.get("soundtrack"):  # a song under the export: {take, music_db?}
        said = params["soundtrack"] if isinstance(params["soundtrack"], dict) else {"take": params["soundtrack"]}
        song = music.one_take(str(said.get("take") or ""))
        try:
            level = float(said.get("music_db") if said.get("music_db") not in (None, "") else music.MUSIC_DB)
        except (TypeError, ValueError):
            raise HTTPException(400, "music_db is a number")
        if not -30 <= level <= 0:
            raise HTTPException(400, "music_db is between -30 and 0")
        params["soundtrack"] = {"take": song["id"], "song": song["path"], "music_db": level}
    found = references(params) if kind == "video" else {}
    referenced, ref_media = bool(found), bool(found and (found["videos"] or found["audios"]))
    if kind in ("video", "studio", "long") and not referenced and not params.get("quality"):
        params["quality"] = API_QUALITY  # a job that names no quality gets the page's own default, as Home sends it
    if kind in ("video", "studio", "long") and not referenced and not str(params.get("quality", "fh8" if SPARK else "standard")).startswith("fh") and not turbo_ready():
        raise HTTPException(400, "MiniMax H3 Turbo is not installed here (oneshot-setup.sh --turbo adds it): use FastH3, quality fh4, fh8 or fh20")
    job_id = time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    # how many Sparks the job may use, this one counted (sparks.py): the number given, or the one set on the Sparks page
    try:
        chosen = int(body.get("sparks") or hub.config()["sparks"])
    except (TypeError, ValueError):
        raise HTTPException(400, f"sparks is a whole number from 1 to {sparks.LIMIT}")
    if not 1 <= chosen <= sparks.LIMIT:
        raise HTTPException(400, f"sparks is a whole number from 1 to {sparks.LIMIT}")
    said = hub.plan({"sparks": chosen, "kind": kind, "params": params})
    if kind not in ("export", "long"):
        # reject bad settings now, not when the job's turn comes; a clip other Sparks may take is held to their memory
        # guards (asked before it is handed over), not to this one's
        build(kind, params, job_id, room=not (kind in ("video", "studio") and said["used"] > 1))
    job = {"id": job_id, "kind": kind, "status": "queued", "title": body.get("title") or (action_title(kind, params) if kind in actions.KINDS else sound_title(kind, params) if kind in sound.KINDS else kind), "params": params,
           "source": action_source(kind, params) if kind in actions.KINDS else sound_source(kind, params) if kind in sound.KINDS else None,
           "outputs": [], "progress": None, "error": None, "log": "", "created": time.time(), "started": None,
           "finished": None, "estimate": clip_estimate(kind, params, referenced, ref_media)}
    if referenced:  # the passes a reference clip runs: the number set by hand, or the model's 20, or the adapter's 4
        job["passes"] = int(params["steps"]) if params.get("steps") not in (None, "") else 4 if params.get("ref_turbo") else 20
    if kind in ("video", "studio", "long"):
        # said beside the job too, for a prompt that did not come through the wizard; the prompt goes as written
        job["negations"] = negation.check(params.get("prompt") or "")["negations"] or None
    job["sparks"] = {"chosen": chosen, "available": said["available"], "used": said["used"], "says": said["says"]}
    with lock:
        accepting_jobs()
        waiting = sum(1 for one in jobs.values() if one["status"] in ("queued", "running"))
        if waiting >= QUEUE_MAX:  # a page or a peer cannot fill the machine with jobs
            raise HTTPException(429, f"the queue is full ({QUEUE_MAX} jobs waiting or running): let some finish first")
        jobs[job_id] = job
        order.append(job_id)
    wake.set()
    hub.kick.set()
    return public(job)


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    return [public(jobs[i]) for i in reversed(order)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    if job_id not in jobs:
        raise HTTPException(404, "no such job")
    return public(jobs[job_id])


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    if job["status"] in ("queued", "running"):
        job["status"] = "cancelled"
        process = running["process"] if running["id"] == job_id else None
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass  # it ended on its own meanwhile
            if SPARK and job["kind"] not in NOT_ON_COMFY:
                stop_comfy(job)
        hub.kick.set()  # a clip on another Spark is cancelled there by the thread that follows it
    return public(job)


NOT_ON_COMFY = ("voice", "export", "soundtrack", "qa")  # jobs a Spark runs without its ComfyUI server


COMFY_PROMPT = re.compile(r"^\[tensorfold\] comfy prompt ([0-9a-f-]{8,64})$", re.M)


def stop_comfy(job: dict) -> bool:
    """On a DGX Spark the render runs inside the ComfyUI server (spark/comfy.sh), not in the script the job started:
    killing the script leaves the prompt it queued running on the GPU. The runner prints the id of every prompt it
    queues ("[tensorfold] comfy prompt <id>"); the last one is this job's. It is taken out of ComfyUI's queue if it is
    still waiting and interrupted only if it is the one running (ComfyUI's /interrupt with its prompt_id), so another
    job's prompt is never stopped. A job that never reached ComfyUI sends nothing. Best effort, 2 s each; the job's
    log says what happened."""

    found = COMFY_PROMPT.findall(job.get("log") or "")
    if not found:
        job["log"] = (job["log"] + "[studio] cancelled before it reached ComfyUI: nothing to stop there\n")[-20000:]
        return True
    prompt_id, base = found[-1], os.environ.get("SPARK_COMFY_URL", "http://127.0.0.1:" + os.environ.get("SPARK_COMFY_PORT", "8190"))

    def post(path: str, body: dict) -> int:
        request = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=2) as answer:
            return answer.status

    try:
        post("/queue", {"delete": [prompt_id]})  # if it is still waiting
        status = post("/interrupt", {"prompt_id": prompt_id})  # if it is the one running; otherwise ComfyUI does nothing
        said, stopped = f"ComfyUI was asked to stop this job's prompt {prompt_id[:8]} (it answered {status})", True
    except urllib.error.HTTPError as error:
        said, stopped = f"ComfyUI did not stop the prompt: it answered {error.code}", False
    except (OSError, ValueError) as error:
        said, stopped = f"ComfyUI did not answer the stop ({getattr(error, 'reason', error)}): it may still be rendering", False
    job["log"] = (job["log"] + f"[studio] cancelled: {said}\n")[-20000:]
    return stopped


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    suffix = Path(file.filename or "image.png").suffix.lower()
    # .safetensors: a LoRA for the hub; sound files: reference audio
    if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".safetensors") + VIDEO_SUFFIXES + AUDIO_SUFFIXES:
        raise HTTPException(400, "images (png, jpg, webp), videos (mp4, mov) and sound (wav, mp3, m4a, aac, flac, ogg) only")
    target = HOME / "uploads" / f"{uuid.uuid4().hex[:10]}{suffix}"
    with open(target, "wb") as handle:
        shutil.copyfileobj(file.file, handle)
    return {"path": relative(target)}


@app.post("/api/frame")
def take_frame(body: dict) -> dict:
    """A still from a Library clip, saved to the Library: ``at`` is "last", "first" or seconds into the clip. For a
    first or last frame of a new clip (continue from this clip, end on this clip, any moment in between)."""

    source = inside(str(body.get("path") or ""))
    if not source.is_file() or source.suffix.lower() not in VIDEO_SUFFIXES:
        raise HTTPException(400, "a frame is taken from a video in the studio's folder")
    if not media_tools()["ffmpeg"]:
        raise HTTPException(400, f"taking a frame of a clip needs ffmpeg, which is not installed on this machine: {FFMPEG_HOW}")
    at = body.get("at", "last")
    if at not in ("last", "first") and (isinstance(at, bool) or not isinstance(at, (int, float, str))):
        raise HTTPException(400, 'at is "first", "last" or a number of seconds into the clip')
    if isinstance(at, str) and at not in ("last", "first"):
        try:
            at = float(at)
        except ValueError:
            raise HTTPException(400, 'at is "first", "last" or a number of seconds into the clip')
    if isinstance(at, float) and not math.isfinite(at):
        raise HTTPException(400, 'at is "first", "last" or a number of seconds into the clip')
    name = f"{source.stem[:40]}_{'last' if at == 'last' else 'first' if at == 'first' else str(at).replace('.', 's')}_{uuid.uuid4().hex[:6]}.png"
    target = HOME / "uploads" / name
    try:
        if at == "last":
            last_frame(source, target)
        else:
            seconds = 0.0 if at == "first" else float(at)
            length, _ = media_seconds(str(source))
            if seconds < 0 or (length and seconds > length):
                raise HTTPException(400, f"the clip is {length:.1f} seconds long")
            frame_at(source, target, seconds)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
        raise HTTPException(400, "no frame could be read there")
    if not target.is_file():
        raise HTTPException(400, "no frame could be read there")
    return {"path": relative(target)}


@app.get("/api/library")
def library() -> list[dict]:
    """Every clip, still, voice line, song and sound effect under the studio folder, newest first."""

    found = []
    for folder, kinds in (("renders", (".mp4",)), ("exports", (".mp4",)), ("scouts", (".png",)), ("sound", (".mp4",)),
                          ("uploads", (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov")), ("projects", (".mp4",)), ("slate", (".mp4",)),
                          ("agent", (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov"))):
        for path in (HOME / folder).rglob("*"):
            if path.suffix.lower() in kinds and not hidden(path.relative_to(HOME)) and path.is_file():
                try:
                    stat = path.stat()
                except OSError:  # a file taken out of the Library while it is being listed
                    continue
                found.append({"path": relative(path), "kind": "video" if path.suffix.lower() in (".mp4", ".mov")
                              else "image", "folder": folder, "modified": stat.st_mtime,
                              "size": stat.st_size})
    found += library_sounds()
    found = sorted(found, key=lambda item: -item["modified"])[:400]
    made = hub.takes()  # which Spark rendered a clip, and at which seed (sparks.py)
    for item in found:
        if item["path"] in made:
            item.update(spark=made[item["path"]].get("spark"), seed=made[item["path"]].get("seed"))
    return found


SOUND_FILES = (".wav", ".mp3", ".flac", ".m4a", ".ogg")


def library_sounds() -> list[dict]:
    """Voice lines and songs (their kept takes) and sound effects (the sound folder): kind "audio", `sound` says which."""

    out, seen = [], set()
    for which, module in (("voice", globals().get("voice")), ("music", globals().get("music"))):
        try:
            takes = module.takes() if module else []
        except Exception:  # a take folder that cannot be read leaves the rest of the Library alone
            takes = []
        for take in takes:
            path = HOME / str(take.get("path") or "")
            if path.suffix.lower() in SOUND_FILES and path.is_file() and relative(path) not in seen:
                seen.add(relative(path))
                words = str(take.get("text") or take.get("caption") or take.get("title") or "")
                out.append({"path": relative(path), "kind": "audio", "sound": which, "folder": which, "title": " ".join(words.split())[:80],
                            "modified": path.stat().st_mtime, "size": path.stat().st_size})
    for path in (HOME / "sound").rglob("*") if (HOME / "sound").is_dir() else []:
        if path.suffix.lower() in SOUND_FILES and path.is_file() and not hidden(path.relative_to(HOME)) and relative(path) not in seen:
            try:  # a sound effect's or a foley's words, from its take file (sound.py)
                said = json.loads(path.with_name(path.stem + ".take.json").read_text())
                words = str(said.get("prompt") or ("Foley for " + Path(str(said.get("source") or "")).name if said.get("kind") == "foley" else ""))
            except (OSError, ValueError, AttributeError):
                words = ""
            out.append({"path": relative(path), "kind": "audio", "sound": "sfx", "folder": "sound", "title": " ".join(words.split())[:80],
                        "modified": path.stat().st_mtime, "size": path.stat().st_size})
    return out


@app.delete("/api/library")
def discard(path: str) -> dict:
    """Take a clip or still out of the library: it is moved to the studio's trash folder, not erased."""

    target = inside(path)
    if not target.is_file() or relative(target).split("/")[0] not in ("renders", "exports", "scouts", "uploads", "projects", "slate") \
            or target.suffix.lower() not in (".mp4", ".mov", ".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(400, "not a library file")
    with lock:
        if any(job["status"] in ("queued", "running") and relative(target) in json.dumps(job["params"]) for job in jobs.values()):
            raise HTTPException(400, "a queued or running job uses this file")
    kept = HOME / "trash" / (time.strftime("%Y%m%d-%H%M%S-") + relative(target).replace("/", "__"))
    kept.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(target), str(kept))
    return {"trash": relative(kept)}


def seconds_of(data: dict) -> float:
    """A clip's duration from ffprobe's answer: 0 when it gave none or something that is not a number."""

    try:
        return float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


@app.get("/api/probe")
def probe(path: str) -> dict:
    target = inside(path)
    if not media_tools()["ffprobe"]:
        raise HTTPException(400, f"measuring a clip needs ffprobe, which comes with ffmpeg and is not installed on this machine: {FFMPEG_HOW}")
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "stream=codec_type,width,height,nb_frames:format=duration", "-of", "json", str(target)],
                             capture_output=True, text=True, env=environment({}), timeout=30)  # in a request: never without a limit
    except subprocess.TimeoutExpired:
        raise HTTPException(400, "the file could not be read in 30 seconds")
    try:
        data = json.loads(out.stdout or "{}")
    except ValueError:
        raise HTTPException(400, "the file could not be read")
    if not isinstance(data, dict):
        raise HTTPException(400, "the file could not be read")
    streams = data.get("streams") or []
    stream = next((one for one in streams if one.get("codec_type") == "video"), {})
    # sound: whether the file has an audio track (a reference video's own sound, a reference audio clip)
    return {"width": stream.get("width"), "height": stream.get("height"), "frames": stream.get("nb_frames"),
            "duration": seconds_of(data),
            "sound": any(one.get("codec_type") == "audio" for one in streams)}


def qa_run(job: dict) -> None:
    """The QA gate as a job of its own: on a clip of this studio folder, or on one fetched from another machine's app
    first. It uses no GPU, so it does not wait in the render queue."""

    params = job["params"]
    try:
        can = qa.ready()
        if not can["runs"]:
            raise RuntimeError("the QA gate cannot run on this machine: it has no " + " and no ".join(can["missing"]))
        prompt, seed, path = params.get("prompt") or "", params.get("seed"), params.get("path") or ""
        if params.get("from"):
            base = remote.address(params["from"])
            if params.get("job"):  # the clip, its prompt and its seed as that app's job has them
                theirs = remote.job(base, str(params["job"]))
                if theirs.get("status") != "done" or not theirs.get("outputs"):
                    raise RuntimeError(f"job {params['job']} on {base} is {theirs.get('status')}, with no clip to check")
                path = path or theirs["outputs"][0]
                prompt = prompt or (theirs.get("params") or {}).get("prompt") or ""
                seed = seed if seed is not None else (theirs.get("params") or {}).get("seed")
            job["progress"] = {"stage": "fetching the clip from " + base, "at": 0, "of": 1}
            clip = remote.fetch(base, path, HOME / "qa" / "fetched" / f"{job['id']}{Path(path).suffix.lower()}")
        else:
            clip = inside(path)
            if not clip.is_file():
                raise RuntimeError("no such clip in the studio's folder")
        job["outputs"] = [relative(clip)]
        job["progress"] = {"stage": "QA gate, take", "at": 1, "of": 1}
        picture = HOME / "qa" / f"{job['id']}-take1.jpg"
        report = qa.check(clip, prompt, picture, params.get("brain"))
        job["qa"] = {"passed": report["passed"], "takes": [{**report, "take": 1, "seed": int(seed or 0), "sheet": relative(picture)}],
                     "from": params.get("from") or None}
        if job["status"] != "cancelled":
            job["status"] = "done"
    except Exception as error:  # noqa: BLE001 - the reason goes beside the job
        if job["status"] != "cancelled":
            job["status"], job["error"] = "failed", str(getattr(error, "detail", error))[-400:]
    finally:
        job["finished"] = time.time()


@app.post("/api/qa")
def qa_gate(body: dict) -> dict:
    """Run Sam Wasserman's QA gate on a clip: ``path`` in this studio folder, or, with ``from`` (another machine's app,
    `http://host:port`), a ``path`` in its folder or one of its finished jobs (``job``: the clip, prompt and seed are
    read from it). ``prompt`` is the prompt the clip was made from (its spoken lines are what the audio gate listens
    for). The answer is a job to poll: its ``qa`` is the gate's report, as on a clip rendered here."""

    if not (body.get("path") or (body.get("from") and body.get("job"))):
        raise HTTPException(400, "give the clip: a path, or another app's address (from) with a path or a job id")
    try:
        if body.get("from"):
            remote.remember(remote.address(str(body["from"])), body.get("key"))  # its access key: in memory, never in the job
    except ValueError as error:
        raise HTTPException(400, str(error))
    can = qa.ready()
    if not can["runs"]:
        raise HTTPException(400, "the QA gate cannot run on this machine: it has no " + " and no ".join(can["missing"]))
    job_id = time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    params = {key: body[key] for key in ("path", "from", "job", "prompt", "seed", "brain") if body.get(key) not in (None, "")}
    job = {"id": job_id, "kind": "qa", "status": "running", "title": body.get("title") or "QA gate: " + str(params.get("path") or params.get("job")),
           "params": params, "source": None, "outputs": [], "progress": None, "error": None, "log": "", "created": time.time(),
           "started": time.time(), "finished": None, "estimate": None}
    with lock:
        accepting_jobs()
        jobs[job_id] = job
        order.append(job_id)
    threading.Thread(target=qa_run, args=(job,), daemon=True).start()
    return public(job)


@app.get("/api/shot/window")
def shot_window(path: str) -> dict:
    """The longest stretch of a clip without a cut (shot.py): for an editor or Production to trim a take to, whatever
    made the clip. A finished job carries the same under ``shot.takes[].window``."""

    target = inside(path)
    if not target.is_file():
        raise HTTPException(404, "no such file")
    try:
        return shot.longest_take(target)
    except LookupError as error:
        raise HTTPException(400, f"the clip's frames cannot be read on this machine: {error}")
    except Exception as error:  # noqa: BLE001 - not a video, or a damaged one
        raise HTTPException(400, f"not a clip whose frames can be read: {str(error)[-200:]}")


def project_file(project_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", project_id):
        raise HTTPException(400, "bad project id")
    return HOME / "projects" / project_id / "project.json"


@app.get("/api/projects")
def projects() -> list[dict]:
    out = []
    for file in sorted((HOME / "projects").glob("*/project.json"), key=lambda f: -f.stat().st_mtime):
        try:
            data = json.loads(file.read_text())
            data["id"]
        except (ValueError, KeyError, TypeError):
            continue  # a file that was cut off while being written must not take the Production tab down
        out.append({"id": data["id"], "name": data.get("name", data["id"]), "segments": len(data.get("segments", [])),
                    "modified": file.stat().st_mtime})
    return out


@app.post("/api/projects")
def new_project(body: dict) -> dict:
    project_id = time.strftime("p%m%d-%H%M%S")
    data = {"id": project_id, "name": body.get("name") or "Untitled", "preset": "small" if SPARK else "draft", "quality": "fh8" if SPARK else "standard",
            "segments": [], "timeline": []}
    file = project_file(project_id)
    file.parent.mkdir(parents=True, exist_ok=True)
    fresh = file.with_suffix(".json.new")
    fresh.write_text(json.dumps(data, indent=1))
    fresh.replace(file)
    return data


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict:
    file = project_file(project_id)
    if not file.is_file():
        raise HTTPException(404, "no such project")
    try:
        return json.loads(file.read_text())
    except (OSError, ValueError):
        raise HTTPException(404, "the project file could not be read")


@app.put("/api/projects/{project_id}")
def save_project(project_id: str, body: dict) -> dict:
    file = project_file(project_id)
    if not file.is_file():
        raise HTTPException(404, "no such project")
    body["id"] = project_id
    fresh = file.with_suffix(".json.new")  # written beside it and swapped in: a reader never sees half a file
    fresh.write_text(json.dumps(body, indent=1))
    fresh.replace(file)
    return body


@app.get("/api/wait")
def wait(ms: int = 1000):
    """Holds a response open; a page asked for with ?shot=1 loads an image from here so that a headless
    screenshot is taken after the app has drawn."""

    time.sleep(min(max(ms, 0), 10000) / 1000)
    return JSONResponse({"waited": ms})


# what an agent's harness wrote while it worked (the whole transcript, its errors, its tool servers): this computer only
AGENT_RECORDS = ("stderr.txt", "stream.jsonl", "log.txt", "mcp.json")


@app.get("/api/credits")
def credits():
    """CREDITS.md and THIRD_PARTY_NOTICES.md as they are in this checkout: the page's Credits shows the repo's own
    files, so the app and the repo credit the same people (tests/test_credits.py)."""
    def read(name):
        try:
            return (PACK / name).read_text(encoding="utf-8")
        except OSError:
            return ""
    return {"credits": read("CREDITS.md"), "notices": read("THIRD_PARTY_NOTICES.md")}


@app.get("/files/{path:path}")
def files(path: str, request: Request):
    if "\0" in path or hidden(path):
        raise HTTPException(404, "no such file")
    target = inside(path)
    if not target.is_file():
        raise HTTPException(404, "no such file")
    parts = target.relative_to(HOME.resolve()).parts
    if parts[0] == "agent" and parts[-1] in AGENT_RECORDS:
        agents.local(request)
    return FileResponse(target)


@app.exception_handler(HTTPException)
async def errors(_, error: HTTPException):
    return JSONResponse({"error": error.detail}, status_code=error.status_code)


@app.exception_handler(RequestValidationError)
async def malformed(_, error: RequestValidationError):
    """A body or parameter FastAPI could not read: said plainly, with where it was wrong, and never with what was sent
    (FastAPI's own answer echoes the whole body back)."""

    where = [".".join(str(part) for part in one.get("loc", ())[:4])[:80] for one in error.errors()[:5]]
    said = [str(one.get("msg") or "")[:120] for one in error.errors()[:5]]
    return JSONResponse({"error": "this request is not what the route takes: " + "; ".join(f"{w}: {m}" for w, m in zip(where, said)),
                         "detail": [{"loc": w, "msg": m} for w, m in zip(where, said)]}, status_code=422)


import lineart  # noqa: E402  (a line-art copy of a clip, for a line-art LoRA)
import lora_flows  # noqa: E402  (what a hub LoRA needs of a clip)
import loras  # noqa: E402  (Keys and Wasserman LoRA Hub, beside this file)
lora_router, lora_resolve, lora_lead = loras.attach(HOME, PACK, SPARK)
app.include_router(lora_router)
import hf_gate  # noqa: E402  (gated weights: the person's Hugging Face sign-in and the licence they accept, beside this file)
app.include_router(hf_gate.attach(lambda request: agents.local(request, True)))
app.include_router(lineart.attach(HOME))
import picture_edit  # noqa: E402  (a picture re-drawn by the image model, with the hub's image LoRAs)
edit_router, edit_build = picture_edit.attach(HOME, PACK, SPARK, lora_resolve, inside, relative,
                                              lambda: [{"id": k, "label": IMAGE_MODELS[k][0], "note": IMAGE_MODELS[k][1]} for k in image_models()])
app.include_router(edit_router)
import pace  # noqa: E402  (whether a clip's sound and motion agree, beside this file)
import negation  # noqa: E402  (what a prompt forbids by name, shown before a job is queued, beside this file)
import shot  # noqa: E402  (a clip's keyframes held and no cut inside it, beside this file)
import qa  # noqa: E402  (Sam Wasserman's QA gate, beside this file)
import remote  # noqa: E402  (another machine's Studio app, read over its own API, beside this file)
import slate  # noqa: E402  (Wasserman Slate AI Studio, beside this file)
import voice  # noqa: E402  (Voice: Fish Audio Drama 3 text to speech, run locally, beside this file)
import bolt  # noqa: E402  (NVIDIA Nemotron 3.5 Bolt, the brains' Studio-managed engine, beside this file)


def local_running() -> bool:
    """Whether a job of the queue renders on this machine now (bolt.make_room leaves ComfyUI alone then)."""

    with lock:
        return any(job["status"] == "running" and not job.get("remote") for job in jobs.values())


bolt.busy = local_running


def enqueue(kind: str, title: str, params: dict, estimate: int | None = None) -> dict:
    """A job that is not a clip (a voice take) into the queue: it renders on this machine, one at a time with the rest."""

    job_id = time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    job = {"id": job_id, "kind": kind, "status": "queued", "title": title, "params": params, "source": None, "outputs": [],
           "progress": None, "error": None, "log": "", "created": time.time(), "started": None, "finished": None,
           "estimate": estimate, "sparks": {"chosen": 1, "available": 1, "used": 1, "says": ""}}
    with lock:
        accepting_jobs()
        waiting = sum(1 for one in jobs.values() if one["status"] in ("queued", "running"))
        if waiting >= QUEUE_MAX:  # a page or a peer cannot fill the machine with jobs
            raise HTTPException(429, f"the queue is full ({QUEUE_MAX} jobs waiting or running): let some finish first")
        jobs[job_id] = job
        order.append(job_id)
    wake.set()
    hub.kick.set()
    return public(job)


voice_router, voice_perform = voice.attach(HOME, run, enqueue, inside, relative, media_seconds, qa)
app.include_router(voice_router)
import music  # noqa: E402  (Music: MiniMax Music 3, songs from lyrics and a style, run locally, beside this file)
music_router, music_perform, music_soundtrack = music.attach(HOME, run, enqueue, inside, relative, media_seconds, qa)
app.include_router(music_router)



def slate_room(plan: dict, lora: bool) -> dict:
    """Whether a Slate shot's render plan fits this machine's memory: the same check a queued job gets (memory_of)."""

    chosen, _ = custom(plan) if plan.get("preset") == "custom" else ({}, False)
    sized = {**(PRESETS[plan["preset"]][2] if plan.get("preset") in PRESETS else {}), **chosen}
    return memory_of(plan, sized, int(plan["frames"]), lora, {"pictures": plan["references"], "videos": []} if plan.get("references") else None)


app.include_router(slate.attach(HOME, DURATIONS, compose, compose_references, REFERENCE_LIMIT,
                                lambda: {"ref2va": ref2va_ready(), "ref2va_turbo": ref2va_turbo_ready()}, slate_room))
import agents  # noqa: E402  (Brains and agents: the user's own CLI sign-ins and MCP tools, beside this file)
app.include_router(agents.attach(HOME))


@app.get("/api/bolt/state")
def bolt_state() -> dict:
    """NVIDIA Nemotron 3.5 Bolt, the brains' Studio-managed engine (bolt.py): state, backend, memory, why not."""

    return bolt.state()


@app.post("/api/bolt/load")
def bolt_load(request: Request) -> dict:
    """The Brains page's Load: starts the engine aside (about 40 s) and answers at once with its state."""

    agents.local(request)
    try:
        return bolt.load_async()
    except RuntimeError as error:
        raise HTTPException(409, str(error))


@app.post("/api/bolt/unload")
def bolt_unload(request: Request) -> dict:
    """The Brains page's Unload: the engine stopped now and its memory given back (refused while it answers)."""

    agents.local(request)
    try:
        return bolt.unload()
    except RuntimeError as error:
        raise HTTPException(409, str(error))
import motion  # noqa: E402  (Motion Previs: a Python port of Sam Wasserman's Motion Previs Studio analysis, beside this file)
app.include_router(motion.attach(HOME, PACK, SPARK))
import actions  # noqa: E402  (Turnaround sheet, Upscale and Remove a person: Library actions, beside this file)
action_router, action_build, action_title, action_source = actions.attach(HOME, PACK, SPARK, inside, relative)
import sound  # noqa: E402  (sound effects and foley: Stable Audio Open and MMAudio, run locally, beside this file)
sound_router, sound_build, sound_title, sound_source = sound.attach(HOME, PACK, SPARK, inside, relative, held_room, media_seconds)
app.include_router(action_router)
app.include_router(sound_router)
import clear  # noqa: E402  (finished jobs and their files into the trash folder, and back, beside this file)
app.include_router(clear.attach(HOME, jobs, order, lock)[0])
import sparks  # noqa: E402  (worker Sparks behind this queue: a job's "sparks" number, beside this file)


def about_this_spark() -> dict:
    """What this app is and has, for a Studio on another Spark that hands clips to it (/api/spark/self)."""

    listing = next(route.endpoint for route in lora_router.routes if route.path == "/api/loras" and "GET" in route.methods)
    info = memory_guard.meminfo()
    memory = {"available_mb": info.get("MemAvailable"), "total_mb": info.get("MemTotal"), "room_mb": max(memory_guard.room_mb() or 0, voice.room_mb() or 0) or None,
              "floor_mb": memory_guard.floor_mb(info.get("MemTotal"))} if SPARK and info else {}
    with lock:
        active = sum(1 for job in jobs.values() if job["status"] in ("queued", "running"))
    # what it renders with its own settings: a head on a Mac hands a Spark only these (sparks.capable)
    renders = {"presets": [name for name, (_, _, env) in PRESETS.items() if not SPARK or not {"X2", "TWOK", "QHD"} & set(env)] + ["custom"],
               "qualities": [name for name in QUALITIES if name.startswith("fh") or (turbo_ready() and not SPARK)]}
    if not SPARK:  # a Mac's models are not in ComfyUI's folders: what its own row on the Sparks tab says it renders
        renders.update(fasth3=Path(os.environ.get("FASTH3_DIR", Path.home() / "h3-models/FastH3-8-Step-V2")).is_dir(), image=bool(image_models()))
    return sparks.about(HOME, hub.code(), state(), [one["id"] for one in listing() if one.get("installed")], active, memory, SPARK_REF2VA.parent, renders)


def clip_time(kind: str, params: dict) -> int | None:
    """One clip's time for the Sparks estimate; None for settings that would be refused or have no measured time."""

    try:
        return clip_estimate(kind, params, bool(params.get("references") or params.get("reference_videos")),
                             bool(params.get("reference_videos") or params.get("reference_audios")))
    except (HTTPException, ValueError, TypeError, KeyError):
        return None


hub = sparks.Hub(HOME, PACK, SPARK, jobs, order, lock, wake, about_this_spark, inside, perform, clip_time)
app.include_router(hub.router())
import updates  # noqa: E402  (Git source updates after an idle, graceful app shutdown)


def update_busy():
    with hub.mutex:
        workers_active = any(worker.get("working") or worker.get("job") for worker in hub.live.values())
    return (update_writes > 0 or workers_active
            or any(job["status"] in ("queued", "running") for job in jobs.values())
            or running.get("process") is not None
            or bool(bolt.summary().get("in_flight"))  # an answer of the Studio's Bolt engine (an agent turn) in flight
            or Path(os.environ.get("STUDIO_GPU_LOCK", Path.home() / "studio-gpu.lock.d")).exists())


updater = updates.Manager(PACK, HOME, lock, update_busy)
hub.follow = updater.automatic  # automatic updates here bring the workers to this code too (sparks.Hub.catch_up)


def fits_here(job: dict) -> bool:
    """Whether a queued clip fits this machine's memory now (sparks.Hub.claim leaves one that does not to a worker)."""

    if job["kind"] not in ("video", "studio"):
        return True
    try:
        build(job["kind"], job["params"], job["id"])
    except HTTPException:
        return False
    return True


hub.fits_here = fits_here
app.include_router(updater.router())
app.router.on_startup.append(updater.start)
app.router.on_shutdown.append(updater.stop.set)
if hasattr(app.router, "on_shutdown"):
    app.router.on_shutdown.append(hub.close)
    app.router.on_shutdown.append(bolt.shutdown)  # the Studio's Bolt engine ends with the app (its leash also sees the pipe close)  # the tunnels to the workers are this app's: closed with it (and at exit, sparks.py)
import telemetry  # noqa: E402  (this machine's readings for the status bar: GPU, memory, disk, queue, beside this file)
app.include_router(telemetry.attach(HOME, jobs, order, lock, memory_guard, engines=lambda: {"bolt": bolt.summary()}))
import suite  # noqa: E402  (the filmmaker suite's files in and out: imports, timeline, dailies, beside this file)
app.include_router(suite.attach(HOME, jobs, lock, PRESETS, app))
app.include_router(settings.attach(HOME))
try:
    import nodes  # noqa: E402  (Nodes: a canvas of the studio's own jobs, run in order, beside this file)
except ImportError:  # an install without it keeps the rest of the app
    nodes = None
if nodes is not None:
    app.include_router(nodes.attach(HOME, enqueue, jobs, lock, inside, relative))
try:
    import discover  # noqa: E402  (models, weights and LoRAs found anywhere on this machine: Machine → Models & weights)
except ImportError:
    discover = None
if discover is not None:
    app.include_router(discover.attach(HOME, Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio")).expanduser()))


@app.middleware("http")
async def fresh_pages(request, call_next):
    """The page and its scripts are checked again on every load, so an update shows without a forced reload."""
    response = await call_next(request)
    if not request.url.path.startswith(("/api/", "/files/")):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


app.mount("/", StaticFiles(directory=HERE / "static", html=True), name="static")

if not media_tools()["ffmpeg"]:
    print("[studio] " + FFMPEG_NOTE, flush=True)

if __name__ == "__main__":
    import uvicorn

    if os.environ.get("STUDIO_PIDFILE"):  # a worker Spark's app, started by another Spark's Studio: how that one finds it again
        Path(os.environ["STUDIO_PIDFILE"]).write_text(str(os.getpid()))
    hub.start()

    # Update authorization needs the actual socket peer. Tailscale Serve forwards the browser's IP,
    # but its authenticated local proxy is the peer allowed to manage this loopback installation.
    web_server = uvicorn.Server(uvicorn.Config(app, host=os.environ.get("HOST", "127.0.0.1"),
                               port=int(os.environ.get("PORT", "7870")), log_level="warning", proxy_headers=False,
                               timeout_graceful_shutdown=10))  # open connections (event streams) never hold a restart

    def restart() -> None:
        telemetry.STOPPING.set()  # every /api/events stream ends at its next tick, so the server can close
        web_server.should_exit = True

    updater.restart = restart
    web_server.run()
