"""File integrity, workflow reuse and allocator-cap checks without model or CUDA execution."""
import hashlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import windows_sfx


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


sfx = module("test_sfx_check", "windows-sfx-check.py")
launcher = module("test_calibration_launcher", "windows-comfy-calibration-launcher.py")


def test_files_are_checked_before_any_model_load(tmp_path, monkeypatch):
    data = b"test"
    monkeypatch.setattr(windows_sfx, "FILES", {"model.safetensors": (4, hashlib.sha256(data).hexdigest())})
    (tmp_path / "model.safetensors").write_bytes(data)
    (tmp_path / "LICENSE.md").write_text("test license")
    assert windows_sfx.verify(tmp_path)["weight_bytes"] == 4
    (tmp_path / "model.safetensors").write_bytes(b"fail")
    with pytest.raises(RuntimeError, match="SHA-256"):
        windows_sfx.verify(tmp_path)


def test_missing_weight_does_not_pass(tmp_path):
    with pytest.raises(RuntimeError, match="missing model"):
        windows_sfx.verify(tmp_path)


def test_workflow_preserves_the_existing_sound_recipe():
    graph = sfx.workflow()
    assert graph["1"]["class_type"] == "WindowsCalibrationCheckpointLoader"
    assert graph["12"]["inputs"]["steps"] == 100
    assert graph["12"]["inputs"]["cfg"] == 7
    assert graph["12"]["inputs"]["sampler_name"] == "dpmpp_3m_sde_gpu"
    assert graph["12"]["inputs"]["scheduler"] == "exponential"
    assert graph["6"]["inputs"]["seconds"] == 5
    assert graph["17"]["inputs"]["volume"] == -12
    assert graph["19"]["inputs"]["audio"] == ["18", 0]


def fake_torch(calls, backend="native"):
    return SimpleNamespace(cuda=SimpleNamespace(
        is_available=lambda: True, device_count=lambda: 1,
        get_device_properties=lambda index: SimpleNamespace(total_memory=32 << 30),
        memory=SimpleNamespace(get_allocator_backend=lambda: backend),
        set_per_process_memory_fraction=lambda fraction, device: calls.append((fraction, device)),
    ))


def test_allocator_cap_sets_the_exact_fraction():
    calls = []
    result = launcher.cap_allocator(fake_torch(calls), 12 * 1024)
    assert calls == [(12 / 32, 0)]
    assert result["gpu_limit_bytes"] == 12 << 30


def test_async_allocator_does_not_claim_a_native_cap():
    with pytest.raises(RuntimeError, match="native"):
        launcher.cap_allocator(fake_torch([], "cudaMallocAsync"), 12 * 1024)


def test_cap_must_leave_device_margin():
    with pytest.raises(RuntimeError, match="reserve"):
        launcher.cap_allocator(fake_torch([]), 31 * 1024)


def receipt(**changes):
    values = dict(allocator="native", peak_tensor_bytes=1 << 30, peak_reserved_bytes=2 << 30,
                  gpu_total_bytes=32 << 30, gpu_fraction=12 / 32)
    return {**values, **changes}


def test_receipt_requires_applied_cap_not_just_small_peak():
    sfx.validate_receipt(receipt(), 12 << 30)
    with pytest.raises(RuntimeError, match="not applied"):
        sfx.validate_receipt(receipt(gpu_fraction=0.9), 12 << 30)


@pytest.mark.parametrize("changes", [
    {"peak_tensor_bytes": 0}, {"peak_reserved_bytes": 13 << 30},
    {"peak_tensor_bytes": True}, {"gpu_fraction": float("nan")},
])
def test_invalid_peaks_are_refused(changes):
    with pytest.raises(RuntimeError):
        sfx.validate_receipt(receipt(**changes), 12 << 30)


