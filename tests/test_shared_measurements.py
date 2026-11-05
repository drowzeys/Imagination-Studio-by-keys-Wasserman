"""Portable shared evidence and changed-model plans; no real runtime or model calls."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "spark")]
import shared_measurements as shared
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
    monkeypatch.setattr(shared, "recipes", lambda: iter([("qwen","basic",module,"qwen")]))
    monkeypatch.setattr(shared.evidence, "saved", lambda key,suffix: receipt if not suffix else attempt)
    data = shared.export()
    assert len(data["profiles"]) == 2
    passed, failed = data["profiles"]
    assert passed["provenance_complete"] and not passed["interrupted"]
    assert failed["outcome"] == "commit_reserve" and failed["interrupted"]
    assert not failed["provenance_complete"] and failed["identity"] is None
    text = json.dumps(data)
    for token in ("private", "secret", "device", "Users", "model_sha256", "mtime"):
        assert token not in text
    assert failed["observations"][0]["minimum_free_mb"]["host_commit_headroom_mb"] == 3800


def test_export_does_not_invent_provenance_for_old_failures():
    row=shared.make_profile("voice","basic","memory_refusal",{
        "observations":{"rendered":False,"elapsed_s":1}},None,{})
    assert row["identity"] is None and not row["provenance_complete"]
    assert row["hardware"] == {}


def test_reference_never_creates_local_qualification():
    data={"profiles":[{"model":"qwen","scope":"basic","outcome":"passed"},
                      {"model":"qwen","scope":"large","outcome":"commit_reserve"}]}
    found=shared.references("qwen",data)
    assert found["passed_recipes"]==1 and found["failed_recipes"]==1
    assert "local fit is not inferred" in found["summary"]
    assert "qualified" not in found


@pytest.fixture
def campaign(monkeypatch):
    fp=fingerprint()
    module=SimpleNamespace(__name__="windows_image_checks",prerequisites=lambda model:"",
                           fingerprint=lambda model,scope:copy.deepcopy(fp))
    monkeypatch.setattr(shared,"recipes",lambda:iter([("qwen","basic",module,"qwen")]))
    receipt={"fingerprint":copy.deepcopy(fp),"model_sha256":{next(iter(fp["models"])):"a"*64}}
    monkeypatch.setattr(shared.evidence,"saved",lambda *args:receipt)
    monkeypatch.setattr(windows_audio_checks,"digest",lambda *args:"a"*64)
    identity=shared.portable_identity(fp,receipt["model_sha256"])
    reference={"profiles":[{"model":"qwen","scope":"basic","outcome":"passed","identity":identity,
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
    monkeypatch.setattr(shared,"recipes",lambda:iter([("qwen","basic",module,"qwen")]))
    monkeypatch.setattr(shared,"load",lambda *args:{"schema":1,"profiles":[]})
    monkeypatch.setattr(shared,"export",lambda:{"schema":1,"profiles":[]})
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
    text=json.dumps(data).lower()
    for field in ('"device"', '"token"', '"job_id"', '"path"', '"hostname"', 'c:\\\\users\\\\'):
        assert field not in text
    for row in data["profiles"]:
        assert "minimum_installed_ram_mb" not in row
        assert row["checked_at"] is None or "T" in row["checked_at"]
