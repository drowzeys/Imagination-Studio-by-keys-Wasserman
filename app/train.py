"""Train a LoRA in the Studio: a folder of the Library's clips or stills becomes a look or a character for MiniMax H3.

    POST /api/train/datasets {name, trigger, kind: look|character, items: [Library paths], captions: auto|prompts|none}
    POST /api/train/start    {dataset, steps, rank, lr, size, save_every}  or  {resume: <lora id>}
    GET  /api/train          what this machine has for training, the datasets and the runs

A dataset is a folder HOME/train/<id>/: the items linked (or copied) into media/, one caption each, the jsonl files and
the dataset.toml that kohya-ss's musubi-tuner reads. A run is a job of kind `train` in the Studio's queue, on this
machine only, one at a time with the renders: spark/train_lora.py caches the latents and the text, trains with
musubi-tuner (the recipe below) and writes the LoRA into the hub (HOME/loras/<id>.safetensors and a hub.json record),
in the layout ComfyUI reads. It saves a checkpoint every `save_every` steps; a cancelled or failed run resumes from the
last one.

The recipe, measured on a DGX Spark (musubi-tuner at f8a1b03, version 0.3.6): MiniMax H3's FL2VA transformer (pruned,
bf16, quantized to int8 at load) with ostris's de-distillation adapter v2 merged under it, rank 16, AdamW 8-bit at 1e-4,
constant, 928x512 and 124 frames (two windows a clip), about 110 seconds a step and about 55 GB held. Installed by
spark/setup-train.sh (oneshot-setup-spark.sh --train).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

KINDS = ("train",)
MUSUBI_COMMIT = "f8a1b03794a49239a3539015075f5123d6c07d66"  # kohya-ss/musubi-tuner, 2026-09-27, version 0.3.6
import windows_cuda  # noqa: E402  (the native Windows CUDA tier, beside this file)

WINDOWS = sys.platform == "win32"
# natively on Windows (the CUDA tier): scripts\setup-windows-cuda.ps1's train part (musubi-tuner's venv and the files)
SETUP = windows_cuda.setup_hint("train") if WINDOWS else "bash oneshot-setup-spark.sh --train"
# the sizes a run trains at: width, height, frames a window, seconds a step (measured at standard on a DGX Spark; the
# small one is that time scaled by its pixels, not measured)
SIZES = {"standard": {"width": 928, "height": 512, "frames": 124, "per_step": 110, "label": "928x512, 124 frames (measured)"},
         "small": {"width": 704, "height": 384, "frames": 124, "per_step": 63, "label": "704x384, 124 frames (quicker, softer)"}}
STEPS = (300, 3000, 1000)  # least, most, default
RANKS = (8, 32, 16)
RATES = (1e-5, 5e-4, 1e-4)
SAVE_EVERY = (50, 500, 100)
LEAST_ITEMS, MOST_ITEMS = 8, 200
FPS = 24
LEAST_SECONDS = 124 / FPS - 0.1  # a clip holds one 124-frame window (5.2 s at 24 fps); a 5 s take of the Studio's has it
VIDEO = (".mp4", ".mov")
IMAGE = (".png", ".jpg", ".jpeg", ".webp")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.'-]{0,47}$")
TRIGGER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,31}$")
SHOT = re.compile(r"\[Shot \d+\]\s*")
NEED_GB = 55  # what a run holds at the standard size (measured)

# the files a run reads, under SPARK_MODELS; the text encoder is the first of these that is there
DIT = "MiniMax-H3-Comfy/diffusion_models/minimax_h3_fl2va_pruned_bf16.safetensors"
# the stock int8 text encoder first: the trainer's loader refuses the NVFP4 file the Studio ships (its per-tensor
# scales are 1-element tensors, not scalars), and a LoRA trained with the stock encoder renders the same with either
TEXT_ENCODERS = ("FastH3-Comfy/text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
                 "FastH3-Comfy/text_encoders/qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors")
AUDIO_VAE = "FastH3-Comfy/vae/minimax_h3_audio_vae_fp32.safetensors"
VIDEO_VAE = "Train-H3/vae/minimax_h3_video_vae_fp16.safetensors"  # Comfy-Org/MiniMax-H3; the int8 one does not load
ADAPTER = "Train-H3/adapters/ostris_dd_adapter_v2.safetensors"   # ostris/minimax_h3_training_adapter v2


def spark() -> bool:
    """Whether training runs here: a DGX Spark (Linux), or Windows with the native CUDA tier (an RTX Spark N1X)."""

    return sys.platform.startswith("linux") or windows_cuda.active()


def prefix() -> Path:
    return Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio")).expanduser()


def models() -> Path:
    return Path(os.environ.get("SPARK_MODELS", Path.home() / "spark-studio-models")).expanduser()


def tools() -> dict:
    """Where the trainer is: musubi-tuner's checkout, its environment and the optimizer setup-train.sh found working."""

    root = prefix() / "train"
    try:
        optimizer = (root / "optimizer").read_text().strip() or "adamw8bit"
    except OSError:
        optimizer = "adamw8bit"
    bin_ = root / ("venv/Scripts" if WINDOWS else "venv/bin")
    exe = ".exe" if WINDOWS else ""
    return {"root": root, "musubi": root / "musubi-tuner", "python": bin_ / f"python{exe}",
            "accelerate": bin_ / f"accelerate{exe}", "optimizer": optimizer if optimizer in ("adamw8bit", "adamw") else "adamw8bit"}


