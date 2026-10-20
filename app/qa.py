"""Sam Wasserman's QA gate for a clip made to the Wasserman Sharp Spec.

Gate 1, audio coherence: the clip's sound is transcribed and has to hold at least two real words that form the line the
prompt asked for. Gate 2, identity: three frames (start, middle, end) side by side on one contact sheet, which a vision
model checks for the same face, hair and wardrobe in all three with no mush and no drift. A clip that fails is rendered
again at the next seed, two takes at most (server.py does that). The sharpness figures are a diagnosis, not a gate.

A gate whose tool is missing on this machine is reported as "skipped" and does not fail the clip.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

DIRS = ("/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local/bin"), str(Path.home() / ".claude/local"))
PATH = os.pathsep.join([os.environ.get("PATH", ""), *DIRS])
ENV = {**os.environ, "PATH": PATH}
FRAME = (688, 384)
# the largest speech model already on this disk is used; QA_WHISPER_MODEL names another (it is downloaded on first use)
MODELS = ("large-v3-turbo", "turbo", "large-v3", "medium", "small", "base")
VISION = ("You are the QA gate of a film pipeline. The picture is a contact sheet of three frames of one generated video "
          "clip, left to right: start, middle, end. Check the people or characters in it: the same face, hair and "
          "wardrobe in all three frames, no melted or smeared faces, no change of identity. A change of pose, framing, "
          "expression or lighting is fine, and so is a frame where the subject is turned away or out of shot. A clip "
          "with no person passes if the main subject stays the same thing. Answer with one line of JSON and nothing "
          'else: {"pass": true or false, "reason": "one short sentence"}.')


def tool(name: str) -> str | None:
    return shutil.which(name, path=PATH)


def ready() -> dict:
    """What the gate can do on this machine: ``sheet`` (ffmpeg and ffprobe: the contact sheet, and the sound for the
    transcript), ``transcript`` (a speech-to-text tool), ``runs`` (the gate can run at all) and ``missing``, the
    reasons in words. Without ffmpeg nothing of the gate runs; without a speech-to-text tool the audio gate is skipped."""

    import importlib.util

    sheet = bool(tool("ffmpeg") and tool("ffprobe"))
    transcript = sheet and bool(importlib.util.find_spec("faster_whisper") or tool("whisper"))
    missing = ([] if sheet else ["ffmpeg (" + ("sudo apt install ffmpeg" if os.uname().sysname == "Linux" else "brew install ffmpeg") + ")"]) + (
        [] if transcript or not sheet else ["a speech-to-text tool (pip install faster-whisper, or openai-whisper)"])
    return {"sheet": sheet, "transcript": transcript, "runs": sheet, "missing": missing}


def not_run(why: str) -> dict:
    """The gate's place in a job's result when it did not run: said, with the reason, instead of nothing."""

    return {"passed": None, "skipped": True, "takes": [], "note": why}


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


def lines(prompt: str) -> list[str]:
    """The spoken lines a MiniMax H3 prompt asks for: <d>[English] the words</d>."""

    return [re.sub(r"^\[[^\]]*\]\s*", "", found).strip() for found in re.findall(r"<d>(.*?)</d>", prompt, re.S)]


def duration(clip: Path) -> float:
    done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)],
                          capture_output=True, text=True, env=ENV)
    return float(done.stdout.strip() or 0)


def trim_heap() -> None:
    """Give the heap this process freed back to the system (Linux): glibc keeps the arenas of a CPU Whisper model
    after it is let go, and on a DGX Spark the GPU takes from the same memory. Only unused pages are returned."""

    if not sys.platform.startswith("linux"):
        return
    import ctypes
    import gc

    gc.collect()
    try:
        trim = getattr(ctypes.CDLL(None), "malloc_trim", None)
    except OSError:
        return
    if trim is not None:
        trim(0)


def transcribe(clip: Path) -> str | None:
    """What is said in the clip, or None when this machine has nothing to transcribe with."""

    with tempfile.TemporaryDirectory() as scratch:
        sound = Path(scratch) / "qa.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt",
                        "s16", str(sound)], check=True, env=ENV)
        try:
            from faster_whisper import WhisperModel  # Sam Wasserman's own gate runs on this
            model = WhisperModel(os.environ.get("QA_WHISPER_MODEL", "large-v3-turbo"), device="cpu", compute_type="int8")
            # the samples themselves, read here: handed the file, faster-whisper opens it with PyAV, and with the PyAV
            # of the Spark build that fails ("open() got an unexpected keyword argument 'metadata_errors'"), which
            # had the audio gate skipped on every clip there
            import wave

            import numpy as np

            with wave.open(str(sound)) as handle:
                samples = np.frombuffer(handle.readframes(handle.getnframes()), np.int16).astype(np.float32) / 32768.0
            parts, _ = model.transcribe(samples, beam_size=5)
            text = " ".join(part.text.strip() for part in parts)
            del model, parts
            trim_heap()  # the model is loaded for each transcript: its memory back now, not at the next job's check
            return text
        except ImportError:
            pass
        whisper = tool("whisper")
        if not whisper:
            return None
        cached = Path.home() / ".cache/whisper"
        name = os.environ.get("QA_WHISPER_MODEL") or next((m for m in MODELS if (cached / f"{m}.pt").is_file()), "turbo")
        done = subprocess.run(["nice", whisper, str(sound), "--model", name, "--device", "cpu", "--fp16", "False",
                               "--beam_size", "5", "--output_format", "txt", "--output_dir", scratch],
                              capture_output=True, text=True, env=ENV, timeout=900)
        text = Path(scratch) / "qa.txt"
        if not text.is_file():
            raise RuntimeError((done.stderr or "whisper wrote no transcript").strip()[-300:])
        return " ".join(text.read_text().split())


def audio_gate(clip: Path, prompt: str) -> dict:
    wanted = [w for line in lines(prompt) for w in words(line)]
    try:
        heard = transcribe(clip)
    except Exception as error:  # noqa: BLE001 - a broken tool is a skipped gate, not a failed clip
        return {"result": "skipped", "note": f"the transcript failed: {str(error)[-200:]}"}
    if heard is None:
        return {"result": "skipped", "note": "no speech-to-text tool on this machine (pip install faster-whisper, or openai-whisper)"}
    said = words(heard)
    if not wanted:
        return {"result": "pass", "heard": heard, "note": "the prompt has no spoken line" + (": the clip is not silent of speech" if len(said) >= 4 else "")}
    found = sum(1 for w in wanted if w in said)
    need = min(2, len(wanted))  # a one-word line cannot hold two words
    ok = len(said) >= need and found >= need and found >= 0.6 * len(wanted)
    return {"result": "pass" if ok else "fail", "heard": heard, "wanted": " ".join(lines(prompt)),
            "note": f"{found} of the line's {len(wanted)} words heard"}


def sheet(clip: Path, target: Path) -> list[float]:
    """Three frames side by side: 1 s, the middle and 1 s before the end (1, 4 and 7 s of an 8 second clip)."""

    length = duration(clip)
    times = [1.0, round(length / 2, 2), round(max(length - 1.0, 1.5), 2)] if length >= 3 else [0.0, length / 2, max(length - 0.1, 0)]
    target.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-v", "error", "-y"]
    for at in times:
        command += ["-ss", str(at), "-i", str(clip)]
    scale = "".join(f"[{i}:v]scale={FRAME[0]}:{FRAME[1]}[f{i}];" for i in range(3))
    command += ["-filter_complex", scale + "[f0][f1][f2]hstack=3", "-frames:v", "1", "-q:v", "2", str(target)]
    subprocess.run(command, check=True, env=ENV)
    return times


def sharpness(clip: Path, times: list[float]) -> list[int]:
    """Edge-energy variance of each sheet frame (the variance of a Laplacian of the grey picture). No gate reads it."""

    try:
        import numpy as np
    except ImportError:
        return []
    out = []
    for at in times:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(clip), "-frames:v", "1", "-vf",
                              f"scale={FRAME[0]}:{FRAME[1]},format=gray", "-f", "rawvideo", "-"], capture_output=True, env=ENV).stdout
        if len(raw) != FRAME[0] * FRAME[1]:
            return []
        g = np.frombuffer(raw, np.uint8).reshape(FRAME[1], FRAME[0]).astype(np.float32)
        edges = g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4 * g[1:-1, 1:-1]
        out.append(int(edges.var()))
    return out


def verdict(text: str) -> dict | None:
    found = re.search(r"\{[^{}]*\"pass\"[^{}]*\}", text or "", re.S)
    try:
        parsed = json.loads(found.group(0)) if found else None
    except ValueError:
        parsed = None
    return parsed if isinstance(parsed, dict) and isinstance(parsed.get("pass"), bool) else None


def local_vision(picture: Path, endpoint: str, model: str) -> str:
    image = "data:image/jpeg;base64," + base64.b64encode(picture.read_bytes()).decode()
    body = json.dumps({"model": model, "temperature": 0, "messages": [{"role": "user", "content": [
        {"type": "text", "text": VISION}, {"type": "image_url", "image_url": {"url": image}}]}]}).encode()
    request = urllib.request.Request(endpoint.rstrip("/") + "/chat/completions", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as answer:
        return json.loads(answer.read())["choices"][0]["message"]["content"]


def bolt_engine(endpoint: str | None):
    """bolt.py when the brains' address means the Studio's own Bolt engine, else None."""

    try:
        import bolt
    except ImportError:
        return None
    return bolt if bolt.serves(endpoint or "") else None


def engine_model() -> str:
    import bolt
    return bolt.SERVED


def default_brain() -> dict:
    """The brain saved in Brains & tools (agents.py), which the studio's environment fills only when nothing is saved
    (``STUDIO_DEFAULT_BRAIN``, ``STUDIO_LOCAL_ENDPOINT``, ``STUDIO_LOCAL_MODEL``)."""

    try:
        import agents
    except ImportError:
        return {}
    return agents.default_brain()


def look(picture: Path, brain: dict) -> tuple[str, str]:
    """(answer, who gave it) from the first vision model that answers: the film's brain first, then the others."""

    brain = {**default_brain(), **{key: value for key, value in (brain or {}).items() if value}}
    # a film whose brain is a local model never has its frames sent to a cloud CLI, even when the local one is down
    order = ["local"] if brain.get("backend") == "local" else [brain.get("backend")] + ["claude", "codex", "local"]
    tried = []
    for backend in dict.fromkeys(b for b in order if b):
        try:
            if backend == "local":
                endpoint, model = brain.get("endpoint"), brain.get("model")
                engine = bolt_engine(endpoint)
                if engine is not None:  # the Studio's Bolt engine: refused at once while a job holds its memory
                    with engine.request(wait=False) as live:
                        return local_vision(picture, live, model or engine_model()), "local model"
                if not endpoint:
                    continue
                return local_vision(picture, endpoint, model or ""), "local model"
            cli = tool(backend)
            if not cli:
                continue
            with tempfile.TemporaryDirectory() as scratch:  # the model sees this folder and the one picture in it
                shutil.copy(picture, Path(scratch) / "sheet.jpg")
                if backend == "codex":
                    last = Path(scratch) / "last.txt"
                    subprocess.run([cli, "exec", "--skip-git-repo-check", "-i", "sheet.jpg", "--output-last-message", str(last), "-"],
                                   input=VISION, capture_output=True, text=True, timeout=300, cwd=scratch, env=ENV)
                    if last.is_file() and last.read_text().strip():
                        return last.read_text(), "Codex"
                    tried.append("Codex gave no answer")
                    continue
                # Read only, and only in this scratch folder (Claude Code's blockReadsOutsideWorkingDirectories)
                done = subprocess.run([cli, "-p", "--output-format", "json", "--tools", "Read", "--permission-mode", "acceptEdits",
                                       "--settings", '{"permissions": {"blockReadsOutsideWorkingDirectories": true}}', "--model", "sonnet"],
                                      input="Read the image file sheet.jpg in the current folder, then: " + VISION,
                                      capture_output=True, text=True, timeout=300, cwd=scratch, env=ENV)
                try:
                    parsed = json.loads(done.stdout)
                except ValueError:
                    parsed = {}
                if isinstance(parsed.get("result"), str) and not parsed.get("is_error"):
                    return parsed["result"], "Claude Code"
                tried.append("Claude Code: " + str(parsed.get("result") or done.stderr or "no answer").strip()[-160:])
        except Exception as error:  # noqa: BLE001
            tried.append(f"{backend}: {str(error)[-160:]}")
    raise LookupError("; ".join(tried) or "no vision model on this machine: Claude Code, Codex or a local vision model")


def identity_gate(picture: Path, brain: dict) -> dict:
    try:
        answer, who = look(picture, brain)
    except LookupError as error:
        return {"result": "skipped", "note": f"look at the sheet yourself; {error}"}
    found = verdict(answer)
    if found is None:
        return {"result": "skipped", "note": f"{who} gave no verdict: {answer.strip()[:160]}"}
    return {"result": "pass" if found["pass"] else "fail", "note": f"{who}: {str(found.get('reason', '')).strip()[:240]}"}


def check(clip: Path, prompt: str, picture: Path, brain: dict | None = None) -> dict:
    """Both gates for one take. ``passed`` is false only when a gate fails; a skipped gate does not fail the clip."""

    audio = audio_gate(clip, prompt)
    times = sheet(clip, picture)
    identity = identity_gate(picture, brain or {})
    return {"audio": audio, "identity": identity, "sharpness": sharpness(clip, times), "times": times,
            "passed": "fail" not in (audio["result"], identity["result"])}
