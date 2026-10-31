#!/usr/bin/env python3
"""The memory guard of a DGX Spark: what a clip needs, whether this machine has it, and the floor under a render.

On a Spark the GPU and the system share one pool of memory, and a machine that runs out stops answering: on
2026-10-13 a worker was lost to one clip with a LoRA at 1376x768 (the app took the job, the server's one-second,
4 GB check did not act, and the machine went from 40.9 GB available to unreachable inside twelve seconds). This
file is the three layers that now stand in the way, and everything that starts a render goes through them:

1. `check()`: before a job is queued (app/server.py) and again before anything is loaded (spark_generate.py), the
   clip's peak is estimated from measured peaks and the job is refused, with the reason and the nearest size and
   length that fit, when it would leave less than the floor.
2. `watch` (run by spark/comfy.sh beside the ComfyUI server, whatever started it): reads the machine's available
   memory twenty times a second and stops the server, long before the machine is in danger, when it is under the
   floor, when the fall of the last quarter second would take it under a third of the floor within a second, or
   when the kernel reports that work is stalled on memory with under twice the floor available (a cold start's
   model load stalls on the page cache with half the machine free). The render then fails with that reason.
3. The server is marked as the first process the kernel ends if memory runs out all the same (oom_score_adj).

    python3 spark/memory_guard.py check --width 1376 --height 768 --frames 124 --lora
    python3 spark/memory_guard.py watch --pid 1234 [--floor MB]
    python3 spark/memory_guard.py sound      # sound effects and foley: measured, and whether they fit

What was measured (2026-10-13, a 124.5 GB Spark with nothing else running, 118.7 GB available; MemAvailable sampled
ten times a second through whole jobs of the app; "need" is what the job took from the machine at its lowest point):

    TensorFold's int8 engine (a clip without a LoRA)             rows     need
      864x480,  124 frames                                       15.4K    57.6 GB
      1376x768, 124 frames                                       39.2K    62.2 GB (62.1 to 64.2 in a production run)
      1376x768, 192 frames, sampled every 11 s in that run       59.9K    67.1 GB
      1376x768, 243 frames, the same                             75.3K    69.0 to 70.9 GB
    TensorFold's int8 engine with a LoRA merged into it (2026-10-14, a worker Spark, The Wasserman 80s Horror at 1.0,
    each from a stopped server, under a second watchdog at 25 GB)
      864x480,  124 frames                                       15.4K    57.6 GB
      1280x736, 124 frames                                       35.0K    61.4 GB
      1376x768, 124 frames                                       39.2K    62.3 GB
      1376x768, 192 frames                                       59.9K    66.5 GB
      1376x768, 243 frames                                       75.3K    69.7 GB
    ComfyUI's own blocks, bfloat16 weights with a LoRA merged in
      864x480,   56 frames                                        7.3K    75.4 GB
      1280x736,  22 frames                                        7.4K    74.8 GB
      864x480,  124 frames                                       15.4K    75.9 GB
      1280x736,  56 frames (twice)                               16.6K    75.8 and 76.2 GB
      1280x736,  90 frames                                       25.8K    76.2 GB
      1280x736, 107 frames                                       30.4K    76.5 GB
      1280x736, 124 frames                                       35.0K    76.6 GB

The estimate is a straight line for each route (ESTIMATES) read at the clip's rows, plus ERROR_MB. No measured
point is more than 1.7 GB from its line; 6 GB is added because most points are one run and the longest clips were
sampled coarsely. The bfloat16 route is nearly flat: the text encoder (26 GB) and the transformer (42 GB) are both
held, and the rows add little.

A LoRA merged into the int8 engine (the TensorFoldH3Blocks node, an engine with tf_h3_set_weight) takes what the
engine takes without one: its points lie on the int8 line. A clip with a LoRA (or the Ref2VA Turbo adapter, which is
one) is refused above the largest size it was measured at on its route (MEASURED_ROWS, and a frame larger than
MEASURED_PIXELS), whatever the line says. On the bfloat16 route, by the line the clip that took the Spark down (39.2K rows) needs 77 GB and
leaves 41, which is what that Spark showed for a minute before it was lost: its end is not a matter of how much
memory the clip takes, no line through smaller sizes predicts it, and nobody may find out where it starts by trying.
Raise MEASURED_ROWS only with a new measurement, approached from below. Reference clips (Ref2VA), MiniMax H3's own
transformer and a machine without the engine were not measured here: they are estimated on the bfloat16 line with
their references' rows added, which agrees with the README's figure for a reference clip with a LoRA (33 GB of 121
left at 864x480).

Why the old check did not act, as far as it can be known (the lost machine's logs were never read):
- It looked once a second for less than 4 GB available, starting awk each time. Memory falls much faster than
  that: 7.2 GB a second while ComfyUI loads a model from the page cache, 29.5 GB a second (13 GB in half a second)
  while TensorFold's engine loads its weights. At 7 GB a second there is 0.6 s between 4 GB and nothing.
- It read MemAvailable, which counts the page cache. With a LoRA ComfyUI loads until nothing is free: MemFree sat
  at 1.2 to 2.5 GB through every such render while 43 GB read as available (the model files' own cache), and the
  kernel logged the GPU driver being refused memory (NVRM ... NV_ERR_NO_MEMORY) at 2 GB free and 54 GB available.
  A check on what is available cannot see that state; this one keeps it from arising (release_cache).
"""

from __future__ import annotations

import argparse
import ctypes
import os
import signal
import subprocess
import sys
import time

FPS, STEP, BASE, SHORTEST, LONGEST = 24, 17, 5, 22, 362
# The sizes the app offers, smallest first: what "a smaller size" means when a clip does not fit.
SIZES = ((864, 480), (1280, 736), (1344, 768), (1376, 768))
# What an idle Spark keeps for itself (measured: 124.5 GB in all, 118.7 GB available with nothing running).
IDLE_MB = 6000
# route -> (MB with the models loaded and no rows, MB per thousand rows). See the table above.
ESTIMATES = {"int8": (54_500, 215), "int8-lora": (54_500, 215), "bf16": (75_000, 47)}
ERROR_MB = 6000
# The most rows each route was measured at; a route with patched bfloat16 weights is refused above its own.
MEASURED_ROWS = {"int8": 75_336, "int8-lora": 75_336, "bf16": 34_960}
# ... and the largest frame it was measured at: a wider frame with fewer of them is as unknown
MEASURED_PIXELS = {"int8-lora": 1376 * 768, "bf16": 1280 * 736}
# the largest clip each patched route was measured at, as the refusal names it
LARGEST = {"int8-lora": "1376x768, 243 frames", "bf16": "1280x736, 124 frames"}
# the measured points themselves, (rows, MB): tests/test_memory_guard.py holds the lines to them
MEASURED = {"int8": ((15_390, 57_575), (39_216, 62_187), (39_216, 64_200), (59_856, 67_100), (75_336, 69_000), (75_336, 70_900)),
            "int8-lora": ((15_390, 57_622), (34_960, 61_418), (39_216, 62_321), (59_856, 66_468), (75_336, 69_668)),
            "bf16": ((7_290, 75_369), (7_360, 74_805), (15_390, 75_898), (16_560, 75_841), (16_560, 76_160), (25_760, 76_237),
                     (30_360, 76_505), (34_960, 76_600))}
