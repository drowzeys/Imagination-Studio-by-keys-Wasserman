"""Voice: Fish Audio Drama 3 text to speech, run on this machine. Built with Fish Audio.

A take is one job in the app's queue (one at a time, so it never runs beside a MiniMax H3 render: Drama 3 alone peaks
at about 50 GB of memory on a Mac, 42 GB on a DGX Spark). Two engines, both TensorFold's, each a separate process:

- ``mlx`` (Apple Silicon, the default on a Mac): the engine's own command line in its own environment, one process
  a take, which holds the studio's GPU lock folder (~/studio-gpu.lock.d, the one the render scripts outside the app
  take with ``mkdir``) for the time it runs:

      $DRAMA3_VENV/bin/python -I -m tensorfold.drama3 --text-file t.txt [--ref-audio a.wav --ref-text "..."] --out x.wav

- ``cuda`` (DGX Spark, the default on Linux): ``drama3-generate --serve 1``, TensorFold's native Zig + CUDA port, as a
  persistent server loaded on the first take, holding the GPU lock while loaded, unloaded after five idle minutes and
  at once before any other job of this machine's queue (``Cuda`` below says how).

Nothing here calls Fish Audio's API. The weights are Fish Audio Materials under the Fish Audio Community License v1.0:
research, non-commercial and small-company commercial use; a hosted text-to-speech or voice-cloning service needs a
licence of its own from Fish Audio; a real person's voice is cloned only with that person's consent, which a take with a
reference clip must state (``consent``).

Measured on a Mac Studio M5 Ultra (TensorFold drama3-mlx f623fb1a, 8-bit experts): about 63 frames a second while
decoding against 21.5 for real time, so a take is made in about 0.4 times its length, plus about 3 s to load the
model; 47 to 52 GB of memory at the peak. On a DGX Spark (TensorFold drama3-cuda 81173e6c): 29 to 31 frames a
second (about 0.7 times the take's length), first sound after about 0.27 s (0.47 s with a clip to clone), 18 s to load
once, 41.6 GB at the peak.
"""

from __future__ import annotations

import difflib
import json
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

try:
    import discover  # Models & weights: what was found elsewhere on this machine and linked
except ImportError:  # pragma: no cover - imported as app.voice from outside the app folder
    try:
        from app import discover
    except ImportError:
        discover = None

router = APIRouter()
HOME: Path = Path.home() / "ImaginationStudio"
qa = None  # app/qa.py, given by attach
SPARK = sys.platform.startswith("linux")
FRAME_RATE = 21.5  # the Fish codec's frames per second of audio
SAMPLING = {"temperature": (1.0, 0.1, 2.0), "top_p": (0.9, 0.1, 1.0), "top_k": (30, 1, 200)}  # Fish's defaults, then the range
TEXT_LIMIT = 6000  # characters in one take (long text is split at sentence ends by the engine)
REFERENCE_SECONDS = (2.0, 60.0)  # a voice to clone: Fish suggests 10 to 30 seconds of clean speech
MEMORY_GB = 64  # a Mac with less does not run it: the model peaks at about 52 GB
LOCK = Path(os.environ.get("STUDIO_GPU_LOCK", Path.home() / "studio-gpu.lock.d"))
CREDIT = "Built with Fish Audio"
LICENCE = ("Fish Audio Drama 3 runs on this machine under the Fish Audio Community License v1.0 (research, non-commercial "
           "and small-company commercial use; a hosted text-to-speech or voice-cloning service needs a licence from "
           "Fish Audio). Clone a real person's voice only with their consent.")
SPEAKER = re.compile(r"<\|speaker:(\d+)\|>")
DIRECTION = re.compile(r"\[[^\]]*\]")
SAMPLE_LINE = "I have been waiting for you. Sit down, and tell me everything, from the very beginning."


def tool(name: str) -> str | None:
    return shutil.which(name) or shutil.which(name, path="/opt/homebrew/bin:/usr/local/bin")


# ---------- backends ----------

def mem_available_gb() -> float:
    """MemAvailable of this machine in GB (a DGX Spark's GPU and system share one pool); on a Mac what the kernel gets
    back without compressing (telemetry.parse_vm_stat: free, file-backed and purgeable pages), 0 while macOS says its
    memory pressure is critical; -1 when unknown."""

    if sys.platform == "darwin":
        import telemetry
        try:
            said = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=2).stdout
            mb = telemetry.parse_vm_stat(said, 1 << 40)
            if mb is None:
                return -1.0
            return 0.0 if telemetry.mac_pressure_critical() else mb / 1024
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return -1.0
    ram = -1.0
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    ram = int(line.split()[1]) / 1024 ** 2
                    break
    except (OSError, ValueError, IndexError):
        pass
    card = gpu_free_gb()
    return ram if card is None or ram < 0 else min(ram, card)


def gpu_free_gb() -> float | None:
    """A discrete card's free memory in GB (spark/memory_guard.py's ``device``: an RTX Pro 6000, or the simulation of
    one on a Spark); None on a DGX Spark, whose GPU memory is the machine's."""

    try:
        spark = str(Path(__file__).resolve().parent.parent / "spark")
        if spark not in sys.path:
            sys.path.insert(0, spark)
        import memory_guard
        card = memory_guard.device(fresh=True)
    except Exception:  # noqa: BLE001  (a reading, never a reason to fail)
        return None
    return None if card is None else card["free_mb"] / 1024


def comfy_up(port: int | None = None) -> bool:
    """Whether the studio's ComfyUI server (spark/comfy.sh) answers on its port: it may hold the video models."""

    import socket

    try:
        with socket.create_connection(("127.0.0.1", port or int(os.environ.get("SPARK_COMFY_PORT", 8190))), timeout=0.3):
            return True
    except OSError:
        return False


def pieces(text: str, limit: int) -> list[str]:
    """Text over ``limit`` words cut at sentence ends into parts of about ``limit`` words; each part opens on the
    speaker tag that was speaking (the CUDA engine takes one part a request, its context is 4096 rows)."""

    if len(words(spoken(text))) <= limit:
        return [text]
    sentences, speaker = [], None
    for part in re.split(r"(<\|speaker:\d+\|>)", text):
        if SPEAKER.fullmatch(part):
            speaker = part
            continue
        for sentence in re.split(r"(?<=[.!?…])\s+", part.strip()):
            if sentence.strip():
                sentences.append((speaker, sentence.strip()))
    out, current, count, said = [], [], 0, None
    for who, sentence in sentences:
        size = len(words(spoken(sentence)))
        if current and count + size > limit:
            out.append(" ".join(current))
            current, count, said = [], 0, None
        if who and who != said:
            current.append(who)
            said = who
        current.append(sentence)
        count += size
    if current:
        out.append(" ".join(current))
    return [one for one in out if spoken(one)]


def dialogue(text: str) -> str:
    """Two or more speaker tags: each turn after the first on a line of its own. Written on one line, Drama 3 drops
    later turns far more often (TensorFold drama3-mlx 92c09a8e: streamed dialogue takes short 12 of 48 times on one
    line, 1 of 48 a turn a line)."""

    if len(SPEAKER.findall(text)) < 2:
        return text
    first = SPEAKER.search(text)
    head, rest = text[: first.end()], text[first.end():]
    return head + re.sub(r"[ \t]*\n?[ \t]*(<\|speaker:\d+\|>)", r"\n\1", rest)


def one_speaker(text: str) -> bool:
    return set(SPEAKER.findall(text)) <= {"0"}


