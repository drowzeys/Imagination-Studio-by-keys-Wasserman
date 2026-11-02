"""HDR finish: a finished clip graded and written as real HDR, BT.2020 / HLG in 10-bit HEVC, with an SDR copy beside it.

    job kind "hlg"     params {video, strength?: 0.1..1 (1), sdr?: true}

The clip's frames become HLG code values (ARIB STD-B67, ITU-R BT.2100, SDR white near code 0.75), a tone curve moves
their brightness onto ``TARGET``, split toning tints what has little colour of its own, then a chroma push and a little
micro contrast. The curve is fitted once from a sample of the whole clip, so that it cannot flicker from frame to frame.
Finish a whole cut as one clip: a curve for each shot of a film would jump at the cuts.

A clip from the video model is 8-bit SDR, so this grade makes the highlights and the range of the HDR file: it finds no
detail that the clip does not hold. ``TARGET`` and ``BANDS`` were set by eye on one film against the figures measured
from one reference clip (the owner's pick, 2026-11-01; a bright, very contrasty look), and are a look, not a standard.

CPU and ffmpeg only, with no model and no GPU lock. The job's runner is this file run as a script
(``python3 app/hlg.py run ...``): it prints ``[tensorfold] step n/m`` lines. numpy only: the Studio's venv has no OpenCV.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

KINDS = ("hlg",)
A, B, C = 0.17883277, 0.28466892, 0.55991073  # the HLG curve's constants (ITU-R BT.2100)
M709_2020 = np.array([[0.6274, 0.3293, 0.0433], [0.0691, 0.9195, 0.0114], [0.0164, 0.0880, 0.8956]], np.float32)  # BT.2087
PCT = [0.5, 1, 5, 10, 25, 50, 75, 90, 95, 99, 99.9, 100]
TARGET = np.array([0.006, 0.012, 0.035, 0.060, 0.150, 0.330, 0.560, 0.740, 0.830, 0.930, 0.985, 1.0], np.float32)
TONE, CHROMA, SHARP = 0.34, 0.42, 0.5  # at strength 1: split tone, chroma added, micro contrast
BANDS = [(0.26, 0.11, (0.19, 0.20, 0.62)),  # lower mids: cobalt
         (0.42, 0.10, (0.25, 0.26, 0.66)),  # mids: cobalt
         (0.62, 0.09, (0.76, 0.42, 0.62))]  # upper mids: salmon magenta
HDR_SHOWN = ("arib-std-b67", "smpte2084")  # a clip already in one of these is not graded again
TIMEOUT = 20  # seconds for one question to ffprobe or ffmpeg
SIGMA = 2.0
_RADIUS = 8
_KERNEL = np.exp(-0.5 * (np.arange(-_RADIUS, _RADIUS + 1) / SIGMA) ** 2).astype(np.float32)
_KERNEL /= _KERNEL.sum()
_GRID = np.linspace(0, 1, 1025, dtype=np.float32)


def to_codes(rgb: np.ndarray) -> np.ndarray:
    """8-bit BT.709 RGB (a clip from the video model) to BT.2020 HLG code values, SDR white at about 0.75."""

    x = rgb.astype(np.float32) / 255.0
    linear = np.clip(np.power(x, 2.4) @ M709_2020.T, 0, None)
    scene = np.power(linear * 0.203, 1 / 1.2)
    return np.where(scene <= 1 / 12, np.sqrt(3 * scene), A * np.log(np.maximum(12 * scene - B, 1e-6)) + C).astype(np.float32)


def fit_curve(frames: list[np.ndarray], strength: float = 1.0) -> np.ndarray:
    """The clip's tone curve, a table over ``_GRID``, from a sample of its frames (RGB)."""

    levels = np.concatenate([to_codes(f).mean(2).ravel() for f in frames])
    source = np.maximum.accumulate(np.percentile(levels, PCT).astype(np.float32)) + np.arange(len(PCT)) * 1e-4
    source[-1] = max(source[-1], 1.0)
    return (1 - strength) * _GRID + strength * np.interp(_GRID, source, TARGET)


