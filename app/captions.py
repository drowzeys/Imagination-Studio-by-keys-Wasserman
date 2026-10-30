"""The Library knows what is in every clip and picture. A background thread looks at the ones it has not seen (a clip
by its three-frame strip, a picture as it is) with the local vision model and writes one sentence and a few tags
into library/captions.json; the Library searches them. Nothing leaves this machine: only the local model is asked."""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import threading
import time
import urllib.request
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

ROUND = 60  # seconds between looks
PER_ROUND = 5
NOW_LIMIT = 5  # a run for this many paths or fewer is answered at once
RETRY = 3600  # seconds before a file the model could not describe is tried again
ASK = ("Describe this picture for a film library's search. If it is a strip of three frames, it is three frames of one "
       "video clip, left to right: describe the clip as a whole. Answer with one line of strict JSON and nothing else: "
       '{"caption": "one plain sentence saying who or what is in it and what happens", "tags": ["5 to 10 lowercase words '
       'for the subjects, objects, setting, light and camera"], "people": ["short descriptions of people, if any"], '
       '"place": "where it is, or empty", "mood": "the feeling, or empty"}.')
STOP = {"a", "an", "the", "of", "in", "on", "at", "and", "to", "with", "is", "are", "for", "it", "its", "by", "from", "as"}


class Busy(Exception):
    """The vision model cannot be asked now (a render holds the memory, or none is running)."""


