"""The HDR finish (app/hlg.py): its numbers, its refusals, and one real small clip through ffmpeg.
Run: python3 -m pytest tests/test_hlg.py"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import actions  # noqa: E402
import hlg  # noqa: E402
from fastapi import HTTPException  # noqa: E402

PACK = Path(__file__).resolve().parent.parent
TOOLS = shutil.which("ffmpeg") and shutil.which("ffprobe")


def has_x265():
    return TOOLS and "libx265" in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=20).stdout


def clip(path, extra=(), seconds=1):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s=128x72:r=24:d={seconds}", "-f", "lavfi", "-i", f"sine=d={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", *extra, str(path)], check=True, timeout=60)


def studio():
    home = Path(tempfile.mkdtemp()) / "TensorFoldStudio"
    for name in ("renders", "uploads", "loras"):
        (home / name).mkdir(parents=True)

    def inside(path):
        resolved = (home / path).resolve()
        if home.resolve() not in (resolved, *resolved.parents):
            raise HTTPException(400, "path is outside the studio folder")
        return resolved

    router, build, title, source = actions.attach(home, PACK, True, inside, lambda p: str(Path(p).resolve().relative_to(home.resolve())))
    return home, build, next(r.endpoint for r in router.routes if r.path == "/api/actions")


def test_hlg_curve_meets_the_standard_at_its_join_and_at_top():
    # BT.2100: scene light 1/12 gives code 0.5 from both branches, and scene light 1 gives code 1 (the log branch)
    assert np.sqrt(3 / 12) == pytest.approx(0.5)
    assert hlg.A * np.log(12 - hlg.B) + hlg.C == pytest.approx(1.0, abs=1e-4)
    assert hlg.A * np.log(12 * (1 / 12) - hlg.B) + hlg.C == pytest.approx(0.5, abs=1e-3)


def test_black_stays_black_and_codes_rise_with_the_picture():
    ramp = np.repeat(np.arange(256, dtype=np.uint8)[None, :, None], 3, 2)
    codes = hlg.to_codes(ramp)[0]
    assert codes[0].max() == pytest.approx(0, abs=1e-5)
    assert np.all(np.diff(codes.mean(1)) > 0)
    assert 0.70 < codes[255].mean() < 0.80  # SDR white sits near code 0.75


def test_the_curve_only_rises_and_strength_zero_would_change_nothing():
    frame = np.random.default_rng(1).integers(0, 256, (54, 96, 3), dtype=np.uint8)
    curve = hlg.fit_curve([frame])
    assert np.all(np.diff(curve) >= 0)
    assert curve[0] == pytest.approx(hlg.TARGET[0], abs=0.02) and curve[-1] == pytest.approx(1, abs=1e-5)
    assert np.allclose(hlg.fit_curve([frame], 0.0), hlg._GRID)


def test_grade_makes_a_brighter_top_a_darker_bottom_and_more_colour():
    rng = np.random.default_rng(2)
    frame = (np.clip(rng.normal(120, 45, (90, 160, 3)), 0, 255)).astype(np.uint8)
    frame[..., 0] = np.clip(frame[..., 0] * 1.2, 0, 255)
    before = hlg.to_codes(frame)
    after = hlg.grade(frame, hlg.fit_curve([frame]))
    assert after.dtype == np.float32 and after.min() >= 0 and after.max() <= 1
    assert np.percentile(after.mean(2), 99) > np.percentile(before.mean(2), 99)
    assert np.percentile(after.mean(2), 5) < np.percentile(before.mean(2), 5)
    chroma = lambda e: (e.max(2) - e.min(2)).mean()
    assert chroma(after) > chroma(before)


def test_blur_keeps_a_flat_picture_flat_and_its_size():
    flat = np.full((40, 60), 0.5, np.float32)
    out = hlg._blur(flat)
    assert out.shape == flat.shape and np.allclose(out, 0.5, atol=1e-5)


def test_action_is_listed_and_needs_no_lora():
    _, _, state = studio()
    card = state()["hlg"]
    assert card["title"] == "keys-HDR LORA" and card["lora"] is None and card["lora_name"] is None
    assert actions.LORA["hlg"] is None and "hlg" in actions.KINDS


@pytest.mark.skipif(not has_x265(), reason="needs ffmpeg with libx265")
def test_builder_refuses_what_it_cannot_finish():
    home, build, _ = studio()
    with pytest.raises(HTTPException):
        build("hlg", {}, "j")
    with pytest.raises(HTTPException):
        build("hlg", {"video": "../outside.mp4"}, "j")
    clip(home / "renders/a.mp4")
    for bad in (0, 2, -1):
        with pytest.raises(HTTPException):
            build("hlg", {"video": "renders/a.mp4", "strength": bad if bad else 0.05}, "j")
    hdr = home / "renders/h.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(home / "renders/a.mp4"), "-c:v", "libx265", "-x265-params", "log-level=error:transfer=arib-std-b67:colorprim=bt2020:colormatrix=bt2020nc",
                    "-color_trc", "arib-std-b67", "-color_primaries", "bt2020", "-colorspace", "bt2020nc", "-c:a", "copy", str(hdr)], check=True, timeout=60)
    with pytest.raises(HTTPException) as error:
        build("hlg", {"video": "renders/h.mp4"}, "j")
    assert "already HDR" in error.value.detail


@pytest.mark.skipif(not has_x265(), reason="needs ffmpeg with libx265")
def test_a_real_clip_becomes_tagged_ten_bit_hlg_with_its_sound_and_an_sdr_copy():
    home, build, _ = studio()
    clip(home / "renders/a.mp4")
    command, env, outputs = build("hlg", {"video": "renders/a.mp4"}, "job1")
    assert len(outputs) == 2
    done = subprocess.run(command, capture_output=True, text=True, timeout=120, env={**os.environ, **env})
    assert done.returncode == 0, done.stderr
    assert "[tensorfold] step " in done.stdout and "[tensorfold] hlg:" in done.stdout
    def streams(path):
        return json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", path], capture_output=True, text=True, timeout=20).stdout)["streams"]
    master = streams(outputs[0])
    picture = next(s for s in master if s["codec_type"] == "video")
    assert picture["codec_name"] == "hevc" and picture["pix_fmt"] == "yuv420p10le"
    assert picture["color_transfer"] == "arib-std-b67" and picture["color_primaries"] == "bt2020" and picture["color_space"] == "bt2020nc"
    assert (picture["width"], picture["height"]) == (128, 72) and int(picture["nb_frames"]) == 24
    assert any(s["codec_type"] == "audio" for s in master)
    plain = next(s for s in streams(outputs[1]) if s["codec_type"] == "video")
    assert plain["pix_fmt"] == "yuv420p" and plain["color_space"] == "bt709" and int(plain["nb_frames"]) == 24
    assert not list((home / "renders").glob("*picture*"))
    command, _, outputs = build("hlg", {"video": "renders/a.mp4", "sdr": False, "strength": 0.5}, "job2")
    assert len(outputs) == 1 and "--sdr" not in command


def test_odd_sized_clip_is_made_even():
    if not has_x265():
        pytest.skip("needs ffmpeg with libx265")
    home, build, _ = studio()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=129x73:r=24:d=0.5", "-c:v", "libx264", "-pix_fmt", "yuv444p", str(home / "renders/o.mp4")], check=True, timeout=60)
    command, env, outputs = build("hlg", {"video": "renders/o.mp4"}, "odd")
    assert subprocess.run(command, capture_output=True, text=True, timeout=120).returncode == 0
    size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", outputs[0]], capture_output=True, text=True, timeout=20).stdout.strip()
    assert size == "128,72"
