"""Bolt: NVIDIA Nemotron 3.5 Bolt, the Studio's own brain, run on this machine as a Studio-managed engine.

The brains (agents.py) and Slate ask a local OpenAI-compatible server. Bolt is that server, and the Studio runs it the
way it runs Fish Audio Drama 3 (voice.py) and MiniMax Music 3 (music.py): a separate process outside the app, started
on the first request, held while it is used, unloaded after ``BOLT_IDLE_S`` idle seconds and, when a job of this
machine's queue needs its memory, before that job. Four backends, ``BOLT_BACKEND=cuda|metal|llamacpp|external``:

- ``cuda`` (a DGX Spark, the default on Linux where it is built): TensorFold's native engine, ``tensorfold-native
  serve``, with Mia's Bolt lane backend (libbolt.so and NVIDIA's LiLiCorr drafter), from MiaAI-Lab's
  Nemotron-3.5-Bolt-TensorFold-DGX-Spark recipe on TensorFold 46a08c18 and its patches; ``spark/build-bolt.sh``
  puts it in ``$PREFIX/bolt``. No Docker at run time.
- ``metal`` (a Mac, the default there when built): TensorFold's Metal port of the same engine (fork branch
  ``nemotron-bolt-metal``, not released yet), the same ``tensorfold-native serve`` command line.
- ``llamacpp`` (a Mac, when ``BOLT_BACKEND`` says so): llama.cpp's ``llama-server`` with NVIDIA's GGUF, until the
  Metal engine lands. Never chosen by itself (a Mac may already run Bolt in a llama.cpp service of its own).
- ``external``: a server the Studio does not manage (the address in Settings → Connections), as before.

The engine serves the local brain when the brains' address is empty or ``studio:bolt`` (``ENDPOINT``); an address
typed in Settings is an external server, unchanged.

Lifecycle (``Engine``), mirroring voice.Served:

- states: ``unloaded`` → ``loading`` → ``loaded`` → ``unloaded``; ``held`` while a job of the queue has the memory;
- **loaded on demand**: the first brain or agent request (``request``) starts it on a loopback port and waits for its
  ``/health``; the brains' address follows it (agents.py, slate.find_local);
- **released before a render, when the machine needs the memory** (``admit``, called at the top of server.perform):
  the job's peak is asked of the memory guard's estimate (server.bolt_need) with Bolt resident; on a Spark with too
  little room it is unloaded and held until the queue is empty; on a 256 GB Mac, or a head whose clips render on worker
  Sparks, it stays;
- **agent turns**: a request in flight (``request``: an answer of the local harness's turn, a brain question) is never
  cut. A release waits for it to end, then unloads; a request that comes while Bolt is held waits for the queue (the
  local harness's turn) or is refused at once with the reason (a brain question from a page). While a turn is open
  (``turn``) the idle unload does not run, so a turn that waits ten minutes for its clip keeps its model loaded unless
  that clip needs the memory;
- **idle unload** after ``BOLT_IDLE_S`` (600) seconds without a request; ``never`` (or 0) keeps it, for a head that
  never renders;
- **memory**: before it loads, ``NEED_GB`` must be free; while it is loaded, its memory counts as a job's
  (``room_mb``, server.held_room), since the job unloads it; a kill-guard reads the available memory (MemAvailable on
  Linux, vm_stat on a Mac) and kills the engine's process group under ``BOLT_GUARD_GB`` (8 GB);
- it dies with the app: it runs under a leash (bolt_leash.py) that ends it when the app's pipe closes.

It never takes the studio's GPU lock: a brain is not a render, it is held to the machine's memory by measurement, and a
loaded brain must not keep the updater or a render script waiting for the lock.

The weights are NVIDIA's (nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB and its LiLiCorr drafter) under the OpenMDW
License Agreement 1.1: use, modification and redistribution are free, commercial use included; a copy that is passed
on keeps the agreement and NVIDIA's notices; no warranty. The CUDA engine is Mia's (MiaAI-Lab, MIT) on TensorFold (Ash
Hart and the TensorFold contributors, Apache 2.0).
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import voice  # mem_available_gb, settle, located, found_hint: the same readings Drama 3's engine uses

SPARK = sys.platform.startswith("linux")
MAC = sys.platform == "darwin"
PACK = Path(__file__).resolve().parent.parent
PREFIX = Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio")).expanduser()
ENDPOINT = "studio:bolt"  # the brains' address that means "the Studio's own engine"
NAME = "NVIDIA Nemotron 3.5 Bolt"
LABEL = NAME + " (Studio engine)"
SERVED = "nvidia-nemotron-3.5-bolt"  # the model id the engine answers with (agents.LOCAL_NAMES knows it)
MODEL_REPO, MODEL_REVISION = "nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB", "a326822af111290f2af5ac59ef4b5040347dabc4"
DRAFT_REPO, DRAFT_REVISION = "nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-LiLiCorr", "4c6a5613b6d887194a3a5493e3a18a5b3930bb86"
LICENCE = ("NVIDIA Nemotron 3.5 Bolt runs on this machine under the OpenMDW License Agreement 1.1: use, modification and "
           "redistribution are free, commercial use included; a copy passed on keeps the agreement and NVIDIA's notices. "
           "The CUDA engine is MiaAI-Lab's recipe (MIT) on TensorFold (Apache 2.0).")
CREDIT = "NVIDIA Nemotron 3.5 Bolt · engine by MiaAI-Lab on TensorFold"
HEALTH_TIMEOUT_S = 900  # the most a load may take (40 s warm on a GB10; minutes from a cold disk)
RELEASE_WAIT_S = 600  # the most a release waits for an answer in flight (agents.LOCAL_ANSWER_TIMEOUT)
# The machine's peak for a job on a Mac, measured on an M5 Ultra (README: the 2.8.0 demo renders 29.0 and 33.0 GB, FastH3
# alone 23 to 27 GB; images 15 to 28 GiB; MiniMax H3 Turbo 103 GiB; Drama 3 47 to 52 GB; MiniMax Music 3 27.6 to 33 GB).
# A kind without a line here takes the largest (Turbo's).
MAC_PEAK_GB = {"video": 33.0, "studio": 33.0, "long": 33.0, "scout": 28.0, "image": 28.0, "edit": 28.0, "voice": 52.0,
               "music": 33.0, "turbo": 103.0}
MAC_PEAK_UNKNOWN_GB = 103.0


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def idle_seconds(text: str | None) -> float:
    """``BOLT_IDLE_S``: seconds, or ``never`` / 0 for a server kept until the app ends (a head that never renders)."""

    text = (text or "").strip().lower()
    if text in ("never", "off", "0", "inf"):
        return 0.0
    try:
        return max(0.0, float(text)) if text else 600.0
    except ValueError:
        return 600.0


class Held(RuntimeError):
    """Bolt is unloaded while a job of the queue has its memory: a request that may not wait is refused with this."""


class Unloaded(RuntimeError):
    """A load stopped because a job of the queue took the memory meanwhile (the request waits for it, then loads)."""


class Engine:
    """A Studio-managed model server: an OpenAI-compatible HTTP server on a loopback port, run under the leash and the
    kill-guard, loaded on demand, unloaded when idle or when a job needs its memory (see the module's notes)."""

    name, label = "", ""
    load_s = 40.0  # what the page says a load takes
    NEED_GB = 43.0  # free memory wanted before it loads
    RESIDENT_GB = 28.0  # what it holds once loaded (measured; the job's room counts it)
    tools = True  # takes OpenAI tool calls: the local harness (agents.drive) can run on it

    def __init__(self) -> None:
        self.idle_s = idle_seconds(os.environ.get("BOLT_IDLE_S"))
        self.guard_gb = float(os.environ.get("BOLT_GUARD_GB", 8))
        self.mutex = threading.RLock()  # a load, an unload and a release never run at once
        self.changed = threading.Condition(self.mutex)  # in flight, the hold and the state, waited on
        self.process: subprocess.Popen | None = None
        self.port = 0
        self.phase = "unloaded"  # unloaded, loading, loaded
        self.in_flight = 0  # requests being answered now
        self.turns = 0  # local-harness turns open now (no idle unload meanwhile)
        self.hold: str = ""  # why it may not load now: the queue job that has its memory, "" for none
        self.loaded_at = self.last_used = 0.0
        self.before_gb = -1.0  # available just before it started: what a job has once it is unloaded
        self.held_gb: float | None = None  # what loading it took from the machine (before less after, settled)
        self.load_s_measured: float | None = None
        self.killed = ""
        self.error = ""  # why the last load failed
        self.log: list[str] = []
        self.events: list[dict] = []
        self.known: tuple[float, str] | None = None
        self.idler: threading.Thread | None = None

    # -- what the backend is --

    def argv(self, port: int) -> list[str]:
        raise NotImplementedError

    def environment(self) -> dict:
        return dict(os.environ)

    def present(self) -> str:
        """What is missing for this backend ("" when its files are all here): files only, no process."""
        raise NotImplementedError

    def check(self) -> str:
        """Why it cannot run here ("" when it can): files, then memory; looked at every 10 s at most."""

        if self.loaded():
            return ""
        if self.known and time.time() - self.known[0] < 10:
            return self.known[1]
        why = self.present()
        if not why:
            free = voice.mem_available_gb()
            if 0 <= free < self.NEED_GB and not self.holds_room():
                why = (f"{NAME} needs about {self.NEED_GB:.0f} GB of free memory to load and this machine has {free:.0f} GB "
                       "available now (a render or a model outside the Studio holds the rest).")
        self.known = (time.time(), why)
        return why

    def holds_room(self) -> bool:
        """Whether the Studio itself holds memory that a load would get back (a ComfyUI with models, Drama 3, Music)."""
        return SPARK and voice.comfy_up()

    # -- the server --

    def loaded(self) -> bool:
        return self.phase == "loaded" and self.process is not None and self.process.poll() is None

    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def note(self, what: str, **more) -> None:
        self.events = (self.events + [{"at": time.time(), "what": what, **more}])[-20:]

    def state(self) -> str:
        if self.phase == "loading":
            return "loading"
        if self.loaded():
            return "loaded"
        return "held" if self.hold else "unloaded"

    def status(self) -> dict:
        loaded = self.loaded()
        return {"state": self.state(), "backend": self.name, "label": self.label, "loaded": loaded,
                "base": self.base() if loaded else None, "port": self.port if loaded else None,
                "memory_gb": round(self.held_gb, 1) if loaded and self.held_gb is not None else (self.RESIDENT_GB if loaded else None),
                "resident_gb": self.RESIDENT_GB, "need_gb": self.NEED_GB, "load_s": self.load_s_measured or self.load_s,
                "idle_unload_s": self.idle_s or None, "idle_for_s": round(time.time() - self.last_used, 1) if loaded and self.last_used else None,
                "in_flight": self.in_flight, "turns": self.turns, "held_for": self.hold or None, "error": self.error or None,
                "killed": self.killed or None, "events": self.events[-6:]}

    def read(self, process: subprocess.Popen) -> None:
        for line in process.stderr:
            self.log = (self.log + [line.rstrip("\n")])[-200:]

    def guard(self, process: subprocess.Popen) -> None:
        """The available memory four times a second (once on a Mac: vm_stat is a process); under the floor the
        engine's whole process group is killed."""

        while process.poll() is None:
            free = voice.mem_available_gb()
            if 0 <= free < self.guard_gb:
                self.killed = f"available memory {free:.1f} GB < {self.guard_gb:g} GB: the memory guard killed {NAME} (pid {process.pid})"
                kill_group(process)
                return
            time.sleep(0.25 if SPARK else 1.0)

    def healthy(self) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as answer:
                return answer.status == 200
        except Exception:  # noqa: BLE001 - not up yet (refused, 503 while loading)
            return False

    def idle(self) -> None:
        """Unloads after ``idle_s`` without a request, while no turn is open; notes an engine that ended by itself."""

        while True:
            time.sleep(5)
            with self.mutex:
                if self.process is not None and self.process.poll() is not None and self.phase == "loaded":
                    self.stop("the engine ended (" + (self.killed or f"status {self.process.returncode}") + ")")
                    continue
                if (self.loaded() and self.idle_s and not self.in_flight and not self.turns
                        and time.time() - self.last_used >= self.idle_s):
                    self.stop(f"idle for {self.idle_s:g} s")

    def start(self, stopped=lambda: False) -> None:
        """Loaded and answering on its port, or a RuntimeError saying why not. Called with the mutex held."""

        if self.loaded():
            return
        if self.process is not None:
            self.stop("the engine had ended (" + (self.killed or f"status {self.process.returncode}") + ")")
        why = self.present()
        if why:
            raise RuntimeError(why)
        self.make_room()
        self.port = int(os.environ.get("BOLT_PORT") or 0) or free_port()
        self.phase, self.log, self.killed, self.error, self.held_gb = "loading", [], "", "", None
        self.changed.notify_all()
        began, self.before_gb = time.time(), voice.mem_available_gb()
        try:
            self.process = subprocess.Popen([sys.executable, "-I", str(Path(__file__).with_name("bolt_leash.py")), "--", *self.argv(self.port)],
                                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                            bufsize=1, env=self.environment(), start_new_session=True)
            threading.Thread(target=self.read, args=(self.process,), daemon=True).start()
            threading.Thread(target=self.guard, args=(self.process,), daemon=True).start()
            if self.idler is None:
                self.idler = threading.Thread(target=self.idle, daemon=True)
                self.idler.start()
            process = self.process
            while not self.healthy():
                if self.process is not process or self.phase != "loading":  # released meanwhile (a job took the memory)
                    raise Unloaded(self.hold or "unloaded while loading")
                if process.poll() is not None:
                    tail = [line for line in self.log if line.strip()][-3:]
                    raise RuntimeError(f"{NAME} stopped while loading: " + (self.killed or f"status {self.process.returncode}")
                                       + (" (" + " / ".join(tail)[-400:] + ")" if tail else ""))
                if stopped():
                    raise InterruptedError("cancelled while loading")
                if time.time() - began > HEALTH_TIMEOUT_S:
                    raise RuntimeError(f"{NAME} did not answer in {HEALTH_TIMEOUT_S // 60} minutes")
                self.changed.wait(0.5)
            self.phase = "loaded"
            self.loaded_at = self.last_used = time.time()
            self.load_s_measured = round(self.loaded_at - began, 1)
            voice.settle()
            after = voice.mem_available_gb()
            if self.before_gb >= 0 and after >= 0:
                self.held_gb = max(0.0, self.before_gb - after)
            self.note("loaded", seconds=self.load_s_measured, held_gb=round(self.held_gb, 1) if self.held_gb is not None else None,
                      port=self.port)
            self.changed.notify_all()
        except Unloaded:
            raise
        except BaseException as error:
            self.error = str(error)[:400]
            self.stop("it failed to load")
            raise

    def make_room(self) -> None:
        """Enough free memory to load, or a RuntimeError saying why not. On a Spark a studio ComfyUI that holds models
        while the queue is idle is stopped first (the next render starts it again), as Drama 3 does."""

        free = voice.mem_available_gb()
        if free < 0 or free >= self.NEED_GB:
            return
        if SPARK and voice.comfy_up() and not busy():
            subprocess.run(["bash", str(PACK / "spark" / "comfy.sh"), "stop"], capture_output=True, text=True, timeout=30,
                           env={**os.environ, "PREFIX": str(PREFIX)})
            for _ in range(60):
                if not voice.comfy_up():
                    break
                time.sleep(1)
            voice.settle()
            free = voice.mem_available_gb()
            self.note("stopped ComfyUI before loading", available_gb=round(free, 1))
        if 0 <= free < self.NEED_GB:
            raise RuntimeError(f"{NAME} needs about {self.NEED_GB:.0f} GB of free memory to load and this machine has "
                               f"{free:.0f} GB available")

    def stop(self, why: str) -> None:
        """The server ended (its leash's pipe closed, then the group killed after 25 s), its memory back."""

        with self.mutex:
            process, self.process = self.process, None
            was = self.phase
            self.phase = "unloaded"
            if process is not None:
                if process.poll() is None:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=25)
                    except subprocess.TimeoutExpired:
                        kill_group(process)
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            pass
                voice.settle()
                if was != "unloaded":
                    self.note("unloaded", why=why, available_gb=round(voice.mem_available_gb(), 1))
            self.changed.notify_all()

    # -- requests, turns and the queue --

    @contextlib.contextmanager
    def request(self, stopped=lambda: False, wait: bool = True, say=None):
        """One request to the engine: loaded first (waiting for a held engine when ``wait``, else ``Held``), counted in
        flight so that no release cuts it, the base address yielded."""

        with self.mutex:
            told = loading = False
            while True:
                if self.hold:
                    if not wait:
                        raise Held(f"{NAME} is unloaded while {self.hold} has this machine's memory: ask again when it is done")
                    if say and not told:
                        say(f"[{NAME} is unloaded while {self.hold} has the memory: waiting for it]")
                        told = True
                elif self.loaded():
                    break
                elif self.phase != "loading":  # unloaded and free to load: this request loads it
                    if say and not loading:
                        say(f"[loading {NAME} ({self.label.split(' · ')[0]}, about {self.load_s_measured or self.load_s:.0f} s)]")
                        loading = True
                    try:
                        self.start(stopped)
                    except Unloaded:  # a job took the memory while it loaded: wait for it like any held request
                        continue
                    break
                if stopped():
                    raise InterruptedError("cancelled while waiting for the memory")
                self.changed.wait(1.0)  # held, or another request is loading it
            self.in_flight += 1
            self.last_used = time.time()
            base = self.base()
        try:
            yield base
        finally:
            with self.mutex:
                self.in_flight -= 1
                self.last_used = time.time()
                self.changed.notify_all()

    @contextlib.contextmanager
    def turn(self):
        """A local-harness turn: no idle unload while it is open (its answers are ``request``s of their own)."""

        with self.mutex:
            self.turns += 1
        try:
            yield
        finally:
            with self.mutex:
                self.turns -= 1
                self.last_used = time.time()

    def release(self, why: str, wait_s: float = RELEASE_WAIT_S) -> bool:
        """Held for ``why`` (no new request starts), then unloaded once the requests in flight are answered (at most
        ``wait_s``; after that it is unloaded all the same and those answers fail). True when it was loaded."""

        with self.mutex:
            self.hold = why
            self.changed.notify_all()
            if not self.loaded() and self.phase != "loading":
                return False
            ends = time.time() + wait_s
            while self.in_flight and time.time() < ends:
                self.changed.wait(0.5)
            if self.in_flight:
                self.note("an answer was cut", why=f"{why} waited {wait_s:g} s")
            self.stop(why)
            return True

    def unhold(self, why: str = "") -> None:
        with self.mutex:
            if self.hold:
                self.note("free to load again", why=why or "the queue is empty")
            self.hold = ""
            self.changed.notify_all()


def kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass


# ---------- the backends ----------

def snapshot(repo: str, revision: str, there) -> Path | None:
    """A Hugging Face cache snapshot of ``repo`` at ``revision`` (~/.cache/huggingface or HF_HOME), when ``there``."""

    root = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")).expanduser() / "hub"
    found = root / ("models--" + repo.replace("/", "--")) / "snapshots" / revision
    return found if there(found) else None


def has_checkpoint(folder: Path) -> bool:
    return (folder / "config.json").is_file() and (folder / "model.safetensors.index.json").is_file()


def has_drafter(folder: Path) -> bool:
    return (folder / "model.safetensors").is_file() and (folder / "config.json").is_file()


MODELS = Path(os.environ.get("BOLT_MODELS", Path(os.environ.get("SPARK_MODELS", Path.home() / "spark-studio-models")) if SPARK
                             else Path.home() / "bolt-models")).expanduser()


class Cuda(Engine):
    """TensorFold's native engine with Mia's Bolt lane backend on a DGX Spark (``tensorfold-native serve``, Zig + CUDA,
    libbolt.so, NVIDIA's LiLiCorr drafter; no Python, no Docker at run time), from ``$PREFIX/bolt``
    (spark/build-bolt.sh). The Studio's settings, trimmed from Mia's defaults to sit beside renders: 4 streams, a
    65,536-token window each, 2 GiB of prompt cache, pictures and video on, thinking off. Measured on .4 (GB10) with
    those settings in Mia's image: 28 GB resident, about 40 s to answer with the files in the page cache, 57 to 60
    tokens a second for one stream."""

    name, label = "cuda", "TensorFold CUDA · DGX Spark (Mia's Bolt recipe)"
    load_s = 40.0
    # free memory wanted before it loads: Mia's start check for these settings (weights 23, vision 3, recurrent state
    # 2, attention 1, TensorFold's reserve 10, 2, prompt cache 2 GiB)
    NEED_GB = 43.0
    RESIDENT_GB = 28.0
    folder_name = "bolt"

    def __init__(self) -> None:
        super().__init__()
        here = PREFIX / self.folder_name
        self.bin = Path(os.environ.get("BOLT_BIN", here / "native" / "bin" / "tensorfold-native")).expanduser()
        self.lib = Path(os.environ.get("BOLT_LIB", self.bin.parent.parent / "lib")).expanduser()
        self.model = Path(os.environ.get("BOLT_MODEL", MODELS / "Nemotron-3.5-Bolt-24GB")).expanduser()
        self.draft = Path(os.environ.get("BOLT_DRAFT_MODEL", MODELS / "Nemotron-3.5-Bolt-24GB-LiLiCorr")).expanduser()
        self.cache = Path(os.environ.get("BOLT_KERNEL_CACHE", here / "cache")).expanduser()
        self.parallel = int(os.environ.get("BOLT_PARALLEL", 4))
        self.context = int(os.environ.get("BOLT_CONTEXT", 65536))
        self.prompt_cache = os.environ.get("BOLT_PROMPT_CACHE_GIB", "2")
        self.vision = os.environ.get("BOLT_VISION", "1") != "0"
        self.thinking = os.environ.get("BOLT_THINKING", "0") == "1"
        self.drafts = os.environ.get("BOLT_DRAFTS", "1") != "0"

    def present(self) -> str:
        if self.name == "cuda" and not SPARK:
            return "The CUDA backend runs on a DGX Spark (Linux, CUDA)."
        self.bin = voice.located("bolt-binary", self.bin, lambda p: os.access(p, os.X_OK))
        if not os.access(self.bin, os.X_OK):
            return (f"No {NAME} engine at {self.bin} (bash {self.build_hint()}; BOLT_BIN names another)." + voice.found_hint("bolt-binary"))
        if self.name == "cuda" and not (self.lib / "libbolt.so").is_file():
            return f"No libbolt.so in {self.lib} (the same build puts it there; BOLT_LIB names another folder)."
        if not has_checkpoint(self.model):
            self.model = snapshot(MODEL_REPO, MODEL_REVISION, has_checkpoint) or voice.located("bolt-checkpoint", self.model, has_checkpoint)
        if not has_checkpoint(self.model):
            return (f"No {NAME} checkpoint at {self.model} ({self.setup_hint()}: NVIDIA's 24 GB checkpoint at {MODEL_REVISION[:8]}; "
                    "BOLT_MODEL names another folder)." + voice.found_hint("bolt-checkpoint"))
        if self.drafts and not has_drafter(self.draft):
            self.draft = snapshot(DRAFT_REPO, DRAFT_REVISION, has_drafter) or self.draft
        if self.drafts and not has_drafter(self.draft):
            return (f"No LiLiCorr drafter at {self.draft} ({self.setup_hint()}; BOLT_DRAFT_MODEL names another folder, "
                    "BOLT_DRAFTS=0 serves without it).")
        return ""

    def build_hint(self) -> str:
        return "spark/build-bolt.sh, or oneshot-setup-spark.sh --bolt"

    def setup_hint(self) -> str:
        return "bash oneshot-setup-spark.sh --bolt"

    def environment(self) -> dict:
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = str(self.lib) + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        self.cache.mkdir(parents=True, exist_ok=True)
        env.update(HF_HUB_OFFLINE="1", TENSORFOLD_NO_UPDATE_CHECK="1", BOLT_KV=os.environ.get("BOLT_KV", "bf16"),
                   BOLT_KV_PIN=os.environ.get("BOLT_KV_PIN", "0"), CUDA_CACHE_PATH=str(self.cache / "nv"),
                   TENSORFOLD_MEMORY_RESERVE_GIB=os.environ.get("TENSORFOLD_MEMORY_RESERVE_GIB", "10"),
                   TENSORFOLD_BOLT_LIB=str(self.lib / "libbolt.so"), TENSORFOLD_LIBTURBOJPEG=str(self.lib / "libturbojpeg.so.0"))
        if self.drafts:
            env["BOLT_DRAFT"] = str(self.draft)
        else:
            env.pop("BOLT_DRAFT", None)
        return env

    def argv(self, port: int) -> list[str]:
        out = [str(self.bin), "serve", str(self.model), "--name", SERVED, "--host", "127.0.0.1", "--port", str(port),
               "--context", str(self.context), "--parallel", str(self.parallel), "--max-tokens", "32768",
               "--temperature", "1.0", "--top-p", "0.95", "--prompt-cache-gib", str(self.prompt_cache),
               "--thinking" if self.thinking else "--no-thinking"]
        if not self.drafts:
            out.append("--no-drafts")
        if self.vision:
            out += ["--vision", "--vision-max-images", "64", "--vision-max-videos", "4", "--vision-image-tokens", "262144",
                    "--vision-video-tokens", "131072"]
        return out