def files() -> dict:
    found = next((models() / one for one in TEXT_ENCODERS if (models() / one).is_file()), models() / TEXT_ENCODERS[0])
    return {"dit": models() / DIT, "text_encoder": found, "audio_vae": models() / AUDIO_VAE,
            "video_vae": models() / VIDEO_VAE, "adapter": models() / ADAPTER}


def installed_commit(musubi: Path) -> str:
    """The commit of musubi-tuner's checkout, read from git's own files (no git process)."""

    head = musubi / ".git/HEAD"
    try:
        said = head.read_text().strip()
    except OSError:
        return ""
    if said.startswith("ref: "):
        try:
            return (musubi / ".git" / said[5:]).read_text().strip()
        except OSError:
            return ""
    return said


def ready() -> dict:
    """Whether this machine can train: {"ready", "missing": [what is not there], "setup", "optimizer", "spark"}."""

    if not spark():
        return {"ready": False, "spark": False, "missing": ["a DGX Spark (or an RTX Spark N1X with the native CUDA tier): training runs on one"],
                "setup": SETUP, "optimizer": None}
    have, missing = tools(), []
    if installed_commit(have["musubi"]) != MUSUBI_COMMIT:
        missing.append(f"musubi-tuner at {MUSUBI_COMMIT[:7]} in {have['musubi']}")
    if not have["python"].exists() or not have["accelerate"].exists():
        missing.append(f"the training environment {have['root'] / 'venv'}")
    names = {"dit": "MiniMax H3's FL2VA transformer (bf16)", "text_encoder": "the H3 text encoder",
             "audio_vae": "the H3 audio decoder", "video_vae": "the H3 video decoder in fp16",
             "adapter": "ostris's de-distillation adapter v2"}
    for key, path in files().items():
        if not path.is_file():
            missing.append(f"{names[key]} ({path})")
    return {"ready": not missing, "spark": True, "missing": missing, "setup": SETUP, "optimizer": have["optimizer"]}


# ---------- datasets ----------

def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "lora"


def clean(prompt: str) -> str:
    """A generation prompt as a caption: no shot markers, one line, at most 900 characters."""

    text = " ".join(SHOT.sub("", str(prompt or "")).split())
    return text[:900].rsplit(" ", 1)[0] if len(text) > 900 else text


