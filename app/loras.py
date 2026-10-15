"""Keys and Wasserman LoRA Hub: a library of LoRAs, each filed under the model it was trained on.

The hub holds LoRAs for MiniMax H3 (a clip), Qwen-Image (a new picture, Edit a picture) and, stored as they are, for models the
Studio has no engine for yet (lora_kinds.py reads which model a file is for from its header). A job is offered, and merges,
only the LoRAs of its own model.

A recipe says where a LoRA's file comes from and how it is used: its trigger word, the phrases that pick one of its
looks, the strength it was made for. The presets that ship with the pack are in lora-presets.json beside this file;
recipes a user adds are kept in ~/TensorFoldStudio/loras/hub.json. No weights ship with the pack: a LoRA's file is
downloaded from its author's own page when the user installs it, under that file's own licence.

A preset also says where it sits in the hub (`category`, one or two of GROUPS), what kind of clip it is for (`needs`)
and whether the Studio can use it (`status`: ready, gated behind its author's approval, or unsupported with `why`).
`python app/loras.py install camera,style` downloads the ready presets of those groups (the one-shot installers'
`--loras`).

A job carries `loras: [{"id", "strength", "style"}]`. On a Mac a MiniMax H3 file is rewritten once into the layout the MLX
engine merges at load (scripts/convert-lora.py); on a DGX Spark ComfyUI merges the file as it is. A picture LoRA is read as
its author wrote it on both.
"""

from __future__ import annotations

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
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException

import lora_flows
import lora_kinds
import lora_layout

PRESETS = Path(__file__).with_name("lora-presets.json")
FIELDS = ("name", "author", "url", "page", "sha256", "trigger", "styles", "strength", "range", "base", "kind", "licence", "notes")
LIMIT = 4  # LoRAs on one clip
# the hub's sections, in the order they are shown (lora-groups.json, which the essential bundle's folders follow)
SECTIONS = json.loads(Path(__file__).with_name("lora-groups.json").read_text())
GROUPS = tuple((g["id"], g["name"]) for g in SECTIONS) + (("mine", "My LoRAs"),)
FOLDERS = {g["id"]: g["folder"] for g in SECTIONS}


# Adapted builds hosted for the Studio's two engines (lora-host.json lists them). LORA_HOST_BASE is the address of that
# hosted folder; it is empty until one exists, and then an install goes: the local bundle, the hosted build, the
# author's own file (rewritten here on a Mac).
HOSTED = json.loads(Path(__file__).with_name("lora-host.json").read_text()).get("loras", {})


def hosted(lora_id: str, platform: str) -> dict | None:
    base = os.environ.get("LORA_HOST_BASE", "").strip().rstrip("/")
    build = (HOSTED.get(lora_id) or {}).get(platform)
    return {"url": f"{base}/{build['file']}", "sha256": build["sha256"]} if base and build else None


def token() -> str:
    """The user's own Hugging Face token, for a file its author gates. Sent to huggingface.co only."""

    found = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or ""
    saved = Path.home() / ".cache/huggingface/token"
    return found.strip() or (saved.read_text().strip() if saved.is_file() else "")


def check(path: Path, target: str = "video") -> int:
    """The number of adapted modules, after making sure the file is a low-rank LoRA for the model it is said to be for:
    `video` (or `h3`) MiniMax H3's transformer, `image` (or `qwen-image`) Qwen-Image's, or any other kind of lora_kinds.py,
    in any of the layouts LoRAs are published in. A file for another model is refused by name: "it is a Flux.2 LoRA"."""

    wanted = {"video": "h3", "image": "qwen-image"}.get(target, target)
    found = lora_kinds.detect(path)
    if found["kind"] != wanted:
        what = lora_kinds.label(found["kind"]) + (f" ({found['family']})" if found["family"] else "")
        raise ValueError(f"not a LoRA for {lora_kinds.label(wanted)}'s transformer: it is a {what} LoRA")
    return found["modules"]


