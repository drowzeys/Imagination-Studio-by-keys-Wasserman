"""Optional Stable Audio 3 Small SFX on isolated, supervised native Windows CUDA."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from sound_seed import seed_of

MODEL = "stable-audio-3-small-sfx"
TITLE = "Stable Audio 3 Small SFX"
SOURCE_REVISION = "790793b89ddceea612be9ce42ff6830d18f6fa57"
PACK = Path(__file__).resolve().parents[1]
RECIPE = {"steps": 8, "cfg": 1.0}
SECONDS = (1, 5)
GPU_MB, PROCESS_MB, JOB_MB, RESERVE_MB = 12288, 12288, 16384, 2048
HOST_RESERVE_MB = 2048
TIMEOUT_S = 600
CONTRACT = 2
SETUP = r"powershell -ExecutionPolicy Bypass -File scripts\setup-windows-audio3.ps1"
LICENCE = "Stability AI Community License; free under US$1 million yearly revenue. T5Gemma: Gemma Terms of Use."


def selected(params: dict) -> bool:
    return params.get("sound_model") == MODEL


def runtime() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio" / "audio3-gpu-venv"


def python() -> Path:
    return runtime() / "Scripts" / "python.exe"


def models() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio" / "models" / MODEL


@lru_cache(maxsize=64)
def digest(path: Path, size: int, mtime: int) -> str:
    with path.open("rb") as handle:
        value = hashlib.file_digest(handle, "sha256").hexdigest()
    if (path.stat().st_size, path.stat().st_mtime_ns) != (size, mtime):
        raise RuntimeError("An audio model file changed during verification.")
    return value


def inventory() -> dict:
    manifest = json.loads((models() / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("source_revision") != SOURCE_REVISION or not manifest.get("files"):
        raise RuntimeError("Stable Audio 3's model manifest is missing or incompatible; rerun setup.")
    for name, row in manifest["files"].items():
        path = (models() / name).resolve()
        if not path.is_relative_to(models().resolve()):
            raise ValueError("An audio model manifest path is outside its model folder.")
        stat = path.stat()
        if stat.st_size != row["size"] or (stat.st_mtime_ns != row.get("mtime_ns")
                and digest(path, stat.st_size, stat.st_mtime_ns) != row["sha256"]):
            raise RuntimeError("Stable Audio 3 has an incomplete or changed model file; rerun setup.")
    return manifest


def upstream_sources() -> dict:
    root = runtime() / "Lib" / "site-packages" / "stable_audio_3"
    paths = sorted(root.rglob("*.py"))
    if not paths:
        raise RuntimeError("The Stable Audio 3 runtime source is missing.")
    return {path.relative_to(root).as_posix(): digest(path, path.stat().st_size, path.stat().st_mtime_ns) for path in paths}


def prerequisites() -> str:
    if sys.platform != "win32":
        return "Stable Audio 3 Small SFX currently has a native Windows CUDA port only; other platforms are not qualified."
    if not python().is_file() or not (models() / "manifest.json").is_file():
        return "Install Stable Audio 3 Small SFX separately: " + SETUP
    return ""


def fingerprint() -> dict:
    import windows_features as features
    from windows_memory import dedicated_capacity, host_memory, physical_memory_mb, single_gpu_uuid
    from windows_qualification import gpu_version_contract
    manifest = inventory()
    needed = ("torch", "torchaudio", "numpy", "transformers", "huggingface-hub", "stable-audio-3", "soundfile", "safetensors")
    versions = {dist.metadata["Name"].lower().replace("_", "-"): dist.version
                for dist in metadata.distributions(path=[str(runtime() / "Lib" / "site-packages")])}
    missing = set(needed) - versions.keys()
    if missing:
        raise RuntimeError("Stable Audio 3 dependencies are missing: " + ", ".join(sorted(missing)))
    device = single_gpu_uuid()
    sources = ("app/windows_audio3.py", "app/sound_seed.py", "app/windows_process.py", "app/windows_memory.py",
               "app/windows_backend_handle.py", "scripts/windows-comfy-check.py")
    return {"contract": CONTRACT, "recipe": RECIPE, "seconds": list(SECONDS),
            "limits_mb": [GPU_MB, PROCESS_MB, JOB_MB, RESERVE_MB],
            "host_reserve_mb": HOST_RESERVE_MB,
            "models": manifest, "upstream": upstream_sources(), "device": hashlib.sha256(device.encode()).hexdigest(),
            "physical_mb": physical_memory_mb(), "host_mb": host_memory()[0],
            "dedicated_mb": dedicated_capacity(device),
            "runtime": gpu_version_contract("3.13", {name: versions[name] for name in needed}),
            "versions": {name: versions[name] for name in needed},
            "sources": {name: features.source_signature((PACK / name).read_text(encoding="utf-8"),
                                                       exclude=("status", "fingerprint", "main")) for name in sources}}


def compatibility(value: dict) -> dict:
    return {key: row for key, row in value.items() if key not in ("sources", "versions")}


def status() -> dict:
    import windows_features as features
    import windows_runtime
    from feature_checks import matches_qualification
    base = {"installed": False, "qualified": False, "available": False, "state": "not_installed"}
    try:
        if why := prerequisites():
            return {**base, "why": why}
        current = fingerprint()
        base.update(installed=True, state="needs_qualification")
        receipt = features.saved(MODEL, "")
        previous = receipt.get("fingerprint")
        qualified = (receipt.get("ok") is True and isinstance(previous, dict)
                     and matches_qualification(previous, current, compatibility(previous), compatibility(current)))
        if not qualified and windows_runtime.mode()["enabled"]:
            return {**base, "why": "Check Stable Audio 3 Small SFX in Machine > Feature checks before using this recipe."}
        return {**base, "available": True, "qualified": qualified,
                "state": "qualified" if qualified else "available_unverified", "why": "",
                "runtime_updated": qualified and previous != current}
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        return {**base, "state": "verification_error", "why": f"Stable Audio 3 verification failed: {error}"}


def parameters(params: dict) -> dict:
    out = {**params, "seed": seed_of(params)}
    if not selected(out):
        raise ValueError("Choose Stable Audio 3 Small SFX for this runtime.")
    if set(out) - {"sound_model", "prompt", "negative", "seconds", "steps", "cfg", "seed"}:
        raise ValueError("Stable Audio 3 takes a prompt, seconds, seed and its fixed eight-pass recipe only.")
    prompt = out.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 600:
        raise ValueError("Say what is heard in at most 600 characters.")
    if out.get("negative", "") != "":
        raise ValueError("Small SFX uses CFG 1; negative prompts are not supported by this recipe.")
    seconds = out.get("seconds", 5)
    if type(seconds) not in (int, float) or not SECONDS[0] <= seconds <= SECONDS[1]:
        raise ValueError("Stable Audio 3's Windows recipe is 1 to 5 seconds.")
    for key, value in RECIPE.items():
        if type(out.get(key, value)) not in (int, float) or out.get(key, value) != value:
            raise ValueError("Stable Audio 3 Small SFX uses eight passes and CFG 1.")
    return {**out, "prompt": prompt, "negative": "", "seconds": seconds, **RECIPE}


def check_room() -> None:
    from windows_memory import WddmMemory, WindowsSignals, host_memory, single_gpu_uuid
    _, free = host_memory()
    with WindowsSignals() as signals:
        used, limit, _, low = signals.read()
    configured = os.environ.get("SPARK_MEMORY_FLOOR_MB", "").strip()
    if configured and (not configured.isdigit() or int(configured) <= 0):
        raise ValueError("SPARK_MEMORY_FLOOR_MB must be a positive integer.")
    host_floor = max(HOST_RESERVE_MB, int(configured or 0))
    if low or free < host_floor:
        raise RuntimeError(f"Stable Audio 3 host reserve policy requires {host_floor} MiB free "
                           f"(available {free} MiB, Windows low-memory signal: {low}); free memory and retry.")
    if limit - used < JOB_MB + RESERVE_MB:
        raise RuntimeError(f"Stable Audio 3's {JOB_MB} MiB job cap plus {RESERVE_MB} MiB reserve "
                           f"requires {JOB_MB + RESERVE_MB} MiB commit headroom "
                           f"(available {limit - used} MiB); free memory and retry.")
    _, dedicated = WddmMemory(single_gpu_uuid()).read()
    if dedicated < GPU_MB + RESERVE_MB:
        raise RuntimeError("Stable Audio 3 needs 14 GiB free dedicated GPU memory including its reserve.")


def preflight() -> None:
    found = status()
    if not found["available"]:
        raise RuntimeError(found["why"])
    check_room()


def memory_pressure(snapshot, previous=None) -> str:
    from windows_memory import pressure
    reason = pressure(snapshot, os.environ.get("SPARK_MEMORY_FLOOR_MB", ""), previous)
    if reason:
        return reason
    if snapshot.host_free_mb < HOST_RESERVE_MB:
        return f"Stable Audio 3 host memory is below its {HOST_RESERVE_MB} MiB reserve."
    if snapshot.host_commit_limit_mb - snapshot.host_commit_used_mb < RESERVE_MB:
        return "Stable Audio 3 commit headroom is below its 2 GiB reserve."
    return ""


def build(params: dict, home: Path, job_id: str) -> tuple[list[str], dict, list[str]]:
    params = parameters(params)
    preflight()
    if job_id == "check":
        return [], {}, []
    output = home / "sound" / "sfx" / job_id
    output.mkdir(parents=True, exist_ok=False)
    plan = {**params, "output": str(output), "home": str(home), "id": job_id}
    path = output / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return [sys.executable, "-X", "utf8", str(Path(__file__)), "--render", "--plan", str(path)], {}, [str(output / "sound.wav")]


def check_audio(path: Path, seconds: float) -> dict:
    import numpy as np
    import soundfile as sf
    audio, rate = sf.read(path, always_2d=True)
    if (rate != 44100 or audio.shape[1] != 2 or abs(len(audio) - seconds * rate) > 1
            or not np.isfinite(audio).all()):
        raise RuntimeError("Stable Audio 3 did not produce the requested finite stereo 44.1 kHz WAV.")
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(audio ** 2)))
    if rms < 1e-6 or peak > 10 ** (-1 / 20):
        raise RuntimeError("Stable Audio 3 produced silence or exceeded -1 dBFS.")
    return {"rate": rate, "seconds": seconds, "channels": 2, "peak": peak, "rms": rms}


def worker(plan: dict) -> None:
    from windows_backend_handle import identity
    output = Path(plan["output"])
    temporary = output / "identity.tmp"
    temporary.write_text(json.dumps(identity()), encoding="utf-8")
    temporary.replace(output / "identity.json")
    deadline = time.monotonic() + 35
    while not (output / "admitted").is_file():
        if time.monotonic() > deadline:
            raise RuntimeError("Stable Audio 3 did not receive owned-handle memory admission.")
        time.sleep(.05)
    import torch
    import soundfile as sf
    from stable_audio_3 import StableAudioModel
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError("Stable Audio 3 requires exactly its selected native CUDA GPU; no CPU fallback.")
    total = torch.cuda.get_device_properties(0).total_memory
    if total < (GPU_MB + RESERVE_MB) << 20:
        raise RuntimeError("The selected CUDA device cannot hold the bounded Small SFX recipe.")
    torch.cuda.set_per_process_memory_fraction((GPU_MB << 20) / total, device=0)
    model = StableAudioModel.from_pretrained("small-sfx", device="cuda", model_half=True)
    print("[sound] Stable Audio 3 Small SFX: eight passes, CFG 1, native CUDA", flush=True)
    audio = model.generate(prompt=plan["prompt"], duration=plan["seconds"], steps=8, cfg_scale=1.0,
                           seed=plan["seed"], batch_size=1, sampler_type="pingpong",
                           truncate_output_to_duration=True)
    if audio.ndim != 3 or audio.shape[0] != 1 or audio.shape[1] != 2:
        raise RuntimeError("Stable Audio 3 returned an unexpected waveform shape.")
    data = audio[0].float().cpu().numpy().T
    import numpy as np
    if not np.isfinite(data).all():
        raise RuntimeError("Stable Audio 3 returned non-finite audio.")
    peak = float(np.max(np.abs(data)))
    if peak > .891:
        data *= .891 / peak
    sf.write(output / "sound.wav", data, int(model.model_config["sample_rate"]), subtype="PCM_16")
    result = check_audio(output / "sound.wav", plan["seconds"])
    temporary = output / "audio.tmp"
    temporary.write_text(json.dumps(result), encoding="utf-8")
    temporary.replace(output / "audio.json")
    deadline = time.monotonic() + 10
    while not (output / "finished").is_file():
        if time.monotonic() > deadline:
            raise RuntimeError("Stable Audio 3 supervisor did not acknowledge the finished audio.")
        time.sleep(.05)


def render(plan: dict, qualification: bool = False) -> dict:
    import windows_storyboard as renderer
    from windows_memory import single_gpu_uuid
    from windows_process import OwnedJob
    if qualification:
        check_room()
    else:
        preflight()
    inventory()
    output = Path(plan["output"])
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("identity.json", "admitted", "finished", "sound.wav", "audio.json")):
        raise RuntimeError("Stable Audio 3 needs a fresh output folder.")
    path = output / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    device = single_gpu_uuid()
    owned = OwnedJob(process_commit_mb=PROCESS_MB, job_commit_mb=JOB_MB)
    monitor = None
    started = time.monotonic()
    try:
        with (output / "render.log").open("w+", encoding="utf-8") as log:
            monitor = renderer.tools().checker().MemoryWatchdog(owned, 0, log, wddm_device_uuid=device,
                                                                pressure_policy=memory_pressure)
            monitor.wait_ready()
            env = {**os.environ, "HF_HOME": str(models() / "hub"), "HF_HUB_OFFLINE": "1",
                   "TRANSFORMERS_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": device, "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
                   "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            process = owned.spawn([str(python()), "-X", "utf8", str(Path(__file__)), "--worker", "--plan", str(path)],
                                  env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                  creationflags=0x08000000)
            identity_path = output / "identity.json"
            while not identity_path.is_file():
                monitor.check()
                if process.poll() is not None or time.monotonic() - started > 30:
                    log.flush()
                    log.seek(0)
                    detail = log.read()[-2000:].strip()
                    raise RuntimeError("Stable Audio 3 worker failed to report its owned identity."
                                       + (" Worker log: " + detail if detail else " No worker output was recorded."))
                time.sleep(.05)
            monitor.start_backend_handle(process, json.loads(identity_path.read_text(encoding="utf-8")))
            (output / "admitted").touch(exist_ok=False)
            while not (output / "audio.json").is_file():
                monitor.check()
                if process.poll() is not None:
                    log.flush()
                    log.seek(0)
                    raise RuntimeError("Stable Audio 3 failed: " + log.read()[-2000:])
                if time.monotonic() - started > TIMEOUT_S:
                    raise RuntimeError("Stable Audio 3 exceeded its 600-second deadline.")
                time.sleep(.1)
            monitor.begin_backend_shutdown()
            monitor.check()
            (output / "finished").touch(exist_ok=False)
            process.wait(timeout=10)
            if process.returncode:
                log.flush()
                log.seek(0)
                raise RuntimeError("Stable Audio 3 failed: " + log.read()[-2000:])
            audio = json.loads((output / "audio.json").read_text(encoding="utf-8"))
            result = {"case": "Small SFX " + str(plan["seconds"]) + "s", "rendered": True, "audio": audio,
                      "elapsed_s": time.monotonic() - started, "measurements": monitor.metrics(),
                      "peak_job_commit_mb": owned.commitment()["peak_job_commit_bytes"] / 2 ** 20}
    finally:
        if monitor is not None:
            monitor.begin_backend_shutdown()
            monitor.stopping.set()
        try:
            owned.close()
        finally:
            if monitor is not None:
                monitor.finish(check_result=False)
        for name in ("identity.json", "admitted", "finished"):
            (output / name).unlink(missing_ok=True)
    if not qualification:
        home = Path(plan["home"])
        take = {"id": plan["id"], "kind": "sfx", "model": TITLE, "sound_model": MODEL,
                "prompt": plan["prompt"], "negative": "", "seed": plan["seed"], **RECIPE,
                "seconds": audio["seconds"], "rate": audio["rate"],
                "path": (output / "sound.wav").relative_to(home).as_posix(),
                "created": time.time(), "elapsed": result["elapsed_s"], "licence": LICENCE}
        (output / "sound.take.json").write_text(json.dumps(take), encoding="utf-8")
    return result


def qualify(output: Path, job_id: str) -> dict:
    import windows_features as features
    attempt = {"feature": MODEL, "job_id": job_id, "ok": False, "checked_at": datetime.now(timezone.utc).isoformat()}
    features.save_attempt(MODEL, attempt)
    try:
        if why := prerequisites():
            raise RuntimeError(why)
        if output.exists():
            raise RuntimeError("Choose a fresh audio qualification output folder.")
        before = fingerprint()
        results = []
        for seconds in (1, 5):
            plan = parameters({"sound_model": MODEL, "prompt": "A heavy wooden door slams once with a short echo.",
                               "seconds": seconds, "seed": 7})
            results.append(render({**plan, "output": str(output / f"case-{seconds}")}, qualification=True))
        if before != fingerprint():
            raise RuntimeError("Stable Audio 3 dependencies changed during qualification.")
        receipt = {**attempt, "ok": True, "fingerprint": before, "results": results}
        features.save_pass(MODEL, receipt)
        features.save_attempt(MODEL, receipt)
        return receipt
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        features.save_attempt(MODEL, {**attempt, "error": str(error)})
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--worker", action="store_true")
    modes.add_argument("--render", action="store_true")
    modes.add_argument("--qualify", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--job-id", default="")
    args = parser.parse_args()
    try:
        if args.qualify:
            if args.output is None or args.report is None:
                parser.error("--qualify requires --output and --report")
            receipt = qualify(args.output, args.job_id)
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(receipt), encoding="utf-8")
            print("[qualification] Stable Audio 3's bounded CUDA recipe passed; sound quality was not scored.", flush=True)
        else:
            if args.plan is None:
                parser.error("--plan is required")
            plan = json.loads(args.plan.read_text(encoding="utf-8"))
            plan = {**plan, **parameters({key: plan[key] for key in
                    ("sound_model", "prompt", "negative", "seconds", "steps", "cfg", "seed") if key in plan})}
            worker(plan) if args.worker else render(plan)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"[sound] {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