def index_captions(home: Path) -> dict:
    """The Library's caption index (HOME/library/captions.json) as {path: caption}: a map of path to text or to
    {caption|text|description}, or a list of {path, caption}. {} when there is none."""

    try:
        data = json.loads((home / "library/captions.json").read_text())
    except (OSError, ValueError):
        return {}
    if isinstance(data, dict) and isinstance(data.get("captions"), (dict, list)):
        data = data["captions"]
    pairs = data.items() if isinstance(data, dict) else ((one.get("path"), one) for one in data if isinstance(one, dict)) if isinstance(data, list) else ()
    out = {}
    for path, said in pairs:
        text = said if isinstance(said, str) else next((said.get(k) for k in ("caption", "text", "description") if isinstance(said, dict) and isinstance(said.get(k), str)), None)
        if path and text and text.strip():
            out[str(path)] = text.strip()
    return out


def take_prompts(home: Path) -> dict:
    """{path: the prompt it was made from} from the takes list (HOME/sparks/takes.jsonl), where a line has one."""

    out = {}
    try:
        with open(home / "sparks/takes.jsonl") as lines:
            for line in lines:
                try:
                    one = json.loads(line)
                except ValueError:
                    continue
                said = one.get("prompt") or (one.get("settings") or {}).get("prompt")
                if isinstance(one, dict) and one.get("path") and isinstance(said, str) and said.strip():
                    out[one["path"]] = said
    except OSError:
        pass
    return out


def caption(trigger: str, kind: str, item_kind: str, description: str) -> str:
    """Every caption starts with the trigger; a character's says what it is a picture or a clip of."""

    rest = description.strip()
    if rest.lower().startswith(trigger.lower()):
        rest = rest[len(trigger):].lstrip(" ,.;:")
    lead = f"{trigger}, a {'video' if item_kind == 'video' else 'photo'} of {trigger}" if kind == "character" else trigger
    return f"{lead}, {rest}" if rest else lead


def toml_text(folder: Path, kinds: set, size: str = "standard") -> str:
    """dataset.toml for musubi-tuner: one [[datasets]] for the clips and one for the stills, each with its own cache."""

    chosen = SIZES[size]
    lines = ["[general]", f"resolution = [{chosen['width']}, {chosen['height']}]", "batch_size = 1", "enable_bucket = true",
             "bucket_no_upscale = false", ""]
    if "video" in kinds:
        lines += ["[[datasets]]", f'video_jsonl_file = "{folder / "videos.jsonl"}"', f'cache_directory = "{folder / "cache" / size / "video"}"',
                  f"target_frames = [{chosen['frames']}]", 'frame_extraction = "uniform"', "frame_sample = 2", ""]
    if "image" in kinds:
        lines += ["[[datasets]]", f'image_jsonl_file = "{folder / "images.jsonl"}"', f'cache_directory = "{folder / "cache" / size / "image"}"', ""]
    return "\n".join(lines)


def write_dataset(folder: Path, record: dict) -> None:
    """The dataset's files from its record: metadata.jsonl (every item), videos.jsonl / images.jsonl (what
    musubi-tuner reads), dataset.toml at the standard size, and dataset.json (the record)."""

    folder.mkdir(parents=True, exist_ok=True)
    kinds = {one["kind"] for one in record["items"]}
    rows = {"video": [], "image": []}
    with open(folder / "metadata.jsonl", "w") as meta:
        for one in record["items"]:
            key = "video_path" if one["kind"] == "video" else "image_path"
            line = {key: str(folder / one["file"]), "caption": one["caption"]}
            rows[one["kind"]].append(line)
            meta.write(json.dumps(line) + "\n")
    for kind, name in (("video", "videos.jsonl"), ("image", "images.jsonl")):
        if rows[kind]:
            (folder / name).write_text("".join(json.dumps(line) + "\n" for line in rows[kind]))
    (folder / "dataset.toml").write_text(toml_text(folder, kinds))
    (folder / "dataset.json").write_text(json.dumps(record, indent=1))


