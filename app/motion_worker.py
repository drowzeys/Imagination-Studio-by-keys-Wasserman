#!/usr/bin/env python3
"""Motion Previs analysis: one shot in, a production pack out.

This file is a Python port of the analysis in Motion Previs Studio 4.1.0 by Sam Wasserman
(https://github.com/wassermanproductions/motion-previs-studio, Apache-2.0, Copyright 2026 Sam Wasserman), read at
commit 95e7d0ff. It was modified: the original is an Electron + React/TypeScript app; this is a rewrite of its
src/lib/{pose,poseVideo,openpose,cameraMotion,aiDepth,quality}.ts and of the ffmpeg passes and the bundle writer in
electron/main.cjs. Its NOTICE is kept at LICENSES/Motion-Previs-Studio-NOTICE. What differs is listed in
THIRD_PARTY_NOTICES.md ("Motion Previs Studio") and in the pack's bundle_manifest.json under "port".

It runs as its own process so that its packages (MediaPipe, OpenCV, ONNX Runtime) can live in their own environment
and a cancel is a signal to the process group:

    python motion_worker.py job.json      # run one analysis, progress as "@@{json}" lines on stdout
    python motion_worker.py --probe       # print what this Python can do (pose, camera, AI depth) as JSON

Stages: prepare, pose, camera, depth, encode, pack. Every layer that cannot run on this machine is skipped with a
note; the trimmed reference, the ffmpeg depth proxy, edges, line art, motion mask and contact sheet need only ffmpeg.
"""

from __future__ import annotations

import json
import math
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = {"name": "Motion Previs Studio", "version": "4.1.0", "author": "Sam Wasserman",
          "url": "https://github.com/wassermanproductions/motion-previs-studio",
          "commit": "95e7d0ff1d4cc546f7eb09a74ccbd084988a19bc", "license": "Apache-2.0"}
PORT_NAME = "Wasserman Slate AI Studio · Motion Previs (Python port of Motion Previs Studio by Sam Wasserman)"
STAGES = ("prepare", "pose", "camera", "depth", "encode", "pack")
MODES = ("camera-only", "actor-motion", "object-motion", "full-scene")
LAYERS = ("depth", "ai-depth", "pose", "camera", "edges", "lineart", "masks", "normals")
POSE_MODELS = {"lite": "pose_landmarker_lite.task", "full": "pose_landmarker_full.task", "heavy": "pose_landmarker_heavy.task"}
DEPTH_MODEL = Path("depth-anything-small") / "model_quantized.onnx"
# pose.ts DEFAULT_POSE_SETTINGS, but for the model: the original starts on Lite because Heavy is slow in a browser. Here
# Heavy tracks a 5 second shot in about a second, and on test clips Lite put the shoulders well inside a coat.
POSE_DEFAULTS = {"poseModel": "heavy", "depthModel": "depth-anything", "detectionConfidence": 0.35, "trackingConfidence": 0.35,
                 "smoothing": 0.7, "temporalWindow": 5, "maxPeople": 1, "fillGaps": True, "optimizeForExport": True}


def tool(name: str) -> str:
    """ffmpeg or ffprobe: MOTION_PREVIS_FFMPEG / MOTION_PREVIS_FFPROBE as in the original, then PATH."""

    override = os.environ.get(f"MOTION_PREVIS_{name.upper()}")
    if override:
        return override
    for folder in ("/opt/homebrew/bin", "/usr/local/bin"):
        if (Path(folder) / name).is_file():
            return str(Path(folder) / name)
    return shutil.which(name) or name


def models_dir() -> Path:
    return Path(os.environ.get("MOTION_MODELS") or Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio")) / "motion-models")


def probe() -> dict:
    """What this interpreter can do. A missing package turns its layer off; it never fails the others."""

    out = {"python": sys.executable, "version": sys.version.split()[0], "models": str(models_dir()),
           "ffmpeg": bool(shutil.which(tool("ffmpeg")) or Path(tool("ffmpeg")).is_file()), "numpy": None, "cv2": None,
           "mediapipe": None, "onnxruntime": None, "pose_models": [], "depth_model": False, "why": {}}
    for name in ("numpy", "cv2", "mediapipe", "onnxruntime"):
        try:
            module = __import__(name)
            out[name] = getattr(module, "__version__", "installed")
        except Exception as error:  # noqa: BLE001 - a broken wheel is the same as a missing one here
            out["why"][name] = f"{type(error).__name__}: {error}"[:200]
    out["pose_models"] = [key for key, name in POSE_MODELS.items() if (models_dir() / name).is_file()]
    out["depth_model"] = (models_dir() / DEPTH_MODEL).is_file()
    out["pose"] = bool(out["mediapipe"] and out["numpy"] and out["cv2"] and out["pose_models"])
    out["camera"] = bool(out["cv2"] and out["numpy"])
    out["ai_depth"] = bool(out["onnxruntime"] and out["numpy"] and out["depth_model"])
    return out


# ---------- progress, cancel, ffmpeg ----------

def emit(**event) -> None:
    sys.stdout.write("@@" + json.dumps(event) + "\n")
    sys.stdout.flush()


class Progress:
    """One stage at a time; `at` of `of` are frames or seconds of that stage, as counted, not estimated."""

    def __init__(self) -> None:
        self.stage, self.started, self.timings, self.notes = None, 0.0, {}, []

    def start(self, stage: str, message: str) -> None:
        self.finish()
        self.stage, self.started = stage, time.time()
        emit(stage=stage, at=0, of=1, message=message)

    def step(self, at: float, of: float, message: str) -> None:
        emit(stage=self.stage, at=round(at, 2), of=round(max(of, 1e-6), 2), message=message)

    def finish(self) -> None:
        if self.stage:
            self.timings[self.stage] = round(self.timings.get(self.stage, 0.0) + time.time() - self.started, 3)
            emit(stage=self.stage, done=True, seconds=self.timings[self.stage])
        self.stage = None

    def note(self, text: str) -> None:
        self.notes.append(text)
        emit(note=text)


def ffmpeg(args: list[str], seconds: float = 0.0, tick=None) -> None:
    """Run ffmpeg; `tick(done_seconds)` follows its own -progress output."""

    command = [tool("ffmpeg"), "-v", "error", "-y", "-nostdin", *args]
    if tick:
        command[1:1] = ["-progress", "pipe:1", "-nostats"]
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdout=subprocess.PIPE if tick else subprocess.DEVNULL, stderr=errors, text=True)
        if tick:
            for line in process.stdout:
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        tick(min(max(int(line.split("=")[1]) / 1e6, 0.0), seconds or 1e9))
                    except ValueError:
                        pass
        if process.wait():
            errors.seek(0)
            raise RuntimeError("ffmpeg failed: " + errors.read().decode(errors="replace")[-600:])


def ffprobe(path: Path) -> dict:
    out = subprocess.run([tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames,codec_name:format=duration", "-of", "json",
                          str(path)], capture_output=True, text=True)
    data = json.loads(out.stdout or "{}")
    stream = (data.get("streams") or [{}])[0]
    rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"
    try:
        top, _, bottom = rate.partition("/")
        fps = float(top) / float(bottom or 1)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    return {"width": int(stream.get("width") or 0), "height": int(stream.get("height") or 0), "frameRate": fps,
            "duration": float((data.get("format") or {}).get("duration") or 0)}


