"""Qualify bounded, model-free Windows routes without activating the full native CUDA tier."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import actions
import finish
import graphics
import windows_features as features
import windows_media
from windows_process import OwnedJob


def run(command: list[str]) -> tuple[str, dict]:
    owned = OwnedJob(job_commit_mb=features.COMMIT_MB)
    started = time.monotonic()
    try:
        with features.watch(owned):
            process = owned.spawn(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
            try:
                output, _ = process.communicate(timeout=300)
            except subprocess.TimeoutExpired:
                owned.terminate()
                process.communicate(timeout=10)
                raise RuntimeError("The owned feature check exceeded five minutes.") from None
            if process.returncode:
                raise RuntimeError(f"Feature check exited {process.returncode}:\n{output[-4000:]}")
        return output, {"elapsed_s": round(time.monotonic() - started, 2),
                        "peak_job_commit_mb": round(owned.commitment()["peak_job_commit_bytes"] / 2 ** 20, 1)}
    finally:
        owned.close()


def inspect(path: Path) -> dict:
    output, _ = run(["ffprobe", "-v", "error", "-show_entries",
                     "format=duration:stream=codec_type,width,height,avg_frame_rate,pix_fmt,color_transfer,color_space,color_primaries",
                     "-of", "json", str(path)])
    return json.loads(output)


def validate(path: Path, seconds: float, audio: bool, hdr: bool = False) -> dict:
    info = graphics.probe(path)
    features.check_media(info)
    if (info["width"], info["height"]) != (1280, 720) or abs(info["seconds"] - seconds) > .12 or info["audio"] != audio:
        raise RuntimeError(f"{path.name}: size, duration or sound does not match the requested cut")
    picture = next(s for s in inspect(path)["streams"] if s["codec_type"] == "video")
    if hdr and (picture.get("color_transfer"), picture.get("color_space"), picture.get("color_primaries"), picture.get("pix_fmt")) != (
            "arib-std-b67", "bt2020nc", "bt2020", "yuv420p10le"):
        raise RuntimeError("The HDR file is not tagged as 10-bit BT.2020 / HLG.")
    run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-f", "null", "-"])
    return info


def audio_packets(path: Path) -> list:
    output, _ = run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_packets",
                     "-show_entries", "packet=data_hash", "-show_data_hash", "sha256", "-of", "json", str(path)])
    return json.loads(output)["packets"]


def check(kind: str) -> dict:
    if why := features.prerequisites(kind):
        raise RuntimeError(why)
    features.headroom(kind)
    before = features.fingerprint(kind)
    results = []
    with tempfile.TemporaryDirectory(prefix=f"studio-{kind}-check-") as scratch:
        home = Path(scratch)
        (home / "renders").mkdir()

        def inside(value):
            path = (home / value).resolve()
            if not path.is_relative_to(home):
                raise ValueError("the check's media must stay in its temporary folder")
            return path

        clips = []
        for index, sound in enumerate((True, False)):
            clip = home / f"clip{index}.mp4"
            command = ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                       f"testsrc2=s=1280x720:r=24:d=10"]
            if sound:
                command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=10",
                            "-af", "volume=0.1", "-c:a", "aac"]
            run(command + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(clip)])
            clips.append(clip)
        if kind == "hlg":
            _, build, _, _ = actions.attach(home, features.PACK, False, inside, str)
            for index, clip in enumerate(clips):
                features.check_media(graphics.probe(clip))
                command, _, outputs = build(kind, {"video": clip.name, "strength": 1 if index == 0 else .3}, f"check{index}")
                _, measured = run(command)
                for number, path in enumerate(outputs):
                    validate(Path(path), 10, index == 0, hdr=number == 0)
                    if audio_packets(Path(path)) != audio_packets(clip):
                        raise RuntimeError("The HDR finish changed or lost the source sound.")
                results.append({"case": "sound-full" if index == 0 else "silent-light", **measured})
        else:
            finish.attach(home, features.PACK, inside, str)
            song = home / "song.wav"
            run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(clips[0]), "-vn", "-t", "10",
                 "-c:a", "pcm_s16le", str(song)])
            params = {"clips": [{"path": clips[i % 2].name, "in": 1, "out": 1.5} for i in range(features.MAX_CLIPS)],
                      "song": song.name, "voice": song.name, "title": {"text": "Windows Finish", "subtitle": "Qualified local cut"},
                      "size": "720p", "fps": features.FPS}
            for mix in finish.MIXES:
                command, _, outputs = finish.build({**params, "mix": mix}, f"check-{mix}", local_only=True)
                _, measured = run(command)
                validate(Path(outputs[0]), 9.5, True)
                if not all(Path(p).is_file() and Path(p).stat().st_size for p in outputs):
                    raise RuntimeError("Finish did not write all MP4, Tesseract, FCPXML and EDL outputs.")
                ET.parse(outputs[2])
                results.append({"case": mix, **measured})
            command, _, outputs = finish.build({"clips": [{"path": clips[1].name}], "size": "720p", "fps": features.FPS},
                                               "silent", local_only=True)
            _, measured = run(command)
            validate(Path(outputs[0]), 10, False)
            results.append({"case": "silent", **measured})
    if features.fingerprint(kind) != before:
        raise RuntimeError("The runtime changed during qualification; repeat the check.")
    receipt = {"ok": True, "feature": kind, "checked_at": datetime.now(timezone.utc).isoformat(),
               "fingerprint": before, "results": results}
    features.save_pass(kind, receipt)
    return {"ok": True, "feature": kind, "results": results, "scope": before["scope"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature", choices=(*features.KINDS, "all"), default="all")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("this qualification requires native Windows")
    os.environ["PATH"] = windows_media.search_path(os.environ.get("PATH", ""))
    failed = False
    results = []
    for kind in features.KINDS if args.feature == "all" else (args.feature,):
        started = datetime.now(timezone.utc).isoformat()
        try:
            result = check(kind)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            failed = True
            result = {"ok": False, "feature": kind, "error": str(error)}
        features.save_attempt(kind, {**result, "started": started, "finished": datetime.now(timezone.utc).isoformat()})
        results.append(result)
        print(json.dumps(result), flush=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"ok": not failed, "checks": results}, indent=2), encoding="utf-8")
    raise SystemExit(1 if failed else 0)
