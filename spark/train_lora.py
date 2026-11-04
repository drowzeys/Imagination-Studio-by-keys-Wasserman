#!/usr/bin/env python3
"""One LoRA run on a DGX Spark (or natively on Windows with the CUDA tier, an RTX Spark N1X), as a job of the Studio's queue (kind `train`, app/train.py): the dataset's latents and
text cached, the LoRA trained with kohya-ss's musubi-tuner, and the result written into the Studio's LoRA hub.

    python3 spark/train_lora.py --home ~/ImaginationStudio --dataset ~/ImaginationStudio/train/<id> --lora <id> \
        --name NAME --trigger WORD --kind look|character --steps 1000 --rank 16 --lr 1e-4 --size standard --save-every 100
    ... --resume       go on from the last checkpoint of this run
    ... --print        print the commands it would run, as JSON, and stop

It runs with the Studio's own Python (it writes the hub through app/loras.py) and starts musubi-tuner from its own
environment, $PREFIX/train (spark/setup-train.sh). On the way it:
  - takes the GPU lock (STUDIO_GPU_LOCK) for the whole run, so nothing else of the Studio's renders beside it;
  - inside a machine-wide workload governor (SPARK_GOVERNOR, spark/workload.py) starts only inside a reservation of its
    own (SPARK_GOVERNOR_TRAIN_GIB, 70), with the governor's telemetry beside it;
  - admits the run only when the machine has what it holds (about 55 GB, TRAIN_NEED_GB) above the memory floor, and
    keeps a memory guard beside every step of it (spark/memory_guard.py's verdict, four times a second): under the
    floor, or falling there within a second, the step is stopped;
  - prints `[tensorfold] train n/m` while it trains (the queue's progress), and saves a checkpoint every --save-every
    steps; a run that stops is resumed from the last one (musubi-tuner counts a resumed part from 0, so each part after
    the first is named by the step it starts from: app/train.py segment_name);
  - converts the final file into the layout ComfyUI reads (musubi-tuner's convert_lora.py --target other) as
    HOME/loras/<id>.safetensors and adds its record to the hub.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACK = HERE.parent
sys.path.insert(0, str(PACK / "app"))
sys.path.insert(0, str(HERE))
import lora_layout  # noqa: E402
import train  # noqa: E402

TQDM = re.compile(rb"steps:\s*\d+%\|[^|\r\n]*\|\s*(\d+)/(\d+)")
NEED_GB = float(os.environ.get("TRAIN_NEED_GB") or train.NEED_GB)
LOCK = Path(os.environ.get("STUDIO_GPU_LOCK", Path.home() / "studio-gpu.lock.d"))
WINDOWS = sys.platform == "win32"  # natively on Windows (the CUDA tier): job objects and Windows' memory signals
CURRENT: dict = {"process": None, "stop": False}


def say(text: str) -> None:
    print(text, flush=True)


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--home", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--lora", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--trigger", required=True)
    parser.add_argument("--kind", choices=("look", "character"), default="look")
    parser.add_argument("--steps", type=int, default=train.STEPS[2])
    parser.add_argument("--rank", type=int, default=train.RANKS[2])
    parser.add_argument("--lr", type=float, default=train.RATES[2])
    parser.add_argument("--size", choices=tuple(train.SIZES), default="standard")
    parser.add_argument("--save-every", type=int, default=train.SAVE_EVERY[2])
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print", action="store_true", dest="dry")
    return parser.parse_args(argv)


def plan(args: argparse.Namespace) -> dict:
    """Everything a run needs to know: its folders, where it starts (the last checkpoint), and its commands."""

    folder = Path(args.dataset)
    record = json.loads((folder / "dataset.json").read_text())
    kinds = {one["kind"] for one in record["items"]}
    out = folder / "runs" / args.lora
    toml = out / "dataset.toml"
    have, models = train.tools(), train.files()
    last = train.last_checkpoint(out)
    offset, state = last if last else (0, None)  # a run with checkpoints goes on from the last one
    segment = train.segment_name(args.lora, offset)
    images = ["--one_frame"] if "image" in kinds else []
    still_only = ["--video_only"] if kinds == {"image"} else []  # stills carry no sound to learn
    source = have["musubi"] / "src/musubi_tuner"
    launch = [str(have["accelerate"]), "launch", "--num_processes", "1", "--num_machines", "1", "--dynamo_backend", "no",
              "--num_cpu_threads_per_process", "4", "--mixed_precision", "bf16"]
    commands = {
        "latents": launch + [str(source / "minimax_h3_cache_latents.py"), "--dataset_config", str(toml), "--task", "t2va",
                             "--video_vae", str(models["video_vae"]), "--audio_vae", str(models["audio_vae"]),
                             "--cache_seed", "42", "--skip_existing", "--skip_broken"] + images,
        "text": [str(have["python"]), str(source / "minimax_h3_cache_text_encoder_outputs.py"), "--dataset_config", str(toml),
                 "--task", "t2va", "--text_encoder", str(models["text_encoder"]), "--text_encoder_blocks_to_swap", "50",
                 "--text_cache_dtype", "bf16", "--skip_existing"] + images,
        "train": launch + [str(source / "minimax_h3_train_network.py"), "--dataset_config", str(toml), "--task", "t2va",
                           "--dit", str(models["dit"]), "--convrot_int8", "--base_weights", str(models["adapter"]),
                           "--network_module", "networks.lora_minimax_h3", "--network_dim", str(args.rank),
                           "--network_alpha", str(args.rank), "--sdpa", "--mixed_precision", "bf16", "--save_precision", "bf16",
                           "--gradient_checkpointing", "--optimizer_type", have["optimizer"], "--learning_rate", f"{args.lr:g}",
                           "--lr_scheduler", "constant", "--seed", "1234", "--max_data_loader_n_workers", "2",
                           "--persistent_data_loader_workers", "--output_dir", str(out), "--output_name", segment,
                           "--max_train_steps", str(max(1, args.steps - offset)), "--save_every_n_steps", str(args.save_every),
                           "--save_state", "--save_last_n_steps_state", str(args.save_every * 2),
                           "--save_last_n_steps", str(args.save_every * 5)]
                          + images + still_only + (["--resume", str(state)] if state else []),
    }
    return {"record": record, "kinds": sorted(kinds), "out": out, "toml": toml, "offset": offset, "state": state,
            "segment": segment, "final": out / f"{segment}.safetensors", "commands": commands, "cwd": have["musubi"],
            "python": have["python"], "source": source}


# ---------- memory ----------

class Guard(threading.Thread):
    """Beside one step of the run: the memory guard's verdict on the machine four times a second; when it says stop,
    the step's processes are killed."""

    def __init__(self, process: subprocess.Popen, telemetry=None) -> None:
        super().__init__(daemon=True)
        self.process, self.telemetry, self.why = process, telemetry, ""

    def run(self) -> None:
        if WINDOWS:
            return self.run_windows()
        if not Path("/proc/meminfo").exists():
            return
        import memory_guard

        meter, samples, floor = memory_guard.Meter(), [], memory_guard.floor_mb()
        while self.process.poll() is None:
            now = time.monotonic()
            samples.append((now, meter.read()["available"]))
            while now - samples[0][0] > 1.0:
                samples.pop(0)
            why = memory_guard.verdict(samples, floor)
            if not why and self.telemetry is not None and self.telemetry.failure:
                why = "; ".join(self.telemetry.failure)
            if why:
                self.why = why
                say(f"[tensorfold] memory floor: {why}: stopping the training")
                kill(self.process, signal.SIGKILL)
                return
            time.sleep(0.25)


    def run_windows(self) -> None:
        """The native tier's guard (spark/comfy_win.py's, Windows' commit and low-memory signals and the GPU's
        dedicated memory), its sampler in the step's own job object; the job is ended when it names a reason."""

        import comfy_win
        import windows_cuda

        job = getattr(self.process, "_studio_job", None)
        if job is None:
            return
        try:
            guard = comfy_win.Guard(job, os.environ.get("SPARK_MEMORY_FLOOR_MB", ""), python=windows_cuda.python())
        except (OSError, RuntimeError) as error:
            say(f"[tensorfold] memory guard: the sampler could not start ({error}): the run goes on without it")
            return
        previous = None
        while self.process.poll() is None:
            if guard.latest is not None:
                why = guard.reason(previous)
                previous = guard.latest
                if not why and self.telemetry is not None and self.telemetry.failure:
                    why = "; ".join(self.telemetry.failure)
                if why:
                    self.why = why
                    say(f"[tensorfold] memory floor: {why}: stopping the training")
                    kill(self.process)
                    return
            time.sleep(0.25)


