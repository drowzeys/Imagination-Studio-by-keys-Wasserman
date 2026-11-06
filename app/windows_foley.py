"""Bounded MMAudio foley on the shared, owned Windows ComfyUI calibration path."""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import windows_features
import windows_media
import windows_storyboard as renderer
from windows_brain import verify_file
from windows_process import OwnedJob
from windows_memory import DedicatedMemory, WindowsSignals, host_memory, physical_memory_mb, gpu_memory_warning
from feature_checks import EvidenceStore

PACK = Path(__file__).resolve().parents[1]
NODE_COMMIT = "8eaeb72edc3aaf2059b57f2d96a1f6f689f19ae2"
RECIPE = {"seconds": 5, "steps": 25, "cfg": 4.5, "rows": 360, "seed": 7}
CUSTOM_NODES = ("ComfyUI-MMAudio", "studio_mmaudio")
MAX_WIDTH = 660
HOST_REQUIRED_MB = 9216
FILES = {
    "mmaudio_large_44k_v2_fp16.safetensors": (2061198424, "a61378951ba4e8bfde2e977c0ad8211a7e02e1e92fd6a5b1c258a6464c1d102f"),
    "mmaudio_vae_44k_fp16.safetensors": (610966060, "2598189f081bd10c86975106e5b6e2490168667429a75b52d77671283f245649"),
    "mmaudio_synchformer_fp16.safetensors": (474981098, "b8d05128c76da6ee88720a89d25a5f86c603589883d02b53f563c3c66afe3581"),
    "apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors": (1973509450, "d8ad903a79adc2b29bacfeedabfa3d78b03d36d3b4dcb885c0649fe8c5d763cb"),
    "nvidia/bigvgan_v2_44khz_128band_512x/bigvgan_generator.pt": (489041291, "d9fe7ec6bd0b44ed9d66973d5012d8181c1570b01e5c72df51973e241dccd357"),
    "nvidia/bigvgan_v2_44khz_128band_512x/config.json": (1403, "65b7f487bfaf15256056a75f6667ef5f640214c981922a111927873705ea4ddb"),
}


def folder() -> Path:
    return Path(os.environ.get("STUDIO_WINDOWS_FOLEY_DIR") or
                Path(os.environ.get("LOCALAPPDATA", "")) / "ImaginationStudio/models/Sound-Comfy/mmaudio")


def node_folder() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio/ComfyUI/custom_nodes/ComfyUI-MMAudio"


def gpu_python() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio/foley-gpu-venv/Scripts/python.exe"


@lru_cache(maxsize=1)
def device_uuid() -> str:
    from windows_memory import single_gpu_uuid
    return single_gpu_uuid()


