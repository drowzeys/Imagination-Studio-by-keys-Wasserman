"""Portable shared evidence and changed-model plans; no real runtime or model calls."""
import copy
import importlib.util
import json
import subprocess
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "spark")]
import shared_measurements as shared
import windows_measurements as windows
import windows_audio_checks

spec = importlib.util.spec_from_file_location("measure_models", ROOT / "scripts" / "measure-windows-models.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def fingerprint():
    return {"model": "qwen", "contract": 1, "recipe": {
        "width": 512, "height": 512, "count": 1, "prompt": "private text"},
        "hardware": {"physical_total_mb": 65536, "host_total_mb": 32460,
                     "gpu_dedicated_total_mb": 31232, "device": "private-device"},
        "abi": {"python_abi": "3.13", "torch_major": 2, "cuda_major": 13},
        "models": {r"C:\Users\private\weights.safetensors": {"size": 20, "mtime_ns": 10}},
        "backend": {"python": "private-install-hash", "flags": ["--use-ck-attention"]},
        "sources": {"private-source": "old"}}


def test_portable_identity_omits_personal_values_and_ignores_path_timestamp_cosmetic_changes():
    fp = fingerprint()
    hashes = {next(iter(fp["models"])): "a" * 64}
    value = shared.portable_identity(fp, hashes)
    text = json.dumps(value)
    assert "private" not in text and "mtime" not in text and "device" not in text
    changed = copy.deepcopy(fp)
    changed["models"] = {"other-name": {"size": 20, "mtime_ns": 99}}
    changed["sources"] = {"new-file": "new"}
    changed["backend"]["python"] = "other-install"
    assert shared.portable_identity(changed, {"other-name": "a"*64}) == value
    assert shared.portable_identity(changed, {"other-name": "b"*64}) != value
    changed["recipe"]["width"] = 1024
    assert shared.portable_identity(changed, {"other-name": "a"*64}) != value


def test_export_preserves_failure_lower_bound_and_excludes_raw_errors_and_host_identity(monkeypatch):
    fp = fingerprint()
    observations = {"rendered": False, "elapsed_s": 12, "peak_job_commit_mb": 29000,
        "private": "do not export", "measurements": {"samples": 20,
            "initial": {"physical_total_mb": 65536, "gpu_dedicated_total_mb": 31232,
                        "host_commit_limit_mb": 67276, "device": "private"},
            "minima": {"dedicated_gpu_free_mb": 5000, "host_commit_headroom_mb": 3800},
            "secret": "not exported"}}
    receipt = {"ok": True, "checked_at": "2026-11-05T07:00:00+00:00", "fingerprint": fp,
               "model_sha256": {next(iter(fp["models"])):"a"*64},
               "results": [{**observations, "rendered": True}]}
    attempt = {"ok": False, "error": r"memory guard commit headroom C:\Users\private\secret",
               "observations": observations}
    module = SimpleNamespace(__name__="windows_image_checks")
    monkeypatch.setattr(windows, "recipes", lambda: iter([("qwen","basic",module,"qwen")]))
    monkeypatch.setattr(windows.evidence, "saved", lambda key,suffix:
                        (receipt if not suffix else attempt) if key.startswith("qwen") else {})
    data = windows.export()
    assert len(data["profiles"]) == 2
    passed, failed = data["profiles"]
    assert passed["provenance_complete"] and not passed["interrupted"]
    assert failed["outcome"] == "commit_reserve" and failed["interrupted"]
    assert not failed["provenance_complete"] and failed["identity"] is None
    text = json.dumps(data)
    for token in ("private", "secret", '"device"', "Users", "model_sha256", "mtime"):
        assert token not in text
    assert failed["observations"][0]["minimum_free_mb"]["host_commit_headroom_mb"] == 3800


def test_export_does_not_invent_provenance_for_old_failures():
    row=windows.make_profile("voice","basic","memory_refusal",{
        "observations":{"rendered":False,"elapsed_s":1}},None,{})
    assert row["identity"] is None and not row["provenance_complete"]
    assert row["hardware"] == {}


def test_published_local_measurements_are_visible_without_installed_models():
    result = shared.references("qwen", platform="windows", backend="torch-cuda", engine="comfyui")
    assert result["passed_recipes"] >= 5
    assert result["memory_ranges"]["gpu_usage"]["incomplete"]
    assert "23.0" in result["memory_summary"] and "25.9+" in result["memory_summary"]
    assert "64 GiB installed RAM" in result["summary"]
    assert "qualified" not in result


@pytest.mark.parametrize("physical,blocked", [(65536, True), (131071, True), (131072, False), (262144, False)])
def test_shared_failure_download_gate_applies_only_below_128_gib(physical, blocked):
    execution = {**shared.execution_profile("windows", "torch-cuda", "comfyui"),
                 "architecture": "arm64", "topology": {"memory": "unified", "devices": 1,
                 "device_family": "nvidia-n1x", "device_model": "nvidia-rtx-spark-n1x"}}
    hardware = {"physical_total_mb": physical, "gpu_dedicated_total_mb": 31232}
    data = {"profiles": [{"model": "moss-image", "execution": execution, "hardware": hardware,
                          "outcome": "commit_reserve"}]}
    advice = shared.install_advice("moss-image", hardware, execution, data)
    assert advice["blocked"] is blocked and advice["state"] == "matching_failures"
    assert ("warning only" in advice["why"]) is (not blocked)


def test_reference_never_creates_local_qualification():
    execution = shared.execution_profile("windows", "torch-cuda", "comfyui")
    data={"profiles":[{"model":"qwen","feature":"qwen","scope":"basic","outcome":"passed","execution":execution},
                      {"model":"qwen","feature":"qwen","scope":"large","outcome":"commit_reserve","execution":execution}]}
    found=shared.references("qwen",data,platform="windows")
    assert found["passed_recipes"]==1 and found["failed_recipes"]==1
    assert "not a local qualification" in found["summary"]
    assert "qualified" not in found


def test_export_includes_bounded_measurements_without_inventing_portable_identity(tmp_path, monkeypatch):
    import windows_baseline_checks as baseline
    measured = {"ok": True, "hardware": {"gpu_dedicated_total_mb": 8192, "device": "private-device"},
                "results": [{"rendered": True, "measurements": {
                    "initial": {"gpu_dedicated_total_mb": 8192},
                    "minima": {"dedicated_gpu_free_mb": 4096}}}]}
    marker = tmp_path / "qualification.json"
    marker.write_text("{}")
    monkeypatch.setattr(windows, "recipes", lambda: iter(()))
    monkeypatch.setattr(baseline, "receipt_path", lambda kind: marker)
    monkeypatch.setattr(baseline, "evidence", lambda *args: measured)
    monkeypatch.setattr(windows.evidence, "saved", lambda key, suffix: measured if key == "foley" and not suffix else {})
    profiles = windows.export()["profiles"]
    assert {row["model"] for row in profiles} == {"sd-turbo", "sound", "foley"}
    for row in profiles:
        assert row["identity"] is None and not row["provenance_complete"]
        assert row["observations"][0]["minimum_free_mb"]["dedicated_gpu_free_mb"] == 4096
        assert "private" not in json.dumps(row)


@pytest.fixture
def campaign(monkeypatch):
    fp=fingerprint()
    fp["measurement_profile"] = {**shared.execution_profile("windows", "torch-cuda", "comfyui"),
        "architecture": "arm64", "topology": {"memory": "unified", "devices": 1, "device_family": "nvidia-n1x",
                                           "device_model": "nvidia-rtx-spark-n1x"}}
    module=SimpleNamespace(__name__="windows_image_checks",prerequisites=lambda model:"",
                           fingerprint=lambda model,scope:copy.deepcopy(fp))
    monkeypatch.setattr(windows,"recipes",lambda:iter([("qwen","basic",module,"qwen")]))
    receipt={"fingerprint":copy.deepcopy(fp),"model_sha256":{next(iter(fp["models"])):"a"*64}}
    monkeypatch.setattr(windows.evidence,"saved",lambda *args:receipt)
    monkeypatch.setattr(windows_audio_checks,"digest",lambda *args:"a"*64)
    identity=shared.portable_identity(fp,receipt["model_sha256"])
    reference={"profiles":[{"model":"qwen","feature":"qwen","scope":"basic","outcome":"passed","identity":identity,
                            "provenance_complete": True, "execution": copy.deepcopy(fp["measurement_profile"]),
                            "hardware":{k:fp["hardware"][k] for k in shared.HARDWARE if k in fp["hardware"]}}]}
    return fp,module,reference


def test_changed_only_and_force_selection_detect_weight_recipe_contract_and_hardware(campaign):
    fp,module,reference=campaign
    assert runner.selection(set(),set(),False,reference)[0]["state"]=="unchanged"
    assert runner.selection(set(),set(),True,reference)[0]["state"]=="run"
    fp["sources"]["new"]="cosmetic"
    assert runner.selection(set(),set(),False,reference)[0]["state"]=="unchanged"
    fp["contract"]=2
    assert runner.selection(set(),set(),False,reference)[0]["state"]=="run"
    fp["contract"]=1
    fp["hardware"]["physical_total_mb"]=131072
    assert runner.selection(set(),set(),False,reference)[0]["state"]=="run"
    fp["hardware"]["physical_total_mb"]=65536
    fp["recipe"]["width"]=1024
    assert runner.selection(set(),set(),False,reference)[0]["state"]=="run"


def test_selection_filters_and_missing_prerequisites_are_not_passes(campaign):
    fp,module,reference=campaign
    assert not runner.selection({"qwen-full"},set(),False,reference)
    assert not runner.selection(set(),{"large"},False,reference)
    module.prerequisites=lambda model:"missing"
    assert runner.selection(set(),set(),False,reference)==[{"model":"qwen","scope":"basic","state":"not_installed"}]


def test_sweep_saves_each_completed_recipe_and_never_queues_in_plan_mode(tmp_path,monkeypatch):
    import windows_cuda
    monkeypatch.setattr(windows_cuda,"activate",lambda:None)
    module=SimpleNamespace(__name__="windows_image_checks",timeout_seconds=lambda *args:600)
    monkeypatch.setattr(windows,"recipes",lambda:iter([("qwen","basic",module,"qwen")]))
    monkeypatch.setattr(shared,"load",lambda *args:{"schema":1,"profiles":[]})
    monkeypatch.setattr(windows,"export",lambda:{"schema":1,"profiles":[]})
    monkeypatch.setattr(runner,"selection",lambda *args:[{"model":"qwen","scope":"basic","identity":"test","state":"run"}])
    calls=[]
    def call(path,body=None):
        calls.append((path,body))
        if path=="/api/jobs": return []
        if path.endswith("/check"): return {"id":"job1"}
        return {"status":"done"}
    monkeypatch.setattr(runner,"call",call)
    target=tmp_path/"report.json"
    assert runner.main(["--output",str(target)])==0 and not calls
    assert runner.main(["--output",str(target),"--run"])==0
    assert json.loads(target.with_suffix(".resume.json").read_text())["completed"]==["test"]
    assert calls[1]==("/api/feature-checks/check",{"feature":"qwen","scope":"basic"})
    calls.clear()
    runner.main(["--output",str(target),"--run"])
    assert not any(path.endswith("/check") for path,_ in calls)


def test_checked_in_profiles_contain_real_mixed_outcomes_and_no_private_fields():
    data=shared.load()
    assert any(row["model"]=="qwen" and row["outcome"]=="passed" for row in data["profiles"])
    assert any(row["model"]=="qwen-full" and row["outcome"]=="passed" for row in data["profiles"])
    assert any(row["outcome"]=="commit_reserve" for row in data["profiles"])
    for model in ("sd-turbo", "sound", "foley"):
        row = next(row for row in data["profiles"] if row["model"] == model)
        assert row["outcome"] == "passed"
        assert all(observed["minimum_free_mb"].get("dedicated_gpu_free_mb") is not None
                   for observed in row["observations"])
    text=json.dumps(data).lower()
    for field in ('"device"', '"token"', '"job_id"', '"path"', '"hostname"', 'c:\\\\users\\\\'):
        assert field not in text
    for row in data["profiles"]:
        assert "minimum_installed_ram_mb" not in row
        assert row["checked_at"] is None or "T" in row["checked_at"]


def test_migration_round_trips_every_original_profile_and_identity_without_loss():
    data = shared.load()
    migrated = [row for row in data["profiles"] if row["provenance"].get("source_profile_id")]
    assert data["schema"] == 1 and len(migrated) == 17
    for row in migrated:
        restored = copy.deepcopy(row)
        provenance = restored.pop("provenance")
        restored["id"] = provenance["source_profile_id"]
        restored["model"] = provenance["source_model"]
        restored["provenance_complete"] = provenance["source_provenance_complete"]
        del restored["execution"], restored["feature"]
        identity = restored["identity"]
        if identity:
            assert identity["weights_expected"] is None and not identity["weights_complete"]
            del identity["weights_expected"], identity["weights_complete"], identity["engine_expected"]
            identity["model"] = identity.pop("feature")
            identity["sha256"] = provenance["source_identity_sha256"]
            assert identity["sha256"] == shared.digest({k: v for k, v in identity.items() if k != "sha256"})
        for reading in restored["observations"]:
            assert reading.pop("case") is None
        assert restored["id"] == shared.digest({k: v for k, v in restored.items() if k != "id"})
        assert row["execution"]["architecture"] == "arm64"
        assert row["execution"]["topology"]["device_family"] == "nvidia-n1x"
        assert not row["provenance_complete"]
    assert shared.validate(json.loads(json.dumps(data))) == data


def test_neutral_import_and_load_do_not_import_windows_modules():
    code = """
import sys
class BlockWindows:
    def find_spec(self, fullname, *args):
        if fullname.startswith('windows_'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, BlockWindows())
import shared_measurements
shared_measurements.load()
assert not any(name.startswith('windows_') for name in sys.modules)
"""
    done = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=ROOT / "app",
                          capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stderr


def test_unknown_schema_is_rejected(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({**shared.load(), "schema": 2}), encoding="utf-8")
    with pytest.raises(ValueError):
        shared.load(path)


def test_unified_host_device_and_commit_ranges_are_not_added_or_relabelled():
    row = {"rendered": True, "samples": 1, "case": None, "minimum_free_mb": {
        "unified_available_mb": 8192, "host_available_mb": 4096, "dedicated_gpu_free_mb": 2048},
        "unified_pool_mb": 32768, "host_pool_mb": 16384, "gpu_pool_mb": 8192,
        "peak_job_commit_mb": 10240}
    summary = shared.memory_summary([row])
    assert summary["memory_ranges"]["unified_usage"]["max_mb"] == 24576
    assert summary["memory_ranges"]["host_usage"]["max_mb"] == 12288
    assert summary["memory_ranges"]["gpu_usage"]["max_mb"] == 6144
    assert summary["memory_ranges"]["job_commitment"]["max_mb"] == 10240
    assert "do not add" in summary["memory_note"]
    unified_only = {**row, "minimum_free_mb": {"unified_available_mb": 8192}}
    assert shared.memory_summary([unified_only])["memory_ranges"]["gpu_usage"] is None


def test_complete_provenance_requires_every_expected_weight_hash():
    fp = fingerprint()
    first = next(iter(fp["models"]))
    fp["models"]["second"] = {"size": 1}
    identity = shared.portable_identity(fp, {first: "a"*64})
    assert len(identity["weights"]) == 1 and identity["weights_expected"] == 2
    assert not identity["weights_complete"]
    row = windows.make_profile("qwen", "basic", "passed", {}, identity, {})
    assert not row["provenance_complete"]
    assert shared.portable_identity(fp, {first: "a"*64, "second": "b"*64})["weights_complete"]
    fp["engine"] = {"one": "c"*64, "two": "invalid"}
    identity = shared.portable_identity(fp, {first: "a"*64, "second": "b"*64})
    assert not shared.complete_identity(identity)


@pytest.mark.parametrize("change", ["platform", "backend", "engine", "architecture", "device_family", "device_model",
                                   "devices", "memory", "unknown"])
def test_changed_only_never_matches_incompatible_same_capacity_devices(campaign, change):
    fp, _, reference = campaign
    profile = fp["measurement_profile"]
    if change == "platform":
        reference["profiles"][0]["execution"]["platform"] = "linux"
    elif change == "backend":
        reference["profiles"][0]["execution"]["backend"] = "mlx"
    elif change == "engine":
        reference["profiles"][0]["execution"]["engine"] = "mlx"
    elif change == "architecture":
        profile["architecture"] = "x86-64"
    elif change == "unknown":
        profile["topology"]["device_family"] = "unknown"
    else:
        profile["topology"][change] = {"device_family": "nvidia-gb10", "device_model": "nvidia-dgx-spark-gb10",
                                      "devices": 2, "memory": "discrete"}[change]
    assert runner.selection(set(), set(), False, reference)[0]["state"] == "run"


@pytest.mark.parametrize("mutate", [
    lambda row: row.update(path=r"C:\Users\private\weights"),
    lambda row: row["execution"].update(hostname="private"),
    lambda row: row["execution"]["topology"].update(device_family="private-account"),
    lambda row: row["hardware"].update(device="private-id"),
    lambda row: row["observations"][0].update(elapsed_s=float("nan")),
    lambda row: row["observations"][0].update(samples=-1),
    lambda row: row["observations"][0].update(case=r"C:\Users\private"),
    lambda row: row["identity"]["contracts"].update(abi={"python_abi": "private"}),
    lambda row: row["identity"]["weights"][0].update(sha256="invalid"),
    lambda row: row.update(provenance_complete=True),
])
def test_nested_validation_and_privacy_fail_closed(tmp_path, mutate):
    data = copy.deepcopy(shared.load())
    mutate(data["profiles"][0])
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        shared.load(path)


def test_case_labels_are_preserved_but_arbitrary_text_is_not_exported():
    for case in ("Qwen-Image 2.1 Turbo: 1 image(s), 512x512", "5s-under",
                 "Short local Voice", "Turnaround sheet on bundled baker fixture"):
        assert windows.observation({"case": case})["case"] == case
    assert windows.observation({"case": "private-account's prompt"})["case"] is None


def test_canonical_models_and_features_are_distinct_and_platform_filtered():
    data = shared.load()
    assert windows.model_id("scout") == "sd-turbo" and windows.model_id("sfx") == "sound"
    for model, feature in (("sd-turbo", "scout"), ("sound", "sfx")):
        assert shared.references(model, data, platform="windows", feature=feature)["passed_recipes"] == 1
        assert shared.references(feature, data, platform="windows") == {}
        assert shared.references(model, data, platform="macos") == {}
    assert shared.references("qwen", data, platform="windows", backend="mlx") == {}


def test_recorded_bounded_gpu_ranges_remain_visible():
    data = shared.load()
    expected = {"sd-turbo": (4.0, 4.0), "sound": (7.4, 7.4), "foley": (7.0, 7.7)}
    for model, (low, high) in expected.items():
        readings = [reading for row in data["profiles"] if row["model"] == model
                    for reading in row["observations"]]
        result = shared.memory_summary(readings)
        observed = result["memory_ranges"]["gpu_usage"]
        assert round(observed["min_mb"] / 1024, 1) == low
        assert round(observed["max_mb"] / 1024, 1) == high
        assert "verified" not in result["memory_summary"]


@pytest.mark.parametrize("platform,need,model,backend", [
    ("macos", "qwen-image-2.1", "qwen", "mlx"),
    ("macos", "moss-image", "moss-image", "torch-metal"),
    ("linux", "qwen-image-2.1", "qwen", "torch-cuda"),
    ("linux", "drama3-checkpoint", "voice", "native-cuda"),
])
def test_inventory_metadata_is_platform_and_layout_specific(platform, need, model, backend):
    import model_metadata
    result = model_metadata.inventory_info(need, platform)
    assert result["model"] == model
    assert result["model_metadata"]["backend"] == backend
    assert not result["shared_measurements"]
    assert "download_size" not in result and "runtime_fit" not in result
    assert model_metadata.inventory_info("qwen-text-encoder", platform) == {}
    assert model_metadata.inventory_info("drama3-binary", platform) == {}


@pytest.mark.parametrize("spark", [True, False])
def test_discovery_state_exposes_common_metadata_without_windows_policy_or_probes(monkeypatch, spark):
    import discover
    monkeypatch.setattr(discover, "SPARK", spark)
    monkeypatch.setattr(discover, "MAC", not spark)
    monkeypatch.setattr(discover, "load", lambda: {"needs": {}})
    monkeypatch.setattr(discover, "scanning", lambda: False)
    monkeypatch.setattr(discover, "present", lambda _: True)
    monkeypatch.setattr(discover, "install_plan", lambda _: None)
    monkeypatch.setattr(discover, "shown", lambda _: "model-folder")
    monkeypatch.setattr(discover, "all_needs", lambda: [
        {"id": "qwen-image-2.1", "label": "Qwen", "engine": "Picture", "optional": False,
         "expected": Path("model-folder"), "env": "QWEN_MODEL_DIR"}])
    monkeypatch.setitem(sys.modules, "installs", SimpleNamespace(public=lambda: {"state": "idle"}))
    result = discover.state()["needs"][0]
    assert result["model"] == "qwen"
    assert result["model_metadata"]["backend"] == ("torch-cuda" if spark else "mlx")
    assert result["shared_measurements"] == {}
    assert "download_size" not in result and "runtime_fit" not in result and "recipe_fit" not in result


def test_unified_platform_profile_round_trip_is_valid_without_windows_fields():
    data = copy.deepcopy(shared.load())
    row = data["profiles"][0]
    data["profiles"] = [row]
    row["provenance"] = {}
    row["execution"] = {**shared.execution_profile("linux", "torch-cuda", "comfyui"),
        "architecture": "arm64", "topology": {"memory": "unified", "devices": 1,
            "device_family": "nvidia-gb10", "device_model": "nvidia-dgx-spark-gb10"}}
    row["hardware"] = {"unified_total_mb": 131072}
    row["observations"] = [{"rendered": True, "samples": 10, "case": {"frames": 22},
                            "unified_pool_mb": 131072, "minimum_free_mb": {"unified_available_mb": 65536}}]
    row["id"] = shared.digest({key: value for key, value in row.items() if key != "id"})
    assert shared.validate(json.loads(json.dumps(data))) == data
    assert shared.references("qwen", data, platform="linux")["passed_recipes"] == 1
    assert shared.references("qwen", data, platform="windows") == {}
