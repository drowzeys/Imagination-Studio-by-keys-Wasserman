"""Models and weights: what the studio needs, found anywhere on this machine and linked, instead of a fresh download.

Every engine looks for its files in one expected place (an environment variable or a default path). This module
knows all of those places (``catalogue``: names read from the code that uses them, not copied), searches the machine
for each file or folder that is not there (``scan``), proves a candidate by its signature (file names, folder
members, sizes, a safetensors header, a sha256 where the studio knows one) and links it:

- ``use``: a symlink at the expected place pointing at what was found, when that place can be written; else a link
  recorded in ``$STUDIO_HOME/.discovery.json`` that loaders read through ``resolve``, and the variable that names
  the place set for the running process. Done by itself when exactly one verified candidate exists.
- ``unlink``: undoes a link the studio made, never a link of the user's own.

Nothing is moved, copied, deleted or downloaded. A scan runs in a background thread, one at a time, at a low
priority, never inside a request handler: once after the app starts (``STUDIO_DISCOVER_DELAY``, 20 s) and on
demand, for at most ``STUDIO_SCAN_SECONDS`` (120). ``python3 app/discover.py --scan [--link]`` runs one from a
shell (the one-shot installers do, before each download). Standard library only at the top, so the installers'
Python can run it.
"""

from __future__ import annotations

import ast
import collections
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
PACK = APP.parent
SPARK = sys.platform.startswith("linux")
MAC = sys.platform == "darwin"
DEPTH = 8
NOTE = ("Nothing is moved, copied or downloaded: a file found elsewhere is linked where the studio looks for it "
        "(a symlink, or a recorded link when that folder cannot be written).")
# folders never entered: system, caches of other tools, environments, trash, backups, the studio's own renders
SKIP_NAMES = {"proc", "sys", "dev", "node_modules", ".git", ".hg", ".svn", "__pycache__", ".Trash", ".Trashes", "Trash",
              ".Spotlight-V100", ".fseventsd", ".DocumentRevisions-V100", ".TemporaryItems", "Backups.backupdb",
              ".timemachine", ".MobileBackups", "site-packages", "dist-packages", ".npm", ".cargo", ".rustup",
              ".pyenv", ".conda", "pkgs", "snap", "lost+found", ".docker", "containerd", "Photos Library.photoslibrary",
              "triton-cache", ".triton", "managed-logs", "logs", "zig-cache", ".zig-cache"}
SKIP_SUFFIXES = (".backupbundle", ".sparsebundle", ".photoslibrary", ".app", ".xcarchive")
# file systems the walk does not enter below its roots unless STUDIO_SCAN_NETWORK=1 (a hung network mount would hold
# the scan; the studio's own folders are walked whatever they are on)
NETWORK = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "afpfs", "webdav", "fuse.sshfs", "sshfs", "fuse.rclone", "9p",
           "autofs", "fuse.s3fs", "davfs"}
VIRTUAL = {"proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2", "squashfs", "overlay", "debugfs",
           "tracefs", "securityfs", "pstore", "bpf", "configfs", "fusectl", "mqueue", "hugetlbfs", "binfmt_misc",
           "efivarfs", "ramfs", "nsfs", "devfs"}
LORA_FOLDERS = {"loras", "lora", "Lora", "LoRA", "LoRAs", "Loras"}
# file names too common to find a LoRA by (a preset whose author's file is called this is found by its id's name)
# what may follow a LoRA's own name in another copy of it (80s_horror_wk_comfy): a training step's checkpoint is not one
LORA_SUFFIX = re.compile(r"^([._-](comfy|comfyui|bf16|fp16|fp32|pruned|converted|mlx|final))*$", re.I)
GENERIC = {"model.safetensors", "adapter_model.safetensors", "pytorch_lora_weights.safetensors", "lora.safetensors",
           "diffusion_pytorch_model.safetensors"}

_lock = threading.Lock()  # the cache file
_scan_lock = threading.Lock()  # one scan at a time
_progress: dict = {}
_set_env: dict[str, str] = {}  # variables this process set for a recorded link (never the user's own)
_cache: tuple[float, dict] | None = None


# ---------- where things are ----------

def home() -> Path:
    return Path.home()


def studio_home() -> Path:
    named = os.environ.get("STUDIO_HOME", "").strip()
    if named:
        return Path(named).expanduser()
    new = home() / "ImaginationStudio"  # app/studio_home.py: an older ~/TensorFoldStudio until the server moves it
    return new if new.exists() or not (home() / "TensorFoldStudio").is_dir() else home() / "TensorFoldStudio"


def env_path(name: str, default) -> Path:
    """The path a variable names, unless this process set it itself for a recorded link: then its default."""

    given = os.environ.get(name)
    if given and _set_env.get(name) != given:
        return Path(given).expanduser()
    return Path(default).expanduser()


def prefix() -> Path:
    return env_path("PREFIX", home() / ".local/opt/tensorfold-studio")


def spark_models() -> Path:
    return env_path("SPARK_MODELS", home() / "spark-studio-models")


def hf_caches() -> list[Path]:
    found = []
    for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if os.environ.get(name):
            found.append(Path(os.environ[name]).expanduser())
    if os.environ.get("HF_HOME"):
        found.append(Path(os.environ["HF_HOME"]).expanduser() / "hub")
    found.append(home() / ".cache/huggingface/hub")
    return list(dict.fromkeys(found))


def shown(path) -> str:
    """A path as a person reads it: ~/... under the home folder."""

    if path is None:
        return ""
    text, base = str(path), str(home())
    return "~" + text[len(base):] if text == base or text.startswith(base + os.sep) else text


# ---------- names read from the code that uses them ----------

_literal_cache: dict = {}


def literals(file: Path) -> dict:
    """The module-level constants of a Python file that are plain literals, read without importing it."""

    try:
        key = (str(file), file.stat().st_mtime)
    except OSError:
        return {}
    if key in _literal_cache:
        return _literal_cache[key]
    out = {}
    try:
        tree = ast.parse(file.read_text())
    except (OSError, SyntaxError, ValueError):
        tree = None
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, SyntaxError, TypeError):
                # NAME = os.environ.get("VAR", "default"): its default
                call = node.value
                if not (isinstance(call, ast.Call) and len(call.args) == 2 and isinstance(call.args[1], ast.Constant)
                        and ast.unparse(call.func) in ("os.environ.get", "os.getenv")):
                    continue
                value = call.args[1].value
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = value
                elif isinstance(target, ast.Tuple) and isinstance(value, tuple) and len(target.elts) == len(value):
                    for one, part in zip(target.elts, value):
                        if isinstance(one, ast.Name):
                            out[one.id] = part
    _literal_cache[key] = out
    return out


def shell_lines(file: Path) -> list[str]:
    """A shell script's lines with continuations joined."""

    try:
        return file.read_text().replace("\\\n", " ").splitlines()
    except OSError:
        return []


def shell_vars(file: Path) -> dict:
    out = {}
    for line in shell_lines(file):
        found = re.match(r'^\s*([A-Z0-9_]+)="([^"$]*)"\s*(#.*)?$', line)
        if found:
            out[found.group(1)] = found.group(2)
    return out


def spark_fetches() -> list[tuple[str, str, str]]:
    """(repository, folder under SPARK_MODELS, file) of every ``fetch`` in oneshot-setup-spark.sh."""

    out = []
    flux3 = literals(PACK / "spark" / "spark_generate.py").get("FLUX3")
    for line in shell_lines(PACK / "oneshot-setup-spark.sh"):
        words = line.split()
        if len(words) >= 4 and words[0] == "fetch" and not words[1].startswith("("):
            for f in words[3:]:
                if f.startswith("#"):
                    continue
                if f.strip('"') == "${KLEIN[@]}" and isinstance(flux3, dict):
                    # BFL's checkpoints, fetched under their present names (renamed 2026-10-17): the names spark_generate.py loads
                    out += [(words[1], words[2], f"diffusion_models/{v[0]}") for v in flux3.values() if isinstance(v, (tuple, list)) and v]
                elif not f.startswith(('"$', "$")):
                    out.append((words[1], words[2], f))
    return out


