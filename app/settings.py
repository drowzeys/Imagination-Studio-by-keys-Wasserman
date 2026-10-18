"""Settings: how the studio looks, which rooms the sidebar shows, the defaults a new take starts from, and the Hermes
command. Kept in $STUDIO_HOME/settings.json, written whole and atomically.

    GET  /api/settings   {settings, defaults, schema}
    POST /api/settings   a partial settings object, merged in; every key is checked and an unknown one is refused (400)

Nothing here is a secret and nothing secret is ever accepted: sign-ins stay the CLIs' own, a local model server is an
address (Brains: /api/brains/settings). The write goes through server.py's WriteGuard like every other POST.
agents.py reads ``hermes_profile`` and ``hermes_command`` from the same file; ``job_env`` hands the delivery sound rate
to the jobs the app launches (DELIVERY_AUDIO_RATE, spark/video.sh)."""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

HOME: Path | None = None
lock = threading.Lock()

# the sidebar's rooms (shell.js ROOMS): Home is not a room, and Machine and Settings always stay in the sidebar
ROOMS = ("story", "image", "video", "previs", "sound", "edit", "agent", "library", "looks", "suite", "nodes", "machine", "learn", "settings")
ALWAYS = ("machine", "settings")
STARTS = ("clip", "scout", "still", "sharp", "long", "voice", "song", "agent")
PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
MODEL_ID = re.compile(r"[a-z0-9][a-z0-9_.-]{0,31}")

DEFAULTS = {
    "appearance": {"type_size": "comfortable", "glow": True, "poster": True, "accent": "amber", "motion": "system",
                   "start_cards": ["clip", "scout", "still", "sharp", "long", "voice"]},
    "layout": {"hidden": [], "order": []},
    "presets": {"recipe": "quick", "image_model": "qwen", "seconds": 5, "qa_gate": True, "audio_rate": "keep",
                "seed": "fixed", "mode": "clip"},
    "hermes_profile": "",
    "hermes_command": "",
}
CHOICES = {
    ("appearance", "type_size"): ("comfortable", "large", "largest"),
    ("appearance", "accent"): ("amber", "cobalt"),
    ("appearance", "motion"): ("system", "reduce"),
    ("presets", "recipe"): ("quick", "sharp", "final", "custom"),
    ("presets", "audio_rate"): ("keep", "48000"),
    ("presets", "seed"): ("fixed", "random"),
    ("presets", "mode"): ("clip", "picture", "voice", "song"),
}
FLAGS = {("appearance", "glow"), ("appearance", "poster"), ("presets", "qa_gate")}


def schema() -> dict:
    """What the page may send, for the page and for anyone reading the file."""

    return {"choices": {f"{a}.{b}": list(v) for (a, b), v in CHOICES.items()}, "flags": sorted(f"{a}.{b}" for a, b in FLAGS),
            "rooms": list(ROOMS), "always": list(ALWAYS), "start_cards": list(STARTS), "seconds": [1, 15]}


def path() -> Path | None:
    return HOME / "settings.json" if HOME else None


def rooms_list(value, what: str, allowed=ROOMS) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(v, str) or v not in allowed for v in value):
        raise HTTPException(400, f"{what} lists rooms: " + ", ".join(allowed))
    return list(dict.fromkeys(value))


def check_one(section: str, key: str, value):
    """The value, checked; 400 with the reason otherwise."""

    if (section, key) in CHOICES:
        if value not in CHOICES[(section, key)]:
            raise HTTPException(400, f"{section}.{key} is one of " + ", ".join(CHOICES[(section, key)]))
        return value
    if (section, key) in FLAGS:
        if not isinstance(value, bool):
            raise HTTPException(400, f"{section}.{key} is true or false")
        return value
    if (section, key) == ("appearance", "start_cards"):
        if not isinstance(value, list) or any(v not in STARTS for v in value):
            raise HTTPException(400, "appearance.start_cards lists cards: " + ", ".join(STARTS))
        return list(dict.fromkeys(value))
    if (section, key) == ("layout", "hidden"):
        hidden = rooms_list(value, "layout.hidden", tuple(r for r in ROOMS if r not in ALWAYS))
        return hidden
    if (section, key) == ("layout", "order"):
        return rooms_list(value, "layout.order")
    if (section, key) == ("presets", "image_model"):
        if not isinstance(value, str) or not MODEL_ID.fullmatch(value):
            raise HTTPException(400, "presets.image_model is a picture model's id, like qwen or flux3k-fast")
        return value
    if (section, key) == ("presets", "seconds"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 15:
            raise HTTPException(400, "presets.seconds is a number from 1 to 15")
        return value
    raise HTTPException(400, f"{section}.{key} is not a setting")


def check_top(key: str, value):
    if key == "hermes_profile":
        if not isinstance(value, str) or (value and not PROFILE.fullmatch(value)):
            raise HTTPException(400, "hermes_profile is a Hermes profile name: letters, digits, dot, dash and underscore")
        return value
    if key == "hermes_command":
        if not isinstance(value, str) or (value and (not value.startswith("/") or len(value) > 400 or re.search(r"[\s;&|$`<>\\\"'\0]", value))):
            raise HTTPException(400, "hermes_command is the full path of the hermes program, with no spaces or shell characters")
        return value
    raise HTTPException(400, f"{key} is not a setting")


def merge(now: dict, body: dict) -> dict:
    if not isinstance(body, dict):
        raise HTTPException(400, "settings are an object")
    out = copy.deepcopy(now)
    for key, value in body.items():
        if key in ("appearance", "layout", "presets"):
            if not isinstance(value, dict):
                raise HTTPException(400, f"{key} is an object")
            for sub, one in value.items():
                out[key][sub] = check_one(key, sub, one)
        else:
            out[key] = check_top(key, value)
    return out


def load() -> dict:
    """The saved settings over the defaults; a value that no longer passes its check falls back to the default."""

    out = copy.deepcopy(DEFAULTS)
    try:
        saved = json.loads(path().read_text()) if path() else {}
    except (OSError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        return out
    for key, value in saved.items():
        try:
            if key in ("appearance", "layout", "presets") and isinstance(value, dict):
                for sub, one in value.items():
                    try:
                        out[key][sub] = check_one(key, sub, one)
                    except HTTPException:
                        pass
            elif key in DEFAULTS:
                out[key] = check_top(key, value)
        except HTTPException:
            pass
    return out


def save(body: dict) -> dict:
    with lock:
        fresh = merge(load(), body)
        target = path()
        if target:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
            temp.write_text(json.dumps(fresh, indent=2) + "\n")
            os.chmod(temp, 0o600)
            temp.replace(target)
    return fresh


def job_env() -> dict:
    """What the settings add to a job's environment (server.environment): the delivery sound rate."""

    rate = load()["presets"]["audio_rate"]
    return {"DELIVERY_AUDIO_RATE": rate} if rate != "keep" else {}


def attach(home: Path) -> APIRouter:
    global HOME
    HOME = home
    router = APIRouter()

    @router.get("/api/settings")
    def get_settings() -> dict:
        return {"settings": load(), "defaults": DEFAULTS, "schema": schema()}

    @router.post("/api/settings")
    def post_settings(body: dict) -> dict:
        return {"settings": save(body)}

    return router
