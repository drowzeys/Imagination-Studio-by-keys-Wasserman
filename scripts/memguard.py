#!/usr/bin/env python3
"""Imagination Studio's machine memory guard: a small watchdog, installed by the one-shot setup, that acts before the
kernel's OOM killer or a hard hang does.

Why: a DGX Spark hung hard during a render (2026-11-08) with three things resident at once: a separate LLM server
(22 GB) an agent had started, the FP4 video worker (52 GB) and the app. Another Spark was OOM-rebooted by a ComfyUI
run outside the Studio's guard. The app guards what it starts itself (app/memory_manager.py, spark/memory_guard.py);
this guard watches the whole machine, whatever started what, and tells the Studio to give memory back in time.

What it reads, every second (half a second on Linux), every 0.1 s near the floor, while memory falls over 1 GB a
second or while the kernel reports any memory stall (on 2026-11-08 the GB10's driver logged NV_ERR_NO_MEMORY and the
Spark hung within two seconds, past a guard that read MemAvailable every few seconds with a 12 GB floor):
  Linux    MemAvailable and its fall over the last second, MemFree against the page cache (the GB10's driver takes
           what is free, not what is available: the model files' cache is given back, as spark/memory_guard.py does),
           the kernel's memory stall time (/proc/pressure/memory: "some avg10" is the early sign, "full avg10" counts as
           under the floor) and, under strict overcommit (vm.overcommit_memory=2), the commit left
  macOS    vm_stat's free, file-backed and purgeable pages (what the kernel gets back without compressing) and the
           kernel's memory pressure level (kern.memorystatus_vm_pressure_level: 2 warn, 4 critical)
  Windows  GlobalMemoryStatusEx: available physical memory and the commit left (the page file's available)

The floor is the Studio's own (spark/memory_guard.py floor_mb): STUDIO_MEMGUARD_FLOOR_MB, else a tenth of the machine,
never under 8 GB. What it does, in this order, each with a cooldown, and every action logged:
  0. free memory crowded out by the page cache (Linux): the model files' cache is given back (posix_fadvise).
  1. under 1.5 floors, falling toward the floor within two seconds, a memory stall over 10 % (PSI some) or macOS
     says "warn": POST /api/memory/release, so the Studio unloads its IDLE modules (the
     voice and song engines, ComfyUI's cached models, the Bolt brain). The running render is never touched there.
  2. under the floor (or the commit left is under it, or the machine waits on memory over 20 % of the time): with
     --stop-foreign only, the largest of the person's own model servers that match STOP_PATTERNS (vLLM, llama.cpp's
     server, Ollama, SGLang, LM Studio, mlx_lm.server, a separate ComfyUI ...) is asked to stop (SIGTERM; SIGKILL after
     10 s). Off by default: the guard never stops a program the Studio did not start unless the person said so. A
     process run from the Studio's checkout or install is never one of them.
  3. under half the floor (or falling there within a second) for 1 s, or macOS says "critical": the last resort, the render running on this machine is
     cancelled through the Studio's API (POST /api/jobs/<id>/cancel), so the machine itself survives. --no-cancel
     leaves even that to the kernel.

    python3 scripts/memguard.py status                     # thresholds, a reading now, installed or not, the last log lines
    python3 scripts/memguard.py install [--stop-foreign] [--dry-run]   # systemd user unit / launchd agent / logon task
    python3 scripts/memguard.py uninstall [--dry-run]      # everything install made, gone
    python3 scripts/memguard.py run [--stop-foreign] [--once]          # the watchdog itself (what the service runs)

The install is per user and needs no administrator (Windows' logon task excepted, which the one-shot runs elevated):
  Linux    ~/.config/systemd/user/imagination-studio-memguard.service, enabled with systemctl --user. A user unit runs
           while the person is logged in; `loginctl enable-linger` keeps it running without a login (said, not done).
  macOS    ~/Library/LaunchAgents/com.imagination-studio.memguard.plist, loaded with launchctl bootstrap gui/<uid>.
  Windows  the Task Scheduler task "Imagination Studio Memory Guard", at logon, running a .cmd in
           %LOCALAPPDATA%\\ImaginationStudio (Task Scheduler's command line is limited to 261 characters).
Each also places STUDIO-AGENT.md (how an agent renders through the Studio) in the Studio's folder and, when Claude Code
is set up on this machine (~/.claude), as the skill ~/.claude/skills/imagination-studio/SKILL.md. A manifest lists what
was written; uninstall removes exactly that. Logs: memguard.log in the state folder `status` names.
Standard library only: it runs on the system's Python 3.9 or newer.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
UNIT = "imagination-studio-memguard.service"
MAC_LABEL = "com.imagination-studio.memguard"
WIN_TASK = "Imagination Studio Memory Guard"
WARN_FACTOR, CRITICAL_FACTOR = 1.5, 0.5
STALL_PCT = 20.0           # the kernel's "full" memory stall share over 10 s that counts as under the floor
SOME_PCT = 10.0            # its "some" share (a task waited on memory): the early sign, the Studio gives back idle modules
FAST_POLL_S = 0.1          # the poll near the floor, while memory falls fast or while the kernel reports a stall
CRITICAL_GRACE_S = 1.0     # half the floor this long before a render is cancelled (under 6 GB on a Spark nothing passes)
COOLDOWN = {"release": 10.0, "release_cache": 1.0, "stop_foreign": 30.0, "cancel_render": 60.0}
STOP_WAIT_S = 10.0
# the person's own model servers the guard may stop with --stop-foreign (matched in the command line, any case);
# STUDIO_MEMGUARD_STOP_PATTERNS (comma separated) replaces the list
STOP_PATTERNS = ("vllm", "llama-server", "llama_server", "llama.cpp", "ollama", "sglang", "lm studio", "lmstudio", "lms server",
                 "mlx_lm.server", "mlx_lm server", "text-generation-launcher", "tabbyapi", "koboldcpp", "exllama",
                 "comfyui/main.py", "comfyui\\main.py")


# ---------- where things go ----------

def state_dir(platform: str | None = None, home: Path | None = None) -> Path:
    platform = platform or sys.platform
    home = home or Path.home()
    if platform == "darwin":
        return home / "Library" / "Logs" / "ImaginationStudio"
    if platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "ImaginationStudio"
    return Path(os.environ.get("XDG_STATE_HOME") or home / ".local" / "state") / "imagination-studio"


def studio_folder() -> Path:
    sys.path.insert(0, str(HERE / "app"))
    try:
        from studio_home import studio_home  # noqa: PLC0415

        return studio_home()
    except Exception:  # noqa: BLE001 - the default when the app's helper cannot be read
        return Path(os.environ.get("STUDIO_HOME") or Path.home() / "ImaginationStudio")


# ---------- a reading ----------

def parse_meminfo(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        if rest.strip():
            try:
                out[name] = int(rest.split()[0]) // 1024
            except ValueError:
                pass
    return out


def parse_psi(text: str, kind: str = "full") -> float | None:
    """/proc/pressure/memory's avg10 for ``kind`` ("some": at least one task waited on memory, "full": all did)."""

    for line in text.splitlines():
        if line.startswith(kind):
            match = re.search(r"avg10=([0-9.]+)", line)
            return float(match.group(1)) if match else None
    return None


