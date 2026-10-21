"""Music: MiniMax Music 3 (MiniMax-Music3) songs from lyrics and a style caption, made on this machine.

Lyrics with section tags ([Verse], [Chorus], [Bridge], [Instrumental] ...) and a caption of the style (genre, tempo,
key, mood, vocals, instruments) become a song of up to five minutes with sung vocals, 44.1 kHz stereo. A song is one
job in the app's queue, so it never runs beside a MiniMax H3 render or a Drama 3 take. Four routes, all run outside the
app's own process:

- ``metal`` (a Mac with an M5 or later, preferred there when built): TensorFold's Metal port (fork branch
  ``upstream/music3-metal``, ``$PREFIX/music3/bin/music3-generate --serve 1``, Zig + Metal, no Python), the CUDA
  route's binary protocol and lifecycle; int8 weights by default (``MUSIC3_WEIGHTS=bf16``). It does not stream yet.
  Without the binary or the weights, or on an M1 to M4, a Mac falls back to ``mlx``.
- ``mlx`` (a Mac): TensorFold's MLX port (fork branch ``music3-mlx``, ``python -m tensorfold.music3 --serve`` in its
  own venv), a persistent server that speaks Drama 3's protocol (voice.Served): loaded on the first song, holding the
  studio's GPU lock while loaded, unloaded after ``MUSIC3_IDLE_S`` idle seconds and at once before any other job of
  this machine's queue (``release``), under a memory kill-guard (``MUSIC3_GUARD_GB``).
- ``comfy`` (a DGX Spark): ComfyUI's own MiniMax Music 3 nodes through ``spark/spark_generate.py music``, in the
  studio's ComfyUI server (spark/comfy.sh, whose memory guard stands beside it). The server keeps the song's models
  loaded after it, which counts as loaded here: the GPU lock is held, and they are freed (ComfyUI's /free) after the
  idle time and before any other job, as for Drama 3. A song starts from a server with nothing else loaded.
- ``cuda`` (a DGX Spark, preferred there when built): TensorFold's CUDA port (fork branch ``music3-cuda``,
  ``$PREFIX/music3/bin/music3-generate --serve 1``), the Mac engine's protocol and lifecycle (voice.Served), int8
  weights made on the GPU as it loads (``MUSIC3_WEIGHTS=bf16`` for the checkpoint's own). A ComfyUI holding models is
  stopped before it loads. Without the binary or the weights a Spark falls back to ``comfy``; ``MUSIC3_BACKEND``
  chooses.

The weights are Comfy-Org/MiniMax-Music-3's (MiniMax-Music3 Community License): commercial use is allowed, a
commercial product shows "MiniMax-Music3" in its interface (the Music tab does), a company above US$20 million of
yearly revenue needs MiniMax's written authorisation, and a hosted service keeps safeguards against misuse.

A song can be laid under a long video or the Editor's export (``lay``): ffmpeg mixes it under the clip's own sound,
ducked under the dialogue, with the app's sound finish (no hard clip, peaks under -1 dBFS).

On a Mac a song can play while it is made (``live``): the engine's stream (``pcm`` in the request, as Drama 3's server)
appends raw 16-bit stereo PCM to a file as each piece is decoded, and ``/api/music/takes/{id}/live`` serves it as one
stereo WAV whose sound is held back until enough is made that playing never runs dry (``lead_s``). The kept take is the
engine's finished WAV, as without streaming. The MLX route alone streams: not the Metal one yet, nor a Spark (its
CUDA engine and ComfyUI's route alike).
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

import voice

router = APIRouter()
HOME: Path = Path.home() / "ImaginationStudio"
PACK = Path(__file__).resolve().parent.parent
SPARK = sys.platform.startswith("linux")
MAC = sys.platform == "darwin"
PREFIX = Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio")).expanduser()


def comfy_kind_path() -> Path:
    """The loaded-model mark for this process's ComfyUI. Port 8190 keeps PREFIX/comfy.kind."""

    spark = str(Path(__file__).resolve().parent.parent / "spark")
    if spark not in sys.path:
        sys.path.insert(0, spark)
    import memory_guard
    return Path(memory_guard.comfy_file("kind"))
SECONDS = (10, 300)  # the longest a song may be, as the app offers it (the model takes up to 360 s)
CAPTION_LIMIT, LYRICS_LIMIT = 2000, 6000  # characters (the model reads 5000 tokens of caption and lyrics in all)
SAMPLING = {"steps": (30, 8, 60), "dit_cfg": (1.7, 1.0, 5.0), "ar_cfg": (1.5, 1.0, 5.0), "top_k": (50, 1, 500)}
CREDIT = "MiniMax-Music3"
LICENCE = ("MiniMax-Music3 runs on this machine under the MiniMax-Music3 Community License: commercial use is allowed, "
           "a commercial product shows “MiniMax-Music3” in its interface, a company above US$20 million of yearly revenue "
           "needs MiniMax's written authorisation, and a hosted service keeps safeguards against misuse.")
SECTIONS = ("Intro", "Verse", "Pre-Chorus", "Chorus", "Post-Chorus", "Bridge", "Hook", "Instrumental", "Solo", "Break",
            "Build", "Outro")
TAG = re.compile(r"\[([^\]\n]{1,40})\]")
# the song under a clip (lay): the music's level against the clip's own sound, and the most it is lowered under speech
MUSIC_DB, DUCK_DB = -10.0, 12.0
PEAK_DBFS = -1.0  # no peak over this, measured between the samples (four times the rate)
# Played while it is made (the Mac engine's stream, music3-mlx 6844b413, int8, overlap on), measured on a Mac Studio M5
# Ultra (docs/recipes/minimax-music3-mlx.md there): seconds of making a second of song when streamed, by length; the
# first sound came after 7.6 s (60 s song) and 8.0 s (300 s), and the earliest start that never ran dry (NO_STALL_S,
# seconds from the request) was 10.3 s and 52.7 s. A stream is a little slower than real time and slows down as the
# song gets longer (through the app, 60 s hip-hop: a 4 s piece every 4.2 s at the start, every 4.7 s near the end),
# so the player waits longer the longer the song. Through the app a start at 10.7 s ran dry for 0.18 s at 53 s
# (README, Music): hence the margin.
STREAM_RTF = ((60.0, 1.06), (300.0, 1.15))
NO_STALL_S = ((60.0, 10.3), (300.0, 52.7))
HOLD_MARGIN_S = 3.0  # seconds after the measured no-stall start
LEAD_MARGIN_S, LEAD_MIN_S = 2.0, 2.0  # seconds of song beyond the lead the rate asks for, and the least ever held
RATE, CHANNELS = 44100, 2
SPARK_COMFY_URL = os.environ.get("SPARK_COMFY_URL", "http://127.0.0.1:" + os.environ.get("SPARK_COMFY_PORT", "8190"))


def tool(name: str) -> str | None:
    return voice.tool(name)


# ---------- the routes ----------