def _blur(channel: np.ndarray) -> np.ndarray:
    """A Gaussian blur of one channel, mirrored at the edges without repeating the edge row."""

    out = channel
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (_RADIUS, _RADIUS)
        padded = np.pad(out, pad, mode="reflect")
        total = np.zeros_like(out)
        for i, weight in enumerate(_KERNEL):
            total += weight * (padded[i:i - 2 * _RADIUS or None] if axis == 0 else padded[:, i:i - 2 * _RADIUS or None])
        out = total
    return out


def grade(rgb: np.ndarray, curve: np.ndarray, strength: float = 1.0, pool: ThreadPoolExecutor | None = None) -> np.ndarray:
    """One 8-bit RGB frame to graded HLG code values, RGB float32, 0 to 1."""

    codes = to_codes(rgb)
    level = codes.mean(2)
    new = np.interp(level, _GRID, curve).astype(np.float32)
    codes = codes * (new / np.maximum(level, 1e-3))[..., None]
    level = new
    # only what has little colour of its own takes the tint: yellow cabs and neon keep their colour
    grey = 1 - np.clip((codes.max(2) - codes.min(2)) / np.maximum(level, 0.05) / 0.55, 0, 1)
    for centre, width, tint in BANDS:
        weight = (np.exp(-((level - centre) / width) ** 2) * grey)[..., None] * TONE * strength
        tint = np.array(tint, np.float32)
        codes = codes * (1 - weight) + weight * (tint / tint.mean())[None, None] * level[..., None]
    mean = codes.mean(2, keepdims=True)
    codes = mean + (codes - mean) * (1 + CHROMA * strength)
    channels = [codes[..., i] for i in range(3)]
    blurred = np.stack(list(pool.map(_blur, channels) if pool else map(_blur, channels)), 2)
    return np.clip(codes + (codes - blurred) * SHARP * strength, 0, 1)


def to_sdr(codes: np.ndarray) -> np.ndarray:
    """The SDR copy: the HLG codes shown nearly directly, which keeps the look on an ordinary screen."""

    return (np.power(np.clip(codes / 0.90, 0, 1), 1.12) * 255 + 0.5).astype(np.uint8)


def probe(path: Path) -> dict:
    """{width, height, rate, frames, transfer, matrix} of a clip's picture, from ffprobe."""

    done = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=width,height,avg_frame_rate,nb_frames,duration,color_transfer,color_space",
                           "-of", "json", str(path)], capture_output=True, text=True, timeout=TIMEOUT)
    streams = json.loads(done.stdout or "{}").get("streams") or []
    if done.returncode or not streams:
        raise ValueError(f"cannot read a picture from {path.name}")
    one = streams[0]
    top, _, under = str(one.get("avg_frame_rate") or "24/1").partition("/")
    seconds = float(one.get("duration") or 0)
    rate = (float(top) / float(under or 1)) if float(under or 1) else 24.0
    return {"width": int(one["width"]), "height": int(one["height"]), "rate": str(one.get("avg_frame_rate") or "24/1"),
            "frames": int(one.get("nb_frames") or 0) or int(round(seconds * rate)),
            "transfer": one.get("color_transfer") or "", "matrix": one.get("color_space") or ""}


