"""A job's live preview in a file, for a page or an app that drives the Studio over its API (opt-in).

With ``STUDIO_PREVIEW_FILE`` set (the app sets it for a job that asks, ``"preview": true`` in its params, or for every
clip with ``STUDIO_PREVIEWS=1``), ComfyUI is asked for its latent2rgb preview of each pass (``extra_data``), and the
newest one is kept in that file as a JPEG, replaced whole each time so a reader never sees half of it. The job's
record says where it is (``preview``) and when it was written (``preview_at``) while the job runs. Unset (the default),
nothing is asked and nothing is written.

A video's latent2rgb preview shows its first latent frame: for a clip from a keyframe that is the keyframe itself,
held, so the clip's own graph points the preview at its middle frame (``frame``, the TensorFold H3 node's
``preview_frame``). The sampling is unchanged.
"""

from __future__ import annotations

import json
import os
import struct

PREVIEW_IMAGE, PREVIEW_IMAGE_WITH_METADATA = 1, 4  # ComfyUI's binary event types (server.BinaryEventTypes)
METHOD = "latent2rgb"


def path() -> str:
    return os.environ.get("STUDIO_PREVIEW_FILE", "").strip()


def wanted() -> bool:
    return bool(path())


def extra_data() -> dict:
    """What a prompt adds to ask ComfyUI for previews ({} when none is wanted)."""

    return {"extra_data": {"preview_method": METHOD}} if wanted() else {}


def picture(message: bytes) -> tuple[bytes | None, dict]:
    """The JPEG (or PNG) of one binary message of ComfyUI's socket, and its metadata ({} for the older kind)."""

    if len(message) < 8:
        return None, {}
    kind = struct.unpack(">I", message[:4])[0]
    if kind == PREVIEW_IMAGE:
        return message[8:] or None, {}
    if kind == PREVIEW_IMAGE_WITH_METADATA:
        size = struct.unpack(">I", message[4:8])[0]
        try:
            meta = json.loads(message[8:8 + size])
        except ValueError:
            meta = {}
        return message[8 + size:] or None, meta if isinstance(meta, dict) else {}
    return None, {}


def take(message: bytes, prompt_id: str | None = None) -> bool:
    """One binary message: its picture written to the preview file when it is a preview of this prompt. True when
    a picture was written. A file that cannot be written is skipped (nothing raises)."""

    target = path()
    if not target:
        return False
    image, meta = picture(message)
    if image is None or (prompt_id and meta.get("prompt_id") not in (None, prompt_id)):
        return False
    part = target + ".part"
    try:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(part, "wb") as handle:
            handle.write(image)
        os.replace(part, target)
    except OSError:
        return False
    return True