class Served:
    """A Drama 3 engine run as a persistent server, the protocol both TensorFold ports speak (``--serve``: one JSON
    request a line on its stdin, one JSON answer a line on its stderr, lines that are not protocol never start with
    ``{``). Loaded on the first take, and then:

    - holds the studio's GPU lock folder (``~/studio-gpu.lock.d``) for as long as it is loaded, not only while a take
      runs, so a script outside the app that respects the lock never loads a model beside it;
    - is shut down, its memory given back, after ``DRAMA3_IDLE_S`` seconds without a take (300 by default), and at
      once when any other job of this machine's queue starts (``release``, called by server.py), so it never stands
      beside a MiniMax H3 or Qwen render;
    - on a DGX Spark, stops the studio's ComfyUI server before it loads when that server holds the memory it needs
      (the next video reloads its models, as after ``spark/comfy.sh stop``);
    - stands under a kill-guard: a thread reads the available memory (MemAvailable on Linux, vm_stat on a Mac) and
      kills the engine by its PID under ``DRAMA3_GUARD_GB`` (8 GB);
    - dies with the app: its stdin is the app's pipe, and the engine ends at the end of its input.
    """

    load_s = 10.0
    NEED_GB = 50.0
    # what the server is called in messages and in the lock folder's owner file, and the variables of its idle time and
    # its guard: Drama 3's here, MiniMax Music 3's in music.py
    title, owner_tag, idle_env, guard_env = "Drama 3", "studio-voice drama3", "DRAMA3_IDLE_S", "DRAMA3_GUARD_GB"

    def __init__(self) -> None:
        self.idle_s = float(os.environ.get(self.idle_env, 300))
        self.guard_gb = float(os.environ.get(self.guard_env, 8))
        self.known: tuple[float, str] | None = None
        self.mutex = threading.RLock()  # a request, a load and an unload never run at once
        self.process: subprocess.Popen | None = None
        self.answers: "queue.Queue[dict]" = queue.Queue()
        self.log: list[str] = []
        self.owner = ""  # what this server wrote in the lock folder's owner file
        self.loaded_at = self.last_used = 0.0
        self.before_gb = -1.0  # MemAvailable just before the server started: what a job has once it is unloaded
        self.load_s_measured: float | None = None
        self.device_gb: float | None = None
        self.killed = ""  # why the guard killed the engine, if it did
        self.events: list[dict] = []  # loads and unloads, newest last (shown in /api/voice/state)
        self.idler: threading.Thread | None = None
        self.ready: dict = {}  # the engine's ready line

    # -- the server --

    def loaded(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def note(self, what: str, **more) -> None:
        self.events = (self.events + [{"at": time.time(), "what": what, **more}])[-20:]

    def status(self) -> dict:
        return {"loaded": self.loaded(), "idle_unload_s": self.idle_s, "load_s": self.load_s_measured,
                "idle_for_s": round(time.time() - self.last_used, 1) if self.loaded() and self.last_used else None,
                "events": self.events[-5:]}

    def read(self, process: subprocess.Popen) -> None:
        """The engine's stderr: its JSON answers into ``answers``, the rest into ``log``."""

        for line in process.stderr:
            line = line.rstrip("\n")
            if line.startswith("{"):
                try:
                    self.answers.put(json.loads(line))
                    continue
                except ValueError:
                    pass
            self.log = (self.log + [line])[-200:]
            found = re.search(r"weights on the device: ([\d.]+) GB", line)
            if found:
                self.device_gb = float(found.group(1))
        self.answers.put({"ok": False, "error": "the engine stopped", "gone": True})

    def guard(self, process: subprocess.Popen) -> None:
        """MemAvailable four times a second; the engine is killed by its PID under the floor."""

        while process.poll() is None:
            free = mem_available_gb()
            if 0 <= free < self.guard_gb:
                self.killed = f"MemAvailable {free:.1f} GB < {self.guard_gb:g} GB: the memory guard killed {self.title} (pid {process.pid})"
                try:
                    os.kill(process.pid, 9)
                except OSError:
                    pass
                return
            time.sleep(0.25 if SPARK else 1.0)  # a Mac's count is a vm_stat process: once a second

    def idle(self) -> None:
        """Unloads the engine after ``idle_s`` without a take, and gives its memory and the GPU lock back."""

        while True:
            time.sleep(5)
            if not self.loaded():
                if self.process is not None and self.mutex.acquire(blocking=False):
                    try:  # it ended by itself (a crash, the guard): the lock goes back
                        if self.process is not None and not self.loaded():
                            self.stop("the engine ended (" + (self.killed or f"status {self.process.returncode}") + ")")
                    finally:
                        self.mutex.release()
                continue
            if time.time() - self.last_used < self.idle_s or not self.mutex.acquire(blocking=False):
                continue
            try:
                if self.loaded() and time.time() - self.last_used >= self.idle_s:
                    self.stop(f"idle for {self.idle_s:g} s")
            finally:
                self.mutex.release()

    def make_room(self, job: dict) -> None:
        """Enough free memory to load, or a RuntimeError saying why not: the studio's ComfyUI server (the video
        models) is stopped first when it holds what Drama 3 needs."""

        free = mem_available_gb()
        if free < 0 or free >= self.NEED_GB:
            return
        if SPARK and comfy_up():
            job["progress"] = {"stage": "stopping ComfyUI to free the video models' memory", "at": 0, "of": 1}
            subprocess.run(["bash", str(Path(__file__).resolve().parent.parent / "spark" / "comfy.sh"), "stop"],
                           capture_output=True, text=True, timeout=30)
            for _ in range(60):
                if not comfy_up():
                    break
                time.sleep(1)
            settle()
            free = mem_available_gb()
            self.note("stopped ComfyUI before loading", available_gb=round(free, 1))
        elif 0 <= free < self.NEED_GB:
            # a server that has just ended (ComfyUI stopped by its memory floor, a render's process) gives its memory
            # back over some seconds: a voice take queued right after one was refused with half of it still away
            settle()
            free = mem_available_gb()
        if 0 <= free < self.NEED_GB:
            raise RuntimeError(f"{self.title} needs about {self.NEED_GB:.0f} GB of free memory to load and this machine has "
                               f"{free:.0f} GB available")

    def start(self, job: dict) -> bool:
        """The server loaded and ready, under the GPU lock; False when the job was cancelled meanwhile."""

        if self.loaded():
            return True
        if self.process is not None:
            self.stop("the engine had ended (" + (self.killed or f"status {self.process.returncode}") + ")")
        mine = f"{self.owner_tag} {os.getpid()}"
        if not take_lock(job, mine):
            return False
        job.pop("lock", None)  # the server's, not the take's: it is given back when the server stops
        self.owner = mine
        try:
            self.make_room(job)
            job["progress"] = {"stage": f"loading {self.title} (about {self.load_s:.0f} s)", "at": 0, "of": 1}
            env = self.environment()
            self.answers, self.log, self.killed, self.device_gb = queue.Queue(), [], "", None
            began, self.before_gb = time.time(), mem_available_gb()
            self.process = subprocess.Popen(self.argv(), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.PIPE, text=True, bufsize=1, env=env)
            threading.Thread(target=self.read, args=(self.process,), daemon=True).start()
            threading.Thread(target=self.guard, args=(self.process,), daemon=True).start()
            if self.idler is None:
                self.idler = threading.Thread(target=self.idle, daemon=True)
                self.idler.start()
            while True:
                if job["status"] == "cancelled":
                    self.stop("the take was cancelled while loading")
                    return False
                try:
                    answer = self.answers.get(timeout=0.5)
                except queue.Empty:
                    if time.time() - began > 600:
                        raise RuntimeError(f"{self.title} did not load in 10 minutes")
                    continue
                if answer.get("ready"):
                    self.ready = answer
                    break
                raise RuntimeError(f"{self.title} did not load: " + self.why(answer))
            self.loaded_at = self.last_used = time.time()
            self.load_s_measured = round(self.loaded_at - began, 1)
            self.note("loaded", seconds=self.load_s_measured, device_gb=self.device_gb)
            job["load_s"] = self.load_s_measured
            return True
        except BaseException:
            self.stop("it failed to load")
            raise

    def argv(self) -> list[str]:
        raise NotImplementedError

    def environment(self) -> dict:
        return dict(os.environ)

    def why(self, answer: dict) -> str:
        said = self.killed or answer.get("error") or "no answer"
        tail = [line for line in self.log if line.strip()][-2:]
        return said + (" (" + " / ".join(tail)[-300:] + ")" if tail and answer.get("gone") else "")

    def ask(self, job: dict, request: dict, seconds: float, progress=None) -> dict | None:
        """One request to the loaded server: its answer, or None when the job was cancelled (the server is then
        stopped: a request cannot be taken back). Progress lines (``{"id", "progress"}``, sent when the request asks
        for them) go to ``progress``."""

        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        began = time.time()
        while True:
            if job["status"] == "cancelled":
                self.stop("the take was cancelled")
                return None
            try:
                answer = self.answers.get(timeout=0.25)
            except queue.Empty:
                if time.time() - began > seconds:
                    self.stop("a request took longer than %d s" % seconds)
                    raise RuntimeError(f"{self.title} did not answer in {seconds:.0f} s")
                continue
            if answer.get("ready"):
                continue
            if "ok" not in answer and isinstance(answer.get("progress"), dict):
                if progress is not None:
                    progress(answer["progress"])
                continue
            if not answer.get("ok"):
                if answer.get("gone"):
                    self.stop("the engine ended during a take")
                raise RuntimeError(f"{self.title}: " + self.why(answer))
            return answer

    def stop(self, why: str) -> None:
        """The server ended (stdin closed, killed after 20 s), its memory back, the GPU lock given back."""

        with self.mutex:
            process, self.process = self.process, None
            if process is not None:
                if process.poll() is None:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
                settle()
                self.note("unloaded", why=why, available_gb=round(mem_available_gb(), 1))
            if self.owner and lock_owner() == self.owner:
                shutil.rmtree(LOCK, ignore_errors=True)
            self.owner = ""

    def release(self, why: str) -> bool:
        """Unload now if loaded (another job needs the GPU): True when it was loaded."""

        with self.mutex:
            if self.process is None:
                return False
            self.stop(why)
            return True

class Mlx(Served):
    """TensorFold's MLX port of Drama 3 on a Mac, in its own venv. From drama3-mlx 92c09a8e on it runs as a persistent
    server (``python -m tensorfold.drama3 --serve``, ``Served``: about 5.3 s to ready, once, model, codec and Whisper
    kept, 45 to 50 GB); an older engine runs its command line once a take (``command``, ``mlx_take``). The engine
    chunks long text itself and checks its own words (``--verify``, ``--stop-rule``)."""

    name, label = "mlx", "TensorFold MLX · Apple Silicon"
    fps, load_s = 75.0, 5.5  # measured on an M5 Ultra: 70 to 73 frames a second streamed, 83 not; 5.3 s to ready
    NEED_GB = 58.0  # free memory wanted before it loads: about 50 GB with Whisper, and the guard's 8 GB beside it

    def __init__(self) -> None:
        super().__init__()
        self.venv = Path(os.environ.get("DRAMA3_VENV", Path.home() / "drama3-venv")).expanduser()
        self.model = Path(os.environ.get("DRAMA3_MODEL", Path.home() / "models/drama-3-mlx")).expanduser()
        self.known: tuple[float, str] | None = None  # (when, why not) of the last look: a look runs a process
        # an engine with --stream (a growing WAV while it speaks) and its own end-of-text check (--verify, on by
        # default there, with --stop-rule): drama3-mlx 4a8a982f or later; an older one gets neither flag
        self.streams = False
        self.serves = False  # --serve: drama3-mlx 92c09a8e or later
        self.hears = False  # --serve hears a reference left without its text: drama3-mlx 8f3d4b33 or later

    def serving(self) -> bool:
        return self.serves and self.check() == ""

    @property
    def checks(self) -> bool:
        """Whether the engine checks its own words (drama3-mlx 4a8a982f on): the app then does not."""
        return self.streams

    def argv(self) -> list[str]:
        return [str(self.python()), "-I", "-m", "tensorfold.drama3", "--model", str(self.model), "--serve"]

    def python(self) -> Path:
        return self.venv / "bin" / "python"

    def check(self) -> str:
        """Why Drama 3 cannot run here ("" when it can): looked at once a minute at most, never longer than 20 s."""

        if self.loaded():
            return ""
        if self.known and (not self.known[1] or time.time() - self.known[0] < 60):
            return self.known[1]
        why = self.look()
        self.known = (time.time(), why)
        return why

    def look(self) -> str:
        if sys.platform != "darwin":
            return "The MLX backend runs on a Mac (Apple Silicon)."
        if not self.python().is_file():
            return f"No Drama 3 environment at {self.venv} (README, Voice: TensorFold's drama3-mlx branch in its own venv)."
        self.model = located("drama3-mlx", self.model, lambda p: (p / "config.json").is_file() and (p / "codec.safetensors").is_file())
        if not (self.model / "config.json").is_file() or not (self.model / "codec.safetensors").is_file():
            return (f"No converted Drama 3 model at {self.model} (python -m tensorfold.families.drama3.convert "
                    "~/models/drama-3 ~/models/drama-3-mlx)." + found_hint("drama3-mlx"))
        try:
            memory = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout.strip() or 0)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            memory = 0
        if memory and memory < MEMORY_GB * 1024 ** 3:
            return f"This Mac has {memory / 1024 ** 3:.0f} GB of memory; Drama 3 needs about 52 GB free, so {MEMORY_GB} GB or more."
        try:
            done = subprocess.run([str(self.python()), "-I", "-c", "import importlib.util as u; s = u.find_spec('tensorfold.drama3'); "
                                   "print(bool(s), bool(u.find_spec('tensorfold.families.drama3.stream')), "
                                   "bool(s and s.origin and '\"--serve\"' in open(s.origin).read()), "
                                   "bool(s and s.origin and 'ref_text_optional' in open(s.origin).read()))"],
                                  capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"The Drama 3 environment did not answer: {error}"
        found = done.stdout.split()
        if not found or found[0] != "True":
            return f"The environment at {self.venv} has no tensorfold.drama3 (install TensorFold's drama3-mlx branch into it)."
        self.streams = found[1:2] == ["True"]
        self.serves = found[2:3] == ["True"]
        self.hears = found[3:4] == ["True"]
        return ""

    def command(self, text_file: Path, out: Path, stats: Path, reference: tuple[Path, str] | None, seed: int, sampling: dict,
                live: bool = False) -> list[str]:
        command = [str(self.python()), "-I", "-m", "tensorfold.drama3", "--model", str(self.model), "--text-file", str(text_file),
                   "--out", str(out), "--stats", str(stats), "--seed", str(seed), "--temperature", f"{sampling['temperature']:g}",
                   "--top-p", f"{sampling['top_p']:g}", "--top-k", str(int(sampling["top_k"]))]
        if reference:
            command += ["--ref-audio", str(reference[0]), "--ref-text", reference[1]]
        if self.streams and live:  # the WAV at --out grows as it is spoken (its sizes are set when done): /live reads it
            command += ["--stream"]
        return command

    def make(self, job: dict, text: str, wav: Path, reference: tuple[Path, str] | None, seed: int, sampling: dict,
             live: bool = True) -> dict | None:
        """One take through the server, the whole text in one request (the engine chunks it): the measurements, or
        None when cancelled. Played while made (``pcm``, /live), a part the engine's check would have drawn again
        cannot be (it has been heard): the take is asked once more without streaming, where the engine redraws it."""

        folder = wav.with_suffix(".live")
        folder.mkdir(exist_ok=True)
        pcm = folder / "0.pcm"
        if live:
            LIVE[job["id"]] = {"parts": [pcm], "done": False, "finished": 0, "gap_s": 0}
        request = {"text": dialogue(text), "wav": str(wav), "seed": seed, "temperature": sampling["temperature"],
                   "top_k": int(sampling["top_k"]), "top_p": sampling["top_p"], "id": job["id"]}
        if reference:
            request.update(ref_audio=str(reference[0]), ref_text=reference[1])
        seconds = 300 + expected_seconds(text) * 4  # frames, the check and two redraws a part, with room
        with self.mutex:
            began = time.time()
            try:
                if not self.start(job):
                    return None
                loaded = time.time()
                job["progress"] = {"stage": "Drama 3 voice", "at": 0, "of": 1}
                answer = self.ask(job, {**request, "pcm": str(pcm), "stream": True} if live else {**request, "stream": False}, seconds)
                if answer is None:
                    return None
                retaken = None
                if live and any(isinstance(part, dict) and part.get("action") == "redraw" for part in answer.get("verify") or []):
                    # (measured with drama3-mlx 4a8a982f on the M5 Ultra, the two-speaker example: seed 1 played at
                    # 35 % came back whole after one redraw; seed 2 at 80 % stayed short after three draws)
                    retaken = {"played": checked_by_engine(answer), "seconds": answer.get("audio_s")}
                    job["log"] += "[tensorfold] a part was short while playing: the take is made again without playing, so it can be redrawn\n"
                    job["progress"] = {"stage": "Drama 3 voice, again without playing", "at": 0, "of": 1}
                    again = self.ask(job, {**request, "stream": False}, seconds)
                    if again is None:
                        return None
                    answer = again
            finally:
                self.last_used = time.time()
                if job["id"] in LIVE:
                    LIVE[job["id"]].update(finished=1, done=True)
                threading.Timer(60, forget_live, args=(job["id"], folder)).start()
        chunks = answer.get("chunks")
        return {"frames": answer.get("frames"), "audio_s": answer.get("audio_s"), "frames_per_s": answer.get("fps"),
                "rtf": answer.get("rtf"), "total_s": round(time.time() - began, 1),
                "engine_s": round(float(answer.get("total_ms") or 0) / 1000, 2), "first_audio_ms": answer.get("first_audio_ms"),
                "encode_ms": answer.get("encode_ms"), "load_s": job.get("load_s"), "peak_memory_gb": answer.get("peak_memory_gb"),
                "chunks": len(chunks) if isinstance(chunks, list) else chunks, "finish": answer.get("finish"),
                "verify": answer.get("verify"), "stream": bool(answer.get("stream")), "retaken": retaken,
                "ref_texts": answer.get("ref_texts"), "ref_asr_ms": answer.get("ref_asr_ms"),
                "made_s": round(time.time() - loaded, 2)}


SPARK_DIR = Path(__file__).resolve().parent.parent / "spark"


def located(need: str, path: Path, there) -> Path:
    """``path`` when ``there(path)``; else where Models & weights (app/discover.py) says that file or folder is on this
    machine (a link the studio made, or the variable that names it), when that passes the same test."""

    if there(path) or discover is None:
        return path
    try:
        found = discover.resolve(need)
    except Exception:  # noqa: BLE001 - a broken cache never stops a check
        return path
    return found if found is not None and there(found) else path


def found_hint(need: str) -> str:
    """What Models & weights adds to a "not installed" message: where a scan found it, or that it looked."""

    try:
        return discover.hint(need) if discover is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def managed() -> bool:
    """``DRAMA3_MANAGED_GUARD=1``: on a machine with a workload governor (``SPARK_GOVERNOR``, spark/workload.py),
    Drama 3 starts inside a reservation of its own (spark/managed_voice.py) and every part of a take waits for the
    start checks (ownership, memory, pressure, a cool start) first. Off by default."""

    return os.environ.get("DRAMA3_MANAGED_GUARD") == "1"


def submission_check() -> None:
    """The start checks before a part is sent to a managed engine: a RuntimeError with the reason when refused."""

    done = subprocess.run(["python3", str(SPARK_DIR / "managed_voice.py"), "--check"], capture_output=True, text=True, timeout=190)
    if done.returncode:
        raise RuntimeError("the voice was not sent: " + (done.stderr or done.stdout).strip()[-1500:])


class Cuda(Served):
    """TensorFold's CUDA port of Drama 3 (``drama3-generate``, Zig + CUDA, no Python) on a DGX Spark.

    A persistent server (``--serve 1``, ``Served``), loaded on the first take: about 18 s, 38.6 GB of weights,
    41.6 GB at the peak. Long text goes in parts here (``pieces``): the engine's context is 4096 rows.
    """

    name, label = "cuda", "TensorFold CUDA · DGX Spark"
    fps, load_s = 30.0, 18.0  # measured on a GB10: 29 to 31 frames a second sampled, 17 to 18 s to load from NFS
    NEED_GB = 50.0  # free memory wanted before it loads: 41.6 GB at the peak, and the guard's 8 GB beside it
    CHUNK_WORDS = 60  # a request's text at most (about 27 s of speech, ~600 frames, well inside max_ctx 4096)
    GAP_S = 0.15  # the silence put between two parts of a long take

    def __init__(self) -> None:
        super().__init__()
        prefix = Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio")).expanduser()
        here = prefix / "drama3"
        self.bin = Path(os.environ.get("DRAMA3_CUDA_BIN", here / "bin" / "drama3-generate")).expanduser()
        self.lib = Path(os.environ.get("DRAMA3_CUDA_LIB", self.bin.parent.parent / "lib" / "libtensorfold_drama3.so")).expanduser()
        self.model = Path(os.environ.get("DRAMA3_CUDA_MODEL", Path.home() / "models" / "drama-3")).expanduser()
        self.codec = Path(os.environ.get("DRAMA3_CUDA_CODEC", here / "codec-v1.safetensors")).expanduser()

    checks = False  # no end-of-text check of its own: the app's (``redrawn``)

    def serving(self) -> bool:
        return True

    def environment(self) -> dict:
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = str(self.lib.parent) + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        return env

    # -- availability --

    def check(self) -> str:
        """Why Drama 3 cannot run here ("" when it can): files looked at, then memory; at most every 10 s."""

        if self.process is not None and self.process.poll() is None:
            return ""
        if self.known and time.time() - self.known[0] < 10:
            return self.known[1]
        why = self.look()
        self.known = (time.time(), why)
        return why

    def look(self) -> str:
        if not sys.platform.startswith("linux"):
            return "The CUDA backend runs on a DGX Spark (Linux, CUDA)."
        if managed() and not os.environ.get("SPARK_GOVERNOR"):
            return "DRAMA3_MANAGED_GUARD=1 starts Drama 3 under the machine's workload governor: set SPARK_GOVERNOR to its folder."
        # a file not where it is expected is looked for where Models & weights found and linked it
        self.bin = located("drama3-binary", self.bin, lambda p: os.access(p, os.X_OK))
        self.lib = located("drama3-lib", self.lib, Path.is_file)
        self.model = located("drama3-checkpoint", self.model,
                             lambda p: (p / "config.json").is_file() and (p / "model.safetensors.index.json").is_file())
        self.codec = located("drama3-codec", self.codec, Path.is_file)
        if not os.access(self.bin, os.X_OK):
            return (f"No Drama 3 engine at {self.bin} (bash oneshot-setup-spark.sh --voice builds TensorFold's drama3-cuda engine "
                    "and sets up the rest; DRAMA3_CUDA_BIN names another)."
                    + found_hint("drama3-binary"))
        if not self.lib.is_file():
            return (f"No libtensorfold_drama3.so at {self.lib} (spark/build-drama3.sh makes it with the engine: bash "
                    "oneshot-setup-spark.sh --voice; DRAMA3_CUDA_LIB names another)." + found_hint("drama3-lib"))
        if not (self.model / "config.json").is_file() or not (self.model / "model.safetensors.index.json").is_file():
            return (f"No Drama 3 checkpoint at {self.model} (bash oneshot-setup-spark.sh --voice, or hf download fishaudio/drama-3; "
                    "DRAMA3_CUDA_MODEL names another)."
                    + found_hint("drama3-checkpoint"))
        if not self.codec.is_file():
            return (f"No converted codec at {self.codec} (converted once from the checkpoint's codec.pth: bash "
                    f"oneshot-setup-spark.sh --voice, or spark/drama3_codec.py {self.model} {self.codec}; "
                    "DRAMA3_CUDA_CODEC names another)." + found_hint("drama3-codec"))
        free = mem_available_gb()
        if 0 <= free < self.NEED_GB and not comfy_up():
            return (f"Drama 3 needs about {self.NEED_GB:.0f} GB of free memory and this Spark has {free:.0f} GB available now "
                    "(something outside the studio holds the rest).")
        return ""

    def argv(self) -> list[str]:
        engine = [str(self.bin), "--model", str(self.model), "--codec", str(self.codec), "--serve", "1"]
        return ["python3", str(SPARK_DIR / "managed_voice.py"), *engine] if managed() else engine

    # -- a take --

    def make(self, job: dict, text: str, wav: Path, reference: tuple[Path, str] | None, seed: int, sampling: dict,
             live: bool = True) -> dict | None:
        """One take through the server: the measurements (the stats the MLX engine writes), or None when cancelled.
        Long text goes in parts (``pieces``); after the first, a one-speaker take without a clip clones the first
        part's voice, so it keeps one voice; a clip given is used for every part."""

        parts = pieces(text, self.CHUNK_WORDS)
        folder = wav.with_suffix(".live")
        if live:  # played while it is made: the engine writes each part's raw PCM there as it goes (/live)
            folder.mkdir(exist_ok=True)
            LIVE[job["id"]] = {"parts": [folder / f"{i}.pcm" for i in range(len(parts))], "done": False, "finished": 0, "gap_s": self.GAP_S}
        with self.mutex:
            began = time.time()
            if not self.start(job):
                return None
            loaded = time.time()
            answers, files = [], []
            try:
                for index, part in enumerate(parts):
                    if managed():
                        submission_check()
                    job["progress"] = {"stage": "Drama 3 voice" + (f", part {index + 1} of {len(parts)}" if len(parts) > 1 else ""),
                                       "at": index, "of": len(parts)}
                    out = wav if len(parts) == 1 else wav.with_name(f"{wav.stem}.part{index}.wav")
                    request = {"text": dialogue(part), "wav": str(out), "seed": seed + index,
                               "temperature": sampling["temperature"], "top_k": int(sampling["top_k"]),
                               "top_p": sampling["top_p"], "max_frames": 1500}
                    if live:
                        request["pcm"] = str(LIVE[job["id"]]["parts"][index])
                    voice_of = reference
                    if not voice_of and index and files and one_speaker(text):
                        voice_of = (files[0], spoken(parts[0]))
                    if voice_of:
                        request.update(ref_audio=str(voice_of[0]), ref_text=voice_of[1])
                    answer = self.ask(job, request, 120 + 1500 / 10)
                    if answer is None:
                        return None
                    answers.append(answer)
                    files.append(out)
                    if live:
                        LIVE[job["id"]]["finished"] = index + 1
                    self.last_used = time.time()
            finally:
                self.last_used = time.time()
                if live:
                    LIVE[job["id"]]["done"] = True
                    # the raw parts go a minute later: a page may still be playing the end of the live take
                    threading.Timer(60, forget_live, args=(job["id"], folder)).start()
        if len(files) > 1:
            join_wavs(files, wav, self.GAP_S)
            for one in files:
                one.unlink(missing_ok=True)
        frames = sum(int(a.get("frames") or 0) for a in answers)
        audio = sum(float(a.get("audio_s") or 0) for a in answers) + self.GAP_S * (len(answers) - 1)
        decoding = sum(int(a.get("frames") or 0) / float(a["fps"]) for a in answers if a.get("fps"))
        made = time.time() - loaded
        return {"frames": frames, "audio_s": round(audio, 3), "frames_per_s": round(frames / decoding, 1) if decoding else None,
                "rtf": round(made / audio, 2) if audio else None, "total_s": round(time.time() - began, 1),
                "engine_s": round(sum(float(a.get("total_ms") or 0) for a in answers) / 1000, 2),
                "first_audio_ms": answers[0].get("first_audio_ms") if answers else None,
                "encode_ms": answers[0].get("encode_ms") if answers else None,
                "load_s": job.get("load_s"), "device_gb": self.device_gb,
                "chunks": len(answers), "finish": ["stop" if a.get("eos") else "length" for a in answers], "stream": live}


LIVE: dict[str, dict] = {}  # a running take's raw PCM parts, as the engine writes them (the live WAV, /live)
LIVE_GRACE_S = 2.0  # how long /live waits for a take the worker has not begun yet (its text not written)


def forget_live(job_id: str, folder: Path) -> None:
    LIVE.pop(job_id, None)
    shutil.rmtree(folder, ignore_errors=True)


def live_header(rate: int = 44100, channels: int = 1) -> bytes:
    """A 16-bit PCM WAV header with its lengths left open (a stream): mono for a Drama 3 take, stereo for a song."""

    import struct

    block = 2 * channels
    return (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * block, block, 16)
            + b"data" + struct.pack("<I", 0xFFFFFFFF - 36))