def admit(paths: list[Path]) -> None:
    """The run starts only when the machine has about NEED_GB above the memory floor; the page cache of the model
    files is given back once first if that is what is in the way."""

    if WINDOWS:  # an RTX Spark N1X: the GPU's own pool (nvidia-smi's free memory) is what a run holds
        import memory_guard

        card = memory_guard.device(fresh=True)
        if card and card.get("free_mb"):
            if card["free_mb"] < NEED_GB * 1024:
                raise SystemExit(f"not enough GPU memory to train: {card['free_mb'] / 1024:.0f} GB free, a run holds about "
                                 f"{NEED_GB:g} GB. Stop what else runs on this GPU and resume")
            say(f"[tensorfold] memory: {card['free_mb'] / 1024:.0f} GB of the GPU free, the run holds about {NEED_GB:g} GB")
        return
    if not Path("/proc/meminfo").exists():
        return
    import memory_guard

    floor = memory_guard.floor_mb()
    have = memory_guard.meminfo().get("MemAvailable", 0)
    if have - NEED_GB * 1024 < floor:
        memory_guard.release_cache([str(p) for p in paths])
        have = memory_guard.meminfo().get("MemAvailable", 0)
    if have - NEED_GB * 1024 < floor:
        raise SystemExit(f"not enough memory to train: {have / 1024:.0f} GB available, a run holds about {NEED_GB:g} GB and "
                         f"{floor / 1024:.0f} GB stays free. Stop what else runs on this Spark and resume")
    say(f"[tensorfold] memory: {have / 1024:.0f} GB available, the run holds about {NEED_GB:g} GB, the floor is {floor / 1024:.0f} GB")


