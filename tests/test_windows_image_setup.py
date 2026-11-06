"""Model-free image installer policy and mocked PowerShell composition."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import windows_image_setup as setup


def tensor_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".json":
        path.write_text("{}")
    else:
        header = json.dumps({"weight": {"dtype": "I8", "shape": [4], "data_offsets": [0, 4]}}).encode()
        path.write_bytes(len(header).to_bytes(8, "little") + header + b"1234")


def install_model(base, model):
    for _, folder, names in setup.model_files(model):
        for name in names:
            tensor_file(base / folder / name)


def test_auto_never_claims_a_native_reference_match_or_changes_existing_models():
    for installed in (set(), {"sd-turbo"}, {"qwen"}):
        result = setup.plan(installed=installed)
        assert result["selected_model"] == "sd-turbo"
        assert result["allowed"] and not result["requires_acknowledgment"]
        assert "RAM size alone" in result["why"]
        assert result["download"] == ("sd-turbo" not in installed)


def test_cached_sd_is_detected_without_network_qualification_or_hardware(tmp_path, monkeypatch, capsys):
    import windows_storyboard
    root = tmp_path / "SD-Turbo"
    weight = root / "unet" / "weight.safetensors"
    tensor_file(weight)
    tensor_file(root / "model_index.json")
    (root / "LICENSE.md").write_text("fixture licence")
    monkeypatch.delenv("STUDIO_WINDOWS_IMAGE_DIR", raising=False)
    monkeypatch.setattr(windows_storyboard, "FILES", {"unet/weight.safetensors": (weight.stat().st_size, "unused")})
    monkeypatch.setattr(setup, "preflight_sd", lambda: pytest.fail("No qualification or probes"))
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **k: pytest.fail("No subprocesses"))
    assert setup.main(["--models", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["model_files_complete"] and not result["download"]


@pytest.mark.parametrize("model", setup.MODELS[2:])
def test_unknown_native_warns_without_blocking_or_substituting(model):
    result = setup.plan(model)
    assert result["selected_model"] == model
    assert result["allowed"] and result["native_fit"] == "unknown"
    assert "WARNING" in result["shared_advice"]["why"]
    assert setup.plan(model, allow_unverified=True)["allowed"]
    assert setup.plan(model, no_download=True)["allowed"]
    cached = setup.plan(model, installed={model})
    assert cached["allowed"] and not cached["download"]
    assert cached["qualification"].startswith("not performed")


def test_native_notice_distinguishes_download_size_from_ram_and_license():
    notice = " ".join(setup.notices("qwen"))
    assert "33 GB" in notice and "NOT runtime RAM" in notice
    assert "Qwen Research License" in notice and "non-commercial" in notice
    with pytest.raises(ValueError, match="Unknown image model"):
        setup.plan("unknown")


def test_image_matching_failure_requires_flag_but_cached_and_unknown_downloads_do_not():
    pools = {"physical_total_mb": 65536, "gpu_dedicated_total_mb": 31232,
             "architecture": "arm64", "n1x": True}
    assert setup.plan("qwen", hardware=pools)["allowed"]
    assert setup.plan("qwen", hardware=pools)["shared_advice"]["state"] == "partially_useful"
    assert not setup.plan("moss-image", hardware=pools)["allowed"]
    assert setup.plan("moss-image", hardware=pools, allow_unverified=True)["allowed"]
    assert setup.plan("moss-image", hardware=pools, no_download=True)["allowed"]
    assert setup.plan("moss-image", hardware=pools, installed={"moss-image"})["allowed"]
    assert not setup.plan("flux3k-full", hardware=pools)["allowed"]


def test_cached_image_guard_never_probes_hardware(tmp_path, monkeypatch, capsys):
    import windows_setup_fit
    install_model(tmp_path, "qwen")
    monkeypatch.setattr(windows_setup_fit, "probe", lambda: pytest.fail("No probes for cached repair"))
    assert setup.main(["--guard-parts", "image", "--models", str(tmp_path)]) == 0
    assert not json.loads(capsys.readouterr().out)[0]["download"]


def test_complete_native_files_skip_guard_without_gpu_or_model_imports(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **k: pytest.fail("No probes for cached weights"))
    install_model(tmp_path, "qwen")
    plans = setup.guard_parts(["image"], tmp_path)
    assert plans[0]["allowed"] and not plans[0]["download"]
    weight = tmp_path / setup.model_files("qwen")[0][1] / setup.model_files("qwen")[0][2][0]
    original = weight.read_bytes()
    weight.write_bytes(original[:-1])
    assert setup.guard_parts(["image"], tmp_path)[0]["download"]
    weight.write_bytes(b"not an installed model")
    assert setup.guard_parts(["image"], tmp_path)[0]["download"]


def test_turbo_does_not_certify_full_or_flux_variants(tmp_path):
    install_model(tmp_path, "qwen")
    found = {row["selected_model"]: row for row in setup.guard_parts(["image", "image-edit", "flux3", "moss"], tmp_path)}
    assert found["qwen"]["allowed"]
    assert all(row["allowed"] and row["download"] for name, row in found.items() if name != "qwen")
    install_model(tmp_path, "flux3k-fast")
    fast, full = setup.guard_parts(["flux3"], tmp_path)
    assert fast["allowed"] and full["allowed"] and full["download"]
    install_model(tmp_path, "flux3k-full")
    assert all(row["allowed"] for row in setup.guard_parts(["flux3"], tmp_path))


def test_full_and_flux_selection_fetches_shared_components_without_other_transformers():
    full = [name for _, _, names in setup.model_files("qwen-full") for name in names]
    assert "diffusion_models/qwen_image_2.1_bf16.safetensors" in full
    assert any(name.startswith("text_encoders/") for name in full)
    assert any(name.startswith("vae/") for name in full)
    assert not any("turbo" in name for name in full)
    for model in ("flux3k-fast", "flux3k-full"):
        files = [name for _, _, names in setup.model_files(model) for name in names]
        assert len([name for name in files if name.startswith("diffusion_models/")]) == 1
        assert "system_prompts.json" in files


def test_selected_download_reuses_downloader_without_modifying_its_source(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "check_model_access", lambda *_: None)
    module = setup.downloader()
    original = module.PARTS
    calls = []
    def download(args):
        calls.append((args, module.PARTS[args.parts]))
        install_model(tmp_path, "qwen-full")
        return 0
    monkeypatch.setattr(module, "models", download)
    monkeypatch.setattr(module, "PARTS", original.copy())
    assert setup.download_model("qwen-full", tmp_path) == 0
    assert calls[0][0].models == str(tmp_path)
    assert calls[0][1] == setup.model_files("qwen-full")
    assert "selected-image" not in original


@pytest.fixture
def access_api(monkeypatch):
    from types import ModuleType, SimpleNamespace
    hub = ModuleType("huggingface_hub")
    errors = ModuleType("huggingface_hub.errors")
    calls = []

    class HttpError(Exception):
        def __init__(self, status):
            self.response = SimpleNamespace(status_code=status)

    errors.HfHubHTTPError = HttpError
    errors.GatedRepoError = type("GatedError", (HttpError,), {})
    hub.hf_hub_url = lambda repo, name: "https://huggingface.co/" + repo + "/resolve/main/" + name
    hub.get_hf_file_metadata = lambda url, **kwargs: calls.append((url, kwargs))
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setitem(sys.modules, "huggingface_hub.errors", errors)
    return hub, HttpError, calls


@pytest.mark.parametrize("status", [401, 403])
def test_gated_repository_refusal_is_actionable_before_any_download(tmp_path, monkeypatch, access_api, status):
    hub, error, calls = access_api

    def denied(url, **kwargs):
        calls.append((url, kwargs))
        raise error(status)

    monkeypatch.setattr(hub, "get_hf_file_metadata", denied)
    monkeypatch.setattr(setup.downloader(), "models", lambda *_: pytest.fail("No download may start before access passes"))
    with pytest.raises(RuntimeError, match="Accept that repository's access/license terms") as failure:
        setup.download_model("flux3k-full", tmp_path)
    assert "gated repositories" in str(failure.value) and "No model download was started" in str(failure.value)
    assert len(calls) == 1 and calls[0][1] == {"timeout": 10}


def test_access_checks_are_per_missing_repository_and_skip_cached_weights(tmp_path, access_api):
    _, _, calls = access_api
    setup.check_model_access(["flux3k-fast", "flux3k-full"], tmp_path)
    assert len(calls) == 2  # shared weights repository and prompt-template repository
    calls.clear()
    install_model(tmp_path, "flux3k-fast")
    install_model(tmp_path, "flux3k-full")
    setup.check_model_access(["flux3k-fast", "flux3k-full"], tmp_path)
    assert not calls


def test_incomplete_cached_components_are_not_silently_kept_as_success(tmp_path, monkeypatch):
    module = setup.downloader()
    monkeypatch.setattr(module, "models", lambda *a: pytest.fail("Bad cache must be refused first"))
    _, folder, names = setup.model_files("qwen")[0]
    target = tmp_path / folder / names[0]
    target.parent.mkdir(parents=True)
    target.write_bytes(b"partial file")
    with pytest.raises(RuntimeError, match="not overwritten"):
        setup.download_model("qwen", tmp_path)
    assert target.read_bytes() == b"partial file"


def test_offline_runtime_reuse_checks_pins_and_tracked_source_without_gpu_imports(tmp_path, monkeypatch):
    from types import SimpleNamespace
    dependencies = []
    audit = SimpleNamespace(dependencies=lambda *a, **k: dependencies.append((a, k)))
    monkeypatch.setattr(setup.importlib.util, "spec_from_file_location", lambda *a: SimpleNamespace(
        loader=SimpleNamespace(exec_module=lambda module: None)))
    monkeypatch.setattr(setup.importlib.util, "module_from_spec", lambda spec: audit)
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="b9b290e43f1eb379946dfeb013faf9bbefa1a618" if "rev-parse" in command else "")
    monkeypatch.setattr(setup.subprocess, "run", run)
    setup.check_runtime("comfy", tmp_path)
    assert dependencies[0][0][1].name == "requirements-windows-comfy-constraints.txt"
    assert all(call[1]["timeout"] == 10 for call in calls)
    assert "--untracked-files=no" in calls[-1][0]
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="different revision"))
    with pytest.raises(RuntimeError, match="not changed"):
        setup.check_runtime("comfy", tmp_path)


def sd_hardware(**more):
    return {"platform": "win32", "architecture": "ARM64", "host_free_mb": 24000,
            "commit_free_mb": 24000, "gpu_free_mb": 16000, "low_memory": False, **more}


@pytest.mark.parametrize("more,message", [
    ({"platform": "linux"}, "native Windows ARM64"),
    ({"architecture": "AMD64"}, "native Windows ARM64"),
    ({"gpu_free_mb": 14335}, "GPU cap"),
    ({"commit_free_mb": 18431}, "commit headroom"),
    ({"low_memory": True}, "physical memory"),
    ({"host_free_mb": 1000, "host_floor_mb": 2000}, "physical memory"),
])
def test_unsupported_or_insufficient_fallback_is_refused_not_reported_success(more, message):
    with pytest.raises(RuntimeError, match=message):
        setup.validate_sd_capacity(sd_hardware(**more))
    setup.validate_sd_capacity(sd_hardware(gpu_free_mb=14336, commit_free_mb=18432))


@pytest.mark.parametrize("physical_gb", [64, 80, 128])
def test_bounded_sd_admission_does_not_invent_a_total_ram_minimum(physical_gb):
    setup.validate_sd_capacity(sd_hardware(physical_total_mb=physical_gb * 1024))
    assert setup.plan()["selected_model"] == "sd-turbo"
    assert setup.plan("qwen")["allowed"]  # Unknown hardware warns; RAM size alone neither qualifies nor blocks.


def test_native_inventory_uses_existing_positive_gpu_uuid_filter(monkeypatch):
    import windows_memory
    monkeypatch.setattr(setup.sys, "platform", "win32")
    monkeypatch.setattr(setup.platform, "machine", lambda: "ARM64")
    from types import SimpleNamespace
    gpu = "GPU-00000000-0000-0000-0000-000000000001"
    monkeypatch.setattr(windows_memory.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=f"NPU-1\nDLA-1\n{gpu}\n"))
    assert setup.native_device() == gpu
    monkeypatch.setattr(windows_memory.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="NPU-1\nDLA-1\n"))
    with pytest.raises(RuntimeError, match="one physical GPU"):
        setup.native_device()


def test_guard_cli_fails_before_download_and_no_download_does_not_need_ack(tmp_path, monkeypatch, capsys):
    import windows_setup_fit
    monkeypatch.setattr(windows_setup_fit, "probe", lambda: {
        "physical_total_mb": 65536, "gpu_dedicated_total_mb": 31232, "n1x": True, "architecture": "arm64"})
    monkeypatch.setattr(setup, "download_model", lambda *a: pytest.fail("Guard must run first"))
    args = ["--model", "moss-image", "--models", str(tmp_path)]
    assert setup.main([*args, "--download-model"]) == 1
    said = capsys.readouterr().err
    assert "No model bytes" in said and "-AllowUnverifiedImageModel" in said and "-Model auto" in said
    assert setup.main([*args, "--no-download"]) == 0
    assert setup.main(["--guard-parts", "image,image-edit,flux3,moss", "--no-download"]) == 0


SHELL = shutil.which("powershell") or shutil.which("pwsh")


def run_ps(command):
    return subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-Command", command], capture_output=True, text=True, timeout=30)


@pytest.mark.skipif(not SHELL, reason="PowerShell is required")
@pytest.mark.parametrize("model", setup.MODELS)
def test_safe_plan_only_has_no_calls_or_probes(model):
    command = f"""
