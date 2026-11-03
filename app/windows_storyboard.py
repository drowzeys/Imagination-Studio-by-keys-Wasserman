"""Measured, narrowly bounded SD-Turbo stills on native Windows ComfyUI."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid

from windows_brain import fetch, verify_file

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
    paths = ["app/windows_storyboard.py", "app/windows_brain.py", "app/windows_memory.py", "app/windows_process.py",
             "scripts/windows-comfy-check.py", "scripts/windows-sfx-check.py",
             "scripts/windows-calibration-node.py", "scripts/windows-comfy-calibration-launcher.py"]
    return {name: hashlib.sha256((PACK / name).read_bytes()).hexdigest() for name in paths}


def status() -> dict:
    root = folder()
    try:
        receipt = json.loads((root / "qualification.json").read_text(encoding="utf-8"))
        mtimes = {name: (root / name).stat().st_mtime_ns for name in FILES}
        valid = (receipt.get("weight_manifest_sha256") == MANIFEST_SHA256 and receipt.get("revision") == REVISION
                 and receipt.get("width") == SIDE and receipt.get("height") == SIDE
                 and receipt.get("tool_sha256") == tool_hashes()
                 and receipt.get("model_mtime_ns") == mtimes
                 and all((root / name).stat().st_size == info[0] for name, info in FILES.items())
                 and receipt.get("result", {}).get("ok") is True
                 and isinstance(receipt.get("hardware"), dict)
                 and all(type(receipt["hardware"].get(key)) is int and receipt["hardware"][key] > 0
                         for key in ("physical_total_mb", "host_total_mb", "gpu_dedicated_total_mb")))
    except FileNotFoundError:
        return {"available": False, "why": "Run setup-windows-storyboard.ps1 and check-windows-storyboard.ps1 first."}
    except (OSError, ValueError, TypeError, AttributeError) as error:
        return {"available": False, "why": f"The Windows storyboard qualification is unreadable: {type(error).__name__}."}
    if not valid:
        return {"available": False, "why": "The model or render code changed; repeat check-windows-storyboard.ps1."}
    return {"available": True, "why": "", "width": SIDE, "height": SIDE, "max_count": MAX_COUNT,
            "model": MODEL, "hardware": receipt["hardware"]}


def parameters(params: dict) -> dict:
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    allowed = {"prompt", "count", "seed", "width", "height", "image_model", "suite_import", "suite_shot"}
    extra = set(params) - allowed
    if extra:
        raise ValueError("unsupported Windows still settings: " + ", ".join(sorted(extra)))
    prompt = params.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000:
        raise ValueError("a still needs a prompt of 1 to 20,000 characters")
    out = {"prompt": prompt, "count": params.get("count", 1), "seed": params.get("seed", 1),
           "width": params.get("width", SIDE), "height": params.get("height", SIDE),
           "image_model": params.get("image_model", MODEL)}
    if out["image_model"] != MODEL:
        raise ValueError("only SD-Turbo is qualified for Windows stills")
    if any(type(out[key]) is not int for key in ("count", "seed", "width", "height")):
        raise ValueError("count, seed, width and height must be whole numbers")
    if not 1 <= out["count"] <= MAX_COUNT or not 0 <= out["seed"] <= 2 ** 32 - MAX_COUNT:
        raise ValueError("Windows stills take 1 to 6 sequential images and a seed from 0 to 4294967290")
    if (out["width"], out["height"]) != (SIDE, SIDE):
        raise ValueError("the measured Windows storyboard route is 512x512 only; larger canvases are not enabled")
    for key in ("suite_import", "suite_shot"):
        if key in params:
            if not isinstance(params[key], str) or not params[key] or len(params[key]) > 64:
                raise ValueError(f"{key} must be an import/shot id")
            out[key] = params[key]
    if ("suite_import" in out) != ("suite_shot" in out):
        raise ValueError("suite_import and suite_shot are supplied together")
    return out


def preflight() -> None:
    from windows_memory import DedicatedMemory, WindowsSignals, host_memory, physical_memory_mb
    found = status()
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
    if limit - used < JOB_LIMIT_MB + 2048:
        raise RuntimeError("the Windows image job cap does not fit commit headroom with its reserve")
    device = subprocess.run(["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader", "--id=0"],
                            capture_output=True, text=True, timeout=5, check=True).stdout.strip()
    with DedicatedMemory(device) as memory:
        dedicated, free = memory.read()
    if dedicated != hardware["gpu_dedicated_total_mb"]:
        raise RuntimeError("the dedicated GPU partition changed; repeat storyboard qualification")
    if free < GPU_LIMIT_MB + 2048:
        raise RuntimeError("the Windows image allocation cap does not fit dedicated GPU headroom with its reserve")


def build(params: dict, home: Path, job_id: str) -> tuple[list[str], dict, list[str]]:
    params = parameters(params)
    target = home / "scouts" / job_id
    python = Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio" / "gpu-venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise RuntimeError("the separate Windows GPU environment is missing")
    command = [str(python), "-X", "utf8", str(Path(__file__).resolve()), "--render", "--output", str(target),
               "--count", str(params["count"]), "--seed", str(params["seed"])]
    outputs = [str(target / f"scout_s{params['seed'] + i}.png") for i in range(params["count"])]
    return command, {"STUDIO_IMAGE_PROMPT": params["prompt"]}, outputs + [str(target / "sheet.jpg")]


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


def render(output: Path, prompt: str, count: int, seed: int, qualify: bool) -> dict:
    from PIL import Image, ImageDraw, ImageStat
    root = folder()
    verify_weights(root)
    if qualify:
        (root / "qualification.json").unlink(missing_ok=True)
    if not qualify:
        preflight()
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
        for index in range(count):
            graph = workflow(prompt, seed + index)
            reply = helper.post_prompt(base, graph)
            if reply.get("error") or reply.get("node_errors"):
                raise RuntimeError("ComfyUI rejected the Windows image workflow")
            prompt_id = str(uuid.UUID(reply["prompt_id"]))
            deadline = time.monotonic() + 120
            while True:
                monitor.check()
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
            image.save(output / f"scout_s{seed + index}.png")
            images.append(image)
            print(f"[tensorfold] step {index + 1}/{count}", flush=True)
        columns = min(2 if count <= 4 else 3, count)
        sheet = Image.new("RGB", (columns * SIDE, ((count + columns - 1) // columns) * SIDE), "black")
        for index, image in enumerate(images):
            tile = image.copy()
            draw = ImageDraw.Draw(tile)
            draw.rectangle((0, 0, 110, 26), fill="black")
            draw.text((8, 6), f"seed {seed + index}", fill="white")
            sheet.paste(tile, ((index % columns) * SIDE, (index // columns) * SIDE))
        sheet.save(output / "sheet.jpg", quality=90)
        return {"model": MODEL, "count": count, "width": SIDE, "height": SIDE,
                "request_to_files_seconds": time.monotonic() - started, "allocator_receipts": receipts}

    case = check.CalibrationCase(prepare, exercise)
    source = Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio" / "ComfyUI"
    result = check.run_check(source, 120, 0, guarded=True, calibration=case)
    if qualify:
        initial = result["memory_guard"]["initial"]
        qualification = {"weight_manifest_sha256": MANIFEST_SHA256, "revision": REVISION, "width": SIDE, "height": SIDE,
                         "model_mtime_ns": {name: (root / name).stat().st_mtime_ns for name in FILES},
                         "tool_sha256": tool_hashes(),
                         "hardware": {key: initial[key] for key in
                                      ("physical_total_mb", "host_total_mb", "gpu_dedicated_total_mb")},
                         "result": result}
        temporary = root / "qualification.json.part"
        temporary.write_text(json.dumps(qualification, indent=2) + "\n", encoding="utf-8")
        temporary.replace(root / "qualification.json")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--qualify", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--seed", type=int, default=1)
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
            prompt = os.environ.get("STUDIO_IMAGE_PROMPT")
            if not prompt and args.qualify:
                prompt = "A sunlit empty corridor, a closed oak door, wide cinematic storyboard frame."
            parameters({"prompt": prompt, "count": args.count, "seed": args.seed})
            if args.output is None:
                raise ValueError("an output folder is required")
            result = render(args.output, prompt, args.count, args.seed, args.qualify)
        else:
            result = {"ok": True, "scope": "checkpoint verified; no inference"}
    except (ImportError, OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