def fingerprint() -> dict:
    from windows_qualification import GPU_CONTRACT, gpu_compatibility
    needed = {"torch", "torchvision", "numpy", "pillow", "soxr", "librosa", "numba", "llvmlite", "torchdiffeq",
              "einops", "timm", "omegaconf", "open_clip_torch", "accelerate", "ftfy", "safetensors", "av"}
    site = gpu_python().parent.parent / "Lib/site-packages"
    versions = {dist.metadata["Name"].lower().replace("-", "_"): dist.version
                for dist in metadata.distributions(path=[str(site)])}
    missing = needed - versions.keys()
    if missing:
        raise RuntimeError("Foley dependencies are missing: " + ", ".join(sorted(missing)))
    sources = {}
    for name in ("app/windows_foley.py", "app/windows_process.py", "app/windows_backend_handle.py", "app/windows_memory.py",
                 "scripts/windows-comfy-check.py", "scripts/windows-comfy-calibration-launcher.py",
                 "scripts/windows-calibration-node.py", "spark/studio_mmaudio/__init__.py", "sound_generate.py"):
        sources[name] = windows_features.source_signature((PACK / name).read_text(encoding="utf-8"),
                                                          exclude=("status", "main", "fingerprint"))
    sources["audio_validation"] = windows_features.source_signature((PACK / "app/windows_sound.py").read_text(encoding="utf-8"),
                                                                    ("validate_audio",))
    sources["shared_graph"] = windows_features.source_signature((PACK / "spark/spark_generate.py").read_text(encoding="utf-8"),
                                                               ("foley_workflow", "prepared_clip"))
    for path in node_folder().rglob("*"):
        if path.suffix in (".py", ".json", ".yaml") and ".git" not in path.parts:
            sources["nodes/" + path.relative_to(node_folder()).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    from windows_memory import dedicated_capacity
    dedicated = dedicated_capacity(device_uuid())
    python = gpu_python().stat()
    limits = renderer.tools().checker().CalibrationCase
    media = {}
    for name in ("ffmpeg", "ffprobe"):
        executable = shutil.which(name, path=windows_media.search_path(os.environ.get("PATH", "")))
        if not executable:
            raise RuntimeError("Install the native FFmpeg/ffprobe tools before qualifying foley.")
        stat = Path(executable).stat()
        media[name] = [hashlib.sha256(str(Path(executable).resolve()).encode()).hexdigest(), stat.st_size, stat.st_mtime_ns]
    return {"schema": 1, "runner_contract": GPU_CONTRACT,
            "limits_mb": [limits.gpu_limit_mb, limits.process_commit_mb, limits.job_commit_mb],
            "python_abi": gpu_compatibility(gpu_python().parent.parent)["python_abi"],
            "recipe": RECIPE, "max_width": MAX_WIDTH, "host_required_mb": HOST_REQUIRED_MB,
            "sources": sources, "versions": {name: versions[name] for name in sorted(needed)},
            "python": [python.st_size, python.st_mtime_ns], "media": media, "physical_mb": physical_memory_mb(),
            "host_mb": host_memory()[0], "dedicated_mb": dedicated,
            "weights": {name: [size, digest, (folder() / name).stat().st_mtime_ns] for name, (size, digest) in FILES.items()}}


def compatibility(value: dict) -> dict:
    from windows_qualification import gpu_version_contract
    keys = ("schema", "recipe", "max_width", "host_required_mb", "physical_mb", "host_mb", "dedicated_mb", "weights", "versions")
    if not all(key in value for key in keys):
        return value
    runtime = gpu_version_contract(value.get("python_abi", "3.13"), value["versions"])
    runtime["contract"] = value.get("runner_contract", 1)
    return {**{key: value[key] for key in keys if key not in ("weights", "versions")},
            "limits_mb": value.get("limits_mb", [12288, 12288, 16384]),
            "runtime": runtime, "weights": {name: metadata[:2] for name, metadata in value["weights"].items()}}


def status() -> dict:
    from feature_checks import matches_qualification
    from windows_qualification import verify_model_updates
    base = {"available": False, "qualified": False, "installed": False, "state": "not_installed"}
    try:
        if not gpu_python().is_file() or not (node_folder() / "nodes.py").is_file():
            return {**base, "why": "Install the foley components with its setup command."}
        if any(not (folder() / name).is_file() or (folder() / name).stat().st_size != size for name, (size, _) in FILES.items()):
            return {**base, "why": "Foley weights are missing or incomplete; run its setup command."}
        current = fingerprint()
        base.update(installed=True, state="needs_qualification")
        receipt = EvidenceStore(windows_features.folder()).read("foley")
        previous = receipt.get("fingerprint")
        if receipt.get("ok") is not True or not isinstance(previous, dict) or not matches_qualification(
                previous, current, compatibility(previous), compatibility(current)):
            attempt = EvidenceStore(windows_features.folder()).read("foley", "attempt")
            failure = str(attempt.get("error") or "")
            if any(reason in failure for reason in ("sampler stopped answering", "samples are stale", "budget monitoring failed")):
                return {**base, "state": "check_refused",
                        "why": "The last foley check was refused because memory telemetry did not stay fresh. This profile remains unqualified; no guard was bypassed."}
            return {**base, "why": "Run the foley check in Machine > Feature checks; saved evidence must match this runtime and recipe."}
        if "weights" in previous:
            verify_model_updates(folder(), FILES, {name: data[2] for name, data in previous["weights"].items()})
        return {**base, "available": True, "qualified": True, "state": "ready", "why": "", "runtime_updated": previous != current,
                "hardware": receipt["hardware"], "max_seconds": 5, "fps": 24, "max_prepared_width": MAX_WIDTH}
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        return {**base, "state": "setup_error", "why": str(error)}


def parameters(params: dict) -> dict:
    allowed = {"video", "prompt", "negative", "seed", "steps", "cfg", "keep_sound", "foley_db"}
    if not isinstance(params, dict) or set(params) - allowed:
        raise ValueError("Foley takes video, prompt, negative, seed, steps, cfg, keep_sound and foley_db only.")
    out = dict(params)
    if not isinstance(out.get("video"), str) or not out["video"]:
        raise ValueError("Choose a clip from the Library for foley.")
    for key in ("prompt", "negative"):
        value = out.get(key, "")
        if not isinstance(value, str) or len(value) > 600:
            raise ValueError(f"{key} must be at most 600 characters.")
        out[key] = value
    for key in ("steps", "cfg"):
        value = out.get(key, RECIPE[key])
        if type(value) not in (int, float) or not math.isfinite(value) or value != RECIPE[key]:
            raise ValueError("This foley recipe uses 25 passes and CFG 4.5.")
        out[key] = RECIPE[key]
    out["seed"] = out.get("seed", 7)
    if type(out["seed"]) is not int or not 0 <= out["seed"] <= 2 ** 32 - 1:
        raise ValueError("The foley seed must be an unsigned 32-bit whole number.")
    out["keep_sound"] = out.get("keep_sound", True)
    if type(out["keep_sound"]) is not bool:
        raise ValueError("keep_sound is true or false.")
    level = out.get("foley_db", -6)
    if type(level) not in (int, float) or not math.isfinite(level) or not -30 <= level <= 0:
        raise ValueError("foley_db is between -30 and 0.")
    out["foley_db"] = level
    return out


def clip_info(clip: Path) -> dict:
    import graphics
    info = graphics.probe(clip)
    values = [info[k] for k in ("width", "height", "seconds", "fps")]
    if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("The foley source must be a readable clip.")
    width, height, seconds, fps = values
    if not 1 <= seconds <= 5.02 or abs(fps - 24) > .001 or width > 1920 or height > 1080:
        raise ValueError("Qualified foley takes 1-5 second clips at 24 fps, up to 1920x1080.")
    if math.ceil(width / height * RECIPE["rows"] / 2) * 2 > MAX_WIDTH:
        raise ValueError("The clip is wider than the qualified foley aspect-ratio limit.")
    return info


def preflight() -> None:
    from windows_runtime import usage_status
    found = usage_status("foley", status())
    if not found["available"]:
        raise RuntimeError(found["why"])
    check_room()


def check_room() -> None:
    _, free = host_memory()
    with WindowsSignals() as signals:
        used, limit, _, low = signals.read()
    configured = os.environ.get("SPARK_MEMORY_FLOOR_MB", "").strip()
    if configured and (not configured.isdigit() or int(configured) <= 0):
        raise ValueError("SPARK_MEMORY_FLOOR_MB must be a positive integer.")
    floor = int(configured or 0)
    required_host = max(HOST_REQUIRED_MB, floor)
    if low or free < required_host or limit - used < renderer.JOB_LIMIT_MB + 2048:
        raise RuntimeError(f"Foley needs {required_host} MiB host memory and 18432 MiB commit headroom"
                           f" (available {free} MiB host, {limit - used} MiB commit); unload unused local models and retry.")
    with DedicatedMemory(device_uuid()) as memory:
        _, available = memory.read()
    if available < renderer.GPU_LIMIT_MB + 2048:
        raise RuntimeError(gpu_memory_warning(available, renderer.GPU_LIMIT_MB + 2048, device_uuid()))


def build(params: dict, home: Path, job_id: str, inside) -> tuple[list[str], dict, list[str]]:
    params = parameters(params)
    source = inside(params["video"])
    if not source.is_file():
        raise ValueError("The foley source is missing from the Library.")
    info = clip_info(source)
    if job_id == "check":
        return [], {}, []
    output = home / "sound/foley" / job_id
    output.mkdir(parents=True, exist_ok=False)
    plan = {**params, "video": str(source), "seconds": min(info["seconds"], 5),
            "home": str(home), "output": str(output), "id": job_id}
    (output / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return [str(gpu_python()), "-X", "utf8", str(Path(__file__)), "--render", "--plan", str(output / "plan.json")], {}, [
        str(output / "foley.mp4"), str(output / "foley.wav")]


def verify_weights(root: Path) -> None:
    for name, (size, digest) in FILES.items():
        verify_file(root / name, size, digest)


def verify_nodes() -> None:
    root = node_folder()
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=True)
    if result.stdout.strip() != NODE_COMMIT:
        raise RuntimeError("The ComfyUI-MMAudio node pack does not match the Studio's pin.")
    changed = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                             capture_output=True, text=True, timeout=10, check=True)
    if changed.stdout.strip():
        raise RuntimeError("The pinned ComfyUI-MMAudio files have local changes; qualification was not started.")


