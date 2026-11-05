"""Measured, narrowly bounded SD-Turbo stills on native Windows ComfyUI."""
from __future__ import annotations

import argparse
import atexit
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

from windows_brain import fetch, verify_file
from windows_comfy import WindowsComfy
from feature_checks import matches_qualification
from windows_qualification import LEGACY_GPU_COMPATIBILITY, baseline_gpu_compatibility, baseline_gpu_runtime, hardware_matches, verify_model_updates

PACK = Path(__file__).resolve().parents[1]
MODEL = "sd-turbo"
REPO = "stabilityai/sd-turbo"
REVISION = "b261bac6fd2cf515557d5d0707481eafa0485ec2"
FILES = {
    "unet/diffusion_pytorch_model.fp16.safetensors": (1731904736, "40ec400881e27d1376c7c95c5bd495f407b33756e80eb6365e301c33a07af6e5"),
    "text_encoder/model.fp16.safetensors": (680820392, "bc1827c465450322616f06dea41596eac7d493f4e95904dcb51f0fc745c4e13f"),
    "vae/diffusion_pytorch_model.fp16.safetensors": (167335342, "3e4c08995484ee61270175e9e7a072b66a6e4eeb5f0c266667fe1f45b90daf9a"),
}
MANIFEST_SHA256 = hashlib.sha256(json.dumps(FILES, sort_keys=True).encode()).hexdigest()
SIDE = 512
MAX_COUNT = 6
GPU_LIMIT_MB, PROCESS_LIMIT_MB, JOB_LIMIT_MB = 12288, 12288, 16384
_backend: WindowsComfy | None = None
_backend_error = ""
_backend_kind = None
_backend_lock = threading.RLock()
_prompt_lock = threading.RLock()
_active_job = _active_prompt = None


def folder() -> Path:
    return Path(os.environ.get("STUDIO_WINDOWS_IMAGE_DIR") or
                Path(os.environ.get("LOCALAPPDATA", "")) / "ImaginationStudio" / "models" / "SD-Turbo")


def verify_weights(root: Path) -> None:
    for name, (size, digest) in FILES.items():
        verify_file(root / name, size, digest)
    for name in ("LICENSE.md", "model_index.json"):
        if not (root / name).is_file() or not (root / name).stat().st_size:
            raise RuntimeError(f"the SD-Turbo {name} file is missing")


def tool_hashes() -> dict:
    paths = ["app/windows_storyboard.py", "app/windows_sound.py", "app/windows_sfx.py", "app/windows_comfy.py", "app/windows_brain.py", "app/windows_memory.py", "app/windows_process.py",
             "scripts/windows-comfy-check.py", "scripts/windows-sfx-check.py",
             "scripts/windows-calibration-node.py", "scripts/windows-comfy-calibration-launcher.py"]
    return {name: hashlib.sha256((PACK / name).read_bytes()).hexdigest() for name in paths}


def status() -> dict:
    root = folder()
    try:
        receipt = json.loads((root / "qualification.json").read_text(encoding="utf-8"))
        mtimes = {name: (root / name).stat().st_mtime_ns for name in FILES}
        current_tools = tool_hashes()
        provenance = {"tools": receipt.get("tool_sha256"), "models": receipt.get("model_mtime_ns"), "runtime": receipt.get("runtime")}
        current = {"tools": current_tools, "models": mtimes, "runtime": baseline_gpu_runtime()}
        valid = (receipt.get("weight_manifest_sha256") == MANIFEST_SHA256 and receipt.get("revision") == REVISION
                 and receipt.get("width") == SIDE and receipt.get("height") == SIDE
                 and matches_qualification(provenance, current, receipt.get("compatibility", LEGACY_GPU_COMPATIBILITY),
                                           baseline_gpu_compatibility())
                 and all((root / name).stat().st_size == info[0] for name, info in FILES.items())
                 and receipt.get("result", {}).get("ok") is True
                 and isinstance(receipt.get("hardware"), dict)
                 and all(type(receipt["hardware"].get(key)) is int and receipt["hardware"][key] > 0
                         for key in ("physical_total_mb", "host_total_mb", "gpu_dedicated_total_mb"))
                 and hardware_matches(receipt["hardware"]))
    except FileNotFoundError:
        return {"available": False, "why": "Run setup-windows-images.ps1 -Model auto, then check it in Machine > Feature checks."}
        if valid:
            verify_model_updates(root, FILES, receipt.get("model_mtime_ns", {}))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, AttributeError) as error:
        return {"available": False, "why": f"The Windows storyboard qualification is unreadable: {type(error).__name__}."}
    if not valid:
        return {"available": False, "why": "The model or render code changed; repeat check-windows-storyboard.ps1."}
    return {"available": True, "why": "", "width": SIDE, "height": SIDE, "max_count": MAX_COUNT,
            "model": MODEL, "hardware": receipt["hardware"], "runtime_updated": provenance != current}