def workflow_models() -> list[tuple[str, str, str]]:
    """(folder under Workflows-Comfy, name there, sha256) of spark/setup-workflows.sh's ``model`` lines."""

    out = []
    for line in shell_lines(PACK / "spark" / "setup-workflows.sh"):
        words = line.split()
        if len(words) >= 6 and words[0] == "model" and re.fullmatch(r"[0-9a-f]{64}", words[5]):
            out.append((words[3], words[4], words[5]))
    return out


def motion_models() -> list[tuple[str, str]]:
    """(name under the motion models folder, sha256) of scripts/setup-motion.sh."""

    text = "\n".join(shell_lines(PACK / "scripts" / "setup-motion.sh"))
    out = [(m.group(1), m.group(2)) for m in re.finditer(r"(pose_landmarker_\w+\.task) ([0-9a-f]{64})", text)]
    names, sums = re.search(r'DEPTH_NAME="([^"]+)"', text), re.search(r'DEPTH_SHA256="([0-9a-f]{64})"', text)
    if names and sums:
        out.append((names.group(1), sums.group(1)))
    return out


def lora_presets() -> list[dict]:
    try:
        built = json.loads((APP / "lora-presets.json").read_text())
    except (OSError, ValueError):
        built = []
    try:
        mine = json.loads((studio_home() / "loras" / "hub.json").read_text())
    except (OSError, ValueError):
        mine = []
    return [r for r in built + mine if isinstance(r, dict) and r.get("id")]


# ---------- signatures ----------

def safetensors_header(path: Path, limit: int = 100 << 20) -> dict | None:
    try:
        with open(path, "rb") as f:
            size = struct.unpack("<Q", f.read(8))[0]
            if not 2 <= size <= limit:
                return None
            return json.loads(f.read(size))
    except (OSError, ValueError, struct.error):
        return None


LORA_KEY = re.compile(r"lora[_.]?(a|b|down|up)\b|lora_(a|b|down|up)|\.lora\.|_lora\.", re.I)


def is_lora(path: Path) -> bool:
    found = safetensors_header(path)
    return bool(found) and any(LORA_KEY.search(k) for k in found if k != "__metadata__")


def is_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
    except OSError:
        return False
    return magic == b"\x7fELF" or magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xcf")


def head(path: Path, count: int) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(count)
    except OSError:
        return b""


def json_of(path: Path) -> dict:
    try:
        if path.stat().st_size > 50 << 20:
            return {}
        found = json.loads(path.read_text(errors="replace"))
        return found if isinstance(found, dict) else {}
    except (OSError, ValueError):
        return {}


def size_of(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def sha256_of(path: Path, hashes: dict) -> str:
    """A file's sha256, kept by path, size and time in the cache so a file is read once."""

    try:
        st = path.stat()
    except OSError:
        return ""
    key = f"{path}|{st.st_size}|{int(st.st_mtime)}"
    if key in hashes:
        return hashes[key]
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1 << 22):
                digest.update(chunk)
    except OSError:
        return ""
    hashes[key] = digest.hexdigest()
    return hashes[key]


# ---------- the catalogue ----------

def need(id: str, label: str, engine: str, expected: Path, kind: str = "file", **more) -> dict:
    """One thing the studio needs. kind "file": ``names`` are the file names that may be it; kind "folder":
    ``members`` (paths inside it) prove it, found through ``anchor`` (one member's path). ``env`` is the variable
    that names exactly this place; ``link`` where a symlink goes (``expected`` by default); ``check`` a further
    test of a candidate (path -> "" or why not); ``alt`` other layouts that need a conversion first."""

    item = {"id": id, "label": label, "engine": engine, "expected": Path(expected), "kind": kind, "env": None,
            "names": (), "members": (), "anchor": "", "min_bytes": 0, "sha256": "", "optional": False, "check": None,
            "alt": [], "link": None, "auto": True, "executable": False, "download": "", "prefixes": ()}
    item.update(more)
    if kind == "file" and not item["names"]:
        item["names"] = (Path(expected).name,)
    if kind == "folder" and not item["anchor"] and item["members"]:
        item["anchor"] = item["members"][0]
    item["link"] = Path(item["link"] or expected)
    return item


def fish_checkpoint(root: Path) -> str:
    config = json_of(root / "config.json")
    kind = str(config.get("model_type") or "") + " " + " ".join(map(str, config.get("architectures") or []))
    if "fish" not in kind.lower() and not (root / "codec.pth").is_file():
        return "a model folder, but not Fish Audio's (config.json names " + (kind.strip() or "nothing") + ")"
    index = json_of(root / "model.safetensors.index.json")
    shards = set((index.get("weight_map") or {}).values())
    lost = [s for s in shards if not (root / s).is_file()]
    return f"{len(lost)} of its {len(shards)} weight files are missing" if lost else ""


def music3_original(root: Path) -> str:
    config = json_of(root / "config.json")
    return "" if config.get("model_type") == "minimax_music3" or (root / "dav.pth").is_file() else "not MiniMax-Music3's checkpoint"


def drama3_needs() -> list[dict]:
    if SPARK:
        here = prefix() / "drama3"
        binary = env_path("DRAMA3_CUDA_BIN", here / "bin" / "drama3-generate")
        model = env_path("DRAMA3_CUDA_MODEL", home() / "models" / "drama-3")
        codec = env_path("DRAMA3_CUDA_CODEC", here / "codec-v1.safetensors")
        convert = ("python3 <TensorFold drama3-cuda checkout>/tools/drama3/codec_convert.py {found_dir} " + str(codec))
        return [
            need("drama3-checkpoint", "Fish Audio Drama 3 checkpoint", "Voice (Drama 3)", model, "folder", env="DRAMA3_CUDA_MODEL",
                 members=("config.json", "model.safetensors.index.json"), anchor="model.safetensors.index.json",
                 check=fish_checkpoint, download="hf download fishaudio/drama-3"),
            need("drama3-codec", "Drama 3 codec (converted, codec-v1.safetensors)", "Voice (Drama 3)", codec, env="DRAMA3_CUDA_CODEC",
                 min_bytes=100 << 20, check=lambda p: "" if safetensors_header(p) else "not a safetensors file",
                 alt=[{"names": ("codec.pth",), "check": lambda p: fish_checkpoint(p.parent), "convert": convert,
                       "note": "the checkpoint's own codec.pth: the CUDA engine reads codec-v1.safetensors, converted from it once"}],
                 download="converted once from the checkpoint's codec.pth (codec_convert.py)"),
            need("drama3-binary", "drama3-generate (TensorFold CUDA engine)", "Voice (Drama 3)", binary, env="DRAMA3_CUDA_BIN",
                 executable=True, check=lambda p: "" if is_binary(p) else "not a program", download="README, Voice: build TensorFold's drama3-cuda branch"),
            need("drama3-lib", "libtensorfold_drama3.so", "Voice (Drama 3)",
                 env_path("DRAMA3_CUDA_LIB", binary.parent.parent / "lib" / "libtensorfold_drama3.so"), env="DRAMA3_CUDA_LIB",
                 check=lambda p: "" if is_binary(p) else "not a library", download="the same build as drama3-generate"),
        ]
    model = env_path("DRAMA3_MODEL", home() / "models/drama-3-mlx")
    return [need("drama3-mlx", "Fish Audio Drama 3 for MLX (converted)", "Voice (Drama 3)", model, "folder", env="DRAMA3_MODEL",
                 members=("config.json", "codec.safetensors"), anchor="codec.safetensors",
                 check=lambda r: "" if any(f.suffix == ".safetensors" and f.name != "codec.safetensors" for f in r.iterdir()) else "only a codec, no weights",
                 alt=[{"anchor": "model.safetensors.index.json", "check": fish_checkpoint,
                       "convert": "$DRAMA3_VENV/bin/python -I -m tensorfold.families.drama3.convert {found} " + str(model),
                       "note": "Fish Audio's own checkpoint: the MLX engine reads it converted once"}],
                 download="scripts/setup-voice.sh")]


