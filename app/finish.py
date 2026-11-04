"""Finish: a timeline's final cut and mix, rendered by Tesseract (github.com/mirage-hq/tesseract), and the same cut
for DaVinci Resolve (FCPXML, EDL), Premiere Pro and After Effects (Tesseract Converter, github.com/mirage-hq/Tesseract-Converter).

    job kind "finish"               params {project | clips[{path, in, out}], song?, voice?, title?: {text, subtitle},
                                    captions?, mix: dialogue|balanced|music, size: 720p|1080p|4k, fps: 24|25|30|60}
    GET  /api/finish                whether Tesseract is ready: {ok, where: here|ssh:<host>, version, why, converter, ...}
    POST /api/finish/{id}/convert   {to: premiere|aftereffects}: the finished cut as a Premiere or After Effects project

A finish writes, in exports/: <id>.mp4 (the finished cut), <id>.tsrct (the editable Tesseract project, its media packed
inside), <id>.fcpxml and <id>.edl (the same cut for Resolve, by suite.fcpxml and suite.edl). The cut keeps each clip's
own sound at full level, lays narration (``voice``) from the first shot, and lays the song under it all, lowered by a
timed envelope wherever a clip or the narration has sound (the dialogue) and faded in and out. A title is an opening
scene of its own, made of native Tesseract layers (a card, the title, a line and the subtitle) that fade and rise in.
Captions are the words a local speech-to-text model (faster-whisper) hears; without one they are left out, never made up.

Tesseract runs on macOS, Windows (on Windows on Arm, its x86_64 build through Windows' x64 emulation) and Linux x86_64,
not on Linux ARM: on a DGX Spark the cut is finished on the computer
set as the remote host in Settings > Connections (Brains & tools), the Mac beside it, over ssh. The clips are copied to a
folder of their own there, the project is built and rendered there, and the results come back. The Studio never changes
Tesseract's usage-count setting, and sends no attribution of its own.

The job's runner is this file run as a script (``python3 app/finish.py run <plan.json>``): it prints
``[tensorfold] step n/m`` lines (the render's frames, from ``tsrct export --json``) and ``[tensorfold] finish: ...`` notes.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

KINDS = ("finish",)
TSRCT_VERSION = "0.4.0"        # the release Tesseract's own skills pin (fxSchemaVersion 77)
CONVERTER_VERSION = "0.3.0"    # Tesseract Converter built on the same FX schema
# natively on Windows: scripts\setup-windows-finish.ps1 (the windows-x86_64 release; on Windows on Arm it runs through
# Windows' own x64 emulation)
SETUP = ("powershell -ExecutionPolicy Bypass -File scripts\\setup-windows-finish.ps1" if sys.platform == "win32"
         else "bash scripts/setup-finish.sh")
# the song's level under the cut and how far it dips under the dialogue, in dB, for each mix
MIXES = {"dialogue": (-18.0, 12.0), "balanced": (-10.0, 12.0), "music": (-5.0, 6.0)}
SIZES = ("720p", "1080p", "4k")
RATES = (24, 25, 30, 60)
TITLE_MS = 3500                 # the opening title scene
CLIPS = (".mp4", ".mov", ".m4v")
SOUNDS = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")   # what tsrct import-asset takes
TARGETS = {"premiere": ("premiere", "Premiere Pro"), "aftereffects": ("after-effects", "After Effects")}
PREMIERE_RATES = (24, 25, 30, 50, 60)
ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
REMOTE_PATH = 'PATH="$PATH:/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin"; export PATH; '
PROBE_TTL = 60
# the fonts the title card and captions use (setup-finish.sh, SIL Open Font License), then ones the computer has
FONTS = {"title": ("InstrumentSerif-Regular.ttf", "/System/Library/Fonts/Supplemental/Georgia.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "C:/Windows/Fonts/georgia.ttf"),
         "text": ("Geist[wght].ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf")}

HOME: Path | None = None
PACK = Path(__file__).resolve().parent.parent
inside = relative = None
seen: dict = {}
seen_lock = threading.Lock()


def prefix() -> Path:
    if sys.platform == "win32" and not os.environ.get("PREFIX"):
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "ImaginationStudio"
    return Path(os.environ.get("PREFIX") or Path.home() / ".local/opt/tensorfold-studio").expanduser()


def runs_here() -> bool:
    """Whether Tesseract runs on this computer: macOS, Windows (its x86_64 build; on Windows on Arm, such as an RTX
    Spark N1X, through Windows' x64 emulation), or Linux on x86_64 (not Linux ARM, such as a DGX Spark)."""

    import platform
    if sys.platform in ("darwin", "win32"):
        return True
    return sys.platform.startswith("linux") and platform.machine().lower() in ("x86_64", "amd64")


EXE = ".exe" if sys.platform == "win32" else ""


def tsrct_places() -> list[Path]:
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    places = [prefix() / f"finish/bin/tsrct{EXE}", Path.home() / "Library/Application Support/Tesseract/bin/tsrct",
              data / "Tesseract/bin/tsrct"]
    if sys.platform == "win32":  # scripts/setup-windows-finish.ps1's bundle, and where Tesseract's own install.ps1 puts it
        places[1:1] = [prefix() / "finish/tesseract/bin/tsrct.exe",
                       Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "Tesseract/bin/tsrct.exe"]
    found = shutil.which("tsrct")
    return places + ([Path(found)] if found else [])


def converter_places() -> list[Path]:
    found = shutil.which("tsrct-conv")
    windows = [prefix() / "finish/converter/tsrct-conv.exe"] if sys.platform == "win32" else []  # setup-windows-finish.ps1
    return [prefix() / f"finish/bin/tsrct-conv{EXE}", *windows, Path.home() / ".local/bin/tsrct-conv"] + ([Path(found)] if found else [])


def version_of(path: Path | str) -> str | None:
    try:
        said = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    words = said.split()
    return words[1] if len(words) > 1 else None


def first_working(places: list[Path]) -> tuple[str | None, str | None]:
    for place in places:
        if place.is_file() and os.access(place, os.X_OK):
            version = version_of(place)
            if version:
                return str(place), version
    return None, None


def remote_host() -> str:
    """The computer set as the remote host (Settings > Connections), where CLIs run over ssh; "" when none is set."""

    try:
        import agents
        return agents.saved_settings()["remote"]["host"] or ""
    except Exception:  # noqa: BLE001 - no settings yet, or agents not importable here
        return ""


def ssh_of(host: str) -> list[str]:
    name, _, port = host.partition(":")
    return SSH + (["-p", port] if port else []) + [name, "--"]


def scp_of(host: str) -> tuple[list[str], str]:
    name, _, port = host.partition(":")
    return ["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"] + (["-P", port] if port else []), name


PROBE = REMOTE_PATH + r'''
look() { for c in "$@"; do if [ -n "$c" ] && [ -x "$c" ]; then echo "$c"; return; fi; done; }
p="${PREFIX:-$HOME/.local/opt/tensorfold-studio}/finish/bin"
t=$(look "$p/tsrct" "$HOME/Library/Application Support/Tesseract/bin/tsrct" "${XDG_DATA_HOME:-$HOME/.local/share}/Tesseract/bin/tsrct" "$(command -v tsrct 2>/dev/null)")
c=$(look "$p/tsrct-conv" "$HOME/.local/bin/tsrct-conv" "$(command -v tsrct-conv 2>/dev/null)")
echo "tsrct=$t"; [ -n "$t" ] && echo "version=$("$t" --version 2>/dev/null | head -1)"
echo "conv=$c"; [ -n "$c" ] && echo "convversion=$("$c" --version 2>/dev/null | head -1)"
echo "os=$(uname -sm)"; echo "name=$(scutil --get ComputerName 2>/dev/null || hostname)"; echo "ffmpeg=$(command -v ffmpeg 2>/dev/null)"
'''


def probe_remote(host: str) -> dict:
    """What the remote computer has, in one ssh call: {tsrct, version, conv, convversion, os, name, ffmpeg} or {error}."""

    try:
        done = subprocess.run([*ssh_of(host), PROBE], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"error": f"{host} did not answer over ssh in 20 seconds"}
    except OSError as error:
        return {"error": f"ssh is not available here: {error}"}
    if done.returncode:
        return {"error": f"ssh to {host} failed: " + (done.stderr.strip()[-200:] or "no answer")}
    out = {}
    for line in done.stdout.splitlines():
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    for key in ("version", "convversion"):
        words = out.get(key, "").split()
        out[key] = words[1] if len(words) > 1 else ""
    return out


def ready(fresh: bool = False, local_only: bool = False) -> dict:
    """Where the cut is finished, asked at most once a minute: {ok, where ("here" or "ssh:<host>"), version, why, path,
    machine, converter: {ok, version, path}, setup, pinned}."""

    host = "" if local_only else remote_host()
    key = (host, str(prefix()))
    with seen_lock:
        last = seen.get(key)
    if last and not fresh and time.monotonic() - last[0] < PROBE_TTL:
        return last[1]
    out = {"ok": False, "where": "here", "version": None, "why": "", "path": None, "machine": None, "pinned": TSRCT_VERSION,
           "setup": SETUP, "converter": {"ok": False, "version": None, "path": None}, "ffmpeg": None, "here": runs_here(),
           "linux": sys.platform.startswith("linux")}
    if runs_here():
        path, version = first_working(tsrct_places())
        if path:
            out.update(ok=True, path=path, version=version)
            if sys.platform.startswith("linux"):
                out["ffmpeg"] = shutil.which("ffmpeg")
                if not out["ffmpeg"]:
                    out.update(ok=False, why="Tesseract on Linux needs ffmpeg with libx264 to write MP4: sudo apt install ffmpeg")
    conv, conv_version = first_working(converter_places()) if runs_here() else (None, None)
    if conv:
        out["converter"] = {"ok": True, "version": conv_version, "path": conv, "where": "here"}
    if not out["path"] and host:
        found = probe_remote(host)
        if found.get("error"):
            out["why"] = (found["error"] + f". Tesseract is to run on {host}: check that `ssh {host} true` works from this "
                          "computer without a password, then look again.")
        elif not found.get("tsrct"):
            out["why"] = f"Tesseract is not installed on {found.get('name') or host}: run `{SETUP}` there."
        elif not found.get("os", "").startswith("Darwin") and "x86_64" not in found.get("os", ""):
            out["why"] = f"{found.get('name') or host} cannot run Tesseract ({found.get('os')}): set a Mac as the remote computer."
        elif found.get("os", "").startswith("Linux") and not found.get("ffmpeg"):
            out["why"] = f"Tesseract on {found.get('name') or host} needs ffmpeg with libx264 to write MP4."
        else:
            out.update(ok=True, where="ssh:" + host, path=found["tsrct"], version=found.get("version") or None,
                       machine=found.get("name") or host, ffmpeg=found.get("ffmpeg") or None, why="",
                       linux=found.get("os", "").startswith("Linux"))
        if not conv and found.get("conv"):
            out["converter"] = {"ok": True, "version": found.get("convversion") or None, "path": found["conv"], "where": "ssh:" + host}
    if not out["ok"] and not out["why"]:
        out["why"] = (f"Tesseract is not installed: run `{SETUP}`." if runs_here() else
                      "Tesseract does not run on this computer (Linux on ARM). Install it on your Mac "
                      f"(`{SETUP}` there) and set the Mac as the remote computer in Settings > Connections (Brains & tools).")
    if out["ok"] and out["version"] != TSRCT_VERSION:
        out["note"] = f"Tesseract {out['version']} is installed; the Studio is made for {TSRCT_VERSION}: `{SETUP}` installs it beside."
    with seen_lock:
        seen[key] = (time.monotonic(), out)
    return out


def font_files() -> dict:
    """{title: path, text: path}: the Studio's own fonts (setup-finish.sh), or ones this computer has."""

    out = {}
    for role, (own, *others) in FONTS.items():
        places = [prefix() / "finish/fonts" / own] + [Path(p) for p in others]
        out[role] = next((str(p) for p in places if p.is_file()), None)
    return out


# ---------- what a finish is made of ----------

def bad(message: str):
    from fastapi import HTTPException
    return HTTPException(400, message)


def probe(path: Path) -> dict:
    import suite
    return suite.probe(path)


def song_path(value) -> Path | None:
    """A song: a Music take's id, or a sound file in the studio folder."""

    if value in (None, "", False):
        return None
    value = str(value)
    if ID.fullmatch(value) and HOME and (HOME / "music" / f"{value}.take.json").is_file():
        try:
            meta = json.loads((HOME / "music" / f"{value}.take.json").read_text())
            return inside(meta["path"])
        except (OSError, ValueError, KeyError, TypeError):
            raise bad(f"the song {value} cannot be read")
    path = inside(value)
    if not path.is_file() or path.suffix.lower() not in SOUNDS:
        raise bad(f"{value} is not a song of the Music tab or a sound file in the studio folder")
    return path


def plan_of(params: dict) -> dict:
    """The finish asked for, checked: every clip with its cut, the song, the narration, the title, the mix and the size.
    400 with the reason for anything that cannot be finished."""

    clips, name, song = params.get("clips"), "Finished cut", params.get("song")
    if params.get("project"):
        project_id = str(params["project"])
        if not ID.fullmatch(project_id):
            raise bad("project is a timeline's id")
        file = HOME / "projects" / project_id / "project.json"
        if not file.is_file():
            raise bad("no such timeline")
        project = json.loads(file.read_text())
        clips, name = project.get("timeline") or [], project.get("name") or project_id
        if "song" not in params:  # the timeline's own soundtrack, unless the finish names another (or none)
            said = project.get("soundtrack")
            song = said.get("take") if isinstance(said, dict) else said
    if not isinstance(clips, list) or not clips:
        raise bad("the timeline is empty: add clips to it first" if params.get("project") else "give the clips to finish")
    if len(clips) > 300:
        raise bad("a finish takes at most 300 clips")
    cut = []
    for clip in clips:
        if isinstance(clip, str):
            clip = {"path": clip}
        if not isinstance(clip, dict) or not isinstance(clip.get("path"), str):
            raise bad("each clip is {path, in?, out?} with the path of a clip in the studio folder")
        path = inside(clip["path"])
        if not path.is_file() or path.suffix.lower() not in CLIPS:
            raise bad(f"{clip['path']} is not a clip in the studio folder")
        for key in ("in", "out", "duration"):
            if clip.get(key) is not None and (isinstance(clip[key], bool) or not isinstance(clip[key], (int, float))):
                raise bad(f"a clip's {key} is a number of seconds")
        seen_ = probe(path)
        if seen_ and not seen_.get("video"):
            raise bad(f"{clip['path']} has no picture")
        whole = float(seen_.get("seconds") or clip.get("duration") or 0) or 5.0
        start = min(max(0.0, float(clip.get("in") or 0)), max(whole - 0.1, 0.0))
        end = float(clip.get("out") or 0)
        length = (min(end, whole) - start) if end > start else whole - start
        if length < 0.1:
            raise bad(f"{clip['path']}: the cut is shorter than a tenth of a second")
        cut.append({"path": str(path), "start": round(start, 3), "length": round(length, 3), "seconds": whole,
                    "audio": bool(seen_.get("audio", True)), "width": seen_.get("width"), "height": seen_.get("height")})
    song_file = song_path(song)
    voice = None
    if params.get("voice"):
        voice = inside(str(params["voice"]))
        if not voice.is_file() or voice.suffix.lower() not in SOUNDS:
            raise bad("voice is a narration sound file in the studio folder (WAV, MP3, M4A, FLAC)")
    title = params.get("title")
    if isinstance(title, str):
        title = {"text": title}
    if title is not None and not isinstance(title, dict):
        raise bad("title is {text, subtitle}")
    title = {"text": " ".join(str((title or {}).get("text") or "").split()), "subtitle": " ".join(str((title or {}).get("subtitle") or "").split())}
    if len(title["text"]) > 120 or len(title["subtitle"]) > 160:
        raise bad("a title is at most 120 characters and its subtitle 160")
    mix = params.get("mix") or "dialogue"
    if mix not in MIXES:
        raise bad("mix is dialogue, balanced or music")
    size = params.get("size") or "1080p"
    if size not in SIZES:
        raise bad("size is 720p, 1080p or 4k")
    try:
        fps = int(params.get("fps") or 24)
    except (TypeError, ValueError):
        raise bad("fps is 24, 25, 30 or 60")
    if fps not in RATES:
        raise bad("fps is 24, 25, 30 or 60")
    width, height = cut[0]["width"] or 1920, cut[0]["height"] or 1080
    canvas = [1920, 1080] if width >= height * 1.2 else [1080, 1920] if height >= width * 1.2 else [1080, 1080]
    return {"name": str(name)[:120], "clips": cut,
            "song": {"path": str(song_file), "seconds": probe(song_file).get("seconds")} if song_file else None,
            "voice": {"path": str(voice), "seconds": probe(voice).get("seconds")} if voice else None,
            "title": title if title["text"] else None, "captions": bool(params.get("captions")), "mix": mix,
            "size": size, "fps": fps, "canvas": canvas}


def build(params: dict, job_id: str, local_only: bool = False) -> tuple[list[str], dict, list[str]]:
    """(command, environment, output paths) of a finish job: the plan is written beside the job's other files and this
    file runs it. Refused (400) when Tesseract is not ready here or on the remote computer."""

    state = ready(local_only=True) if local_only else ready()
    if not state["ok"]:
        raise bad(state["why"])
    plan = plan_of(params)
    if local_only:
        import windows_features
        try:
            windows_features.check_finish(plan)
        except ValueError as error:
            raise bad(str(error)) from None
    exports = HOME / "exports"
    work = exports / ".finish" / job_id
    host = state["where"][4:] if state["where"].startswith("ssh:") else ""
    outputs = {kind: str(exports / f"{job_id}.{kind}") for kind in ("mp4", "tsrct", "fcpxml", "edl")}
    plan.update(id=job_id, work=str(work), outputs=outputs, tsrct=state["path"], host=host, machine=state.get("machine"),
                ssh=ssh_of(host) if host else None, scp=scp_of(host) if host else None, fonts=font_files(),
                linux=bool(state.get("linux")), ffmpeg=state.get("ffmpeg"))
    if plan["title"] and not plan["fonts"]["title"]:
        raise bad(f"no font for the title on this computer: run `{SETUP}`")
    if job_id != "check":  # the queue's check of the settings writes nothing; the job's turn writes its plan
        work.mkdir(parents=True, exist_ok=True)
        (work / "plan.json").write_text(json.dumps(plan, indent=1))
    return [sys.executable, str(Path(__file__).resolve()), "run", str(work / "plan.json")], {}, list(outputs.values())


# ---------- the Tesseract project (what tsrct is told) ----------

def timeline(plan: dict, durations: list[int] | None = None) -> dict:
    """Where everything sits, in milliseconds: {lead (the title scene), shots [{at, start, length, intrinsic}], total}.
    ``durations`` are the clips' lengths as Tesseract imported them; each cut stays inside its file."""

    lead = TITLE_MS if plan.get("title") else 0
    at, shots = lead, []
    for index, clip in enumerate(plan["clips"]):
        intrinsic = int((durations or [])[index] if durations and index < len(durations) else round(clip["seconds"] * 1000))
        start = min(int(round(clip["start"] * 1000)), max(intrinsic - 100, 0))
        length = max(40, min(int(round(clip["length"] * 1000)), intrinsic - start))
        shots.append({"at": at, "start": start, "length": length, "intrinsic": intrinsic})
        at += length
    return {"lead": lead, "shots": shots, "total": at}


def windowed(at: int, length: int, offset: int) -> dict:
    return {"type": "windowed", "inputRange": {"start": at, "duration": length},
            "mapping": {"type": "linear", "input": {"start": at, "duration": length}, "output": {"start": 0, "duration": length}},
            "inputOffsetMs": offset}


IDENTITY = {"anchorPoint": [0, 0], "position": [0, 0], "scale": [100, 100], "rotation": 0, "opacity": 100}
SONG_ID, VOICE_ID, SHOT_ID = 3, 2, 100
TITLE_IDS = {"card": 10, "title": 11, "line": 12, "subtitle": 13, "scene": 14}
CAPTION_ID = 1000


def fitted(width: int, height: int, canvas: list) -> dict:
    """A clip's frame at its own size, scaled and centred to fit the canvas whole (as the export job pads it). The frame
    keeps the source's own dimensions: the Premiere converter takes only that."""

    scale = min(canvas[0] / width, canvas[1] / height)
    return {**IDENTITY, "position": [round((canvas[0] - width * scale) / 2, 3), round((canvas[1] - height * scale) / 2, 3)],
            "scale": [round(scale * 100, 4), round(scale * 100, 4)]}


def document(base: dict, plan: dict, durations: list[int], sizes: list | None = None) -> dict:
    """The editable document Tesseract checks out, with the canvas, the length and the media layers of the cut: each
    clip a Video layer (its own sound at unity), the narration and the song Audio layers. Graphics and the song's
    envelope come after, as actions (``actions``). ``sizes`` are the clips' (width, height) as Tesseract imported them."""

    out = json.loads(json.dumps(base))
    place = timeline(plan, durations)
    width, height = plan["canvas"]
    layers = []
    for index, (clip, shot) in enumerate(zip(plan["clips"], place["shots"])):
        own = (sizes[index] if sizes and index < len(sizes) and all(sizes[index]) else None) or (clip.get("width") or width, clip.get("height") or height)
        layer = {"type": "Video", "id": SHOT_ID + index, "name": f"Shot {index + 1}: {Path(clip['path']).stem}"[:80],
                 "playback": windowed(shot["at"], shot["length"], shot["start"]),
                 "sourceRange": {"start": shot["start"], "duration": shot["length"]},
                 "sourceIntrinsicDuration": shot["intrinsic"], "transform": fitted(int(own[0]), int(own[1]), [width, height]),
                 "source": {"assetId": f"clip-{index + 1}", "fit": "contain", "sourceRect": {"x": 0, "y": 0, "width": int(own[0]), "height": int(own[1])}}}
        if clip.get("audio", True):
            layer["volume"] = 1.0  # the dialogue: each clip's own sound, at full level
        layers.append(layer)
    if plan.get("voice"):
        voice_ms = int(round((plan["voice"].get("seconds") or 0) * 1000)) or place["total"]
        length = max(40, min(voice_ms, place["total"] - place["lead"]))
        layers.append({"type": "Audio", "id": VOICE_ID, "name": "Narration", "playback": windowed(place["lead"], length, 0),
                       "sourceRange": {"start": 0, "duration": length}, "sourceIntrinsicDuration": voice_ms,
                       "source": {"assetId": "voice"}, "volume": 1.0, "captionsEnabled": False})
    if plan.get("song"):
        song_ms = int(round((plan["song"].get("seconds") or 0) * 1000)) or place["total"]
        length = max(40, min(song_ms, place["total"]))
        bed = 10 ** (MIXES[plan["mix"]][0] / 20)
        layers.append({"type": "Audio", "id": SONG_ID, "name": "Music", "playback": windowed(0, length, 0),
                       "sourceRange": {"start": 0, "duration": length}, "sourceIntrinsicDuration": song_ms,
                       "source": {"assetId": "song"}, "volume": round(bed, 4), "captionsEnabled": False})
    out["dimensions"] = {"width": width, "height": height}
    out["duration"] = round(place["total"] / 1000, 3)
    out.setdefault("composition", {"id": "main", "name": "Main"})
    out["composition"]["layers"] = layers
    out["composition"]["name"] = plan.get("name") or "Finished cut"
    return out


def duck_script(spans: list, mix: str, total_s: float, song_s: float) -> str:
    """The song's gain over time (a jsScript on its volume): its bed level, dipped by the mix's amount under each span
    of dialogue (0.25 s down before it, 0.6 s back up after), faded in over 0.6 s and out over the last 1.5 s."""

    bed_db, duck_db = MIXES[mix]
    bed, low = 10 ** (bed_db / 20), 10 ** (-duck_db / 20)
    end = min(total_s, song_s or total_s)
    marks = ",".join(f"[{a:.3f},{b:.3f}]" for a, b in spans[:2000])
    return (f"var t=input.time.seconds,S=[{marks}],bed={bed:.5f},low={low:.5f},end={end:.3f};"
            "var d=1;for(var i=0;i<S.length;i++){var a=S[i][0],b=S[i][1],g;"
            "if(t<a-0.25||t>b+0.6)continue;if(t<a)g=(a-t)/0.25;else if(t<=b)g=0;else g=(t-b)/0.6;"
            "var f=low+(1-low)*g;if(f<d)d=f;}"
            "var fi=Math.min(1,t/0.6),fo=Math.max(0,Math.min(1,(end-t)/1.5));return bed*d*fi*fo;")


def text_layer(layer_id: int, name: str, text: str, font: tuple, size: float, color, box: list, at: list, start: int,
               length: int, parent: int | None = None, justification: str = "center") -> dict:
    action = {"type": "createFxTextLayer", "compositionId": "main", "layerId": layer_id, "name": name, "insertIndex": 0,
              "activeRange": {"start": start, "duration": length},
              "transform": {**IDENTITY, "position": [round(at[0], 1), round(at[1], 1)]},
              "sourceText": {"text": text, "fontFamily": font[0], "fontStyle": font[1], "fontSize": round(size, 1),
                             "fillColor": color, "justification": justification, "boxText": True, "boxPosition": [0, 0],
                             "boxSize": [round(box[0], 1), round(box[1], 1)]}}
    if parent is not None:
        action["parentLayerId"] = parent
    return action


def rect_layer(layer_id: int, name: str, size: list, at: list, color, start: int, length: int) -> dict:
    return {"type": "createFxRectLayer", "compositionId": "main", "layerId": layer_id, "name": name, "insertIndex": 0,
            "activeRange": {"start": start, "duration": length},
            "transform": {**IDENTITY, "position": [round(at[0], 1), round(at[1], 1)]},
            "rect": {"size": [round(size[0], 1), round(size[1], 1)], "fillColor": color}}


def keys(layer_id: int, prop: str, points: list) -> dict:
    """Keyframes on one property: points are (ms, value), eased in to each."""

    frames = [{"id": f"finish-{layer_id}-{prop}-{i}", "layerTime": int(ms), "value": {"type": "float", "value": value},
               "easing": {"type": "cubicBezier", "x1": 0.2, "y1": 0, "x2": 0.2, "y2": 1} if i else {"type": "linear"}}
              for i, (ms, value) in enumerate(points)]
    return {"type": "setFxPropertyKeyframes", "compositionId": "main", "property": {"layerId": layer_id, "propertyType": prop},
            "keyframes": frames}


def actions(plan: dict, durations: list[int], fonts: dict, spans: list, captions: list) -> list[dict]:
    """The action batch applied after the document: the title scene (a card, the title, a line, the subtitle, grouped,
    fading and rising in), the captions, and the song's envelope. ``fonts`` are (family, style) per role, as Tesseract
    imported them; ``spans`` the dialogue in seconds on the cut's clock; ``captions`` [{start, end, text}]."""

    place = timeline(plan, durations)
    width, height = plan["canvas"]
    small = min(width, height)
    out = []
    title = plan.get("title")
    if title:
        lead, ids = place["lead"], TITLE_IDS
        size = small * (0.11 if width > height else 0.095)
        box_w, x = width * 0.84, width * 0.08
        y = height * 0.5 - size * (1.25 if title.get("subtitle") else 0.9)
        members = [ids["card"], ids["title"], ids["line"]]
        out.append(rect_layer(ids["card"], "Title card", [width, height], [0, 0], [0.02, 0.02, 0.025, 1], 0, lead))
        out.append(text_layer(ids["title"], "Title", title["text"], fonts["title"], size, [1, 1, 1, 1], [box_w, size * 2.6],
                              [x, y], 0, lead))
        line_y = y + size * 1.45
        out.append(rect_layer(ids["line"], "Title line", [small * 0.08, max(2, small * 0.003)], [width / 2 - small * 0.04, line_y],
                              [0.85, 0.62, 0.3, 1], 0, lead))
        if title.get("subtitle"):
            members.insert(0, ids["subtitle"])
            out.append(text_layer(ids["subtitle"], "Subtitle", title["subtitle"], fonts["text"], size * 0.32, [0.78, 0.78, 0.8, 1],
                                  [box_w, size * 1.2], [x, line_y + size * 0.35], 0, lead))
        out.append({"type": "groupFxCompositionLayers", "compositionId": "main", "layerIds": members, "groupLayerId": ids["scene"],
                    "name": "Title scene", "transform": dict(IDENTITY)})
        out.append(keys(ids["scene"], "opacity", [(0, 0), (600, 100), (lead - 600, 100), (lead - 20, 0)]))
        out.append(keys(ids["title"], "positionY", [(0, round(y + size * 0.25, 1)), (1100, round(y, 1))]))
        out.append(keys(ids["line"], "opacity", [(0, 0), (500, 0), (1200, 100)]))
        if title.get("subtitle"):
            out.append(keys(ids["subtitle"], "opacity", [(0, 0), (700, 0), (1500, 100)]))
    if captions:
        size = small * 0.045
        box_w, x, y = width * 0.8, width * 0.1, height * (0.8 if width > height else 0.74)
        for index, line in enumerate(captions[:400]):
            start, end = int(line["start"] * 1000), int(line["end"] * 1000)
            start, end = max(start, place["lead"]), min(end, place["total"])
            if end - start < 200:
                continue
            band = CAPTION_ID + 2 * index
            out.append(rect_layer(band, f"Caption {index + 1} band", [box_w + size, size * 2.9], [x - size / 2, y - size * 0.45],
                                  [0, 0, 0, 0.5], start, end - start))
            out.append(text_layer(band + 1, f"Caption {index + 1}", line["text"], fonts["text"], size, [1, 1, 1, 1],
                                  [box_w, size * 2.6], [x, y], start, end - start))
    if plan.get("song"):
        song_s = (plan["song"].get("seconds") or 0) or place["total"] / 1000
        out.append({"type": "setFxPropertyAnimator", "compositionId": "main", "property": {"layerId": SONG_ID, "propertyType": "volume"},
                    "dependencies": [], "animator": {"type": "jsScript", "layerTimeJsCode": duck_script(spans, plan["mix"], place["total"] / 1000, song_s)}})
    return out


# ---------- the dialogue and the captions, measured on this computer ----------

SILENCE = re.compile(r"silence_(start|end): (-?[0-9.]+)")


def heard(path: str, start: float, length: float, ffmpeg: str = "ffmpeg") -> list[list[float]]:
    """The spans of a stretch of a file where there is sound above a speaking level (seconds from the stretch's start)."""

    try:
        done = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", path,
                               "-vn", "-af", "silencedetect=noise=-32dB:d=0.35", "-f", "null", "-"],
                              capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode:
        return []
    spans, at = [], 0.0
    for kind, value in SILENCE.findall(done.stderr):
        value = max(0.0, min(float(value), length))
        if kind == "start":
            if at is not None and value - at > 0.15:
                spans.append([at, value])
            at = None
        else:
            at = value
    if at is not None and length - at > 0.15:
        spans.append([at, length])
    return spans


