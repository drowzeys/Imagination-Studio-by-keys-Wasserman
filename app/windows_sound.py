"""Narrow five-second Stable Audio Open route using the guarded Windows backend."""
from __future__ import annotations

import argparse
from array import array
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import windows_sfx
import windows_storyboard as renderer
from feature_checks import matches_qualification
from windows_qualification import LEGACY_GPU_COMPATIBILITY, baseline_gpu_compatibility, baseline_gpu_runtime, hardware_matches, verify_model_updates

SECONDS, STEPS, CFG = 5, 100, 7.0
RECIPE = {"seconds": SECONDS, "steps": STEPS, "cfg": CFG}


def folder() -> Path:
    return Path(os.environ.get("STUDIO_WINDOWS_SOUND_DIR") or
                Path(os.environ.get("LOCALAPPDATA", "")) / "ImaginationStudio" / "models" / "Sound-Comfy" / "stable-audio-open-1.0")


def status() -> dict:
    root = folder()
    try:
        receipt = json.loads((root / "qualification.json").read_text(encoding="utf-8"))
        current_files = {name: {"size": size, "sha256": digest, "mtime_ns": (root / name).stat().st_mtime_ns}
                         for name, (size, digest) in windows_sfx.FILES.items()}
        previous_files = receipt.get("files", {})
        current_tools = renderer.tool_hashes()
        provenance = {"tools": receipt.get("tool_sha256"), "files": previous_files, "runtime": receipt.get("runtime")}
        current = {"tools": current_tools, "files": current_files, "runtime": baseline_gpu_runtime()}
        valid = (receipt.get("revision") == windows_sfx.REVISION and receipt.get("recipe") == RECIPE
                 and {name: (value["size"], value["sha256"]) for name, value in previous_files.items()}
                     == {name: (size, digest) for name, (size, digest) in windows_sfx.FILES.items()}
                 and matches_qualification(provenance, current, receipt.get("compatibility", LEGACY_GPU_COMPATIBILITY),
                                           baseline_gpu_compatibility())
                 and all((root / name).stat().st_size == info[0] for name, info in windows_sfx.FILES.items())
                 and (root / "LICENSE.md").is_file() and receipt.get("result", {}).get("ok") is True
                 and isinstance(receipt.get("hardware"), dict)
                 and all(type(receipt["hardware"].get(key)) is int and receipt["hardware"][key] > 0
                         for key in ("physical_total_mb", "host_total_mb", "gpu_dedicated_total_mb"))
                 and hardware_matches(receipt["hardware"]))
    except FileNotFoundError:
        return {"available": False, "why": "Run setup-windows-sfx.ps1, then check-windows-sound.ps1 to qualify the five-second route."}
        if valid:
            verify_model_updates(root, windows_sfx.FILES, {name: value["mtime_ns"] for name, value in previous_files.items()})
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, AttributeError) as error:
        return {"available": False, "why": f"The Windows sound qualification is unreadable: {type(error).__name__}."}
    if not valid:
        return {"available": False, "why": "The sound files or render code changed; repeat check-windows-sound.ps1."}
    return {"available": True, "why": "", "hardware": receipt["hardware"], "runtime_updated": provenance != current, **RECIPE}


def parameters(params: dict) -> dict:
    if not isinstance(params, dict) or set(params) - {"prompt", "negative", "seconds", "steps", "cfg", "seed"}:
        raise ValueError("Windows sound effects take prompt, negative, seconds, steps, cfg and seed only")
    out = {}
    for key in ("prompt", "negative"):
        text = params.get(key, "")
        if not isinstance(text, str) or len(text) > 600 or key == "prompt" and not text.strip():
            raise ValueError(f"{key} must be a sentence or two, at most 600 characters")
        out[key] = text
    for key, value in RECIPE.items():
        given = params.get(key, value)
        if type(given) not in (int, float) or not math.isfinite(given) or given != value:
            raise ValueError("Windows sound effects are qualified only at 5 seconds, 100 passes and CFG 7")
        out[key] = value
    seed = params.get("seed", 7)
    if type(seed) is not int or not 0 <= seed <= 2 ** 32 - 1:
        raise ValueError("the sound seed must be an unsigned 32-bit whole number")
    out["seed"] = seed
    return out


