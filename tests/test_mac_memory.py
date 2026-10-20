"""A Mac's memory as the Studio reads it (telemetry.parse_vm_stat, voice.mem_available_gb) and the check before a Mac
ComfyUI job (server.mac_comfy_room). On 2026-10-20 Remove a person ran on a Mac where ollama held 114 GB: 15 MB was
free, 57 GB stood "inactive" (counted as available then), the window server stalled 125 s and macOS restarted."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))

import server  # noqa: E402
import telemetry  # noqa: E402
from fastapi import HTTPException  # noqa: E402


@pytest.fixture
def mac(monkeypatch):
    memory = {"gb": 220.0, "held_kb": ""}
    monkeypatch.setattr(server, "SPARK", False)
    monkeypatch.setattr(server.voice, "mem_available_gb", lambda: memory["gb"])
    monkeypatch.setattr(server, "mac_heaviest", lambda *a, **k: "ollama 114 GB, tensorfold-native 34 GB")

    def run(command, **kwargs):  # pgrep finds the Studio's ComfyUI only when it holds memory; ps gives its RSS
        out = ("4242" if memory["held_kb"] else "") if command[0] == "pgrep" else memory["held_kb"]
        return type("Done", (), {"stdout": out, "returncode": 0})()
    monkeypatch.setattr(server.subprocess, "run", run)
    return memory


def test_a_job_that_does_not_fit_is_refused_and_says_who_holds_the_memory(mac):
    mac["gb"] = 106.0  # the panic's Mac: ollama's 114 GB and Bolt's 34 GB beside it
    with pytest.raises(HTTPException) as refused:
        server.mac_comfy_room("remove")
    assert refused.value.status_code == 400 and "135 GB" in refused.value.detail and "ollama 114 GB" in refused.value.detail


def test_a_job_that_fits_starts_and_what_comfyui_holds_counts_as_room(mac):
    server.mac_comfy_room("remove")  # 220 GB: Bolt alone beside it
    mac["gb"], mac["held_kb"] = 40.0, str(118 * 1024 ** 2)  # ComfyUI still holds Upscale's models: they make way
    server.mac_comfy_room("remove")
    mac["held_kb"] = ""
    with pytest.raises(HTTPException):
        server.mac_comfy_room("remove")


def test_only_measured_comfy_jobs_on_a_mac_are_checked(mac, monkeypatch):
    mac["gb"] = 1.0
    server.mac_comfy_room("qwen")  # not a ComfyUI picture model on a Mac
    server.mac_comfy_room("turnaround")  # the MLX engine's
    monkeypatch.setattr(server, "SPARK", True)
    server.mac_comfy_room("remove")  # a Spark has its own guard


def test_inactive_pages_are_not_available_memory():
    panic = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 926.\nPages active: 3736622.\n"
             "Pages inactive: 3735636.\nPages speculative: 986.\nPages purgeable: 0.\nFile-backed pages: 6604.\n")
    assert telemetry.parse_vm_stat(panic, 262144) < 200  # MB: the panic's Mac had next to nothing left
    calm = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 9508271.\nPages inactive: 4944990.\n"
            "Pages purgeable: 22239.\nFile-backed pages: 4990056.\n")
    assert telemetry.parse_vm_stat(calm, 262144) > 200_000  # the same Mac idle, Bolt loaded: about 225 GB