class Mlx(voice.Served):
    """TensorFold's MLX port of MiniMax Music 3 on a Mac (``python -m tensorfold.music3 --serve``), in its own venv
    (``scripts/setup-music.sh``: the ``music3-mlx`` branch at a pinned commit). Measured on a Mac Studio M5 Ultra:
    see ``load_s``, ``RTF`` and ``NEED_GB``."""

    name, label = "mlx", "TensorFold MLX · Apple Silicon"
    title, owner_tag, idle_env, guard_env = "MiniMax Music 3", "studio-music music3", "MUSIC3_IDLE_S", "MUSIC3_GUARD_GB"
    load_s = 5.0  # measured: 0.9 s to ready with the files in the page cache; more from a cold disk (not measured)
    # seconds of making a second of song, measured through the app on a Mac Studio M5 Ultra: a 60 s song in 75.8 s
    RTF = 1.25
    # free memory wanted before it loads: MLX's own peak was 27.6 GB for that song and the machine's memory in use rose
    # by 32.9 GB, and the guard's 8 GB beside it
    NEED_GB = 42.0
    MEMORY_GB = 64  # a Mac with less does not run it

    def __init__(self) -> None:
        super().__init__()
        self.venv = Path(os.environ.get("MUSIC3_VENV", PREFIX / "music3-venv")).expanduser()
        self.model = Path(os.environ.get("MUSIC3_MODEL", Path.home() / "music3-models" / "MiniMax-Music3-Comfy")).expanduser()
        # a TensorFold source tree to run from instead of the venv's own install (a checkout of music3-mlx)
        self.src = os.environ.get("MUSIC3_SRC", "")
        # int8 weights by default (measured: AR 40 -> 65 frames/s, peak 27.6 -> 20.5 GB, parity and lyrics within
        # noise of bf16; docs/recipes/minimax-music3-mlx.md on the engine branch). MUSIC3_QUANT=none runs bf16.
        self.quant = os.environ.get("MUSIC3_QUANT", "int8").strip() or "none"
        # the engine plays a song while it is made (a "pcm" path in the request; its ready line says "stream": true):
        # music3-mlx 6844b413 or later. Looked for in its source before it loads, then read from its ready line
        self.streams = False

    def python(self) -> Path:
        return self.venv / "bin" / "python"

    def argv(self) -> list[str]:
        if self.src:
            return [str(self.python()), "-I", "-c", "import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); "
                    "runpy.run_module('tensorfold.music3', run_name='__main__', alter_sys=True)",
                    str(Path(self.src).expanduser()), "--model", str(self.model), "--quant", self.quant, "--serve"]
        return [str(self.python()), "-I", "-m", "tensorfold.music3", "--model", str(self.model), "--quant", self.quant,
                "--serve"]

    def check(self) -> str:
        if self.loaded():
            return ""
        if self.known and (not self.known[1] or time.time() - self.known[0] < 60):
            return self.known[1]
        why = self.look()
        self.known = (time.time(), why)
        return why

    def look(self) -> str:
        if sys.platform != "darwin":
            return "The MLX route runs on a Mac (Apple Silicon)."
        if not self.python().is_file():
            return f"No MiniMax Music 3 environment at {self.venv} (bash scripts/setup-music.sh, or oneshot-setup.sh --music)."
        self.model = voice.located("music3-weights", self.model, has_weights)
        missing = [str(self.model / part) for part in WEIGHTS if not (self.model / part).is_file()]
        if missing:
            return (f"No MiniMax Music 3 weights at {self.model} (oneshot-setup.sh --music downloads them; MUSIC3_MODEL names another folder)."
                    + voice.found_hint("music3-weights"))
        try:
            memory = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout.strip() or 0)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            memory = 0
        if memory and memory < self.MEMORY_GB * 1024 ** 3:
            return f"This Mac has {memory / 1024 ** 3:.0f} GB of memory; MiniMax Music 3 takes about 33 GB, so {self.MEMORY_GB} GB or more."
        probe = ("import importlib.util as u; s = u.find_spec('tensorfold.music3'); "
                 "print(bool(s), bool(s and s.origin and 'q.get(\"pcm\")' in open(s.origin).read()))")
        command = [str(self.python()), "-I", "-c", probe] if not self.src else \
            [str(self.python()), "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); " + probe, str(Path(self.src).expanduser())]
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"The MiniMax Music 3 environment did not answer: {error}"
        found = done.stdout.split()
        if not found or found[0] != "True":
            return f"The environment at {self.venv} has no tensorfold.music3 (bash scripts/setup-music.sh installs TensorFold's music3-mlx branch into it)."
        self.streams = found[1:2] == ["True"]
        return ""

    def streaming(self) -> bool:
        """Whether a song can play while it is made: the loaded engine's ready line, else what its source said."""

        if self.loaded() and "stream" in self.ready:
            return bool(self.ready["stream"])
        return self.streams

    def estimate(self, seconds: float) -> int:
        return int((0 if self.loaded() else self.load_s) + seconds * self.RTF + 2)

    def make(self, job: dict, params: dict, wav: Path) -> dict | None:
        """One song through the server: its measurements, or None when cancelled. Played while it is made (``live``
        asked and the engine streams): the request carries a ``pcm`` path, which ``/live`` reads as it grows."""

        stats = wav.with_suffix(".stats.json")
        request = {"id": job["id"], "caption": params["caption"], "lyrics": params["lyrics"], "wav": str(wav),
                   "seconds": float(params["seconds"]), "seed": int(params["seed"]), "ar_cfg": params["ar_cfg"],
                   "top_k": int(params["top_k"]), "steps": int(params["steps"]), "dit_cfg": params["dit_cfg"],
                   "stats": str(stats), "progress": True}
        words = {"ar": "the model writes the song", "dit": "the sound", "dav": "decoding", "stream": "made so far (s)"}
        live = bool(params.get("live", True)) and self.streaming()
        folder = wav.with_suffix(".live")
        if live:  # before the load, so a page that asks for /live meanwhile finds it (and waits for the sound)
            folder.mkdir(parents=True, exist_ok=True)
            LIVE[job["id"]] = {"parts": [folder / "0.pcm"], "done": False, "finished": 0, "gap_s": 0,
                               "seconds": float(params["seconds"]), "asked": None, "played": None}

        def progress(said: dict) -> None:
            stage = str(said.get("stage") or "")
            job["progress"] = {"stage": "music: " + words.get(stage, stage), "at": int(said.get("done") or 0), "of": int(said.get("total") or 1)}

        with self.mutex:
            began = time.time()
            try:
                if not self.start(job):
                    return None
                loaded = time.time()
                live = live and self.streaming()  # the ready line has the last word
                if job["id"] in LIVE and not live:
                    LIVE[job["id"]]["done"] = True
                job["progress"] = {"stage": "music: the model writes the song", "at": 0, "of": 1}
                if live:
                    request.update(pcm=str(LIVE[job["id"]]["parts"][0]), stream=True)
                    LIVE[job["id"]]["asked"] = time.time()
                answer = self.ask(job, request, 600 + 15 * float(params["seconds"]), progress)
                if answer is None:
                    return None
            finally:
                self.last_used = time.time()
                if job["id"] in LIVE:
                    LIVE[job["id"]].update(finished=1, done=True)
                    # the raw PCM goes a minute later: a page may still be playing the end of the song
                    threading.Timer(60, forget_live, args=(job["id"], folder)).start()
        entry = LIVE.get(job["id"]) or {}
        played = entry.get("played")
        streamed = {"stream": bool(answer.get("stream")), "first_audio_s": round(float(answer["first_audio_ms"]) / 1000, 1)
                    if live and answer.get("first_audio_ms") is not None else None,
                    "play_s": round(played - entry["asked"], 1) if live and played and entry.get("asked") else None,
                    "lead_s": entry.get("lead_s") if live else None} if live else {}
        return {**streamed, "audio_s": answer.get("audio_s"), "frames": answer.get("frames"), "eos": answer.get("eos"),
                "ar_s": round(float(answer.get("ar_ms") or 0) / 1000, 1), "dit_s": round(float(answer.get("dit_ms") or 0) / 1000, 1),
                "dav_s": round(float(answer.get("dav_ms") or 0) / 1000, 1), "engine_s": round(float(answer.get("total_ms") or 0) / 1000, 1),
                "rtf": answer.get("rtf"), "peak_memory_gb": answer.get("peak_memory_gb"), "load_s": job.get("load_s"),
                "total_s": round(time.time() - began, 1), "made_s": round(time.time() - loaded, 1)}


