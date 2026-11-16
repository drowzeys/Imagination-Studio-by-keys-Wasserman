"""Work on a clip you already have: restyle it, extend it, or fix one region of it. Three Library actions.

    Restyle   three steps. The clip's first frame is redrawn by the picture model in the look the prompt describes
              (an `edit` job), the clip's motion is read by Motion Previs (its depth layer), then the clip is made again
              (MiniMax H3 Ref2VA) from the depth layer as <Video 1> and the redrawn frame as <Picture 1>.
    Extend    a new clip that opens on the clip's last frame, then (by default) the two joined into one file:
              renders/<job>-extended.mp4. Over 15 seconds the new part is a long take, a chain of clips.
    Fix       the same three steps as Restyle, with an instruction for the first frame in place of a look.

Restyle and Fix go the way the create wizard goes for a control layer (server.motion_template): the source clip
itself as a reference video is followed so closely that the clip comes back unchanged (measured: a new look and an
edited first frame were both ignored), while a depth map of it carries only the structure, motion and camera, and
the look and the setting come from the first-frame picture. The whole frame is redrawn, so small details can change.

Each is queued through the studio's own jobs (``video``, ``long`` and ``edit``) and the Motion Previs analysis queue
(motion.py), so the queues, the Sparks, the QA gate and the Library treat the results like any other. A step that
follows another (Extend's join, the analysis after the frame, the clip after the analysis) is started by ``then``,
which server.perform calls when the job whose ``params.then`` names a chain here has finished; an analysis is
followed by a thread of its own. The chains live in memory, like the jobs. The pack of a clip is kept and used again
(motion/revise.json) while the clip is unchanged.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

KINDS = ("restyle", "extend", "fix")
TITLES = {"restyle": "Restyle", "extend": "Extend", "fix": "Fix"}
# presets the model renders at their own size (no 2x decoder, no crop of a larger picture): a clip of that size keeps it
NATIVE = ("small", "native", "p720", "sharp")
REFERENCE_SECONDS = (2.0, 15.0)   # what Ref2VA takes of a reference video (server.REFERENCE_CLIP_SECONDS)
EXTEND_SECONDS = (5, 30)
REDRAW = "Redraw this frame as: {look}; keep the composition"   # Restyle's first frame
LOOK = "The look of the clip is that of <Picture 1>: {look}."   # the shot's words in the final prompt
STEPS = ("the first frame", "the motion", "the clip")
WATCH = 2.0   # seconds between two looks at a running analysis
CONTINUE = "continue the shot"
IMAGE_MODELS = ("qwen", "qwen-full", "flux3k-fast", "flux3k-full")
MODES = ("camera-only", "actor-motion", "object-motion", "full-scene")   # server.MOTION_MODES
TOOLS = ("/opt/homebrew/bin", "/usr/local/bin")


def tool(name: str) -> str | None:
    return shutil.which(name) or shutil.which(name, path=":".join(TOOLS))


def probe(path: Path) -> dict:
    """{width, height, duration, sound} of a clip, by ffprobe; a 400 with the reason when it cannot be read."""

    found = tool("ffprobe")
    if found is None:
        raise HTTPException(400, "working on a clip needs ffprobe, which comes with ffmpeg and is not installed on this machine")
    try:
        done = subprocess.run([found, "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                               "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
        data = json.loads(done.stdout or "{}")
        streams = data.get("streams") or []
        video = next(one for one in streams if one.get("codec_type") == "video")
        return {"width": int(video["width"]), "height": int(video["height"]),
                "duration": float((data.get("format") or {}).get("duration") or 0),
                "sound": any(one.get("codec_type") == "audio" for one in streams)}
    except (subprocess.TimeoutExpired, ValueError, StopIteration, KeyError, TypeError):
        raise HTTPException(400, f"{path.name} could not be read as a video")


def size_of(width: int, height: int, presets: dict, sides: tuple, most: int) -> dict:
    """The size a new clip is made at to match ``width`` x ``height``: the preset of exactly that size the model renders
    natively, else a size of its own (multiples of 32, within the sides and pixels a custom size may have) of about the
    same shape."""

    for name in NATIVE:
        if name in presets and tuple(presets[name][1]) == (width, height):
            return {"preset": name}
    low, high = sides
    scale = min(1.0, (most / max(width * height, 1)) ** 0.5)
    side = lambda n: max(low, min(high, int(round(n * scale / 32)) * 32))  # noqa: E731
    w, h = side(width), side(height)
    while w * h > most:  # rounding up may go over the pixels: the longer side gives way
        if w >= h:
            w -= 32
        else:
            h -= 32
    return {"preset": "custom", "width": w, "height": h}


def attach(studio):
    """``studio``: the server module (or a stand-in with its names): HOME, inside, relative, queue_job, build, jobs,
    frame_at, environment, PRESETS, CUSTOM_SIDES, CUSTOM_PIXELS, LONGEST, FPS, LEAD, VIDEO_SUFFIXES, hub, SPARK,
    compose_references, motion_template and motion (the Motion Previs module: API and motion_reference)."""

    router = APIRouter()
    chains: dict[str, dict] = {}
    guard = threading.Lock()

    def clip_of(params: dict) -> Path:
        given = params.get("video")
        if not given:
            raise HTTPException(400, "choose a clip from the Library first")
        path = studio.inside(str(given))
        if not path.is_file() or path.suffix.lower() not in studio.VIDEO_SUFFIXES:
            raise HTTPException(400, f"{given} is not a clip in the studio folder")
        return path

    def number(params: dict, key: str, default, kind=float):
        try:
            return kind(params.get(key) if params.get(key) not in (None, "") else default)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{key} is a number")

    def size(info: dict) -> dict:
        return size_of(info["width"], info["height"], studio.PRESETS, studio.CUSTOM_SIDES, studio.CUSTOM_PIXELS)

    def cut(clip: Path, seconds: float) -> Path:
        """The first ``seconds`` of a clip, as a file of its own in uploads/ (a reference video is at most 15 s)."""

        target = studio.HOME / "uploads" / f"{clip.stem[:40]}_first{seconds:g}s_{uuid.uuid4().hex[:6]}.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-t", f"{seconds:.3f}", "-c:v", "libx264",
                               "-crf", "16", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
                               str(target)], capture_output=True, text=True, env=studio.environment({}), timeout=600)
        if done.returncode or not target.is_file():
            raise HTTPException(400, f"the clip could not be cut to {seconds:g} s: {done.stderr.strip()[-200:]}")
        return target

    def reference(clip: Path, info: dict, asked) -> tuple[Path, float, list[str]]:
        """(the reference video, the new clip's seconds, notes): the clip itself, or its first part when it is longer
        than the model takes or than the length asked for."""

        low, high = REFERENCE_SECONDS
        length = info["duration"]
        if length and length < low - 0.05:
            raise HTTPException(400, f"the clip is {length:.1f} s: a clip worked on this way is at least {low:g} seconds long")
        if asked not in (None, ""):
            try:
                asked = float(asked)
            except (TypeError, ValueError):
                raise HTTPException(400, "seconds is a number")
            if not low <= asked <= high:
                raise HTTPException(400, f"seconds is between {low:g} and {high:g}")
            if length and asked > length + 0.05:
                raise HTTPException(400, f"the clip is only {length:.1f} s long")
        seconds = min(float(asked) if asked not in (None, "") else (length or high), high)
        notes = []
        if length and length > seconds + 0.05:
            if length - seconds >= 0.25:  # a 15 s clip of the model's is 15.08 s: cut without a word
                notes.append(f"The clip is {length:.1f} s long; " + (f"a new clip follows at most {high:g} s of it, so its first {high:g} s are used."
                                                                       if asked in (None, "") else f"its first {seconds:g} s are used."))
            return cut(clip, seconds), seconds, notes
        return clip, seconds, notes

    def analysis() -> dict:
        """Motion Previs' queue (motion.API), or a 400 that says what is missing."""

        found = getattr(getattr(studio, "motion", None), "API", None) or {}
        if not all(name in found for name in ("submit", "job", "pack", "able")):
            raise HTTPException(400, "Restyle and Fix read the clip's motion with Motion Previs, which is not part of this studio")
        if not (found["able"]() or {}).get("ffmpeg"):
            raise HTTPException(400, "Restyle and Fix read the clip's motion with Motion Previs, which needs ffmpeg: "
                                     + ("bash oneshot-setup-spark.sh --motion" if getattr(studio, "SPARK", False) else "bash oneshot-setup.sh --motion")
                                     + " installs it")
        return found

    def cache_file() -> Path:
        return studio.HOME / "motion" / "revise.json"

    def cache_key(clip: Path, seconds: float) -> str:
        stat = clip.stat()
        return f"{studio.relative(clip)}|{stat.st_size}|{int(stat.st_mtime)}|{seconds:g}"

    def cached(key: str) -> str | None:
        """The pack made for this clip before, while it is still there with a depth layer."""

        try:
            pack = json.loads(cache_file().read_text()).get(key)
        except (OSError, ValueError, AttributeError):
            return None
        if not pack:
            return None
        try:
            layers = analysis()["pack"](pack).get("layers") or []
        except Exception:  # noqa: BLE001 - gone or unreadable: made again
            return None
        return pack if ("ai-depth" in layers or "depth" in layers) else None

    def remember(key: str, pack: str) -> None:
        try:
            found = json.loads(cache_file().read_text()) if cache_file().is_file() else {}
        except (OSError, ValueError):
            found = {}
        found[key] = pack
        cache_file().parent.mkdir(parents=True, exist_ok=True)
        cache_file().write_text(json.dumps(found, indent=1))

    def pixels(made: dict) -> tuple[int, int]:
        if made["preset"] == "custom":
            return made["width"], made["height"]
        width, height = studio.PRESETS[made["preset"]][1]
        return int(width), int(height)

    def redraw(kind: str, clip: Path, params: dict, seed: int, loras) -> dict:
        """Restyle and Fix: the first frame redrawn (edit), the clip's motion read (analysis), the clip made again from
        the depth layer and the redrawn frame (video). The last two steps are finished when the one before is done."""

        name = clip.name
        if kind == "restyle":
            look = str(params.get("prompt") or "").strip()
            if not look:
                raise HTTPException(400, "say what the clip should look like now")
            words = REDRAW.format(look=look)
        else:
            look = str(params.get("instruction") or "").strip()
            if not look:
                raise HTTPException(400, "say what to change, e.g. change the sky to a stormy sunset")
            words = look
        model = str(params.get("image_model") or "qwen")
        if model not in IMAGE_MODELS:
            raise HTTPException(400, f"image_model is one of {', '.join(IMAGE_MODELS)}")
        mode = params.get("mode") or None
        if mode is not None and mode not in MODES:
            raise HTTPException(400, f"mode is one of {', '.join(MODES)}")
        queue_of = analysis()
        info = probe(clip)
        ref, seconds, notes = reference(clip, info, params.get("seconds") if kind == "restyle" else None)
        keep = bool(params.get("keep_sound", True))
        if keep and not info["sound"]:
            keep = False
            notes.append("The clip has no sound of its own: the new one gets sound made for it.")
        frame = studio.HOME / "uploads" / f"{clip.stem[:40]}_first_{uuid.uuid4().hex[:6]}.png"
        frame.parent.mkdir(parents=True, exist_ok=True)
        try:
            studio.frame_at(clip, frame, 0)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            raise HTTPException(400, "the clip's first frame could not be taken (is ffmpeg installed?)")
        edit = {"image": studio.relative(frame), "prompt": words, "image_model": model, "seed": seed, "size": "source"}
        if loras and model == "qwen" and kind == "fix":
            edit["loras"] = loras
        made = size(info)
        video = {"references": [], "reference_videos": [], "reference_video_sound": keep, "seconds": seconds, "seed": seed, **made}
        if loras and kind == "restyle":
            video["loras"] = loras
        if params.get("ref_turbo"):
            video["ref_turbo"] = True
        # the last step is checked now (Ref2VA installed, the lengths), with the frame and the clip standing in
        studio.build("video", {**video, "prompt": "<Picture 1> <Video 1>", "references": [studio.relative(frame)], "reference_videos": [studio.relative(ref)]}, "check")
        # the analysis reads the clip where Motion Previs takes it from (the studio's clip folders), else the cut
        source = clip if studio.relative(clip).split("/")[0] in ("uploads", "renders", "exports", "projects", "slate") else None
        if source is None:
            source = ref if ref != clip else cut(clip, seconds)
        able = queue_of["able"]() or {}
        layers = ["depth"] + [layer for layer, flag in (("ai-depth", "ai_depth"), ("pose", "pose"), ("camera", "camera")) if able.get(flag)]
        title = TITLES[kind]
        return {"steps": [{"kind": "edit", "title": f"{title}: {name} (1/3: {STEPS[0]})", "params": edit},
                          {"kind": "analysis", "title": f"{title}: {name} (2/3: {STEPS[1]})",
                           "params": {"source": studio.relative(source), "start": 0, "end": round(seconds, 3), "mode": mode or "actor-motion",
                                      "layers": layers, "title": f"{title}: {name}"},
                           "key": cache_key(clip, seconds), "mode": mode},
                          {"kind": "video", "title": f"{title}: {name} (3/3: {STEPS[2]})", "params": video, "look": look,
                           "keep": keep, "sound_from": studio.relative(ref)}],
                "notes": notes}

    def chosen_mode(described: dict) -> str:
        """actor-motion where the pose pass found a person in the clip, object-motion where it ran and found nobody,
        actor-motion where it did not run (the default)."""

        pose = described.get("pose") if isinstance(described.get("pose"), dict) else None
        if not pose or not pose.get("totalFrames"):
            return "actor-motion"
        return "actor-motion" if pose.get("detectedFrames", 0) >= 0.25 * pose["totalFrames"] else "object-motion"

    def final(chain: dict, step: dict, pack: str) -> dict:
        """The last step's job, as the create wizard queues a control layer: the redrawn frame as <Picture 1>, the depth
        layer as <Video 1> (motion.motion_reference) and the prompt from the wizard's composer (compose_references with
        ``motion``, so motion_template's control-video wording)."""

        described = analysis()["pack"](pack)
        have = described.get("layers") or []
        layer = "ai-depth" if "ai-depth" in have else "depth" if "depth" in have else None
        if layer is None:
            raise RuntimeError("the motion analysis made no depth layer of the clip")
        mode = chain.get("mode") or chosen_mode(described)
        params = dict(step["params"])
        width, height = pixels(params)
        handed = studio.motion.motion_reference(pack, layer, mode, params["seconds"], width, height, timing="trim")
        sound = bool(step["keep"] and handed.get("sound"))
        chain["lay"] = step["sound_from"] if step["keep"] and not sound else None
        camera = (handed.get("camera") or "").strip() if handed.get("camera_reliable") else ""
        measured = described.get("pace") if isinstance(described.get("pace"), dict) else None
        motion = {"layer": layer, "mode": mode, "camera": camera, "people": 1, "pace": "auto", "measured": measured, "free": False}
        # the template's own sentences, with its "photorealistic" left out: the look is the redrawn frame's, which may
        # be a watercolour or a cartoon
        template = studio.motion_template(motion, 1)
        fields = {"scene": LOOK.format(look=step["look"].rstrip(".")), "action": "", "camera": "", "dialogue": [], "soundscape": "",
                  "music": "N/A", "subjects": [""], "video_subjects": [""], "audio_notes": [], "video_sound": sound, "motion": motion,
                  "lead": template.get("lead", "").replace("A photorealistic video", "A video"),
                  "avoid": template.get("avoid", "").replace("The picture is photorealistic throughout, continuing the look of <Picture 1>",
                                                             "The picture keeps the look of <Picture 1> throughout")}
        params.update(prompt=studio.compose_references(fields, 1), references=[chain["frame"]], reference_videos=[handed["clip"]],
                      reference_video_sound=sound, pace="auto",
                      motion={"pack": pack, "layer": layer, "mode": mode, "measured": measured, "free": False})
        return params

    def own_prompt(clip: Path) -> str:
        """The prompt the clip was made from: its job's while the studio has it, else the takes list's, else ''."""

        path = studio.relative(clip)
        for job in list(studio.jobs.values()):
            if job.get("kind") in ("video", "studio", "long") and path in (job.get("outputs") or []):
                prompt = str((job.get("params") or {}).get("prompt") or "")
                if prompt.startswith(studio.LEAD):
                    prompt = prompt[len(studio.LEAD):]
                return prompt.strip()
        try:
            return str((studio.hub.takes().get(path) or {}).get("prompt") or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    def plan(kind: str, params: dict) -> dict:
        """{steps: [{kind, title, params}], notes, join?} for one action; 400 with the reason when it cannot run."""

        clip = clip_of(params)
        name = clip.name
        seed = number(params, "seed", 0, int)
        loras = params.get("loras") or None
        if kind in ("restyle", "fix"):
            return redraw(kind, clip, params, seed, loras)
        if kind == "extend":
            seconds = number(params, "seconds", 5)
            low, high = EXTEND_SECONDS
            if not low <= seconds <= high:
                raise HTTPException(400, f"an extension is {low} to {high} seconds")
            info = probe(clip)
            prompt = str(params.get("prompt") or "").strip() or own_prompt(clip) or CONTINUE
            job = {"first_frame_from": studio.relative(clip), "seed": seed, **size(info)}
            if loras:
                job["loras"] = loras
            if seconds <= studio.LONGEST:
                step = {"kind": "video", "params": {**job, "seconds": seconds,
                                                    "prompt": prompt if prompt.startswith("For the target video") else f"{studio.LEAD}\n\n{prompt}"}}
            else:  # longer than one clip: a long take, each part opening on the last frame of the one before
                step = {"kind": "long", "params": {**job, "total_seconds": int(round(seconds)), "prompt": prompt}}
            step["title"] = f"Extend: {name} +{seconds:g} s"
            join = params.get("join", True) is not False
            return {"steps": [step], "notes": [], "join": studio.relative(clip) if join else None}
        raise HTTPException(400, f"unknown action {kind}: one of {', '.join(KINDS)}")

    def step_of(one: str, chain: dict) -> dict:
        if one not in chain.get("analyses", {}):
            return {"id": one, "status": (studio.jobs.get(one) or {}).get("status", "gone")}
        made = chain["analyses"][one]
        if made.get("cached"):
            return {"id": one, "kind": "analysis", "title": made["title"], "status": "done", "pack": made["pack"], "cached": True}
        try:
            job = analysis()["job"](one)
        except Exception:  # noqa: BLE001 - the analysis queue forgot it (a restart)
            job = {"status": "gone"}
        stage = job.get("stage")
        return {"id": one, "kind": "analysis", "title": made["title"], "status": job.get("status", "gone"), "pack": job.get("pack"),
                "stage": stage, "error": job.get("error")}

    def shown(chain: dict) -> dict:
        steps = [step_of(one, chain) for one in chain["steps"]]
        waiting = len(chain["pending"])
        if chain["error"]:
            status = "failed"
        elif any(s["status"] in ("failed", "cancelled", "gone") for s in steps):
            status = next(s["status"] for s in steps if s["status"] in ("failed", "cancelled", "gone"))
        elif not waiting and steps and steps[-1]["status"] == "done" and (not chain["join"] or chain["outputs"]):
            status = "done"
        else:
            status = "running"
        return {"id": chain["id"], "kind": chain["kind"], "title": chain["title"], "steps": steps, "of": len(steps) + waiting,
                "notes": chain["notes"], "error": chain["error"], "outputs": chain["outputs"], "status": status}

    def queue(body: dict) -> dict:
        """Queue an action's first step; the rest follow from ``then``. The first job, with ``revise`` (the chain)."""

        kind = body.get("kind")
        if kind not in KINDS:
            raise HTTPException(400, f"unknown action {kind}: one of {', '.join(KINDS)}")
        if body.get("params") is not None and not isinstance(body.get("params"), dict):
            raise HTTPException(400, "params is an object of the action's settings")
        made = plan(kind, dict(body.get("params") or {}))
        first, rest = made["steps"][0], made["steps"][1:]
        chain = {"id": uuid.uuid4().hex[:12], "kind": kind, "title": first["title"].split(" (")[0], "steps": [], "pending": rest,
                 "join": made.get("join"), "notes": made["notes"], "error": None, "outputs": [], "created": time.time(),
                 "analyses": {}, "frame": None, "mode": None, "lay": None}
        if rest or chain["join"]:
            first["params"]["then"] = chain["id"]
        with guard:
            chains[chain["id"]] = chain
        try:
            job = studio.queue_job({"kind": first["kind"], "title": body.get("title") or first["title"], "params": first["params"],
                                    **({"sparks": body["sparks"]} if body.get("sparks") is not None else {})})
        except Exception:
            with guard:
                chains.pop(chain["id"], None)
            raise
        chain["steps"].append(job["id"])
        return {**job, "revise": shown(chain)}

    def join(source: Path, new: Path, target: Path) -> None:
        """The clip and its extension as one file: the extension's first frame repeats the clip's last, so it is
        dropped; the extension is brought to the clip's size and both to 24 frames a second and 48 kHz sound."""

        one, two = probe(source), probe(new)
        fps, width, height = studio.FPS, one["width"], one["height"]
        inputs = ["-i", str(source), "-i", str(new)]
        filters = [f"[0:v]fps={fps},scale={width}:{height},setsar=1,format=yuv420p[v0]",
                   f"[1:v]trim=start_frame=1,setpts=PTS-STARTPTS,fps={fps},scale={width}:{height},setsar=1,format=yuv420p[v1]"]
        sound = one["sound"] or two["sound"]
        if sound:
            for index, info in enumerate((one, two)):
                start = 1 / fps if index else 0
                if info["sound"]:
                    filters.append(f"[{index}:a]atrim=start={start},asetpts=PTS-STARTPTS,aresample=48000,"
                                   f"aformat=channel_layouts=stereo[a{index}]")
                else:  # silence for the part without sound, as long as its picture
                    length = max(info["duration"] - start, 0.05)
                    filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={length:.3f}[a{index}]")
            filters.append("[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
            maps = ["-map", "[v]", "-map", "[a]", "-c:a", "aac", "-b:a", "256k"]
        else:
            filters.append("[v0][v1]concat=n=2:v=1:a=0[v]")
            maps = ["-map", "[v]"]
        target.parent.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(filters), *maps,
                               "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p",
                               "-movflags", "+faststart", str(target)], capture_output=True, text=True,
                              env=studio.environment({}), timeout=1800)
        if done.returncode or not target.is_file():
            raise RuntimeError(f"ffmpeg could not join the clips: {done.stderr.strip()[-300:]}")

    def then(job: dict) -> None:
        """What follows a finished step of a chain: Extend's join, the analysis after Restyle's or Fix's first frame,
        or the clip's own sound laid under the last step's. Never fails the job itself:
        what went wrong is said on the chain and in the job's log."""

        chain = chains.get(str(job.get("params", {}).get("then") or ""))
        if chain is None or not chain["steps"] or chain["steps"][-1] != job["id"]:
            return
        try:
            if chain["join"]:
                new = studio.inside(job["outputs"][0])
                target = new.with_name(new.stem + "-extended.mp4")  # beside the new clip, in its take's folder
                job["progress"] = {"stage": "joining with the clip it continues", "at": 0, "of": 1}
                join(studio.inside(chain["join"]), new, target)
                job["progress"] = None
                job["outputs"] = [studio.relative(target)] + [p for p in job["outputs"] if p != studio.relative(target)]
                chain["outputs"] = list(job["outputs"])
                job["log"] = (job.get("log") or "") + f"[studio] joined with {chain['join']}: {studio.relative(target)}\n"
            elif chain["pending"]:
                chain["frame"] = job["outputs"][0]  # the redrawn first frame
                said = analyse(chain)
                job["log"] = (job.get("log") or "") + f"[studio] {said}\n"
            elif chain.get("lay"):
                new = studio.inside(job["outputs"][0])
                target = new.with_name(new.stem + "-sound.mp4")  # beside the new clip, in its take's folder
                job["progress"] = {"stage": "laying the clip's own sound under it", "at": 0, "of": 1}
                lay(new, studio.inside(chain["lay"]), target)
                job["progress"] = None
                job["outputs"] = [studio.relative(target)] + [p for p in job["outputs"] if p != studio.relative(target)]
                chain["outputs"] = list(job["outputs"])
                job["log"] = (job.get("log") or "") + f"[studio] the clip's own sound laid under it: {studio.relative(target)}\n"
        except Exception as error:  # noqa: BLE001
            job["progress"] = None
            chain["error"] = str(getattr(error, "detail", error))
            job["log"] = (job.get("log") or "") + f"[studio] the next step could not follow: {chain['error']}\n"

    def analyse(chain: dict) -> str:
        """The analysis step: the clip's pack from before, or a Motion Previs job followed by a thread of its own."""

        step = chain["pending"].pop(0)
        chain["mode"] = step.get("mode")
        pack = cached(step["key"])
        if pack:
            one = f"pack-{pack}"
            chain["analyses"][one] = {"title": step["title"], "pack": pack, "cached": True}
            chain["steps"].append(one)
            render(chain, pack)
            return f"the clip's motion was read before (pack {pack}): the clip is queued"
        made = analysis()["submit"](dict(step["params"]))
        chain["analyses"][made["id"]] = {"title": step["title"], "key": step["key"]}
        chain["steps"].append(made["id"])
        threading.Thread(target=watch, args=(chain, made["id"]), daemon=True).start()
        return f"the clip's motion is being read: Motion Previs job {made['id']}"

    def watch(chain: dict, one: str) -> None:
        while True:
            try:
                job = analysis()["job"](one)
            except Exception as error:  # noqa: BLE001
                chain["error"] = f"the motion analysis was lost: {getattr(error, 'detail', error)}"
                return
            if job.get("status") == "done":
                break
            if job.get("status") in ("failed", "cancelled"):
                chain["error"] = (f"the motion analysis failed: {job.get('error') or 'no reason given'}"
                                  if job.get("status") == "failed" else "the motion analysis was cancelled")
                return
            time.sleep(WATCH)
        try:
            remember(chain["analyses"][one]["key"], job["pack"])
            render(chain, job["pack"])
        except Exception as error:  # noqa: BLE001 - said on the chain, which the page shows
            chain["error"] = str(getattr(error, "detail", error))

    def render(chain: dict, pack: str) -> None:
        step = chain["pending"].pop(0)
        params = final(chain, step, pack)
        if chain.get("lay"):
            params["then"] = chain["id"]
        made = studio.queue_job({"kind": step["kind"], "title": step["title"], "params": params})
        chain["steps"].append(made["id"])

    def lay(new: Path, source: Path, target: Path) -> None:
        """The source clip's sound under the new picture, where the depth layer could not carry it to the model."""

        done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(new), "-i", str(source), "-map", "0:v:0", "-map", "1:a:0",
                               "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", str(target)],
                              capture_output=True, text=True, env=studio.environment({}), timeout=600)
        if done.returncode or not target.is_file():
            raise RuntimeError(f"ffmpeg could not lay the clip's sound under the new one: {done.stderr.strip()[-300:]}")

    @router.get("/api/revise")
    def listing() -> dict:
        return {"kinds": [{"kind": k, "title": TITLES[k]} for k in KINDS], "chains": [shown(c) for c in list(chains.values())]}

    @router.get("/api/revise/{chain_id}")
    def one(chain_id: str) -> dict:
        chain = chains.get(chain_id)
        if chain is None:
            raise HTTPException(404, "no such action")
        return shown(chain)

    @router.post("/api/revise")
    def submit(body: dict) -> dict:
        try:
            return queue(body)
        except (TypeError, ValueError, AttributeError, KeyError):
            raise HTTPException(400, "a setting was sent as the wrong type") from None

    return router, queue, then