def live_wav(entry: dict, rate: int = 44100, poll: float = 0.05, channels: int = 1):
    """A running take as one WAV, while it is made: the header (its length left open), then each part's raw PCM as the
    engine writes it, the gap between parts. Ends when the take is done, or after 10 minutes without a byte. Sent in
    whole sample frames (``channels`` x 16 bits), so a stereo stream never swaps its sides."""

    block = 2 * channels
    yield live_header(rate, channels)
    quiet = time.time()
    for index, part in enumerate(entry["parts"]):
        sent = 0
        if entry.get("wav"):  # a growing WAV (the MLX engine's --stream): its samples start after its "data" header
            sent = -1
            while sent < 0:
                try:
                    with open(part, "rb") as handle:
                        head = handle.read(4096)
                except OSError:
                    head = b""
                at = head.find(b"data")
                if at >= 0 and len(head) >= at + 8:
                    sent = at + 8
                elif entry["done"] or time.time() - quiet > 600:
                    return
                else:
                    time.sleep(poll)
        start = sent
        while True:
            try:
                with open(part, "rb") as handle:
                    handle.seek(sent)
                    data = handle.read()
            except OSError:
                data = b""
            data = data[: len(data) - (len(data) % block)] if entry["finished"] <= index else data
            if data:
                if index and sent == start:
                    yield b"\0" * (int(rate * entry.get("gap_s", 0)) * block)
                sent += len(data)
                quiet = time.time()
                yield data
                continue
            if entry["finished"] > index:
                break
            if entry["done"] or time.time() - quiet > 600:
                return
            time.sleep(poll)