def rewritten(path: Path) -> bool:
    """On a DGX Spark: whether ComfyUI needs this file rewritten. A PEFT save (DiffSynth-Studio's layout) matches no
    key as it is, and a pair on an adaln projection was made on weights ComfyUI's pruned checkpoints do not have."""

    names = list(lora_layout.header(path)[0])
    return lora_layout.layout(names) == "peft" or any(lora_layout.ADALN.search(name) for name in lora_layout.modules(names))


def adaln_basis(folder: Path):
    """(basis, offset) that carries an adaln pair onto the pruned Ref2VA checkpoint (lora_layout.curve_basis), kept
    in the hub's folder once worked out. It comes from a LoRA bundle that holds it, else from the checkpoint's own
    table and MiniMax H3's time embedder: that of the model on this machine, or the first 64 MB of MiniMax's file."""

    import numpy as np

    kept = folder / "ref2va-adaln-basis.npy"
    where = bundle()
    for place in (kept, Path(where).expanduser() / kept.name if where and not where.startswith(("http://", "https://")) else None):
        if place is not None and place.is_file():
            both = np.load(place)
            return both[:-1], both[-1]
    models = Path(os.environ.get("SPARK_MODELS", str(Path.home() / "spark-studio-models")))
    checkpoint = next(iter(sorted(models.glob("**/minimax_h3_ref2va_pruned_bf16.safetensors"))), None)
    table = lora_layout.curve_table(checkpoint) if checkpoint else None
    if table is None:
        return None, None  # no pruned Ref2VA here: a checkpoint with the full time embedder takes the pair as it is
    local = Path(os.environ.get("H3_MODEL_DIR", str(Path.home() / "h3-models/MiniMax-H3"))) / "Ref2VA/transformer/model-00001-of-00013.safetensors"
    if not local.is_file() and os.environ.get("LORA_OFFLINE") == "1":
        raise RuntimeError("this LoRA adapts the model's time layers, and moving it onto the DGX Spark's checkpoint takes 64 MB of "
                           "MiniMax H3 from MiniMax's page once (or ref2va-adaln-basis.npy in the LoRA bundle); LORA_OFFLINE=1 keeps this off the network")
    basis, offset, error = lora_layout.curve_basis(table, lora_layout.embedder(local if local.is_file() else None))
    if error > 1e-3:
        raise RuntimeError(f"the checkpoint's time table does not follow MiniMax H3's time embedder (fit error {error:.1e}): the LoRA is not moved onto it")
    np.save(kept, np.concatenate([basis, offset[None]], axis=0))
    return basis, offset


def bundle() -> str:
    """Where a LoRA bundle is, if there is one: LORA_BUNDLE (a folder, or the base address of a copy of one), else
    the folder lora-bundle in the studio's home. scripts/lora-bundle.py builds one."""

    named = os.environ.get("LORA_BUNDLE", "").strip()
    if named:
        return named.rstrip("/")
    local = Path(os.environ.get("STUDIO_HOME", str(Path.home() / "TensorFoldStudio"))) / "lora-bundle"
    return str(local) if local.is_dir() else ""


def bundled(recipe: dict) -> Path | None:
    """The recipe's file in a bundle folder on this machine, when it is there."""

    where = bundle()
    if not where or where.startswith(("http://", "https://")) or not recipe.get("url"):
        return None
    name, first = recipe["url"].rsplit("/", 1)[-1], (recipe.get("category") or ["mine"])[0]
    for found in (Path(where).expanduser() / FOLDERS.get(first, "") / recipe["id"] / name, Path(where).expanduser() / recipe["id"] / name):
        if found.is_file():
            return found
    return None