function global:py {{ throw 'Python must not run during PlanOnly' }}
function global:Test-Path {{ throw 'Filesystem must not be probed during PlanOnly' }}
& '{ROOT / "scripts" / "setup-windows-images.ps1"}' -Model '{model}' -PlanOnly
"""
    done = run_ps(command)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["selected_model"] == ("sd-turbo" if model == "auto" else model)
    assert result["native_toolchain"] is False and result["qualification"] is False


def ps_fixture(tmp_path, *, gpu=False, comfy=False, model="auto", bad_runtime=False, cached=False):
    """All subprocess/setup commands are PowerShell functions, never real installers."""
    home = tmp_path / "ImaginationStudio"
    gpu_path = home / "gpu-venv" / "Scripts" / "python.exe"
    if gpu:
        gpu_path.parent.mkdir(parents=True)
        gpu_path.touch()
    if comfy:
        (home / "ComfyUI").mkdir(parents=True, exist_ok=True)
    log = tmp_path / "calls.jsonl"
    script = ROOT / "scripts" / "setup-windows-images.ps1"
    selected = "sd-turbo" if model == "auto" else model
    return f"""
$ErrorActionPreference = 'Stop'
$env:LOCALAPPDATA = '{tmp_path}'
$env:OS = 'Windows_NT'
$env:PROCESSOR_ARCHITECTURE = 'ARM64'
$global:CallLog = '{log}'
$global:FakeGpu = '{gpu_path}'
function global:MockPython {{
  $a = @($args)
  Add-Content -LiteralPath $global:CallLog -Value (ConvertTo-Json -InputObject $a -Compress)
  $global:LASTEXITCODE = 0
  if ($a -contains '--check-runtime' -and {'$true' if bad_runtime else '$false'}) {{
    $global:LASTEXITCODE = 1
  }}
  if ($a -contains '--model' -and $a -notcontains '--download-model') {{
    '{{"selected_model":"{selected}","why":"mocked policy","download":{str(not cached).lower()},"model_files_complete":{str(cached).lower()}}}'
  }}
}}
Set-Item -Path 'Function:global:py' -Value ${{function:MockPython}}
Set-Item -Path 'Function:global:{gpu_path}' -Value ${{function:MockPython}}
Set-Item -Path 'Function:global:{ROOT / "scripts" / "setup-windows-gpu.ps1"}' -Value {{
  Add-Content -LiteralPath $global:CallLog -Value '["setup-gpu"]'
  New-Item -ItemType Directory -Force (Split-Path $global:FakeGpu) | Out-Null
  New-Item -ItemType File -Force $global:FakeGpu | Out-Null
  $global:LASTEXITCODE = 0
}}
Set-Item -Path 'Function:global:{ROOT / "scripts" / "setup-windows-comfy.ps1"}' -Value {{
  Add-Content -LiteralPath $global:CallLog -Value '["setup-comfy"]'
  $global:LASTEXITCODE = 0
}}
Set-Item -Path 'Function:global:{ROOT / "scripts" / "setup-windows-storyboard.ps1"}' -Value {{
  param([switch]$Verify)
  Add-Content -LiteralPath $global:CallLog -Value (ConvertTo-Json -InputObject @('setup-storyboard', [bool]$Verify) -Compress)
  $global:LASTEXITCODE = 0
}}
& '{script}' -Model '{model}' {'-AllowUnverifiedImageModel' if model not in ('auto', 'sd-turbo') else ''}
"""


@pytest.mark.skipif(not SHELL, reason="PowerShell is required")
@pytest.mark.parametrize("gpu,comfy,expected", [
    (False, False, ["setup-gpu", "setup-comfy", "setup-storyboard"]),
    (True, False, ["setup-comfy", "setup-storyboard"]),
    (True, True, ["setup-storyboard"]),
])
def test_fresh_prerequisites_are_composed_and_healthy_runtime_reused(tmp_path, gpu, comfy, expected):
    done = run_ps(ps_fixture(tmp_path, gpu=gpu, comfy=comfy))
    assert done.returncode == 0, done.stderr
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [row[0] for row in calls if row[0].startswith("setup-")] == expected
    assert any("--preflight-sd" in row for row in calls)
    assert not any("pip" in row or "config" in row for row in calls)


@pytest.mark.skipif(not SHELL, reason="PowerShell is required")
def test_unhealthy_existing_runtime_is_not_blindly_reinstalled(tmp_path):
    done = run_ps(ps_fixture(tmp_path, gpu=True, comfy=True, bad_runtime=True))
    assert done.returncode != 0 and "not reinstalled" in done.stderr
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert not any(row[0].startswith("setup-") for row in calls)


@pytest.mark.skipif(not SHELL, reason="PowerShell is required")
def test_cached_sd_only_verifies_weights_without_redundant_download_or_probes(tmp_path):
    done = run_ps(ps_fixture(tmp_path, gpu=True, comfy=True, cached=True))
    assert done.returncode == 0, done.stderr
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert ["setup-storyboard", True] in calls
    assert not any("--preflight-sd" in row or "--download-model" in row for row in calls)


@pytest.mark.skipif(not SHELL, reason="PowerShell is required")
def test_explicit_native_script_downloads_only_selected_model_and_configures_without_h3(tmp_path):
    done = run_ps(ps_fixture(tmp_path, gpu=True, comfy=True, model="qwen-full"))
    assert done.returncode == 0, done.stderr
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert any("--download-model" in row and "qwen-full" in row for row in calls)
    assert any("config" in row for row in calls)
    assert not any("--preflight-sd" in row or row[0].startswith("setup-") for row in calls)


@pytest.mark.skipif(not SHELL, reason="PowerShell is required")
@pytest.mark.skipif(sys.platform != "win32", reason="the guard probes this machine's Windows memory; run by the windows-arm64 CI job")
@pytest.mark.parametrize("cached,no_download,allowed", [(False, False, True), (True, False, True), (False, True, True)])
def test_direct_native_image_guard_runs_before_installation(tmp_path, cached, no_download, allowed):
    if cached:
        install_model(tmp_path / "models", "qwen")
    script = ROOT / "scripts" / "setup-windows-cuda.ps1"
    helper = ROOT / "app" / "windows_image_setup.py"
    command = f"""