def parameters(params: dict) -> dict:
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    allowed = {"prompt", "prompts", "seeds", "count", "seed", "width", "height", "image_model",
               "suite_import", "suite_shot", "suite_shots"}
    extra = set(params) - allowed
    if extra:
        raise ValueError("unsupported Windows still settings: " + ", ".join(sorted(extra)))
    batch = "prompts" in params
    prompts = params["prompts"] if batch else [params.get("prompt")]
    if (not isinstance(prompts, list) or not 1 <= len(prompts) <= MAX_COUNT
            or any(not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000 for prompt in prompts)):
        raise ValueError("provide 1 to 6 prompts, each of 1 to 20,000 characters")
    if batch and "prompt" in params:
        raise ValueError("use prompt or prompts, not both")
    out = {"prompts" if batch else "prompt": prompts if batch else prompts[0],
           "count": params.get("count", len(prompts) if batch else 1), "seed": params.get("seed", 1),
           "width": params.get("width", SIDE), "height": params.get("height", SIDE),
           "image_model": params.get("image_model", MODEL)}
    if out["image_model"] != MODEL:
        raise ValueError("only SD-Turbo is qualified for Windows stills")
    if any(type(out[key]) is not int for key in ("count", "seed", "width", "height")):
        raise ValueError("count, seed, width and height must be whole numbers")
    if not 1 <= out["count"] <= MAX_COUNT or not 0 <= out["seed"] <= 2 ** 32 - MAX_COUNT:
        raise ValueError("Windows stills take 1 to 6 sequential images and a seed from 0 to 4294967290")
    if batch and out["count"] != len(prompts):
        raise ValueError("count must match the number of prompts")
    if "seeds" in params:
        seeds = params["seeds"]
        if (not batch or not isinstance(seeds, list) or len(seeds) != len(prompts)
                or any(type(seed) is not int or not 0 <= seed <= 2 ** 32 - 1 for seed in seeds)):
            raise ValueError("seeds must give one unsigned 32-bit whole number per batched prompt")
        out["seeds"] = list(seeds)
    if (out["width"], out["height"]) != (SIDE, SIDE):
        raise ValueError("the measured Windows storyboard route is 512x512 only; larger canvases are not enabled")
    for key in ("suite_import", "suite_shot"):
        if key in params:
            if not isinstance(params[key], str) or not params[key] or len(params[key]) > 64:
                raise ValueError(f"{key} must be an import/shot id")
            out[key] = params[key]
    if "suite_shots" in params:
        ids = params["suite_shots"]
        if (not batch or "suite_shot" in out or not isinstance(ids, list) or len(ids) != len(prompts)
                or any(not isinstance(one, str) or not one or len(one) > 64 for one in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError("suite_shots must give one distinct import shot id per batched prompt")
        out["suite_shots"] = list(ids)
    if ("suite_import" in out) != ("suite_shot" in out or "suite_shots" in out):
        raise ValueError("suite_import and its shot id(s) are supplied together")
    if batch and "suite_shot" in out:
        raise ValueError("batched prompts use suite_shots, not suite_shot")
    return out


def image_plan(params: dict) -> list[tuple[str, int, str]]:
    batch = "prompts" in params
    prompts = params["prompts"] if batch else [params["prompt"]] * params["count"]
    seeds = params.get("seeds", [params["seed"] + i for i in range(params["count"])])
    return [(prompt, seed, f"scout_s{seed}_shot{i + 1}.png" if batch else f"scout_s{seed}.png")
            for i, (prompt, seed) in enumerate(zip(prompts, seeds, strict=True))]


def preflight(qualification: dict | None = None) -> None:
    from windows_memory import DedicatedMemory, WindowsSignals, gpu_memory_warning, host_memory, physical_memory_mb
    if qualification is None:
        from windows_runtime import usage_status
        found = usage_status("scout", status())
    else:
        found = qualification
    if not found["available"]:
        raise RuntimeError(found["why"])
    hardware = found["hardware"]
    total, available = host_memory()
    if physical_memory_mb() != hardware["physical_total_mb"] or total != hardware["host_total_mb"]:
        raise RuntimeError("the Windows memory partition changed; repeat storyboard qualification")
    floor = os.environ.get("SPARK_MEMORY_FLOOR_MB", "").strip()
    if floor and (not floor.isdigit() or int(floor) <= 0):
        raise ValueError("SPARK_MEMORY_FLOOR_MB must be a positive integer")
    with WindowsSignals() as signals:
        used, limit, _, low = signals.read()
    if low or not available or available < int(floor or "0"):
        raise RuntimeError("Windows reports low physical memory or the configured host reserve was reached")
    with _backend_lock:
        backend = _backend
        credit = backend.accounting() if backend is not None else {"commit_mb": 0, "reserved_mb": 0}
    if limit - used < JOB_LIMIT_MB - credit["commit_mb"] + 2048:
        raise RuntimeError("the Windows image job cap does not fit commit headroom with its reserve")
    device = subprocess.run(["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader", "--id=0"],
                            capture_output=True, text=True, timeout=5, check=True).stdout.strip()
    with DedicatedMemory(device) as memory:
        dedicated, free = memory.read()
    if dedicated != hardware["gpu_dedicated_total_mb"]:
        raise RuntimeError("the dedicated GPU partition changed; repeat storyboard qualification")
    if free < GPU_LIMIT_MB - credit["reserved_mb"] + 2048:
        raise RuntimeError(gpu_memory_warning(free, GPU_LIMIT_MB - credit["reserved_mb"] + 2048, device))


def build(params: dict, home: Path, job_id: str) -> tuple[list[str], dict, list[str]]:
    params = parameters(params)
    target = home / "scouts" / job_id
    python = Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio" / "gpu-venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise RuntimeError("the separate Windows GPU environment is missing")
    command = [str(python), "-X", "utf8", str(Path(__file__).resolve()), "--render", "--output", str(target),
               "--count", str(params["count"]), "--seed", str(params["seed"])]
    outputs = [str(target / name) for _, _, name in image_plan(params)]
    if "prompts" in params:
        command.append("--prompts-stdin")
        env = {}
    else:
        env = {"STUDIO_IMAGE_PROMPT": params["prompt"]}
    return command, env, outputs + [str(target / "sheet.jpg")]


def tools():
    spec = importlib.util.spec_from_file_location("windows_image_calibration_tools", PACK / "scripts" / "windows-sfx-check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow(prompt: str, seed: int) -> dict:
    return {
        "1": {"class_type": "DiffusersLoader", "inputs": {"model_path": MODEL}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": SIDE, "height": SIDE, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
              "latent_image": ["4", 0], "seed": seed, "steps": 1, "cfg": 1.0,
              "sampler_name": "euler_ancestral", "scheduler": "normal", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "storyboard"}},
        "19": {"class_type": "WindowsCalibrationImageReceipt", "inputs": {"images": ["6", 0]}},
    }


def render_case(output: Path, params: dict, cancelled=lambda: False, submit=None, completed=None, progress=None):
    from PIL import Image, ImageDraw, ImageStat
    root, plan = folder(), image_plan(params)
    count = len(plan)
    if output.exists():
        raise RuntimeError("the output folder already exists; existing files are not replaced")
    helper = tools()
    check = helper.checker()

    def prepare(directory):
        for name in (*FILES, "model_index.json"):
            target = directory / "models" / "diffusers" / MODEL / name
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(root / name, target)
        node = directory / "custom_nodes" / "windows_calibration" / "__init__.py"
        node.parent.mkdir(parents=True)
        shutil.copyfile(PACK / "scripts" / "windows-calibration-node.py", node)

    def exercise(base, directory, process, monitor):
        images, receipts = [], []
        started = time.monotonic()
        for index, (prompt, seed, name) in enumerate(plan):
            if cancelled():
                return {"cancelled": True}
            graph = workflow(prompt, seed)
            reply = submit(graph) if submit else helper.post_prompt(base, graph)
            if reply is None:
                return {"cancelled": True}
            if reply.get("error") or reply.get("node_errors"):
                raise RuntimeError("ComfyUI rejected the Windows image workflow")
            prompt_id = str(uuid.UUID(reply["prompt_id"]))
            deadline = time.monotonic() + 120
            while True:
                monitor.check()
                if cancelled():
                    if completed:
                        completed(prompt_id, True)
                    return {"cancelled": True}
                if process.poll() is not None:
                    raise RuntimeError("the owned image server exited")
                helper.validate_queue(check.read_json(base, "/queue"), prompt_id)
                history = check.read_json(base, "/history/" + prompt_id)
                if prompt_id in history:
                    record = history[prompt_id]
                    if record.get("status", {}).get("status_str") != "success":
                        errors = [message[1] for message in record.get("status", {}).get("messages", [])
                                  if isinstance(message, list) and len(message) == 2 and message[0] == "execution_error"
                                  and isinstance(message[1], dict)]
                        reason = errors[0].get("exception_message", "unknown engine error") if errors else "unknown engine error"
                        raise RuntimeError("the Windows image workflow failed: " + str(reason)[:800])
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("the Windows still exceeded its 120 second render deadline")
                time.sleep(0.25)
            if completed:
                completed(prompt_id, False)
            if cancelled():
                return {"cancelled": True}
            receipt = record["outputs"]["19"]["windows_calibration"][0]
            helper.validate_receipt(receipt, GPU_LIMIT_MB << 20)
            receipts.append(receipt)
            saved = record["outputs"]["7"]["images"]
            if len(saved) != 1 or saved[0].get("type") != "output":
                raise RuntimeError("the image workflow did not save exactly one PNG")
            inside = (directory / "output").resolve()
            source = (inside / saved[0].get("subfolder", "") / saved[0]["filename"]).resolve()
            if not source.is_relative_to(inside):
                raise RuntimeError("ComfyUI returned a file outside its isolated output folder")
            with Image.open(source) as picture:
                picture.load()
                image = picture.convert("RGB")
            if image.size != (SIDE, SIDE) or max(ImageStat.Stat(image).stddev) < 1:
                raise RuntimeError("the generated still has the wrong size or is blank")
            output.mkdir(parents=True, exist_ok=True)
            image.save(output / name)
            images.append(image)
            if progress:
                progress(index + 1, count)
            else:
                print(f"[tensorfold] step {index + 1}/{count}", flush=True)
        if cancelled():
            return {"cancelled": True}
        columns = min(2 if count <= 4 else 3, count)
        sheet = Image.new("RGB", (columns * SIDE, ((count + columns - 1) // columns) * SIDE), "black")
        for index, image in enumerate(images):
            tile = image.copy()
            draw = ImageDraw.Draw(tile)
            draw.rectangle((0, 0, 110, 26), fill="black")
            draw.text((8, 6), f"seed {plan[index][1]}", fill="white")
            sheet.paste(tile, ((index % columns) * SIDE, (index // columns) * SIDE))
        sheet.save(output / "sheet.jpg", quality=90)
        return {"model": MODEL, "count": count, "width": SIDE, "height": SIDE,
                "request_to_files_seconds": time.monotonic() - started, "allocator_receipts": receipts}

    from windows_memory import single_gpu_uuid
    return check.CalibrationCase(prepare, exercise, wddm_device_uuid=single_gpu_uuid())


def render(output: Path, params: dict, qualify: bool) -> dict:
    root = folder()
    verify_weights(root)
    if not qualify:
        preflight()
    check = tools().checker()
    case = render_case(output, params)
    source = Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio" / "ComfyUI"
    result = check.run_check(source, 120, 0, guarded=True, calibration=case)
    if qualify:
        initial = result["memory_guard"]["initial"]
        qualification = {"weight_manifest_sha256": MANIFEST_SHA256, "revision": REVISION, "width": SIDE, "height": SIDE,
                         "model_mtime_ns": {name: (root / name).stat().st_mtime_ns for name in FILES},
                         "tool_sha256": tool_hashes(), "compatibility": baseline_gpu_compatibility(), "runtime": baseline_gpu_runtime(),
                         "hardware": {key: initial[key] for key in
                                      ("physical_total_mb", "host_total_mb", "gpu_dedicated_total_mb")},
                         "result": result}
        temporary = root / "qualification.json.part"
        temporary.write_text(json.dumps(qualification, indent=2) + "\n", encoding="utf-8")
        temporary.replace(root / "qualification.json")
    return result


def backend_status() -> dict:
    state = _backend.summary() if _backend else {"running": False, "loaded": False, "address": "", "why": ""}
    return {**state, "why": _backend_error or state["why"], "busy": _active_job is not None, "kind": _backend_kind}


def cancel_job(job_id: str):
    with _prompt_lock:
        if _active_job == job_id and _active_prompt and _backend:
            _backend.cancel(_active_prompt)


def release_backend():
    global _backend, _backend_error, _backend_kind
    with _prompt_lock:
        if _active_job is not None:
            raise RuntimeError("a storyboard is still rendering; cancel it first")
        with _backend_lock:
            if _backend:
                _backend.close()
            _backend = None
            _backend_error = ""
            _backend_kind = None


def shutdown_backend():
    global _backend, _backend_kind
    with _backend_lock:
        if _backend:
            _backend.close()
            _backend = None
            _backend_kind = None


atexit.register(shutdown_backend)


def render_job(job: dict, home: Path) -> dict:
    global _backend, _backend_error, _backend_kind, _active_job, _active_prompt
    import windows_sound
    route = windows_sound if job.get("kind") == "sfx" else sys.modules[__name__]
    kind = job.get("kind", "scout")
    params = route.parameters(job["params"])
    with _backend_lock:
        if _backend_error:
            raise RuntimeError("ComfyUI needs explicit unloading before retry: " + _backend_error)
        if _backend is not None and _backend_kind != kind:
            try:
                _backend.close()
            except (OSError, RuntimeError) as error:
                _backend_error = f"model switch cleanup failed: {error}"
                raise
            _backend = None
            _backend_kind = None
    route.preflight()
    with _prompt_lock:
        if job["status"] == "cancelled":
            return {"cancelled": True}
        _active_job = job["id"]
    def submit(graph):
        global _active_prompt
        with _prompt_lock:
            if job["status"] == "cancelled":
                return None
            reply = _backend.post("/prompt", {"prompt": graph, "client_id": uuid.uuid4().hex})
            if reply.get("error") or reply.get("node_errors"):
                raise RuntimeError("ComfyUI rejected the storyboard workflow")
            _active_prompt = str(uuid.UUID(reply["prompt_id"]))
            return reply
    def completed(prompt_id, cancelled):
        global _active_prompt
        with _prompt_lock:
            if cancelled:
                _backend.drain(prompt_id)
            _active_prompt = None
    def progress(at, total):
        job["progress"] = {"stage": "sound" if kind == "sfx" else "picture", "at": at, "of": total}
    try:
        output = home / "sound" / "sfx" / job["id"] if kind == "sfx" else home / "scouts" / job["id"]
        case = route.render_case(output, params, lambda: job["status"] == "cancelled",
                           submit, completed, progress)
        with _backend_lock:
            if _backend_error:
                raise RuntimeError("ComfyUI needs explicit unloading before retry: " + _backend_error)
            reused = _backend is not None
            if _backend is None:
                route.verify_weights(route.folder())
                _backend = WindowsComfy(tools().checker(), case.prepare)
                _backend_kind = kind
                _backend.start()
            _backend.check()
        result = case.exercise(_backend.base, _backend.directory, _backend.process, _backend.monitor)
        if not result.get("cancelled"):
            _backend.loaded = True
        return {**result, "backend_reused": reused}
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        _backend_error = str(error)
        raise
    finally:
        with _prompt_lock:
            _active_job = _active_prompt = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--qualify", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--count", type=int)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--prompts-stdin", action="store_true")
    args = parser.parse_args()
    try:
        if os.name != "nt":
            raise RuntimeError("this renderer requires native Windows")
        root = folder()
        if args.download:
            root.mkdir(parents=True, exist_ok=True)
            for name, (size, digest) in FILES.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                fetch(f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}", root / name, size, digest)
            for name in ("LICENSE.md", "model_index.json"):
                with urllib.request.urlopen(f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}", timeout=30) as answer:
                    (root / name).write_bytes(answer.read(1 << 20))
        verify_weights(root)
        if args.qualify or args.render:
            if args.prompts_stdin:
                data = sys.stdin.read((1 << 20) + 1)
                if len(data) > 1 << 20:
                    raise ValueError("the batched prompt payload exceeds 1 MiB")
                given = json.loads(data)
                if not isinstance(given, dict):
                    raise ValueError("the batched prompt payload must be an object")
            else:
                prompt = os.environ.get("STUDIO_IMAGE_PROMPT")
                if not prompt and args.qualify:
                    prompt = "A sunlit empty corridor, a closed oak door, wide cinematic storyboard frame."
                given = {"prompt": prompt}
            given["seed"] = args.seed
            if args.count is not None:
                given["count"] = args.count
            params = parameters(given)
            if args.output is None:
                raise ValueError("an output folder is required")
            result = render(args.output, params, args.qualify)
        else:
            result = {"ok": True, "scope": "checkpoint verified; no inference"}
    except (ImportError, OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
