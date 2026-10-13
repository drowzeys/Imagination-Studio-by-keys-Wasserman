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
   when the kernel reports that work is stalled on memory. The render then fails with that reason.
3. The server is marked as the first process the kernel ends if memory runs out all the same (oom_score_adj).

    python3 spark/memory_guard.py check --width 1376 --height 768 --frames 124 --lora
    python3 spark/memory_guard.py watch --pid 1234 [--floor MB]

What was measured (2026-10-13, a 124.5 GB Spark with nothing else running, 118.7 GB available; MemAvailable sampled
ten times a second through whole jobs of the app; "need" is what the job took from the machine at its lowest point):

    TensorFold's int8 engine (a clip without a LoRA)             rows     need
      864x480,  124 frames                                       15.4K    57.6 GB
      1376x768, 124 frames                                       39.2K    62.2 GB (62.1 to 64.2 in a production run)
      1376x768, 192 frames, sampled every 11 s in that run       59.9K    67.1 GB
      1376x768, 243 frames, the same                             75.3K    69.0 to 70.9 GB
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

A clip with a LoRA (or the Ref2VA Turbo adapter, which is one) is refused above the largest size it was measured at
(MEASURED_ROWS, and a frame larger than MEASURED_PIXELS), whatever the line says. By the line the clip that took the Spark down (39.2K rows) needs 77 GB and
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
import sys
import time

