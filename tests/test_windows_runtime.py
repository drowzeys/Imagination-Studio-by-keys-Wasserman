"""Unified Windows dispatch and qualification policy; synthetic installations, no model/GPU work."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from test_windows_ui import client, server  # noqa: F401
import windows_checks
import windows_cuda
import windows_runtime as runtime

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_route_is_not_hidden_behind_static_mount(client):
    response = client.get("/api/desktop/session")
    assert response.status_code == 404
    assert response.json()["error"] == "This app was not started by the desktop controller."


@pytest.mark.parametrize("feature,hidden", [(feature, True) for feature in
    ("qwen", "voice", "music", "hlg", "finish", "scout", "sfx", "foley", "graphics", "mask")])
def test_native_image_checker_outer_process_does_not_open_a_console(monkeypatch, feature, hidden):
    import windows_process

    calls = []
    process = SimpleNamespace(stdout=[], pid=123, wait=lambda *args: 0)

    class Owned:
        def __init__(self, **kwargs):
            calls.append(("ownership", kwargs))

        def spawn(self, command, **kwargs):
            calls.append(("spawn", kwargs))
            return process

        def close(self):
            calls.append(("closed", {}))

    monkeypatch.setattr(windows_process, "OwnedJob", Owned)
    monkeypatch.setattr(server, "SPARK", False)
    monkeypatch.setattr(server, "WINDOWS", False)
    monkeypatch.setattr(server, "WIN32", True)
    monkeypatch.setattr(server, "WINCUDA", True)
    monkeypatch.setattr(server, "environment", lambda env: env)
    monkeypatch.setattr(server, "running", {"process": None})
    job = {"id": "test-console", "kind": "qualification", "params": {"feature": feature},
           "status": "running", "log": ""}
    assert server.run(job, ["python", "checker.py"], {})
    launch = next(kwargs for label, kwargs in calls if label == "spawn")
    assert bool(launch["creationflags"] & 0x08000000) is hidden
    assert launch["stdout"] == subprocess.PIPE and launch["stderr"] == subprocess.STDOUT
    assert ("closed", {}) in calls
    assert server.running["process"] is None


@pytest.fixture
def install(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    monkeypatch.setattr(windows_cuda, "root", lambda: root)
    monkeypatch.setenv("STUDIO_CUDA_TIER", str(root / "cuda-tier.json"))
    monkeypatch.setenv("SPARK_MODELS", str(root / "models"))
    monkeypatch.setattr(windows_cuda, "why_not", lambda: "")
    monkeypatch.setattr(runtime, "mode_file", lambda: root / "qualification-mode.json")
    for name in ("gpu-venv/Scripts/python.exe", "ComfyUI/main.py", "ComfyUI/extra_model_paths.yaml"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test")
    (root / "cuda-tier.json").write_text(json.dumps({"cuda_pool_mb": 46680, "models": str(root / "models")}))
    title, folder, transformer, _ = runtime.IMAGE_SPECS["qwen"]
    for name in ("diffusion_models/" + transformer, "text_encoders/qwen3vl_8b_int8_convrot.safetensors",
                 "vae/qwen_image_2.1_vae_bf16.safetensors"):
        path = root / "models" / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")
    return root


@pytest.mark.parametrize("physical,required", [(49152, True), (65536, True), (81920, True), (131072, False), (262144, False)])
def test_qualification_defaults_and_locked_small_hardware(tmp_path, monkeypatch, physical, required):
    monkeypatch.setattr(runtime, "physical_memory_mb", lambda: physical)
    monkeypatch.setattr(runtime, "mode_file", lambda: tmp_path / "mode.json")
    assert runtime.mode()["enabled"] == required
    assert runtime.mode()["required"] == required
    assert runtime.mode()["can_disable"] is not required
    if required:
        with pytest.raises(ValueError, match="cannot be disabled"):
            runtime.set_mode(False)
        assert not runtime.mode_file().exists()
    else:
        assert runtime.set_mode(True)["enabled"]
        assert runtime.set_mode(False)["enabled"] is False


def test_unknown_capacity_keeps_gating_on_and_reports_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "physical_memory_mb", lambda: 0)
    monkeypatch.setattr(runtime, "mode_file", lambda: tmp_path / "mode.json")
    runtime.mode_file().write_text('{"enabled":false}')
    found = runtime.mode()
    assert found["enabled"] and found["required"] and not found["can_disable"]
    assert found["physical_total_mb"] is None and "could not be established" in found["reason"]


def test_corrupt_mode_setting_is_not_silently_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "physical_memory_mb", lambda: 131072)
    monkeypatch.setattr(runtime, "mode_file", lambda: tmp_path / "mode.json")
    runtime.mode_file().write_text("invalid")
    assert runtime.mode()["enabled"]
    assert "could not be read" in runtime.mode()["reason"]
    assert not runtime.set_mode(False)["enabled"]


def test_image_candidates_are_not_blocked_by_h3_or_its_pool(install, monkeypatch):
    monkeypatch.setattr(runtime, "physical_memory_mb", lambda: 65536)
    qwen = next(row for row in runtime.image_options() if row["id"] == "qwen")
    assert qwen["installed"] and not qwen["available"] and not qwen["qualified"]
    assert qwen["state"] == "needs_verification" and "verification" in qwen["why"]
    assert "H3" not in qwen["why"] and "90000" not in qwen["why"]
    assert all(requirement["required_mb"] is None for requirement in qwen["requirements"])
    assert qwen["check_available"] and "512x512, one image" in qwen["scope"]
    monkeypatch.setattr(runtime, "physical_memory_mb", lambda: 131072)
    qwen = next(row for row in runtime.image_options() if row["id"] == "qwen")
    assert qwen["available"] and not qwen["qualified"] and not qwen["qualification_required"]
    assert runtime.default_image() == "qwen"
    assert not runtime.bounded("scout", {"image_model": "qwen"})
    assert runtime.bounded("scout", {"image_model": "sd-turbo"})


def test_missing_image_files_are_installation_not_memory_failure(install, monkeypatch):
    monkeypatch.setattr(runtime, "physical_memory_mb", lambda: 131072)
    qwen = next(row for row in runtime.image_options() if row["id"] == "qwen")
    path = runtime.model_root() / runtime.IMAGE_SPECS["qwen"][1] / "diffusion_models" / runtime.IMAGE_SPECS["qwen"][2]
    path.unlink()
    missing = next(row for row in runtime.image_options() if row["id"] == "qwen")
    assert qwen["available"] and not missing["installed"] and not missing["available"]
    assert missing["state"] == "needs_install" and "Missing model/runtime files" in missing["why"]


def test_gating_off_does_not_bypass_h3_capacity(install, monkeypatch):
    monkeypatch.setattr(runtime, "physical_memory_mb", lambda: 131072)
    (install / "tf-h3").mkdir()
    (install / "tf-h3" / "tf_h3.dll").write_bytes(b"engine")
    video = runtime.model_root() / "FastH3-Comfy"
    for name in ("text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
                 "vae/minimax_h3_video_vae_int8_convrot.safetensors", "vae/minimax_h3_audio_vae_fp32.safetensors",
                 "diffusion_models/fastvideo_fasth3_8step_v2_pruned_bf16.safetensors"):
        path = video / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")
    assert not runtime.mode()["enabled"]
    found = runtime.native_status("video", runtime.mode())
    assert found["installed"] and not found["available"]
    assert found["state"] == "insufficient_capacity" and found["cuda_pool_required_mb"] == 90000
    with pytest.raises(runtime.RouteUnavailable, match="90000"):
        runtime.require_native("video", {})


def test_prerequisite_failure_is_not_relabelled_as_verification(monkeypatch):
    import windows_audio_checks
    monkeypatch.setattr(windows_audio_checks, "status", lambda _: {
        "installed": True, "qualified": False, "available": False, "why": "Run the check.", "state": "needs_verification"})
    monkeypatch.setattr(windows_cuda, "configuration_error", lambda: "")
    found = runtime.gate_native_state("voice", {"installed": True, "available": False, "why": "Not enough GPU memory"},
                                      {"enabled": True})
    assert found["why"] == "Not enough GPU memory" and not found["available"]


def test_mode_api_stays_available_without_native_core_and_checks_remain_runnable(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "jobs", {})
    monkeypatch.setattr(runtime, "mode_file", lambda: tmp_path / "mode.json")
    monkeypatch.setattr(runtime, "physical_memory_mb", lambda: 131072)
    monkeypatch.setattr(server, "WINDOWS", True)
    monkeypatch.setattr(server, "WIN32", True)
    assert client.post("/api/feature-checks/mode", json={"enabled": False}).status_code == 200
    queued = []
    monkeypatch.setattr(server, "enqueue", lambda *args: queued.append(args) or {"status": "queued"})
    response = client.post("/api/feature-checks/check", json={"feature": "hlg"})
    assert response.status_code == 200 and queued
    assert not runtime.mode()["enabled"]
    assert client.post("/api/feature-checks/mode", json={"enabled": "false"}).status_code == 400
    assert client.post("/api/feature-checks/mode", json={"enabled": False}, headers={"origin": "http://example.invalid"}).status_code == 403
    monkeypatch.setattr(runtime, "physical_memory_mb", lambda: 81920)
    assert client.post("/api/feature-checks/mode", json={"enabled": False}).status_code == 409
    assert runtime.mode()["enabled"]


def test_explicit_native_model_is_not_silently_replaced_by_draft(client, install, monkeypatch):
    monkeypatch.setattr(server, "WINDOWS", False)
    monkeypatch.setattr(server, "WIN32", True)
    monkeypatch.setattr(server, "WINCUDA", True)
    monkeypatch.setattr(runtime, "physical_memory_mb", lambda: 65536)
    before = len(server.jobs)
    response = client.post("/api/jobs", json={"kind": "scout", "params": {"prompt": "A hall.", "image_model": "qwen"}})
    assert response.status_code == 501 and "verification" in response.json()["error"]
    assert len(server.jobs) == before
    assert client.post("/api/jobs", json={"kind": "scout", "params": {"image_model": "unknown"}}).status_code == 400


def test_native_image_build_does_not_require_h3(client, install, monkeypatch):
    monkeypatch.setattr(server, "WINDOWS", False)
    monkeypatch.setattr(server, "WIN32", True)
    monkeypatch.setattr(server, "WINCUDA", True)
    monkeypatch.setattr(runtime, "physical_memory_mb", lambda: 131072)
    command, env, outputs = server.build("scout", {"image_model": "qwen", "prompt": "A hall.", "count": 1}, "native-image")
    assert command[0] == "bash" and Path(command[1]).name == "scout.sh"
    assert env["IMAGE_MODEL"] == "qwen" and len(outputs) == 2
    assert not (install / "tf-h3" / "tf_h3.dll").exists()


def test_native_discovery_uses_runtime_image_inventory_and_powershell(install, monkeypatch):
    import discover
    monkeypatch.setattr(discover, "SPARK", False)
    monkeypatch.setattr(discover, "MAC", False)
    monkeypatch.setattr(discover.sys, "platform", "win32")
    monkeypatch.setattr(discover, "all_needs", lambda: pytest.fail("Native inventory must not read the Mac catalogue"))
    monkeypatch.setattr(discover, "load", lambda: pytest.fail("Native inventory must not trust a stale scan"))
    found = discover.state()
    assert found["inventory_only"] and not found["scanning"]
    rows = {row["id"]: row for row in found["needs"]}
    assert rows["qwen"]["state"] == "expected" and rows["qwen"]["feature"] == "qwen"
    assert "Qwen-Image-2.1-Comfy" in rows["qwen"]["expected"]
    assert rows["qwen-full"]["state"] == "missing"
    for row in rows.values():
        assert row["install"]["how"] == "command"
        assert row["install"]["shown"].startswith("powershell ")
        assert "bash " not in row["install"]["shown"] and ".sh " not in row["install"]["shown"]
    assert rows["qwen-full"]["install"]["shown"].endswith("-Model qwen-full")
    for row_id, feature in (("windows-music", "music"), ("windows-music-engine", "music"),
                            ("windows-sound", "sfx"), ("windows-foley", "foley"),
                            ("windows-drama-engine", "voice"), ("windows-drama-weights", "voice")):
        assert rows[row_id]["feature"] == feature
    path = runtime.image_files("qwen")[0]
    path.unlink()
    rows = {row["id"]: row for row in discover.state()["needs"]}
    assert rows["qwen"]["state"] == "missing" and path.name in rows["qwen"]["why"]


def test_native_discovery_never_starts_posix_scan_or_installer(client, install, monkeypatch):
    import discover
    monkeypatch.setattr(discover, "SPARK", False)
    monkeypatch.setattr(discover, "MAC", False)
    monkeypatch.setattr(discover.sys, "platform", "win32")
    monkeypatch.setenv("STUDIO_DISCOVER_DELAY", "0")
    monkeypatch.setattr(discover, "start_scan", lambda: pytest.fail("Windows refresh must not scan disks"))
    monkeypatch.setattr(discover, "apply_links", lambda: pytest.fail("Windows must not apply Mac links"))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(discover.attach(install, install))
    with TestClient(app) as local:
        assert local.get("/api/discover").json()["inventory_only"]
        response = local.post("/api/discover/scan", json={})
        assert response.status_code == 200 and not response.json()["started"]
        for action in ("install", "use", "unlink", "link-all"):
            response = local.post("/api/discover/" + action, json={"need": "qwen"})
            assert response.status_code == 400
            assert "PowerShell" in response.json()["detail"]


def test_native_audio_cards_are_registered_once_and_checks_available(client, install, monkeypatch):
    import windows_audio_checks
    import voice
    import music
    monkeypatch.setattr(server, "WIN32", True)
    monkeypatch.setattr(server, "WINDOWS", False)
    monkeypatch.setattr(windows_audio_checks, "status", lambda kind: {
        "installed": True, "available": False, "qualified": False, "why": "Check the audio recipe.",
        "state": "needs_verification", "verified_recipe": windows_audio_checks.recipe(kind)})
    monkeypatch.setattr(voice, "state", lambda: {"available": True, "backend": "cuda", "why": ""})
    monkeypatch.setattr(music, "state", lambda: {"available": True, "route": "cuda", "why": ""})
    response = client.get("/api/feature-checks")
    assert response.status_code == 200
    cards = response.json()["features"]
    for kind in windows_audio_checks.KINDS:
        rows = [card for card in cards if card["id"] == kind]
        assert len(rows) == 1
        assert rows[0]["check_available"] and rows[0]["installed"] and not rows[0]["qualified"]


def test_native_library_cards_are_independent_and_not_hidden_by_h3(client, install, monkeypatch):
    import windows_library_checks as library
    monkeypatch.setattr(server,"WIN32",True)
    monkeypatch.setattr(server,"WINDOWS",False)
    monkeypatch.setattr(library,"status",lambda kind:{
        "installed":kind=="mask","available":kind=="mask","qualified":kind=="mask",
        "state":"qualified" if kind=="mask" else "not_installed",
        "why":"" if kind=="mask" else "Missing model files","verified_recipe":library.recipe(kind)})
    response=client.get("/api/feature-checks")
    assert response.status_code==200
    cards=response.json()["features"]
    for kind in library.KINDS:
        found=[card for card in cards if card["id"]==kind]
        assert len(found)==1 and found[0]["check_available"]
        assert found[0]["installed"] is (kind=="mask")
        assert "bundled" in found[0]["scope"].lower()
    state=server.state()
    assert state["capabilities"]["mask"]["available"]
    assert state["capabilities"]["image_actions"]["available"]
    assert not state["capabilities"]["remove"]["available"]


@pytest.mark.parametrize("model", tuple(runtime.IMAGE_SPECS))
def test_native_qualification_only_unlocks_its_checked_canvas_and_count(client, install, monkeypatch, model):
    import windows_image_checks
    from fastapi import HTTPException

    monkeypatch.setattr(server, "WINDOWS", False)
    monkeypatch.setattr(server, "WIN32", True)
    monkeypatch.setattr(server, "WINCUDA", True)
    monkeypatch.setattr(runtime, "physical_memory_mb", lambda: 65536)
    monkeypatch.setattr(windows_image_checks, "status", lambda _: {
        "installed": True, "available": True, "qualified": True, "check_available": True, "why": "",
        "verified_recipe": {"width": 512, "height": 512, "count": 1},
        "verified_scopes": [{"id": "basic", "width": 512, "height": 512, "count": 1}]})
    params = {"image_model": model, "prompt": "A hall.", "count": 1, "width": 512, "height": 512}
    _, env, _ = server.build("scout", params, "native-image")
    assert env["WIDTH"] == env["HEIGHT"] == 512
    assert env["IMAGE_MODEL"] == model
    for changed in ({"width": 1024}, {"count": 2}):
        with pytest.raises(HTTPException, match="not a verified recipe"):
            server.build("scout", {**params, **changed}, "native-image")
    monkeypatch.setattr(runtime, "physical_memory_mb", lambda: 131072)
    _, env, _ = server.build("scout", {**params, "width": 1024}, "native-image")
    assert env["WIDTH"] == 1024


def test_native_comfy_ownership_rejects_other_session_before_opening_pid(monkeypatch):
    import comfy_win
    monkeypatch.setenv("STUDIO_WINDOWS_SESSION_KEY", "test-session")
    monkeypatch.setattr(comfy_win, "recorded", lambda: {"pid": 123, "created": 456, "session": "someone-else"})
    monkeypatch.setattr(comfy_win, "running", lambda: True)
    monkeypatch.setattr(comfy_win, "open_recorded", lambda *_args, **_kwargs: pytest.fail("Must not open or kill another session"))
    assert comfy_win.stop() == 1
    assert comfy_win.start() == 1


def test_native_comfy_restarts_owned_server_when_environment_changes(tmp_path, monkeypatch):
    import comfy_win
    import sys
    monkeypatch.setenv("STUDIO_WINDOWS_SESSION_KEY", "test-session")
    monkeypatch.setattr(comfy_win, "recorded", lambda: {
        "session": hashlib.sha256(b"test-session").hexdigest(), "python": str(tmp_path / "previous" / "python.exe")})
    monkeypatch.setattr(comfy_win, "running", lambda: True)
    monkeypatch.setattr(comfy_win, "PREFIX", tmp_path)
    monkeypatch.setattr(comfy_win, "COMFY_LOG", tmp_path / "native.log")
    monkeypatch.setattr(comfy_win, "COMFY_KIND", tmp_path / "kind")
    monkeypatch.setattr(comfy_win, "STOP", tmp_path / "stop")
    (tmp_path / "ComfyUI").mkdir()
    (tmp_path / "ComfyUI" / "main.py").write_text("not executed")
    calls = []
    monkeypatch.setattr(comfy_win, "stop", lambda: calls.append("stopped") or 0)
    monkeypatch.setattr(comfy_win.subprocess, "Popen", lambda command, **kwargs: calls.append(command[0]))
    assert comfy_win.start() == 0
    assert calls == ["stopped", sys.executable]


@pytest.mark.skipif(not (shutil.which("powershell") or shutil.which("pwsh")), reason="PowerShell is required")
@pytest.mark.parametrize("parts,h3,toolchain,gpu", [
    ("image", False, False, True), ("moss", False, False, True), ("music", False, True, True),
    ("video,image", True, True, True), ("foley", False, False, True), ("finish", False, False, False),
])
def test_setup_plan_only_selects_needed_components(parts, h3, toolchain, gpu):
    shell = shutil.which("powershell") or shutil.which("pwsh")
    done = subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(ROOT / "scripts" / "setup-windows-cuda.ps1"), "-Parts", parts, "-PlanOnly"],
                          capture_output=True, text=True, timeout=30, check=True)
    found = json.loads(done.stdout)
    assert found["h3_engine"] == h3 and found["native_toolchain"] == toolchain and found["gpu_runtime"] == gpu
