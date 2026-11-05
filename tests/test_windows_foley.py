"""Foley calibration protocol checks without loading weights or probing a GPU."""
import importlib.util
from array import array
import json
from pathlib import Path
from types import SimpleNamespace
import uuid
import time
import threading

import pytest

import windows_foley as foley

ROOT = Path(__file__).resolve().parents[1]


def test_qualification_retains_each_render_memory_observation(tmp_path, monkeypatch):
    initial = {"physical_total_mb": 65536, "host_total_mb": 32000, "gpu_dedicated_total_mb": 30000}
    measured = {"initial": initial, "minima": {"dedicated_gpu_free_mb": 22000}}
    result = {"memory_guard": measured, "calibration": {"request_to_file_seconds": 2, "audio": {}},
              "job_commitment": {"peak_job_commit_bytes": 4 << 20, "peak_process_commit_bytes": 3 << 20}}
    monkeypatch.setattr(foley, "check_room", lambda: None)
    monkeypatch.setattr(foley, "fingerprint", lambda: {})
    monkeypatch.setattr(foley, "fixture", lambda *args: None)
    monkeypatch.setattr(foley, "render", lambda *args, **kwargs: result)
    monkeypatch.setattr(foley.windows_features, "folder", lambda: tmp_path / "receipts")
    receipt = foley.qualify(tmp_path)
    assert len(receipt["results"]) == 2
    assert all(row["measurements"] == measured and row["rendered"] for row in receipt["results"])