FPS, STEP, BASE, SHORTEST, LONGEST = 24, 17, 5, 22, 362
# The sizes the app offers, smallest first: what "a smaller size" means when a clip does not fit.
SIZES = ((864, 480), (1280, 736), (1344, 768), (1376, 768))
# What an idle Spark keeps for itself (measured: 124.5 GB in all, 118.7 GB available with nothing running).
IDLE_MB = 6000
PORT = os.environ.get("SPARK_COMFY_PORT", "8190")
# route -> (MB with the models loaded and no rows, MB per thousand rows). See the table above.
ESTIMATES = {"int8": (54_500, 215), "bf16": (75_000, 47)}
ERROR_MB = 6000
# The most rows each route was measured at; a route with patched bfloat16 weights is refused above its own.
MEASURED_ROWS = {"int8": 75_336, "bf16": 34_960}
# ... and the largest frame it was measured at (1280x736): a wider frame with fewer of them is as unknown
MEASURED_PIXELS = {"bf16": 1280 * 736}
# the measured points themselves, (rows, MB): tests/test_memory_guard.py holds the lines to them
MEASURED = {"int8": ((15_390, 57_575), (39_216, 62_187), (39_216, 64_200), (59_856, 67_100), (75_336, 69_000), (75_336, 70_900)),
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


def state_file() -> str:
    prefix = os.environ.get("PREFIX") or os.path.expanduser("~/.local/opt/tensorfold-studio")
    return os.path.join(prefix, "comfy.mem")


def server_pid() -> int | None:
    """The ComfyUI server of spark/comfy.sh on this machine, if one runs (by its command line, as comfy.sh finds it)."""

    wanted = b"\x00".join([b"main.py", b"--listen", b"127.0.0.1", b"--port", PORT.encode(), b""])
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


def room_mb() -> int | None:
    """What this machine has for a render, with none of the Studio's own models counted against it: what is
    available now, or, while the ComfyUI server runs, what was available when it started (the guard beside it
    wrote that down). A server from before the guard has no such note: the machine is then taken as otherwise
    idle. None where there is no /proc/meminfo."""

    info = meminfo()
    now = info.get("MemAvailable")
    if now is None:
        return None
    try:
        with open(state_file()) as handle:
            before, pid = handle.read().split()[:2]
        if alive(int(pid)):
            return max(now, int(before))
    except (OSError, ValueError):
        pass
    return max(now, info["MemTotal"] - IDLE_MB) if server_pid() else now


# ---------- layer 1: the estimate ----------

def rows(width: int, height: int, frames: int, pictures: int = 0, reference_frames: int = 0) -> int:
    """The rows every pass carries: one per 32x32 pixels of each latent frame (17 frames of video are 5 latent
    frames, and the first 5 are 3). A reference picture is one more latent frame, a reference video its own."""

    per_frame = (width // 32) * (height // 32)
    latent = lambda count: 5 * max(0, (count - BASE) // STEP) + 3
    return per_frame * (latent(frames) + pictures + (latent(reference_frames) if reference_frames else 0))


def route_of(lora: bool = False, references: bool = False, base: bool = False, engine: bool = True) -> str:
    """Which weights a clip runs on: TensorFold's int8 engine, or ComfyUI's own blocks with the bfloat16 file (a
    LoRA, a clip from references, MiniMax H3's own transformer, or a machine without the engine)."""

    return "bf16" if lora or references or base or not engine else "int8"


def need_mb(route: str, count: int) -> int:
    fixed, per_thousand = ESTIMATES[route]
    return int(fixed + per_thousand * count / 1000)


def counts() -> list[int]:
    return list(range(SHORTEST, LONGEST + 1, STEP))


def says(frames: int) -> str:
    return f"{frames} frames ({frames / FPS:.1f} s)"


def check(width: int, height: int, frames: int, lora: bool = False, references: bool = False, base: bool = False,
          engine: bool = True, pictures: int = 0, reference_frames: int = 0, available_mb: int | None = None,
          total_mb: int | None = None) -> dict:
    """Whether this clip fits this machine. ``available_mb`` is what the machine has with none of the Studio's own
    models loaded (default: all of it less what an idle Spark keeps). Returns {"fits", "reason", "need_mb",
    "error_mb", "left_mb", "floor_mb", "available_mb", "route", "rows", "nearest"}; on a machine without
    /proc/meminfo everything fits (the Mac build has its own limits)."""

    if total_mb is None:
        total_mb = meminfo().get("MemTotal", 0)
    if not total_mb:
        return {"fits": True, "reason": "", "route": None}
    if available_mb is None:
        available_mb = room_mb() or total_mb - IDLE_MB
    floor = floor_mb(total_mb)
    route = route_of(lora, references, base, engine)
    patched = route == "bf16" and lora  # a LoRA or the Turbo adapter, merged into the bfloat16 weights as they load

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
    what = ("a clip with a LoRA" if lora else "a clip from references" if references else "this clip")
    how = "ComfyUI's own blocks with the full-precision (bfloat16) weights" if route == "bf16" else "TensorFold's int8 engine"
    if why == "unmeasured":
        over = (f"{width}x{height} is a larger frame than" if width * height > MEASURED_PIXELS[route]
                else f"{width}x{height}, {says(frames)} is {out['rows'] / 1000:.1f}K rows a pass, more than")
        said = (f"{what} renders on {how}, and {over} the largest such clip measured on a DGX Spark "
                f"(1280x736, 124 frames: {MEASURED_ROWS[route] / 1000:.1f}K rows a pass). Above that the memory it takes "
                "is not known, and one such clip (1376x768, 124 frames) took a Spark down, so it is refused")
    else:
        said = (f"{what} renders on {how} and needs about {need / 1000:.0f} GB at its peak (give or take {ERROR_MB / 1000:g}); this "
                f"machine has {available_mb / 1000:.0f} GB for it and keeps {floor / 1000:.0f} GB free as its floor, so it is refused")
    fit = [f"{says(shorter)} at this size" if shorter else "", f"{smaller[0]}x{smaller[1]} at this length" if smaller else ""]
    fit = " or ".join(bit for bit in fit if bit)
    out["reason"] = said + (f". What fits: {fit}." if fit else ". No smaller size or shorter length of this kind fits either.")
    if lora and why == "memory":
        out["reason"] += " Without the LoRA the clip runs on the int8 engine, which needs less."
    return out


# ---------- layer 2: the floor under a running server ----------

def verdict(samples: list[tuple[float, int]], floor: int, stalled: float = 0.0) -> str:
    """Why the server must stop now ("" while it may run). ``samples`` are (time, MB available), newest last;
    ``stalled`` is the share of the last STALL_WINDOW_S in which every task waited on memory."""

    now, free = samples[-1]
    if free < floor:
        return f"{free} MB available, under the floor of {floor} MB"
    before = next(((t, m) for t, m in samples if now - t <= RATE_WINDOW_S), samples[-1])
    if now - before[0] >= RATE_WINDOW_S * 0.6:
        fall = (before[1] - free) / (now - before[0])
        if fall > 0 and free - fall < floor / 3:
            return (f"{free} MB available and falling by {fall:.0f} MB a second, which is under {floor // 3} MB within a second "
                    f"(the floor is {floor} MB)")
    if STALL_SHARE and stalled >= STALL_SHARE:
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
    args = parser.parse_args(argv)
    if args.kind == "watch":
        return watch(args.pid, args.floor or floor_mb())
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
