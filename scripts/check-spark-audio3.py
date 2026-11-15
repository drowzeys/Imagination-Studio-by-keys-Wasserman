"""Explicit Small SFX worker check; capped ComfyUI, existing guard, and a second 25 GB watchdog."""
import argparse
import array
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "spark")]
import local_gpus
import memory_guard
import spark_audio3
import spark_generate as renderer
from windows_audio3 import GPU_MB, JOB_MB, TIMEOUT_S

PROMPT = "A single heavy wooden door slams shut in a quiet room, with a short room echo."
WATCH_FLOOR_MB = 25000


def available_mb() -> int:
    found = memory_guard.meminfo().get("MemAvailable")
    if type(found) is not int or found <= 0:
        raise RuntimeError("The worker check needs valid live MemAvailable counters.")
    return found


def preflight() -> str:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("This check runs on a Linux DGX Spark worker, not Windows or Mac.")
    if Path("/proc/sys/vm/panic_on_oom").read_text().strip() != "0":
        raise RuntimeError("Do not run this check on a worker with vm.panic_on_oom enabled.")
    if os.environ.get("STUDIO_GPUS") or os.environ.get("SPARK_GOVERNOR"):
        raise RuntimeError("Use an idle single-card worker without a paired-GPU or governor configuration; neither is disabled by this check.")
    if os.environ.get("SPARK_COMFY_ARGS", "").strip() not in ("", "--use-ck-attention"):
        raise RuntimeError("The first worker check uses the default ComfyUI recipe, not custom backend flags.")
    if why := spark_audio3.prerequisites():
        raise RuntimeError(why)
    result = subprocess.run(["nvidia-smi", "--query-gpu=uuid,name", "--format=csv,noheader"],
                            capture_output=True, text=True, timeout=10, check=True)
    devices = [line.split(",", 1) for line in result.stdout.splitlines() if line.strip().startswith("GPU-")]
    if len(devices) != 1 or len(devices[0]) != 2 or "GB10" not in devices[0][1].upper():
        raise RuntimeError("This first check is scoped to one GB10 DGX Spark; other cards need their own check.")
    running = subprocess.run(["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10, check=True)
    if running.stdout.strip():
        raise RuntimeError("The worker GPU is busy: stop its model servers yourself before checking. This check never stops existing servers.")
    floor = max(WATCH_FLOOR_MB, memory_guard.floor_mb())
    if available_mb() < floor + JOB_MB:
        raise RuntimeError("The worker needs its 25 GB (or larger configured) reserve plus 16 GiB trial headroom.")
    return devices[0][0].strip()


def validate_audio(path: Path, seconds: int) -> dict:
    with wave.open(str(path), "rb") as handle:
        rate, channels, width, frames = handle.getframerate(), handle.getnchannels(), handle.getsampwidth(), handle.getnframes()
        if (rate, channels, width) != (44100, 2, 2) or frames != seconds * 44100:
            raise RuntimeError("The check must produce exact-duration 44.1 kHz stereo PCM16 audio.")
        data = array.array("h", handle.readframes(frames))
    if sys.byteorder != "little":
        data.byteswap()
    peak = max((abs(value) for value in data), default=0) / 32768
    if not peak or peak > 10 ** (-1 / 20) + 1 / 32768:
        raise RuntimeError("The audio is silent or exceeds the -1 dBFS output bound.")
    return {"sample_rate": rate, "channels": channels, "frames": frames,
            "peak_dbfs": round(20 * math.log10(peak), 3),
            "rms": math.sqrt(sum(value * value for value in data) / len(data)) / 32768}


class Watchdog:
    def __init__(self, process, owner: str, baseline: int):
        self.process, self.owner, self.baseline = process, owner, baseline
        self.start = local_gpus.process_identity(process.pid)
        self.floor = max(WATCH_FLOOR_MB, memory_guard.floor_mb())
        self.minimum, self.samples, self.failure = baseline, 0, ""
        self.deadline = time.monotonic() + TIMEOUT_S
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self.watch, name="audio3-worker-watchdog")

    def drain(self) -> None:
        family = local_gpus.marked_processes("STUDIO_AUDIO3_CHECK_OWNER", self.owner)
        if self.start and local_gpus.process_identity(self.process.pid) == self.start:
            family[self.process.pid] = self.start
        if family and not local_gpus.drain_processes(family):
            raise RuntimeError("The owned check processes did not drain; the private ownership record is retained.")
        self.process.wait(timeout=10)

    def watch(self) -> None:
        try:
            while not self.finished.is_set():
                current = available_mb()
                self.samples += 1
                self.minimum = min(self.minimum, current)
                if current < self.floor or self.baseline - current > JOB_MB:
                    raise RuntimeError("The second watchdog reached its reserve or 16 GiB system-memory-growth bound.")
                if time.monotonic() >= self.deadline:
                    raise RuntimeError("The worker check reached its 600-second deadline.")
                if self.process.poll() is not None:
                    raise RuntimeError("The owned ComfyUI stopped before the check finished.")
                self.finished.wait(0.05)
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
            self.failure = str(error)
            try:
                self.drain()
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as stop_error:
                self.failure += " " + str(stop_error)

    def require(self) -> None:
        if self.failure:
            raise RuntimeError(self.failure)
        if not self.thread.is_alive() or self.process.poll() is not None:
            raise RuntimeError("The worker check lost its live supervisor or ComfyUI.")


@contextmanager
def server(log: Path, device: str):
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    owner = uuid.uuid4().hex
    state_dir = local_gpus.private_dir(Path.home() / ".cache/studio-audio3-checks")
    if state_dir.resolve().is_relative_to(ROOT):
        raise RuntimeError("Private check ownership must stay outside the repository.")
    receipt = state_dir / (owner + ".json")
    baseline = available_mb()
    environment = {**os.environ, "CUDA_VISIBLE_DEVICES": device, "SPARK_COMFY_PORT": str(port),
                   "STUDIO_AUDIO3_CALIBRATION": "1", "STUDIO_AUDIO3_CHECK_OWNER": owner,
                   "STUDIO_MEMORY_GUARD": "strict", "PYTHONUNBUFFERED": "1"}
    with log.open("w", encoding="utf-8") as handle:
        process = subprocess.Popen(["bash", str(ROOT / "spark/comfy.sh"), "run"], env=environment,
                                   stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
        guard = Watchdog(process, owner, baseline)
        try:
            if not guard.start:
                raise RuntimeError("The check could not establish its owned Linux process identity.")
            with receipt.open("x", encoding="utf-8") as identity:
                os.chmod(receipt, 0o600)
                json.dump({"pid": process.pid, "start": guard.start, "owner": owner}, identity)
            guard.thread.start()
            previous_url, previous_call = renderer.URL, renderer.call
            renderer.URL = f"http://127.0.0.1:{port}"
            try:
                until = time.monotonic() + 120
                while not renderer.up():
                    guard.require()
                    if time.monotonic() >= until:
                        raise RuntimeError("The owned ComfyUI did not become ready within 120 seconds; inspect the check log.")
                    time.sleep(0.1)
                guard.require()
                local_gpus.check_owned_listener(port, "STUDIO_AUDIO3_CHECK_OWNER", owner, require_listener=True)
                def owned_call(path, data=None):
                    guard.require()
                    local_gpus.check_owned_listener(port, "STUDIO_AUDIO3_CHECK_OWNER", owner, require_listener=True)
                    return previous_call(path, data)
                renderer.call = owned_call
                yield guard
            finally:
                renderer.URL, renderer.call = previous_url, previous_call
        finally:
            guard.finished.set()
            if guard.thread.ident is not None:
                guard.thread.join(timeout=10)
            guard.drain()
            if guard.thread.is_alive():
                raise RuntimeError("The second watchdog did not stop.")
            receipt.unlink(missing_ok=True)


def check_case(folder: Path, device: str, seconds: int, prompt: str) -> dict:
    with server(folder / f"case-{seconds}.log", device) as guard:
        graph = renderer.audio3_workflow(prompt, seconds, 1, "studio-audio3-check/" + uuid.uuid4().hex)
        guard.require()
        item, elapsed = renderer.render(graph, "12")
        guard.require()
        with tempfile.TemporaryDirectory(prefix="studio-audio3-track-") as scratch:
            track = Path(scratch) / "sound.flac"
            renderer.fetch(item, track)
            guard.require()
            target = folder / f"case-{seconds}.wav"
            renderer.music_finish(track, target, "sound effect", 2)
        output_root = (spark_audio3.prefix() / "ComfyUI/output").resolve()
        made = (output_root / item.get("subfolder", "") / item["filename"]).resolve()
        if not made.is_relative_to(output_root):
            raise RuntimeError("ComfyUI returned an output outside its output folder.")
        made.unlink(missing_ok=True)
        audio = validate_audio(target, seconds)
        guard.require()
        return {"seconds": seconds, "seed": 1, "elapsed_s": round(elapsed, 3), "audio": audio,
                "baseline_available_mb": guard.baseline, "minimum_available_mb": guard.minimum,
                "maximum_system_available_drop_mb": max(0, guard.baseline - guard.minimum),
                "watchdog_samples": guard.samples, "watchdog_floor_mb": guard.floor}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help="confirm this is an idle development worker")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/spark-audio3-check")
    parser.add_argument("--prompt", default=PROMPT)
    args = parser.parse_args()
    if not args.worker:
        parser.error("--worker is required; this is not a production-app qualification")
    report = {"schema": 1, "model": spark_audio3.MODEL, "engine": "comfyui", "outcome": "failed",
              "recipe": {"steps": 8, "cfg": 1, "sampler": "lcm", "scheduler": "simple", "seconds": [1, 5]},
              "bounds": {"torch_allocator_mb": GPU_MB, "system_available_growth_mb": JOB_MB,
                         "extra_watchdog_floor_mb": WATCH_FLOOR_MB, "owned_comfy_deadline_s": TIMEOUT_S},
              "note": "An explicit worker check, not local qualification or a shared measurement profile. "
                      "System-wide MemAvailable changes are not CUDA allocator peaks.", "cases": []}
    owned_output = False
    try:
        spark_audio3.parameters({"sound_model": spark_audio3.MODEL, "prompt": args.prompt})
        device = preflight()
        if args.output.exists() and any(args.output.iterdir()):
            raise RuntimeError("Use a new empty output folder; existing check files are never overwritten.")
        args.output.mkdir(parents=True, exist_ok=True)
        owned_output = True
        for seconds in (1, 5):
            print(f"Checking {seconds} s, eight passes, CFG 1, seed 1; guarded and capped.", flush=True)
            report["cases"].append(check_case(args.output, device, seconds, args.prompt))
        report["outcome"] = "passed"
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, SystemExit) as error:
        report["failure_type"] = type(error).__name__
        print(f"Spark Small SFX check failed: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        if owned_output:
            (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Both WAVs passed execution checks. Listen to them for prompt matching; no Studio qualification or shared profile was written.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
