"""Model-free native Library checks on isolated fixtures/registries; no live probes."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "spark"))
import windows_library_checks as checks
import windows_checks
import windows_features as features
import windows_runtime
import windows_cuda


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(features, "folder", lambda: tmp_path / "evidence")
    monkeypatch.setattr(checks, "prerequisites", lambda _: "")
    value = {"contract": 1, "kind": "mask", "recipe": checks.recipe("mask"), "fixtures": checks.FIXTURES,
             "hardware": {}, "abi": {}, "models": {}, "backend": {}, "sources": {"workflow": "old"}}
    def fingerprint(kind):
        return {**copy.deepcopy(value), "kind": kind, "recipe": checks.recipe(kind)}
    monkeypatch.setattr(checks, "fingerprint", fingerprint)
    return value


@pytest.mark.parametrize("kind", checks.KINDS)
def test_independent_check_uses_production_builder_and_success_only_evidence(tmp_path, monkeypatch, evidence, kind):
    monkeypatch.setattr(checks, "fixtures", lambda *args: {"fixture": True})
    monkeypatch.setattr(checks, "built", lambda *args: (["python", "production.py"], {}, ["output"]))
    launched = []
    def observe(command, output, validate, result, timeout):
        launched.append(command)
        result.update(rendered=True, measurements={"samples": 5})
    monkeypatch.setattr(checks.native, "observe_native", observe)
    assert checks.qualify(kind, tmp_path / "check", "job")["ok"]
    assert checks.status(kind)["qualified"]
    assert launched[0][launched[0].index("--feature")+1] == kind
    assert "--worker" in launched[0]
    evidence["sources"]["workflow"] = "new"
    assert checks.status(kind)["qualified"] and checks.status(kind)["runtime_updated"]
    monkeypatch.setenv("STUDIO_QUALIFICATION_STRICT", "1")
    assert not checks.status(kind)["qualified"]


def test_failed_rerun_retains_pass_and_measurements(tmp_path, monkeypatch, evidence):
    previous = {"ok": True, "fingerprint": checks.fingerprint("mask"), "model_sha256": {}, "checked_at": "old"}
    features.save_pass("mask", previous)
    monkeypatch.setattr(checks, "fixtures", lambda *args: {})
    monkeypatch.setattr(checks, "built", lambda *args: ([], {}, []))
    def observe(command, output, validate, result, timeout):
        result["measurements"] = {"samples": 5}
        raise RuntimeError("memory floor reached")
    monkeypatch.setattr(checks.native, "observe_native", observe)
    with pytest.raises(RuntimeError, match="memory floor"):
        checks.qualify("mask", tmp_path / "check", "job")
    assert features.saved("mask", "") == previous
    windows_checks.save_outcome({"id":"job","params":{"feature":"mask"},"status":"failed","finished":1,"error":"outer"})
    assert features.saved("mask", "-attempt")["observations"]["measurements"]["samples"] == 5


@pytest.mark.parametrize("kind", checks.KINDS)
def test_native_queue_check_registration_keeps_supervision_not_draft_caps(tmp_path, evidence, kind):
    params = windows_checks.parameters({"feature": kind})
    command, env, outputs = windows_checks.build(tmp_path, params, "job")
    assert command[command.index("--feature")+1] == kind and len(outputs) == 1
    job = {"kind":"qualification","params":params}
    assert windows_checks.gpu_check(job) and windows_checks.native_image_check(job)
    assert windows_checks.timeout_seconds(job) > checks.TIMEOUT_S


@pytest.mark.skipif(sys.platform != "win32", reason="uses Windows-only APIs or file layouts; run by the windows-arm64 CI job")
def test_mask_does_not_require_h3_or_loras_but_generative_actions_need_bf16(tmp_path, monkeypatch):
    monkeypatch.setattr(windows_runtime, "model_root", lambda: tmp_path / "models")
    monkeypatch.setattr(windows_cuda, "root", lambda: tmp_path)
    monkeypatch.setattr(checks, "builders", lambda _: (SimpleNamespace(lora_file=lambda _: None), None))
    monkeypatch.setattr(checks, "studio_home", lambda: tmp_path / "studio")
    mask = checks.required_files("mask")
    assert any("sam3.1" in path.name for path in mask)
    assert all("ref2va" not in path.name and "qwen3vl" not in path.name for path in mask)
    for kind in ("turnaround","upscale","remove"):
        names = [path.name for path in checks.required_files(kind)]
        assert "minimax_h3_ref2va_pruned_bf16.safetensors" in names
        assert "minimax_h3_ref2va_pruned_int8_convrot.safetensors" not in names


@pytest.mark.skipif(sys.platform != "win32", reason="uses Windows-only APIs or file layouts; run by the windows-arm64 CI job")
def test_windows_workflow_refuses_int8_for_runtime_action_adapter_merge(monkeypatch):
    import spark_workflows as workflow
    monkeypatch.setattr(workflow.sg, "WINDOWS", True)
    monkeypatch.setattr(workflow, "listed", lambda *args: [workflow.sg.REF2VA["int8"]])
    assert workflow.ref2va() is None
    monkeypatch.setattr(workflow, "listed", lambda *args: [workflow.sg.REF2VA["bf16"]])
    assert workflow.ref2va() == workflow.sg.REF2VA["bf16"]


@pytest.mark.parametrize("kind", checks.KINDS)
def test_scope_limits_media_and_options_with_no_silent_resize(tmp_path, monkeypatch, kind):
    from PIL import Image
    import graphics
    monkeypatch.setattr(checks, "studio_home", lambda: tmp_path)
    Image.new("RGB",(512,288),"red").save(tmp_path / "person.png")
    (tmp_path / "clip.mp4").write_bytes(b"test")
    dimensions = {"width":512,"height":288,"fps":24,"seconds":22/24,"audio":False}
    monkeypatch.setattr(graphics, "probe", lambda _: dimensions)
    params={"image":"person.png","video":"clip.mp4","size":512,"strength":1,"megapixels":1,"denoise":0.1,
            "window":22,"steps":12,"silent":True,"seconds":5,"quality":480,"anchor":False}
    if kind=="swap":
        dimensions.update(width=864,height=480,seconds=5)
    checks.require_scope(kind, params)
    bad = {"size":1024} if kind=="turnaround" else {"megapixels":2} if kind=="upscale" else {
        "steps":20} if kind=="remove" else {"anchor":True} if kind=="swap" else {}
    if bad:
        with pytest.raises(ValueError):
            checks.require_scope(kind,{**params,**bad})
    if kind!="turnaround":
        dimensions["width"]=1920
        with pytest.raises(ValueError,match="input up to"):
            checks.require_scope(kind,params)


@pytest.mark.parametrize("kind", checks.KINDS)
def test_native_admission_unblocks_only_checked_action(evidence, monkeypatch, kind):
    monkeypatch.setattr(windows_runtime, "mode", lambda: {"enabled":True})
    with pytest.raises(ValueError):
        windows_runtime.require_native(kind,{})
    features.save_pass(kind, {"ok":True,"fingerprint":checks.fingerprint(kind),"model_sha256":{}})
    calls=[]
    monkeypatch.setattr(checks,"require_scope",lambda *args:calls.append(args))
    windows_runtime.require_native(kind,{"tested":True})
    assert calls==[(kind,{"tested":True})]
    monkeypatch.setattr(windows_runtime,"mode",lambda:{"enabled":False})
    windows_runtime.require_native(kind,{})
    assert len(calls)==1


def test_fixture_hashes_match_bundled_media():
    for name,expected in checks.FIXTURES.items():
        path=ROOT/"samples"/name
        assert checks.audio.digest(path,path.stat().st_size,path.stat().st_mtime_ns)==expected


def test_empty_matte_is_not_a_pass(tmp_path, monkeypatch):
    import graphics
    target=tmp_path/"person_matte.mp4"
    target.write_bytes(b"test")
    monkeypatch.setattr(graphics,"probe",lambda _:{"seconds":22/24,"width":512,"height":288,"fps":24})
    monkeypatch.setattr(checks,"tool",lambda name:name)
    monkeypatch.setattr(checks.subprocess,"run",lambda *args,**kwargs:SimpleNamespace(stdout=bytes(64*36)))
    with pytest.raises(RuntimeError,match="empty or full"):
        checks.check_outputs("mask",[str(target)])


def test_copy_only_fixture_preparation_does_not_start_models(tmp_path, monkeypatch):
    monkeypatch.setattr(checks,"tool",lambda name:name)
    commands=[]
    monkeypatch.setattr(checks.subprocess,"run",lambda command,**kwargs:commands.append((command,kwargs)))
    params=checks.fixtures(tmp_path,"remove")
    assert params["silent"] and params["window"]==22 and params["steps"]==12
    assert commands[0][0][0]=="ffmpeg" and "-frames:v" in commands[0][0]
    assert commands[0][1]["timeout"]==60


@pytest.mark.parametrize("kind",checks.KINDS)
def test_actual_native_builders_produce_native_commands_without_h3_pool_gate(tmp_path,monkeypatch,kind):
    import os
    for key in ("PREFIX","SPARK_MODELS","LORA_BUNDLE"):
        monkeypatch.setenv(key,os.environ.get(key,""))
    from test_actions import studio
    # Existing action test helper creates a complete isolated installation/LoRA registry.
    home,action,_,_,_=studio()
    monkeypatch.setattr(checks,"builders",lambda _: (action,None))
    monkeypatch.setattr(windows_cuda,"native_os",lambda:True)
    monkeypatch.setattr(windows_cuda,"active",lambda:True)
    python=tmp_path/"native-python.exe"
    python.write_text("test")
    monkeypatch.setattr(windows_cuda,"python",lambda:str(python))
    monkeypatch.setattr(windows_runtime,"mode",lambda:{"enabled":True})
    params={"image":"uploads/a.png","video":"renders/c.mp4","person":checks.PERSON,
            "size":512,"strength":1,"megapixels":1,"denoise":0.1,"window":22,"steps":12,"silent":True}
    if kind=="swap":
        from test_swap import stand_in
        environment=stand_in(tmp_path)
        home,swapped=environment.home,environment.build
        monkeypatch.setattr(checks,"builders",lambda _:(None,swapped))
        import memory_guard
        monkeypatch.setattr(memory_guard,"swap_check",lambda *args,**kwargs:{"fits":True})
        params={"video":"uploads/clip.mp4","image":"uploads/hero.png","seconds":5,"quality":480,"anchor":False}
    command,env,outputs=checks.built(kind,home,params)
    assert command[0]==str(python)
    assert "bash" not in command and outputs
    if kind=="turnaround":
        assert env["STEPS"]=="28" and "--" not in command[:2]
    if kind=="swap":
        assert "--no-anchor" in command