def checker():
    spec = importlib.util.spec_from_file_location("foley_comfy_check", ROOT / "scripts" / "windows-comfy-check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extra_node_packs_are_explicit_and_do_not_change_the_default_guard():
    check = checker()
    case = check.CalibrationCase(lambda _: None, lambda *_: None)
    assert case.custom_nodes == ()
    foley_case = check.CalibrationCase(case.prepare, case.exercise, custom_nodes=foley.CUSTOM_NODES)
    assert (foley_case.gpu_limit_mb, foley_case.process_commit_mb, foley_case.job_commit_mb) == (
        case.gpu_limit_mb, case.process_commit_mb, case.job_commit_mb)
    with pytest.raises(ValueError, match="explicitly supported"):
        check.CalibrationCase(case.prepare, case.exercise, custom_nodes=("another-node-pack",))
    built_in = check.CalibrationCase(case.prepare, case.exercise, wddm_device_uuid="selected-device")
    assert built_in.wddm_device_uuid == "selected-device"
    assert (built_in.gpu_limit_mb, built_in.process_commit_mb, built_in.job_commit_mb) == (
        case.gpu_limit_mb, case.process_commit_mb, case.job_commit_mb)
    with pytest.raises(ValueError, match="bounded built-in or MMAudio"):
        check.CalibrationCase(case.prepare, case.exercise, custom_nodes=("studio_mmaudio",), wddm_device_uuid="selected-device")


@pytest.mark.parametrize("head,dirty,message", [
    ("wrong-commit", "", "does not match"),
    (foley.NODE_COMMIT, " M nodes.py", "local changes"),
])
def test_changed_node_pack_is_refused_before_calibration(tmp_path, monkeypatch, head, dirty, message):
    monkeypatch.setattr(foley, "node_folder", lambda: tmp_path)
    def run(command, **kwargs):
        assert kwargs["timeout"] == 10 and kwargs["check"]
        return SimpleNamespace(stdout=head if "rev-parse" in command else dirty)
    monkeypatch.setattr(foley.subprocess, "run", run)
    with pytest.raises(RuntimeError, match=message):
        foley.verify_nodes()


def test_missing_nodes_refuse_before_a_prompt_is_submitted(tmp_path, monkeypatch):
    check = checker()
    monkeypatch.setattr(check, "read_json", lambda *args: {})
    monkeypatch.setattr(foley.renderer, "tools", lambda: SimpleNamespace(
        post_prompt=lambda *_: pytest.fail("Missing nodes must not submit a prompt")))
    with pytest.raises(RuntimeError, match="Foley nodes are missing"):
        foley.exercise(check, "http://127.0.0.1:1", tmp_path, None, None, tmp_path)


def test_foley_output_cannot_escape_the_owned_backend(tmp_path, monkeypatch):
    check = checker()
    prompt = str(uuid.uuid4())
    def read(base, endpoint):
        if endpoint == "/object_info":
            return {name: {} for name in ("MMAudioModelLoader", "MMAudioFeatureUtilsLoader", "LoadVideo",
                                          "GetVideoComponents", "StudioMMAudioSampler", "AudioAdjustVolume",
                                          "SaveAudio", "WindowsCalibrationReceipt")}
        return {prompt: {"status": {"status_str": "success"}, "outputs": {
            "19": {"windows_calibration": [{}]},
            "18": {"audio": [{"type": "output", "filename": "../../outside.flac"}]},
        }}}
    monkeypatch.setattr(check, "read_json", read)
    monkeypatch.setattr(foley.renderer, "tools", lambda: SimpleNamespace(
        post_prompt=lambda *_: {"prompt_id": prompt}, validate_queue=lambda *_: None,
        validate_receipt=lambda *_: None))
    with pytest.raises(RuntimeError, match="outside the owned"):
        foley.exercise(check, "http://127.0.0.1:1", tmp_path,
                       SimpleNamespace(poll=lambda: None), SimpleNamespace(check=lambda: None), tmp_path)


@pytest.mark.parametrize("platform,expected", [
    ("win32", ["cache", "model", "cache", "feature-a", "cache", "feature-b", "cache", "features", "cache"]),
    ("linux", ["model", "features", "cache"]),
])
def test_windows_offload_releases_allocator_cache_before_host_copies(monkeypatch, platform, expected):
    spec = importlib.util.spec_from_file_location("foley_sampler", ROOT / "spark" / "studio_mmaudio" / "__init__.py")
    sampler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sampler)
    monkeypatch.setattr(sampler.sys, "platform", platform)
    calls = []
    model = SimpleNamespace(to=lambda device: calls.append("model"))
    features = SimpleNamespace(to=lambda device: calls.append("features"), children=lambda: [
        SimpleNamespace(to=lambda device: calls.append("feature-a")),
        SimpleNamespace(to=lambda device: calls.append("feature-b")),
    ])
    memory = SimpleNamespace(soft_empty_cache=lambda: calls.append("cache"))
    sampler.offload_models(model, features, "cpu", memory)
    assert calls == expected


def test_native_mmaudio_mono_output_does_not_change_the_stereo_sound_effect_contract(monkeypatch):
    import windows_sound
    def run(command, **kwargs):
        if command[0] == "ffprobe":
            return SimpleNamespace(stdout=json.dumps({"streams": [{"sample_rate": "44100", "channels": 1}]}))
        return SimpleNamespace(stdout=array("f", [.1] * (5 * 44100)).tobytes())
    monkeypatch.setattr(windows_sound.subprocess, "run", run)
    assert windows_sound.validate_audio(Path("foley.wav"), channels=1, expected_samples=5 * 44100)["channels"] == 1
    with pytest.raises(RuntimeError, match="2 channels"):
        windows_sound.validate_audio(Path("foley.wav"))


@pytest.mark.parametrize("changes", [
    {"seed": True}, {"seed": -1}, {"steps": 10}, {"cfg": 5}, {"keep_sound": "yes"},
    {"foley_db": float("nan")}, {"unexpected": 1},
])
def test_foley_refuses_settings_outside_the_bounded_recipe(changes):
    params = {"video": "uploads/clip.mp4", "prompt": "A ball bounces."}
    checked = foley.parameters(params)
    assert checked["steps"] == 25 and checked["cfg"] == 4.5
    with pytest.raises(ValueError):
        foley.parameters({**params, **changes})