def fixture(target: Path, seconds: int = 5, width: int = MAX_WIDTH) -> None:
    from PIL import Image, ImageDraw
    owned = OwnedJob(job_commit_mb=4096)
    try:
        process = owned.spawn(["ffmpeg", "-nostdin", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                               "-s", f"{width}x{RECIPE['rows']}", "-r", "24", "-i", "-", "-f", "lavfi", "-i",
                               f"sine=frequency=440:sample_rate=44100:duration={seconds}", "-af", "volume=0.02",
                               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(target)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for frame in range(seconds * 24):
            image = Image.new("RGB", (width, RECIPE["rows"]), "#303840")
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 300, width, RECIPE["rows"]), fill="#606060")
            x = 70 + frame * 4
            y = 266 - int(160 * abs(math.sin(math.pi * frame / 24)))
            draw.ellipse((x - 24, y - 24, x + 24, y + 24), fill="#e08020")
            process.stdin.write(image.tobytes())
        process.stdin.close()
        process.stdin = None
        _, error = process.communicate(timeout=30)
        if process.returncode:
            raise RuntimeError(error.decode("utf-8", errors="replace")[-500:])
    finally:
        owned.close()


def prepare(directory: Path, clip: Path) -> None:
    log = directory / "startup.log"
    log.touch()
    os.link(log, clip.parent / "backend.log")
    custom = directory / "custom_nodes"
    node = custom / "windows_calibration" / "__init__.py"
    node.parent.mkdir(parents=True)
    shutil.copyfile(PACK / "scripts/windows-calibration-node.py", node)
    shutil.copytree(node_folder(), custom / "ComfyUI-MMAudio", ignore=shutil.ignore_patterns(".git", "__pycache__"))
    shutil.copytree(PACK / "spark/studio_mmaudio", custom / "studio_mmaudio", ignore=shutil.ignore_patterns("__pycache__"))
    for name in FILES:
        target = directory / "models/mmaudio" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(folder() / name, target)
    target = directory / "input" / "foley.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(clip, target)


