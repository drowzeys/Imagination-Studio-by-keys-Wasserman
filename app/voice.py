"""Voice: Fish Audio Drama 3 text to speech, run on this machine. Built with Fish Audio.

A take is one job in the app's queue (one at a time, so it never runs beside a MiniMax H3 render: Drama 3 alone peaks
at about 50 GB of memory). It runs the engine's own command line as a separate process, in its own environment:

    $DRAMA3_VENV/bin/python -I -m tensorfold.drama3 --text-file t.txt [--ref-audio a.wav --ref-text "..."] --out x.wav

and, for the time it runs, also holds the studio's GPU lock folder (~/studio-gpu.lock.d, the one the render scripts
outside the app take with ``mkdir``), so nothing that respects the lock loads a model beside it.

Nothing here calls Fish Audio's API. The weights are Fish Audio Materials under the Fish Audio Community License v1.0:
research, non-commercial and small-company commercial use; a hosted text-to-speech or voice-cloning service needs a
licence of its own from Fish Audio; a real person's voice is cloned only with that person's consent, which a take with a
reference clip must state (``consent``).

Backends (``DRAMA3_BACKEND``): ``mlx`` (TensorFold's MLX port, Apple Silicon; the default on a Mac) and ``cuda``
(TensorFold's CUDA port for a DGX Spark; the default on Linux, and not available yet: the Voice tab says so).

Measured on a Mac Studio M5 Ultra (TensorFold drama3-mlx f623fb1a, 8-bit experts): about 63 frames a second while
decoding against 21.5 for real time, so a take is made in about 0.4 times its length, plus about 3 s to load the
model; 47 to 52 GB of memory at the peak.
"""

from __future__ import annotations

import difflib
import json
import os
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

router = APIRouter()
HOME: Path = Path.home() / "TensorFoldStudio"
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

