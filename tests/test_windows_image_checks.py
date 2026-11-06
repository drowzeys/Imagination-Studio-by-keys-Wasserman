"""Native image qualification contracts; no models, GPU probes or app instances."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "spark"))
import windows_features as features
import windows_image_checks as images


@pytest.mark.parametrize("windows", [True, False])
@pytest.mark.parametrize("action", ["start", "status", "stop"])
def test_comfy_control_subprocess_does_not_open_a_windows_console(monkeypatch, windows, action):
    import spark_generate
    calls = []
    monkeypatch.setattr(spark_generate, "WINDOWS", windows)
    monkeypatch.setattr(spark_generate.subprocess, "run",
                        lambda argv, **kwargs: calls.append((argv, kwargs)))
    spark_generate.run_comfy_control(action, capture_output=True, text=True)
    assert calls[0][0] == spark_generate.comfy_control(action)
    assert calls[0][1]["creationflags"] == (0x08000000 if windows else 0)
    assert calls[0][1]["capture_output"]


def test_identity_probe_does_not_open_a_console(monkeypatch):
    import windows_memory
    flags = []
    monkeypatch.setattr(windows_memory.sys, "platform", "win32")
    def run(argv, **kwargs):
        flags.append(kwargs["creationflags"])
        return SimpleNamespace(stdout="GPU-00000000-0000-0000-0000-000000000001")
    monkeypatch.setattr(windows_memory.subprocess, "run", run)
    assert windows_memory.single_gpu_uuid().startswith("GPU-")
    assert flags == [0x08000000]


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(features, "folder", lambda: tmp_path / "qualification")
    monkeypatch.setattr(images, "prerequisites", lambda _: "")
    value = {"contract": images.CONTRACT, "model": "qwen", "recipe": images.recipe(),
             "models": {}, "model_root": "example-root", "hardware": {"physical_total_mb": 65536},
             "abi": {"torch_major": 2}, "backend": {"flags": ["--use-ck-attention"]}, "sources": {"producer": "old"}}
    def fingerprint(model, scope="basic"):
        result = copy.deepcopy(value)
        result["model"] = model
        if scope != "basic" or model != "qwen":
            result["recipe"] = images.recipe(scope, model)
        return result
    monkeypatch.setattr(images, "fingerprint", fingerprint)
    return value


def test_native_pass_survives_compatible_updates_but_not_a_recipe_change(evidence):
    features.save_pass("qwen", {"ok": True, "fingerprint": copy.deepcopy(evidence), "model_sha256": {}})
    evidence["sources"]["producer"] = "new"
    found = images.status("qwen")
    assert found["qualified"] and found["runtime_updated"]
    evidence["recipe"]["width"] = 1024
    assert not images.status("qwen")["qualified"]


def test_native_strict_policy_retains_exact_provenance(evidence, monkeypatch):
    features.save_pass("qwen", {"ok": True, "fingerprint": copy.deepcopy(evidence), "model_sha256": {}})
    monkeypatch.setenv("STUDIO_QUALIFICATION_STRICT", "1")
    evidence["sources"]["producer"] = "new"
    assert not images.status("qwen")["qualified"]


def test_progressive_scopes_keep_legacy_pass_and_do_not_certify_other_canvases(evidence, tmp_path, monkeypatch):
    original = {"ok": True, "fingerprint": copy.deepcopy(evidence), "model_sha256": {}, "checked_at": "original"}
    features.save_pass("qwen", original)
    basic = images.status("qwen")
    assert basic["qualified"] and basic["next_scope"] == "studio"
    assert [level["id"] for level in basic["verified_scopes"]] == ["basic"]
    observed = {"case": "studio", "elapsed_s": 1}
    monkeypatch.setattr(images, "run_case", lambda model, output, observations, scope: observed)
    assert images.qualify("qwen", tmp_path / "studio", "job-studio", "studio")["ok"]
    assert features.saved("qwen", "") == original
    found = images.status("qwen")
    assert [level["id"] for level in found["verified_scopes"]] == ["basic", "studio"]
    assert found["next_scope"] == "scout"
    images.require_scope("qwen", 512, 512, 1)
    images.require_scope("qwen", 1024, 576, 1)
    for width, height, count in ((1024, 576, 2), (576, 1024, 1), (768, 432, 1), (1344, 768, 1)):
        with pytest.raises(ValueError, match="not a verified recipe"):
            images.require_scope("qwen", width, height, count)

    def failed(*_):
        raise RuntimeError("larger scope refused")
    monkeypatch.setattr(images, "run_case", failed)
    with pytest.raises(RuntimeError, match="larger scope refused"):
        images.qualify("qwen", tmp_path / "large", "job-large", "large")
    assert images.status("qwen")["verified_scopes"] == found["verified_scopes"]
    assert features.saved("qwen", "-attempt")["scope"] == "large"


def test_progressive_scope_parameters_are_bounded_and_default_to_basic():
    import windows_checks
    from fastapi import HTTPException
    assert windows_checks.parameters({"feature": "qwen"}) == {"feature": "qwen", "scope": "basic"}
    assert windows_checks.parameters({"feature": "qwen", "scope": "studio"})["scope"] == "studio"
    assert windows_checks.parameters({"feature": "qwen", "group": "images"}) == {"feature": "qwen", "group": "images"}
    for params in ({"feature": "qwen", "scope": "unknown"}, {"feature": "hlg", "scope": "studio"},
                   {"feature": "qwen", "scope": "studio", "width": 4096},
                   {"feature": "qwen", "scope": "studio", "group": "images"},
                   {"feature": "qwen", "group": "unknown"}):
        with pytest.raises(HTTPException):
            windows_checks.parameters(params)


def test_recipe_failure_persists_independently_and_pass_takes_precedence(evidence):
    features.save_attempt("qwen", {"results": [{"scope": "landscape", "state": "failed", "error": "commit reserve"}]})
    levels = {level["id"]: level for level in images.status("qwen")["qualification_levels"]}
    assert levels["landscape"]["outcome"] == "failed"
    assert levels["portrait"]["outcome"] == "not_checked"
    features.save_attempt("qwen-landscape", {"state": "failed", "error": "commit reserve"})
    features.save_attempt("qwen", {"scope": "portrait", "state": "cancelled", "error": "cancelled"})
    levels = {level["id"]: level for level in images.status("qwen")["qualification_levels"]}
    assert levels["landscape"]["outcome"] == "failed"
    assert levels["portrait"]["outcome"] == "cancelled"
    recorded = images.fingerprint("qwen", "landscape")
    features.save_pass("qwen-landscape", {"ok": True, "fingerprint": recorded, "model_sha256": {}})
    levels = {level["id"]: level for level in images.status("qwen")["qualification_levels"]}
    assert levels["landscape"]["outcome"] == "passed"


@pytest.mark.parametrize("failure,safety", [(RuntimeError("blank image"), False),
                                           (images.SafetyRefusal("native memory floor reached"), True)])
def test_group_skips_passes_saves_each_result_and_stops_only_for_safety(evidence, tmp_path, monkeypatch, failure, safety):
    passed = {"basic"}
    calls = []
    original = {"ok": True, "checked_at": "original"}
    features.save_pass("qwen", original)
    monkeypatch.setattr(images, "scope_status", lambda model, scope: {"qualified": scope in passed})

    def qualify(model, output, job_id, scope):
        calls.append(scope)
        if scope == "studio":
            features.save_attempt(model, {"ok": False, "observations": {"scope": scope}})
            raise failure
        receipt = {"ok": True, "results": [{"case": scope}]}
        features.save_pass(images.receipt_key(model, scope), receipt)
        passed.add(scope)
        return receipt

    monkeypatch.setattr(images, "qualify", qualify)
    report = images.qualify_group("qwen", tmp_path, "group-job", "studio")
    assert not report["ok"] and report["stopped_for_safety"] is safety
    assert report["results"][0]["state"] == "skipped"
    assert features.saved("qwen", "") == original
    assert calls == (["studio"] if safety else ["studio", "scout", "candidates"])
    assert report["not_run"] == (["scout", "candidates"] if safety else [])
    assert features.saved("qwen", "-attempt")["group"] == "studio"
    if not safety:
        calls.clear()
        report = images.qualify_group("qwen", tmp_path, "retry-job", "studio")
        assert calls == ["studio"]
        assert [result["state"] for result in report["results"]] == ["skipped", "failed", "skipped", "skipped"]


def test_group_cancellation_keeps_completed_recipe_receipts(evidence, tmp_path, monkeypatch):
    monkeypatch.setattr(images, "scope_status", lambda *args: {"qualified": False})

    def qualify(model, output, job_id, scope):
        if scope == "square":
            raise KeyboardInterrupt
        receipt = {"ok": True, "results": [{"case": scope}]}
        features.save_pass(images.receipt_key(model, scope), receipt)
        return receipt

    monkeypatch.setattr(images, "qualify", qualify)
    with pytest.raises(KeyboardInterrupt):
        images.qualify_group("qwen", tmp_path, "cancel-job", "images")
    assert features.saved("qwen-landscape", "")["ok"]
    assert not features.saved("qwen-square", "") and not features.saved("qwen-portrait", "")


def test_failed_rerun_keeps_the_original_native_pass(evidence, tmp_path, monkeypatch):
    prior = {"ok": True, "fingerprint": copy.deepcopy(evidence), "model_sha256": {}, "checked_at": "original"}
    features.save_pass("qwen", prior)

    def failed(*_):
        raise RuntimeError("memory observer stopped")

    monkeypatch.setattr(images, "run_case", failed)
    with pytest.raises(RuntimeError, match="memory observer stopped"):
        images.qualify("qwen", tmp_path / "case")
    assert features.saved("qwen", "") == prior
    assert features.saved("qwen", "-attempt")["error"] == "memory observer stopped"


def test_native_run_cannot_hide_a_primary_failure_with_cleanup(tmp_path, monkeypatch):
    import windows_memory
    import windows_process
    import windows_storyboard

    class Job:
        def close(self):
            raise RuntimeError("drain failed")

    def unavailable(*_, **__):
        raise RuntimeError("counter unavailable")

    monkeypatch.setattr(windows_process, "OwnedJob", Job)
    monkeypatch.setattr(windows_memory, "single_gpu_uuid", lambda: "GPU-example")
    monkeypatch.setattr(windows_storyboard, "tools",
                        lambda: SimpleNamespace(checker=lambda: SimpleNamespace(MemoryWatchdog=unavailable)))
    with pytest.raises(RuntimeError, match="counter unavailable") as failure:
        images.run_case("qwen", tmp_path)
    assert any("drain failed" in note for note in failure.value.__notes__)


@pytest.mark.parametrize("scope", ["basic", "studio", "candidates"])
@pytest.mark.parametrize("model", images.KINDS)
def test_native_pass_is_published_only_after_owned_work_drains(evidence, tmp_path, monkeypatch, scope, model):
    import threading
    import windows_memory
    import windows_process
    import windows_storyboard
    import spark_generate
    monkeypatch.setattr(spark_generate, "flux3_system_prompt", lambda: "Write a scene caption.")

    events = []
    commands = []

    class Job:
        def spawn(self, command, **kwargs):
            commands.append(command)
            return SimpleNamespace(poll=lambda: 0, returncode=0)

        def commitment(self):
            return {"peak_job_commit_bytes": 100 << 20}

        def close(self):
            events.append("drained")

    class Monitor:
        def __init__(self, *_, pressure_policy, **__):
            assert pressure_policy is images.native_pressure
            self.stopping = threading.Event()
            self.initial = object()

        def wait_ready(self):
            pass

        def check(self):
            pass

        def metrics(self):
            return {}

        def finish(self, check_result=True):
            assert check_result is False
            events.append("observer stopped")

    save = features.save_pass
    monkeypatch.setattr(features, "save_pass", lambda *args: (events.append("pass"), save(*args)))
    monkeypatch.setattr(windows_process, "OwnedJob", Job)
    monkeypatch.setattr(windows_memory, "single_gpu_uuid", lambda: "GPU-example")
    monkeypatch.setattr(windows_storyboard, "tools",
                        lambda: SimpleNamespace(checker=lambda: SimpleNamespace(MemoryWatchdog=Monitor)))
    outputs = []
    def checked(path, width=512, height=512):
        outputs.append((path.name, width, height))
        events.append("PNG checked")
    monkeypatch.setattr(images, "check_output", checked)
    assert images.qualify(model, tmp_path / "case", scope=scope)["ok"]
    settings = images.scope_settings(scope)
    assert events == ["PNG checked"] * settings["count"] + ["drained", "observer stopped", "pass"]
    command = commands[0]
    assert command[command.index("--model") + 1] == ("qwen" if model == "qwen-full" else model)
    assert command[command.index("--width") + 1] == str(settings["width"])
    assert command[command.index("--height") + 1] == str(settings["height"])
    expected_names = ["image.png"] if settings["count"] == 1 else [f"image_s{seed}.png" for seed in range(7, 11)]
    assert outputs == [(name, settings["width"], settings["height"]) for name in expected_names]
    if settings["count"] > 1:
        assert command[command.index("--seeds") + 1] == "7,8,9,10"
    assert ("--base" in command) is (model == "qwen-full")
    if model == "qwen-full":
        assert command[command.index("--steps") + 1] == "40"
        assert command[command.index("--cfg") + 1] == "4"
    assert "--no-upsample" not in command
    assert features.saved(images.receipt_key(model, scope), "")["fingerprint"]["model"] == model


@pytest.mark.parametrize("model", images.KINDS)
def test_qualification_graph_keeps_production_model_settings(monkeypatch, model):
    import hashlib
    import json
    import spark_generate as generation
    monkeypatch.setattr(generation, "flux3_system_prompt", lambda: "Write a scene caption.")
    monkeypatch.delenv("NEGATIVE", raising=False)
    args = SimpleNamespace(model=model, base=model == "qwen-full", width=512, height=512,
                           negative="", steps=None, cfg=4.0)
    if model.startswith("qwen"):
        graph = generation.qwen_image_workflow(args, images.PROMPT, 7)
        if model == "qwen-full":
            assert graph["1"]["inputs"]["unet_name"] == generation.QWEN_BASE
            assert graph["12"]["inputs"]["steps"] == 40 and graph["12"]["inputs"]["cfg"] == 4
        else:
            assert graph["10"]["inputs"]["sigmas"] == generation.TURBO_SIGMAS
    elif model.startswith("flux3k"):
        graph = generation.flux3_workflow(args, images.PROMPT, 7)
        _, steps, cfg = generation.FLUX3[model]
        assert graph["12"]["inputs"]["steps"] == steps
        assert graph["12"]["inputs"]["cfg"] == cfg
        assert graph["21"]["class_type"] == "TextGenerate"
        assert graph["5"]["inputs"]["text"] == ["21", 0]
    else:
        graph = generation.moss_workflow(args, images.PROMPT, 7)
        assert graph["12"]["inputs"]["steps"] == 25
        assert graph["12"]["inputs"]["cfg"] == 2.5
        assert graph["12"]["inputs"]["sampler_name"] == "er_sde"
        assert graph["7"]["inputs"]["text"] == generation.MOSS_NEGATIVE
    graph["16"]["inputs"]["filename_prefix"] = "qualification"
    expected = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
    assert images.recipe(model=model)["workflow_sha256"] == expected


@pytest.mark.parametrize("strict", [False, True])
def test_inflight_source_updates_follow_balanced_or_strict_policy(evidence, tmp_path, monkeypatch, strict):
    if strict:
        monkeypatch.setenv("STUDIO_QUALIFICATION_STRICT", "1")
    def run(*_):
        evidence["sources"]["producer"] = "compatible update"
        return {"elapsed_s": 1}
    monkeypatch.setattr(images, "run_case", run)
    if strict:
        with pytest.raises(images.SafetyRefusal, match="changed during"):
            images.qualify("qwen", tmp_path)
        assert not features.saved("qwen", "")
    else:
        assert images.qualify("qwen", tmp_path)["ok"]


def test_inflight_model_metadata_change_never_saves_a_new_pass(evidence, tmp_path, monkeypatch):
    def run(*_):
        evidence["models"] = {"weights": {"size": 1, "mtime_ns": 10}}
        return {"elapsed_s": 1}
    monkeypatch.setattr(images, "run_case", run)
    with pytest.raises(images.SafetyRefusal, match="changed during"):
        images.qualify("qwen", tmp_path)
    assert not features.saved("qwen", "")


@pytest.mark.parametrize("model", images.KINDS)
def test_every_model_registers_independent_checks_and_deadlines(tmp_path, monkeypatch, model):
    import windows_checks
    monkeypatch.setattr(images, "prerequisites", lambda _: "")
    assert windows_checks.parameters({"feature": model}) == {"feature": model, "scope": "basic"}
    command, env, outputs = windows_checks.build(tmp_path, {"feature": model, "group": "all"}, "job")
    assert command[command.index("--model") + 1] == model
    assert command[command.index("--group") + 1] == "all" and not env
    assert len(outputs) == 1
    deadline = windows_checks.timeout_seconds({"kind": "qualification", "params": {"feature": model, "group": "all"}})
    assert deadline > sum(images.timeout_seconds(model, scope) for scope in images.SCOPES)
    assert images.receipt_key(model, "studio") == model + "-studio"


@pytest.mark.parametrize("model", [kind for kind in images.KINDS if kind != "qwen"])
def test_turbo_pass_never_qualifies_another_image_model(evidence, monkeypatch, model):
    import spark_generate
    monkeypatch.setattr(spark_generate, "flux3_system_prompt", lambda: "Write a scene caption.")
    features.save_pass("qwen", {"ok": True, "fingerprint": copy.deepcopy(evidence), "model_sha256": {}})
    assert images.status("qwen")["qualified"]
    other = images.status(model)
    assert other["installed"] and other["check_available"] and not other["qualified"]
    assert other["next_scope"] == "basic" and not other["verified_scopes"]
    features.save_pass(model, {"ok": True, "fingerprint": images.fingerprint(model), "model_sha256": {}})
    assert images.status(model)["qualified"]
    assert images.status("qwen")["qualified"]


@pytest.mark.parametrize("model", images.KINDS)
def test_missing_models_offer_a_checker_but_refuse_a_run_without_files(tmp_path, monkeypatch, model):
    import windows_checks
    from fastapi import HTTPException
    monkeypatch.setattr(images.windows_cuda, "why_not", lambda: "")
    monkeypatch.setattr(images.windows_runtime, "image_files", lambda _: [tmp_path / "missing.safetensors"])
    monkeypatch.setattr(features, "folder", lambda: tmp_path / "evidence")
    found = images.status(model)
    assert found["check_available"] and not found["installed"] and not found["qualified"]
    with pytest.raises(HTTPException, match="Missing model/runtime files"):
        windows_checks.build(tmp_path, {"feature": model, "scope": "basic"}, "job")


def test_native_guard_does_not_extrapolate_transient_model_staging(monkeypatch):
    monkeypatch.delenv("SPARK_MEMORY_FLOOR_MB", raising=False)
    previous = SimpleNamespace(host_low_memory=False, host_free_mb=8192, host_commit_limit_mb=65536,
                               host_commit_used_mb=0, gpu_dedicated_free_mb=8192, at=1.0, validate=lambda: None)
    current = SimpleNamespace(**{**vars(previous), "host_commit_used_mb": 40000, "at": 1.25})
    assert images.native_pressure(current, previous) == ""


def test_failed_guard_keeps_readings_and_primary_error_through_queue_finalization(evidence, tmp_path, monkeypatch):
    import threading
    import windows_checks
    import windows_memory
    import windows_process
    import windows_storyboard

    class Job:
        def spawn(self, *_, **kwargs):
            assert kwargs["creationflags"] & 0x08000000
            return SimpleNamespace(poll=lambda: 0, returncode=0)

        def commitment(self):
            return {"peak_job_commit_bytes": 100 << 20}

        def close(self):
            pass

    class Monitor:
        def __init__(self, *_, **__):
            self.stopping = threading.Event()
            self.initial = object()

        def wait_ready(self):
            pass

        def check(self):
            raise RuntimeError("native commit floor reached")

        def metrics(self):
            return {"samples": 215, "minima": {"host_commit_headroom_mb": 4000},
                    "refused_sample": {"host_commit_used_mb": 60000}}

        def finish(self, check_result=True):
            assert check_result is False

    monkeypatch.setattr(windows_process, "OwnedJob", Job)
    monkeypatch.setattr(windows_memory, "single_gpu_uuid", lambda: "GPU-example")
    monkeypatch.setattr(windows_storyboard, "tools",
                        lambda: SimpleNamespace(checker=lambda: SimpleNamespace(MemoryWatchdog=Monitor)))
    with pytest.raises(RuntimeError, match="native commit floor reached"):
        images.qualify("qwen", tmp_path / "case", "current-job")
    job = {"id": "current-job", "kind": "qualification", "params": {"feature": "qwen"},
           "status": "failed", "finished": 1, "error": "generic wrapper error"}
    windows_checks.save_outcome(job)
    attempt = features.saved("qwen", "-attempt")
    assert attempt["error"] == job["error"] == "native commit floor reached"
    assert attempt["observations"]["measurements"]["samples"] == 215
    assert attempt["observations"]["peak_job_commit_mb"] == 100
    assert not attempt["observations"]["rendered"] and not features.saved("qwen", "")
    windows_checks.save_outcome({**job, "id": "different-job"})
    assert "observations" not in features.saved("qwen", "-attempt")


@pytest.mark.parametrize("changes,reason", [
    ({"host_low_memory": True}, "low physical"),
    ({"host_free_mb": 2047}, "host memory"),
    ({"host_commit_used_mb": 4097}, "commit memory"),
    ({"gpu_dedicated_free_mb": 511}, "dedicated GPU"),
])
def test_native_observer_keeps_production_floors(changes, reason, monkeypatch):
    monkeypatch.delenv("SPARK_MEMORY_FLOOR_MB", raising=False)
    sample = SimpleNamespace(host_low_memory=False, host_free_mb=8192, host_commit_limit_mb=8192,
                             host_commit_used_mb=0, gpu_dedicated_free_mb=8192, at=1.0, validate=lambda: None)
    for key, value in changes.items():
        setattr(sample, key, value)
    assert reason in images.native_pressure(sample)


def test_native_output_must_be_correct_size_and_nonblank(tmp_path):
    from PIL import Image
    target = tmp_path / "image.png"
    Image.new("RGB", (512, 512), "red").save(target)
    with pytest.raises(RuntimeError, match="nonblank"):
        images.check_output(target)
    image = Image.new("RGB", (512, 512), "red")
    image.paste("blue", (0, 0, 256, 512))
    image.save(target)
    images.check_output(target)
    image.resize((1024, 1024)).save(target)
    with pytest.raises(RuntimeError, match="512x512"):
        images.check_output(target)
