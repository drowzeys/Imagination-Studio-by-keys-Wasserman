"""The arithmetic of Character Swap (spark/spark_swap.py, app/swap.py): the canvas a part is prepared at, the windows a
part is made in, and which pixel frames each of MiniMax H3's latent frames holds. Plain Python, nothing to install.

MiniMax H3 takes 17n + 5 frames at 24 a second (22, 39, ..., 124, ..., 362). Its video latent has 5k + 2 frames for
17k + 5 pixel frames: the first latent frame is one pixel frame, the next four hold four each, and so on in fives
(1, 4, 4, 4, 4, 1, 4, ...). So 17k + 1 pixel frames fill whole latent frames, which is why the history carried from one
window to the next is 18 frames: 17 frames before the join and the frame at the join itself.

A part longer than one window is made in windows of at most 124 frames each, the frames carried in included. The
first starts at the part's first frame. Each later window starts 17 frames before the last frame the window before
delivered: those 17 frames and that last frame (the join) are the previous window's own output, held as they are, and
the window draws what follows, at most 107 frames from the join (17 + 107 = 124), so 106 new frames a window. The last
window is made a little longer than what is left (to the next 17n + 5) and the extra frames at its end are dropped.
"""

from __future__ import annotations

import math

FPS = 24
WINDOW = 124          # frames a window is at most, the history included (5.17 s)
HISTORY = 18          # frames carried into the next window: 17 before the join, and the join
SECONDS = (5.0, 15.0)  # the part of the clip that is swapped
# Where the memory guard warns instead of refusing (a small card, docs/small-cards.md) a longer part is tried: a part
# is made in windows of at most 124 frames whatever its length, so only the tracking and the time grow with it. Not
# measured on a DGX Spark, which keeps 15 s.
LONGEST_TRIED = 25.0
QUALITIES = (480, 768)  # the short side of the canvas
MULTIPLE = 32
MAX_PIXELS = 768 * 1344
FRAME_PER_LATENT = (1, 4, 4, 4, 4)
# Several people in one job: each is one more swap, made on the result of the one before (the chain of Noncoder's
# MiniMax H3 Multi Swap workflow, which has four). A person picked by number is drawn in a crop of the frame around
# them and pasted back, so everything outside their mask stays the clip's own pixels however many swaps there are.
MOST_SWAPS = 4
MOST_PEOPLE = 9       # the people SAM 3.1 follows and the who's-who picture numbers
# The crop's shape is kept between 9:16 and 16:9 (the shapes the canvas itself has). Choices, not measurements.
NARROWEST, WIDEST = 9 / 16, 16 / 9


def on_grid(frames: int) -> bool:
    return frames >= 5 and (frames - 5) % 17 == 0


def grid_up(frames: int) -> int:
    """The smallest frame count the model takes that holds ``frames``."""

    frames = max(5, int(frames))
    return frames + (5 - frames % 17) % 17 if frames % 17 != 5 else frames


def latent_frames(frames: int) -> int:
    """Latent frames for a frame count on the grid: 5k + 2 for 17k + 5."""

    if not on_grid(frames):
        raise ValueError(f"{frames} frames is not a length MiniMax H3 takes (17n + 5)")
    return (frames - 5) // 17 * 5 + 2


def latent_spans(frames: int) -> list[tuple[int, int]]:
    """For each latent frame, the first and last pixel frame it holds."""

    spans, at = [], 0
    for k in range(latent_frames(frames)):
        count = FRAME_PER_LATENT[k % 5]
        spans.append((at, at + count - 1))
        at += count
    return spans


def canvas(width: int, height: int, quality: int) -> tuple[int, int]:
    """The size a part is prepared at: ``quality`` pixels on the short side, the clip's own shape, each side a multiple
    of 32, and no more than 768 x 1344 pixels in all."""

    if quality not in QUALITIES:
        raise ValueError(f"the quality is {' or '.join(str(q) for q in QUALITIES)}")
    if width <= 0 or height <= 0:
        raise ValueError("the clip has no picture size")
    ratio = width / height
    wide, tall = (quality * ratio, float(quality)) if ratio >= 1 else (float(quality), quality / ratio)
    if wide * tall > MAX_PIXELS:
        scale = math.sqrt(MAX_PIXELS / (wide * tall))
        wide, tall = wide * scale, tall * scale
    side = lambda value: max(MULTIPLE, round(value / MULTIPLE) * MULTIPLE)  # noqa: E731
    return side(wide), side(tall)