def settle() -> None:
    """Waits until MemAvailable stops climbing (the memory comes back a little after a process is gone), 30 s at most."""

    before = -1.0
    for _ in range(30):
        free = mem_available_gb()
        if free - before < 0.2:
            return
        before = free
        time.sleep(1)


def join_wavs(files: list[Path], target: Path, gap_s: float) -> None:
    import wave

    with wave.open(str(files[0])) as first:
        params = first.getparams()
    with wave.open(str(target), "wb") as out:
        out.setparams(params)
        silence = b"\0" * (int(params.framerate * gap_s) * params.sampwidth * params.nchannels)
        for index, one in enumerate(files):
            with wave.open(str(one)) as handle:
                if index:
                    out.writeframes(silence)
                out.writeframes(handle.readframes(handle.getnframes()))


BACKENDS = {"mlx": Mlx, "cuda": Cuda}
_backend = None


def backend():
    global _backend
    if _backend is None:
        chosen = os.environ.get("DRAMA3_BACKEND") or ("cuda" if SPARK else "mlx")
        _backend = BACKENDS.get(chosen, Cuda)()
    return _backend


def state() -> dict:
    one = backend()
    why = one.check()
    out = {"available": not why, "why": why, "backend": one.name, "label": one.label, "credit": CREDIT, "licence": LICENCE,
           "defaults": {k: v[0] for k, v in SAMPLING.items()}, "ranges": {k: v[1:] for k, v in SAMPLING.items()},
           "text_limit": TEXT_LIMIT, "reference_seconds": REFERENCE_SECONDS, "speakers": 16,
           "live": bool(getattr(one, "streams", False)) or (hasattr(one, "serving") and one.serving()), "checks": bool(getattr(one, "streams", False)),
           "serves": bool(hasattr(one, "serving") and one.serving())}
    if hasattr(one, "status"):
        out["server"] = one.status()
    return out