class Metal(Cuda):
    """TensorFold's Metal port of the Bolt engine on a Mac (fork branch ``nemotron-bolt-metal``, the same
    ``tensorfold-native serve`` command line), from ``$PREFIX/bolt-metal``. Not released yet: until it is built there,
    a Mac runs Bolt through llama.cpp (``Llamacpp``, BOLT_BACKEND=llamacpp). Swapping it in is building it into that
    folder (or naming it with BOLT_BIN) and setting BOLT_BACKEND=metal, or leaving it unset: nothing else changes."""

    name, label = "metal", "TensorFold Metal · Apple Silicon"
    # not measured yet: the CUDA engine's figures until the Metal port has its own
    folder_name = "bolt-metal"

    def present(self) -> str:
        if not MAC:
            return "The Metal backend runs on a Mac (Apple Silicon)."
        return super().present()

    def build_hint(self) -> str:
        return "TensorFold's nemotron-bolt-metal branch, built into $PREFIX/bolt-metal (not released yet)"

    def setup_hint(self) -> str:
        return "bash oneshot-setup.sh --bolt"

    def environment(self) -> dict:
        env = super().environment()
        for key in ("CUDA_CACHE_PATH", "TENSORFOLD_BOLT_LIB", "TENSORFOLD_LIBTURBOJPEG"):
            env.pop(key, None)
        env["DYLD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH", str(self.lib))
        return env