def music3_needs() -> list[dict]:
    weights = tuple(literals(APP / "music.py").get("WEIGHTS") or ())
    default = spark_models() / "MiniMax-Music3-Comfy" if SPARK else home() / "music3-models" / "MiniMax-Music3-Comfy"
    alt = [{"anchor": "flowmatching_vae.pth", "check": music3_original, "convert": None,
            "note": ("MiniMaxAI's original checkpoint (its own layout: dav.pth, flowmatching_vae.pth, language_model/, "
                     "transformer/). Every Music route here reads Comfy-Org's three repackaged files instead, and the "
                     "studio has no converter for it: Comfy-Org/MiniMax-Music-3 (22 GB) is still needed")}]
    out = [need("music3-weights", "MiniMax-Music3 weights (Comfy-Org's three files)", "Music (MiniMax-Music3)",
                env_path("MUSIC3_MODEL", default), "folder", env="MUSIC3_MODEL", members=weights, alt=alt,
                download="oneshot-setup" + ("-spark" if SPARK else "") + ".sh --music (22 GB)")]
    if SPARK:
        out.append(need("music3-binary", "music3-generate (TensorFold CUDA engine)", "Music (MiniMax-Music3)",
                        env_path("MUSIC3_CUDA_BIN", prefix() / "music3" / "bin" / "music3-generate"), env="MUSIC3_CUDA_BIN",
                        executable=True, optional=True, check=lambda p: "" if is_binary(p) else "not a program",
                        download="bash spark/build-music3.sh"))
    else:
        out.append(need("music3-binary", "music3-generate (TensorFold Metal engine)", "Music (MiniMax-Music3)",
                        env_path("MUSIC3_METAL_BIN", prefix() / "music3" / "bin" / "music3-generate"), env="MUSIC3_METAL_BIN",
                        executable=True, optional=True, check=lambda p: "" if is_binary(p) else "not a program",
                        download="bash scripts/build-music3-metal.sh"))
    return out


# the ids of spark_generate.py's constants (its file names are read from it)
SPARK_IDS = {("FASTH3", "bf16"): ("fasth3", "FastH3 8-step transformer (bf16)", "Video (FastH3)", False),
             ("FASTH3", "int8"): ("fasth3-int8", "FastH3 8-step transformer (int8)", "Video (FastH3)", True),
             ("REF2VA", "bf16"): ("ref2va", "MiniMax H3 Ref2VA transformer (bf16)", "Video (MiniMax H3)", True),
             ("REF2VA", "int8"): ("ref2va-int8", "MiniMax H3 Ref2VA transformer (int8)", "Video (MiniMax H3)", True),
             ("REF2VA_TURBO", None): ("ref2va-turbo", "Ref2VA Turbo adapter (4 passes)", "Video (MiniMax H3)", True),
             ("H3_FL2VA", None): ("minimax-h3", "MiniMax H3 FL2VA transformer (bf16)", "Video (MiniMax H3)", True),
             ("H3_TEXT", None): ("h3-text-encoder", "MiniMax H3 text encoder (8-bit)", "Video (FastH3)", False),
             ("H3_VAE", None): ("h3-video-vae", "MiniMax H3 video decoder", "Video (FastH3)", False),
             ("H3_AUDIO_VAE", None): ("h3-audio-vae", "MiniMax H3 audio decoder", "Video (FastH3)", False),
             ("QWEN", None): ("qwen-image-2.1", "Qwen-Image-2.1-Turbo (8 steps)", "Picture (Qwen-Image-2.1)", False),
             ("QWEN_BASE", None): ("qwen-full", "Qwen-Image-2.1 base model (40 steps)", "Picture (Qwen-Image-2.1)", True),
             ("QWEN_TEXT", None): ("qwen-text-encoder", "Qwen-Image text encoder (8-bit)", "Picture (Qwen-Image-2.1)", False),
             ("QWEN_VAE", None): ("qwen-vae", "Qwen-Image decoder", "Picture (Qwen-Image-2.1)", False),
             ("FLUX3", "flux3k-fast"): ("flux3k-fast", "FLUX3-K Fast (16 steps)", "Picture (FLUX3-K)", True),
             ("FLUX3", "flux3k-full"): ("flux3k-full", "FLUX3-K Full (50 steps)", "Picture (FLUX3-K)", True),
             ("FLUX3_TEXT", None): ("flux3k-text-encoder", "FLUX3-K text encoder (Qwen3-VL-4B)", "Picture (FLUX3-K)", True),
             ("FLUX3_UPSAMPLER", None): ("flux3k-upsampler", "FLUX3-K prompt upsampler (8-bit)", "Picture (FLUX3-K)", True),
             ("FLUX3_VAE", None): ("flux3k-vae", "FLUX3-K decoder", "Picture (FLUX3-K)", True)}


def spark_comfy_needs() -> list[dict]:
    """Every ComfyUI file spark_generate.py loads, at the place oneshot-setup-spark.sh puts it under SPARK_MODELS."""

    constants = literals(PACK / "spark" / "spark_generate.py")
    where = {}  # file name -> folder/sub under SPARK_MODELS, from the installer's fetch lines
    repo_of = {}
    for repo, folder, file in spark_fetches():
        where[Path(file).name] = f"{folder}/{file}"
        repo_of[Path(file).name] = repo
    out, models = [], spark_models()
    for (name, key), (nid, label, engine, optional) in SPARK_IDS.items():
        value = constants.get(name)
        if isinstance(value, dict):
            value = value.get(key)
        if isinstance(value, (tuple, list)):
            value = value[0]
        if not isinstance(value, str):
            continue
        place = where.get(value)
        if not place:  # a variant the installer does not fetch (an 8-bit file): beside its bf16 sibling
            sibling = constants.get(name, {}).get("bf16") if isinstance(constants.get(name), dict) else None
            if sibling and sibling in where:
                place = str(Path(where[sibling]).parent / value)
        if not place:
            continue
        repo = repo_of.get(value) or repo_of.get(Path(place).name, "")
        out.append(need(nid, label, engine, models / place, min_bytes=1 << 20, optional=optional or value == constants.get("QWEN_BASE"),
                        check=lambda p: "" if safetensors_header(p) else "not a safetensors file",
                        download=f"oneshot-setup-spark.sh ({repo})" if repo else "oneshot-setup-spark.sh"))
    vae = constants.get("H3_VAE")
    for repo, folder, file in spark_fetches():  # the other video decoder SPARK_VAE may name, and BFL's system prompts
        if file.endswith("system_prompts.json"):
            out.append(need("flux3k-system-prompts", "FLUX3-K prompt upsampler's system prompts", "Picture (FLUX3-K)",
                            models / folder / file, optional=True, check=lambda p: "" if json_of(p) else "not BFL's JSON",
                            download=f"oneshot-setup-spark.sh --flux3 ({repo})"))
    if isinstance(vae, str) and "int8" in vae and vae in where:
        fp16 = vae.replace("int8_convrot", "fp16")
        out.append(need("h3-video-vae-fp16", "MiniMax H3 video decoder (fp16, SPARK_VAE)", "Video (FastH3)",
                        models / Path(where[vae]).parent / fp16, optional=True, min_bytes=1 << 20,
                        check=lambda p: "" if safetensors_header(p) else "not a safetensors file"))
    for folder, name, digest in workflow_models():
        nid = "sam3.1" if name.startswith("sam3") else "h3-latent-upscaler" if "upscaler" in name else "workflow-" + Path(name).stem
        out.append(need(nid, {"sam3.1": "SAM 3.1 (Remove a person)", "h3-latent-upscaler": "H3 latent upscaler (Upscale)"}.get(nid, name),
                        "Library actions", models / "Workflows-Comfy" / folder / name, sha256=digest, optional=True,
                        download="spark/setup-workflows.sh"))
    lib = env_path("TF_H3_LIB", prefix() / "tf-h3" / "libtf_h3.so")
    out.append(need("tf-h3-engine", "TensorFold H3 engine (libtf_h3.so)", "Video (FastH3)", lib, env="TF_H3_LIB", optional=True,
                    check=lambda p: "" if is_binary(p) else "not a library", download="bash spark/build-engine.sh"))
    return out


