"""Isolated Small SFX API, CUDA dispatch, safety, qualification and browser controls."""
import json
import importlib.util
import sys
import subprocess
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException

from test_windows_storyboard import client, enabled, server  # noqa: F401
from test_shell_page import run
import sound
import sound_seed
import windows_audio3 as audio
import windows_checks
import windows_features
import windows_runtime


@pytest.fixture
def ready(enabled, monkeypatch):
    monkeypatch.setattr(audio, "prerequisites", lambda: "")
    monkeypatch.setattr(audio, "status", lambda: {"installed": True, "qualified": True, "available": True, "why": ""})
    monkeypatch.setattr(audio, "check_room", lambda: None)
    return enabled[0]


def test_seed_resolution_is_random_once_and_preserves_explicit_zero(monkeypatch):
    drawn = []
    monkeypatch.setattr(sound_seed.secrets, "randbits", lambda bits: drawn.append(bits) or 123456)
    params = sound.parameters("sfx", {"prompt": "Rain."})
    assert params["seed"] == 123456
    assert sound.parameters("sfx", params) == params and drawn == [32]
    assert sound.parameters("foley", {"seed": 0})["seed"] == 0
    assert sound.parameters("sfx", {"seed": None})["seed"] == 123456
    for seed in (-1, 2 ** 32, True, 1.5, "7"):
        with pytest.raises(HTTPException):
            sound.parameters("sfx", {"seed": seed})


@pytest.mark.parametrize("change", [{"seconds": 0}, {"seconds": 6}, {"seconds": float("nan")},
    {"seconds": True}, {"steps": 100}, {"cfg": 7}, {"negative": "music"}, {"prompt": ""},
    {"prompt": "x" * 601}, {"sound_model": sound.SFX_MODEL}, {"video": "clip.mp4"}])
def test_small_sfx_recipe_refuses_unsupported_settings(change):
    with pytest.raises((ValueError, HTTPException)):
        audio.parameters({"sound_model": audio.MODEL, "prompt": "Rain.", **change})


def test_small_sfx_keeps_words_and_uses_its_own_recipe():
    value = audio.parameters({"sound_model": audio.MODEL, "prompt": "  A door slams.  ", "seconds": 1.5, "seed": 0})
    assert value == {"sound_model": audio.MODEL, "prompt": "  A door slams.  ", "seconds": 1.5,
                     "seed": 0, "negative": "", "steps": 8, "cfg": 1.0}


