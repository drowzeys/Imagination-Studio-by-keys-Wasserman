"""Install a weight the studio needs and this machine does not have, from Models & weights, with one click.

Every need in app/discover.py says how it is downloaded (`download`, words written for a person). This turns those
words into the installer that does it (`plan_of`): the setup script of its engine (scripts/setup-*.sh), the one-shot
installer with the option that adds it (oneshot-setup.sh --no-render, so nothing is rendered), a Hugging Face
download into the place the studio looks, or the LoRA hub's own Install. What cannot be run (a build from source,
a file a library fetches on first use) is shown as it is.

Before anything is downloaded the studio scans the machine, so a copy already here is linked instead (discover.py);
Install checks the free disk against the size the need names, runs one installer at a time with its output on the
page, never as root (stdin is closed: a password prompt fails), and when the output says a Hugging Face repo is
gated it hands the page the gate (app/hf_gate.py) so the person accepts the licence and presses Install again.
Standard library only at the top, like discover.py.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import signal
import subprocess
import threading
import time
from pathlib import Path

PACK = Path(__file__).resolve().parent.parent
TIMEOUT = 8 * 60 * 60  # a 70 GB model on a slow line
SCRIPTS = {"--music": "scripts/setup-music.sh", "--voice": "scripts/setup-voice.sh", "--flux3": "scripts/setup-flux3.sh",
           "--motion": "scripts/setup-motion.sh", "--sound": "scripts/setup-sound.sh"}
ONESHOT_FLAGS = ("--image-edit", "--ref2va", "--turbo", "--image-only")
# a DGX Spark installs every option through oneshot-setup-spark.sh (the Mac's scripts/setup-*.sh are not its own)
SPARK = sys.platform.startswith("linux")
SPARK_FLAGS = ("--ref2va", "--h3-base", "--image-edit", "--flux3", "--moss", "--music", "--bolt", "--sound", "--motion", "--voice",
               "--image-only")
GATED = re.compile(r"GatedRepoError|is gated|gated repo|Access to model .* is restricted|accept (?:its|the) licen[cs]e|401 Client Error|403 Client Error", re.I)
REPO = re.compile(r"huggingface\.co/(?!api/|settings/|datasets/|spaces/)([A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*)")
lock = threading.Lock()
job: dict = {"need": None}


def size_of(words: str) -> float | None:
    found = re.search(r"(\d+(?:\.\d+)?)\s*GB", words or "")
    return float(found.group(1)) if found else None


def plan_of(item: dict) -> dict:
    """How this need is installed: {how: script | oneshot | hf | lora | first_use | manual, command, shown, size_gb}."""

    words = str(item.get("download") or "").strip()
    out = {"how": "manual", "shown": words, "size_gb": size_of(words), "command": None}
    if item["id"].startswith("lora:"):
        return {**out, "how": "lora", "lora": item["id"][5:], "shown": "the LoRA hub's Install"}
    if re.search(r"on first use", words):
        return {**out, "how": "first_use"}
    hf = re.match(r"hf download ([A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*)$", words)
    if hf:
        target = str(item["expected"] if item["kind"] == "folder" else Path(item["expected"]).parent)
        return {**out, "how": "hf", "repo": hf.group(1), "command": ["hf", "download", hf.group(1), "--local-dir", target],
                "shown": f"hf download {hf.group(1)} --local-dir {target}"}
    script = re.match(r"(?:bash )?((?:scripts|spark)/[a-z0-9-]+\.sh)\b(.*)$", words)
    if script and (PACK / script.group(1)).is_file():
        args = script.group(2).split()
        args = [a for a in args if a.startswith("--")]
        return {**out, "how": "script", "command": ["bash", str(PACK / script.group(1)), *args], "shown": " ".join(["bash", script.group(1), *args])}
    oneshot = re.match(r"(?:bash )?oneshot-setup(?:-spark)?\.sh((?:\s+--[a-z0-9-]+)*)", words)
    if oneshot and SPARK:
        flags = [f for f in oneshot.group(1).split() if f in SPARK_FLAGS]
        return {**out, "how": "oneshot", "command": ["bash", str(PACK / "oneshot-setup-spark.sh"), "--no-render", *flags],
                "shown": " ".join(["bash oneshot-setup-spark.sh --no-render", *flags])}
    if oneshot:
        flags = oneshot.group(1).split()
        own = next((SCRIPTS[f] for f in flags if f in SCRIPTS), None)
        if own and (PACK / own).is_file():  # the engine's own setup, not the whole one-shot install
            return {**out, "how": "script", "command": ["bash", str(PACK / own)], "shown": "bash " + own}
        flags = [f for f in flags if f in ONESHOT_FLAGS]
        return {**out, "how": "oneshot", "command": ["bash", str(PACK / "oneshot-setup.sh"), "--no-render", *flags],
                "shown": " ".join(["bash oneshot-setup.sh --no-render", *flags])}
    return out


def free_gb(place: Path) -> float | None:
    place = Path(place)
    while not place.exists() and place != place.parent:
        place = place.parent
    try:
        return shutil.disk_usage(place).free / 1e9
    except OSError:
        return None


def gate_of_output(lines: list[str]) -> dict | None:
    text = "\n".join(lines)
    if not GATED.search(text):
        return None
    repo = next(iter(REPO.findall(text)), None)
    if not repo:
        return {"gated": "unknown", "access": "accept", "page": None}
    try:
        import hf_gate
        return {**hf_gate.repo_gate(repo), "repo": repo}
    except Exception:  # noqa: BLE001 - the page still says which repo and what to do
        return {"gated": "unknown", "repo": repo, "page": f"https://huggingface.co/{repo}", "access": "accept"}


def start(item: dict, env: dict | None = None) -> dict:
    """Run the need's installer in the background; one at a time. Raises ValueError with the reason it cannot."""

    plan = plan_of(item)
    if plan["how"] in ("manual", "first_use", "lora") or not plan["command"]:
        raise ValueError({"first_use": "This one comes by itself the first time it is used.",
                          "lora": "Install LoRAs from the LoRA hub (Looks)."}.get(plan["how"], "This one is installed by hand: " + (plan["shown"] or "see the README")))
    if plan["how"] == "hf" and not shutil.which("hf"):
        raise ValueError("Hugging Face's `hf` command is not installed here: pip install -U huggingface_hub")
    need_gb, have_gb = plan["size_gb"], free_gb(Path(item["expected"]))
    if need_gb and have_gb is not None and have_gb < need_gb * 1.1:
        raise ValueError(f"{item['label']} needs about {need_gb:g} GB and the disk has {have_gb:.0f} GB free: make room first "
                         "(Machine → Storage shows where it went).")
    with lock:
        if job.get("process") is not None and job["process"].poll() is None:
            raise ValueError(f"{job['label']} is installing: wait for it to finish")
        job.clear()
        job.update(need=item["id"], label=item["label"], shown=plan["shown"], lines=[], started=time.time(), state="running",
                   size_gb=need_gb, expected=str(item["expected"]))
    run_env = {**os.environ, **(env or {}), "NONINTERACTIVE": "1", "HOMEBREW_NO_AUTO_UPDATE": "1"}
    run_env.pop("SUDO_ASKPASS", None)
    try:
        process = subprocess.Popen(plan["command"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   env=run_env, cwd=str(PACK), start_new_session=True)
    except OSError as error:
        with lock:
            job.update(state="failed", reason=f"could not start the installer: {error}")
        raise ValueError(f"could not start the installer: {error}")
    with lock:
        job["process"] = process
    threading.Thread(target=follow, args=(process,), daemon=True).start()
    return public()


def follow(process) -> None:
    timer = threading.Timer(TIMEOUT, lambda: os.killpg(process.pid, signal.SIGTERM))
    timer.daemon = True
    timer.start()
    try:
        for line in process.stdout:
            line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]|\r", "", line).strip()
            line = re.sub(r"hf_[A-Za-z0-9]{20,}", "hf_[hidden]", line)
            if line:
                with lock:
                    job["lines"] = (job["lines"] + [line[:300]])[-80:]
        process.wait()
    finally:
        timer.cancel()
    with lock:
        job["process"] = None
        job["finished"] = time.time()
        if job.get("cancelled"):
            job.update(state="failed", reason="Cancelled.")
        elif process.returncode == 0:
            job["state"] = "done"
        else:
            gate = gate_of_output(job["lines"])
            last = job["lines"][-1] if job["lines"] else ""
            job.update(state="gated" if gate else "failed", gate=gate,
                       reason=(last + " · " if last else "") + f"the installer stopped (exit {process.returncode})")
    try:
        import discover  # the new files in place: look again, so the page shows them and loaders find them
        discover.start_scan()
    except Exception:  # noqa: BLE001
        pass


def cancel() -> dict:
    with lock:
        process = job.get("process")
        if process is not None and process.poll() is None:
            job["cancelled"] = True
            os.killpg(process.pid, signal.SIGTERM)
    return public()


def public() -> dict:
    with lock:
        return {k: v for k, v in job.items() if k != "process"} if job.get("need") else {"need": None, "state": "idle"}
