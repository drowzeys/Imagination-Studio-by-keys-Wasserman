"""A long video's joinery (issue: longer videos are chains of clips): the last frame a part hands to the next one, and
the join of the parts into one file. Used by the queue (server.long_take) and by the Windows long-video Feature check
(windows_engine_checks.py), so the check runs what a job runs. ffmpeg only: no model, no GPU."""
from __future__ import annotations

from pathlib import Path
import subprocess


class JoinFailed(RuntimeError):
    """ffmpeg refused a part or the join; ``log`` is what it said."""

    def __init__(self, message: str, log: str = ""):
        super().__init__(message)
        self.log = log


def last_frame(video: Path, image: Path, env: dict | None = None) -> None:
    """The clip's last frame. No -vsync/-fps_mode: ffmpeg 7+ dropped -vsync and 4.x lacks -fps_mode, and with -update
    each decoded frame overwrites the image, so the last one written is the last frame either way."""

    subprocess.run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.2", "-i", str(video), "-update", "1",
                    "-q:v", "1", str(image)], check=True, env=env, timeout=120)


def join(clips: list[Path], final: Path, total: float, fps: int, folder: Path, env: dict | None = None) -> None:
    """``clips`` one after another as ``final``, cut to ``total`` seconds. Every part after the first repeats the frame
    it started from, so that frame (and its sound) is dropped."""

    listing = folder / "parts.txt"
    trimmed = []
    try:
        for index, clip in enumerate(clips):
            part = folder / f"join{index + 1:03d}.ts"
            cut = ["-vf", "trim=start_frame=1,setpts=PTS-STARTPTS", "-af", f"atrim=start={1 / fps},asetpts=PTS-STARTPTS"] if index else []
            trimmed.append(part)
            done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), *cut, "-c:v", "libx264", "-crf", "17",
                                   "-preset", "medium", "-pix_fmt", "yuv420p", "-r", str(fps), "-c:a", "aac", "-b:a",
                                   "256k", "-ar", "48000", "-f", "mpegts", str(part)], capture_output=True, text=True, env=env)
            if done.returncode:
                raise JoinFailed("ffmpeg failed while preparing a part", done.stderr)
        listing.write_text("".join(f"file '{part}'\n" for part in trimmed))
        done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-t",
                               str(total), "-c", "copy", "-movflags", "+faststart", str(final)], capture_output=True,
                              text=True, env=env)
        if done.returncode:
            raise JoinFailed("ffmpeg failed while joining the parts", done.stderr)
    finally:
        for part in trimmed:
            part.unlink(missing_ok=True)