# ---------- the GPU lock ----------

def take_lock(lora: str) -> str:
    mine = f"studio-train {lora} {os.getpid()}"
    said = 0.0
    while not CURRENT["stop"]:
        try:
            LOCK.mkdir(parents=False)
            (LOCK / "owner").write_text(mine + "\n")
            return mine
        except FileExistsError:
            try:
                owner = (LOCK / "owner").read_text().strip()
            except OSError:
                owner = ""
            pid = owner.split()[-1] if owner else ""
            gone = False
            if pid.isdigit():
                gone = not alive(int(pid))
            elif time.time() - LOCK.stat().st_mtime > 60:
                gone = True
            if gone:
                shutil.rmtree(LOCK, ignore_errors=True)
                continue
            if time.monotonic() - said > 60:
                say(f"[tensorfold] waiting for the GPU lock ({owner or 'held'})")
                said = time.monotonic()
        time.sleep(2)
    raise SystemExit("cancelled while waiting for the GPU lock")


def alive(pid: int) -> bool:
    """Whether process ``pid`` runs (app/windows_cuda.py's pid_alive: os.kill(pid, 0) is a CTRL_C_EVENT on Windows)."""

    import windows_cuda

    return windows_cuda.pid_alive(pid)


def drop_lock(mine: str) -> None:
    try:
        if (LOCK / "owner").read_text().strip() == mine:
            shutil.rmtree(LOCK, ignore_errors=True)
    except OSError:
        pass


# ---------- one step ----------

def kill(process: subprocess.Popen, how=signal.SIGTERM) -> None:
    if WINDOWS:  # the step's whole tree, by its job object (app/windows_process.py)
        job = getattr(process, "_studio_job", None)
        try:
            job.terminate() if job is not None else process.kill()
        except OSError:
            pass
        return
    try:
        os.killpg(process.pid, how)
    except (ProcessLookupError, PermissionError):
        pass


