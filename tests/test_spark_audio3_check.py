"""Worker-check safety and outputs; no real GPU, Linux runtime, or model is probed."""
import array
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import wave

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("spark_audio3_worker_check", ROOT / "scripts/check-spark-audio3.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def wav(path, seconds=1, channels=2, amplitude=1000):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(array.array("h", [amplitude] * seconds * 44100 * channels).tobytes())


def test_audio_checks_measure_the_required_shape_not_just_file_presence(tmp_path):
    path = tmp_path / "sound.wav"
    wav(path)
    result = check.validate_audio(path, 1)
    assert result["frames"] == 44100 and result["channels"] == 2 and result["rms"] > 0
    with pytest.raises(RuntimeError, match="exact-duration"):
        check.validate_audio(path, 5)
    wav(path, channels=1)
    with pytest.raises(RuntimeError, match="stereo"):
        check.validate_audio(path, 1)
    for amplitude in (0, 32767):
        wav(path, amplitude=amplitude)
        with pytest.raises(RuntimeError, match="silent or exceeds"):
            check.validate_audio(path, 1)


def test_preflight_does_not_probe_hardware_off_linux(monkeypatch):
    monkeypatch.setattr(check.sys, "platform", "win32")
    monkeypatch.setattr(check.subprocess, "run", lambda *a, **k: pytest.fail("No hardware probe off Linux"))
    with pytest.raises(RuntimeError, match="Linux"):
        check.preflight()


def test_preflight_counts_only_the_gpu_and_refuses_busy_or_custom_backends(monkeypatch):
    monkeypatch.setattr(check.sys, "platform", "linux")
    monkeypatch.delenv("STUDIO_GPUS", raising=False)
    monkeypatch.delenv("SPARK_GOVERNOR", raising=False)
    monkeypatch.delenv("SPARK_COMFY_ARGS", raising=False)
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: "0")
    monkeypatch.setattr(check.spark_audio3, "prerequisites", lambda: "")
    monkeypatch.setattr(check.memory_guard, "floor_mb", lambda: 12000)
    monkeypatch.setattr(check, "available_mb", lambda: 100000)
    busy = {"pids": ""}
    def query(command, **kwargs):
        assert kwargs["timeout"] == 10 and kwargs["check"]
        return SimpleNamespace(stdout="GPU-simulated, NVIDIA GB10\nNPU-simulated, NPU"
                               if "--query-gpu=uuid,name" in command else busy["pids"])
    monkeypatch.setattr(check.subprocess, "run", query)
    assert check.preflight() == "GPU-simulated"
    busy["pids"] = "321"
    with pytest.raises(RuntimeError, match="GPU is busy"):
        check.preflight()
    monkeypatch.setenv("SPARK_COMFY_ARGS", "--cpu")
    with pytest.raises(RuntimeError, match="custom backend"):
        check.preflight()


def test_watchdog_uses_exact_private_ownership_not_a_port_or_process_name(monkeypatch):
    identities = {12: "parent-start", 34: "child-start"}
    monkeypatch.setattr(check.local_gpus, "process_identity", lambda pid: identities.get(pid))
    monkeypatch.setattr(check.memory_guard, "floor_mb", lambda: 30000)
    family = {}
    monkeypatch.setattr(check.local_gpus, "marked_processes",
                        lambda name, key: {34: "child-start"} if (name, key) == ("STUDIO_AUDIO3_CHECK_OWNER", "private") else {})
    monkeypatch.setattr(check.local_gpus, "drain_processes", lambda found: family.update(found) or True)
    process = SimpleNamespace(pid=12, poll=lambda: None, wait=lambda timeout: 0)
    guard = check.Watchdog(process, "private", 100000)
    assert guard.floor == 30000
    guard.drain()
    assert family == identities
    identities[12] = "reused-pid"
    family.clear()
    guard.drain()
    assert family == {34: "child-start"}