$ErrorActionPreference = 'Stop'
$env:LOCALAPPDATA = '{tmp_path}'
$env:OS = 'Windows_NT'
$env:PROCESSOR_ARCHITECTURE = 'ARM64'
function global:py {{
  if ($args -contains '--parts') {{
    '{{"plans":[],"skip_parts":[],"blocked":false}}'
    $global:LASTEXITCODE=0
    return
  }}
  & '{sys.executable}' -X utf8 '{helper}' --guard-parts image --models '{tmp_path / "models"}'
  $global:LASTEXITCODE = $LASTEXITCODE
}}
Set-Item -Path 'Function:global:{ROOT / "scripts" / "app.ps1"}' -Value {{ throw 'AFTER_GUARD_NO_INSTALL' }}
& '{script}' -Parts image -Models '{tmp_path / "models"}' -SkipToolchain -SkipBuild {'-NoDownload' if no_download else ''}
"""
    done = run_ps(command)
    assert done.returncode != 0
    assert ("AFTER_GUARD_NO_INSTALL" in done.stderr) == allowed
    if not allowed:
        assert "refused before model downloads" in done.stderr


@pytest.mark.skipif(not SHELL, reason="PowerShell is required")
@pytest.mark.parametrize("parts,toolchain,gpu", [("image", False, True), ("finish", False, False), ("foley", False, True)])
def test_direct_plan_only_preserves_component_boundaries(parts, toolchain, gpu):
    command = f"""
function global:py {{ throw 'No probes' }}
& '{ROOT / "scripts" / "setup-windows-cuda.ps1"}' -Parts '{parts}' -PlanOnly
"""
    done = run_ps(command)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["native_toolchain"] == toolchain and result["gpu_runtime"] == gpu
    assert result["h3_engine"] is False
    assert result["foley_isolated"] == (parts == "foley")