# ---------- played while it is made ----------

LIVE: dict[str, dict] = {}  # a running song's raw PCM, as the engine writes it (/live)


def forget_live(job_id: str, folder: Path) -> None:
    LIVE.pop(job_id, None)
    shutil.rmtree(folder, ignore_errors=True)


def stream_rtf(seconds: float) -> float:
    """The measured seconds of making a second of streamed song, between the two lengths measured (held beyond)."""

    (a, ra), (b, rb) = STREAM_RTF
    at = min(max(float(seconds), a), b)
    return ra + (rb - ra) * (at - a) / (b - a)


def start_s(seconds: float) -> float:
    """Seconds from the request to the engine before a song of ``seconds`` is let play: the measured no-stall start,
    along the line through the two lengths measured (beyond them too), and HOLD_MARGIN_S."""

    (a, ta), (b, tb) = NO_STALL_S
    return round(max(0.0, ta + (tb - ta) * (float(seconds) - a) / (b - a)) + HOLD_MARGIN_S, 2)


def lead_s(seconds: float, rate: float | None = None) -> float:
    """Seconds of song to have made before playing starts, for a song of ``seconds`` (its cap: one the model ends sooner
    is waited for a little more than it needed). The engine makes ``r`` seconds of song a second (the measured rate
    since the first sound when there is one, never more than STREAM_RTF says); played from the start, the end is the
    tightest point, so the lead is the part of the song it cannot make while the rest plays, and a margin."""

    r = 1 / stream_rtf(seconds)
    if rate is not None and rate > 0:
        r = min(r, rate)
    return round(min(float(seconds), max(LEAD_MIN_S, seconds * (1 - r) + LEAD_MARGIN_S)), 2)


def hold(entry: dict, poll: float = 0.1, limit_s: float = 900) -> None:
    """Waits until the song may play without running dry, or is done: ``start_s`` after the request reached the engine
    (the measured no-stall start), and the stream ``lead_s`` ahead at the rate it is going (a slower engine than the
    one measured waits for more). The rate is the PCM file's growth from its first piece to its latest one (the engine
    writes it a piece of a few seconds at a time, so the time since the latest piece is not counted)."""

    pcm, first, latest, began = entry["parts"][0], None, None, time.time()
    while not entry["done"] and time.time() - began < limit_s:
        try:
            made = pcm.stat().st_size / (2 * CHANNELS * RATE)
        except OSError:
            made = 0.0
        now = time.time()
        if made and first is None:
            first = latest = (now, made)
        elif made and made > latest[1]:
            latest = (now, made)
        asked = entry.get("asked")
        if made and asked and now - asked >= start_s(entry["seconds"]):
            rate = (latest[1] - first[1]) / (latest[0] - first[0]) if latest[0] - first[0] >= 3 else None
            if made >= lead_s(entry["seconds"], rate):
                entry.setdefault("lead_s", round(made, 1))
                break
        time.sleep(poll)
    if entry.get("played") is None:
        entry["played"] = time.time()


def live_song(entry: dict):
    """The song being made as one stereo 44.1 kHz WAV: the header at once, the sound once ``hold`` lets it go."""

    stream = voice.live_wav(entry, rate=RATE, channels=CHANNELS)
    yield next(stream)
    hold(entry)
    yield from stream


WEIGHTS = ("diffusion_models/minimax_music3_dit_fp16.safetensors", "text_encoders/minimax_music3_text_encoder_pruned_bf16.safetensors",
           "vae/minimax_music3_dav.safetensors")


def has_weights(folder: Path) -> bool:
    return all((folder / part).is_file() for part in WEIGHTS)