def preflight():
    if not shutil.which("ffmpeg"):
        raise RuntimeError("run scripts\\setup-windows-ffmpeg.ps1 and restart Studio before making sound effects")
    from windows_runtime import usage_status
    renderer.preflight(usage_status("sfx", status()))


def verify_weights(root: Path):
    windows_sfx.verify(root)


def validate_audio(path: Path, *, channels: int = 2, expected_samples: int | None = None) -> dict:
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_type,sample_rate,channels", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=30, check=True)
    streams = json.loads(probe.stdout).get("streams", [])
    if len(streams) != 1 or streams[0].get("sample_rate") != "44100" or streams[0].get("channels") != channels:
        raise RuntimeError(f"sound must be one 44.1 kHz stream with {channels} channels")
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-acodec", "pcm_f32le", "-"],
                             capture_output=True, timeout=30, check=True)
    if len(decoded.stdout) % (4 * channels):
        raise RuntimeError("the sound has an incomplete audio sample")
    values = array("f")
    values.frombytes(decoded.stdout)
    if sys.byteorder != "little":
        values.byteswap()
    samples = len(values) // channels
    expected = expected_samples if expected_samples is not None else round((SECONDS * 44100 / 2048) / 2) * 2 * 2048
    if not values or abs(samples - expected) > 2048 or any(not math.isfinite(value) for value in values):
        raise RuntimeError("sound has invalid samples or the wrong five-second latent window")
    peak = max(abs(value) for value in values)
    rms = math.sqrt(sum(value * value for value in values) / len(values))
    if rms < 1e-6 or peak > 10 ** (-1 / 20):
        raise RuntimeError("sound is silent or exceeds -1 dBFS")
    return {"samples_per_channel": samples, "seconds": samples / 44100, "sample_rate": 44100,
            "channels": channels, "peak_dbfs": 20 * math.log10(peak), "rms": rms}