POLL_S, RATE_WINDOW_S, STALL_WINDOW_S, STALL_SHARE = 0.05, 0.25, 2.0, 0.5


# ---------- the machine ----------

def meminfo(text: str | None = None) -> dict:
    """/proc/meminfo in MB ({} where there is none: a Mac, which this guard does not cover)."""

    if text is None:
        try:
            with open("/proc/meminfo") as handle:
                text = handle.read()
        except OSError:
            return {}
    out = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        if rest.strip():
            out[name] = int(rest.split()[0]) // 1024
    return out


# What the kernel's own counters leave unexplained on an idle Spark (MemTotal less free, cache, process and kernel
# memory): 3.5 to 4 GB on the workers (2026-10-23, driver 580.173.02). Far more than that with no CUDA process running
# is memory the NVIDIA driver kept after a process ended: drivers from 590 on keep it in their system memory pools
# (issue #59), where MemAvailable does not count it.
UNEXPLAINED_IDLE_MB = 16_000
EXPLAINED = ("MemFree", "Buffers", "Cached", "SwapCached", "AnonPages", "Slab", "KernelStack", "PageTables",
             "SecPageTables", "VmallocUsed")


def unexplained_mb(info: dict | None = None) -> int:
    """The machine's memory that /proc/meminfo puts nowhere: the GPU driver's, on a Spark (0 without meminfo)."""

    info = meminfo() if info is None else info
    if not info.get("MemTotal"):
        return 0
    return max(0, info["MemTotal"] - sum(info.get(name, 0) for name in EXPLAINED))


def driver_pools() -> int | None:
    """The driver's EnableSystemMemoryPools setting (0 is off), None where it does not say (R580 has no such line)."""

    try:
        with open("/proc/driver/nvidia/params") as handle:
            for line in handle:
                name, _, value = line.partition(":")
                if name.strip() == "EnableSystemMemoryPools":
                    return int(value.strip())
    except (OSError, ValueError):
        pass
    return None


def held_note(info: dict | None = None) -> str:
    """A sentence for a refusal when the GPU driver, not a program, seems to hold the missing memory: much of it
    unexplained by the kernel's counters while no CUDA process runs. "" otherwise, and where nvidia-smi does not
    answer (it cannot then be told apart from a program's GPU memory)."""

    held = unexplained_mb(info)
    if held < UNEXPLAINED_IDLE_MB or not smi("--query-gpu=name") or processes_mb():
        return ""
    pools = driver_pools()
    said = (f" About {held / 1000:.0f} GB is held outside every program with no GPU program running: the NVIDIA driver "
            "keeps memory a finished process freed")
    said += " (its system memory pools are on)" if pools else ""
    return said + (". Turning those pools off (NVreg_EnableSystemMemoryPools=0), or the R580 driver, gives it "
                   "back: README, \"The memory guard (DGX Spark)\".")


