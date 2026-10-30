"""A clip's progress in a file, for a workload governor or job broker beside the studio (opt-in).

With ``SPARK_PROGRESS_FILE`` set, each video render keeps that file at two lines, so a governor can show what the GPU
it handed out is doing (for example by reading the file on a timer):

    3/8 passes · FastH3 bf16 (ComfyUI) · 864x480 124f
    generating · 21 s

Line 1 is the clip (its passes done of all, the engine and the size); line 2 is what it is doing now, and when it
ends, ``done in 9 s`` or ``failed: ...``. Unset (the default), nothing is written. The file is replaced whole each
time, so a reader never sees half of it, and a file that can't be written is skipped: progress never stops a render.
"""

from __future__ import annotations

import os
import time

STAGES = {"UNETLoader": "loading the model", "CLIPLoader": "loading the text encoder", "VAELoader": "loading the decoders",
          "MiniMaxH3ImageToVideo": "encoding the prompt", "SamplerCustomAdvanced": "generating", "KSampler": "generating",
          "VAEDecode": "decoding the video", "VAEDecodeTiled": "decoding the video", "VAEDecodeAudio": "decoding the sound",
          "CreateVideo": "writing the clip", "SaveVideo": "writing the clip", "LoadImage": "reading the pictures"}
state = {"label": "", "done": 0, "total": 0, "stage": "", "since": time.time()}


def path() -> str:
    return os.environ.get("SPARK_PROGRESS_FILE", "").strip()


def _write(timed: bool = True) -> None:
    target = path()
    if not target:
        return
    passes = f"{state['done']}/{state['total']} passes" if state["total"] else "0/1"
    now = f" · {time.time() - state['since']:.0f} s" if timed and state["stage"] else ""
    temp = f"{target}.{os.getpid()}"
    try:
        with open(temp, "w") as out:
            out.write(f"{passes} · {state['label']}\n{state['stage']}{now}\n")
        os.chmod(temp, 0o644)
        os.replace(temp, target)
    except OSError:
        pass


def begin(label: str, total: int, stage: str = "starting") -> None:
    state.update(label=label, done=0, total=int(total or 0), stage=stage, since=time.time())
    _write()


def step(done: int, total: int) -> None:
    state.update(done=int(done), total=int(total) or state["total"], stage="generating")
    _write()


def stage(node_class: str = "", words: str = "") -> None:
    said = words or STAGES.get(node_class, "")
    if said and said != state["stage"]:
        state["stage"] = said
        _write()


def done(seconds: float, name: str = "") -> None:
    state.update(done=state["total"], stage=f"done in {seconds:.0f} s" + (f" · {name}" if name else ""))
    _write(timed=False)


def failed(why: object) -> None:
    state["stage"] = "failed: " + " ".join(str(why).split())[:60]
    _write(timed=False)
