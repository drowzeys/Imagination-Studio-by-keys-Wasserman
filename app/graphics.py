"""Motion graphics: titles, lower thirds, kinetic type, captions, slideshows, music videos and explainers, made from the
Library's own clips, pictures, voice lines and songs and rendered to MP4 on this machine with HyperFrames.

HyperFrames (the open-source `hyperframes` npm package, Apache-2.0) renders a web page frame by frame in a headless
Chromium and encodes the frames with ffmpeg. Each kind of graphic is a page of its own in app/graphics/<template>/,
written to HyperFrames' composition contract with GSAP for the motion. A job copies its template to
$STUDIO_HOME/graphics/<job>/, writes what to show into data.json (and the same object as data.js, which the page reads
before it draws), links in the media it uses, sets the page's size and length, and renders it to renders/<job>.mp4.
Nothing leaves the machine: no cloud render, no hosted voice or music; the voice is a Voice tab take and the music a
Music tab take.

Installed by scripts/setup-graphics.sh (`oneshot-setup.sh --graphics`, `oneshot-setup-spark.sh --graphics`) under
$PREFIX/graphics (or $GRAPHICS_DIR): a private Node 22 when the system's is older, the pinned hyperframes and GSAP, and
the browser it renders with (a Chromium already in ~/.cache/ms-playwright is used where there is one: on a DGX Spark,
Linux on arm64, HyperFrames' own download has no build). Captions need the clip's words with their times: faster-whisper
when the Studio's Python has it, else the whisper command (as the QA gate), cached beside the clip as <stem>.words.json.
"""

from __future__ import annotations

import json
import hashlib
from fractions import Fraction
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

KINDS = ("graphics",)
HYPERFRAMES = "0.8.186"   # the pinned hyperframes (scripts/setup-graphics.sh installs this one)
GSAP = "3.14.2"
WINDOWS = sys.platform == "win32"
WINDOWS_TEMPLATES = ("title", "lower-third", "kinetic", "slideshow", "music-video", "explainer")
WINDOWS_SECONDS = 10
WINDOWS_COMMIT_MB = 4096
WINDOWS_PROBE_SHA256 = "9fb1ffb62d77aec5585216f70d7a06f523d28a92312f566a0ed662f50dc01f63"
TEMPLATES = Path(__file__).with_name("graphics")
FONTS = Path(__file__).with_name("static") / "fonts"
FONT_FILES = ("geist.woff2", "instrument-serif.woff2", "instrument-serif-italic.woff2", "OFL-geist.txt", "OFL-instrument-serif.txt")
FPS = 24
# what each template makes, in a line, and what it needs from the Library
CATALOG = {
    "title": ("Title", "A film title and a subtitle, dark and slow, over black or over a clip"),
    "lower-third": ("Lower third", "A name and a role laid over a clip"),
    "kinetic": ("Kinetic type", "A sentence word by word, on the beat of a song"),
    "captions": ("Captions", "A clip's speech burned in, word by word as it is said"),
    "slideshow": ("Slideshow", "Pictures with a slow push-in, with a voice line or a song under them"),
    "music-video": ("Music video", "Clips cut on the beat of a song, with flash and whip cuts"),
    "explainer": ("Explainer", "Sections of words and pictures timed to a voice-over"),
}
SIZES = {"1080p": (1920, 1080), "720p": (1280, 720), "vertical": (1080, 1920), "square": (1080, 1080), "4k": (3840, 2160)}
VIDEO = (".mp4", ".mov", ".m4v", ".webm")
PICTURE = (".png", ".jpg", ".jpeg", ".webp")
SOUND = (".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac")
LONGEST = 600.0          # seconds: the longest graphic made here
DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", str(Path.home() / ".local/bin"))


class Refused(ValueError):
    """A graphic that cannot be made as asked, said in words for the page."""


# ---------- where things are ----------

def prefix() -> Path:
    return Path(os.environ.get("PREFIX") or Path.home() / ".local/opt/tensorfold-studio").expanduser()


def install_dir() -> Path:
    if WINDOWS and not os.environ.get("GRAPHICS_DIR"):
        return Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio" / "graphics"
    return Path(os.environ.get("GRAPHICS_DIR") or prefix() / "graphics").expanduser()


def search_path() -> str:
    return os.pathsep.join([os.environ.get("PATH", ""), *DIRS])


def node_version(binary: str | Path) -> tuple[int, ...] | None:
    try:
        said = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.match(r"v?(\d+)\.(\d+)\.(\d+)", said)
    return tuple(int(part) for part in found.groups()) if found else None


# the oldest Node of each line with the latest security fixes (Node's 2026-10-21 release): an older one is never run
NODE_SAFE = {22: (22, 23, 4), 24: (24, 22, 1), 26: (26, 12, 1)}


def node_safe(version: tuple | None) -> bool:
    return bool(version) and (version >= NODE_SAFE[version[0]] if version[0] in NODE_SAFE else version[0] > max(NODE_SAFE))


