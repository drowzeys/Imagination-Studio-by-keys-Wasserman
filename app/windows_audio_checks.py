"""Explicit native Voice/MiniMax-Music3 recipe checks; installation is not verification."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

from feature_checks import matches_qualification
import windows_cuda
import windows_features as features
import windows_image_checks as native
import windows_media
import windows_runtime
from windows_qualification import hardware_profile, verify_model_updates, version_family

KINDS = ("voice", "music")
CONTRACT = 1
TIMEOUT_S = 900
VOICE_TEXT = "The studio is ready. This is a short local voice generation check."
MUSIC_CAPTION = "Gentle instrumental piano, warm acoustic sound, steady relaxed tempo."


def recipe(kind: str) -> dict:
    if kind == "voice":
        import voice
        return {"text": VOICE_TEXT, "max_words": 20, "max_characters": 120, "max_seconds": 15,
                "reference": False, "live": False, "asr": False,
                "sampling": {key: value[0] for key, value in voice.SAMPLING.items()}}
    if kind == "music":
        import music
        return {"seconds": 10, "lyrics": "[Instrumental]", "caption": MUSIC_CAPTION,
                "engine": "music3", "backend": "cuda", "weights": "int8", "live": False, "asr": False,
                "sampling": {key: value[0] for key, value in music.SAMPLING.items()}}
    raise ValueError("Choose Voice or MiniMax-Music3.")


def files(kind: str) -> tuple[list[Path], Path, list[Path]]:
    """Native engine prerequisites only; no H3 or shared ComfyUI requirement."""
    paths = windows_cuda.paths()
    root = windows_runtime.model_root()
    if kind == "voice":
        binary = Path(os.environ.get("DRAMA3_CUDA_BIN") or paths["drama3_bin"])
        library = Path(os.environ.get("DRAMA3_CUDA_LIB") or paths["drama3_lib"])
        model = Path(os.environ.get("DRAMA3_CUDA_MODEL") or root / "drama-3")
        codec = Path(os.environ.get("DRAMA3_CUDA_CODEC") or binary.parent / "codec-v1.safetensors")
        index = model / "model.safetensors.index.json"
        required = [model / "config.json", index, codec]
        if index.is_file():
            contents = json.loads(index.read_text(encoding="utf-8"))
            shards = set(contents.get("weight_map", {}).values())
            if not shards:
                raise ValueError("Drama 3's model index contains no weight shards.")
            for name in sorted(shards):
                path = (model / name).resolve()
                if not path.is_relative_to(model.resolve()):
                    raise ValueError("Drama 3's index names a file outside its model folder.")
                required.append(path)
        return [binary, library], model, required
    if kind == "music":
        import music
        binary = Path(os.environ.get("MUSIC3_CUDA_BIN") or paths["music3_bin"])
        model = Path(os.environ.get("MUSIC3_MODEL") or root / "MiniMax-Music3-Comfy")
        return [binary], model, [model / name for name in music.WEIGHTS]
    raise ValueError("Choose Voice or MiniMax-Music3.")


def prerequisites(kind: str) -> str:
    if why := windows_cuda.configuration_error():
        return why
    engine, _, weights = files(kind)
    missing = [path.name for path in engine + weights if not path.is_file() or path.stat().st_size == 0]
    if missing:
        return "Missing native audio files: " + ", ".join(missing)
    for tool in ("ffmpeg", "ffprobe"):
        import shutil
        if not shutil.which(tool, path=windows_media.search_path(os.environ.get("PATH", ""))):
            return "Install the native media tools with scripts\\setup-windows-ffmpeg.ps1."
    if kind == "voice" and os.environ.get("DRAMA3_BACKEND", "cuda") != "cuda":
        return "This Windows check requires DRAMA3_BACKEND=cuda."
    if kind == "music" and (os.environ.get("MUSIC3_BACKEND", "cuda") != "cuda"
                            or os.environ.get("MUSIC3_WEIGHTS", "int8") != "int8"):
        return "This check covers MiniMax-Music3's native CUDA int8 engine only."
    return ""


@lru_cache(maxsize=32)
def digest(path: Path, size: int, mtime: int) -> str:
    with path.open("rb") as handle:
        result = hashlib.file_digest(handle, "sha256").hexdigest()
    if (path.stat().st_size, path.stat().st_mtime_ns) != (size, mtime):
        raise RuntimeError("The engine changed while its identity was checked.")
    return result


def fingerprint(kind: str) -> dict:
    engine, model, weights = files(kind)
    mapping = {}
    for path in weights:
        # The codec can live beside the engine, outside the weights root.
        mapping[str(path.resolve())] = {"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
    return {"contract": CONTRACT, "kind": kind, "recipe": recipe(kind),
            "hardware": {**hardware_profile(), "device": hashlib.sha256(native.device_uuid().encode()).hexdigest()},
            "python_abi": version_family(sys.version, 2), "models": mapping,
            "engine": {path.name: digest(path.resolve(), path.stat().st_size, path.stat().st_mtime_ns) for path in engine},
            "model_root": hashlib.sha256(str(model.resolve()).encode()).hexdigest(),
            "sources": {name: hashlib.sha256((features.PACK / "app" / name).read_bytes()).hexdigest()
                        for name in ("voice.py", "music.py", "windows_audio_checks.py", "windows_image_checks.py")},
            "guard": {"host_floor": os.environ.get("SPARK_MEMORY_FLOOR_MB", ""),
                      "engine_floor": os.environ.get("DRAMA3_GUARD_GB" if kind == "voice" else "MUSIC3_GUARD_GB", "8")}}


def compatibility(value: dict) -> dict:
    return {**{key: value[key] for key in ("contract", "kind", "recipe", "hardware", "python_abi", "engine", "model_root", "guard")},
            "model_sizes": {name: row["size"] for name, row in value["models"].items()}}


def status(kind: str) -> dict:
    base = {"installed": False, "qualified": False, "available": False, "check_available": True,
            "state": "not_installed", "verified_recipe": recipe(kind)}
    try:
        if why := prerequisites(kind):
            return {**base, "why": why}
        base.update(installed=True, state="needs_verification")
        receipt = features.saved(kind, "")
        if receipt.get("ok") is not True:
            return {**base, "why": "Run this native audio recipe in Machine > Feature checks."}
        current = fingerprint(kind)
        recorded = receipt["fingerprint"]
        if not matches_qualification(recorded, current, compatibility(recorded), compatibility(current)):
            return {**base, "why": "The native engine, weights, hardware or recipe changed. Check this recipe again."}
        verify_model_updates(Path(), {name: (row["size"], receipt["model_sha256"][name])
                                      for name, row in recorded["models"].items()},
                             {name: row["mtime_ns"] for name, row in recorded["models"].items()})
        return {**base, "available": True, "qualified": True, "state": "qualified", "why": "",
                "runtime_updated": recorded != current, "hardware": recorded["hardware"]}
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        return {**base, "state": "qualification_error", "why": f"Native audio verification could not be checked: {error}"}


def require_scope(kind: str, params: dict) -> None:
    import voice
    import music
    settings = recipe(kind)
    if kind == "voice":
        text = str(params.get("text") or "").strip()
        if (len(text) > settings["max_characters"] or len(voice.words(text)) > settings["max_words"]
                or "<|" in text or "[" in text or "\n" in text or params.get("ref_audio")
                or params.get("live", True) is not False or params.get("asr", True) is not False):
            raise ValueError("Verified Voice scope: at most 20 words / 120 characters, one plain speaker, no reference, streaming or transcription.")
        defaults = voice.SAMPLING
    else:
        if (params.get("engine", "music3") != "music3" or float(params.get("seconds", 60)) != 10
                or (params.get("lyrics") or "[Instrumental]").strip() != "[Instrumental]"
                or params.get("live", False) or params.get("asr", True)):
            raise ValueError("Verified Music scope: MiniMax-Music3, 10-second instrumental, no streaming or transcription.")
        defaults = music.SAMPLING
    if any(params.get(key, row[0]) != row[0] for key, row in defaults.items()):
        raise ValueError("This native audio qualification covers the engine's default sampling settings only.")


def check_wav(kind: str, path: Path) -> None:
    import array
    import shutil
    search = windows_media.search_path(os.environ.get("PATH", ""))
    probe = subprocess.run([shutil.which("ffprobe", path=search), "-v", "error", "-show_streams", "-show_format",
                            "-of", "json", str(path)], capture_output=True, text=True, timeout=30, check=True,
                           creationflags=0x08000000)
    data = json.loads(probe.stdout)
    streams = [stream for stream in data["streams"] if stream["codec_type"] == "audio"]
    seconds = float(data["format"]["duration"])
    maximum = 15 if kind == "voice" else 10.5
    channels = 1 if kind == "voice" else 2
    if len(streams) != 1 or int(streams[0]["sample_rate"]) != 44100 or int(streams[0]["channels"]) != channels or not 0.5 <= seconds <= maximum:
        raise RuntimeError(f"The {kind} check did not produce its bounded 44.1 kHz WAV ({channels} channels).")
    decoded = subprocess.run([shutil.which("ffmpeg", path=search), "-v", "error", "-i", str(path),
                              "-t", str(maximum), "-f", "f32le", "-acodec", "pcm_f32le", "-"],
                             capture_output=True, timeout=30, check=True, creationflags=0x08000000)
    samples = array.array("f", decoded.stdout)
    if not samples or any(not math.isfinite(value) for value in samples) or max(abs(value) for value in samples) < 0.0001:
        raise RuntimeError("The native audio check produced silent or invalid samples.")


def worker(kind: str, output: Path):
    """Use the production take writers and lifecycle, with an isolated media home."""
    import voice
    import music
    import qa
    output.mkdir(parents=True, exist_ok=True)
    def relative(path):
        return str(Path(path).relative_to(output))
    for module in (voice, music):
        module.attach(output, None, None, lambda path: output / path, relative, None, qa)
    settings = recipe(kind)
    params = {"seed": 7, "live": False, "asr": False, **settings["sampling"]}
    if kind == "voice":
        params["text"] = VOICE_TEXT
        one = voice.backend()
    else:
        params.update(caption=MUSIC_CAPTION, lyrics="[Instrumental]", seconds=10, engine="music3")
        one = music.engine("music3")
    if one.name != "cuda":
        raise RuntimeError("The qualification selected a different backend; no fallback is permitted.")
    job = {"id": "check", "status": "running", "params": params, "log": "", "title": "Native audio qualification"}
    try:
        if why := one.check():
            raise RuntimeError(why)
        if not (voice.perform(job) if kind == "voice" else music.perform(job)):
            raise RuntimeError("The native audio check did not complete.")
    finally:
        process = one.process
        one.release("Native audio qualification finished")
        if process is not None and process.poll() is None:
            raise RuntimeError("The owned audio engine did not drain; no qualification can be saved.")


def qualify(kind: str, output: Path, job_id: str) -> dict:
    attempt = {"feature": kind, "job_id": job_id, "ok": False, "checked_at": datetime.now(timezone.utc).isoformat()}
    observations = {"case": "Short local Voice" if kind == "voice" else "MiniMax-Music3: 10-second instrumental", "rendered": False}
    features.save_attempt(kind, attempt)
    try:
        if why := prerequisites(kind):
            raise RuntimeError(why)
        if output.exists():
            raise RuntimeError("Audio qualification needs a fresh output directory.")
        before = fingerprint(kind)
        command = [sys.executable, "-X", "utf8", str(features.PACK / "scripts" / "windows-native-audio-check.py"),
                   "--feature", kind, "--output", str(output), "--worker"]
        target = output / kind / "check.wav"
        native.observe_native(command, output, lambda: check_wav(kind, target), observations, TIMEOUT_S)
        digests = {}
        for path, row in before["models"].items():
            digests[path] = digest(Path(path), row["size"], row["mtime_ns"])
        current = fingerprint(kind)
        if (before["models"] != current["models"] or
                not matches_qualification(before, current, compatibility(before), compatibility(current))):
            raise RuntimeError("The audio engine, weights or execution contract changed during qualification.")
        receipt = {**attempt, "ok": True, "fingerprint": before, "model_sha256": digests, "results": [observations]}
        features.save_pass(kind, receipt)
        features.save_attempt(kind, receipt)
        return receipt
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        features.save_attempt(kind, {**attempt, "error": str(error), "observations": observations})
        raise