def mac_picture_video_needs() -> list[dict]:
    server = literals(APP / "server.py")
    setup = shell_vars(PACK / "oneshot-setup.sh")
    h3 = env_path("H3_MODEL_DIR", home() / "h3-models/MiniMax-H3")
    qwen = env_path("QWEN_MODEL_DIR", home() / "qwen-models/Qwen-Image-2.1-Turbo")
    base = env_path("QWEN_BASE_DIR", qwen.parent / "Qwen-Image-2.1")
    fasth3 = env_path("FASTH3_DIR", home() / "h3-models/FastH3-8-Step-V2")
    flux3 = env_path("FLUX3_MODEL_DIR", home() / "flux3-models")

    def qwen_kind(turbo: bool):
        def test(root: Path) -> str:
            index = json_of(root / "model_index.json")
            if "Qwen" not in str(index.get("_class_name", "")):
                return "not a Qwen-Image pipeline"
            return "" if ("sample_sigmas" in json.dumps(index)) == turbo else ("the base model, not Turbo" if turbo else "Turbo, not the base model")
        return test

    def flux_kind(full: bool):
        return lambda root: "" if (("base" in str(root).lower() or "full" in str(root).lower()) == full) else (
            "looks like FLUX3-K Fast" if full else "looks like FLUX3-K Full (base)")

    out = [
        need("qwen-image-2.1", "Qwen-Image-2.1-Turbo", "Picture (Qwen-Image-2.1)", qwen, "folder", env="QWEN_MODEL_DIR",
             members=("model_index.json", "transformer/diffusion_pytorch_model-00002-of-00002.safetensors"), anchor="model_index.json",
             check=qwen_kind(True), download="oneshot-setup.sh (33 GB)"),
        need("qwen-full", "Qwen-Image-2.1 base model (40 steps)", "Picture (Qwen-Image-2.1)", base, "folder", env="QWEN_BASE_DIR",
             members=("model_index.json", "transformer", "processor"), anchor="model_index.json", check=qwen_kind(False), optional=True,
             download="oneshot-setup.sh --image-edit"),
        need("minimax-h3", "MiniMax H3 (FL2VA: text encoder and decoders)", "Video (MiniMax H3)", h3, "folder", env="H3_MODEL_DIR",
             members=("FL2VA/text_encoder/model-00014-of-00014.safetensors", "FL2VA/audio_vae/model.safetensors",
                      "FL2VA/video_vae/config.json", "FL2VA/transformer/config.json"), download="oneshot-setup.sh (72 GB)"),
        need("ref2va", "MiniMax H3 Ref2VA transformer", "Video (MiniMax H3)", h3 / "Ref2VA", "folder",
             members=("transformer/model-00013-of-00013.safetensors", "model_index.json"),
             check=lambda r: "" if r.name == "Ref2VA" else "another partition's transformer", optional=True,
             download="oneshot-setup.sh --ref2va (62 GB)"),
        need("fasth3", "FastH3 8-Step V2 transformer", "Video (FastH3)", fasth3, "folder", env="FASTH3_DIR",
             members=("fastvideo_inference.json", "transformer/diffusion_pytorch_model-00014-of-00014.safetensors"),
             download="oneshot-setup.sh (70 GB)"),
        need("flux3k-fast", "FLUX3-K Fast", "Picture (FLUX3-K)", flux3 / "FLUX3-K-Fast", "folder",
             members=("ae.safetensors", "model.safetensors", "text_encoder/config.json"), check=flux_kind(False), optional=True,
             download="oneshot-setup.sh --flux3"),
        need("flux3k-full", "FLUX3-K Full", "Picture (FLUX3-K)", flux3 / "FLUX3-K-Full", "folder",
             members=("ae.safetensors", "model.safetensors", "text_encoder/config.json"), check=flux_kind(True), optional=True,
             download="oneshot-setup.sh --flux3"),
        need("zig-engine", "TensorFold FastH3 engine (tf-h3-dit)", "Video (FastH3)", prefix() / "zig-engine" / "tf-h3-dit",
             optional=True, check=lambda p: "" if is_binary(p) else "not a program", download="oneshot-setup.sh"),
    ]
    adapters = prefix() / "adapters"
    for nid, label, name, digest in (("h3-turbo-adapter", "MiniMax H3 Turbo adapter (LightX2V)", setup.get("H3_ADAPTER_NAME"), setup.get("H3_ADAPTER_SHA256")),
                                     ("ref2va-turbo", "Ref2VA Turbo adapter (4 passes)", server.get("REF2VA_ADAPTER") or setup.get("REF_ADAPTER_NAME"), ""),
                                     ("h3-x2-decoder", "MiniMax H3 2x video decoder", setup.get("X2_NAME"), setup.get("X2_SHA256"))):
        if name:
            out.append(need(nid, label, "Video (MiniMax H3)", adapters / name, sha256=digest or "", optional=True, min_bytes=1 << 20,
                            download="oneshot-setup.sh"))
    return out


def text_encoder_folder() -> Path:
    """Where the Create wizard's text encoder chooser lists MiniMax H3 text encoders (server.text_encoders)."""

    if SPARK:
        return spark_models() / "FastH3-Comfy" / "text_encoders"
    return env_path("H3_MODEL_DIR", home() / "h3-models/MiniMax-H3").parent / "text_encoders"


def motion_needs() -> list[dict]:
    folder = env_path("MOTION_MODELS", prefix() / "motion-models")
    return [need("motion-" + Path(name).stem.replace("pose_landmarker_", "pose-").replace("model_quantized", "depth"),
                 {"model_quantized": "Depth-Anything-small (ONNX)"}.get(Path(name).stem, "MediaPipe " + Path(name).stem.replace("_", " ")),
                 "Motion Previs", folder / name, sha256=digest, optional=True, download="scripts/setup-motion.sh")
            for name, digest in motion_models()]


def whisper_needs() -> list[dict]:
    qa = literals(APP / "qa.py")
    names = tuple(qa.get("MODELS") or ("large-v3-turbo",))
    wanted = os.environ.get("QA_WHISPER_MODEL") or names[0]
    repo = "mobiuslabsgmbh/faster-whisper-large-v3-turbo" if wanted in ("large-v3-turbo", "turbo") else f"Systran/faster-whisper-{wanted}"
    folder = "models--" + repo.replace("/", "--")
    out = []
    if not os.path.isabs(wanted):
        out.append(need("whisper-faster", f"Whisper {wanted} (faster-whisper, the QA gate's speech check)", "Speech check (Whisper)",
                        hf_caches()[0] / folder, "folder", members=("snapshots",), anchor="", optional=True, faster=folder,
                        check=lambda r: "" if any(r.glob("snapshots/*/model.bin")) else "no model.bin in its snapshots",
                        download="downloaded by faster-whisper on first use"))
    out.append(need("whisper-openai", "Whisper (openai-whisper's checkpoint)", "Speech check (Whisper)", home() / ".cache/whisper" / f"{names[0]}.pt",
                    names=tuple(f"{n}.pt" for n in names), optional=True, min_bytes=50 << 20, link_by_name=True,
                    check=lambda p: "" if head(p, 2) == b"PK" else "not a PyTorch checkpoint",
                    download="downloaded by openai-whisper on first use"))
    return out


