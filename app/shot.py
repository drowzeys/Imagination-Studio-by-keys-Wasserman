"""Whether a clip is the one shot it was asked for: its keyframes held, and no cut inside it.

Two things FastH3 did in a production run of 32 first-and-last-frame clips (1280x736, 8 and 10 seconds, 8 passes on
TensorFold's CUDA engine), neither of which the prompt asked for:

- A first keyframe not held. In ComfyUI's node and in the Mac engine alike a keyframe is a row of the sequence the
  model reads beside the frames, not a frame written into the clip: the model reproduces it at its moment, and
  almost always does. One clip of the 32 opened on another picture (a close-up laid over the keyframe), so the join
  with the clip before it jumped. Rendered again on another Spark with the same seed and prompt, every frame came
  out the same (a difference of 0.00 over all 243), the missed keyframe with them; and on ComfyUI's own bfloat16
  blocks in place of TensorFold's int8 engine the clip was another one that missed its first frame the same way
  (70.9 against 74.4). It is what FastH3 does with that seed and prompt, not the engine and not a slip of one render.
- A hard cut inside the clip, to a close-up that is in neither keyframe (9 of the 32 clips, the three dining-hall
  ones among them, 12 cuts in all).

What is not seen here: the same move to a close-up made by a dissolve through a double exposure, without a cut
(3 of 8 renders of one such clip did that). Half a second of it measures like half a second of fast movement in the
run's honest clips, so there is nothing to set a threshold between.

Both are measured here on small grey copies of the frames (160 pixels wide), and reported beside the clip like the QA
gate's verdict; server.py renders the clip once more at the next seed when the job asks for that. Nothing here fails
a clip that was made, and a machine that cannot read the frames reports the check as not run.

The thresholds are set from that run's clips and nothing else; read them as that, not as a law.

Keyframes (frame 0 against the first picture, the last frame against the last): of 64 comparisons 63 held, with a
mean difference of 1.4 to 3.9 grey levels (of 255) and a correlation of 0.996 or more; the one that did not: 74.4
and 0.25. HELD_DIFFERENCE is three times the worst held figure, a sixth of the missed one. One missed keyframe is
all the run had, so the gap between 3.9 and 74.4 is not explored.

Cuts (each frame against the one before; 7081 pairs): the 11 clean cuts have a correlation of 0.48 or less (9 of
them under 0.2) and a change 4.2 to 52 times the median change of the twelve frames around them. The twelfth, a jump
out of a double exposure, has 0.66 and 2.4. Among the 7069 other pairs, fast action reaches a correlation of 0.63
but at 1.1 times its neighbours (the whole stretch moves), and a whip into a cut reaches 3.5 times its neighbours
at a correlation of 0.74. So a cut is a correlation under CUT_LIKENESS together with a change of CUT_RATIO times
the neighbours' or more: halfway between the weakest cut and the nearest other pair on each axis. A cross-fade is not
a cut and is not flagged: its frames change no faster than the frames around them.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

WIDTH = 160  # the grey copies: 160x92 for a 1280x736 clip
FPS = 24
HELD_DIFFERENCE = 12.0  # mean absolute difference, grey levels of 255: over this the keyframe was not held
HELD_LIKENESS = 0.90    # correlation: under this it was not held either
CUT_LIKENESS = 0.70     # correlation of a frame with the one before
CUT_RATIO = 1.75        # its change against the median change of the frames around it
CUT_WINDOW = 6          # frames on each side
CUT_FLOOR = 0.5         # grey levels: a still stretch does not make a small change a large ratio
MARK = "[Shot 2]"       # a prompt that writes a second shot asks for a cut


def one_shot(prompt: str) -> bool:
    return MARK not in (prompt or "")


def grey(image, size: tuple[int, int]):
    import numpy as np
    from PIL import Image

    return np.asarray(image.convert("L").resize(size, Image.Resampling.BILINEAR), np.float32)


def frames(clip: Path):
    """(grey frames [n, h, w] as float32, (width, height) of the clip). PyAV when the environment has it (both
    builds' do), else ffmpeg; LookupError when this machine has neither."""

    import numpy as np

    try:
        import av
    except ImportError:
        av = None
    if av is not None:
        out, size = [], None
        with av.open(str(clip)) as container:
            for frame in container.decode(video=0):
                if size is None:
                    size = (frame.width, frame.height)
                    small = (WIDTH, max(2, round(WIDTH * frame.height / frame.width)))
                out.append(grey(frame.to_image(), small))
        if not out:
            raise ValueError(f"no frames in {clip.name}")
        return np.stack(out), size
    tool = shutil.which("ffmpeg") or shutil.which("ffmpeg", path="/opt/homebrew/bin:/usr/local/bin")
    probe = shutil.which("ffprobe") or shutil.which("ffprobe", path="/opt/homebrew/bin:/usr/local/bin")
    if not tool or not probe:
        raise LookupError("neither PyAV nor ffmpeg is on this machine")
    found = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of",
                            "csv=p=0", str(clip)], capture_output=True, text=True).stdout.strip().split(",")
    size = (int(found[0]), int(found[1]))
    small = (WIDTH, max(2, round(WIDTH * size[1] / size[0])))
    raw = subprocess.run([tool, "-v", "error", "-i", str(clip), "-vf", f"scale={small[0]}:{small[1]}:flags=bilinear", "-pix_fmt",
                          "rgb24", "-f", "rawvideo", "-"], capture_output=True).stdout
    count = len(raw) // (small[0] * small[1] * 3)
    if not count:
        raise ValueError(f"no frames in {clip.name}")
    rgb = np.frombuffer(raw[:count * small[0] * small[1] * 3], np.uint8).reshape(count, small[1], small[0], 3).astype(np.float32)
    return rgb @ np.array([0.299, 0.587, 0.114], np.float32), size  # the same grey as PIL's


