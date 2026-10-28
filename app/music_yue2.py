"""YuE2 for the Music tab: m-a-p's song model (YuE2-3B and the YuE2-Vae decoder) on this machine, beside MiniMax Music 3.

A song's style and lyrics (the Music tab's section tags) become a song with sung vocals and accompaniment, 48 kHz
stereo: YuE2 first writes a score of the song (melody and chords, in ABC), then the song itself. Lyrics with no words to
sing make an instrumental (the yue2-music skill's instrumental workflow, spark/yue2_server.py). It runs as a persistent
server in its own environment ($PREFIX/yue2, spark/setup-yue2.sh): loaded on the first song, holding the GPU lock while
loaded, unloaded after ``YUE2_IDLE_S`` idle seconds and at once before any other job of this machine's queue
(music.release), under the memory kill-guard (``YUE2_GUARD_GB``), a ComfyUI that holds models stopped before it loads,
as MiniMax Music 3's CUDA engine (music.Cuda).

Where it runs: Linux with an NVIDIA GPU with BF16 (a DGX Spark, an RTX Pro 6000). YuE2 has no Mac build, so a Mac keeps
MiniMax Music 3. Where it is installed it is the Music tab's default engine (music.default_engine; Settings, Presets,
"Song engine" chooses, and each song can choose in the Music tab).

Licences: YuE2's code is Apache 2.0. Its weights are CC BY-NC 4.0 with the authors' additional permission for
individual creators (MODEL_LICENSE of multimodal-art-projection/YuE, 2026-09-16): personal users, content creators and
musicians acting in an individual capacity may generate, publish, distribute, sell, license or otherwise monetize their
outputs free of charge, with no fees or royalties; it does not authorise commercial use of the weights by companies,
which need a commercial licence from the authors; illegal, harmful, deceptive or unethical use is not permitted.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

import music
import voice

CREDIT = "YuE2"
LICENCE = ("YuE2 runs on this machine. Its code is Apache 2.0; its weights are CC BY-NC 4.0 with the authors' creator "
           "permission: personal users, content creators and musicians may publish, sell and license the songs they make, "
           "royalty-free; a company needs a commercial licence from the YuE2 authors; no illegal, harmful or deceptive use.")
SECONDS = (10, 300)  # as MiniMax Music 3's: the longest a song may be (YuE2's own limit is 360 s, 9000 tokens)
# the section tags YuE2 was trained with (the yue2-music skill's native labels); the Music tab's others are named as
# the nearest of them
NATIVE = {"intro": "Intro", "verse": "Verse", "pre-chorus": "Pre-Chorus", "chorus": "Chorus", "bridge": "Bridge",
          "interlude": "Interlude", "outro": "Outro", "instrumental": "Instrumental"}
NEAREST = {"post-chorus": "Chorus", "hook": "Chorus", "refrain": "Chorus", "prechorus": "Pre-Chorus", "pre chorus": "Pre-Chorus",
           "solo": "Interlude", "break": "Interlude", "build": "Interlude", "drop": "Interlude", "inst": "Instrumental",
           "ending": "Outro", "coda": "Outro"}
SECTIONS = tuple(NATIVE.values()) + ("Post-Chorus", "Hook", "Refrain", "Solo", "Break", "Build", "Drop", "Ending", "Coda")  # read
TAG_LINE = re.compile(r"^\s*\[([^\]\n]{1,40})\]\s*$")


def native_tag(tag: str) -> str:
    """A section tag as YuE2 reads it: "Verse 2" is [Verse], "Hook" is [Chorus], an unknown one is left as it is."""

    word = re.sub(r"\s*\d+$", "", " ".join(tag.split())).strip().lower()
    return NATIVE.get(word) or NEAREST.get(word) or tag.strip()


def lyrics_for(lyrics: str) -> tuple[str, bool]:
    """(the lyrics as YuE2 reads them, whether there is nothing to sing): tags on lines of their own made native,
    blank lines between sections, the words left as they are (backing vocals in parentheses too)."""

    out: list[str] = []
    for line in str(lyrics or "").replace("\r\n", "\n").split("\n"):
        found = TAG_LINE.match(line)
        if found:
            if out and out[-1] != "":
                out.append("")
            out.append("[" + native_tag(found.group(1)) + "]")
        elif line.strip() or (out and out[-1] != ""):
            out.append(line.rstrip())
    text = "\n".join(out).strip()
    return text, not music.sung(text).strip()


def has_weights(folder: Path) -> bool:
    return all((folder / name).is_file() for name in ("YuE2-3B/model.safetensors", "YuE2-3B/qwen.tiktoken", "YuE2-Vae/model.safetensors"))


def nvidia() -> bool:
    return bool(shutil.which("nvidia-smi")) or Path("/dev/nvidiactl").exists()


class Yue2(music.Cuda):
    """YuE2's server (``spark/yue2_server.py --serve``) in its own environment, as a ``voice.Served``. Measured on one
    GB10 (README, YuE2 songs): see ``load_s``, ``RTF_OF``, ``NEED_OF`` and memory_guard.MUSIC_YUE2_ESTIMATE."""

    name, label = "yue2", "PyTorch CUDA · NVIDIA"
    title, owner_tag, idle_env, guard_env = "YuE2", "studio-music yue2", "YUE2_IDLE_S", "YUE2_GUARD_GB"
    # measured to ready on a GB10, the weights read in one pass (spark/yue2_server.py read_weights): see the README
    load_s = 15.0
    # seconds of making a second of song, through the server on a GB10, the model loaded (English pop, seed 21): int8
    # 30 s in 34.6 s, 60 s in 48.1 s, 120 s in 79.8 s; bf16 30 s in 70.8 s, 60 s in 94.9 s, 120 s in 146.7 s. The score
    # comes first and hardly grows with the length (PLAN_S_OF: about 1700 tokens), the song after it (SONG_RTF_OF)
    RTF_OF = {"bf16": 1.6, "int8": 0.8}  # the 60 s figure, as the tab shows it
    PLAN_S_OF = {"bf16": 46.0, "int8": 20.0}
    SONG_RTF_OF = {"bf16": 0.85, "int8": 0.5}
    # free memory wanted before it loads: the song's peak (19.4 GB bf16, 19.1 GB int8 at 120 s) and the guard's 8 GB
    NEED_OF = {"bf16": 28.0, "int8": 28.0}
    # with TensorFold's decode engine for the tokens (``--backend blocks``, spark/build-yue2-engine.sh: a GB10 only),
    # through the server on a GB10, the model loaded, the same lyrics at seeds 7, 11 and 21: 60 s in 35.0 to 40.4 s,
    # 120 s in 60.4 to 66.9 s (int8 Triton loop: 45.7 to 47.6 s and 76.0 to 78.8 s), ready in 6 s instead of 21
    BLOCKS_RTF_OF, BLOCKS_PLAN_S_OF, BLOCKS_SONG_RTF_OF, BLOCKS_LOAD_S = {"int8": 0.65}, {"int8": 15.0}, {"int8": 0.4}, 6.0
    BLOCKS_NEED_GB = 29.0  # its peak (21.0 GB at 120 s: the engine's 2.6 GB beside the bf16 model) and the guard's 8 GB

    def __init__(self) -> None:
        voice.Served.__init__(self)
        self.home = Path(os.environ.get("YUE2_HOME", music.PREFIX / "yue2")).expanduser()
        self.venv = Path(os.environ.get("YUE2_VENV", self.home / "venv")).expanduser()
        self.model = Path(os.environ.get("YUE2_WEIGHTS", self.home / "weights")).expanduser()
        self.src = Path(os.environ.get("YUE2_SRC", self.home / "src")).expanduser()
        self.server = Path(os.environ.get("YUE2_SERVER", music.PACK / "spark" / "yue2_server.py")).expanduser()
        weights = os.environ.get("YUE2_QUANT", "int8").strip().lower() or "int8"
        self.weights = weights if weights in self.RTF_OF else "int8"
        # the tokens on TensorFold's decode engine where it is built (the server falls back to its own loop if it
        # cannot use it), else YuE2's PyTorch loop; YUE2_BACKEND=torch|blocks chooses
        self.backend = (os.environ.get("YUE2_BACKEND") or ("blocks" if engine_built() else "torch")).strip().lower()
        self.RTF, self.NEED_GB = self.RTF_OF[self.weights], self.NEED_OF[self.weights]
        self.plan_s, self.song_rtf = self.PLAN_S_OF[self.weights], self.SONG_RTF_OF[self.weights]
        if self.backend == "blocks" and self.weights in self.BLOCKS_RTF_OF:
            self.RTF, self.load_s = self.BLOCKS_RTF_OF[self.weights], self.BLOCKS_LOAD_S
            self.plan_s, self.song_rtf = self.BLOCKS_PLAN_S_OF[self.weights], self.BLOCKS_SONG_RTF_OF[self.weights]
            self.label, self.NEED_GB = "TensorFold CUDA · DGX Spark", self.BLOCKS_NEED_GB
        self.guard_route = "music-yue2-" + ("blocks" if self.label.startswith("TensorFold") else self.weights)

    def python(self) -> Path:
        return self.venv / "bin" / "python"

    def environment(self) -> dict:
        """The studio's environment as it is: YuE2's PyTorch brings its own CUDA libraries, which the system toolkit's
        folder (music.Cuda.environment, for MiniMax Music 3's engine) put first would override."""

        return voice.Served.environment(self)

    def estimate(self, seconds: float) -> int:
        return int((0 if self.loaded() else self.load_s) + self.plan_s + seconds * self.song_rtf + 2)

    def argv(self) -> list[str]:
        return [str(self.python()), "-I", str(self.server), "--model", str(self.model / "YuE2-3B"), "--vae",
                str(self.model / "YuE2-Vae"), "--src", str(self.src), "--quant", self.weights,
                "--backend", self.backend if self.backend in ("torch", "blocks") else "torch", "--serve"]

    def check(self) -> str:
        """music.Cuda.check, but off a Spark the platform's answer is given as it is now, never one cached from a
        moment ago when the platform read otherwise."""

        return self.present() if not music.SPARK else music.Cuda.check(self)

    def present(self) -> str:
        """What is missing for YuE2 here ("" when its environment and weights are there): files only, no process."""

        if not music.SPARK and music.MAC:
            return "YuE2 has no Mac build: on a Mac the Music tab makes songs with MiniMax Music 3."
        if not music.SPARK:
            return "YuE2 runs on Linux with an NVIDIA GPU (a DGX Spark, an RTX Pro 6000); here the Music tab uses MiniMax Music 3."
        if not nvidia():
            return "YuE2 needs an NVIDIA GPU with BF16, and this machine shows none."
        if not self.python().is_file():
            return f"No YuE2 environment at {self.venv} (bash spark/setup-yue2.sh, or oneshot-setup-spark.sh --yue2, about 13 GB)."
        if not has_weights(self.model):
            return (f"No YuE2 weights at {self.model} (bash spark/setup-yue2.sh downloads them; YUE2_WEIGHTS names another folder).")
        if not self.server.is_file():
            return f"No YuE2 server script at {self.server}."
        return ""

    def look(self) -> str:
        why = self.present()
        if why:
            return why
        free = voice.mem_available_gb()
        if 0 <= free < self.NEED_GB and not voice.comfy_up():
            who = "GPU" if voice.has_card_memory() else "machine"
            return (f"YuE2 needs about {self.NEED_GB:.0f} GB of free memory and this {who} has {free:.1f} GB available "
                    "now (something outside the studio holds the rest).")
        return ""

    def make(self, job: dict, params: dict, wav: Path) -> dict | None:
        """One song through the server: its measurements, or None when cancelled. Not streamed."""

        request = {"id": job["id"], "style": params["caption"], "lyrics": params["yue2_lyrics"], "seconds": float(params["seconds"]),
                   "seed": int(params["seed"]), "instrumental": bool(params.get("instrumental")), "cot": "full",
                   "wav": str(wav), "artifacts": str(wav.with_suffix(".yue2")), "progress": True}
        words = {"plan": "YuE2 writes the score", "semantic": "the song's tokens", "nar": "the sound", "vae": "decoding"}

        def progress(said: dict) -> None:
            stage = str(said.get("stage") or "")
            job["progress"] = {"stage": "music: " + words.get(stage, stage), "at": int(said.get("done") or 0), "of": int(said.get("total") or 1)}

        with self.mutex:
            began = time.time()
            try:
                if not self.start(job):
                    return None
                loaded = time.time()
                job["progress"] = {"stage": "music: YuE2 writes the score", "at": 0, "of": 1}
                answer = self.ask(job, request, 600 + 15 * float(params["seconds"]), progress)
                if answer is None:
                    return None
            finally:
                self.last_used = time.time()
        ms = lambda key: round(float(answer.get(key) or 0) / 1000, 1)  # noqa: E731
        return {"audio_s": answer.get("audio_s"), "eos": answer.get("ended"), "truncated": answer.get("truncated"),
                "plan_s": ms("plan_ms"), "semantic_s": ms("semantic_ms"), "nar_s": ms("nar_ms"), "vae_s": ms("vae_ms"),
                "engine_s": ms("total_ms"), "rtf": answer.get("rtf"), "peak_memory_gb": answer.get("peak_memory_gb"),
                "tokens": answer.get("tokens"), "score": answer.get("score"), "cot": answer.get("cot"),
                "instrumental": answer.get("instrumental"), "load_s": job.get("load_s"),
                "weights": self.ready.get("weights") or self.weights,
                "total_s": round(time.time() - began, 1), "made_s": round(time.time() - loaded, 1)}


def engine_built() -> bool:
    """Whether TensorFold's YuE2 decode engine is built here (spark/build-yue2-engine.sh, a GB10 only)."""

    return Path(os.environ.get("TF_YUE2_LIB") or music.PREFIX / "tf-yue2" / "libtf_yue2.so").expanduser().is_file()


_engine: Yue2 | None = None


def engine() -> Yue2:
    global _engine
    if _engine is None:
        _engine = Yue2()
    return _engine


def installed() -> bool:
    """Whether YuE2 can be this machine's default: Linux, an NVIDIA GPU, its environment and weights (files only)."""

    return not engine().present()


def checked(body: dict, params: dict) -> dict:
    """A YuE2 song's own settings over the common ones music.checked made: its lyrics as YuE2 reads them."""

    text, instrumental = lyrics_for(params["lyrics"])
    params.update(engine="yue2", yue2_lyrics=text if not instrumental else (text if text != "[Instrumental]" else ""),
                  instrumental=instrumental)
    return params