class Mlx:
    name, label = "mlx", "TensorFold MLX · Apple Silicon"

    def __init__(self) -> None:
        self.venv = Path(os.environ.get("DRAMA3_VENV", Path.home() / "drama3-venv")).expanduser()
        self.model = Path(os.environ.get("DRAMA3_MODEL", Path.home() / "models/drama-3-mlx")).expanduser()
        self.known: tuple[float, str] | None = None  # (when, why not) of the last look: a look runs a process

    def python(self) -> Path:
        return self.venv / "bin" / "python"

    def check(self) -> str:
        """Why Drama 3 cannot run here ("" when it can): looked at once a minute at most, never longer than 20 s."""

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
        if not (self.model / "config.json").is_file() or not (self.model / "codec.safetensors").is_file():
            return (f"No converted Drama 3 model at {self.model} (python -m tensorfold.families.drama3.convert "
                    "~/models/drama-3 ~/models/drama-3-mlx).")
        try:
            memory = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout.strip() or 0)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            memory = 0
        if memory and memory < MEMORY_GB * 1024 ** 3:
            return f"This Mac has {memory / 1024 ** 3:.0f} GB of memory; Drama 3 needs about 52 GB free, so {MEMORY_GB} GB or more."
        try:
            done = subprocess.run([str(self.python()), "-I", "-c", "import importlib.util as u; print(bool(u.find_spec('tensorfold.drama3')))"],
                                  capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"The Drama 3 environment did not answer: {error}"
        if done.stdout.strip() != "True":
            return f"The environment at {self.venv} has no tensorfold.drama3 (install TensorFold's drama3-mlx branch into it)."
        return ""

    def command(self, text_file: Path, out: Path, stats: Path, reference: tuple[Path, str] | None, seed: int, sampling: dict) -> list[str]:
        command = [str(self.python()), "-I", "-m", "tensorfold.drama3", "--model", str(self.model), "--text-file", str(text_file),
                   "--out", str(out), "--stats", str(stats), "--seed", str(seed), "--temperature", f"{sampling['temperature']:g}",
                   "--top-p", f"{sampling['top_p']:g}", "--top-k", str(int(sampling["top_k"]))]
        if reference:
            command += ["--ref-audio", str(reference[0]), "--ref-text", reference[1]]
        return command


class Cuda:
    """TensorFold's CUDA port of Drama 3 for a DGX Spark: still being built. When it lands, ``check`` looks for its
    environment and ``command`` returns its command line (same arguments, same stats file); nothing else changes."""

    name, label = "cuda", "TensorFold CUDA · DGX Spark"

    def check(self) -> str:
        return ("Not available yet on a DGX Spark: TensorFold's CUDA port of Drama 3 is still being built. "
                "The Voice step runs on a Mac (MLX) for now.")

    def command(self, *args, **kwargs) -> list[str]:
        raise RuntimeError(self.check())


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
    return {"available": not why, "why": why, "backend": one.name, "label": one.label, "credit": CREDIT, "licence": LICENCE,
            "defaults": {k: v[0] for k, v in SAMPLING.items()}, "ranges": {k: v[1:] for k, v in SAMPLING.items()},
            "text_limit": TEXT_LIMIT, "reference_seconds": REFERENCE_SECONDS, "speakers": 16}


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
    seconds = expected_seconds(text)
    # load 3 s, prefill under 0.5 s a chunk, frames at ~63 a second, the codec; a reference is encoded first;
    # Whisper on the CPU after it (loading included)
    return int(4 + seconds * (FRAME_RATE / 60 + 0.05) + (3 if referenced else 0) + (6 + seconds * 0.4 if asr else 0))


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


def take_lock(job: dict) -> bool:
    """Waits for the GPU lock (a job that is cancelled meanwhile stops waiting): True once it is this job's."""

    mine = f"studio-voice {job['id']} {os.getpid()}"
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
        if not ref_text and not qa.ready()["transcript"]:
            raise HTTPException(400, "write what the reference clip says (this machine has no speech-to-text tool to hear it)")
        params.update(ref_audio=relative(clip), ref_text=ref_text, consent=True)
    params["asr"] = body.get("asr", True) is not False
    if isinstance(body.get("slate"), dict):  # a Slate voice sheet's cast voice: which film and sheet it belongs to
        params["slate"] = {k: str(v)[:80] for k, v in body["slate"].items() if k in ("project", "voice")}
    return params


def perform(job: dict) -> bool:
    """One voice take to its end; False when it was cancelled meanwhile."""

    params, folder = job["params"], HOME / "voice"
    folder.mkdir(parents=True, exist_ok=True)
    stem = folder / job["id"]
    wav, stats, text_file = stem.with_suffix(".wav"), stem.with_suffix(".stats.json"), stem.with_suffix(".txt")
    text_file.write_text(params["text"] + "\n")
    reference = None
    if params.get("ref_audio"):
        job["progress"] = {"stage": "reading the voice reference", "at": 0, "of": 1}
        source, clean = inside(params["ref_audio"]), stem.with_suffix(".ref.wav")
        ffmpeg = tool("ffmpeg")
        if ffmpeg:  # any sound file or video: the engine reads WAV, FLAC and OGG itself
            done = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "44100", str(clean)],
                                  capture_output=True, text=True, timeout=120)
            if done.returncode:
                raise RuntimeError("the reference clip could not be read: " + done.stderr.strip()[-200:])
        else:
            shutil.copy(source, clean)
        if not params.get("ref_text"):
            heard = qa.transcribe(clean)
            if not heard:
                raise RuntimeError("no transcript of the reference clip: write what it says")
            params["ref_text"] = heard.strip()
        reference = (clean, params["ref_text"])
    command = backend().command(text_file, wav, stats, reference, int(params["seed"]),
                                {k: params[k] for k in SAMPLING})
    if not take_lock(job):
        return False
    try:
        job["progress"] = {"stage": "Drama 3 voice", "at": 0, "of": 1}
        job["engine"] = backend().name
        if not run(job, command, {}):
            return False
    finally:
        drop_lock(job)
    if not wav.is_file():
        raise RuntimeError("Drama 3 finished without writing its take")
    try:
        measured = json.loads(stats.read_text())
    except (OSError, ValueError):
        measured = {}
    take = {"id": job["id"], "path": relative(wav), "title": params.get("title") or job.get("title"), "text": params["text"],
            "seed": params["seed"], **{k: params[k] for k in SAMPLING}, "backend": backend().name, "created": time.time(),
            "duration": measured.get("audio_s"), "frames": measured.get("frames"), "frames_per_s": measured.get("frames_per_s"),
            "rtf": measured.get("rtf"), "total_s": measured.get("total_s"), "peak_memory_gb": measured.get("peak_memory_gb"),
            "chunks": measured.get("chunks"), "finish": measured.get("finish"),
            "reference": {"path": params["ref_audio"], "text": params["ref_text"], "consent": True} if params.get("ref_audio") else None,
            "slate": params.get("slate"), "credit": CREDIT}
    if params.get("asr") and qa.ready()["transcript"]:
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
