"""Which GPU this pytest process reads.

A DGX Spark has one GPU and nvidia-smi reports its memory as [N/A]. Nothing is set, and the
suite asks nvidia-smi with no index, as it always has. A workstation whose first GPU drives
the monitors is different: that row is a small card, and the compute GPU is a later index.
The app refuses to guess when two compute GPUs are present. The suite is one process, so it
takes the first compute GPU. Set STUDIO_GPU before pytest to choose the other.
"""
import os
import subprocess

PRESET = ""
VISIBLE = ""
CHOSEN: str | None = None


def compute_indexes(text: str) -> list[str]:
    """nvidia-smi indexes with memory of their own that are not driving a display, in order."""

    found = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        index, memory, display = parts[0], parts[1], parts[2]
        if index.isdigit() and memory.isdigit() and display.lower() != "enabled":
            found.append(index)
    return found


def choose_suite_gpu(indexes: list[str], already: str = "", visible: str = "") -> str | None:
    """The index to export, or None to leave nvidia-smi's first row.

    Index 0 is already that row when it is the only compute GPU. An operator's STUDIO_GPU
    is kept, including a display card they named on purpose."""

    if already.isdigit():
        return already
    if visible.isdigit():
        return visible
    if not indexes or indexes == ["0"]:
        return None
    return indexes[0]


def pin_suite_gpu() -> str | None:
    """Export STUDIO_GPU for this process. nvidia-smi ignores CUDA_VISIBLE_DEVICES, so both are set."""

    global PRESET, VISIBLE, CHOSEN
    PRESET = os.environ.get("STUDIO_GPU", "").strip()
    VISIBLE = os.environ.get("CUDA_VISIBLE_DEVICES", "")
    chosen = choose_suite_gpu([], PRESET, VISIBLE if not PRESET else "")
    if chosen is None and not PRESET:
        try:
            done = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,memory.total,display_active", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            done = None
        if done is not None and done.returncode == 0:
            chosen = choose_suite_gpu(compute_indexes(done.stdout), "")
    CHOSEN = chosen
    if chosen is None:
        return None
    os.environ["STUDIO_GPU"] = chosen
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    os.environ["CUDA_VISIBLE_DEVICES"] = chosen
    return chosen