class Comfy:
    """ComfyUI's MiniMax Music 3 nodes on a DGX Spark (``spark_generate.py music``). The studio's ComfyUI server keeps
    the song's models after a song: that is this route's loaded state (``comfy.kind`` reads "music"). While loaded it
    holds the GPU lock; ``release`` (before any other job) and the idle thread free the models with ComfyUI's /free."""

    name, label = "comfy", "ComfyUI · DGX Spark (CUDA)"
    title, owner_tag = "MiniMax Music 3", "studio-music comfy"
    # measured on a DGX Spark through spark_generate.py (README, Music): seconds of making a second of song (60 s in
    # 260.6 s, 120 s in 527.6 s), and the start of ComfyUI with the three files' load (7.6 s with them in the page
    # cache; from a cold disk not measured, so more is allowed)
    RTF, load_s = 4.4, 30.0

    def __init__(self) -> None:
        self.idle_s = float(os.environ.get("MUSIC3_IDLE_S", 300))
        self.models = Path(os.environ.get("SPARK_MODELS", Path.home() / "spark-studio-models")).expanduser() / "MiniMax-Music3-Comfy"
        self.mutex = threading.RLock()
        self.owner = ""
        self.last_used = 0.0
        self.known: tuple[float, str] | None = None
        self.events: list[dict] = []
        self.idler: threading.Thread | None = None

    def note(self, what: str, **more) -> None:
        self.events = (self.events + [{"at": time.time(), "what": what, **more}])[-20:]

    def kind(self) -> str:
        try:
            return comfy_kind_path().read_text().strip()
        except OSError:
            return ""

    def loaded(self) -> bool:
        return bool(self.owner) and self.kind() == "music" and voice.comfy_up()

    def status(self) -> dict:
        return {"loaded": self.loaded(), "idle_unload_s": self.idle_s, "load_s": self.load_s,
                "idle_for_s": round(time.time() - self.last_used, 1) if self.loaded() and self.last_used else None,
                "events": self.events[-5:]}

    def check(self) -> str:
        if self.loaded():
            return ""
        if self.known and time.time() - self.known[0] < 10:
            return self.known[1]
        why = self.look()
        self.known = (time.time(), why)
        return why

    def look(self) -> str:
        if not SPARK:
            return "The ComfyUI route runs on a DGX Spark (Linux, CUDA)."
        if not (PREFIX / "ComfyUI" / "comfy_extras" / "nodes_minimax_music.py").is_file():
            return f"The ComfyUI at {PREFIX / 'ComfyUI'} has no MiniMax Music 3 nodes (oneshot-setup-spark.sh moves it to the pinned commit)."
        missing = [part for part in WEIGHTS if not (self.models / part).is_file()]
        if missing:  # ComfyUI reads its own folders: a file found elsewhere is used once linked there (a symlink)
            return f"No MiniMax Music 3 weights at {self.models} (bash oneshot-setup-spark.sh --music, 22 GB)." + voice.found_hint("music3-weights")
        return ""

    def estimate(self, seconds: float) -> int:
        return int((0 if self.loaded() else self.load_s) + seconds * self.RTF + 5)

    def command(self, params: dict, caption: Path, lyrics: Path, wav: Path) -> list[str]:
        return [str(PREFIX / "venv" / "bin" / "python"), str(PACK / "spark" / "spark_generate.py"), "music",
                "--caption-file", str(caption), "--lyrics-file", str(lyrics), "--seconds", f"{float(params['seconds']):g}",
                "--seed", str(int(params["seed"])), "--steps", str(int(params["steps"])), "--cfg", f"{params['dit_cfg']:g}",
                "--ar-cfg", f"{params['ar_cfg']:g}", "--top-k", str(int(params["top_k"])), "-o", str(wav)]

    def make(self, job: dict, params: dict, wav: Path) -> dict | None:
        caption, lyrics = wav.with_suffix(".caption.txt"), wav.with_suffix(".lyrics.txt")
        caption.write_text(params["caption"] + "\n")
        lyrics.write_text(params["lyrics"] + "\n")
        command = self.command(params, caption, lyrics, wav)
        with self.mutex:
            began = time.time()
            if not self.owner:
                mine = f"{self.owner_tag} {os.getpid()}"
                if not voice.take_lock(job, mine):
                    return None
                job.pop("lock", None)  # the loaded models', given back when they are freed
                self.owner = mine
            if self.idler is None:
                self.idler = threading.Thread(target=self.idle, daemon=True)
                self.idler.start()
            was = self.loaded()
            mark = len(job.get("log") or "")
            try:
                if not run(job, command, {"PREFIX": str(PREFIX)}, "music: "):
                    self.interrupt()  # the script is gone; ComfyUI would finish the song for nobody
                    self.stop("the song was cancelled")
                    return None
            except BaseException:
                if self.kind() != "music" or not voice.comfy_up():
                    self.stop("the song failed")
                raise
            finally:
                self.last_used = time.time()
            if not was:
                self.note("loaded")
        said = [line for line in (job.get("log") or "")[mark:].splitlines() if line.startswith("[tensorfold] {")]
        try:
            answer = json.loads(said[-1].split("] ", 1)[1]) if said else {}
        except ValueError:
            answer = {}
        audio = answer.get("audio_s")
        total = round(time.time() - began, 1)
        for one in (caption, lyrics):
            one.unlink(missing_ok=True)
        return {"audio_s": audio, "eos": answer.get("eos"), "ar_s": answer.get("ar_s"), "dit_s": answer.get("dit_s"),
                "dav_s": answer.get("dav_s"), "engine_s": answer.get("total_s"), "load_s": answer.get("load_s") if not was else None,
                "rtf": round(total / audio, 2) if audio else None, "total_s": total, "peak_dbfs_decoded": answer.get("peak_dbfs_decoded"),
                "limited": answer.get("limited")}

    def interrupt(self) -> None:
        try:
            request = urllib.request.Request(SPARK_COMFY_URL + "/interrupt", data=b"{}", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=10):
                pass
        except OSError:
            pass

    def free(self) -> bool:
        """ComfyUI's models freed (its /free), the server left running: True when it answered."""

        try:
            request = urllib.request.Request(SPARK_COMFY_URL + "/free", data=json.dumps({"unload_models": True, "free_memory": True}).encode(),
                                             headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=30):
                pass
        except OSError:
            return False
        try:
            comfy_kind_path().unlink()
        except OSError:
            pass
        voice.settle()
        return True

    def stop(self, why: str) -> None:
        with self.mutex:
            if self.kind() == "music" and voice.comfy_up():
                self.free()
                self.note("unloaded", why=why, available_gb=round(voice.mem_available_gb(), 1))
            if self.owner and voice.lock_owner() == self.owner:
                shutil.rmtree(voice.LOCK, ignore_errors=True)
            self.owner = ""

    def release(self, why: str) -> bool:
        with self.mutex:
            if not self.owner:
                return False
            self.stop(why)
            return True

    def idle(self) -> None:
        while True:
            time.sleep(5)
            if not self.owner or time.time() - self.last_used < self.idle_s or not self.mutex.acquire(blocking=False):
                continue
            try:
                if self.owner and time.time() - self.last_used >= self.idle_s:
                    self.stop(f"idle for {self.idle_s:g} s")
            finally:
                self.mutex.release()