def likeness(a, b) -> tuple[float, float]:
    """(mean absolute difference in grey levels, correlation) of two grey pictures of one size."""

    import numpy as np

    one, two = a - a.mean(), b - b.mean()
    scale = float(np.sqrt((one * one).sum() * (two * two).sum()))
    return float(np.abs(a - b).mean()), float((one * two).sum() / scale) if scale > 1e-6 else 0.0


def fitted(picture: Path, canvas: tuple[int, int], clip: tuple[int, int], small: tuple[int, int], stretch: bool):
    """A keyframe as the renderer put it in the clip, as a grey copy: stretched onto the generation canvas (the
    first of a clip's keyframes, on both builds) or fitted by a centre crop (a last frame beside a first), then
    cropped as the clip was (the 720p preset generates 736 rows and keeps 720)."""

    from PIL import Image, ImageOps

    with Image.open(picture) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    wide, tall = canvas
    if stretch:
        image = image.resize((wide, tall), Image.Resampling.LANCZOS)
    else:
        image = ImageOps.fit(image, (wide, tall), Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    # the clip may be the canvas at another scale (the 2x decoder) with rows or columns cropped away
    scale = max(clip[0] / wide, clip[1] / tall)
    keep = (min(wide, round(clip[0] / scale)), min(tall, round(clip[1] / scale)))
    left, top = (wide - keep[0]) // 2, (tall - keep[1]) // 2
    return grey(image.crop((left, top, left + keep[0], top + keep[1])), small)


def held(frame, picture) -> dict:
    difference, same = likeness(frame, picture)
    return {"held": difference <= HELD_DIFFERENCE and same >= HELD_LIKENESS, "difference": round(difference, 1), "likeness": round(same, 3)}


def cuts(stack) -> list[dict]:
    """The hard cuts in a clip's grey frames: ``frame`` is the first frame of the new shot."""

    import numpy as np

    if len(stack) < 3:
        return []
    change = np.abs(stack[1:] - stack[:-1]).mean(axis=(1, 2))
    found = []
    for at in range(len(change)):
        around = np.concatenate([change[max(0, at - CUT_WINDOW):at], change[at + 1:at + 1 + CUT_WINDOW]])
        ratio = float(change[at] / max(float(np.median(around)), CUT_FLOOR))
        if ratio < CUT_RATIO:
            continue
        _, same = likeness(stack[at], stack[at + 1])
        if same < CUT_LIKENESS:
            found.append({"frame": at + 1, "seconds": round((at + 1) / FPS, 2), "likeness": round(same, 2), "ratio": round(ratio, 1)})
    return found


def window(count: int, found: list[dict] | None) -> dict:
    """The longest stretch of a clip without a cut, for an editor to trim to: ``start`` is its first frame, ``end``
    the frame after its last, ``frames`` their number, ``in`` and ``out`` the same in seconds (what the Editor's
    timeline takes), ``whole`` whether it is the whole clip. FastH3 opens some clips on an establishing shot nobody
    asked for and then cuts (4 of 36 in one production run, at frames 10 to 28): the window is then what follows.
    Of two stretches of one length the later is given, for the same reason."""

    marks = [0] + sorted({int(cut["frame"]) for cut in found or [] if 0 < int(cut["frame"]) < count}) + [count]
    start, end = max(zip(marks, marks[1:]), key=lambda span: (span[1] - span[0], span[0]))
    return {"start": start, "end": end, "frames": end - start, "in": round(start / FPS, 3), "out": round(end / FPS, 3),
            "whole": start == 0 and end == count}


def longest_take(clip: Path) -> dict:
    """The same for a clip on disk, whatever it was asked for: its cuts are looked for first."""

    stack, _ = frames(Path(clip))
    found = cuts(stack)
    return {**window(len(stack), found), "cuts": found, "of": int(len(stack))}


def check(clip: Path, first: Path | None = None, last: Path | None = None, canvas: tuple[int, int] | None = None,
          single: bool = True, alone_stretched: bool = True) -> dict:
    """The report for one take. ``first`` and ``last`` are the keyframe pictures the clip was given (None for none),
    ``canvas`` the size it was generated at (the clip's own when not given), ``single`` whether it was asked for as
    one shot. ``alone_stretched``: a last frame without a first is stretched onto the canvas (the Mac engine) and
    not fitted by a centre crop (ComfyUI's node, which crops it always). ``passed`` is False when a keyframe was not
    held or a cut was found in a single shot."""

    stack, size = frames(Path(clip))
    small = (stack.shape[2], stack.shape[1])
    canvas = canvas or size
    report = {"frames": int(len(stack)), "first": None, "last": None, "cuts": cuts(stack) if single else None}
    # where to trim to when the model cut all the same: the longest stretch without one (None when cuts were asked for)
    report["window"] = window(len(stack), report["cuts"]) if single else None
    if first is not None:
        report["first"] = held(stack[0], fitted(Path(first), canvas, size, small, True))
    if last is not None:
        report["last"] = held(stack[-1], fitted(Path(last), canvas, size, small, first is None and alone_stretched))
    said = []
    if report["first"] and not report["first"]["held"]:
        said.append("the clip does not open on its first frame")
    if report["last"] and not report["last"]["held"]:
        said.append("the clip does not end on its last frame")
    if report["cuts"]:
        times = ", ".join(f"{cut['seconds']:g} s" for cut in report["cuts"][:4])
        said.append(f"{len(report['cuts'])} cut{'s' if len(report['cuts']) > 1 else ''} inside the shot, at {times}")
        said.append(f"the longest stretch without one is frames {report['window']['start']} to {report['window']['end'] - 1} "
                    f"({report['window']['frames'] / FPS:.2f} s)")
    report["passed"] = not said
    report["note"] = "; ".join(said)
    return report