class Llamacpp(Engine):
    """llama.cpp's ``llama-server`` with NVIDIA's GGUF of Bolt, on a Mac until TensorFold's Metal engine lands: the
    flags of the Mac's own Bolt service (Metal, flash attention, 65,536 tokens, its Jinja chat template for tool calls,
    reasoning off, one stream). ``BOLT_LLAMACPP_BIN`` and ``BOLT_GGUF`` name the binary and the file; by default
    ~/bolt/llama.cpp/build/bin/llama-server and the GGUF in ~/bolt/models (the copy whose NVFP4 Mamba tensors were
    rewritten as Q8_0 first: Metal has no NVFP4 matmul)."""

    name, label = "llamacpp", "llama.cpp · Metal (until TensorFold's Metal engine)"
    load_s = 25.0
    NEED_GB = 40.0  # the 24 GB file, a 65,536-token window and the guard's 8 GB beside it
    RESIDENT_GB = 27.0

    def __init__(self) -> None:
        super().__init__()
        self.bin = Path(os.environ.get("BOLT_LLAMACPP_BIN", Path.home() / "bolt" / "llama.cpp" / "build" / "bin" / "llama-server")).expanduser()
        self.gguf = Path(os.environ["BOLT_GGUF"]).expanduser() if os.environ.get("BOLT_GGUF") else self.find_gguf()
        self.context = int(os.environ.get("BOLT_CONTEXT", 65536))
        self.thinking = os.environ.get("BOLT_THINKING", "0") == "1"

    @staticmethod
    def find_gguf() -> Path:
        folder = Path.home() / "bolt" / "models"
        found = sorted(folder.glob("*Bolt*mamba-q8*.gguf")) + sorted(folder.glob("*Bolt*.gguf"))
        return found[0] if found else folder / "NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB.gguf"

    def present(self) -> str:
        if not os.access(self.bin, os.X_OK):
            return (f"No llama-server at {self.bin} (llama.cpp built with Metal; BOLT_LLAMACPP_BIN names another). "
                    "TensorFold's Metal engine replaces it when it lands (BOLT_BACKEND=metal).")
        if not self.gguf.is_file():
            return (f"No {NAME} GGUF at {self.gguf} (nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-GGUF; BOLT_GGUF names "
                    "another file).")
        return ""

    def argv(self, port: int) -> list[str]:
        return [str(self.bin), "-m", str(self.gguf), "--alias", SERVED, "--host", "127.0.0.1", "--port", str(port),
                "-ngl", "999", "-fa", "on", "-c", str(self.context), "--jinja", "--reasoning", "on" if self.thinking else "off",
                "--parallel", "1", "--no-webui"]