class Cuda(voice.Served):
    """TensorFold's CUDA port of MiniMax Music 3 on a DGX Spark (``music3-generate --serve 1``, Zig + CUDA, no Python;
    fork branch ``music3-cuda``, built by ``spark/build-music3.sh`` into ``$PREFIX/music3``). It speaks the Mac
    engine's protocol (one JSON request a line on stdin, one answer a line on stderr), so it is a ``voice.Served``
    like the Mac route and Drama 3's CUDA engine: loaded on the first song, holding the GPU lock while loaded,
    unloaded after ``MUSIC3_IDLE_S`` and before any other job (``release``), under the kill-guard
    (``MUSIC3_GUARD_GB``). Before it loads, a ComfyUI of the studio's that holds models is stopped (``make_room``).
    It reads Comfy-Org's three files as they are; int8 weights are made on the GPU as it loads (no cache file).
    Measured on one GB10 (docs/recipes/minimax-music3-cuda.md on the engine branch): see ``load_s``, ``RTF``,
    ``NEED_GB`` and memory_guard.MUSIC_CUDA_ESTIMATE."""

    name, label = "cuda", "TensorFold CUDA · DGX Spark"
    title, owner_tag, idle_env, guard_env = "MiniMax Music 3", "studio-music music3-cuda", "MUSIC3_IDLE_S", "MUSIC3_GUARD_GB"
    load_s = 37.0  # measured: 36.6 to 37.4 s to ready, int8 made at load, the files in the page cache
    # seconds of making a second of song, measured on a GB10, int8: 30 s in 60.3 s, 60 s in 123.9 s, 120 s in 252.0 s
    # (bf16: 60 s in 194.3 s)
    RTF_OF = {"int8": 2.1, "bf16": 3.3}
    # free memory wanted before it loads: the song's peak above an idle machine (19.3 GB int8 at 120 s, 26.6 GB bf16)
    # and the guard's 8 GB beside it
    NEED_OF = {"int8": 30.0, "bf16": 38.0}

    def __init__(self) -> None:
        super().__init__()
        here = PREFIX / "music3"
        self.bin = Path(os.environ.get("MUSIC3_CUDA_BIN", here / "bin" / "music3-generate")).expanduser()
        self.model = Path(os.environ.get("MUSIC3_MODEL", Path(os.environ.get("SPARK_MODELS", Path.home() / "spark-studio-models"))
                                         / "MiniMax-Music3-Comfy")).expanduser()
        weights = os.environ.get("MUSIC3_WEIGHTS", "int8").strip().lower() or "int8"
        self.weights = weights if weights in self.RTF_OF else "int8"
        self.RTF, self.NEED_GB = self.RTF_OF[self.weights], self.NEED_OF[self.weights]
        self.guard_route = "music-cuda-" + self.weights

    def environment(self) -> dict:
        env = dict(os.environ)
        # the engine opens cuBLAS 13 itself (dlopen): the toolkit's folder, where the loader may not look
        cuda = [one for one in ("/usr/local/cuda/lib64", "/usr/local/cuda/targets/sbsa-linux/lib") if Path(one).is_dir()]
        if cuda:
            env["LD_LIBRARY_PATH"] = ":".join(cuda + ([env["LD_LIBRARY_PATH"]] if env.get("LD_LIBRARY_PATH") else []))
        return env

    def argv(self) -> list[str]:
        return [str(self.bin), "--model", str(self.model), "--weights", self.weights, "--serve", "1"]

    def check(self) -> str:
        """Why it cannot run here ("" when it can): files looked at, then memory; at most every 10 s. No process."""

        if self.loaded():
            return ""
        if self.known and time.time() - self.known[0] < 10:
            return self.known[1]
        why = self.look()
        self.known = (time.time(), why)
        return why

    def present(self) -> str:
        """What is missing for this route ("" when the engine and the weights are there): files only."""

        if not SPARK:
            return "The CUDA route runs on a DGX Spark (Linux, CUDA)."
        self.bin = voice.located("music3-binary", self.bin, lambda p: os.access(p, os.X_OK))
        self.model = voice.located("music3-weights", self.model, has_weights)
        if not os.access(self.bin, os.X_OK):
            return (f"No MiniMax Music 3 engine at {self.bin} (bash spark/build-music3.sh, or oneshot-setup-spark.sh --music; "
                    "MUSIC3_CUDA_BIN names another)." + voice.found_hint("music3-binary"))
        missing = [part for part in WEIGHTS if not (self.model / part).is_file()]
        if missing:
            return (f"No MiniMax Music 3 weights at {self.model} (bash oneshot-setup-spark.sh --music, 22 GB; MUSIC3_MODEL names another folder)."
                    + voice.found_hint("music3-weights"))
        return ""

    def look(self) -> str:
        why = self.present()
        if why:
            return why
        free = voice.mem_available_gb()
        if 0 <= free < self.NEED_GB and not voice.comfy_up():
            return (f"MiniMax Music 3 needs about {self.NEED_GB:.0f} GB of free memory and this Spark has {free:.0f} GB available "
                    "now (something outside the studio holds the rest).")
        return ""

    def make_room(self, job: dict) -> None:
        """ComfyUI's models never stand beside the song: a studio ComfyUI that holds any (``comfy.kind``) is stopped
        first, as Drama 3 does it (spark/comfy.sh stop; the next render starts it again), then the memory is checked."""

        if self.comfy_holds() and voice.comfy_up():
            job["progress"] = {"stage": "stopping ComfyUI to free its models' memory", "at": 0, "of": 1}
            self.stop_comfy()
            for _ in range(60):
                if not voice.comfy_up():
                    break
                time.sleep(1)
            voice.settle()
            self.note("stopped ComfyUI before loading", available_gb=round(voice.mem_available_gb(), 1))
        super().make_room(job)

    def comfy_holds(self) -> str:
        """What the studio's ComfyUI server has loaded (spark_generate.py writes it), "" for nothing."""

        try:
            return comfy_kind_path().read_text().strip()
        except OSError:
            return ""

    def stop_comfy(self) -> None:
        subprocess.run(["bash", str(PACK / "spark" / "comfy.sh"), "stop"], capture_output=True, text=True, timeout=30,
                       env={**os.environ, "PREFIX": str(PREFIX)})

    def estimate(self, seconds: float) -> int:
        return int((0 if self.loaded() else self.load_s) + seconds * self.RTF + 2)

    def make(self, job: dict, params: dict, wav: Path) -> dict | None:
        """One song through the server: its measurements, or None when cancelled. Not streamed."""

        stats = wav.with_suffix(".stats.json")
        request = {"id": job["id"], "caption": params["caption"], "lyrics": params["lyrics"], "wav": str(wav),
                   "seconds": float(params["seconds"]), "seed": int(params["seed"]), "ar_cfg": params["ar_cfg"],
                   "top_k": int(params["top_k"]), "steps": int(params["steps"]), "dit_cfg": params["dit_cfg"],
                   "stats": str(stats), "progress": True}
        words = {"ar": "the model writes the song", "dit": "the sound", "dav": "decoding"}

        def progress(said: dict) -> None:
            stage = str(said.get("stage") or "")
            job["progress"] = {"stage": "music: " + words.get(stage, stage), "at": int(said.get("done") or 0), "of": int(said.get("total") or 1)}

        with self.mutex:
            began = time.time()
            try:
                if not self.start(job):
                    return None
                loaded = time.time()
                job["progress"] = {"stage": "music: the model writes the song", "at": 0, "of": 1}
                answer = self.ask(job, request, 600 + 15 * float(params["seconds"]), progress)
                if answer is None:
                    return None
            finally:
                self.last_used = time.time()
        return {"audio_s": answer.get("audio_s"), "frames": answer.get("frames"), "eos": answer.get("eos"),
                "ar_s": round(float(answer.get("ar_ms") or 0) / 1000, 1), "dit_s": round(float(answer.get("dit_ms") or 0) / 1000, 1),
                "dav_s": round(float(answer.get("dav_ms") or 0) / 1000, 1), "engine_s": round(float(answer.get("total_ms") or 0) / 1000, 1),
                "rtf": answer.get("rtf"), "peak_memory_gb": answer.get("peak_memory_gb"), "load_s": job.get("load_s"),
                "weights": self.ready.get("weights") or self.weights,
                "total_s": round(time.time() - began, 1), "made_s": round(time.time() - loaded, 1)}