def node_bin() -> tuple[str | None, str]:
    """(the Node that runs HyperFrames, its version): the private one setup-graphics.sh installed, else the system's,
    each only when it has the latest security fixes of its line; (None, what was found) otherwise."""

    marker = install_dir() / "node-path"
    private = Path(marker.read_text(encoding="utf-8").strip()) if WINDOWS and marker.is_file() else install_dir() / "node" / "bin" / "node"
    found = []
    for node in ([private] if private.is_file() and os.access(private, os.X_OK) else []) + [shutil.which("node", path=search_path())]:
        if not node:
            continue
        version = node_version(node)
        if node_safe(version):
            return str(node), ".".join(map(str, version))
        found.append("Node " + ".".join(map(str, version)) if version else "a Node that does not start")
    return None, (" and ".join(found) + ", without the latest security fixes") if found else "no Node"


def cli() -> Path:
    return install_dir() / "node_modules" / "hyperframes" / "dist" / "cli.js"


def gsap_file() -> Path:
    return install_dir() / "node_modules" / "gsap" / "dist" / "gsap.min.js"


def installed_version() -> str | None:
    try:
        return json.loads((install_dir() / "node_modules" / "hyperframes" / "package.json").read_text()).get("version")
    except (OSError, ValueError):
        return None


def browser() -> str | None:
    """The Chromium HyperFrames renders with: $HYPERFRAMES_BROWSER_PATH, the one setup-graphics.sh chose
    ($GRAPHICS_DIR/browser-path), or HyperFrames' own download in ~/.cache/hyperframes."""

    chosen = os.environ.get("HYPERFRAMES_BROWSER_PATH")
    if not chosen and (install_dir() / "browser-path").is_file():
        chosen = (install_dir() / "browser-path").read_text(encoding="utf-8").strip()
    if chosen:
        return chosen if Path(chosen).is_file() and os.access(chosen, os.X_OK) else None
    own = sorted((Path.home() / ".cache/hyperframes/chrome").glob("chrome-headless-shell/*/*/chrome-headless-shell"))
    return str(own[-1]) if own else None


def ready() -> dict:
    """Whether a graphic can be rendered here: {ok, node, hyperframes, browser, gsap, ffmpeg, why}."""

    runtime_updated = False
    qualified = False
    node, node_said = node_bin()
    version = installed_version() if cli().is_file() else None
    found = browser()
    ffmpeg = shutil.which("ffmpeg", path=search_path())
    why = ("HyperFrames needs Node 22 or newer with the latest security fixes (this machine has " + node_said
           + ")" if not node
           else "HyperFrames is not installed" if not version
           else f"Windows graphics needs HyperFrames {HYPERFRAMES}" if WINDOWS and version != HYPERFRAMES
           else "GSAP is not installed beside HyperFrames" if not gsap_file().is_file()
           else "no browser to render with" if not found
           else "ffmpeg is not installed" if not ffmpeg
           else "ffprobe is not installed" if WINDOWS and not shutil.which("ffprobe", path=search_path()) else "")
    if not why and WINDOWS:
        why = windows_probe_error()
    prerequisites_ok = not why
    if not why and WINDOWS:
        compatible = False
        try:
            receipt = json.loads((install_dir() / "windows-checked.json").read_text(encoding="utf-8"))
            if not isinstance(receipt, dict):
                raise ValueError("the graphics check is not an object")
            if receipt.get("visible_windows_added") != 0:
                why = "The Windows browser-isolation check has not passed"
            elif (not isinstance(receipt.get("fingerprint"), list) or not receipt["fingerprint"]
                  or receipt.get("size") != [1280, 720] or receipt.get("fps") != 24
                  or not isinstance(receipt.get("results"), list)
                  or sorted(row["template"] for row in receipt["results"]) != sorted(legacy_windows_compatibility()["templates"])):
                why = "The saved Windows graphics check is incomplete"
            else:
                from feature_checks import matches_qualification
                current = windows_fingerprint()
                runtime_updated = receipt.get("fingerprint") != current
                compatible = matches_qualification(receipt.get("fingerprint"), current,
                                                    receipt.get("compatibility", legacy_windows_compatibility()),
                                                    windows_compatibility())
            if not why and not compatible:
                why = "The graphics runtime, browser or templates changed; repeat the Windows graphics check"
        except FileNotFoundError:
            why = "The bounded Windows graphics render check has not passed"
        except (OSError, ValueError, KeyError, TypeError) as error:
            why = f"The Windows graphics check is unreadable: {type(error).__name__}"
        qualified = not why
        if prerequisites_ok:
            from windows_runtime import mode
            if not mode()["enabled"]:
                why = ""
    if why and WINDOWS:
        why += ": run `powershell -ExecutionPolicy Bypass -File scripts\\setup-windows-graphics.ps1`."
    elif why:
        why += ": run `bash scripts/setup-graphics.sh` (or oneshot-setup" + ("-spark" if sys.platform.startswith("linux") else "") + ".sh --graphics)."
    return {"ok": not why, "node": node, "node_version": node_said if node else None, "hyperframes": version,
            "pinned": HYPERFRAMES, "browser": found, "gsap": gsap_file().is_file(), "ffmpeg": ffmpeg, "why": why or None,
            "runtime_updated": runtime_updated, "qualified": qualified if WINDOWS else not why}