def room_mb() -> int | None:
    """While a Drama 3 server is loaded: the memory this machine had before it loaded (MB), which any other job of the
    queue gets back, since the server is unloaded before that job starts (server.memory_of). None otherwise."""

    one = _backend
    if one is None or not hasattr(one, "loaded") or not one.loaded() or getattr(one, "before_gb", -1) < 0:
        return None
    return int(one.before_gb * 1024)


def release(why: str) -> bool:
    """Before any other job of this machine's queue (server.py): a loaded Drama 3 server is unloaded and gives back
    its memory and the GPU lock. True when it was loaded."""

    one = _backend
    released = bool(one is not None and hasattr(one, "release") and one.release(why))
    if released and hasattr(qa, "trim_heap"):
        qa.trim_heap()  # Whisper's CPU model (the app's word check) leaves freed heap behind: back before the next job
    return released


# ---------- text ----------

def spoken(text: str) -> str:
    """The words a take should say: speaker tags and [directions] taken out."""

    return " ".join(DIRECTION.sub(" ", SPEAKER.sub(" ", text)).split())


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


def expected_seconds(text: str) -> float:
    """Roughly how long the take will be: about 2.2 words a second (measured on Drama 3's own reads: 17 words in
    8.3 s, 22 in 11.3 s)."""

    return max(1.5, len(words(spoken(text))) / 2.2)