class Metal(Cuda):
    """TensorFold's Metal port of MiniMax Music 3 on a Mac (``music3-generate --serve 1``, Zig + Metal, no Python;
    fork branch ``upstream/music3-metal``, built by ``scripts/build-music3-metal.sh`` into ``$PREFIX/music3``). The
    same binary's protocol and lifecycle as the CUDA route; it needs an M5 or later (the DiT, prompt rows and DAV run on
    its tensor units), so an older Mac stays on ``mlx``. Not streamed. Measured on a Mac Studio M5 Ultra
    (docs/recipes/minimax-music3-metal.md on the engine branch): see ``load_s``, ``RTF`` and ``NEED_GB``."""

    name, label = "metal", "TensorFold Metal · Apple Silicon"
    title, owner_tag, idle_env, guard_env = "MiniMax Music 3", "studio-music music3-metal", "MUSIC3_IDLE_S", "MUSIC3_GUARD_GB"
    load_s = 3.0  # measured: about 3 s to ready, the files in the page cache
    # seconds of making a second of song, measured on an M5 Ultra: int8 60 s in 59.5 to 63.0 s, 120 s in 123.7 s; bf16
    # 60 s in 77.2 s
    RTF_OF = {"int8": 1.1, "bf16": 1.35}
    # free memory wanted before it loads: the song's peak footprint (17.9 GB int8 at 60 s, 18.4 GB at 120 s; bf16 not
    # measured, taken as the CUDA engine's 26.6 GB) and the guard's 8 GB beside it
    NEED_OF = {"int8": 27.0, "bf16": 35.0}
    FIRST_CHIP = 5  # Apple M5

    def __init__(self) -> None:
        super().__init__()
        self.bin = Path(os.environ.get("MUSIC3_METAL_BIN", PREFIX / "music3" / "bin" / "music3-generate")).expanduser()
        self.model = Path(os.environ.get("MUSIC3_MODEL", Path.home() / "music3-models" / "MiniMax-Music3-Comfy")).expanduser()
        del self.guard_route  # the Spark's memory table; a Mac checks its free memory before loading (make_room)

    def environment(self) -> dict:
        return dict(os.environ)

    def chip(self) -> int | None:
        """The Apple M generation of this Mac (5 for an M5 Ultra), None when it does not say."""

        try:
            said = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        found = re.search(r"Apple M(\d+)", said)
        return int(found.group(1)) if found else None

    def present(self) -> str:
        if not MAC:
            return "The Metal route runs on a Mac (Apple Silicon)."
        chip = self.chip()
        if chip is not None and chip < self.FIRST_CHIP:
            return f"TensorFold's Metal engine for MiniMax Music 3 needs an Apple M5 or later; this Mac is an M{chip}."
        self.bin = voice.located("music3-binary", self.bin, lambda p: os.access(p, os.X_OK))
        self.model = voice.located("music3-weights", self.model, has_weights)
        if not os.access(self.bin, os.X_OK):
            return (f"No MiniMax Music 3 engine at {self.bin} (bash scripts/build-music3-metal.sh, or oneshot-setup.sh --music; "
                    "MUSIC3_METAL_BIN names another)." + voice.found_hint("music3-binary"))
        missing = [part for part in WEIGHTS if not (self.model / part).is_file()]
        if missing:
            return (f"No MiniMax Music 3 weights at {self.model} (oneshot-setup.sh --music downloads them; MUSIC3_MODEL names another folder)."
                    + voice.found_hint("music3-weights"))
        return ""

    def look(self) -> str:
        why = self.present()
        if why:
            return why
        free = voice.mem_available_gb()
        if 0 <= free < self.NEED_GB:
            return f"MiniMax Music 3 needs about {self.NEED_GB:.0f} GB of free memory and this Mac has {free:.0f} GB available now."
        return ""

    def make_room(self, job: dict) -> None:
        voice.Served.make_room(self, job)  # no ComfyUI on a Mac


ROUTES = {"mlx": Mlx, "metal": Metal, "comfy": Comfy, "cuda": Cuda}
_engine = None
_chosen_why = ""  # left to choose, why the native engine was not taken ("" when it was, or when told)


def choose() -> tuple[type, str]:
    """The route and, when it falls back, why: MUSIC3_BACKEND=metal|mlx|cuda|comfy when set; else TensorFold's native
    engine where its binary and the weights are (Metal on an M5 or later Mac, CUDA on a DGX Spark), else the MLX port on
    a Mac and ComfyUI's nodes on a Spark."""

    told = (os.environ.get("MUSIC3_BACKEND") or "").strip().lower()
    if told in ROUTES:
        return ROUTES[told], ""
    if not SPARK:
        why = Metal().present()
        return (Mlx, why) if why else (Metal, "")
    why = Cuda().present()
    return (Comfy, why) if why else (Cuda, "")


def engine():
    global _engine, _chosen_why
    if _engine is None:
        route, _chosen_why = choose()
        _engine = route()
    return _engine


def room_mb() -> int | None:
    """While the CUDA engine is loaded: the memory this machine had before it loaded (MB), which any other job of the
    queue gets back, since a song's engine is unloaded before that job starts (as voice.room_mb). None otherwise."""

    one = _engine
    if not isinstance(one, Cuda) or not one.loaded() or one.before_gb < 0:
        return None
    return int(one.before_gb * 1024)


def release(why: str) -> bool:
    """Before any other job of this machine's queue (server.py): a loaded MiniMax Music 3 gives back its memory and the
    GPU lock. True when it was loaded."""

    one = _engine
    return bool(one is not None and one.release(why))


def state() -> dict:
    one = engine()
    why = one.check()
    return {"available": not why, "why": why, "route": one.name, "label": one.label, "credit": CREDIT, "licence": LICENCE,
            "seconds": SECONDS, "defaults": {k: v[0] for k, v in SAMPLING.items()}, "ranges": {k: v[1:] for k, v in SAMPLING.items()},
            "sections": SECTIONS, "caption_limit": CAPTION_LIMIT, "lyrics_limit": LYRICS_LIMIT, "rtf": one.RTF,
            "music_db": MUSIC_DB, "duck_db": DUCK_DB, "server": one.status() if hasattr(one, "status") else None,
            # played while it is made: the Mac's MLX engine only (Metal does not stream yet, nor a Spark on either route)
            "stream": bool(not why and hasattr(one, "streaming") and one.streaming()),
            "stream_lead": {"no_stall_s": dict(NO_STALL_S), "margin_s": HOLD_MARGIN_S, "first_audio_s": 7.6},
            "fallback": _chosen_why or None, "weights": getattr(one, "weights", None)}


# ---------- text ----------

def sung(lyrics: str) -> str:
    """The words of the lyrics: section tags out."""

    return " ".join(TAG.sub(" ", lyrics).split())


def tags(lyrics: str) -> list[str]:
    return [one.strip() for one in TAG.findall(lyrics)]


def heard(lyrics: str, transcript: str) -> dict:
    """Whisper's transcript against the lyrics, word by word in order (difflib): ``score`` is the share of the lyrics'
    words up to the last one heard that were heard, ``reached`` how far into the lyrics the song got (a song cut by its
    length ends early). Sung words are harder to hear than spoken ones, so this is a rough count."""

    import difflib

    wanted, said = voice.words(sung(lyrics)), voice.words(transcript or "")
    blocks = [b for b in difflib.SequenceMatcher(None, wanted, said, autojunk=False).get_matching_blocks() if b.size]
    if not wanted or not blocks:
        return {"heard": transcript, "score": 0, "found": 0, "words": len(wanted), "reached": 0, "total": len(wanted)}
    last = blocks[-1].a + blocks[-1].size
    found = sum(b.size for b in blocks)
    return {"heard": transcript, "score": round(100 * found / last), "found": found, "words": last,
            "reached": round(100 * last / len(wanted)), "total": len(wanted)}