WINDOWS_QUALIFICATION_CONTRACT = 1


def legacy_windows_compatibility() -> dict:
    return {"contract": 1, "job_commit_mb": 4096, "size": [1280, 720], "fps": 24, "seconds": 10, "hyperframes_family": "0.8",
            "templates": ["title", "lower-third", "kinetic", "slideshow", "music-video", "explainer"]}


def windows_compatibility() -> dict:
    from windows_qualification import version_family
    return {"contract": WINDOWS_QUALIFICATION_CONTRACT, "job_commit_mb": WINDOWS_COMMIT_MB,
            "size": [1280, 720], "fps": FPS, "seconds": WINDOWS_SECONDS,
            "hyperframes_family": version_family(HYPERFRAMES, 2), "templates": list(WINDOWS_TEMPLATES)}


def windows_probe_file() -> Path:
    return install_dir() / "node_modules" / "hyperframes" / "dist" / "chunk-GTMBII7X.js"


def windows_probe_error() -> str:
    try:
        actual = hashlib.sha256(windows_probe_file().read_bytes()).hexdigest()
    except OSError as error:
        return f"The Windows headless-browser compatibility fix is unavailable: {type(error).__name__}"
    if actual != WINDOWS_PROBE_SHA256:
        return "HyperFrames needs the pinned Windows headless-browser compatibility fix"
    return ""