def exercise(check, base: str, directory: Path, process, monitor, output: Path, params=None, seconds=5) -> dict:
    sys.path.insert(0, str(PACK / "spark"))
    import spark_generate
    import windows_sound
    helper = renderer.tools()
    params = params or {"prompt": "A rubber ball bounces on a hard floor, with soft thumps.", "negative": "", "seed": 7}
    graph = spark_generate.foley_workflow("foley.mp4", params["prompt"], params["negative"],
                                          seconds, params["seed"], RECIPE["steps"], RECIPE["cfg"],
                                          prefix="windows_calibration/foley")
    graph["19"] = {"class_type": "WindowsCalibrationReceipt", "inputs": {"audio": ["18", 0]}}
    needed = {node["class_type"] for node in graph.values()}
    registered = check.read_json(base, "/object_info")
    if not needed.issubset(registered):
        raise RuntimeError("Foley nodes are missing: " + ", ".join(sorted(needed - set(registered))))
    reply = helper.post_prompt(base, graph)
    if reply.get("error") or reply.get("node_errors"):
        raise RuntimeError("ComfyUI rejected the pinned foley graph: " + json.dumps(reply)[:500])
    prompt_id = str(uuid.UUID(reply["prompt_id"]))
    started = time.monotonic()
    while True:
        monitor.check()
        if process.poll() is not None:
            raise RuntimeError("The owned foley backend exited.")
        helper.validate_queue(check.read_json(base, "/queue"), prompt_id)
        history = check.read_json(base, "/history/" + prompt_id)
        if prompt_id in history:
            record = history[prompt_id]
            if record.get("status", {}).get("status_str") != "success":
                errors = [event[1].get("exception_message", "engine error") for event in record.get("status", {}).get("messages", [])
                          if isinstance(event, list) and len(event) == 2 and event[0] == "execution_error"]
                raise RuntimeError("The foley graph failed: " + "; ".join(errors)[:1000])
            break
        if time.monotonic() - started > 300:
            raise RuntimeError("Foley exceeded its five-minute render deadline.")
        time.sleep(.25)
    outputs = record["outputs"]
    receipts = outputs.get("19", {}).get("windows_calibration")
    if not isinstance(receipts, list) or len(receipts) != 1:
        raise RuntimeError("Foley did not return its measured allocator receipt.")
    helper.validate_receipt(receipts[0], check.CalibrationCase.gpu_limit_mb << 20)
    files = [value for values in outputs.get("18", {}).values() for value in (values if isinstance(values, list) else [])
             if isinstance(value, dict) and value.get("type") == "output" and value.get("filename", "").endswith(".flac")]
    if len(files) != 1:
        raise RuntimeError("Foley did not write exactly one lossless audio file.")
    root = (directory / "output").resolve()
    source = (root / files[0].get("subfolder", "") / files[0]["filename"]).resolve()
    if not source.is_relative_to(root):
        raise RuntimeError("Foley output is outside the owned backend's folder.")
    native = output / ".native.wav"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(source), "-af", "alimiter=limit=0.891250938:level=false",
                    "-c:a", "pcm_s16le", str(native)], capture_output=True, check=True, timeout=30)
    generated_seconds = min(int(seconds * 8) / 8, int(seconds * 25) / 25)
    native_audio = windows_sound.validate_audio(native, channels=1, expected_samples=round(generated_seconds * 44100))
    target = output / "foley.wav"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(native),
                    "-af", f"apad=whole_dur={seconds:.6f},atrim=duration={seconds:.6f}",
                    "-c:a", "pcm_s16le", str(target)], capture_output=True, check=True, timeout=30)
    native.unlink()
    audio = windows_sound.validate_audio(target, channels=1, expected_samples=round(seconds * 44100))
    return {"recipe": RECIPE, "audio": audio, "allocator_receipt": receipts[0],
            "native_audio": native_audio, "request_to_file_seconds": time.monotonic() - started, "qualified_route": False}