def build_dataset(home: Path, body: dict, inside, media_seconds, prompt_of=None) -> dict:
    """A dataset folder from a request: checked (names, at least LEAST_ITEMS items, every clip long enough), the items
    linked into media/ (copied where a link cannot be made), a caption each. Returns the record."""

    name, trigger = str(body.get("name") or "").strip(), str(body.get("trigger") or "").strip()
    kind, source = str(body.get("kind") or "look"), str(body.get("captions") or "auto")
    if not NAME.match(name):
        raise HTTPException(400, "the name is 1 to 48 letters, digits, spaces, dots, dashes or underscores")
    if not TRIGGER.match(trigger):
        raise HTTPException(400, "the trigger is one word of 2 to 32 letters, digits, dashes or underscores (e.g. wk_noir)")
    if kind not in ("look", "character"):
        raise HTTPException(400, "kind is look or character")
    if source not in ("auto", "prompts", "none"):
        raise HTTPException(400, "captions is auto, prompts or none")
    items = body.get("items")
    if not isinstance(items, list) or not all(isinstance(one, str) for one in items):
        raise HTTPException(400, "items is a list of Library paths")
    items = list(dict.fromkeys(one.strip() for one in items if one.strip()))
    if len(items) < LEAST_ITEMS:
        raise HTTPException(400, f"a LoRA needs at least {LEAST_ITEMS} clips or stills; {len(items)} were picked")
    if len(items) > MOST_ITEMS:
        raise HTTPException(400, f"at most {MOST_ITEMS} clips or stills in one dataset")
    found = []
    for path in items:
        where = inside(path)
        suffix = where.suffix.lower()
        if not where.is_file() or suffix not in VIDEO + IMAGE:
            raise HTTPException(400, f"{path} is not a clip or a still in the Library")
        seconds = None
        if suffix in VIDEO:
            seconds = media_seconds(str(where))[0]
            if seconds is None:
                raise HTTPException(400, f"the length of {path} could not be read (ffprobe is needed)")
            if seconds < LEAST_SECONDS:
                raise HTTPException(400, f"{path} is {seconds:.1f} s: every clip needs at least 5 seconds (124 frames)")
        found.append((path, where, "video" if suffix in VIDEO else "image", seconds))
    prompts = take_prompts(home) if source != "none" else {}
    index = index_captions(home) if source == "auto" else {}
    base = home / "train"
    base.mkdir(parents=True, exist_ok=True)
    dataset_id = slug(name)
    for n in range(2, 1000):
        if not (base / dataset_id).exists():
            break
        dataset_id = f"{slug(name)}-{n}"
    folder = base / dataset_id
    (folder / "media").mkdir(parents=True)
    record = {"id": dataset_id, "name": name, "trigger": trigger, "kind": kind, "captions": source, "created": time.time(), "items": []}
    try:
        for number, (path, where, item_kind, seconds) in enumerate(found, 1):
            target = folder / "media" / f"{number:03d}{where.suffix.lower()}"
            try:
                os.link(where, target)
            except OSError:
                shutil.copy2(where, target)
            said, came = None, "trigger"
            if source != "none":
                said = (prompt_of(path) if prompt_of else None) or prompts.get(path)
                came = "prompt" if said else came
            if not said and source == "auto":
                said = index.get(path)
                came = "index" if said else came
            record["items"].append({"path": path, "file": f"media/{target.name}", "kind": item_kind, "seconds": seconds,
                                    "caption": caption(trigger, kind, item_kind, clean(said or "")), "from": came})
        write_dataset(folder, record)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return record


def summary(record: dict) -> dict:
    items = record.get("items") or []
    return {key: record.get(key) for key in ("id", "name", "trigger", "kind", "captions", "created")} | {
        "items": len(items), "videos": sum(1 for one in items if one["kind"] == "video"),
        "stills": sum(1 for one in items if one["kind"] == "image"),
        "from": {way: sum(1 for one in items if one.get("from") == way) for way in ("prompt", "index", "trigger")}}


# ---------- runs ----------