def floor_mb(total_mb: int | None = None) -> int:
    """The least memory that stays available while a render runs: SPARK_MEMORY_FLOOR_MB, or a tenth of the machine
    (and never under 8 GB)."""

    asked = os.environ.get("SPARK_MEMORY_FLOOR_MB", "").strip()
    if asked.isdigit() and int(asked) > 0:
        return int(asked)
    if total_mb is None:
        total_mb = meminfo().get("MemTotal", 0)
    return max(8000, total_mb // 10)


def keep_free_mb(floor: int) -> int:
    """Free memory (not page cache) under which the model files' cache is given back: SPARK_MEMORY_FREE_MB, or two
    thirds of the floor."""

    asked = os.environ.get("SPARK_MEMORY_FREE_MB", "").strip()
    return int(asked) if asked.isdigit() else floor * 2 // 3


def comfy_file(ext: str) -> str:
    """A ComfyUI side file under PREFIX.

    The default port keeps ``comfy.log``, ``comfy.kind`` and ``comfy.mem``. Another ``SPARK_COMFY_PORT`` gets
    ``comfy-<port>.<ext>``, so two studios that share this install do not share a log or a loaded-model mark.
    """

    prefix = os.environ.get("PREFIX") or os.path.expanduser("~/.local/opt/tensorfold-studio")
    port = os.environ.get("SPARK_COMFY_PORT", "8190").strip() or "8190"
    stem = "comfy" if port == "8190" else f"comfy-{port}"
    return os.path.join(prefix, f"{stem}.{ext}")


def state_file() -> str:
    return comfy_file("mem")


def server_pid() -> int | None:
    """The ComfyUI server of spark/comfy.sh on this machine, if one runs (by its command line, as comfy.sh finds it)."""

    port = os.environ.get("SPARK_COMFY_PORT", "8190").strip() or "8190"  # read now: a test or a second studio sets it after import
    wanted = b"\x00".join([b"main.py", b"--listen", b"127.0.0.1", b"--port", port.encode(), b""])
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None
    for name in entries:
        if name.isdigit():
            try:
                with open(f"/proc/{name}/cmdline", "rb") as handle:
                    if wanted in handle.read() + b"\x00":
                        return int(name)
            except OSError:
                pass
    return None


def family(pid: int) -> set[int]:
    """``pid`` and every process under it (the ComfyUI server's own: the second card's rank, spark/tensorfold_h3/tp.py)."""

    children: dict[int, list[int]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return {pid}
    for name in entries:
        if name.isdigit():
            try:
                with open(f"/proc/{name}/stat", "rb") as handle:
                    parent = int(handle.read().rsplit(b")", 1)[1].split()[1])
                children.setdefault(parent, []).append(int(name))
            except (OSError, ValueError, IndexError):
                pass
    found, todo = set(), [pid]
    while todo:
        one = todo.pop()
        if one not in found:
            found.add(one)
            todo.extend(children.get(one, []))
    return found


# ---------- a discrete GPU: an NVIDIA card with memory of its own (an RTX Pro 6000 Blackwell, 96 GB) ----------
# A DGX Spark's GB10 has one memory for the CPU and the GPU (nvidia-smi says [N/A] for the GPU's), and everything above
# reads it as /proc/meminfo. A card with its own memory is full long before the machine's RAM is: there a job's room is
# the card's own (device_room_mb, the 2 GB margin already taken out), held to the RAM as well (card_pool). The estimates stay the Spark's, measured as the fall of the whole machine's memory: a card
# holds a part of that (the rest is the processes' own RAM), so they are an upper bound there.
# STUDIO_VRAM_GB pretends to be such a card on a Spark (the RTX Pro 6000 simulation, docs/rtx-pro-6000.md): its total
# is that, its used memory what the CUDA processes hold (nvidia-smi's compute apps).
VRAM_MARGIN_MB = 2048  # kept free on the card: CUDA's own context and fragmentation
_DEVICE: dict = {"at": 0.0, "value": None}


_CHOSEN: dict = {}


def chosen_gpu() -> str:
    """The nvidia-smi index of the studio's card when ``STUDIO_GPU`` is unset, as ``spark/gpu.sh`` picks it: a single
    ``CUDA_VISIBLE_DEVICES`` index, else the only GPU not driving a display when another one is listed (a server
    started without scripts/app.sh beside a display card). "" for one GPU (a DGX Spark), for two compute cards (the
    app asks for STUDIO_GPU there) and where nvidia-smi does not answer. Asked once."""

    if "index" not in _CHOSEN:
        visible = os.environ.get("CUDA_VISIBLE_DEVICES", "").strip()
        pick = visible if visible.isdigit() else ""
        if not pick:
            try:
                said = subprocess.run(["nvidia-smi", "--query-gpu=index,memory.total,display_active", "--format=csv,noheader,nounits"],
                                      capture_output=True, text=True, timeout=5)
                rows = [[part.strip() for part in line.split(",")] for line in said.stdout.splitlines() if line.strip()] if said.returncode == 0 else []
            except (OSError, subprocess.SubprocessError):
                rows = []
            compute = [row[0] for row in rows if len(row) >= 3 and row[0].isdigit() and row[1].isdigit() and row[2].lower() != "enabled"]
            pick = compute[0] if len(rows) > 1 and len(compute) == 1 else ""
        _CHOSEN["index"] = pick
    return _CHOSEN["index"]


def nvidia_argv() -> list[str]:
    """`nvidia-smi` for the studio's card. `STUDIO_GPU` is an nvidia-smi index (`spark/gpu.sh`).

    Unset, the card ``chosen_gpu`` names, else the first GPU, which is the only GPU on a DGX Spark. nvidia-smi ignores
    `CUDA_VISIBLE_DEVICES`, so the index is passed with `-i`."""

    paired = paired_gpus()
    if paired:
        # paired cards (spark/gpus.py): every card this Studio renders on, the head's first
        return ["nvidia-smi", "-i", ",".join(paired)]
    raw = os.environ.get("STUDIO_GPU", "").strip()
    raw = raw if raw.isdigit() else chosen_gpu()
    return ["nvidia-smi", "-i", raw] if raw.isdigit() else ["nvidia-smi"]


def paired_gpus() -> list[str]:
    """``STUDIO_GPUS`` (spark/gpu.sh): two or more nvidia-smi indexes one Studio renders on together, the head's card
    (``STUDIO_GPU``) first. [] when unset or invalid, when STUDIO_GPU_SPLIT is off, and in a managed local worker (a
    UUID pin: one card a worker)."""

    raw = os.environ.get("STUDIO_GPUS", "").strip()
    if not raw or os.environ.get("STUDIO_LOCAL_GPU_UUID") or not _split_allowed():
        return []
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) < 2 or not all(part.isdigit() for part in parts) or len(set(parts)) != len(parts):
        return []
    head = os.environ.get("STUDIO_GPU", "").strip()
    return parts if not head or head == parts[0] else []


def smi(*query: str) -> list[list[str]]:
    try:
        said = subprocess.run([*nvidia_argv(), *query, "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    return [[part.strip() for part in line.split(",")] for line in said.stdout.splitlines() if line.strip()] if said.returncode == 0 else []


def processes_mb() -> dict[int, int]:
    """Each CUDA process's device memory (MB), as nvidia-smi reports it."""

    out = {}
    for row in smi("--query-compute-apps=pid,used_memory"):
        if len(row) == 2 and row[0].isdigit() and row[1].isdigit():
            out[int(row[0])] = out.get(int(row[0]), 0) + int(row[1])
    return out


def device(fresh: bool = False) -> dict | None:
    """The card's own memory, {"total_mb", "used_mb", "free_mb", "simulated"}, or None where the GPU has none of its
    own (a DGX Spark) or there is no nvidia-smi. Asked again at most every 2 s."""

    if not fresh and time.monotonic() - _DEVICE["at"] < 2:
        return _DEVICE["value"]
    value = None
    pretend = os.environ.get("STUDIO_VRAM_GB", "").strip()
    if pretend:
        try:
            total = int(float(pretend) * 1024)
            used = sum(processes_mb().values())
            value = {"total_mb": total, "used_mb": used, "free_mb": max(0, total - used), "simulated": True}
        except ValueError:
            value = None
    else:
        rows_ = [row for row in smi("--query-gpu=memory.total,memory.used,memory.free")
                 if len(row) >= 3 and all(part.isdigit() for part in row[:3])]
        if len(rows_) >= 2 and _split_allowed():
            # two or more cards, each stage of a workflow placed on its card (spark/gpus.py): the job's room is all of
            # them together; ``largest_free_mb`` is what one card has for an engine that runs on a single one
            cards = [tuple(int(part) for part in row[:3]) for row in rows_]
            value = {"total_mb": sum(c[0] for c in cards), "used_mb": sum(c[1] for c in cards),
                     "free_mb": sum(c[2] for c in cards), "simulated": False, "cards": len(cards),
                     "largest_free_mb": max(c[2] for c in cards), "card_total_mb": min(c[0] for c in cards)}
        elif rows_:
            total, used, free = (int(part) for part in rows_[0][:3])
            value = {"total_mb": total, "used_mb": used, "free_mb": free, "simulated": False}
    _DEVICE.update(at=time.monotonic(), value=value)
    return value


def _split_allowed() -> bool:
    """Whether two or more cards are split (spark/gpus.py: STUDIO_GPU_SPLIT, on unless off)."""

    return os.environ.get("STUDIO_GPU_SPLIT", "auto").strip().lower() not in ("0", "off", "no", "false")


def single_card_free_mb() -> int | None:
    """What the emptiest-to-fullest card has free: what an engine that runs on one card (Drama 3, Bolt) can use."""

    card = device()
    if card is None:
        return None
    return card.get("largest_free_mb", card["free_mb"])


def discrete() -> bool:
    return device() is not None


def recent_device(within_s: float = 2.0) -> tuple[bool, dict | None]:
    """(whether ``device`` read the card less than ``within_s`` seconds ago, that reading). A caller that only needs
    to know whether there is a card reuses a fresh reading instead of asking nvidia-smi again."""

    return time.monotonic() - _DEVICE["at"] < within_s, _DEVICE["value"]


def room_budget_mb() -> int | None:
    """STUDIO_ROOM_MB (MB) when it is set to a whole number above 0, else None (room_mb says what it is for)."""

    asked = os.environ.get("STUDIO_ROOM_MB", "").strip()
    return int(asked) if asked.isdigit() and int(asked) > 0 else None


def card_room_applies() -> bool:
    """Whether a job's room is the card's own (``device_room_mb``, the 2 GB margin as its floor): a discrete card with
    no STUDIO_ROOM_MB. With STUDIO_ROOM_MB the budget is the machine's RAM and the checks keep the Spark's arithmetic,
    as they did before the card had its own pool. False on a Spark."""

    return device_room_mb() is not None and room_budget_mb() is None


def device_room_mb() -> int | None:
    """What the card has for a render with none of the Studio's ComfyUI models counted against it (its server's
    memory counts as the job's, as room_mb counts it on a Spark); None on a Spark."""

    card = device()
    if card is None:
        return None
    pid = server_pid()
    used = processes_mb()
    # what the server holds, its own processes too (the second card's rank of a two-GPU pass): all of it is the job's
    held = sum(used.get(one, 0) for one in family(pid)) if pid else 0
    return max(0, card["free_mb"] + held - VRAM_MARGIN_MB)


def card_room_for_job(raw_befores: list | None = None) -> int | None:
    """The card's room for a job that unloads Drama 3, Music, or Bolt first (MB, the 2 GB margin already out).

    ``device_room_mb`` adds back only the ComfyUI server. Each engine's ``room_mb`` is the card's raw free memory
    just before that engine loaded, which the job gets back. The room is the larger of those, and the margin is
    taken out once. None on a Spark (``device_room_mb`` is None). The checks hold it to the RAM (``card_pool``). A
    caller that already holds an after-margin room (Bolt beside the job) does not use this."""

    base = device_room_mb()
    if base is None:
        return None
    raw = base + VRAM_MARGIN_MB
    for one in raw_befores or ():
        if one:
            raw = max(raw, int(one))
    return max(0, raw - VRAM_MARGIN_MB)


def room_mb() -> int | None:
    """What this machine has for a render, with none of the Studio's own models counted against it: what is
    available now, or, while the ComfyUI server runs, what was available when it started (the guard beside it
    wrote that down). A server from before the guard has no such note: the machine is then taken as otherwise
    idle. None where there is no /proc/meminfo.

    On a discrete card, that card's room (``device_room_mb``, the 2 GB margin already taken out). The checks still
    hold it to the machine's RAM (``card_pool``): the models are read through RAM and ComfyUI's guard kills on it.
    The Spark branch, ``device_room_mb`` None, returns the RAM figure above and is not mixed.

    STUDIO_ROOM_MB (MB) stands in for the card's room on a discrete card, for a card smaller than the video models
    when ComfyUI offloads them to RAM and streams blocks to the card (docs/rtx-5090.md). It is a budget of the
    machine's RAM, so it never gives more than the RAM room above; unset, empty or not a number, it is ignored.
    A Spark has no card to stand in for."""

    ram = ram_room_mb()
    if ram is None:
        return None
    card = device_room_mb()
    if card is None:
        return ram
    asked = room_budget_mb()
    if asked:
        return min(ram, asked)
    return card


def ram_room_mb() -> int | None:
    """The machine's RAM for a render (``room_mb`` on a Spark): MemAvailable now, or, while the ComfyUI server runs,
    what was available when it started. None where there is no /proc/meminfo."""

    info = meminfo()
    now = info.get("MemAvailable")
    if now is None:
        return None
    ram = now
    try:
        with open(state_file()) as handle:
            before, pid = handle.read().split()[:2]
        if alive(int(pid)):
            ram = max(now, int(before))
        elif server_pid():
            ram = max(now, info["MemTotal"] - IDLE_MB)
    except (OSError, ValueError):
        if server_pid():
            ram = max(now, info["MemTotal"] - IDLE_MB)
    return ram


# ---------- layer 1: the estimate ----------

def rows(width: int, height: int, frames: int, pictures: int = 0, reference_frames: int = 0) -> int:
    """The rows every pass carries: one per 32x32 pixels of each latent frame (17 frames of video are 5 latent
    frames, and the first 5 are 3). A reference picture is one more latent frame, a reference video its own."""

    per_frame = (width // 32) * (height // 32)
    latent = lambda count: 5 * max(0, (count - BASE) // STEP) + 3
    return per_frame * (latent(frames) + pictures + (latent(reference_frames) if reference_frames else 0))


def route_of(lora: bool = False, references: bool = False, base: bool = False, engine: bool = True) -> str:
    """Which weights a clip runs on: TensorFold's int8 engine (with a LoRA merged into it: "int8-lora"), or ComfyUI's
    own blocks with the bfloat16 file (a clip from references, MiniMax H3's own transformer, or a machine without the
    engine, or with an engine build that cannot take a LoRA)."""

    if references or base or not engine:
        return "bf16"
    return "int8-lora" if lora else "int8"


def engine_takes_lora(library) -> bool:
    """Whether the engine library at `library` merges a LoRA itself (it has tf_h3_set_weight); read from the file,
    so that nothing loads CUDA to find out."""

    try:
        with open(library, "rb") as handle:
            return b"tf_h3_set_weight" in handle.read()
    except OSError:
        return False


def need_mb(route: str, count: int) -> int:
    fixed, per_thousand = ESTIMATES[route]
    return int(fixed + per_thousand * count / 1000)


def counts() -> list[int]:
    return list(range(SHORTEST, LONGEST + 1, STEP))


def says(frames: int) -> str:
    return f"{frames} frames ({frames / FPS:.1f} s)"


def card_pool(card_mb: int, total_mb: int | None = None) -> tuple[int, int, bool]:
    """(available MB, floor MB, whether the sentence names the GPU) for a job on a discrete card whose room is
    ``card_mb`` (after the 2 GB margin), held to the machine's RAM as ``min(ram, card + floor)`` held it.

    The card's side compares its room plus the margin against the margin as the floor; the RAM side compares
    ``ram_room_mb`` against ``floor_mb``. Whichever runs out first is the pool, and the sentence names that one:
    the GPU, or the machine. A job fits exactly where it fitted under ``min(ram, card + floor)``."""

    ram = ram_room_mb()
    if total_mb is None:
        total_mb = meminfo().get("MemTotal", 0)
    if ram is not None and total_mb:
        floor = floor_mb(total_mb)
        if ram - floor < card_mb:
            return ram, floor, False
    return card_mb + VRAM_MARGIN_MB, VRAM_MARGIN_MB, True


def _pool(available_mb: int | None, total_mb: int, card: bool = False) -> tuple[int, int, bool]:
    """(available MB, floor MB, whether the sentence names the GPU).

    A caller that omits ``available_mb`` on a discrete card gets the card's free memory, with what the studio's
    ComfyUI server holds counted back, and the 2 GB margin as the floor (``card_pool``, held to the RAM).

    ``card`` is for a caller that already passed that after-margin room (a job beside Bolt, or one that unloads an
    engine first). A passed ``available_mb`` without it keeps the Spark arithmetic, so a test that names a Spark's
    pool stays on it even when this machine has a card. With STUDIO_ROOM_MB the Spark arithmetic is kept too: the
    budget is the machine's RAM (``room_mb``).
    """

    if card_room_applies() and available_mb is None:
        return card_pool(device_room_mb(), total_mb)
    if card and available_mb is not None:
        return card_pool(available_mb, total_mb)
    if available_mb is None:
        available_mb = room_mb() or total_mb - IDLE_MB
    return available_mb, floor_mb(total_mb), False


def check(width: int, height: int, frames: int, lora: bool = False, references: bool = False, base: bool = False,
          engine: bool = True, pictures: int = 0, reference_frames: int = 0, available_mb: int | None = None,
          total_mb: int | None = None, card: bool = False, adapter: bool = False) -> dict:
    """Whether this clip fits this machine. ``available_mb`` is what the machine has with none of the Studio's own
    models loaded (default: all of it less what an idle Spark keeps). Returns {"fits", "reason", "need_mb",
    "error_mb", "left_mb", "floor_mb", "available_mb", "route", "rows", "nearest"}; on a machine without
    /proc/meminfo everything fits (the Mac build has its own limits)."""

    if total_mb is None:
        total_mb = meminfo().get("MemTotal", 0)
    if not total_mb:
        return {"fits": True, "reason": "", "route": None}
    measured_here = available_mb is None and not card
    available_mb, floor, on_card = _pool(available_mb, total_mb, card)
    route = route_of(lora, references, base, engine)
    patched = lora and route in MEASURED_PIXELS  # a LoRA or the Turbo adapter merged into the weights as they load

    def one(w: int, h: int, f: int) -> tuple[bool, int, int, str]:
        count = rows(w, h, f, pictures, reference_frames)
        need = need_mb(route, count)
        left = available_mb - need - ERROR_MB
        if patched and (count > MEASURED_ROWS[route] or w * h > MEASURED_PIXELS[route]):
            return False, need, left, "unmeasured"
        return left >= floor, need, left, "" if left >= floor else "memory"

    fits, need, left, why = one(width, height, frames)
    out = {"fits": fits, "reason": "", "need_mb": need, "error_mb": ERROR_MB, "left_mb": left, "floor_mb": floor,
           "available_mb": available_mb, "route": route, "rows": rows(width, height, frames, pictures, reference_frames),
           "nearest": None}
    if fits:
        return out
    shorter = next((f for f in reversed(counts()) if f < frames and one(width, height, f)[0]), None)
    smaller = next(((w, h) for w, h in reversed(SIZES) if w * h < width * height and one(w, h, frames)[0]), None)
    out["nearest"] = {"frames": shorter, "seconds": round(shorter / FPS, 3) if shorter else None,
                      "size": list(smaller) if smaller else None}
    # `adapter`: the reference clip's own Turbo 4-pass speed-up, which the guard counts as a LoRA but the person never picked
    what = ("a clip from references with the Turbo 4-pass speed-up" if adapter and references else
            "a clip with a LoRA" if lora else "a clip from references" if references else "this clip")
    how = {"bf16": "ComfyUI's own blocks with the full-precision (bfloat16) weights", "int8": "TensorFold's int8 engine",
           "int8-lora": "TensorFold's int8 engine with the LoRA merged into it"}[route]
    if why == "unmeasured":
        over = (f"{width}x{height} is a larger frame than" if width * height > MEASURED_PIXELS[route]
                else f"{width}x{height}, {says(frames)} is {out['rows'] / 1000:.1f}K rows a pass, more than")
        said = (f"{what} renders on {how}, and {over} the largest such clip measured on a DGX Spark "
                f"({LARGEST[route]}: {MEASURED_ROWS[route] / 1000:.1f}K rows a pass). Above that the memory it takes "
                "is not known, and a clip with a LoRA on the bfloat16 weights (1376x768, 124 frames) took a Spark down, "
                "so it is refused")
    else:
        who = "GPU" if on_card else "machine"
        said = (f"{what} renders on {how} and needs about {need / 1000:.0f} GB at its peak (give or take {ERROR_MB / 1000:g}); this "
                f"{who} has {available_mb / 1000:.0f} GB for it and keeps {floor / 1000:.0f} GB free as its floor, so it is refused")
    fit = [f"{says(shorter)} at this size" if shorter else "", f"{smaller[0]}x{smaller[1]} at this length" if smaller else ""]
    fit = " or ".join(bit for bit in fit if bit)
    out["reason"] = said + (f". What fits: {fit}." if fit else ". No smaller size or shorter length of this kind fits either.")
    if lora and why == "memory" and route == "bf16":
        out["reason"] += " Without the LoRA the clip runs on the int8 engine, which needs less."
    if adapter and references and why == "unmeasured":
        out["reason"] += " To keep this size, choose 20 passes (the model as released) instead of Turbo: that setting is not held to this limit."
    if why == "memory" and measured_here and not on_card:
        out["reason"] += held_note()
    return out


# MiniMax Music 3 on ComfyUI (spark_generate.py music): (MB with its three files loaded and nothing made, MB per second
# of song asked for). Measured 2026-10-16 on a worker Spark (124.5 GB, 5.7 GB in use before the first song, ComfyUI
# 5a665890, DiT fp16 + text encoder pruned bf16, one run each): the peak a song took from the machine, MemTotal -
# MemAvailable sampled once a second through the whole run, less those 5.7 GB. The text encoder (16.7 GB) and the DiT
# (4.9 GB) are held throughout; the length adds little.
MUSIC_ESTIMATE = (27_800, 11)
# (seconds asked, seconds made, MB): the measured songs; tests/test_music.py holds the line to them
MUSIC_MEASURED = ((30, 30.0, 28_070), (60, 60.0, 28_665), (120, 55.6, 28_617), (120, 120.0, 29_078),
                  (300, 189.8, 28_996), (60, 60.0, 29_299))  # the last: through the app, from an empty page cache
MUSIC_LONGEST = 300  # the app's longest song
# MiniMax Music 3 on TensorFold's CUDA engine (music3-generate, branch music3-cuda 6b714531), int8 and bf16 weights
# (the TensorFold 1.1.5 build, music3-cuda-115 5505dbfa, peaked at the same 16.46 GB for a 30 s int8 song):
# (MB, MB per second of song asked for), the same way, measured 2026-10-16 on worker .2 (5.1 GB in use before each
# song, one process a song, MemTotal - MemAvailable twice a second, less those 5.1 GB; hiphop seed 7, and a 120 s
# song of longer lyrics). The weights are on the GPU throughout; the length adds the AR cache and the DiT's windows.
# Songs over 120 s are not measured: the line goes on at the same slope.
MUSIC_CUDA_ESTIMATE = {"music-cuda-int8": (15_900, 30), "music-cuda-bf16": (23_200, 30)}
MUSIC_CUDA_MEASURED = {"music-cuda-int8": ((30, 30.0, 16_632), (60, 60.0, 17_928), (120, 63.3, 18_885), (120, 120.0, 19_291),
                                           (60, 55.2, 18_656)),  # the last: through the app (Studio 2.9.1), Whisper after it
                       "music-cuda-bf16": ((30, 30.0, 24_012), (60, 60.0, 25_485), (120, 61.3, 25_683), (120, 120.0, 26_647))}
# YuE2 (app/music_yue2.py, spark/yue2_server.py: YuE2-3B and YuE2-Vae in YuE2's own environment, PyTorch 2.14.1), bf16
# and with the int8 weight-only token loop: (MB, MB per second of song asked for). Measured 2026-10-27 on a GB10 Spark:
# the server's own peak through each song, its GPU memory (nvidia-smi, per process) and its resident memory, sampled
# twice a second; the machine's MemTotal - MemAvailable above idle rose by the same give or take 0.5 GB. The weights and
# the decoder stay on the GPU throughout (7.5 GB bf16, 9.3 GB with the int8 copies); the length adds the KV cache and
# the flow matching's frames. A load peaks at 15.1 GB (the file read once into memory beside its copy on the GPU).
# Songs over 120 s are not measured: the line goes on at the same slope.
MUSIC_YUE2_ESTIMATE = {"music-yue2-bf16": (14_100, 45), "music-yue2-int8": (15_600, 30),
                       # the tokens on TensorFold's decode engine (its own int8 copy, 2.6 GB, beside the bf16 model)
                       "music-yue2-blocks": (18_200, 30)}
# (seconds asked, seconds made, MB): English pop lyrics seed 21 at 30, 60, 120 s; the int8 65 and 70 s rows are the two
# A/B instrumentals at seed 42 and 43
MUSIC_YUE2_MEASURED: dict = {"music-yue2-bf16": ((30, 30.0, 12_193), (60, 60.0, 15_809), (120, 120.0, 19_438)),
                             "music-yue2-int8": ((30, 30.0, 13_495), (60, 60.0, 17_031), (120, 120.0, 19_149),
                                                 (65, 65.0, 16_752), (70, 70.0, 17_079)),
                             "music-yue2-blocks": ((30, 30.0, 17_048), (60, 60.0, 19_930), (120, 120.0, 21_035))}


def music_check(seconds: float, available_mb: int | None = None, total_mb: int | None = None, route: str = "music",
                card: bool = False) -> dict:
    """Whether a MiniMax Music 3 song of up to ``seconds`` fits this machine, as ``check`` answers for a clip:
    ``route`` "music" (ComfyUI's nodes), "music-cuda-int8" / "music-cuda-bf16" (TensorFold's CUDA engine) or
    "music-yue2-bf16" / "music-yue2-int8" (YuE2); everything fits where there is no /proc/meminfo."""

    if total_mb is None:
        total_mb = meminfo().get("MemTotal", 0)
    if not total_mb:
        return {"fits": True, "reason": "", "route": None}
    measured_here = available_mb is None and not card
    available_mb, floor, on_card = _pool(available_mb, total_mb, card)
    fixed, per_second = MUSIC_CUDA_ESTIMATE.get(route) or MUSIC_YUE2_ESTIMATE.get(route) or MUSIC_ESTIMATE
    route = route if route in MUSIC_CUDA_ESTIMATE or route in MUSIC_YUE2_ESTIMATE else "music"
    need = int(fixed + per_second * float(seconds))
    left = available_mb - need - ERROR_MB
    out = {"fits": left >= floor, "reason": "", "need_mb": need, "error_mb": ERROR_MB, "left_mb": left, "floor_mb": floor,
           "available_mb": available_mb, "route": route, "rows": None, "nearest": None}
    if not out["fits"]:
        named = card_name() if on_card else ""
        who = (f"GPU ({named})" if named else "GPU") if on_card else "machine"
        out["reason"] = (f"a song of up to {seconds:g} s needs about {need / 1000:.1f} GB at its peak (give or take "
                         f"{ERROR_MB / 1000:g}); this {who} has {available_mb / 1000:.1f} GB for it and keeps "
                         f"{floor / 1000:.1f} GB free as its floor, so it is refused. " +
                         ("Choose a shorter song, or on a card smaller than the models set STUDIO_ROOM_MB (docs/rtx-5090.md)"
                          if on_card else "Something outside the studio holds the rest"))
        note = held_note() if measured_here and not on_card else ""
        out["reason"] += "." + note if note else ""
    return out


# Sound effects (Stable Audio Open 1.0) and foley (MMAudio large_44k_v2) on ComfyUI (spark_generate.py sfx / foley):
# kind -> (MB with the models loaded and nothing made, MB per second of sound asked for). Each is measured the way a
# song was, from a stopped server on a worker Spark under a second watchdog, smallest first; a kind with no line here
# has not been measured on a DGX Spark, and is refused there.
# Measured 2026-10-20 on worker .5 (6.4 to 7.2 GB in use before each, ComfyUI stopped before each run): Stable Audio
# Open's 100 steps hardly grow with the length; MMAudio's foley grows with the clip's frames it reads.
SOUND_ESTIMATE: dict = {"sfx": (7_000, 40), "foley": (13_000, 300)}
# kind -> ((seconds asked, MB), ...): the measured runs; tests/test_sound.py holds each line to them
SOUND_MEASURED: dict = {"sfx": ((5, 6_211), (20, 4_665), (47, 8_679)), "foley": ((5, 13_744), (10, 15_426), (15, 16_617))}
SOUND_LONGEST = {"sfx": 47.0, "foley": 15.0}  # the app's longest of each (app/sound.py)
SOUND_NAMES = {"sfx": "a sound effect (Stable Audio Open 1.0)", "foley": "foley (MMAudio)"}


def sound_check(kind: str, seconds: float, available_mb: int | None = None, total_mb: int | None = None,
                card: bool = False) -> dict:
    """Whether a sound effect or a clip's foley of ``seconds`` fits this machine, as ``music_check`` answers for a song;
    a kind not measured here is refused with that reason. Everything fits where there is no /proc/meminfo."""

    if total_mb is None:
        total_mb = meminfo().get("MemTotal", 0)
    if not total_mb:
        return {"fits": True, "reason": "", "route": None}
    measured_here = available_mb is None and not card
    available_mb, floor, on_card = _pool(available_mb, total_mb, card)
    out = {"fits": False, "reason": "", "need_mb": None, "error_mb": ERROR_MB, "left_mb": None, "floor_mb": floor,
           "available_mb": available_mb, "route": f"sound-{kind}", "rows": None, "nearest": None}
    if kind not in SOUND_ESTIMATE:
        if on_card:
            out["reason"] = (f"{SOUND_NAMES.get(kind, kind)} has not been measured on this GPU yet, and nothing renders "
                             "here without a measured memory estimate")
        else:
            out["reason"] = (f"{SOUND_NAMES.get(kind, kind)} has not been measured on a DGX Spark yet, and nothing renders on a "
                             "Spark without a measured memory estimate (spark/memory_guard.py), so it is refused here")
        return out
    if seconds > SOUND_LONGEST.get(kind, 0):
        out["reason"] = f"{SOUND_NAMES[kind]} is at most {SOUND_LONGEST[kind]:g} s here, the longest measured"
        return out
    fixed, per_second = SOUND_ESTIMATE[kind]
    need = int(fixed + per_second * float(seconds))
    left = available_mb - need - ERROR_MB
    out.update(fits=left >= floor, need_mb=need, left_mb=left)
    if not out["fits"]:
        who = "GPU" if on_card else "machine"
        out["reason"] = (f"{SOUND_NAMES[kind]} of {seconds:g} s needs about {need / 1000:.0f} GB at its peak (give or take "
                         f"{ERROR_MB / 1000:g}); this {who} has {available_mb / 1000:.0f} GB for it and keeps "
                         f"{floor / 1000:.0f} GB free as its floor, so it is refused. Something outside the studio holds the rest")
        note = held_note() if measured_here and not on_card else ""
        out["reason"] += "." + note if note else ""
    return out

# Character Swap (app/swap.py, spark/spark_swap.py): SAM 3.1, the anchor's picture model (FLUX3-K Fast) and MiniMax H3
# Ref2VA with its two LoRAs in one ComfyUI, the windows at most 124 frames however long the part, so its peak is one per
# quality. Measured 2026-10-27 through the app on a DGX Spark (GB10, 121.7 GB), a 5 s part of an 864x480 clip with one
# person, int8 Ref2VA, each from a stopped ComfyUI, MemAvailable and every CUDA process's memory sampled once a second;
# the need is what the machine had before the job less its lowest point (the GPU's share is in it: one pool): 480p
# 55.4 GiB (347 s), 768p (1376x768) 60.6 GiB (811 s).
SWAP_ESTIMATE = {480: 56_800, 768: 62_100}
# On a card the models are on the card. Measured the same way under a CUDA MPS limit of 96 GB (an RTX Pro 6000 imitated,
# STUDIO_VRAM_GB=96): the studio's processes' peak on the card, 49.4 GiB at 480p and 52.7 GiB at 768p (the machine
# fell by 55.4 and 60.1, as on the Spark).
SWAP_CARD_ESTIMATE = {480: 50_600, 768: 54_000}
# With STUDIO_ROOM_MB on a card smaller than the models (docs/rtx-5090.md), ComfyUI keeps them in RAM and streams them
# to the card. Measured under a 32 GB limit (an RTX 5090 imitated) with STUDIO_ROOM_MB=84000: the machine's drop, the
# card's share included (one pool on the Spark it was measured on, so a card's own RAM is held to more than it uses):
# 480p 37.8 GiB with 32.1 GiB on the card (349 s), 768p 39.5 GiB with 32.1 on the card (800 s): ComfyUI no longer keeps
# every model resident, so the machine gives less than on a Spark.
SWAP_OFFLOAD_ESTIMATE = {480: 38_800, 768: 40_500}
# the bf16 Ref2VA (ComfyUI's own blocks on its 40.2 GB file) was not measured: its weights are 19.3 GB more than the
# int8 file's 21.0 GB, and they are held throughout, so that is added
SWAP_BF16_EXTRA_MB = 19_300
SWAP_NAMES = {480: "Fast (480p)", 768: "High (768p)"}


def card_name() -> str:
    """The studio's card as nvidia-smi names it ("" when it says nothing)."""

    found = smi("--query-gpu=name")
    return found[0][0] if found and found[0] else ""


def swap_check(quality: int, available_mb: int | None = None, total_mb: int | None = None, card: bool = False,
               weights: str = "int8") -> dict:
    """Whether a Character Swap at ``quality`` (480 or 768) fits this machine, as ``check`` answers for a clip: on a
    Spark the machine's measured peak, on a card the card's (or, with STUDIO_ROOM_MB, the RAM's with the models
    offloaded). A refusal names the GPU and what fits instead. Everything fits where there is no /proc/meminfo."""

    if total_mb is None:
        total_mb = meminfo().get("MemTotal", 0)
    if not total_mb:
        return {"fits": True, "reason": "", "route": None}
    measured_here = available_mb is None and not card
    available_mb, floor, on_card = _pool(available_mb, total_mb, card)
    offloaded = not on_card and room_budget_mb() is not None and discrete()
    table = SWAP_CARD_ESTIMATE if on_card else SWAP_OFFLOAD_ESTIMATE if offloaded else SWAP_ESTIMATE
    extra = SWAP_BF16_EXTRA_MB if weights == "bf16" else 0

    def need_of(q: int) -> int:
        return table[q] + extra

    def fits(q: int) -> bool:
        return available_mb - need_of(q) - ERROR_MB >= floor

    need = need_of(quality)
    left = available_mb - need - ERROR_MB
    out = {"fits": left >= floor, "reason": "", "need_mb": need, "error_mb": ERROR_MB, "left_mb": left, "floor_mb": floor,
           "available_mb": available_mb, "route": f"swap-{quality}" + ("-card" if on_card else "-offload" if offloaded else ""),
           "rows": None, "nearest": None}
    if out["fits"]:
        return out
    named = card_name() if on_card or offloaded else ""
    who = (f"this GPU ({named})" if named else "this GPU") if on_card else "this machine"
    where = ("of the card's memory with the models on the card" if on_card else
             "of RAM with ComfyUI keeping the models in RAM (STUDIO_ROOM_MB)" if offloaded else "at its peak")
    said = (f"Character Swap at {SWAP_NAMES.get(quality, quality)} needs about {need / 1000:.0f} GB {where} (give or take "
            f"{ERROR_MB / 1000:g}); {who} has {available_mb / 1000:.0f} GB for it and keeps {floor / 1000:.0f} GB free as its "
            "floor, so it is refused")
    smaller = [q for q in sorted(SWAP_NAMES) if q < quality and fits(q)]
    if smaller:
        out["nearest"] = {"quality": smaller[-1]}
        said += f". {SWAP_NAMES[smaller[-1]]} fits: choose it in the panel"
    elif on_card:
        said += (". On a card smaller than the models, set STUDIO_ROOM_MB (docs/rtx-5090.md) and ComfyUI keeps them in RAM "
                 "and streams them to the card")
    out["reason"] = said + "."
    if measured_here and not on_card:
        out["reason"] += held_note()
    return out


# ---------- layer 2: the floor under a running server ----------

def verdict(samples: list[tuple[float, int]], floor: int, stalled: float = 0.0) -> str:
    """Why the server must stop now ("" while it may run). ``samples`` are (time, MB available), newest last;
    ``stalled`` is the share of the last STALL_WINDOW_S in which every task waited on memory. A stall stops the
    server only under twice the floor available: loading the models from a cold start (the text encoder, then
    42 GB of H3 staged from the page cache) has the kernel reclaim cache for seconds, which it counts as waiting
    on memory, with half the machine still available (a 2-minute clip was stopped so with 62 GB available, and
    ran to the end without the stop). Running out is caught by the floor and the rate of the fall."""

    now, free = samples[-1]
    if free < floor:
        return f"{free} MB available, under the floor of {floor} MB"
    before = next(((t, m) for t, m in samples if now - t <= RATE_WINDOW_S), samples[-1])
    if now - before[0] >= RATE_WINDOW_S * 0.6:
        fall = (before[1] - free) / (now - before[0])
        if fall > 0 and free - fall < floor / 3:
            return (f"{free} MB available and falling by {fall:.0f} MB a second, which is under {floor // 3} MB within a second "
                    f"(the floor is {floor} MB)")
    if STALL_SHARE and stalled >= STALL_SHARE and free < 2 * floor:
        return f"the machine spent {100 * stalled:.0f} % of the last {STALL_WINDOW_S:g} s waiting on memory, with {free} MB available"
    return ""


class Meter:
    """The machine's memory and the kernel's memory stall time, read without a new process or a new file each time."""

    def __init__(self) -> None:
        self.info = os.open("/proc/meminfo", os.O_RDONLY)
        try:
            self.pressure = os.open("/proc/pressure/memory", os.O_RDONLY)
        except OSError:
            self.pressure = None

    def read(self) -> dict:
        """MB: {"available", "free", "cache"} (cache: file pages, which the kernel can give back)."""

        found = {}
        for line in os.pread(self.info, 4096, 0).split(b"\n"):
            name, _, rest = line.partition(b":")
            if name in (b"MemAvailable", b"MemFree", b"Cached", b"Shmem"):
                found[name] = int(rest.split()[0]) // 1024
        return {"available": found[b"MemAvailable"], "free": found[b"MemFree"], "cache": found[b"Cached"] - found.get(b"Shmem", 0)}

    def stall_us(self) -> int | None:
        if self.pressure is None:
            return None
        try:
            for line in os.pread(self.pressure, 512, 0).split(b"\n"):
                if line.startswith(b"full"):
                    return int(line.rsplit(b"total=", 1)[1])
        except (OSError, ValueError, IndexError):
            pass
        return None


def model_files(pid: int | None = None) -> list[str]:
    """The large files a render reads: the model folders (SPARK_MODELS, ComfyUI's own), links followed, and whatever
    the server has mapped."""

    prefix = os.environ.get("PREFIX") or os.path.expanduser("~/.local/opt/tensorfold-studio")
    roots = [os.environ.get("SPARK_MODELS") or os.path.expanduser("~/spark-studio-models"), os.path.join(prefix, "ComfyUI", "models")]
    found = set()
    for root in roots:
        for folder, _, names in os.walk(root, followlinks=True):
            for name in names:
                path = os.path.realpath(os.path.join(folder, name))
                try:
                    if os.path.getsize(path) >= 64 << 20:
                        found.add(path)
                except OSError:
                    pass
    if pid:
        try:
            with open(f"/proc/{pid}/maps") as handle:
                for line in handle:
                    path = line.split(None, 5)[-1].strip() if line.count(" ") >= 5 else ""
                    if path.startswith("/") and path.endswith((".safetensors", ".tf-int8")) and os.path.isfile(path):
                        found.add(path)
        except OSError:
            pass
    return sorted(found)


def release_cache(paths: list[str]) -> int:
    """Give the page cache of these files back to the machine (no privilege needed; pages a process has mapped and
    pages not yet written stay). Returns how many files were done."""

    done = 0
    for path in paths:
        try:
            handle = os.open(path, os.O_RDONLY)
            try:
                os.posix_fadvise(handle, 0, 0, os.POSIX_FADV_DONTNEED)
                done += 1
            finally:
                os.close(handle)
        except OSError:
            pass
    return done


def alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def watch(pid: int, floor: int, say=print) -> int:
    """Stand beside the server ``pid`` until it ends: keep free memory from being crowded out by the model files'
    page cache, and stop the server when verdict() says so. Returns 3 when it stopped it."""

    import threading

    try:  # this process must keep running when memory is short: its own pages stay in memory
        ctypes.CDLL(None, use_errno=True).mlockall(1)
    except (OSError, AttributeError):
        pass
    try:  # layer 3: if the kernel has to end something, it is the render server
        with open(f"/proc/{pid}/oom_score_adj", "w") as handle:
            handle.write("1000")
    except OSError:
        pass
    meter, samples, stalls, releasing, released = Meter(), [], [], [None], [0.0, 0]
    keep = keep_free_mb(floor)
    try:  # what the machine had before this server took anything: room_mb() reads it while the server runs
        scratch = state_file() + ".tmp"
        with open(scratch, "w") as handle:
            handle.write(f"{meter.read()['available']} {pid}\n")
        os.replace(scratch, state_file())
    except OSError:
        pass
    say(f"[tensorfold] memory floor: watching every {int(POLL_S * 1000)} ms; the server is stopped under {floor} MB available, "
        f"and the model files' page cache is given back when under {keep} MB is free", flush=True)

    def release() -> None:
        card = device()
        if card and not card["simulated"]:  # the page cache is the GB10's trouble (one memory for both); a card has its
            return                         # own. A Spark pretending to be a card (STUDIO_VRAM_GB) is still a GB10.
        before = meter.read()["free"]
        count = release_cache(model_files(pid))
        released[1] += 1
        if released[1] <= 3:  # said the first few times: after that it is routine
            say(f"[tensorfold] memory floor: {before} MB free with the rest in the page cache: gave back the cache of {count} "
                f"model files, {meter.read()['free']} MB free now", flush=True)

    while alive(pid):
        now, mem = time.monotonic(), meter.read()
        samples.append((now, mem["available"]))
        while now - samples[0][0] > 1.0:
            samples.pop(0)
        stall, stalled = meter.stall_us(), 0.0
        if stall is not None:
            stalls.append((now, stall))
            while now - stalls[0][0] > STALL_WINDOW_S:
                stalls.pop(0)
            if now - stalls[0][0] >= STALL_WINDOW_S * 0.9:
                stalled = (stall - stalls[0][1]) / 1e6 / (now - stalls[0][0])
        why = verdict(samples, floor, stalled)
        if why:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            say(f"[tensorfold] memory floor: {why}: stopping ComfyUI", flush=True)
            started = time.monotonic()
            while alive(pid) and time.monotonic() - started < 30:
                time.sleep(0.05)
            time.sleep(0.5)
            say(f"[tensorfold] memory floor: the server is gone after {time.monotonic() - started:.1f} s, {meter.read()['available']} MB available", flush=True)
            return 3
        # The GPU driver takes its memory from what is free, not from what is available: with the model files in
        # the page cache it was refused memory (NV_ERR_NO_MEMORY in the kernel log) at 2 GB free and 54 GB available.
        if mem["free"] < keep and mem["cache"] > keep and now - released[0] > 1.0 and not (releasing[0] and releasing[0].is_alive()):
            released[0] = now
            releasing[0] = threading.Thread(target=release, daemon=True)
            releasing[0].start()
        time.sleep(POLL_S)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    kinds = parser.add_subparsers(dest="kind", required=True)
    w = kinds.add_parser("watch", help="stand beside a ComfyUI server (spark/comfy.sh does this)")
    w.add_argument("--pid", type=int, required=True)
    w.add_argument("--floor", type=int, default=0, help="MB; default SPARK_MEMORY_FLOOR_MB, else a tenth of the machine")
    c = kinds.add_parser("check", help="whether a clip fits this machine")
    c.add_argument("--width", type=int, default=864); c.add_argument("--height", type=int, default=480)
    c.add_argument("--frames", type=int, default=124)
    c.add_argument("--lora", action="store_true"); c.add_argument("--references", action="store_true")
    c.add_argument("--base", action="store_true"); c.add_argument("--no-engine", action="store_true")
    c.add_argument("--pictures", type=int, default=0); c.add_argument("--reference-frames", type=int, default=0)
    kinds.add_parser("sound", help="whether sound effects and foley (their longest) fit this machine")
    args = parser.parse_args(argv)
    if args.kind == "watch":
        return watch(args.pid, args.floor or floor_mb())
    if args.kind == "sound":
        for kind, longest in SOUND_LONGEST.items():
            found = sound_check(kind, longest)
            if found.get("route") is None:
                print("no /proc/meminfo here: this guard is for a DGX Spark")
                return 0
            said = (f"about {found['need_mb'] / 1000:.1f} GB at {longest:g} s, " if found["need_mb"] else "") + ("fits" if found["fits"] else "REFUSED")
            print(f"{SOUND_NAMES[kind]}: {said}" + ("" if found["fits"] else f": {found['reason']}"))
        return 0
    found = check(args.width, args.height, args.frames, args.lora, args.references, args.base, not args.no_engine,
                  args.pictures, args.reference_frames)
    if found.get("route") is None:
        print("no /proc/meminfo here: this guard is for a DGX Spark")
        return 0
    print(f"{args.width}x{args.height}, {says(args.frames)}, {found['rows']} rows on the {found['route']} route: about "
          f"{found['need_mb'] / 1000:.1f} GB (give or take {found['error_mb'] / 1000:g}), {found['left_mb'] / 1000:.1f} GB left of "
          f"{found['available_mb'] / 1000:.1f}, floor {found['floor_mb'] / 1000:.1f} GB: " + ("fits" if found["fits"] else "REFUSED"))
    if not found["fits"]:
        print(found["reason"])
    return 0 if found["fits"] else 1


if __name__ == "__main__":
    sys.exit(main())