def progress(buffer: bytes, offset: int, total: int, shown: list) -> list[str]:
    """`[tensorfold] train n/m` lines for the training progress bars in ``buffer`` (one line a new step)."""

    lines = []
    for hit in TQDM.finditer(buffer):
        at = offset + int(hit.group(1))
        if at != shown[0]:
            shown[0] = at
            lines.append(f"[tensorfold] train {at}/{total}")
    return lines


def execute(command: list[str], cwd: Path, offset: int = 0, total: int = 0, telemetry=None) -> None:
    """Run one step of the run in a session of its own, its output into the job's log (the training's progress bar as
    `[tensorfold] train n/m`), with the memory guard beside it. Raises SystemExit when it fails."""

    if WINDOWS:  # in a job object of its own: the step and everything it starts end together
        from windows_process import OwnedJob

        process = OwnedJob().spawn(command, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   creationflags=0x200)  # CREATE_NEW_PROCESS_GROUP
    else:
        process = subprocess.Popen(command, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    CURRENT["process"] = process
    guard = Guard(process, telemetry)
    guard.start()
    shown, pending = [-1], b""
    while True:
        chunk = process.stdout.read1(65536) if hasattr(process.stdout, "read1") else process.stdout.read(4096)
        if not chunk:
            break
        pending += chunk
        *done, pending = re.split(rb"[\r\n]", pending)
        for piece in done:
            for line in progress(piece, offset, total, shown):
                say(line)
            if piece.strip() and not TQDM.search(piece) and b"it/s]" not in piece and b"s/it]" not in piece:
                say(piece.decode("utf-8", "replace").rstrip())
    code = process.wait()
    CURRENT["process"] = None
    guard.join(timeout=2)
    if WINDOWS:
        process._studio_job.close()
    if CURRENT["stop"]:
        raise SystemExit("cancelled")
    if guard.why:
        raise SystemExit(f"stopped by the memory guard: {guard.why}. Resume once the machine has its memory back")
    if code:
        script = next((Path(part).name for part in command if part.endswith(".py")), Path(command[0]).name)
        raise SystemExit(f"{script} stopped (status {code}): its last lines above say why")


def on_stop(*_) -> None:
    CURRENT["stop"] = True
    if CURRENT["process"] is not None:
        kill(CURRENT["process"])


# ---------- the end ----------

def convert_with_musubi(python: Path, source_dir: Path, cwd: Path):
    def convert(final: Path, part: Path) -> None:
        execute([str(python), str(source_dir / "convert_lora.py"), "--input", str(final), "--output", str(part), "--target", "other"], cwd)
    return convert


def finish(home: Path, lora: str, name: str, trigger: str, kind: str, steps: int, items: int, final: Path, convert,
           extra: dict | None = None) -> Path:
    """The trained file converted into the layout ComfyUI reads, checked, put in the hub's folder as <lora>.safetensors,
    and its record added to hub.json. Returns the file."""

    import loras

    folder = home / "loras"
    folder.mkdir(parents=True, exist_ok=True)
    part = folder / f"{lora}.train-part.safetensors"
    target = folder / f"{lora}.safetensors"
    try:
        convert(final, part)
        names = list(lora_layout.header(part)[0])
        if lora_layout.layout(names) in ("kohya", "peft"):  # a converter that left musubi-tuner's own names
            renamed = part.with_name(part.name + ".comfy")
            lora_layout.to_comfy(part, renamed)
            renamed.replace(part)
        modules = loras.check(part)
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)
    # trained on the pruned checkpoint ComfyUI renders with: a pair on an adaln layer is already where ComfyUI reads it,
    # so the hub's own rewrite for LoRAs made on MiniMax's full weights (loras.prepare) must not move it again
    if any(lora_layout.ADALN.search(one) for one in lora_layout.modules(list(lora_layout.header(target)[0]))):
        shutil.copyfile(target, folder / f"{lora}.comfy.safetensors")
    loras.remember(folder, train.hub_record(lora, name, trigger, kind, steps, items, {"modules": modules, **(extra or {})}))
    say(f"[tensorfold] LoRA ready: {target.name}, {modules} layers, in Looks as {name} (trigger {trigger})")
    return target