def read_frames(video: Path, fps: float, width: int, height: int, pixels: str = "rgb24"):
    """Frames of `video` sampled at `fps` and scaled, as numpy arrays (height, width, channels)."""

    import numpy as np

    channels = 1 if pixels == "gray" else 3
    size = width * height * channels
    process = subprocess.Popen([tool("ffmpeg"), "-v", "error", "-nostdin", "-i", str(video), "-vf",
                                f"fps={fps},scale={width}:{height}:flags=bicubic", "-f", "rawvideo", "-pix_fmt", pixels, "-"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            data = process.stdout.read(size)
            if len(data) < size:
                break
            yield np.frombuffer(data, np.uint8).reshape(height, width, channels).copy()
    finally:
        process.stdout.close()
        process.kill() if process.poll() is None else None
        process.wait()


class Encoder:
    """Frames in, an H.264 mp4 out: one encoded frame per frame given (frameEncode.cjs: veryfast, crf 18, yuv420p)."""

    def __init__(self, target: Path, width: int, height: int, fps: float, pixels: str = "bgr24", size: tuple[int, int] | None = None):
        self.errors = tempfile.TemporaryFile()
        scale = ["-vf", f"scale={size[0]}:{size[1]}:flags=bicubic"] if size and size != (width, height) else []
        self.process = subprocess.Popen([tool("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", pixels, "-s",
                                         f"{width}x{height}", "-r", str(fps), "-i", "-", *scale, "-an", "-c:v", "libx264",
                                         "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(target)],
                                        stdin=subprocess.PIPE, stderr=self.errors)

    def write(self, frame) -> None:
        self.process.stdin.write(frame.tobytes())

    def close(self) -> None:
        self.process.stdin.close()
        if self.process.wait():
            self.errors.seek(0)
            raise RuntimeError("ffmpeg failed while encoding: " + self.errors.read().decode(errors="replace")[-400:])
        self.errors.close()


def clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


# ---------- pose (pose.ts) ----------

def normalize_pose_settings(settings: dict | None) -> dict:
    s = {**POSE_DEFAULTS, **{k: v for k, v in (settings or {}).items() if v is not None and k in POSE_DEFAULTS}}
    return {"poseModel": s["poseModel"] if s["poseModel"] in POSE_MODELS else "lite",
            "depthModel": s["depthModel"] if s["depthModel"] in ("proxy", "depth-anything") else "depth-anything",
            "detectionConfidence": clamp(float(s["detectionConfidence"]), 0, 1), "trackingConfidence": clamp(float(s["trackingConfidence"]), 0, 1),
            "smoothing": clamp(float(s["smoothing"]), 0, 1), "temporalWindow": int(clamp(round(float(s["temporalWindow"])), 1, 30)),
            "maxPeople": int(clamp(round(float(s["maxPeople"])), 1, 4)), "fillGaps": bool(s["fillGaps"]),
            "optimizeForExport": bool(s["optimizeForExport"])}


def pose_model_label(key: str) -> str:
    return {"lite": "MediaPipe Pose (Lite)", "full": "MediaPipe Pose (Full)", "heavy": "MediaPipe Pose (Heavy)"}.get(key, key)


def landmark(point) -> dict:
    out = {"x": float(point.x or 0), "y": float(point.y or 0), "z": float(point.z or 0)}
    for key in ("visibility", "presence"):  # left out when the model gave none, as JSON.stringify drops undefined
        value = getattr(point, key, None)
        if value is not None:
            out[key] = float(value)
    return out


def score_landmarks(landmarks: list[dict]) -> float:
    if not landmarks:
        return 0.0
    return sum(p.get("visibility", p.get("presence", 0.75)) for p in landmarks) / len(landmarks)


def clone_landmarks(landmarks: list[dict]) -> list[dict]:
    return [dict(p) for p in landmarks]


def clone_frame(frame: dict) -> dict:
    out = {**frame, "landmarks": clone_landmarks(frame["landmarks"]), "worldLandmarks": clone_landmarks(frame["worldLandmarks"])}
    if frame.get("poses") is not None:
        out["poses"] = [{"id": p["id"], "landmarks": clone_landmarks(p["landmarks"]), "worldLandmarks": clone_landmarks(p["worldLandmarks"]),
                         "score": p["score"]} for p in frame["poses"]]
    return out


def blend_optional(previous, following, t: float):
    if previous is None:
        return following
    if following is None:
        return previous
    return previous + (following - previous) * t


def mix(previous: list[dict], following: list[dict], t: float, empty: list[dict]) -> list[dict]:
    """interpolateLandmarks and blendLandmarks are the same arithmetic: previous + (following - previous) * t."""

    count = min(len(previous), len(following))
    if not count:
        return clone_landmarks(empty)
    out = []
    for a, b in zip(previous[:count], following[:count]):
        point = {"x": a["x"] + (b["x"] - a["x"]) * t, "y": a["y"] + (b["y"] - a["y"]) * t, "z": a["z"] + (b["z"] - a["z"]) * t}
        for key in ("visibility", "presence"):
            value = blend_optional(a.get(key), b.get(key), t)
            if value is not None:
                point[key] = value
        out.append(point)
    return out


def fill_missing_frames(frames: list[dict], window: int) -> list[dict]:
    def neighbour(index: int, direction: int):
        for offset in range(1, window + 1):
            at = index + offset * direction
            if 0 <= at < len(frames) and frames[at]["landmarks"]:
                return frames[at]
        return None

    out = []
    for index, frame in enumerate(frames):
        if frame["landmarks"]:
            out.append(frame)
            continue
        previous, following = neighbour(index, -1), neighbour(index, 1)
        if not previous and not following:
            out.append(frame)
            continue
        if previous and following:
            t = clamp((frame["time"] - previous["time"]) / max(0.001, following["time"] - previous["time"]), 0, 1)
            landmarks = mix(previous["landmarks"], following["landmarks"], t, previous["landmarks"] or following["landmarks"])
            world = mix(previous["worldLandmarks"], following["worldLandmarks"], t, previous["worldLandmarks"] or following["worldLandmarks"])
            score = previous["score"] + (following["score"] - previous["score"]) * t
            filled = {"time": frame["time"], "landmarks": landmarks, "worldLandmarks": world, "score": score,
                      "poses": [{"id": 0, "landmarks": landmarks, "worldLandmarks": world, "score": score}]}
        else:
            filled = clone_frame(previous or following)
        out.append({**filled, "time": frame["time"], "source": "filled", "filled": True, "score": filled["score"] * 0.92})
    return out


def smooth_frames(frames: list[dict], smoothing: float) -> list[dict]:
    alpha = clamp(1 - smoothing, 0.08, 1)
    previous = previous_world = None
    out = []
    for frame in frames:
        if not frame["landmarks"]:
            out.append(frame)
            continue
        landmarks = mix(previous, frame["landmarks"], alpha, frame["landmarks"]) if previous else clone_landmarks(frame["landmarks"])
        world = (mix(previous_world, frame["worldLandmarks"], alpha, frame["worldLandmarks"])
                 if previous_world and frame["worldLandmarks"] else clone_landmarks(frame["worldLandmarks"]))
        previous, previous_world = landmarks, (world if world else previous_world)
        score = score_landmarks(landmarks) or frame["score"]
        out.append({**frame, "landmarks": landmarks, "worldLandmarks": world, "score": score, "smoothed": True,
                    "poses": [{"id": 0, "landmarks": landmarks, "worldLandmarks": world, "score": score}, *(frame.get("poses") or [])[1:]]})
    return out


def motion_energy(frames: list[dict]) -> float:
    previous, total, count = None, 0.0, 0
    for frame in frames:
        if not frame["landmarks"]:
            continue
        if previous:
            pairs = list(zip(previous, frame["landmarks"]))
            if pairs:
                total += sum(math.sqrt((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2 + (a["z"] - b["z"]) ** 2) for a, b in pairs) / len(pairs)
            count += 1
        previous = frame["landmarks"]
    return total / count if count else 0.0


def summarize_pose(raw: list[dict], frames: list[dict], settings: dict, model: str, fallback: bool) -> dict:
    raw_detected = sum(1 for f in raw if f["landmarks"])
    detected = sum(1 for f in frames if f["landmarks"])
    filled = sum(1 for f in frames if f.get("filled"))
    average = sum(f["score"] for f in frames if f["landmarks"]) / detected if detected else 0.0
    diagnostics = []
    if fallback:
        diagnostics.append(f"{pose_model_label(settings['poseModel'])} was not available, so Lite was used for this run.")
    if not raw_detected:
        diagnostics.append("No body pose was detected. Use a clearer subject, lower confidence, or switch to Camera only mode.")
    elif raw_detected / max(len(raw), 1) < 0.45:
        diagnostics.append("Pose detection is intermittent. Lower detection confidence or trim to frames where the body is more visible.")
    if filled:
        diagnostics.append(f"Filled {filled} short pose gaps using a {settings['temporalWindow']}-frame temporal window.")
    if 0 < average < settings["detectionConfidence"]:
        diagnostics.append("Average landmark confidence is below the selected detection threshold.")
    if settings["maxPeople"] > 1 and not any(len(f.get("poses") or []) > 1 for f in raw):
        diagnostics.append("Multi-person tracking is enabled, but only one person was detected in this range.")
    if not diagnostics:
        diagnostics.append("Pose track is ready for export.")
    return {"totalFrames": len(frames), "detectedFrames": detected, "rawDetectedFrames": raw_detected, "filledFrames": filled,
            "missingFrames": len(frames) - detected,
            "lowConfidenceFrames": sum(1 for f in frames if f["landmarks"] and f["score"] < settings["detectionConfidence"]),
            "maxPeopleDetected": max([0, *(len(f.get("poses") or []) for f in raw)]), "averageScore": average,
            "motionEnergy": motion_energy(frames), "settings": settings, "runtimeModel": model, "runtimeDelegate": "CPU",
            "diagnostics": diagnostics}


def empty_pose(reference: dict, fps: float, settings: dict, why: str) -> dict:
    """pose_landmarks.json for a pack made without a pose track: the file other tools expect, with no frames."""

    return {"fps": fps, "duration": reference["duration"], "width": reference["width"], "height": reference["height"], "frames": [],
            "summary": {"totalFrames": 0, "detectedFrames": 0, "rawDetectedFrames": 0, "filledFrames": 0, "missingFrames": 0,
                        "lowConfidenceFrames": 0, "maxPeopleDetected": 0, "averageScore": 0, "motionEnergy": 0, "settings": settings,
                        "diagnostics": [why]}}


def analyze_pose(video: Path, reference: dict, fps: float, settings: dict, progress: Progress) -> dict:
    import mediapipe as mp
    import numpy as np
    from mediapipe.tasks import python as tasks
    from mediapipe.tasks.python import vision

    plan = [key for key in dict.fromkeys([settings["poseModel"], "lite"]) if (models_dir() / POSE_MODELS[key]).is_file()]
    if not plan:
        raise RuntimeError(f"no pose model in {models_dir()}")
    landmarker = None
    for model in plan:  # the model asked for, then Lite, on the CPU
        try:
            landmarker = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
                base_options=tasks.BaseOptions(model_asset_path=str(models_dir() / POSE_MODELS[model]), delegate=tasks.BaseOptions.Delegate.CPU),
                running_mode=vision.RunningMode.VIDEO, num_poses=settings["maxPeople"],
                min_pose_detection_confidence=settings["detectionConfidence"], min_pose_presence_confidence=settings["detectionConfidence"],
                min_tracking_confidence=settings["trackingConfidence"]))
            break
        except Exception as error:  # noqa: BLE001 - try the next model
            last = error
    if landmarker is None:
        raise RuntimeError(f"could not load a MediaPipe pose model: {last}")
    duration = reference["duration"] or 1.0
    total = max(1, math.ceil(duration * fps))
    raw = []
    try:
        for index, frame in enumerate(read_frames(video, fps, reference["width"], reference["height"])):
            when = min(index / fps, max(duration - 0.001, 0))
            result = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(frame)),
                                                 int(round(index * 1000 / fps)))
            people, worlds = result.pose_landmarks or [], result.pose_world_landmarks or []
            poses = []
            for person, points in enumerate(people[:settings["maxPeople"]]):
                marks = [landmark(p) for p in points]
                poses.append({"id": person, "landmarks": marks,
                              "worldLandmarks": [landmark(p) for p in (worlds[person] if person < len(worlds) else [])],
                              "score": score_landmarks(marks)})
            first = poses[0] if poses else None
            raw.append({"time": when, "landmarks": first["landmarks"] if first else [], "worldLandmarks": first["worldLandmarks"] if first else [],
                        "score": first["score"] if first else 0, "poses": poses, "source": "detected" if first else "missing"})
            progress.step(index + 1, max(total, index + 1), f"Tracking pose {index + 1}/{max(total, index + 1)}")
    finally:
        landmarker.close()
    frames = [clone_frame(f) for f in raw]
    if settings["fillGaps"]:
        frames = fill_missing_frames(frames, settings["temporalWindow"])
    if settings["smoothing"] > 0:
        frames = smooth_frames(frames, settings["smoothing"])
    return {"fps": fps, "duration": duration, "width": reference["width"], "height": reference["height"], "frames": frames,
            "summary": summarize_pose(raw, frames, settings, model, model != settings["poseModel"])}


# ---------- OpenPose BODY_25 (openpose.ts) and the high-contrast skeleton (poseVideo.ts) ----------

BODY_25_FROM_MEDIAPIPE = [0, (11, 12), 12, 14, 16, 11, 13, 15, (23, 24), 24, 26, 28, 23, 25, 27, 5, 2, 8, 7, 31, 31, 29, 32, 32, 30]
BODY_25_PAIRS = [(1, 0), (1, 2), (1, 5), (2, 3), (3, 4), (5, 6), (6, 7), (1, 8), (8, 9), (9, 10), (10, 11), (8, 12), (12, 13),
                 (13, 14), (1, 8), (0, 15), (0, 16), (15, 17), (16, 18), (14, 19), (19, 20), (14, 21), (11, 22), (22, 23), (11, 24)]
BODY_25_COLORS = ["#ff0000", "#ff5500", "#ffaa00", "#ffff00", "#aaff00", "#55ff00", "#00ff00", "#00ff55", "#00ffaa", "#00ffff",
                  "#00aaff", "#0055ff", "#0000ff", "#5500ff", "#aa00ff", "#ff00ff", "#ff00aa", "#ff0055", "#ff0000", "#ff0033",
                  "#ff0066", "#ff0099", "#3300ff", "#6600ff", "#9900ff"]
POSE_CONNECTIONS = [(11, 12), (11, 13), (13, 15), (15, 17), (15, 19), (15, 21), (17, 19), (12, 14), (14, 16), (16, 18), (16, 20),
                    (16, 22), (18, 20), (11, 23), (12, 24), (23, 24), (23, 25), (24, 26), (25, 27), (26, 28), (27, 29), (28, 30),
                    (29, 31), (30, 32), (27, 31), (28, 32), (0, 1), (1, 2), (2, 3), (0, 4), (4, 5), (5, 6), (3, 7), (6, 8), (9, 10)]


def bgr(colour: str, alpha: float = 1.0) -> tuple[int, int, int]:
    """A CSS hex colour as OpenCV's blue, green, red; `alpha` is the canvas globalAlpha over the black background."""

    value = colour.lstrip("#")
    red, green, blue = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    return int(blue * alpha), int(green * alpha), int(red * alpha)


def confidence_of(point: dict) -> float:
    value = point.get("visibility", point.get("presence"))
    return 0.9 if value is None else clamp(value, 0, 1)


def body25(landmarks: list[dict], width: int, height: int) -> list[tuple[float, float, float]]:
    """One MediaPipe person as BODY_25 keypoints (x, y, confidence) in pixels; a joint with no source is (0, 0, 0)."""

    out = []
    for source in BODY_25_FROM_MEDIAPIPE:
        if isinstance(source, tuple):
            a, b = (landmarks[i] if i < len(landmarks) else None for i in source)
            out.append(((a["x"] + b["x"]) / 2 * width, (a["y"] + b["y"]) / 2 * height, min(confidence_of(a), confidence_of(b))) if a and b else (0.0, 0.0, 0.0))
        else:
            point = landmarks[source] if source < len(landmarks) else None
            out.append((point["x"] * width, point["y"] * height, confidence_of(point)) if point else (0.0, 0.0, 0.0))
    return out


def js_round(value: float, digits: int) -> float:
    scale = 10 ** digits
    return math.floor(value * scale + 0.5) / scale  # Math.round rounds halves up


def frame_people(frame: dict | None) -> list[dict]:
    if not frame:
        return []
    if frame.get("poses"):
        return frame["poses"]
    if frame.get("landmarks"):
        return [{"id": 0, "landmarks": frame["landmarks"], "worldLandmarks": frame["worldLandmarks"], "score": frame["score"]}]
    return []


def openpose_json(pose: dict, width: int, height: int) -> list[dict]:
    """One OpenPose object per pose frame, 75 numbers per person, in pixels of the reference clip."""

    out = []
    for frame in pose["frames"]:
        people = []
        for index, person in enumerate(frame_people(frame)):
            flat = []
            for x, y, c in body25(person["landmarks"], width, height):
                flat += [js_round(x, 2), js_round(y, 2), js_round(c, 4)]
            people.append({"person_id": [-1 if index == 0 else index], "pose_keypoints_2d": flat})
        out.append({"version": 1.3, "people": people})
    return out


def draw_openpose(frame: dict | None, width: int, height: int):
    import cv2
    import numpy as np

    canvas = np.zeros((height, width, 3), np.uint8)
    limb = max(3, round(min(width, height) * 0.012))
    joint = max(3, round(min(width, height) * 0.008))
    for person in frame_people(frame):
        points = body25(person["landmarks"], width, height)
        for a, b in BODY_25_PAIRS:
            (ax, ay, ac), (bx, by, bc) = points[a], points[b]
            if ac <= 0.05 or bc <= 0.05:
                continue
            cv2.line(canvas, (round(ax), round(ay)), (round(bx), round(by)), bgr(BODY_25_COLORS[a], min(1, min(ac, bc) + 0.25)), limb, cv2.LINE_AA)
        for index, (x, y, c) in enumerate(points):
            if c > 0.05:
                cv2.circle(canvas, (round(x), round(y)), joint, bgr(BODY_25_COLORS[index], min(1, c + 0.3)), -1, cv2.LINE_AA)
    return canvas


def connection_colour(a: int, b: int) -> str:
    if a <= 10 or b <= 10:
        return "#e646d8"
    for group, colour in (({11, 13, 15, 17, 19, 21}, "#46f083"), ({12, 14, 16, 18, 20, 22}, "#56e7ff"),
                          ({23, 25, 27, 29, 31}, "#f4e855"), ({24, 26, 28, 30, 32}, "#ff7a2f")):
        if a in group or b in group:
            return colour
    return "#f5d76e"


def draw_pose(frame: dict | None, width: int, height: int):
    """The high-contrast MediaPipe skeleton: 33 landmarks on a dark grid, limbs coloured by side."""

    import cv2
    import numpy as np

    canvas = np.full((height, width, 3), bgr("#030405"), np.uint8)
    for x in range(0, width, 80):
        cv2.line(canvas, (x, 0), (x, height), bgr("#172226"), 1)
    for y in range(0, height, 80):
        cv2.line(canvas, (0, y), (width, y), bgr("#172226"), 1)
    people = frame_people(frame)
    if not people:
        size = cv2.getTextSize("No pose detected", cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)[0]
        cv2.putText(canvas, "No pose detected", ((width - size[0]) // 2, height // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.6, bgr("#667176"), 2, cv2.LINE_AA)
        return canvas
    for index, person in enumerate(people):
        points, fade = person["landmarks"], (1.0 if index == 0 else 0.58)
        for a, b in POSE_CONNECTIONS:
            if a >= len(points) or b >= len(points):
                continue
            sure = min(points[a].get("visibility", 1), points[b].get("visibility", 1)) > 0.5
            cv2.line(canvas, (round(points[a]["x"] * width), round(points[a]["y"] * height)),
                     (round(points[b]["x"] * width), round(points[b]["y"] * height)),
                     bgr(connection_colour(a, b), fade if sure else fade * 0.38), 7 if sure else 3, cv2.LINE_AA)
        for at, point in enumerate(points):
            sure = point.get("visibility", 1) > 0.5
            centre, radius = (round(point["x"] * width), round(point["y"] * height)), (5 if at <= 10 else 7)
            cv2.circle(canvas, centre, radius, bgr("#e646d8" if at == 0 else "#8ff5ff", fade if sure else fade * 0.45), -1, cv2.LINE_AA)
            cv2.circle(canvas, centre, radius, bgr("#050708"), 2, cv2.LINE_AA)
    label = f"{(frame or {}).get('time', 0):.2f}s  {'filled gap' if (frame or {}).get('source') == 'filled' else 'tracked'}"
    cv2.putText(canvas, label, (18, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.5, bgr("#c8d2d2"), 1, cv2.LINE_AA)
    return canvas


# ---------- camera solve (cameraMotion.ts, on OpenCV) ----------

CORNER_TARGET = 150
LK_WINDOW = 21
PYRAMID_LEVELS = 3
ANALYSIS_EDGE = 384        # the original tracks on a 192 pixel canvas, a size chosen for JavaScript
RANSAC_INLIER_PX = 2.0     # the original's 1.6 at 192 pixels
MASK_SEGMENTS = [(11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24), (23, 25), (25, 27), (24, 26), (26, 28)]


def analysis_size(width: int, height: int) -> tuple[int, int]:
    if not width or not height:
        return ANALYSIS_EDGE, round(ANALYSIS_EDGE * 0.5625)
    if width >= height:
        return ANALYSIS_EDGE, max(48, round(height / width * ANALYSIS_EDGE))
    return max(48, round(width / height * ANALYSIS_EDGE)), ANALYSIS_EDGE


def visible(point: dict) -> bool:
    value = point.get("visibility", point.get("presence"))
    return True if value is None else value > 0.3


def subject_mask(width: int, height: int, frame: dict | None):
    """255 where a corner may be seeded: everything but capsules round the limbs and a circle round the head."""

    import cv2
    import numpy as np

    people = [p["landmarks"] for p in frame_people(frame)]
    if not people:
        return None
    mask = np.full((height, width), 255, np.uint8)
    radius = max(4, round(min(width, height) * 0.06))
    drawn = False
    for marks in people:
        if marks and visible(marks[0]):
            cv2.circle(mask, (round(marks[0]["x"] * width), round(marks[0]["y"] * height)), round(radius * 1.3), 0, -1)
            drawn = True
        for a, b in MASK_SEGMENTS:
            if a < len(marks) and b < len(marks) and visible(marks[a]) and visible(marks[b]):
                cv2.line(mask, (round(marks[a]["x"] * width), round(marks[a]["y"] * height)),
                         (round(marks[b]["x"] * width), round(marks[b]["y"] * height)), 0, radius * 2)
                drawn = True
    return mask if drawn else None


def pose_frame_at(pose: dict | None, when: float) -> dict | None:
    frames = (pose or {}).get("frames") or []
    return min(frames, key=lambda f: abs(f["time"] - when)) if frames else None


def seed_corners(gray, mask):
    """Shi-Tomasi corners on the background. With no pose track the middle of the frame, where a subject usually is,
    is the last place corners are taken from (the original halves the score there)."""

    import cv2
    import numpy as np

    height, width = gray.shape
    spacing = max(3, round(min(width, height) / 18))
    find = lambda limit, where: cv2.goodFeaturesToTrack(gray, maxCorners=limit, qualityLevel=0.01, minDistance=spacing, mask=where, blockSize=3)
    if mask is not None:
        found = find(CORNER_TARGET, mask)
        return found if found is not None else np.empty((0, 1, 2), np.float32)
    edge = np.full((height, width), 255, np.uint8)
    edge[round(height * 0.275):round(height * 0.725), round(width * 0.275):round(width * 0.725)] = 0  # edgeBias <= 0.45
    found = find(CORNER_TARGET, edge)
    found = found if found is not None else np.empty((0, 1, 2), np.float32)
    if len(found) < CORNER_TARGET * 0.6:
        more = find(CORNER_TARGET - len(found), 255 - edge)
        if more is not None:
            found = np.concatenate([found, more])
    return found


def solve_step(previous, current, mask) -> dict:
    """The similarity (shift, zoom, roll) that carries the background of `previous` onto `current`."""

    import cv2
    import numpy as np

    height, width = previous.shape
    corners = seed_corners(previous, mask)
    identity = {"tx": 0.0, "ty": 0.0, "scale": 1.0, "rotation": 0.0, "inlierRatio": 0.0}
    if len(corners) == 0:
        return identity
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.01)
    window = dict(winSize=(LK_WINDOW, LK_WINDOW), maxLevel=PYRAMID_LEVELS - 1, criteria=criteria)
    moved, found, _ = cv2.calcOpticalFlowPyrLK(previous, current, corners, None, **window)
    back, found_back, _ = cv2.calcOpticalFlowPyrLK(current, previous, moved, None, **window)
    start, end = corners.reshape(-1, 2), moved.reshape(-1, 2)
    flow = end - start
    keep = ((found.ravel() == 1) & (found_back.ravel() == 1) & (np.abs(back.reshape(-1, 2) - start).max(axis=1) < 1.0)
            & (end[:, 0] >= 0) & (end[:, 1] >= 0) & (end[:, 0] < width) & (end[:, 1] < height)
            & (np.abs(flow).max(axis=1) <= min(width, height) * 0.5))
    start, end = start[keep], end[keep]
    if len(start) < 3:
        return {**identity, "inlierRatio": 0.15 if len(start) else 0.0}
    cv2.setRNGSeed(0x1e3779b9)  # the same clip gives the same solve every run
    matrix, inliers = cv2.estimateAffinePartial2D(start, end, method=cv2.RANSAC, ransacReprojThreshold=RANSAC_INLIER_PX,
                                                  maxIters=2000, confidence=0.995, refineIters=10)
    count = int(inliers.sum()) if inliers is not None else 0
    if matrix is None or count < 3:
        shift = np.median(end - start, axis=0)  # a slow shot that starves RANSAC still gets its translation
        return {"tx": float(shift[0]), "ty": float(shift[1]), "scale": 1.0, "rotation": 0.0, "inlierRatio": 0.2}
    a, b = float(matrix[0, 0]), float(matrix[1, 0])
    cx, cy = (width - 1) / 2, (height - 1) / 2
    # The shift is taken at the centre of the frame, not at its top-left corner as in the original: a push-in on the
    # middle of the picture then has no shift, where the original reads it as a pan up and to the left.
    tx = a * cx - b * cy + float(matrix[0, 2]) - cx
    ty = b * cx + a * cy + float(matrix[1, 2]) - cy
    limit = max(width, height) * 0.6
    return {"tx": clamp(tx, -limit, limit), "ty": clamp(ty, -limit, limit), "scale": clamp(math.hypot(a, b), 0.8, 1.25),
            "rotation": clamp(true_roll(start[inliers.ravel() == 1], end[inliers.ravel() == 1], math.atan2(b, a)), -0.2, 0.2),
            "inlierRatio": count / len(start)}


def true_roll(start, end, fitted: float) -> float:
    """The roll in a step, kept only where the picture turns as a whole.

    A camera that travels past near and far things (an orbit, a push-in over a cliff) shears the picture: things low
    in the frame slide sideways faster than things high in it. A similarity fit, the original's and this one's, reads
    part of that shear as rotation, and the sum over a shot becomes a roll of several degrees that is not there. A
    real roll turns both axes the same way; so the points are also fitted with a free 2x2 matrix, and the roll is the
    smaller of the two axes' turns when they agree in direction, and none when they do not."""

    import numpy as np

    if len(start) < 6:
        return fitted
    design = np.hstack([start, np.ones((len(start), 1), start.dtype)]).astype(np.float64)
    solved, *_ = np.linalg.lstsq(design, end.astype(np.float64), rcond=None)
    across, down = math.atan2(solved[0, 1], solved[0, 0]), math.atan2(-solved[1, 0], solved[1, 1])  # the x axis, the y axis
    if across * down <= 0:
        return 0.0
    return math.copysign(min(abs(across), abs(down), abs(fitted)), across)


def analyze_camera(video: Path, reference: dict, fps: float, pose: dict | None, progress: Progress) -> dict:
    sample = min(max(fps, 2), 12)
    duration = reference["duration"] or 1.0
    total = max(1, math.ceil(duration * sample))
    width, height = analysis_size(reference["width"], reference["height"])
    sx, sy = reference["width"] / width, reference["height"] / height
    previous, frames = None, []
    x = y = roll = 0.0
    zoom = 1.0
    for index, frame in enumerate(read_frames(video, sample, width, height, "gray")):
        gray = frame[:, :, 0]
        motion, confidence = {"xPixels": 0, "yPixels": 0, "scale": 1, "rollRadians": 0}, 1
        if previous is not None:
            when = min(index / sample, max(duration - 0.001, 0))
            step = solve_step(previous, gray, subject_mask(width, height, pose_frame_at(pose, when - 1 / sample)))
            motion = {"xPixels": step["tx"] * sx, "yPixels": step["ty"] * sy, "scale": step["scale"], "rollRadians": step["rotation"]}
            confidence = clamp(step["inlierRatio"], 0, 1)
            x, y, zoom, roll = x + motion["xPixels"], y + motion["yPixels"], zoom * step["scale"], roll + step["rotation"]
        frames.append({"time": min(index / sample, max(duration - 0.001, 0)), "frameIndex": index, "imageMotion": motion,
                       "cameraMove": {"pan": -x / max(reference["width"], 1), "tilt": -y / max(reference["height"], 1), "dollyZoom": zoom, "roll": -roll},
                       "confidence": confidence})
        previous = gray
        progress.step(index + 1, max(total, index + 1), f"Solving camera move {index + 1}/{max(total, index + 1)}")
    last = frames[-1]["cameraMove"] if frames else None
    return {"fps": sample, "duration": duration, "width": reference["width"], "height": reference["height"], "frames": frames,
            "summary": {"panPixels": -last["pan"] * reference["width"] if last else 0, "tiltPixels": -last["tilt"] * reference["height"] if last else 0,
                        "zoomRatio": (last["dollyZoom"] if last else 1) or 1, "rollDegrees": (last["roll"] if last else 0) * 180 / math.pi,
                        "averageConfidence": sum(f["confidence"] for f in frames) / max(len(frames), 1)}}


# ---------- AI depth (aiDepth.ts: Depth-Anything-small, the same ONNX file transformers.js loads) ----------

def ai_depth(video: Path, target: Path, reference: dict, fps: float, progress: Progress) -> None:
    import numpy as np
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = int(os.environ.get("MOTION_DEPTH_THREADS", min(8, os.cpu_count() or 4)))
    session = ort.InferenceSession(str(models_dir() / DEPTH_MODEL), options, providers=["CPUExecutionProvider"])
    name = session.get_inputs()[0].name
    # the model's own preprocessing: a multiple of 14 on each side, ImageNet mean and deviation. The long side is 518
    # (the processor in the original makes the short side 518; this is a quarter of the work for a control layer).
    edge = int(os.environ.get("MOTION_DEPTH_EDGE", 518))
    ratio = reference["width"] / reference["height"]
    width, height = (edge, max(14, round(edge / ratio / 14) * 14)) if ratio >= 1 else (max(14, round(edge * ratio / 14) * 14), edge)
    width = max(14, width // 14 * 14)
    mean, deviation = np.array([0.485, 0.456, 0.406], np.float32), np.array([0.229, 0.224, 0.225], np.float32)
    total = max(1, math.ceil((reference["duration"] or 1.0) * fps))
    encoder, low, high = None, None, None
    try:
        for index, frame in enumerate(read_frames(video, fps, width, height)):
            pixels = ((frame.astype(np.float32) / 255.0 - mean) / deviation).transpose(2, 0, 1)[None]
            depth = np.squeeze(session.run(None, {name: pixels})[0]).astype(np.float32)
            # near is bright, as in the original; the range follows the clip slowly so the levels do not flicker
            near, far = float(np.percentile(depth, 99.5)), float(np.percentile(depth, 0.5))
            low, high = (far, near) if low is None else (low + (far - low) * 0.15, high + (near - high) * 0.15)
            grey = (np.clip((depth - low) / max(high - low, 1e-6), 0, 1) * 255).astype(np.uint8)
            if encoder is None:
                encoder = Encoder(target, grey.shape[1], grey.shape[0], fps, "gray", (reference["width"], reference["height"]))
            encoder.write(grey)
            progress.step(index + 1, max(total, index + 1), f"Rendering AI depth {index + 1}/{max(total, index + 1)}")
    finally:
        if encoder:
            encoder.close()
    if encoder is None:
        raise RuntimeError("no frames were read for the AI depth pass")


# ---------- quality (quality.ts / shared/quality.cjs) ----------

def quality_band(value: float) -> str:
    return "Excellent" if value >= 0.82 else "Good" if value >= 0.64 else "Review" if value > 0 else "Missing"


def quality_report(pose: dict, camera: dict | None, layer_count: int) -> dict:
    summary = pose["summary"]
    raw, filled, total = summary.get("rawDetectedFrames", 0), summary.get("filledFrames", 0), len(pose["frames"])
    tracking = clamp(raw / total + min(filled / total, 0.18), 0, 1) if total else 0.0
    confidence = clamp((camera or {}).get("summary", {}).get("averageConfidence", 0), 0, 1)
    layers = min(1.0, layer_count / 6)
    score = math.floor(tracking * 34 + confidence * 42 + layers * 24 + 0.5)
    return {"score": score, "tracking": quality_band(tracking), "camera": quality_band(confidence) if camera else "Missing",
            "layers": quality_band(layers), "readiness": "Ready" if score >= 80 else "Review" if score >= 58 else "Blocked",
            "notes": [f"Pose frames detected: {raw}/{total}", f"Filled pose gaps: {filled}", f"Camera confidence: {round(confidence * 100)}%",
                      "Control layers generated: depth, edges, lineart, motion mask, normals proxy, pose, camera."]}


# ---------- the camera move in words ----------

SHAKE = 0.004  # of the frame per sample: above this the shot reads as handheld

def describe_camera(camera: dict | None, seconds: float | None = None) -> dict:
    """camera_motion.json as a sentence or two a cinematographer would say, for a video model's prompt.

    Returns {"sentence", "moves", "confidence", "reliable"}. Signs: pan is the camera turning right, the picture's
    content sliding left; tilt up is the content sliding down. A flat solve cannot tell a pan from a sideways track,
    a tilt from a crane, or a zoom from a dolly: the words name what it looks like on screen.
    """

    frames = (camera or {}).get("frames") or []
    if len(frames) < 2:
        return {"sentence": "", "moves": [], "confidence": 0.0, "reliable": False}
    length = float(seconds or camera.get("duration") or frames[-1]["time"] or 1.0)
    pan = [f["cameraMove"]["pan"] for f in frames]                       # of the frame width, + is right
    tilt = [-f["cameraMove"]["tilt"] for f in frames]                    # of the frame height, + is up
    zoom = [math.log(max(f["cameraMove"]["dollyZoom"], 1e-3)) for f in frames]
    roll = [math.degrees(f["cameraMove"]["roll"]) for f in frames]       # + is clockwise
    confidence = float(camera.get("summary", {}).get("averageConfidence", 0))

    def amount(track, floor):
        """(final value, whether it went out and came back, where in the shot most of it happens)."""
        end, peak = track[-1], max(track, key=abs)
        if abs(peak) >= floor and abs(end) < 0.4 * abs(peak):
            return peak, True, None
        if abs(end) < floor:
            return 0.0, False, None
        last = max(frames[-1]["time"], 1e-6)
        passed = lambda part: next((f["time"] for f, v in zip(frames, track) if abs(v) >= part * abs(end) and v * end > 0), 0.0)
        return end, False, (passed(0.2) / last, passed(0.8) / last)

    def pace(per_second, slow, fast):
        return "slowly " if per_second < slow else "quickly " if per_second > fast else ""

    def span(value):
        value = abs(value)
        return ("a little" if value < 0.08 else "across about a tenth of the frame" if value < 0.15
                else "across about a quarter of the frame" if value < 0.3 else "across about a third of the frame" if value < 0.42
                else "across about half the frame" if value < 0.65 else "across most of the frame" if value < 0.9
                else "across more than a full frame")

    moves, parts, timing = [], [], []
    for name, track, words in (("pan", pan, ("pans left", "pans right")), ("tilt", tilt, ("tilts down", "tilts up"))):
        value, returns, when = amount(track, 0.03)
        if not value:
            continue
        verb = words[value > 0]
        if returns:
            text = f"{verb} {span(value)} and comes back"
        elif abs(value) / length > 0.7:
            text = f"whip-{verb} {span(value)}"
        else:
            text = f"{pace(abs(value) / length, 0.05, 0.22)}{verb} {span(value)}"
        moves.append({"move": name, "direction": verb.split()[1], "amount": round(value, 4), "returns": returns})
        parts.append((abs(value), text))
        timing.append(when)
    value, returns, when = amount(zoom, 0.04)
    if value:
        ratio = math.exp(abs(value))
        verb = "pushes in" if value > 0 else "pulls back"
        size = " slightly" if ratio < 1.12 else "" if ratio < 1.4 else " a long way"
        text = f"{verb} and returns" if returns else f"{pace(abs(value) / length, 0.03, 0.15)}{verb}{size}"
        moves.append({"move": "zoom", "direction": "in" if value > 0 else "out", "amount": round(math.exp(value), 4), "returns": returns})
        parts.append((abs(value) * 1.5, text))
        timing.append(when)
    # under 4 degrees over a shot is not called a roll: what is left of parallax after true_roll is of that size
    value, returns, when = amount(roll, 4.0)
    if value:
        text = f"rolls {'clockwise' if value > 0 else 'counter-clockwise'} about {round(abs(value))} degrees" + (" and back" if returns else "")
        moves.append({"move": "roll", "direction": "clockwise" if value > 0 else "counter-clockwise", "amount": round(value, 2), "returns": returns})
        parts.append((abs(value) / 60, text))
        timing.append(when)

    # unsteadiness: what is left of the frame-to-frame shift once the move itself is taken out
    width, height = max(camera.get("width", 1), 1), max(camera.get("height", 1), 1)
    dx = [f["imageMotion"]["xPixels"] / width for f in frames[1:]]
    dy = [f["imageMotion"]["yPixels"] / height for f in frames[1:]]
    near = max(1, round(camera.get("fps", 12) * 0.25))

    def rough(track):
        smooth = [sum(track[max(0, i - near):i + near + 1]) / len(track[max(0, i - near):i + near + 1]) for i in range(len(track))]
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(track, smooth)) / len(track))

    shake = math.hypot(rough(dx), rough(dy))
    parts.sort(key=lambda item: -item[0])
    if not parts:
        sentence = ("Handheld shot that holds its framing: the camera stays on one spot with a slight natural shake." if shake > SHAKE
                    else "Locked-off static shot on a still camera.")
    else:
        words = [text for _, text in parts]
        sentence = "The camera " + (words[0] if len(words) == 1 else ", ".join(words[:-1]) + " while it " + words[-1]) + "."
        when = next((w for w in timing if w), None)
        if shake > SHAKE:
            sentence += " The move is handheld, with a slight natural shake."
        elif when and when[0] > 0.4:
            sentence += " The move starts gently and gathers pace toward the end of the shot."
        elif when and when[1] < 0.6:
            sentence += " The move is quickest at the start and then eases to a stop."
        else:
            sentence += " The move is smooth and continuous through the whole shot."
    return {"sentence": sentence, "moves": moves, "confidence": round(confidence, 4), "reliable": confidence >= 0.3,
            "shake": round(shake, 5)}


# ---------- the pack (main.cjs savePoseArtifacts) ----------

def mode_instruction(mode: str) -> str:
    return {"camera-only": "Preserve only the camera move and timing. Replace the character, car, object, and environment freely.",
            "actor-motion": "Preserve the actor body motion and camera movement. Replace identity, wardrobe, styling, and environment.",
            "object-motion": "Preserve the motion path of the main object or vehicle plus camera movement. Replace design, surface, and world.",
            }.get(mode, "Preserve camera, blocking, subject motion, and depth rhythm as a full-scene reference.")


def planning_data(job: dict, duration: float, quality: dict, settings: dict, camera_on: bool) -> dict:
    plan = job.get("planning") or {}
    mode = job["mode"]
    scene, shot = (plan.get("sceneTitle") or "").strip() or "Scene 01", (plan.get("shotTitle") or "").strip() or "Shot 01A"
    intent = (plan.get("creativeIntent") or "").strip()
    return {"projectTitle": (plan.get("projectTitle") or "").strip() or "Motion Previs Project", "sceneTitle": scene, "shotTitle": shot,
            "creativeIntent": intent or "Preserve reference timing and camera language for a new AI-film shot.",
            "visualStyle": (plan.get("visualStyle") or "").strip() or "Professional cinematic previs.", "subjectMode": mode,
            "selectedLayers": job["layers"], "exportPresets": ["seedance", "comfyui", "blender"],
            "shotBible": [{"id": "shot-001", "scene": scene, "shot": shot, "description": intent or "Reference-derived previs shot.",
                           "duration": duration, "subjectMode": mode,
                           "cameraIntent": ("Recreate the solved camera pan, tilt, zoom, roll, and timing from the reference." if camera_on
                                            else "Camera solve disabled for this pass."), "selected": True}],
            "qualityReport": quality, "analysisSettings": settings}


def write_texts(folder: Path, job: dict, planning: dict, pose: dict, said: dict) -> None:
    source, span, mode, quality = job["source_name"], job["range"], job["mode"], planning["qualityReport"]
    dump = lambda name, data: (folder / name).write_text(json.dumps(data, indent=2))
    dump("comfyui_manifest.json", {
        "name": f"Motion Previs Studio v{SOURCE['version']} Control Reference Bundle", "source": source, "range": span,
        "recommendedInputs": [
            {"role": "reference_video", "file": "reference.mp4"},
            {"role": "depth_control", "file": "ai_depth.mp4", "note": "Use when present for AI monocular depth conditioning."},
            {"role": "fast_depth_proxy", "file": "depth.mp4", "note": "Always present fast local proxy depth pass."},
            {"role": "pose_control", "file": "pose_high_contrast.mp4", "note": "Use with OpenPose/DWPose-style control workflows."},
            {"role": "camera_move", "file": "camera_motion.json", "note": "Use to recreate shot camera pan, tilt, zoom, and roll without preserving the subject."},
            {"role": "edge_control", "file": "edges.mp4", "note": "Use for composition, silhouettes, and line fidelity."},
            {"role": "lineart_control", "file": "lineart.mp4", "note": "Use for strong graphic line guidance."},
            {"role": "mask_motion_proxy", "file": "motion_mask.mp4", "note": "Use as a motion/foreground proxy mask."},
            {"role": "normals_proxy", "file": "normals_proxy.mp4", "note": "Use as a depth-gradient normals proxy."},
            {"role": "combined_review", "file": "combined_reference_depth_pose.mp4"}],
        "comfyuiNotes": [
            "Load reference.mp4, ai_depth.mp4 or depth.mp4, and pose_high_contrast.mp4 as separate conditioning sources when possible.",
            "If a workflow only accepts one control movie, start with ai_depth.mp4 when present, otherwise depth.mp4, then test pose_high_contrast.mp4.",
            "pose_landmarks.json preserves MediaPipe landmark coordinates for custom nodes or retiming.",
            "camera_motion.json contains global camera move keyframes for subject-independent camera recreation.",
            "edges.mp4, lineart.mp4, motion_mask.mp4, and normals_proxy.mp4 provide extra control-layer options for precision workflows."]})
    pose_line = ("- `pose_high_contrast.mp4` only if you decide to reintroduce body-motion guidance; keep it optional for camera-only remixes."
                 if mode == "camera-only" else "- `pose_high_contrast.mp4` as the human/object motion control input.")
    (folder / "seedance_prompt.md").write_text("\n".join([
        "# Seedance / AI Video Reference Notes", "", f"Source: {source or 'Imported clip'}",
        f"Shot range: {span['start']:.2f}s to {span['end']:.2f}s", f"Subject mode: {mode}", "",
        "Use the attached reference assets according to the selected subject mode.", mode_instruction(mode), "", "Suggested inputs:",
        "- `reference.mp4` for composition, staging, timing, and camera feel.",
        "- `ai_depth.mp4` as the primary AI monocular depth control input when present.",
        "- `depth.mp4` as the fast local proxy depth control input.", pose_line,
        "- `camera_motion.json` to recreate the camera pan, tilt, zoom, and roll without reusing the original character, car, or object motion.",
        "- `edges.mp4`, `lineart.mp4`, `motion_mask.mp4`, and `normals_proxy.mp4` as optional precision control layers.",
        "- `blender_import_camera.py` to build an animated Blender camera from the solved camera move.",
        "- `blender_import_scene.py` to assemble a Blender scene with camera, pose, and reference plates.",
        "- `combined_reference_depth_pose.mp4` for a quick visual review plate.", "", "Prompt starter:",
        "Create a new shot that follows the same camera move, depth rhythm, and timing as the reference. Do not copy the original subject or objects unless requested; use the camera_motion.json / camera reference to preserve the movement of the camera independently from the scene content."]))
    (folder / "prompt_pack.md").write_text("\n".join([
        f"# Motion Previs Studio v{SOURCE['version']} Prompt Pack", "", f"Project: {planning['projectTitle']}", f"Scene: {planning['sceneTitle']}",
        f"Shot: {planning['shotTitle']}", f"Subject mode: {mode}", f"Quality score: {quality['score']}/100 ({quality['readiness']})", "",
        "## Creative Intent", planning["creativeIntent"], "", "## Visual Style", planning["visualStyle"], "",
        "## Universal Camera Instruction",
        "Use the supplied camera_motion.json and reference timing to preserve camera movement, lens rhythm, pan/tilt/zoom, and shot duration. Do not copy the original subject unless the subject mode explicitly asks for it.",
        "", "## Subject Handling", mode_instruction(mode), "", "## Seedance",
        "Use reference.mp4 for timing, ai_depth.mp4 or depth.mp4 for depth structure, pose_high_contrast.mp4 only when subject motion should be retained, and camera_motion.json as the camera-move authority.",
        "", "## ComfyUI",
        "Load separate control inputs where possible: depth/ai_depth, pose, edges, lineart, motion_mask, and camera_motion.json. Disable pose when using Camera only mode.",
        "", "## Runway / Kling",
        "Use the reference clip for camera/timing, but phrase the prompt around new subjects and environments. Use camera-only wording when replacing the source subject.",
        "", "## Negative Guidance",
        "Do not drift from the camera move. Do not inherit unwanted faces, wardrobe, props, cars, or background objects unless requested. Avoid inconsistent lens changes, sudden perspective jumps, and unmotivated speed ramps.",
        "", "## MiniMax H3 (added by the Python port in Wasserman Slate AI Studio)",
        "The camera move in words, from camera_motion.json: " + (said["sentence"] or "no camera solve in this pack.")
        + ("" if said.get("reliable", True) or not said["sentence"] else " (Low solve confidence: check it against the clip.)")]))
    dump("shot_bible.json", planning["shotBible"])
    dump("quality_report.json", quality)
    dump("model_presets.json", {"selected": planning["exportPresets"], "analysisSettings": pose["summary"].get("settings"),
                                "poseDiagnostics": pose["summary"].get("diagnostics", []), "presets": {
        "seedance": {"primaryInputs": ["reference.mp4", "ai_depth.mp4", "depth.mp4", "pose_high_contrast.mp4", "camera_motion.json"],
                     "guidance": "Use separate depth and pose controls when available. Use camera-only prompt language when replacing subjects."},
        "comfyui": {"primaryInputs": ["depth.mp4", "edges.mp4", "lineart.mp4", "motion_mask.mp4", "pose_high_contrast.mp4"],
                    "guidance": "Wire control videos as separate conditioning branches. Start with depth + camera, then add pose only if needed."},
        "blender": {"primaryInputs": ["blender_import_scene.py", "blender_import_camera.py", "blender_import_pose.py"],
                    "guidance": "Run blender_import_scene.py from Blender Text Editor or Python console with the bundle folder as the script location."},
        "runway": {"primaryInputs": ["reference.mp4", "prompt_pack.md", "camera_motion.json"],
                   "guidance": "Use the prompt pack language to preserve camera while replacing subject and world."},
        "kling": {"primaryInputs": ["reference.mp4", "depth.mp4", "prompt_pack.md"],
                  "guidance": "Use reference video for timing and depth/video controls for structure when available."}}})
    dump("control_layers_manifest.json", {"selectedLayers": planning["selectedLayers"], "layers": [
        {"key": "depth", "file": "depth.mp4", "purpose": "Fast structural/depth proxy."},
        {"key": "ai-depth", "file": "ai_depth.mp4", "purpose": "Optional AI monocular depth pass."},
        {"key": "pose", "file": "pose_high_contrast.mp4", "purpose": "Human pose and motion control."},
        {"key": "camera", "file": "camera_motion.json", "purpose": "Subject-independent camera move."},
        {"key": "edges", "file": "edges.mp4", "purpose": "Composition and silhouette edges."},
        {"key": "lineart", "file": "lineart.mp4", "purpose": "High-contrast line control."},
        {"key": "masks", "file": "motion_mask.mp4", "purpose": "Motion/foreground proxy mask (frame-difference)."},
        {"key": "normals", "file": "normals_proxy.mp4", "purpose": "Depth-gradient normals proxy."}]})
    for script in ("blender_import_pose.py", "blender_import_camera.py", "blender_import_scene.py"):
        shutil.copyfile(HERE / "motion_templates" / script, folder / script)


# bundle_manifest.json "files": the original's keys -> the file each names
BUNDLE_FILES = {"reference": "reference.mp4", "depth": "depth.mp4", "aiDepthWebm": None, "aiDepthMp4": "ai_depth.mp4", "edges": "edges.mp4",
                "lineart": "lineart.mp4", "motionMask": "motion_mask.mp4", "normalsProxy": "normals_proxy.mp4",
                "contactSheet": "contact_sheet.jpg", "animatic": "animatic.mp4", "poseJson": "pose_landmarks.json",
                "cameraMotionJson": "camera_motion.json", "poseWebm": None, "poseMp4": "pose_high_contrast.mp4",
                "openPosePose": "openpose_pose.mp4", "openPoseKeypoints": "openpose_keypoints.json",
                "combined": "combined_reference_depth_pose.mp4", "blenderImportScript": "blender_import_pose.py",
                "blenderCameraImportScript": "blender_import_camera.py", "blenderSceneImportScript": "blender_import_scene.py",
                "comfyuiManifest": "comfyui_manifest.json", "seedancePrompt": "seedance_prompt.md", "promptPack": "prompt_pack.md",
                "shotBible": "shot_bible.json", "qualityReport": "quality_report.json", "modelPresets": "model_presets.json",
                "controlLayersManifest": "control_layers_manifest.json"}


def zip_folder(folder: Path, target: Path) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file() and "handoff" not in path.relative_to(folder).parts and not path.name.startswith("."):
                archive.write(path, path.relative_to(folder).as_posix())


def peak_memory_mb() -> dict:
    scale = 1 if sys.platform == "darwin" else 1024  # bytes on macOS, kilobytes on Linux
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale / 1048576
    children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * scale / 1048576
    return {"analysis": round(own), "largest_ffmpeg": round(children)}


# ---------- one job ----------

def run(job: dict) -> None:
    folder = Path(job["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    can, progress = probe(), Progress()
    layers = [layer for layer in job["layers"] if layer in LAYERS]
    job["layers"] = layers
    settings = normalize_pose_settings(job.get("pose"))
    fps = clamp(float(job.get("fps") or 12), 4, 24)
    source = Path(job["source"])
    meta = ffprobe(source)
    if not meta["width"] or not meta["height"]:
        raise RuntimeError("this file has no video stream")
    start = clamp(float(job["start"]), 0, max(meta["duration"] - 0.1, 0))
    end = clamp(float(job["end"]), start + 0.1, meta["duration"] or start + 8)
    span = {"start": start, "end": end, "duration": end - start}
    job["range"] = span
    names = {name: folder / name for name in ("reference.mp4", "depth.mp4", "ai_depth.mp4", "edges.mp4", "lineart.mp4", "motion_mask.mp4",
                                              "normals_proxy.mp4", "animatic.mp4", "contact_sheet.jpg", "preview.jpg",
                                              "pose_high_contrast.mp4", "openpose_pose.mp4", "combined_reference_depth_pose.mp4")}
    landscape = meta["width"] >= meta["height"]
    hd = job.get("resolution") == "720p"
    # the original's sizes (long edge 1280 for the reference, 960 for the control passes; or short edge 720), except
    # that a smaller source is not enlarged
    even = lambda edge, side: f"trunc(min({edge}\\,{side})/2)*2"
    reference_scale = ((f"scale=-2:{even(720, 'ih')}" if landscape else f"scale={even(720, 'iw')}:-2") if hd
                       else (f"scale={even(1280, 'iw')}:-2" if landscape else f"scale=-2:{even(1280, 'ih')}"))
    control_scale = (("scale=-2:720" if landscape else "scale=720:-2") if hd else
                     (f"scale={even(960, 'iw')}:-2" if landscape else f"scale=-2:{even(960, 'ih')}"))

    def control(target: Path, filters: str, label: str, done: float, of: float, origin: Path | None = None) -> None:
        ffmpeg(["-i", str(origin or names["reference.mp4"]), "-vf", filters, "-r", f"{fps:g}", "-an", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "18", "-pix_fmt", "yuv420p", str(target)], span["duration"],
               lambda at: progress.step(done + at, of, label))

    # 1. prepare: the trimmed, normalised reference clip
    progress.start("prepare", "Trimming and normalising the reference clip")
    ffmpeg(["-ss", f"{start}", "-t", f"{span['duration']}", "-i", str(source), "-map", "0:v:0", "-map", "0:a?", "-vf", reference_scale,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(names["reference.mp4"])], span["duration"], lambda at: progress.step(at, span["duration"], "Trimming and normalising the reference clip"))
    ffmpeg(["-ss", "0", "-i", str(names["reference.mp4"]), "-frames:v", "1", "-q:v", "3", str(names["preview.jpg"])])
    reference = ffprobe(names["reference.mp4"])
    if not reference["duration"]:
        raise RuntimeError("the trimmed clip is empty: check the range")

    # 2. pose
    progress.start("pose", "Tracking pose")
    pose = None
    if "pose" not in layers:
        why = "The pose layer was not selected for this pack."
    elif not can["pose"]:
        why = "Pose is not available on this machine: " + ("no pose model was downloaded." if can["mediapipe"] and can["cv2"]
                                                             else "MediaPipe is not installed (oneshot-setup.sh --motion adds it).")
        progress.note(why)
    else:
        try:
            pose = analyze_pose(names["reference.mp4"], reference, fps, settings, progress)
        except Exception as error:  # noqa: BLE001 - the other layers do not depend on this one
            why = f"Pose did not run: {str(error)[:200]}"
            progress.note(why)
    pose_data = pose or empty_pose(reference, fps, settings, why)

    # 3. camera
    progress.start("camera", "Solving the subject-masked camera move")
    camera = None
    if "camera" in layers:
        if can["camera"]:
            camera = analyze_camera(names["reference.mp4"], reference, min(fps, 12), pose, progress)
            if not pose:
                progress.note("Camera solved without a pose track: corners were taken from the edges of the frame first.")
        else:
            progress.note("The camera solve is not available on this machine: OpenCV is not installed (oneshot-setup.sh --motion adds it).")

    # 4. depth: the ffmpeg proxy (always, as in the original), its normals, and Depth-Anything
    progress.start("depth", "Rendering depth")
    wants_ai = "ai-depth" in layers and settings["depthModel"] == "depth-anything"
    control(names["depth.mp4"], f"fps={fps:g},{control_scale},format=gray,eq=contrast=1.45:brightness=0.03,boxblur=2:1", "Rendering the depth proxy",
            0, span["duration"] * (2 if "normals" in layers else 1))
    if "normals" in layers:
        control(names["normals_proxy.mp4"], f"fps={fps:g},format=gray,sobel,eq=contrast=1.7", "Rendering the normals proxy", span["duration"],
                span["duration"] * 2, names["depth.mp4"])
    if wants_ai:
        if can["ai_depth"]:
            try:
                ai_depth(names["reference.mp4"], names["ai_depth.mp4"], reference, fps, progress)
            except Exception as error:  # noqa: BLE001 - the proxy stands in
                names["ai_depth.mp4"].unlink(missing_ok=True)
                progress.note(f"AI depth did not run, the ffmpeg proxy stands in: {str(error)[:200]}")
        else:
            progress.note("AI depth is not available on this machine (oneshot-setup.sh --motion adds Depth-Anything-small): the ffmpeg proxy stands in.")

    # 5. encode: skeleton videos, edges, line art, motion mask
    progress.start("encode", "Rendering the control layers")
    passes = [(key, name, filters) for key, name, filters in (
        ("edges", "edges.mp4", f"fps={fps:g},{control_scale},edgedetect=low=0.08:high=0.28,format=gray,eq=contrast=1.35"),
        ("lineart", "lineart.mp4", f"fps={fps:g},{control_scale},format=gray,sobel,negate,eq=contrast=1.2"),
        ("masks", "motion_mask.mp4", f"fps={fps:g},{control_scale},tblend=all_mode=difference,format=gray,eq=contrast=3.2:brightness=0.02,boxblur=1:1"))
        if key in layers]
    count = len(pose["frames"]) if pose else 0
    of = len(passes) * span["duration"] + 2 * count + span["duration"]
    done = 0.0
    for key, name, filters in passes:
        control(names[name], filters, f"Rendering {name}", done, of)
        done += span["duration"]
    keypoints = None
    if pose:
        width, height = reference["width"], reference["height"]
        for name, draw, label in (("openpose_pose.mp4", draw_openpose, "Rendering OpenPose skeleton"),
                                  ("pose_high_contrast.mp4", draw_pose, "Rendering pose video")):
            encoder = Encoder(names[name], width, height, fps)
            try:
                for index, frame in enumerate(pose["frames"]):
                    encoder.write(draw(frame, width, height))
                    progress.step(done + index + 1, of, f"{label} {index + 1}/{count}")
            finally:
                encoder.close()
            done += count
        keypoints = openpose_json(pose, width, height)
        best_depth = names["ai_depth.mp4"] if names["ai_depth.mp4"].is_file() else names["depth.mp4"]
        try:  # the review plate: reference, depth and pose side by side
            ffmpeg(["-i", str(names["reference.mp4"]), "-i", str(best_depth), "-i", str(names["pose_high_contrast.mp4"]), "-filter_complex",
                    "[0:v]scale=640:-2,setpts=PTS-STARTPTS[a];[1:v]scale=640:-2,setpts=PTS-STARTPTS[b];[2:v]scale=640:-2,setpts=PTS-STARTPTS[c];"
                    f"[a][b][c]hstack=inputs=3,fps={fps:g}[v]", "-map", "[v]", "-t", f"{pose['duration']}", "-r", f"{fps:g}", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(names["combined_reference_depth_pose.mp4"])], span["duration"],
                   lambda at: progress.step(done + at, of, "Rendering the review plate"))
        except RuntimeError as error:
            progress.note(f"The review plate was not made: {str(error)[-120:]}")

    # 6. pack: contact sheet, animatic, the JSON files, the prompt pack, the manifest, the ZIP
    progress.start("pack", "Writing the production pack")
    ffmpeg(["-i", str(names["reference.mp4"]), "-vf", "fps=1,scale=240:-2,tile=5x2", "-frames:v", "1", str(names["contact_sheet.jpg"])])
    progress.step(1, 4, "Contact sheet written")
    ffmpeg(["-i", str(names["reference.mp4"]), "-vf", "fps=2,scale=960:-2,setpts=1.4*PTS", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf",
            "20", "-pix_fmt", "yuv420p", str(names["animatic.mp4"])])
    progress.step(2, 4, "Animatic written")
    (folder / "pose_landmarks.json").write_text(json.dumps(pose_data, indent=2))
    if camera:
        (folder / "camera_motion.json").write_text(json.dumps(camera, indent=2))
    if keypoints is not None:
        (folder / "openpose_keypoints.json").write_text(json.dumps(keypoints, indent=2))
    said = describe_camera(camera)
    quality = quality_report(pose_data, camera, len(layers))
    planning = planning_data(job, span["duration"], quality, settings, bool(camera))
    write_texts(folder, job, planning, pose_data, said)
    created = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    (folder / "analysis_manifest.json").write_text(json.dumps({
        "schemaVersion": 1, "analysisId": job["id"], "createdAt": created, "sourceName": job["source_name"], "range": span, "sampleFps": fps,
        "outputDir": ".", "frameSize": {"width": reference["width"], "height": reference["height"]}, "status": "prepared",
        "files": {key: name for key, name in (("reference", "reference.mp4"), ("depth", "depth.mp4"), ("edges", "edges.mp4"),
                                              ("lineart", "lineart.mp4"), ("motionMask", "motion_mask.mp4"), ("normals", "normals_proxy.mp4"),
                                              ("animatic", "animatic.mp4"), ("contactSheet", "contact_sheet.jpg"), ("preview", "preview.jpg"))
                  if (folder / name).is_file()}}, indent=2))
    progress.step(3, 4, "Manifests written")
    progress.finish()
    manifest = {"schemaVersion": 1, "app": PORT_NAME, "version": job.get("studio_version", ""), "createdAt": created,
                "sourceName": job["source_name"], "range": span, "sampleFps": fps, "frameCount": len(pose_data["frames"]),
                "cameraMoveFrames": len(camera["frames"]) if camera else 0, "analysisSettings": settings,
                "poseDiagnostics": pose_data["summary"]["diagnostics"], "planning": planning,
                "files": {key: (name if name and (folder / name).is_file() else None) for key, name in BUNDLE_FILES.items()},
                # everything below is this port's own: the original's manifest ends at "files"
                "port": {"of": SOURCE, "modified": True, "cameraDescription": said, "notes": progress.notes,
                         "frameSize": {"width": reference["width"], "height": reference["height"]},
                         "frameRate": reference["frameRate"], "duration": reference["duration"],
                         "available": {key: can[key] for key in ("pose", "camera", "ai_depth")},
                         "packages": {key: can[key] for key in ("numpy", "cv2", "mediapipe", "onnxruntime")},
                         "timings": progress.timings, "peakMemoryMB": peak_memory_mb()}}
    (folder / "bundle_manifest.json").write_text(json.dumps(manifest, indent=2))
    zip_folder(folder, folder.with_suffix(".zip"))
    emit(finished=True, timings=progress.timings, memory=manifest["port"]["peakMemoryMB"], notes=progress.notes)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--probe":
        print(json.dumps(probe()))
        sys.exit(0)
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    try:
        run(json.loads(Path(sys.argv[1]).read_text()))
    except Exception as error:  # noqa: BLE001 - the app shows the reason
        emit(failed=str(error)[-600:])
        sys.exit(1)
