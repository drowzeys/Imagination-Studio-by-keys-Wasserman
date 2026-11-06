"""Pre-download fit policy: synthetic pools only; never load models or probe hardware."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import windows_setup_fit as fit
import windows_measurements as measurements


def hardware(total=31232, free=28000, physical=65536, n1x=True):
    return {"physical_total_mb": physical, "host_total_mb": 32460, "host_free_mb": 12000,
            "gpu_total_mb": total, "gpu_free_mb": free, "n1x": n1x}


def test_requirements_are_runtime_policies_not_weight_sizes():
    assert fit.requirement("voice")["required_mb"] == 50 * 1024
    assert fit.requirement("music")["required_mb"] == 30 * 1024
    assert fit.requirement("bolt")["required_mb"] == 43 * 1024
    for part in ("image", "image-edit", "flux3", "moss", "sound", "foley", "video", "ref2va", "train"):
        assert fit.requirement(part)["required_mb"] is None


def test_recipe_summary_uses_current_passes_and_failures_not_invented_gpu_requirements():
    evidence = {"qualification_levels": [
        {"qualified": True}, {"qualified": True},
        {"qualified": False, "outcome": "failed"},
        {"qualified": False, "outcome": "not_checked"}]}
    result = measurements.recipe_fit(evidence)
    assert result["passed"] == 2 and result["failed"] == 1 and result["total"] == 4
    assert "some recipes run" in result["summary"] and "1 not verified" in result["summary"]
    assert "GiB" not in result["summary"]
    assert measurements.recipe_fit({"qualification_levels": [{"qualified": True}]})["summary"] == "All 1 recipes verified on this machine."
    assert measurements.recipe_fit({"qualification_levels": [{"qualified": False}]}) is None


def test_observed_ranges_keep_gpu_and_commit_separate_and_mark_partial_memory_runs():
    def readings(free, peak, rendered=True):
        return {"rendered": rendered, "peak_job_commit_mb": peak, "measurements": {
            "initial": {"gpu_dedicated_total_mb": 32768},
            "minima": {"dedicated_gpu_free_mb": free}}}
    levels = [
        {"qualified": True, "evidence": {"results": [readings(12288, 24576)]}},
        {"qualified": True, "evidence": {"results": [readings(8192, 28672)]}},
        {"qualified": False, "outcome": "failed", "attempt": {
            "error": "memory watchdog: commit headroom below floor",
            "observations": readings(4096, 30720, False)}}]
    result = measurements.recipe_memory(levels)
    assert result["memory_ranges"]["gpu_usage"] == {"min_mb": 20480, "max_mb": 28672, "incomplete": True}
    assert result["memory_ranges"]["job_commitment"] == {"min_mb": 24576, "max_mb": 30720, "incomplete": True}
    assert "20.0–28.0+ GiB" in result["memory_summary"]
    assert "24.0–30.0+ GiB" in result["memory_summary"]
    assert "Stopped by" not in result["memory_summary"]
    assert "Stopped by commit backing" in result["memory_note"]
    assert "other apps" in result["memory_note"]


def test_completed_render_or_nonmemory_failure_does_not_add_lower_bound_marker():
    row = {"rendered": True, "peak_job_commit_mb": 1024, "measurements": {
        "initial": {"gpu_dedicated_total_mb": 4096}, "minima": {"dedicated_gpu_free_mb": 2048}}}
    result = measurements.recipe_memory([{"qualified": False, "outcome": "failed", "attempt": {
        "error": "memory guard cleanup failure", "observations": row}}])
    assert not result["memory_ranges"]["gpu_usage"]["incomplete"]
    assert "+" not in result["memory_summary"]
    assert measurements.recipe_memory([{"qualified": False, "outcome": "failed", "attempt": {
        "error": "source changed", "observations": row}}]) == {}
    assert measurements.recipe_memory([{"qualified": True, "evidence": {"results": [
        {"peak_job_commit_mb": float("nan"), "measurements": {"initial": {
            "gpu_dedicated_total_mb": 1024}, "minima": {"dedicated_gpu_free_mb": 2048}}}]}}]) == {}


@pytest.mark.parametrize("kind", ["scout", "sfx", "foley", "music", "voice", "mask"])
def test_all_feature_measurements_use_saved_readings_without_qualification_or_hardware_probes(monkeypatch, kind):
    import windows_baseline_checks as baseline
    import windows_features as features
    row = {"peak_job_commit_mb": 8192, "measurements": {
        "initial": {"gpu_dedicated_total_mb": 32768},
        "minima": {"dedicated_gpu_free_mb": 24576}}}
    receipt = {"ok": True, "results": [row]}
    monkeypatch.setattr(features, "saved", lambda key, suffix: receipt if not suffix else {})
    monkeypatch.setattr(baseline, "evidence", lambda key, qualified: {**receipt, "matches": False})
    monkeypatch.setattr(fit, "probe", lambda: pytest.fail("No measurement-time hardware probes"))
    result = measurements.feature_measurements([kind])
    assert "8.0–8.0 GiB" in result["memory_summary"]
    assert result["memory_ranges"]["gpu_usage"]["max_mb"] == 8192
    assert "not a current fit guarantee" in result["summary"]
    assert "qualified" not in result


def test_component_catalog_wires_all_linked_feature_measurements(monkeypatch):
    monkeypatch.setattr(measurements, "feature_measurements", lambda kinds: {"linked": kinds})
    result = fit.model_info("workflows", hardware={})
    assert result["recipe_fit"]["linked"] == ["turnaround", "upscale", "mask", "remove"]
    result = fit.model_info("sound", hardware={})
    assert result["recipe_fit"]["linked"] == ["sfx"]


def test_missing_gpu_measurements_never_invent_gpu_usage(monkeypatch):
    import windows_features as features
    monkeypatch.setattr(features, "saved", lambda *args: {})
    assert measurements.feature_measurements(["music", "swap"]) is None
    monkeypatch.setattr(features, "saved", lambda key, suffix: {"ok": True, "results": [
        {"peak_job_commit_mb": 2048}]} if not suffix else {})
    result = measurements.feature_measurements(["foley"])
    assert result["memory_ranges"]["gpu_usage"] is None
    assert "GPU usage" not in result["memory_summary"]
    assert "owned job commitment" in result["memory_summary"]


def test_known_insufficient_voice_is_skipped_before_automatic_download():
    plan = fit.plan("voice", hardware(), automatic=True)
    assert plan["state"] == "insufficient_capacity" and plan["action"] == "skip"
    assert "50 GiB free" in plan["why"]
    assert plan["minimum_installed_ram_mb"] is None and not plan["qualified"]


def test_explicit_mismatch_requires_acknowledgment_but_never_changes_runtime_policy():
    assert fit.plan("voice", hardware())["action"] == "blocked"
    plan = fit.plan("voice", hardware(), allow_insufficient=True)
    assert plan["action"] == "download" and plan["state"] == "insufficient_capacity"
    assert plan["required_mb"] == 50 * 1024
    assert fit.plan("voice", hardware(), automatic=True, allow_insufficient=True)["action"] == "download"


def test_larger_machine_is_not_qualified_from_ram_and_contention_is_not_a_skip():
    roomy = hardware(total=65536, free=60000, physical=98304)
    plan = fit.plan("voice", roomy, automatic=True)
    assert plan["state"] == "capacity_possible" and plan["action"] == "download" and not plan["qualified"]
    busy = fit.plan("voice", {**roomy, "gpu_free_mb": 28000}, automatic=True)
    assert busy["state"] == "temporarily_busy" and busy["action"] == "download"
    same_pool = fit.plan("voice", hardware(physical=98304), automatic=True)
    assert same_pool["action"] == "skip"  # 96 GiB RAM cannot make a smaller GPU pool fit


def test_unknown_capacity_and_unmeasured_models_are_never_claimed_to_fit():
    for pools in ({}, hardware(total=None), hardware(n1x=None)):
        plan = fit.plan("voice", pools, automatic=True)
        if pools.get("gpu_total_mb") == 31232:
            assert plan["state"] == "insufficient_capacity"
        else:
            assert plan["state"] == "unknown" and plan["action"] == "download"
    for part in ("image", "flux3", "moss", "video", "foley"):
        plan = fit.plan(part, hardware(), automatic=True)
        assert plan["state"] == "unknown" and plan["required_mb"] is None and not plan["qualified"]


def test_cached_weights_allow_repair_without_redownload_or_fake_qualification():
    plan = fit.plan("voice", hardware(), installed=True, automatic=True)
    assert plan["action"] == "reuse" and plan["state"] == "insufficient_capacity" and not plan["qualified"]


def test_discrete_gpu_keeps_host_and_gpu_capacity_separate():
    plan = fit.plan("voice", hardware(total=98304, free=80000, n1x=False), automatic=True)
    assert plan["action"] == "skip" and "host RAM" in plan["why"]
    assert fit.plan("voice", hardware(total=98304, free=80000, n1x=True), automatic=True)["action"] == "download"


def test_music_uses_its_selected_weight_route(monkeypatch):
    monkeypatch.setenv("MUSIC3_WEIGHTS", "bf16")
    assert fit.requirement("music")["required_mb"] == 38 * 1024
    assert fit.plan("music", hardware(), automatic=True)["action"] == "skip"


def test_probe_filters_npu_and_honors_explicit_gpu(monkeypatch):
    import windows_memory
    monkeypatch.setattr(windows_memory, "host_memory", lambda: (32000, 12000))
    monkeypatch.setattr(windows_memory, "physical_memory_mb", lambda: 65536)
    monkeypatch.delenv("STUDIO_GPU", raising=False)
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout="0, GPU-example, NVIDIA RTX Spark N1X, 31232, 28000\n1, NPU-example, NVIDIA NPU, 100000, 90000\n")
    monkeypatch.setattr(fit.subprocess, "run", run)
    found = fit.probe()
    assert found["gpu_total_mb"] == 31232 and found["gpu_free_mb"] == 28000 and found["n1x"]
    assert calls[0][1]["timeout"] == 5
    assert "GPU-example" not in json.dumps(found)
    monkeypatch.setenv("STUDIO_GPU", "1")
    found = fit.probe()
    assert found["gpu_total_mb"] is None and found["error"]


def test_voice_cached_checkpoint_requires_every_indexed_shard(tmp_path, monkeypatch):
    monkeypatch.delenv("DRAMA3_CUDA_MODEL", raising=False)
    folder = tmp_path / "drama-3"
    folder.mkdir()
    (folder / "config.json").write_text("{}")
    (folder / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"a": "one.safetensors"}}))
    assert not fit.cached("voice", tmp_path)
    (folder / "one.safetensors").write_text("test")
    assert fit.cached("voice", tmp_path)


def test_cli_plans_before_any_download_and_keeps_unknown_parts(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fit, "probe", lambda: hardware())
    monkeypatch.setattr(fit, "cached", lambda *args: False)
    assert fit.main(["--parts", "voice,image,music", "--models", str(tmp_path), "--automatic"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["skip_parts"] == ["voice"] and not result["blocked"]
    assert "[setup-fit]" in captured.err
    assert fit.main(["--parts", "voice", "--models", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["blocked"]


def test_unknown_only_plan_uses_shared_configuration_inventory(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fit, "probe", lambda: hardware())
    monkeypatch.setattr(fit, "cached", lambda *args: False)
    assert fit.main(["--parts", "image,flux3,moss", "--models", str(tmp_path), "--automatic"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert all(row["state"] == "unknown" for row in result["plans"])
    assert result["skip_parts"] == []


def measured_hardware():
    return {**hardware(), "gpu_dedicated_total_mb": 31232, "architecture": "arm64"}


def test_matching_failed_shared_recipes_block_downloads_only_without_explicit_override():
    pools = measured_hardware()
    result = fit.download_plan("moss", pools)
    assert result["action"] == "blocked"
    assert result["shared_advice"][0]["state"] == "matching_failures"
    assert fit.download_plan("moss", pools, allow_insufficient=True)["action"] == "download"
    assert fit.download_plan("moss", pools, allow_shared_image_failures=True)["action"] == "download"
    assert fit.download_plan("moss", pools, automatic=True)["action"] == "skip"
    assert fit.download_plan("moss", pools, installed=True)["action"] == "reuse"
    useful = fit.download_plan("image", pools)
    assert useful["action"] == "download" and useful["shared_advice"][0]["state"] == "partially_useful"
    assert fit.download_plan("voice", pools, allow_shared_image_failures=True)["action"] == "blocked"


def test_missing_shared_metadata_warns_only_and_different_configuration_does_not_block():
    result = fit.shared_advice("bolt", measured_hardware())
    assert result["state"] == "no_matching_metadata" and not result["blocked"]
    assert "WARNING" in result["why"]
    for changed in ({"architecture": "x86-64"}, {"n1x": False}, {"physical_total_mb": 131072},
                    {"gpu_dedicated_total_mb": 98304}):
        result = fit.shared_advice("qwen", {**measured_hardware(), **changed})
        assert not result["blocked"] and result["state"] == "no_matching_metadata"
    # A model with successful shared tests but no failures on this config is not blocked.
    result = fit.shared_advice("sound", measured_hardware())
    assert not result["blocked"] and result["state"] == "matching_observations"


def test_shared_preflight_cli_blocks_before_download_and_emits_missing_metadata_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fit, "probe", measured_hardware)
    monkeypatch.setattr(fit, "cached", lambda *args: False)
    args = ["--parts", "image,flux3", "--models", str(tmp_path)]
    assert fit.main(args) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["blocked"]
    assert "Not recommended for this machine" in captured.err and "no tested preset completed" in captured.err
    assert fit.main([*args, "--allow-insufficient"]) == 0
    assert not json.loads(capsys.readouterr().out)["blocked"]


def test_shared_failure_applies_to_lower_capacity_but_success_is_not_extrapolated():
    pools = {**measured_hardware(), "physical_total_mb": 49152, "gpu_dedicated_total_mb": 24576}
    assert fit.shared_advice("moss-image", pools)["blocked"]
    assert fit.shared_advice("qwen", pools)["blocked"]
    assert fit.shared_advice("sound", pools)["state"] == "no_matching_metadata"
    assert not fit.shared_advice("moss-image", {**pools, "n1x": False})["blocked"]


@pytest.mark.skipif(not (shutil.which("powershell") or shutil.which("pwsh")), reason="PowerShell is required")
def test_voice_plan_only_is_pure_and_no_download_skips_capacity_probe(tmp_path):
    shell = shutil.which("powershell") or shutil.which("pwsh")
    source = str(ROOT / "scripts" / "setup-windows-cuda.ps1").replace("'", "''")
    command = f"""