def estimate(text: str, referenced: bool, asr: bool) -> int:
    seconds, one = expected_seconds(text), backend()
    if getattr(one, "streams", False):  # the MLX engine checks its words itself (~0.2 s a part): no Whisper after
        asr, seconds = False, seconds * 1.05
    # load (3 s on a Mac; 18 s on a Spark unless the server is loaded already), prefill under 0.5 s a chunk, frames at
    # the engine's rate (~63 a second on an M5 Ultra, ~30 on a GB10), the codec; a reference is encoded first;
    # Whisper on the CPU after it (loading included)
    fps, load = float(getattr(one, "fps", 60.0)), float(getattr(one, "load_s", 3.0))
    if hasattr(one, "loaded") and one.loaded():
        load = 0.0
    return int(1 + load + seconds * (FRAME_RATE / fps + 0.05) + (3 if referenced else 0) + (6 + seconds * 0.4 if asr else 0))


def sheet_text(sheet: dict) -> str:
    """A voice sheet (Slate's Sound tab) as a Drama 3 line: the sheet's description as a [direction] ahead of its
    sample line. Drama 3 designs the voice from such a direction and does not read it out (measured: "a deep,
    gravelly male voice..." 76 Hz, "a bright ... young woman" 228 Hz, both transcribed without the direction)."""

    who = " ".join(str(sheet.get("ageGender") or "").split()).rstrip(".")
    sound = ", ".join(" ".join(str(sheet.get(key) or "").split()).rstrip(".") for key in
                      ("accent", "timbre", "pitch", "pacing", "energy", "texture") if str(sheet.get(key) or "").strip())
    direction = ", ".join(bit for bit in (f"the voice of {who}" if who else "", sound) if bit)
    direction = direction.replace("[", "(").replace("]", ")")[:400]
    line = " ".join(str(sheet.get("sampleLine") or "").split()) or SAMPLE_LINE
    return (f"[{direction}] " if direction else "") + line


def match(text: str, heard: str) -> dict:
    wanted, said = words(spoken(text)), words(heard)
    found = sum(1 for one in wanted if one in set(said))
    ratio = difflib.SequenceMatcher(None, wanted, said).ratio() if wanted and said else 0.0
    return {"heard": heard, "score": round(100 * ratio), "found": found, "words": len(wanted)}


# ---------- the GPU lock (shared with the scripts that run outside the app) ----------

def lock_owner() -> str:
    try:
        return (LOCK / "owner").read_text().strip()
    except OSError:
        return ""


def stale() -> bool:
    """A lock whose owner is a process of this machine that is gone, or one left without an owner for a minute."""

    owner = lock_owner()
    pid = owner.split()[-1] if owner else ""
    if pid.isdigit():
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False
    try:
        return not owner and time.time() - LOCK.stat().st_mtime > 60
    except OSError:
        return False


def take_lock(job: dict, mine: str = "") -> bool:
    """Waits for the GPU lock (a job that is cancelled meanwhile stops waiting): True once it is this job's."""

    mine = mine or f"studio-voice {job['id']} {os.getpid()}"
    while True:
        try:
            LOCK.mkdir()
            (LOCK / "owner").write_text(mine + "\n")
            job["lock"] = mine
            return True
        except FileExistsError:
            if stale():
                shutil.rmtree(LOCK, ignore_errors=True)
                continue
        if job["status"] == "cancelled":
            return False
        job["progress"] = {"stage": "waiting for the GPU lock (" + (lock_owner() or "held") + ")", "at": 0, "of": 1}
        time.sleep(2)


def drop_lock(job: dict) -> None:
    if job.get("lock") and lock_owner() == job["lock"]:
        shutil.rmtree(LOCK, ignore_errors=True)
    job.pop("lock", None)


# ---------- jobs ----------

def checked(body: dict) -> dict:
    """A take's settings, checked before it is queued: the params of a ``voice`` job."""

    text = str(body.get("text") or "").strip()
    if not spoken(text):
        raise HTTPException(400, "the text has no words to say")
    if len(text) > TEXT_LIMIT:
        raise HTTPException(400, f"at most {TEXT_LIMIT} characters in one take")
    bad = [n for n in SPEAKER.findall(text) if int(n) > 15]
    if bad:
        raise HTTPException(400, f"speakers are <|speaker:0|> to <|speaker:15|>, not <|speaker:{bad[0]}|>")
    why = backend().check()
    if why:
        raise HTTPException(400, why)
    params = {"text": text, "title": str(body.get("title") or "").strip()[:120]}
    try:
        params["seed"] = int(body.get("seed") if body.get("seed") not in (None, "") else random.randrange(1, 100000))
        for key, (default, low, high) in SAMPLING.items():
            value = body.get(key)
            value = default if value in (None, "") else (int(value) if key == "top_k" else float(value))
            if not low <= value <= high:
                raise HTTPException(400, f"{key} is between {low:g} and {high:g}")
            params[key] = value
    except (TypeError, ValueError):
        raise HTTPException(400, "seed, temperature, top_p and top_k are numbers")
    if body.get("ref_audio"):
        clip = inside(str(body["ref_audio"]))
        if not clip.is_file():
            raise HTTPException(400, "no reference clip at " + str(body["ref_audio"]))
        if body.get("consent") is not True:
            raise HTTPException(400, "cloning a voice needs the speaker's consent: tick “I have the speaker's consent to clone this voice”")
        seconds = media_seconds(str(clip))[0]
        low, high = REFERENCE_SECONDS
        if seconds is not None and not low - 0.05 <= seconds <= high + 0.05:
            raise HTTPException(400, f"a voice to clone is {low:g} to {high:g} seconds of speech, and this clip is {seconds:.1f} s")
        ref_text = " ".join(str(body.get("ref_text") or "").split())
        if not ref_text and not qa.ready()["transcript"] and not hears_references(backend()):
            raise HTTPException(400, "write what the reference clip says (this machine has no speech-to-text tool to hear it)")
        params.update(ref_audio=relative(clip), ref_text=ref_text, consent=True)
    params["asr"] = body.get("asr", True) is not False
    # MLX: play the take while it is made (--stream). Sent audio cannot be taken back, so a part with missing words
    # is then reported instead of drawn again; "live": false lets the engine redraw it
    params["live"] = body.get("live", True) is not False
    if isinstance(body.get("slate"), dict):  # a Slate voice sheet's cast voice: which film and sheet it belongs to
        params["slate"] = {k: str(v)[:80] for k, v in body["slate"].items() if k in ("project", "voice")}
    return params