class External:
    """A server the Studio does not manage: nothing is started or stopped; the brains ask the address in Settings."""

    name, label = "external", "a server the Studio does not manage"
    phase = "unloaded"
    hold = ""

    def present(self) -> str:
        return "BOLT_BACKEND=external: the brains ask the local server named in Settings → Connections."

    check = present

    def loaded(self) -> bool:
        return False

    def state(self) -> str:
        return "external"

    def status(self) -> dict:
        return {"state": "external", "backend": self.name, "label": self.label, "loaded": False}

    def release(self, why: str, wait_s: float = 0) -> bool:
        return False

    def unhold(self, why: str = "") -> None:
        return None


BACKENDS = {"cuda": Cuda, "metal": Metal, "llamacpp": Llamacpp, "external": External}
_engine = None
_chosen_why = ""
busy = lambda: False  # noqa: E731 - whether the queue runs a job here (server.py sets it)


def choose() -> tuple[type, str]:
    """``BOLT_BACKEND`` when set; else TensorFold's engine where it is built (CUDA on a DGX Spark, Metal on a Mac), else
    external (nothing managed, as before). llama.cpp is never chosen by itself: a Mac that already runs Bolt in its own
    llama.cpp service would load a second copy beside it; ``BOLT_BACKEND=llamacpp`` asks for it."""

    told = (os.environ.get("BOLT_BACKEND") or "").strip().lower()
    if told in BACKENDS:
        return BACKENDS[told], ""
    if SPARK:
        why = Cuda().present()
        return (External, why) if why else (Cuda, "")
    if MAC:
        why = Metal().present()
        return (External, why + " BOLT_BACKEND=llamacpp runs it through llama.cpp until then.") if why else (Metal, "")
    return External, "no Studio engine for this platform"