@pytest.mark.parametrize("changes", [
    {"seconds": .5}, {"seconds": 6}, {"fps": 30}, {"width": 1920, "height": 768},
])
def test_foley_refuses_unqualified_clip_duration_rate_and_aspect(monkeypatch, changes):
    import graphics
    info = {"width": 1280, "height": 720, "seconds": 5, "fps": 24}
    monkeypatch.setattr(graphics, "probe", lambda path: info)
    foley.clip_info(Path("clip.mp4"))
    monkeypatch.setattr(graphics, "probe", lambda path: {**info, **changes})
    with pytest.raises(ValueError):
        foley.clip_info(Path("clip.mp4"))


def test_dedicated_snapshot_uses_actual_dedicated_counters_without_adding_pools(monkeypatch):
    import windows_memory
    monkeypatch.setattr(windows_memory, "host_memory", lambda: (32460, 12000))
    value = windows_memory.dedicated_snapshot(65536, SimpleNamespace(read=lambda: (20000, 69000, 2000, False)),
                                        SimpleNamespace(read=lambda: (31232, 22000)))
    assert value.gpu_total_mb == value.gpu_dedicated_total_mb == 31232
    assert value.gpu_free_mb == value.gpu_dedicated_free_mb == 22000
    assert value.host_total_mb == 32460


def test_owned_handle_monitor_keeps_deadline_and_checks_caps_without_http(monkeypatch):
    check = checker()
    from windows_memory import Snapshot
    monitor = check.MemoryWatchdog.__new__(check.MemoryWatchdog)
    for name in ("stopping", "failed", "ready", "backend_stopping", "backend_ready"):
        setattr(monitor, name, threading.Event())
    monitor.state_lock = threading.Lock()
    monitor.backend_thread = None
    monitor.backend_samples = 0
    monitor.backend_min_free_mb = None
    monitor.configured_floor = ""
    monitor.reason = ""
    monitor.latest = Snapshot(48000, 22000, 12000, 26000, 23000, time.monotonic(), 12000, 40000, 1000, False, 26000, 23000)
    def accounting(process):
        monitor.stopping.set()
        return {"private_commit_bytes": 2048 << 20}
    monitor.job = SimpleNamespace(commitment=lambda: {"process_commit_limit_bytes": 12288 << 20},
                                  process_accounting=accounting)
    import windows_backend_handle
    monkeypatch.setattr(windows_backend_handle, "open_owned", lambda job, owner: SimpleNamespace(read=lambda: accounting(None)))
    monitor.fail = lambda message: pytest.fail(message)
    monkeypatch.setattr(check, "read_json", lambda *_args, **_kwargs: pytest.fail("The handle monitor must not block the busy backend HTTP/GIL"))
    monitor.start_backend_handle(SimpleNamespace(poll=lambda: None), {"pid": 1, "created": 1})
    monitor.backend_thread.join(timeout=2)
    assert monitor.backend_samples == 1 and monitor.backend_budget["free_mb"] == 23000
    assert monitor.backend_budget["private_commit_mb"] == 2048


def test_gpu_inventory_does_not_count_the_dla_accelerator_as_a_second_gpu(monkeypatch):
    identity = "GPU-00000000-0000-0000-0000-000000000001"
    foley.device_uuid.cache_clear()
    monkeypatch.setattr(foley.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(
        stdout=identity + "\nDLA-00000000-0000-0000-0000-000000000002\n"))
    assert foley.device_uuid() == identity
    foley.device_uuid.cache_clear()
    monkeypatch.setattr(foley.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(
        stdout=identity + "\nGPU-00000000-0000-0000-0000-000000000003\n"))
    with pytest.raises(RuntimeError, match="one physical GPU"):
        foley.device_uuid()
    foley.device_uuid.cache_clear()