def lora_needs() -> list[dict]:
    folder = studio_home() / "loras"
    out = []
    for recipe in lora_presets():
        name = (recipe.get("url") or "").rsplit("/", 1)[-1]
        if not name.endswith(".safetensors") or name in GENERIC:
            name = ""
        names = tuple(n for n in (name, recipe["id"] + ".safetensors") if n)
        stems = tuple(Path(n).stem for n in names if len(Path(n).stem) >= 6)
        out.append(need("lora:" + recipe["id"], recipe.get("name") or recipe["id"], "LoRA hub", folder / f"{recipe['id']}.safetensors",
                        names=names, prefixes=stems, sha256=recipe.get("sha256") or "", optional=True, lora=True,
                        check=lambda p: "" if is_lora(p) else "not a LoRA (no lora_A/lora_down tensors)",
                        download="the LoRA hub's Install (from " + (recipe.get("page") or "its author's page") + ")"))
    return out


def catalogue() -> list[dict]:
    """Everything the studio checks for before it can run, on this kind of machine."""

    items = drama3_needs() + music3_needs()
    items += spark_comfy_needs() if SPARK else mac_picture_video_needs()
    return items + motion_needs() + whisper_needs() + lora_needs()


def by_id(nid: str) -> dict | None:
    return next((n for n in catalogue() if n["id"] == nid), None)


# ---------- the cache ----------

def cache_file() -> Path:
    return studio_home() / ".discovery.json"


def load() -> dict:
    global _cache
    path = cache_file()
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return {"needs": {}, "links": {}, "hashes": {}, "extra": []}
    if _cache and _cache[0] == stamp:
        return _cache[1]
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    for key, empty in (("needs", {}), ("links", {}), ("hashes", {}), ("extra", [])):
        data.setdefault(key, empty)
    _cache = (stamp, data)
    return data


def save(data: dict) -> None:
    global _cache
    path = cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(data, indent=1))
    part.replace(path)
    _cache = None


# ---------- the walk ----------