def main(argv: list[str]) -> int:
    args = parse(argv)
    run = plan(args)
    if args.dry:
        print(json.dumps({**{k: str(v) if isinstance(v, Path) else v for k, v in run.items() if k not in ("record", "commands")},
                          "commands": run["commands"]}, indent=1))
        return 0
    # inside a machine-wide workload governor: start again inside a reservation of its own
    import workload

    if workload.enabled() and not os.environ.get("SPARK_ADMISSION_TOKEN"):
        gib = workload.number("SPARK_GOVERNOR_TRAIN_GIB", 70.0, least=1.0)
        launch = workload.client_command(os.environ.get("SPARK_GOVERNOR_TRAIN_OWNER") or "tensorfold-studio-train", "h3", gib,
                                         [sys.executable, str(Path(__file__).resolve()), *argv])
        os.execv(launch[0], launch)
    signal.signal(signal.SIGTERM, on_stop)
    signal.signal(signal.SIGINT, on_stop)
    telemetry = None
    if workload.enabled():
        telemetry = workload.Telemetry("train")
        telemetry.start()
    home, record = Path(args.home), run["record"]
    models = train.files()
    missing = [str(path) for path in models.values() if not path.is_file()]
    if missing:
        raise SystemExit(f"training is not set up here (missing {missing[0]}): {train.SETUP}")
    comfy = [sys.executable, "-X", "utf8", str(HERE / "comfy_win.py")] if WINDOWS else ["bash", str(HERE / "comfy.sh")]
    if subprocess.run(comfy + ["status"], capture_output=True, text=True).stdout.startswith("running"):
        say("[tensorfold] ComfyUI is running: stopping it, the run needs its memory")
        subprocess.run(comfy + ["stop"], capture_output=True, text=True, timeout=90)
    mine = take_lock(args.lora)
    try:
        admit([models["dit"], models["text_encoder"]])
        run["out"].mkdir(parents=True, exist_ok=True)
        run["toml"].write_text(train.toml_text(Path(args.dataset), set(run["kinds"]), args.size))
        done_file = run["out"] / "final.json"
        try:
            final = Path(json.loads(done_file.read_text())["file"])
        except (OSError, ValueError, KeyError):
            final = None
        if final is None or not final.is_file():
            if run["state"] is not None:
                say(f"[tensorfold] resuming from the checkpoint at step {run['offset']} of {args.steps}")
            elif args.resume:
                say("[tensorfold] no checkpoint was saved yet: the run starts from the beginning")
            say(f"[tensorfold] caching {len(record['items'])} clips and stills at {train.SIZES[args.size]['label']}")
            execute(run["commands"]["latents"], run["cwd"], telemetry=telemetry)
            say("[tensorfold] caching the captions with the text encoder")
            execute(run["commands"]["text"], run["cwd"], telemetry=telemetry)
            say(f"[tensorfold] training {args.steps - run['offset']} steps, rank {args.rank}, a checkpoint every {args.save_every}")
            say(f"[tensorfold] train {run['offset']}/{args.steps}")
            execute(run["commands"]["train"], run["cwd"], run["offset"], args.steps, telemetry)
            final = run["final"]
            if not final.is_file():
                raise SystemExit(f"the training ended without writing {final.name}")
            done_file.write_text(json.dumps({"file": str(final), "at": time.time()}))
        say("[tensorfold] converting the LoRA for ComfyUI")
        finish(home, args.lora, args.name, args.trigger, args.kind, args.steps, len(record["items"]), final,
               convert_with_musubi(run["python"], run["source"], run["cwd"]),
               {"rank": args.rank, "size": args.size, "dataset": record["id"]})
    finally:
        drop_lock(mine)
        if telemetry is not None:
            telemetry.done.set()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