def checked(body: dict) -> dict:
    """A song's settings, checked before it is queued: the params of a ``music`` job."""

    caption = " ".join(str(body.get("caption") or "").split())
    lyrics = str(body.get("lyrics") or "").replace("\r\n", "\n").strip()
    if not caption:
        raise HTTPException(400, "describe the style of the song (genre, tempo, mood, vocals, instruments)")
    if len(caption) > CAPTION_LIMIT:
        raise HTTPException(400, f"the style is at most {CAPTION_LIMIT} characters")
    if len(lyrics) > LYRICS_LIMIT:
        raise HTTPException(400, f"the lyrics are at most {LYRICS_LIMIT} characters")
    if not lyrics:
        lyrics = "[Instrumental]"  # an instrumental: no words to sing
    try:
        seconds = float(body.get("seconds") if body.get("seconds") not in (None, "") else 60)
    except (TypeError, ValueError):
        raise HTTPException(400, "seconds is a number")
    low, high = SECONDS
    if not low <= seconds <= high:
        raise HTTPException(400, f"a song is {low} to {high} seconds long")
    params = {"caption": caption, "lyrics": lyrics, "seconds": seconds, "title": str(body.get("title") or "").strip()[:120]}
    try:
        params["seed"] = int(body.get("seed") if body.get("seed") not in (None, "") else random.randrange(1, 100000))
        for key, (default, low, high) in SAMPLING.items():
            value = body.get(key)
            value = default if value in (None, "") else (int(value) if key in ("steps", "top_k") else float(value))
            if not low <= value <= high:
                raise HTTPException(400, f"{key} is between {low:g} and {high:g}")
            params[key] = value
    except (TypeError, ValueError):
        raise HTTPException(400, "seed, steps, dit_cfg, ar_cfg and top_k are numbers")
    why = engine().check()
    if why:
        raise HTTPException(400, why)
    if SPARK:
        import memory_guard

        # a loaded Drama 3 or MiniMax Music 3 server gives its memory back before the song loads (or is the song's)
        held = [one for one in (voice.room_mb(), room_mb()) if one]
        room = max([memory_guard.room_mb() or 0] + held) if held else None
        found = memory_guard.music_check(seconds, available_mb=room, route=getattr(engine(), "guard_route", "music"))
        if not found["fits"]:
            raise HTTPException(400, found["reason"])
    params["asr"] = body.get("asr", True) is not False
    params["live"] = body.get("live", True) is not False  # played while it is made, where the engine streams (a Mac)
    return params


def perform(job: dict) -> bool:
    """One song to its end; False when it was cancelled meanwhile."""

    params, folder = job["params"], HOME / "music"
    folder.mkdir(parents=True, exist_ok=True)
    wav = folder / f"{job['id']}.wav"
    one = engine()
    job["engine"] = one.name
    measured = one.make(job, params, wav)
    if measured is None:
        return False
    if not wav.is_file():
        raise RuntimeError("MiniMax Music 3 finished without writing its song")
    take = {"id": job["id"], "path": relative(wav), "title": params.get("title") or job.get("title"), "caption": params["caption"],
            "lyrics": params["lyrics"], "seconds": params["seconds"], "seed": params["seed"], **{k: params[k] for k in SAMPLING},
            "route": one.name, "created": time.time(), "duration": measured.get("audio_s"), "ended": measured.get("eos"),
            "ar_s": measured.get("ar_s"), "dit_s": measured.get("dit_s"), "dav_s": measured.get("dav_s"), "load_s": measured.get("load_s"),
            "rtf": measured.get("rtf"), "total_s": measured.get("total_s"), "peak_memory_gb": measured.get("peak_memory_gb"),
            "credit": CREDIT}
    if measured.get("stream"):  # played while it was made: when the first sound came and when the player was let start
        take.update(streamed=True, first_audio_s=measured.get("first_audio_s"), play_s=measured.get("play_s"), lead_s=measured.get("lead_s"))
    if params.get("asr") and sung(params["lyrics"]) and qa.ready()["transcript"]:
        job["progress"] = {"stage": "Whisper hears the lyrics", "at": 0, "of": 1}
        try:
            transcript = qa.transcribe(wav)
            take["asr"] = heard(params["lyrics"], transcript) if transcript is not None else {"skipped": "no speech-to-text tool"}
        except Exception as error:  # noqa: BLE001 - a song that was made is kept whatever the check does
            take["asr"] = {"skipped": str(error)[-200:]}
    elif params.get("asr") and sung(params["lyrics"]):
        take["asr"] = {"skipped": "no speech-to-text tool on this machine (pip install faster-whisper)"}
    wav.with_suffix(".take.json").write_text(json.dumps(take, indent=1) + "\n")
    job["outputs"] = [take["path"]]
    job["music"] = take
    job["progress"] = None
    return True


def takes() -> list[dict]:
    out = []
    for file in (HOME / "music").glob("*.take.json"):
        try:
            take = json.loads(file.read_text())
        except (OSError, ValueError):
            continue
        if (HOME / take.get("path", "")).is_file():
            out.append(take)
    return sorted(out, key=lambda t: -float(t.get("created") or 0))


def one_take(take_id: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", take_id):
        raise HTTPException(400, "bad take id")
    file = HOME / "music" / f"{take_id}.take.json"
    if not file.is_file():
        raise HTTPException(404, "no such song")
    return json.loads(file.read_text())


# ---------- a song under a clip ----------

def peak_dbfs(path: Path) -> float | None:
    """The highest peak of a file's sound in dBFS, between the samples too (read at four times its rate)."""

    import numpy as np

    ffmpeg = tool("ffmpeg")
    if not ffmpeg:
        return None
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-vn", "-ac", "2", "-ar", "192000", "-f", "f32le", "pipe:1"],
                         capture_output=True, timeout=600).stdout
    wave = np.frombuffer(raw, np.float32)
    if not len(wave) or not float(np.abs(wave).max()):
        return None
    return round(20 * math.log10(float(np.abs(wave).max())), 2)


def mix_filter(has_sound: bool, length: float, music_db: float, duck_db: float, limit_dbfs: float) -> str:
    """The ffmpeg filter that lays input 1 (the song) under input 0 (the clip) as ``[a]``: the song brought to
    ``music_db``, faded in and out at the clip's ends; under the clip's dialogue (its sound filtered to the band of
    speech, 200 Hz to 3.5 kHz, as the key) a compressor lowers it by up to ``duck_db``; the two summed, never
    normalised; then a limiter at four times the rate keeps every peak under ``limit_dbfs``."""

    fade = min(2.0, max(length / 4, 0.05))
    song = (f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={music_db:g}dB,atrim=0:{length:.3f},"
            f"afade=t=in:d=0.3,afade=t=out:st={max(length - fade, 0):.3f}:d={fade:.3f}")
    finish = (f"aresample=192000,alimiter=limit={10 ** (limit_dbfs / 20):.4f}:level=disabled:attack=2:release=60,"
              "aresample=48000[a]")
    if not has_sound:
        return f"{song},{finish}"
    # A hard compressor keyed by the clip's speech band takes the song nearly to nothing while someone speaks; the dry
    # song beside it at -duck_db is the floor, so the song is lowered by duck_db at most (the two paths are in phase:
    # the compressor has no look-ahead)
    floor = 10 ** (-duck_db / 20)
    return (f"{song},asplit=2[m1][m2];[0:a]aresample=48000,aformat=channel_layouts=stereo,asplit=2[d][k];"
            f"[k]highpass=f=200,lowpass=f=3500[key];"
            f"[m1][key]sidechaincompress=threshold=0.02:ratio=20:attack=15:release=400:makeup=1[mc];"
            f"[mc]volume={1 - floor:.4f}[mw];[m2]volume={floor:.4f}[mf];[mw][mf]amix=inputs=2:normalize=0[md];"
            f"[d][md]amix=inputs=2:duration=first:normalize=0,{finish}")