def windows_fingerprint() -> list:
    files = [Path(__file__), Path(__file__).with_name("windows_process.py"), Path(node_bin()[0]), Path(browser()),
             cli(), gsap_file(), install_dir() / "package-lock.json",
             Path(shutil.which("ffmpeg", path=search_path())), Path(shutil.which("ffprobe", path=search_path()))]
    files += [windows_probe_file(), Path(__file__).resolve().parents[1] / "scripts" / "windows-graphics" / "hyperframes-browser.patch",
              Path(__file__).resolve().parents[1] / "scripts" / "windows-graphics-check.py"]
    files += [p for name in WINDOWS_TEMPLATES for p in (TEMPLATES / name).rglob("*") if p.is_file()]
    files += [FONTS / name for name in FONT_FILES if (FONTS / name).is_file()]
    return [{"path": str(p.resolve()), "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns} for p in files]


def environment() -> dict:
    """What a render runs with: no telemetry, no update check, the browser chosen at install, this Node first."""

    env = {"HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1", "HYPERFRAMES_NO_UPDATE_CHECK": "1",
           "HYPERFRAMES_SKIP_SKILLS": "1", "NO_UPDATE_NOTIFIER": "1"}
    found = browser()
    if found:
        env["HYPERFRAMES_BROWSER_PATH"] = found
    node, _ = node_bin()
    if node:
        env["PATH"] = os.pathsep.join([str(Path(node).parent), search_path()])
    return env


# ---------- media ----------

def probe(path: Path) -> dict:
    """{seconds, width, height, audio, fps} of a clip, picture or sound (bounded ffprobe)."""

    ffprobe = shutil.which("ffprobe", path=search_path())
    if not ffprobe:
        raise Refused("ffprobe is not installed on this machine (it comes with ffmpeg)")
    try:
        said = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,avg_frame_rate",
                               "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
        data = json.loads(said.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        raise Refused(f"{path.name} could not be read")
    streams = data.get("streams") or []
    picture = next((s for s in streams if s.get("codec_type") == "video"), {})
    try:
        seconds = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        seconds = 0.0
    try:
        fps = float(Fraction(picture.get("avg_frame_rate") or "0/1"))
    except (TypeError, ValueError, ZeroDivisionError):
        fps = 0.0
    return {"seconds": seconds, "width": int(picture.get("width") or 0), "height": int(picture.get("height") or 0),
            "fps": fps,
            "audio": any(s.get("codec_type") == "audio" for s in streams)}


def words_path(clip: Path) -> Path:
    return clip.with_name(clip.stem + ".words.json")


def transcribe(clip: Path) -> list[dict]:
    """The words said in a clip (or a sound), each {word, start, end} in seconds; cached as <stem>.words.json beside it.
    faster-whisper when this Python has it, else the whisper command, as the QA gate (qa.py) finds them."""

    cache = words_path(clip)
    if cache.is_file():
        try:
            return json.loads(cache.read_text())["words"]
        except (OSError, ValueError, KeyError):
            pass
    import tempfile

    import qa  # the QA gate's speech-to-text: the same tools and model choice, beside this file

    found: list[dict] = []
    with tempfile.TemporaryDirectory() as scratch:
        sound = Path(scratch) / "words.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
                        str(sound)], check=True, env=qa.ENV)
        try:
            from faster_whisper import WhisperModel
            import wave

            import numpy as np

            model = WhisperModel(os.environ.get("QA_WHISPER_MODEL", "large-v3-turbo"), device="cpu", compute_type="int8")
            with wave.open(str(sound)) as handle:  # the samples, read here (the Spark's PyAV cannot open the file: qa.py)
                samples = np.frombuffer(handle.readframes(handle.getnframes()), np.int16).astype(np.float32) / 32768.0
            parts, _ = model.transcribe(samples, beam_size=5, word_timestamps=True)
            for part in parts:
                for word in part.words or []:
                    found.append({"word": word.word.strip(), "start": round(float(word.start), 3), "end": round(float(word.end), 3)})
            del model, parts
            qa.trim_heap()
        except ImportError:
            whisper = qa.tool("whisper")
            if not whisper:
                raise Refused("no speech-to-text tool on this machine (pip install faster-whisper, or openai-whisper)")
            cached = Path.home() / ".cache/whisper"
            name = os.environ.get("QA_WHISPER_MODEL") or next((m for m in qa.MODELS if (cached / f"{m}.pt").is_file()), "turbo")
            done = subprocess.run(["nice", whisper, str(sound), "--model", name, "--device", "cpu", "--fp16", "False",
                                   "--word_timestamps", "True", "--output_format", "json", "--output_dir", scratch],
                                  capture_output=True, text=True, env=qa.ENV, timeout=1800)
            out = Path(scratch) / "words.json"
            if not out.is_file():
                raise RuntimeError((done.stderr or "whisper wrote no transcript").strip()[-300:])
            for segment in json.loads(out.read_text()).get("segments") or []:
                for word in segment.get("words") or []:
                    found.append({"word": str(word.get("word", "")).strip(), "start": round(float(word["start"]), 3),
                                  "end": round(float(word["end"]), 3)})
    found = [w for w in found if w["word"]]
    cache.write_text(json.dumps({"clip": clip.name, "words": found}, indent=1))
    return found


# ---------- the parts of a page ----------

def text(params: dict, key: str, most: int, needed: bool = False, what: str = "") -> str:
    value = params.get(key)
    if value is not None and not isinstance(value, str):
        raise Refused(f"{key} is text")
    value = " ".join((value or "").split())
    if needed and not value:
        raise Refused(f"{what or key} is needed")
    if len(value) > most:
        raise Refused(f"{what or key} is at most {most} characters")
    return value


def number(params: dict, key: str, default: float, low: float, high: float, what: str = "") -> float:
    value = params.get(key)
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise Refused(f"{key} is a number")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise Refused(f"{key} is a number")
    if not low <= value <= high:
        raise Refused(f"{what or key} is between {low:g} and {high:g}")
    return value


def beat_list(params: dict, seconds: float | None) -> list[float]:
    """The song's beat times: ``beats`` as given (seconds, rising), else an even grid at ``bpm`` from ``first_beat``."""

    given = params.get("beats")
    if given:
        if not isinstance(given, list) or not all(isinstance(b, (int, float)) and not isinstance(b, bool) for b in given):
            raise Refused("beats is a list of times in seconds")
        beats = sorted({round(float(b), 3) for b in given if 0 <= float(b) <= LONGEST})
        if len(beats) < 2:
            raise Refused("beats needs at least two times")
        return beats
    bpm = number(params, "bpm", 100, 40, 240, "the tempo (bpm)")
    first = number(params, "first_beat", 0, 0, 30, "the first beat")
    end = min(seconds or 60.0, LONGEST)
    step = 60.0 / bpm
    return [round(first + i * step, 3) for i in range(int((end - first) / step) + 1)]


def attr(value) -> str:
    return str(value).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def video_tag(ident: str, src: str, start: float, duration: float, media_start: float = 0.0, audio: bool = False,
              volume: float = 1.0) -> str:
    sound = f' data-has-audio="true" data-volume="{volume:g}"' if audio else " muted"
    offset = f' data-media-start="{media_start:.3f}"' if media_start else ""
    return (f'<video id="{ident}" src="{attr(src)}" playsinline{sound} data-start="{start:.3f}" '
            f'data-duration="{duration:.3f}"{offset} data-track-index="0"></video>')


def audio_tag(ident: str, src: str, start: float, duration: float, volume: float = 1.0) -> str:
    return (f'<audio id="{ident}" src="{attr(src)}" data-start="{start:.3f}" data-duration="{duration:.3f}" '
            f'data-volume="{volume:g}" data-track-index="2"></audio>')


def img_tag(ident: str, src: str) -> str:
    return f'<img id="{ident}" src="{attr(src)}" alt="" />'


def even(value: int) -> int:
    return max(2, int(value) // 2 * 2)


def page(template: str, width: int, height: int, duration: float, media: str) -> str:
    """The template's page with its size, length and media set (the root's data-* and the studio:media marker)."""

    html = (TEMPLATES / template / "index.html").read_text(encoding="utf-8")
    html = re.sub(r'content="width=\d+, height=\d+"', f'content="width={width}, height={height}"', html, count=1)

    def root(found: re.Match) -> str:
        tag = found.group(0)
        tag = re.sub(r'data-width="\d+"', f'data-width="{width}"', tag)
        tag = re.sub(r'data-height="\d+"', f'data-height="{height}"', tag)
        tag = re.sub(r'data-duration="[\d.]+"', f'data-duration="{duration:.3f}"', tag)
        return re.sub(r'data-fps="\d+"', f'data-fps="{FPS}"', tag)

    html = re.sub(r'<div id="root"[^>]*>', root, html, count=1)
    return html.replace("<!-- studio:media -->", media, 1)


# ---------- a graphic's job ----------

def plan(params: dict, resolve) -> dict:
    """What a graphic is: {template, width, height, duration, data, media (tags), files {name: source}, transcribe}.
    ``resolve(value, kinds, what)`` turns a Library path (or a take id) into a file of those kinds, or refuses."""

    template = params.get("template")
    if template not in CATALOG:
        raise Refused("template is one of " + ", ".join(CATALOG))
    if WINDOWS and template not in WINDOWS_TEMPLATES:
        raise Refused("Windows graphics supports " + ", ".join(WINDOWS_TEMPLATES) + " only; no transcription runs")
    files: dict[str, Path] = {}
    tags: list[str] = []
    data: dict = {"template": template}
    words = None

    def media(key: str, value, kinds: tuple, what: str) -> tuple[str, dict]:
        source = resolve(value, kinds, what)
        name = f"assets/{key}{source.suffix.lower()}"
        files[name] = source
        return name, probe(source)

    def sound(key: str, what: str, needed: bool = False):
        if not params.get(key):
            if needed:
                raise Refused(f"choose {what} first")
            return None, None
        return media(key, params[key], SOUND, what)

    size = params.get("size")
    if size not in (None, "", "match") and size not in SIZES:
        raise Refused("size is one of " + ", ".join(SIZES) + " or match")
    default_size = "720p" if WINDOWS else "1080p"
    width, height = SIZES.get(size or default_size, SIZES[default_size])
    if params.get("width") or params.get("height"):
        width, height = int(number(params, "width", 1920, 160, 3840)), int(number(params, "height", 1080, 160, 3840))
    data["seed"] = int(number(params, "seed", 1, 0, 2 ** 31))

    def match(info: dict):
        nonlocal width, height
        if (size == "match" or not size) and not (params.get("width") or params.get("height")) and info.get("width"):
            width, height = info["width"], info["height"]

    if template == "title":
        data["title"] = text(params, "title", 120, True, "the title")
        data["subtitle"] = text(params, "subtitle", 160)
        hold = number(params, "seconds", 6, 2, 30, "the title's length (seconds)")
        duration = hold
        if params.get("video"):
            name, info = media("video", params["video"], VIDEO, "a clip")
            match(info)
            duration = min(max(info["seconds"], 1.0), LONGEST)
            hold = min(hold, duration)
            tags.append('<div class="bgwrap" id="bgwrap">' + video_tag("bg", name, 0, duration, audio=info["audio"]) + "</div>")
        elif params.get("image"):
            name, _ = media("image", params["image"], PICTURE, "a picture")
            tags.append('<div class="bgwrap" id="bgwrap">' + img_tag("bg", name) + "</div>")
        data["hold"] = hold
    elif template == "lower-third":
        data["name"] = text(params, "name", 80, True, "the name")
        data["role"] = text(params, "role", 120)
        data["side"] = "right" if params.get("side") == "right" else "left"
        if not params.get("video"):
            raise Refused("choose the clip the name goes over")
        name, info = media("video", params["video"], VIDEO, "a clip")
        match(info)
        duration = min(max(info["seconds"], 1.0), LONGEST)
        data["at"] = min(number(params, "at", 1, 0, LONGEST, "when it comes in (seconds)"), max(0.0, duration - 1.5))
        data["hold"] = min(number(params, "seconds", 5, 1.5, 60, "how long it stays (seconds)"), duration - data["at"])
        tags.append(video_tag("clip", name, 0, duration, audio=info["audio"]))
    elif template == "kinetic":
        sentence = text(params, "text", 400, True, "the words")
        data["words"] = sentence.split()
        if len(data["words"]) > 80:
            raise Refused("kinetic type takes at most 80 words")
        song, info = sound("song", "a song")
        beats = beat_list(params, info["seconds"] if info else None)
        every = int(number(params, "every", 1, 1, 8, "beats per word"))
        times = beats[::every]
        while len(times) < len(data["words"]):  # past the beats given: on at the beats' own pace
            step = (times[-1] - times[0]) / max(1, len(times) - 1) if len(times) > 1 else 0.5
            times.append(round(times[-1] + max(step, 0.2), 3))
        data["times"] = times[:len(data["words"])]
        duration = min(data["times"][-1] + 1.8, LONGEST)
        if song:
            tags.append(audio_tag("song", song, 0, duration, number(params, "volume", 1, 0, 1)))
    elif template == "captions":
        if not params.get("video"):
            raise Refused("choose the clip to caption")
        name, info = media("video", params["video"], VIDEO, "a clip")
        match(info)
        duration = min(max(info["seconds"], 1.0), LONGEST)
        tags.append(video_tag("clip", name, 0, duration, audio=info["audio"]))
        data["position"] = "top" if params.get("position") == "top" else "bottom"
        data["per_line"] = int(number(params, "per_line", 5, 1, 12, "words on a line"))
        given = params.get("words")
        if given:
            if not isinstance(given, list) or not all(isinstance(w, dict) and "word" in w and "start" in w and "end" in w for w in given):
                raise Refused("words is a list of {word, start, end}")
            data["words"] = [{"word": str(w["word"]), "start": float(w["start"]), "end": float(w["end"])} for w in given]
        else:
            source = files[name]
            cached = words_path(source)
            if cached.is_file():
                try:
                    data["words"] = json.loads(cached.read_text())["words"]
                except (OSError, ValueError, KeyError):
                    words = source
            else:
                words = source  # transcribed in the job, before the render (graphics.py words)
            if not info["audio"] and words is not None:
                raise Refused("this clip has no sound to caption")
            data.setdefault("words", [])
    elif template == "slideshow":
        pictures = params.get("pictures") or []
        if not isinstance(pictures, list) or not 1 <= len(pictures) <= 40:
            raise Refused("a slideshow is 1 to 40 pictures")
        names = [media(f"picture{i + 1}", p, PICTURE, "a picture")[0] for i, p in enumerate(pictures)]
        each = number(params, "seconds_each", 4, 1.5, 20, "seconds per picture")
        voice, said = sound("voice", "a voice line")
        song, sung = sound("song", "a song")
        duration = each * len(names)
        if said:
            duration = max(said["seconds"] + 0.8, 1.5 * len(names))
            each = duration / len(names)
        duration = min(duration, LONGEST)
        data.update(pictures=names, each=each, title=text(params, "title", 120))
        tags += ['<div class="slide" id="slide%d">%s</div>' % (i, img_tag(f"pic{i}", n)) for i, n in enumerate(names)]
        if voice:
            tags.append(audio_tag("voice", voice, 0.4, min(said["seconds"], duration - 0.4)))
        if song:
            tags.append(audio_tag("song", song, 0, min(sung["seconds"] or duration, duration), number(params, "volume", 0.35 if voice else 1, 0, 1)))
    elif template == "music-video":
        clips = params.get("clips") or []
        if not isinstance(clips, list) or not 1 <= len(clips) <= 40:
            raise Refused("a music video is cut from 1 to 40 clips")
        song, sung = sound("song", "the song", needed=True)
        found = [media(f"clip{i + 1}", c, VIDEO, "a clip") for i, c in enumerate(clips)]
        longest = number(params, "seconds", min(sung["seconds"] or 60, 60), 2, LONGEST, "the length (seconds)")
        duration = min(longest, sung["seconds"] or longest)
        every = int(number(params, "every", 2, 1, 16, "beats per cut"))
        cuts = [b for b in beat_list(params, duration)[::every] if b < duration - 0.25]
        if not cuts or cuts[0] > 0.01:
            cuts = [0.0] + cuts
        transition = params.get("transition") or "mixed"
        if transition not in ("mixed", "flash", "whip", "cut"):
            raise Refused("the transition is mixed, flash, whip or cut")
        shots = []
        for k, start in enumerate(cuts):
            end = cuts[k + 1] if k + 1 < len(cuts) else duration
            name, info = found[k % len(found)]
            length = end - start
            room = max(0.0, info["seconds"] - length)
            offset = round(((k // len(found)) * length) % room, 3) if room > 0.05 else 0.0
            shots.append({"start": round(start, 3), "end": round(end, 3)})
            tags.append(f'<div class="shot" id="shot{k}">' + video_tag(f"cut{k}", name, start, length, offset) + "</div>")
        if len(shots) > 400:
            raise Refused("too many cuts: cut on fewer beats (every) or make it shorter")
        tags.append(audio_tag("song", song, 0, duration))
        data.update(shots=shots, transition=transition, title=text(params, "title", 120))
        if size == "match":
            match(found[0][1])
    else:  # explainer
        sections = params.get("sections") or []
        if not isinstance(sections, list) or not 1 <= len(sections) <= 12 or not all(isinstance(s, dict) for s in sections):
            raise Refused("an explainer is 1 to 12 sections, each {heading, text, picture?}")
        voice, said = sound("voice", "the voice-over")
        song, sung = sound("song", "a song")
        out = []
        for i, section in enumerate(sections):
            item = {"heading": text(section, "heading", 120, True, f"section {i + 1}'s heading"),
                    "text": text(section, "text", 600, False, f"section {i + 1}'s words")}
            if section.get("picture"):
                item["picture"] = media(f"picture{i + 1}", section["picture"], PICTURE, "a picture")[0]
            out.append(item)
        if said:
            duration = min(said["seconds"] + 1.0, LONGEST)
        else:
            duration = min(number(params, "seconds_each", 6, 2, 30, "seconds per section") * len(out), LONGEST)
        # each section's share of the voice-over by its words (the voice reads the sections in order)
        weights = [max(4, len((s["heading"] + " " + s["text"]).split())) for s in out]
        at = 0.0
        for item, weight in zip(out, weights):
            item["start"] = round(at, 3)
            at += duration * weight / sum(weights)
            item["end"] = round(at, 3)
        for i, item in enumerate(out):
            if item.get("picture"):
                tags.append(f'<div class="pic" id="pic{i}">' + img_tag(f"img{i}", item["picture"]) + "</div>")
        if voice:
            tags.append(audio_tag("voice", voice, 0.3, min(said["seconds"], duration - 0.3)))
        if song:
            tags.append(audio_tag("song", song, 0, min(sung["seconds"] or duration, duration), number(params, "volume", 0.25 if voice else 1, 0, 1)))
        data.update(sections=out, title=text(params, "title", 120))
    width, height = even(width), even(height)
    if not (160 <= width <= 3840 and 160 <= height <= 3840):
        raise Refused("a graphic is 160 to 3840 pixels on a side")
    if WINDOWS and (width > 1280 or height > 720 or duration > WINDOWS_SECONDS):
        raise Refused("Windows graphics is limited to 1280x720 and 10 seconds; trim longer source clips in the Editor first")
    data.update(width=width, height=height, duration=round(duration, 3), fps=FPS)
    return {"template": template, "width": width, "height": height, "duration": duration, "data": data,
            "media": "\n  ".join(tags), "files": files, "transcribe": words}


def write(folder: Path, made: dict) -> None:
    """The job's folder: the template's page with its size, length and media, data.json and data.js, the fonts, GSAP,
    and the media linked in (a hard link where the disk allows, else a copy)."""

    if folder.exists():
        shutil.rmtree(folder)
    shutil.copytree(TEMPLATES / made["template"], folder)
    (folder / "index.html").write_text(page(made["template"], made["width"], made["height"], made["duration"], made["media"]), encoding="utf-8")
    write_data(folder, made["data"])
    (folder / "fonts").mkdir(exist_ok=True)
    for name in FONT_FILES:
        if (FONTS / name).is_file():
            shutil.copy2(FONTS / name, folder / "fonts" / name)
    if gsap_file().is_file():
        shutil.copy2(gsap_file(), folder / "gsap.min.js")
    for name, source in made["files"].items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)


def write_data(folder: Path, data: dict) -> None:
    said = json.dumps(data, indent=1, ensure_ascii=False)
    (folder / "data.json").write_text(said, encoding="utf-8")
    (folder / "data.js").write_text("window.STUDIO_DATA = " + said.replace("</", "<\\/") + ";\n", encoding="utf-8")


def command(folder: Path, target: Path, quality: str = "delivery") -> list[str]:
    node, _ = node_bin()
    return [node or "node", str(cli()), "render", str(folder), "--quality", quality, "--fps", str(FPS), "-o", str(target)] + (
        ["--workers", "1", "--no-browser-gpu"] if WINDOWS else [])


def attach(home: Path, pack: Path, spark: bool, inside, relative):
    """The Studio's hooks: (router, build, title). ``build(params, job_id)`` → (command, environment, outputs)."""

    from fastapi import APIRouter, HTTPException

    router = APIRouter()

    def resolve(value, kinds: tuple, what: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise Refused(f"choose {what} from the Library")
        value = value.strip()
        if "/" not in value and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value) and not Path(value).suffix:
            # a take's id: a Voice tab line (voice/<id>.wav) or a Music tab song (music/<id>.take.json says its file)
            if (home / "voice" / f"{value}.wav").is_file():
                return home / "voice" / f"{value}.wav"
            take = home / "music" / f"{value}.take.json"
            if take.is_file():
                try:
                    value = str(json.loads(take.read_text()).get("path") or value)
                except (OSError, ValueError):
                    pass
        try:
            path = inside(value)
        except HTTPException as error:
            raise Refused(f"{value}: {error.detail}")
        if not path.is_file() or path.suffix.lower() not in kinds:
            raise Refused(f"{value} is not {what} in the studio folder")
        return path

    def build(params: dict, job_id: str) -> tuple[list[str], dict, list[str]]:
        state = ready()
        if not state["ok"]:
            raise HTTPException(400, state["why"])
        try:
            made = plan(params, resolve)
        except Refused as error:
            raise HTTPException(400, str(error))
        folder = home / "graphics" / job_id
        target = home / "renders" / f"{job_id}.mp4"
        if job_id != "check":  # the queue's check of the settings writes nothing; the job's turn writes its folder
            target.parent.mkdir(parents=True, exist_ok=True)
            write(folder, made)
        # through this file's runner, which says the render's progress as the queue reads it and, for captions whose
        # clip has no words yet, transcribes them first (in the job: they take a while)
        runner = [sys.executable, str(Path(__file__).resolve()), "run", str(folder)]
        if made["transcribe"] is not None:
            runner += ["--words", str(made["transcribe"])]
        return runner + ["--"] + command(folder, target, "draft" if params.get("draft") else "delivery"), environment(), [str(target)]

    def title(params: dict) -> str:
        template = params.get("template")
        name = CATALOG.get(template, ("Motion graphics",))[0]
        said = params.get("title") or params.get("name") or params.get("text") or ""
        said = " ".join(str(said).split())[:48] if isinstance(said, str) else ""
        return f"{name}: {said}" if said else name

    @router.get("/api/graphics")
    def state() -> dict:
        return {"ready": ready(), "templates": [{"id": k, "title": v[0], "one": v[1]} for k, v in CATALOG.items() if not WINDOWS or k in WINDOWS_TEMPLATES],
                "sizes": {k: list(v) for k, v in SIZES.items() if not WINDOWS or k == "720p"}, "fps": FPS, "spark": spark,
                "windows": WINDOWS, "max_seconds": WINDOWS_SECONDS if WINDOWS else LONGEST}

    return router, build, title


def selftest(target: Path) -> int:
    """One small title (320x180, three seconds) rendered with what is installed: setup-graphics.sh's test."""

    import tempfile

    def nothing(value, kinds, what):
        raise Refused("the test uses no media")

    made = plan({"template": "title", "title": "Imagination Studio", "subtitle": "Motion graphics", "seconds": 3,
                 "width": 320, "height": 180}, nothing)
    with tempfile.TemporaryDirectory() as scratch:
        folder = Path(scratch) / "title"
        write(folder, made)
        target.parent.mkdir(parents=True, exist_ok=True)
        code = subprocess.call(command(folder, target, "draft"), env={**os.environ, **environment()},
                               stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    if code or not target.is_file() or target.stat().st_size < 1000:
        print(f"the test render failed (status {code})", file=sys.stderr)
        return code or 1
    info = probe(target)
    print(f"{info['width']}x{info['height']}, {info['seconds']:.1f} s: {target}")
    return 0


PROGRESS = re.compile(r'^@hf-progress (\{.*\})\s*$')


def main(argv: list[str]) -> int:
    """`graphics.py selftest <out.mp4>`: a small title rendered with what is installed.
    `graphics.py run <job folder> [--words <clip>] -- <render command…>`: the clip's words into the job's data first
    when asked (a captions job whose clip has not been transcribed yet), then the render, its progress said as
    "[tensorfold] step N/100" for the queue and its trace lines left out of the job's log."""

    if argv[:1] == ["selftest"] and len(argv) == 2:
        return selftest(Path(argv[1]))
    if len(argv) < 3 or argv[0] != "run" or "--" not in argv:
        print("usage: graphics.py run <job folder> [--words <clip>] -- <render command>", file=sys.stderr)
        return 2
    head, rest = argv[1:argv.index("--")], argv[argv.index("--") + 1:]
    folder = Path(head[0])
    if "--words" in head:
        clip = Path(head[head.index("--words") + 1])
        print("[tensorfold] graphics: listening for the clip's words", flush=True)
        try:
            found = transcribe(clip)
        except Refused as error:
            print(str(error), flush=True)
            return 1
        data = json.loads((folder / "data.json").read_text(encoding="utf-8"))
        data["words"] = found
        write_data(folder, data)
        print(f"[tensorfold] graphics: {len(found)} words", flush=True)
    if not rest:
        return 0
    process = subprocess.Popen(rest, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    last = -1
    for line in process.stdout:
        found = PROGRESS.match(line.strip())
        if found:
            try:
                pct = int(float(json.loads(found.group(1)).get("pct") or 0))
            except (ValueError, TypeError, AttributeError):
                continue
            if pct != last:
                last = pct
                print(f"[tensorfold] step {max(0, min(pct, 100))}/100", flush=True)
            continue
        if ("[Render:trace]" in line or not line.strip() or line.lstrip().startswith(("█", "░", "[initSession"))
                or "hyperframes.dev/studio" in line):  # the trace, the bar, and the desktop app's advert
            continue
        print(line.rstrip(), flush=True)
    return process.wait()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
