"""The studio's card as a profile: the engine settings that suit it when the environment does not name them.

The defaults in spark/ were measured on a DGX Spark's GB10 (compute capability 12.1). An NVIDIA GB300 (DGX Station
class: Blackwell Ultra, compute capability 10.3, about 250 GB of HBM) runs the same install, but three of them are the
slower choice there. Measured on a GB300 (docs/gb300.md):
  - TensorFold's int8 blocks are tuned for the GB10: on the GB300 its int8 products reach 156 TOPS where bf16 ones reach
    1,893 TFLOPS, and FastH3 at 864x480, 56 frames took 23.3 s on the engine (2.42 s a pass) against 9.8 s on
    ComfyUI's own bf16 blocks.
  - ComfyUI's --use-ck-attention: 25.2 s for 107 frames against 15.5 s with --use-pytorch-cross-attention (PyTorch picks
    cuDNN's kernel there, 2.7 ms a call against 12.1 ms).
  - The 8-bit video decoder: 6.7 s for 107 frames of 864x480 against 1.3 s for the fp16 one.

So on a GB300 a clip renders on ComfyUI's own blocks, with PyTorch's attention and the fp16 video decoder. Every other
card keeps the defaults it had. SPARK_ENGINE, SPARK_COMFY_ARGS and SPARK_VAE, when set, win over the profile.

Environment: STUDIO_GPU_PROFILE (gb300 or default) names the profile instead of asking nvidia-smi.
  python3 spark/gpu_profile.py            # gb300 or default
  python3 spark/gpu_profile.py comfy-args # the ComfyUI flags spark/comfy.sh starts the server with
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_guard  # noqa: E402  (nvidia-smi for the studio's card: STUDIO_GPU, or the card spark/gpu.sh pins)

GB300, DEFAULT = "gb300", "default"
COMFY_ARGS = "--use-ck-attention"  # ComfyUI's own flash-attention kernel, 12% faster a pass than PyTorch's on a GB10
GB300_COMFY_ARGS = "--use-pytorch-cross-attention"
GB300_VAE = "minimax_h3_video_vae_fp16.safetensors"  # Comfy-Org/MiniMax-H3, 5.2 GB
_SEEN: dict = {}  # nvidia-smi's command for the card -> its profile: a card's compute capability does not change
_FAILED: dict = {}  # the same command -> when nvidia-smi last did not say: asked again after RETRY_S, never kept
RETRY_S = 60.0


def classify(compute_cap: str, name: str = "") -> str:
    """The profile of a card from what nvidia-smi says of it: "gb300" for compute capability 10.3 (sm_103), or, from a
    driver that does not know the compute_cap field, a name with GB300 in it; "default" for every other card."""

    cap = (compute_cap or "").strip()
    if re.fullmatch(r"\d+\.\d+", cap):
        return GB300 if cap == "10.3" else DEFAULT
    return GB300 if "GB300" in (name or "").upper() else DEFAULT


def profile() -> str:
    """The profile of the studio's card: STUDIO_GPU_PROFILE when it says gb300 or default, else asked of nvidia-smi
    (the card memory_guard reads: STUDIO_GPU, the only compute card beside a display card, or the head's card of a
    pair) and kept per card once it answers. "default" without nvidia-smi (a Mac) or while it does not answer: that
    is not kept, and nvidia-smi is asked again after RETRY_S, so a long-running app does not keep a wrong engine
    after one slow or failed query while a fresh render process detects the card."""

    said = os.environ.get("STUDIO_GPU_PROFILE", "").strip().lower()
    if said in (GB300, DEFAULT):
        return said
    key = tuple(memory_guard.nvidia_argv())
    if key in _SEEN:
        return _SEEN[key]
    if key in _FAILED and time.monotonic() - _FAILED[key] < RETRY_S:
        return DEFAULT
    rows = memory_guard.smi("--query-gpu=compute_cap,name")
    if len(rows) > 1 and "-i" not in key:
        # several cards and none chosen (the choice failed, or two compute cards without STUDIO_GPU): the first row
        # may be a display card, so nothing is kept and the default stands until a card is named
        _FAILED[key] = time.monotonic()
        return DEFAULT
    cap, name = (rows[0] + ["", ""])[:2] if rows else ("", "")
    if not re.fullmatch(r"\d+\.\d+", cap.strip()) and not name:
        # an older driver refuses the whole query over compute_cap: the name alone
        named = memory_guard.smi("--query-gpu=name")
        name = named[0][0] if named and named[0] else ""
    if not re.fullmatch(r"\d+\.\d+", cap.strip()) and not name.strip():
        _FAILED[key] = time.monotonic()  # nvidia-smi did not say: not kept, asked again later
        return DEFAULT
    _FAILED.pop(key, None)
    _SEEN[key] = classify(cap, name)
    return _SEEN[key]


def engine(library) -> str:
    """What runs FastH3's transformer blocks, "tensorfold" or "comfy": SPARK_ENGINE when it is set; else TensorFold's
    engine once spark/build-engine.sh has built it at ``library``, except on a GB300, where ComfyUI's own blocks are
    the faster ones. spark_generate.py renders with this and the app's estimates and memory checks follow it."""

    said = os.environ.get("SPARK_ENGINE", "").strip()
    if said:
        return said
    if not Path(library).exists():
        return "comfy"
    return "comfy" if profile() == GB300 else "tensorfold"


def comfy_args() -> str:
    """The flags spark/comfy.sh starts ComfyUI with: SPARK_COMFY_ARGS when it is set, else the card's own."""

    return os.environ.get("SPARK_COMFY_ARGS") or (GB300_COMFY_ARGS if profile() == GB300 else COMFY_ARGS)


def video_vae(default: str) -> str:
    """The video decoder a clip asks for: SPARK_VAE when it is set, the fp16 one on a GB300, else ``default``."""

    return os.environ.get("SPARK_VAE") or (GB300_VAE if profile() == GB300 else default)


def main(argv: list[str]) -> int:
    what = argv[0] if argv else "profile"
    if what == "profile":
        print(profile())
    elif what == "comfy-args":
        print(comfy_args())
    else:
        print("usage: gpu_profile.py [profile|comfy-args]", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