def merged(spans: list, gap: float = 0.5) -> list[list[float]]:
    out = []
    for a, b in sorted(spans):
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [[round(a, 3), round(b, 3)] for a, b in out]


def dialogue(plan: dict, durations: list[int] | None = None) -> list[list[float]]:
    """Where the cut has sound of its own (each clip's, and the narration), on the cut's clock in seconds: where the
    song dips."""

    ffmpeg = shutil.which("ffmpeg") or next((p for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg") if Path(p).is_file()), None)
    if not ffmpeg:
        return []
    place, spans = timeline(plan, durations), []
    for clip, shot in zip(plan["clips"], place["shots"]):
        if clip.get("audio", True):
            spans += [[shot["at"] / 1000 + a, shot["at"] / 1000 + b] for a, b in heard(clip["path"], shot["start"] / 1000, shot["length"] / 1000, ffmpeg)]
    if plan.get("voice"):
        lead = place["lead"] / 1000
        length = min(plan["voice"].get("seconds") or place["total"] / 1000, place["total"] / 1000 - lead)
        spans += [[lead + a, lead + b] for a, b in heard(plan["voice"]["path"], 0.0, length, ffmpeg)]
    return merged(spans)


def phrases(text: str, start: float, end: float, words: int = 8) -> list[dict]:
    """A heard segment as caption lines of at most ``words`` words, each given its share of the time."""

    said = text.split()
    if not said or end <= start:
        return []
    chunks = [said[i:i + words] for i in range(0, len(said), words)]
    out, at, total = [], start, len(said)
    for chunk in chunks:
        span = (end - start) * len(chunk) / total
        out.append({"start": round(at, 3), "end": round(at + span, 3), "text": " ".join(chunk)})
        at += span
    return out


def caption_lines(plan: dict, durations: list[int] | None = None, say=print) -> list[dict]:
    """The words a local faster-whisper hears in each clip's cut and in the narration, as timed caption lines on the
    cut's clock. [] (and a note) when this computer has no speech-to-text model: captions are never made up."""

    try:
        from faster_whisper import WhisperModel
        import numpy as np
        import wave
    except ImportError:
        say("[tensorfold] finish: captions left out: no speech-to-text here (pip install faster-whisper)")
        return []
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    model = WhisperModel(os.environ.get("FINISH_WHISPER_MODEL") or os.environ.get("QA_WHISPER_MODEL") or "large-v3-turbo",
                         device="cpu", compute_type="int8")
    place, lines = timeline(plan, durations), []
    pieces = [(clip["path"], shot["start"] / 1000, shot["length"] / 1000, shot["at"] / 1000)
              for clip, shot in zip(plan["clips"], place["shots"]) if clip.get("audio", True)]
    if plan.get("voice"):
        pieces.append((plan["voice"]["path"], 0.0, place["total"] / 1000 - place["lead"] / 1000, place["lead"] / 1000))
    with tempfile.TemporaryDirectory() as scratch:
        for path, start, length, at in pieces:
            sound = Path(scratch) / "piece.wav"
            done = subprocess.run([ffmpeg, "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", path, "-vn",
                                   "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(sound)], capture_output=True, text=True)
            if done.returncode or not sound.is_file():
                continue
            with wave.open(str(sound)) as handle:
                samples = np.frombuffer(handle.readframes(handle.getnframes()), np.int16).astype(np.float32) / 32768.0
            parts, _ = model.transcribe(samples, beam_size=5)
            for part in parts:
                lines += phrases(part.text.strip(), at + part.start, at + min(part.end, length))
    return lines


# ---------- the runner ----------

class Stop(RuntimeError):
    pass


def safe_name(index: int, path: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(path).stem)[:40].strip("-.") or "media"
    return f"{index:03d}-{stem}{Path(path).suffix.lower()}"


class Host:
    """Where tsrct runs: this computer (``work`` holds the project; media by its own paths) or the remote computer over
    ssh (a folder of its own there holds the project and a copy of every input)."""

    def __init__(self, plan: dict, say):
        self.plan, self.say, self.remote = plan, say, plan.get("host") or ""
        self.work = Path(plan["work"])
        self.folder = None  # the folder on the remote computer
        self.names: dict[str, str] = {}

    def run(self, args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
        if self.remote:
            line = REMOTE_PATH + f"cd {shlex.quote(self.folder)} && " + " ".join(shlex.quote(a) for a in args)
            return subprocess.run([*self.plan["ssh"], line], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return subprocess.run(args, cwd=self.work, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)

    def popen(self, args: list[str]) -> subprocess.Popen:
        if self.remote:
            line = REMOTE_PATH + f"cd {shlex.quote(self.folder)} && " + " ".join(shlex.quote(a) for a in args)
            return subprocess.Popen([*self.plan["ssh"], line], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
        return subprocess.Popen(args, cwd=self.work, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)

    def tsrct(self, *args: str) -> dict:
        done = self.run([self.plan["tsrct"], *args])
        if done.returncode:
            said = (done.stderr.strip() or done.stdout.strip())[-600:]
            raise Stop(f"Tesseract stopped at `{args[0]} {args[1] if len(args) > 1 else ''}`: {said}")
        lines = [line for line in done.stdout.splitlines() if line.strip().startswith("{")]
        return json.loads(lines[-1]) if lines else {}

    def media(self, path: str) -> str:
        """The name tsrct is given for an input: its own path here, or the copy's name in the remote folder."""

        return self.names.get(path, path) if self.remote else path

    def send(self, paths: list[str]) -> None:
        if not self.remote:
            return
        made = subprocess.run([*self.plan["ssh"], "mkdir -p ~/.imagination-studio/finish && d=$(mktemp -d ~/.imagination-studio/finish/"
                               + self.plan["id"] + ".XXXXXX) && mkdir -p \"$d/media\" && echo \"$d\""],
                              capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        folder = made.stdout.strip()
        if made.returncode or not folder.startswith("/"):
            raise Stop(f"could not make a working folder on {self.remote}: " + (made.stderr.strip()[-300:] or "no answer"))
        self.folder = folder
        scp, name = self.plan["scp"]
        for index, path in enumerate(dict.fromkeys(paths)):
            self.names[path] = "media/" + safe_name(index, path)
            sent = subprocess.run([*scp, path, f"{name}:{folder}/{self.names[path]}"], capture_output=True, text=True, timeout=3600)
            if sent.returncode:
                raise Stop(f"could not copy {Path(path).name} to {self.remote}: " + sent.stderr.strip()[-300:])

    def fetch(self, name: str, target: Path) -> None:
        if not self.remote:
            if (self.work / name).resolve() != target.resolve():
                shutil.move(str(self.work / name), str(target))
            return
        scp, host = self.plan["scp"]
        part = target.with_name(target.name + ".part")
        got = subprocess.run([*scp, f"{host}:{self.folder}/{name}", str(part)], capture_output=True, text=True, timeout=3600)
        if got.returncode or not part.is_file():
            part.unlink(missing_ok=True)
            raise Stop(f"could not copy {name} back from {self.remote}: " + got.stderr.strip()[-300:])
        part.replace(target)

    def clean(self) -> None:
        if self.remote and self.folder and "/.imagination-studio/finish/" in self.folder:
            subprocess.run([*self.plan["ssh"], f"rm -rf {shlex.quote(self.folder)}"], capture_output=True, timeout=60, stdin=subprocess.DEVNULL)


def resolve_files(plan: dict, durations: list[int]) -> None:
    """The same cut as FCPXML and EDL, for DaVinci Resolve (suite.fcpxml, suite.edl), naming the clips where they are."""

    import suite
    place = timeline(plan, durations)
    clips = [{"path": Path(clip["path"]), "in": shot["start"] / 1000, "out": (shot["start"] + shot["length"]) / 1000,
              "duration": None, "seconds": None, "file_seconds": clip["seconds"], "audio": clip.get("audio", True)}
             for clip, shot in zip(plan["clips"], place["shots"])]
    song = {"path": Path(plan["song"]["path"]), "seconds": plan["song"].get("seconds")} if plan.get("song") else None
    rate = (plan["fps"], 1)
    Path(plan["outputs"]["fcpxml"]).write_text(suite.fcpxml(plan["name"], clips, rate, tuple(plan["canvas"]), song))
    Path(plan["outputs"]["edl"]).write_text(suite.edl(plan["name"], clips, rate))


def run_plan(plan: dict, say=None) -> dict:
    """Finish one cut: measure the dialogue (and hear the captions), build the Tesseract project, render it, and write
    the Resolve files. Prints the job's progress lines."""

    say = say or (lambda line: print(line, flush=True))
    work = Path(plan["work"])
    work.mkdir(parents=True, exist_ok=True)
    host = Host(plan, say)
    where = f"on {plan.get('machine') or plan['host']} over ssh" if plan.get("host") else "on this computer"
    say(f"[tensorfold] finish: {len(plan['clips'])} clips, mix {plan['mix']}, {plan['size']} at {plan['fps']} fps, Tesseract {where}")
    say("[tensorfold] finish: measuring the dialogue")
    spans = dialogue(plan) if plan.get("song") else []
    captions = caption_lines(plan, say=say) if plan.get("captions") else []
    if plan.get("song"):
        say(f"[tensorfold] finish: the music dips under {len(spans)} stretches of dialogue")
    if plan.get("captions"):
        say(f"[tensorfold] finish: {len(captions)} caption lines heard")
    title = plan.get("title") or {}
    roles = ({"title"} if title else set()) | ({"text"} if title.get("subtitle") or captions else set())
    fonts_needed = {role: path for role, path in (plan.get("fonts") or {}).items() if path and role in roles}
    if captions and not fonts_needed:
        say(f"[tensorfold] finish: captions left out: no font for them on this computer (`{SETUP}`)")
        captions = []
    inputs = [clip["path"] for clip in plan["clips"]] + [plan[k]["path"] for k in ("song", "voice") if plan.get(k)] + list(fonts_needed.values())
    try:
        if plan.get("host"):
            say(f"[tensorfold] finish: copying {len(set(inputs))} files to {plan.get('machine') or plan['host']}")
        host.send(inputs)
        say("[tensorfold] finish: building the Tesseract project")
        project = "cut.tsrct"
        if host.remote:
            host.run(["rm", "-f", project])
        else:
            (work / project).unlink(missing_ok=True)
        host.tsrct("project", "create", "--project", project)
        durations, sizes = [], []
        for index, clip in enumerate(plan["clips"]):
            said = host.tsrct("project", "import-video", "--project", project, "--file", host.media(clip["path"]), "--asset-id", f"clip-{index + 1}")
            durations.append(int(said.get("durationMs") or round(clip["seconds"] * 1000)))
            sizes.append((said.get("width"), said.get("height")))
        for kind in ("song", "voice"):
            if plan.get(kind):
                host.tsrct("project", "import-asset", "--project", project, "--file", host.media(plan[kind]["path"]), "--asset-id", kind, "--kind", "audio")
        fonts = {}
        for role, path in fonts_needed.items():
            said = host.tsrct("project", "import-font", "--project", project, "--file", host.media(path))
            fonts[role] = (said.get("fontFamily"), said.get("fontStyle") or "Regular")
        fonts.setdefault("text", fonts.get("title"))
        fonts.setdefault("title", fonts.get("text"))
        host.tsrct("project", "checkout", "--project", project, "--output", "editable.json")
        base = read_back(host, "editable.json")
        doc = document(base, plan, durations, sizes)
        batch = actions(plan, durations, fonts, spans, captions)
        (work / "document.json").write_text(json.dumps(doc, indent=1))
        (work / "actions.json").write_text(json.dumps(batch, indent=1))
        put_up(host, work / "document.json", "document.json")
        put_up(host, work / "actions.json", "actions.json")
        host.tsrct("project", "commit", "--project", project, "--file", "document.json")
        if batch:
            host.tsrct("project", "apply", "--project", project, "--actions", "actions.json")
        say(f"[tensorfold] finish: rendering {timeline(plan, durations)['total'] / 1000:.1f} s")
        render(host, plan, project, say)
        say("[tensorfold] finish: writing the cut and its project")
        outputs = plan["outputs"]
        host.fetch("cut.mp4", Path(outputs["mp4"]))
        host.fetch(project, Path(outputs["tsrct"]))
        resolve_files(plan, durations)
    finally:
        host.clean()
    say("[tensorfold] finish: done")
    return {"spans": spans, "captions": len(captions)}


def read_back(host: Host, name: str) -> dict:
    if host.remote:
        done = host.run(["cat", name], timeout=120)
        if done.returncode:
            raise Stop(f"could not read {name} on {host.remote}")
        return json.loads(done.stdout)
    return json.loads((host.work / name).read_text())


def put_up(host: Host, local: Path, name: str) -> None:
    """A file written here, into the folder tsrct works in."""

    if not host.remote:
        if local.resolve() != (host.work / name).resolve():
            shutil.copyfile(local, host.work / name)
        return
    scp, target = host.plan["scp"]
    sent = subprocess.run([*scp, str(local), f"{target}:{host.folder}/{name}"], capture_output=True, text=True, timeout=300)
    if sent.returncode:
        raise Stop(f"could not copy {name} to {host.remote}: " + sent.stderr.strip()[-300:])


def render(host: Host, plan: dict, project: str, say) -> None:
    """``tsrct export --json``, its progress (NDJSON on standard error) said as ``[tensorfold] step n/m`` lines."""

    args = [plan["tsrct"], "export", "--project", project, "--output", "cut.mp4", "--resolution", plan["size"],
            "--fps", str(plan["fps"]), "--json"]
    if plan.get("linux") and plan.get("ffmpeg"):  # the Linux bundle has no H.264 encoder of its own
        args += ["--encoder-backend", "external-ffmpeg-command", "--ffmpeg-path", plan["ffmpeg"]]
    process = host.popen(args)
    said, last = [], -1
    for line in process.stderr:
        said.append(line)
        try:
            event = json.loads(line)
        except ValueError:
            continue
        done, total = event.get("completed"), event.get("total")
        if isinstance(done, int) and isinstance(total, int) and total > 0 and (done - last >= max(1, total // 50) or done == total):
            say(f"[tensorfold] step {done}/{total}")
            last = done
    out = process.stdout.read()
    if process.wait():
        lines = [line.strip() for line in said if line.strip()]
        why = lines[-1] if lines else out.strip()
        try:
            why = json.loads(why).get("message") or why
        except (ValueError, AttributeError):
            pass
        raise Stop("Tesseract could not render the cut: " + str(why)[-500:])


# ---------- Premiere Pro and After Effects ----------

def converter_where(state: dict) -> tuple[str, str]:
    """(path, host) of the converter: this computer's, or the remote computer's ("" host for here)."""

    conv = state.get("converter") or {}
    if not conv.get("ok"):
        raise bad(f"Tesseract Converter is not installed: run `{SETUP}`" + (" on the Mac" if not runs_here() else "")
                  + ". Resolve needs nothing more: use FCPXML or the zip.")
    where = conv.get("where", "here")
    return conv["path"], where[4:] if where.startswith("ssh:") else ""


def convert(job_id: str, to: str) -> dict:
    """The finished cut's Tesseract project as a Premiere Pro (.prproj) or After Effects (.aep) project with its media,
    zipped into exports/<id>-<to>.zip. The converter's warnings come back as notes."""

    if not ID.fullmatch(job_id or ""):
        raise bad("no such finish")
    if to not in TARGETS:
        raise bad("to is premiere or aftereffects")
    source = HOME / "exports" / f"{job_id}.tsrct"
    if not source.is_file():
        raise bad("this finish has no Tesseract project: finish the cut first")
    target = HOME / "exports" / f"{job_id}-{to}.zip"
    if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
        return {"path": relative(target), "to": to, "notes": [], "kept": True}
    fps = 24
    try:
        fps = int(json.loads((HOME / "exports/.finish" / job_id / "plan.json").read_text()).get("fps") or 24)
    except (OSError, ValueError, TypeError):
        pass
    fmt, label = TARGETS[to]
    args = ["convert", "", "--to", fmt, "--output", "", "--json"]
    if (to == "premiere" and fps in PREMIERE_RATES) or to == "aftereffects":
        args += ["--fps", str(fps)]
    conv, host = converter_where(ready())
    with tempfile.TemporaryDirectory() as scratch:
        out = Path(scratch) / f"{job_id}-{to}"
        if host:
            scp, name = scp_of(host)
            made = subprocess.run([*ssh_of(host), "mkdir -p ~/.imagination-studio/finish && mktemp -d ~/.imagination-studio/finish/convert.XXXXXX"],
                                  capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
            folder = made.stdout.strip()
            if made.returncode or not folder.startswith("/"):
                raise bad(f"could not make a working folder on {host}")
            try:
                sent = subprocess.run([*scp, str(source), f"{name}:{folder}/cut.tsrct"], capture_output=True, text=True, timeout=3600)
                if sent.returncode:
                    raise bad(f"could not copy the project to {host}: " + sent.stderr.strip()[-200:])
                args[1], args[5] = "cut.tsrct", "out"
                done = subprocess.run([*ssh_of(host), REMOTE_PATH + f"cd {shlex.quote(folder)} && " + " ".join(shlex.quote(a) for a in [conv, *args])],
                                      capture_output=True, text=True, timeout=1800, stdin=subprocess.DEVNULL)
                if not done.returncode:
                    got = subprocess.run([*scp, "-r", f"{name}:{folder}/out", str(out)], capture_output=True, text=True, timeout=3600)
                    if got.returncode:
                        raise bad(f"could not copy the {label} project back from {host}: " + got.stderr.strip()[-200:])
            finally:
                subprocess.run([*ssh_of(host), f"rm -rf {shlex.quote(folder)}"], capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        else:
            args[1], args[5] = str(source), str(out)
            done = subprocess.run([conv, *args], capture_output=True, text=True, timeout=1800)
        if done.returncode:
            raise bad(f"the {label} project could not be made: " + conversion_error(done.stderr, done.stdout))
        notes = conversion_notes(done.stdout)
        if not out.is_dir():
            raise bad(f"the converter wrote no {label} project")
        part = target.with_name(target.name + ".part")
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as bundle:
            for file in sorted(out.rglob("*")):
                if file.is_file():
                    bundle.write(file, f"{job_id}-{to}/{file.relative_to(out)}")
            bundle.writestr(f"{job_id}-{to}/README.txt", readme(label, notes))
        part.replace(target)
    return {"path": relative(target), "to": to, "notes": notes}


def conversion_error(stderr: str, stdout: str) -> str:
    for line in reversed((stderr or "").splitlines() + (stdout or "").splitlines()):
        try:
            said = json.loads(line)
        except ValueError:
            continue
        if isinstance(said, dict) and said.get("message"):
            return str(said["message"])[-400:]
    return ((stderr or stdout or "").strip() or "no reason given")[-400:]


def conversion_notes(stdout: str) -> list[str]:
    try:
        said = json.loads(stdout)
    except ValueError:
        return []
    notes = [str(d.get("message") or "").replace("warning: ", "", 1) for d in said.get("diagnostics") or [] if isinstance(d, dict)]
    return [n for n in dict.fromkeys(notes) if n][:20]


def readme(label: str, notes: list[str]) -> str:
    opener = "project.prproj" if label == "Premiere Pro" else "project.aep"
    return (f"The finished cut as a {label} project, made by Tesseract Converter from the Studio's Tesseract project.\n\n"
            f"Unzip, keep the folder together, and open {opener} in {label}. Fonts in fonts/ need installing first.\n"
            "Some features are approximated or left out by the converter: check the cut against the finished MP4.\n\n"
            + ("What the converter said:\n- " + "\n- ".join(notes) + "\n" if notes else ""))


# ---------- the routes ----------

def attach(home: Path, pack: Path, within, short, windows=False):
    """The routes, with the server's path helpers. ``build`` is called by server.build for kind "finish"."""

    global HOME, PACK, inside, relative, Request  # Request: the routes' annotations are read from this module's names
    from fastapi import APIRouter, Request

    import agents

    HOME, PACK, inside, relative = home, pack, within, short
    router = APIRouter()

    @router.get("/api/finish")
    def state(request: Request, fresh: bool = False) -> dict:
        limited = bool(windows and windows())
        if not limited and remote_host():  # the probe runs ssh with the user's keys: this computer or its Tailscale address, as the brains
            agents.local(request)
        out = ready(fresh, local_only=True) if limited else ready(fresh)
        if limited:
            import windows_features
            qualified = windows_features.status("finish")
            out = {**out, "ok": qualified["available"], "why": qualified["why"],
                   "converter": {**out["converter"], "ok": False,
                                 "why": "Editor conversion is not qualified in this Windows profile."}}
            return {**out, "fonts": {role: bool(path) for role, path in font_files().items()}, "mixes": list(MIXES),
                    "sizes": ["720p"], "rates": [windows_features.FPS], "max_seconds": windows_features.MAX_SECONDS}
        return {**out, "fonts": {role: bool(path) for role, path in font_files().items()}, "mixes": list(MIXES),
                "sizes": list(SIZES), "rates": list(RATES)}

    @router.post("/api/finish/{job_id}/convert")
    def to_editor(job_id: str, body: dict, request: Request) -> dict:
        agents.local(request, True)  # the converter may run over ssh with the user's keys, as the brains routes
        return convert(job_id, str((body or {}).get("to") or ""))

    return router


if __name__ == "__main__":
    if sys.argv[1:2] == ["run"] and len(sys.argv) == 3:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            run_plan(json.loads(Path(sys.argv[2]).read_text()))
        except Stop as error:
            print(f"[tensorfold] finish: {error}", flush=True)
            print(str(error), flush=True)
            sys.exit(1)
    else:
        print("usage: finish.py run <plan.json>", file=sys.stderr)
        sys.exit(2)