def test_listener_ownership_and_missing_listener_fail_closed_without_signalling(monkeypatch):
    port = 43210
    state = {"listen": True}
    def tables(path, **kwargs):
        if not state["listen"]:
            return "header\n"
        return f"header\n0: 0100007F:{port:04X} 00000000:0000 0A 0 0 0 0 0 12345\n"
    def entries(path):
        return [Path("/proc/11")] if path.name == "proc" else [Path("/proc/11/fd/3")]
    monkeypatch.setattr(Path, "read_text", tables)
    monkeypatch.setattr(Path, "read_bytes", lambda path: b"STUDIO_AUDIO3_CHECK_OWNER=private\0")
    monkeypatch.setattr(Path, "iterdir", entries)
    monkeypatch.setattr(check.os, "readlink", lambda path: "socket:[12345]")
    monkeypatch.setattr(check.local_gpus, "signal_owned", lambda *a: pytest.fail("Listener checks never signal"))
    check.local_gpus.check_owned_listener(port, "STUDIO_AUDIO3_CHECK_OWNER", "private", require_listener=True)
    with pytest.raises(RuntimeError, match="unrelated"):
        check.local_gpus.check_owned_listener(port, "STUDIO_AUDIO3_CHECK_OWNER", "different", require_listener=True)
    state["listen"] = False
    with pytest.raises(RuntimeError, match="not present"):
        check.local_gpus.check_owned_listener(port, "STUDIO_AUDIO3_CHECK_OWNER", "private", require_listener=True)


@pytest.mark.parametrize("available", [24000, 80000, None])
def test_watchdog_stops_on_floor_growth_or_missing_counters(monkeypatch, available):
    monkeypatch.setattr(check.memory_guard, "floor_mb", lambda: 12000)
    monkeypatch.setattr(check.local_gpus, "process_identity", lambda pid: "start")
    guard = check.Watchdog(SimpleNamespace(pid=12, poll=lambda: None), "private", 100000)
    def current():
        if available is None:
            raise RuntimeError("missing counters")
        return available
    monkeypatch.setattr(check, "available_mb", current)
    stopped = []
    monkeypatch.setattr(guard, "drain", lambda: stopped.append(True))
    guard.watch()
    assert guard.failure and stopped == [True]


def test_main_records_worker_results_without_qualification_or_shared_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["check", "--worker", "--output", str(tmp_path / "report")])
    monkeypatch.setattr(check, "preflight", lambda: "GPU-test-only")
    def case(folder, device, seconds, prompt):
        assert device == "GPU-test-only" and folder == tmp_path / "report"
        return {"seconds": seconds, "maximum_system_available_drop_mb": 10000}
    monkeypatch.setattr(check, "check_case", case)
    assert check.main() == 0
    report = json.loads((tmp_path / "report/report.json").read_text())
    assert report["outcome"] == "passed" and [case["seconds"] for case in report["cases"]] == [1, 5]
    assert report["bounds"]["torch_allocator_mb"] == check.GPU_MB == 12288
    assert report["bounds"]["system_available_growth_mb"] == check.JOB_MB == 16384
    assert "qualification" not in report and "shared_measurements" not in report
    assert check.spark_audio3.MODEL not in check.memory_guard.SOUND_ESTIMATE


def test_failed_worker_run_is_explicit_and_preserves_existing_reports(tmp_path, monkeypatch):
    output = tmp_path / "check"
    monkeypatch.setattr(sys, "argv", ["check", "--worker", "--output", str(output)])
    monkeypatch.setattr(check, "preflight", lambda: "GPU-test-only")
    monkeypatch.setattr(check, "check_case", lambda *args: (_ for _ in ()).throw(RuntimeError("worker failed")))
    assert check.main() == 1
    report = (output / "report.json").read_text()
    assert json.loads(report)["outcome"] == "failed"
    assert check.main() == 1 and (output / "report.json").read_text() == report


def test_comfy_worker_path_keeps_the_standard_guard_and_reuses_the_native_allocator_cap():
    text = (ROOT / "spark/comfy.sh").read_text(encoding="utf-8")
    assert "STUDIO_AUDIO3_CALIBRATION" in text and "STUDIO_AUDIO3_CHECK_OWNER" in text
    assert "windows-comfy-calibration-launcher.py" in text and "--gpu-limit-mb 12288" in text
    assert "--disable-cuda-malloc" in text and 'memory_guard.py" watch --pid "$pid"' in text
    assert 'nice -n 19 "${COMMAND[@]}"' in text