def download(recipe: dict, target: Path, state: dict) -> None:
    """The recipe's file into `target`, checked against the recipe's sha256 and layout: from a LoRA bundle when one
    holds it (no network), else from its author's page."""

    part = target.with_name(target.name + ".part")
    headers = {"User-Agent": "TensorFoldStudio"}
    if recipe.get("status") == "gated" and "huggingface.co/" in recipe["url"] and token():
        headers["Authorization"] = "Bearer " + token()
    try:
        digest = hashlib.sha256()
        local, where = bundled(recipe), bundle()
        try:
            if local:
                reply = open(local, "rb")
                state["of"] = local.stat().st_size
            else:
                reply = None
                if where.startswith(("http://", "https://")):
                    try:
                        mirror = f"{where}/{FOLDERS.get((recipe.get('category') or ['mine'])[0], '')}/{recipe['id']}/{recipe['url'].rsplit('/', 1)[-1]}"
                        reply = urllib.request.urlopen(urllib.request.Request(mirror, headers={"User-Agent": "TensorFoldStudio"}), timeout=60)
                    except (urllib.error.URLError, OSError):
                        reply = None  # not in that bundle: the author's page is next
                if reply is None and hosted(recipe["id"], "spark") and os.environ.get("LORA_OFFLINE") != "1":
                    try:  # the hosted copy of the author's file (the same bytes, checked below)
                        reply = urllib.request.urlopen(urllib.request.Request(hosted(recipe["id"], "spark")["url"], headers={"User-Agent": "TensorFoldStudio"}), timeout=60)
                    except (urllib.error.URLError, OSError):
                        reply = None
                if reply is None:
                    if os.environ.get("LORA_OFFLINE") == "1":
                        raise RuntimeError("not in the LoRA bundle, and LORA_OFFLINE=1 keeps this off the network")
                    reply = urllib.request.urlopen(urllib.request.Request(recipe["url"], headers=headers), timeout=60)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise RuntimeError("its author gates this file: " + (recipe.get("why") or "ask for access on the LoRA's page")) from None
            raise
        with reply, open(part, "wb") as out:
            if not local:
                state["of"] = int(reply.headers.get("Content-Length") or 0)
            while chunk := reply.read(1 << 20):
                out.write(chunk)
                digest.update(chunk)
                state["done"] += len(chunk)
        if recipe.get("sha256") and digest.hexdigest() != recipe["sha256"]:
            raise RuntimeError("the downloaded file does not match the recipe's sha256")
        check(part, recipe.get("kind") or lora_kinds.kind_of_target(recipe.get("target")))
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)