def lay(video: Path, song: Path, target: Path, music_db: float = MUSIC_DB, duck_db: float = DUCK_DB,
        log=None) -> dict:
    """``song`` laid under ``video`` into ``target`` (the picture copied, the sound made again at 256 kb/s AAC): the
    song cut to the clip's length, ducked under its dialogue, no peak over PEAK_DBFS. Runs in the job thread, with
    a time limit all the same. Returns {"peak_dbfs", "seconds", "ducked"}."""

    ffmpeg = tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("laying a song under a clip needs ffmpeg, which is not installed on this machine")
    length, has_sound = media_seconds(str(video))
    if not length:
        raise RuntimeError(f"cannot read the length of {video.name}")
    limit = PEAK_DBFS - 0.5
    for _ in range(3):  # AAC can overshoot what the limiter let through: lower the limit and make it again
        done = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(video), "-i", str(song), "-filter_complex",
                               mix_filter(bool(has_sound), length, music_db, duck_db, limit), "-map", "0:v:0", "-map", "[a]",
                               "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-t", f"{length:.3f}",
                               "-movflags", "+faststart", str(target)], capture_output=True, text=True, timeout=1800)
        if done.returncode or not target.is_file():
            raise RuntimeError("ffmpeg could not lay the song under the clip: " + done.stderr.strip()[-300:])
        peak = peak_dbfs(target)
        if peak is None or peak <= PEAK_DBFS:
            break
        if log:
            log(f"[tensorfold] soundtrack: peak {peak:+.2f} dBFS after AAC, limit lowered\n")
        limit -= 0.7
    return {"peak_dbfs": peak, "seconds": round(length, 3), "ducked": bool(has_sound), "music_db": music_db}


def soundtrack_params(body: dict) -> dict:
    """{take (a song's id), video (a clip of the studio), music_db?}: checked before the job is queued."""

    take = one_take(str(body.get("take") or ""))
    video = inside(str(body.get("video") or ""))
    if not video.is_file() or video.suffix.lower() not in (".mp4", ".mov"):
        raise HTTPException(400, "no such video in the studio")
    if not tool("ffmpeg"):
        raise HTTPException(400, "laying a song under a clip needs ffmpeg, which is not installed on this machine")
    try:
        level = float(body.get("music_db") if body.get("music_db") not in (None, "") else MUSIC_DB)
    except (TypeError, ValueError):
        raise HTTPException(400, "music_db is a number")
    if not -30 <= level <= 0:
        raise HTTPException(400, "music_db is between -30 and 0")
    return {"take": take["id"], "song": take["path"], "video": relative(video), "music_db": level}


def soundtrack(job: dict) -> bool:
    """A ``soundtrack`` job: the song under the clip, a new file in exports/ beside the clip it came from."""

    params = job["params"]
    (HOME / "exports").mkdir(parents=True, exist_ok=True)
    target = HOME / "exports" / f"{job['id']}.mp4"
    job["progress"] = {"stage": "laying the song under the clip", "at": 0, "of": 1}
    said = lay(inside(params["video"]), inside(params["song"]), target, float(params["music_db"]),
               log=lambda line: job.update(log=job["log"] + line))
    job["log"] += f"[tensorfold] soundtrack: {json.dumps(said)}\n"
    job["soundtrack"] = said
    job["outputs"] = [relative(target)]
    job["progress"] = None
    return True


# ---------- the API ----------

@router.get("/api/music/state")
def music_state() -> dict:
    return state()


@router.post("/api/music/takes")
def render(body: dict) -> dict:
    """Queue a song: {caption, lyrics, seconds, seed?, steps?, dit_cfg?, ar_cfg?, top_k?, title?, asr?, live?}."""

    params = checked(body)
    title = params["title"] or "Music: " + (sung(params["lyrics"])[:40] or params["caption"][:40])
    return enqueue("music", title, params, engine().estimate(params["seconds"]) + (20 if params["asr"] else 0))


@router.get("/api/music/takes")
def list_takes() -> list[dict]:
    return takes()


@router.get("/api/music/takes/{take_id}")
def get_take(take_id: str) -> dict:
    return one_take(take_id)


@router.get("/api/music/takes/{take_id}/audio")
def take_audio(take_id: str):
    take = one_take(take_id)
    target = inside(take["path"])
    if not target.is_file():
        raise HTTPException(404, "the song's file is gone (deleted to the trash folder?)")
    return FileResponse(target, media_type="audio/wav", filename=f"minimax-music3-{take_id}.wav")


@router.get("/api/music/takes/{take_id}/live")
def take_live(take_id: str):
    """The song that is being made, as a stereo WAV that plays while it is made (the Mac engine's raw PCM as it comes),
    its sound held back until playing it will not run dry."""

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", take_id):
        raise HTTPException(400, "bad take id")
    entry, began = LIVE.get(take_id), time.time()
    while entry is None and time.time() - began < 10:  # the job has started and is not at the engine yet
        time.sleep(0.2)
        entry = LIVE.get(take_id)
    if entry is None:
        raise HTTPException(404, "no song is being made under that id")
    from fastapi.responses import StreamingResponse

    return StreamingResponse(live_song(entry), media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.post("/api/music/soundtrack")
def lay_under(body: dict) -> dict:
    """Queue a song laid under a clip: {take, video, music_db?}. The result is a new clip in exports/."""

    params = soundtrack_params(body)
    return enqueue("soundtrack", "Soundtrack: " + Path(params["video"]).name[:40], params, 20)


def attach(home: Path, runner, queue, within, short, measure, gate) -> tuple[APIRouter, callable, callable]:
    """As voice.attach: ``runner(job, command, env, label)`` (server.run), ``queue`` (server.enqueue), the path helpers,
    server.media_seconds and the QA module. Returns the router, the song job and the soundtrack job."""

    global HOME, run, enqueue, inside, relative, media_seconds, qa
    HOME, run, enqueue, inside, relative, media_seconds, qa = home, runner, queue, within, short, measure, gate
    (HOME / "music").mkdir(parents=True, exist_ok=True)
    if not SPARK:
        threading.Thread(target=engine().check, daemon=True).start()  # the first look runs a process: not in a request
    return router, perform, soundtrack