def perform(job: dict) -> bool:
    """One voice take to its end; False when it was cancelled meanwhile."""

    params, folder = job["params"], HOME / "voice"
    folder.mkdir(parents=True, exist_ok=True)
    stem = folder / job["id"]
    wav, stats, text_file = stem.with_suffix(".wav"), stem.with_suffix(".stats.json"), stem.with_suffix(".txt")
    text_file.write_text(dialogue(params["text"]) + "\n")  # what the engine reads: a turn a line
    reference = None
    if params.get("ref_audio"):
        job["progress"] = {"stage": "reading the voice reference", "at": 0, "of": 1}
        source, clean = inside(params["ref_audio"]), stem.with_suffix(".ref.wav")
        ffmpeg = tool("ffmpeg")
        if ffmpeg:  # any sound file or video, as 44.1 kHz mono 16-bit WAV (the CUDA engine reads WAV only)
            done = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "44100",
                                   "-c:a", "pcm_s16le", str(clean)], capture_output=True, text=True, timeout=120)
            if done.returncode:
                raise RuntimeError("the reference clip could not be read: " + done.stderr.strip()[-200:])
        elif backend().name == "cuda" and source.suffix.lower() != ".wav":
            raise RuntimeError("the CUDA engine reads a WAV reference only, and this machine has no ffmpeg to convert it")
        else:
            shutil.copy(source, clean)
        if not params.get("ref_text") and hears_references(backend()):
            pass  # the Mac engine's server hears the clip itself with its loaded Whisper (ref_texts in its answer)
        elif not params.get("ref_text"):
            heard = qa.transcribe(clean)
            if not heard:
                raise RuntimeError("no transcript of the reference clip: write what it says")
            params["ref_text"] = heard.strip()
        reference = (clean, params.get("ref_text") or "")
    one = backend()
    job["engine"] = one.name
    if hasattr(one, "make") and one.serving():  # a persistent server, which holds the GPU lock itself while loaded
        measured = one.make(job, params["text"], wav, reference, int(params["seed"]), {k: params[k] for k in SAMPLING},
                            live=params.get("live", True) is not False)
        if measured is None:
            return False
        if params.get("ref_audio") and not params.get("ref_text") and measured.get("ref_texts"):
            params["ref_text"] = str(measured["ref_texts"][0]).strip()  # what the engine heard in the clip
        if not getattr(one, "checks", False) and params.get("asr") and qa.ready()["transcript"]:
            measured = redrawn(job, one, params, wav, reference, measured)
            if measured is None:
                return False
        stats.write_text(json.dumps(measured, indent=1) + "\n")
    else:
        live = bool(getattr(one, "streams", False) and params.get("live", True))
        retaken = None
        while True:
            outcome = mlx_take(job, one, text_file, wav, stats, reference, params, live)
            if outcome is None:
                return False
            measured = outcome
            short = [part for part in (measured.get("verify") or []) if isinstance(part, dict) and part.get("action") == "redraw"]
            if not (live and short):
                break
            # played as it was made, a part came out with words missing and could not be drawn again (sent audio is not
            # taken back): the take is made once more without playing, where the engine redraws such a part itself
            # (measured on the M5 Ultra, the two-speaker example: seed 1 played at 35 % and came back whole after one
            # redraw; seed 2 played at 80 % and stayed at 80 % after three draws, the last line missing)
            retaken = {"played": checked_by_engine(measured), "seconds": measured.get("audio_s")}
            job["log"] += "[tensorfold] a part was short while playing: the take is made again without playing, so it can be redrawn\n"
            live = False
        if retaken:
            measured["retaken"] = retaken
    if not wav.is_file():
        raise RuntimeError("Drama 3 finished without writing its take")
    take = {"id": job["id"], "path": relative(wav), "title": params.get("title") or job.get("title"), "text": params["text"],
            "seed": params["seed"], **{k: params[k] for k in SAMPLING}, "backend": one.name, "created": time.time(),
            "duration": measured.get("audio_s"), "frames": measured.get("frames"), "frames_per_s": measured.get("frames_per_s"),
            "rtf": measured.get("rtf"), "total_s": measured.get("total_s"), "peak_memory_gb": measured.get("peak_memory_gb"),
            "device_gb": measured.get("device_gb"),
            "chunks": measured.get("chunks"), "finish": measured.get("finish"), "first_audio_ms": measured.get("first_audio_ms"),
            "load_s": measured.get("load_s"), "engine_s": measured.get("engine_s"), "verify": checked_by_engine(measured),
            "streamed": bool(measured.get("stream")), "retaken": measured.get("retaken"),
            "reference": {"path": params["ref_audio"], "text": params["ref_text"], "consent": True} if params.get("ref_audio") else None,
            "slate": params.get("slate"), "credit": CREDIT}
    if params.get("asr") and measured.get("app_check"):
        # the app's own end-of-text check (``redrawn``: the CUDA engine has none) heard the kept take already
        take["asr"] = {**engine_score(take["verify"]), "by": "whisper"}
    elif params.get("asr") and take["verify"]:
        # the engine heard every part with Whisper already (its end-of-text check): its count is the take's score,
        # and the take is not transcribed a second time
        take["asr"] = engine_score(take["verify"])
    elif params.get("asr") and qa.ready()["transcript"]:
        job["progress"] = {"stage": "Whisper word check", "at": 0, "of": 1}
        try:
            heard = qa.transcribe(wav)
            take["asr"] = match(params["text"], heard) if heard is not None else {"skipped": "no speech-to-text tool"}
        except Exception as error:  # noqa: BLE001 - a take that was made is kept whatever the check does
            take["asr"] = {"skipped": str(error)[-200:]}
    elif params.get("asr"):
        take["asr"] = {"skipped": "no speech-to-text tool on this machine (pip install faster-whisper)"}
    stem.with_suffix(".take.json").write_text(json.dumps(take, indent=1) + "\n")
    job["outputs"] = [take["path"]]
    job["voice"] = take
    job["progress"] = None
    return True


def hears_references(one) -> bool:
    """Whether the engine transcribes a reference clip itself when its text is left out (the Mac engine's server,
    drama3-mlx 8f3d4b33 on): the app then does not run its own Whisper on the CPU first."""

    return bool(getattr(one, "hears", False) and hasattr(one, "serving") and one.serving())


RETRIES = 2  # draws again of a take with missing words, as the Mac engine's --verify-retries


def redrawn(job: dict, one, params: dict, wav: Path, reference, measured: dict) -> dict | None:
    """The end-of-text check for an engine without one of its own (the CUDA engine): Whisper hears the take and
    ``wordcheck`` (the Mac engine's rules) aligns it to the text. Words missing (under 85 % heard, three in a row, or
    the ending): the take is made again with another seed, not played, ``RETRIES`` times at most, and the best kept.
    The kept take's check is its ``verify`` (attempts counted); a take that was redrawn keeps the first one's check
    as ``retaken``, as on the Mac. None when the job was cancelled."""

    import wordcheck

    def heard(path: Path) -> dict:
        job["progress"] = {"stage": "Whisper word check", "at": 0, "of": 1}
        return wordcheck.check_text(params["text"], qa.transcribe(path) or "")

    def score(check: dict) -> tuple:
        return (check["action"] != "redraw", round(check["coverage"], 3), -check["longest_missing_run"], -len(check["tail_words"]))

    first = heard(wav)
    best, best_measured, attempts = first, measured, 1
    while best["action"] == "redraw" and attempts <= RETRIES:
        seed = int(params["seed"]) + 1000 * attempts
        job["log"] += f"[tensorfold] words missing ({best['reason']}): drawn again, seed {seed}, not played\n"
        attempt = wav.with_name(f"{wav.stem}.draw{attempts}.wav")
        again = one.make(job, params["text"], attempt, reference, seed, {k: params[k] for k in SAMPLING}, live=False)
        if again is None:
            attempt.unlink(missing_ok=True)
            return None
        attempts += 1
        check = heard(attempt)
        if score(check) > score(best):
            attempt.replace(wav)
            best, best_measured = check, {**again, "redraw_seed": seed}
        else:
            attempt.unlink(missing_ok=True)
    keep = ("words", "matched", "missing", "tail_words", "action", "reason", "heard", "coverage", "longest_missing_run", "ending_missing")
    out = dict(best_measured)
    out["verify"] = [{**{k: best.get(k) for k in keep}, "attempts": attempts}]
    out["app_check"] = True
    if attempts > 1:
        out["retaken"] = {"played": [{k: first.get(k) for k in keep}], "seconds": measured.get("audio_s"), "attempts": attempts}
        out["load_s"] = measured.get("load_s")
        out["first_audio_ms"] = measured.get("first_audio_ms")
    return out