def engine():
    global _engine, _chosen_why
    if _engine is None:
        route, _chosen_why = choose()
        _engine = route()
    return _engine


def managed() -> bool:
    """Whether the Studio runs Bolt here: a managed backend whose files are all present."""

    one = engine()
    return not isinstance(one, External) and one.present() == ""


def serves(endpoint: str) -> bool:
    """Whether the brains' address means the Studio's engine: empty or ``studio:bolt``, with the engine managed here."""

    given = (endpoint or "").strip().lower()
    if given == ENDPOINT:
        return not isinstance(engine(), External)
    return not given and managed()


def request(stopped=lambda: False, wait: bool = True, say=None):
    return engine().request(stopped, wait, say)


def turn():
    one = engine()
    return one.turn() if hasattr(one, "turn") else contextlib.nullcontext()


def room_mb() -> int | None:
    """While the engine is loaded: the memory this machine had before it loaded (MB), which a job of the queue gets
    back, since the job unloads it when it needs it (as voice.room_mb). None otherwise."""

    one = _engine
    if one is None or not one.loaded() or getattr(one, "before_gb", -1) < 0:
        return None
    return int(one.before_gb * 1024)


def held_mb() -> int:
    """What the loaded engine holds (MB): measured at its load, else its RESIDENT_GB; 0 when not loaded."""

    one = _engine
    if one is None or not one.loaded():
        return 0
    return int(1024 * (one.held_gb if one.held_gb is not None else one.RESIDENT_GB))


