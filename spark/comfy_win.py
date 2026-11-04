"""The ComfyUI server behind the Studio on native Windows (the CUDA tier, app/windows_cuda.py): spark/comfy.sh in Python.

    python spark/comfy_win.py start     # in the background, on 127.0.0.1:8190 (spark_generate.py does this itself)
    python spark/comfy_win.py stop      # frees the models' memory
    python spark/comfy_win.py status

As comfy.sh does on a DGX Spark: TensorFold's H3 node is copied into ComfyUI's custom_nodes with TF_H3_LIB pointing at
the engine (tf_h3.dll), the server runs at a low priority, and a memory guard stands beside it for as long as it runs.
The guard is the Windows one (app/windows_memory.py): a sampler process reads Windows' commit and low-memory signals
and the CUDA pool (cudaMemGetInfo, NVML's dedicated counters) four times a second, and ``windows_memory.pressure``
decides, with NVML's dedicated memory held to DEDICATED_FLOOR_MB beside it; when it names a reason the server is stopped (the log says ``memory guard: ... stopping ComfyUI``, which
spark_generate.py reports as the reason the render ended). SPARK_MEMORY_FLOOR_MB adds a floor of free physical memory.

Ownership is by handle, never by name or port: ``run`` holds ComfyUI and the sampler in a job object of its own
(app/windows_process.py, killed when the job closes), and records its own process id and creation time
(comfy-win.json beside comfy.log). ``stop`` asks that process to end (a stop file), and terminates it only when the
recorded identity still matches; another ComfyUI on the port (one a person started by hand) is never touched.
``start`` leaves the job object of whoever started it (CREATE_BREAKAWAY_FROM_JOB): the server stays warm after the
render that started it, as comfy.sh's setsid does on Linux.

Environment: PREFIX (%LOCALAPPDATA%\\ImaginationStudio), SPARK_COMFY_PORT (8190), SPARK_COMFY_ARGS (default
--use-ck-attention), TF_H3_LIB, SPARK_MEMORY_FLOOR_MB.
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent / "app"
sys.path.insert(0, str(APP))
PREFIX = Path(os.environ.get("PREFIX") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ImaginationStudio")
PORT = (os.environ.get("SPARK_COMFY_PORT") or "8190").strip()
STEM = "comfy" if PORT == "8190" else f"comfy-{PORT}"
COMFY_LOG, COMFY_KIND = PREFIX / f"{STEM}.log", PREFIX / f"{STEM}.kind"
STATE = PREFIX / f"{STEM}-win.json"
STOP = PREFIX / f"{STEM}-win.stop"
SAMPLE_WAIT_S = 60.0  # the sampler's first reading (it imports torch and opens a CUDA context)
SAMPLE_STALE_S = 5.0  # a sampler silent this long is replaced by the plain host-memory loop
COMMIT_FLOOR_MB = 4096  # Windows commit headroom kept: under it any allocation on the machine may fail
DEDICATED_FLOOR_MB = 512  # NVML's dedicated GPU memory kept free (past it WDDM spills into shared system memory)
BELOW_NORMAL = 0x4000  # BELOW_NORMAL_PRIORITY_CLASS: comfy.sh's nice, without starving the GPU's feeder thread
DETACHED, NEW_GROUP, BREAKAWAY = 0x8, 0x200, 0x01000000


# ---------- process identity: an id and its creation time, never a name ----------

def _kernel():
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(ctypes.c_uint64)] * 4
    api.GetProcessTimes.restype = wintypes.BOOL
    api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.GetExitCodeProcess.restype = wintypes.BOOL
    api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.TerminateProcess.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.GetCurrentProcess.restype = wintypes.HANDLE
    return api


def created(handle) -> int:
    api = _kernel()
    times = [ctypes.c_uint64() for _ in range(4)]
    if not api.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
        return 0
    return times[0].value


def own_identity() -> dict:
    return {"pid": os.getpid(), "created": created(_kernel().GetCurrentProcess())}


def open_recorded(identity: dict, terminate: bool = False):
    """A handle to the recorded process when it is still that process (same id, same creation time), else None."""

    api = _kernel()
    rights = 0x1000 | 0x100000 | (0x1 if terminate else 0)  # QUERY_LIMITED_INFORMATION, SYNCHRONIZE, TERMINATE
    try:
        pid, when = int(identity["pid"]), int(identity["created"])
    except (KeyError, TypeError, ValueError):
        return None
    handle = api.OpenProcess(rights, False, pid)
    if not handle:
        return None
    from ctypes import wintypes
    code = wintypes.DWORD()
    if created(handle) != when or not api.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
        api.CloseHandle(handle)
        return None
    return handle


def recorded() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def running() -> bool:
    handle = open_recorded(recorded())
    if handle is None:
        return False
    _kernel().CloseHandle(handle)
    return True


def answers() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/system_stats", timeout=2):
            return True
    except OSError:
        return False


# ---------- start, stop, status ----------

def start() -> int:
    if running():
        print("already running")
        return 0
    python = Path(sys.executable)
    if not python.is_file() or not (PREFIX / "ComfyUI" / "main.py").is_file():
        print(f"run oneshot-setup-rtx-spark-n1x.ps1 -Native first: nothing at {PREFIX}", file=sys.stderr)
        return 1
    COMFY_KIND.unlink(missing_ok=True)
    STOP.unlink(missing_ok=True)
    PREFIX.mkdir(parents=True, exist_ok=True)
    argv = [str(python), "-X", "utf8", str(Path(__file__).resolve()), "run"]
    with open(COMFY_LOG, "wb") as log:
        try:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=str(PREFIX),
                             creationflags=DETACHED | NEW_GROUP | BREAKAWAY, close_fds=True)
        except OSError:
            # the job this runs in does not let a child leave it: the server then ends with that job (a later render
            # starts it again and loads the models again), which is safe, only slower
            log.write(b"[tensorfold] ComfyUI could not leave its starter's job object: it stops when that job ends\n")
            log.flush()
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=str(PREFIX),
                             creationflags=DETACHED | NEW_GROUP, close_fds=True)
    print(f"ComfyUI starting on 127.0.0.1:{PORT} (log: {COMFY_LOG})")
    return 0


def stop() -> int:
    identity = recorded()
    handle = open_recorded(identity)
    api = _kernel()
    if handle is None:
        STATE.unlink(missing_ok=True)
        COMFY_KIND.unlink(missing_ok=True)
        print("not running")
        return 0
    STOP.write_text(str(identity.get("pid")), encoding="utf-8")
    ended = ctypes.windll.kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 25_000) == 0
    api.CloseHandle(handle)
    if not ended:
        # it did not end by itself: terminated only while it is still the recorded process; its job object then closes,
        # which ends ComfyUI and the sampler with it (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        handle = open_recorded(identity, terminate=True)
        if handle is not None:
            api.TerminateProcess(handle, 1)
            ctypes.windll.kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 10_000)
            api.CloseHandle(handle)
    STOP.unlink(missing_ok=True)
    STATE.unlink(missing_ok=True)
    COMFY_KIND.unlink(missing_ok=True)
    print("stopped")
    return 0


def status() -> int:
    print(f"running on 127.0.0.1:{PORT}" if running() else "not running")
    return 0


# ---------- run: the server and its guard ----------

def install_nodes(env: dict) -> None:
    """TensorFold's H3 node (and the Studio's MMAudio sampler beside kijai's pack), as comfy.sh copies them."""

    nodes = PREFIX / "ComfyUI" / "custom_nodes"
    lib = Path(os.environ.get("TF_H3_LIB") or PREFIX / "tf-h3" / "tf_h3.dll")
    if lib.is_file():
        target = nodes / "tensorfold_h3"
        target.mkdir(parents=True, exist_ok=True)
        for source in sorted((HERE / "tensorfold_h3").glob("*.py")):
            part = target / (source.name + f".{os.getpid()}")
            shutil.copyfile(source, part)
            os.replace(part, target / source.name)
        env["TF_H3_LIB"] = str(lib)
    shutil.rmtree(nodes / "studio_split_h3", ignore_errors=True)  # two cards are never paired here
    if (nodes / "ComfyUI-MMAudio" / "nodes.py").is_file() and (HERE / "studio_mmaudio" / "__init__.py").is_file():
        (nodes / "studio_mmaudio").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE / "studio_mmaudio" / "__init__.py", nodes / "studio_mmaudio" / "__init__.py")