def render(plan: dict, qualification=False) -> dict:
    sys.path.insert(0, str(PACK / "spark"))
    sys.path.insert(0, str(PACK))
    import spark_generate
    import sound_generate
    source, output = Path(plan["video"]), Path(plan["output"])
    info = clip_info(source)
    params = parameters({key: plan[key] for key in ("video", "prompt", "negative", "seed", "steps", "cfg", "keep_sound", "foley_db") if key in plan})
    if not qualification:
        preflight()
    verify_nodes()
    verify_weights(folder())
    work = output / ".work"
    work.mkdir(exist_ok=True)
    prepared = spark_generate.prepared_clip(source, min(info["seconds"], 5), work, rows=RECIPE["rows"])
    check = renderer.tools().checker()
    case = check.CalibrationCase(lambda directory: prepare(directory, prepared),
                                 lambda base, directory, process, monitor: exercise(check, base, directory, process, monitor, output,
                                                                                  params, min(info["seconds"], 5)),
                                 custom_nodes=CUSTOM_NODES, wddm_device_uuid=device_uuid())
    result = check.run_check(Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio/ComfyUI", 120, 0, guarded=True, calibration=case)
    sound_generate.lay(source, output / "foley.wav", output / "foley.mp4", not params["keep_sound"], params["foley_db"])
    validate_film(source, output / "foley.mp4")
    result["source_seconds"] = info["seconds"]
    (output / "render.json").write_text(json.dumps(result), encoding="utf-8")
    return result


def save_take(job: dict, home: Path, output: Path) -> None:
    result = json.loads((output / "render.json").read_text(encoding="utf-8"))
    params = job["params"]
    value = {"id": job["id"], "kind": "foley", "model": "MMAudio large_44k_v2", "prompt": params["prompt"],
             "negative": params["negative"], "seed": params["seed"], "steps": 25, "cfg": 4.5,
             "path": (output / "foley.wav").relative_to(home).as_posix(),
             "clip": (output / "foley.mp4").relative_to(home).as_posix(), "source": params["video"],
             "seconds": result["calibration"]["audio"]["seconds"], "rate": 44100, "created": time.time(),
             "elapsed": time.time() - job["started"], "keep_sound": params["keep_sound"],
             "licence": "CC-BY-NC 4.0, non-commercial"}
    (output / "foley.take.json").write_text(json.dumps(value), encoding="utf-8")


def validate_film(source: Path, target: Path) -> None:
    import graphics
    original, finished = graphics.probe(source), graphics.probe(target)
    if (finished["width"], finished["height"], finished["fps"]) != (original["width"], original["height"], original["fps"]):
        raise RuntimeError("Foley changed the clip's picture format.")
    if not finished["audio"] or abs(finished["seconds"] - original["seconds"]) > 1 / 24:
        raise RuntimeError("Foley dropped sound or changed the clip duration.")
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(target), "-f", "null", "-"],
                   capture_output=True, check=True, timeout=30)
    hashes = []
    for path in (source, target):
        answer = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "framemd5", "-"],
                                capture_output=True, check=True, timeout=30)
        hashes.append([line.rsplit(b",", 1)[-1].strip() for line in answer.stdout.splitlines() if not line.startswith(b"#")])
    if hashes[0] != hashes[1]:
        raise RuntimeError("Foley changed the decoded source frames.")