def admit(job: dict, need) -> str:
    """At the top of a job of this machine's queue (server.perform): whether Bolt may stay beside it. ``need(resident)``
    answers, from the memory guard's estimate, whether the job fits with ``resident`` GB held by Bolt (None: not
    measured, it goes). Loaded and not fitting: unloaded now (after the answers in flight) and held until the queue is
    empty. Unloaded and not fitting if it loaded: held, so a brain question does not load it beside the render. Returns
    what the job's log says ("" for nothing)."""

    one = engine()
    if isinstance(one, External):
        return ""
    title = f"a {job.get('kind', 'job')} job"
    resident = (held_mb() / 1024) if one.loaded() else one.RESIDENT_GB
    fits = need(resident)
    if fits is not None and fits[0]:
        one.unhold(f"{title} fits beside it")
        return (f"[tensorfold] {NAME} stays loaded beside this job: {fits[1]}\n" if one.loaded() else "")
    why = fits[1] if fits is not None else "this kind of job has no measured peak"
    if one.release(title + " (" + job.get("id", "") + ")" if job.get("id") else title):
        return f"[tensorfold] {NAME} was unloaded to give this job its memory ({why}); it loads again when the queue is empty\n"
    return ""


def queue_empty() -> None:
    """The queue has nothing for this machine: a held engine may load again (on the next request)."""

    one = _engine
    if one is not None:
        one.unhold("the queue is empty")