def parse_vm_stat(text: str, total_mb: int) -> int | None:
    """As app/telemetry.py parse_vm_stat: free, file-backed and purgeable pages (MB)."""

    size, pages = 16384, {}
    for line in text.splitlines():
        if "page size of" in line:
            try:
                size = int(line.split("page size of")[1].split()[0])
            except (IndexError, ValueError):
                pass
        name, _, value = line.partition(":")
        if value.strip().rstrip(".").isdigit():
            pages[name.strip()] = int(value.strip().rstrip("."))
    names = (("Pages free", "File-backed pages", "Pages purgeable") if "File-backed pages" in pages
             else ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"))
    return min(total_mb, sum(pages.get(n, 0) for n in names) * size // 2 ** 20) if pages else None


def _read(path: str) -> str:
    try:
        with open(path) as handle:
            return handle.read()
    except OSError:
        return ""


def reading() -> dict:
    """{"available_mb", "total_mb", "commit_left_mb", "pressure" (normal, warn, critical), "stall_pct"}."""

    out = {"available_mb": None, "total_mb": None, "commit_left_mb": None, "pressure": "normal", "stall_pct": None,
           "some_pct": None, "free_mb": None, "cache_mb": None}
    if sys.platform == "darwin":
        def sysctl(name: str) -> str:
            try:
                return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=2).stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                return ""
        total = int(sysctl("hw.memsize") or 0) // 2 ** 20
        try:
            said = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=2).stdout
        except (OSError, subprocess.TimeoutExpired):
            said = ""
        level = sysctl("kern.memorystatus_vm_pressure_level")
        out.update(total_mb=total or None, available_mb=parse_vm_stat(said, total) if total else None,
                   pressure={"2": "warn", "4": "critical"}.get(level, "normal"))
    elif sys.platform == "win32":
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        *[(name, ctypes.c_ulonglong) for name in
                          ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]]
        status = Status()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            out.update(total_mb=status.total >> 20, available_mb=status.available >> 20, commit_left_mb=status.page_available >> 20)
    else:
        info = parse_meminfo(_read("/proc/meminfo"))
        psi = _read("/proc/pressure/memory")
        out.update(total_mb=info.get("MemTotal"), available_mb=info.get("MemAvailable"), stall_pct=parse_psi(psi, "full"),
                   some_pct=parse_psi(psi, "some"), free_mb=info.get("MemFree"),
                   cache_mb=info.get("Cached", 0) - info.get("Shmem", 0) if "Cached" in info else None)
        if _read("/proc/sys/vm/overcommit_memory").strip() == "2" and "CommitLimit" in info:
            out["commit_left_mb"] = info["CommitLimit"] - info.get("Committed_AS", 0)
    return out