function global:py {{throw 'No Python probe in PlanOnly'}}
function global:Test-Path {{throw 'No filesystem probe in PlanOnly'}}
& '{source}' -Parts voice -Voice -PlanOnly
"""
    done = subprocess.run([shell, "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
    result = json.loads(done.stdout)
    assert "Not probed" in result["runtime_fit"]
    command = f"""
$env:OS='Windows_NT'; $env:PROCESSOR_ARCHITECTURE='ARM64'; $env:LOCALAPPDATA='{tmp_path}'
function global:py {{throw 'No capacity probe with NoDownload'}}
Set-Item -Path 'Function:global:{ROOT / "scripts" / "app.ps1"}' -Value {{throw 'AFTER_GUARD_BEFORE_INSTALL'}}
& '{source}' -Parts voice -Voice -SkipToolchain -SkipBuild -NoDownload
"""
    done = subprocess.run([shell, "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=30)
    assert done.returncode != 0
    assert "AFTER_GUARD_BEFORE_INSTALL" in done.stderr and "No capacity probe" not in done.stderr


SHELL = shutil.which("powershell") or shutil.which("pwsh")


@pytest.mark.skipif(not SHELL, reason="PowerShell is required")
@pytest.mark.parametrize("automatic,override,allowed", [(True, False, True), (False, False, False), (False, True, True)])
def test_powershell_capacity_guard_runs_before_toolchain_and_filters_parts(tmp_path, automatic, override, allowed):
    source = str(ROOT / "scripts" / "setup-windows-cuda.ps1").replace("'", "''")
    marker = tmp_path / "reached.txt"
    # All actual tools/installers are mocked; stop at the very first toolchain action.
    command = f"""