def mlx_take(job: dict, one, text_file: Path, wav: Path, stats: Path, reference, params: dict, live: bool) -> dict | None:
    """One run of the MLX engine's command line under the GPU lock: its stats (load time and first sound added), or
    None when the job was cancelled. ``live``: --stream, the WAV at ``wav`` growing as it is spoken (/live reads it)."""

    sampling = {k: params[k] for k in SAMPLING}
    if live:
        command = one.command(text_file, wav, stats, reference, int(params["seed"]), sampling, live=True)
    else:
        command = one.command(text_file, wav, stats, reference, int(params["seed"]), sampling)
    if not take_lock(job):
        return None
    if live:
        wav.unlink(missing_ok=True)
        LIVE[job["id"]] = {"parts": [wav], "wav": True, "done": False, "finished": 0, "gap_s": 0}
    stats.unlink(missing_ok=True)
    began = len(job.get("log") or "")
    try:
        job["progress"] = {"stage": "Drama 3 voice" + ("" if live or not params.get("live", True) else ", again without playing"), "at": 0, "of": 1}
        if not run(job, command, {}):
            return None
    finally:
        drop_lock(job)
        if live:
            LIVE[job["id"]].update(finished=1, done=True)
            threading.Timer(60, LIVE.pop, args=(job["id"], None)).start()
    try:
        measured = json.loads(stats.read_text())
    except (OSError, ValueError):
        measured = {}
    if measured.get("first_audio_s") is not None:
        measured["first_audio_ms"] = round(1000 * float(measured["first_audio_s"]), 1)
    loaded = re.search(r"\[drama3\] loaded .* in ([\d.]+)s", (job.get("log") or "")[began:])
    measured.setdefault("load_s", float(loaded.group(1)) if loaded else None)
    return measured


def checked_by_engine(measured: dict) -> list[dict] | None:
    """The MLX engine's end-of-text check, part by part (``verify`` in its stats), as kept on the take."""

    parts = measured.get("verify")
    if not isinstance(parts, list) or not parts:
        return None
    keep = ("words", "matched", "missing", "tail_words", "action", "attempts", "cut_s", "late_s", "heard", "asr_s", "reason")
    return [{k: one.get(k) for k in keep if k in one} for one in parts if isinstance(one, dict)] or None


def engine_score(parts: list[dict]) -> dict:
    """A take's word score from the engine's check: words heard of words written, over all its parts."""

    total = sum(int(one.get("words") or 0) for one in parts)
    found = sum(int(one.get("matched") or 0) for one in parts)
    return {"heard": " ".join(str(one.get("heard") or "").strip() for one in parts).strip(), "score": round(100 * found / total) if total else None,
            "found": found, "words": total, "missing": [w for one in parts for w in (one.get("missing") or [])], "by": "engine"}


def takes() -> list[dict]:
    out = []
    for file in (HOME / "voice").glob("*.take.json"):
        try:
            take = json.loads(file.read_text())
        except (OSError, ValueError):
            continue
        if (HOME / take.get("path", "")).is_file():  # a take whose job was deleted is in the trash folder
            out.append(take)
    return sorted(out, key=lambda t: -float(t.get("created") or 0))


def one_take(take_id: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", take_id):
        raise HTTPException(400, "bad take id")
    file = HOME / "voice" / f"{take_id}.take.json"
    if not file.is_file():
        raise HTTPException(404, "no such take")
    return json.loads(file.read_text())


# ---------- the API ----------

@router.get("/api/voice/state")
def voice_state() -> dict:
    return state()


@router.post("/api/voice/takes")
def render(body: dict) -> dict:
    """Queue a take: {text, ref_audio?, ref_text?, consent?, seed?, temperature?, top_p?, top_k?, title?, asr?}."""

    params = checked(body)
    title = params["title"] or "Voice: " + spoken(params["text"])[:48]
    return enqueue("voice", title, params, estimate(params["text"], bool(params.get("ref_audio")), params["asr"] and qa.ready()["transcript"]))


@router.post("/api/voice/sheet")
def cast_voice(body: dict) -> dict:
    """A Slate voice sheet's cast voice: its sample line, its description as the direction (sheet_text)."""

    sheet = body.get("sheet") if isinstance(body.get("sheet"), dict) else {}
    return render({"text": sheet_text(sheet), "seed": body.get("seed"), "title": "Cast voice: " + str(sheet.get("name") or "voice")[:60],
                   "slate": {"project": body.get("project") or "", "voice": sheet.get("id") or ""}})


@router.get("/api/voice/takes")
def list_takes() -> list[dict]:
    return takes()


@router.get("/api/voice/takes/{take_id}")
def get_take(take_id: str) -> dict:
    return one_take(take_id)


@router.get("/api/voice/takes/{take_id}/audio")
def take_audio(take_id: str):
    take = one_take(take_id)
    target = inside(take["path"])
    if not target.is_file():
        raise HTTPException(404, "the take's file is gone (deleted to the trash folder?)")
    return FileResponse(target, media_type="audio/wav", filename=f"drama3-{take_id}.wav")


@router.get("/api/voice/takes/{take_id}/live")
def take_live(take_id: str):
    """The take that is being made, as a WAV that plays while it is made (the CUDA engine's raw PCM as it comes)."""

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", take_id):
        raise HTTPException(400, "bad take id")
    entry, began = LIVE.get(take_id), time.time()
    # a take that has started (its text is written first) and is not at the engine yet: its clip is being read
    # the page may ask before the worker has written the text: a short grace for that, never minutes for an unknown id
    while entry is None and ((HOME / "voice" / f"{take_id}.txt").is_file() or time.time() - began < LIVE_GRACE_S) \
            and not (HOME / "voice" / f"{take_id}.take.json").is_file() and time.time() - began < 300:
        time.sleep(0.2)
        entry = LIVE.get(take_id)
    if entry is None:
        raise HTTPException(404, "no take is being made under that id")
    from fastapi.responses import StreamingResponse

    return StreamingResponse(live_wav(entry), media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.post("/api/voice/cut")
def cut(body: dict) -> dict:
    """A sound file's first ``seconds`` (15 by default, a reference audio clip's limit), from ``start``, as a WAV
    beside the takes: {path, duration}."""

    source = inside(str(body.get("path") or ""))
    if not source.is_file():
        raise HTTPException(400, "no such file")
    try:
        start, seconds = max(0.0, float(body.get("start") or 0)), float(body.get("seconds") or 15)
    except (TypeError, ValueError):
        raise HTTPException(400, "start and seconds are numbers")
    if not 2 <= seconds <= 60:
        raise HTTPException(400, "a cut is 2 to 60 seconds")
    ffmpeg = tool("ffmpeg")
    if not ffmpeg:
        raise HTTPException(400, "cutting a clip needs ffmpeg, which is not installed on this machine")
    (HOME / "voice").mkdir(parents=True, exist_ok=True)
    target = HOME / "voice" / f"{source.stem}-cut{start:g}-{seconds:g}s.wav"
    try:
        done = subprocess.run([ffmpeg, "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", str(source), "-vn",
                               "-af", "afade=t=out:st=" + f"{max(seconds - 0.08, 0):.3f}" + ":d=0.08", "-ac", "1", "-ar", "44100", str(target)],
                              capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise HTTPException(400, "the cut took longer than a minute")
    if done.returncode or not target.is_file():
        raise HTTPException(400, "the cut failed: " + (done.stderr or "").strip()[-200:])
    return {"path": relative(target), "duration": media_seconds(str(target))[0]}


def attach(home: Path, runner, queue, within, short, measure, gate) -> tuple[APIRouter, callable]:
    """``runner(job, command, env)`` runs a process for a job (server.run), ``queue(kind, title, params, estimate)``
    puts a job in the app's queue, ``within`` / ``short`` turn a studio path into a full one and back, ``measure``
    is server.media_seconds and ``gate`` the QA module (its Whisper transcript)."""

    global HOME, run, enqueue, inside, relative, media_seconds, qa
    HOME, run, enqueue, inside, relative, media_seconds, qa = home, runner, queue, within, short, measure, gate
    (HOME / "voice").mkdir(parents=True, exist_ok=True)
    if not SPARK:
        threading.Thread(target=backend().check, daemon=True).start()  # the first look runs a process: not in a request
    return router, perform