def convert(source: Path, ready: Path, pack: Path) -> None:
    """On a Mac: the author's file rewritten once into the layout the MLX engine merges at load."""

    model = os.environ.get("H3_MODEL_DIR", str(Path.home() / "h3-models/MiniMax-H3"))
    part = ready.with_name(ready.name.replace(".mlx.safetensors", ".part.safetensors"))  # the writer adds .safetensors to any other name
    done = subprocess.run([sys.executable, str(pack / "scripts/convert-lora.py"), str(source), str(part), model],
                          capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError((done.stderr or done.stdout).strip()[-300:])
    part.replace(ready)


def attach(home: Path, pack: Path, spark: bool):
    folder = home / "loras"
    folder.mkdir(parents=True, exist_ok=True)
    book = folder / "hub.json"
    router = APIRouter()
    busy: dict[str, dict] = {}  # id -> {"stage", "done", "of"} while a file is being fetched
    lock = threading.Lock()

    def added() -> list[dict]:
        try:
            return json.loads(book.read_text())
        except (OSError, ValueError):
            return []

    def shaped(recipe: dict) -> dict:
        """A recipe with the model it is for (`kind`, from the file's header or the preset), the jobs that merge it on this
        machine, and, for a model the Studio has no engine for, the status `stored`."""

        kind = recipe.get("kind") or lora_kinds.kind_of_target(recipe.get("target"))
        kind = kind if kind in lora_kinds.KINDS else "other"
        out = {**recipe, "kind": kind, "base": lora_kinds.label(kind), "target": lora_kinds.target_of(kind)}
        if kind != "h3" and recipe.get("status", "ready") == "ready" and not lora_kinds.KINDS[kind]["jobs"]:
            out.update(status="stored", why=lora_kinds.why_not(kind, "video", spark))
        out["jobs"] = list(lora_kinds.jobs_for(kind, spark)) if out.get("status", "ready") in ("ready", "gated") else []
        out["jobs_why"] = {job: lora_kinds.why_not(kind, job, spark) for job in lora_kinds.KINDS[kind]["jobs"] if job not in out["jobs"] and out.get("status", "ready") in ("ready", "gated")}
        return out

    def recipes() -> list[dict]:
        built = [{"status": "ready", "category": ["mine"], **r, "builtin": True} for r in json.loads(PRESETS.read_text())]
        # a LoRA that belongs to a Library action (actions.py) is installed like the others and used by that action,
        # not on a clip; where the action does not run (a Mac), its card says why
        built = [r if r.get("status") != "action" or spark or "mac" in r.get("platforms", ()) else {**r, "status": "unsupported", "why": r.get("mac", r.get("why", ""))} for r in built]
        taken = {r["id"] for r in built}
        where = bundle()
        if where and not where.startswith(("http://", "https://")):  # a bundle newer than this pack brings its own cards
            try:
                extra = json.loads((Path(where).expanduser() / "bundle.json").read_text()).get("loras", [])
            except (OSError, ValueError, AttributeError):
                extra = []
            built += [{"status": "ready", "needs": "text", **r, "builtin": True} for r in extra if r.get("id") and r.get("url") and r["id"] not in taken]
            taken = {r["id"] for r in built}
        return [shaped(r) for r in built + [{"status": "ready", "category": ["mine"], "needs": "text", **r, "builtin": False} for r in added() if r.get("id") not in taken]]

    def find(lora_id: str) -> dict:
        found = next((r for r in recipes() if r["id"] == lora_id), None)
        if found is None:
            raise HTTPException(404, f"no LoRA {lora_id} in the hub")
        return found

    def source(lora_id: str) -> Path:
        return folder / f"{lora_id}.safetensors"

    def kind_of(lora_id: str) -> str:
        """The model this LoRA is for (lora_kinds.py): "h3" unless the recipe says another."""

        return next((r["kind"] for r in recipes() if r["id"] == lora_id), "h3")

    def ready(lora_id: str) -> Path:
        """The file a render reads: the author's on a Spark, the rewritten one on a Mac, for a MiniMax H3 LoRA. A LoRA for any
        other model is read as its author wrote it on both."""

        if kind_of(lora_id) != "h3":
            return source(lora_id)
        if not spark:
            return folder / f"{lora_id}.mlx.safetensors"
        given, made = source(lora_id), folder / f"{lora_id}.comfy.safetensors"
        try:
            return made if made.is_file() or (given.is_file() and rewritten(given)) else given
        except (OSError, ValueError):
            return given

    def public(recipe: dict) -> dict:
        state = busy.get(recipe["id"])
        installed = ready(recipe["id"]).is_file() and not state
        # usable: its file is here and this machine has what it runs on (a clip, a Library action or Edit a picture)
        return {**recipe, "installed": installed, "installing": state, "usable": installed and recipe.get("status") not in ("unsupported", "stored"),
                "flow": lora_flows.public(recipe["id"]) if recipe.get("flow") else None}

    def prepare(lora_id: str) -> None:
        if ready(lora_id).is_file() or kind_of(lora_id) != "h3":
            return
        if not spark:
            convert(source(lora_id), ready(lora_id), pack)
        elif source(lora_id).is_file() and rewritten(source(lora_id)):
            # ComfyUI's layout, and any adaln pair moved onto the pruned checkpoint (lora_layout.py)
            names = lora_layout.modules(list(lora_layout.header(source(lora_id))[0]))
            basis, offset = adaln_basis(folder) if any(lora_layout.ADALN.search(name) for name in names) else (None, None)
            lora_layout.to_comfy(source(lora_id), ready(lora_id), basis, offset)

    def prebuilt(recipe: dict, state: dict) -> bool:
        """On a Mac with no file from a bundle: the hosted build already in the MLX engine's layout, when there is one."""

        if recipe["kind"] != "h3":
            return False  # read as its author wrote it: there is no build to fetch
        if not spark and not source(recipe["id"]).is_file():  # a Mac build an essential package put in the bundle
            where, name = bundle(), recipe["id"] + ".mlx.safetensors"
            local = None if not where or where.startswith(("http://", "https://")) else Path(where).expanduser() / FOLDERS.get((recipe.get("category") or ["mine"])[0], "") / recipe["id"] / name
            wanted = ((HOSTED.get(recipe["id"]) or {}).get("mac") or {}).get("sha256")
            if local and local.is_file() and wanted and hashlib.sha256(local.read_bytes()).hexdigest() == wanted:
                shutil.copyfile(local, ready(recipe["id"]))
                return True
        build = None if spark or source(recipe["id"]).is_file() or bundled(recipe) else hosted(recipe["id"], "mac")
        if not build or os.environ.get("LORA_OFFLINE") == "1":
            return False
        part = ready(recipe["id"]).with_name(recipe["id"] + ".hosted.part")
        try:
            digest = hashlib.sha256()
            with urllib.request.urlopen(urllib.request.Request(build["url"], headers={"User-Agent": "TensorFoldStudio"}), timeout=60) as reply, open(part, "wb") as out:
                state["of"] = int(reply.headers.get("Content-Length") or 0)
                while chunk := reply.read(1 << 20):
                    out.write(chunk)
                    digest.update(chunk)
                    state["done"] += len(chunk)
            if digest.hexdigest() != build["sha256"]:
                return False
            part.replace(ready(recipe["id"]))
            return True
        except (urllib.error.URLError, OSError):
            return False
        finally:
            part.unlink(missing_ok=True)

    def adopt() -> None:
        """At start: every usable preset a local bundle holds becomes installed, with no click and no network."""

        for recipe in recipes():
            lora_id = recipe["id"]
            packed = not spark and bundle() and not bundle().startswith("http") and (Path(bundle()).expanduser() / FOLDERS.get((recipe.get("category") or ["mine"])[0], "") / lora_id / (lora_id + ".mlx.safetensors")).is_file()
            if recipe.get("status") == "unsupported" or ready(lora_id).is_file() or not (bundled(recipe) or source(lora_id).is_file() or packed):
                continue
            with lock:
                if lora_id in busy:
                    continue
                busy[lora_id] = {"stage": "preparing", "done": 0, "of": 0}
            fetch(recipe)
            if busy.get(lora_id, {}).get("stage") == "failed":
                busy.pop(lora_id, None)  # a preset nobody asked for does not show an error; Install says why

    def fetch(recipe: dict) -> None:
        lora_id, state = recipe["id"], busy[recipe["id"]]
        try:
            if not prebuilt(recipe, state):
                if not source(lora_id).is_file():
                    download(recipe, source(lora_id), state)
                state["stage"] = "preparing"
                prepare(lora_id)
            busy.pop(lora_id, None)
        except Exception as error:  # noqa: BLE001 - shown on the hub's button
            busy[lora_id] = {"stage": "failed", "error": str(error)[-300:], "done": 0, "of": 0}

    @router.get("/api/loras")
    def listing() -> list[dict]:
        return [public(r) for r in recipes()]

    @router.get("/api/loras/groups")
    def groups() -> list[dict]:
        return [{"id": key, "name": name} for key, name in GROUPS]

    @router.get("/api/loras/kinds")
    def kinds() -> list[dict]:
        """The models a LoRA can be for, and the jobs that merge each one on this machine."""

        return [{"id": key, "label": info["label"], "short": info["short"], "jobs": list(lora_kinds.jobs_for(key, spark)),
                 "why": {job: lora_kinds.why_not(key, job, spark) for job in lora_kinds.JOBS if lora_kinds.why_not(key, job, spark)}}
                for key, info in lora_kinds.KINDS.items()]

    @router.post("/api/loras")
    def add(body: dict) -> dict:
        name = (body.get("name") or "").strip()
        url, upload = (body.get("url") or "").strip(), (body.get("file") or "").strip()
        if not name or not (url or upload):
            raise HTTPException(400, "a LoRA needs a name and either a download address or an uploaded file")
        if url and not url.startswith(("https://", "http://")):
            raise HTTPException(400, "the download address starts with https://")
        said = (body.get("kind") or "").strip()
        if said and said not in lora_kinds.KINDS:
            raise HTTPException(400, "kind is one of " + ", ".join(lora_kinds.KINDS))
        lora_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "lora"
        with lock:
            taken = {r["id"] for r in recipes()}
            if lora_id in taken:
                lora_id += "-" + time.strftime("%H%M%S")
            styles = body.get("styles") if isinstance(body.get("styles"), dict) else {}
            try:
                strength = float(body.get("strength") or 1.0)
            except (TypeError, ValueError):
                raise HTTPException(400, "strength is a number")
            kind = said or "h3"  # a file behind an address is checked against this when it arrives
            recipe = {"id": lora_id, "name": name, "author": (body.get("author") or "").strip(), "url": url,
                      "page": (body.get("page") or "").strip(), "trigger": (body.get("trigger") or "").strip(),
                      "styles": {str(k).strip(): str(v).strip() for k, v in styles.items() if str(k).strip() and str(v).strip()},
                      "strength": strength, "licence": (body.get("licence") or "").strip(),
                      "notes": (body.get("notes") or "").strip()}
            if upload:
                given = (home / upload).resolve()
                if home.resolve() not in given.parents or not given.is_file():
                    raise HTTPException(400, "the uploaded file is not in the studio's folder")
                try:
                    found = lora_kinds.detect(given)  # the header says which model it is for
                    if said and said != found["kind"]:
                        raise ValueError(f"you said it is a {lora_kinds.label(said)} LoRA, and its tensors are a {lora_kinds.label(found['kind'])} LoRA's")
                    kind = found["kind"]
                    recipe["kind"] = kind
                    shutil.move(str(given), source(lora_id))
                    if kind == "h3":
                        prepare(lora_id)
                except (ValueError, RuntimeError) as error:
                    source(lora_id).unlink(missing_ok=True)
                    raise HTTPException(400, f"that file cannot be used: {error}")
            recipe["kind"] = kind
            recipe["base"] = lora_kinds.label(kind)
            book.write_text(json.dumps(added() + [recipe], indent=1))
        return public(shaped({**recipe, "builtin": False, "needs": "text", "category": ["mine"]}))

    @router.post("/api/loras/{lora_id}/install")
    def install(lora_id: str) -> dict:
        recipe = find(lora_id)
        if recipe.get("status") == "unsupported":
            raise HTTPException(400, f"{recipe['name']} is not supported in the Studio yet. {recipe.get('why', '')}".strip())
        with lock:
            state = busy.get(lora_id)
            if not ready(lora_id).is_file() and (state is None or state["stage"] == "failed"):
                if not recipe.get("url") and not source(lora_id).is_file():
                    raise HTTPException(400, "this recipe has no download address")
                busy[lora_id] = {"stage": "downloading", "done": 0, "of": 0}
                threading.Thread(target=fetch, args=(recipe,), daemon=True).start()
        return public(recipe)

    @router.delete("/api/loras/{lora_id}")
    def remove(lora_id: str) -> dict:
        """A user's recipe leaves the hub; a preset only loses its downloaded file. Files go to the trash folder."""

        recipe = find(lora_id)
        with lock:
            trash = home / "trash"
            trash.mkdir(exist_ok=True)
            for path in (source(lora_id), ready(lora_id)):
                if path.is_file():
                    shutil.move(str(path), trash / (time.strftime("%Y%m%d-%H%M%S-") + path.name))
            busy.pop(lora_id, None)
            if not recipe["builtin"]:
                book.write_text(json.dumps([r for r in added() if r.get("id") != lora_id], indent=1))
        return {"removed": lora_id, "recipe": not recipe["builtin"]}

    def resolve(picked, target: str = "video", job: str = "") -> list[dict]:
        """A job's `loras` as [{"path", "strength", "lead"}]; 400 when one is unknown, not installed, or made for another
        model than the job's (`target`: "video" for a clip, "image" for a new picture or Edit a picture; `job` names the
        job, "video", "scout" or "edit", where a machine does one image job and not the other)."""

        out = []
        job = job or ("video" if target == "video" else "edit")
        for item in picked or []:
            recipe = find(str(item.get("id")))
            if recipe.get("status") == "stored":
                raise HTTPException(400, f"{recipe['name']} is stored in the hub: {recipe.get('why', 'the Studio has no engine for it yet')}")
            if recipe.get("target") != target:
                raise HTTPException(400, f"{recipe['name']} is a {recipe['base']} LoRA: it cannot go on {lora_kinds.JOBS.get(job, job)}"
                                    + (", it is for pictures: use it in Create, a new picture or Edit a picture" if recipe["kind"] == "qwen-image" else ", it is for clips" if recipe["kind"] == "h3" else ""))
            if job not in recipe["jobs"] and recipe.get("status") in ("ready", "gated") and (reason := lora_kinds.why_not(recipe["kind"], job, spark)):
                raise HTTPException(400, f"{recipe['name']} cannot go on {lora_kinds.JOBS.get(job, job)} here: {reason}")
            if not ready(recipe["id"]).is_file():
                raise HTTPException(400, f"the LoRA {recipe['name']} is not installed: install it in the LoRA hub first")
            try:
                strength = float(item.get("strength") if item.get("strength") not in (None, "") else recipe.get("strength", 1.0))
            except (TypeError, ValueError):
                raise HTTPException(400, "a LoRA's strength is a number")
            low, high = (list(recipe.get("range") or []) + [0.1, 2])[:2] if len(recipe.get("range") or []) == 2 else (0.1, 2)
            low, high = (low if low < 0 else 0.0), max(high, 2)  # a slider LoRA takes minus values; the rest go up to 2
            if not strength or not low <= strength <= high:
                raise HTTPException(400, f"the strength of {recipe['name']} is between {low:g} and {high:g}, and not 0")
            if recipe.get("status") == "unsupported":
                raise HTTPException(400, f"{recipe['name']} is not supported in the Studio yet. {recipe.get('why', '')}".strip())
            if recipe.get("status") == "action":
                raise HTTPException(400, f"{recipe['name']} is not for a clip: {recipe.get('why', 'the Library has an action for it')}")
            phrase = (recipe.get("styles") or {}).get(str(item.get("style") or ""), "")
            lead = ", ".join(part for part in (recipe.get("trigger", ""), phrase) if part)
            out.append({"id": recipe["id"], "name": recipe["name"], "path": str(ready(recipe["id"])), "strength": strength, "trigger": recipe.get("trigger", ""), "lead": lead,
                        "comma": bool(recipe.get("comma")), "kind": recipe["kind"]})
        if len(out) > LIMIT:
            raise HTTPException(400, f"at most {LIMIT} LoRAs on one " + ("clip" if target == "video" else "picture"))
        return out

    def lead(prompt: str, picked: list[dict]) -> str:
        """The prompt with each LoRA's trigger and look phrase at the start of the shot, unless it is there already."""

        # most triggers open the shot as a sentence; one whose author writes "trigger, subject, ..." keeps its comma
        words = " ".join(p["lead"] + "," if p.get("comma") and not p["lead"].endswith(".") else p["lead"] if p["lead"].endswith(".") else p["lead"] + "."
                         for p in picked if p["lead"] and (not p["trigger"] or p["trigger"] not in prompt))
        if not words:
            return prompt
        return prompt.replace("[Shot 1] ", f"[Shot 1] {words} ", 1) if "[Shot 1] " in prompt else f"{words} {prompt}"

    threading.Thread(target=adopt, daemon=True).start()
    return router, resolve, lead


def main(argv: list[str]) -> int:
    """`loras.py install GROUP[,GROUP...]|all`: the ready presets of those groups, from their authors' pages."""

    if len(argv) < 2 or argv[0] != "install":
        print("usage: loras.py install all|" + ",".join(key for key, _ in GROUPS[:-1]), file=sys.stderr)
        return 2
    wanted = {key for key, _ in GROUPS} if argv[1] == "all" else set(argv[1].split(","))
    unknown = wanted - {key for key, _ in GROUPS}
    if unknown:
        print("no LoRA group " + ", ".join(sorted(unknown)), file=sys.stderr)
        return 2
    folder = Path(os.environ.get("STUDIO_HOME", str(Path.home() / "TensorFoldStudio"))) / "loras"
    folder.mkdir(parents=True, exist_ok=True)
    failed = 0
    for recipe in json.loads(PRESETS.read_text()):
        if not wanted & set(recipe.get("category") or []) or recipe.get("status", "ready") == "unsupported":
            continue
        target = folder / f"{recipe['id']}.safetensors"
        where = bundle()
        build = None if not where or where.startswith(("http://", "https://")) else Path(where).expanduser() / FOLDERS.get(recipe["category"][0], "") / recipe["id"] / f"{recipe['id']}.mlx.safetensors"
        made = folder / f"{recipe['id']}.mlx.safetensors"
        if sys.platform == "darwin" and not made.is_file() and build and build.is_file() \
                and hashlib.sha256(build.read_bytes()).hexdigest() == ((HOSTED.get(recipe["id"]) or {}).get("mac") or {}).get("sha256"):
            shutil.copyfile(build, made)  # an essential package's Mac build: installed as it is
            print(f"got   {recipe['name']} by {recipe['author']} ({made.stat().st_size >> 20} MB), the Mac build from the package")
            continue
        if made.is_file() and not target.is_file():
            print(f"have  {recipe['name']}")
            continue
        if not target.is_file() and not bundled(recipe) and os.environ.get("LORA_OFFLINE") == "1":
            continue  # only what the bundle holds
        if recipe.get("status") == "gated" and not target.is_file() and not bundled(recipe) and not token():
            print(f"gated {recipe['name']}: its author approves each download ({recipe['page']}); set HF_TOKEN once you have access")
            continue
        mlx = folder / f"{recipe['id']}.mlx.safetensors"
        try:
            if target.is_file():
                print(f"have  {recipe['name']}")
            else:
                came = "the bundle" if bundled(recipe) or bundle().startswith("http") else recipe["page"]
                download(recipe, target, {"done": 0, "of": 0})
                print(f"got   {recipe['name']} by {recipe['author']} ({target.stat().st_size >> 20} MB), from {came}")
            if sys.platform == "darwin" and not mlx.is_file() and lora_kinds.kind_of_target(recipe.get("target")) == "h3":
                convert(target, mlx, Path(__file__).resolve().parent.parent)
        except Exception as error:  # noqa: BLE001 - one author's page being down does not stop the rest
            failed += 1
            print(f"skip  {recipe['name']}: {error}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