def test_isolated_worker_reaches_identity_without_web_or_gpu_imports(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"sound_model": audio.MODEL, "prompt": "Door.", "seed": 0,
                               "seconds": 1, "output": str(tmp_path)}))
    (tmp_path / "admitted").touch()
    script = """
import importlib.abc
import sys
sys.path.insert(0, sys.argv[1])
plan_path = sys.argv[2]
class BlockDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in ("sound", "fastapi"):
            raise RuntimeError("Web dependencies must not enter the isolated worker")
        if fullname.split(".")[0] in ("torch", "stable_audio_3", "numpy"):
            raise RuntimeError("GPU imports intentionally blocked after identity")
sys.meta_path.insert(0, BlockDependencies())
import windows_audio3
import windows_backend_handle
windows_backend_handle.identity = lambda: {"pid": 123, "created": 456}
sys.argv = ["windows_audio3.py", "--worker", "--plan", plan_path]
assert windows_audio3.main() == 1
assert "sound" not in sys.modules and "fastapi" not in sys.modules
"""
    done = subprocess.run([sys.executable, "-I", "-c", script, str(audio.PACK / "app"), str(plan)],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "GPU imports intentionally blocked after identity" in done.stderr
    assert json.loads((tmp_path / "identity.json").read_text()) == {"pid": 123, "created": 456}


def test_new_model_queues_without_old_sound_or_h3_installation(ready, monkeypatch):
    monkeypatch.setattr(server.windows_sound, "preflight", lambda: pytest.fail("Old model was used"))
    monkeypatch.setattr(server.windows_cuda, "require", lambda: pytest.fail("H3 was used"), raising=False)
    monkeypatch.setattr(sound_seed.secrets, "randbits", lambda bits: 99)
    answer = ready.post("/api/jobs", json={"kind": "sfx", "params": {"sound_model": audio.MODEL, "prompt": "Rain."}})
    assert answer.status_code == 200, answer.text
    params = answer.json()["params"]
    assert params["seed"] == 99 and params["steps"] == 8 and params["cfg"] == 1
    assert windows_runtime.bounded("sfx", params)
    assert ready.post("/api/jobs", json={"kind": "sfx", "params": params, "sparks": 2}).status_code == 400
    state = ready.get("/api/sound/state").json()
    assert state["default_model"] == audio.MODEL
    new = state["models"][audio.MODEL]
    assert new["ready"] and new["seconds"] == [1, 5] and new["negative"] is False
    assert "Gemma" in new["licence"]


def test_windows_api_defaults_to_small_sfx_and_never_silently_uses_legacy(ready, monkeypatch):
    monkeypatch.setattr(sound, "WINDOWS", True)
    monkeypatch.setattr(server.windows_sound, "preflight", lambda: pytest.fail("Legacy default was used"))
    answer = ready.post("/api/jobs", json={"kind": "sfx", "params": {"prompt": "Rain.", "seed": 0}})
    assert answer.status_code == 200, answer.text
    assert answer.json()["params"]["sound_model"] == audio.MODEL
    state = ready.get("/api/sound/state").json()
    assert state["default_model"] == audio.MODEL and state["seconds"]["sfx"] == [1, 5]
    assert state["kinds"]["sfx"]["id"] == audio.MODEL
    monkeypatch.setattr(audio, "status", lambda: {"installed": False, "qualified": False,
                                                "available": False, "why": "Install Small SFX"})
    rejected = ready.post("/api/jobs", json={"kind": "sfx", "params": {"prompt": "Rain."}})
    assert rejected.status_code >= 400 and "Install Small SFX" in rejected.text


@pytest.mark.parametrize("windows,cuda,expected", [(False, False, sound.SFX_MODEL),
                                                 (True, False, audio.MODEL), (False, True, audio.MODEL)])
def test_sound_default_is_platform_specific_and_preserves_explicit_selection(monkeypatch, windows, cuda, expected):
    monkeypatch.setattr(sound, "WINDOWS", windows)
    monkeypatch.setattr(sound, "WINCUDA", cuda)
    assert sound.parameters("sfx", {"seed": 7})["sound_model"] == expected
    assert sound.parameters("sfx", {"seed": 7, "sound_model": sound.SFX_MODEL})["sound_model"] == sound.SFX_MODEL


def test_browser_uses_platform_default_only_before_user_selects_a_model():
    result = run("""
      answers["/api/sound/state"] = {default_model:"stable-audio-3-small-sfx",
        kinds:{sfx:{ready:true},foley:{ready:false}},windows:true};
      answers["/api/sound/takes"] = []; answers["/api/library"] = [];
      await sfxLoad(); out.initial = SFX.model;
      SFX.model = "stable-audio-open-1.0"; await sfxLoad(); out.selected = SFX.model;
    """)
    assert result == {"initial": audio.MODEL, "selected": sound.SFX_MODEL}


def test_take_uses_new_runner_not_the_legacy_comfy_route(ready, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "HOME", tmp_path)
    monkeypatch.setattr(server.windows_storyboard, "render_job", lambda *args: pytest.fail("Legacy sound renderer selected"))
    params = audio.parameters({"sound_model": audio.MODEL, "prompt": "Rain.", "seed": 7})
    job = {"id": "new-take", "kind": "sfx", "params": params, "status": "running"}
    commands = []
    monkeypatch.setattr(server, "run", lambda job, command, env, label="": commands.append(command) or True)
    done, _, outputs = server.take(job, params)
    assert done and commands[0][3].endswith("windows_audio3.py")
    assert outputs == [str(tmp_path / "sound" / "sfx" / "new-take" / "sound.wav")]
    plan = json.loads((Path(outputs[0]).parent / "plan.json").read_text())
    assert plan["sound_model"] == audio.MODEL and plan["seed"] == 7


def test_qualification_registration_and_deadline(ready, tmp_path):
    params = {"feature": audio.MODEL}
    assert windows_checks.parameters(params) == params
    command, _, _ = windows_checks.build(tmp_path, params, "check1")
    assert "--qualify" in command and "--job-id" in command
    assert windows_checks.gpu_check({"kind": "qualification", "params": params})
    assert windows_checks.timeout_seconds({"kind": "qualification", "params": params}) > 2 * audio.TIMEOUT_S
    assert windows_checks.CHECKS[audio.MODEL].public()["title"] == audio.TITLE
    requirements = windows_checks.CHECKS[audio.MODEL].public()["requirements"]
    assert requirements[0] == {"pool": "host", "required_mb": 2048, "basis": "policy", "reserve_mb": 0}
    assert requirements[1]["required_mb"] + requirements[1]["reserve_mb"] == 14336
    assert requirements[2]["required_mb"] + requirements[2]["reserve_mb"] == 18432


def test_evidence_gating_is_independent_and_off_never_invents_passes(monkeypatch):
    monkeypatch.setattr(audio, "prerequisites", lambda: "")
    monkeypatch.setattr(audio, "fingerprint", lambda: {"contract": 1, "versions": {}, "sources": {}})
    monkeypatch.setattr(windows_features, "saved", lambda *args: {})
    monkeypatch.setattr(windows_runtime, "mode", lambda: {"enabled": True})
    assert not audio.status()["available"]
    monkeypatch.setattr(windows_runtime, "mode", lambda: {"enabled": False})
    found = audio.status()
    assert found["available"] and not found["qualified"]
    calls = []
    monkeypatch.setattr(audio, "check_room", lambda: calls.append("live-admission"))
    audio.preflight()
    assert calls == ["live-admission"]


def test_saved_qualification_matches_json_round_tripped_fingerprint(monkeypatch):
    import windows_memory
    import windows_qualification
    monkeypatch.setattr(audio, "inventory", lambda: {"files": {"weight": {"size": 7}}})
    monkeypatch.setattr(audio, "upstream_sources", lambda: {"model.py": "source-hash"})
    names = ("torch", "torchaudio", "numpy", "transformers", "huggingface-hub",
             "stable-audio-3", "soundfile", "safetensors")
    monkeypatch.setattr(audio.metadata, "distributions", lambda **kwargs: [
        SimpleNamespace(metadata={"Name": name}, version="1.0") for name in names])
    monkeypatch.setattr(windows_memory, "single_gpu_uuid", lambda: "GPU-example")
    monkeypatch.setattr(windows_memory, "physical_memory_mb", lambda: 65536)
    monkeypatch.setattr(windows_memory, "host_memory", lambda: (32460, 13000))
    monkeypatch.setattr(windows_memory, "dedicated_capacity", lambda _: 31232)
    monkeypatch.setattr(windows_qualification, "gpu_version_contract", lambda *args: {"contract": 1})
    current = audio.fingerprint()
    recorded = json.loads(json.dumps(current))
    assert current == recorded
    monkeypatch.setattr(audio, "prerequisites", lambda: "")
    monkeypatch.setattr(windows_features, "saved", lambda *args: {"ok": True, "fingerprint": recorded})
    monkeypatch.setattr(windows_runtime, "mode", lambda: {"enabled": True})
    for strict in ("0", "1"):
        monkeypatch.setenv("STUDIO_QUALIFICATION_STRICT", strict)
        assert audio.status()["qualified"] and audio.status()["available"]


def test_missing_and_changed_files_fail_closed(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(audio, "models", lambda: tmp_path)
    path = tmp_path / "weight"
    path.write_bytes(b"weights")
    manifest = {"source_revision": audio.SOURCE_REVISION,
                "files": {"weight": {"size": 7, "sha256": audio.digest(path, 7, path.stat().st_mtime_ns)}}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert audio.inventory() == manifest
    original_mtime = path.stat().st_mtime_ns
    path.write_bytes(b"changed")
    os.utime(path, ns=(path.stat().st_atime_ns, original_mtime + 1_000_000_000))
    with pytest.raises(RuntimeError, match="changed"):
        audio.inventory()
    manifest["files"] = {"../outside": {"size": 1}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="outside"):
        audio.inventory()


def test_browser_model_random_and_locked_seed_controls():
    result = run("""
      answers["/api/sound/state"] = { windows: true, spark: false, seconds: {sfx:[5,5],foley:[1,5]},
        kinds: {sfx:{ready:false},foley:{ready:false}},
        models: {"stable-audio-open-1.0": {id:"stable-audio-open-1.0",label:"Stable Audio Open",ready:false,seconds:[5,5]},
          "stable-audio-3-small-sfx": {id:"stable-audio-3-small-sfx",label:"Stable Audio 3 Small SFX",
            ready:true,bounded:true,seconds:[1,5],negative:false,licence:"Gemma"}} };
      answers["/api/sound/takes"] = [];
      await sfxLoad(); SFX.model = "stable-audio-3-small-sfx"; SFX.prompt = "Door."; SFX.negative = "music";
      let page = viewFoley(); out.text = text(page); out.seconds = SFX.seconds;
      out.models = find(page, n => n.tag === "select" && n.attrs["aria-label"] === "Sound model").kids.length;
      SFX.seed = -1; await sfxMake();
      SFX.seedLocked = true; SFX.seed = 0; await sfxMake(); await sfxMake();
      out.requests = calls.filter(c => c[0] === "/api/jobs").map(c => c[1].params);
      out.locked = SFX.seedLocked;
    """)
    assert result["models"] == 2 and result["seconds"] == 5
    assert "eight passes" in result["text"] and "Lock seed" in result["text"]
    requests = result["requests"]
    assert 0 <= requests[0]["seed"] <= 2 ** 32 - 1
    assert requests[1]["seed"] == requests[2]["seed"] == 0
    assert all(row["sound_model"] == audio.MODEL and row["negative"] == "" for row in requests)


def test_browser_again_restores_model_duration_and_locked_seed():
    result = run("""
      SFX.seedLocked = true;
      const take = sfxTake({id:"t",kind:"sfx",sound_model:"stable-audio-3-small-sfx",prompt:"Door.",seconds:3,seed:0});
      const again = find(take, n => n.tag === "button" && text(n) === "again");
      again.attrs.onclick();
      out.value = {model:SFX.model,seconds:SFX.seconds,seed:SFX.seed,locked:SFX.seedLocked};
    """)
    assert result["value"] == {"model": audio.MODEL, "seconds": 3, "seed": 0, "locked": True}


def test_qualify_records_both_endpoints_and_does_not_remove_old_pass_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "prerequisites", lambda: "")
    monkeypatch.setattr(audio, "fingerprint", lambda: {"contract": 1})
    seen, passes, attempts = [], [], []
    monkeypatch.setattr(audio, "render", lambda plan, qualification=False: seen.append((plan, qualification)) or {"rendered": True})
    monkeypatch.setattr(windows_features, "save_pass", lambda *args: passes.append(args))
    monkeypatch.setattr(windows_features, "save_attempt", lambda *args: attempts.append(args))
    receipt = audio.qualify(tmp_path / "new-check", "j")
    assert [plan["seconds"] for plan, _ in seen] == [1, 5]
    assert all(qualification and plan["steps"] == 8 and plan["cfg"] == 1 for plan, qualification in seen)
    assert receipt["ok"] and len(passes) == 1
    def fail(*args, **kwargs):
        raise RuntimeError("memory sampler stopped answering")
    monkeypatch.setattr(audio, "render", fail)
    with pytest.raises(RuntimeError):
        audio.qualify(tmp_path / "failed-check", "j2")
    assert len(passes) == 1 and attempts[-1][1]["ok"] is False


def test_audio_validation_requires_exact_shape_non_silence_and_headroom(monkeypatch):
    data = {"audio": np.full((44100, 2), .1), "rate": 44100}
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(read=lambda *args, **kwargs: (data["audio"], data["rate"])))
    assert audio.check_audio(Path("stand-in.wav"), 1)["seconds"] == 1
    for values, rate in ((np.zeros((44100, 2)), 44100), (np.ones((44100, 2)), 44100),
                         (np.full((44100, 2), np.nan), 44100), (np.zeros((44100, 1)), 44100),
                         (np.ones((22050, 2)) * .1, 44100), (np.ones((44100, 2)) * .1, 48000)):
        data.update(audio=values, rate=rate)
        with pytest.raises(RuntimeError):
            audio.check_audio(Path("stand-in.wav"), 1)


def test_new_policy_adds_commit_reserve_without_replacing_existing_pressure(monkeypatch):
    import windows_memory
    monkeypatch.setattr(windows_memory, "pressure", lambda *args: "")
    snapshot = SimpleNamespace(host_commit_limit_mb=20000, host_commit_used_mb=19000, host_free_mb=2048)
    assert "commit headroom" in audio.memory_pressure(snapshot)
    snapshot.host_commit_used_mb = 17000
    assert audio.memory_pressure(snapshot) == ""
    snapshot.host_free_mb = 2047
    assert "host memory" in audio.memory_pressure(snapshot)
    monkeypatch.setattr(windows_memory, "pressure", lambda *args: "dedicated GPU reserve")
    assert audio.memory_pressure(snapshot) == "dedicated GPU reserve"


@pytest.mark.parametrize("free,commit,gpu,low,floor,error", [
    (2048, 18432, 14336, False, "", None),
    (4096, 18432, 14336, False, "", None),
    (2047, 18432, 14336, False, "", "host reserve policy"),
    (4096, 18432, 14336, True, "", "low-memory signal: True"),
    (4096, 18431, 14336, False, "", "available 18431 MiB"),
    (4096, 18432, 14335, False, "", "dedicated GPU"),
    (4096, 18432, 14336, False, " 4096 ", None),
    (4096, 18432, 14336, False, "4097", "4097 MiB free"),
    (4096, 18432, 14336, False, "0", "positive integer"),
    (4096, 18432, 14336, False, "invalid", "positive integer"),
])
def test_host_reserve_is_independent_of_gpu_and_commit_caps(monkeypatch, free, commit, gpu, low, floor, error):
    import windows_memory
    monkeypatch.setenv("SPARK_MEMORY_FLOOR_MB", floor)
    monkeypatch.setattr(windows_memory, "host_memory", lambda: (30720, free))
    signals = SimpleNamespace(read=lambda: (10000, 10000 + commit, 0, low))
    class Signals:
        def __enter__(self):
            return signals
        def __exit__(self, *_):
            pass
    monkeypatch.setattr(windows_memory, "WindowsSignals", Signals)
    monkeypatch.setattr(windows_memory, "single_gpu_uuid", lambda: "GPU-example")
    monkeypatch.setattr(windows_memory, "WddmMemory", lambda _: SimpleNamespace(read=lambda: (100352, gpu)))
    if error:
        with pytest.raises((RuntimeError, ValueError), match=error):
            audio.check_room()
    else:
        audio.check_room()


def test_live_monitor_keeps_native_signals_configured_reserves_and_freshness(monkeypatch):
    from dataclasses import replace
    from windows_memory import Snapshot
    snapshot = Snapshot(131072, 30720, 2048, 100352, 14336, 1,
                        10000, 28432, 100, False, 100352, 14336)
    monkeypatch.delenv("SPARK_MEMORY_FLOOR_MB", raising=False)
    assert audio.memory_pressure(snapshot) == ""
    monkeypatch.setenv("SPARK_MEMORY_FLOOR_MB", "4096")
    assert "4096 MiB reserve" in audio.memory_pressure(snapshot)
    monkeypatch.delenv("SPARK_MEMORY_FLOOR_MB")
    assert audio.memory_pressure(replace(snapshot, host_low_memory=True)) == "Windows reports low physical memory"
    previous = replace(snapshot, at=-2)
    with pytest.raises(ValueError, match="stale"):
        audio.memory_pressure(snapshot, previous)


@pytest.mark.parametrize("startup_error", [None, "ModuleNotFoundError: No module named 'example_dependency'"])
def test_supervisor_admits_by_owned_handle_and_publishes_only_after_drain(tmp_path, monkeypatch, startup_error):
    import windows_storyboard
    import windows_process
    import windows_memory
    events = []
    output = tmp_path / "take"
    output.mkdir()
    plan = audio.parameters({"sound_model": audio.MODEL, "prompt": "Door.", "seed": 0})
    plan.update(output=str(output), home=str(tmp_path), id="take")
    monkeypatch.setattr(audio, "preflight", lambda: None)
    monkeypatch.setattr(audio, "inventory", lambda: {})
    monkeypatch.setattr(windows_memory, "single_gpu_uuid", lambda: "GPU-example")
    class Process:
        returncode = 0
        def poll(self):
            if startup_error:
                return 1
            return None if not (output / "finished").exists() else 0
        def wait(self, timeout):
            events.append("worker-drained")
            return 0
    class Owned:
        def __init__(self, **kwargs):
            events.append(("limits", kwargs))
        def spawn(self, command, **kwargs):
            events.append(("spawn", kwargs["env"]))
            assert not (output / "admitted").exists()
            if startup_error:
                kwargs["stdout"].write(startup_error)
            else:
                (output / "identity.json").write_text('{"pid":123,"created":456}')
            return Process()
        def commitment(self):
            return {"peak_job_commit_bytes": 1024}
        def close(self):
            events.append("owned-drained")
            assert not (output / "sound.take.json").exists()
    class Monitor:
        def __init__(self, *args, **kwargs):
            assert kwargs["wddm_device_uuid"] == "GPU-example"
            assert kwargs["pressure_policy"] is audio.memory_pressure
            self.stopping = SimpleNamespace(set=lambda: events.append("stopping"))
        def wait_ready(self):
            events.append("fresh-memory")
        def check(self):
            pass
        def start_backend_handle(self, process, identity):
            assert identity == {"pid": 123, "created": 456}
            events.append("identity-validated")
            (output / "sound.wav").write_bytes(b"stand-in")
            (output / "audio.json").write_text('{"seconds":5,"rate":44100}')
        def begin_backend_shutdown(self):
            events.append("begin-drain")
        def metrics(self):
            return {"samples": 2}
        def finish(self, **kwargs):
            events.append("monitor-drained")
    monkeypatch.setattr(windows_process, "OwnedJob", Owned)
    monkeypatch.setattr(windows_storyboard, "tools", lambda: SimpleNamespace(checker=lambda: SimpleNamespace(MemoryWatchdog=Monitor)))
    if startup_error:
        with pytest.raises(RuntimeError, match="example_dependency"):
            audio.render(plan)
        assert "owned-drained" in events and "monitor-drained" in events
        assert not (output / "sound.take.json").exists()
        return
    result = audio.render(plan)
    assert result["rendered"]
    assert events[0] == ("limits", {"process_commit_mb": audio.PROCESS_MB, "job_commit_mb": audio.JOB_MB})
    assert events.index("fresh-memory") < events.index("identity-validated") < events.index("owned-drained")
    env = next(row[1] for row in events if isinstance(row, tuple) and row[0] == "spawn")
    assert env["HF_HUB_OFFLINE"] == "1" and env["CUDA_VISIBLE_DEVICES"] == "GPU-example"
    take = json.loads((output / "sound.take.json").read_text())
    assert take["seed"] == 0 and take["sound_model"] == audio.MODEL and take["path"] == "take/sound.wav"
    assert not (output / "identity.json").exists() and not (output / "admitted").exists()


def test_dependency_patch_applies_to_pinned_upstream_and_is_repeatable(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('''[project]
dependencies = [
    "packaging>=26.0",
    "safetensors>=0.7.0",
    "torch==2.7.1",
    "torchaudio==2.7.1",
    "tqdm>=4.67.3",
    "huggingface-hub>=1.7.1",
]
''')
    patch = audio.PACK / "scripts" / "audio3-windows-dependencies.patch"
    result = subprocess.run(["git", "apply", str(patch)], cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert '"torch==2.14.0+cu134"' in path.read_text()
    result = subprocess.run(["git", "apply", "--reverse", "--check", str(patch)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell native stderr behavior")
@pytest.mark.parametrize("already_applied", [False, True])
def test_windows_powershell_installer_probe_handles_first_and_repeat_setup(tmp_path, already_applied):
    powershell = shutil.which("powershell")
    if not powershell:
        pytest.skip("Windows PowerShell is unavailable")
    path = tmp_path / "pyproject.toml"
    path.write_text('''[project]
dependencies = [
    "packaging>=26.0",
    "safetensors>=0.7.0",
    "torch==2.7.1",
    "torchaudio==2.7.1",
    "tqdm>=4.67.3",
    "huggingface-hub>=1.7.1",
]
''')
    scripts = audio.PACK / "scripts"
    patch = scripts / "audio3-windows-dependencies.patch"
    if already_applied:
        subprocess.run(["git", "apply", str(patch)], cwd=tmp_path, check=True, capture_output=True, timeout=10)
    installer = (scripts / "setup-windows-audio3.ps1").read_text()
    probe = installer[installer.index("    $Patch ="):installer.index("    Write-Host 'Installing")]
    quote = lambda value: str(value).replace("'", "''")
    command = f"$ErrorActionPreference = 'Stop'; $Source = '{quote(tmp_path)}'; $PSScriptRoot = '{quote(scripts)}';\n{probe}"
    result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert '"torch==2.14.0+cu134"' in path.read_text()


def test_worker_uses_cuda_and_the_upstream_eight_pass_api(tmp_path, monkeypatch):
    import windows_backend_handle
    events, generated = [], []
    monkeypatch.setattr(windows_backend_handle, "identity", lambda: {"pid": 123, "created": 456})
    (tmp_path / "admitted").touch()
    (tmp_path / "finished").touch()
    waveform = np.ones((1, 2, 44100), dtype=np.float32) * .1
    class Tensor:
        ndim, shape = waveform.ndim, waveform.shape
        def __getitem__(self, index):
            return SimpleNamespace(float=lambda: SimpleNamespace(cpu=lambda: SimpleNamespace(numpy=lambda: waveform[index])))
    class Model:
        model_config = {"sample_rate": 44100}
        def generate(self, **kwargs):
            generated.append(kwargs)
            return Tensor()
    def load(name, **kwargs):
        events.append(("load", name, kwargs))
        return Model()
    cuda = SimpleNamespace(is_available=lambda: True, device_count=lambda: 1,
        get_device_properties=lambda index: SimpleNamespace(total_memory=24 << 30),
        set_per_process_memory_fraction=lambda fraction, device: events.append(("cap", fraction, device)))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=cuda))
    monkeypatch.setitem(sys.modules, "stable_audio_3", SimpleNamespace(StableAudioModel=SimpleNamespace(from_pretrained=load)))
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(
        write=lambda *args, **kwargs: None, read=lambda *args, **kwargs: (waveform[0].T, 44100)))
    audio.worker({"output": str(tmp_path), "prompt": "Door.", "seconds": 1, "seed": 0})
    assert events == [("cap", .5, 0), ("load", "small-sfx", {"device": "cuda", "model_half": True})]
    assert generated == [{"prompt": "Door.", "duration": 1, "steps": 8, "cfg_scale": 1.0, "seed": 0,
                          "batch_size": 1, "sampler_type": "pingpong", "truncate_output_to_duration": True}]
    assert json.loads((tmp_path / "audio.json").read_text())["channels"] == 2
    cuda.is_available = lambda: False
    with pytest.raises(RuntimeError, match="no CPU fallback"):
        audio.worker({"output": str(tmp_path), "prompt": "Door.", "seconds": 1, "seed": 0})


def test_download_uses_the_bundled_encoder_not_the_separately_gated_google_repo(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("audio3_setup", audio.PACK / "scripts" / "windows-audio3-setup.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    requests = []
    revision = "a" * 40
    class Api:
        def __init__(self, **kwargs):
            pass
        def model_info(self, repo, **kwargs):
            requests.append(repo)
            return SimpleNamespace(sha=revision, siblings=[
                SimpleNamespace(size=7, rfilename="model.safetensors"),
                SimpleNamespace(size=7, rfilename="t5gemma-b-b-ul2/model.safetensors")])
    def snapshot(repo, **kwargs):
        requests.append(repo)
        root = kwargs["cache_dir"] / "snapshots" / revision
        (root / "t5gemma-b-b-ul2").mkdir(parents=True)
        (root / "model.safetensors").write_bytes(b"weights")
        (root / "t5gemma-b-b-ul2" / "model.safetensors").write_bytes(b"encoder")
        return str(root)
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(HfApi=Api, get_token=lambda: None,
                                                                       snapshot_download=snapshot))
    setup.setup(tmp_path, True)
    assert requests == ["stabilityai/stable-audio-3-small-sfx"] * 2
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert list(manifest["revisions"]) == ["stabilityai/stable-audio-3-small-sfx"]
    assert any("t5gemma-b-b-ul2" in path for path in manifest["files"])
    setup.setup(tmp_path, False)


@pytest.mark.parametrize("installed", [False, True])
def test_weights_inventory_lists_optional_model_independently_of_runtime_qualification(ready, monkeypatch, installed):
    import discover
    import windows_measurements
    monkeypatch.setattr(audio, "inventory", lambda: {
        "files": {"hub/hub/model/snapshots/revision/model.safetensors": {"size": 7},
                  "hub/hub/model/snapshots/revision/t5gemma/model.safetensors": {"size": 3},
                  "hub/hub/model/blobs/weight": {"size": 7}}} if installed else {})
    monkeypatch.setattr(windows_measurements, "feature_measurements", lambda *args: None)
    state = discover.windows_state()
    row = next(item for item in state["needs"] if item.get("model") == audio.MODEL)
    assert row["state"] == ("expected" if installed else "missing")
    assert not row["optional"] and row["feature"] == audio.MODEL
    assert row["install"]["shown"] == audio.SETUP
    assert row["model_metadata"]["engine"] == "stable-audio-3"
    assert "uninstall" not in row and "do not qualify" in row["requirements_note"]
    if installed:
        assert row["download_size"]["bytes"] == 10


def test_isolated_catalog_does_not_enable_generic_installation_or_cache_removal(tmp_path):
    import model_catalog
    import windows_image_setup
    import windows_model_remove
    assert model_catalog.model(audio.MODEL)["kind"] == "isolated"
    assert model_catalog.size_info(audio.MODEL)["bytes"] == 3492094029
    assert model_catalog.download_rows(audio.MODEL)[0]["repo"] == "stabilityai/stable-audio-3-small-sfx"
    assert "audio3" not in model_catalog.parts()
    assert "audio3" not in windows_image_setup.NATIVE_PARTS
    with pytest.raises(ValueError, match="automatic removal is unavailable"):
        windows_model_remove.plan(audio.MODEL, tmp_path)


@pytest.mark.skipif(not (shutil.which("powershell") or shutil.which("pwsh")), reason="PowerShell is required")
@pytest.mark.parametrize("no_download", [False, True])
def test_sound_setup_invokes_isolated_installer_and_preserves_no_download(tmp_path, no_download):
    source = (audio.PACK / "scripts" / "setup-windows-cuda.ps1").read_text()
    block = source.split("if ($Audio3) {", 1)[1].split('\nif ($Want -contains "train")', 1)[0]
    (tmp_path / "setup-windows-audio3.ps1").write_text(
        'param([switch]$DependenciesOnly)\n'
        '@{model="small-sfx"; dependencies_only=[bool]$DependenciesOnly} | ConvertTo-Json -Compress\n')
    wrapper = tmp_path / "invoke.ps1"
    wrapper.write_text('param([switch]$NoDownload)\n'
                       'function Say($text) {}\nfunction Native($text) {}\n'
                       'if ($true) {' + block)
    command = [shutil.which("powershell") or shutil.which("pwsh"), "-NoProfile", "-ExecutionPolicy",
               "Bypass", "-File", str(wrapper)]
    if no_download:
        command.append("-NoDownload")
    done = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(done.stdout) == {"model": "small-sfx", "dependencies_only": no_download}