def mounts() -> dict[str, str]:
    """Mount point -> file system type."""

    out = {}
    if SPARK:
        try:
            for line in Path("/proc/mounts").read_text().splitlines():
                parts = line.split()
                if len(parts) >= 3:
                    out[parts[1].replace("\\040", " ")] = parts[2]
        except OSError:
            pass
    elif MAC:
        try:
            said = subprocess.run(["mount"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            said = ""
        for line in said.splitlines():
            found = re.match(r".* on (.+) \(([^,)]+)", line)
            if found:
                out[found.group(1)] = found.group(2)
    return out


def roots() -> list[Path]:
    """Where the walk starts, the studio's own places first: expected folders, caches, then the machine."""

    first = [prefix(), spark_models(), home() / "models", home() / "h3-models", home() / "qwen-models", home() / "music3-models",
             home() / "flux3-models", home() / ".cache/whisper", prefix() / "ComfyUI" / "models", home() / "ComfyUI" / "models"]
    first += hf_caches()
    for item in catalogue():
        place = item["expected"].parent if item["kind"] == "file" else item["expected"]
        first.append(place.parent if item["kind"] == "folder" else place)
    rest = [home(), Path("/data"), Path("/opt"), Path("/srv"), Path("/mnt"), Path("/media"), Path("/Volumes")]
    if os.environ.get("STUDIO_SCAN_ROOTS"):  # the machine's own roots, named instead (os.pathsep between them)
        rest = [home()] + [Path(p).expanduser() for p in os.environ["STUDIO_SCAN_ROOTS"].split(os.pathsep) if p]
    out, seen, own = [], set(), str(studio_home())
    for one in first + rest:
        key = str(one)
        if key == own or key.startswith(own + os.sep):
            continue
        if key not in seen and one.is_dir() and one != Path("/"):
            seen.add(key)
            out.append(one)
    return out


def wanted() -> dict:
    """What the walk keeps: exact file names, LoRA name stems, and the folder names of faster-whisper caches."""

    names, stems = set(), set()
    for item in catalogue():
        names.update(item["names"])
        if item["anchor"]:
            names.add(Path(item["anchor"]).name)
        for alt in item["alt"]:
            names.update(alt.get("names", ()))
            if alt.get("anchor"):
                names.add(Path(alt["anchor"]).name)
        stems.update(item["prefixes"])
    names.add("codec_convert.py")
    return {"names": names, "stems": tuple(sorted(stems))}


def walk(budget: float, report=None) -> dict:
    """Walk the roots breadth-first, a share of each in turn, for at most ``budget`` seconds; return what it kept."""

    want = wanted()
    names, stems = want["names"], want["stems"]
    own = studio_home()
    table = mounts()
    network = os.environ.get("STUDIO_SCAN_NETWORK") == "1"
    starts = roots()
    skipped_mounts = sorted(m for m, kind in table.items() if (kind in NETWORK and not network) or kind in VIRTUAL)
    blocked = set(skipped_mounts) - {str(r) for r in starts}
    hits: dict[str, set] = collections.defaultdict(set)
    loras, encoders, whisper = set(), set(), set()
    seen_dirs: set = set()  # paths: the walk never follows a folder's symlink, so a path is a folder once
    starting = {str(r) for r in starts}
    queues = [collections.deque([(root, 0)]) for root in starts]
    began, dirs, files = time.time(), 0, 0
    deadline = began + budget

    def keep(path: str, name: str, parent: str) -> None:
        if name in names:
            hits[name].add(path)
        if name.endswith(".safetensors"):
            folder = os.path.basename(parent)
            if folder in LORA_FOLDERS:
                loras.add(path)
            if stems and name.startswith(stems):
                hits["~lora"].add(path)
            if "minimax_h3" in name and (folder in ("text_encoders", "clip", "H3") or name.startswith("qwen3vl")):
                encoders.add(path)

    while any(queues) and time.time() < deadline:
        for queue in queues:
            for _ in range(64):
                if not queue or time.time() >= deadline:
                    break
                folder, depth = queue.popleft()
                if folder in seen_dirs:
                    continue
                seen_dirs.add(folder)
                dirs += 1
                try:
                    entries = list(os.scandir(folder))
                except OSError:
                    continue
                if any(e.name == "pyvenv.cfg" for e in entries):
                    continue  # a Python environment
                for entry in entries:
                    name = entry.name
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if depth + 1 > DEPTH or name in SKIP_NAMES or name.endswith(SKIP_SUFFIXES) or entry.path in blocked:
                                continue
                            if entry.path == str(own) or "Time Machine" in name or entry.path in starting:
                                continue  # the studio's own folder, a backup, or another root (walked from there)
                            if name.startswith("models--") and "faster-whisper" in name:
                                whisper.add(entry.path)
                            queue.append((entry.path, depth + 1))
                        elif (name in names or name.endswith(".safetensors")) and entry.is_file():
                            # only a file of interest is asked about (a stat, for a symlink: a Hugging Face snapshot is made of them)
                            files += 1
                            keep(entry.path, name, folder)
                    except OSError:
                        continue
        if report:
            report({"dirs": dirs, "files": files, "seconds": round(time.time() - began, 1)})
    complete = not any(queues)
    # the studio's own LoRA folder is not walked (its renders are beside it): its files are the hub's own
    return {"hits": {k: sorted(v) for k, v in hits.items()}, "loras": sorted(loras), "encoders": sorted(encoders),
            "whisper": sorted(whisper), "dirs": dirs, "files": files, "complete": complete,
            "seconds": round(time.time() - began, 1), "roots": [str(r) for r in starts], "skipped_mounts": sorted(blocked)}


def seed(names: set) -> list[str]:
    """Paths an index already knows (plocate or locate on Linux, Spotlight on a Mac), checked again before use."""

    found: list[str] = []
    plain = sorted(n for n in names if re.fullmatch(r"[\w.\-]+", n))
    if not plain or os.environ.get("STUDIO_SCAN_INDEX") == "0":
        return found
    try:
        if SPARK:
            tool = shutil.which("plocate") or shutil.which("locate")
            if tool:
                pattern = "^(" + "|".join(n.replace(".", "\\.") for n in plain) + ")$"
                said = subprocess.run([tool, "-b", "-l", "20000", "-r", pattern], capture_output=True, text=True, timeout=30).stdout
                found = [line for line in said.splitlines() if os.path.basename(line) in names]
        elif MAC and shutil.which("mdfind"):
            query = " || ".join(f"kMDItemFSName == '{n}'" for n in plain)
            said = subprocess.run(["mdfind", query], capture_output=True, text=True, timeout=30).stdout
            found = [line for line in said.splitlines() if os.path.basename(line) in names]
    except (OSError, subprocess.TimeoutExpired):
        return []
    blocked = ("/.Trash", "/Trash/", "/.git/", "/node_modules/", "Backups.backupdb", "/proc/", "/sys/", ".timemachine",
               str(studio_home()) + os.sep)
    return [p for p in found if not any(b in p for b in blocked) and os.path.isfile(p)]


# ---------- proving a candidate ----------

def same(a: Path, b: Path) -> bool:
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return False


def file_candidate(item: dict, path: Path, hashes: dict) -> dict | None:
    if not path.is_file() or same(path, item["expected"]):
        return None
    size = size_of(path)
    why = []
    if size < item["min_bytes"]:
        why.append(f"only {size} bytes")
    if item["executable"] and not os.access(path, os.X_OK):
        why.append("not executable")
    if not why and item["check"]:
        said = item["check"](path)
        if said:
            why.append(said)
    if why:
        return None
    verified, proof = True, "name" + (" and file type" if item["check"] else "")
    named = path.name in item["names"]
    if item["sha256"] and size < (8 << 30):
        if sha256_of(path, hashes) == item["sha256"]:
            proof = "sha256"
        elif named:
            verified, proof = False, "same name, different sha256 (another build or version of this file)"
        else:
            verified, proof = False, "a file whose name starts like this one's, with another sha256"
    elif not named:
        verified, proof = False, "a file whose name starts like this one's"
    return {"path": str(path), "shown": shown(path), "size": size, "verified": verified, "proof": proof}


def folder_candidate(item: dict, root: Path) -> dict | None:
    if not root.is_dir() or same(root, item["expected"]):
        return None
    lost = [m for m in item["members"] if not (root / m).exists()]
    if lost:
        return None
    if item["check"]:
        said = item["check"](root)
        if said:
            return None
    return {"path": str(root), "shown": shown(root), "size": 0, "verified": True, "proof": "folder members" + (" and contents" if item["check"] else "")}


def anchored(path: str, anchor: str) -> Path | None:
    """The root a found file belongs to when the file is ``anchor`` inside it."""

    parts = Path(anchor).parts
    found = Path(path)
    if tuple(found.parts[-len(parts):]) != parts:
        return None
    return found.parents[len(parts) - 1]


def candidates(item: dict, kept: dict, hashes: dict) -> tuple[list[dict], list[dict]]:
    """(candidates that can be linked, ones that need a conversion first) of one need."""

    hits = kept["hits"]
    good, convert = [], []
    if item["kind"] == "file":
        paths = set()
        for name in item["names"]:
            paths.update(hits.get(name, ()))
        for path in hits.get("~lora", ()) if item["prefixes"] else ():
            stem = Path(path).name[:-len(".safetensors")]
            if any(stem.startswith(s) and LORA_SUFFIX.match(stem[len(s):]) for s in item["prefixes"]):
                paths.add(path)
        for path in sorted(paths):
            found = file_candidate(item, Path(path), hashes)
            if found:
                good.append(found)
    elif item.get("faster"):
        for path in kept.get("whisper", ()):
            if os.path.basename(path) == item["faster"]:
                found = folder_candidate(item, Path(path))
                if found:
                    good.append(found)
    else:
        roots_seen = set()
        for path in hits.get(Path(item["anchor"]).name, ()):
            root = anchored(path, item["anchor"])
            if root is not None and str(root) not in roots_seen:
                roots_seen.add(str(root))
                found = folder_candidate(item, root)
                if found:
                    good.append(found)
    for alt in item["alt"]:
        names = alt.get("names") or ((Path(alt["anchor"]).name,) if alt.get("anchor") else ())
        for name in names:
            for path in hits.get(name, ()):
                target = Path(path) if alt.get("names") else anchored(path, alt["anchor"])
                if target is None or alt["check"](target):
                    continue
                command = alt.get("convert")
                if command:
                    command = command.replace("{found_dir}", str(Path(path).parent if alt.get("names") else target)).replace("{found}", str(target))
                convert.append({"path": str(target), "shown": shown(target), "verified": False, "convert": command,
                                "note": alt.get("note", ""), "proof": "another layout"})
    # identical copies (the same sha256) are one candidate: the first is kept
    if item["sha256"]:
        exact = [c for c in good if c["proof"] == "sha256"]
        if len(exact) > 1:
            good = [exact[0]] + [c for c in good if c["proof"] != "sha256"]
            good[0] = {**good[0], "copies": [c["shown"] for c in exact[1:]]}
    return good, convert


def dynamic_needs(kept: dict, items: list[dict], hashes: dict) -> list[dict]:
    """Things found that no need names: other MiniMax H3 text encoders (offered by the Create wizard's chooser once
    linked into its folder) and LoRAs the hub does not list."""

    out = []
    known = set()
    for item in items:
        known.add(os.path.realpath(item["expected"]))
    te_folder = text_encoder_folder()
    regular = literals(PACK / "spark" / "spark_generate.py").get("H3_TEXT") or ""
    names_known = {n for item in items for n in item["names"]}
    taken: set = set()
    for path in kept.get("encoders", ()):
        name = os.path.basename(path)
        if name == regular or name in names_known or name in taken or os.path.realpath(path) in known:
            continue
        if (te_folder / name).exists():  # the chooser lists it already
            continue
        taken.add(name)
        if not safetensors_header(Path(path)) or is_lora(Path(path)) or size_of(Path(path)) < (1 << 30):
            continue
        out.append(need("h3-text-encoder:" + name, "Another MiniMax H3 text encoder: " + name, "Video (FastH3)", te_folder / name,
                        optional=True, extra=True, source=path, download="found on this machine: the Create wizard's text encoder chooser offers it once linked"))
    preset_hits = {os.path.realpath(p) for p in kept["hits"].get("~lora", ())}
    for item in items:
        if item.get("lora"):
            for name in item["names"]:
                preset_hits.update(os.path.realpath(p) for p in kept["hits"].get(name, ()))
    comfy_loras = prefix() / "ComfyUI" / "models" / "loras"
    seen_names = set()
    for path in kept.get("loras", ()):
        real = os.path.realpath(path)
        name = os.path.basename(path)
        if real in preset_hits or real in known or name.startswith("hub_") or name in names_known or name in seen_names:
            continue
        if str(studio_home()) in real:
            continue
        if not is_lora(Path(path)):
            continue
        seen_names.add(name)
        link = comfy_loras / name if SPARK and comfy_loras.parent.is_dir() else None
        out.append(need("lora-unlisted:" + name, "An unlisted LoRA: " + name, "LoRAs (not in the hub)", link or Path(path), optional=True,
                        extra=True, auto=False, unlisted=True, linkable=bool(link), source=path,
                        download="ComfyUI lists it once linked; the LoRA hub adds it under My LoRAs" if link else "add it in the LoRA hub under My LoRAs"))
    return out


def scan(link: bool = True, budget: float | None = None) -> dict:
    """One scan of the machine: walk, seed from an index, prove every candidate, keep it, and link what is
    unambiguous. Returns the cache. A second call while one runs returns at once with ``busy``."""

    if not _scan_lock.acquire(blocking=False):
        return {**load(), "busy": True}
    try:
        _progress.clear()
        _progress.update({"started": time.time(), "dirs": 0, "files": 0})
        if sys.platform.startswith("linux") and threading.current_thread() is not threading.main_thread():
            try:
                os.nice(10)  # Linux: this thread only
            except (OSError, AttributeError):
                pass
        seconds = budget if budget is not None else float(os.environ.get("STUDIO_SCAN_SECONDS", 120))
        kept = walk(seconds, _progress.update)
        names = wanted()["names"]
        for path in seed(names):
            name = os.path.basename(path)
            if path not in kept["hits"].setdefault(name, []):
                kept["hits"][name].append(path)
        hashes = dict(load().get("hashes") or {})
        items = catalogue()
        extra = dynamic_needs(kept, items, hashes)
        result = {}
        for item in items + extra:
            if item.get("extra"):
                source = Path(item["source"])
                good = [{"path": str(source), "shown": shown(source), "size": size_of(source), "verified": True, "proof": "safetensors header"}]
                result[item["id"]] = {"candidates": good, "conversions": []}
                continue
            good, convert = candidates(item, kept, hashes)
            result[item["id"]] = {"candidates": good, "conversions": convert}
        tools = kept["hits"].get("codec_convert.py", [])
        with _lock:
            data = load()
            data.update({"needs": result, "hashes": hashes,
                         "extra": [{k: (str(v) if isinstance(v, Path) else list(v) if isinstance(v, tuple) else v)
                                    for k, v in e.items() if k not in ("check", "alt")} for e in extra],
                         "tools": {"codec_convert.py": tools[:5]},
                         "scan": {"at": time.time(), "seconds": kept["seconds"], "complete": kept["complete"], "dirs": kept["dirs"],
                                  "files": kept["files"], "roots": [shown(r) for r in kept["roots"]],
                                  "skipped_mounts": [shown(m) for m in kept["skipped_mounts"]]}})
            save(data)
        if link and os.environ.get("STUDIO_DISCOVER_AUTO", "1") != "0":
            auto_link()
        return load()
    finally:
        _progress.clear()
        _scan_lock.release()


def scanning() -> bool:
    return _scan_lock.locked()


# ---------- linking ----------

def all_needs() -> list[dict]:
    items = catalogue()
    taken = {n["id"] for n in items}
    for extra in load().get("extra", []):
        if extra.get("id") in taken:
            continue
        item = need(extra["id"], extra["label"], extra["engine"], Path(extra["expected"]),
                    **{k: v for k, v in extra.items() if k in ("optional", "extra", "auto", "unlisted", "linkable", "source", "download")})
        items.append(item)
    return items


def find_need(nid: str) -> dict | None:
    return next((n for n in all_needs() if n["id"] == nid), None)


def present(item: dict) -> bool:
    place = item["expected"]
    if item.get("unlisted"):
        return bool(item.get("linkable")) and place.exists()
    if item.get("link_by_name"):
        return any((place.parent / n).is_file() for n in item["names"])
    if item["kind"] == "folder":
        return place.is_dir() and all((place / m).exists() for m in item["members"]) if item["members"] else place.is_dir()
    return place.is_file() and (not item["executable"] or os.access(place, os.X_OK))


def use(nid: str, path: str) -> dict:
    """Link ``path`` (one of the scan's candidates for this need) where the studio looks for it."""

    item = find_need(nid)
    if item is None:
        raise LookupError(f"nothing called {nid} is needed")
    data = load()
    known = data["needs"].get(nid) or {}
    pick = next((c for c in known.get("candidates", []) if c.get("path") == path), None)
    if pick is None:
        conv = next((c for c in known.get("conversions", []) if c.get("path") == path), None)
        if conv:
            raise ValueError("that one needs converting first" + (": " + conv["convert"] if conv.get("convert") else ": " + conv.get("note", "")))
        raise ValueError("that path is not one this machine's scan found for " + item["label"])
    found = Path(path)
    if not found.exists():
        raise ValueError(f"{shown(found)} is not there any more: scan again")
    if item.get("unlisted") and not item.get("linkable"):
        raise ValueError("there is no folder to link an unlisted LoRA into here: add it in the LoRA hub under My LoRAs")
    place = item["link"].parent / found.name if item.get("link_by_name") else item["link"]
    mine = (data.get("links") or {}).get(nid)
    if place.is_symlink() or place.exists():
        if mine and mine.get("how") == "symlink" and place.is_symlink() and os.readlink(place) == mine.get("path"):
            os.unlink(place)  # the studio's own link, replaced
        elif same(place, found):
            pass
        else:
            raise ValueError(f"there is already something at {shown(place)}; it is left as it is")
    how = "symlink"
    try:
        if not same(place, found):
            place.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(str(found), str(place), target_is_directory=found.is_dir())
    except OSError:
        how = "record"
    record = {"path": str(found), "link": str(place), "how": how, "at": time.time()}
    if how == "record" and item["env"] and not (os.environ.get(item["env"]) and _set_env.get(item["env"]) != os.environ.get(item["env"])):
        os.environ[item["env"]] = str(found)
        _set_env[item["env"]] = str(found)
        record["env"] = item["env"]
    with _lock:
        data = load()
        data["links"] = {**data.get("links", {}), nid: record}
        save(data)
    return record


def unlink(nid: str) -> dict:
    """Undo a link the studio made: its own symlink only (a symlink of the user's is never touched)."""

    with _lock:
        data = load()
        record = (data.get("links") or {}).get(nid)
        if not record:
            raise LookupError("the studio made no link for this")
        place = Path(record["link"])
        if record.get("how") == "symlink" and place.is_symlink() and os.readlink(place) == record["path"]:
            os.unlink(place)
        env = record.get("env")
        if env and _set_env.get(env) == os.environ.get(env):
            os.environ.pop(env, None)
            _set_env.pop(env, None)
        data["links"] = {k: v for k, v in data["links"].items() if k != nid}
        save(data)
    return {"unlinked": nid}


def apply_links() -> None:
    """At start: a recorded (not symlinked) link's variable set again for this process."""

    for nid, record in (load().get("links") or {}).items():
        env = record.get("env")
        if record.get("how") == "record" and env and not os.environ.get(env) and Path(record["path"]).exists():
            os.environ[env] = record["path"]
            _set_env[env] = record["path"]


def auto_link(places: set | None = None) -> list[str]:
    """Link every need that is missing and has exactly one verified candidate (and no other); with ``places``, only
    the needs whose expected place is one of them or inside one."""

    done = []
    data = load()
    for item in all_needs():
        if not item["auto"] or present(item) or item["id"] in (data.get("links") or {}):
            continue
        if places is not None and not under(item["link"], places):
            continue
        found = [c for c in (data["needs"].get(item["id"]) or {}).get("candidates", []) if c.get("verified") and c.get("path")]
        if len(found) == 1:  # exactly one proved: others offered (another sha256, a similar name) wait for a choice
            try:
                use(item["id"], found[0]["path"])
                done.append(item["id"])
            except (OSError, ValueError, LookupError):
                continue
    return done


def under(place: Path, places: set) -> bool:
    """Whether ``place`` is one of ``places``, inside one, or holds one (a folder need, asked for by a file in it)."""

    text = os.path.abspath(str(place)).rstrip("/")
    return any(text == p.rstrip("/") or text.startswith(p.rstrip("/") + "/") or p.startswith(text + "/") for p in places)


def resolve(nid: str) -> Path | None:
    """Where a need is on this machine, for a loader: the variable that names it, then the studio's link, then the
    expected place; None when none of them is there. Reads the cache only: no scan, no process."""

    item = find_need(nid)
    if item is None:
        return None
    if item["env"] and os.environ.get(item["env"]) and _set_env.get(item["env"]) != os.environ[item["env"]]:
        given = Path(os.environ[item["env"]]).expanduser()
        if given.exists():
            return given
    record = (load().get("links") or {}).get(nid)
    if record:
        for place in (Path(record["link"]), Path(record["path"])):
            if place.exists():
                return place
    return item["expected"] if item["expected"].exists() else None


def hint(nid: str) -> str:
    """What a "not installed" message adds: where the scan found it, or when it looked and found nothing."""

    item = find_need(nid)
    data = load()
    known = data["needs"].get(nid)
    if item is None or known is None or not data.get("scan"):
        return ""
    good, convert = known.get("candidates", []), known.get("conversions", [])
    if good:
        return f" Not at {shown(item['expected'])}; found at {good[0]['shown']}" + (f" (and {len(good) - 1} more)" if len(good) > 1 else "") + \
            " → Use it in Machine → Models & weights."
    if convert:
        c = convert[0]
        return f" Found {c['shown']}, which needs converting first: " + (c.get("convert") or c.get("note") or "")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(data["scan"]["at"]))
    return f" Not found on this machine after a scan at {when}" + (f"; download: {item['download']}" if item.get("download") else "") + "."


# ---------- the page's view ----------

def state() -> dict:
    data = load()
    links = data.get("links") or {}
    out = []
    for item in all_needs():
        known = data["needs"].get(item["id"]) or {}
        record = links.get(item["id"])
        good, convert = known.get("candidates", []), known.get("conversions", [])
        if item.get("unlisted"):
            status = "expected" if record else "found"
        elif present(item) or (record and Path(record["path"]).exists()):
            status = "expected"
        elif good:
            status = "found"
        elif convert:
            status = "needs-conversion"
        elif scanning() and not data.get("scan"):
            status = "scanning"
        else:
            status = "missing"
        out.append({"id": item["id"], "label": item["label"], "engine": item["engine"], "optional": item["optional"],
                    "state": status, "expected": shown(item["expected"]) if not item.get("unlisted") or item.get("linkable") else "",
                    "env": item["env"], "candidates": good, "conversions": convert, "download": item.get("download", ""),
                    "link": {**record, "shown": shown(record["path"]), "link_shown": shown(record["link"])} if record else None,
                    "linked_by_studio": bool(record), "unlisted": bool(item.get("unlisted")), "linkable": item.get("linkable", True),
                    "install": install_plan(item)})
    scan_info = data.get("scan")
    try:
        import installs
        installing = installs.public()
    except ImportError:
        installing = {"need": None, "state": "idle"}
    return {"needs": out, "scanning": scanning(), "progress": dict(_progress), "last_scan": scan_info, "installing": installing,
            "tools": {k: [shown(p) for p in v] for k, v in (data.get("tools") or {}).items()},
            "platform": "spark" if SPARK else "mac" if MAC else sys.platform, "note": NOTE}


def install_plan(item: dict) -> dict | None:
    """How Install would add this need (installs.py), as the page shows it; None when nothing can run."""

    try:
        import installs
        plan = installs.plan_of(item)
    except ImportError:
        return None
    return {k: plan.get(k) for k in ("how", "shown", "size_gb", "lora")}


def start_scan() -> bool:
    if scanning():
        return False
    threading.Thread(target=scan, name="discover-scan", daemon=True).start()
    return True


def attach(home_folder: Path, prefix_folder: Path):
    """The API (GET /api/discover, POST /api/discover/scan|use|unlink) and the scan after start."""

    from fastapi import APIRouter, HTTPException, Request

    import suite

    globals()["Request"] = Request  # the handlers' annotations are read from this module (annotations are strings here)
    os.environ.setdefault("STUDIO_HOME", str(home_folder))
    os.environ.setdefault("PREFIX", str(prefix_folder))
    apply_links()
    router = APIRouter()

    @router.get("/api/discover")
    def listing() -> dict:
        return state()

    @router.post("/api/discover/scan")
    def again(request: Request) -> dict:
        suite.local(request, json_body=True)
        return {"started": start_scan(), **state()}

    @router.post("/api/discover/use")
    async def link(request: Request) -> dict:
        suite.local(request, json_body=True)
        body = await request.json()
        try:
            record = use(str(body.get("need") or ""), str(body.get("path") or ""))
        except LookupError as error:
            raise HTTPException(404, str(error))
        except (ValueError, OSError) as error:
            raise HTTPException(400, str(error))
        return {"linked": {**record, "shown": shown(record["path"])}, **state()}

    @router.post("/api/discover/install")
    async def install(request: Request) -> dict:
        """Install a need this machine does not have: its own installer, one at a time (installs.py)."""

        suite.local(request, json_body=True)
        import installs
        body = await request.json()
        item = find_need(str(body.get("need") or ""))
        if item is None:
            raise HTTPException(404, "no such model or weight")
        if present(item):
            return {"installing": installs.public(), **state()}
        try:
            installs.start(item)
        except ValueError as error:
            raise HTTPException(400, str(error.args[0]))
        return state()

    @router.get("/api/discover/install")
    def installing() -> dict:
        import installs
        return installs.public()

    @router.post("/api/discover/install/cancel")
    async def stop_install(request: Request) -> dict:
        suite.local(request, json_body=True)
        import installs
        installs.cancel()
        return state()

    @router.post("/api/discover/link-all")
    async def link_all(request: Request) -> dict:
        """Every need found on this machine with exactly one proved copy, linked where the studio looks."""

        suite.local(request, json_body=True)
        done = auto_link()
        return {"linked": done, **state()}

    @router.post("/api/discover/unlink")
    async def undo(request: Request) -> dict:
        suite.local(request, json_body=True)
        body = await request.json()
        try:
            unlink(str(body.get("need") or ""))
        except LookupError as error:
            raise HTTPException(404, str(error))
        except OSError as error:
            raise HTTPException(400, str(error))
        return state()

    delay = float(os.environ.get("STUDIO_DISCOVER_DELAY", 20))
    if delay >= 0:
        timer = threading.Timer(delay, start_scan)
        timer.daemon = True
        timer.start()
    return router


# ---------- from a shell ----------

def table(view: dict, only: set | None = None) -> str:
    lines = []
    for row in view["needs"]:
        if only is not None and row["id"] not in only:
            continue
        where = row["link"]["shown"] if row["link"] else row["candidates"][0]["shown"] if row["candidates"] else \
            row["conversions"][0]["shown"] if row["conversions"] else ""
        lines.append(f"{row['state']:<17} {row['id']:<34} {row['expected']}" + (f"  <- {where}" if where and row["state"] != "missing" else ""))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="discover.py", description="Find what the studio needs on this machine, and link it.")
    parser.add_argument("--scan", action="store_true", help="scan the machine (or use a scan younger than --max-age)")
    parser.add_argument("--link", action="store_true", help="link what was found unambiguously (a symlink where the studio looks)")
    parser.add_argument("--max-age", type=float, default=0, help="seconds a kept scan stays good (0: always scan)")
    parser.add_argument("--seconds", type=float, default=None, help="the scan's time limit (STUDIO_SCAN_SECONDS, 120)")
    parser.add_argument("--paths", nargs="*", default=None, help="only these expected places: print a line for each one linked")
    parser.add_argument("--json", action="store_true", help="print the state as JSON")
    args = parser.parse_args(argv)
    if sys.platform.startswith("linux") and __name__ == "__main__":
        try:
            os.nice(10)
        except (OSError, AttributeError):
            pass
    before = set((load().get("links") or {}))
    last = (load().get("scan") or {}).get("at", 0)
    if args.scan and (not args.max_age or time.time() - last > args.max_age):
        scan(link=False, budget=args.seconds)
    places = {os.path.abspath(os.path.expanduser(p)) for p in args.paths} if args.paths is not None else None
    if args.link:
        auto_link(places)
    view = state()
    if places is not None:  # the installers' gates: a line for each place just linked
        for row in view["needs"]:
            if row["link"] and row["id"] not in before and under(Path(row["link"]["link"]), places):
                print(f"{row['link']['link_shown']} -> {row['link']['shown']}")
        return 0
    if args.json:
        print(json.dumps(view, indent=1, default=str))
    else:
        print(table(view))
        scanned = view.get("last_scan") or {}
        if scanned:
            print(f"\nscanned {scanned.get('dirs')} folders in {scanned.get('seconds')} s"
                  + ("" if scanned.get("complete") else " (stopped at the time limit)") + f". {NOTE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