STATE = re.compile(r"^(?P<name>.+?)(?:-from(?P<off>\d{5}))?-step(?P<n>\d{8})-state$")


def segment_name(lora_id: str, offset: int) -> str:
    """musubi-tuner counts the steps of a resumed run from 0 again: each part after the first is named by the step it
    starts from, so its checkpoints never take an earlier part's names."""

    return lora_id if offset <= 0 else f"{lora_id}-from{offset:05d}"


def checkpoints(out: Path) -> list[tuple[int, Path]]:
    """[(step, state folder)] of a run's saved states, oldest first; a folder with no file in it is not one."""

    found = []
    try:
        entries = list(out.iterdir())
    except OSError:
        return []
    for entry in entries:
        hit = STATE.match(entry.name)
        if hit and entry.is_dir() and any(child.is_file() for child in entry.iterdir()):
            found.append((int(hit["off"] or 0) + int(hit["n"]), entry))
    return sorted(found)


def last_checkpoint(out: Path) -> tuple[int, Path] | None:
    found = checkpoints(out)
    return found[-1] if found else None


def run_folder(home: Path, dataset_id: str, lora_id: str) -> Path:
    return home / "train" / dataset_id / "runs" / lora_id


def runs(home: Path) -> list[dict]:
    """Every run of every dataset: its settings, the last step saved, and whether its LoRA is in the hub."""

    out = []
    for path in sorted((home / "train").glob("*/runs/*/run.json")):
        try:
            run = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        last = last_checkpoint(path.parent)
        done = (home / "loras" / f"{run.get('lora')}.safetensors").is_file()
        out.append({**run, "saved": last[0] if last else 0, "done": done,
                    "can_resume": not done and last is not None and last[0] < int(run.get("steps") or 0)})
    return sorted(out, key=lambda one: -float(one.get("created") or 0))


def hub_ids(home: Path, presets: Path) -> set:
    taken = set()
    for source in (presets, home / "loras/hub.json"):
        try:
            taken |= {one.get("id") for one in json.loads(source.read_text())}
        except (OSError, ValueError, TypeError):
            pass
    return taken


def checked_params(body: dict) -> dict:
    """A run's settings, in range: steps, rank, lr, size, save_every."""

    def number(key, kind, limits):
        value = body.get(key)
        try:
            value = kind(limits[2] if value in (None, "") else value)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{key} is a number")
        if not limits[0] <= value <= limits[1]:
            raise HTTPException(400, f"{key} is between {limits[0]:g} and {limits[1]:g}")
        return value

    size = str(body.get("size") or "standard")
    if size not in SIZES:
        raise HTTPException(400, "size is " + " or ".join(SIZES))
    return {"steps": number("steps", int, STEPS), "rank": number("rank", int, RANKS), "lr": number("lr", float, RATES),
            "size": size, "save_every": number("save_every", int, SAVE_EVERY)}


def estimate(steps: int, size: str = "standard", done: int = 0) -> int:
    """Seconds: the steps still to go at the size's measured time a step."""

    return max(0, int(steps) - int(done)) * SIZES[size]["per_step"]


def stop_comfy(pack: Path) -> str:
    """Before a run: ComfyUI stopped, so its models give their memory back (a run holds about 55 GB). Returns the
    line for the job's log."""

    try:
        said = subprocess.run(windows_cuda.comfy_argv("stop") if WINDOWS else ["bash", str(pack / "spark/comfy.sh"), "stop"],
                              capture_output=True, text=True, timeout=90)
        return f"[tensorfold] ComfyUI: {(said.stdout or said.stderr).strip() or 'stopped'} (a LoRA run needs its memory)\n"
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"[tensorfold] ComfyUI was not stopped: {error}\n"