def say(line: str) -> None:
    print(f"[tensorfold] {line}", flush=True)


class Guard:
    """windows_memory's sampler beside the server, its readings judged by windows_memory.pressure."""

    def __init__(self, job, floor: str):
        import windows_memory
        self.memory = windows_memory
        self.floor = floor
        self.latest = None
        self.at = 0.0
        self.error = ""
        self.process = job.spawn([sys.executable, "-X", "utf8", str(APP / "windows_memory.py"), "--watch", "--device-index", "0"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                 encoding="utf-8", creationflags=NEW_GROUP)
        threading.Thread(target=self.read, daemon=True).start()

    def read(self) -> None:
        for line in self.process.stdout:
            try:
                data = json.loads(line)
                if "error" in data:
                    self.error = str(data["error"])[:300]
                    return
                snapshot = self.memory.Snapshot(**data)
                snapshot.validate()
            except (TypeError, ValueError) as error:
                self.error = f"the sampler's reading was not valid: {error}"[:300]
                return
            self.latest, self.at = snapshot, time.monotonic()

    def reason(self, previous) -> str:
        """windows_memory.pressure on the latest reading, held to floors rather than to its 0.5-second projection: a
        model load commits tens of GB in one step (TensorFold's int8 H3 copy, 21 GB, as a video loaded after a picture
        on an N1X), which the projection read as commit falling to nothing and stopped the load with 19 GB still free."""

        s = self.latest
        if s is None:
            return ""
        said = self.memory.pressure(s, self.floor)
        if not said and s.host_commit_limit_mb - s.host_commit_used_mb < COMMIT_FLOOR_MB:
            said = (f"Windows commit headroom {s.host_commit_limit_mb - s.host_commit_used_mb} MiB is under the "
                    f"{COMMIT_FLOOR_MB} MiB floor")
        # on WDDM cudaMemGetInfo is this sampler's own budget (it stayed at 105.7 of 113.1 GB free while ComfyUI held
        # 58 GB, measured on an N1X): NVML's dedicated counter is the card's, so it is held to a floor too
        if not said and s.gpu_dedicated_free_mb < DEDICATED_FLOOR_MB:
            said = f"dedicated GPU memory {s.gpu_dedicated_free_mb} MiB is under the {DEDICATED_FLOOR_MB} MiB floor"
        if said:
            said += (f" (CUDA {s.gpu_free_mb} MiB free, dedicated {s.gpu_dedicated_free_mb}, host {s.host_free_mb}, "
                     f"commit {s.host_commit_used_mb} of {s.host_commit_limit_mb})")
        return said


def host_reason(floor_mb: int) -> str:
    """The plain loop's check when the sampler cannot run: Windows' available physical memory against the floor."""

    import windows_memory
    _, available = windows_memory.host_memory()
    if available < floor_mb:
        return f"available memory {available} MB is under the floor of {floor_mb} MB"
    return ""


def run() -> int:
    from windows_process import OwnedJob

    env = dict(os.environ)
    env.pop("STUDIO_GPU_JOB_KEY", None)  # a warm server belongs to the GPU session, not to the job that loaded it
    env.update(STUDIO_GPU_MODEL_RAM="1", MAX_JOBS=env.get("MAX_JOBS", "2"), PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    install_nodes(env)
    STATE.write_text(json.dumps({**own_identity(), "port": PORT}), encoding="utf-8")
    flags = (os.environ.get("SPARK_COMFY_ARGS") or "--use-ck-attention").split()
    job = OwnedJob()
    try:
        server = job.spawn([sys.executable, "-X", "utf8", "main.py", "--listen", "127.0.0.1", "--port", PORT, *flags],
                           cwd=str(PREFIX / "ComfyUI"), env=env, stdin=subprocess.DEVNULL, creationflags=BELOW_NORMAL | NEW_GROUP)
        floor = (os.environ.get("SPARK_MEMORY_FLOOR_MB") or "").strip()
        try:
            guard = Guard(job, floor)
        except (OSError, RuntimeError) as error:
            guard = None
            say(f"memory guard: the sampler could not start ({error}); holding a floor of free memory with a plain loop")
        plain = int(floor) if floor.isdigit() and int(floor) > 0 else 2048
        began, previous, told = time.monotonic(), None, 0.0
        while True:
            code = server.poll()
            if code is not None:
                say(f"ComfyUI ended (status {code})")
                return code
            if STOP.exists():
                job.terminate()
                server.wait(10)
                say("ComfyUI stopped")
                return 0
            why = ""
            if guard is not None:
                fresh = guard.latest is not None and time.monotonic() - guard.at < SAMPLE_STALE_S
                waiting = guard.latest is None and time.monotonic() - began < SAMPLE_WAIT_S
                if fresh:
                    why = guard.reason(previous)
                    previous = guard.latest
                    if time.monotonic() - told >= 60:  # a reading a minute in the log: what the guard saw
                        told = time.monotonic()
                        s = guard.latest
                        say(f"memory guard: CUDA {s.gpu_free_mb} of {s.gpu_total_mb} MB free (dedicated {s.gpu_dedicated_free_mb} of "
                            f"{s.gpu_dedicated_total_mb}), host {s.host_free_mb} of "
                            f"{s.host_total_mb} MB, commit {s.host_commit_used_mb} of {s.host_commit_limit_mb} MB")
                elif not waiting:
                    say("memory guard: the sampler stopped answering (" + (guard.error or "no reading") +
                        f"); holding {plain} MB of free memory with a plain loop")
                    guard = None
            if guard is None:
                why = host_reason(plain)
            if why:
                say(f"memory guard: {why}: stopping ComfyUI")
                job.terminate()
                server.wait(10)
                return 3
            time.sleep(0.05)
    finally:
        job.close()
        STATE.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if sys.platform != "win32":
        print("comfy_win.py is the native Windows port of spark/comfy.sh: use spark/comfy.sh here", file=sys.stderr)
        return 2
    action = argv[0] if argv else "start"
    actions = {"start": start, "stop": stop, "status": status, "run": run}
    if action not in actions:
        print("usage: comfy_win.py start|stop|status", file=sys.stderr)
        return 2
    return actions[action]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