def refusal(path: Path) -> str:
    """Why this clip cannot be finished ("" when it can): said before the job is queued."""

    try:
        found = probe(path)
    except (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        return str(error) or f"cannot read {path.name}"
    if found["transfer"] in HDR_SHOWN:
        return f"{path.name} is already HDR ({found['transfer']}): the finish starts from an SDR clip"
    return ""


def _picture(info: dict) -> str:
    """ffmpeg filters that read the clip as BT.709 (as a clip with no matrix of its own is) and make its size even."""

    matrix = "" if info["matrix"] not in ("", "unknown") else "in_color_matrix=bt709:"
    return f"scale=trunc(iw/2)*2:trunc(ih/2)*2:{matrix}flags=lanczos,format=rgb24"


def _frames(path: Path, info: dict, filters: str, width: int, height: int):
    """Frames as RGB arrays, through ffmpeg."""

    reader = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-vf", filters, "-fps_mode", "passthrough",
                               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    size = width * height * 3
    try:
        while True:
            data = reader.stdout.read(size)
            if len(data) < size:
                break
            yield np.frombuffer(data, np.uint8).reshape(height, width, 3)
    finally:
        reader.stdout.close()
        reader.kill()
        reader.wait()


def _sample(path: Path, info: dict) -> list[np.ndarray]:
    """About every 20th frame, small (never fewer than six on a short clip): what the curve is fitted from."""

    step = max(1, min(20, info["frames"] // 6))
    width, height = 480, 270
    return list(_frames(path, info, f"select='not(mod(n\\,{step}))',scale={width}:{height}:flags=area,format=rgb24", width, height))


def writer_hdr(out: Path, info: dict, width: int, height: int) -> subprocess.Popen:
    return subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", f"{width}x{height}", "-r", info["rate"], "-i", "-",
                             "-vf", "scale=out_color_matrix=bt2020:out_range=tv:flags=lanczos,format=yuv420p10le", "-c:v", "libx265", "-preset", "medium", "-crf", "16", "-tag:v", "hvc1",
                             "-x265-params", "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:range=limited:log-level=error",
                             "-colorspace", "bt2020nc", "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-color_range", "tv", str(out)], stdin=subprocess.PIPE)


def writer_sdr(out: Path, info: dict, width: int, height: int) -> subprocess.Popen:
    return subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", info["rate"], "-i", "-",
                             "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                             "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv", str(out)], stdin=subprocess.PIPE)


def _with_sound(picture: Path, source: Path, out: Path) -> None:
    """The clip's own sound, unchanged, beside the graded picture."""

    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(picture), "-i", str(source), "-map", "0:v:0", "-map", "1:a?", "-c", "copy",
                    "-movflags", "+faststart", str(out)], check=True)
    picture.unlink()


def finish(source: Path, out: Path, sdr: Path | None, strength: float = 1.0, say=print) -> dict:
    """Writes ``out`` (HLG) and ``sdr``; returns the master's brightness figures."""

    info = probe(source)
    width, height = info["width"] & ~1, info["height"] & ~1
    curve = fit_curve(_sample(source, info), strength)
    out.parent.mkdir(parents=True, exist_ok=True)
    picture = out.with_name(out.stem + ".picture.mp4")
    plain = out.with_name(out.stem + ".sdr-picture.mp4")
    hdr_writer = writer_hdr(picture, info, width, height)
    sdr_writer = writer_sdr(plain, info, width, height) if sdr else None
    done, seen = 0, []
    with ThreadPoolExecutor(3) as pool:
        for frame in _frames(source, info, _picture(info), width, height):
            codes = grade(frame, curve, strength, pool)
            hdr_writer.stdin.write((codes * 65535 + 0.5).astype(np.uint16).tobytes())
            if sdr_writer:
                sdr_writer.stdin.write(to_sdr(codes).tobytes())
            if done % 12 == 0:
                seen.append(np.percentile(codes.mean(2)[::4, ::4], [1, 50, 99]))
            done += 1
            if done % 6 == 0:
                say(f"[tensorfold] step {done}/{max(done, info['frames'])}")
    for writer in filter(None, (hdr_writer, sdr_writer)):
        writer.stdin.close()
        if writer.wait():
            raise RuntimeError("ffmpeg could not write the finish")
    if not done:
        raise RuntimeError(f"no frames read from {source.name}")
    _with_sound(picture, source, out)
    if sdr:
        _with_sound(plain, source, sdr)
    figures = np.mean(seen, 0).round(3).tolist()
    say(f"[tensorfold] hlg: {done} frames, {width}x{height}, code values p1 {figures[0]} p50 {figures[1]} p99 {figures[2]}")
    return {"frames": done, "p1": figures[0], "p50": figures[1], "p99": figures[2]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HDR finish of one clip")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    parser.add_argument("--sdr", type=Path)
    parser.add_argument("--strength", type=float, default=1.0)
    args = parser.parse_args()
    finish(args.video, args.output, args.sdr, args.strength, say=lambda line: print(line, flush=True))
    sys.exit(0)