def thresholds(total_mb: int, floor_mb: int | None = None) -> dict:
    asked = os.environ.get("STUDIO_MEMGUARD_FLOOR_MB", "").strip()
    floor = floor_mb or (int(asked) if asked.isdigit() and int(asked) > 0 else max(8000, (total_mb or 0) // 10))
    return {"warn_mb": int(floor * WARN_FACTOR), "floor_mb": floor, "critical_mb": int(floor * CRITICAL_FACTOR)}


def level_of(now: dict, limits: dict, fall_mb_s: float = 0.0) -> str:
    """ok, low (give back the Studio's idle modules), floor (and, opted in, the person's own servers) or critical.
    ``fall_mb_s`` is how fast the available memory fell over the last second: a fall that would reach the critical
    line within a second is critical now (a GB10's driver was refused memory, NV_ERR_NO_MEMORY, and the Spark hung
    within two seconds of a load starting: a poll of the level alone sees it too late)."""

    available, commit = now.get("available_mb"), now.get("commit_left_mb")
    if available is None:
        return "ok"
    if (available < limits["critical_mb"] or now.get("pressure") == "critical" or (commit is not None and commit < limits["critical_mb"])
            or (fall_mb_s > 0 and available - fall_mb_s < limits["critical_mb"])):
        return "critical"
    if (available < limits["floor_mb"] or (commit is not None and commit < limits["floor_mb"])
            or (now.get("stall_pct") or 0) >= STALL_PCT and available < 2 * limits["floor_mb"]):
        return "floor"
    if (available < limits["warn_mb"] or now.get("pressure") == "warn"
            or (now.get("some_pct") or 0) >= SOME_PCT and available < 2 * limits["floor_mb"]
            or (fall_mb_s > 0 and available - 2 * fall_mb_s < limits["floor_mb"])):
        return "low"
    return "ok"


def cache_crowding(now: dict, limits: dict) -> bool:
    """A GB10's GPU driver takes memory from what is free, not from what is available: with the model files in the
    page cache it was refused memory (NV_ERR_NO_MEMORY) at 2 GB free and 54 GB available (spark/memory_guard.py).
    True when free memory is under two thirds of the floor with more than that in the page cache."""

    free, cache = now.get("free_mb"), now.get("cache_mb")
    return free is not None and cache is not None and free < limits["floor_mb"] * 2 // 3 and cache > limits["floor_mb"] * 2 // 3


def poll_s(now: dict, limits: dict, fall_mb_s: float, base: float) -> float:
    """Fast (FAST_POLL_S) near the floor, while memory falls fast or the kernel reports any memory stall: a model
    load takes 7 to 30 GB a second on a Spark, so a reading every few seconds misses the whole fall."""

    available = now.get("available_mb")
    if available is None:
        return base
    near = available < 2 * limits["floor_mb"] or fall_mb_s > 1024 or (now.get("some_pct") or 0) > 0 or cache_crowding(now, limits)
    return min(base, FAST_POLL_S) if near else base


def decide(level: str, memo: dict, now_s: float, stop_foreign: bool, cancel: bool, crowded: bool = False) -> list[str]:
    """The actions for this reading, each held to its cooldown (``memo`` keeps when each last ran). ``crowded``: the
    page cache crowds out free memory (cache_crowding), which is given back first whatever the level."""

    if level != "critical":
        memo.pop("critical_since", None)
    wanted = ["release_cache"] if crowded else []
    if level == "ok":
        actions = [a for a in wanted if now_s - memo.get(a, -1e9) >= COOLDOWN[a]]
        for action in actions:
            memo[action] = now_s
        return actions
    wanted.append("release")
    if level in ("floor", "critical") and stop_foreign:
        wanted.append("stop_foreign")
    if level == "critical" and cancel:
        since = memo.setdefault("critical_since", now_s)
        if now_s - since >= CRITICAL_GRACE_S:
            wanted.append("cancel_render")
    actions = [a for a in wanted if now_s - memo.get(a, -1e9) >= COOLDOWN[a]]
    for action in actions:
        memo[action] = now_s
    return actions


# ---------- the person's own processes ----------

def processes() -> list[dict]:
    """{"pid", "mb", "command"} of every process this user can see."""

    out = []
    if sys.platform.startswith("linux"):
        for name in os.listdir("/proc"):
            if name.isdigit():
                status = _read(f"/proc/{name}/status")
                match = re.search(r"^VmRSS:\s+(\d+)", status, re.M)
                try:
                    with open(f"/proc/{name}/cmdline", "rb") as handle:
                        command = handle.read().replace(b"\0", b" ").decode(errors="replace").strip()
                except OSError:
                    continue
                if match and command:
                    out.append({"pid": int(name), "mb": int(match.group(1)) // 1024, "command": command})
    elif sys.platform == "darwin":
        try:
            said = subprocess.run(["ps", "-axo", "pid=,rss=,command="], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            said = ""
        for line in said.splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                out.append({"pid": int(parts[0]), "mb": int(parts[1]) // 1024, "command": parts[2]})
    elif sys.platform == "win32":
        try:
            said = subprocess.run(["powershell", "-NoProfile", "-Command",
                                   "Get-CimInstance Win32_Process | Select-Object ProcessId,WorkingSetSize,CommandLine | ConvertTo-Json -Compress"],
                                  capture_output=True, text=True, timeout=30).stdout
            rows = json.loads(said or "[]")
        except (OSError, subprocess.TimeoutExpired, ValueError):
            rows = []
        for row in rows if isinstance(rows, list) else [rows]:
            if row.get("ProcessId") and row.get("CommandLine"):
                out.append({"pid": int(row["ProcessId"]), "mb": int(row.get("WorkingSetSize") or 0) >> 20, "command": row["CommandLine"]})
    return out


def patterns() -> tuple:
    asked = os.environ.get("STUDIO_MEMGUARD_STOP_PATTERNS", "").strip()
    return tuple(p.strip().lower() for p in asked.split(",") if p.strip()) if asked else STOP_PATTERNS


def studio_markers() -> list[str]:
    prefix = os.environ.get("PREFIX") or str(Path.home() / ".local" / "opt" / "tensorfold-studio")
    out = [str(HERE), prefix]
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        out.append(str(Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio"))
    return [m.lower() for m in out]


def pick_foreign(rows: list[dict], wanted: tuple, markers: list[str], me: int) -> dict | None:
    """The largest process that matches one of ``wanted`` and is not the Studio's own (run from its checkout or
    install) nor this guard."""

    found = [row for row in rows if row["pid"] != me and any(p in row["command"].lower() for p in wanted)
             and not any(m and m in row["command"].lower() for m in markers)]
    return max(found, key=lambda row: row["mb"]) if found else None


def stop(pid: int) -> bool:
    """SIGTERM (Windows: taskkill), then SIGKILL (taskkill /F) after STOP_WAIT_S. True when it is gone."""

    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True, timeout=30)
        time.sleep(STOP_WAIT_S)
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30)
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return True
    ends = time.monotonic() + STOP_WAIT_S
    while time.monotonic() < ends:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.25)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    return True


# ---------- the Studio ----------

class Studio:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")

    def key(self) -> str:
        if os.environ.get("STUDIO_KEY"):
            return os.environ["STUDIO_KEY"].strip()
        try:
            return (studio_folder() / "access-key").read_text().strip()
        except OSError:
            return ""

    def call(self, method: str, path: str, body=None, timeout: float = 15):
        data = json.dumps(body).encode() if body is not None else None
        head = {"Content-Type": "application/json"} if data is not None else {}
        if key := self.key():
            head["X-Studio-Key"] = key
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=head)
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return json.loads(answer.read() or b"null")

    def release(self, why: str) -> list:
        return self.call("POST", "/api/memory/release", {"why": why}, timeout=120).get("released") or []

    def renders(self) -> list[dict]:
        jobs = self.call("GET", "/api/jobs")
        jobs = jobs.get("jobs", []) if isinstance(jobs, dict) else jobs or []
        return [j for j in jobs if j.get("status") == "running" and not j.get("remote") and not j.get("spark")]

    def cancel(self, job_id: str) -> None:
        self.call("POST", f"/api/jobs/{job_id}/cancel", {})


# ---------- the watchdog ----------

def spark_guard():
    """spark/memory_guard.py (its release_cache and model_files), or None off Linux."""

    if not sys.platform.startswith("linux"):
        return None
    sys.path.insert(0, str(HERE / "spark"))
    try:
        import memory_guard  # noqa: PLC0415

        return memory_guard
    except Exception:  # noqa: BLE001
        return None


class Log:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __call__(self, text: str) -> None:
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + text
        print(line, flush=True)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > 1 << 20:
                self.path.replace(self.path.with_suffix(".log.1"))
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass


def act(actions: list[str], now: dict, limits: dict, studio: Studio, say) -> None:
    gb = lambda mb: f"{(mb or 0) / 1024:.1f} GB"  # noqa: E731
    said = (f"{gb(now['available_mb'])} available (warn {gb(limits['warn_mb'])}, floor {gb(limits['floor_mb'])}, "
            f"critical {gb(limits['critical_mb'])})" + (f", commit left {gb(now['commit_left_mb'])}" if now.get("commit_left_mb") is not None else "")
            + (f", pressure {now['pressure']}" if now.get("pressure") != "normal" else "")
            + (f", memory stall {now['stall_pct']:.0f} %" if now.get("stall_pct") else ""))
    for action in actions:
        try:
            if action == "release_cache":
                guard = spark_guard()
                count = guard.release_cache(guard.model_files()) if guard else 0
                say(f"release_cache: {said}, {gb(now.get('free_mb'))} free: gave back the page cache of {count} model files")
            elif action == "release":
                done = studio.release(f"the machine guard: {said}")
                say(f"release: {said}: the Studio unloaded " + (", ".join(one.get("label", "?") for one in done) or "nothing (nothing idle)"))
            elif action == "stop_foreign":
                one = pick_foreign(processes(), patterns(), studio_markers(), os.getpid())
                if one is None:
                    say(f"stop_foreign: {said}: none of the person's own model servers is running (patterns: {', '.join(patterns())})")
                else:
                    say(f"stop_foreign: {said}: stopping pid {one['pid']} ({gb(one['mb'])}): {one['command'][:160]}")
                    stop(one["pid"])
            elif action == "cancel_render":
                renders = studio.renders()
                for job in renders:
                    studio.cancel(job["id"])
                    say(f"cancel_render: {said}: LAST RESORT, cancelled the render {job['id']} ({job.get('kind')}, {job.get('title') or ''}) so the machine survives")
                if not renders:
                    say(f"cancel_render: {said}: no render runs on this machine; the kernel decides from here")
        except (urllib.error.URLError, OSError, ValueError, subprocess.SubprocessError) as error:
            say(f"{action}: {said}: the Studio did not answer at {studio.url} ({error})")


def run(args) -> int:
    say = Log(state_dir() / "memguard.log")
    studio = Studio(args.url)
    poll = args.poll or (0.5 if sys.platform.startswith("linux") else 1.0)
    first = reading()
    limits = thresholds(first.get("total_mb") or 0, args.floor_mb)
    say(f"start: watching every {poll:g} s; warn {limits['warn_mb']} MB, floor {limits['floor_mb']} MB, critical "
        f"{limits['critical_mb']} MB; stop the person's own servers: {'yes' if args.stop_foreign else 'no'}; cancel a render "
        f"as the last resort: {'no' if args.no_cancel else 'yes'}; Studio {studio.url}")
    if sys.platform.startswith("linux") and not args.once:
        try:  # its pages now stay in memory (MCL_CURRENT, as memory_guard.watch): it must keep running when memory is short
            import ctypes

            ctypes.CDLL(None, use_errno=True).mlockall(1)
        except (OSError, AttributeError):
            pass
    memo: dict = {}
    seen: list = []  # (time, available) over the last second, for the rate of the fall
    while True:
        now, at = reading(), time.monotonic()
        if now.get("available_mb") is not None:
            seen.append((at, now["available_mb"]))
        while seen and at - seen[0][0] > 1.0:
            seen.pop(0)
        fall = (seen[0][1] - seen[-1][1]) / max(at - seen[0][0], 0.05) if len(seen) >= 2 and at - seen[0][0] >= 0.2 else 0.0
        actions = decide(level_of(now, limits, fall), memo, at, args.stop_foreign, not args.no_cancel, cache_crowding(now, limits))
        if actions:
            act(actions, now, limits, studio, say)
        if args.once:
            return 0
        time.sleep(poll_s(now, limits, fall, poll))


# ---------- install and uninstall ----------

def guard_argv(python: str, url: str, stop_foreign: bool) -> list[str]:
    return [python, str(HERE / "scripts" / "memguard.py"), "run", "--url", url] + (["--stop-foreign"] if stop_foreign else [])


def systemd_quote(word: str) -> str:
    return '"' + word.replace("\\", "\\\\").replace('"', '\\"') + '"' if re.search(r'[\s"\\$%]', word) else word


def unit_text(argv: list[str]) -> str:
    return ("[Unit]\nDescription=Imagination Studio memory guard (gives memory back before the OOM killer acts)\n\n"
            "[Service]\nType=simple\nExecStart=" + " ".join(systemd_quote(w).replace("%", "%%") for w in argv) + "\n"
            "Restart=always\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n")


def plist_text(argv: list[str], log: Path) -> str:
    def esc(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    args = "".join(f"\n    <string>{esc(w)}</string>" for w in argv)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0">\n<dict>\n'
            f"  <key>Label</key>\n  <string>{MAC_LABEL}</string>\n  <key>ProgramArguments</key>\n  <array>{args}\n  </array>\n"
            "  <key>RunAtLoad</key>\n  <true/>\n  <key>KeepAlive</key>\n  <true/>\n  <key>ProcessType</key>\n  <string>Interactive</string>\n"
            f"  <key>StandardOutPath</key>\n  <string>{esc(str(log))}</string>\n  <key>StandardErrorPath</key>\n  <string>{esc(str(log))}</string>\n"
            "</dict>\n</plist>\n")


def cmd_text(argv: list[str]) -> str:
    return "@echo off\r\nrem Imagination Studio memory guard (scripts\\memguard.py), started at logon by Task Scheduler\r\n" + \
        subprocess.list2cmdline(argv) + "\r\n"


def agent_note_targets(platform: str, home: Path) -> list[Path]:
    out = [studio_folder() / "STUDIO-AGENT.md"]
    if (home / ".claude").is_dir():
        out.append(home / ".claude" / "skills" / "imagination-studio" / "SKILL.md")
    return out


def plan(action: str, platform: str, home: Path, python: str, url: str, stop_foreign: bool) -> list[tuple]:
    """What install or uninstall does on ``platform``: ("write", path, text), ("remove", path) and ("run", argv)."""

    state = state_dir(platform, home)
    argv = guard_argv(python, url, stop_foreign)
    steps: list[tuple] = []
    if platform == "darwin":
        where, uid = home / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist", str(os.getuid()) if hasattr(os, "getuid") else "501"
        if action == "install":
            steps += [("run", ["launchctl", "bootout", f"gui/{uid}/{MAC_LABEL}"], True), ("write", where, plist_text(argv, state / "memguard.log")),
                      ("run", ["launchctl", "bootstrap", f"gui/{uid}", str(where)], False)]
        else:
            steps += [("run", ["launchctl", "bootout", f"gui/{uid}/{MAC_LABEL}"], True), ("remove", where)]
    elif platform == "win32":
        cmd = state / "memguard.cmd"
        if action == "install":
            pythonw = python[:-len("python.exe")] + "pythonw.exe" if python.lower().endswith("python.exe") else python  # no console
            steps += [("write", cmd, cmd_text(guard_argv(pythonw, url, stop_foreign))),
                      ("run", ["schtasks", "/Create", "/TN", WIN_TASK, "/SC", "ONLOGON", "/RL", "LIMITED", "/F", "/TR", f'"{cmd}"'], False),
                      ("run", ["schtasks", "/Run", "/TN", WIN_TASK], True)]
        else:
            steps += [("run", ["schtasks", "/End", "/TN", WIN_TASK], True), ("run", ["schtasks", "/Delete", "/TN", WIN_TASK, "/F"], True),
                      ("remove", cmd)]
    else:
        where = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "systemd" / "user" / UNIT
        if action == "install":
            steps += [("write", where, unit_text(argv)), ("run", ["systemctl", "--user", "daemon-reload"], False),
                      ("run", ["systemctl", "--user", "enable", "--now", UNIT], False),
                      ("run", ["systemctl", "--user", "restart", UNIT], False)]
        else:
            steps += [("run", ["systemctl", "--user", "disable", "--now", UNIT], True), ("remove", where),
                      ("run", ["systemctl", "--user", "daemon-reload"], True)]
    note = (HERE / "STUDIO-AGENT.md").read_text(encoding="utf-8") if (HERE / "STUDIO-AGENT.md").is_file() else ""
    for target in agent_note_targets(platform, home):
        steps.append(("write", target, note) if action == "install" else ("remove", target))
    manifest = state / "memguard-installed.json"
    steps.append(("write", manifest, json.dumps({"platform": platform, "files": [str(s[1]) for s in steps if s[0] == "write"],
                                                 "stop_foreign": stop_foreign, "url": url, "at": int(time.time())}, indent=1) + "\n")
                 if action == "install" else ("remove", manifest))
    return steps


def apply(steps: list[tuple], dry_run: bool, say=print) -> int:
    """Carry out a plan (``dry_run``: say it, change nothing). A "run" step marked True may fail (what it undoes is not
    there); any other failure stops the plan with its message and exit 1."""

    for step in steps:
        if step[0] == "write":
            say(f"  write {step[1]}" + (f"\n{step[2]}" if dry_run and step[2] and len(step[2]) < 1500 else ""))
            if not dry_run:
                Path(step[1]).parent.mkdir(parents=True, exist_ok=True)
                Path(step[1]).write_text(step[2], encoding="utf-8")
        elif step[0] == "remove":
            say(f"  remove {step[1]}")
            if not dry_run:
                try:
                    Path(step[1]).unlink()
                except FileNotFoundError:
                    pass
        else:
            argv, may_fail = step[1], step[2]
            say("  run " + " ".join(shlex.quote(w) for w in argv))
            if dry_run:
                continue
            try:
                done = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            except (OSError, subprocess.TimeoutExpired) as error:
                done = None
                why = str(error)
            else:
                why = (done.stderr or done.stdout).strip()[-300:]
            if (done is None or done.returncode) and not may_fail:
                say(f"  ! that failed: {why}")
                return 1
    return 0


def status(args) -> int:
    now = reading()
    limits = thresholds(now.get("total_mb") or 0, args.floor_mb)
    state = state_dir()
    manifest = state / "memguard-installed.json"
    print(f"memory: {now}")
    print(f"thresholds: {limits} -> now {level_of(now, limits)}")
    print(f"installed: {'yes (' + str(manifest) + ')' if manifest.is_file() else 'no'}")
    log = state / "memguard.log"
    if log.is_file():
        print("last log lines:\n" + "".join(log.read_text(encoding="utf-8", errors="replace").splitlines(True)[-10:]))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("action", choices=("run", "install", "uninstall", "status"))
    parser.add_argument("--url", default=os.environ.get("STUDIO_URL") or f"http://127.0.0.1:{os.environ.get('PORT', '7870')}")
    parser.add_argument("--stop-foreign", action="store_true", default=os.environ.get("STUDIO_MEMGUARD_STOP_FOREIGN") == "1",
                        help="under the floor, stop the largest of the person's own model servers (off by default)")
    parser.add_argument("--no-cancel", action="store_true", help="never cancel a render, even as the last resort")
    parser.add_argument("--floor-mb", type=int, default=0)
    parser.add_argument("--poll", type=float, default=0.0)
    parser.add_argument("--once", action="store_true", help="run: one reading and its actions, then exit")
    parser.add_argument("--dry-run", action="store_true", help="install/uninstall: say what would be done, change nothing")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)
    if args.action == "run":
        return run(args)
    if args.action == "status":
        return status(args)
    steps = plan(args.action, sys.platform, Path.home(), args.python, args.url, args.stop_foreign)
    print(("Would " if args.dry_run else "") + ("install" if args.action == "install" else "remove") + " the memory guard:")
    code = apply(steps, args.dry_run)
    if code == 0 and not args.dry_run:
        if args.action == "install":
            print(f"The memory guard runs now and at every login. Log: {state_dir() / 'memguard.log'}. "
                  f"Undo: python3 {HERE / 'scripts' / 'memguard.py'} uninstall")
            if sys.platform.startswith("linux"):
                print("It runs while you are logged in; `loginctl enable-linger $USER` keeps it running without a login.")
        else:
            print("The memory guard is removed.")
    elif code and args.action == "install" and sys.platform.startswith("linux"):
        print("No systemd user session here (an ssh login without one, a container): run it yourself, e.g. "
              f"nohup python3 {HERE / 'scripts' / 'memguard.py'} run &")
    return code


if __name__ == "__main__":
    sys.exit(main())