def release(why: str) -> bool:
    one = _engine
    return bool(one is not None and one.release(why))


def state() -> dict:
    one = engine()
    why = "" if isinstance(one, External) and not os.environ.get("BOLT_BACKEND") else one.check()
    out = {"name": NAME, "label": LABEL, "available": not isinstance(one, External) and not why, "why": why or None,
           "managed": not isinstance(one, External), "fallback": _chosen_why or None, "licence": LICENCE, "credit": CREDIT,
           "endpoint": ENDPOINT, "model": SERVED, **one.status()}
    out["backend_label"] = one.label
    return out


def load_async() -> dict:
    """The page's Load: starts a load aside (it takes about 40 s) and answers at once."""

    one = engine()
    if isinstance(one, External):
        raise RuntimeError(one.present())
    if one.hold:
        raise Held(f"{NAME} is unloaded while {one.hold} has this machine's memory: load it when that is done")
    why = one.present()
    if why:
        raise RuntimeError(why)

    def run() -> None:
        try:
            with one.request(wait=False):
                pass
        except Exception:  # noqa: BLE001 - the reason is kept in the engine's status (error)
            pass

    if not one.loaded() and one.phase != "loading":
        threading.Thread(target=run, daemon=True).start()
        deadline = time.time() + 2
        while one.phase != "loading" and not one.loaded() and not one.error and time.time() < deadline:
            time.sleep(0.05)
    return state()


def unload(why: str = "unloaded from the page") -> dict:
    one = engine()
    if not isinstance(one, External):
        with one.mutex:
            if one.in_flight:
                raise RuntimeError(f"{NAME} is answering now (an agent turn or a brain question): unload it when that is done")
            one.stop(why)
    return state()


def shutdown() -> None:
    one = _engine
    if one is not None and not isinstance(one, External) and one.process is not None:
        one.stop("the app is stopping")


def summary() -> dict:
    """For the telemetry (Machine room, the status bar): state, backend and memory, cheap."""

    one = _engine
    if one is None or isinstance(one, External):
        return {"state": "external" if isinstance(one, External) else "unloaded", "managed": False}
    loaded = one.loaded()
    return {"state": one.state(), "managed": True, "backend": one.name, "label": one.label,
            "memory_gb": round(one.held_gb if one.held_gb is not None else one.RESIDENT_GB, 1) if loaded else None,
            "held_for": one.hold or None, "in_flight": one.in_flight}


__all__ = ["ENDPOINT", "Engine", "Cuda", "Metal", "Llamacpp", "External", "Held", "engine", "managed", "serves", "request",
           "turn", "room_mb", "held_mb", "admit", "queue_empty", "release", "state", "load_async", "unload", "summary"]