def part_frames(seconds: float) -> int:
    return int(round(float(seconds) * FPS))


def windows(frames: int, window: int = WINDOW, history: int = HISTORY) -> list[dict]:
    """The windows ``frames`` source frames are made in. Each: ``start`` (the source frame at the window's first
    frame), ``length`` (frames the model makes, 17n + 5), ``held`` (frames at the start that are the previous window's
    output, 0 for the first), ``pad`` (frames past the part's end, dropped), ``first`` and ``last`` (the source frames
    this window delivers, inclusive) and ``skip`` (the window frame ``first`` is at)."""

    if not on_grid(window):
        raise ValueError(f"a window is 17n + 5 frames, not {window}")
    if history < 1 or (history - 1) % 17 or history >= window:
        raise ValueError(f"the history is 17k + 1 frames, shorter than the window, not {history}")
    if frames < 5:
        raise ValueError("a part is at least 5 frames")
    past = history - 1
    most = window - past  # a later window's frames from the join on: the window, the history included, stays in ``window``
    plan = []
    length = min(window, grid_up(frames))
    plan.append({"index": 1, "start": 0, "length": length, "held": 0, "pad": max(0, length - frames),
                 "first": 0, "last": min(frames, length) - 1, "skip": 0})
    while plan[-1]["last"] < frames - 1:
        join = plan[-1]["last"]
        left = frames - join  # the join and everything after it
        target = most if left > most else grid_up(left)
        start = join - past
        plan.append({"index": len(plan) + 1, "start": start, "length": past + target, "held": history,
                     "pad": max(0, target - left), "first": join + 1, "last": min(frames - 1, join + target - 1),
                     "skip": history})
    for one in plan:
        one["of"] = len(plan)
    return plan


def held_latents(history: int) -> int:
    """How many latent frames at a window's start the history fills (0 for none)."""

    if not history:
        return 0
    return (history - 1) // 17 * 5 + 1


def crop(left: int, top: int, right: int, bottom: int, width: int, height: int) -> dict:
    """The crop one person is drawn in. ``left, top, right, bottom`` is the box that holds their grown mask on every
    frame of the part (right and bottom past the last pixel), ``width`` x ``height`` the part's canvas. Returns
    ``box`` (x, y, w, h on the canvas: one box for the whole part, so the crop does not move) and ``size`` (the
    width and height the crop is drawn at: multiples of 32, the box's shape, and never more pixels than the canvas
    has, so a crop asks no more memory than the measured whole frame). A box as large as the canvas is the canvas."""

    if width <= 0 or height <= 0:
        raise ValueError("the part has no picture size")
    left, top = max(0, int(left)), max(0, int(top))
    right, bottom = min(width, int(right)), min(height, int(bottom))
    if right <= left or bottom <= top:
        raise ValueError("the person's box is empty")
    wide, tall = float(right - left), float(bottom - top)
    if wide / tall < NARROWEST:
        wide = tall * NARROWEST
    elif wide / tall > WIDEST:
        tall = wide / WIDEST
    wide, tall = min(wide, float(width)), min(tall, float(height))
    scale = math.sqrt(width * height / (wide * tall))
    side = lambda value: max(MULTIPLE, int(value * scale + 1e-6) // MULTIPLE * MULTIPLE)  # noqa: E731
    size = (side(wide), side(tall))
    # the box takes the drawn size's shape where the canvas has the room, so the person is not stretched
    shape = size[0] / size[1]
    if wide / tall < shape:
        wide = min(float(width), tall * shape)
    else:
        tall = min(float(height), wide / shape)
    w, h = min(width, max(2, round(wide))), min(height, max(2, round(tall)))
    x = min(max(0, round((left + right) / 2 - w / 2)), width - w)
    y = min(max(0, round((top + bottom) / 2 - h / 2)), height - h)
    return {"box": (x, y, w, h), "size": size}
