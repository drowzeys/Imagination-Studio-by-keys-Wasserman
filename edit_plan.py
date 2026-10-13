"""What both builds of Edit a picture agree on before the model runs: the noise an edit starts from, the size it is
made at and the size that comes back. qwen_image_edit.py (MLX) and spark/spark_edit.py (ComfyUI) both read this file.

The noise. An edit starts from noise drawn for its seed, and so did the picture being edited when the Studio made it.
With the same seed and the same size the two draws are the same numbers, and the model, shown a picture beside the
very noise that picture came from, walks back to it: the result is the source again with its contrast doubled (in a
production run 12 of 71 edits, every one whose seed was its source's; edge energy 1.4 to 3.5 times the source's
against 0.2 to 1.3 for other seeds). So the noise is drawn for the seed and the pictures together: `noise_seed`.
Same seed, same pictures, same prompt still gives the same result; it is no longer the noise of any other picture.

The size. The model's own rule brings a picture to about 1024 x 1024 pixels at multiples of 32, so a 1280x736
picture came back as 1344x768. An edit is now made at the picture's own size and comes back at it (`canvas`).

No model code here, and nothing but the standard library.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

MULTIPLE = 32  # the model's grid: a vision slot covers 2x2 latents of 16 pixels
# the pixel counts an edit is made at here (512x512 to 1536x1536): what the app has always accepted as its size
AREA = (512 * 512, 1536 * 1536)
SIDES = (256, 4096)  # a size asked for by hand
PICTURES = 10  # pictures in one edit: the edited one and nine more (mflux's pipeline takes 10, ComfyUI's node 16)


def noise_seed(seed: int, pictures) -> int:
    """The seed the noise is drawn from: the user's seed and the bytes of every picture given, in order. 48 bits, so
    it is a whole number everywhere it travels (JSON, both runtimes' generators)."""

    digest = hashlib.sha256(f"studio-edit:{int(seed)}".encode())
    for picture in pictures:
        with open(picture, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        digest.update(b"|")
    return int.from_bytes(digest.digest()[:6], "big")


def grid(value: float) -> int:
    return max(MULTIPLE, round(value / MULTIPLE) * MULTIPLE)


def fit(width: int, height: int, resolution: int) -> tuple[int, int]:
    """The model's own rule: about resolution x resolution pixels on the grid, the shape kept."""

    ratio = width / height
    return grid(math.sqrt(resolution * resolution * ratio)), grid(math.sqrt(resolution * resolution / ratio))


def canvas(width: int, height: int, mode: str = "source", resolution: int = 1024, asked: tuple[int, int] | None = None,
           most: int = 0) -> dict:
    """Where an edit of a width x height picture is made and what comes back.

    ``made``: the size the model works at (on its grid); the picture is brought to it first. ``size``: the size of the
    file that is written. ``note``: a sentence for the user when the two differ from what was asked, else "".

    - ``asked`` (a width and a height): that size comes back. When its shape is not the picture's, the picture keeps
      its own shape beside it (``source``) and the edit may sit a little off: the model lines an edit up with a
      picture of the same size.
    - mode "source" (the default): the picture's own size. A picture with fewer pixels than 512x512 or more than
      1536x1536 is outside what the model is run at; it is made at the nearest of the two, in its own shape, and comes
      back at that size, with a note saying so. Nothing is enlarged afterwards and called detail.
    - mode "model": the model's own rule, about resolution x resolution pixels (what 2.6.5 and earlier returned).

    ``most`` lowers the largest pixel count of mode "source" (a Mac with less memory: the edit's memory grows with the
    pixels, 41 GiB at 1280x736 and 58 GiB at 2016x1152 in bfloat16 on an M5 Ultra).
    """

    if asked:
        wide, tall = int(asked[0]), int(asked[1])
        made = (grid(wide), grid(tall))
        side = grid(math.sqrt(made[0] * made[1]))
        same = abs(wide / tall - width / height) < 0.02
        return {"made": made, "size": (wide, tall), "source": made if same else fit(width, height, side), "note": ""}
    if mode == "model":
        made = fit(width, height, resolution)
        return {"made": made, "size": made, "source": made, "note": ""}
    low, high = AREA[0], min(AREA[1], most) if most else AREA[1]
    if low <= width * height <= high:
        made = (grid(width), grid(height))
        return {"made": made, "size": (width, height), "source": made, "note": ""}
    bound = int(math.sqrt(low if width * height < low else high))
    made = fit(width, height, bound)
    return {"made": made, "size": made, "source": made,
            "note": f"the picture is {width}x{height}, {'smaller' if width * height < low else 'larger'} than the model edits at: "
                    f"the result is {made[0]}x{made[1]}"}


def refused(width: int, height: int) -> str:
    """Why a size asked for by hand is not one an edit is made at ("" when it is)."""

    low, high = SIDES
    if not (low <= width <= high and low <= height <= high):
        return f"width and height are between {low} and {high}"
    if not AREA[0] <= width * height <= AREA[1]:
        return f"an edit is made at 512x512 to 1536x1536 pixels in all, and {width}x{height} is outside that"
    return ""


def beside(width: int, height: int, made: tuple[int, int]) -> tuple[int, int]:
    """The size a further picture is brought to: about as many pixels as the edit itself, in its own shape."""

    return fit(width, height, grid(math.sqrt(made[0] * made[1])))


def picture_size(path) -> tuple[int, int]:
    """(width, height) of a picture as it is shown: a phone photo's rotation tag is applied."""

    from PIL import Image, ImageOps

    with Image.open(Path(path)) as image:
        return ImageOps.exif_transpose(image).size
