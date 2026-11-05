"""Native audio qualification tests: synthetic files, no model loads or hardware probes."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "spark"))
import windows_audio_checks as audio
import windows_checks
import windows_features as features
import windows_runtime


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(features, "folder", lambda: tmp_path / "evidence")
    monkeypatch.setattr(audio, "prerequisites", lambda kind: "")
    value = {"contract": 1, "kind": "voice", "recipe": audio.recipe("voice"), "hardware": {},
             "python_abi": "3.13", "models": {}, "engine": {"engine": "digest"}, "model_root": "example",
             "sources": {"voice": "old"}, "guard": {}}
    def fingerprint(kind):
        return {**copy.deepcopy(value), "kind": kind, "recipe": audio.recipe(kind)}
    monkeypatch.setattr(audio, "fingerprint", fingerprint)
    return value


@pytest.mark.parametrize("kind", audio.KINDS)
def test_audio_pass_is_success_only_and_survives_compatible_updates(evidence, tmp_path, monkeypatch, kind):
    observed = []
    def run(command, output, validate, result, timeout):
        observed.append(command)
        result.update(rendered=True, elapsed_s=1, measurements={"samples": 4})
        return result
    monkeypatch.setattr(audio.native, "observe_native", run)
    assert audio.qualify(kind, tmp_path / kind, "check-job")["ok"]
    assert audio.status(kind)["qualified"]
    command = observed[0]
    assert command[command.index("--feature") + 1] == kind and "--worker" in command
    assert windows_checks.parameters({"feature": kind}) == {"feature": kind}
    evidence["sources"]["voice"] = "new"
    assert audio.status(kind)["qualified"] and audio.status(kind)["runtime_updated"]
    monkeypatch.setenv("STUDIO_QUALIFICATION_STRICT", "1")
    assert not audio.status(kind)["qualified"]


def test_audio_failure_retains_prior_pass_and_queue_keeps_observations(evidence, tmp_path, monkeypatch):
    previous = {"ok": True, "fingerprint": audio.fingerprint("voice"), "model_sha256": {}, "results": []}
    features.save_pass("voice", previous)
    def failed(command, output, validate, result, timeout):
        result["measurements"] = {"samples": 10}
        raise RuntimeError("native memory floor reached")
    monkeypatch.setattr(audio.native, "observe_native", failed)
    with pytest.raises(RuntimeError, match="native memory floor"):
        audio.qualify("voice", tmp_path / "check", "job1")
    windows_checks.save_outcome({"id": "job1", "params": {"feature": "voice"}, "status": "failed",
                                "finished": 1, "error": "outer error"})
    assert features.saved("voice", "") == previous
    assert features.saved("voice", "-attempt")["observations"]["measurements"]["samples"] == 10
    assert audio.status("voice")["qualified"]


@pytest.mark.parametrize("kind", audio.KINDS)
def test_audio_scope_is_not_broader_than_the_checked_recipe(kind):
    settings = audio.recipe(kind)
    params = {**settings["sampling"], "live": False, "asr": False}
    params.update({"text": audio.VOICE_TEXT} if kind == "voice" else
                  {"seconds": 10, "lyrics": "[Instrumental]", "engine": "music3"})
    audio.require_scope(kind, params)
    changes = ([{"text": "a " * 21}, {"text": "a" * 121}, {"ref_audio": "voice.wav"}, {"live": True},
                {"asr": True}, {"text": "<|speaker:1|> Hello"}, {"text": "[whisper] Hello"},
                {"temperature": 0.5}] if kind == "voice" else
               [{"seconds": 60}, {"lyrics": "[Verse] Hello"}, {"engine": "yue2"}, {"asr": True},
                {"live": True}, {"steps": 10}])
    for changed in changes:
        with pytest.raises(ValueError):
            audio.require_scope(kind, {**params, **changed})


@pytest.mark.parametrize("kind", audio.KINDS)
def test_native_audio_queue_build_uses_supervision_not_draft_caps(evidence, tmp_path, kind):
    command, env, outputs = windows_checks.build(tmp_path, {"feature": kind}, "job")
    assert command[command.index("--feature") + 1] == kind
    job = {"kind": "qualification", "params": {"feature": kind}}
    assert windows_checks.native_image_check(job) and windows_checks.gpu_check(job)
    assert windows_checks.timeout_seconds(job) > audio.TIMEOUT_S
    assert not env and len(outputs) == 1


def test_voice_installation_checks_index_shards_and_codec_without_h3(tmp_path, monkeypatch):
    import windows_cuda
    root = tmp_path
    monkeypatch.setattr(windows_cuda, "paths", lambda: {
        "drama3_bin": root / "engine.exe", "drama3_lib": root / "engine.dll"})
    monkeypatch.setattr(windows_runtime, "model_root", lambda: root / "models")
    monkeypatch.setattr(windows_cuda, "configuration_error", lambda: "")
    for name in ("DRAMA3_CUDA_BIN", "DRAMA3_CUDA_LIB", "DRAMA3_CUDA_MODEL", "DRAMA3_CUDA_CODEC"):
        monkeypatch.delenv(name, raising=False)
    model = root / "models" / "drama-3"
    model.mkdir(parents=True)
    (model / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"a": "shard.safetensors"}}))
    for path in (root / "engine.exe", root / "engine.dll", root / "codec-v1.safetensors",
                 model / "config.json", model / "shard.safetensors"):
        path.write_text("test")
    monkeypatch.setattr(audio.windows_media, "search_path", lambda _: "")
    import shutil
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: "tool")
    assert not audio.prerequisites("voice")
    (model / "shard.safetensors").unlink()
    assert "shard.safetensors" in audio.prerequisites("voice")
    (model / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"a": "../escape"}}))
    with pytest.raises(ValueError, match="outside"):
        audio.files("voice")


def test_audio_state_does_not_confuse_installed_with_current_memory(evidence, monkeypatch):
    features.save_pass("voice", {"ok": True, "fingerprint": audio.fingerprint("voice"), "model_sha256": {}})
    found = windows_runtime.gate_native_state("voice", {"available": False, "why": "Not enough GPU room", "backend": "cuda"},
                                              {"enabled": True})
    assert found["installed"] and found["qualified"] and not found["available"]
    assert found["why"] == "Not enough GPU room"
    ready = windows_runtime.gate_native_state("voice", {"available": True, "why": "", "backend": "cuda"},
                                              {"enabled": True})
    assert ready["available"] and ready["text_limit"] == 120 and not ready["live"]
    assert ready["reference_seconds"] is None


def test_music_state_preserves_native_engine_selection_and_exact_seconds(evidence):
    features.save_pass("music", {"ok": True, "fingerprint": audio.fingerprint("music"), "model_sha256": {}})
    raw = {"available": True, "route": "cuda", "why": "", "seconds": [10, 300]}
    found = windows_runtime.gate_native_state("music (music3)", raw, {"enabled": True})
    assert found["qualified"] and found["seconds"] == [10, 10] and not found["stream"]
    unrestricted = windows_runtime.gate_native_state("music (music3)", raw, {"enabled": False})
    assert unrestricted["seconds"] == [10, 300]
    other = windows_runtime.gate_native_state("music", {**raw, "engine": "yue2"}, {"enabled": True})
    assert not other["qualified"] and not other["available"]


@pytest.mark.parametrize("kind", audio.KINDS)
def test_audio_pass_enforces_scope_at_native_admission(evidence, monkeypatch, kind):
    features.save_pass(kind, {"ok": True, "fingerprint": audio.fingerprint(kind), "model_sha256": {}})
    monkeypatch.setattr(windows_runtime, "mode", lambda: {"enabled": True})
    params = {**audio.recipe(kind)["sampling"], "live": False, "asr": False}
    params.update({"text": audio.VOICE_TEXT} if kind == "voice" else
                  {"seconds": 10, "lyrics": "[Instrumental]", "engine": "music3"})
    windows_runtime.require_native(kind, params)
    with pytest.raises(ValueError):
        windows_runtime.require_native(kind, {**params, "asr": True})


def test_unqualified_check_does_not_block_existing_comfy_music_route_when_gating_off(evidence, monkeypatch):
    import music
    import windows_cuda
    monkeypatch.setattr(windows_runtime, "mode", lambda: {"enabled": False})
    monkeypatch.setattr(windows_cuda, "configuration_error", lambda: "")
    monkeypatch.setattr(music, "engine_state", lambda _: {"available": True, "route": "comfy", "why": ""})
    windows_runtime.require_native("music", {"engine": "music3"})


@pytest.mark.parametrize("kind", audio.KINDS)
def test_audio_output_accepts_finite_non_silent_correct_shape(monkeypatch, kind):
    import array
    import shutil
    def run(command, **kwargs):
        if "-show_streams" in command:
            return SimpleNamespace(stdout=json.dumps({"streams": [{"codec_type": "audio", "sample_rate": "44100",
                "channels": 1 if kind == "voice" else 2}], "format": {"duration": "5"}}))
        return SimpleNamespace(stdout=array.array("f", [0.1, -0.1]).tobytes())
    monkeypatch.setattr(audio.subprocess, "run", run)
    monkeypatch.setattr(shutil, "which", lambda name, **kwargs: name)
    audio.check_wav(kind, Path("example.wav"))


@pytest.mark.parametrize("kind", audio.KINDS)
def test_audio_worker_uses_production_take_writer_and_drains_engine(tmp_path, monkeypatch, kind):
    import voice
    import music
    for module in (voice, music):
        monkeypatch.setattr(module, "attach", lambda *args: None)
    events = []
    one = SimpleNamespace(name="cuda", process=SimpleNamespace(poll=lambda: 0), check=lambda: "",
                          release=lambda why: events.append("released"))
    monkeypatch.setattr(voice, "backend", lambda: one)
    monkeypatch.setattr(music, "engine", lambda name: one)
    def perform(job):
        events.append(kind)
        assert job["params"]["live"] is False and job["params"]["asr"] is False
        audio.require_scope(kind, job["params"])
        return True
    monkeypatch.setattr(voice if kind == "voice" else music, "perform", perform)
    audio.worker(kind, tmp_path)
    assert events == [kind, "released"]


def test_audio_worker_refuses_undrained_native_engine(tmp_path, monkeypatch):
    import voice
    import music
    for module in (voice, music):
        monkeypatch.setattr(module, "attach", lambda *args: None)
    monkeypatch.setattr(voice, "backend", lambda: SimpleNamespace(
        name="cuda", check=lambda: "", process=SimpleNamespace(poll=lambda: None), release=lambda why: None))
    monkeypatch.setattr(voice, "perform", lambda job: True)
    with pytest.raises(RuntimeError, match="did not drain"):
        audio.worker("voice", tmp_path)


@pytest.mark.parametrize("kind", audio.KINDS)
@pytest.mark.parametrize("invalid", ["silent", "duration", "channels", "nan"])
def test_audio_output_validation_rejects_invalid_wavs(monkeypatch, kind, invalid):
    import array
    import shutil
    channels = 1 if kind == "voice" else 2
    def run(command, **kwargs):
        if "-show_streams" in command:
            return SimpleNamespace(stdout=json.dumps({"streams": [{"codec_type": "audio", "sample_rate": "44100",
                "channels": 3 if invalid == "channels" else channels}],
                "format": {"duration": "30" if invalid == "duration" else "5"}}))
        return SimpleNamespace(stdout=array.array("f", [float("nan") if invalid == "nan" else 0]).tobytes())
    monkeypatch.setattr(audio.subprocess, "run", run)
    monkeypatch.setattr(shutil, "which", lambda name, **kwargs: name)
    with pytest.raises(RuntimeError):
        audio.check_wav(kind, Path("example.wav"))