def qualify(output: Path) -> dict:
    check_room()
    before = fingerprint()
    results, hardware = [], None
    for seconds, keep in ((5, True), (1, False)):
        case = output / f"case-{seconds}"
        case.mkdir(parents=True, exist_ok=False)
        clip = case / "source.mp4"
        fixture(clip, seconds)
        plan = {"video": str(clip), "output": str(case), "prompt": "A rubber ball bounces on a hard floor, with soft thumps.",
                "negative": "", "seed": 7, "keep_sound": keep}
        result = render(plan, qualification=True)
        initial = result["memory_guard"]["initial"]
        hardware = {key: initial[key] for key in ("physical_total_mb", "host_total_mb", "gpu_dedicated_total_mb")}
        results.append({"case": f"{seconds}s-{'under' if keep else 'replace'}",
                        "elapsed_s": result["calibration"]["request_to_file_seconds"],
                        "peak_job_commit_mb": result["job_commitment"]["peak_job_commit_bytes"] / 2 ** 20,
                        "peak_process_commit_mb": result["job_commitment"]["peak_process_commit_bytes"] / 2 ** 20,
                        "rendered": True, "measurements": result["memory_guard"],
                        "audio": result["calibration"]["audio"]})
    if before != fingerprint():
        raise RuntimeError("Foley's rendering dependencies changed during the check.")
    receipt = {"ok": True, "feature": "foley", "fingerprint": before, "hardware": hardware,
               "checked_at": datetime.now(timezone.utc).isoformat(), "results": results}
    EvidenceStore(windows_features.folder()).write("foley", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--calibrate", action="store_true")
    mode.add_argument("--qualify", action="store_true")
    mode.add_argument("--render", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    os.environ["PATH"] = windows_media.search_path(os.environ.get("PATH", ""))
    try:
        if os.name != "nt":
            raise RuntimeError("This calibration requires native Windows.")
        if args.render:
            if not args.plan:
                raise ValueError("A foley render requires its checked plan.")
            result = render(json.loads(args.plan.read_text(encoding="utf-8")))
            print(json.dumps(result))
            return 0
        if not args.output:
            raise ValueError("Choose a new output folder for the foley check.")
        verify_nodes()
        verify_weights(folder())
        args.output.mkdir(parents=True, exist_ok=False)
        if args.qualify:
            result = qualify(args.output)
            windows_features.save_attempt("foley", {"ok": True, "finished": result["checked_at"]})
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(json.dumps({"ok": True, "feature": "foley", "results": result["results"]}))
            return 0
        clip = args.output / "source.mp4"
        fixture(clip)
        check = renderer.tools().checker()
        case = check.CalibrationCase(lambda directory: prepare(directory, clip),
                                     lambda base, directory, process, monitor: exercise(check, base, directory, process, monitor, args.output),
                                     custom_nodes=CUSTOM_NODES, wddm_device_uuid=device_uuid())
        source = Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio/ComfyUI"
        result = check.run_check(source, 120, 0, guarded=True, calibration=case)
        (args.output / "report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        if args.qualify:
            windows_features.save_attempt("foley", {"ok": False, "state": "failed", "error": str(error),
                                                   "finished": datetime.now(timezone.utc).isoformat()})
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