$ErrorActionPreference='Stop'
$env:OS='Windows_NT'; $env:PROCESSOR_ARCHITECTURE='ARM64'; $env:LOCALAPPDATA='{tmp_path}'
function global:py {{
  if ($args -contains '--parts') {{
    $auto=$args -contains '--automatic'; $override=$args -contains '--allow-insufficient'
    $skip=if($auto -and -not $override){{@('voice')}}else{{@()}}
    @{{plans=@();skip_parts=$skip;blocked=(-not $auto -and -not $override)}} | ConvertTo-Json -Compress
    $global:LASTEXITCODE=0
  }} else {{ throw 'UNEXPECTED_PYTHON' }}
}}
Set-Item -Path 'Function:global:{ROOT / "scripts" / "app.ps1"}' -Value {{
  Set-Content -LiteralPath '{marker}' -Value ("Voice="+$Voice+";Parts="+$Parts)
  throw 'STOP_BEFORE_INSTALL'
}}
& '{source}' -Parts 'voice,finish' -Voice -SkipToolchain -SkipBuild {'-AutomaticModelSelection' if automatic else ''} {'-AllowInsufficientModel' if override else ''}
"""
    done = subprocess.run([SHELL, "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=30)
    assert done.returncode != 0
    assert marker.exists() == allowed, done.stdout + done.stderr
    if automatic:
        assert marker.read_text().strip() == "Voice=False;Parts=finish"
    if not allowed:
        assert "AllowInsufficientModel" in done.stderr