def test_gpu_first_loader_uses_comfy_helpers_without_cpu_fallback(monkeypatch):
    calls = []
    comfy = ModuleType("comfy")
    comfy.model_management = SimpleNamespace(get_torch_device=lambda: "cuda:0")
    comfy.utils = SimpleNamespace(load_torch_file=lambda path, **kwargs:
                                 (calls.append((path, kwargs)) or {"gpu_state": True}, {"metadata": True}))
    comfy.sd = SimpleNamespace(load_state_dict_guess_config=lambda state, **kwargs:
                              (calls.append((state, kwargs)) or ("model", "clip", "vae", None)))
    folders = SimpleNamespace(get_full_path_or_raise=lambda *args: r"C:\models\test.safetensors",
                              get_folder_paths=lambda *args: [])
    for name, value in (("torch", SimpleNamespace()), ("comfy", comfy), ("comfy.model_management", comfy.model_management),
                        ("comfy.utils", comfy.utils), ("comfy.sd", comfy.sd), ("folder_paths", folders),
                        ("aiohttp", SimpleNamespace(web=SimpleNamespace(json_response=lambda value: value))),
                        ("server", SimpleNamespace(PromptServer=SimpleNamespace(
                            instance=SimpleNamespace(routes=SimpleNamespace(get=lambda path: lambda fn: fn)))))):
        monkeypatch.setitem(sys.modules, name, value)
    nodes = module("test_calibration_node", "windows-calibration-node.py")
    assert nodes.WindowsCalibrationCheckpointLoader().load("test") == ("model", "clip", "vae")
    assert calls[0][1] == {"device": "cuda:0", "return_metadata": True}
    assert calls[1][1]["model_options"] == {"load_device": "cuda:0", "offload_device": "cuda:0"}
    comfy.sd.load_state_dict_guess_config = lambda *args, **kwargs: None
    with pytest.raises(RuntimeError, match="could not identify"):
        nodes.WindowsCalibrationCheckpointLoader().load("test")


def test_model_calibration_cannot_use_unguarded_diagnostic(monkeypatch, tmp_path):
    check = sfx.checker()
    monkeypatch.setattr(check.sys, "platform", "win32")
    prepared = []
    case = check.CalibrationCase(lambda *args: prepared.append(True), lambda *args: {})
    with pytest.raises(ValueError, match="guarded"):
        check.run_check(tmp_path, 5, 0, calibration=case)
    assert prepared == []


def test_only_owned_sampler_progress_can_trigger_cancel():
    message = {"type": "progress", "data": {"prompt_id": "owned", "node": "12", "value": 1, "max": 100}}
    assert sfx.sampling_progress(message, "owned")["value"] == 1
    assert sfx.sampling_progress(message, "other") is None
    message["data"]["node"] = "1"
    assert sfx.sampling_progress(message, "owned") is None


def test_targeted_cancel_requires_interruption_not_just_http_acceptance():
    record = {"status": {"status_str": "error", "messages": [
        ["execution_interrupted", {"prompt_id": "owned", "node_id": "12", "node_type": "KSampler"}]
    ]}, "outputs": {}}
    sfx.validate_cancelled(record, "owned")
    with pytest.raises(RuntimeError, match="not confirmed"):
        sfx.validate_cancelled(record, "other")
    record["status"]["status_str"] = "success"
    with pytest.raises(RuntimeError):
        sfx.validate_cancelled(record, "owned")


def test_cancel_in_a_later_node_does_not_qualify_sampler_cancellation():
    record = {"status": {"status_str": "error", "messages": [
        ["execution_interrupted", {"prompt_id": "owned", "node_id": "18", "node_type": "SaveAudio"}]
    ]}, "outputs": {}}
    with pytest.raises(RuntimeError):
        sfx.validate_cancelled(record, "owned")


def test_calibration_queue_refuses_other_jobs():
    assert sfx.validate_queue({"queue_running": [[1, "owned"]], "queue_pending": []}, "owned")
    with pytest.raises(RuntimeError, match="another"):
        sfx.validate_queue({"queue_running": [[1, "another"]], "queue_pending": []}, "owned")


@pytest.mark.parametrize("record", [None, {}, {"status": {"messages": None}},
                                   {"status": {"messages": [], "status_str": "success"}}])
def test_invalid_cancellation_history_is_not_success(record):
    with pytest.raises(RuntimeError):
        sfx.validate_cancelled(record, "owned")