def attach(home: Path, pack: Path, is_spark: bool, inside, media_seconds, enqueue, jobs: dict, lock):
    """(router, build, free): the routes, the job builder for server.build(), and what server.perform() runs before a
    run starts (ComfyUI stopped)."""

    router = APIRouter()
    base = home / "train"
    making = threading.Lock()
    presets = pack / "app/lora-presets.json"

    def prompt_of(path: str) -> str | None:
        """The prompt of the job that made this clip, while the queue still holds it."""

        with lock:
            found = [one for one in jobs.values() if path in (one.get("outputs") or [])]
        for one in found:
            said = (one.get("params") or {}).get("prompt")
            if isinstance(said, str) and said.strip():
                return said
        return None

    def dataset(dataset_id: str) -> tuple[Path, dict]:
        if not re.fullmatch(r"[a-z0-9-]{1,48}", str(dataset_id or "")):
            raise HTTPException(400, "no such dataset")
        folder = base / dataset_id
        try:
            return folder, json.loads((folder / "dataset.json").read_text())
        except (OSError, ValueError):
            raise HTTPException(404, f"no dataset {dataset_id}")

    def build(params: dict, job_id: str) -> tuple[list[str], dict, list[str]]:
        """The runner's command for a `train` job (spark/train_lora.py), its environment and output."""

        if not is_spark:
            raise HTTPException(400, "Training a LoRA runs on a DGX Spark")
        said = ready()
        if not said["ready"]:
            raise HTTPException(400, f"training is not set up here ({said['missing'][0]}): {SETUP}")
        folder, record = dataset(params.get("dataset"))
        lora_id = str(params.get("lora") or "")
        if not re.fullmatch(r"[a-z0-9-]{1,48}", lora_id):
            raise HTTPException(400, "the LoRA's id is lower-case letters, digits and dashes")
        chosen = checked_params(params)
        command = [sys.executable, str(pack / "spark/train_lora.py"), "--home", str(home), "--dataset", str(folder),
                   "--lora", lora_id, "--name", str(params.get("name") or record["name"]), "--trigger", record["trigger"],
                   "--kind", record["kind"], "--steps", str(chosen["steps"]), "--rank", str(chosen["rank"]),
                   "--lr", f"{chosen['lr']:g}", "--size", chosen["size"], "--save-every", str(chosen["save_every"])]
        if params.get("resume"):
            command.append("--resume")
        return command, {"PYTHONUNBUFFERED": "1", "TRAIN_JOB": job_id}, [str(home / "loras" / f"{lora_id}.safetensors")]

    def free(job: dict) -> str:
        return stop_comfy(pack) if is_spark else ""

    def queue(params: dict, title: str, done: int = 0) -> dict:
        build(params, "check")
        return enqueue("train", title, params, estimate(params["steps"], params["size"], done))

    @router.get("/api/train")
    def overview() -> dict:
        found = []
        for path in sorted(base.glob("*/dataset.json")):
            try:
                found.append(summary(json.loads(path.read_text())))
            except (OSError, ValueError):
                continue
        return {**ready(), "datasets": sorted(found, key=lambda one: -float(one.get("created") or 0)), "runs": runs(home),
                "sizes": {key: {"label": one["label"], "per_step": one["per_step"]} for key, one in SIZES.items()},
                "limits": {"steps": STEPS, "rank": RANKS, "lr": RATES, "save_every": SAVE_EVERY, "items": LEAST_ITEMS}}

    @router.post("/api/train/datasets")
    def new_dataset(body: dict) -> dict:
        with making:
            return summary(build_dataset(home, body, inside, media_seconds, prompt_of))

    @router.get("/api/train/datasets/{dataset_id}")
    def one_dataset(dataset_id: str) -> dict:
        return dataset(dataset_id)[1]

    @router.delete("/api/train/datasets/{dataset_id}")
    def drop_dataset(dataset_id: str) -> dict:
        folder, record = dataset(dataset_id)
        with lock:
            if any(one["kind"] == "train" and one["status"] in ("queued", "running") and one["params"].get("dataset") == record["id"] for one in jobs.values()):
                raise HTTPException(409, "a training run of this dataset is in the queue: cancel it first")
        trash = home / "trash"
        trash.mkdir(exist_ok=True)
        shutil.move(str(folder), trash / (time.strftime("%Y%m%d-%H%M%S-") + "train-" + dataset_id))
        return {"removed": dataset_id}

    @router.post("/api/train/start")
    def start(body: dict) -> dict:
        """A run of a dataset, or ({resume: lora id}) the rest of a run that stopped, from its last checkpoint."""

        if body.get("resume"):
            found = next((one for one in runs(home) if one.get("lora") == str(body["resume"])), None)
            if found is None:
                raise HTTPException(404, f"no run {body['resume']}")
            if found["done"]:
                raise HTTPException(400, f"{found['name']} is finished: it is in Looks")
            with lock:
                if any(one["kind"] == "train" and one["status"] in ("queued", "running") and one["params"].get("lora") == found["lora"] for one in jobs.values()):
                    raise HTTPException(400, f"{found['name']} is in the queue already")
            params = {key: found[key] for key in ("dataset", "lora", "name", "steps", "rank", "lr", "size", "save_every")} | {"resume": True}
            return queue(params, f"Train LoRA · {found['name']} (resumed)", found["saved"])
        folder, record = dataset(body.get("dataset"))
        chosen = checked_params(body)
        name = str(body.get("name") or record["name"]).strip()
        if not NAME.match(name):
            raise HTTPException(400, "the name is 1 to 48 letters, digits, spaces, dots, dashes or underscores")
        taken = hub_ids(home, presets) | {one.get("lora") for one in runs(home)}
        lora_id = slug(name)
        for n in range(2, 1000):
            if lora_id not in taken:
                break
            lora_id = f"{slug(name)}-{n}"
        params = {"dataset": record["id"], "lora": lora_id, "name": name, **chosen}
        made = queue(params, f"Train LoRA · {name}")
        out = run_folder(home, record["id"], lora_id)
        out.mkdir(parents=True, exist_ok=True)
        (out / "run.json").write_text(json.dumps({**params, "trigger": record["trigger"], "kind": record["kind"],
                                                  "items": len(record["items"]), "created": time.time(), "job": made["id"]}, indent=1))
        return made

    @router.get("/api/train/status/{key}")
    def status(key: str) -> dict:
        """A run by its job id or its LoRA's id: the job, the last step saved and whether the LoRA is in the hub."""

        with lock:
            job = jobs.get(key) or next((one for one in sorted(jobs.values(), key=lambda j: -j["created"])
                                         if one["kind"] == "train" and one["params"].get("lora") == key), None)
            job = dict(job) if job and job["kind"] in KINDS else None  # another kind's job id is no training run
        lora_id = job["params"].get("lora") if job else key
        run = next((one for one in runs(home) if one.get("lora") == lora_id), None)
        if job is None and run is None:
            raise HTTPException(404, f"no training job or run {key}")
        out = {"lora": lora_id, "run": run, "installed": (home / "loras" / f"{lora_id}.safetensors").is_file()}
        if job:
            out["job"] = {k: job.get(k) for k in ("id", "kind", "status", "title", "progress", "error", "estimate", "outputs", "started", "finished")}
        return out

    return router, build, free


# ---------- the hub ----------

def hub_record(lora_id: str, name: str, trigger: str, kind: str, steps: int, items: int, extra: dict | None = None) -> dict:
    """The hub.json record of a LoRA trained here (app/loras.py lists it under My LoRAs)."""

    styles = {"look": trigger} if kind == "look" else {"character": f"a video of {trigger}"}
    return {"id": lora_id, "name": name, "author": "", "url": "", "page": "", "trigger": trigger, "styles": styles,
            "strength": 1.0, "status": "ready", "target": "video", "base": "minimax-h3", "category": ["mine"], "needs": "text",
            "comma": True, "licence": "", "notes": f"Trained in the Studio on {items} clips and stills, {steps} steps.",
            "trained": {"steps": steps, "items": items, "at": time.time(), "kind": kind, **(extra or {})}}