def render_case(output: Path, params: dict, cancelled=lambda: False, submit=None, completed=None, progress=None):
    helper = renderer.tools()
    check = helper.checker()
    if output.exists():
        raise RuntimeError("the sound output folder already exists")

    def prepare(directory):
        helper.prepare(directory, folder())

    def exercise(base, directory, process, monitor):
        started = time.monotonic()
        graph = helper.workflow()
        graph["3"]["inputs"]["text"] = params["prompt"]
        graph["4"]["inputs"]["text"] = params["negative"]
        graph["12"]["inputs"]["seed"] = params["seed"]
        if cancelled():
            return {"cancelled": True}
        reply = submit(graph) if submit else helper.post_prompt(base, graph)
        if reply is None:
            return {"cancelled": True}
        if reply.get("error") or reply.get("node_errors"):
            raise RuntimeError("ComfyUI rejected the Windows sound workflow")
        prompt_id = str(uuid.UUID(reply["prompt_id"]))
        deadline = time.monotonic() + 300
        while True:
            monitor.check()
            if cancelled():
                if completed:
                    completed(prompt_id, True)
                return {"cancelled": True}
            if process.poll() is not None:
                raise RuntimeError("the owned sound backend exited")
            helper.validate_queue(check.read_json(base, "/queue"), prompt_id)
            history = check.read_json(base, "/history/" + prompt_id)
            if prompt_id in history:
                record = history[prompt_id]
                if record.get("status", {}).get("status_str") != "success":
                    errors = [item[1].get("exception_message", "unknown engine error")
                              for item in record.get("status", {}).get("messages", [])
                              if isinstance(item, list) and len(item) == 2 and item[0] == "execution_error"
                              and isinstance(item[1], dict)]
                    raise RuntimeError("the Windows sound workflow failed: " + str(errors[:1]))
                break
            if time.monotonic() > deadline:
                raise RuntimeError("the Windows sound workflow exceeded 300 seconds")
            time.sleep(.25)
        if completed:
            completed(prompt_id, False)
        if cancelled():
            return {"cancelled": True}
        receipt = record["outputs"]["19"]["windows_calibration"][0]
        helper.validate_receipt(receipt, renderer.GPU_LIMIT_MB << 20)
        saved = record["outputs"]["18"]["audio"]
        if len(saved) != 1 or saved[0].get("type") != "output":
            raise RuntimeError("the sound workflow did not save exactly one audio file")
        inside = (directory / "output").resolve()
        source = (inside / saved[0].get("subfolder", "") / saved[0]["filename"]).resolve()
        if not source.is_relative_to(inside):
            raise RuntimeError("ComfyUI returned sound outside its isolated output folder")
        audio = validate_audio(source)
        output.mkdir(parents=True)
        target = output / "sound.wav"
        # A lossless PCM WAV is directly usable by the Studio's existing sound library.
        done = subprocess.run(["ffmpeg", "-v", "error", "-n", "-i", str(source), "-c:a", "pcm_s16le", str(target)],
                              capture_output=True, text=True, timeout=30)
        if done.returncode:
            raise RuntimeError("FFmpeg could not save the sound take: " + done.stderr[-400:])
        validate_audio(target)
        if cancelled():
            target.unlink()
            return {"cancelled": True}
        if progress:
            progress(1, 1)
        return {"model": "Stable Audio Open 1.0", "audio": audio, "allocator_receipts": [receipt],
                "request_to_files_seconds": time.monotonic() - started}
    from windows_memory import single_gpu_uuid
    return check.CalibrationCase(prepare, exercise, wddm_device_uuid=single_gpu_uuid())


def save_take(job: dict, home: Path, target: Path, seconds: float):
    params = job["params"]
    take = {"id": job["id"], "kind": "sfx", "model": "Stable Audio Open 1.0", "prompt": params["prompt"],
            "negative": params["negative"], "seed": params["seed"], "seconds": seconds, "rate": 44100,
            "path": target.relative_to(home).as_posix(), "created": time.time(),
            "elapsed": time.time() - job["started"], "engine": "comfy", "licence": "Stability AI Community License"}
    temporary = target.parent / "sound.take.json.part"
    temporary.write_text(json.dumps(take, indent=1) + "\n", encoding="utf-8")
    temporary.replace(target.parent / "sound.take.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualify", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        import windows_media
        os.environ["PATH"] = windows_media.search_path(os.environ.get("PATH", ""))
        if os.name != "nt" or not shutil.which("ffmpeg"):
            raise RuntimeError("this check requires Windows and FFmpeg; run setup-windows-ffmpeg.ps1 first")
        root = folder()
        windows_sfx.verify(root)
        params = parameters({"prompt": "Soft steady rain falling on a metal roof.", "seed": 7})
        case = render_case(args.output, params)
        source = Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio" / "ComfyUI"
        result = renderer.tools().checker().run_check(source, 120, 0, guarded=True, calibration=case)
        initial = result["memory_guard"]["initial"]
        receipt = {"revision": windows_sfx.REVISION, "recipe": RECIPE, "tool_sha256": renderer.tool_hashes(),
                   "compatibility": baseline_gpu_compatibility(),
                   "runtime": baseline_gpu_runtime(),
                   "files": {name: {"size": size, "sha256": digest, "mtime_ns": (root / name).stat().st_mtime_ns}
                             for name, (size, digest) in windows_sfx.FILES.items()},
                   "hardware": {key: initial[key] for key in
                                ("physical_total_mb", "host_total_mb", "gpu_dedicated_total_mb")}, "result": result}
        temporary = root / "qualification.json.part"
        temporary.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        temporary.replace(root / "qualification.json")
    except (ImportError, OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