class Store:
    def __init__(self, home: Path):
        self.home = home
        self.file = home / "library" / "captions.json"
        self.lock = threading.Lock()

    def read(self) -> dict:
        try:
            data = json.loads(self.file.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def write(self, data: dict) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        scratch = self.file.with_name(f".captions-{uuid.uuid4().hex[:8]}.tmp")
        scratch.write_text(json.dumps(data, indent=1, sort_keys=True))
        os.replace(scratch, self.file)

    def put(self, path: str, entry: dict) -> None:
        with self.lock:
            data = self.read()
            data[path] = entry
            self.write(data)

    def forget(self, paths) -> int:
        with self.lock:
            data = self.read()
            gone = [p for p in paths if p in data]
            for p in gone:
                del data[p]
            if gone:
                self.write(data)
            return len(gone)

    def prune(self) -> int:
        """Entries whose file is gone."""

        with self.lock:
            data = self.read()
            gone = [p for p in data if not (self.home / p).is_file()]
            for p in gone:
                del data[p]
            if gone:
                self.write(data)
            return len(gone)


# ---- the vision model ---------------------------------------------------------------------------------------------

def ask_model(picture: Path) -> str:
    """The local vision model's answer about a picture; Busy when it cannot be asked now. Only a local model is used."""

    import qa
    brain = qa.default_brain()
    endpoint = brain.get("endpoint") or ""
    try:
        engine = qa.bolt_engine(endpoint)
        model = brain.get("model") or ""
        if engine is not None:
            # the Studio's Bolt without a vision tower (the Metal engine, llama.cpp) cannot describe a picture: asking it
            # would only load it (about 30 GB) for a refusal, every round, and keep it from unloading when idle
            if not getattr(engine.engine(), "vision", False):
                raise Busy(f"the Studio's {engine.NAME} here has no vision tower (BOLT_VISION=1 asks for one)")
            with engine.request(wait=False) as live:  # refused at once while a render holds the memory
                return _vision(picture, live, model or qa.engine_model())
        if endpoint:
            return _vision(picture, endpoint, model)
    except Exception as error:  # noqa: BLE001
        if type(error).__name__ == "Held":
            raise Busy(str(error))
        raise
    raise Busy("no local vision model is set up")


def _vision(picture: Path, endpoint: str, model: str) -> str:
    kind = "image/png" if picture.suffix.lower() == ".png" else "image/jpeg"  # a picture as it is; a frame is a JPEG
    image = f"data:{kind};base64," + base64.b64encode(picture.read_bytes()).decode()
    body = json.dumps({"model": model, "temperature": 0, "messages": [{"role": "user", "content": [
        {"type": "text", "text": ASK}, {"type": "image_url", "image_url": {"url": image}}]}]}).encode()
    request = urllib.request.Request(endpoint.rstrip("/") + "/chat/completions", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as answer:
        return json.loads(answer.read())["choices"][0]["message"]["content"]


def look(picture: Path) -> str:
    """The step the tests replace: a picture in, the model's words out."""

    return ask_model(picture)


def clean(text: str) -> dict | None:
    """The model's JSON, made safe: {caption, tags, people, place, mood}; None when there is no caption in it."""

    found = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(found.group(0)) if found else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or not str(data.get("caption") or "").strip():
        return None

    def words(value, limit):
        value = value if isinstance(value, list) else []
        return [" ".join(str(v).lower().split())[:60] for v in value if str(v).strip()][:limit]

    out = {"caption": " ".join(str(data["caption"]).split())[:300], "tags": words(data.get("tags"), 10)}
    if words(data.get("people"), 6):
        out["people"] = words(data.get("people"), 6)
    for key in ("place", "mood"):
        if str(data.get(key) or "").strip():
            out[key] = " ".join(str(data[key]).split())[:100]
    return out


def describe(home: Path, item: dict, takes: dict) -> dict:
    """A library item's entry. Raises Busy (model cannot be asked) or ValueError (it gave nothing usable)."""

    import qa
    source = home / item["path"]
    if item["kind"] == "video":
        with tempfile.TemporaryDirectory() as scratch:
            strip = Path(scratch) / "strip.jpg"
            qa.sheet(source, strip)
            answer = look(strip)
    else:
        answer = look(source)
    parsed = clean(answer)
    if parsed is None:
        raise ValueError("the model gave no caption")
    entry = {**parsed, "model": "local vision model", "at": time.time(), "modified": item.get("modified", 0)}
    made = takes.get(item["path"]) or {}
    own = " ".join(str(made.get("prompt") or made.get("title") or "").split())[:500]
    if own:
        entry["prompt"] = own
    return entry


# ---- search -------------------------------------------------------------------------------------------------------

def stem(word: str) -> str:
    for end, to in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if word.endswith(end) and len(word) - len(end) >= 3:
            word = word[: -len(end)] + to
            break
    if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "ls":  # chasing -> chas, running -> runn -> run
        word = word[:-1]
    return word.rstrip("e") if len(word) > 4 else word


def tokens(text: str) -> list[str]:
    return [stem(w) for w in re.findall(r"[a-z0-9]+", str(text).lower()) if w not in STOP]


FIELDS = (("caption", 3.0), ("tags", 2.5), ("people", 2.0), ("place", 1.5), ("mood", 1.0), ("prompt", 1.5), ("name", 1.0))


def score(query: str, path: str, entry: dict) -> float:
    wanted = tokens(query)
    if not wanted:
        return 0.0
    fields = {"name": Path(path).stem.replace("_", " ").replace("-", " ")}
    for key, _ in FIELDS[:-1]:
        value = entry.get(key) or ""
        fields[key] = " ".join(value) if isinstance(value, list) else str(value)
    total = 0.0
    for key, weight in FIELDS:
        have = tokens(fields.get(key, ""))
        if not have:
            continue
        hits = sum(1 for w in set(wanted) if w in have)
        total += weight * hits
    # every word found somewhere beats a few found often; a phrase said as written counts extra
    every = set(w for key, _ in FIELDS for w in tokens(fields.get(key, "")))
    covered = sum(1 for w in set(wanted) if w in every)
    if not covered:
        return 0.0
    total *= covered / len(set(wanted))
    joined = " ".join(wanted)
    if len(wanted) > 1 and any(joined in " ".join(tokens(fields.get(key, ""))) for key, _ in FIELDS):
        total += 4.0
    return round(total, 3)


def search(store: Store, items: list[dict], query: str, limit: int = 60) -> list[dict]:
    data = store.read()
    out = []
    for item in items:
        entry = data.get(item["path"])
        if not entry:
            continue
        found = score(query, item["path"], entry)
        if found > 0:
            out.append({**item, "caption": entry.get("caption", ""), "tags": entry.get("tags", []), "score": found})
    out.sort(key=lambda i: (-i["score"], -i.get("modified", 0)))
    return out[:limit]


# ---- the background captioner and the routes ----------------------------------------------------------------------

def attach(home: Path, listing, inside, relative, guard, idle, takes, auto) -> APIRouter:
    """listing() the Library items; guard(request, mutation) the local check; idle() true when no job runs or waits;
    takes() path -> the take line written when a clip was rendered; auto() whether new clips are described by themselves."""

    store = Store(home)
    router = APIRouter()
    failed: dict[str, float] = {}
    mine = threading.Lock()

    def todo(items: list[dict]) -> list[dict]:
        data = store.read()
        out = []
        for item in items:
            if item.get("kind") not in ("video", "image"):
                continue
            entry = data.get(item["path"])
            if entry and abs(float(entry.get("modified", 0)) - float(item.get("modified", 0))) < 0.01:
                continue
            if time.time() - failed.get(item["path"], 0) < RETRY:
                continue
            out.append(item)
        return out

    def caption(items: list[dict]) -> dict:
        """Describe these items now. {done: [paths], failed: [paths], busy: bool}; stops at the first Busy."""

        done, bad, busy = [], [], False
        table = takes() or {}
        with mine:
            for item in items:
                try:
                    store.put(item["path"], describe(home, item, table))
                    done.append(item["path"])
                except Busy:
                    busy = True
                    break
                except Exception:  # noqa: BLE001 - a clip that cannot be described is left for later
                    failed[item["path"]] = time.time()
                    bad.append(item["path"])
        return {"done": done, "failed": bad, "busy": busy}

    def one_round() -> None:
        if not auto() or not idle():
            return
        items = listing()
        store.prune()
        wanted = todo(items)[:PER_ROUND]
        if wanted and idle():
            caption(wanted)

    def loop() -> None:
        while True:
            time.sleep(ROUND)
            try:
                one_round()
            except Exception:  # noqa: BLE001 - the captioner never stops the studio
                pass

    def start() -> None:
        threading.Thread(target=loop, daemon=True, name="captions").start()

    router.captioner = {"round": one_round, "caption": caption, "start": start, "store": store, "todo": todo}

    def known(path: str) -> str:
        return relative(inside(path))

    @router.get("/api/library/captions")
    def one(path: str = "") -> dict:
        data = store.read()
        if not path:
            return {"captions": {p: {"caption": e.get("caption", ""), "tags": e.get("tags", [])} for p, e in data.items()}}
        entry = data.get(known(path))
        if not entry:
            raise HTTPException(404, "this file has not been described yet")
        return {"path": known(path), **entry}

    @router.post("/api/library/captions/run")
    def run(request: Request, body: dict | None = None) -> dict:
        guard(request, True)
        paths = (body or {}).get("paths")
        items = listing()
        if paths:
            if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
                raise HTTPException(400, "paths is a list of Library paths")
            wanted = {known(p) for p in paths}
            chosen = [i for i in items if i["path"] in wanted and i.get("kind") in ("video", "image")]
            if not chosen:
                raise HTTPException(400, "none of those is a clip or a picture in the Library")
        else:
            chosen = todo(items)
        if len(chosen) <= NOW_LIMIT:
            for item in chosen:
                failed.pop(item["path"], None)
            return caption(chosen)
        threading.Thread(target=lambda: caption(chosen), daemon=True, name="captions-run").start()
        return {"started": len(chosen)}

    @router.get("/api/library/search")
    def find(q: str = "", limit: int = 60) -> dict:
        return {"items": search(store, listing(), q, max(1, min(int(limit), 200)))}

    @router.delete("/api/library/captions")
    def forget(request: Request, path: str) -> dict:
        guard(request)  # local and same-origin; a DELETE has no JSON body to check, as the trash routes
        return {"forgotten": store.forget([known(path)])}

    return router
